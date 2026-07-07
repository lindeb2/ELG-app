"""Settings view embedded in the app shell."""
from __future__ import annotations

import customtkinter as ctk

from app_config import (
    apply_startup_registration,
    app_preferences_from_config,
    merge_app_preferences,
    read_config,
    write_config,
)
from app_preferences_ui import AppBehaviorPreferencesPanel
from app_update import format_last_checked, load_pending_update
from app_version import current_version
from platform_keys import primary_modifier_label
from update_dialog import show_update_dialog
from notification_preferences import NotificationPreferencesPanel, fetch_notification_prefs
from session_guard import confirm_discard_session, has_unlogged_time


class SettingsFrame(ctk.CTkFrame):
    def __init__(self, parent, shell=None):
        super().__init__(parent, fg_color="transparent")
        self._shell = shell

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Settings",
            font=("Arial", 24, "bold"),
        ).grid(row=0, column=0, padx=24, pady=(24, 12), sticky="w")

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        scroll.grid_columnconfigure(0, weight=1)

        row = 0
        ctk.CTkLabel(scroll, text="Account", font=("Arial", 16, "bold"), anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 8), sticky="ew"
        )
        row += 1

        ctk.CTkLabel(scroll, text="Username", anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 4), sticky="ew"
        )
        row += 1
        self._username_entry = ctk.CTkEntry(scroll)
        self._username_entry.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
        row += 1

        ctk.CTkLabel(scroll, text="Discord account", anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 4), sticky="ew"
        )
        row += 1
        self._discord_entry = ctk.CTkEntry(
            scroll,
            placeholder_text="Linked automatically via /link",
        )
        self._discord_entry.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
        row += 1

        ctk.CTkLabel(scroll, text="Discord notifications", font=("Arial", 16, "bold"), anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 8), sticky="ew"
        )
        row += 1

        self._notification_panel = NotificationPreferencesPanel(
            scroll,
            compact=True,
            include_discord_account=False,
        )
        self._notification_panel.grid(row=row, column=0, padx=12, pady=(0, 20), sticky="ew")
        row += 1

        ctk.CTkLabel(scroll, text="App behavior", font=("Arial", 16, "bold"), anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 8), sticky="ew"
        )
        row += 1

        self._behavior_panel = AppBehaviorPreferencesPanel(scroll)
        self._behavior_panel.grid(row=row, column=0, padx=12, pady=(0, 16), sticky="ew")
        row += 1

        self._ctrl_r_reload_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            scroll,
            text=f"Enable reload of current view with {primary_modifier_label()}+R",
            variable=self._ctrl_r_reload_var,
        ).grid(row=row, column=0, padx=12, pady=(0, 20), sticky="w")
        row += 1

        ctk.CTkLabel(scroll, text="Timetable session", font=("Arial", 16, "bold"), anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 8), sticky="ew"
        )
        row += 1

        self._discard_btn = ctk.CTkButton(
            scroll,
            text="Discard current session",
            fg_color="#5A1A1A",
            hover_color="#7A2020",
            command=self._discard_session,
        )
        self._discard_btn.grid(row=row, column=0, padx=12, pady=(0, 20), sticky="w")
        row += 1

        ctk.CTkLabel(scroll, text="Updates", font=("Arial", 16, "bold"), anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 8), sticky="ew"
        )
        row += 1

        self._version_label = ctk.CTkLabel(
            scroll,
            text=f"Version: {current_version()}",
            anchor="w",
        )
        self._version_label.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        row += 1

        self._include_prereleases_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            scroll,
            text="Include pre-releases",
            variable=self._include_prereleases_var,
        ).grid(row=row, column=0, padx=12, pady=(0, 8), sticky="w")
        row += 1

        self._last_checked_label = ctk.CTkLabel(
            scroll,
            text="Last checked: Never",
            anchor="w",
            text_color="#B0B0B0",
        )
        self._last_checked_label.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        row += 1

        self._pending_label = ctk.CTkLabel(
            scroll,
            text="",
            anchor="w",
            text_color="#B0B0B0",
        )
        self._pending_label.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        row += 1

        self._install_update_btn = ctk.CTkButton(
            scroll,
            text="Install update",
            command=self._install_pending_update,
        )
        self._install_update_btn.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="w")
        row += 1

        ctk.CTkButton(
            scroll,
            text="Check for updates",
            command=self._check_for_updates,
        ).grid(row=row, column=0, padx=12, pady=(0, 20), sticky="w")
        row += 1

        ctk.CTkButton(scroll, text="Save", command=self._save).grid(
            row=row, column=0, padx=12, pady=(0, 8), sticky="w"
        )
        row += 1

        self._status_label = ctk.CTkLabel(scroll, text="", font=("Arial", 14), text_color="#00AD00")
        self._status_label.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="w")

        self._load_config()

    def refresh_session_controls(self) -> None:
        timetable = self._shell.get_timetable() if self._shell is not None else None
        has_session = has_unlogged_time(timetable)
        self._discard_btn.configure(state="normal" if has_session else "disabled")

    def _discard_session(self) -> None:
        if self._shell is None:
            return
        timetable = self._shell.get_timetable()
        if not has_unlogged_time(timetable):
            return
        if not confirm_discard_session(self.winfo_toplevel(), timetable):
            return
        self._shell.discard_timetable_session()
        self.refresh_session_controls()
        self._status_label.configure(text="Session discarded.", text_color="#B0B0B0")
        self.after(2500, lambda: self._status_label.configure(text=""))

    def _load_config(self) -> None:
        config = read_config()

        app_prefs = app_preferences_from_config(config)

        self._username_entry.delete(0, "end")
        self._username_entry.insert(0, config.get("user") or "")

        username = config.get("user") or ""
        prefs = fetch_notification_prefs(username)
        self._notification_panel.username = username
        self._notification_panel.load_from(prefs)
        self._discord_entry.delete(0, "end")
        if prefs.get("discord_user_id"):
            self._discord_entry.insert(0, str(prefs["discord_user_id"]))
        self._behavior_panel.load_from(app_prefs)
        self._ctrl_r_reload_var.set(bool(app_prefs.get("enable_ctrl_r_reload", False)))
        self._include_prereleases_var.set(bool(app_prefs.get("include_prereleases", False)))
        self._last_checked_label.configure(
            text=f"Last checked: {format_last_checked(app_prefs.get('last_update_check_at'))}"
        )
        self._version_label.configure(text=f"Version: {current_version()}")
        self.refresh_update_controls()
        self.refresh_session_controls()

    def refresh_update_controls(self) -> None:
        release = load_pending_update()
        if release is None:
            self._pending_label.configure(text="")
            self._install_update_btn.grid_remove()
            return
        self._pending_label.configure(text=f"Update {release.version} is ready to install.")
        self._install_update_btn.grid()
        self._install_update_btn.configure(state="normal")

    def _install_pending_update(self) -> None:
        release = load_pending_update()
        if release is None:
            self.refresh_update_controls()
            self._status_label.configure(text="No pending update.", text_color="#FF4444")
            return
        if self._shell is None:
            return
        show_update_dialog(
            self.winfo_toplevel(),
            release,
            shell=self._shell,
            instance_guard=getattr(self._shell, "_instance_guard", None),
        )

    def _check_for_updates(self) -> None:
        if self._shell is None or self._shell._manual_update_check is None:
            self._status_label.configure(
                text="Update checks are unavailable.",
                text_color="#FF4444",
            )
            return

        self._status_label.configure(text="Checking for updates…", text_color="#B0B0B0")

        def on_status(message: str, color: str) -> None:
            self._status_label.configure(text=message, text_color=color)
            config = read_config()
            app_prefs = app_preferences_from_config(config)
            self._last_checked_label.configure(
                text=f"Last checked: {format_last_checked(app_prefs.get('last_update_check_at'))}"
            )
            self.refresh_update_controls()
            if color == "#00AD00":
                self.after(2500, lambda: self._status_label.configure(text=""))

        self._shell._manual_update_check(on_status)

    def _save(self) -> None:
        config = read_config()

        app_prefs = self._behavior_panel.values()
        app_prefs["enable_ctrl_r_reload"] = bool(self._ctrl_r_reload_var.get())
        app_prefs["include_prereleases"] = bool(self._include_prereleases_var.get())

        username = self._username_entry.get().strip()
        config["user"] = username
        config = merge_app_preferences(config, app_prefs)

        write_config(config)
        apply_startup_registration(
            enabled=app_prefs["launch_at_startup"],
            minimized=app_prefs["launch_minimized_to_tray"],
        )

        if self._shell is not None:
            self._shell.set_app_preferences(app_prefs)

        try:
            self._notification_panel.username = username
            self._notification_panel.save(
                username,
                discord_user_id=self._discord_entry.get().strip() or None,
            )
        except Exception as exc:
            self._status_label.configure(
                text=f"Settings saved, but notification prefs failed: {exc}",
                text_color="#FF4444",
            )
            return

        self._status_label.configure(text="Saved.", text_color="#00AD00")
        self.after(2500, lambda: self._status_label.configure(text=""))
