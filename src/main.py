# nuitka-project: --standalone
# nuitka-project: --assume-yes-for-downloads
# nuitka-project: --enable-plugin=tk-inter
# nuitka-project: --include-package-data=customtkinter
# nuitka-project: --include-package-data=tzdata
# nuitka-project: --include-package-data=pystray
# nuitka-project: --include-package-data=certifi
# nuitka-project: --include-package=certifi
# nuitka-project: --nofollow-import-to=tkinter.test
# nuitka-project: --company-name=ELG Studio
# nuitka-project: --product-name=ELG
# nuitka-project: --file-description=ELG
# nuitka-project: --copyright=ELG Studio
# nuitka-project-if: {OS} == "Windows":
#    nuitka-project: --lto=no
#    nuitka-project: --low-memory
# nuitka-project-if: {OS} != "Windows":
#    nuitka-project: --lto=yes
# nuitka-project: --include-data-files=nuitka/icons/elg.ico=elg.ico
# nuitka-project-if: {OS} == "Windows":
#    nuitka-project: --windows-console-mode=disable
#    nuitka-project: --windows-icon-from-ico=nuitka/icons/elg.ico
# nuitka-project-if: {OS} == "Darwin":
#    nuitka-project: --macos-create-app-bundle
#    nuitka-project: --macos-app-icon=nuitka/icons/elg.icns
#    nuitka-project: --macos-app-name=ELG
#    nuitka-project: --output-filename=ELG
"""Single entry point for the ELG desktop app."""
from __future__ import annotations

import argparse
import os
import sys
import tkinter as tk

import customtkinter as ctk

import timetable_db
from app_config import app_preferences_from_config, read_config
from app_shell import AppShell
from app_update import schedule_automatic_checks
from setup_window import SetupFrame
from single_instance import SingleInstanceGuard
from update_dialog import show_update_dialog
from window_chrome import (
    apply_app_title_bar_chrome,
    configure_app_icon,
    configure_window_title,
    deactivate_ctk_title_bar_manipulation,
)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_APP_BG = "#000000"


def load_config() -> dict:
    return read_config()


def _sync_db_user() -> None:
    timetable_db.sync_user()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ELG desktop app")
    parser.add_argument(
        "--started-at-login",
        action="store_true",
        help="Set when launched from the Windows startup registry entry.",
    )
    parser.add_argument(
        "--minimized-tray",
        action="store_true",
        help="Start hidden in the system tray.",
    )
    return parser.parse_args()


def _resolve_startup_view(app_prefs: dict) -> str:
    view = app_prefs.get("startup_view", "timetable")
    return view if view in ("timetable", "statistics") else "timetable"


def _should_start_minimized(app_prefs: dict, args: argparse.Namespace) -> bool:
    if args.minimized_tray:
        return True
    if args.started_at_login and app_prefs.get("launch_minimized_to_tray"):
        return True
    return False


def _mount_app_shell(
    root: ctk.CTk,
    container: tk.Frame,
    shell_holder: dict[str, AppShell | None],
    *,
    initial_view: str,
    start_minimized_to_tray: bool,
) -> AppShell:
    shell = AppShell(
        root,
        container,
        initial_view=initial_view,
        start_minimized_to_tray=start_minimized_to_tray,
    )
    shell.grid(row=0, column=0, sticky="nsew")
    shell.mount_initial_view()
    shell_holder["shell"] = shell
    return shell


def _finish_setup(root: ctk.CTk, setup: SetupFrame, shell: AppShell) -> None:
    _sync_db_user()
    app_prefs = app_preferences_from_config(read_config())
    shell._initial_view = _resolve_startup_view(app_prefs)
    shell.set_app_preferences(app_prefs)
    setup.grid_remove()
    setup.destroy()
    configure_app_icon(root)
    configure_window_title(root, "ELG")
    apply_app_title_bar_chrome(root)
    shell.mount_initial_view()


def _start_update_scheduler(
    root: ctk.CTk,
    shell_holder: dict[str, AppShell | None],
    instance_guard: SingleInstanceGuard,
) -> None:
    def show_update(release, *, force: bool = False) -> None:
        shell = shell_holder.get("shell")
        if shell is None:
            return
        show_update_dialog(
            shell._window,
            release,
            shell=shell,
            instance_guard=instance_guard,
            force=force,
        )

    manual_check = schedule_automatic_checks(
        root,
        shell_holder=shell_holder,
        instance_guard=instance_guard,
        show_dialog=lambda release, force=False: show_update(release, force=force),
        on_pending_changed=lambda: _refresh_pending_ui(shell_holder),
    )
    shell = shell_holder.get("shell")
    if shell is not None:
        shell.set_manual_update_check(manual_check)
        shell.set_instance_guard(instance_guard)
        shell.refresh_settings_update_controls()


def _refresh_pending_ui(shell_holder: dict[str, AppShell | None]) -> None:
    shell = shell_holder.get("shell")
    if shell is not None:
        shell.refresh_settings_update_controls()


def main() -> None:
    args = _parse_args()

    instance_guard = SingleInstanceGuard()
    if not instance_guard.try_acquire_or_notify():
        return

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    deactivate_ctk_title_bar_manipulation()

    root = ctk.CTk(fg_color=_APP_BG)
    shell_holder: dict[str, AppShell | None] = {"shell": None}

    def _on_second_instance_launch() -> None:
        def restore() -> None:
            shell = shell_holder["shell"]
            if shell is not None:
                shell.restore_after_secondary_launch()
            else:
                if root.state() == "iconic":
                    root.state("normal")
                root.deiconify()
                root.lift()
                root.focus_force()

        root.after(0, restore)

    instance_guard.start_listener(_on_second_instance_launch)

    _sync_db_user()

    config = load_config()
    app_prefs = app_preferences_from_config(config)
    initial_view = _resolve_startup_view(app_prefs)
    start_minimized = _should_start_minimized(app_prefs, args)

    needs_setup = not (config.get("user") or "").strip()
    _map_on_start = not needs_setup and not start_minimized
    if sys.platform.startswith("win") and _map_on_start:
        root.withdraw()
    configure_app_icon(root)
    configure_window_title(root, "ELG")

    container = tk.Frame(root, bg=_APP_BG, highlightthickness=0, bd=0)
    container.pack(fill="both", expand=True)
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)

    if needs_setup:
        root.title("ELG Setup")

        shell = AppShell(
            root,
            container,
            initial_view=initial_view,
            start_minimized_to_tray=False,
        )
        shell.grid(row=0, column=0, sticky="nsew")
        shell_holder["shell"] = shell

        def _on_setup_complete() -> None:
            _finish_setup(root, setup, shell)
            _start_update_scheduler(root, shell_holder, instance_guard)

        setup = SetupFrame(
            container,
            on_complete=_on_setup_complete,
        )
        setup.grid(row=0, column=0, sticky="nsew")
        setup.tkraise()
        setup.set_continue_enabled(False)

        def _preload() -> None:
            shell.switch_view("timetable", update_geometry=True)
            setup.set_continue_enabled(True)

        root.after(0, _preload)
    else:
        def _mount_and_schedule() -> None:
            _mount_app_shell(
                root,
                container,
                shell_holder,
                initial_view=initial_view,
                start_minimized_to_tray=start_minimized,
            )
            _start_update_scheduler(root, shell_holder, instance_guard)

        root.after(0, _mount_and_schedule)

    if sys.platform.startswith("win") and _map_on_start:
        root.after(0, lambda: (apply_app_title_bar_chrome(root), root.deiconify()))
    elif not start_minimized:
        root.after(150, lambda: apply_app_title_bar_chrome(root))

    try:
        root.mainloop()
    finally:
        instance_guard.release()


if __name__ == "__main__":
    main()
