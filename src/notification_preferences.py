"""Discord notification preference storage and settings UI."""
from __future__ import annotations

import customtkinter as ctk

from timetable_db import status_meeting

NOTIFICATION_PREFS_DOC_ID = "Notification Preferences"


def fetch_notification_prefs(username: str) -> dict:
    doc = status_meeting.find_one(
        {"_id": NOTIFICATION_PREFS_DOC_ID},
        projection={f"data.{username}": 1},
    ) or {}
    return (doc.get("data") or {}).get(username) or {}


def save_notification_prefs(
    username: str,
    *,
    notify_others_start: bool,
    notify_others_end: bool,
    notify_own_start: bool,
    notify_own_end: bool,
    discord_user_id: str | None = None,
) -> dict:
    update_fields = {
        f"data.{username}.notify_others_start": notify_others_start,
        f"data.{username}.notify_others_end": notify_others_end,
        f"data.{username}.notify_own_start": notify_own_start,
        f"data.{username}.notify_own_end": notify_own_end,
    }
    if discord_user_id:
        update_fields[f"data.{username}.discord_user_id"] = discord_user_id

    status_meeting.update_one(
        {"_id": NOTIFICATION_PREFS_DOC_ID},
        {"$set": update_fields},
        upsert=True,
    )
    saved = {
        "notify_others_start": notify_others_start,
        "notify_others_end": notify_others_end,
        "notify_own_start": notify_own_start,
        "notify_own_end": notify_own_end,
    }
    if discord_user_id:
        saved["discord_user_id"] = discord_user_id
    return saved


class NotificationPreferencesPanel(ctk.CTkFrame):
    """Embeddable panel for Discord notification recipient preferences."""

    def __init__(self, parent, username: str, prefs: dict | None = None, **kwargs):
        super().__init__(parent, fg_color="#181C20", **kwargs)
        self.username = username
        self._prefs = prefs if prefs is not None else fetch_notification_prefs(username)

        self.grid_columnconfigure(0, weight=1)

        self._others_start_var = ctk.BooleanVar(
            value=bool(self._prefs.get("notify_others_start", True))
        )
        self._others_end_var = ctk.BooleanVar(
            value=bool(self._prefs.get("notify_others_end", True))
        )
        self._own_start_var = ctk.BooleanVar(
            value=bool(self._prefs.get("notify_own_start", False))
        )
        self._own_end_var = ctk.BooleanVar(
            value=bool(self._prefs.get("notify_own_end", False))
        )

        ctk.CTkLabel(
            self,
            text=f"Preferences for {username}",
            font=("Arial", 28, "bold"),
            text_color="white",
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(24, 8))

        ctk.CTkLabel(
            self,
            text="Choose which session events send you a Discord DM.",
            font=("Arial", 16),
            text_color="#B0B0B0",
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 16))

        ctk.CTkSwitch(
            self,
            text="When others start a session",
            variable=self._others_start_var,
            font=("Arial", 18),
        ).grid(row=2, column=0, sticky="w", padx=24, pady=8)

        ctk.CTkSwitch(
            self,
            text="When others finish a session",
            variable=self._others_end_var,
            font=("Arial", 18),
        ).grid(row=3, column=0, sticky="w", padx=24, pady=8)

        ctk.CTkSwitch(
            self,
            text="When you start a session",
            variable=self._own_start_var,
            font=("Arial", 18),
        ).grid(row=4, column=0, sticky="w", padx=24, pady=8)

        ctk.CTkSwitch(
            self,
            text="When you finish a session",
            variable=self._own_end_var,
            font=("Arial", 18),
        ).grid(row=5, column=0, sticky="w", padx=24, pady=8)

        ctk.CTkLabel(
            self,
            text="Discord user ID",
            font=("Arial", 18),
            text_color="white",
        ).grid(row=6, column=0, sticky="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            self,
            text="Run /link in Discord, or paste your user ID here (Developer Mode → Copy User ID).",
            font=("Arial", 14),
            text_color="#B0B0B0",
            wraplength=560,
            justify="left",
        ).grid(row=7, column=0, sticky="w", padx=24, pady=(0, 8))

        self._discord_id_entry = ctk.CTkEntry(self, width=320, font=("Arial", 16))
        self._discord_id_entry.grid(row=8, column=0, sticky="w", padx=24, pady=(0, 16))
        if self._prefs.get("discord_user_id"):
            self._discord_id_entry.insert(0, str(self._prefs["discord_user_id"]))

        ctk.CTkLabel(
            self,
            text="Record breaks are always posted to the team records channel.",
            font=("Arial", 14),
            text_color="#B0B0B0",
            wraplength=560,
            justify="left",
        ).grid(row=9, column=0, sticky="w", padx=24, pady=(0, 16))

        self._save_button = ctk.CTkButton(
            self,
            text="Save",
            font=("Arial", 18),
            fg_color="#000000",
            hover_color="#121212",
            command=self._on_save,
        )
        self._save_button.grid(row=10, column=0, sticky="w", padx=24, pady=(0, 24))

        self._status_label = ctk.CTkLabel(self, text="", font=("Arial", 14), text_color="#00AD00")
        self._status_label.grid(row=11, column=0, sticky="w", padx=24, pady=(0, 16))

    def _on_save(self):
        discord_user_id = self._discord_id_entry.get().strip() or None
        try:
            self._prefs = save_notification_prefs(
                self.username,
                notify_others_start=bool(self._others_start_var.get()),
                notify_others_end=bool(self._others_end_var.get()),
                notify_own_start=bool(self._own_start_var.get()),
                notify_own_end=bool(self._own_end_var.get()),
                discord_user_id=discord_user_id,
            )
            self._status_label.configure(text="Saved.", text_color="#00AD00")
        except Exception as exc:
            self._status_label.configure(text=f"Save failed: {exc}", text_color="#FF4444")
