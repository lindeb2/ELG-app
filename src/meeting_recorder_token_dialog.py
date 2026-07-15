"""Modal prompt for the Meeting Recorder's Discord bot token.

Matches session_guard.py's small modal-dialog shape (title/body/buttons,
centered on the parent toplevel, wait_window()-blocking). Kept separate
from session_guard.py since that module's dialogs are timetable-specific and
its _ModalDialog helper isn't exported.

See meeting_recorder_secrets.py - the token entered here is stored locally
on this machine only and never transmitted anywhere except to Discord's own
API to validate it (meeting_recorder_setup.validate_and_store_token).
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


class DiscordBotTokenDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("Meeting Recorder setup")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self._result: str | None = None

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Enter a Discord bot token",
            font=("Arial", 18, "bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")

        ctk.CTkLabel(
            self,
            text=(
                "Used only to connect this app to your Discord server for meeting "
                "recording. Stored locally on this machine and never sent anywhere else."
            ),
            font=("Arial", 13),
            text_color="#B0B0B0",
            wraplength=380,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")

        self._entry = ctk.CTkEntry(self, width=380, show="•")
        self._entry.grid(row=2, column=0, padx=20, pady=(0, 8), sticky="ew")
        self._entry.bind("<Return>", lambda _e: self._submit())

        self._error_label = ctk.CTkLabel(self, text="", font=("Arial", 12), text_color="#FF6B6B", anchor="w")
        self._error_label.grid(row=3, column=0, padx=20, pady=(0, 8), sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="e")
        ctk.CTkButton(buttons, text="Cancel", width=100, command=self._cancel).pack(side="left", padx=(0, 8))
        ctk.CTkButton(buttons, text="Continue", width=100, command=self._submit).pack(side="left")

        self.update_idletasks()
        top = parent.winfo_toplevel()
        top.update_idletasks()
        width, height = 440, 230
        x = top.winfo_rootx() + max(0, (top.winfo_width() - width) // 2)
        y = top.winfo_rooty() + max(0, (top.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self._entry.focus_set()

    def _submit(self) -> None:
        value = self._entry.get().strip()
        if not value:
            self._error_label.configure(text="Enter a token to continue.")
            return
        self._result = value
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self._result = None
        self.grab_release()
        self.destroy()

    def show(self) -> str | None:
        self.wait_window()
        return self._result


def prompt_discord_bot_token(parent: tk.Misc) -> str | None:
    dialog = DiscordBotTokenDialog(parent)
    return dialog.show()
