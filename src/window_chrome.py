"""Windows-native title bar chrome (proven in src/temp.py)."""
from __future__ import annotations

import os
import tkinter

import customtkinter as ctk

from platform_keys import IS_MACOS, IS_WINDOWS

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
CAPTION_COLOR_BLACK_BGR = 0x000000
CAPTION_COLOR_BLACK_HEX = "#000000"
CAPTION_HOVER_HEX = "#252525"

# Backward-compatible aliases.
CAPTION_COLOR_BGR = CAPTION_COLOR_BLACK_BGR
CAPTION_COLOR_HEX = CAPTION_COLOR_BLACK_HEX

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_EX_DLGMODALFRAME = 0x00000001
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
_FRAME_CHANGED = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | SWP_NOACTIVATE

_ICON_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_app_icon_path() -> str:
    """Single multi-size app icon: dist root when frozen, nuitka/icons in dev."""
    candidates = (
        os.path.join(_ICON_DIR, "elg.ico"),
        os.path.join(os.path.dirname(_ICON_DIR), "nuitka", "icons", "elg.ico"),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return candidates[0]


APP_ICON_PATH = _resolve_app_icon_path()
CAPTION_ICON_PATH = APP_ICON_PATH
TASKBAR_ICON_PATH = APP_ICON_PATH

def deactivate_ctk_title_bar_manipulation() -> None:
    if IS_WINDOWS:
        ctk.CTk._deactivate_windows_window_header_manipulation = True  # type: ignore[attr-defined]


def _hwnd(window: ctk.CTk) -> int:
    from ctypes import windll

    return windll.user32.GetParent(window.winfo_id())  # type: ignore[attr-defined]


def warm_widget_title_bar_assets() -> None:
    """No-op; kept for callers that pre-warm widget chrome."""
    return


def _hide_title_bar_icon_hwnd(hwnd: int) -> None:
    from ctypes import windll

    ex_style = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    needs_frame_refresh = not (ex_style & WS_EX_DLGMODALFRAME)
    if needs_frame_refresh:
        windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_DLGMODALFRAME)

    # Real icons on both slots (Task Manager reads ICON_SMALL). DLGMODALFRAME hides
    # the caption icon in the title bar without replacing it with a black placeholder.
    _set_window_icons(
        hwnd,
        caption_icon_path=CAPTION_ICON_PATH,
        taskbar_icon_path=TASKBAR_ICON_PATH,
    )

    if needs_frame_refresh:
        windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _FRAME_CHANGED)


def hide_visible_caption_text(window: ctk.CTk, caption_color: int | None = None) -> None:
    """Keep wm/taskbar title; hide the caption label beside the icon."""
    if not IS_WINDOWS:
        return
    if caption_color is None:
        caption_color = CAPTION_COLOR_BLACK_BGR
    try:
        from ctypes import byref, c_int, sizeof, windll

        hwnd = _hwnd(window)
        windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(c_int(1)), sizeof(c_int)
        )
        color = c_int(caption_color)
        windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd, DWMWA_CAPTION_COLOR, byref(color), sizeof(c_int)
        )
        windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd, DWMWA_TEXT_COLOR, byref(color), sizeof(c_int)
        )
    except (ImportError, AttributeError, OSError):
        pass


def configure_app_icon(
    window: ctk.CTk,
    *,
    caption_icon_path: str | None = None,
    taskbar_icon_path: str | None = None,
) -> None:
    """Install caption (16px) and taskbar icons before the window maps."""
    if IS_MACOS:
        return
    caption = caption_icon_path or CAPTION_ICON_PATH
    taskbar = taskbar_icon_path or TASKBAR_ICON_PATH
    taskbar_for_tk = taskbar if os.path.isfile(taskbar) else caption
    if os.path.isfile(taskbar_for_tk):
        try:
            window.tk.call(  # type: ignore[attr-defined]
                "wm", "iconbitmap", window._w, "-default", taskbar_for_tk
            )
        except tkinter.TclError:
            pass
        window.iconbitmap(taskbar_for_tk)
    if IS_WINDOWS:
        _set_window_icons(_hwnd(window), caption_icon_path=caption, taskbar_icon_path=taskbar)


def _set_icon_from_file(hwnd: int, icon_type: int, icon_path: str, width: int, height: int) -> None:
    from ctypes import windll

    lr_loadfromfile = 0x0010
    image_icon = 1
    handle = windll.user32.LoadImageW(
        0,
        icon_path,
        image_icon,
        width,
        height,
        lr_loadfromfile,
    )
    if not handle:
        handle = windll.user32.LoadImageW(
            0,
            icon_path,
            image_icon,
            0,
            0,
            lr_loadfromfile,
        )
    if handle:
        windll.user32.SendMessageW(hwnd, WM_SETICON, icon_type, handle)


def _set_window_icons(
    hwnd: int,
    *,
    caption_icon_path: str,
    taskbar_icon_path: str,
) -> None:
    if os.path.isfile(caption_icon_path):
        _set_icon_from_file(hwnd, ICON_SMALL, caption_icon_path, 16, 16)
    if os.path.isfile(taskbar_icon_path):
        _set_icon_from_file(hwnd, ICON_BIG, taskbar_icon_path, 32, 32)


def hide_title_bar_icon(window: ctk.CTk) -> None:
    """Hide the caption icon only; taskbar / Alt+Tab keep the app icon."""
    if not IS_WINDOWS:
        return
    try:
        _hide_title_bar_icon_hwnd(_hwnd(window))
    except (ImportError, AttributeError, OSError):
        pass


def restore_title_bar_icon(
    window: ctk.CTk,
    *,
    caption_icon_path: str | None = None,
    taskbar_icon_path: str | None = None,
) -> None:
    if not IS_WINDOWS:
        return
    caption = caption_icon_path or CAPTION_ICON_PATH
    taskbar = taskbar_icon_path or TASKBAR_ICON_PATH
    try:
        from ctypes import windll

        hwnd = _hwnd(window)
        _set_window_icons(hwnd, caption_icon_path=caption, taskbar_icon_path=taskbar)
        ex_style = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style & ~WS_EX_DLGMODALFRAME)
        windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _FRAME_CHANGED)
    except (ImportError, AttributeError, OSError):
        pass


def remove_minimize_maximize_buttons(window: ctk.CTk) -> None:
    """Remove minimize and maximize; keep close button and app icon."""
    if not IS_WINDOWS:
        return
    try:
        from ctypes import windll

        hwnd = _hwnd(window)
        style = windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        style &= ~WS_MINIMIZEBOX
        style &= ~WS_MAXIMIZEBOX
        windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
        windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _FRAME_CHANGED)
    except (ImportError, AttributeError, OSError):
        pass


def restore_minimize_maximize_buttons(window: ctk.CTk) -> None:
    """Restore minimize and maximize buttons (full app mode)."""
    if not IS_WINDOWS:
        return
    try:
        from ctypes import windll

        hwnd = _hwnd(window)
        style = windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        style |= WS_MINIMIZEBOX
        style |= WS_MAXIMIZEBOX
        windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
        windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _FRAME_CHANGED)
    except (ImportError, AttributeError, OSError):
        pass


def configure_window_title(window: ctk.CTk, title: str) -> None:
    window.title("" if IS_MACOS else title)


def outer_size_for_client(
    window: ctk.CTk, client_width: int, client_height: int
) -> tuple[int, int]:
    """Total window size for a desired client (content) area."""
    if not IS_WINDOWS:
        return client_width, client_height
    try:
        from ctypes import byref, windll, wintypes

        hwnd = _hwnd(window)
        style = windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        ex_style = windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        rect = wintypes.RECT(0, 0, client_width, client_height)
        if not windll.user32.AdjustWindowRectEx(byref(rect), style, False, ex_style):
            return client_width, client_height
        return rect.right - rect.left, rect.bottom - rect.top
    except (ImportError, AttributeError, OSError):
        return client_width, client_height


def apply_app_title_bar_chrome(
    window: ctk.CTk,
    *,
    caption_icon_path: str | None = None,
    taskbar_icon_path: str | None = None,
) -> None:
    """Full app: icon, hidden title text, normal min/max/close."""
    if IS_MACOS:
        window.title("")
        return
    restore_title_bar_icon(
        window,
        caption_icon_path=caption_icon_path,
        taskbar_icon_path=taskbar_icon_path,
    )
    hide_visible_caption_text(window, CAPTION_COLOR_BLACK_BGR)
    restore_minimize_maximize_buttons(window)


def apply_widget_title_bar_chrome(window: ctk.CTk) -> None:
    """Widget mode: no icon, hidden title text, close only."""
    if IS_MACOS:
        window.title("")
        return
    hide_title_bar_icon(window)
    hide_visible_caption_text(window, CAPTION_COLOR_BLACK_BGR)
    remove_minimize_maximize_buttons(window)


def apply_widget_chrome(window: ctk.CTk, *, enabled: bool) -> None:
    generation = getattr(window, "_chrome_generation", 0) + 1
    window._chrome_generation = generation  # type: ignore[attr-defined]

    if enabled:
        window.attributes("-topmost", True)
        apply_widget_title_bar_chrome(window)

        def _resync_widget() -> None:
            if getattr(window, "_chrome_generation", 0) != generation:
                return
            apply_widget_title_bar_chrome(window)

        window.after(150, _resync_widget)
    else:
        window.attributes("-topmost", False)
        apply_app_title_bar_chrome(window)

        def _resync_app() -> None:
            if getattr(window, "_chrome_generation", 0) != generation:
                return
            apply_app_title_bar_chrome(window)

        window.after(150, _resync_app)


# Backward-compatible alias used by meeting fullscreen exit paths.
def apply_dwm_theming(window: ctk.CTk) -> None:
    apply_app_title_bar_chrome(window)
