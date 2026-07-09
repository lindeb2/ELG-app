"""Monitor detection and window position persistence/restoration.

Windows: enumerates real monitors (work-area rects) via EnumDisplayMonitors.
Other platforms: falls back to a single "monitor" spanning the Tk-reported
screen size, which is good enough for centering/placement purposes there.
"""
from __future__ import annotations

from dataclasses import dataclass

from platform_keys import IS_WINDOWS

_SAFE_MARGIN = 50


@dataclass(frozen=True)
class MonitorRect:
    x: int
    y: int
    width: int
    height: int
    primary: bool = False

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def _monitors_windows() -> list[MonitorRect]:
    """Enumerate real monitors (work-area, i.e. taskbar-excluded) on Windows."""
    import ctypes

    monitors: list[MonitorRect] = []

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", ctypes.c_ulong),
        ]

    _MONITORINFOF_PRIMARY = 0x1

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_RECT),
        ctypes.c_ssize_t,
    )

    def _callback(hmonitor, _hdc, _lprect, _lparam):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rect = info.rcWork
            monitors.append(
                MonitorRect(
                    x=rect.left,
                    y=rect.top,
                    width=rect.right - rect.left,
                    height=rect.bottom - rect.top,
                    primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                )
            )
        return 1

    callback = MonitorEnumProc(_callback)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
    return monitors


def list_monitors(window) -> list[MonitorRect]:
    """All monitors, working-area rects. Falls back to a single Tk-sized monitor."""
    if IS_WINDOWS:
        try:
            monitors = _monitors_windows()
            if monitors:
                return monitors
        except Exception:
            pass
    return [
        MonitorRect(
            0, 0, window.winfo_screenwidth(), window.winfo_screenheight(), primary=True
        )
    ]


def primary_monitor(window) -> MonitorRect:
    monitors = list_monitors(window)
    for monitor in monitors:
        if monitor.primary:
            return monitor
    return monitors[0]


def monitor_containing_point(window, x: int, y: int) -> MonitorRect | None:
    for monitor in list_monitors(window):
        if monitor.contains_point(x, y):
            return monitor
    return None


def center_geometry(window, width: int, height: int) -> str:
    """A 'WxH+X+Y' geometry string centered on the primary monitor."""
    monitor = primary_monitor(window)
    x = monitor.x + max(0, (monitor.width - width) // 2)
    y = monitor.y + max(0, (monitor.height - height) // 2)
    return f"{width}x{height}+{x}+{y}"


def safe_top_left_geometry(width: int, height: int) -> str:
    """A small, reliably-on-screen offset from the top-left corner."""
    return f"{width}x{height}+{_SAFE_MARGIN}+{_SAFE_MARGIN}"


def capture_window_state(window) -> dict:
    """Snapshot a window's position plus the monitor it currently sits on.

    The monitor rect is stored alongside (x, y) so that, on a later launch,
    we can tell whether the monitor layout is still the same before trusting
    the saved coordinates.
    """
    window.update_idletasks()
    x, y = window.winfo_x(), window.winfo_y()
    monitor = monitor_containing_point(window, x, y) or primary_monitor(window)
    return {"x": x, "y": y, "monitor": monitor.as_dict()}


def resolve_saved_position(window, saved: dict | None, width: int, height: int) -> str | None:
    """Return a 'WxH+X+Y' geometry string for a saved position, or None if it
    can no longer be trusted (monitor setup changed, window would be off-screen).
    """
    if not saved:
        return None
    try:
        x = int(saved["x"])
        y = int(saved["y"])
        saved_monitor = saved.get("monitor") or {}
        mon_x = int(saved_monitor["x"])
        mon_y = int(saved_monitor["y"])
        mon_w = int(saved_monitor["width"])
        mon_h = int(saved_monitor["height"])
    except (KeyError, TypeError, ValueError):
        return None

    # Only trust the saved coordinates if a monitor with the exact same
    # working-area rect still exists — a best-effort identity match that
    # tolerates unrelated monitors changing elsewhere but not this one.
    match = next(
        (
            m
            for m in list_monitors(window)
            if m.x == mon_x and m.y == mon_y and m.width == mon_w and m.height == mon_h
        ),
        None,
    )
    if match is None:
        return None

    if x < match.x or y < match.y or x + width > match.right or y + height > match.bottom:
        return None

    return f"{width}x{height}+{x}+{y}"
