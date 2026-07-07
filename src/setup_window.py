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
from notification_preferences import NotificationPreferencesPanel, fetch_notification_prefs


class SetupFrame(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkFrame, on_complete: Callable[[], None]):
        super().__init__(master, fg_color="#000000", corner_radius=0)
        self._on_complete = on_complete

        config = read_config()

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

        ctk.CTkLabel(scroll, text="Discord notifications", font=("Arial", 14, "bold"), anchor="w").grid(
            row=row, column=0, padx=12, pady=(0, 8), sticky="ew"
        )
        row += 1

        username = config.get("user") or ""
        prefs = fetch_notification_prefs(username)
        self._discord_entry.insert(0, str(prefs.get("discord_user_id") or ""))
        self._notification_panel = NotificationPreferencesPanel(
            scroll,
            username=username,
            prefs=prefs,
            compact=True,
            include_discord_account=False,
        )
        self._notification_panel.grid(row=row, column=0, padx=12, pady=(0, 12), sticky="ew")
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

        self._username_entry.bind("<Return>", lambda _event: self._submit())

    def set_continue_enabled(self, enabled: bool) -> None:
        self._continue_btn.configure(state="normal" if enabled else "disabled")

    def _submit(self) -> None:
        username = self._username_entry.get().strip()
        if not username:
            self._error_label.configure(text="Username is required.")
            return

        try:
            config = read_config()
        except OSError as exc:
            self._error_label.configure(text=f"Could not read config: {exc}")
            return

        config["user"] = username
        app_prefs = self._behavior_panel.values()
        config = merge_app_preferences(config, app_prefs)

        try:
            write_config(config)
        except OSError as exc:
            self._error_label.configure(text=f"Could not save config: {exc}")
            return

        try:
            self._notification_panel.username = username
            self._notification_panel.save(
                username,
                discord_user_id=self._discord_entry.get().strip() or None,
            )
        except Exception as exc:
            self._error_label.configure(text=f"Could not save notification prefs: {exc}")
            return

        apply_startup_registration(
            enabled=app_prefs["launch_at_startup"],
            minimized=app_prefs["launch_minimized_to_tray"],
        )

        self._on_complete()
