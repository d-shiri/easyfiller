"""Custom Qt dialogs for the add-on.

Anki's stock askUser / showWarning boxes look dated next to our in-editor
overlay, so both the duplicate prompt and error reports are frameless, rounded
cards with a drop shadow. The duplicate prompt highlights the word and shows
each existing deck as a pill. Light/dark aware via Anki's theme manager.

`confirm_duplicate` returns one of "generate" / "view" / "cancel";
`show_error` reports a failure with a single dismiss button.
"""

from aqt.qt import (
    QApplication,
    QColor,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPoint,
    QPushButton,
    QRect,
    QSize,
    Qt,
    QVBoxLayout,
    QWidget,
)
from aqt.theme import theme_manager


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


def show_error(parent, message, title="Something went wrong"):
    """Report a failure in the same styled card as the duplicate prompt.

    `message` is shown selectable so the user can copy the underlying error
    text. Blocks until dismissed.
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
    lay.addSpacing(2)

    btns = QHBoxLayout()
    btns.addStretch(1)
    ok = QPushButton("Dismiss")
    ok.setObjectName("gaPrimary")
    ok.setCursor(Qt.CursorShape.PointingHandCursor)
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    btns.addWidget(ok)
    lay.addLayout(btns)

    dlg.setStyleSheet(_STYLE % c)
    _center(dlg, parent)
    ok.setFocus()
    dlg.exec()


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
    card.setMinimumWidth(340)
    card.setMaximumWidth(460)
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
