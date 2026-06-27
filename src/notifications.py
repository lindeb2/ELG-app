"""Discord notification helpers for ELG Timetable events."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from typing import Any

import requests

from period_model import add_calendar_days, as_utc, to_local
from timetable_db import aggregations, collection, status_meeting

_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
_config_path = os.path.join(_project_root, "config.json")

NOTIFICATION_TYPES = frozenset({"session_start", "session_end", "records_broken"})


def _doc_lookup(doc: dict, key: str | int):
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


def week_bucket_from_agg(agg: dict, iso_year: str, iso_week: str) -> dict:
    year_bucket = _doc_lookup(agg.get("years") or {}, iso_year) or {}
    weeks = year_bucket.get("weeks") if isinstance(year_bucket, dict) else {}
    result = _doc_lookup(weeks or {}, iso_week)
    return result if isinstance(result, dict) else {}



def load_notification_config() -> dict[str, Any]:
    try:
        with open(_config_path, encoding="utf-8") as file:
            cfg = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"enabled": False, "gae_url": "", "secret": ""}

    notif = cfg.get("notifications") or {}
    gae_url = (notif.get("gae_url") or "").strip()
    secret = (notif.get("secret") or "").strip()
    return {
        "enabled": bool(gae_url and secret),
        "gae_url": gae_url,
        "secret": secret,
    }


def format_time(seconds: int | float) -> str:
    total = int(float(seconds or 0))
    if total <= 0:
        return "00:00"
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours >= 24:
        return f"{hours} hours"
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"



def fetch_week_goal_context(username: str, iso_year: str, iso_week: str) -> dict[str, Any]:
    goals_doc = status_meeting.find_one({"_id": "Goals"}) or {}
    user_goals = (week_goals(goals_doc, iso_year, iso_week).get(username)) or {}
    agg = aggregations.find_one({"_id": username}) or {}
    bucket = week_bucket_from_agg(agg, iso_year, iso_week)
    return {
        "active_days": int(bucket.get("active_days") or 0),
        "goal_days": int(user_goals.get("days") or 0),
        "hours": int(bucket.get("time") or 0) / 3600,
        "goal_hours": int(user_goals.get("hours") or 0),
    }


def user_has_activity_on_calendar_day(username: str, log_ts: datetime) -> bool:
    """True if the user already has a committed log on the local calendar day of log_ts."""
    local = to_local(log_ts)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    start_utc = as_utc(day_start).replace(tzinfo=None)
    end_utc = as_utc(day_end).replace(tzinfo=None)
    return (
        collection.find_one(
            {"user": username, "timestamp": {"$gte": start_utc, "$lt": end_utc}},
            projection={"_id": 1},
        )
        is not None
    )


def fetch_week_goal_context_at_start(
    username: str,
    iso_year: str,
    iso_week: str,
    log_ts: datetime,
) -> dict[str, Any]:
    """Week stats for session start, counting today as active before the log is committed."""
    context = fetch_week_goal_context(username, iso_year, iso_week)
    today_becomes_active = not user_has_activity_on_calendar_day(username, log_ts)
    active_days = context["active_days"]
    if today_becomes_active:
        active_days += 1
    return {
        **context,
        "active_days": active_days,
    }



def _format_hours_stat_line(hours: float, goal_hours: int) -> str:
    formatted_hours = f"{int(hours * 10) / 10:g}"
    if goal_hours > 0:
        percentage = int((hours / float(goal_hours)) * 100)
        return f"{formatted_hours} / {goal_hours} h ({percentage} %)"
    return f"{formatted_hours} h"


def _format_days_stat_line(active_days: int, goal_days: int) -> str:
    if goal_days > 0:
        return f"{active_days} / {goal_days} days active"
    return f"{active_days} days active"


def _format_week_stats_block(
    hours: float,
    goal_hours: int,
    active_days: int,
    goal_days: int,
    *,
    for_self: bool,
) -> str:
    header = "## Your weeks stats:" if for_self else "## Their weeks stats:"
    return "\n".join([
        header,
        _format_hours_stat_line(hours, goal_hours),
        _format_days_stat_line(active_days, goal_days),
    ])


def format_start_message(
    actor: str,
    hours: float,
    goal_hours: int,
    active_days: int,
    goal_days: int,
    *,
    for_self: bool = False,
) -> str:
    stats = _format_week_stats_block(
        hours,
        goal_hours,
        active_days,
        goal_days,
        for_self=for_self,
    )
    if for_self:
        return stats
    return f"## **{actor}** just started a session!\n\n{stats}"


def format_end_message(
    actor: str,
    hours: float,
    goal_hours: int,
    active_days: int,
    goal_days: int,
    *,
    for_self: bool = False,
) -> str:
    stats = _format_week_stats_block(
        hours,
        goal_hours,
        active_days,
        goal_days,
        for_self=for_self,
    )
    if for_self:
        return stats
    return f"## **{actor}** just finished a session!\n\n{stats}"


def days_since_record(old_date: datetime | None, reference: datetime) -> int:
    if not isinstance(old_date, datetime):
        return 0
    count_start = add_calendar_days(
        to_local(old_date).replace(hour=0, minute=0, second=0, microsecond=0),
        1,
    )
    ref_local = to_local(reference)
    if ref_local < count_start:
        return 0
    return (ref_local.date() - count_start.date()).days


def format_record_message(record_pair: dict, days: int) -> str:
    old_record = record_pair["old_record"]
    new_record = record_pair["new_record"]
    time_period = old_record["time_type"].lower()

    if old_record["scope"] == "global":
        record_holder = f"{old_record.get('user', 'The')}'s" if old_record.get("user") else "The"
        record_type = "world record"
    elif old_record["scope"] == "personal":
        record_holder = "The"
        record_type = "PB"
    else:
        record_holder = "The"
        record_type = "team record"

    if old_record["metric"] == "total_time":
        old_time = format_time(old_record["value"]["total_time"])
        new_time = format_time(new_record["value"]["total_time"])
        return f"{record_holder} {days} days old {time_period} time {record_type}: {old_time} → {new_time}\n"
    if old_record["metric"] in ("consecutive_days", "consecutive_weeks"):
        unit = "days" if old_record["metric"] == "consecutive_days" else "weeks"
        old_streak = old_record["value"]["streak"]
        new_streak = new_record["value"]["streak"]
        return (
            f"{record_holder} {days} days old lifetime consecutive {unit} {record_type}: "
            f"{old_streak} → {new_streak}\n"
        )

    old_ratio = (
        f"{old_record['value']['active_days']}/{old_record['value']['total_days']} "
        f"({old_record['value']['percentage']:.1%})"
    )
    new_ratio = (
        f"{new_record['value']['active_days']}/{new_record['value']['total_days']} "
        f"({new_record['value']['percentage']:.1%})"
    )
    return (
        f"{record_holder} {days} days old {time_period} activity {record_type}: "
        f"{old_ratio} → {new_ratio}\n"
    )


def create_broken_records_notification(
    actor: str,
    global_records: list,
    personal_records: list,
    combined_records: list,
    reference: datetime,
) -> str:
    filtered_personal_records = []
    for personal_record in personal_records:
        is_duplicate = any(
            personal_record["old_record"]["time_type"] == global_record["old_record"]["time_type"]
            and personal_record["old_record"]["metric"] == global_record["old_record"]["metric"]
            for global_record in global_records
        )
        if not is_duplicate:
            filtered_personal_records.append(personal_record)

    record_counts = []
    if global_records:
        record_counts.append(f"{len(global_records)} world {'record' if len(global_records) == 1 else 'records'}")
    if filtered_personal_records:
        record_counts.append(
            f"{len(filtered_personal_records)} PB{'s' if len(filtered_personal_records) > 1 else ''}"
        )
    if combined_records:
        record_counts.append(
            f"{len(combined_records)} team {'record' if len(combined_records) == 1 else 'records'}"
        )

    if len(record_counts) == 1:
        message = f"## {actor} just broke {record_counts[0]}!\n\n"
    elif len(record_counts) == 2:
        message = f"## {actor} just broke {record_counts[0]} and {record_counts[1]}!\n\n"
    else:
        message = f"## {actor} just broke {record_counts[0]}, {record_counts[1]} and {record_counts[2]}!\n\n"

    all_records = global_records + filtered_personal_records + combined_records
    for record_pair in all_records:
        days = days_since_record(record_pair["old_record"].get("date"), reference)
        message += format_record_message(record_pair, days)
    return message


def _post_notification_sync(
    notification_type: str,
    actor: str,
    message: str,
    message_self: str | None = None,
) -> None:
    if notification_type not in NOTIFICATION_TYPES:
        raise ValueError(f"Unknown notification type: {notification_type}")

    config = load_notification_config()
    print("\n=== Notification Preview ===")
    print(f"[{notification_type}] {actor}")
    print(f"(others) {message}")
    if message_self and message_self != message:
        print(f"(self)   {message_self}")
    print("===========================\n")

    if not config["enabled"]:
        print("Notifications disabled (missing gae_url or secret in config.json).")
        return

    payload = {
        "secret": config["secret"],
        "type": notification_type,
        "actor": actor,
        "message": message,
    }
    if message_self is not None:
        payload["message_self"] = message_self

    try:
        response = requests.post(
            config["gae_url"],
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"Failed to send notification: {exc}")


def post_notification(
    notification_type: str,
    actor: str,
    message: str,
    *,
    message_self: str | None = None,
) -> None:
    threading.Thread(
        target=_post_notification_sync,
        args=(notification_type, actor, message, message_self),
        name=f"notify-{notification_type}",
        daemon=True,
    ).start()
