"""Checks the ELG-side Meeting Mike client against the fake Host.

Verifies marker detection, the process lifecycle and the state machine the
Record overlay renders from. Runs headless: no Tk, Discord or Mongo.

Usage: python tools/meeting_mike_client_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

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


def wait_for(predicate, timeout: float = 30.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def write_marker(data_dir: Path, exe_path: str, *, version: str = "0.0.1-test") -> Path:
    marker = data_dir / "meeting_host.json"
    marker.write_text(
        json.dumps({"schema_version": 1, "exe_path": exe_path, "installed_version": version}),
        encoding="utf-8",
    )
    return marker


def make_launcher(tmp: Path, name: str, *extra_args: str) -> Path:
    """Shell wrapper so the fake Host looks like a normal executable."""
    fake = REPO_ROOT / "tools" / "fake_meeting_host.py"
    launcher = tmp / f"MeetingHost-{name}.sh"
    extra = " ".join(f'"{arg}"' for arg in extra_args)
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{fake}" {extra} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mike-client-") as raw_tmp:
        tmp = Path(raw_tmp)
        data_dir = tmp / "data"
        data_dir.mkdir()
        os.environ["ELG_TEST_DATA_DIR"] = str(data_dir)

        import storage

        storage.get_data_dir = lambda: data_dir  # type: ignore[assignment]

        import meeting_mike_client as client

        client.get_data_dir = lambda: data_dir  # type: ignore[assignment]

        print("marker detection")
        check("absent marker means unavailable", not client.is_meeting_host_available())

        write_marker(data_dir, str(tmp / "does-not-exist"))
        check("marker with missing exe is rejected", not client.is_meeting_host_available())

        (data_dir / "meeting_host.json").write_text("{not json", encoding="utf-8")
        check("corrupt marker is rejected", not client.is_meeting_host_available())

        launcher = make_launcher(tmp, "ok")
        write_marker(data_dir, str(launcher))
        check("valid marker is accepted", client.is_meeting_host_available())
        check("version is exposed", client.host_version() == "0.0.1-test", client.host_version())

        print("start validation")
        session = client.MeetingHostSession()
        started, error = session.start(elg_username="", discord_user_id="111")
        check("missing username is refused", not started and "username" in error.lower(), error)
        started, error = session.start(elg_username="Johan", discord_user_id="")
        check("missing discord id is refused", not started and "discord" in error.lower(), error)

        print("recording lifecycle")
        events: list[str] = []
        session.add_listener(lambda snapshot: events.append(snapshot["state"]))

        started, error = session.start(elg_username="Johan", discord_user_id="111")
        check("start succeeds with a valid marker", started, error)

        check(
            "reaches recording state",
            wait_for(lambda: session.snapshot()["state"] == client.STATE_RECORDING),
            session.snapshot()["state"],
        )
        snapshot = session.snapshot()
        check("channel name captured", "boardroom" in snapshot["channel_name"], snapshot["channel_name"])
        check("participant tracked", snapshot["participants"] == {"111": "Johan"}, str(snapshot["participants"]))
        check("session reports busy", session.is_busy())

        check("elapsed timer advances", wait_for(lambda: session.snapshot()["elapsed"] > 0.0))

        started_again, error_again = session.start(elg_username="Johan", discord_user_id="111")
        check("double start is refused", not started_again and "already" in error_again.lower(), error_again)

        session.stop()
        check(
            "reaches processing state",
            wait_for(lambda: session.snapshot()["state"] == client.STATE_PROCESSING),
            session.snapshot()["state"],
        )

        check(
            "reaches done state",
            wait_for(lambda: session.snapshot()["state"] == client.STATE_DONE),
            session.snapshot()["state"],
        )
        snapshot = session.snapshot()
        check("meeting dir reported", snapshot["meeting_dir"].endswith("fake"), snapshot["meeting_dir"])
        check("summary file exists", (Path(snapshot["meeting_dir"]) / "summary.md").is_file())
        check("phases were observed", client.STATE_PROCESSING in events)
        check("no longer busy", not session.is_busy())
        check("child process reaped", wait_for(lambda: session._process is None))

        session.reset_if_finished()
        check("reset returns to idle", session.snapshot()["state"] == client.STATE_IDLE)

        print("failure handling")
        failing_launcher = make_launcher(tmp, "fail", "--fail-start", "You are not in a voice channel.")
        write_marker(data_dir, str(failing_launcher))
        failing = client.MeetingHostSession()
        started, error = failing.start(elg_username="Johan", discord_user_id="111")
        check("start command is accepted", started, error)
        check(
            "host error surfaces",
            wait_for(lambda: failing.snapshot()["state"] == client.STATE_ERROR),
            failing.snapshot()["state"],
        )
        check(
            "error message preserved",
            "voice channel" in failing.snapshot()["message"],
            failing.snapshot()["message"],
        )

        print("shutdown path")
        write_marker(data_dir, str(launcher))
        quitting = client.MeetingHostSession()
        started, error = quitting.start(elg_username="Johan", discord_user_id="111")
        check("start for shutdown test", started, error)
        check("recording before shutdown", wait_for(lambda: quitting.snapshot()["state"] == client.STATE_RECORDING))
        quitting.shutdown()
        check("child exits on shutdown", wait_for(lambda: quitting._process is None, timeout=10))

        print("no-op safety")
        client.shutdown_session()
        check("module shutdown is safe when unused", True)
        (data_dir / "meeting_host.json").unlink()
        check("uninstall hides the feature", not client.is_meeting_host_available())

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
