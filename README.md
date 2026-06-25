# EasyFiller (Anki add-on)

![EasyFiller demo](assets/demo.gif)

One keypress turns a bare word into a complete flashcard — meaning, example
sentences, translations, and pronunciation — using a local LLM CLI (**Claude** or
**Ollama**) and a built-in TTS engine using free Microsoft **Neural voices**.
Ships configured for German, but works
for **any language** by editing the config (see [Use another language](#use-another-language)).

From a word in the source field (**Back** by default):

- **Generate** (`Ctrl+Shift+G`) — call the configured LLM CLI to fill the meaning
  (**Front**) plus two example sentences and their translations. Only *empty*
  fields are filled. Before writing it also:
  - **rewrites the word to its dictionary form** (e.g. `herausforderung` →
    `die Herausforderung`, `studiert` → `studieren`), while keeping at least one
    example in the form you typed (toggle with `normalize_word`);
  - **checks every deck for duplicates** — if the word (or its dictionary form)
    already exists, a dialog offers **See duplicate / Cancel / Generate anyway**.
- **Regenerate** (`Ctrl+Shift+R`) — write fresh example sentences and translations,
  **overwriting** the current ones. A box first lets you optionally steer them
  (e.g. *"use the word Reise"*, *"make them about cooking"*, *"keep them short"*);
  leave it blank for different examples. The word and meaning are left as they are.
- **Pronounce** (`Ctrl+Shift+P`) — silently add TTS audio (`[sound:...]`) to the
  configured fields using the **built-in TTS engine** (free Microsoft Neural
  voices, no install, no API key). Fields that already contain audio are skipped.
- **Both** (`Ctrl+Shift+B`) — generate, then pronounce.

Works on **Linux, macOS, and Windows** — executable paths and console-window
handling are resolved per-platform. The `install.sh` / `build.sh` helper scripts
below are bash (Linux/macOS); on Windows, install the built `.ankiaddon` file or
copy the folder into your add-ons directory (see [Install](#install)).

## Prerequisites

1. **An LLM CLI** for your chosen `provider` (default `claude`):
   - **Claude Code CLI** installed and signed in (`claude` works in a terminal). Auth uses
     your Claude Code login — no API key is stored. The add-on prefers the self-contained
     native binary `~/.local/bin/claude` and repairs `PATH` for node-based shims automatically.
   - or **Ollama** running locally with your model pulled (`ollama pull llama3.1`); set
     `"provider": "ollama"` and `"ollama_model"` in the config. Fully offline.
2. **Pronunciation needs no install** — TTS is built in. The add-on speaks
   Microsoft's free Neural voices directly over the network (default voice
   *de-DE-AmalaNeural*, rate +25%); no `edge-tts`, no API key. (Optional: if you
   already have the `edge-tts` CLI installed, it's used as an automatic fallback.)
3. A note type whose field names match `config.json` (`Back`, `Front`, `German_example`,
   `English_translation`, `German_example_2`, `English_translation_2`) — or edit the config.

## Install

Dev (symlink into Anki, edits stay in sync — just restart Anki):
```bash
./install.sh
```

Packaged file (to share / install elsewhere):
```bash
./build.sh        # produces ../german_autofill.ankiaddon
```
Then in Anki: **Tools → Add-ons → Install from file…**

Or copy/symlink this folder into your Anki add-ons directory as `german_autofill`:
- **Linux** `~/.local/share/Anki2/addons21/`
- **macOS** `~/Library/Application Support/Anki2/addons21/`
- **Windows** `%APPDATA%\Anki2\addons21\`

(Tools → Add-ons → View Files opens this folder in Anki.)

> The internal package id and folder are `german_autofill`; only the display name
> ("EasyFiller") differs — that's normal for Anki add-ons.

## Setup & Diagnostics

**Tools → EasyFiller: Setup & Diagnostics** opens a one-screen health check. It
verifies the three things new users trip over and fixes each on the spot:

- **LLM provider** — probes the Claude CLI (`claude --version`) or pings Ollama;
  when an Ollama model isn't pulled yet it offers a one-click **Download** with a
  progress readout.
- **Pronunciation** — confirms the built-in TTS engine (and notes the edge-tts
  CLI if you have it as a fallback) and gives a **▶ Play sample** button so you
  can *hear* the configured voice/speed/pitch before committing to it.
- **Note type fields** — lists the fields the add-on writes to and, if no note
  type has them all, **creates a ready-made one** so field names line up
  automatically.

Each row shows a green ✓ or a red ✗ with the exact fix command (copyable). Use
this first when something isn't working.

## Configure

**Tools → Add-ons → EasyFiller → Config**. Change field names, shortcuts, TTS
voice/speed/pitch, `normalize_word`, or the `provider` (Claude or Ollama) and its
path/model. Full list in [`config.md`](config.md).

## Use another language

Nothing is hard-coded to German — it's all config:

- **`tts_voice`** — pick a voice for your target language, e.g.
  `fr-FR-DeniseNeural`, `es-ES-ElviraNeural`. Microsoft's Neural voices cover
  **300+ voices across 140 locales (~75 languages)** — browse and preview them in
  this [Edge-TTS voice playground](https://huggingface.co/spaces/innoai/Edge-TTS-Text-to-Speech)
  (or, if you have the optional CLI, `edge-tts --list-voices`).
- **`llm_prompt`** — set a custom prompt for the new language. It must keep the
  `{word}` placeholder and return the same minified JSON shape
  (`{"canonical": ..., "meaning": ..., "examples": [{"de","en"}, ...]}`). Include
  `canonical` if you want `normalize_word` to keep working.
- **field names** — point `source_field`, `meaning_field`, `example_fields`,
  `translation_fields`, and `tts_fields` at your note type's fields.

## Files

- `__init__.py` — buttons, shortcuts, duplicate check, dictionary-form rewrite, and
  mapping results into empty fields.
- `llm_client.py` — runs the configured LLM CLI (Claude or Ollama) and parses JSON
  (no Anki imports; runs in a background thread).
- `tts.py` — synthesizes audio in a background thread (built-in engine, edge-tts
  CLI fallback); inserts `[sound:]` on the main thread.
- `edge_tts_native.py` — the built-in, dependency-free Edge TTS client (stdlib
  WebSocket + DRM token); no Anki imports, runs in a background thread.
- `dialogs.py` — the styled duplicate-found dialog.
- `diagnostics.py` — the **Setup & Diagnostics** panel (dependency checks, voice
  preview, one-click note-type creation).
- `overlay.py` / `loaders.py` — the in-editor loading overlay and its spinners.
- `util.py` — HTML/field helpers.
