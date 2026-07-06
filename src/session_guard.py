"""Timetable session safety helpers and confirmation dialogs."""
from __future__ import annotations

import tkinter as tk
from typing import Literal

import customtkinter as ctk

from format_time import format_time

ExitChoice = Literal["cancel", "discard"]


def has_unlogged_time(timetable) -> bool:
    if timetable is None:
        return False
    if getattr(timetable, "running", False):
        return True
    if getattr(timetable, "elapsed_time", 0) > 0:
        return True
    return False


def current_elapsed_seconds(timetable) -> float:
    if timetable is None:
        return 0.0
    elapsed = float(getattr(timetable, "elapsed_time", 0) or 0)
    if getattr(timetable, "running", False):
        import time

        anchor = getattr(timetable, "_monotonic_anchor", 0.0)
        elapsed += max(0.0, time.perf_counter() - anchor)
    return elapsed


def formatted_unlogged_time(timetable) -> str:
    return format_time(current_elapsed_seconds(timetable))


class _ModalDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Misc, *, title: str, width: int, height: int):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self._result: ExitChoice | bool | None = None

        self.update_idletasks()
        top = parent.winfo_toplevel()
        top.update_idletasks()
        x = top.winfo_rootx() + max(0, (top.winfo_width() - width) // 2)
        y = top.winfo_rooty() + max(0, (top.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _finish(self, value: ExitChoice | bool) -> None:
        self._result = value
        self.grab_release()
        self.destroy()

    def show(self) -> ExitChoice | bool | None:
        self.wait_window()
        return self._result


class UnloggedExitDialog(_ModalDialog):
    def __init__(self, parent: tk.Misc, *, elapsed_label: str):
        super().__init__(parent, title="Unlogged time", width=460, height=220)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="You still have unlogged time.",
            font=("Arial", 18, "bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")

        ctk.CTkLabel(
            self,
            text=(
                f"Current session: {elapsed_label}\n\n"
                "Log it from Timetable before exiting, or discard the session."
            ),
            font=("Arial", 14),
            text_color="#B0B0B0",
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 16), sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(
            buttons,
            text="Stay",
            width=100,
            command=lambda: self._finish("cancel"),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons,
            text="Discard and exit",
            width=140,
            fg_color="#8B1A1A",
            hover_color="#A02020",
            command=lambda: self._finish("discard"),
        ).pack(side="left")


class DiscardSessionDialog(_ModalDialog):
    def __init__(self, parent: tk.Misc, *, elapsed_label: str):
        super().__init__(parent, title="Discard session", width=420, height=200)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="Discard current session?",
            font=("Arial", 18, "bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")

        ctk.CTkLabel(
            self,
            text=f"This will permanently remove {elapsed_label} of unlogged time.",
            font=("Arial", 14),
            text_color="#B0B0B0",
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 16), sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")

        ctk.CTkButton(
            buttons,
            text="Cancel",
            width=100,
            command=lambda: self._finish(False),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            buttons,
            text="Discard",
            width=100,
            fg_color="#8B1A1A",
            hover_color="#A02020",
            command=lambda: self._finish(True),
        ).pack(side="left")


class UnloggedActionBlockedDialog(_ModalDialog):
    def __init__(self, parent: tk.Misc, *, elapsed_label: str, action: str):
        super().__init__(parent, title="Unlogged time", width=440, height=190)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="You still have unlogged time.",
            font=("Arial", 18, "bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")

        ctk.CTkLabel(
            self,
            text=(
                f"Current session: {elapsed_label}\n\n"
                f"Log it from Timetable before you {action}."
            ),
            font=("Arial", 14),
            text_color="#B0B0B0",
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 16), sticky="ew")

        ctk.CTkButton(
            self,
            text="OK",
            width=100,
            command=lambda: self._finish(False),
        ).grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")


def prompt_unlogged_exit(parent: tk.Misc, timetable) -> ExitChoice:
    dialog = UnloggedExitDialog(parent, elapsed_label=formatted_unlogged_time(timetable))
    result = dialog.show()
    return result if result in ("cancel", "discard") else "cancel"


def confirm_discard_session(parent: tk.Misc, timetable) -> bool:
    dialog = DiscardSessionDialog(parent, elapsed_label=formatted_unlogged_time(timetable))
    return bool(dialog.show())


def prompt_unlogged_action_blocked(parent: tk.Misc, timetable, *, action: str) -> None:
    dialog = UnloggedActionBlockedDialog(
        parent,
        elapsed_label=formatted_unlogged_time(timetable),
        action=action,
    )
    dialog.show()
