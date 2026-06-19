"""Custom Qt dialogs for the add-on.

Anki's stock askUser / showWarning boxes look dated next to our in-editor
overlay, so both the duplicate prompt and error reports are frameless, rounded
cards with a drop shadow. The duplicate prompt highlights the word and shows
each existing deck as a pill. Light/dark aware via Anki's theme manager.

`confirm_duplicate` returns one of "generate" / "view" / "cancel";
`show_error` reports a failure with a single dismiss button.
"""

import os

from aqt.qt import (
    QApplication,
    QColor,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QIcon,
    QLabel,
    QLayout,
    QPainter,
    QPixmap,
    QPlainTextEdit,
    QPoint,
    QPushButton,
    QRect,
    QSize,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
)
from aqt.theme import theme_manager

_COPY_ICON = os.path.join(os.path.dirname(__file__), "assets", "icons", "copy.png")


class _Dialog(QDialog):
    """Frameless modal that the user can still drag by its body.

    Frameless windows have no title bar to grab, so we track a left-button drag
    anywhere on the card (button clicks are consumed by the buttons themselves
    and never reach here).
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None


class _FlowLayout(QLayout):
    """Left-to-right layout that wraps to a new row when it runs out of width.

    Qt ships no flow layout, so this is the canonical minimal implementation
    (from Qt's own examples), used here to wrap the deck pills instead of
    stacking one per row.
    """

    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do_layout(self, rect, test_only):
        x, y = rect.x(), rect.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


class _FlowWidget(QWidget):
    """A QWidget hosting a _FlowLayout that reports height-for-width upward, so
    the surrounding QVBoxLayout reserves the right height for wrapped rows."""

    def __init__(self, spacing=8):
        super().__init__()
        self._flow = _FlowLayout(self, spacing)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, widget):
        self._flow.addWidget(widget)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._flow.heightForWidth(width)

    def resizeEvent(self, event):
        # Pin the widget to the height its rows actually need at the current
        # width; otherwise the layout reserves only one row's worth and the
        # wrapped rows below get clipped. Guarded so it settles without looping.
        super().resizeEvent(event)
        needed = self._flow.heightForWidth(self.width())
        if needed != self.minimumHeight():
            self.setMinimumHeight(needed)


def _tinted_icon(path, color):
    """Load `path` and recolor its opaque pixels to `color`.

    The copy.png is a flat black glyph, invisible on a dark pill -- tinting it to
    the current text color keeps it readable in both themes. Returns an empty
    QIcon if the file is missing so the button just shows its text."""
    pm = QPixmap(path)
    if pm.isNull():
        return QIcon()
    tinted = QPixmap(pm.size())
    tinted.fill(Qt.GlobalColor.transparent)
    p = QPainter(tinted)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(tinted.rect(), QColor(color))
    p.end()
    return QIcon(tinted)


class _CopyButton(QPushButton):
    """A pill/code chip that copies `value` to the clipboard when clicked.

    The copy icon sits to the right of the text (RightToLeft layout), and the
    label flips to a brief "Copied" confirmation that reverts after a moment, so
    the click visibly does something.
    """

    def __init__(self, value, object_name, color):
        super().__init__()
        self._value = value
        self.setObjectName(object_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        self.setFlat(True)
        self.setIcon(_tinted_icon(_COPY_ICON, color))
        self.setIconSize(QSize(13, 13))
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setText(value)
        self.clicked.connect(self._copy)

    def _copy(self):
        QApplication.clipboard().setText(self._value)
        self.setText("Copied")
        QTimer.singleShot(1100, lambda: self.setText(self._value))


def _palette(night):
    if night:
        return {
            "card": "#2c2c30",
            "text": "#f5f5f7",
            "muted": "#a1a1a6",
            "pill_bg": "#3a3a3f",
            "pill_text": "#f5f5f7",
            "accent": "#0a84ff",
            "accent_hover": "#3393ff",
            "btn_bg": "#3a3a3f",
            "btn_hover": "#46464c",
            "word_bg": "rgba(10,132,255,.18)",
        }
    return {
        "card": "#ffffff",
        "text": "#1d1d1f",
        "muted": "#6e6e73",
        "pill_bg": "#eef0f3",
        "pill_text": "#1d1d1f",
        "accent": "#0a84ff",
        "accent_hover": "#3393ff",
        "btn_bg": "#eef0f3",
        "btn_hover": "#e2e4e8",
        "word_bg": "rgba(10,132,255,.12)",
    }


def confirm_duplicate(parent, word, decks, count=1):
    """Ask what to do about `word` already existing in `decks`.

    `count` is the number of duplicate notes (for button pluralization).
    Returns one of: "generate", "view", "cancel".
    """
    c = _palette(theme_manager.night_mode)
    dlg, card, lay = _make_card(parent, c)

    # Header: warning glyph + title.
    head = QHBoxLayout()
    head.setSpacing(12)
    icon = QLabel("⚠️")
    icon.setObjectName("gaIcon")
    title = QLabel("Already in your collection")
    title.setObjectName("gaTitle")
    head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    head.addWidget(title, 1)
    lay.addLayout(head)

    # The word, highlighted.
    intro = QLabel(
        "The word <span style='color:%s; font-weight:700;'>%s</span> "
        "already exists in:" % (c["accent"], _esc(word))
    )
    intro.setObjectName("gaIntro")
    intro.setTextFormat(Qt.TextFormat.RichText)
    intro.setWordWrap(True)
    lay.addWidget(intro)

    # Deck pills, wrapping to new rows instead of stacking one per line.
    pills = _FlowWidget()
    for d in decks:
        pill = QLabel(_esc(d))
        pill.setObjectName("gaPill")
        pills.add(pill)
    lay.addWidget(pills)

    lay.addSpacing(2)

    # Buttons. "See duplicate(s)" sits on the left; Cancel / Generate on the
    # right. The chosen action is stashed in `result` and read after exec().
    result = {"choice": "cancel"}

    def choose(value):
        result["choice"] = value
        dlg.accept()

    btns = QHBoxLayout()
    btns.setSpacing(10)
    view = QPushButton("See duplicate" + ("s" if count > 1 else ""))
    view.setObjectName("gaBtn")
    view.setCursor(Qt.CursorShape.PointingHandCursor)
    view.setAutoDefault(False)
    view.clicked.connect(lambda: choose("view"))
    cancel = QPushButton("Cancel")
    cancel.setObjectName("gaBtn")
    cancel.setCursor(Qt.CursorShape.PointingHandCursor)
    cancel.setDefault(True)
    cancel.clicked.connect(dlg.reject)
    proceed = QPushButton("Generate anyway")
    proceed.setObjectName("gaPrimary")
    proceed.setCursor(Qt.CursorShape.PointingHandCursor)
    proceed.setAutoDefault(False)
    proceed.clicked.connect(lambda: choose("generate"))
    btns.addWidget(view)
    btns.addStretch(1)
    btns.addWidget(cancel)
    btns.addWidget(proceed)
    lay.addLayout(btns)

    dlg.setStyleSheet(_STYLE % c)
    _center(dlg, parent)
    cancel.setFocus()
    dlg.exec()
    return result["choice"]


def show_error(parent, message, title="Something went wrong", hint=None, models=None,
               download=None, recommend=None, details=None):
    """Report a failure in the same styled card as the duplicate prompt.

    `message` is shown selectable so the user can copy the underlying error text.
    `details` is optional long text (e.g. a full CLI traceback) hidden behind a
    "Show details" toggle in a fixed-height scrollable box, so a wall of text
    never stretches the card off-screen. `hint` is an optional shell command
    rendered as a selectable monospace block (e.g. the `ollama pull ...` to fix a
    missing model); `models` is an optional list of available names shown as
    pills. `download` is a model name; when set it adds a primary "Download
    <name>" button. `recommend` is a list of (model, note) tuples rendered as
    accent download chips. Returns the model name the user chose to download, or
    None if dismissed. Blocks until dismissed.
    """
    c = _palette(theme_manager.night_mode)
    dlg, card, lay = _make_card(parent, c)

    head = QHBoxLayout()
    head.setSpacing(12)
    icon = QLabel("⚠️")
    icon.setObjectName("gaIcon")
    head_title = QLabel(_esc(title))
    head_title.setObjectName("gaTitle")
    head_title.setWordWrap(True)
    head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    head.addWidget(head_title, 1)
    lay.addLayout(head)

    body = QLabel(_esc(message))
    body.setObjectName("gaIntro")
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(body)

    if hint:
        lay.addWidget(_CopyButton(hint, "gaCode", c["text"]))

    if details:
        # Long technical output (a CLI traceback) lives behind a toggle in a
        # fixed-height scrollable box; the dialog re-fits each time it opens or
        # closes so it grows only when the user asks to see it.
        toggle = QPushButton("Show details")
        toggle.setObjectName("gaBtn")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setAutoDefault(False)
        box = QPlainTextEdit()
        box.setObjectName("gaDetails")
        box.setReadOnly(True)
        box.setPlainText(details)
        box.setFixedHeight(170)
        box.setVisible(False)

        def _toggle_details():
            shown = not box.isVisible()
            box.setVisible(shown)
            toggle.setText("Hide details" if shown else "Show details")
            _place(dlg, parent)

        toggle.clicked.connect(_toggle_details)
        row = QHBoxLayout()
        row.addWidget(toggle)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(box)

    if models is not None:
        caption = QLabel(
            "Available models (click to copy)" if models else "No models installed"
        )
        caption.setObjectName("gaIntro")
        if models:
            pills = _FlowWidget(spacing=9)
            for m in models:
                pills.add(_CopyButton(m, "gaCopyPill", c["pill_text"]))
            lay.addLayout(_group(caption, pills))
        else:
            lay.addWidget(caption)

    # The chosen download model (None == dismissed); set by any download control.
    result = {"model": None}

    def pick(model):
        result["model"] = model
        dlg.accept()

    if recommend:
        rcap = QLabel("Or download a recommended model:")
        rcap.setObjectName("gaIntro")
        chips = _FlowWidget(spacing=9)
        for model, note in recommend:
            chip = QPushButton("↓  " + model)
            chip.setObjectName("gaDownloadPill")
            chip.setToolTip(note)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setAutoDefault(False)
            chip.clicked.connect(lambda _checked=False, m=model: pick(m))
            chips.add(chip)
        lay.addLayout(_group(rcap, chips))

    lay.addSpacing(6)

    # When a download is offered, Dismiss becomes the secondary button and the
    # download is primary; otherwise Dismiss alone is primary.
    btns = QHBoxLayout()
    btns.addStretch(1)
    ok = QPushButton("Dismiss")
    ok.setObjectName("gaPrimary" if not download else "gaBtn")
    ok.setCursor(Qt.CursorShape.PointingHandCursor)
    ok.setAutoDefault(False)
    ok.clicked.connect(dlg.accept)
    btns.addWidget(ok)
    if download:
        dl = QPushButton("Download " + download)
        dl.setObjectName("gaPrimary")
        dl.setCursor(Qt.CursorShape.PointingHandCursor)
        dl.setDefault(True)
        dl.clicked.connect(lambda: pick(download))
        btns.addWidget(dl)
    lay.addLayout(btns)

    dlg.setStyleSheet(_STYLE % c)
    _center(dlg, parent)
    (dl if download else ok).setFocus()
    dlg.exec()
    return result["model"]


def _make_card(parent, c):
    """Build the shared frameless, shadowed, draggable card shell.

    Returns (dialog, card_frame, card_layout); callers fill the layout, then
    apply `_STYLE` and call `_center`.
    """
    dlg = _Dialog(parent)
    dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    dlg.setModal(True)

    # Outer layout just provides margin so the drop shadow isn't clipped.
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(24, 24, 24, 24)

    card = QFrame()
    card.setObjectName("gaCard")
    card.setMinimumWidth(380)
    card.setMaximumWidth(480)
    shadow = QGraphicsDropShadowEffect(card)
    shadow.setBlurRadius(40)
    shadow.setOffset(0, 12)
    shadow.setColor(QColor(0, 0, 0, 110))
    card.setGraphicsEffect(shadow)
    outer.addWidget(card)

    lay = QVBoxLayout(card)
    lay.setContentsMargins(28, 26, 28, 22)
    lay.setSpacing(14)
    return dlg, card, lay


def _center(dlg, parent):
    """Center on the parent (or its screen), clamped fully on-screen so a
    frameless card never lands half off the display."""
    _place(dlg, parent)
    # Wrapped flow pills only learn their true height once the event loop has
    # laid them out, which can be taller than the pre-show sizeHint. Re-fit once
    # the loop starts (via exec) so the card grows to fit instead of clipping.
    QTimer.singleShot(0, lambda: dlg.isVisible() and _place(dlg, parent))


def _place(dlg, parent):
    dlg.adjustSize()
    screen = (parent.screen() if parent is not None else None) or (
        QApplication.primaryScreen()
    )
    avail = screen.availableGeometry() if screen is not None else None
    if parent is not None:
        center = parent.geometry().center()
    elif avail is not None:
        center = avail.center()
    else:
        return
    x = center.x() - dlg.width() // 2
    y = center.y() - dlg.height() // 2
    if avail is not None:
        x = max(avail.left(), min(x, avail.right() - dlg.width()))
        y = max(avail.top(), min(y, avail.bottom() - dlg.height()))
    dlg.move(x, y)


def _group(caption, content):
    """Stack a caption tight above its content as one visual block, so the gap to
    its own caption is smaller than the layout's gap to the next section."""
    v = QVBoxLayout()
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(7)
    v.addWidget(caption)
    v.addWidget(content)
    return v


def _esc(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


_STYLE = """
#gaCard{
  background:%(card)s;
  border-radius:16px;
}
#gaIcon{ font-size:22px; }
#gaTitle{
  font-size:17px; font-weight:700; color:%(text)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
#gaIntro{
  font-size:13px; color:%(muted)s;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
#gaPill{
  background:%(pill_bg)s; color:%(pill_text)s;
  border-radius:9px; padding:5px 12px; font-size:13px; font-weight:600;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
#gaCode{
  background:%(pill_bg)s; color:%(text)s; text-align:left;
  border:none; border-radius:9px; padding:9px 12px; font-size:12px;
  font-family:'SF Mono','JetBrains Mono',Menlo,Consolas,monospace;
}
#gaCode:hover{ background:%(btn_hover)s; }
#gaDetails{
  background:%(pill_bg)s; color:%(text)s;
  border:none; border-radius:9px; padding:8px 10px; font-size:11px;
  font-family:'SF Mono','JetBrains Mono',Menlo,Consolas,monospace;
}
#gaCopyPill{
  background:%(pill_bg)s; color:%(pill_text)s; text-align:left;
  border:none; border-radius:9px; padding:5px 12px; font-size:13px; font-weight:600;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
#gaCopyPill:hover{ background:%(btn_hover)s; }
#gaDownloadPill{
  background:%(accent)s; color:#ffffff; text-align:left;
  border:none; border-radius:9px; padding:5px 12px; font-size:13px; font-weight:600;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
#gaDownloadPill:hover{ background:%(accent_hover)s; }
#gaBtn, #gaPrimary{
  border:2px solid transparent; border-radius:9px; padding:8px 16px;
  font-size:13px; font-weight:600;
  font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
}
#gaBtn{ background:%(btn_bg)s; color:%(text)s; }
#gaBtn:hover{ background:%(btn_hover)s; }
#gaPrimary{ background:%(accent)s; color:#ffffff; }
#gaPrimary:hover{ background:%(accent_hover)s; }
/* Default/focused button gets a visible ring (transparent border above keeps
   the layout from shifting). */
#gaBtn:focus, #gaBtn:default{ border-color:%(accent)s; background:%(btn_hover)s; }
#gaPrimary:focus, #gaPrimary:default{ border-color:#ffffff; }
"""
