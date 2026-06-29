"""System tray integration for ELG."""
from __future__ import annotations

import threading
from collections.abc import Callable

from PIL import Image
from pystray import Icon, Menu, MenuItem


class SystemTray:
    def __init__(
        self,
        icon_path: str,
        *,
        on_show: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._icon_path = icon_path
        self._on_show = on_show
        self._on_exit = on_exit
        self._icon: Icon | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._icon is not None

    def ensure_started(self) -> None:
        with self._lock:
            if self._icon is not None:
                return

            image = Image.open(self._icon_path)
            menu = Menu(
                MenuItem("Show ELG", self._handle_show, default=True),
                Menu.SEPARATOR,
                MenuItem("Exit", self._handle_exit),
            )
            self._icon = Icon("ELG", image, "ELG", menu)
            self._thread = threading.Thread(target=self._icon.run, name="elg-tray", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            icon = self._icon
            self._icon = None
            self._thread = None
        if icon is not None:
            icon.stop()

    def _handle_show(self, _icon=None, _item=None) -> None:
        self._on_show()

    def _handle_exit(self, _icon=None, _item=None) -> None:
        self._on_exit()
