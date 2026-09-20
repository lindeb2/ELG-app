"""Build-time secrets for Meeting Host (mirrors src/app_secrets.py)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

_rs: ModuleType | None = None


def _bundle_dir() -> Path:
    return Path(os.path.dirname(os.path.abspath(sys.executable)))


def _load_runtime_secrets() -> ModuleType | None:
    global _rs
    if _rs is not None:
        return _rs

    try:
        from meeting_host import runtime_secrets  # type: ignore[attr-defined]
    except ImportError:
        runtime_secrets = None

    if runtime_secrets is not None:
        _rs = runtime_secrets
        return _rs

    path = _bundle_dir() / "meeting_host" / "runtime_secrets.py"
    if path.is_file():
        spec = importlib.util.spec_from_file_location("meeting_host_runtime_secrets", path)
        if spec is not None and spec.loader is not None:
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
            f"meeting_host/runtime_secrets.py (gitignored), or set {env_name}."
        )
    return str(value)


def get_discord_bot_token() -> str:
    return _get("DISCORD_BOT_TOKEN", "ELG_DISCORD_BOT_TOKEN")


def get_discord_guild_id() -> int:
    return int(_get("DISCORD_GUILD_ID", "ELG_DISCORD_GUILD_ID"))


def get_mongodb_uri() -> str:
    return _get("MONGODB_URI", "ELG_MONGODB_URI")
