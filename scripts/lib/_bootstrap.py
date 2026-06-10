"""Add src/ and scripts/lib/ to sys.path for admin scripts."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LIB = Path(__file__).resolve().parent
for path in (_ROOT / "src", _LIB):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)
