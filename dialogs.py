"""Custom Qt dialogs for the add-on.

Anki's stock askUser box looks dated next to our in-editor overlay, so the
duplicate prompt is a frameless, rounded card with a drop shadow, the word
highlighted and each existing deck shown as a pill. Light/dark aware via Anki's
theme manager. Returns True when the user chooses to generate anyway.
"""

from aqt.qt import (
    QColor,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QVBoxLayout,
)
from aqt.theme import theme_manager


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
    night = theme_manager.night_mode
    c = _palette(night)

    dlg = QDialog(parent)
    dlg.setWindowFlags(
        Qt.WindowType.Dialog
        | Qt.WindowType.FramelessWindowHint
    )
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    dlg.setModal(True)

    # Outer layout just provides margin so the drop shadow isn't clipped.
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(24, 24, 24, 24)

    card = QFrame()
    card.setObjectName("gaCard")
    shadow = QGraphicsDropShadowEffect(card)
    shadow.setBlurRadius(40)
    shadow.setOffset(0, 12)
    shadow.setColor(QColor(0, 0, 0, 110))
    card.setGraphicsEffect(shadow)
    outer.addWidget(card)

    lay = QVBoxLayout(card)
    lay.setContentsMargins(28, 26, 28, 22)
    lay.setSpacing(14)

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

    # Deck pills.
    for d in decks:
        pill = QLabel(_esc(d))
        pill.setObjectName("gaPill")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(pill, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch(1)
        lay.addLayout(row)

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

    # Center on parent.
    if parent is not None:
        pg = parent.geometry()
        dlg.adjustSize()
        dlg.move(
            pg.center().x() - dlg.width() // 2,
            pg.center().y() - dlg.height() // 2,
        )

    cancel.setFocus()
    dlg.exec()
    return result["choice"]


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
