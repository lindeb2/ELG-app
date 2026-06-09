"""Atlas App Services commit path — deprecated (EOL September 2025).

MongoDB removed Custom HTTPS Endpoints and the Data API. Desktop apps should use
the local PyMongo path in log_commit.py with atlas_commit.enabled=false.
"""
from __future__ import annotations

import json
import os

EOL_MESSAGE = (
    "Atlas App Services HTTPS endpoints are no longer available (EOL September 2025). "
    'Set "atlas_commit": { "enabled": false } in config.json to use the local '
    "PyMongo commit path, which is the supported approach."
)

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
_config_path = os.path.join(_project_root, "config.json")


def atlas_commit_settings() -> None:
    """No-op when disabled; raises with guidance when enabled."""
    with open(_config_path, encoding="utf-8") as file:
        cfg = json.load(file).get("atlas_commit") or {}
    if cfg.get("enabled"):
        raise RuntimeError(EOL_MESSAGE)
