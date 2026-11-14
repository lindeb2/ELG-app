"""Record controls for the optional Meeting Host sidecar.

`attach` returns None when the Host is not installed, so the meeting view is
untouched for everyone who never installs Meeting Mike.
"""
from __future__ import annotations

import customtkinter as ctk

from meeting_mike_client import (
    STATE_RECORDING,
    get_session,
    is_meeting_host_available,
)
from meeting_mike_window import open_status_window

_BTN_IDLE = {"fg_color": "#333333", "hover_color": "#404040", "text_color": "white"}
_BTN_RECORDING = {"fg_color": "#8B1A1A", "hover_color": "#A82121", "text_color": "white"}


class MeetingMikePanel(ctk.CTkFrame):
    """Bottom-left overlay: Host button that launches the Meeting Mike window."""

    def __init__(self, parent, username: str):
        super().__init__(parent, fg_color="transparent", bg_color="transparent")
        self._username = username or ""
        self._session = get_session()
        self._detached = False

        self._button = ctk.CTkButton(
            self,
            text="Host",
            width=80,
            height=24,
            corner_radius=0,
            font=("Arial", 12),
            command=self._on_host,
            **_BTN_IDLE,
        )
        self._button.pack(side="left")

        self.bind("<Destroy>", self._on_destroy, add="+")
        self._session.add_listener(self._on_session_event)
        self._render(self._session.snapshot())

    # --- lifecycle -------------------------------------------------------
    def detach(self) -> None:
        """Stop observing the session; the recording itself keeps running."""
        if self._detached:
            return
        self._detached = True
        self._session.remove_listener(self._on_session_event)

    def _on_destroy(self, _event) -> None:
        # CTkFrame.bind() delegates to an internal canvas, so the event widget
        # is never this frame; any destroy here means the panel is going away.
        self.detach()

    def _on_session_event(self, snapshot: dict) -> None:
        if self._detached:
            return
        try:
            self.after(0, lambda: self._render(snapshot))
        except Exception:
            pass

    # --- rendering -------------------------------------------------------
    def _render(self, snapshot: dict) -> None:
        if self._detached or not self.winfo_exists():
            return

        state = snapshot["state"]
        if state == STATE_RECORDING:
            self._button.configure(**_BTN_RECORDING)
        else:
            self._button.configure(**_BTN_IDLE)

    # --- actions ---------------------------------------------------------
    def _on_host(self) -> None:
        open_status_window(self, username=self._username, focus=True)


def attach(parent, username: str) -> MeetingMikePanel | None:
    """Place the panel bottom-left, or return None when Host is absent."""
    if not is_meeting_host_available():
        return None
    panel = MeetingMikePanel(parent, username)
    panel.place(relx=0.02, rely=0.9644, anchor="sw")
    return panel
