"""Generate the English meaning + two German examples via a local LLM CLI.

This module has no Anki dependencies so it can run inside a background thread.
The provider is chosen with the "provider" config key, and we parse JSON from
whatever it returns:

  * "claude" -- the Claude Code CLI (`claude -p ...`). Auth uses the user's Claude
    Code login -- no API key is needed or stored.
  * "ollama" -- a local Ollama server over its HTTP API (`/api/generate`), fully
    offline. We use HTTP, not `ollama run`, because the CLI injects terminal cursor
    codes into stdout that corrupt the JSON.

Adding another provider is just a new function in _PROVIDERS; the prompts and JSON
parsing below are provider-agnostic.
"""

import glob
import json
import os
import random
import re
import string
import subprocess
import urllib.error
import urllib.request

from .util import IS_WINDOWS, resolve_executable, run_hidden

PROMPT_TEMPLATE = (
    "You are a German teacher creating Anki flashcards. "
    'For the German word or phrase "{word}", respond with ONLY a single minified '
    "JSON object -- no markdown, no code fences, no commentary -- in exactly this shape:\n"
    '{{"canonical": "<dictionary citation form>", '
    '"meaning": "<concise English gloss, comma-separated synonyms>", '
    '"examples": ['
    '{{"de": "<German example sentence>", "en": "<English translation>"}}, '
    '{{"de": "<second German example sentence>", "en": "<English translation>"}}'
    "]}}\n"
    "Set canonical to the dictionary citation form of the input: nouns in the "
    "nominative SINGULAR with the definite article, capitalized -- even when the "
    'input is plural or inflected (e.g. "weltläden" -> "der Weltladen", '
    '"Häuser" -> "das Haus", "herausforderung" -> "die Herausforderung"); verbs as '
    'the infinitive (e.g. "studiert" -> "studieren"); adjectives and other words in '
    "their base form. "
    'If the input "{word}" is an inflected or plural form different from canonical, '
    'write at least one example sentence that uses "{word}" exactly as given; the '
    "remaining example(s) may use the canonical form. "
    "Write two natural example sentences that actually use the word. {level} "
    "Keep the English meaning short (a few words)."
)

# CEFR difficulty presets for the example sentences. An empty "cefr_level" config
# keeps the built-in default (roughly A2-B1); each value below completes the
# template's "...that actually use the word. <X> Keep the English meaning short."
_CEFR_LEVELS = {
    "A1": "Write them at CEFR level A1: only the most common everyday words in "
          "short, simple present-tense sentences.",
    "A2": "Write them at CEFR level A2: simple everyday vocabulary and short "
          "sentences (present and perfect tense, basic connectors).",
    "B1": "Write them at CEFR level B1: common everyday vocabulary with some "
          "subordinate clauses; clear and not too long.",
    "B2": "Write them at CEFR level B2: varied vocabulary and more complex "
          "sentence structures, including some abstract topics.",
    "C1": "Write them at CEFR level C1: sophisticated, idiomatic language with "
          "complex structures and precise, less common vocabulary.",
    "C2": "Write them at CEFR level C2: near-native, nuanced and idiomatic, with "
          "advanced vocabulary and sophisticated structures.",
}
_DEFAULT_CEFR = "Aim for roughly A2-B1 (CEFR) difficulty."


def cefr_phrase(level):
    """Sentence telling the model what CEFR difficulty to write examples at.

    Empty/blank `level` -> the built-in default (~A2-B1). A known level (A1-C2,
    case-insensitive) gets a rich descriptor; any other non-empty value is passed
    through generically so an unusual setting still steers the model instead of
    being silently ignored.
    """
    level = (level or "").strip().upper()
    if not level:
        return _DEFAULT_CEFR
    return _CEFR_LEVELS.get(level, "Write them at CEFR level %s." % level)


def cefr_instruction(config):
    """CEFR difficulty sentence for the configured `cefr_level`."""
    return cefr_phrase(config.get("cefr_level"))

class ModelError(RuntimeError):
    """A provider error worth presenting with extra structure.

    `hint` is a shell command the user can run to fix it (shown monospace), and
    `models` is the list of models actually available (shown as pills). `pull` is
    the model name to offer an in-app download for (Ollama only). All optional;
    show_error() falls back to a plain message when they're absent.
    """

    def __init__(self, message, hint=None, models=None, pull=None):
        super().__init__(message)
        self.hint = hint
        self.models = models
        self.pull = pull


# Curated Ollama models for this add-on's task (German meaning + example JSON),
# best first: (name, one-line note). Qwen punches above its size on multilingual
# + structured output; the user can still pull anything else by name.
RECOMMENDED_MODELS = [
    ("qwen2.5:7b-instruct", "Recommended — strong German & JSON (~5 GB)"),
    ("qwen2.5:3b", "Fastest usable — good German (~2 GB)"),
    ("qwen2.5:14b-instruct", "Best quality — needs ~10 GB free (~9 GB)"),
]
BEST_MODEL = RECOMMENDED_MODELS[0][0]


def active_model(config):
    """Human-readable "provider · model" for the configured provider.

    Used to show what's actually generating in the editor overlay. Claude with no
    explicit model falls back to "default" (its CLI picks one)."""
    provider = (config.get("provider") or "claude").strip().lower()
    if provider == "ollama":
        return "ollama · " + (config.get("ollama_model") or "?")
    if provider == "claude":
        model = (config.get("claude_model") or "").strip()
        return "claude · " + (model or "default")
    return provider


TRANSLATE_TEMPLATE = (
    "You are a German teacher. Translate each of the following German sentences "
    "into natural English. Respond with ONLY a single minified JSON array of "
    "strings -- no markdown, no code fences, no commentary -- one translation per "
    "input sentence, in the same order and with the same count:\n"
)


def resolve_claude_path(configured):
    """Best-effort resolution of the claude executable.

    Prefer the self-contained native binary (~/.local/bin/claude on every OS,
    `.exe` on Windows), which does NOT depend on `node`. Anki's stripped PATH
    typically resolves `claude` to the npm shim (-> cli.js, '#!/usr/bin/env node'
    on Unix, claude.cmd on Windows), which fails when node isn't on PATH. The
    shared resolver checks ~/.local/bin ahead of PATH so the native binary wins.
    """
    return resolve_executable(configured, "claude")


def _node_dirs():
    """Directories that may contain a `node`/`node.exe` binary, newest first.

    Used to repair PATH for a node-based claude shim when Anki launches with a
    minimal environment (no nvm/fnm/volta shims loaded). Covers the common Unix
    version managers plus Windows' nvm-windows / global npm install dir.
    """
    node = "node.exe" if IS_WINDOWS else "node"
    dirs = []
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA", "")
        nvm_home = os.environ.get("NVM_HOME") or os.environ.get("NVM_SYMLINK", "")
        for base in (nvm_home, os.path.join(appdata, "nvm") if appdata else ""):
            if base:
                dirs += sorted(glob.glob(os.path.join(base, "v*")), reverse=True)
        if appdata:
            dirs.append(os.path.join(appdata, "npm"))  # global npm bin
        dirs += [r"C:\Program Files\nodejs"]
    else:
        nvm = os.environ.get("NVM_DIR") or os.path.expanduser("~/.config/nvm")
        for base in (nvm, os.path.expanduser("~/.nvm")):
            dirs += sorted(glob.glob(os.path.join(base, "versions/node/*/bin")), reverse=True)
        dirs += sorted(
            glob.glob(os.path.expanduser("~/.local/share/fnm/node-versions/*/installation/bin")),
            reverse=True,
        )
        dirs.append(os.path.expanduser("~/.volta/bin"))
        dirs += ["/usr/local/bin", "/usr/bin"]
    return [d for d in dirs if os.path.exists(os.path.join(d, node))]


def _build_env():
    env = dict(os.environ)
    extra = _node_dirs()
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def _timeout(config):
    # llm_timeout is the provider-neutral key; fall back to the old claude_timeout.
    return config.get("llm_timeout", config.get("claude_timeout", 120))


def _run_claude(prompt, config, fmt=None):
    """Run the Claude Code CLI and return its stdout text. `fmt` is ignored."""
    exe = resolve_claude_path(config.get("claude_path", "claude"))
    cmd = [exe, "-p", prompt, "--output-format", "text"]
    model = config.get("claude_model")
    if model:
        cmd += ["--model", model]
    timeout = _timeout(config)
    try:
        proc = run_hidden(
            cmd, capture_output=True, text=True, timeout=timeout, env=_build_env()
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Could not find the 'claude' CLI at '%s'. Set 'claude_path' in the "
            "add-on config to its absolute path." % exe
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude timed out after %s seconds." % timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            "Claude CLI failed: "
            + (proc.stderr.strip() or proc.stdout.strip() or "unknown error")
        )
    return proc.stdout


def _ollama_base(config):
    """Base URL of the Ollama server (config key, env var, or localhost default)."""
    host = config.get("ollama_host") or os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434"
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


def _ollama_get(base, path, timeout):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_version(config):
    """Best-effort Ollama version string, or None if the server is unreachable."""
    try:
        return _ollama_get(_ollama_base(config), "/api/version", 5).get("version")
    except Exception:
        return None


def ollama_models(config):
    """List of installed model names, or None if the server is unreachable."""
    try:
        data = _ollama_get(_ollama_base(config), "/api/tags", 10)
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return None


def _run_ollama(prompt, config, fmt=None):
    """Call the local Ollama HTTP API (`/api/generate`) and return the response text.

    We use HTTP rather than `ollama run` because the CLI injects terminal cursor
    codes into stdout that corrupt the JSON. The model is checked against the
    installed list first, so a typo or missing model gives a clear error instead
    of a silent multi-GB background pull (which is what made the editor hang).
    """
    base = _ollama_base(config)
    model = config.get("ollama_model")
    if not model:
        raise RuntimeError(
            'Set "ollama_model" in the add-on config to a pulled model (e.g. "llama3.2:3b").'
        )
    installed = ollama_models(config)
    if installed is None:
        raise RuntimeError(
            "Could not reach Ollama at %s. Make sure it is running (try `ollama list` "
            "in a terminal), or set \"ollama_host\" in the add-on config." % base
        )
    # Server is reachable -- include its version in any further error for context.
    where = "Ollama %s at %s" % (ollama_version(config) or "?", base)
    if not (model in installed or (model + ":latest") in installed):
        raise ModelError(
            "The Ollama model '%s' isn't installed. Download it below, pull it in a "
            'terminal, or set "ollama_model" in the config to one you have. (%s)'
            % (model, where),
            hint="ollama pull %s" % model,
            models=installed,
            pull=model,
        )
    payload = {"model": model, "prompt": prompt, "stream": False}
    if fmt:
        payload["format"] = fmt
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/generate", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout(config)) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError("Ollama request failed: %s (%s)" % (getattr(e, "reason", e), where))
    return resp.get("response", "")


def pull_model(model, config, on_progress=None):
    """Stream `ollama pull <model>` from the server, reporting progress.

    Calls on_progress(completed, total, status) for every status line Ollama
    emits -- completed/total are byte counts during the blob downloads and None
    during the manifest/verify phases, so callers should treat a missing total as
    an indeterminate stage. Raises ModelError if the server is unreachable or the
    pull reports an error. Returns when the stream ends (download complete).
    """
    if not model:
        raise ModelError('No model to download -- set "ollama_model" in the config.')
    base = _ollama_base(config)
    body = json.dumps({"model": model, "stream": True}).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/pull", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        # A generous per-read timeout: Ollama emits progress frequently, so a long
        # gap means the connection stalled rather than a slow-but-healthy download.
        resp = urllib.request.urlopen(req, timeout=300)
    except (urllib.error.URLError, TimeoutError) as e:
        raise ModelError(
            "Could not reach Ollama at %s to download '%s': %s"
            % (base, model, getattr(e, "reason", e))
        )
    with resp:
        for raw in resp:  # newline-delimited JSON, one status object per line
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            err = msg.get("error")
            if err:
                raise ModelError("Ollama couldn't download '%s': %s" % (model, err))
            if on_progress:
                on_progress(msg.get("completed"), msg.get("total"), msg.get("status", ""))


# Each provider runs a prompt and returns raw text; `fmt` (e.g. "json") is an
# optional output-format hint that only some providers honor.
_PROVIDERS = {
    "claude": _run_claude,
    "ollama": _run_ollama,
}


def _extract_json(text, open_ch="{", close_ch="}"):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON value found in model output:\n" + text[:500])
    return json.loads(text[start:end + 1])


def _run_llm(prompt, config, fmt=None):
    """Dispatch `prompt` to the configured provider and return its raw text."""
    provider = (config.get("provider") or "claude").strip().lower()
    run = _PROVIDERS.get(provider)
    if not run:
        raise RuntimeError(
            'Unknown provider "%s". Set "provider" to one of: %s.'
            % (provider, ", ".join(sorted(_PROVIDERS)))
        )
    return run(prompt, config, fmt)


def translate(sentences, config):
    """Translate German `sentences` to English, preserving order.

    Returns a list of English strings the same length as `sentences`. Raises
    ValueError if the model returns a mismatched count.
    """
    if not sentences:
        return []
    prompt = TRANSLATE_TEMPLATE
    for i, s in enumerate(sentences, 1):
        prompt += "%d. %s\n" % (i, s)
    data = _extract_json(_run_llm(prompt, config), "[", "]")
    if not isinstance(data, list) or len(data) != len(sentences):
        raise ValueError("Model returned unexpected translations: " + json.dumps(data)[:500])
    return [str(x) for x in data]


def generate(word, config, avoid=None, instruction=None, current=None, level=None):
    """Return {"meaning": str, "examples": [{"de","en"}, {"de","en"}]}.

    The built-in prompt also returns "canonical" (the dictionary citation form);
    it is optional, so custom prompts without it still validate.

    `avoid` is a list of example sentences already on the card; the model is told to
    write different ones (otherwise it returns the same "canonical" example for a
    given word every time). `instruction` is optional free text from the user (e.g.
    "use the word Reise", "make them about cooking") appended to steer the example
    sentences; it never overrides the required JSON shape.

    `current` is the card's example sentences in field order. When given together
    with `instruction` (the regenerate-with-a-voice/text-command flow) we show the
    model the numbered current sentences and tell it to honor the instruction
    literally -- so "only change the first sentence" keeps the second verbatim
    instead of replacing both. Raises RuntimeError/ValueError on failure.
    """
    # A custom prompt (advanced) must keep the "{word}" placeholder and still ask
    # for the same JSON shape; use .replace so its literal braces don't break.
    # llm_prompt is the provider-neutral key; fall back to the old claude_prompt.
    # `level` (a per-run CEFR override, e.g. from the Regenerate dialog) wins over
    # the configured "cefr_level"; None means "use the config".
    effective_level = level if level is not None else config.get("cefr_level")
    custom = config.get("llm_prompt") or config.get("claude_prompt")
    if custom:
        prompt = custom.replace("{word}", word)
        # A custom template has no {level} slot, so only steer difficulty when a
        # level is actually set -- otherwise leave their prompt untouched.
        if (effective_level or "").strip():
            prompt += "\n" + cefr_phrase(effective_level)
    else:
        prompt = PROMPT_TEMPLATE.format(word=word, level=cefr_phrase(effective_level))
    # Targeted only when there's at least one real sentence to anchor a positional
    # instruction to; an all-blank `current` falls through to the normal path.
    targeted = bool(
        instruction and instruction.strip()
        and current and any(s.strip() for s in current)
    )
    if targeted:
        # Give the model the current sentences as numbered, ordered content so a
        # positional instruction has something to anchor to, and require it to
        # return EVERY position. The default is to REWRITE every sentence -- the
        # button's promise is fresh examples -- and only an instruction that
        # explicitly scopes the change ("only change the second sentence") gets
        # untouched positions copied back verbatim. The write-back is positional
        # (examples[i] -> example_fields[i]), so a verbatim copy is what "leave
        # that one alone" means, and one example per position keeps the two
        # halves aligned.
        numbered = "\n".join(
            "%d. %s" % (i, s.strip() or "(currently empty)")
            for i, s in enumerate(current, 1)
        )
        prompt += (
            "\nThe card currently has these example sentences, by position:\n"
            + numbered
            + "\nRewrite the examples following this instruction from the user:\n"
            + instruction.strip()
            + "\nUnless the instruction says otherwise, replace EVERY position "
            "with a fresh, clearly different sentence that follows the "
            "instruction -- do not repeat or paraphrase the current sentences. "
            "Only if the instruction explicitly limits the change to particular "
            'positions (e.g. "only change the second sentence") or asks to keep '
            "some, copy each kept position's German sentence back EXACTLY, "
            "character for character. For a position marked (currently empty), "
            "always write a fresh natural example sentence. Return exactly %d "
            "example objects, one per position above, in the same order."
            % len(current)
        )
    else:
        if avoid:
            prompt += (
                "\nThese example sentences are already used -- do NOT repeat or "
                "paraphrase them, write clearly different ones:\n- "
                + "\n- ".join(avoid)
            )
        if instruction and instruction.strip():
            prompt += (
                "\nAdditional instructions from the user for the example sentences -- "
                "follow them, but keep the exact JSON shape required above:\n"
                + instruction.strip()
            )
    # A random token nudges the model off its deterministic default so repeated
    # regenerations actually differ. Output instructions above forbid echoing it.
    # In a targeted edit we scope the nudge to NEW sentences so it doesn't fight
    # the rule to copy kept sentences verbatim.
    nonce = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    if targeted:
        prompt += (
            "\n(Variety token %s: when you write a NEW sentence, vary your choice; "
            "leave kept sentences exactly as given. Never output this token.)" % nonce
        )
    else:
        prompt += "\n(Variety token %s: vary your sentence choices; never output it.)" % nonce
    # fmt="json" makes Ollama emit a syntactically valid JSON object (Claude ignores it).
    data = _extract_json(_run_llm(prompt, config, fmt="json"))
    # Default fill expects the template's two examples; a targeted edit returns one
    # per position shown, so require exactly that many (never below one).
    need = max(1, len(current)) if targeted else 2
    if (
        not isinstance(data, dict)
        or "meaning" not in data
        or not isinstance(data.get("examples"), list)
        or len(data["examples"]) < need
    ):
        raise ValueError("Model returned unexpected JSON: " + json.dumps(data)[:500])
    return data
