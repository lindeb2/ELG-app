#!/usr/bin/env bash
# Build ELG.dmg with Finder styling via create-dmg (AppleScript on mounted volume).
#
# A copied .DS_Store is not enough: backgroundImageAlias stores volume-specific file
# IDs. Finder only applies icon positions and backgrounds when that alias resolves
# on the live mounted DMG. create-dmg mounts the image and runs AppleScript so
# Finder writes a fresh .DS_Store for this volume.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${1:-$ROOT/build/macos-arm64/ELG.app}"
STAGING="${RUNNER_TEMP:-/tmp}/elg-dmg-staging"
DMG_OUT="$ROOT/dist-installers/ELG.dmg"
BACKGROUND="$ROOT/installer/macos/background.tiff"

python "$ROOT/scripts/build_dmg_background.py"

if ! command -v create-dmg >/dev/null 2>&1; then
  echo "create-dmg not found; install with: brew install create-dmg" >&2
  exit 1
fi

rm -rf "$STAGING"
mkdir -p "$STAGING" "$ROOT/dist-installers"
cp -R "$APP_PATH" "$STAGING/"
rm -f "$DMG_OUT"

create-dmg \
  --volname "ELG" \
  --background "$BACKGROUND" \
  --window-pos 100 100 \
  --window-size 512 342 \
  --icon-size 128 \
  --text-size 12 \
  --icon "ELG.app" 140 160 \
  --hide-extension "ELG.app" \
  --app-drop-link 372 160 \
  --filesystem APFS \
  --format UDZO \
  --no-internet-enable \
  "$DMG_OUT" \
  "$STAGING/"

echo "Wrote $DMG_OUT"
