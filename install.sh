#!/usr/bin/env bash
# Dev install: symlink this add-on into Anki's add-ons folder so edits in the repo
# are picked up live (just restart Anki). Re-runnable.
#
# Usage: ./german_autofill/install.sh   (run from the repo root or anywhere)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ADDONS="$HOME/.local/share/Anki2/addons21"

if [ ! -d "$ADDONS" ]; then
  echo "Anki add-ons folder not found: $ADDONS" >&2
  echo "Open Anki at least once, or set ADDONS manually." >&2
  exit 1
fi

ln -sfn "$SRC" "$ADDONS/german_autofill"
echo "Linked $ADDONS/german_autofill -> $SRC"

# Ensure the edge-tts CLI (pronunciation) is installed somewhere Anki can find it.
if command -v edge-tts >/dev/null 2>&1; then
  echo "edge-tts found: $(command -v edge-tts)"
elif command -v uv >/dev/null 2>&1; then
  echo "Installing edge-tts (uv tool install edge-tts)..."
  uv tool install edge-tts
elif command -v pipx >/dev/null 2>&1; then
  echo "Installing edge-tts (pipx install edge-tts)..."
  pipx install edge-tts
else
  echo "WARNING: edge-tts not found and neither uv nor pipx is available." >&2
  echo "Install it manually (e.g. 'pipx install edge-tts') or set 'edge_tts_path'" >&2
  echo "in the add-on config; Pronounce won't work without it." >&2
fi

echo "Restart Anki to (re)load the add-on."
