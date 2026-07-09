"""Windows shutdown / logoff blocking per Microsoft guidance."""
from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
GWLP_WNDPROC = -4

_SHUTDOWN_REASON = "ELG has an unlogged timetable session."

_user32 = None
_WNDPROC = None


def _configure_user32() -> None:
    if _user32 is None:
        return

    from ctypes import wintypes

    _user32.SetWindowLongPtrW.argtypes = (
        wintypes.HWND,
        ctypes.c_int,
        _LRESULT,
    )
    _user32.SetWindowLongPtrW.restype = _LRESULT

    _user32.CallWindowProcW.argtypes = (
        _LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    _user32.CallWindowProcW.restype = _LRESULT

    _user32.DefWindowProcW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    _user32.DefWindowProcW.restype = _LRESULT

    _user32.ShutdownBlockReasonCreate.argtypes = (wintypes.HWND, wintypes.LPCWSTR)
    _user32.ShutdownBlockReasonCreate.restype = wintypes.BOOL
    _user32.ShutdownBlockReasonDestroy.argtypes = (wintypes.HWND,)
    _user32.ShutdownBlockReasonDestroy.restype = wintypes.BOOL


if sys.platform.startswith("win"):
    from ctypes import wintypes

    _user32 = ctypes.windll.user32

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _LRESULT = ctypes.c_longlong
    else:
        _LRESULT = ctypes.c_long

    _WNDPROC = ctypes.WINFUNCTYPE(
        _LRESULT,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    _configure_user32()


def _proc_pointer(proc) -> int:
    return ctypes.cast(proc, ctypes.c_void_p).value or 0


class ShutdownBlocker:
    """Block OS shutdown while unlogged timetable time exists.

    Uses ShutdownBlockReasonCreate/Destroy plus WM_QUERYENDSESSION as
    recommended by Microsoft Learn shutdown documentation.
    """

    def __init__(
        self,
        window,
        should_block: Callable[[], bool],
        on_end_session: Callable[[], None] | None = None,
    ) -> None:
        self._window = window
        self._should_block = should_block
        self._on_end_session = on_end_session
        self._hwnd: int | None = None
        self._old_wndproc: int = 0
        self._new_wndproc = None
        self._installed = False
        self._reason_active = False
        self._end_session_notified = False

    def _notify_end_session(self) -> None:
        if self._end_session_notified or self._on_end_session is None:
            return
        self._end_session_notified = True
        try:
            self._on_end_session()
        except Exception:
            return

    def install(self) -> None:
        if self._installed or not sys.platform.startswith("win") or _user32 is None:
            return

        self._window.update_idletasks()
        self._hwnd = int(self._window.winfo_id())
        self._new_wndproc = _WNDPROC(self._wndproc)
        previous = _user32.SetWindowLongPtrW(
            self._hwnd,
            GWLP_WNDPROC,
            _proc_pointer(self._new_wndproc),
        )
        self._old_wndproc = int(previous)
        self._installed = True
        self.sync()

    def sync(self) -> None:
        if not self._installed or self._hwnd is None or _user32 is None:
            return

        blocking = bool(self._should_block())
        if blocking and not self._reason_active:
            if _user32.ShutdownBlockReasonCreate(self._hwnd, _SHUTDOWN_REASON):
                self._reason_active = True
        elif not blocking and self._reason_active:
            if _user32.ShutdownBlockReasonDestroy(self._hwnd):
                self._reason_active = False

    def teardown(self) -> None:
        if not self._installed or self._hwnd is None or _user32 is None:
            return

        if self._reason_active:
            _user32.ShutdownBlockReasonDestroy(self._hwnd)
            self._reason_active = False

        if self._new_wndproc is not None and self._old_wndproc:
            _user32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, self._old_wndproc)
            self._old_wndproc = 0
            self._new_wndproc = None

        self._installed = False

    def _call_default(self, hwnd, msg, wparam, lparam):
        assert _user32 is not None
        if self._old_wndproc:
            return _user32.CallWindowProcW(
                self._old_wndproc,
                hwnd,
                msg,
                wparam,
                lparam,
            )
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        assert _user32 is not None
        if msg == WM_QUERYENDSESSION and wparam:
            if self._should_block():
                if not self._reason_active:
                    _user32.ShutdownBlockReasonCreate(hwnd, _SHUTDOWN_REASON)
                    self._reason_active = True
                return 0
            self.sync()
            self._notify_end_session()
        elif msg == WM_ENDSESSION:
            if wparam:
                self._notify_end_session()
            if self._reason_active:
                _user32.ShutdownBlockReasonDestroy(hwnd)
                self._reason_active = False

        return self._call_default(hwnd, msg, wparam, lparam)
