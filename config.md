# EasyFiller

Two one-press helpers for the card editor:

- **Generate** (document icon, default `Ctrl+Shift+G`): reads the German word from
  `source_field` and uses a local LLM CLI (**Claude** or **Ollama**, see `provider`)
  to fill the English meaning plus two example sentences and their translations.
  Only *empty* fields are filled.
- **Regenerate** (default `Ctrl+Shift+R`): pops up a box where you can optionally
  type how you want the new examples (e.g. “use the word Reise”, “make them about
  cooking”, “keep them short”), then **overwrites** the example sentences and their
  translations with fresh ones. Leave the box blank for different examples. The word
  and meaning are left untouched.
- **Pronounce** (microphone icon, default `Ctrl+Shift+P`): silently adds TTS audio
  (built-in engine — free Microsoft Neural voices, no install) to the configured
  `tts_fields`. Fields that already contain a `[sound:...]` tag are skipped.
- **Both** (document + microphone icon, default `Ctrl+Shift+B`): generate, then pronounce.
- **Clear** (trash icon, default `Ctrl+Shift+X`): empties every field on the note so
  you can enter a different word. Reversible with the editor's undo (`Ctrl+Z`).

## Settings

- `source_field` – field holding the German word (default `Back`).
- `meaning_field` – where the English gloss goes (default `Front`).
- `example_fields` / `translation_fields` – ordered target fields for the two examples.
- `tts_fields` – fields that should receive pronunciation audio.
- `normalize_word` – when `true` (default), generating rewrites `source_field` to the
  word's dictionary citation form: nouns gain their article and capitalization
  (`herausforderung` → `die Herausforderung`), verbs become the infinitive
  (`studiert` → `studieren`), and at least one example still uses the form you typed.
  Set `false` to leave the field exactly as entered. Note: a custom `llm_prompt`
  must include the `canonical` field for this to take effect.
- `provider` – which local LLM CLI to use: `"claude"` (default) or `"ollama"`. Each
  provider reads its own `*_path` / `*_model` keys below.
- `claude_path` – path to the `claude` CLI. Leave as `"claude"` to auto-resolve, or set
  an absolute path (e.g. `/home/you/.local/bin/claude`) if Anki can't find it.
- `claude_model` – optional Claude model id (e.g. `claude-haiku-4-5-20251001` for faster
  cards). Empty = the CLI default.
- `ollama_host` – Ollama server URL (used when `provider` is `"ollama"`). Leave empty for
  the default `http://127.0.0.1:11434` (or the `OLLAMA_HOST` env var). The add-on talks to
  Ollama over HTTP, not the CLI, so the model must already be pulled — it will **not**
  auto-download (a missing model gives a clear error with the `ollama pull` command to run).
- `ollama_model` – Ollama model to run, e.g. `llama3.2:3b` or `gemma4:e4b-it-qat`. Must
  already be pulled (`ollama pull <model>`). Required when `provider` is `"ollama"`.
- `llm_timeout` – seconds to wait for the model (was `claude_timeout`; the old key still
  works as a fallback).
- `llm_prompt` – optional custom prompt template (advanced). Must contain `{word}` and
  still instruct the model to return the same JSON shape. Include a `canonical` field if
  you want `normalize_word` to work. Empty = built-in prompt. (Was `claude_prompt`; the
  old key still works as a fallback.)
- `edge_tts_path` – path to the optional `edge-tts` CLI. Pronunciation works
  without it (the built-in engine is used first); if the CLI is installed it acts
  as an automatic fallback. Leave as `"edge-tts"` to auto-resolve, or set an
  absolute path.
- `tts_voice` – Microsoft Neural voice name (default `de-DE-AmalaNeural`). Browse
  voices in the [Edge-TTS playground](https://huggingface.co/spaces/innoai/Edge-TTS-Text-to-Speech),
  or with `edge-tts --list-voices` if you have the optional CLI.
- `tts_speed` – playback rate; `1.25` becomes `+25%`. `tts_pitch` – `0` becomes `+0Hz`.
- `tts_timeout` – seconds to wait per clip (default 60).
- `shortcut_*` – keyboard shortcuts.

Pronunciation needs no install. You only need the LLM CLI for your chosen
`provider`: either the **Claude** CLI signed in (`claude` working in a terminal)
or a running **Ollama** install with your model pulled (`ollama pull <model>`).
No API key is stored here.
