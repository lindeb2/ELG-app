"""First-launch setup UI."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from app_config import (
    apply_startup_registration,
    merge_app_preferences,
    normalize_app_preferences,
    read_config,
    write_config,
)
from app_preferences_ui import AppBehaviorPreferencesPanel


class SetupFrame(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkFrame, on_complete: Callable[[], None]):
        super().__init__(master, fg_color="#000000", corner_radius=0)
        self._on_complete = on_complete

        config = read_config()

        notif = config.get("notifications") or {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text="Welcome to ELG",
            font=("Arial", 24, "bold"),
        ).grid(row=0, column=0, padx=24, pady=(24, 8), sticky="w")

        scroll = ctk.CTkScrollableFrame(self, fg_color="#000000")
        scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        scroll.grid_columnconfigure(0, weight=1)

        row = 0
        ctk.CTkLabel(
            scroll,
            text="Enter your details to get started.",
            font=("Arial", 14),
            text_color="#B0B0B0",
        ).grid(row=row, column=0, padx=12, pady=(0, 16), sticky="w")
        row += 1

        ctk.CTkLabel(scroll, text="Username", anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 4), sticky="ew"
        )
        row += 1
        self._username_entry = ctk.CTkEntry(scroll)
        self._username_entry.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
        self._username_entry.insert(0, config.get("user") or "")
        row += 1

        ctk.CTkLabel(scroll, text="Discord name", anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 4), sticky="ew"
        )
        row += 1
        self._discord_entry = ctk.CTkEntry(scroll)
        self._discord_entry.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
        self._discord_entry.insert(0, config.get("discordname") or "")
        row += 1

        ctk.CTkLabel(scroll, text="Notification secret", anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 4), sticky="ew"
        )
        row += 1
        self._secret_entry = ctk.CTkEntry(scroll, show="•")
        self._secret_entry.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
        self._secret_entry.insert(0, notif.get("secret") or "")
        row += 1

        self._notifications_var = ctk.BooleanVar(
            value=bool(notif.get("enabled", True))
        )
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

        self._behavior_panel = AppBehaviorPreferencesPanel(scroll, compact=True)
        self._behavior_panel.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="ew")
        self._behavior_panel.load_from(normalize_app_preferences(config.get("app")))
        row += 1

        self._error_label = ctk.CTkLabel(scroll, text="", font=("Arial", 13), text_color="#FF4444")
        self._error_label.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="w")
        row += 1

        self._continue_btn = ctk.CTkButton(scroll, text="Continue", command=self._submit)
        self._continue_btn.grid(row=row, column=0, padx=12, pady=(0, 16), sticky="ew")

        for entry in (self._username_entry, self._discord_entry, self._secret_entry):
            entry.bind("<Return>", lambda _event: self._submit())

    def set_continue_enabled(self, enabled: bool) -> None:
        self._continue_btn.configure(state="normal" if enabled else "disabled")

    def _submit(self) -> None:
        username = self._username_entry.get().strip()
        discordname = self._discord_entry.get().strip()
        if not username:
            self._error_label.configure(text="Username is required.")
            return
        if not discordname:
            self._error_label.configure(text="Discord name is required.")
            return

        try:
            config = read_config()
        except OSError as exc:
            self._error_label.configure(text=f"Could not read config: {exc}")
            return

        notif = config.get("notifications") or {}
        notif["secret"] = self._secret_entry.get().strip()
        notif["enabled"] = bool(self._notifications_var.get())
        if "gae_url" not in notif:
            notif["gae_url"] = "https://training-bot-450717.appspot.com/notify"

        config["user"] = username
        config["discordname"] = discordname
        config["notifications"] = notif
        app_prefs = self._behavior_panel.values()
        config = merge_app_preferences(config, app_prefs)

        try:
            write_config(config)
        except OSError as exc:
            self._error_label.configure(text=f"Could not save config: {exc}")
            return

        apply_startup_registration(
            enabled=app_prefs["launch_at_startup"],
            minimized=app_prefs["launch_minimized_to_tray"],
        )

        self._on_complete()
