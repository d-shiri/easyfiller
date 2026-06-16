"""Generate the English meaning + two German examples via the local Claude CLI.

This module has no Anki dependencies so it can run inside a background thread.
The Claude Agent SDK (see talk_to_claude.py) just wraps the same CLI, which Anki's
bundled Python can't import, so we call the CLI directly. Auth uses the user's
Claude Code login -- no API key is needed or stored.
"""

import glob
import json
import os
import random
import re
import shutil
import string
import subprocess

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
    "Set canonical to the dictionary citation form of the input: nouns with the "
    "definite article, capitalized (e.g. \"die Herausforderung\"); verbs as the "
    "infinitive (e.g. \"studieren\"); adjectives and other words in their base form. "
    'If the input "{word}" is an inflected form different from canonical, write at '
    'least one example sentence that uses "{word}" exactly as given; the remaining '
    "example(s) may use the canonical form. "
    "Write two natural example sentences at A2-B1 level that actually use the word. "
    "Keep the English meaning short (a few words)."
)

TRANSLATE_TEMPLATE = (
    "You are a German teacher. Translate each of the following German sentences "
    "into natural English. Respond with ONLY a single minified JSON array of "
    "strings -- no markdown, no code fences, no commentary -- one translation per "
    "input sentence, in the same order and with the same count:\n"
)


def resolve_claude_path(configured):
    """Best-effort resolution of the claude executable.

    Prefer the self-contained native binary (~/.local/bin/claude), which does NOT
    depend on `node`. Anki's stripped PATH typically resolves `claude` to the npm
    shim (/usr/local/bin/claude -> cli.js, '#!/usr/bin/env node'), which fails when
    node isn't on PATH.
    """
    if configured and os.path.isabs(configured) and os.path.exists(configured):
        return configured
    # Prefer the self-contained native binary (no node dependency).
    native = os.path.expanduser("~/.local/bin/claude")
    if os.path.exists(native) or os.path.islink(native):
        return native
    found = shutil.which(configured or "claude")
    if found:
        return found
    return configured or "claude"


def _node_dirs():
    """Directories that may contain a `node` binary, newest first.

    Used to repair PATH for a node-based claude shim when Anki launches with a
    minimal environment (no nvm/fnm/volta shims loaded).
    """
    dirs = []
    nvm = os.environ.get("NVM_DIR") or os.path.expanduser("~/.config/nvm")
    for base in (nvm, os.path.expanduser("~/.nvm")):
        dirs += sorted(glob.glob(os.path.join(base, "versions/node/*/bin")), reverse=True)
    dirs += sorted(
        glob.glob(os.path.expanduser("~/.local/share/fnm/node-versions/*/installation/bin")),
        reverse=True,
    )
    dirs.append(os.path.expanduser("~/.volta/bin"))
    dirs += ["/usr/local/bin", "/usr/bin"]
    return [d for d in dirs if os.path.exists(os.path.join(d, "node"))]


def _build_env():
    env = dict(os.environ)
    extra = _node_dirs()
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def _extract_json(text, open_ch="{", close_ch="}"):
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON value found in Claude output:\n" + text[:500])
    return json.loads(text[start:end + 1])


def _run_claude(prompt, config):
    """Invoke the Claude CLI with `prompt` and return its stdout text."""
    claude = resolve_claude_path(config.get("claude_path", "claude"))
    timeout = config.get("claude_timeout", 120)
    cmd = [claude, "-p", prompt, "--output-format", "text"]
    model = config.get("claude_model")
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_build_env(),
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Could not find the 'claude' CLI at '%s'. Set 'claude_path' in the "
            "add-on config to its absolute path." % claude
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude timed out after %s seconds." % timeout)

    if proc.returncode != 0:
        raise RuntimeError(
            "Claude CLI failed: "
            + (proc.stderr.strip() or proc.stdout.strip() or "unknown error")
        )
    return proc.stdout


def translate(sentences, config):
    """Translate German `sentences` to English, preserving order.

    Returns a list of English strings the same length as `sentences`. Raises
    ValueError if Claude returns a mismatched count.
    """
    if not sentences:
        return []
    prompt = TRANSLATE_TEMPLATE
    for i, s in enumerate(sentences, 1):
        prompt += "%d. %s\n" % (i, s)
    data = _extract_json(_run_claude(prompt, config), "[", "]")
    if not isinstance(data, list) or len(data) != len(sentences):
        raise ValueError("Claude returned unexpected translations: " + json.dumps(data)[:500])
    return [str(x) for x in data]


def generate(word, config, avoid=None):
    """Return {"meaning": str, "examples": [{"de","en"}, {"de","en"}]}.

    The built-in prompt also returns "canonical" (the dictionary citation form);
    it is optional, so custom prompts without it still validate.

    `avoid` is a list of example sentences already on the card; Claude is told to
    write different ones (otherwise it returns the same "canonical" example for a
    given word every time). Raises RuntimeError/ValueError on failure.
    """
    # A custom prompt (advanced) must keep the "{word}" placeholder and still ask
    # for the same JSON shape; use .replace so its literal braces don't break.
    custom = config.get("claude_prompt")
    prompt = custom.replace("{word}", word) if custom else PROMPT_TEMPLATE.format(word=word)
    if avoid:
        prompt += (
            "\nThese example sentences are already used -- do NOT repeat or "
            "paraphrase them, write clearly different ones:\n- "
            + "\n- ".join(avoid)
        )
    # A random token nudges Claude off its deterministic default so repeated
    # regenerations actually differ. Output instructions above forbid echoing it.
    nonce = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    prompt += "\n(Variety token %s: vary your sentence choices; never output it.)" % nonce
    data = _extract_json(_run_claude(prompt, config))
    if (
        not isinstance(data, dict)
        or "meaning" not in data
        or not isinstance(data.get("examples"), list)
        or len(data["examples"]) < 2
    ):
        raise ValueError("Claude returned unexpected JSON: " + json.dumps(data)[:500])
    return data
