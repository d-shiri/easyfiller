"""Small helpers shared across the add-on (no Anki imports here)."""

import html
import os
import re
import shutil
import subprocess
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_SOUND_RE = re.compile(r"\[sound:[^\]]+\]")

# --------------------------------------------------------------------------- #
# Cross-platform process / executable helpers                                 #
#                                                                             #
# The add-on shells out to external CLIs (claude, edge-tts). Doing that from a #
# GUI app portably has two gotchas this module hides so the rest of the code  #
# can stay platform-agnostic:                                                 #
#   1. On Windows every subprocess.run on a console app flashes a black        #
#      console window unless launched with CREATE_NO_WINDOW -- intolerable on  #
#      a tool fired on every keypress. run_hidden() adds that flag on Windows  #
#      and is a no-op elsewhere.                                              #
#   2. A bare name like "claude" must also resolve to "claude.exe"/".cmd" on   #
#      Windows, and tools installed by pipx / the native installers live in    #
#      ~/.local/bin (true on all three OSes) which Anki's stripped PATH often  #
#      misses. resolve_executable() handles both.                            #
# --------------------------------------------------------------------------- #
IS_WINDOWS = os.name == "nt"


def run_hidden(cmd, **kwargs):
    """subprocess.run that never flashes a console window on Windows."""
    if IS_WINDOWS:
        # CREATE_NO_WINDOW exists only on Windows; safe to reference here.
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def _exe_candidates(path):
    """Filenames to try for `path`. On Windows a name with no extension also
    matches the .exe / .cmd / .bat the installer actually produced."""
    if not IS_WINDOWS or os.path.splitext(path)[1]:
        return [path]
    return [path + ext for ext in (".exe", ".cmd", ".bat", "")]


def resolve_executable(configured, default, prefer_dirs=("~/.local/bin",)):
    """Best-effort absolute path to an external CLI, portably.

    Resolution order, first hit wins: an absolute `configured` path that exists;
    the tool inside each `prefer_dirs` entry (where pipx and the native installers
    drop it, ahead of PATH so Anki's minimal PATH can't shadow it with a broken
    shim); then a normal PATH lookup. Returns the configured/default name
    unchanged when nothing is found, so the caller still surfaces a clear
    "not found" error. On Windows every step also tries the .exe/.cmd suffixes.
    """
    name = configured or default
    if name and os.path.isabs(name):
        for cand in _exe_candidates(name):
            if os.path.exists(cand):
                return cand
    base = os.path.basename(name or default)
    for d in prefer_dirs:
        for cand in _exe_candidates(os.path.join(os.path.expanduser(d), base)):
            if os.path.exists(cand):
                return cand
    found = shutil.which(name or default)
    if found:
        return found
    return name or default

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
