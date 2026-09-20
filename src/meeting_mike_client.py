"""ELG-side client for the optional Meeting Host (Meeting Mike) sidecar.

The Host is a separate install. This module only detects it, drives it over a
JSONL pipe and exposes its state to the meeting view. Nothing here imports
Discord, Whisper or LLM code, and every entry point is a no-op when the Host
is absent, so the base app is unaffected.

The session outlives the meeting view (which is destroyed on navigation), so
the process manager is a module-level singleton.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from storage import get_data_dir

MARKER_FILENAME = "meeting_host.json"

STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_RECORDING = "recording"
STATE_PROCESSING = "processing"
STATE_DONE = "done"
STATE_ERROR = "error"

_PHASE_LABELS = {
    "mix": "Mixing audio",
    "transcribe_mix": "Transcribing meeting",
    "transcribe_users": "Transcribing speakers",
    "merge": "Identifying speakers",
    "summary": "Writing summary",
}

_STOP_GRACE_SECONDS = 3.0


def marker_path() -> Path:
    return get_data_dir() / MARKER_FILENAME


def read_marker() -> dict | None:
    """Parsed Host marker, or None when not installed/unreadable."""
    try:
        with open(marker_path(), encoding="utf-8") as file:
            marker = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(marker, dict):
        return None
    exe = marker.get("exe_path")
    if not exe or not Path(exe).is_file():
        return None
    return marker


def _launch_command(marker: dict, data_dir: Path) -> list[str]:
    """The Host ships as an interpreter plus args, so both come from the marker."""
    args = marker.get("args")
    extra = [str(arg) for arg in args] if isinstance(args, list) else []
    return [str(marker["exe_path"]), *extra, "--elg-data-dir", str(data_dir)]


def is_meeting_host_available() -> bool:
    return read_marker() is not None


def host_version() -> str:
    marker = read_marker()
    return str((marker or {}).get("installed_version") or "")


def open_in_file_manager(path: str | Path) -> None:
    target = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except OSError:
        pass


class MeetingHostSession:
    """Owns at most one Host child process and mirrors its reported state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._listeners: list[Callable[[dict], None]] = []

        self._state = STATE_IDLE
        self._message = ""
        self._channel_name = ""
        self._meeting_dir = ""
        self._phase = ""
        self._phase_progress = 0.0
        self._started_monotonic: float | None = None
        self._participants: dict[str, str] = {}

    # --- observation -----------------------------------------------------
    def add_listener(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = 0.0
            if self._started_monotonic is not None:
                elapsed = max(0.0, time.monotonic() - self._started_monotonic)
            return {
                "state": self._state,
                "message": self._message,
                "channel_name": self._channel_name,
                "meeting_dir": self._meeting_dir,
                "phase": self._phase,
                "phase_label": _PHASE_LABELS.get(self._phase, ""),
                "phase_progress": self._phase_progress,
                "elapsed": elapsed,
                "participants": dict(self._participants),
                "busy": self._state in (STATE_STARTING, STATE_RECORDING, STATE_PROCESSING),
            }

    def is_busy(self) -> bool:
        return bool(self.snapshot()["busy"])

    def _notify(self) -> None:
        snapshot = self.snapshot()
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(snapshot)
            except Exception:
                pass

    # --- lifecycle -------------------------------------------------------
    def start(self, *, elg_username: str, discord_user_id: str) -> tuple[bool, str]:
        """Spawn the Host and ask it to record. Returns (started, error)."""
        marker = read_marker()
        if marker is None:
            return False, "Meeting Host is not installed."
        if not elg_username:
            return False, "Set your username in Settings first."
        if not discord_user_id:
            return False, "Set your Discord user-ID in Settings first."

        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False, "A recording is already running."

            exe = str(marker["exe_path"])
            creationflags = 0
            if sys.platform.startswith("win"):
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                process = subprocess.Popen(
                    _launch_command(marker, get_data_dir()),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=str(Path(exe).parent),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except OSError as exc:
                return False, f"Could not launch Meeting Host: {exc}"

            self._process = process
            self._state = STATE_STARTING
            self._message = "Starting Meeting Mike..."
            self._channel_name = ""
            self._meeting_dir = ""
            self._phase = ""
            self._phase_progress = 0.0
            self._started_monotonic = None
            self._participants = {}

            self._reader = threading.Thread(
                target=self._read_events,
                args=(process,),
                name="meeting-mike-reader",
                daemon=True,
            )
            self._reader.start()

            command = {
                "cmd": "start",
                "elg_username": elg_username,
                "discord_user_id": str(discord_user_id),
            }
            if not self._send(command):
                self._state = STATE_ERROR
                self._message = "Meeting Host did not accept the start command."
                self._notify()
                return False, self._message

        self._notify()
        return True, ""

    def stop(self) -> None:
        """Ask the Host to stop recording and run the pipeline."""
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                return
            if self._state == STATE_RECORDING:
                self._state = STATE_PROCESSING
                self._message = "Finishing up..."
            self._send({"cmd": "stop"})
        self._notify()

    def shutdown(self, *, wait: float = _STOP_GRACE_SECONDS) -> None:
        """Stop the child for app exit: ask nicely, then kill."""
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return

        self._send({"cmd": "shutdown"})
        deadline = time.monotonic() + max(0.0, wait)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.05)

        try:
            process.terminate()
        except OSError:
            pass
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.05)
        try:
            process.kill()
        except OSError:
            pass

    def _send(self, command: dict) -> bool:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            return False
        try:
            process.stdin.write(json.dumps(command) + "\n")
            process.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    # --- event pump ------------------------------------------------------
    def _read_events(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is not None:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(payload, dict) and payload.get("event"):
                    self._apply_event(payload)

        process.wait()
        with self._lock:
            if self._state in (STATE_STARTING, STATE_RECORDING, STATE_PROCESSING):
                self._state = STATE_ERROR
                self._message = "Meeting Host stopped unexpectedly."
                self._started_monotonic = None
            self._process = None
        self._notify()

    def _apply_event(self, payload: dict) -> None:
        event = payload.get("event")
        with self._lock:
            if event == "need_models":
                self._state = STATE_STARTING
                self._message = payload.get("detail") or "Downloading models..."
            elif event == "models_ready":
                self._message = "Models ready."
            elif event == "joined":
                self._channel_name = str(payload.get("channel_name") or "")
                self._message = f"Joined {self._channel_name}"
            elif event == "recording":
                self._state = STATE_RECORDING
                self._started_monotonic = time.monotonic()
                self._message = ""
            elif event == "participant":
                discord_id = str(payload.get("discord_user_id") or "")
                label = str(payload.get("elg_username") or payload.get("display_name") or discord_id)
                if payload.get("action") == "leave":
                    self._participants.pop(discord_id, None)
                elif discord_id:
                    self._participants[discord_id] = label
            elif event == "stopped":
                self._state = STATE_PROCESSING
                self._started_monotonic = None
                self._message = "Processing recording..."
            elif event == "phase":
                self._state = STATE_PROCESSING
                self._phase = str(payload.get("name") or "")
                self._phase_progress = float(payload.get("progress") or 0.0)
                self._message = _PHASE_LABELS.get(self._phase, self._phase)
            elif event == "done":
                self._state = STATE_DONE
                self._meeting_dir = str(payload.get("meeting_dir") or "")
                self._phase = ""
                self._phase_progress = 1.0
                self._started_monotonic = None
                self._message = "Summary saved."
            elif event == "error":
                self._state = STATE_ERROR
                self._started_monotonic = None
                self._message = str(payload.get("message") or "Meeting Host failed.")
        self._notify()

    def reset_if_finished(self) -> None:
        with self._lock:
            if self._state in (STATE_DONE, STATE_ERROR):
                self._state = STATE_IDLE
                self._message = ""
                self._phase = ""
                self._phase_progress = 0.0
        self._notify()


_session: MeetingHostSession | None = None
_session_lock = threading.Lock()


def get_session() -> MeetingHostSession:
    global _session
    with _session_lock:
        if _session is None:
            _session = MeetingHostSession()
        return _session


def shutdown_session() -> None:
    """Called on app exit; safe when the Host was never used."""
    with _session_lock:
        session = _session
    if session is not None:
        session.shutdown()
