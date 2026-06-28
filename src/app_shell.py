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

_SHORTCUT_VIEWS: dict[str, str] = {
    "1": "timetable",
    "2": "statistics",
    "3": "meeting",
    "4": "meeting_points",
}

_BTN_TRANSPARENT = {"fg_color": "transparent", "hover_color": "#2C2C2C", "text_color": "white"}
_BTN_ACTIVE = {"fg_color": "#2C2C2C", "hover_color": "#3C3C3C", "text_color": "white"}


def apply_dwm_theming(window: ctk.CTk) -> None:
    try:
        from ctypes import byref, c_int, sizeof, windll

        hwnd = windll.user32.GetParent(window.winfo_id())  # type: ignore[attr-defined]
        windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, byref(c_int(1)), sizeof(c_int))  # type: ignore[attr-defined]
        windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, byref(c_int(0x00000000)), sizeof(c_int))  # type: ignore[attr-defined]
        windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, byref(c_int(0x00000000)), sizeof(c_int))  # type: ignore[attr-defined]
    except (ImportError, AttributeError, OSError):
        print("DWM API not available, skipping window attribute settings.")


class AppShell(ctk.CTkFrame):
    def __init__(self, window: ctk.CTk, master: ctk.CTkFrame):
        super().__init__(master, fg_color="#000000", corner_radius=0)
        self._window = window
        self._sidebar_visible = True
        self._sidebar_before_fullscreen: bool | None = None

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
        self._bind_shortcuts()

    def _bind_shortcuts(self) -> None:
        self._window.bind("<Control-b>", self._on_toggle_sidebar, add="+")
        self._window.bind("<Control-B>", self._on_toggle_sidebar, add="+")
        for key, view in _SHORTCUT_VIEWS.items():
            self._window.bind(f"<Control-Key-{key}>", lambda _e, v=view: self.switch_view(v), add="+")
            self._window.bind(f"<Control-{key}>", lambda _e, v=view: self.switch_view(v), add="+")
        self._window.bind("<Control-comma>", lambda _e: self.switch_view("settings"), add="+")

    def _on_toggle_sidebar(self, _event=None) -> None:
        self.toggle_sidebar()

    def toggle_sidebar(self) -> None:
        self.set_sidebar_visible(not self._sidebar_visible)

    def set_sidebar_visible(self, visible: bool) -> None:
        if visible == self._sidebar_visible:
            return
        self._sidebar_visible = visible
        if visible:
            self._sidebar.grid(row=0, column=0, sticky="ns")
        else:
            self._sidebar.grid_remove()
        self._apply_window_geometry()

    def enter_meeting_fullscreen(self) -> None:
        if self._sidebar_before_fullscreen is None:
            self._sidebar_before_fullscreen = self._sidebar_visible
        if self._sidebar_visible:
            self.set_sidebar_visible(False)

    def exit_meeting_fullscreen(self) -> None:
        if self._sidebar_before_fullscreen is not None:
            self.set_sidebar_visible(self._sidebar_before_fullscreen)
            self._sidebar_before_fullscreen = None
        self._window.update_idletasks()
        apply_dwm_theming(self._window)
        self._window.after_idle(lambda: apply_dwm_theming(self._window))

    def _apply_window_geometry(self) -> None:
        if self._active_view is None:
            return
        width, height = _VIEW_SIZES[self._active_view]
        if not self._sidebar_visible:
            width -= _SIDEBAR_WIDTH
        self._window.geometry(f"{width}x{height}")

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

    def _leave_meeting_fullscreen(self) -> None:
        meeting = self._frames.get("meeting")
        if meeting is not None:
            meeting.leave_fullscreen_if_active()

    def switch_view(self, name: str, *, update_geometry: bool = True) -> None:
        if name != "meeting":
            self._leave_meeting_fullscreen()

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
            self._apply_window_geometry()

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
            return MeetingFrame(self._content, shell=self)
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
