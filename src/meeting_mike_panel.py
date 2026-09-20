"""Record controls for the optional Meeting Host sidecar.

`attach` returns None when the Host is not installed, so the meeting view is
untouched for everyone who never installs Meeting Mike.
"""
from __future__ import annotations

import threading

import customtkinter as ctk

from format_time import format_time
from meeting_mike_client import (
    STATE_DONE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PROCESSING,
    STATE_RECORDING,
    STATE_STARTING,
    get_session,
    is_meeting_host_available,
    open_in_file_manager,
)
from notification_preferences import fetch_notification_prefs
from utils import flash_error

_TICK_MS = 500

_BTN_IDLE = {"fg_color": "#333333", "hover_color": "#404040", "text_color": "white"}
_BTN_RECORDING = {"fg_color": "#8B1A1A", "hover_color": "#A82121", "text_color": "white"}

_STATUS_COLORS = {
    STATE_ERROR: "#FF4444",
    STATE_DONE: "#00AD00",
}


class MeetingMikePanel(ctk.CTkFrame):
    """Bottom-left overlay: Record / Stop plus pipeline status."""

    def __init__(self, parent, username: str):
        super().__init__(parent, fg_color="transparent", bg_color="transparent")
        self._username = username or ""
        self._session = get_session()
        self._tick_id: str | None = None
        self._detached = False

        self._button = ctk.CTkButton(
            self,
            text="Record",
            width=96,
            height=24,
            corner_radius=0,
            font=("Arial", 12),
            command=self._on_button,
            **_BTN_IDLE,
        )
        self._button.pack(side="left")

        self._status = ctk.CTkLabel(
            self,
            text="",
            font=("Arial", 12),
            text_color="#B0B0B0",
            anchor="w",
        )
        self._status.pack(side="left", padx=(8, 0))

        self._open_button = ctk.CTkButton(
            self,
            text="Open folder",
            width=88,
            height=24,
            corner_radius=0,
            font=("Arial", 12),
            command=self._on_open_folder,
            **_BTN_IDLE,
        )
        self._open_button_shown = False

        self.bind("<Destroy>", self._on_destroy, add="+")
        self._session.add_listener(self._on_session_event)
        self._render(self._session.snapshot())
        self._schedule_tick()

    # --- lifecycle -------------------------------------------------------
    def detach(self) -> None:
        """Stop observing the session; the recording itself keeps running."""
        if self._detached:
            return
        self._detached = True
        self._session.remove_listener(self._on_session_event)
        if self._tick_id is not None:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

    def _on_destroy(self, _event) -> None:
        # CTkFrame.bind() delegates to an internal canvas, so the event widget
        # is never this frame; any destroy here means the panel is going away.
        self.detach()

    def _schedule_tick(self) -> None:
        if self._detached:
            return
        try:
            self._tick_id = self.after(_TICK_MS, self._tick)
        except Exception:
            self._tick_id = None

    def _tick(self) -> None:
        self._tick_id = None
        if self._detached or not self.winfo_exists():
            return
        self._render(self._session.snapshot())
        self._schedule_tick()

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
            self._button.configure(text="Stop", state="normal", **_BTN_RECORDING)
        elif state in (STATE_STARTING, STATE_PROCESSING):
            self._button.configure(text="Stop", state="disabled", **_BTN_IDLE)
        else:
            self._button.configure(text="Record", state="normal", **_BTN_IDLE)

        if state == STATE_RECORDING:
            participants = len(snapshot["participants"])
            text = f"Recording {format_time(snapshot['elapsed'])}"
            if participants:
                text += f" · {participants} in call"
        elif state == STATE_PROCESSING:
            text = snapshot["message"] or "Processing..."
            progress = snapshot["phase_progress"]
            if 0.0 < progress < 1.0:
                text += f" {int(progress * 100)}%"
        else:
            text = snapshot["message"]

        self._status.configure(
            text=text,
            text_color=_STATUS_COLORS.get(state, "#B0B0B0"),
        )

        # Tracked explicitly: winfo_ismapped() is false whenever the meeting
        # view is hidden, which would strand the button in the wrong state.
        should_show = state == STATE_DONE and bool(snapshot["meeting_dir"])
        if should_show != self._open_button_shown:
            if should_show:
                self._open_button.pack(side="left", padx=(8, 0))
            else:
                self._open_button.pack_forget()
            self._open_button_shown = should_show

    # --- actions ---------------------------------------------------------
    def _on_button(self) -> None:
        snapshot = self._session.snapshot()
        state = snapshot["state"]

        if state == STATE_RECORDING:
            self._session.stop()
            return
        if state in (STATE_STARTING, STATE_PROCESSING):
            return

        if state in (STATE_DONE, STATE_ERROR):
            self._session.reset_if_finished()

        self._button.configure(state="disabled")
        self._status.configure(text="Checking your Discord link...", text_color="#B0B0B0")
        threading.Thread(
            target=self._start_worker,
            name="meeting-mike-start",
            daemon=True,
        ).start()

    def _start_worker(self) -> None:
        try:
            prefs = fetch_notification_prefs(self._username)
            discord_user_id = str(prefs.get("discord_user_id") or "").strip()
        except Exception as exc:
            self._post_start_result(False, f"Could not read your Discord link: {exc}")
            return

        if not discord_user_id:
            self._post_start_result(
                False,
                "Set your Discord user-ID in Settings to record.",
            )
            return

        started, error = self._session.start(
            elg_username=self._username,
            discord_user_id=discord_user_id,
        )
        self._post_start_result(started, error)

    def _post_start_result(self, started: bool, error: str) -> None:
        if self._detached:
            return

        def apply() -> None:
            if self._detached or not self.winfo_exists():
                return
            self._button.configure(state="normal")
            if not started:
                self._status.configure(text=error, text_color="#FF4444")
                flash_error(self._status)
            self._render(self._session.snapshot())

        try:
            self.after(0, apply)
        except Exception:
            pass

    def _on_open_folder(self) -> None:
        meeting_dir = self._session.snapshot()["meeting_dir"]
        if meeting_dir:
            open_in_file_manager(meeting_dir)


def attach(parent, username: str) -> MeetingMikePanel | None:
    """Place the panel bottom-left, or return None when Host is absent."""
    if not is_meeting_host_available():
        return None
    panel = MeetingMikePanel(parent, username)
    panel.place(relx=0.02, rely=0.9644, anchor="sw")
    return panel
