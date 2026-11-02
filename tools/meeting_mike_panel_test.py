"""Builds the Meeting Mike Record overlay against real CustomTkinter widgets.

Confirms the panel stays invisible without a Host install, renders each state
correctly when one is present, and detaches cleanly so an active recording is
never stopped by leaving the meeting view.

Usage: python tools/meeting_mike_panel_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Stub the Mongo-backed module before it is imported: the panel only needs the
# Discord id lookup, and this test must not open a database connection.
_prefs_stub = types.ModuleType("notification_preferences")
_prefs_stub.fetch_notification_prefs = lambda username: {"discord_user_id": "111"}  # type: ignore[attr-defined]
sys.modules["notification_preferences"] = _prefs_stub

import customtkinter as ctk

_failures: list[str] = []
_checks = 0


def _is_packed(widget) -> bool:
    """pack_info() raises rather than returning empty for unpacked widgets."""
    try:
        return bool(widget.pack_info())
    except Exception:
        return False


def check(label: str, condition: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(' - ' + detail) if detail else ''}")
        _failures.append(label)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mike-panel-") as raw_tmp:
        tmp = Path(raw_tmp)
        data_dir = tmp / "data"
        data_dir.mkdir()

        import storage

        storage.get_data_dir = lambda: data_dir  # type: ignore[assignment]

        import meeting_mike_client as client

        client.get_data_dir = lambda: data_dir  # type: ignore[assignment]

        import meeting_mike_panel as panel_module

        root = ctk.CTk()
        root.withdraw()

        print("hidden without a Host install")
        parent = ctk.CTkFrame(root)
        check("attach returns None", panel_module.attach(parent, "Johan") is None)
        check("no widgets were added", parent.winfo_children() == [])

        marker = data_dir / "meeting_host.json"
        exe = tmp / "MeetingHost"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        marker.write_text(
            json.dumps({"schema_version": 1, "exe_path": str(exe), "installed_version": "0.0.1"}),
            encoding="utf-8",
        )

        print("visible with a Host install")
        panel = panel_module.attach(parent, "Johan")
        check("attach returns a panel", panel is not None)
        assert panel is not None
        root.update_idletasks()
        check("panel is placed", bool(panel.place_info()), str(panel.place_info()))
        check("starts on Record", panel._button.cget("text") == "Record", panel._button.cget("text"))
        check("status button is separate", panel._status_button.cget("text") == "Status")
        check("open-folder button hidden", not panel._open_button_shown)

        print("state rendering")
        session = panel._session

        panel._render(
            {
                "state": client.STATE_RECORDING,
                "message": "",
                "channel_name": "boardroom",
                "meeting_dir": "",
                "phase": "",
                "phase_label": "",
                "phase_progress": 0.0,
                "elapsed": 95.0,
                "participants": {"1": "Johan", "2": "Ada"},
                "busy": True,
            }
        )
        root.update_idletasks()
        status = panel_module.open_status_window(panel, focus=False)
        root.update_idletasks()
        check("shows Stop while recording", panel._button.cget("text") == "Stop")
        check("Stop is enabled", panel._button.cget("state") == "normal")
        headline = status._headline.cget("text")
        check("elapsed is in the status window", "01:35" in headline, headline)
        check("names are in the status window", "Johan" in headline and "Ada" in headline, headline)

        panel._render(
            {
                "state": client.STATE_PROCESSING,
                "message": "Transcribing meeting",
                "channel_name": "boardroom",
                "meeting_dir": "",
                "phase": "transcribe_mix",
                "phase_label": "Transcribing meeting",
                "phase_progress": 0.4,
                "elapsed": 0.0,
                "participants": {},
                "busy": True,
                "log": ["16:12:01  Transcribing meeting"],
                "steps": [
                    {"name": "mix", "label": "Mixing audio", "model": "", "parent": "", "state": "done", "seconds": 0.4},
                    {
                        "name": "whisper_mix",
                        "label": "Transcribing meeting",
                        "model": "distil-large-v3 · CUDA float16",
                        "parent": "",
                        "state": "running",
                        "seconds": 12.4,
                    },
                    {
                        "name": "whisper_user:2",
                        "label": "Ada",
                        "model": "small.en · CUDA float16",
                        "parent": "whisper_users",
                        "state": "done",
                        "seconds": 9,
                    },
                ],
            }
        )
        root.update_idletasks()
        check("Stop disabled while processing", panel._button.cget("state") == "disabled")
        step_text = " ".join(label.cget("text") for label in status._step_labels.values())
        time_text = " ".join(label.cget("text") for label in status._time_labels.values())
        model_text = " ".join(label.cget("text") for label in status._model_labels.values())
        check("status log is not shown in the window", not hasattr(status, "_log"))
        check("finished step shows its duration", "Mixing audio" in step_text and "0.40s" in time_text, time_text)
        check("running step shows whole seconds", "Transcribing meeting" in step_text and "12s" in time_text, time_text)
        check("meeting model is shown", "distil-large-v3" in model_text, model_text)
        check("speaker model is shown", "small.en" in model_text, model_text)

        meeting_dir = tmp / "meeting"
        meeting_dir.mkdir()
        panel._render(
            {
                "state": client.STATE_DONE,
                "message": "Summary saved.",
                "channel_name": "boardroom",
                "meeting_dir": str(meeting_dir),
                "phase": "",
                "phase_label": "",
                "phase_progress": 1.0,
                "elapsed": 0.0,
                "participants": {},
                "busy": False,
            }
        )
        root.update_idletasks()
        check("back to Record when done", panel._button.cget("text") == "Record")
        check("open-folder button shown", panel._open_button_shown)
        check("open-folder is packed", _is_packed(panel._open_button))
        check("success colour used", status._headline.cget("text_color") == "#00AD00")

        panel._render(
            {
                "state": client.STATE_ERROR,
                "message": "You are not in a voice channel.",
                "channel_name": "",
                "meeting_dir": "",
                "phase": "",
                "phase_label": "",
                "phase_progress": 0.0,
                "elapsed": 0.0,
                "participants": {},
                "busy": False,
            }
        )
        root.update_idletasks()
        check("error text shown", "voice channel" in status._headline.cget("text"))
        check("error colour used", status._headline.cget("text_color") == "#FF4444")
        check("open-folder hidden again", not panel._open_button_shown)
        check("open-folder is unpacked", not _is_packed(panel._open_button))

        print("detach and teardown")
        check("panel listens to the session", panel._on_session_event in session._listeners)
        panel.detach()
        check("listener removed on detach", panel._on_session_event not in session._listeners)
        check("status window stays open after leaving the meeting", status.winfo_exists())
        panel.detach()
        check("detach is idempotent", True)

        print("destroy path")
        panel2 = panel_module.attach(parent, "Johan")
        assert panel2 is not None
        root.update_idletasks()
        check("second panel registered", panel2._on_session_event in session._listeners)
        parent.destroy()
        root.update_idletasks()
        check("listener removed on destroy", panel2._on_session_event not in session._listeners)
        check("no recording was started", session.snapshot()["state"] == client.STATE_IDLE)

        root.destroy()

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
