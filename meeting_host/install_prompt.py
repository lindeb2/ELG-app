"""Ask whether to download local Whisper when Groq cannot transcribe.

The Host process has no window of its own. Under ELG it emits an event and
waits for install_local or skip_local. A terminal resume can ask on stdin.
"""
from __future__ import annotations

import sys
import threading

from meeting_host import protocol


class InstallPrompt:
    def ask(self, detail: str) -> bool:
        raise NotImplementedError


class WaitingInstallPrompt(InstallPrompt):
    """Blocks the pipeline thread until ELG answers, or the pipe closes."""

    def __init__(self, events: protocol.EventWriter) -> None:
        self._events = events
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._allow = False
        self._closed = False

    def ask(self, detail: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._allow = False
            self._event.clear()
        self._events.emit(protocol.EVENT_NEED_LOCAL_MODELS, detail=detail)
        self._event.wait()
        with self._lock:
            return self._allow and not self._closed

    def answer(self, allow: bool) -> None:
        with self._lock:
            if self._closed:
                return
            self._allow = bool(allow)
            self._event.set()

    def close(self) -> None:
        """Unblock a pipeline that is waiting, and refuse the download."""
        with self._lock:
            self._closed = True
            self._allow = False
            self._event.set()


class DecliningInstallPrompt(InstallPrompt):
    """No one is listening. Do not download and do not block."""

    def ask(self, detail: str) -> bool:
        return False


class ConsoleInstallPrompt(InstallPrompt):
    def ask(self, detail: str) -> bool:
        print(detail, flush=True)
        try:
            answer = input("Download the local speech models? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}


def prompt_for_resume(events: protocol.EventWriter) -> InstallPrompt:
    if sys.stdin is not None and sys.stdin.isatty():
        return ConsoleInstallPrompt()
    return DecliningInstallPrompt()
