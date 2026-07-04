#!/usr/bin/env bash
# Build a styled ELG.dmg using Discord's Finder layout (.DS_Store template).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="${1:-$ROOT/build/macos-arm64/ELG.app}"
DMG_RW="$ROOT/dist-installers/rw.ELG.dmg"
DMG_OUT="$ROOT/dist-installers/ELG.dmg"
BACKGROUND="$ROOT/installer/macos/background.tiff"
DS_STORE="$ROOT/installer/macos/.DS_Store"

python "$ROOT/scripts/build_dmg_background.py"
python "$ROOT/scripts/patch_dmg_ds_store.py"

rm -f "$DMG_RW" "$DMG_OUT"
mkdir -p "$ROOT/dist-installers"

APP_KB=$(du -sk "$APP_PATH" | awk '{print $1}')
DMG_MB=$((APP_KB / 1024 + 20))

hdiutil create \
  -size "${DMG_MB}m" \
  -volname "ELG" \
  -fs HFS+ \
  -fsargs "c=c=64,a=16,e=16" \
  -layout NONE \
  "$DMG_RW" >/dev/null

DEVICE="$(hdiutil attach -readwrite -noverify -noautoopen -nobrowse "$DMG_RW" | grep -E '^/dev/' | sed 1q | awk '{print $1}')"
MOUNT="$(hdiutil info | grep -A1 "$DEVICE" | grep '/Volumes/' | sed 1q | awk '{print $3}')"

cp -R "$APP_PATH" "$MOUNT/"
ln -s /Applications "$MOUNT/Applications"
mkdir -p "$MOUNT/.background"
cp "$BACKGROUND" "$MOUNT/.background/background.tiff"
cp "$DS_STORE" "$MOUNT/.DS_Store"

# Ensure Finder metadata is flushed before detach.
sync
sleep 2
hdiutil detach "$DEVICE"

hdiutil convert "$DMG_RW" -format UDZO -imagekey zlib-level=9 -o "$DMG_OUT" >/dev/null
rm -f "$DMG_RW"
echo "Wrote $DMG_OUT"
