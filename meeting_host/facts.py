"""Meeting context pulled straight from Mongo.

Goal outcomes are computed here, never inferred by the language model. The
rules mirror src/stats_viewer.py classify_user_goal_week, and the actual
hours/days come from the same weekly logs the meeting slides display
(src/meeting.py s1_build_all_charts_data), so the summary cannot disagree
with what the team saw on screen.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from meeting_host.period import iso_week_of, to_local, week_bounds_utc_naive


def _doc_lookup(doc: dict, key: str | int):
    """MongoDB subdocuments may use str or int keys (src/mongo_doc_lookup.py)."""
    if not isinstance(doc, dict):
        return None
    candidates: list[str | int] = [key]
    if isinstance(key, str) and key.isdigit():
        ik = int(key)
        candidates.extend([ik, key.zfill(2)])
    elif isinstance(key, int):
        candidates.extend([str(key), str(key).zfill(2)])
    seen: set[str | int] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate in doc:
            return doc[candidate]
    return None


def week_goals(goals_doc: dict, iso_year: str, iso_week: str) -> dict:
    year_bucket = _doc_lookup(goals_doc, iso_year) or {}
    result = _doc_lookup(year_bucket, iso_week)
    return result if isinstance(result, dict) else {}


def classify_goal(goal_hours, goal_days, actual_hours: float, actual_days: int) -> str:
    """'none' | 'reached' | 'missed' (src/stats_viewer.classify_user_goal_week)."""
    if not goal_hours and not goal_days:
        return "none"

    hours_ok = not goal_hours or actual_hours >= float(goal_hours)
    days_ok = not goal_days or actual_days >= int(goal_days)

    if goal_hours and goal_days:
        reached = hours_ok and days_ok
    elif goal_hours:
        reached = hours_ok
    else:
        reached = days_ok
    return "reached" if reached else "missed"


@dataclass
class UserGoalFact:
    username: str
    goal_hours: float | None
    goal_days: int | None
    actual_hours: float
    actual_days: int
    status: str


@dataclass
class MeetingFacts:
    iso_year: str
    iso_week: str
    next_iso_year: str
    next_iso_week: str
    goals: list[UserGoalFact] = field(default_factory=list)
    next_goals: dict[str, dict] = field(default_factory=dict)
    discussion_points: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)


def _fetch_week_logs(week_start) -> list[dict]:
    from meeting_host import mongo_host

    start, end = week_bounds_utc_naive(week_start)
    cursor = mongo_host.timetable().find(
        {"timestamp": {"$gte": start, "$lt": end}, "user": {"$nin": [None, ""]}},
        projection={"user": 1, "timestamp": 1, "elapsed_time": 1, "name": 1, "description": 1},
    )
    return list(cursor)


def load_facts() -> MeetingFacts:
    from meeting_host import mongo_host

    status = mongo_host.status_meeting()

    state = status.find_one({"_id": "State"}, projection={"week": 1}) or {}
    week_start = state.get("week")
    if week_start is None:
        from meeting_host.period import utc_now

        week_start = utc_now()

    iso_year, iso_week = iso_week_of(week_start)
    from datetime import timedelta

    next_week_anchor = to_local(week_start) + timedelta(days=7)
    next_iso_year, next_iso_week = iso_week_of(next_week_anchor)

    goals_doc = status.find_one({"_id": "Goals"}) or {}
    current_goals = week_goals(goals_doc, iso_year, iso_week)
    next_goals = week_goals(goals_doc, next_iso_year, next_iso_week)

    # Discussion Points stores a list under {year}.{week}
    points_doc = status.find_one({"_id": "Discussion Points"}) or {}
    points = _doc_lookup(_doc_lookup(points_doc, iso_year) or {}, iso_week)
    if not isinstance(points, list):
        points = []
    discussion_points = [
        {
            "title": str(point.get("title") or "").strip(),
            "description": str(point.get("description") or "").strip(),
        }
        for point in (points or [])
        if isinstance(point, dict) and str(point.get("title") or "").strip()
    ]

    logs = _fetch_week_logs(week_start)

    user_seconds: dict[str, int] = defaultdict(int)
    user_days: dict[str, set] = defaultdict(set)
    for log in logs:
        username = log.get("user")
        if not username:
            continue
        seconds = int(log.get("elapsed_time") or 0)
        user_seconds[username] += seconds
        timestamp = log.get("timestamp")
        if timestamp is not None and seconds > 0:
            user_days[username].add(to_local(timestamp).date())

    facts: list[UserGoalFact] = []
    for username in sorted(set(user_seconds) | set(current_goals)):
        goal = current_goals.get(username) or {}
        goal_hours = goal.get("hours") or None
        goal_days = goal.get("days") or None
        actual_hours = user_seconds.get(username, 0) / 3600
        actual_days = len(user_days.get(username, ()))
        facts.append(
            UserGoalFact(
                username=username,
                goal_hours=float(goal_hours) if goal_hours else None,
                goal_days=int(goal_days) if goal_days else None,
                actual_hours=round(actual_hours, 2),
                actual_days=actual_days,
                status=classify_goal(goal_hours, goal_days, actual_hours, actual_days),
            )
        )

    log_rows = [
        {
            "user": log.get("user"),
            "name": str(log.get("name") or "").strip(),
            "seconds": int(log.get("elapsed_time") or 0),
            "date": to_local(log["timestamp"]).strftime("%a %d %b") if log.get("timestamp") else "",
        }
        for log in logs
        if log.get("user")
    ]
    log_rows.sort(key=lambda row: (row["user"] or "", row["date"]))

    return MeetingFacts(
        iso_year=iso_year,
        iso_week=iso_week,
        next_iso_year=next_iso_year,
        next_iso_week=next_iso_week,
        goals=facts,
        next_goals=next_goals if isinstance(next_goals, dict) else {},
        discussion_points=discussion_points,
        logs=log_rows,
    )


def load_facts_safe() -> MeetingFacts:
    """Facts, or an empty shell if Mongo is unreachable."""
    try:
        return load_facts()
    except Exception:
        from meeting_host.period import utc_now

        iso_year, iso_week = iso_week_of(utc_now())
        return MeetingFacts(
            iso_year=iso_year,
            iso_week=iso_week,
            next_iso_year=iso_year,
            next_iso_week=str(int(iso_week) + 1),
        )
