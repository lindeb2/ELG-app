"""Meeting Host entry point.

Reads JSONL commands from ELG on stdin and emits JSONL events on stdout. If
stdin closes (ELG exited or crashed) the bot leaves Discord immediately and
the lock is released, so no bot is ever left sitting in a voice channel.
"""
from __future__ import annotations

import argparse
import sys
import threading
import traceback
from pathlib import Path

from meeting_host import config, mongo_host, pipeline, protocol
from meeting_host.install_prompt import WaitingInstallPrompt, prompt_for_resume
from meeting_host.protocol import EventWriter


class Host:
    def __init__(self, base_dir: Path, events: EventWriter) -> None:
        self._base_dir = base_dir
        self._events = events
        self._lock = threading.RLock()
        self._recorder = None
        self._host_username = ""
        self._host_discord_user_id = ""
        self._lock_held = False
        self._worker: threading.Thread | None = None
        self._finished = threading.Event()
        self._exit_code = 0
        self._install_prompt = WaitingInstallPrompt(events)

    # --- command handling -------------------------------------------------
    def handle(self, command: dict) -> None:
        name = command.get("cmd")
        if name == protocol.CMD_START:
            self._start(command)
        elif name == protocol.CMD_STOP:
            self._stop(run_pipeline=True)
        elif name == protocol.CMD_SHUTDOWN:
            self._stop(run_pipeline=False)
            self._finished.set()
        elif name == protocol.CMD_INSTALL_LOCAL:
            self._install_prompt.answer(True)
        elif name == protocol.CMD_SKIP_LOCAL:
            self._install_prompt.answer(False)

    def _start(self, command: dict) -> None:
        with self._lock:
            if self._recorder is not None:
                return

            self._host_username = str(command.get("elg_username") or "").strip()
            self._host_discord_user_id = str(command.get("discord_user_id") or "").strip()

            if not self._host_discord_user_id:
                self._fail("No Discord user-ID was provided.")
                return

            worker = threading.Thread(target=self._start_worker, name="mike-start", daemon=True)
            self._worker = worker
            worker.start()

    def _start_worker(self) -> None:
        from meeting_host.recorder import MeetingRecorder, RecorderError

        try:
            recorder = MeetingRecorder(events=self._events, base_dir=self._base_dir)
            recorder.set_host(
                elg_username=self._host_username,
                discord_user_id=self._host_discord_user_id,
            )
            recorder.start(host_discord_user_id=self._host_discord_user_id)

            acquired, holder = mongo_host.acquire_lock(
                host_username=self._host_username or "unknown",
                channel_id=recorder.channel_id,
                meeting_dir=str(recorder.meeting_dir or ""),
            )
            if not acquired:
                recorder.stop(
                    host_elg_user=self._host_username,
                    host_discord_user_id=self._host_discord_user_id,
                )
                self._fail(f"Already recording on {holder}.")
                return

            with self._lock:
                self._recorder = recorder
                self._lock_held = True

            self._events.emit(protocol.EVENT_RECORDING)
        except RecorderError as exc:
            self._fail(str(exc))
        except Exception as exc:
            self._fail(f"Could not start recording: {exc}")

    def _stop(self, *, run_pipeline: bool) -> None:
        with self._lock:
            recorder = self._recorder
            self._recorder = None
            if recorder is None:
                if not run_pipeline:
                    self._finished.set()
                return

        try:
            result = recorder.stop(
                host_elg_user=self._host_username,
                host_discord_user_id=self._host_discord_user_id,
            )
        except Exception as exc:
            self._release_lock()
            self._fail(f"Could not finish the recording: {exc}")
            return

        self._release_lock()
        self._events.emit(protocol.EVENT_STOPPED)

        meeting_dir = result.meeting_dir
        pipeline.write_status(
            meeting_dir,
            pipeline="pending",
            channel_name=result.channel_name,
            host_username=self._host_username,
        )

        if not run_pipeline:
            # ELG is closing; audio is on disk and `--resume` can finish later.
            self._finished.set()
            return

        worker = threading.Thread(
            target=self._pipeline_worker,
            args=(meeting_dir,),
            name="mike-pipeline",
            daemon=True,
        )
        self._worker = worker
        worker.start()

    def _pipeline_worker(self, meeting_dir: Path) -> None:
        try:
            pipeline.run(
                self._base_dir,
                meeting_dir,
                self._events,
                install_prompt=self._install_prompt,
            )
            self._events.emit(protocol.EVENT_DONE, meeting_dir=str(meeting_dir))
        except Exception as exc:
            pipeline.write_status(meeting_dir, pipeline="failed", error=str(exc))
            self._fail(f"Processing failed: {exc}")
        finally:
            self._finished.set()

    # --- helpers ---------------------------------------------------------
    def _release_lock(self) -> None:
        with self._lock:
            if not self._lock_held:
                return
            self._lock_held = False
        mongo_host.release_lock(host_username=self._host_username or "unknown")

    def _fail(self, message: str) -> None:
        self._events.emit(protocol.EVENT_ERROR, message=message)
        self._exit_code = 1
        self._finished.set()

    def shutdown(self) -> None:
        """Leave Discord and free the lock; keep whatever is on disk."""
        self._stop(run_pipeline=False)
        self._release_lock()
        mongo_host.close()

    def on_stdin_closed(self) -> None:
        """ELG exited or crashed: never leave the bot sitting in voice."""
        self._install_prompt.close()
        self.shutdown()
        self._finished.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._finished.wait(timeout)

    @property
    def exit_code(self) -> int:
        return self._exit_code


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="meeting_host", description="ELG Meeting Host")
    parser.add_argument("--elg-data-dir", default=None, help="ELG per-user data directory")
    parser.add_argument(
        "--resume",
        default=None,
        metavar="MEETING_DIR",
        help="Re-run processing for a meeting folder ('latest' for the newest unfinished one)",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Verify imports and paths without touching Discord",
    )
    parser.add_argument(
        "--invite",
        action="store_true",
        help="Print (and open) an Administrator re-invite URL for Meeting Mike",
    )
    return parser.parse_args(argv)


def _run_resume(base_dir: Path, events: EventWriter, target: str) -> int:
    if target == "latest":
        meeting_dir = pipeline.latest_pending(base_dir)
        if meeting_dir is None:
            events.emit(protocol.EVENT_ERROR, message="No unfinished meeting was found.")
            return 1
    else:
        meeting_dir = Path(target)
        if not meeting_dir.is_dir():
            events.emit(protocol.EVENT_ERROR, message=f"No such meeting folder: {target}")
            return 1

    try:
        pipeline.resume(base_dir, meeting_dir, events, install_prompt=prompt_for_resume(events))
    except Exception as exc:
        pipeline.write_status(meeting_dir, pipeline="failed", error=str(exc))
        events.emit(protocol.EVENT_ERROR, message=f"Processing failed: {exc}")
        return 1

    events.emit(protocol.EVENT_DONE, meeting_dir=str(meeting_dir))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    events = EventWriter()

    try:
        base_dir = config.data_dir(args.elg_data_dir)
    except Exception as exc:
        events.emit(protocol.EVENT_ERROR, message=f"Could not resolve the ELG data folder: {exc}")
        return 1

    if args.invite:
        from meeting_host.bot_invite import invite_url

        url = invite_url()
        print(url)
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
        return 0

    if args.selftest:
        events.emit(
            protocol.EVENT_READY,
            data_dir=str(base_dir),
            models_dir=str(config.models_dir(base_dir)),
        )
        return 0

    if args.resume:
        return _run_resume(base_dir, events, args.resume)

    host = Host(base_dir, events)
    events.emit(protocol.EVENT_READY, data_dir=str(base_dir))

    def pump() -> None:
        try:
            # Returns on EOF, i.e. when ELG closes the pipe or exits.
            protocol.read_commands(host.handle)
        except Exception:
            events.emit(protocol.EVENT_ERROR, message=traceback.format_exc(limit=3))
        finally:
            host.on_stdin_closed()

    reader = threading.Thread(target=pump, name="mike-stdin", daemon=True)
    reader.start()

    # Exits once the recording is processed, failed, or ELG went away.
    host.wait()
    host.shutdown()
    return host.exit_code


if __name__ == "__main__":
    sys.exit(main())
