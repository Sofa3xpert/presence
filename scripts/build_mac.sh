#!/bin/bash
# Build Presence.app and the .dmg on a Mac. Run from the repository root:
#   scripts/build_mac.sh
# Env: SKIP_SYNC=1 (reuse the current venv), CODESIGN_IDENTITY (sign + hardened
# runtime; leave unset for a local/unsigned build).
set -euo pipefail
cd "$(dirname "$0")/.."

[ "$(uname -s)" = "Darwin" ] || { echo "build_mac.sh: macOS only" >&2; exit 1; }
START=$(date +%s)

if [ -z "${SKIP_SYNC:-}" ]; then
  uv sync --group macapp
fi
VER="$(uv run --no-sync python -c 'import presence; print(presence.__version__)' 2>/dev/null | grep -v Conda | tail -1)"
echo "== Presence ${VER} ($(uname -m)) =="

rm -rf build/Presence dist/Presence dist/Presence.app
uv run --no-sync pyinstaller --noconfirm --clean packaging/macos/Presence.spec

echo "== smoke test =="
PRESENCE_SMOKE=1 dist/Presence.app/Contents/MacOS/Presence | tee /dev/stderr | grep -q "SMOKE OK"

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  echo "== verifying signature =="
  codesign -vvv --deep --strict dist/Presence.app
fi

echo "== dmg =="
DMG="$(packaging/macos/make_dmg.sh dist/Presence.app "$VER" | tail -1)"

END=$(date +%s)
echo "== done in $((END - START)) s =="
du -sh dist/Presence.app "$DMG"
