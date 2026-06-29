"""Load/save config.json and Windows startup registration."""
from __future__ import annotations

import json
import os
import sys
from typing import Literal

CloseAction = Literal["tray", "exit"]
StartupView = Literal["timetable", "statistics"]

DEFAULT_APP_PREFERENCES: dict = {
    "close_action": "tray",
    "launch_at_startup": False,
    "launch_minimized_to_tray": False,
    "startup_view": "timetable",
    "enable_ctrl_r_reload": False,
}

_STARTUP_REG_NAME = "ELG"
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
_MAIN_SCRIPT = os.path.join(_SCRIPT_DIR, "main.py")


def config_path() -> str:
    return _DEFAULT_CONFIG_PATH


def read_config(path: str | None = None) -> dict:
    path = path or _DEFAULT_CONFIG_PATH
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def write_config(config: dict, path: str | None = None) -> None:
    path = path or _DEFAULT_CONFIG_PATH
    with open(path, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=4)
        file.write("\n")


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

    merged["enable_ctrl_r_reload"] = bool(app.get("enable_ctrl_r_reload", False))

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
    python = sys.executable
    if os.name == "nt" and python.lower().endswith("python.exe"):
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.isfile(pythonw):
            python = pythonw

    parts = [f'"{python}"', f'"{_MAIN_SCRIPT}"', "--started-at-login"]
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
