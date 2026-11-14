#!/usr/bin/env bash
# Build ELG.dmg using committed installer assets + create-dmg (Finder layout).
#
# Committed in installer/macos/:
#   background.tiff  — DMG background (regenerate via scripts/dmg/build_background.py)
#
# .DS_Store is written by Finder during create-dmg (volume-specific aliases). A copied
# .DS_Store cannot be reused across builds.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${1:-$ROOT/build/macos-arm64/ELG.app}"
STAGING="${RUNNER_TEMP:-/tmp}/elg-dmg-staging"
DMG_OUT="$ROOT/dist-installers/ELG.dmg"
BACKGROUND="$ROOT/installer/macos/background.tiff"

if [[ ! -f "$BACKGROUND" ]]; then
  echo "Missing committed background: $BACKGROUND" >&2
  echo "Run: pip install -r scripts/dmg/requirements.txt && python scripts/dmg/build_background.py" >&2
  exit 1
fi

if ! command -v create-dmg >/dev/null 2>&1; then
  echo "create-dmg not found; install with: brew install create-dmg" >&2
  exit 1
fi

rm -rf "$STAGING"
mkdir -p "$STAGING" "$ROOT/dist-installers"
cp -R "$APP_PATH" "$STAGING/"
rm -f "$DMG_OUT"

# create-dmg uses HFS+ by default. Do not force APFS, as APFS causes
# 'cannot find IO media' / 'interstitial disk image was not found' on modern macOS.
# If create-dmg fails (or exits 2 from Finder AppleScript on headless runners),
# ensure a valid DMG is produced via hdiutil fallback.
set +e
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
  --format UDZO \
  --no-internet-enable \
  "$DMG_OUT" \
  "$STAGING/"
CREATE_DMG_STATUS=$?
set -e

if [[ ! -f "$DMG_OUT" ]]; then
  echo "create-dmg did not create output (status: $CREATE_DMG_STATUS); falling back to hdiutil create..." >&2
  # Detach any dangling interstitial mounts from create-dmg
  hdiutil info | grep -E '^/dev/disk[0-9]+' | awk '{print $1}' | while read -r dev; do
    hdiutil detach "$dev" -force 2>/dev/null || true
  done
  ln -sf /Applications "$STAGING/Applications"
  hdiutil create \
    -volname "ELG" \
    -srcfolder "$STAGING" \
    -ov \
    -format UDZO \
    "$DMG_OUT"
fi

echo "Wrote $DMG_OUT"
