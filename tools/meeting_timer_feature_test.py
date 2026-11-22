"""Offline tests for the weekly meeting timer lock, attendance logging,
summary exclusion, and minimize-from-meeting behavior.

Usage: .venv/bin/python tools/meeting_timer_feature_test.py
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from meeting_host.attendance import (
    Participant,
    build_attendance_payload,
    close_session,
    finalize_attendance,
    meeting_id_for_dir,
    open_session,
)
from meeting_host import facts as facts_module
from meeting_lock import LOCK_STALE_SECONDS, MeetingLockWatcher, is_lock_doc_active
from meeting_log_commit import MEETING_LOG_NAME, commit_meeting_logs, plan_meeting_logs
import meeting_mike_client as mike_client

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


def test_attendance_intervals() -> None:
    print("attendance interval math")
    started = datetime(2026, 11, 22, 17, 0, 0, tzinfo=timezone.utc)

    full_a = Participant(
        discord_user_id="1",
        elg_username="Johan",
        joined_late=False,
        sessions=[(0.0, None)],
    )
    full_b = Participant(
        discord_user_id="2",
        elg_username="Ada",
        joined_late=False,
        sessions=[(0.0, None)],
    )
    # Joined 2 seconds late (no late-join grace window)
    late_user = Participant(
        discord_user_id="3",
        elg_username="Sam",
        joined_late=True,
        sessions=[(2.0, None)],
    )
    # Present at start, disconnected for 5s (do not ignore disconnects), rejoined
    flaky_user = Participant(
        discord_user_id="4",
        elg_username="Kai",
        joined_late=False,
        sessions=[(0.0, None)],
    )
    close_session(flaky_user, 100.0)
    open_session(flaky_user, 105.0)
    # Left early at 60s
    early_leaver = Participant(
        discord_user_id="5",
        elg_username="Mia",
        joined_late=False,
        sessions=[(0.0, None)],
    )
    close_session(early_leaver, 60.0)
    # Very brief attendee (2s, no minimum time)
    brief_user = Participant(
        discord_user_id="6",
        elg_username="Leo",
        joined_late=True,
        sessions=[(10.0, None)],
    )
    close_session(brief_user, 12.0)

    total_seconds, ended_at = finalize_attendance(
        [full_a, full_b, late_user, flaky_user, early_leaver, brief_user],
        started_at=started,
        total_duration_seconds=300.4,
    )

    check("total duration rounded to whole seconds", total_seconds == 300, str(total_seconds))
    check("ended_at matches started + duration", abs((ended_at - started).total_seconds() - 300.4) < 0.01)
    check(
        "full attendees get identical elapsed_seconds",
        full_a.elapsed_seconds == full_b.elapsed_seconds == 300,
        f"{full_a.elapsed_seconds} vs {full_b.elapsed_seconds}",
    )
    check(
        "full attendees get identical log_timestamp",
        full_a.log_timestamp == full_b.log_timestamp == started.isoformat(),
    )
    check(
        "late joiner has no grace window (loses 2s)",
        late_user.elapsed_seconds == 298,
        str(late_user.elapsed_seconds),
    )
    check(
        "late joiner timestamp starts at join time",
        late_user.log_timestamp == (started + timedelta(seconds=2.0)).isoformat(),
        late_user.log_timestamp,
    )
    check(
        "5s disconnect is NOT ignored",
        flaky_user.elapsed_seconds == 295,
        str(flaky_user.elapsed_seconds),
    )
    check(
        "early leaver stops accruing at leave time",
        early_leaver.elapsed_seconds == 60,
        str(early_leaver.elapsed_seconds),
    )
    check(
        "short 2s attendance is preserved (no minimum)",
        brief_user.elapsed_seconds == 2,
        str(brief_user.elapsed_seconds),
    )
    mid = meeting_id_for_dir(Path("/tmp/meetings/2026-W47/20261122T170000Z"))
    check("meeting_id_for_dir keeps week/stamp", mid == "2026-W47/20261122T170000Z", mid)


def test_facts_exclusion() -> None:
    print("summary facts exclusion")
    from meeting_host import mongo_host

    queries: list[dict] = []
    week_start = datetime(2026, 11, 16, 0, 0, 0)
    raw_logs = [
        {"user": "Johan", "timestamp": datetime(2026, 11, 17, 10, 0, 0), "elapsed_time": 3600, "name": "Work"},
        {
            "user": "Johan",
            "timestamp": datetime(2026, 11, 16, 18, 0, 0),
            "elapsed_time": 1800,
            "name": "Weekly Status Meeting",
            "meeting_id": "2026-W46/prev",
        },
        {
            "user": "Johan",
            "timestamp": datetime(2026, 11, 22, 18, 0, 0),
            "elapsed_time": 2400,
            "name": "Weekly Status Meeting",
            "meeting_id": "2026-W47/curr",
        },
    ]

    class FakeTimetable:
        def find(self, query, projection=None):
            queries.append(query)
            excluded = None
            mid_filter = query.get("meeting_id")
            if isinstance(mid_filter, dict):
                excluded = mid_filter.get("$ne")
            return [row for row in raw_logs if excluded is None or row.get("meeting_id") != excluded]

    class FakeStatus:
        def find_one(self, flt, projection=None):
            if flt.get("_id") == "State":
                return {"week": week_start}
            return {}

    orig_tt = mongo_host.timetable
    orig_st = mongo_host.status_meeting
    try:
        mongo_host.timetable = lambda: FakeTimetable()  # type: ignore[assignment]
        mongo_host.status_meeting = lambda: FakeStatus()  # type: ignore[assignment]

        facts = facts_module.load_facts(exclude_meeting_id="2026-W47/curr")
        check("query filters out current meeting_id", queries[-1].get("meeting_id") == {"$ne": "2026-W47/curr"})
        check("current meeting log excluded from facts", len(facts.logs) == 2, str(facts.logs))
        johan_goal = next(g for g in facts.goals if g.username == "Johan")
        check(
            "previous week's meeting log stays counted (1.5h total, not 2.17h)",
            johan_goal.actual_hours == 1.5,
            str(johan_goal.actual_hours),
        )

        facts_all = facts_module.load_facts()
        check("without exclude_meeting_id query has no meeting_id filter", "meeting_id" not in queries[-1])
        check("without exclude_meeting_id all logs included", len(facts_all.logs) == 3)
    finally:
        mongo_host.timetable = orig_tt
        mongo_host.status_meeting = orig_st


def test_meeting_log_commit() -> None:
    print("meeting log commit & notifications")
    started = datetime(2026, 11, 22, 17, 0, 0, tzinfo=timezone.utc)
    p1 = Participant(discord_user_id="1", elg_username="Johan", sessions=[(0.0, None)])
    p2 = Participant(discord_user_id="2", elg_username="Ada", joined_late=True, sessions=[(30.0, None)])
    p3 = Participant(discord_user_id="3", elg_username=None, discord_nick="Guest", sessions=[(0.0, None)])
    dur, ended = finalize_attendance([p1, p2, p3], started_at=started, total_duration_seconds=600.0)
    payload = build_attendance_payload(
        meeting_id="2026-W47/20261122T170000Z",
        started_at=started,
        ended_at=ended,
        duration_seconds=dur,
        channel_id="99",
        channel_name="boardroom",
        host_elg_user="Johan",
        host_discord_user_id="1",
        participants=[p1, p2, p3],
    )

    entries, skipped = plan_meeting_logs(payload)
    check("plans 2 ELG attendees", len(entries) == 2, str(entries))
    check("unmapped guest skipped", skipped == ["Guest"], str(skipped))
    by_user = {e.user: e for e in entries}
    check("consistent meeting name", by_user["Johan"].name == MEETING_LOG_NAME)
    check("consistent week description", by_user["Johan"].description == "Week 47, 2026", by_user["Johan"].description)
    check("Johan full duration", by_user["Johan"].elapsed_time == 600)
    check("Ada late duration", by_user["Ada"].elapsed_time == 570)
    check("log timestamps are UTC naive", by_user["Johan"].log_ts.tzinfo is None)

    committed_docs: list[dict] = []
    persisted_updates: list[dict] = []
    notifications: list[tuple[str, str, str]] = []
    log_lines: list[str] = []

    class FakeCollection:
        def find_one(self, query, projection=None):
            for doc in committed_docs:
                if doc.get("meeting_id") == query.get("meeting_id") and doc.get("user") == query.get("user"):
                    return {"_id": "exists"}
            return None

    class FakeStatus:
        def update_one(self, flt, update, upsert=False):
            persisted_updates.append({"filter": flt, "update": update, "upsert": upsert})

    def fake_commit(collection, aggregations, client, *, name, user, description, log_ts, elapsed_time, extra_fields=None):
        doc = {
            "name": name,
            "user": user,
            "description": description,
            "log_ts": log_ts,
            "elapsed_time": elapsed_time,
            **(extra_fields or {}),
        }
        committed_docs.append(doc)
        # Simulate both users breaking the same combined record, plus Ada breaking a personal record.
        breaks = [
            {
                "old_record": {"scope": "combined", "time_type": "Week", "metric": "total_time", "value": {"total_time": 100 if user == "Ada" else 670}},
                "new_record": {"scope": "combined", "time_type": "Week", "metric": "total_time", "value": {"total_time": 670 if user == "Ada" else 1270}},
            }
        ]
        if user == "Ada":
            breaks.append(
                {
                    "old_record": {"scope": "personal", "time_type": "Day", "metric": "total_time", "value": {"total_time": 0}},
                    "new_record": {"scope": "personal", "time_type": "Day", "metric": "total_time", "value": {"total_time": 570}},
                }
            )
        return log_ts, breaks

    def fake_format(actor, global_r, personal_r, combined_r, ts):
        return f"actor={actor} g={len(global_r)} p={len(personal_r)} c={len(combined_r)} comb_vals={[(r['old_record']['value']['total_time'], r['new_record']['value']['total_time']) for r in combined_r]}"

    def fake_notify(kind, actor, message, **_kwargs):
        notifications.append((kind, actor, message))

    res = commit_meeting_logs(
        payload,
        collection=FakeCollection(),
        aggregations=None,
        client=None,
        status_meeting=FakeStatus(),
        commit_fn=fake_commit,
        notify_fn=fake_notify,
        format_broken_fn=fake_format,
        on_log=log_lines.append,
    )

    check("committed both ELG users", res.committed == ["Ada", "Johan"], str(res.committed))
    check("meeting_id tagged on docs", all(d.get("meeting_id") == "2026-W47/20261122T170000Z" for d in committed_docs))
    check("broken records persisted for each user", len(persisted_updates) == 2, str(len(persisted_updates)))
    check("no session_end notifications sent", all(kind == "records_broken" for kind, _, _ in notifications))
    check(
        "personal notification sent once + combined coalesced into one notification",
        len(notifications) == 2,
        str(notifications),
    )
    combined_note = notifications[-1][2]
    check(
        "coalesced combined notification keeps original old_value and final new_value",
        "[(100, 1270)]" in combined_note,
        combined_note,
    )
    check("log lines emitted for host window", any("Logged meeting" in line for line in log_lines), str(log_lines))

    # Idempotency: second run skips already-committed users
    res2 = commit_meeting_logs(
        payload,
        collection=FakeCollection(),
        aggregations=None,
        client=None,
        status_meeting=FakeStatus(),
        commit_fn=fake_commit,
        notify_fn=fake_notify,
        format_broken_fn=fake_format,
    )
    check("second commit run is idempotent", res2.committed == [] and res2.already_logged == ["Ada", "Johan"])


def test_client_stopped_triggers_commit() -> None:
    print("MeetingHostSession stopped -> commit hook")
    seen = threading.Event()
    received: list[dict] = []

    def fake_committer(attendance, *, on_log=None):
        received.append(attendance)
        if on_log:
            on_log("Logged meeting for Johan (10:00)")
        seen.set()

    session = mike_client.MeetingHostSession(log_committer=fake_committer)
    session._apply_event({"event": "stopped", "attendance": {"meeting_id": "m1", "participants": []}})
    check("stopped with attendance triggers committer", seen.wait(5.0))
    check("attendance payload passed through", received == [{"meeting_id": "m1", "participants": []}])
    check("commit log appended to session log", any("Logged meeting" in line for line in session.snapshot()["log"]))


def test_meeting_lock_watcher() -> None:
    print("MeetingLockWatcher state & expiry")
    now = datetime(2026, 11, 22, 18, 0, 0, tzinfo=timezone.utc)
    fresh_doc = {"recording": True, "started_at": now - timedelta(minutes=45), "heartbeat_at": now - timedelta(seconds=20)}
    stale_hb = {"recording": True, "started_at": now - timedelta(minutes=45), "heartbeat_at": now - timedelta(seconds=LOCK_STALE_SECONDS + 5)}
    legacy_fresh = {"recording": True, "started_at": now - timedelta(seconds=30)}
    released = {"recording": False, "started_at": now, "heartbeat_at": now}

    check("fresh heartbeat is active even after 45m meeting", is_lock_doc_active(fresh_doc, now_utc=now))
    check("stale heartbeat is inactive", not is_lock_doc_active(stale_hb, now_utc=now))
    check("legacy started_at fallback works", is_lock_doc_active(legacy_fresh, now_utc=now))
    check("recording=False is inactive", not is_lock_doc_active(released, now_utc=now))
    check("None doc is inactive", not is_lock_doc_active(None, now_utc=now))

    watcher = MeetingLockWatcher(None, stale_seconds=60)
    changes: list[bool] = []
    watcher.add_listener(changes.append)

    t0 = datetime.now(timezone.utc)
    watcher._apply_doc({"recording": True, "heartbeat_at": t0}, server_time=t0)
    check("watcher flips active on live doc", watcher.is_active and changes == [True], str(changes))

    # Simulate crashed host: heartbeat becomes older than stale_seconds without a DB write
    watcher._last_doc = {"recording": True, "heartbeat_at": t0 - timedelta(seconds=120)}
    watcher._reevaluate_expiry()
    check("watcher auto-unlocks when heartbeat expires", (not watcher.is_active) and changes == [True, False], str(changes))


def test_minimize_from_meeting_logic() -> None:
    print("AppShell minimize-from-meeting logic")
    import types

    stubbed_names = (
        "customtkinter",
        "meeting",
        "meeting_point_manager",
        "session_guard",
        "settings_frame",
        "shutdown_block",
        "stats_viewer",
        "system_tray",
        "Timetable",
        "timetable_db",
        "title_bar_pin",
        "window_chrome",
        "window_geometry",
    )
    saved = {name: sys.modules.get(name) for name in stubbed_names}
    try:
        ctk_mod = types.ModuleType("customtkinter")
        ctk_mod.CTk = object
        ctk_mod.CTkFrame = object
        ctk_mod.CTkButton = object
        sys.modules["customtkinter"] = ctk_mod

        for mod_name, attrs in (
            ("meeting", {"MeetingFrame": object}),
            ("meeting_point_manager", {"MeetingPointManagerFrame": object}),
            (
                "session_guard",
                {
                    "has_unlogged_time": lambda *a, **k: False,
                    "prompt_unlogged_action_blocked": lambda *a, **k: None,
                    "prompt_unlogged_exit": lambda *a, **k: "cancel",
                },
            ),
            ("settings_frame", {"SettingsFrame": object}),
            ("shutdown_block", {"ShutdownBlocker": object}),
            ("stats_viewer", {"StatsFrame": object}),
            ("system_tray", {"SystemTray": object}),
            ("Timetable", {"TimetableFrame": object}),
            ("timetable_db", {"status_meeting": None}),
            (
                "title_bar_pin",
                {
                    "FLUENT_ICON_FONT": ("Arial", 10),
                    "TitleBarButtonOverlay": object,
                    "WIDGET_ENTER_GLYPH": "",
                },
            ),
            (
                "window_chrome",
                {
                    "CAPTION_ICON_PATH": "",
                    "apply_app_title_bar_chrome": lambda *a, **k: None,
                    "apply_widget_chrome": lambda *a, **k: None,
                    "apply_widget_title_bar_chrome": lambda *a, **k: None,
                    "hide_title_bar_icon": lambda *a, **k: None,
                    "warm_widget_title_bar_assets": lambda *a, **k: None,
                },
            ),
            (
                "window_geometry",
                {
                    "capture_window_state": lambda *a, **k: None,
                    "resolve_saved_position": lambda *a, **k: None,
                    "safe_top_left_geometry": lambda *a, **k: "300x170+0+0",
                },
            ),
        ):
            mod = types.ModuleType(mod_name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[mod_name] = mod

        sys.modules.pop("app_shell", None)
        from app_shell import AppShell

        switched: list[tuple[str, bool]] = []
        restored_geom: list[bool] = []

        class FakeWindow:
            def __init__(self) -> None:
                self._state = "normal"
                self.withdrawn = False

            def state(self):
                return self._state

            def withdraw(self):
                self.withdrawn = True

        class FakeTray:
            def __init__(self) -> None:
                self.started = False

            def ensure_started(self):
                self.started = True

        win = FakeWindow()
        shell = SimpleNamespace(
            _window=win,
            _tray=FakeTray(),
            _active_view="meeting",
            _pending_geometry_on_restore=False,
            switch_view=lambda name, update_geometry=True: (
                setattr(shell, "_active_view", name),
                switched.append((name, update_geometry)),
            ),
            _apply_window_geometry=lambda: restored_geom.append(True),
            _sync_title_bar_chrome=lambda: None,
        )
        shell._leave_meeting_on_minimize = lambda *, update_geometry: AppShell._leave_meeting_on_minimize(
            shell, update_geometry=update_geometry
        )

        # 1. Taskbar/dock minimize while in meeting
        win._state = "iconic"
        AppShell._on_window_unmap(shell, SimpleNamespace(widget=win))
        check("iconic unmap leaves meeting -> timetable", shell._active_view == "timetable")
        check("iconic unmap defers geometry until restore", switched == [("timetable", False)] and shell._pending_geometry_on_restore)

        # Restore from iconic applies geometry
        win._state = "normal"
        AppShell._on_window_map(shell, SimpleNamespace(widget=win))
        check("map restores timetable geometry", restored_geom == [True] and not shell._pending_geometry_on_restore)

        # 2. Minimize to tray while in meeting
        switched.clear()
        shell._active_view = "meeting"
        AppShell._minimize_to_tray(shell)
        check("minimize to tray leaves meeting -> timetable", shell._active_view == "timetable")
        check("minimize to tray updates geometry before withdraw", switched == [("timetable", True)] and win.withdrawn)

        # 3. Minimize while NOT in meeting (e.g. statistics) stays on statistics
        switched.clear()
        shell._active_view = "statistics"
        win._state = "iconic"
        AppShell._on_window_unmap(shell, SimpleNamespace(widget=win))
        check("minimizing from statistics does not switch views", shell._active_view == "statistics" and switched == [])
    finally:
        sys.modules.pop("app_shell", None)
        for name, orig in saved.items():
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig


def test_timetable_lock_state_machine() -> None:
    print("TimetableFrame meeting lock behavior")
    import types

    stubbed_names = (
        "customtkinter",
        "commit_transaction",
        "CTkStickyPlaceholderEntry",
        "mongo_doc_lookup",
        "notifications",
        "settings_ui_constants",
        "timetable_db",
        "utils",
    )
    saved = {name: sys.modules.get(name) for name in stubbed_names}
    try:
        ctk_mod = types.ModuleType("customtkinter")
        ctk_mod.CTkFrame = object
        sys.modules["customtkinter"] = ctk_mod

        for mod_name, attrs in (
            ("commit_transaction", {"CommitTransactionManager": object, "TXN_REFRESH_INTERVAL_MS": 1000}),
            ("CTkStickyPlaceholderEntry", {"CTkStickyPlaceholderEntry": object}),
            ("mongo_doc_lookup", {"week_bucket_from_agg": lambda *a, **k: {}, "week_goals": lambda *a, **k: {}}),
            (
                "notifications",
                {
                    "create_broken_records_notification": lambda *a, **k: "",
                    "fetch_week_goal_context_at_start": lambda *a, **k: {},
                    "format_end_message": lambda *a, **k: "",
                    "format_start_message": lambda *a, **k: "",
                    "post_notification": lambda *a, **k: None,
                },
            ),
            ("settings_ui_constants", {"ACCENT": "#fff"}),
            (
                "timetable_db",
                {
                    "aggregations": None,
                    "client": None,
                    "collection": None,
                    "db": None,
                    "get_user": lambda: "Johan",
                    "status_meeting": None,
                },
            ),
            ("utils", {"flash_error": lambda *a, **k: None}),
        ):
            mod = types.ModuleType(mod_name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[mod_name] = mod

        sys.modules.pop("Timetable", None)
        from Timetable import TimetableFrame

        class FakeWidget:
            def __init__(self, state: str = "normal", text: str = "") -> None:
                self.props = {"state": state, "text": text}
                self.mapped = False
                self.focused = False

            def configure(self, **kwargs):
                self.props.update(kwargs)

            def cget(self, key: str):
                return self.props.get(key)

            def grid(self, **_kwargs):
                self.mapped = True

            def grid_configure(self, **_kwargs):
                pass

            def grid_forget(self):
                self.mapped = False

            def winfo_ismapped(self) -> bool:
                return self.mapped

            def focus_set(self):
                self.focused = True

            def delete(self, *_args):
                pass

            def _activate_placeholder(self):
                pass

        tt = object.__new__(TimetableFrame)
        tt._shell = None
        tt.local_start = None
        tt.log_ts = datetime(2026, 11, 22, 16, 0, 0)
        tt.elapsed_time = 0.0
        tt._monotonic_anchor = time.perf_counter() - 12.0
        tt.running = True
        tt._meeting_locked = False
        tt._commit_plan = None
        tt._plan_refresh_job = None
        tt._prepare_retry_job = None
        tt._start_notified = True
        tt._focus_highlight_defaults = {}
        tt._commit_txn = SimpleNamespace(abort_async=lambda: None)
        tt.toggle_run_button = FakeWidget(state="normal", text="Pause")
        tt.toggle_run_button.mapped = True
        tt.done_button = FakeWidget(state="normal", text="Done")
        tt.continue_button = FakeWidget(state="normal", text="Continue")
        tt.log_button = FakeWidget(state="normal", text="Log Entry")
        tt.name_entry = FakeWidget()
        tt.desc_entry = FakeWidget()
        tt.time_label = FakeWidget(text="00:12")
        tt.overlay_canvas = FakeWidget()
        tt.focus_get = lambda: tt.toggle_run_button

        # 1. Meeting starts while timer is running -> auto-pauses, disables Continue, keeps Done enabled
        tt.set_meeting_locked(True)
        check("running timer auto-paused on meeting lock", not tt.running and tt.elapsed_time >= 11.0)
        check("toggle button switched to Continue and disabled", tt.toggle_run_button.cget("text") == "Continue" and tt.toggle_run_button.cget("state") == "disabled")
        check("overlay Continue button disabled", tt.continue_button.cget("state") == "disabled")
        check("Done button is shown and still enabled", tt.done_button.winfo_ismapped() and tt.done_button.cget("state") == "normal")

        # 2. Attempting to continue via toggle_button or continue_timer is blocked
        tt.toggle_button()
        check("toggle_button cannot resume while meeting locked", not tt.running)
        tt.continue_timer()
        check("continue_timer cannot resume while meeting locked", not tt.running)

        # 3. After discarding/committing while meeting is still running, Start stays disabled
        tt.discard_session()
        check("after discard while locked, Start button stays disabled", tt.toggle_run_button.cget("text") == "Start" and tt.toggle_run_button.cget("state") == "disabled")
        tt.toggle_button()
        check("cannot start new timer while meeting locked", not tt.running)

        # 4. Meeting ends -> Start & Continue re-enabled (does not auto-start)
        tt.set_meeting_locked(False)
        check("unlock re-enables Start and Continue without auto-starting", tt.toggle_run_button.cget("state") == "normal" and tt.continue_button.cget("state") == "normal" and not tt.running)
    finally:
        sys.modules.pop("Timetable", None)
        for name, orig in saved.items():
            if orig is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig


def main() -> int:
    test_attendance_intervals()
    test_facts_exclusion()
    test_meeting_log_commit()
    test_client_stopped_triggers_commit()
    test_meeting_lock_watcher()
    test_minimize_from_meeting_logic()
    test_timetable_lock_state_machine()

    print()
    if _failures:
        print(f"FAILED {len(_failures)}/{_checks}:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"All {_checks} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
