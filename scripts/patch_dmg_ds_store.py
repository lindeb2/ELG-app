"""Patch Discord's .DS_Store template for the ELG DMG layout."""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "installer" / "macos" / "DS_Store.template"
FALLBACK_TEMPLATE = ROOT / "Temp" / "Discord_.DS_Store"
DEST = ROOT / "installer" / "macos" / ".DS_Store"

_UTF16_DISCORD_APP = "Discord.app".encode("utf-16-be")


def _binary_patch(data: bytes) -> bytes:
    out = bytearray(data)

    replacements = (
        (b"\x07Discord", b"\x03ELG\x00\x00\x00\x00"),
        (b"/Volumes/Discord", b"/Volumes/ELG\x00\x00\x00\x00"),
        (b"mes/Discord", b"mes/ELG\x00\x00\x00\x00"),
    )
    for old, new in replacements:
        if len(old) != len(new):
            raise ValueError(f"alias patch length mismatch: {old!r} -> {new!r}")
        idx = 0
        while True:
            pos = out.find(old, idx)
            if pos == -1:
                break
            out[pos : pos + len(old)] = new
            idx = pos + len(old)

    pos = out.find(_UTF16_DISCORD_APP)
    if pos == -1:
        raise ValueError("Discord.app UTF-16 filename not found in DS_Store template")
    padded = ("ELG.app\x00\x00\x00\x00").encode("utf-16-be")
    if len(padded) != len(_UTF16_DISCORD_APP):
        raise ValueError("ELG.app UTF-16 padding length mismatch")
    out[pos : pos + len(_UTF16_DISCORD_APP)] = padded
    return bytes(out)


def patch_ds_store(template: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_binary_patch(template.read_bytes()))


def main() -> None:
    template = TEMPLATE if TEMPLATE.is_file() else FALLBACK_TEMPLATE
    if not template.is_file():
        raise SystemExit(f"Missing DS_Store template: {template}")
    if not TEMPLATE.is_file() and FALLBACK_TEMPLATE.is_file():
        shutil.copy2(FALLBACK_TEMPLATE, TEMPLATE)
    patch_ds_store(template, DEST)
    print(f"Patched {DEST} from {template}")


if __name__ == "__main__":
    main()
