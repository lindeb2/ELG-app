"""Nuitka packaged-build path helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_packaged_build() -> bool:
    """True when running a Nuitka build, not from source."""
    return "__compiled__" in globals()


def bundle_dir() -> Path:
    return Path(os.path.dirname(os.path.abspath(sys.executable)))
