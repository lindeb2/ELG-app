"""Build the multi-page DMG background TIFF from the arrow PNG.

Discord-style layout: white canvas, centered arrow only (no logo).
Page 1 is 512x320 (1x), page 2 is 1024x640 (2x), both at 96 DPI.

Run after changing the arrow asset:

    python scripts/build_dmg_background.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ARROW_SOURCE = ROOT / "src" / "ELG Studio .dmg background arrow.png"
TIFF_DEST = ROOT / "installer" / "macos" / "background.tiff"

CANVAS_1X = (512, 320)
CANVAS_2X = (1024, 640)
ARROW_SIZE_2X = 64
DPI = (96, 96)


def build_background(arrow_source: Path = ARROW_SOURCE, dest: Path = TIFF_DEST) -> Path:
    arrow = Image.open(arrow_source).convert("RGBA")
    arrow_2x = arrow.resize((ARROW_SIZE_2X, ARROW_SIZE_2X), Image.Resampling.LANCZOS)

    canvas_2x = Image.new("RGB", CANVAS_2X, "white")
    paste_x = (CANVAS_2X[0] - ARROW_SIZE_2X) // 2
    paste_y = (CANVAS_2X[1] - ARROW_SIZE_2X) // 2
    canvas_2x.paste(arrow_2x, (paste_x, paste_y), arrow_2x)

    canvas_1x = canvas_2x.resize(CANVAS_1X, Image.Resampling.LANCZOS)

    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas_1x.save(
        dest,
        format="TIFF",
        save_all=True,
        append_images=[canvas_2x],
        compression="tiff_lzw",
        dpi=DPI,
    )
    return dest


def main() -> None:
    if not ARROW_SOURCE.is_file():
        raise SystemExit(f"Missing arrow asset: {ARROW_SOURCE}")
    path = build_background()
    print(f"Wrote {path} (512x320 + 1024x640 @ {DPI[0]} DPI)")


if __name__ == "__main__":
    main()
