"""Add pronunciation audio by shelling out to the edge-tts CLI.

edge-tts uses Microsoft's free online Neural voices (the same voices Azure TTS
exposes, e.g. de-DE-AmalaNeural) with no API key and no AwesomeTTS dependency.
We call the CLI directly -- exactly like llm_client.py calls its LLM CLI --
because Anki's bundled Python can't import the package. The CLI's own shebang
points at a Python that has edge_tts installed, so an absolute path works even
under Anki's stripped environment.

Install edge-tts so the add-on can find it, e.g.:
    pipx install edge-tts     (or: uv tool install edge-tts)
or set "edge_tts_path" in the add-on config to its absolute path.
"""

import concurrent.futures
import os
import re
import subprocess
import tempfile

from aqt import mw
from aqt.utils import tooltip

from . import dialogs
from . import edge_tts_native
from . import overlay
from .util import field_index, has_audio, resolve_executable, run_hidden, strip_html

DEFAULT_VOICE = "de-DE-AmalaNeural"


class TTSError(RuntimeError):
    """An edge-tts failure whose full CLI output is kept aside for a details pane.

    str(exc) is a one-line headline (the actual error); `.details` holds the raw
    stderr/traceback so show_error can tuck it behind a collapsible section
    instead of stretching the dialog off-screen."""

    def __init__(self, summary, details=None):
        super().__init__(summary)
        self.details = details


def _short_reason(output):
    """The single most useful line from a CLI error blob (often a traceback).

    edge-tts dumps a whole Python traceback on network errors; its last non-empty
    line is the real exception (e.g. 'ConnectionResetError: [Errno 104] Connection
    reset by peer'), which is all the headline needs."""
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    return lines[-1] if lines else "no audio produced"

# German dictionary abbreviations -> full words. edge-tts otherwise pronounces
# "etw." / "jdn." letter-by-letter, so we expand them in the text we synthesize
# (the field's own text is left untouched). Matched as whole tokens ending in a
# period, in any field.
_ABBREVIATIONS = {
    "etw": "etwas",
    "jd": "jemand",
    "jdn": "jemanden",
    "jdm": "jemandem",
    "jds": "jemandes",
    "jmd": "jemand",
    "jmdn": "jemanden",
    "jmdm": "jemandem",
    "jmds": "jemandes",
}
_ABBR_RE = re.compile(
    r"\b(" + "|".join(sorted(_ABBREVIATIONS, key=len, reverse=True)) + r")\.",
    re.IGNORECASE,
)


def expand_abbreviations(text):
    """Replace abbreviations like 'etw.' with 'etwas' so TTS reads them aloud."""
    def repl(match):
        word = _ABBREVIATIONS[match.group(1).lower()]
        if match.group(1)[:1].isupper():  # keep a sentence-initial capital
            word = word[:1].upper() + word[1:]
        return word

    return _ABBR_RE.sub(repl, text)


def resolve_edge_tts_path(configured):
    """Best-effort resolution of the edge-tts executable, portably.

    pipx / `uv tool` drop the CLI in ~/.local/bin on every OS (with a .exe on
    Windows); the shared resolver checks there and on PATH. Anki launches with a
    minimal PATH, so the explicit ~/.local/bin probe is what makes it findable.
    """
    return resolve_executable(configured, "edge-tts")


def _rate_arg(speed):
    """1.25 -> '+25%', 0.9 -> '-10%' (edge-tts rate is relative to normal)."""
    return "%+d%%" % round((float(speed) - 1.0) * 100)


def _pitch_arg(pitch):
    """0 -> '+0Hz', -5 -> '-5Hz'."""
    return "%+dHz" % int(pitch)


def _progress_msg(done, total):
    if total > 1:
        return "Adding pronunciation… %d/%d" % (done, total)
    return "Adding pronunciation…"


def _cli_available(exe):
    """True only when `exe` resolved to a real file -- i.e. the edge-tts CLI is
    actually installed. resolve_edge_tts_path() returns the bare name when it
    isn't found, so a non-absolute/non-existent path means "no CLI"."""
    return bool(exe) and os.path.isabs(exe) and os.path.exists(exe)


def _synthesize(exe, voice, rate, pitch, text, timeout):
    """Synthesize one clip to a temp mp3 and return its path (caller deletes it).

    Uses the built-in, zero-install engine (edge_tts_native) first. If that fails
    and the user happens to have the edge-tts CLI installed, fall back to it --
    so a protocol change we haven't caught up to is still recoverable -- and
    otherwise re-raise the built-in error.
    """
    try:
        return edge_tts_native.synthesize(voice, rate, pitch, text, timeout=timeout)
    except Exception as native_exc:
        if not _cli_available(exe):
            raise TTSError("edge-tts failed: " + _short_reason(str(native_exc)))
        try:
            return _synthesize_cli(exe, voice, rate, pitch, text, timeout)
        except Exception:
            raise native_exc


def _synthesize_cli(exe, voice, rate, pitch, text, timeout):
    """Fallback: run the external edge-tts CLI to a temp mp3 and return its path."""
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        proc = run_hidden(
            [exe, "--voice", voice, "--rate", rate, "--pitch", pitch,
             "--text", text, "--write-media", path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        os.remove(path)
        raise RuntimeError(
            "Could not find the 'edge-tts' CLI at '%s'. Install it (pipx install "
            "edge-tts) or set 'edge_tts_path' in the add-on config." % exe
        )
    except subprocess.TimeoutExpired:
        os.remove(path)
        raise RuntimeError("edge-tts timed out after %s seconds." % timeout)

    if proc.returncode != 0 or not os.path.getsize(path):
        os.remove(path)
        output = proc.stderr.strip() or proc.stdout.strip()
        raise TTSError(
            "edge-tts failed: " + _short_reason(output),
            details=output or None,
        )
    return path


def pronounce(editor, config):
    exe = resolve_edge_tts_path(config.get("edge_tts_path", "edge-tts"))
    voice = config.get("tts_voice") or DEFAULT_VOICE
    rate = _rate_arg(config.get("tts_speed", 1.25))
    pitch = _pitch_arg(config.get("tts_pitch", 0))
    timeout = config.get("tts_timeout", 60)

    note = editor.note
    jobs = []  # (field index, text)
    for fname in config.get("tts_fields", []):
        idx = field_index(note, fname)
        if idx is None:
            continue
        raw = note.fields[idx]
        if has_audio(raw):
            continue
        text = strip_html(raw)
        if not text:
            continue
        # Synthesize the expanded text; the field keeps its original wording.
        jobs.append((idx, expand_abbreviations(text)))

    if not jobs:
        if overlay.is_shown():
            overlay.set_step(editor, "tts", label="No pronunciation needed", state="done")
        overlay.hide(editor)
        tooltip("Nothing to pronounce (fields empty or already have audio).")
        return

    total = len(jobs)
    # Standalone pronounce opens its own cancelable overlay; the "both" flow
    # reuses the one generate already opened (and its existing cancel token).
    if overlay.is_shown():
        token = overlay.current_token()
        overlay.set_step(editor, "tts", label=_progress_msg(0, total), state="active")
    else:
        token = overlay.start(
            editor, [("tts", _progress_msg(0, total), "active")], cancelable=True
        )

    def cancelled():
        return token is not None and token.cancelled

    def task():
        # edge-tts calls are network-bound subprocesses (they release the GIL
        # while waiting), so synthesize all clips concurrently in a thread pool.
        done = {}
        errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(total, 8)) as ex:
            futs = {
                ex.submit(_synthesize, exe, voice, rate, pitch, text, timeout): idx
                for idx, text in jobs
            }
            for fut in concurrent.futures.as_completed(futs):
                idx = futs[fut]
                try:
                    done[idx] = fut.result()
                except Exception as exc:
                    errors.append(exc)
                if cancelled():
                    break  # stop reporting; on_done discards what's collected
                # Update the overlay from the main thread as each clip lands.
                finished = len(done) + len(errors)
                mw.taskman.run_on_main(
                    lambda n=finished: overlay.set_step(
                        editor, "tts", label=_progress_msg(n, total)
                    )
                )
        if cancelled():
            for path in done.values():  # drop any clips already written
                if os.path.exists(path):
                    os.remove(path)
            return []
        if errors:
            for path in done.values():  # discard partial clips before bailing
                if os.path.exists(path):
                    os.remove(path)
            raise errors[0]
        return [(idx, done[idx]) for idx, _ in jobs]  # keep original field order

    def on_done(future):
        if cancelled():
            return  # the Cancel handler already hid the overlay
        try:
            results = future.result()
        except Exception as exc:
            overlay.set_step(editor, "tts", state="error")
            overlay.hide(editor)
            dialogs.show_error(
                editor.parentWindow, "TTS failed: %s" % exc,
                title="Pronunciation failed",
                details=getattr(exc, "details", None),
            )
            return
        note = editor.note
        for idx, path in results:
            try:
                filename = mw.col.media.add_file(path)
            except AttributeError:  # older API name
                filename = mw.col.media.addFile(path)
            finally:
                if os.path.exists(path):
                    os.remove(path)
            tag = "[sound:%s]" % filename
            note.fields[idx] = (note.fields[idx] + " " + tag).strip()
        editor.set_note(note)
        overlay.set_step(editor, "tts", label=_progress_msg(total, total), state="done")
        overlay.hide(editor)
        tooltip("Added %d pronunciation clip(s)." % len(results))

    mw.taskman.run_in_background(task, on_done)
