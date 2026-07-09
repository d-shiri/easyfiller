"""EasyFiller: fill meaning + examples with Claude, add edge-tts pronunciation.

Adds five editor buttons and five shortcuts (generate / regenerate / pronounce /
both / clear).
"""

import concurrent.futures
import os

from aqt import gui_hooks, mw
from aqt.utils import tooltip

from . import llm_client
from . import dialogs
from . import diagnostics
from . import lookup
from . import overlay
from . import recorder
from . import stt
from . import tts as tts_module
from .util import audio_tag, field_index, invalid_word_reason, strip_html


def get_config():
    return mw.addonManager.getConfig(__name__) or {}


def _find_duplicates(word, config, exclude_nid=None):
    """Find notes that already contain `word`.

    Exact match on the source field, plus a word-boundary fallback across all
    fields to catch the headword on note types that name the field differently.
    Backed by Anki's indexed search -- milliseconds even on large collections.
    `exclude_nid` drops one note id from the results (the note being edited); the
    lookup popup, which has no active note, leaves it None.

    Returns (note_ids, sorted_deck_names).
    """
    if not word:
        return [], []
    src = config.get("source_field", "Back")
    safe = word.replace('"', "")
    query = '("{f}:{w}" OR w:"{w}")'.format(f=src, w=safe)
    nids = [nid for nid in mw.col.find_notes(query) if nid != exclude_nid]
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


def _clean_canonical(canonical, typed_word):
    """Return `canonical` only if it plausibly is the citation form of `typed_word`.

    Weak local models sometimes return a grammar note ("n. with def. art.",
    "noun, adjective") instead of the lemma. Writing that into the source field
    would destroy the user's word, so we reject anything that is too long to be a
    citation form or shares no stem with what was typed -- callers then leave the
    field as-is. Returns "" when the canonical can't be trusted.
    """
    canonical = (canonical or "").strip()
    if not canonical or len(canonical.split()) > 3:
        return ""  # more than article + word(s): almost certainly a description
    typed = (typed_word or "").strip().lower()
    if not typed:
        return canonical
    typed_cons = _consonant_skeleton(typed)
    for tok in canonical.lower().split():
        if tok in ("der", "die", "das"):
            continue  # ignore the article we may have added
        if typed in tok or tok in typed:
            return canonical
        shared = 0
        for a, b in zip(typed, tok):
            if a != b:
                break
            shared += 1
        if shared >= 4:  # shared stem handles inflections (studiert -> studieren)
            return canonical
        # Strong verbs change the stem VOWEL (wirbt -> werben, laeuft -> laufen,
        # gab -> geben), so a shared letter prefix is too short. Ablaut leaves the
        # consonants intact, so compare consonant skeletons -- a matching prefix of
        # 2+ consonants still rejects grammar-note garbage ("noun", "verb").
        tok_cons = _consonant_skeleton(tok)
        cons_shared = 0
        for a, b in zip(typed_cons, tok_cons):
            if a != b:
                break
            cons_shared += 1
        if cons_shared >= 2:
            return canonical
    return ""


def _consonant_skeleton(word):
    """`word` with vowels removed, for matching across German stem-vowel changes."""
    return "".join(c for c in word if c not in "aeiouäöü")


def _lemma_dup_ok(editor, data, typed_word, precheck_nids, config):
    """Re-check duplicates against the canonical lemma after generation.

    The pre-check only saw what you typed (e.g. "studiert"); once Claude returns
    the lemma ("studieren") we re-check, prompting only for notes the pre-check
    didn't already surface. Returns True to proceed with applying fields, False
    to abort (the caller hides the overlay). Opens the browser for "view".
    """
    if not config.get("normalize_word", True):
        return True
    canonical = _clean_canonical((data or {}).get("canonical", ""), typed_word)
    if not canonical or canonical.lower() == (typed_word or "").lower():
        return True
    nids, decks = _find_duplicates(canonical, config, exclude_nid=editor.note.id)
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
def _download_model(editor, model, on_ready):
    """Pull an Ollama model in the background, showing a progress bar.

    On success runs `on_ready()` (which retries generation); on failure shows the
    error dialog. The pull streams byte progress, so the bar is determinate during
    the blob downloads and indeterminate (sweeping) during manifest/verify stages.
    """
    config = get_config()
    overlay.start(
        editor,
        [("pull", "Downloading %s" % model, "active")],
        caption=llm_client.active_model(config),
    )
    overlay.set_progress(editor, -1)

    def on_main(fn):
        mw.taskman.run_on_main(fn)

    def report(completed, total, status):
        if total and completed is not None:
            pct = max(0, min(100, int(completed * 100 / total)))
            label = "Downloading %s — %d%%" % (model, pct)
            on_main(lambda: (overlay.set_progress(editor, pct),
                             overlay.set_step(editor, "pull", label=label)))
        else:
            label = (status or "Downloading %s" % model).capitalize()
            on_main(lambda: (overlay.set_progress(editor, -1),
                             overlay.set_step(editor, "pull", label=label)))

    def work():
        llm_client.pull_model(model, config, on_progress=report)

    def on_done(future):
        try:
            future.result()
        except Exception as exc:
            overlay.hide(editor)
            dialogs.show_error(editor.parentWindow, "%s" % exc, title="Download failed")
            return
        # Make the just-downloaded model the active one, so the retry (and future
        # generations) use it even when it differs from what was configured.
        cfg = get_config()
        cfg["ollama_model"] = model
        mw.addonManager.writeConfig(__name__, cfg)
        overlay.set_progress(editor, None)
        overlay.set_step(editor, "pull", label="Downloaded %s" % model, state="done")
        on_ready()

    mw.taskman.run_in_background(work, on_done)


def _generate_async(editor, then=None, regenerate=False, instruction=None, level=None):
    """Generate (or regenerate) meaning + examples for the note's word.

    In the default (fill) mode only empty fields are written. In `regenerate`
    mode the example and translation fields are OVERWRITTEN with fresh sentences
    (meaning and the normalized word are left untouched), and `instruction` --
    optional free text from the user -- steers the new examples. The current
    sentences are passed to the model as things to avoid, so a blank instruction
    still yields different examples. `level` is an optional per-run CEFR override
    ("A1".."C2", or None to use the configured `cefr_level`).
    """
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

    # Validate the typed word up front: reject junk like "___dsd" or "43434_$3rf"
    # immediately instead of spinning up a generation on it.
    reason = invalid_word_reason(word)
    if reason:
        dialogs.show_error(
            editor.parentWindow, reason, title="That doesn't look like a word"
        )
        return

    de_fields = config.get("example_fields", [])
    en_fields = config.get("translation_fields", [])

    # German sentences already on the card whose paired translation is empty:
    # translate those in place instead of leaving them blank. Keyed by the
    # example-field index so we can write back to the matching translation field.
    to_translate = {}
    avoid = []
    # Existing example text per example-field position, blank where the field is
    # empty or missing. Unlike `avoid` (filled sentences only) this stays aligned
    # with `de_fields`, so a positional instruction ("change the first sentence")
    # maps to the same index the write-back uses.
    current = []
    for i, fname in enumerate(de_fields):
        idx = field_index(note, fname)
        existing = strip_html(note.fields[idx]) if idx is not None else ""
        current.append(existing)
        if not existing:
            continue
        # Sentences already on the card, so Claude writes new ones instead of
        # repeating its canonical example for this word.
        avoid.append(existing)
        # In regenerate mode we replace these sentences wholesale, so there's
        # nothing to translate-in-place -- the fresh examples carry their own
        # translations.
        if not regenerate and i < len(en_fields):
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
    # only re-prompts for notes the user hasn't already been shown here. Skipped
    # when regenerating: it's the same card, the word is already on it, and the
    # user only wants different example sentences.
    precheck_nids = set()
    if not regenerate:
        nids, dupes = _find_duplicates(word, config, exclude_nid=note.id)
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
        gen_label = "Regenerating examples" if regenerate else "Generating examples & meaning"
        steps.append(("gen", gen_label, "active"))
    if sentences:
        n = len(sentences)
        label = "Translating existing sentence" + ("s" if n > 1 else "")
        # Runs concurrently with generation (independent LLM calls), so it's
        # active from the start rather than waiting for "gen" to finish.
        steps.append(("translate", label, "active"))
    if then is not None:  # the "both" flow will run pronunciation after this
        steps.append(("tts", "Adding pronunciation", "pending"))
    token = overlay.start(
        editor, steps, caption=llm_client.active_model(config), cancelable=True
    )

    def on_main(fn):
        mw.taskman.run_on_main(fn)

    def work():
        # Generation and translation are independent LLM calls; when the card
        # needs both (a word to generate AND pre-typed sentences to translate),
        # run them concurrently so the total wait is max(gen, translate) instead
        # of their sum. A single task just runs inline.
        if token.cancelled:
            return None, {}
        result = {"data": None, "translations": {}}

        def do_generate():
            # In regenerate mode, hand the current sentences to the model (as
            # `current`) so a targeted instruction like "only change the first
            # sentence" can keep the others verbatim. In fill mode they're just
            # things to avoid repeating.
            result["data"] = llm_client.generate(
                word, config, avoid=avoid, instruction=instruction,
                current=current if regenerate else None, level=level,
            )
            on_main(lambda: overlay.set_step(editor, "gen", state="done"))

        def do_translate():
            result["translations"] = dict(zip(keys, llm_client.translate(sentences, config)))
            on_main(lambda: overlay.set_step(editor, "translate", state="done"))

        tasks = []
        if word:
            tasks.append(do_generate)
        if sentences:
            tasks.append(do_translate)
        if len(tasks) == 1:
            tasks[0]()
        elif tasks:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as ex:
                futures = [ex.submit(t) for t in tasks]
                for fut in concurrent.futures.as_completed(futures):
                    fut.result()  # surface the first error (the other is awaited on exit)
        return result["data"], result["translations"]

    # Use taskman (not QueryOp) so Anki doesn't pop its own "Processing…" dialog
    # on top of our overlay -- our overlay is the only progress indicator.
    def on_done(future):
        if token.cancelled:
            return  # user cancelled; the Cancel handler already hid the overlay
        try:
            data, translations = future.result()
        except Exception as exc:
            overlay.hide(editor)
            pull = getattr(exc, "pull", None)
            # Offer the recommended models too (minus the one already configured),
            # so the user can grab a good model without knowing its name.
            recommend = (
                [(m, n) for m, n in llm_client.RECOMMENDED_MODELS if m != pull]
                if pull else None
            )
            chosen = dialogs.show_error(
                editor.parentWindow, "%s" % exc,
                title="Generation failed",
                hint=getattr(exc, "hint", None),
                models=getattr(exc, "models", None),
                download=pull,
                recommend=recommend,
            )
            if chosen:
                # Pull the model with a progress bar, then retry the whole flow
                # (re-reading the note) so the just-downloaded model is used.
                _download_model(
                    editor,
                    chosen,
                    lambda: _generate_async(editor, then, regenerate, instruction),
                )
            return
        # The lemma re-check only matters in fill mode; regenerate never rewrites
        # the word, so there's no new canonical form to re-check against.
        if not regenerate and not _lemma_dup_ok(editor, data, word, precheck_nids, config):
            overlay.hide(editor)
            return
        _apply_generated(editor, data, translations, config, regenerate=regenerate)
        if then:
            then()  # next stage (pronounce) keeps the overlay up and hides it
        else:
            overlay.hide(editor)

    mw.taskman.run_in_background(work, on_done)


def _apply_generated(editor, data, translations, config, regenerate=False):
    note = editor.note
    changed = False
    de_fields = config.get("example_fields", [])
    en_fields = config.get("translation_fields", [])

    # Regenerate replaces only the example/translation pairs; it must not touch
    # the already-correct word or meaning, so the normalize + meaning steps are
    # fill-mode only.
    if data and not regenerate:
        # Rewrite the source field to the dictionary citation form (e.g.
        # "herausforderung" -> "die Herausforderung", "studiert" -> "studieren").
        # This is the one field we intentionally OVERWRITE; any existing
        # [sound:...] tag on it is preserved.
        if config.get("normalize_word", True):
            sidx = field_index(note, config.get("source_field", "Back"))
            if sidx is not None:
                typed = strip_html(note.fields[sidx])
                # Guard against garbage lemmas so we never clobber the typed word.
                canonical = _clean_canonical(data.get("canonical", ""), typed)
                if canonical and canonical != typed:
                    tag = audio_tag(note.fields[sidx])
                    note.fields[sidx] = canonical + (" " + tag if tag else "")
                    changed = True

        # English meaning/gloss of the word: fill only if empty.
        midx = field_index(note, config.get("meaning_field", "Front"))
        meaning = data.get("meaning", "")
        if midx is not None and meaning and not strip_html(note.fields[midx]):
            note.fields[midx] = meaning
            changed = True

    if data:
        # A generated German sentence and its English translation are a pair. In
        # fill mode we only write an EMPTY German field; in regenerate mode we
        # OVERWRITE whatever is there. Either way, when we set the German we also
        # set the paired translation, since the old one is now stale.
        for i, ex in enumerate(data.get("examples", [])):
            if i >= len(de_fields):
                break
            de_idx = field_index(note, de_fields[i])
            de = ex.get("de", "")
            if de_idx is None or not de:
                continue
            if not regenerate and strip_html(note.fields[de_idx]):
                continue  # fill mode: leave an already-filled German field alone
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
        tooltip("Regenerated examples." if regenerate else "Filled empty fields from Claude.")
    else:
        tooltip("Nothing to fill (fields already populated).")


# --------------------------------------------------------------------------- #
# Editor actions (save the note first so we read the latest text)             #
# --------------------------------------------------------------------------- #
def on_generate(editor):
    editor.call_after_note_saved(lambda: _generate_async(editor))


def _dictate(parent, set_state, insert):
    """Record a voice clip and transcribe it into the regenerate box.

    Capture uses our animated, voice-reactive recorder (recorder.py, which wraps
    Anki's own cross-platform audio recorder); transcription runs in the
    background (shelling out to the whisper CLI) so the UI stays responsive. The
    "Speak" button shows "Transcribing…" while it works, and any failure -- most
    commonly the CLI not being installed -- surfaces in the styled error dialog
    with the install command to copy.
    """
    config = get_config()
    if not stt.available(config):
        err = stt.missing_cli_error()
        dialogs.show_error(
            parent, str(err), title="Voice input needs a one-time setup", hint=err.hint
        )
        return

    def on_recorded(path):
        set_state("Transcribing…", True)

        def work():
            try:
                return stt.transcribe(path, config)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        def on_done(future):
            set_state("", False)
            try:
                text = future.result()
            except stt.STTError as exc:
                dialogs.show_error(
                    parent, str(exc), title="Couldn't transcribe",
                    hint=getattr(exc, "hint", None),
                )
                return
            except Exception as exc:
                dialogs.show_error(parent, "%s" % exc, title="Couldn't transcribe")
                return
            insert(text)

        mw.taskman.run_in_background(work, on_done)

    # Our animated recorder first; if it can't start (no input device, missing
    # QtMultimedia), fall back to Anki's stock recording dialog.
    try:
        recorder.record_audio(parent, on_recorded)
    except Exception:
        from aqt.sound import record_audio as anki_record_audio

        anki_record_audio(parent, mw, False, on_recorded)


def _regenerate_blocked(editor, note, config):
    """Show an error and return True if there's nothing to regenerate from.

    Regenerate needs a word in the source field, so we check that up front -- the
    same conditions _generate_async would reject -- to avoid opening the prompt
    (and letting the user dictate) only to fail afterwards. _generate_async stays
    the authoritative backstop for the generation itself.
    """
    source = config.get("source_field", "Back")
    sidx = field_index(note, source)
    if sidx is None:
        dialogs.show_error(
            editor.parentWindow,
            "Source field '%s' not found on this note type." % source,
            title="Can't generate",
        )
        return True
    word = strip_html(note.fields[sidx])
    if not word:
        dialogs.show_error(
            editor.parentWindow,
            "The '%s' field is empty -- type the word first." % source,
            title="Nothing to generate",
        )
        return True
    reason = invalid_word_reason(word)
    if reason:
        dialogs.show_error(
            editor.parentWindow, reason, title="That doesn't look like a word"
        )
        return True
    return False


def on_regenerate(editor):
    """Ask for optional guidance, then overwrite the examples with fresh ones.

    We save the note first and check the source field before showing anything: if
    there's no word to regenerate from, the "Nothing to generate" error appears
    straight away instead of after the prompt. Otherwise the prompt is shown; a
    cancel leaves the note untouched, and an empty instruction is valid (it just
    means "different examples"), so we only abort on None (the dialog was
    dismissed).
    """
    def proceed():
        config = get_config()
        if _regenerate_blocked(editor, editor.note, config):
            return

        # Dictation language picker disabled (pinning only saved ~0.5-0.8s and added
        # UI noise); transcription just auto-detects. To bring the picker back, also
        # re-enable the commented block in dialogs.ask_instruction and pass
        # lang=config.get("whisper_language", "") + on_lang=set_language here:
        # def set_language(code):
        #     cfg = get_config()
        #     cfg["whisper_language"] = code
        #     mw.addonManager.writeConfig(__name__, cfg)
        answer = dialogs.ask_instruction(
            editor.parentWindow, dictate=_dictate,
            level=config.get("cefr_level", ""),
        )
        if answer is None:
            return
        instruction, level = answer
        # Regenerate overwrites the German example fields wholesale, dropping any
        # old [sound:...] tag with them, so the audio left on the card no longer
        # matches the new sentence. Chain pronunciation (same as the "both" flow)
        # to synthesize fresh clips for the new examples.
        _generate_async(
            editor,
            regenerate=True,
            instruction=instruction,
            level=level,
            then=lambda: tts_module.pronounce(editor, get_config()),
        )

    editor.call_after_note_saved(proceed)


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
# Word lookup (mini dictionary)                                               #
# --------------------------------------------------------------------------- #
def _add_to_anki(word, data, audio_by_text):
    """Open Anki's Add-note window pre-filled from a looked-up word.

    Reuses whatever note type the Add window currently has and fills its fields
    by the add-on's configured names (missing fields are simply skipped, exactly
    like the editor flow). Previewed pronunciation clips are attached to their
    tts fields and their temp files removed. The user reviews, picks the deck,
    and saves in the Add window -- nothing is written to the collection here.
    """
    import aqt

    config = get_config()
    addcards = aqt.dialogs.open("AddCards", mw)
    current = addcards.editor.note
    notetype = current.note_type() if current else mw.col.models.current()
    note = mw.col.new_note(notetype)

    def fill(name, value):
        idx = field_index(note, name)
        if idx is not None and value:
            note.fields[idx] = value

    head = (data or {}).get("canonical") or word
    fill(config.get("source_field", "Back"), head)
    fill(config.get("meaning_field", "Front"), (data or {}).get("meaning", ""))
    de_fields = config.get("example_fields", [])
    en_fields = config.get("translation_fields", [])
    for i, ex in enumerate((data or {}).get("examples", [])):
        if i < len(de_fields):
            fill(de_fields[i], (ex or {}).get("de", ""))
        if i < len(en_fields):
            fill(en_fields[i], (ex or {}).get("en", ""))

    # Attach any audio previewed in the popup: the field's text is the same
    # German string that was synthesized, so it keys straight into audio_by_text.
    for fname in config.get("tts_fields", []):
        idx = field_index(note, fname)
        if idx is None:
            continue
        path = (audio_by_text or {}).get(strip_html(note.fields[idx]))
        if not path or not os.path.exists(path):
            continue
        try:
            try:
                filename = mw.col.media.add_file(path)
            except AttributeError:  # older API name
                filename = mw.col.media.addFile(path)
        finally:
            if os.path.exists(path):
                os.remove(path)
        note.fields[idx] = (note.fields[idx] + " [sound:%s]" % filename).strip()

    # Drop any previewed clips we didn't attach (e.g. field absent on this type).
    for path in (audio_by_text or {}).values():
        if path and os.path.exists(path):
            os.remove(path)

    addcards.set_note(note)


def on_lookup(parent):
    """Open the word-lookup popup, wiring it to the LLM / TTS / dup-check logic."""
    config = get_config()

    def search(word):
        data = llm_client.generate(word, config)
        if isinstance(data, dict):
            canon = data.get("canonical", "") if config.get("normalize_word", True) else ""
            data["canonical"] = _clean_canonical(canon, word)
        return data

    lookup.open_lookup_dialog(
        parent,
        search=search,
        find_dupes=lambda word: _find_duplicates(word, config),
        synthesize=lambda text: tts_module.synthesize_clip(text, config),
        open_in_browser=_open_in_browser,
        add_to_anki=_add_to_anki,
        model_label=llm_client.active_model(config),
    )


# --------------------------------------------------------------------------- #
# Hooks                                                                        #
# --------------------------------------------------------------------------- #
_ICON_DIR = os.path.join(os.path.dirname(__file__), "assets", "icons")


def _icon(name, *fallbacks):
    """Absolute path to an editor-button icon (Anki inlines it as a data URI).

    Falls back to the first existing alternative when `name.png` is missing, so a
    dedicated icon (e.g. regenerate.png) is used once dropped in, but the button
    still renders with a sensible stand-in until then.
    """
    for candidate in (name, *fallbacks):
        path = os.path.join(_ICON_DIR, candidate + ".png")
        if os.path.exists(path):
            return path
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
            _icon("regenerate", "generate"),
            "de_regenerate",
            lambda ed: on_regenerate(ed),
            tip="Regenerate examples with your own instructions — %s"
            % config.get("shortcut_regenerate", "Ctrl+Shift+R"),
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
    buttons.append(
        editor.addButton(
            _icon("lookup", "generate"),
            "de_lookup",
            lambda ed: on_lookup(ed.parentWindow),
            tip="Look up a word before adding it — %s"
            % config.get("shortcut_lookup", "Ctrl+Shift+L"),
        )
    )
    return buttons


def _add_shortcuts(shortcuts, editor):
    config = get_config()
    shortcuts.append(
        (config.get("shortcut_generate", "Ctrl+Shift+G"), lambda: on_generate(editor), True)
    )
    shortcuts.append(
        (config.get("shortcut_regenerate", "Ctrl+Shift+R"), lambda: on_regenerate(editor), True)
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
    shortcuts.append(
        (config.get("shortcut_lookup", "Ctrl+Shift+L"),
         lambda: on_lookup(editor.parentWindow), True)
    )


def _on_js_message(handled, message, context):
    """Catch the overlay Cancel button's `pycmd('ga_cancel')`.

    The editor webview routes pycmd through this global hook with `context` set
    to the Editor; we flag the active run cancelled and tear down the overlay.
    The in-flight CLI/HTTP call finishes in the background but its result is then
    discarded (the job handlers poll the token before applying anything)."""
    from aqt.editor import Editor

    if message == "ga_cancel" and isinstance(context, Editor):
        overlay.request_cancel()
        overlay.hide(context)
        tooltip("Cancelled.")
        return (True, None)
    return handled


def _install_menu():
    """Add the Setup & Diagnostics entry under Tools.

    Add-ons load after Anki's Tools menu is built, so we can append directly at
    import time; guarded in case mw isn't ready (e.g. headless tests).
    """
    if mw is None or not getattr(mw, "form", None):
        return
    from aqt.qt import QAction

    lookup_action = QAction("EasyFiller: Look up a word…", mw)
    lookup_action.triggered.connect(lambda: on_lookup(mw))
    mw.form.menuTools.addAction(lookup_action)

    action = QAction("EasyFiller: Setup && Diagnostics…", mw)
    action.triggered.connect(lambda: diagnostics.open_diagnostics(mw))
    mw.form.menuTools.addAction(action)


gui_hooks.editor_did_init_buttons.append(_add_buttons)
gui_hooks.editor_did_init_shortcuts.append(_add_shortcuts)
gui_hooks.webview_did_receive_js_message.append(_on_js_message)
_install_menu()
