"""Commit per-attendee Timetable logs when a weekly status meeting ends.

Runs on the Host's ELG app right after Meeting Mike stops recording so every
resolved attendee gets an atomic Timetable + Aggregations + Highscores commit
via the existing commit_log transaction path, without duplicating commit logic
into meeting_host.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from format_time import format_time
from period_model import as_utc, to_local
from records_persist import persist_broken_records

MEETING_LOG_NAME = "Weekly Status Meeting"


@dataclass(frozen=True)
class MeetingLogEntry:
    user: str
    name: str
    description: str
    log_ts: datetime
    elapsed_time: int
    meeting_id: str


@dataclass
class MeetingCommitResult:
    committed: list[str] = field(default_factory=list)
    already_logged: list[str] = field(default_factory=list)
    skipped_unmapped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def _parse_utc_naive(raw: str | datetime | None) -> datetime | None:
    if isinstance(raw, datetime):
        return as_utc(raw).replace(tzinfo=None)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return as_utc(dt).replace(tzinfo=None)


def format_meeting_description(started_at: datetime) -> str:
    """Consistent description format for meeting logs, e.g. 'Week 47, 2026'."""
    local = to_local(started_at)
    iso_year, iso_week, _ = local.isocalendar()
    return f"Week {iso_week}, {iso_year}"


def plan_meeting_logs(attendance: dict) -> tuple[list[MeetingLogEntry], list[str]]:
    """Convert an attendance payload into one MeetingLogEntry per ELG attendee.

    Returns (entries, skipped_unmapped_labels).
    No minimum duration threshold and no grace/disconnect overrides are applied
    here — exact values computed by meeting_host.attendance are preserved.
    """
    if not isinstance(attendance, dict):
        return [], []

    meeting_id = str(attendance.get("meeting_id") or "").strip()
    started_at = _parse_utc_naive(attendance.get("started_at")) or datetime.now(timezone.utc).replace(tzinfo=None)
    default_elapsed = max(0, int(attendance.get("duration_seconds") or 0))
    description = format_meeting_description(started_at)

    entries: list[MeetingLogEntry] = []
    skipped: list[str] = []
    seen_users: set[str] = set()

    for participant in attendance.get("participants") or []:
        if not isinstance(participant, dict):
            continue
        elg_user = str(participant.get("elg_username") or "").strip()
        if not elg_user:
            label = str(
                participant.get("discord_nick")
                or participant.get("discord_global_name")
                or participant.get("discord_username")
                or participant.get("discord_user_id")
                or "unknown"
            )
            skipped.append(label)
            continue
        if elg_user in seen_users:
            continue
        seen_users.add(elg_user)

        raw_elapsed = participant.get("elapsed_seconds")
        elapsed = default_elapsed if raw_elapsed is None else max(0, int(raw_elapsed))
        log_ts = (
            _parse_utc_naive(participant.get("log_timestamp"))
            or _parse_utc_naive(participant.get("joined_at"))
            or started_at
        )

        entries.append(
            MeetingLogEntry(
                user=elg_user,
                name=MEETING_LOG_NAME,
                description=description,
                log_ts=log_ts,
                elapsed_time=elapsed,
                meeting_id=meeting_id,
            )
        )

    entries.sort(key=lambda entry: entry.user.lower())
    return entries, skipped


def commit_meeting_logs(
    attendance: dict,
    *,
    collection,
    aggregations,
    client,
    status_meeting,
    commit_fn=None,
    notify_fn=None,
    format_broken_fn=None,
    on_log: Callable[[str], None] | None = None,
) -> MeetingCommitResult:
    """Commit one Timetable log per resolved meeting attendee sequentially.

    - Updates Timetable, Timetable Aggregations, and Highscores atomically via
      commit_log, tagging each doc with `meeting_id` (when present).
    - Skips session_end Discord notifications.
    - Persists broken records to Status Meeting > Records and sends
      records_broken notifications (coalescing combined breaks across the batch).
    """
    if commit_fn is None:
        from commit_transaction import commit_log as commit_fn
    if notify_fn is None or format_broken_fn is None:
        from notifications import create_broken_records_notification, post_notification

        if notify_fn is None:
            notify_fn = post_notification
        if format_broken_fn is None:
            format_broken_fn = create_broken_records_notification

    entries, skipped = plan_meeting_logs(attendance)
    result = MeetingCommitResult(skipped_unmapped=list(skipped))

    combined_breaks: dict[tuple[str, str], dict] = {}
    combined_break_actor = ""
    combined_break_ts: datetime | None = None

    for entry in entries:
        if entry.meeting_id:
            try:
                existing = collection.find_one(
                    {"meeting_id": entry.meeting_id, "user": entry.user},
                    projection={"_id": 1},
                )
            except Exception:
                existing = None
            if existing is not None:
                result.already_logged.append(entry.user)
                continue

        extra = {"meeting_id": entry.meeting_id} if entry.meeting_id else None
        try:
            ts, broken_records = commit_fn(
                collection,
                aggregations,
                client,
                name=entry.name,
                user=entry.user,
                description=entry.description,
                log_ts=entry.log_ts,
                elapsed_time=entry.elapsed_time,
                extra_fields=extra,
            )
        except Exception as exc:
            result.failed[entry.user] = str(exc)
            continue

        result.committed.append(entry.user)

        if broken_records:
            persist_broken_records(status_meeting, entry.user, broken_records, ts)

            global_records = [r for r in broken_records if r["old_record"]["scope"] == "global"]
            personal_records = [r for r in broken_records if r["old_record"]["scope"] == "personal"]
            combined_records = [r for r in broken_records if r["old_record"]["scope"] == "combined"]

            if global_records or personal_records:
                try:
                    msg = format_broken_fn(entry.user, global_records, personal_records, [], ts)
                    notify_fn("records_broken", entry.user, msg)
                except Exception as exc:
                    print(f"Failed to send meeting broken-records notification for {entry.user}: {exc}")

            for rec in combined_records:
                slot = (rec["old_record"]["time_type"], rec["old_record"]["metric"])
                if slot not in combined_breaks:
                    combined_breaks[slot] = {
                        "old_record": rec["old_record"],
                        "new_record": rec["new_record"],
                    }
                else:
                    combined_breaks[slot]["new_record"] = rec["new_record"]
                combined_break_actor = entry.user
                combined_break_ts = ts

    if combined_breaks and combined_break_ts is not None:
        try:
            msg = format_broken_fn(
                combined_break_actor,
                [],
                [],
                list(combined_breaks.values()),
                combined_break_ts,
            )
            notify_fn("records_broken", combined_break_actor, msg)
        except Exception as exc:
            print(f"Failed to send combined meeting broken-records notification: {exc}")

    if on_log is not None:
        if result.committed:
            durations = {e.user: e.elapsed_time for e in entries}
            unique_durations = {durations[u] for u in result.committed if u in durations}
            if len(unique_durations) == 1:
                dur_text = f" ({format_time(next(iter(unique_durations)))})"
            else:
                dur_text = ""
            on_log(f"Logged meeting for {', '.join(result.committed)}{dur_text}")
        if result.skipped_unmapped:
            on_log(f"Skipped unmapped Discord user(s): {', '.join(result.skipped_unmapped)}")
        for user, err in result.failed.items():
            on_log(f"Failed to log meeting for {user}: {err}")

    return result


def commit_meeting_logs_default(
    attendance: dict,
    *,
    on_log: Callable[[str], None] | None = None,
) -> MeetingCommitResult:
    """Commit meeting logs using the app's shared MongoDB connection."""
    from timetable_db import aggregations, client, collection, status_meeting

    return commit_meeting_logs(
        attendance,
        collection=collection,
        aggregations=aggregations,
        client=client,
        status_meeting=status_meeting,
        on_log=on_log,
    )
