"""Add src/ and scripts/lib/ to sys.path for admin scripts."""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for sub in ("src", "scripts/lib"):
    path = str(_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)
