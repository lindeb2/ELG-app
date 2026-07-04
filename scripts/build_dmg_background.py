"""Build the multi-page DMG background TIFF from the arrow PNG.

Discord-style layout: white canvas, centered arrow only (no logo).
Page 1 is 512x320 (1x), page 2 is 1024x640 (2x), both at 96 DPI.
The arrow is drawn at native size per page (64px / 128px), aligned with
the icon row used by create-dmg in the macOS CI workflow.

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
ARROW_SIZE_1X = 64
ARROW_SIZE_2X = 128
# Matches create-dmg icon Y (132) + half of 128px icon size.
ARROW_CENTER_1X = (256, 196)
ARROW_CENTER_2X = (512, 392)
DPI = (96, 96)


def _paste_centered_arrow(
    canvas: Image.Image,
    arrow_source: Image.Image,
    *,
    size: int,
    center: tuple[int, int],
) -> None:
    arrow = arrow_source.resize((size, size), Image.Resampling.LANCZOS)
    paste_x = center[0] - size // 2
    paste_y = center[1] - size // 2
    canvas.paste(arrow, (paste_x, paste_y), arrow)


def build_background(arrow_source: Path = ARROW_SOURCE, dest: Path = TIFF_DEST) -> Path:
    arrow = Image.open(arrow_source).convert("RGBA")

    canvas_1x = Image.new("RGB", CANVAS_1X, "white")
    _paste_centered_arrow(
        canvas_1x,
        arrow,
        size=ARROW_SIZE_1X,
        center=ARROW_CENTER_1X,
    )

    canvas_2x = Image.new("RGB", CANVAS_2X, "white")
    _paste_centered_arrow(
        canvas_2x,
        arrow,
        size=ARROW_SIZE_2X,
        center=ARROW_CENTER_2X,
    )

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
    print(
        f"Wrote {path} ({CANVAS_1X[0]}x{CANVAS_1X[1]} + "
        f"{CANVAS_2X[0]}x{CANVAS_2X[1]} @ {DPI[0]} DPI, "
        f"arrow {ARROW_SIZE_1X}px/{ARROW_SIZE_2X}px)"
    )


if __name__ == "__main__":
    main()
