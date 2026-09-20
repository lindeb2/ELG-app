"""Checks Meeting Host orchestration with Discord, Mongo and Whisper stubbed.

Covers the risky parts: the recording lock, stop versus app-shutdown, and that
audio is never abandoned without a way to finish processing later.

Usage: python tools/meeting_host_flow_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from meeting_host import mongo_host, pipeline, protocol, recorder as recorder_module
from meeting_host.__main__ import Host

_failures: list[str] = []
_checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(' - ' + detail) if detail else ''}")
        _failures.append(label)


def wait_for(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class CapturingEvents(protocol.EventWriter):
    def __init__(self) -> None:
        self.records: list[dict] = []

        class _Sink:
            def write(self_inner, _text: str) -> None:
                pass

            def flush(self_inner) -> None:
                pass

        super().__init__(_Sink())

    def emit(self, event: str, **fields) -> None:
        self.records.append({"event": event, **fields})
        super().emit(event, **fields)

    def names(self) -> list[str]:
        return [record["event"] for record in self.records]

    def first(self, event: str) -> dict | None:
        for record in self.records:
            if record["event"] == event:
                return record
        return None


@dataclass
class StubState:
    stop_calls: int = 0
    lock_acquired: list[dict] = field(default_factory=list)
    lock_released: list[str] = field(default_factory=list)
    pipeline_runs: list[Path] = field(default_factory=list)
    acquire_result: tuple[bool, str] = (True, "Johan")
    start_error: str = ""
    pipeline_error: str = ""


def install_stubs(tmp: Path, state: StubState) -> None:
    meeting_dir = tmp / "meeting"
    (meeting_dir / "audio" / "users").mkdir(parents=True, exist_ok=True)

    class StubRecorder:
        def __init__(self, *, events, base_dir) -> None:
            self._events = events
            self.meeting_dir = meeting_dir
            self.channel_id = "555"
            self.channel_name = "\U0001F935boardroom"

        def set_host(self, *, elg_username, discord_user_id) -> None:
            self.host = (elg_username, discord_user_id)

        def start(self, *, host_discord_user_id, timeout=90.0) -> None:
            if state.start_error:
                raise recorder_module.RecorderError(state.start_error)
            self._events.emit("joined", channel_name=self.channel_name)

        def stop(self, *, host_elg_user, host_discord_user_id):
            state.stop_calls += 1
            (meeting_dir / "attendance.json").write_text(
                json.dumps({"participants": []}), encoding="utf-8"
            )
            return recorder_module.RecordingResult(
                meeting_dir=meeting_dir,
                channel_id=self.channel_id,
                channel_name=self.channel_name,
                started_at=__import__("datetime").datetime.now(),
                ended_at=__import__("datetime").datetime.now(),
                participants=[],
            )

    def stub_acquire(*, host_username, channel_id, meeting_dir):
        state.lock_acquired.append({"host": host_username, "channel": channel_id})
        return state.acquire_result

    def stub_release(*, host_username):
        state.lock_released.append(host_username)

    def stub_run(base_dir, target_dir, events, skip_existing=False):
        if state.pipeline_error:
            raise RuntimeError(state.pipeline_error)
        state.pipeline_runs.append(target_dir)
        events.phase(protocol.PHASE_SUMMARY, 1.0)
        return target_dir / "summary.md"

    recorder_module.MeetingRecorder = StubRecorder  # type: ignore[assignment]
    mongo_host.acquire_lock = stub_acquire  # type: ignore[assignment]
    mongo_host.release_lock = stub_release  # type: ignore[assignment]
    mongo_host.close = lambda: None  # type: ignore[assignment]
    pipeline.run = stub_run  # type: ignore[assignment]

    import meeting_host.models as models_module

    models_module.missing_assets = lambda base: []  # type: ignore[assignment]


def run_case(tmp: Path, name: str, state: StubState) -> tuple[Host, CapturingEvents]:
    case_dir = tmp / name
    case_dir.mkdir(parents=True, exist_ok=True)
    events = CapturingEvents()
    host = Host(case_dir, events)
    return host, events


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mike-flow-") as raw_tmp:
        tmp = Path(raw_tmp)

        print("happy path: start then stop")
        state = StubState()
        install_stubs(tmp, state)
        host, events = run_case(tmp, "happy", state)
        host.handle({"cmd": "start", "elg_username": "Johan", "discord_user_id": "111"})
        check("recording is announced", wait_for(lambda: "recording" in events.names()), str(events.names()))
        check("lock taken once", len(state.lock_acquired) == 1, str(state.lock_acquired))
        check("lock taken for the host user", state.lock_acquired[0]["host"] == "Johan")

        host.handle({"cmd": "stop"})
        check("recording is finalized", state.stop_calls == 1, str(state.stop_calls))
        check("lock released on stop", state.lock_released == ["Johan"], str(state.lock_released))
        check("stopped event emitted", "stopped" in events.names())
        check("pipeline ran", wait_for(lambda: len(state.pipeline_runs) == 1), str(state.pipeline_runs))
        check("done event emitted", wait_for(lambda: "done" in events.names()), str(events.names()))
        check("host finishes", host.wait(5))
        check("exit code is success", host.exit_code == 0, str(host.exit_code))

        print("lock held elsewhere")
        state = StubState(acquire_result=(False, "Ada"))
        install_stubs(tmp, state)
        host, events = run_case(tmp, "locked", state)
        host.handle({"cmd": "start", "elg_username": "Johan", "discord_user_id": "111"})
        check("error is reported", wait_for(lambda: "error" in events.names()), str(events.names()))
        message = (events.first("error") or {}).get("message", "")
        check("error names the holder", "Ada" in message, message)
        check("recorder was torn down", state.stop_calls == 1, str(state.stop_calls))
        check("no pipeline ran", state.pipeline_runs == [])
        check("exit code signals failure", host.exit_code == 1)

        print("missing discord id")
        state = StubState()
        install_stubs(tmp, state)
        host, events = run_case(tmp, "no-id", state)
        host.handle({"cmd": "start", "elg_username": "Johan", "discord_user_id": ""})
        check("start refused", (events.first("error") or {}).get("message", "").startswith("No Discord"))
        check("no lock attempted", state.lock_acquired == [])

        print("discord join failure")
        state = StubState(start_error="You are not in a voice channel.")
        install_stubs(tmp, state)
        host, events = run_case(tmp, "join-fail", state)
        host.handle({"cmd": "start", "elg_username": "Johan", "discord_user_id": "111"})
        check("join error surfaces", wait_for(lambda: "error" in events.names()), str(events.names()))
        check(
            "message is user-facing",
            "voice channel" in (events.first("error") or {}).get("message", ""),
        )
        check("no lock left behind", state.lock_released == [] and state.lock_acquired == [])

        print("app shutdown while recording")
        state = StubState()
        install_stubs(tmp, state)
        host, events = run_case(tmp, "shutdown", state)
        host.handle({"cmd": "start", "elg_username": "Johan", "discord_user_id": "111"})
        check("recording started", wait_for(lambda: "recording" in events.names()))
        host.on_stdin_closed()
        check("recorder stopped so the bot leaves", state.stop_calls == 1, str(state.stop_calls))
        check("lock released", state.lock_released == ["Johan"], str(state.lock_released))
        check("long pipeline skipped", state.pipeline_runs == [], str(state.pipeline_runs))

        status_path = tmp / "meeting" / "status.json"
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
        check("status marks work as pending", status.get("pipeline") == "pending", str(status))
        check("audio is retained for resume", (tmp / "meeting" / "attendance.json").is_file())

        print("pipeline failure")
        state = StubState(pipeline_error="whisper exploded")
        install_stubs(tmp, state)
        host, events = run_case(tmp, "pipeline-fail", state)
        host.handle({"cmd": "start", "elg_username": "Johan", "discord_user_id": "111"})
        check("recording started", wait_for(lambda: "recording" in events.names()))
        host.handle({"cmd": "stop"})
        check("failure reported", wait_for(lambda: "error" in events.names()), str(events.names()))
        check(
            "failure mentions processing",
            "Processing failed" in (events.first("error") or {}).get("message", ""),
        )
        check("lock still released", state.lock_released == ["Johan"], str(state.lock_released))
        status = json.loads((tmp / "meeting" / "status.json").read_text(encoding="utf-8"))
        check("status records the failure", status.get("pipeline") == "failed", str(status))

        print("stop without start")
        state = StubState()
        install_stubs(tmp, state)
        host, events = run_case(tmp, "stop-only", state)
        host.handle({"cmd": "stop"})
        check("stop is a no-op", state.stop_calls == 0 and events.names() == [])
        host.shutdown()
        check("shutdown is a no-op", state.lock_released == [])

    print()
    if _failures:
        print(f"FAILED {len(_failures)}/{_checks}:")
        for failure in _failures:
            print(f"  - {failure}")
        return 1
    print(f"All {_checks} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
