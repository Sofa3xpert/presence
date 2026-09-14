#!/bin/bash
# Wrap dist/Presence.app into dist/Presence-<version>-<arch>.dmg with hdiutil.
#   packaging/macos/make_dmg.sh [path/to/Presence.app] [version]
set -euo pipefail

APP="${1:-dist/Presence.app}"
VER="${2:-${PRESENCE_VERSION:-0.0.0}}"
ARCH="$(uname -m)"
OUT="dist/Presence-${VER}-${ARCH}.dmg"

[ -d "$APP" ] || { echo "make_dmg: $APP not found (build first)" >&2; exit 1; }
mkdir -p dist
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

cp -R "$APP" "$ROOT/"
ln -s /Applications "$ROOT/Applications"
rm -f "$OUT"

# hdiutil occasionally reports "Resource busy" on CI runners; retry a few times.
for attempt in 1 2 3 4 5; do
  if hdiutil create -volname "Presence" -srcfolder "$ROOT" -ov -format UDZO -fs HFS+ "$OUT"; then
    break
  fi
  echo "make_dmg: hdiutil attempt $attempt failed, retrying" >&2
  sleep 3
  [ "$attempt" -lt 5 ] || { echo "make_dmg: giving up" >&2; exit 1; }
done

hdiutil verify "$OUT"
echo "$OUT"
