#!/usr/bin/env bash
# Package the add-on into a shareable german_autofill.ankiaddon file
# (a zip of the add-on's CONTENTS, excluding runtime/cache files).
#
# Install the result via Anki: Tools > Add-ons > Install from file...
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$SRC/../german_autofill.ankiaddon"

rm -f "$OUT"
( cd "$SRC" && zip -r "$OUT" . \
    -x '*.pyc' -x '__pycache__/*' -x 'meta.json' \
    -x 'install.sh' -x 'build.sh' -x 'README.md' )

echo "Built: $(cd "$SRC/.." && pwd)/german_autofill.ankiaddon"
