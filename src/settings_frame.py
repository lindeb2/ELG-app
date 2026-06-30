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

        ctk.CTkLabel(scroll, text="Discord name", anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 4), sticky="ew"
        )
        row += 1
        self._discord_entry = ctk.CTkEntry(scroll)
        self._discord_entry.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
        row += 1

        ctk.CTkLabel(scroll, text="Notification secret", anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 4), sticky="ew"
        )
        row += 1
        self._secret_entry = ctk.CTkEntry(scroll, show="•")
        self._secret_entry.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
        row += 1

        self._notifications_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            scroll,
            text="Enable Discord notifications",
            variable=self._notifications_var,
        ).grid(row=row, column=0, padx=12, pady=(0, 20), sticky="w")
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
            text='Enable reload of current view with Ctrl+R',
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

        notif = config.get("notifications") or {}
        app_prefs = app_preferences_from_config(config)

        self._username_entry.delete(0, "end")
        self._username_entry.insert(0, config.get("user") or "")

        self._discord_entry.delete(0, "end")
        self._discord_entry.insert(0, config.get("discordname") or "")

        self._secret_entry.delete(0, "end")
        self._secret_entry.insert(0, notif.get("secret") or "")

        self._notifications_var.set(bool(notif.get("enabled", True)))
        self._behavior_panel.load_from(app_prefs)
        self._ctrl_r_reload_var.set(bool(app_prefs.get("enable_ctrl_r_reload", False)))
        self.refresh_session_controls()

    def _save(self) -> None:
        config = read_config()

        notif = config.get("notifications") or {}
        notif["secret"] = self._secret_entry.get().strip()
        notif["enabled"] = bool(self._notifications_var.get())

        app_prefs = self._behavior_panel.values()
        app_prefs["enable_ctrl_r_reload"] = bool(self._ctrl_r_reload_var.get())

        config["user"] = self._username_entry.get().strip()
        config["discordname"] = self._discord_entry.get().strip()
        config["notifications"] = notif
        config = merge_app_preferences(config, app_prefs)

        write_config(config)
        apply_startup_registration(
            enabled=app_prefs["launch_at_startup"],
            minimized=app_prefs["launch_minimized_to_tray"],
        )

        if self._shell is not None:
            self._shell.set_app_preferences(app_prefs)

        self._status_label.configure(text="Saved.", text_color="#00AD00")
        self.after(2500, lambda: self._status_label.configure(text=""))
