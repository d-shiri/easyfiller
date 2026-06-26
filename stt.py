"""Speech-to-text for the Regenerate dialog: dictate instructions by voice.

Microphone capture is handled by Anki's own cross-platform recorder
(`aqt.sound.record_audio` -- the same one behind the editor's mic button), so we
never touch QtMultimedia or per-OS recorders ourselves. Transcription shells out
to the `whisper-ctranslate2` CLI (a faster-whisper wrapper), exactly like
llm_client / tts call their own CLIs, because Anki's bundled Python can't import
faster-whisper's native dependencies (CTranslate2, PyAV, onnxruntime). The model
(~150 MB for "base") downloads itself on first use and then runs fully offline --
no API key, no data leaves the machine.

Install the CLI so the add-on can find it, e.g.:
    uv tool install whisper-ctranslate2   (or: pipx install whisper-ctranslate2)
or set "whisper_path" in the add-on config to its absolute path.
"""

import glob
import os
import shutil
import subprocess
import tempfile

from .util import resolve_executable, run_hidden

DEFAULT_CLI = "whisper-ctranslate2"
DEFAULT_MODEL = "base"
INSTALL_HINT = "uv tool install whisper-ctranslate2"


class STTError(RuntimeError):
    """A transcription failure worth presenting to the user.

    `hint` is an optional shell command (e.g. the uv install line) the error
    dialog renders as a copyable code chip.
    """

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint


def resolve_whisper_path(configured):
    """Best-effort absolute path to the whisper CLI (see resolve_executable)."""
    return resolve_executable(configured, DEFAULT_CLI)


def _cli_path(config):
    return resolve_whisper_path(config.get("whisper_path", DEFAULT_CLI))


def available(config):
    """True only when the whisper CLI actually resolved to a real file.

    resolve_whisper_path() returns the bare name when nothing is found, so a
    non-absolute / non-existent path means "not installed".
    """
    exe = _cli_path(config)
    return bool(exe) and os.path.isabs(exe) and os.path.exists(exe)


def missing_cli_error():
    """The STTError shown when the whisper CLI isn't installed."""
    return STTError(
        "Voice input needs the 'whisper-ctranslate2' command, which isn't "
        "installed. The command below installs it with uv (it bundles "
        "faster-whisper and downloads a small model on first use), then try "
        "again. No uv? 'pipx install whisper-ctranslate2' or 'pip install --user "
        "whisper-ctranslate2' work too. You can also point 'whisper_path' in the "
        "add-on config at its full path.",
        hint=INSTALL_HINT,
    )


def transcribe(audio_path, config):
    """Transcribe `audio_path` to plain text with the whisper CLI.

    Runs fully offline once the model is cached. Raises STTError with a friendly
    message (and sometimes an install `hint`) on any failure.
    """
    exe = _cli_path(config)
    if not (os.path.isabs(exe) and os.path.exists(exe)):
        raise missing_cli_error()

    model = config.get("whisper_model") or DEFAULT_MODEL
    language = (config.get("whisper_language") or "").strip()
    timeout = config.get("stt_timeout", 300)
    out_dir = tempfile.mkdtemp(prefix="ga_stt_")
    # int8 on CPU is the ~150 MB footprint and the fast path; --verbose False
    # keeps the CLI from streaming progress we don't read. The CLI writes
    # "<input-basename>.txt" into --output_dir.
    cmd = [
        exe, audio_path,
        "--model", model,
        "--device", "cpu",
        "--compute_type", "int8",
        "--output_format", "txt",
        "--output_dir", out_dir,
        "--verbose", "False",
    ]
    if language:
        cmd += ["--language", language]

    try:
        proc = run_hidden(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise missing_cli_error()
    except subprocess.TimeoutExpired:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise STTError(
            "Transcription timed out after %s seconds. The first run downloads "
            "the model (~150 MB); try again once it's cached, or raise "
            "'stt_timeout' in the add-on config." % timeout
        )

    if proc.returncode != 0:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise STTError(
            "whisper-ctranslate2 failed: "
            + (proc.stderr.strip() or proc.stdout.strip() or "unknown error")
        )

    text = _read_output(out_dir)
    if not text:
        raise STTError("Couldn't make out any speech. Try recording again.")
    return text


def _read_output(out_dir):
    """Read the single .txt the CLI wrote, then delete the temp dir. '' if none."""
    text = ""
    try:
        files = glob.glob(os.path.join(out_dir, "*.txt"))
        if files:
            with open(files[0], encoding="utf-8") as fh:
                text = fh.read().strip()
    except OSError:
        text = ""
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    return text
