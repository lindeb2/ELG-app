"""Frozen-build path helpers (PyInstaller bundle layout)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _frozen_bundle_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(os.path.dirname(os.path.abspath(sys.executable)))
