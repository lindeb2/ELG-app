"""Update available dialog."""
from __future__ import annotations

import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

from app_update import (
    ReleaseInfo,
    apply_update,
    dismiss_for_session,
    download_update_artifact,
)


class UpdateDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent: tk.Misc,
        release: ReleaseInfo,
        *,
        shell=None,
        instance_guard=None,
    ):
        super().__init__(parent)
        self._release = release
        self._shell = shell
        self._instance_guard = instance_guard
        self._busy = False

        self.title("New update available")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self,
            text="New update available",
            font=("Arial", 20, "bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=20, pady=(20, 8), sticky="ew")

        ctk.CTkLabel(
            self,
            text=f"Version {release.version}",
            font=("Arial", 16),
            anchor="w",
        ).grid(row=1, column=0, padx=20, pady=(0, 8), sticky="ew")

        notes = release.body.strip() or "No release notes provided."
        if len(notes) > 1200:
            notes = notes[:1200].rstrip() + "…"

        ctk.CTkLabel(
            self,
            text=notes,
            font=("Arial", 13),
            text_color="#B0B0B0",
            justify="left",
            anchor="nw",
            wraplength=420,
        ).grid(row=2, column=0, padx=20, pady=(0, 12), sticky="ew")

        if sys.platform == "darwin":
            ctk.CTkLabel(
                self,
                text="Update now opens the .dmg download. Replace ELG.app in Applications after download.",
                font=("Arial", 12),
                text_color="#B0B0B0",
                justify="left",
                anchor="w",
                wraplength=420,
            ).grid(row=3, column=0, padx=20, pady=(0, 12), sticky="ew")
            notes_row = 4
        else:
            notes_row = 3

        self._status_label = ctk.CTkLabel(self, text="", font=("Arial", 13), text_color="#B0B0B0")
        self._status_label.grid(row=notes_row, column=0, padx=20, pady=(0, 12), sticky="w")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=notes_row + 1, column=0, padx=20, pady=(0, 20), sticky="e")

        self._update_btn = ctk.CTkButton(
            buttons,
            text="Update now",
            width=120,
            command=self._on_update_now,
        )
        self._update_btn.pack(side="left", padx=(0, 8))

        self._not_now_btn = ctk.CTkButton(
            buttons,
            text="Not now",
            width=100,
            fg_color="#3A3A3A",
            hover_color="#4A4A4A",
            command=self._on_not_now,
        )
        self._not_now_btn.pack(side="left")

        self.update_idletasks()
        top = parent.winfo_toplevel()
        x = top.winfo_rootx() + max(0, (top.winfo_width() - self.winfo_width()) // 2)
        y = top.winfo_rooty() + max(0, (top.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._update_btn.configure(state=state)
        self._not_now_btn.configure(state=state)
        self._status_label.configure(text=message)

    def _on_not_now(self) -> None:
        if self._busy:
            return
        dismiss_for_session(self._release.version)
        self.destroy()

    def _on_update_now(self) -> None:
        if self._busy:
            return

        if sys.platform == "darwin":
            self._set_busy(True, "Opening download…")
            try:
                apply_update(self._release, Path(), shell=self._shell, instance_guard=self._instance_guard)
            except Exception as exc:  # noqa: BLE001
                self._set_busy(False)
                self._status_label.configure(text=str(exc), text_color="#FF4444")
                return
            self.destroy()
            return

        self._set_busy(True, "Downloading update…")

        def worker() -> None:
            error: str | None = None
            downloaded: Path | None = None
            try:
                dest = Path(tempfile.mkdtemp(prefix="elg-update-"))
                downloaded = download_update_artifact(self._release, dest)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

            def ui() -> None:
                if error:
                    self._set_busy(False)
                    self._status_label.configure(text=error, text_color="#FF4444")
                    return
                self._set_busy(True, "Applying update…")
                try:
                    apply_update(
                        self._release,
                        downloaded,
                        shell=self._shell,
                        instance_guard=self._instance_guard,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._set_busy(False)
                    self._status_label.configure(text=str(exc), text_color="#FF4444")

            self.after(0, ui)

        threading.Thread(target=worker, daemon=True).start()


def show_update_dialog(
    parent: tk.Misc,
    release: ReleaseInfo,
    *,
    shell=None,
    instance_guard=None,
    force: bool = False,
) -> None:
    del force
    UpdateDialog(parent, release, shell=shell, instance_guard=instance_guard)
