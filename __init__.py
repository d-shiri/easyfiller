"""EasyFiller: fill meaning + examples with Claude, add edge-tts pronunciation.

Adds four editor buttons and four shortcuts (generate / pronounce / both / clear).
"""

import os

from aqt import gui_hooks, mw
from aqt.utils import tooltip

from . import claude_client
from . import dialogs
from . import overlay
from . import tts as tts_module
from .util import audio_tag, field_index, strip_html


def get_config():
    return mw.addonManager.getConfig(__name__) or {}


def _find_duplicates(word, note, config):
    """Find notes (excluding this one) that already contain `word`.

    Exact match on the source field, plus a word-boundary fallback across all
    fields to catch the headword on note types that name the field differently.
    Backed by Anki's indexed search -- milliseconds even on large collections.

    Returns (note_ids, sorted_deck_names).
    """
    if not word:
        return [], []
    src = config.get("source_field", "Back")
    safe = word.replace('"', "")
    query = '("{f}:{w}" OR w:"{w}")'.format(f=src, w=safe)
    nids = [nid for nid in mw.col.find_notes(query) if nid != note.id]
    decks = set()
    for nid in nids:
        for cid in mw.col.card_ids_of_note(nid):
            decks.add(mw.col.decks.name(mw.col.get_card(cid).did))
    return nids, sorted(decks)


def _open_in_browser(nids):
    """Open Anki's Browse window filtered to the given notes."""
    from aqt import dialogs as aqt_dialogs

    browser = aqt_dialogs.open("Browser", mw)
    browser.search_for("nid:" + ",".join(str(n) for n in nids))
    browser.activateWindow()
    browser.raise_()


def _lemma_dup_ok(editor, data, typed_word, precheck_nids, config):
    """Re-check duplicates against the canonical lemma after generation.

    The pre-check only saw what you typed (e.g. "studiert"); once Claude returns
    the lemma ("studieren") we re-check, prompting only for notes the pre-check
    didn't already surface. Returns True to proceed with applying fields, False
    to abort (the caller hides the overlay). Opens the browser for "view".
    """
    if not config.get("normalize_word", True):
        return True
    canonical = (data or {}).get("canonical", "").strip()
    if not canonical or canonical.lower() == (typed_word or "").lower():
        return True
    nids, decks = _find_duplicates(canonical, editor.note, config)
    if not any(n not in precheck_nids for n in nids):
        return True
    choice = dialogs.confirm_duplicate(
        editor.parentWindow, canonical, decks, len(nids)
    )
    if choice == "view":
        _open_in_browser(nids)
        return False
    return choice == "generate"


# --------------------------------------------------------------------------- #
# Generate (Claude)                                                           #
# --------------------------------------------------------------------------- #
def _generate_async(editor, then=None):
    config = get_config()
    note = editor.note
    source = config.get("source_field", "Back")
    sidx = field_index(note, source)
    if sidx is None:
        dialogs.show_error(
            editor.parentWindow,
            "Source field '%s' not found on this note type." % source,
            title="Can't generate",
        )
        return
    word = strip_html(note.fields[sidx])

    de_fields = config.get("example_fields", [])
    en_fields = config.get("translation_fields", [])

    # German sentences already on the card whose paired translation is empty:
    # translate those in place instead of leaving them blank. Keyed by the
    # example-field index so we can write back to the matching translation field.
    to_translate = {}
    avoid = []
    for i, fname in enumerate(de_fields):
        idx = field_index(note, fname)
        if idx is None:
            continue
        existing = strip_html(note.fields[idx])
        if not existing:
            continue
        # Sentences already on the card, so Claude writes new ones instead of
        # repeating its canonical example for this word.
        avoid.append(existing)
        if i < len(en_fields):
            en_idx = field_index(note, en_fields[i])
            if en_idx is not None and not strip_html(note.fields[en_idx]):
                to_translate[i] = existing

    if not word and not to_translate:
        dialogs.show_error(
            editor.parentWindow,
            "The '%s' field is empty -- type the word first." % source,
            title="Nothing to generate",
        )
        return

    # Warn (but don't block) if this word already lives in another deck. We keep
    # the matched note ids so the post-generation lemma re-check (see on_done)
    # only re-prompts for notes the user hasn't already been shown here.
    nids, dupes = _find_duplicates(word, note, config)
    precheck_nids = set(nids)
    if nids:
        choice = dialogs.confirm_duplicate(
            editor.parentWindow, word, dupes, len(nids)
        )
        if choice == "view":
            _open_in_browser(nids)
            return
        if choice != "generate":
            return

    keys = list(to_translate.keys())
    sentences = [to_translate[k] for k in keys]

    # Build the step checklist for this run: only the stages we'll actually do.
    steps = []
    if word:
        steps.append(("gen", "Generating examples & meaning", "active"))
    if sentences:
        n = len(sentences)
        label = "Translating existing sentence" + ("s" if n > 1 else "")
        steps.append(("translate", label, "active" if not word else "pending"))
    if then is not None:  # the "both" flow will run pronunciation after this
        steps.append(("tts", "Adding pronunciation", "pending"))
    overlay.start(editor, steps)

    def on_main(fn):
        mw.taskman.run_on_main(fn)

    def work():
        data = None
        if word:
            data = claude_client.generate(word, config, avoid=avoid)
            on_main(lambda: overlay.set_step(editor, "gen", state="done"))
            if sentences:
                on_main(lambda: overlay.set_step(editor, "translate", state="active"))
        translations = {}
        if sentences:
            translations = dict(zip(keys, claude_client.translate(sentences, config)))
            on_main(lambda: overlay.set_step(editor, "translate", state="done"))
        return data, translations

    # Use taskman (not QueryOp) so Anki doesn't pop its own "Processing…" dialog
    # on top of our overlay -- our overlay is the only progress indicator.
    def on_done(future):
        try:
            data, translations = future.result()
        except Exception as exc:
            overlay.hide(editor)
            dialogs.show_error(
                editor.parentWindow, "Claude failed: %s" % exc,
                title="Generation failed",
            )
            return
        if not _lemma_dup_ok(editor, data, word, precheck_nids, config):
            overlay.hide(editor)
            return
        _apply_generated(editor, data, translations, config)
        if then:
            then()  # next stage (pronounce) keeps the overlay up and hides it
        else:
            overlay.hide(editor)

    mw.taskman.run_in_background(work, on_done)


def _apply_generated(editor, data, translations, config):
    note = editor.note
    changed = False
    de_fields = config.get("example_fields", [])
    en_fields = config.get("translation_fields", [])

    if data:
        # Rewrite the source field to the dictionary citation form (e.g.
        # "herausforderung" -> "die Herausforderung", "studiert" -> "studieren").
        # This is the one field we intentionally OVERWRITE; any existing
        # [sound:...] tag on it is preserved.
        canonical = data.get("canonical", "").strip()
        if config.get("normalize_word", True) and canonical:
            sidx = field_index(note, config.get("source_field", "Back"))
            if sidx is not None and canonical != strip_html(note.fields[sidx]):
                tag = audio_tag(note.fields[sidx])
                note.fields[sidx] = canonical + (" " + tag if tag else "")
                changed = True

        # English meaning/gloss of the word: fill only if empty.
        midx = field_index(note, config.get("meaning_field", "Front"))
        meaning = data.get("meaning", "")
        if midx is not None and meaning and not strip_html(note.fields[midx]):
            note.fields[midx] = meaning
            changed = True

        # A generated German sentence and its English translation are a pair.
        # Only fill an EMPTY German field -- and when we do, OVERWRITE the paired
        # translation, since the old one belonged to a different sentence and is
        # now stale. If the German field already has text, leave both alone.
        for i, ex in enumerate(data.get("examples", [])):
            if i >= len(de_fields):
                break
            de_idx = field_index(note, de_fields[i])
            de = ex.get("de", "")
            if de_idx is None or not de or strip_html(note.fields[de_idx]):
                continue
            note.fields[de_idx] = de
            changed = True
            if i < len(en_fields):
                en_idx = field_index(note, en_fields[i])
                en = ex.get("en", "")
                if en_idx is not None and en:
                    note.fields[en_idx] = en  # override stale translation

    # Translations of German sentences that were already on the card: fill the
    # empty paired translation field without touching the German.
    for i, en in translations.items():
        if not en or i >= len(en_fields):
            continue
        en_idx = field_index(note, en_fields[i])
        if en_idx is None or strip_html(note.fields[en_idx]):
            continue
        note.fields[en_idx] = en
        changed = True

    if changed:
        editor.set_note(note)
        tooltip("Filled empty fields from Claude.")
    else:
        tooltip("Nothing to fill (fields already populated).")


# --------------------------------------------------------------------------- #
# Editor actions (save the note first so we read the latest text)             #
# --------------------------------------------------------------------------- #
def on_generate(editor):
    editor.call_after_note_saved(lambda: _generate_async(editor))


def on_pronounce(editor):
    editor.call_after_note_saved(lambda: tts_module.pronounce(editor, get_config()))


def on_clear(editor):
    """Empty every field on the current note so you can start a new word.

    Anki's own undo (Ctrl+Z in the editor) reverses this, so we clear without a
    confirmation prompt to keep the button a single press.
    """
    note = editor.note
    if note is None:
        return
    if not any(strip_html(f) for f in note.fields):
        tooltip("Fields are already empty.")
        return
    for i in range(len(note.fields)):
        note.fields[i] = ""
    editor.set_note(note)
    editor.web.setFocus()
    tooltip("Cleared all fields.")


def on_both(editor):
    editor.call_after_note_saved(
        lambda: _generate_async(
            editor, then=lambda: tts_module.pronounce(editor, get_config())
        )
    )


# --------------------------------------------------------------------------- #
# Hooks                                                                        #
# --------------------------------------------------------------------------- #
_ICON_DIR = os.path.join(os.path.dirname(__file__), "assets", "icons")


def _icon(name):
    """Absolute path to an editor-button icon (Anki inlines it as a data URI)."""
    return os.path.join(_ICON_DIR, name + ".png")


def _add_buttons(buttons, editor):
    config = get_config()
    buttons.append(
        editor.addButton(
            _icon("generate"),
            "de_generate",
            lambda ed: on_generate(ed),
            tip="Generate meaning + examples (Claude) — %s"
            % config.get("shortcut_generate", "Ctrl+Shift+G"),
        )
    )
    buttons.append(
        editor.addButton(
            _icon("pronounce"),
            "de_pronounce",
            lambda ed: on_pronounce(ed),
            tip="Add pronunciation (edge-tts) — %s"
            % config.get("shortcut_pronounce", "Ctrl+Shift+P"),
        )
    )
    buttons.append(
        editor.addButton(
            _icon("both"),
            "de_both",
            lambda ed: on_both(ed),
            tip="Generate, then pronounce — %s"
            % config.get("shortcut_both", "Ctrl+Shift+B"),
        )
    )
    buttons.append(
        editor.addButton(
            _icon("clear"),
            "de_clear",
            lambda ed: on_clear(ed),
            tip="Clear all fields — %s"
            % config.get("shortcut_clear", "Ctrl+Shift+X"),
        )
    )
    return buttons


def _add_shortcuts(shortcuts, editor):
    config = get_config()
    shortcuts.append(
        (config.get("shortcut_generate", "Ctrl+Shift+G"), lambda: on_generate(editor), True)
    )
    shortcuts.append(
        (config.get("shortcut_pronounce", "Ctrl+Shift+P"), lambda: on_pronounce(editor), True)
    )
    shortcuts.append(
        (config.get("shortcut_both", "Ctrl+Shift+B"), lambda: on_both(editor), True)
    )
    shortcuts.append(
        (config.get("shortcut_clear", "Ctrl+Shift+X"), lambda: on_clear(editor), True)
    )


gui_hooks.editor_did_init_buttons.append(_add_buttons)
gui_hooks.editor_did_init_shortcuts.append(_add_shortcuts)
