"""Build a multi-page DMG background TIFF from an arrow PNG (manual / other apps).

ELG CI uses the committed installer/macos/background.tiff. Regenerate when the
arrow asset changes:

    pip install -r scripts/dmg/requirements.txt
    python scripts/dmg/build_background.py

Optional ICC: place a reference TIFF at installer/macos/icc_source.tiff.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARROW = ROOT / "installer" / "macos" / "arrow.png"
DEFAULT_DEST = ROOT / "installer" / "macos" / "background.tiff"
DEFAULT_ICC_SOURCE = ROOT / "installer" / "macos" / "icc_source.tiff"

CANVAS_1X = (512, 320)
CANVAS_2X = (1024, 640)
ARROW_SIZE_1X = 32
ARROW_SIZE_2X = 64
ARROW_CENTER_1X = (256, 160)
ARROW_CENTER_2X = (512, 320)
DPI_1X = (72.0, 72.0)
DPI_2X = (144.0, 144.0)
ICC_TAG = 34675


def _icc_profile(source: Path | None) -> bytes | None:
    if source is None or not source.is_file():
        return None
    with Image.open(source) as ref:
        ref.seek(0)
        return ref.info.get("icc_profile")


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


def build_background(
    arrow_source: Path,
    dest: Path,
    *,
    icc_source: Path | None = DEFAULT_ICC_SOURCE,
) -> Path:
    arrow = Image.open(arrow_source).convert("RGBA")
    icc = _icc_profile(icc_source)

    page_1x = Image.new("RGBA", CANVAS_1X, (255, 255, 255, 255))
    _paste_centered_arrow(page_1x, arrow, size=ARROW_SIZE_1X, center=ARROW_CENTER_1X)

    page_2x = Image.new("RGB", CANVAS_2X, "white")
    _paste_centered_arrow(page_2x, arrow, size=ARROW_SIZE_2X, center=ARROW_CENTER_2X)

    dest.parent.mkdir(parents=True, exist_ok=True)

    page0 = np.asarray(page_1x)
    page1 = np.asarray(page_2x)
    extratags = [(ICC_TAG, "B", None, icc, True)] if icc else None

    with tifffile.TiffWriter(dest) as writer:
        writer.write(
            page0,
            compression="zlib",
            photometric="rgb",
            extrasamples=[tifffile.EXTRASAMPLE.UNASSALPHA],
            resolution=(DPI_1X[0], DPI_1X[1]),
            resolutionunit="INCH",
            extratags=extratags,
        )
        writer.write(
            page1,
            compression="zlib",
            photometric="rgb",
            resolution=(DPI_2X[0], DPI_2X[1]),
            resolutionunit="INCH",
        )

    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build DMG background TIFF from arrow PNG")
    parser.add_argument("--arrow", type=Path, default=DEFAULT_ARROW)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--icc-source", type=Path, default=DEFAULT_ICC_SOURCE)
    args = parser.parse_args()

    if not args.arrow.is_file():
        raise SystemExit(f"Missing arrow asset: {args.arrow}")

    path = build_background(args.arrow, args.dest, icc_source=args.icc_source)
    with Image.open(path) as im:
        for i in range(im.n_frames):
            im.seek(i)
            print(f"page {i}: {im.size} {im.mode} dpi={im.info.get('dpi')}")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
