"""Separate Meeting Mike window: timed sections, models, and per-speaker rows.

The meeting view only keeps Record / Stop. This window is parented to the
application root so leaving the meeting view does not close it.
"""
from __future__ import annotations

import customtkinter as ctk

from format_time import format_time
from meeting_mike_client import (
    STATE_DONE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PROCESSING,
    STATE_RECORDING,
    STATE_STARTING,
    format_duration,
    get_session,
)
from settings_ui_constants import (
    ACCENT,
    BOX_BORDER,
    BOX_CORNER,
    CARD_BG,
    FONT_MUTED,
    FONT_ROW,
    FONT_SECTION,
    TEXT_MUTED,
)

_TICK_MS = 500
_APP_BG = "#000000"
_TIME_COLOR = "#E8E8E8"
_MODEL_WIDTH = 250
_TIME_WIDTH = 72

_STATUS_COLORS = {
    STATE_ERROR: "#FF4444",
    STATE_DONE: "#00AD00",
    STATE_RECORDING: "#E0E0E0",
    STATE_PROCESSING: "#E0E0E0",
}

_window: "MeetingMikeWindow | None" = None


class MeetingMikeWindow(ctk.CTkToplevel):
    def __init__(self, master):
        # The app paints its own caption. Leave CustomTkinter's title-bar swap off.
        ctk.CTkToplevel._deactivate_windows_window_header_manipulation = True
        super().__init__(master, fg_color=_APP_BG)
        self.geometry("740x520")
        self.minsize(620, 360)
        self.protocol("WM_DELETE_WINDOW", self._hide)

        self._session = get_session()
        self._tick_id: str | None = None
        self._closed = False
        self._note = ""
        self._note_error = False
        self._header_ready = False
        self._step_labels: dict[str, ctk.CTkLabel] = {}
        self._model_labels: dict[str, ctk.CTkLabel] = {}
        self._time_labels: dict[str, ctk.CTkLabel] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._headline = ctk.CTkLabel(
            self,
            text="Waiting",
            font=("Segoe UI", 18, "bold"),
            text_color="#E0E0E0",
            anchor="w",
            justify="left",
        )
        self._headline.grid(row=0, column=0, padx=20, pady=(18, 12), sticky="ew")

        self._steps = ctk.CTkScrollableFrame(
            self,
            fg_color=CARD_BG,
            border_width=1,
            border_color=BOX_BORDER,
            corner_radius=BOX_CORNER,
            scrollbar_button_color="#3A3A3A",
            scrollbar_button_hover_color="#505050",
            scrollbar_fg_color=CARD_BG,
        )
        self._steps.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="nsew")
        self._steps.grid_columnconfigure(0, weight=1)
        self._steps.grid_columnconfigure(1, minsize=_MODEL_WIDTH)
        self._steps.grid_columnconfigure(2, minsize=_TIME_WIDTH)

        self._session.add_listener(self._on_session_event)
        self.render(self._session.snapshot())
        self._schedule_tick()
        self._apply_chrome()
        self.after(300, self._apply_chrome)

    def set_note(self, text: str, *, error: bool = False) -> None:
        """A line from the meeting view before the Host has reported anything."""
        self._note = text
        self._note_error = error
        self._show()
        self.render(self._session.snapshot())

    def render(self, snapshot: dict) -> None:
        if self._closed or not self.winfo_exists():
            return

        headline = self._headline_text(snapshot)
        color = _STATUS_COLORS.get(snapshot.get("state"), "#B0B0B0")
        if self._note_error and snapshot.get("state") in (STATE_IDLE, STATE_STARTING):
            color = _STATUS_COLORS[STATE_ERROR]
        self._headline.configure(text=headline, text_color=color)
        self._render_steps(snapshot.get("steps") or [])

    def _headline_text(self, snapshot: dict) -> str:
        state = snapshot.get("state")
        if state == STATE_RECORDING:
            names = ", ".join(snapshot.get("participants", {}).values())
            text = f"Recording {format_time(snapshot.get('elapsed') or 0)}"
            channel = snapshot.get("channel_name") or ""
            if channel:
                text += f"  ·  {channel}"
            if names:
                text += f"  ·  {names}"
            return text
        if self._note and state in (STATE_IDLE, STATE_STARTING):
            return self._note
        return snapshot.get("message") or "Waiting"

    def _ensure_header(self) -> None:
        if self._header_ready:
            return
        ctk.CTkLabel(self._steps, text="", font=FONT_MUTED, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(12, 8), pady=(10, 4)
        )
        ctk.CTkLabel(self._steps, text="Model", font=FONT_MUTED, text_color=TEXT_MUTED, anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=(10, 4)
        )
        ctk.CTkLabel(self._steps, text="Time", font=FONT_MUTED, text_color=TEXT_MUTED, anchor="e").grid(
            row=0, column=2, sticky="e", padx=(0, 12), pady=(10, 4)
        )
        self._header_ready = True

    def _render_steps(self, steps: list[dict]) -> None:
        if steps:
            self._ensure_header()
        seen: set[str] = set()
        for index, step in enumerate(steps):
            name = str(step.get("name") or index)
            seen.add(name)
            title = self._step_labels.get(name)
            model = self._model_labels.get(name)
            timing = self._time_labels.get(name)
            if title is None or model is None or timing is None:
                title = ctk.CTkLabel(self._steps, anchor="w", justify="left")
                model = ctk.CTkLabel(self._steps, anchor="w", justify="left", text_color=TEXT_MUTED)
                timing = ctk.CTkLabel(self._steps, anchor="e", justify="right")
                self._step_labels[name] = title
                self._model_labels[name] = model
                self._time_labels[name] = timing

            child = bool(step.get("parent"))
            row = index + 1
            top = 2 if child or index == 0 else 12
            title.grid(row=row, column=0, sticky="ew", padx=(28 if child else 12, 8), pady=(top, 2))
            model.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=(top, 2))
            timing.grid(row=row, column=2, sticky="e", padx=(0, 12), pady=(top, 2))

            state = step.get("state") or "pending"
            title.configure(
                text=str(step.get("label") or name),
                font=FONT_ROW if child else FONT_SECTION,
                text_color="#C8C8C8" if child else "#F2F2F2",
            )
            model.configure(text=str(step.get("model") or ""), font=FONT_MUTED, width=_MODEL_WIDTH)
            seconds = step.get("seconds")
            shown = seconds is not None or state == "running"
            timing.configure(
                text=format_duration(0 if seconds is None else seconds) if shown else "",
                font=FONT_ROW,
                text_color=ACCENT if state == "running" else _TIME_COLOR,
                width=_TIME_WIDTH,
            )

        for name in list(self._step_labels):
            if name in seen:
                continue
            self._step_labels.pop(name).destroy()
            self._model_labels.pop(name).destroy()
            self._time_labels.pop(name).destroy()

    def _apply_chrome(self) -> None:
        if self._closed:
            return
        try:
            from window_chrome import apply_app_title_bar_chrome, configure_app_icon, configure_window_title

            configure_window_title(self, "Meeting Mike")
            configure_app_icon(self)
            apply_app_title_bar_chrome(self)
        except Exception:
            try:
                self.title("Meeting Mike")
            except Exception:
                pass

    def _show(self) -> None:
        try:
            self.deiconify()
            self.lift()
        except Exception:
            pass

    def _hide(self) -> None:
        try:
            self.withdraw()
        except Exception:
            pass

    def _on_session_event(self, snapshot: dict) -> None:
        if self._closed:
            return
        if snapshot.get("state") not in (STATE_IDLE, STATE_STARTING):
            self._note = ""
            self._note_error = False

        def apply() -> None:
            if self._closed or not self.winfo_exists():
                return
            self.render(snapshot)

        try:
            self.after(0, apply)
        except Exception:
            pass

    def _schedule_tick(self) -> None:
        if self._closed:
            return
        try:
            self._tick_id = self.after(_TICK_MS, self._tick)
        except Exception:
            self._tick_id = None

    def _tick(self) -> None:
        self._tick_id = None
        if self._closed or not self.winfo_exists():
            return
        snapshot = self._session.snapshot()
        if snapshot["state"] in (STATE_RECORDING, STATE_PROCESSING, STATE_STARTING):
            self.render(snapshot)
        self._schedule_tick()

    def destroy(self) -> None:
        self._closed = True
        self._session.remove_listener(self._on_session_event)
        if self._tick_id is not None:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None
        super().destroy()


def open_status_window(master, *, focus: bool = True) -> MeetingMikeWindow:
    """Show the status window, creating it on the app root if needed."""
    global _window
    root = master.winfo_toplevel()
    existing = _window
    if existing is not None:
        try:
            if existing.winfo_exists():
                if focus:
                    existing._show()
                return existing
        except Exception:
            pass
    _window = MeetingMikeWindow(root)
    return _window
