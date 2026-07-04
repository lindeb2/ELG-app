"""Patch a Discord-style .DS_Store template for another app/volume name.

Not used by ELG CI (create-dmg + AppleScript writes a valid .DS_Store per build).
Use when bootstrapping layout from a Discord DMG reference for a new app.

Example (Discord → ELG, names must match template field sizes):

    python scripts/dmg/patch_ds_store.py \\
        --template scripts/dmg/examples/Discord_DS_Store.template \\
        --dest /tmp/ELG_DS_Store
"""
from __future__ import annotations

import argparse
from pathlib import Path

_UTF16_DISCORD_APP = "Discord.app".encode("utf-16-be")


def _patch_discord_to_elg(data: bytes, *, from_app: str, to_app: str) -> bytes:
    """Binary patch tuned for Discord's template → ELG (same field widths)."""
    if from_app != "Discord.app" or to_app != "ELG.app":
        raise ValueError("This patcher currently supports Discord.app → ELG.app only")

    utf16_to = ("ELG.app\x00\x00\x00\x00").encode("utf-16-be")
    if len(_UTF16_DISCORD_APP) != len(utf16_to):
        raise ValueError("ELG.app UTF-16 padding length mismatch")

    out = bytearray(data)
    replacements = (
        (b"\x07Discord", b"\x03ELG\x00\x00\x00\x00"),
        (b"/Volumes/Discord", b"/Volumes/ELG\x00\x00\x00\x00"),
        (b"mes/Discord", b"mes/ELG\x00\x00\x00\x00"),
        (_UTF16_DISCORD_APP, utf16_to),
    )
    for old, new in replacements:
        if len(old) != len(new):
            raise ValueError(f"patch length mismatch: {old!r} -> {new!r}")
        idx = 0
        while True:
            pos = out.find(old, idx)
            if pos == -1:
                break
            out[pos : pos + len(old)] = new
            idx = pos + len(old)

    return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch Discord DMG .DS_Store template")
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--from-app", default="Discord.app")
    parser.add_argument("--to-app", default="ELG.app")
    args = parser.parse_args()

    if not args.template.is_file():
        raise SystemExit(f"Missing template: {args.template}")

    args.dest.parent.mkdir(parents=True, exist_ok=True)
    patched = _patch_discord_to_elg(
        args.template.read_bytes(),
        from_app=args.from_app,
        to_app=args.to_app,
    )
    args.dest.write_bytes(patched)
    print(f"Patched {args.dest} from {args.template}")


if __name__ == "__main__":
    main()
