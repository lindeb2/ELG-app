"""JSONL wire protocol between the ELG app and the Meeting Host process.

ELG writes commands to stdin, the Host writes events to stdout. Both sides
ignore lines that are not JSON objects so stray library output is harmless.
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any, Callable

CMD_START = "start"
CMD_STOP = "stop"
CMD_SHUTDOWN = "shutdown"

EVENT_READY = "ready"
EVENT_NEED_MODELS = "need_models"
EVENT_MODELS_READY = "models_ready"
EVENT_JOINED = "joined"
EVENT_PARTICIPANT = "participant"
EVENT_RECORDING = "recording"
EVENT_STOPPED = "stopped"
EVENT_PHASE = "phase"
EVENT_DONE = "done"
EVENT_ERROR = "error"

PHASE_MIX = "mix"
PHASE_TRANSCRIBE_MIX = "transcribe_mix"
PHASE_TRANSCRIBE_USERS = "transcribe_users"
PHASE_MERGE = "merge"
PHASE_SUMMARY = "summary"

PHASE_LABELS = {
    PHASE_MIX: "Mixing audio",
    PHASE_TRANSCRIBE_MIX: "Transcribing meeting",
    PHASE_TRANSCRIBE_USERS: "Transcribing speakers",
    PHASE_MERGE: "Identifying speakers",
    PHASE_SUMMARY: "Writing summary",
}


class EventWriter:
    """Thread-safe JSONL emitter on stdout."""

    def __init__(self, stream=None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()

    def emit(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **fields}
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except (OSError, ValueError):
                # ELG closed the pipe; keep running so files still get written.
                pass

    def phase(self, name: str, progress: float = 0.0, detail: str = "") -> None:
        self.emit(EVENT_PHASE, name=name, progress=round(float(progress), 4), detail=detail)


def decode_command(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("cmd"):
        return None
    return payload


def read_commands(handler: Callable[[dict], None], stream=None) -> None:
    """Block reading stdin, dispatching each decoded command."""
    source = stream if stream is not None else sys.stdin
    for line in source:
        command = decode_command(line)
        if command is not None:
            handler(command)
