"""Single-instance guard: second launches wake the running app."""
from __future__ import annotations

import atexit
import ctypes
import socket
import sys
import threading
import time
from collections.abc import Callable

_INSTANCE_HOST = "127.0.0.1"
_INSTANCE_PORT = 47891
_SHOW_COMMAND = b"ELG_SHOW"
_MUTEX_NAME = r"Local\ELG_SingleInstance_v1"
_ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    def __init__(self) -> None:
        self._mutex_handle: int | None = None
        self._server_socket: socket.socket | None = None
        self._listener_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def try_acquire_or_notify(self) -> bool:
        """Return True if this process should become the primary instance."""
        if sys.platform.startswith("win"):
            if not self._acquire_windows_mutex():
                self._notify_running_instance()
                return False

        if not self._bind_listener_socket():
            self._notify_running_instance()
            return False

        atexit.register(self.release)
        return True

    def start_listener(self, on_show: Callable[[], None]) -> None:
        if self._server_socket is None:
            return

        def serve() -> None:
            assert self._server_socket is not None
            self._server_socket.settimeout(0.5)
            while not self._stop_event.is_set():
                try:
                    conn, _addr = self._server_socket.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                try:
                    data = conn.recv(len(_SHOW_COMMAND) + 16)
                finally:
                    conn.close()
                if data.startswith(_SHOW_COMMAND):
                    on_show()

        self._listener_thread = threading.Thread(
            target=serve,
            name="elg-single-instance",
            daemon=True,
        )
        self._listener_thread.start()

    def release(self) -> None:
        self._stop_event.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None

        if self._mutex_handle is not None and sys.platform.startswith("win"):
            ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None

    def _acquire_windows_mutex(self) -> bool:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            return False
        already_exists = ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS
        if already_exists:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        self._mutex_handle = int(handle)
        return True

    def _bind_listener_socket(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((_INSTANCE_HOST, _INSTANCE_PORT))
            sock.listen(5)
        except OSError:
            sock.close()
            return False
        self._server_socket = sock
        return True

    def _notify_running_instance(self) -> None:
        payload = _SHOW_COMMAND
        for _attempt in range(30):
            try:
                with socket.create_connection((_INSTANCE_HOST, _INSTANCE_PORT), timeout=0.2) as conn:
                    conn.sendall(payload)
                    return
            except OSError:
                time.sleep(0.05)
