"""Centralized access to shared build-time secrets (not per-user)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

_rs: ModuleType | None = None


def _frozen_bundle_dir() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(os.path.dirname(os.path.abspath(sys.executable)))


def _load_runtime_secrets() -> ModuleType | None:
    global _rs
    if _rs is not None:
        return _rs

    try:
        import runtime_secrets
    except ImportError:
        runtime_secrets = None

    if runtime_secrets is not None:
        _rs = runtime_secrets
        return _rs

    if getattr(sys, "frozen", False):
        path = _frozen_bundle_dir() / "runtime_secrets.py"
        if not path.is_file():
            return None
        spec = importlib.util.spec_from_file_location("runtime_secrets", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _rs = module
        return _rs

    return None


def _get(name: str, env_name: str) -> str:
    rs = _load_runtime_secrets()
    value = getattr(rs, name, None) if rs else None
    if not value:
        value = os.environ.get(env_name)
    if not value:
        raise RuntimeError(
            f"Missing secret '{name}'. For local dev, add it to "
            f"src/runtime_secrets.py (gitignored), or set the {env_name} env var."
        )
    return value


def get_mongodb_uri() -> str:
    return _get("MONGODB_URI", "ELG_MONGODB_URI")


def get_gh_models_token() -> str:
    return _get("GH_MODELS_TOKEN", "ELG_GH_MODELS_TOKEN")


def get_gae_url() -> str:
    return _get("GAE_URL", "ELG_GAE_URL")


def get_notifications_secret() -> str:
    return _get("NOTIFICATIONS_SECRET", "ELG_NOTIFICATIONS_SECRET")
