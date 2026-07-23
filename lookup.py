"""Word-lookup popup: a mini dictionary you open before committing a word.

You type a German word, see its English meaning, a couple of example sentences
with translations, and can play the pronunciation of any of them -- plus a
warning if the word already lives in your collection. If you like it, "Add to
Anki" hands everything to Anki's normal Add-note window, pre-filled, so you
review and save there.

This module owns only the UI. Everything that touches the LLM, TTS or the
collection is injected as a callback by __init__.py, so lookup.py never imports
the package root (which would be a circular import) -- it depends only on
dialogs.py (the shared styled-card shell) and Qt.
"""

import os

from aqt import mw
from aqt.qt import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
)
from aqt.sound import av_player
from aqt.theme import theme_manager

from . import dialogs
from .util import invalid_word_reason, strip_html

# Extra styling layered on top of dialogs._STYLE (same %(...)s palette keys).
_EXTRA_STYLE = """
#gaSub{ font-size:12px; color:%(muted)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaRule{ background:%(pill_bg)s; border:none; }
#gaStatus{ font-size:13px; color:%(muted)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaHead{ font-size:21px; font-weight:800; color:%(accent)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaCaption{ font-size:11px; font-weight:700; color:%(muted)s;
  letter-spacing:.6px;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaMeaning{ font-size:15px; color:%(text)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaDe{ font-size:14px; font-weight:600; color:%(text)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaEn{ font-size:13px; color:%(muted)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaExample{ background:%(pill_bg)s; border-radius:12px; }
#gaPlay{ background:%(btn_bg)s; color:%(accent)s; border:2px solid transparent;
  border-radius:10px; font-size:13px; font-weight:800; }
#gaPlay:hover{ background:%(accent)s; color:#ffffff; }
#gaPlay:disabled{ color:%(muted)s; }
#gaDupBar{ background:%(word_bg)s; border-radius:12px; }
#gaDupText{ font-size:13px; color:%(text)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaPrimary:disabled{ background:%(pill_bg)s; color:%(muted)s; }
#gaLink{ background:transparent; color:%(accent)s; border:none; text-align:left;
  font-size:13px; font-weight:700; padding:2px 0;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif; }
#gaLink:hover{ color:%(accent_hover)s; text-decoration:underline; }
"""

# The card is a fixed width, so every wrapped label's real width is known up front.
# Giving each label that exact minimum width makes the layout compute its wrapped
# height correctly (a bare word-wrap QLabel otherwise reports its minimum height as
# if wrapping at ~0 width -- dozens of lines -- which inflates the card and spreads
# the rows apart). Derived from _make_card's 28px content margin.
_CARD_W = 440
_SIDE = 28
_CONTENT_W = _CARD_W - 2 * _SIDE                                # 384
_PLAY_W = 40
_ROW_GAP = 10
_HEAD_TEXT_W = _CONTENT_W - _PLAY_W - _ROW_GAP                  # 334
_EX_PAD = 13
_EX_TEXT_W = _CONTENT_W - 2 * _EX_PAD - _PLAY_W - _ROW_GAP      # 308
_DUP_PAD = 14
_DUP_TEXT_W = _CONTENT_W - 2 * _DUP_PAD                         # 356

# A small, widely-rendered spinner. Braille dots animate smoothly and are present
# in the symbol fonts Anki ships on every platform.
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _Spinner:
    """Cycles a spinner glyph in front of a fixed caption on a QLabel."""

    def __init__(self, label):
        self._label = label
        self._caption = ""
        self._i = 0
        self._timer = QTimer(label)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

    def start(self, caption):
        self._caption = caption
        self._i = 0
        self._tick()
        self._timer.start()

    def _tick(self):
        glyph = _SPINNER[self._i % len(_SPINNER)]
        self._label.setText("%s  %s" % (glyph, self._caption))
        self._i += 1

    def stop(self):
        self._timer.stop()


# The one open popup (None when closed). Keeps the controller alive while the
# non-modal dialog is up, and lets a second Ctrl+Shift+L re-focus the existing
# popup instead of stacking another one.
_open_ctrl = None


def open_lookup_dialog(parent, *, search, find_dupes, synthesize, open_in_browser,
                       add_to_anki, model_label=""):
    """Open the word-lookup popup (non-modal) on `parent`.

    Non-modal because the duplicate banner opens Anki's Browser, which must stay
    clickable (a modal here blocks input to every other Anki window, so the
    Browser couldn't even be closed). If the popup is already open, it is
    re-focused instead of opened twice.

    Callbacks (all supplied by __init__.py):
      search(word) -> data dict {"canonical","meaning","examples":[{"de","en"}]}
                      (runs in a background thread; may raise)
      find_dupes(word) -> (note_ids, sorted_deck_names)   (fast, main thread)
      synthesize(text) -> path to a temp mp3               (background; caller deletes)
      open_in_browser(note_ids) -> None                    (main thread)
      add_to_anki(word, data, audio_by_text) -> None       (main thread)
    `audio_by_text` maps the exact German string that was previewed to the mp3
    path, so Add can reuse already-generated audio; add_to_anki owns those files
    afterwards (adds them to the media store and deletes the temp copies).
    """
    global _open_ctrl
    if _open_ctrl is not None:
        try:
            _open_ctrl.dlg.raise_()
            _open_ctrl.dlg.activateWindow()
            return
        except RuntimeError:  # underlying Qt dialog already deleted
            _open_ctrl = None
    _open_ctrl = _LookupDialog(
        parent, search=search, find_dupes=find_dupes, synthesize=synthesize,
        open_in_browser=open_in_browser, add_to_anki=add_to_anki,
        model_label=model_label,
    )
    _open_ctrl.dlg.show()


class _LookupDialog:
    def __init__(self, parent, *, search, find_dupes, synthesize, open_in_browser,
                 add_to_anki, model_label=""):
        self._search = search
        self._find_dupes = find_dupes
        self._synthesize = synthesize
        self._open_in_browser = open_in_browser
        self._add_to_anki = add_to_anki
        self._parent = parent
        self.pending_add = None
        self._closed = False

        self._word = ""          # the word the current result is for
        self._data = None        # the current result payload
        self._audio = {}         # text -> temp mp3 path (previewed clips)
        self._req = 0            # generation request id (drops stale responses)

        c = dialogs._palette(theme_manager.night_mode)
        self._c = c
        dlg, card, lay = dialogs._make_card(parent, c)
        self.dlg = dlg
        # _make_card defaults to modal; undo that -- the duplicate banner opens
        # Anki's Browser, which must stay usable while this popup is up.
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        lay.setSpacing(13)
        card.setFixedWidth(_CARD_W)
        dlg.finished.connect(lambda _: self._on_finished())
        # If the parent window closes while we're open, the dialog is deleted
        # without `finished` ever firing -- still reclaim the temp clips.
        dlg.destroyed.connect(lambda _=None: self._on_destroyed())

        # Header: icon + (title over model subtitle), tight.
        head = QHBoxLayout()
        head.setSpacing(12)
        glyph = QLabel("\U0001F50D")  # magnifying glass
        glyph.setObjectName("gaIcon")
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("Look up a word")
        title.setObjectName("gaTitle")
        titles.addWidget(title)
        if model_label:
            sub = QLabel(dialogs._esc(model_label))
            sub.setObjectName("gaSub")
            titles.addWidget(sub)
        head.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addLayout(titles, 1)
        lay.addLayout(head)

        # Search row.
        row = QHBoxLayout()
        row.setSpacing(10)
        self.input = QLineEdit()
        self.input.setObjectName("gaInput")
        self.input.setMinimumHeight(38)
        self.input.setPlaceholderText("Type a German word, press Enter…")
        self.input.returnPressed.connect(self._do_lookup)
        self.lookup_btn = QPushButton("Look up")
        self.lookup_btn.setObjectName("gaPrimary")
        self.lookup_btn.setMinimumHeight(38)
        self.lookup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lookup_btn.setAutoDefault(False)
        self.lookup_btn.clicked.connect(self._do_lookup)
        row.addWidget(self.input, 1)
        row.addWidget(self.lookup_btn, 0)
        lay.addLayout(row)

        # Status row: spinner caption while working, plus a Cancel that aborts
        # the in-flight lookup. Both hidden at rest.
        srow = QHBoxLayout()
        srow.setSpacing(10)
        self.status = QLabel("")
        self.status.setObjectName("gaStatus")
        self.status.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("gaBtn")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setAutoDefault(False)
        self.cancel_btn.clicked.connect(self._cancel_lookup)
        self.cancel_btn.hide()
        srow.addWidget(self.status, 1)
        srow.addWidget(self.cancel_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(srow)
        self._spinner = _Spinner(self.status)

        # Duplicate banner (hidden until a duplicate is found).
        self.dup = QFrame()
        self.dup.setObjectName("gaDupBar")
        self.dup_lay = QVBoxLayout(self.dup)
        self.dup_lay.setContentsMargins(14, 11, 14, 12)
        self.dup_lay.setSpacing(8)
        self.dup.hide()
        lay.addWidget(self.dup)

        # Result area (hidden until a lookup returns).
        self.result = QWidget()
        self.result_lay = QVBoxLayout(self.result)
        self.result_lay.setContentsMargins(0, 0, 0, 0)
        self.result_lay.setSpacing(11)
        self.result.hide()
        lay.addWidget(self.result)

        # Footer: divider + buttons.
        rule = QFrame()
        rule.setObjectName("gaRule")
        rule.setFixedHeight(1)
        lay.addWidget(rule)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.add_btn = QPushButton("Add to Anki")
        self.add_btn.setObjectName("gaPrimary")
        self.add_btn.setMinimumHeight(36)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setAutoDefault(False)
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._add)
        close = QPushButton("Close")
        close.setObjectName("gaBtn")
        close.setMinimumHeight(36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAutoDefault(False)
        close.clicked.connect(dlg.reject)
        foot.addStretch(1)
        foot.addWidget(close)
        foot.addWidget(self.add_btn)
        lay.addLayout(foot)

        dlg.setStyleSheet(dialogs._STYLE % c + _EXTRA_STYLE % c)
        dialogs._center(dlg, parent)
        self.input.setFocus()

    # -- lookup ------------------------------------------------------------- #
    def _do_lookup(self):
        word = strip_html(self.input.text()).strip()
        if not word:
            return
        reason = invalid_word_reason(word)
        if reason:
            dialogs.show_error(self.dlg, reason, title="That doesn't look like a word")
            return

        self._reset_results()
        self._req += 1
        req = self._req
        self._set_busy(True, "Looking up “%s”" % word)

        # Duplicate search is a fast indexed query -- run it inline so the banner
        # shows immediately, before the (slower) generation returns.
        try:
            nids, decks = self._find_dupes(word)
        except Exception:
            nids, decks = [], []
        if nids:
            self._show_dup_banner(word, nids, decks)

        def work():
            return self._search(word)

        def done(fut):
            if self._closed or req != self._req:
                return  # dialog closed, or a newer lookup superseded this one
            self._set_busy(False)
            try:
                data = fut.result()
            except Exception as exc:
                dialogs.show_error(
                    self.dlg, "%s" % exc, title="Lookup failed",
                    hint=getattr(exc, "hint", None),
                    models=getattr(exc, "models", None),
                    details=getattr(exc, "details", None),
                )
                return
            self._show_result(word, data)

        mw.taskman.run_in_background(work, done)

    def _cancel_lookup(self):
        """Abort the in-flight lookup.

        The background thread can't be killed, but bumping the request id makes
        its eventual result stale, so `done` drops it on arrival.
        """
        self._req += 1
        # The duplicate banner appears as soon as the lookup starts; a canceled
        # lookup should leave no trace of it either.
        self._reset_results()
        self._set_busy(False)
        self.input.setFocus()

    def _set_busy(self, busy, caption=""):
        self.input.setEnabled(not busy)
        self.lookup_btn.setEnabled(not busy)
        self.lookup_btn.setText("Looking up…" if busy else "Look up")
        self.cancel_btn.setVisible(busy)
        if busy:
            self.status.show()
            self._spinner.start(caption)
        else:
            self._spinner.stop()
            self.status.hide()
        self._grow()

    def _reset_results(self):
        self._data = None
        self._word = ""
        self.add_btn.setEnabled(False)
        _clear_layout(self.dup_lay)
        self.dup.hide()
        _clear_layout(self.result_lay)
        self.result.hide()
        self.cleanup_audio()  # previewed clips belonged to the previous word

    # -- duplicate banner --------------------------------------------------- #
    def _show_dup_banner(self, word, nids, decks):
        line = _text_label(
            "⚠️  Already in your collection as <b>%s</b>" % dialogs._esc(word),
            "gaDupText", _DUP_TEXT_W, rich=True,
        )
        self.dup_lay.addWidget(line)

        pills = dialogs._FlowWidget()
        for d in decks:
            pill = QLabel(dialogs._esc(d))
            pill.setObjectName("gaPill")
            pills.add(pill)
        self.dup_lay.addWidget(pills)

        view = QPushButton(
            "See duplicate" + ("s" if len(nids) > 1 else "") + " in Browser →"
        )
        view.setObjectName("gaLink")
        view.setCursor(Qt.CursorShape.PointingHandCursor)
        view.setAutoDefault(False)
        view.setFlat(True)
        view.clicked.connect(lambda: self._open_in_browser(nids))
        holder = QHBoxLayout()
        holder.addWidget(view)
        holder.addStretch(1)
        self.dup_lay.addLayout(holder)

        self.dup.show()
        self._grow()

    # -- result ------------------------------------------------------------- #
    def _show_result(self, word, data):
        self._word = word
        self._data = data or {}
        _clear_layout(self.result_lay)

        head = (self._data.get("canonical") or word).strip()

        # Headword + a play button for it.
        head_row = QHBoxLayout()
        head_row.setSpacing(_ROW_GAP)
        head_lbl = _text_label(head, "gaHead", _HEAD_TEXT_W)
        head_row.addWidget(head_lbl, 1)
        head_row.addWidget(self._play_button(head), 0, Qt.AlignmentFlag.AlignVCenter)
        self.result_lay.addLayout(head_row)

        meaning = (self._data.get("meaning") or "").strip()
        if meaning:
            self.result_lay.addWidget(_caption("MEANING"))
            self.result_lay.addWidget(_text_label(meaning, "gaMeaning", _CONTENT_W))

        examples = [e for e in (self._data.get("examples") or []) if (e or {}).get("de")]
        if examples:
            self.result_lay.addSpacing(2)
            self.result_lay.addWidget(_caption("EXAMPLES"))
            for ex in examples:
                self.result_lay.addWidget(self._example_card(ex))

        self.add_btn.setEnabled(True)
        self.result.show()
        self._grow()

    def _example_card(self, ex):
        de = (ex.get("de") or "").strip()
        en = (ex.get("en") or "").strip()
        card = QFrame()
        card.setObjectName("gaExample")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(_EX_PAD, 11, _EX_PAD, 11)
        outer.setSpacing(_ROW_GAP)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(_text_label(de, "gaDe", _EX_TEXT_W))
        if en:
            text.addWidget(_text_label(en, "gaEn", _EX_TEXT_W))
        outer.addLayout(text, 1)
        outer.addWidget(self._play_button(de), 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    # -- audio -------------------------------------------------------------- #
    def _play_button(self, text):
        btn = QPushButton("▶")  # play triangle
        btn.setObjectName("gaPlay")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setFixedSize(40, 34)
        btn.clicked.connect(lambda: self._play(text, btn))
        return btn

    def _play(self, text, button):
        text = (text or "").strip()
        if not text:
            return
        path = self._audio.get(text)
        if path and os.path.exists(path):
            av_player.play_file(path)
            return

        button.setEnabled(False)
        button.setText("···")  # busy

        def work():
            return self._synthesize(text)

        def done(fut):
            try:
                path = fut.result()
            except Exception as exc:
                if not self._closed:
                    button.setEnabled(True)
                    button.setText("▶")
                    dialogs.show_error(
                        self.dlg, "Couldn't generate audio: %s" % exc,
                        title="Pronunciation failed",
                        details=getattr(exc, "details", None),
                    )
                return
            if self._closed:
                # Dialog gone while we were synthesizing: don't leak the temp file.
                if path and os.path.exists(path):
                    os.remove(path)
                return
            self._audio[text] = path
            button.setEnabled(True)
            button.setText("▶")
            av_player.play_file(path)

        mw.taskman.run_in_background(work, done)

    def cleanup_audio(self):
        """Delete any previewed temp clips still owned by this dialog."""
        for path in self._audio.values():
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        self._audio = {}

    # -- add / lifecycle ---------------------------------------------------- #
    def _add(self):
        if not self._data:
            return
        # Hand the previewed clips to add_to_anki (it takes ownership) and close;
        # _on_finished performs the add once the dialog has closed.
        self.pending_add = (self._word, self._data, dict(self._audio))
        self._audio = {}  # ownership transferred; don't delete on cleanup
        self.dlg.accept()

    def _on_finished(self):
        global _open_ctrl
        self._closed = True
        self._spinner.stop()
        if _open_ctrl is self:
            _open_ctrl = None
        pending, self.pending_add = self.pending_add, None
        if pending is not None:
            word, data, audio = pending
            # Defer one event-loop turn so the popup is fully gone before
            # Anki's Add window opens on top of where it was.
            QTimer.singleShot(0, lambda: self._add_to_anki(word, data, audio))
        self.cleanup_audio()  # drop any previewed clips the add didn't claim
        self.dlg.deleteLater()

    def _on_destroyed(self):
        # Runs after deleteLater too -- everything here must be idempotent and
        # must not touch Qt objects (they are already gone).
        global _open_ctrl
        self._closed = True
        if _open_ctrl is self:
            _open_ctrl = None
        self.cleanup_audio()

    # -- layout ------------------------------------------------------------- #
    def _grow(self):
        """Resize to fit the current content without moving the window around.

        Re-centering on every lookup made the card jump; instead we keep the
        top-left corner fixed and only nudge the card back onto the screen if the
        new height would push its bottom past the edge.
        """
        pos = self.dlg.pos()
        self.dlg.adjustSize()
        screen = self.dlg.screen()
        avail = screen.availableGeometry() if screen is not None else None
        x, y = pos.x(), pos.y()
        if avail is not None:
            x = max(avail.left(), min(x, avail.right() - self.dlg.width()))
            y = max(avail.top(), min(y, avail.bottom() - self.dlg.height()))
        self.dlg.move(x, y)


def _caption(text):
    lbl = QLabel(text)
    lbl.setObjectName("gaCaption")
    return lbl


def _text_label(text, object_name, width, rich=False):
    lbl = QLabel(text if rich else dialogs._esc(text))
    lbl.setObjectName(object_name)
    lbl.setWordWrap(True)
    if rich:
        lbl.setTextFormat(Qt.TextFormat.RichText)
    else:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    # Pin the minimum width to the real laid-out width so the layout computes the
    # correct wrapped height instead of assuming a near-zero width (see _CARD_W).
    lbl.setMinimumWidth(width)
    lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return lbl


def _clear_layout(layout):
    """Remove and delete every item (widgets and nested layouts) in `layout`."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)
            child.deleteLater()
