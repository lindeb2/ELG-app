"""Load/save user data and Windows startup registration."""
from __future__ import annotations

import os
import struct
import sys
import time
from typing import Literal

from runtime_paths import bundle_dir, is_packaged_build
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
    "last_sidebar_visible": True,
    "last_widget_mode": False,
    "last_active_view": "timetable",
    "last_window_state": None,
}

_STARTUP_REG_NAME = "ELG"
_STARTUP_LEGACY_SHORTCUT_NAME = "ELG.lnk"
_STARTUP_APPROVED_ENABLED = bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


def _startup_approved_disabled() -> bytes:
    filetime = int((time.time() + 11644473600) * 10_000_000)
    return struct.pack("<IQ", 0x03, filetime)


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

    merged["last_sidebar_visible"] = bool(app.get("last_sidebar_visible", True))
    merged["last_widget_mode"] = bool(app.get("last_widget_mode", False))

    last_active_view = app.get("last_active_view", "timetable")
    if last_active_view in ("timetable", "statistics", "meeting", "meeting_points", "settings"):
        merged["last_active_view"] = last_active_view

    last_window_state = app.get("last_window_state")
    if isinstance(last_window_state, dict):
        merged["last_window_state"] = dict(last_window_state)
    else:
        merged["last_window_state"] = None

    if not merged["launch_at_startup"]:
        merged["launch_minimized_to_tray"] = False

    return merged


def app_preferences_from_config(config: dict) -> dict:
    return normalize_app_preferences(config.get("app"))


def merge_app_preferences(config: dict, app_prefs: dict) -> dict:
    config = dict(config)
    config["app"] = normalize_app_preferences({**config.get("app", {}), **app_prefs})
    return config


def _packaged_executable() -> str:
    exe = bundle_dir() / "main.exe"
    if exe.is_file():
        return str(exe)
    return sys.executable


def _startup_arguments(*, minimized: bool) -> str:
    parts = ["--started-at-login"]
    if minimized:
        parts.append("--minimized-tray")
    return " ".join(parts)


def startup_command(*, minimized: bool) -> str:
    launch_target = f'"{_packaged_executable()}"'
    arguments = _startup_arguments(minimized=minimized)
    return f"{launch_target} {arguments}" if arguments else launch_target


def _legacy_startup_shortcut_path() -> str:
    return os.path.join(
        os.environ["APPDATA"],
        r"Microsoft\Windows\Start Menu\Programs\Startup",
        _STARTUP_LEGACY_SHORTCUT_NAME,
    )


def _remove_legacy_startup_shortcut() -> None:
    shortcut = _legacy_startup_shortcut_path()
    if os.path.isfile(shortcut):
        os.remove(shortcut)


def _set_startup_approved(*, scope: Literal["Run", "StartupFolder"], name: str, enabled: bool) -> None:
    import winreg

    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\{scope}"
    try:
        key_handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        if not enabled:
            return
        key_handle = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
    approved = _STARTUP_APPROVED_ENABLED if enabled else _startup_approved_disabled()
    with key_handle as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_BINARY, approved)


def _remove_registry_startup() -> None:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, _STARTUP_REG_NAME)
        except FileNotFoundError:
            pass
    _set_startup_approved(scope="Run", name=_STARTUP_REG_NAME, enabled=False)


def _clear_legacy_startup_entries() -> None:
    _remove_legacy_startup_shortcut()
    _set_startup_approved(scope="StartupFolder", name=_STARTUP_LEGACY_SHORTCUT_NAME, enabled=False)


def apply_startup_registration(*, enabled: bool, minimized: bool) -> None:
    if not sys.platform.startswith("win"):
        return

    _clear_legacy_startup_entries()

    if not is_packaged_build():
        _remove_registry_startup()
        return

    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                _STARTUP_REG_NAME,
                0,
                winreg.REG_SZ,
                startup_command(minimized=minimized),
            )
            _set_startup_approved(scope="Run", name=_STARTUP_REG_NAME, enabled=True)
        else:
            _set_startup_approved(scope="Run", name=_STARTUP_REG_NAME, enabled=False)


def sync_startup_registration_from_config() -> None:
    if not is_packaged_build():
        return
    prefs = app_preferences_from_config(read_config())
    apply_startup_registration(
        enabled=prefs["launch_at_startup"],
        minimized=prefs["launch_minimized_to_tray"],
    )
