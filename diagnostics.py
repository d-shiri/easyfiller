"""Setup & Diagnostics panel: a one-screen health check for the add-on.

Reached from **Tools -> EasyFiller: Setup & Diagnostics**. It verifies the things
a new user trips over -- the LLM provider, the edge-tts CLI, the optional voice-input
CLI, and whether a note type with the configured fields exists -- and lets them fix
each on the spot:

  * the LLM row probes Claude (`claude --version`) or Ollama (its HTTP API) and,
    when an Ollama model is missing, offers a one-click download with progress;
  * the edge-tts row confirms the CLI is found and plays a spoken sample in the
    configured voice/rate/pitch so the choice can be heard before committing;
  * the voice-input row checks the optional whisper-ctranslate2 CLI behind the
    Regenerate "Speak" button, showing the install command when it's missing;
  * the fields row lists the required fields and, when no note type has them all,
    creates a ready-made one.

Each probe runs off the main thread (subprocess / HTTP), updating its row when it
finishes, so the dialog never freezes Anki. The frameless card shell, palette,
and copy-chip are borrowed from dialogs.py so this matches the rest of the UI.
"""

import os
import subprocess

from aqt import mw
from aqt.qt import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QTimer,
    QVBoxLayout,
)
from aqt.theme import theme_manager
from aqt.utils import tooltip

from . import dialogs
from . import llm_client
from . import stt
from . import tts
from .util import run_hidden

# The add-on's package name (this module is german_autofill.diagnostics), which is
# the key addonManager stores config under.
_ADDON = __name__.split(".")[0]

_SAMPLE_TEXT = "Guten Tag! Dies ist eine kurze Hörprobe der ausgewählten Stimme."


def _get_config():
    return mw.addonManager.getConfig(_ADDON) or {}


def _write_config(config):
    mw.addonManager.writeConfig(_ADDON, config)


# --------------------------------------------------------------------------- #
# Field mapping helpers                                                        #
# --------------------------------------------------------------------------- #
def _required_fields(config):
    """Ordered, de-duplicated list of every field the add-on writes to.

    Source and meaning first, then each example paired with its translation, then
    any extra TTS targets -- the natural order to lay them out on a new note type.
    """
    out = []

    def add(name):
        if name and name not in out:
            out.append(name)

    add(config.get("source_field", "Back"))
    add(config.get("meaning_field", "Front"))
    examples = config.get("example_fields", [])
    translations = config.get("translation_fields", [])
    for i in range(max(len(examples), len(translations))):
        if i < len(examples):
            add(examples[i])
        if i < len(translations):
            add(translations[i])
    for name in config.get("tts_fields", []):
        add(name)
    return out


def _matching_models(required):
    """Names of note types that contain every field in `required`."""
    matches = []
    for model in mw.col.models.all():
        names = {fld["name"] for fld in model["flds"]}
        if all(req in names for req in required):
            matches.append(model["name"])
    return matches


def _create_note_type(config):
    """Build a note type with all required fields and a sensible card template.

    Front shows the meaning (the gloss you study toward); the back reveals the
    word and every example with its translation. Returns the created name, made
    unique so repeated clicks don't collide.
    """
    mm = mw.col.models
    required = _required_fields(config)

    base = "EasyFiller (German)"
    name = base
    n = 2
    by_name = getattr(mm, "by_name", None) or getattr(mm, "byName")
    while by_name(name):
        name = "%s %d" % (base, n)
        n += 1

    model = mm.new(name)
    for field in required:
        mm.add_field(model, mm.new_field(field))

    src = config.get("source_field", "Back")
    front = config.get("meaning_field", "Front")
    examples = config.get("example_fields", [])
    translations = config.get("translation_fields", [])

    template = mm.new_template("Card 1")
    template["qfmt"] = "{{%s}}" % front
    back = ["{{FrontSide}}", "<hr id=answer>", "<b>{{%s}}</b>" % src]
    for i, ex in enumerate(examples):
        back.append("<div style='margin-top:8px'>{{%s}}</div>" % ex)
        if i < len(translations):
            back.append(
                "<div style='color:#888;font-size:90%%'>{{%s}}</div>" % translations[i]
            )
    template["afmt"] = "\n".join(back)
    mm.add_template(model, template)

    mm.add(model)
    return name


# --------------------------------------------------------------------------- #
# Dependency probes (run off the main thread)                                  #
# --------------------------------------------------------------------------- #
def _check_llm(config):
    """Dispatch to the configured provider's probe, returning a result dict.

    Result keys: state ("ok"/"warn"/"error"), title, detail, optional `hint`
    (a shell command shown as a copy chip) and optional `download` (an Ollama
    model name to offer a Download button for).
    """
    provider = (config.get("provider") or "claude").strip().lower()
    if provider == "claude":
        return _check_claude(config)
    if provider == "ollama":
        return _check_ollama(config)
    return {
        "state": "error",
        "title": "Unknown provider “%s”" % provider,
        "detail": 'Set "provider" to "claude" or "ollama" in the add-on config.',
    }


def _check_claude(config):
    exe = llm_client.resolve_claude_path(config.get("claude_path", "claude"))
    model = (config.get("claude_model") or "").strip() or "default"
    try:
        proc = run_hidden(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            env=llm_client._build_env(),
        )
    except FileNotFoundError:
        return {
            "state": "error",
            "title": "Claude CLI not found",
            "detail": "Looked for “%s”. Install the Claude Code CLI and make sure "
            "`claude` runs in a terminal, or set \"claude_path\" to its absolute "
            "path in the config." % exe,
        }
    except subprocess.TimeoutExpired:
        return {
            "state": "warn",
            "title": "Claude CLI didn’t respond",
            "detail": "“%s --version” timed out after 20s." % exe,
        }
    except Exception as exc:
        return {"state": "error", "title": "Claude CLI error", "detail": str(exc)}

    if proc.returncode == 0:
        version = (proc.stdout.strip() or proc.stderr.strip() or "installed").splitlines()[0]
        return {
            "state": "ok",
            "title": "Claude CLI ready",
            "detail": "%s · model %s\n%s\nSign-in is confirmed the first time you "
            "Generate." % (exe, model, version),
        }
    return {
        "state": "warn",
        "title": "Claude CLI returned an error",
        "detail": proc.stderr.strip() or proc.stdout.strip() or "unknown error",
    }


def _check_ollama(config):
    base = llm_client._ollama_base(config)
    version = llm_client.ollama_version(config)
    if version is None:
        return {
            "state": "error",
            "title": "Ollama not reachable",
            "detail": "Couldn’t reach Ollama at %s. Start it (try `ollama list` in a "
            'terminal), or set "ollama_host" in the config.' % base,
            "hint": "ollama serve",
        }
    model = (config.get("ollama_model") or "").strip()
    installed = llm_client.ollama_models(config) or []
    if not model:
        return {
            "state": "warn",
            "title": "No Ollama model set",
            "detail": 'Ollama %s is running at %s. Set "ollama_model" to a pulled '
            "model." % (version, base),
            "hint": "ollama pull %s" % llm_client.BEST_MODEL,
            "download": llm_client.BEST_MODEL,
        }
    if model in installed or (model + ":latest") in installed:
        return {
            "state": "ok",
            "title": "Ollama ready",
            "detail": "Ollama %s · model %s (at %s)." % (version, model, base),
        }
    return {
        "state": "warn",
        "title": "Model “%s” isn’t installed" % model,
        "detail": "Ollama %s is running at %s, but this model hasn’t been pulled "
        "yet." % (version, base),
        "hint": "ollama pull %s" % model,
        "download": model,
    }


def _check_stt(config):
    """Probe the whisper-ctranslate2 CLI behind the Regenerate "Speak" button.

    Voice input is optional, so a missing CLI is a warning (not a red error). When
    the CLI is found we actually run it (`--help`) to confirm its native deps load,
    catching a broken install rather than just a present file.
    """
    exe = stt.resolve_whisper_path(config.get("whisper_path", stt.DEFAULT_CLI))
    if not (os.path.isabs(exe) and os.path.exists(exe)):
        return {
            "state": "warn",
            "title": "Voice input not set up (optional)",
            "detail": "The “whisper-ctranslate2” command wasn’t found, so the "
            "microphone button in Regenerate is off. Install it to dictate "
            "instructions by voice — offline, no API key — or set “whisper_path” "
            "in the config to its absolute path.",
            "hint": stt.INSTALL_HINT,
        }
    model = (config.get("whisper_model") or stt.DEFAULT_MODEL).strip()
    try:
        proc = run_hidden([exe, "--help"], capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        return {
            "state": "warn",
            "title": "Voice input CLI didn’t respond",
            "detail": "“%s --help” timed out after 40s." % exe,
        }
    except Exception as exc:
        return {"state": "error", "title": "Voice input CLI error", "detail": str(exc)}

    if proc.returncode == 0:
        return {
            "state": "ok",
            "title": "Voice input ready",
            "detail": "%s · model %s\nThe model (~150 MB) downloads itself the "
            "first time you dictate." % (exe, model),
        }
    return {
        "state": "warn",
        "title": "Voice input CLI returned an error",
        "detail": proc.stderr.strip() or proc.stdout.strip() or "unknown error",
    }


def _play_file(path):
    """Play a synthesized clip through Anki's audio, deleting it shortly after.

    av_player is async, so the temp file must outlive the call; a delayed timer
    removes it once playback has certainly finished.
    """
    try:
        from anki.sound import SoundOrVideoTag
        from aqt.sound import av_player

        av_player.play_tags([SoundOrVideoTag(path)])
    except Exception:
        from aqt.qt import QDesktopServices, QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    QTimer.singleShot(120000, lambda: os.path.exists(path) and os.remove(path))


# --------------------------------------------------------------------------- #
# Widgets                                                                      #
# --------------------------------------------------------------------------- #
_GLYPHS = {"ok": "✅", "warn": "⚠️", "error": "⛔", "info": "ℹ️", "checking": "⏳"}


class _StatusRow(QFrame):
    """One check: a status glyph, a title + detail, and a tools row.

    The tools row holds an optional copy chip (a fix command) and an optional
    action button (Download / Create / Play), inserted before a trailing stretch
    so they hug the left. set_result() clears the tools so each re-run starts clean.
    """

    # Row geometry, kept in sync with the layout below so heightForWidth() can
    # work out how wide the text column is at a given total width.
    _H_MARGIN = 14   # left + right content margin (each)
    _V_MARGIN = 12   # top + bottom content margin (each)
    _GLYPH_W = 22
    _GAP = 12        # spacing between glyph and text column

    def __init__(self, palette):
        super().__init__()
        self.setObjectName("gaRow")
        self._c = palette
        self._action_btn = None
        # Report height-for-width upward; a plain QFrame otherwise hides the
        # wrapped labels' true height from the card layout, clipping the text.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        row = QHBoxLayout(self)
        row.setContentsMargins(self._H_MARGIN, self._V_MARGIN, self._H_MARGIN, self._V_MARGIN)
        row.setSpacing(self._GAP)

        self._glyph = QLabel(_GLYPHS["checking"])
        self._glyph.setObjectName("gaGlyph")
        self._glyph.setFixedWidth(self._GLYPH_W)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignTop)

        self._col = QVBoxLayout()
        self._col.setSpacing(4)
        self._title = QLabel("…")
        self._title.setObjectName("gaRowTitle")
        self._title.setWordWrap(True)
        self._detail = QLabel("")
        self._detail.setObjectName("gaIntro")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._col.addWidget(self._title)
        self._col.addWidget(self._detail)

        self._tools = QHBoxLayout()
        self._tools.setContentsMargins(0, 4, 0, 0)
        self._tools.setSpacing(8)
        self._tools.addStretch(1)
        self._col.addLayout(self._tools)
        row.addLayout(self._col, 1)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        col_w = max(0, width - 2 * self._H_MARGIN - self._GLYPH_W - self._GAP)
        return self._col.heightForWidth(col_w) + 2 * self._V_MARGIN

    def resizeEvent(self, event):
        # Pin the frame to the height its wrapped content actually needs at the
        # current width, so the card grows to fit instead of clipping rows.
        super().resizeEvent(event)
        needed = self.heightForWidth(self.width())
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)
            self.updateGeometry()

    def set_checking(self, title):
        self._glyph.setText(_GLYPHS["checking"])
        self._title.setText(title)
        self.set_detail("Checking…")
        self._clear_tools()

    def set_result(self, state, title, detail=""):
        self._glyph.setText(_GLYPHS.get(state, "•"))
        self._title.setText(title)
        self.set_detail(detail)
        self._clear_tools()

    def set_detail(self, text):
        self._detail.setText(text)
        self._detail.setVisible(bool(text))

    def set_hint(self, command):
        self._tools.insertWidget(
            self._tools.count() - 1,
            dialogs._CopyButton(command, "gaCode", self._c["text"]),
        )

    def set_action(self, label, callback, primary=False):
        btn = QPushButton(label)
        btn.setObjectName("gaPrimary" if primary else "gaBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.clicked.connect(lambda _checked=False: callback())
        self._tools.insertWidget(self._tools.count() - 1, btn)
        self._action_btn = btn

    def set_action_state(self, label, enabled):
        if self._action_btn is not None:
            self._action_btn.setText(label)
            self._action_btn.setEnabled(enabled)

    def _clear_tools(self):
        self._action_btn = None
        for i in reversed(range(self._tools.count())):
            widget = self._tools.itemAt(i).widget()
            if widget is not None:
                self._tools.removeWidget(widget)
                widget.deleteLater()


# Extra style rules layered on top of dialogs._STYLE. No literal "%" so the
# shared %()s palette substitution stays valid.
_EXTRA = """
#gaRow{ background:%(pill_bg)s; border-radius:11px; }
#gaGlyph{ font-size:16px; }
#gaRowTitle{
  font-size:14px; font-weight:600; color:%(text)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
#gaSection{
  font-size:11px; font-weight:700; color:%(muted)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
"""


def open_diagnostics(parent):
    """Open the modal Setup & Diagnostics card."""
    c = dialogs._palette(theme_manager.night_mode)
    dlg, card, lay = dialogs._make_card(parent, c)
    # Fixed width keeps the rows' word-wrap (and the heights they report) stable,
    # so the dialog settles on one height instead of oscillating.
    card.setFixedWidth(520)

    # Track liveness so background callbacks landing after the dialog closes are
    # discarded instead of touching deleted widgets.
    alive = {"v": True}
    dlg.finished.connect(lambda *_: alive.update(v=False))

    def refit():
        # Rows learn their wrapped height only after a layout pass, so re-fit on
        # the next event-loop tick too (matching dialogs._center's own deferral).
        if alive["v"]:
            dialogs._place(dlg, parent)
            QTimer.singleShot(0, lambda: alive["v"] and dialogs._place(dlg, parent))

    # Header.
    head = QHBoxLayout()
    head.setSpacing(12)
    icon = QLabel("🩺")
    icon.setObjectName("gaIcon")
    title = QLabel("EasyFiller — Setup & Diagnostics")
    title.setObjectName("gaTitle")
    title.setWordWrap(True)
    head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    head.addWidget(title, 1)
    lay.addLayout(head)

    sub = QLabel("Checks the tools EasyFiller needs and your note type’s fields. "
                 "Fix anything that isn’t green right here.")
    sub.setObjectName("gaIntro")
    sub.setWordWrap(True)
    lay.addWidget(sub)

    llm_row = _StatusRow(c)
    tts_row = _StatusRow(c)
    stt_row = _StatusRow(c)
    fields_row = _StatusRow(c)
    for r in (llm_row, tts_row, stt_row, fields_row):
        lay.addWidget(r)

    # ---- LLM (background) ------------------------------------------------- #
    def start_pull(model):
        config = _get_config()
        llm_row.set_action_state("Downloading…", False)

        def report(completed, total, status):
            if total and completed is not None:
                pct = max(0, min(100, int(completed * 100 / total)))
                label = "Downloading %s — %d%%" % (model, pct)
            else:
                label = (status or "Downloading %s" % model).capitalize()
            mw.taskman.run_on_main(
                lambda: alive["v"] and llm_row.set_detail(label)
            )

        def work():
            llm_client.pull_model(model, config, on_progress=report)

        def done(future):
            if not alive["v"]:
                return
            try:
                future.result()
            except Exception as exc:
                llm_row.set_action_state("Download %s" % model, True)
                dialogs.show_error(dlg, "%s" % exc, title="Download failed")
                return
            # Make the just-downloaded model active, then re-probe so it shows green.
            config2 = _get_config()
            config2["ollama_model"] = model
            _write_config(config2)
            run_llm_check()

        mw.taskman.run_in_background(work, done)

    def apply_llm(res):
        llm_row.set_result(res["state"], res["title"], res.get("detail", ""))
        if res.get("hint"):
            llm_row.set_hint(res["hint"])
        if res.get("download"):
            model = res["download"]
            llm_row.set_action("Download %s" % model, lambda: start_pull(model), primary=True)
        refit()

    def run_llm_check():
        config = _get_config()
        provider = (config.get("provider") or "claude").strip().lower()
        llm_row.set_checking("LLM provider — %s" % provider)
        refit()

        def work():
            return _check_llm(config)

        def done(future):
            if not alive["v"]:
                return
            try:
                res = future.result()
            except Exception as exc:
                res = {"state": "error", "title": "LLM check failed", "detail": str(exc)}
            apply_llm(res)

        mw.taskman.run_in_background(work, done)

    # ---- edge-tts (sync probe, async sample) ------------------------------ #
    def play_sample():
        config = _get_config()
        exe = tts.resolve_edge_tts_path(config.get("edge_tts_path", "edge-tts"))
        voice = config.get("tts_voice") or tts.DEFAULT_VOICE
        rate = tts._rate_arg(config.get("tts_speed", 1.25))
        pitch = tts._pitch_arg(config.get("tts_pitch", 0))
        timeout = config.get("tts_timeout", 60)
        tts_row.set_action_state("Synthesizing…", False)

        def work():
            return tts._synthesize(exe, voice, rate, pitch, _SAMPLE_TEXT, timeout)

        def done(future):
            if not alive["v"]:
                try:
                    path = future.result()
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
                return
            tts_row.set_action_state("▶  Play sample", True)
            try:
                path = future.result()
            except Exception as exc:
                dialogs.show_error(
                    dlg, "%s" % exc, title="Couldn’t play sample",
                    details=getattr(exc, "details", None),
                )
                return
            _play_file(path)

        mw.taskman.run_in_background(work, done)

    def run_tts_check():
        config = _get_config()
        voice = config.get("tts_voice") or tts.DEFAULT_VOICE
        rate = tts._rate_arg(config.get("tts_speed", 1.25))
        # Pronunciation is built in (no install required); the external edge-tts
        # CLI, if present, only serves as an automatic fallback.
        exe = tts.resolve_edge_tts_path(config.get("edge_tts_path", "edge-tts"))
        if tts._cli_available(exe):
            engine = "built-in engine · CLI fallback available (%s)" % exe
        else:
            engine = "built-in engine · no install needed"
        tts_row.set_result(
            "ok", "Pronunciation ready",
            "%s · voice %s · rate %s" % (engine, voice, rate),
        )
        tts_row.set_action("▶  Play sample", play_sample)
        refit()

    # ---- Voice input / whisper (background) ------------------------------- #
    def run_stt_check():
        config = _get_config()
        stt_row.set_checking("Voice input — whisper")
        refit()

        def work():
            return _check_stt(config)

        def done(future):
            if not alive["v"]:
                return
            try:
                res = future.result()
            except Exception as exc:
                res = {"state": "error", "title": "Voice input check failed",
                       "detail": str(exc)}
            stt_row.set_result(res["state"], res["title"], res.get("detail", ""))
            if res.get("hint"):
                stt_row.set_hint(res["hint"])
            refit()

        mw.taskman.run_in_background(work, done)

    # ---- Note type fields (sync) ------------------------------------------ #
    def create_note_type():
        config = _get_config()
        try:
            name = _create_note_type(config)
        except Exception as exc:
            dialogs.show_error(dlg, "%s" % exc, title="Couldn’t create note type")
            return
        tooltip("Created note type “%s”." % name)
        run_fields_check()

    def run_fields_check():
        config = _get_config()
        required = _required_fields(config)
        if mw.col is None:
            fields_row.set_result("info", "Note type fields", "Open a profile to check fields.")
            refit()
            return
        matches = _matching_models(required)
        if matches:
            fields_row.set_result(
                "ok", "Note type fields match",
                "Have all required fields: %s.\nRequired: %s."
                % (", ".join(matches), ", ".join(required)),
            )
        else:
            fields_row.set_result(
                "warn", "No note type has all required fields",
                "Required: %s.\nCreate a ready-made note type, or rename fields / "
                "edit the config to match." % ", ".join(required),
            )
            fields_row.set_action("Create matching note type", create_note_type, primary=True)
        refit()

    def run_all():
        run_fields_check()
        run_tts_check()
        run_stt_check()
        run_llm_check()

    # Footer.
    lay.addSpacing(4)
    btns = QHBoxLayout()
    rerun = QPushButton("Re-run checks")
    rerun.setObjectName("gaBtn")
    rerun.setCursor(Qt.CursorShape.PointingHandCursor)
    rerun.setAutoDefault(False)
    rerun.clicked.connect(lambda: run_all())
    close = QPushButton("Close")
    close.setObjectName("gaPrimary")
    close.setCursor(Qt.CursorShape.PointingHandCursor)
    close.setDefault(True)
    close.clicked.connect(dlg.accept)
    btns.addWidget(rerun)
    btns.addStretch(1)
    btns.addWidget(close)
    lay.addLayout(btns)

    dlg.setStyleSheet((dialogs._STYLE + _EXTRA) % c)
    dialogs._center(dlg, parent)
    run_all()
    dlg.exec()
