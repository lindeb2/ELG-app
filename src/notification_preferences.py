"""Discord notification preference storage and settings UI."""
from __future__ import annotations

import customtkinter as ctk

from timetable_db import status_meeting

NOTIFICATION_PREFS_DOC_ID = "Notification Preferences"

DEFAULT_NOTIFICATION_PREFS = {
    "notifications_enabled": True,
    "notify_others_start": True,
    "notify_others_end": True,
    "notify_own_start": False,
    "notify_own_end": False,
}

_UNSET = object()


def fetch_notification_prefs(username: str) -> dict:
    doc = status_meeting.find_one(
        {"_id": NOTIFICATION_PREFS_DOC_ID},
        projection={f"data.{username}": 1},
    ) or {}
    return (doc.get("data") or {}).get(username) or {}


def save_notification_prefs(
    username: str,
    *,
    notifications_enabled: bool = True,
    notify_others_start: bool,
    notify_others_end: bool,
    notify_own_start: bool,
    notify_own_end: bool,
    discord_user_id: str | None = None,
) -> dict:
    update_fields = {
        f"data.{username}.notifications_enabled": notifications_enabled,
        f"data.{username}.notify_others_start": notify_others_start,
        f"data.{username}.notify_others_end": notify_others_end,
        f"data.{username}.notify_own_start": notify_own_start,
        f"data.{username}.notify_own_end": notify_own_end,
    }
    update: dict = {"$set": update_fields}
    if discord_user_id:
        update["$set"][f"data.{username}.discord_user_id"] = discord_user_id
    else:
        update["$unset"] = {f"data.{username}.discord_user_id": ""}

    status_meeting.update_one(
        {"_id": NOTIFICATION_PREFS_DOC_ID},
        update,
        upsert=True,
    )
    saved = {
        "notifications_enabled": notifications_enabled,
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

    def __init__(
        self,
        parent,
        username: str = "",
        prefs: dict | None = None,
        *,
        compact: bool = False,
        standalone: bool = False,
        include_discord_account: bool = True,
        **kwargs,
    ):
        fg_color = "#181C20" if standalone else "transparent"
        super().__init__(parent, fg_color=fg_color, **kwargs)
        self.username = username
        self._standalone = standalone
        self._prefs = prefs or {}

        self.grid_columnconfigure(0, weight=1)

        label_font = ("Arial", 18) if not compact else ("Arial", 13)
        hint_font = ("Arial", 14) if not compact else ("Arial", 12)
        padx = 24 if standalone else 0

        self._others_start_var = ctk.BooleanVar(value=False)
        self._others_end_var = ctk.BooleanVar(value=False)
        self._own_start_var = ctk.BooleanVar(value=False)
        self._own_end_var = ctk.BooleanVar(value=False)

        row = 0
        if standalone:
            ctk.CTkLabel(
                self,
                text=f"Preferences for {username}",
                font=("Arial", 28, "bold"),
                text_color="white",
            ).grid(row=row, column=0, sticky="w", padx=padx, pady=(24, 8))
            row += 1

        ctk.CTkCheckBox(
            self,
            text="Team member clocking in",
            variable=self._others_start_var,
            font=label_font,
        ).grid(row=row, column=0, sticky="w", padx=padx, pady=4)
        row += 1

        ctk.CTkCheckBox(
            self,
            text="Team member clocking out",
            variable=self._others_end_var,
            font=label_font,
        ).grid(row=row, column=0, sticky="w", padx=padx, pady=4)
        row += 1

        ctk.CTkCheckBox(
            self,
            text="You clocking in",
            variable=self._own_start_var,
            font=label_font,
        ).grid(row=row, column=0, sticky="w", padx=padx, pady=4)
        row += 1

        ctk.CTkCheckBox(
            self,
            text="You clocking out",
            variable=self._own_end_var,
            font=label_font,
        ).grid(row=row, column=0, sticky="w", padx=padx, pady=4)
        row += 1

        if include_discord_account:
            ctk.CTkLabel(
                self,
                text="Discord account",
                font=label_font,
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=padx, pady=(12, 4))
            row += 1

            self._discord_id_entry = ctk.CTkEntry(
                self,
                font=hint_font,
                placeholder_text="Linked automatically via /link",
            )
            self._discord_id_entry.grid(row=row, column=0, sticky="ew", padx=padx, pady=(0, 8))
            row += 1

        if standalone:
            self._save_button = ctk.CTkButton(
                self,
                text="Save",
                font=("Arial", 18),
                fg_color="#000000",
                hover_color="#121212",
                command=self._on_save,
            )
            self._save_button.grid(row=row, column=0, sticky="w", padx=padx, pady=(0, 24))
            row += 1

            self._status_label = ctk.CTkLabel(
                self, text="", font=("Arial", 14), text_color="#00AD00"
            )
            self._status_label.grid(row=row, column=0, sticky="w", padx=padx, pady=(0, 16))

        if prefs is not None:
            self.load_from(prefs)

    def load_from(self, prefs: dict) -> None:
        self._prefs = dict(prefs)
        self._others_start_var.set(
            bool(prefs.get("notify_others_start", DEFAULT_NOTIFICATION_PREFS["notify_others_start"]))
        )
        self._others_end_var.set(
            bool(prefs.get("notify_others_end", DEFAULT_NOTIFICATION_PREFS["notify_others_end"]))
        )
        self._own_start_var.set(
            bool(prefs.get("notify_own_start", DEFAULT_NOTIFICATION_PREFS["notify_own_start"]))
        )
        self._own_end_var.set(
            bool(prefs.get("notify_own_end", DEFAULT_NOTIFICATION_PREFS["notify_own_end"]))
        )
        if hasattr(self, "_discord_id_entry"):
            self._discord_id_entry.delete(0, "end")
            if prefs.get("discord_user_id"):
                self._discord_id_entry.insert(0, str(prefs["discord_user_id"]))

    def values(self, *, discord_user_id: str | None | object = _UNSET) -> dict:
        if discord_user_id is _UNSET:
            if hasattr(self, "_discord_id_entry"):
                discord_user_id = self._discord_id_entry.get().strip() or None
            else:
                discord_user_id = self._prefs.get("discord_user_id")
        return {
            "notifications_enabled": bool(
                self._prefs.get("notifications_enabled", DEFAULT_NOTIFICATION_PREFS["notifications_enabled"])
            ),
            "notify_others_start": bool(self._others_start_var.get()),
            "notify_others_end": bool(self._others_end_var.get()),
            "notify_own_start": bool(self._own_start_var.get()),
            "notify_own_end": bool(self._own_end_var.get()),
            "discord_user_id": discord_user_id,
        }

    def save(self, username: str, *, discord_user_id: str | None | object = _UNSET) -> dict:
        values = self.values(discord_user_id=discord_user_id)
        saved = save_notification_prefs(
            username,
            notifications_enabled=values["notifications_enabled"],
            notify_others_start=values["notify_others_start"],
            notify_others_end=values["notify_others_end"],
            notify_own_start=values["notify_own_start"],
            notify_own_end=values["notify_own_end"],
            discord_user_id=values["discord_user_id"],
        )
        self._prefs = saved
        return saved

    def _on_save(self):
        try:
            self._prefs = self.save(self.username)
            self._status_label.configure(text="Saved.", text_color="#00AD00")
        except Exception as exc:
            self._status_label.configure(text=f"Save failed: {exc}", text_color="#FF4444")
