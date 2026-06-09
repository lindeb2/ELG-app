"""Rebuild period aggregations from raw Timetable logs (admin/recalculate only)."""
from __future__ import annotations

from period_model import (
    PeriodKeys,
    active_day_expr,
    period_group_id,
    total_days,
)
from timetable_db import aggregations, collection

_ACTIVITY_PERIODS = frozenset({"year", "month", "week"})


def _group_pipeline(period: str, *, include_user: bool) -> list[dict]:
    group_id = period_group_id(period)
    if include_user:
        group_id = {"user": "$user", **group_id}

    group: dict = {
        "_id": group_id,
        "time": {"$sum": "$elapsed_time"},
    }
    if period in _ACTIVITY_PERIODS:
        group["active_days"] = {"$addToSet": active_day_expr()}

    return [
        {
            "$match": {
                "timestamp": {"$type": "date"},
                "user": {"$nin": [None, ""]},
            }
        },
        {"$group": group},
    ]


def _activity_fields(period: str, gid: dict, time: int, active_days: list[str]) -> dict:
    active_count = len(active_days)
    if period == "year":
        keys = PeriodKeys(gid["year"], "01", "01", gid["year"], "01", "1")
        total = total_days("year", keys)
    elif period == "month":
        keys = PeriodKeys(gid["year"], gid["month"], "01", gid["year"], "01", "1")
        total = total_days("month", keys)
    else:
        total = 7
    return {
        "time": time,
        "active_days": active_count,
        "total_days": total,
        "activity_ratio": active_count / total,
    }


def _time_fields(time: int) -> dict:
    return {"time": time}


def _bucket_fields(period: str, gid: dict, entry: dict) -> dict:
    time = int(entry["time"])
    if period in _ACTIVITY_PERIODS:
        return _activity_fields(period, gid, time, entry["active_days"])
    return _time_fields(time)


def _apply_user_bucket(years: dict, period: str, gid: dict, entry: dict) -> None:
    fields = _bucket_fields(period, gid, entry)
    if period == "year":
        years.setdefault(gid["year"], {}).update(fields)
    elif period == "month":
        (
            years.setdefault(gid["year"], {})
            .setdefault("months", {})
            .setdefault(gid["month"], {})
            .update(fields)
        )
    elif period == "week":
        (
            years.setdefault(gid["week_year"], {})
            .setdefault("weeks", {})
            .setdefault(gid["week"], {})
            .update(fields)
        )
    elif period == "day":
        (
            years.setdefault(gid["year"], {})
            .setdefault("months", {})
            .setdefault(gid["month"], {})
            .setdefault("days", {})
            .setdefault(gid["day"], {})
            .update(fields)
        )
    elif period == "weekday":
        (
            years.setdefault(gid["week_year"], {})
            .setdefault("weeks", {})
            .setdefault(gid["week"], {})
            .setdefault("weekdays", {})
            .setdefault(gid["weekday"], {})
            .update(fields)
        )


def _apply_combined_bucket(years: dict, period: str, gid: dict, entry: dict) -> None:
    _apply_user_bucket(years, period, gid, entry)


def _collect_period_results(period: str) -> tuple[dict[str, dict], dict]:
    user_years: dict[str, dict] = {}
    combined_years: dict = {}

    for entry in collection.aggregate(_group_pipeline(period, include_user=True)):
        gid = entry["_id"]
        user = gid.pop("user")
        if user:
            _apply_user_bucket(user_years.setdefault(user, {}), period, gid, entry)

    for entry in collection.aggregate(_group_pipeline(period, include_user=False)):
        gid = entry["_id"]
        _apply_combined_bucket(combined_years, period, gid, entry)

    return user_years, combined_years


def _merge_years(existing: dict, incoming: dict) -> dict:
    for key, value in incoming.items():
        if key not in existing:
            existing[key] = value
            continue
        if isinstance(value, dict) and isinstance(existing[key], dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, dict) and isinstance(existing[key].get(sub_key), dict):
                    _merge_years(existing[key][sub_key], sub_value)
                else:
                    existing[key][sub_key] = sub_value
        else:
            existing[key] = value
    return existing


def rebuild_all_aggregations() -> None:
    """Rebuild all period buckets in one pass per period type using MongoDB date grouping."""
    user_years: dict[str, dict] = {}
    combined_years: dict = {}

    for period in ("year", "month", "week", "day", "weekday"):
        period_users, period_combined = _collect_period_results(period)
        for user, years in period_users.items():
            _merge_years(user_years.setdefault(user, {}), years)
        _merge_years(combined_years, period_combined)

    for user, years in user_years.items():
        aggregations.update_one({"_id": user}, {"$set": {"years": years}}, upsert=True)

    aggregations.update_one({"_id": "Combined"}, {"$set": {"years": combined_years}}, upsert=True)
