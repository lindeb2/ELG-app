"""Centralized access to shared build-time secrets (not per-user)."""
from __future__ import annotations

import importlib.util
import os
from types import ModuleType

from runtime_paths import bundle_dir, is_packaged_build

_rs: ModuleType | None = None


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

    if is_packaged_build():
        path = bundle_dir() / "runtime_secrets.py"
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


def get_discord_bot_token() -> str:
    """A per-user local token (see meeting_recorder_secrets.py - set via
    Settings -> Meeting Recorder) takes priority over any build-time/env
    token, since it's the more specific, more likely to be intentional
    configuration for this particular install."""
    from meeting_recorder_secrets import get_local_discord_bot_token

    local_token = get_local_discord_bot_token()
    if local_token:
        return local_token
    return _get("DISCORD_BOT_TOKEN", "ELG_DISCORD_BOT_TOKEN")


def has_discord_bot_token_configured() -> bool:
    """Non-raising check for UI status - is *some* bot token available
    (local, build-time, or env var)? See get_discord_bot_token for the
    resolution order."""
    try:
        get_discord_bot_token()
    except RuntimeError:
        return False
    return True


def get_discord_guild_id() -> int:
    return int(_get("DISCORD_GUILD_ID", "ELG_DISCORD_GUILD_ID"))
