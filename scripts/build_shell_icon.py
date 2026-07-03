"""Generate platform icon files (elg.ico, elg.icns) from the master PNG.

Run after changing the master icon:

    python scripts/build_shell_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "ELG Studio 0.1_1024_clean_rounded.png"
ICO_DEST = ROOT / "nuitka" / "icons" / "elg.ico"
ICNS_DEST = ROOT / "nuitka" / "icons" / "elg.icns"
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    ICO_DEST.parent.mkdir(parents=True, exist_ok=True)

    image.resize((256, 256), Image.Resampling.LANCZOS).save(
        ICO_DEST, format="ICO", sizes=ICO_SIZES
    )
    print(f"Wrote {ICO_DEST}")

    image.save(ICNS_DEST, format="ICNS")
    print(f"Wrote {ICNS_DEST}")


if __name__ == "__main__":
    main()
