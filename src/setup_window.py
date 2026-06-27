"""First-launch setup UI."""
from __future__ import annotations

import json
from typing import Callable

import customtkinter as ctk


class SetupFrame(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkFrame, on_complete: Callable[[], None], config_path: str):
        super().__init__(master, fg_color="#000000", corner_radius=0)
        self._on_complete = on_complete
        self._config_path = config_path

        with open(config_path, encoding="utf-8") as file:
            config = json.load(file)

        notif = config.get("notifications") or {}

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Welcome to ELG",
            font=("Arial", 24, "bold"),
        ).grid(row=0, column=0, padx=24, pady=(24, 8), sticky="w")

        ctk.CTkLabel(
            self,
            text="Enter your details to get started.",
            font=("Arial", 14),
            text_color="#B0B0B0",
        ).grid(row=1, column=0, padx=24, pady=(0, 16), sticky="w")

        ctk.CTkLabel(self, text="Username", anchor="w").grid(
            row=2, column=0, padx=24, pady=(0, 4), sticky="ew"
        )
        self._username_entry = ctk.CTkEntry(self)
        self._username_entry.grid(row=3, column=0, padx=24, pady=(0, 12), sticky="ew")
        self._username_entry.insert(0, config.get("user") or "")

        ctk.CTkLabel(self, text="Discord name", anchor="w").grid(
            row=4, column=0, padx=24, pady=(0, 4), sticky="ew"
        )
        self._discord_entry = ctk.CTkEntry(self)
        self._discord_entry.grid(row=5, column=0, padx=24, pady=(0, 12), sticky="ew")
        self._discord_entry.insert(0, config.get("discordname") or "")

        ctk.CTkLabel(self, text="Notification secret", anchor="w").grid(
            row=6, column=0, padx=24, pady=(0, 4), sticky="ew"
        )
        self._secret_entry = ctk.CTkEntry(self, show="•")
        self._secret_entry.grid(row=7, column=0, padx=24, pady=(0, 12), sticky="ew")
        self._secret_entry.insert(0, notif.get("secret") or "")

        self._notifications_var = ctk.BooleanVar(
            value=bool(notif.get("enabled", True))
        )
        ctk.CTkCheckBox(
            self,
            text="Enable Discord notifications",
            variable=self._notifications_var,
        ).grid(row=8, column=0, padx=24, pady=(0, 20), sticky="w")

        self._error_label = ctk.CTkLabel(self, text="", font=("Arial", 13), text_color="#FF4444")
        self._error_label.grid(row=9, column=0, padx=24, pady=(0, 8), sticky="w")

        self._continue_btn = ctk.CTkButton(self, text="Continue", command=self._submit)
        self._continue_btn.grid(row=10, column=0, padx=24, pady=(0, 24), sticky="ew")

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
            with open(self._config_path, encoding="utf-8") as file:
                config = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
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

        try:
            with open(self._config_path, "w", encoding="utf-8") as file:
                json.dump(config, file, indent=4)
                file.write("\n")
        except OSError as exc:
            self._error_label.configure(text=f"Could not save config: {exc}")
            return

        self._on_complete()
