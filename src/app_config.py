"""Load/save user data and Windows startup registration."""
from __future__ import annotations

import os
import sys
from typing import Literal

from runtime_paths import is_packaged_build
from storage import get_data_file, load_data, save_data

CloseAction = Literal["tray", "exit"]
StartupView = Literal["timetable", "statistics"]

DEFAULT_APP_PREFERENCES: dict = {
    "close_action": "tray",
    "launch_at_startup": True,
    "launch_minimized_to_tray": True,
    "startup_view": "timetable",
    "include_prereleases": False,
    "pending_update": None,
    "last_update_check_at": None,
}

_STARTUP_REG_NAME = "ELG"
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_MAIN_SCRIPT = os.path.join(_SCRIPT_DIR, "main.py")


def config_path() -> str:
    return str(get_data_file())


def read_config(path: str | None = None) -> dict:
    del path
    return dict(load_data()["data"])


def write_config(config: dict, path: str | None = None) -> None:
    del path
    wrapper = load_data()
    wrapper["data"] = dict(config)
    save_data(wrapper)


def normalize_app_preferences(app: dict | None) -> dict:
    merged = dict(DEFAULT_APP_PREFERENCES)
    if not app:
        return merged

    close_action = app.get("close_action", merged["close_action"])
    if close_action in ("tray", "exit"):
        merged["close_action"] = close_action

    merged["launch_at_startup"] = bool(app.get("launch_at_startup", False))
    merged["launch_minimized_to_tray"] = bool(app.get("launch_minimized_to_tray", False))

    startup_view = app.get("startup_view", merged["startup_view"])
    if startup_view in ("timetable", "statistics"):
        merged["startup_view"] = startup_view

    merged["include_prereleases"] = bool(app.get("include_prereleases", False))

    pending = app.get("pending_update")
    if isinstance(pending, dict) and pending.get("version"):
        merged["pending_update"] = dict(pending)
    else:
        merged["pending_update"] = None

    last_checked = app.get("last_update_check_at")
    merged["last_update_check_at"] = str(last_checked) if last_checked else None

    if not merged["launch_at_startup"]:
        merged["launch_minimized_to_tray"] = False

    return merged


def app_preferences_from_config(config: dict) -> dict:
    return normalize_app_preferences(config.get("app"))


def merge_app_preferences(config: dict, app_prefs: dict) -> dict:
    config = dict(config)
    config["app"] = normalize_app_preferences({**config.get("app", {}), **app_prefs})
    return config


def startup_command(*, minimized: bool) -> str:
    if is_packaged_build():
        launch_target = f'"{sys.executable}"'
    else:
        python = sys.executable
        if os.name == "nt" and python.lower().endswith("python.exe"):
            pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
            if os.path.isfile(pythonw):
                python = pythonw
        launch_target = f'"{python}" "{_MAIN_SCRIPT}"'

    parts = [launch_target, "--started-at-login"]
    if minimized:
        parts.append("--minimized-tray")
    return " ".join(parts)


def apply_startup_registration(*, enabled: bool, minimized: bool) -> None:
    if not sys.platform.startswith("win"):
        return

    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        key_path,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                _STARTUP_REG_NAME,
                0,
                winreg.REG_SZ,
                startup_command(minimized=minimized),
            )
        else:
            try:
                winreg.DeleteValue(key, _STARTUP_REG_NAME)
            except FileNotFoundError:
                pass
