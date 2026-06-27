"""Settings view embedded in the app shell."""
from __future__ import annotations

import json
import os

import customtkinter as ctk

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")


class SettingsFrame(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self._config_path = _CONFIG_PATH

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Settings",
            font=("Arial", 24, "bold"),
        ).grid(row=0, column=0, padx=24, pady=(24, 16), sticky="w")

        ctk.CTkLabel(self, text="Username", anchor="w").grid(
            row=1, column=0, padx=24, pady=(0, 4), sticky="ew"
        )
        self._username_entry = ctk.CTkEntry(self)
        self._username_entry.grid(row=2, column=0, padx=24, pady=(0, 12), sticky="ew")

        ctk.CTkLabel(self, text="Discord name", anchor="w").grid(
            row=3, column=0, padx=24, pady=(0, 4), sticky="ew"
        )
        self._discord_entry = ctk.CTkEntry(self)
        self._discord_entry.grid(row=4, column=0, padx=24, pady=(0, 12), sticky="ew")

        ctk.CTkLabel(self, text="Notification secret", anchor="w").grid(
            row=5, column=0, padx=24, pady=(0, 4), sticky="ew"
        )
        self._secret_entry = ctk.CTkEntry(self, show="•")
        self._secret_entry.grid(row=6, column=0, padx=24, pady=(0, 12), sticky="ew")

        self._notifications_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            self,
            text="Enable Discord notifications",
            variable=self._notifications_var,
        ).grid(row=7, column=0, padx=24, pady=(0, 20), sticky="w")

        ctk.CTkButton(self, text="Save", command=self._save).grid(
            row=8, column=0, padx=24, pady=(0, 8), sticky="w"
        )

        self._status_label = ctk.CTkLabel(self, text="", font=("Arial", 14), text_color="#00AD00")
        self._status_label.grid(row=9, column=0, padx=24, pady=(0, 24), sticky="w")

        self._load_config()

    def _load_config(self) -> None:
        with open(self._config_path, encoding="utf-8") as file:
            config = json.load(file)

        notif = config.get("notifications") or {}

        self._username_entry.delete(0, "end")
        self._username_entry.insert(0, config.get("user") or "")

        self._discord_entry.delete(0, "end")
        self._discord_entry.insert(0, config.get("discordname") or "")

        self._secret_entry.delete(0, "end")
        self._secret_entry.insert(0, notif.get("secret") or "")

        self._notifications_var.set(bool(notif.get("enabled", True)))

    def _save(self) -> None:
        with open(self._config_path, encoding="utf-8") as file:
            config = json.load(file)

        notif = config.get("notifications") or {}
        notif["secret"] = self._secret_entry.get().strip()
        notif["enabled"] = bool(self._notifications_var.get())

        config["user"] = self._username_entry.get().strip()
        config["discordname"] = self._discord_entry.get().strip()
        config["notifications"] = notif

        with open(self._config_path, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=4)
            file.write("\n")

        self._status_label.configure(text="Saved.", text_color="#00AD00")
        self.after(2500, lambda: self._status_label.configure(text=""))
