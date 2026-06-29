"""Single entry point for the ELG desktop app."""
from __future__ import annotations

import json
import os
import sys
import tkinter as tk

import customtkinter as ctk

import timetable_db
from app_shell import AppShell
from setup_window import SetupFrame
from window_chrome import (
    apply_app_title_bar_chrome,
    configure_app_icon,
    configure_window_title,
    deactivate_ctk_title_bar_manipulation,
)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")
_ICON_PATH = os.path.join(_SCRIPT_DIR, "ELG Studio 0.1_16_clean_big.ico")
_APP_BG = "#000000"


def load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as file:
        return json.load(file)


def _sync_db_user() -> None:
    with open(_CONFIG_PATH, encoding="utf-8") as file:
        timetable_db.user = json.load(file)["user"]


def _mount_app_shell(root: ctk.CTk, container: tk.Frame) -> None:
    shell = AppShell(root, container)
    shell.grid(row=0, column=0, sticky="nsew")
    shell.switch_view("timetable")


def _finish_setup(root: ctk.CTk, setup: SetupFrame) -> None:
    _sync_db_user()
    setup.grid_remove()
    setup.destroy()
    configure_app_icon(root, _ICON_PATH)
    configure_window_title(root, "ELG")
    apply_app_title_bar_chrome(root)


def main() -> None:
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    deactivate_ctk_title_bar_manipulation()

    root = ctk.CTk(fg_color=_APP_BG)
    needs_setup = not (load_config().get("discordname") or "").strip()
    _map_on_start = not needs_setup
    if sys.platform.startswith("win") and _map_on_start:
        root.withdraw()
    configure_app_icon(root, _ICON_PATH)
    configure_window_title(root, "ELG")

    container = tk.Frame(root, bg=_APP_BG, highlightthickness=0, bd=0)
    container.pack(fill="both", expand=True)
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)

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
        root.after(0, lambda: _mount_app_shell(root, container))

    if sys.platform.startswith("win") and _map_on_start:
        root.after(0, lambda: (apply_app_title_bar_chrome(root), root.deiconify()))
    else:
        root.after(150, lambda: apply_app_title_bar_chrome(root))

    root.mainloop()


if __name__ == "__main__":
    main()
