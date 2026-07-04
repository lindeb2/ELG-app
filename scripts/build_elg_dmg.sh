#!/usr/bin/env bash
# Build a styled ELG.dmg using Discord's Finder layout (.DS_Store template).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${1:-$ROOT/build/macos-arm64/ELG.app}"
STAGING="${RUNNER_TEMP:-/tmp}/elg-dmg-staging"
DMG_OUT="$ROOT/dist-installers/ELG.dmg"
BACKGROUND="$ROOT/installer/macos/background.tiff"
DS_STORE="$ROOT/installer/macos/.DS_Store"

python "$ROOT/scripts/build_dmg_background.py"
python "$ROOT/scripts/patch_dmg_ds_store.py"

rm -rf "$STAGING"
mkdir -p "$STAGING/.background" "$ROOT/dist-installers"

cp -R "$APP_PATH" "$STAGING/"
ln -sf /Applications "$STAGING/Applications"
cp "$BACKGROUND" "$STAGING/.background/background.tiff"
cp "$DS_STORE" "$STAGING/.DS_Store"

# Immutable flags on signed bundles break hdiutil on Sonoma+.
if command -v chflags >/dev/null 2>&1; then
  chflags -R nouchg "$STAGING" 2>/dev/null || true
fi

rm -f "$DMG_OUT"

create_dmg() {
  # APFS is required on macOS 26 runners; HFS+ create/attach fails with
  # "Operation not permitted" / "no mountable file systems".
  hdiutil create \
    -volname "ELG" \
    -srcfolder "$STAGING" \
    -ov \
    -format UDZO \
    -fs APFS \
    -imagekey zlib-level=9 \
    "$DMG_OUT"
}

for attempt in 1 2 3 4 5; do
  if create_dmg; then
    echo "Wrote $DMG_OUT"
    exit 0
  fi
  echo "hdiutil create failed (attempt $attempt/5), retrying..." >&2
  sleep $((attempt * 3))
done

echo "hdiutil create failed after 5 attempts" >&2
exit 1
