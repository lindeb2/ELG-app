"""Single entry point for the ELG desktop app."""
from __future__ import annotations

import json
import os

import customtkinter as ctk

import timetable_db
from app_shell import AppShell
from setup_window import SetupFrame

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
_ICON_PATH = os.path.join(_SCRIPT_DIR, "ELG Studio 0.1_16_clean_big.ico")
_APP_BG = "#000000"


def load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as file:
        return json.load(file)


def apply_dwm_theming(window: ctk.CTk) -> None:
    try:
        from ctypes import byref, c_int, sizeof, windll

        hwnd = windll.user32.GetParent(window.winfo_id())  # type: ignore[attr-defined]
        windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, byref(c_int(0x00000000)), sizeof(c_int))  # type: ignore[attr-defined]
        windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, byref(c_int(0x00000000)), sizeof(c_int))  # type: ignore[attr-defined]
    except (ImportError, AttributeError, OSError):
        print("DWM API not available, skipping window attribute settings.")


def _sync_db_user() -> None:
    with open(_CONFIG_PATH, encoding="utf-8") as file:
        timetable_db.user = json.load(file)["user"]


def _mount_app_shell(root: ctk.CTk, container: ctk.CTkFrame) -> None:
    shell = AppShell(root, container)
    shell.grid(row=0, column=0, sticky="nsew")
    shell.switch_view("timetable")


def _finish_setup(root: ctk.CTk, setup: SetupFrame) -> None:
    _sync_db_user()
    setup.grid_remove()
    setup.destroy()
    root.title("ELG")


def main() -> None:
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk(fg_color=_APP_BG)
    root.iconbitmap(_ICON_PATH)
    apply_dwm_theming(root)

    container = ctk.CTkFrame(root, fg_color=_APP_BG, corner_radius=0)
    container.pack(fill="both", expand=True)
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)

    needs_setup = not (load_config().get("discordname") or "").strip()

    if needs_setup:
        root.title("ELG Setup")
        root.geometry("420x440")

        shell = AppShell(root, container)
        shell.grid(row=0, column=0, sticky="nsew")

        setup = SetupFrame(
            container,
            on_complete=lambda: _finish_setup(root, setup),
            config_path=_CONFIG_PATH,
        )
        setup.grid(row=0, column=0, sticky="nsew")
        setup.tkraise()
        setup.set_continue_enabled(False)

        def _preload() -> None:
            shell.switch_view("timetable", update_geometry=False)
            setup.set_continue_enabled(True)

        root.after(0, _preload)
    else:
        root.title("ELG")
        root.after(0, lambda: _mount_app_shell(root, container))

    root.mainloop()


if __name__ == "__main__":
    main()
