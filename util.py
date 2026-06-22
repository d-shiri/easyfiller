"""Small helpers shared across the add-on (no Anki imports here)."""

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_SOUND_RE = re.compile(r"\[sound:[^\]]+\]")

# Characters allowed in a headword/phrase besides letters: the separators that
# join or surround words (spaces, hyphens, apostrophes, a trailing period for
# abbreviations like "z.B.").
_WORD_SEPARATORS = set(" -‐‑‒–—'’‘´`.")


def _is_vowel(ch):
    # Compare on the base letter so accented vowels (é, à, ä) still count.
    base = unicodedata.normalize("NFD", ch)[0].lower()
    return base in "aeiouy"


def invalid_word_reason(word):
    """Return a short reason if `word` is clearly not a real word, else None.

    Catches keyboard mash and junk like "___dsd" or "43434_$3rf" before we spend
    a generation on it. A real German headword is letters (with umlauts/ß),
    optionally joined by hyphens/spaces/apostrophes, and always has a vowel;
    digits and symbols ($, _, #, …) are never part of a word, so we reject them
    on sight. Empty input returns None -- that's handled separately as
    "type the word first", not as invalid."""
    text = (word or "").strip()
    if not text:
        return None
    has_letter = has_vowel = False
    for ch in text:
        if ch.isalpha():
            has_letter = True
            has_vowel = has_vowel or _is_vowel(ch)
        elif ch.isdigit():
            return "“%s” isn't a word -- it has numbers in it." % text
        elif ch not in _WORD_SEPARATORS:
            return (
                "“%s” isn't a word -- it has characters that don't "
                "belong in one." % text
            )
    if not has_letter:
        return "“%s” isn't a word." % text
    if not has_vowel:
        return "“%s” doesn't look like a word -- it has no vowels." % text
    return None


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
