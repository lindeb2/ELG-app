"""Centralized access to shared build-time secrets (not per-user)."""
from __future__ import annotations

import os

try:
    import runtime_secrets as _rs
except ImportError:
    _rs = None


def _get(name: str, env_name: str) -> str:
    value = getattr(_rs, name, None) if _rs else None
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
