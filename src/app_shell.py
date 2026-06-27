"""Main application shell with sidebar navigation."""
from __future__ import annotations

import customtkinter as ctk

from meeting_app import MeetingFrame
from meeting_point_manager import MeetingPointManagerFrame
from settings_frame import SettingsFrame
from stats_viewer import StatsFrame
from Timetable import TimetableFrame

_SIDEBAR_WIDTH = 110
_CONTENT_SQUARE = 200
_SMALL_VIEW = (_SIDEBAR_WIDTH + _CONTENT_SQUARE, _CONTENT_SQUARE)

_VIEW_SIZES: dict[str, tuple[int, int]] = {
    "timetable": _SMALL_VIEW,
    "statistics": (1420, 800),
    "meeting": (1360, 820),
    "meeting_points": _SMALL_VIEW,
    "settings": (500, 450),
}

_NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("timetable", "Timetable"),
    ("statistics", "Statistics"),
    ("meeting", "Meeting App"),
    ("meeting_points", "Point Manager"),
)

_BTN_TRANSPARENT = {"fg_color": "transparent", "hover_color": "#2C2C2C", "text_color": "white"}
_BTN_ACTIVE = {"fg_color": "#2C2C2C", "hover_color": "#3C3C3C", "text_color": "white"}


class AppShell(ctk.CTkFrame):
    def __init__(self, window: ctk.CTk, master: ctk.CTkFrame):
        super().__init__(master, fg_color="#000000", corner_radius=0)
        self._window = window

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._active_view: str | None = None
        self._current_frame: ctk.CTkFrame | None = None
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._frames: dict[str, ctk.CTkFrame | None] = {
            "timetable": None,
            "statistics": None,
            "meeting": None,
            "meeting_points": None,
            "settings": None,
        }

        self._sidebar = ctk.CTkFrame(self, width=_SIDEBAR_WIDTH, corner_radius=0)
        self._sidebar.grid(row=0, column=0, sticky="ns")
        self._sidebar.grid_propagate(False)
        self._sidebar.grid_rowconfigure(len(_NAV_ITEMS) + 1, weight=1)

        for row, (key, label) in enumerate(_NAV_ITEMS):
            btn = ctk.CTkButton(
                self._sidebar,
                text=label,
                anchor="w",
                command=lambda k=key: self.switch_view(k),
                **_BTN_TRANSPARENT,
            )
            btn.grid(row=row, column=0, sticky="ew", padx=6, pady=2)
            self._nav_buttons[key] = btn

        divider = ctk.CTkFrame(self._sidebar, height=1, fg_color="#333333")
        divider.grid(row=len(_NAV_ITEMS), column=0, sticky="ew", padx=8, pady=8)

        self._settings_btn = ctk.CTkButton(
            self._sidebar,
            text="Settings",
            anchor="w",
            command=lambda: self.switch_view("settings"),
            **_BTN_TRANSPARENT,
        )
        self._settings_btn.grid(
            row=len(_NAV_ITEMS) + 2,
            column=0,
            sticky="sew",
            padx=6,
            pady=(2, 8),
        )
        self._nav_buttons["settings"] = self._settings_btn

        self._content = ctk.CTkFrame(self, fg_color="#000000", corner_radius=0)
        self._content.grid(row=0, column=1, sticky="nsew")

        window.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_layout(self, name: str) -> None:
        is_small = name in ("timetable", "meeting_points")
        if is_small:
            self.grid_rowconfigure(0, weight=0)
            self.grid_columnconfigure(1, weight=0, minsize=_CONTENT_SQUARE)
            self._content.grid(row=0, column=1, sticky="nw")
            self._content.configure(width=_CONTENT_SQUARE, height=_CONTENT_SQUARE)
            self._content.grid_propagate(False)
        else:
            self.grid_rowconfigure(0, weight=1)
            self.grid_columnconfigure(1, weight=1, minsize=0)
            self._content.grid(row=0, column=1, sticky="nsew")
            self._content.grid_propagate(True)

    def switch_view(self, name: str, *, update_geometry: bool = True) -> None:
        self._apply_layout(name)

        if self._current_frame is not None:
            self._current_frame.pack_forget()

        if self._frames[name] is None:
            self._frames[name] = self._create_frame(name)

        frame = self._frames[name]
        assert frame is not None
        frame.pack(fill="both", expand=True)
        self._current_frame = frame
        self._active_view = name

        if name == "meeting":
            frame.focus_set()

        if update_geometry:
            width, height = _VIEW_SIZES[name]
            self._window.geometry(f"{width}x{height}")

        for key, btn in self._nav_buttons.items():
            style = _BTN_ACTIVE if key == name else _BTN_TRANSPARENT
            btn.configure(**style)

    def _square_host(self, frame_cls: type, **kwargs) -> ctk.CTkFrame:
        host = ctk.CTkFrame(
            self._content,
            fg_color="#000000",
            width=_CONTENT_SQUARE,
            height=_CONTENT_SQUARE,
        )
        host.pack_propagate(False)
        frame = frame_cls(host, **kwargs)
        frame.pack(fill="both", expand=True)
        return host

    def _create_frame(self, name: str) -> ctk.CTkFrame:
        if name == "timetable":
            return self._square_host(TimetableFrame)
        if name == "statistics":
            return StatsFrame(self._content)
        if name == "meeting":
            return MeetingFrame(self._content)
        if name == "settings":
            return SettingsFrame(self._content)
        if name == "meeting_points":
            return self._square_host(
                MeetingPointManagerFrame,
                navigate_back=lambda: self.switch_view("timetable"),
            )
        raise ValueError(f"Unknown view: {name}")

    def _on_close(self) -> None:
        host = self._frames.get("timetable")
        timetable = host.winfo_children()[0] if host and host.winfo_children() else None
        if timetable is not None and getattr(timetable, "running", False):
            dialog = ctk.CTkInputDialog(
                text="Timer is running. Type anything to close, or cancel to stay.",
                title="Confirm close",
            )
            if dialog.get_input() is None:
                return
        self._window.destroy()
