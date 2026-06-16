"""Small helpers shared across the add-on (no Anki imports here)."""

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_SOUND_RE = re.compile(r"\[sound:[^\]]+\]")


def strip_html(text):
    """Return plain text: drop [sound:] tags, HTML tags and entities."""
    if not text:
        return ""
    text = _SOUND_RE.sub("", text)
    text = text.replace("&nbsp;", " ").replace("<br>", " ").replace("<br/>", " ")
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return " ".join(text.split()).strip()


def field_index(note, name):
    """Map a field NAME to its index on the note's type, or None."""
    for i, fld in enumerate(note.note_type()["flds"]):
        if fld["name"] == name:
            return i
    return None


def has_audio(raw):
    return "[sound:" in (raw or "")


def audio_tag(raw):
    """Return the [sound:...] tag(s) in `raw`, joined, or ''."""
    return " ".join(_SOUND_RE.findall(raw or ""))
