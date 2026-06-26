"""A modern, voice-reactive recording dialog for the "Speak" (dictation) button.

We keep Anki's own QtMultimedia recorder for the actual capture -- it handles the
audio format, int16/float conversion, the WAV write and the macOS path, all
cross-platform -- but replace its plain stock dialog with our styled card and a
live waveform that oscillates with the user's voice.

The trick: `QtAudioInputRecorder` accumulates raw samples in a `_buffer` as you
talk, so a ~30 fps timer reads the newly-arrived bytes, computes their RMS level,
and scrolls it across the waveform. Everything degrades gracefully: if that
buffer isn't readable (a future Anki, or the native macOS recorder), the wave
just breathes instead of reacting; if our recorder can't even be built, the
caller falls back to Anki's stock `record_audio`.

`record_audio(parent, on_done)` mirrors Anki's signature minus the encode flag:
it calls `on_done(wav_path)` once the recording is saved, or never if cancelled.
"""

import array
import math
import os
import platform
import tempfile
import time

from aqt import mw
from aqt.qt import (
    QColor,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLinearGradient,
    QPainter,
    QPushButton,
    QRectF,
    Qt,
    QTimer,
    QWidget,
)
from aqt.theme import theme_manager
from aqt.utils import showWarning

from . import dialogs

# Vibrant left-to-right spectrum painted across the bars (Apple-system hues).
_SPECTRUM = ["#0a84ff", "#5e5ce6", "#bf5af2", "#ff375f"]

# Recorders are held here while their stop()/write runs in the background, so the
# QAudioSource and its timers aren't garbage-collected after the dialog closes.
_active = []


def _rms_level(chunk, kind):
    """Root-mean-square loudness of a raw PCM `chunk`, normalized to ~0..1.

    `kind` is the sample layout: "u8", "s16", "s32" or "f32". The signal's mean
    (its DC offset) is removed first -- many mics carry a constant bias that would
    otherwise dominate the RMS and swamp the speech energy, making the meter look
    flat. Pure (no Qt) so the silence-vs-speech discrimination can be unit-tested;
    subsamples so it stays cheap regardless of chunk size.
    """
    if not chunk:
        return 0.0
    if kind == "f32":
        n = len(chunk) // 4
        if not n:
            return 0.0
        samples = array.array("f")
        samples.frombytes(chunk[: n * 4])
        bias, scale = 0.0, 1.0
    elif kind == "s32":
        n = len(chunk) // 4
        if not n:
            return 0.0
        samples = array.array("i")
        samples.frombytes(chunk[: n * 4])
        bias, scale = 0.0, 1.0 / 2147483648.0
    elif kind == "u8":
        samples = array.array("B")
        samples.frombytes(chunk)
        bias, scale = 128.0, 1.0 / 128.0   # unsigned, centered at 128
    else:  # "s16"
        n = len(chunk) // 2
        if not n:
            return 0.0
        samples = array.array("h")
        samples.frombytes(chunk[: n * 2])
        bias, scale = 0.0, 1.0 / 32768.0
    step = max(1, len(samples) // 512)
    vals = [(samples[i] - bias) * scale for i in range(0, len(samples), step)]
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)        # DC offset
    acc = 0.0
    for v in vals:
        d = v - mean
        acc += d * d
    return math.sqrt(acc / len(vals))


def _format_kind(fmt):
    """Map a QAudioFormat to one of our _rms_level `kind` strings."""
    try:
        from PyQt6.QtMultimedia import QAudioFormat

        sf = fmt.sampleFormat()
        return {
            QAudioFormat.SampleFormat.UInt8: "u8",
            QAudioFormat.SampleFormat.Int16: "s16",
            QAudioFormat.SampleFormat.Int32: "s32",
            QAudioFormat.SampleFormat.Float: "f32",
        }.get(sf, "s16")
    except Exception:
        return "s16"


def _make_recorder(output_path, parent):
    """Build Anki's recorder, mirroring its own platform choice."""
    from aqt.sound import NativeMacRecorder, QtAudioInputRecorder, macos_helper

    if macos_helper and platform.machine() == "arm64":
        return NativeMacRecorder(output_path)
    return QtAudioInputRecorder(output_path, mw, parent)


class _Waveform(QWidget):
    """A scrolling, mirrored bar waveform. Newest sample enters from the right.

    `push(level)` feeds a 0..1 amplitude; displayed bars ease toward their target
    each frame for a fluid, springy motion. When silent it shows a gentle
    breathing ripple so the meter never looks dead.
    """

    def __init__(self, bars=46):
        super().__init__()
        self._n = bars
        self._levels = [0.0] * bars   # what's drawn (eased)
        self._targets = [0.0] * bars  # where each bar is heading
        self.setMinimumHeight(72)

    def push(self, level):
        self._targets = self._targets[1:] + [max(0.0, min(1.0, level))]

    def animate(self):
        # Ease each bar toward its target every frame; a gentle factor keeps the
        # vertical motion smooth rather than snapping.
        for i in range(self._n):
            self._levels[i] += (self._targets[i] - self._levels[i]) * 0.22
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        mid = h / 2.0
        max_half = (h - 8) / 2.0
        min_h = 3.0

        grad = QLinearGradient(0, 0, w, 0)
        for i, hexc in enumerate(_SPECTRUM):
            grad.setColorAt(i / (len(_SPECTRUM) - 1), QColor(hexc))
        p.setBrush(grad)
        p.setPen(Qt.PenStyle.NoPen)

        gap = 3.0
        bar_w = max(2.0, (w - gap * (self._n - 1)) / self._n)
        x = 0.0
        for lvl in self._levels:
            bar_h = max(min_h, lvl * max_half * 2.0)
            rect = QRectF(x, mid - bar_h / 2.0, bar_w, bar_h)
            p.drawRoundedRect(rect, bar_w / 2.0, bar_w / 2.0)
            x += bar_w + gap
        p.end()


def record_audio(parent, on_done):
    """Show the animated recorder. Calls on_done(wav_path) when saved.

    Raises if the recorder can't be created (e.g. no input device) so the caller
    can fall back to Anki's stock dialog.
    """
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    recorder = _make_recorder(path, parent)
    _active.append(recorder)

    c = dialogs._palette(theme_manager.night_mode)
    dlg, card, lay = dialogs._make_card(parent, c)

    # Header: pulsing red dot + title.
    head = QHBoxLayout()
    head.setSpacing(12)
    dot = QLabel("●")
    dot.setObjectName("gaRecDot")
    title = QLabel("Listening…")
    title.setObjectName("gaTitle")
    head.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
    head.addWidget(title, 1)
    elapsed = QLabel("0.0s")
    elapsed.setObjectName("gaIntro")
    head.addWidget(elapsed, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addLayout(head)

    intro = QLabel("Speak your instructions, then hit Done.")
    intro.setObjectName("gaIntro")
    intro.setWordWrap(True)
    lay.addWidget(intro)

    wave = _Waveform()
    lay.addWidget(wave)

    state = {"done": False}

    # Closing by any route (Done, Cancel, Esc, window close) lands in finished();
    # the buttons just accept/reject so a single handler owns recorder teardown.
    def on_finished(result):
        if state["done"]:
            return
        state["done"] = True
        timer.stop()
        accepted = result == QDialog.DialogCode.Accepted.value

        def deliver(out_path):
            def fn():
                if recorder in _active:
                    _active.remove(recorder)
                if accepted:
                    on_done(out_path)
                else:
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
            mw.taskman.run_on_main(fn)

        recorder.stop(deliver)

    dlg.finished.connect(on_finished)

    btns = QHBoxLayout()
    btns.addStretch(1)
    cancel = QPushButton("Cancel")
    cancel.setObjectName("gaBtn")
    cancel.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel.setAutoDefault(False)
    cancel.clicked.connect(dlg.reject)
    done = QPushButton("Done")
    done.setObjectName("gaPrimary")
    done.setCursor(Qt.CursorShape.PointingHandCursor)
    done.setAutoDefault(False)
    done.clicked.connect(dlg.accept)
    btns.addWidget(cancel)
    btns.addWidget(done)
    lay.addLayout(btns)

    dlg.setStyleSheet(dialogs._STYLE % c + _REC_STYLE)

    # --- live amplitude + animation ------------------------------------- #
    read_pos = {"n": 0}

    last_lvl = {"v": 0.0}

    def amplitude():
        buf = getattr(recorder, "_buffer", None)
        fmt = getattr(recorder, "_format", None)
        if buf is None or fmt is None:
            return None  # native recorder: no readable buffer -> decorative
        n = len(buf)
        chunk = bytes(buf[read_pos["n"]:n])
        read_pos["n"] = n
        if not chunk:
            return last_lvl["v"]  # no new audio this frame: hold, don't read 0
        last_lvl["v"] = _rms_level(chunk, _format_kind(fmt))
        return last_lvl["v"]

    start = time.time()
    # Animate every frame (smooth easing) but advance the scroll only every few
    # frames, so the wave drifts across calmly instead of racing. The level fed
    # to each new bar is the loudest reading seen during that slot, so a peak is
    # never missed between pushes.
    PUSH_EVERY = 3
    slot = {"level": 0.0, "frames": 0}
    # Map loudness to bar height by how far it sits ABOVE the noise floor, scaled
    # by the recent dynamic range. The floor tracks the quiet baseline (snaps down
    # to new lows, rises only slowly so speech can't drag it up); the peak tracks
    # recent loudness and decays. Because silence makes (lvl - floor) ~ 0, the
    # bars fall to a thin line regardless of what the peak is doing -- which is the
    # bug the old divide-by-decaying-peak had (it filled in during long silences).
    REST = 0.05          # thin resting line when silent
    MIN_RANGE = 0.008    # smallest speech-vs-quiet span; lower = more sensitive to
                         # quiet/distant speech (but risks amplifying noise)
    GATE = 0.12          # ignore this bottom fraction of the span as residual noise
    SENS = 2.2           # overall sensitivity boost above the gate
    PEAK_DECAY = 0.99    # ~per-frame; loud fades / re-adapts over a couple seconds
    env = {"floor": 0.01, "peak": 0.05}

    def tick():
        lvl = amplitude()
        t = time.time() - start
        if lvl is None:
            # Decorative breathing when we genuinely can't read audio.
            disp = 0.10 + 0.08 * (0.5 + 0.5 * math.sin(t * 3.0))
        else:
            f = env["floor"]
            if lvl < f:
                f = lvl                       # snap down to a new quiet instantly
            else:
                f += (lvl - f) * 0.01         # rise slowly; speech won't pull it up
            env["floor"] = f
            pk = max(lvl, env["peak"] * PEAK_DECAY)
            if pk < f:
                pk = f
            env["peak"] = pk
            span = max(MIN_RANGE, pk - f)
            norm = (lvl - f) / span
            # Soft noise gate: nothing below GATE shows (keeps silence a thin
            # line), then boost the rest so even quiet speech climbs visibly.
            norm = max(0.0, (norm - GATE) / (1.0 - GATE))
            disp = REST + (1.0 - REST) * min(1.0, norm * SENS)
        slot["level"] = max(slot["level"], disp)
        slot["frames"] += 1
        if slot["frames"] >= PUSH_EVERY:
            wave.push(slot["level"])
            slot["level"] = 0.0
            slot["frames"] = 0
        wave.animate()
        elapsed.setText("%.1fs" % recorder.duration())
        # Pulse the record dot.
        on = (int(t * 2) % 2) == 0
        dot.setStyleSheet("color:%s;" % (c["accent"] if on else c["muted"]))

    timer = QTimer(dlg)
    timer.timeout.connect(tick)

    def begin():
        read_pos["n"] = len(getattr(recorder, "_buffer", b"") or b"")
        timer.start(40)

    try:
        recorder.start(begin)
    except Exception as exc:
        timer.stop()
        if recorder in _active:
            _active.remove(recorder)
        try:
            os.remove(path)
        except OSError:
            pass
        showWarning("Couldn't start recording: %s" % exc)
        return

    dialogs._center(dlg, parent)
    done.setFocus()
    dlg.exec()


_REC_STYLE = """
#gaRecDot{ font-size:14px; color:#ff375f; }
"""
