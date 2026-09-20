"""Stand-in for the Meeting Host binary, for testing the ELG side.

Speaks the same JSONL protocol without Discord, Whisper or llama.cpp. Point a
marker file at this script (see tools/meeting_mike_client_test.py) to exercise
the Record overlay on a machine that has no Host install.

Usage: python tools/fake_meeting_host.py --elg-data-dir <dir> [--fail-start "reason"]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

_PHASES = ("mix", "transcribe_mix", "transcribe_users", "merge", "summary")


def emit(event: str, **fields) -> None:
    sys.stdout.write(json.dumps({"event": event, **fields}) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elg-data-dir", default=".")
    parser.add_argument("--fail-start", default="")
    parser.add_argument("--speed", type=float, default=0.05, help="Seconds per simulated step")
    args = parser.parse_args()

    data_dir = Path(args.elg_data_dir)
    meeting_dir = data_dir / "meeting_mike" / "meetings" / "fake"
    finished = threading.Event()
    recording = threading.Event()

    emit("ready", data_dir=str(data_dir))

    def run_pipeline() -> None:
        emit("stopped")
        for index, phase in enumerate(_PHASES):
            emit("phase", name=phase, progress=0.0)
            time.sleep(args.speed)
            emit("phase", name=phase, progress=1.0)
        meeting_dir.mkdir(parents=True, exist_ok=True)
        (meeting_dir / "summary.md").write_text("# Fake summary\n", encoding="utf-8")
        emit("done", meeting_dir=str(meeting_dir))
        finished.set()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except ValueError:
            continue

        name = command.get("cmd")
        if name == "start":
            if args.fail_start:
                emit("error", message=args.fail_start)
                return 1
            emit("need_models", detail="Preparing local models (one-time download)")
            time.sleep(args.speed)
            emit("models_ready")
            emit("joined", channel_name="\U0001F935boardroom")
            emit(
                "participant",
                discord_user_id="111",
                elg_username=command.get("elg_username"),
                display_name="host",
                action="join",
                joined_late=False,
            )
            emit("recording")
            recording.set()
        elif name == "stop":
            if recording.is_set():
                recording.clear()
                threading.Thread(target=run_pipeline, daemon=True).start()
                finished.wait(30)
            return 0
        elif name == "shutdown":
            emit("stopped")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
