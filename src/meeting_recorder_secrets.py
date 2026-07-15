"""Per-installation local storage for the Meeting Recorder's Discord bot token.

Distinct from app_secrets.py/runtime_secrets.py: those hold *build-time*
secrets baked into every installer by CI (see .github/workflows/build.yml's
write-runtime-secrets step) and are identical across every install of a
given build. The Meeting Recorder's bot token is the opposite - each user
who enables the feature runs their own bot against their own Discord
server, so it's entered once per installation (see meeting_recorder_setup.py)
and kept only on that machine.

Storage lives under the same platformdirs per-user data directory as
storage.py/meeting_recorder_paths.py, but in its own file (not data.json)
so it never gets swept up with ordinary preferences. Never transmitted
anywhere - read locally by app_secrets.get_discord_bot_token() only, which
meeting_recorder.py uses to open the Discord bot connection itself.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from platformdirs import user_data_dir

from storage import APP_AUTHOR, APP_NAME

_FILE_NAME = "meeting_recorder_secrets.json"


def _secrets_path() -> Path:
    path = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path / _FILE_NAME


def _read() -> dict:
    path = _secrets_path()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    """tmp + os.replace, matching storage.py's save_data pattern."""
    path = _secrets_path()
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp_path, path)
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def get_local_discord_bot_token() -> str | None:
    token = _read().get("discord_bot_token")
    return token or None


def has_local_discord_bot_token() -> bool:
    return bool(get_local_discord_bot_token())


def set_local_discord_bot_token(token: str) -> None:
    token = token.strip()
    if not token:
        raise ValueError("Discord bot token cannot be empty.")
    data = _read()
    data["discord_bot_token"] = token
    _write(data)


def clear_local_discord_bot_token() -> None:
    data = _read()
    if "discord_bot_token" not in data:
        return
    del data["discord_bot_token"]
    _write(data)
