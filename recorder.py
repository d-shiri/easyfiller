"""A modern, voice-reactive recording dialog for the "Speak" (dictation) button.

We keep Anki's own QtMultimedia recorder for the actual capture -- it handles the
audio format, int16/float conversion, the WAV write and the macOS path, all
cross-platform -- but replace its plain stock dialog with our styled card and a
live orb that swells with the user's voice.

The trick: `QtAudioInputRecorder` accumulates raw samples in a `_buffer` as you
talk, so a ~25 fps timer reads the newly-arrived bytes, computes their RMS level,
and feeds it to the orb. Everything degrades gracefully: if that buffer isn't
readable (a future Anki, or the native macOS recorder), the orb just breathes
instead of reacting; if our recorder can't even be built, the caller falls back
to Anki's stock `record_audio`.

`record_audio(parent, on_done)` mirrors Anki's signature minus the encode flag:
it calls `on_done(wav_path)` once the recording is saved, or never if cancelled.
"""

import array
import math
import os
import platform
import random
import tempfile
import time

from aqt import mw
from aqt.qt import (
    QColor,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPainter,
    QPainterPath,
    QPen,
    QPointF,
    QPushButton,
    QRadialGradient,
    Qt,
    QTimer,
    QWidget,
)
from aqt.theme import theme_manager
from aqt.utils import showWarning

from . import dialogs

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


class _VoiceOrb(QWidget):
    """A glassy blue plasma sphere that reacts to the user's voice.

    `set_level(0..1)` feeds the current loudness; the drawn level eases toward it
    with a fast attack so the orb reacts the instant there's a sound, and a quick
    smooth release so it settles without flicker. At rest it softly breathes.

    The look is a glowing glass bubble: a bright cyan-white shell with electric
    flares rippling around its rim, a dark interior holding a dense central star
    cluster that fades to scattered motes, all wrapped in a soft blue bloom. Any
    sound makes the bloom swell, the shell flares flare up, and the stars surge
    and brighten -- the reaction need not be exact, only alive.
    """

    _EDGE = (190, 235, 255)    # hot cyan-white shell edge
    _RIM = (60, 170, 255)      # shell body blue
    _GLOW = (20, 120, 255)     # outer bloom blue
    _STAR_DIM = (90, 170, 255)
    _STAR_HOT = (170, 225, 255)

    def __init__(self):
        super().__init__()
        self._level = 0.0    # what's drawn (eased)
        self._target = 0.0   # where it's heading (last fed loudness)
        self._phase = 0.0    # time accumulator for drift / twinkle / flares
        self.setMinimumHeight(210)
        # Starfield: many faint motes clustered toward the centre (r biased small)
        # plus a scattered few, each with a twinkle phase/rate. Fixed so it's
        # stable frame to frame.
        rng = random.Random(11)
        self._stars = []
        for _ in range(90):
            self._stars.append({
                "r": rng.random() ** 1.7,                 # bias toward centre
                "a": rng.uniform(0.0, 2.0 * math.pi),
                "sz": rng.uniform(0.5, 1.4) if rng.random() < 0.85
                      else rng.uniform(1.6, 2.6),
                "base": rng.uniform(0.2, 1.0),
                "tw": rng.uniform(0.8, 2.6),
                "ph": rng.uniform(0.0, 2.0 * math.pi),
            })
        # A handful of bright flares that ride around the shell rim, each drifting
        # at its own rate and pulsing on its own clock.
        self._flares = []
        for _ in range(5):
            self._flares.append({
                "a": rng.uniform(0.0, 2.0 * math.pi),
                "spd": rng.uniform(-0.5, 0.5),
                "tw": rng.uniform(0.7, 1.8),
                "ph": rng.uniform(0.0, 2.0 * math.pi),
            })

    @staticmethod
    def _rgba(rgb, alpha):
        c = QColor(rgb[0], rgb[1], rgb[2])
        c.setAlpha(max(0, min(255, int(alpha))))
        return c

    def set_level(self, level):
        self._target = max(0.0, min(1.0, level))

    def animate(self):
        # Fast attack so the orb reacts the moment there's a sound; quick release.
        k = 0.6 if self._target > self._level else 0.28
        self._level += (self._target - self._level) * k
        self._phase += 0.03
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        center = QPointF(cx, cy)
        R = min(w, h) / 2.0
        ph = self._phase

        # Effective energy: a gentle idle breath when silent, the live level when
        # there's sound (whichever is greater), so it's alive but pops on noise.
        idle = 0.10 + 0.05 * (0.5 + 0.5 * math.sin(ph * 1.4))
        e = max(idle, self._level)

        base_r = R * (0.60 + 0.05 * e)                  # glass shell radius
        glow_r = min(R, base_r + R * (0.16 + 0.20 * e)) # bloom swells with sound

        # 1. Outer bloom: a blue halo around the shell, brightening + swelling.
        bloom = QRadialGradient(center, glow_r)
        frac = base_r / glow_r
        bloom.setColorAt(max(0.0, frac - 0.30), self._rgba(self._GLOW, 0))
        bloom.setColorAt(min(1.0, frac), self._rgba(self._GLOW, 70 + 150 * e))
        bloom.setColorAt(1.0, self._rgba(self._GLOW, 0))
        p.setBrush(bloom)
        p.drawEllipse(center, glow_r, glow_r)

        # Interior + stars are clipped to the glass disc.
        p.save()
        clip = QPainterPath()
        clip.addEllipse(center, base_r, base_r)
        p.setClipPath(clip)

        # 2. Dark interior, lifting to a faint blue haze toward the rim.
        inner = QRadialGradient(center, base_r)
        inner.setColorAt(0.0, QColor(2, 8, 22, 238))
        inner.setColorAt(0.68, QColor(4, 16, 46, 230))
        inner.setColorAt(1.0, QColor(18, 70, 150, 210))
        p.setBrush(inner)
        p.drawEllipse(center, base_r * 1.1, base_r * 1.1)

        # 3. Central cluster haze + starfield, blended additively so they read as
        # light. Sound surges them outward a touch and brightens them.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        haze = QRadialGradient(center, base_r * 0.5)
        haze.setColorAt(0.0, self._rgba(self._STAR_HOT, 26 + 50 * e))
        haze.setColorAt(1.0, self._rgba(self._STAR_HOT, 0))
        p.setBrush(haze)
        p.drawEllipse(center, base_r * 0.5, base_r * 0.5)

        surge = 0.92 + 0.10 * e
        for s in self._stars:
            tw = 0.4 + 0.6 * math.sin(ph * s["tw"] + s["ph"])
            bright = s["base"] * (0.3 + 0.7 * tw) * (0.55 + 0.6 * e)
            a = int(max(0.0, min(1.0, bright)) * 255)
            if a <= 0:
                continue
            rr = s["r"] * base_r * surge
            ang = s["a"] + ph * 0.04 * (0.4 + s["r"])   # very slow swirl
            sc = QPointF(cx + rr * math.cos(ang), cy + rr * math.sin(ang))
            sz = s["sz"] * (1.0 + 0.5 * e)
            col = self._STAR_HOT if s["base"] > 0.7 else self._STAR_DIM
            sg = QRadialGradient(sc, sz * 2.6)
            sg.setColorAt(0.0, self._rgba(col, a))
            sg.setColorAt(1.0, self._rgba(col, 0))
            p.setBrush(sg)
            p.drawEllipse(sc, sz * 2.6, sz * 2.6)
            p.setBrush(QColor(235, 248, 255, a))
            p.drawEllipse(sc, sz * 0.5, sz * 0.5)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.restore()

        # 4. Glass shell: stacked strokes from wide+soft to thin+hot give the
        # glossy glowing edge.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, rgb, alpha in (
            (R * 0.060, self._GLOW, 55 + 90 * e),
            (R * 0.022, self._RIM, 130 + 100 * e),
            (R * 0.007, self._EDGE, 205 + 50 * e),
        ):
            pen = QPen(self._rgba(rgb, alpha))
            pen.setWidthF(max(1.0, width))
            p.setPen(pen)
            p.drawEllipse(center, base_r, base_r)
        p.setPen(Qt.PenStyle.NoPen)

        # 5. Electric flares riding the rim: soft cyan-white blobs that drift and
        # pulse, flaring up with sound. Additive so they bloom over the shell.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for f in self._flares:
            pulse = 0.5 + 0.5 * math.sin(ph * f["tw"] + f["ph"])
            bright = (0.18 + 0.85 * e) * pulse
            a = int(max(0.0, min(1.0, bright)) * 255)
            if a <= 0:
                continue
            ang = f["a"] + ph * f["spd"]
            fc = QPointF(cx + base_r * math.cos(ang), cy + base_r * math.sin(ang))
            fr = R * (0.10 + 0.07 * e)
            fg = QRadialGradient(fc, fr)
            fg.setColorAt(0.0, self._rgba(self._EDGE, a))
            fg.setColorAt(1.0, self._rgba(self._EDGE, 0))
            p.setBrush(fg)
            p.drawEllipse(fc, fr, fr)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
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

    orb = _VoiceOrb()
    lay.addWidget(orb)

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
    # Map loudness to the orb's swell by how far it sits ABOVE the noise floor,
    # scaled by the recent dynamic range. The floor tracks the quiet baseline
    # (snaps down to new lows, rises only slowly so speech can't drag it up); the
    # peak tracks recent loudness and decays.
    #
    # The gate is an ABSOLUTE amplitude and the normalising span has a real lower
    # bound. This is what keeps silence at REST: during a long pause the peak
    # decays toward the floor, so (peak - floor) shrinks to ~0. The old code
    # divided by exactly that, clamped only at a tiny 0.008, and gated by a
    # *fraction* of it -- so once the span collapsed below the ambient hiss, the
    # gate became negligible and ordinary background noise was amplified into
    # full-height bars. The orb visibly "came alive" and swelled the longer you
    # stayed quiet. Flooring the span (MIN_SPAN) and gating an absolute amount
    # (NOISE_GATE) means quiet hiss can never span the normaliser.
    REST = 0.05          # small resting swell when silent
    NOISE_GATE = 0.012   # amplitude just above the floor treated as residual noise
    MIN_SPAN = 0.05      # smallest speech-vs-quiet span; floors the normaliser so
                         # ambient hiss can't span it. Lower = more sensitive.
    SENS = 1.7           # sensitivity once past the gate
    PEAK_DECAY = 0.994   # ~per-frame (~40 fps); loud re-adapts over a couple seconds
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
            # Signal above the floor, past a fixed noise allowance, normalised by
            # the recent (floored) dynamic range. The absolute gate keeps silence
            # at REST no matter how far the span has adapted down.
            sig = max(0.0, (lvl - f) - NOISE_GATE)
            span = max(MIN_SPAN, pk - f)
            disp = REST + (1.0 - REST) * min(1.0, sig / span * SENS)
        orb.set_level(disp)
        orb.animate()
        elapsed.setText("%.1fs" % recorder.duration())
        # Pulse the record dot.
        on = (int(t * 2) % 2) == 0
        dot.setStyleSheet("color:%s;" % (c["accent"] if on else c["muted"]))

    timer = QTimer(dlg)
    timer.timeout.connect(tick)

    def begin():
        read_pos["n"] = len(getattr(recorder, "_buffer", b"") or b"")
        timer.start(25)   # ~40 fps: smoother motion + faster reaction to speech

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
