"""Meeting attendance interval tracking and log-duration computation.

Kept free of Discord/NumPy/PyMongo imports so both MeetingRecorder and
offline tests can use it directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class Participant:
    discord_user_id: str
    discord_username: str = ""
    discord_global_name: str = ""
    discord_nick: str = ""
    elg_username: str | None = None
    joined_at: str = ""
    left_at: str | None = None
    joined_late: bool = False
    audio_path: str = ""
    sessions: list[tuple[float, float | None]] = field(default_factory=list)
    elapsed_seconds: int = 0
    log_timestamp: str = ""

    def to_json(self) -> dict:
        return {
            "discord_user_id": self.discord_user_id,
            "discord_username": self.discord_username,
            "discord_global_name": self.discord_global_name,
            "discord_nick": self.discord_nick,
            "elg_username": self.elg_username,
            "joined_at": self.joined_at,
            "left_at": self.left_at,
            "joined_late": self.joined_late,
            "audio_path": self.audio_path,
            "sessions": [
                [round(float(start), 3), None if end is None else round(float(end), 3)]
                for start, end in self.sessions
            ],
            "elapsed_seconds": int(self.elapsed_seconds),
            "log_timestamp": self.log_timestamp,
        }


def meeting_id_for_dir(meeting_dir: Path | None) -> str:
    """Stable identifier for one meeting folder, e.g. '2026-W47/20261122T173500Z'."""
    if meeting_dir is None:
        return ""
    parent_name = meeting_dir.parent.name if meeting_dir.parent else ""
    if parent_name and parent_name != meeting_dir.name:
        return f"{parent_name}/{meeting_dir.name}"
    return meeting_dir.name


def open_session(participant: Participant, offset_seconds: float) -> None:
    """Start a new presence interval unless one is already open."""
    offset = max(0.0, float(offset_seconds))
    if not participant.sessions or participant.sessions[-1][1] is not None:
        participant.sessions.append((offset, None))


def close_session(participant: Participant, offset_seconds: float) -> None:
    """Close the currently open presence interval at offset_seconds."""
    if not participant.sessions:
        return
    start, end = participant.sessions[-1]
    if end is None:
        offset = max(start, float(offset_seconds))
        participant.sessions[-1] = (start, offset)


def finalize_attendance(
    participants: list[Participant],
    *,
    started_at: datetime,
    total_duration_seconds: float,
) -> tuple[int, datetime]:
    """Close open sessions and compute exact elapsed_seconds & log_timestamp.

    - Anyone present from start (offset 0.0) without leaving gets the exact
      same `total_seconds` and `started_at`.
    - Anyone joining late or disconnecting/rejoining gets the exact sum of
      their connected intervals (no grace window, no ignored disconnects, no
      minimum threshold) and `started_at + first_join_offset`.
    """
    duration = max(0.0, float(total_duration_seconds))
    total_seconds = int(round(duration))
    ended_at = started_at + timedelta(seconds=duration)

    for participant in participants:
        close_session(participant, duration)
        if not participant.sessions:
            if participant.joined_late:
                participant.sessions = [(duration, duration)]
            else:
                participant.sessions = [(0.0, duration)]

        is_full_attendee = (
            not participant.joined_late
            and len(participant.sessions) == 1
            and participant.sessions[0][0] == 0.0
            and participant.sessions[0][1] == duration
        )
        if is_full_attendee:
            participant.elapsed_seconds = total_seconds
            participant.log_timestamp = started_at.isoformat()
        else:
            attended = sum(
                max(0.0, (end if end is not None else duration) - start)
                for start, end in participant.sessions
            )
            participant.elapsed_seconds = int(round(attended))
            first_offset = participant.sessions[0][0]
            participant.log_timestamp = (started_at + timedelta(seconds=first_offset)).isoformat()

        if participant.left_at is None:
            participant.left_at = ended_at.isoformat()

    return total_seconds, ended_at


def build_attendance_payload(
    *,
    meeting_id: str,
    started_at: datetime,
    ended_at: datetime,
    duration_seconds: int,
    channel_id: str,
    channel_name: str,
    host_elg_user: str,
    host_discord_user_id: str,
    participants: list[Participant],
) -> dict:
    return {
        "meeting_id": meeting_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": int(duration_seconds),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "host_elg_user": host_elg_user,
        "host_discord_user_id": host_discord_user_id,
        "participants": [p.to_json() for p in participants],
    }
