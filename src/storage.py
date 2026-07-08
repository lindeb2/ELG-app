"""Per-user data file with atomic writes and schema versioning."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

from runtime_paths import is_packaged_build

APP_NAME = "ELG-app"
APP_AUTHOR = "ELG Studio"
CURRENT_SCHEMA_VERSION = 1

_DEFAULT_APP = {
    "close_action": "tray",
    "launch_at_startup": True,
    "launch_minimized_to_tray": True,
    "startup_view": "timetable",
}


def get_data_dir() -> Path:
    path = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_file() -> Path:
    return get_data_dir() / "data.json"


def _default_payload() -> dict:
    return {
        "user": "",
        "discordname": "",
        "app": dict(_DEFAULT_APP),
    }


def _default_data() -> dict:
    return {"schema_version": CURRENT_SCHEMA_VERSION, "data": _default_payload()}


def _legacy_config_paths() -> list[Path]:
    paths: list[Path] = []
    script_dir = Path(__file__).resolve().parent
    paths.append(script_dir.parent / "config.json")
    if is_packaged_build():
        paths.append(Path(sys.executable).resolve().parent / "config.json")
    else:
        paths.append(Path.cwd() / "config.json")
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def migrate_legacy_data() -> None:
    """One-time import from config.json next to the exe or project root."""
    file_path = get_data_file()
    if file_path.exists():
        return

    for legacy_path in _legacy_config_paths():
        if not legacy_path.is_file():
            continue
        if legacy_path.resolve() == file_path.resolve():
            continue
        try:
            with open(legacy_path, encoding="utf-8") as file:
                legacy = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(legacy, dict):
            continue
        save_data({"schema_version": CURRENT_SCHEMA_VERSION, "data": legacy})
        return


def _migrate(data: dict) -> dict:
    version = data.get("schema_version", 0)

    if version == 0 and "data" not in data and (
        "user" in data or "discordname" in data or "notifications" in data
    ):
        payload = {key: value for key, value in data.items() if key != "schema_version"}
        data = {"schema_version": CURRENT_SCHEMA_VERSION, "data": payload}
        version = CURRENT_SCHEMA_VERSION

    if version < CURRENT_SCHEMA_VERSION:
        version = CURRENT_SCHEMA_VERSION

    data["schema_version"] = version
    return data


def load_data() -> dict:
    migrate_legacy_data()

    file_path = get_data_file()
    if not file_path.exists():
        return _default_data()

    try:
        with open(file_path, encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        backup_path = file_path.with_suffix(".json.bak")
        try:
            file_path.replace(backup_path)
        except OSError:
            pass
        return _default_data()

    if not isinstance(data, dict):
        return _default_data()

    if data.get("schema_version", 0) < CURRENT_SCHEMA_VERSION:
        data = _migrate(data)
        save_data(data)

    if "data" not in data or not isinstance(data["data"], dict):
        data = _default_data()

    return data


def save_data(data: dict) -> None:
    data["schema_version"] = CURRENT_SCHEMA_VERSION
    if "data" not in data or not isinstance(data["data"], dict):
        data["data"] = _default_payload()

    file_path = get_data_file()
    tmp_path = file_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp_path, file_path)
