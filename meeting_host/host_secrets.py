"""Build-time secrets for Meeting Host (mirrors src/app_secrets.py)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from meeting_host.secret_codec import unseal_secret

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


def _optional(name: str, env_name: str) -> str:
    rs = _load_runtime_secrets()
    value = getattr(rs, name, None) if rs else None
    if not value:
        value = os.environ.get(env_name)
    if not value:
        return ""
    return unseal_secret(str(value))


def get_groq_api_key() -> str:
    value = _optional("GROQ_API_KEY", "ELG_GROQ_API_KEY")
    if not value:
        raise RuntimeError(
            "Missing Groq API key. For local dev, set GROQ_API_KEY in "
            "meeting_host/runtime_secrets.py (gitignored) or ELG_GROQ_API_KEY. "
            "A release build seals it into runtime_secrets.py from the "
            "GROQ_API_KEY GitHub secret."
        )
    return value


def baked_transcription_source() -> str:
    """Source pinned into a build. Empty when the build left the choice to config."""
    rs = _load_runtime_secrets()
    value = getattr(rs, "TRANSCRIPTION_SOURCE", None) if rs else None
    return str(value).strip().lower() if value else ""
