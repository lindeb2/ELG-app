"""Rebuild period aggregations from raw Timetable logs (admin/recalculate only)."""
from datetime import datetime

from period_model import active_day_expr, calendar_bounds, total_days_in_period, timestamp_range_match
from timetable_db import aggregations, collection

_ACTIVE_DAY_EXPR = active_day_expr()

_TIME_BY_USER_GROUP = {"$group": {"_id": "$user", "time": {"$sum": "$elapsed_time"}}}
_TIME_AND_DAYS_BY_USER_GROUP = {
    "$group": {
        "_id": "$user",
        "time": {"$sum": "$elapsed_time"},
        "active_days": {"$addToSet": _ACTIVE_DAY_EXPR},
    }
}


def _sum_time_by_user(start: datetime, end: datetime) -> list:
    pipeline = [{"$match": timestamp_range_match(start, end)}, _TIME_BY_USER_GROUP]
    return list(collection.aggregate(pipeline))


def _sum_time_and_days_by_user(start: datetime, end: datetime) -> list:
    pipeline = [
        {"$match": timestamp_range_match(start, end)},
        _TIME_AND_DAYS_BY_USER_GROUP,
    ]
    return list(collection.aggregate(pipeline))


def _upsert_aggregation(doc_id: str, fields: dict) -> None:
    aggregations.update_one({"_id": doc_id}, {"$set": fields}, upsert=True)


def _write_time_field(result: list, base_path: str) -> None:
    combined_time = sum(entry["time"] for entry in result if entry["_id"])
    _upsert_aggregation("Combined", {f"{base_path}.time": combined_time})
    for entry in result:
        user_id = entry["_id"]
        if user_id:
            _upsert_aggregation(user_id, {f"{base_path}.time": entry["time"]})


def _write_activity_fields(result: list, base_path: str, total_days: int) -> None:
    combined_time = sum(entry["time"] for entry in result if entry["_id"])
    combined_active_days = len({day for entry in result for day in entry["active_days"]})
    combined_ratio = combined_active_days / total_days

    _upsert_aggregation(
        "Combined",
        {
            f"{base_path}.time": combined_time,
            f"{base_path}.active_days": combined_active_days,
            f"{base_path}.total_days": total_days,
            f"{base_path}.activity_ratio": combined_ratio,
        },
    )

    for entry in result:
        user_id = entry["_id"]
        if not user_id:
            continue
        user_active_days = len(entry["active_days"])
        _upsert_aggregation(
            user_id,
            {
                f"{base_path}.time": entry["time"],
                f"{base_path}.active_days": user_active_days,
                f"{base_path}.total_days": total_days,
                f"{base_path}.activity_ratio": user_active_days / total_days,
            },
        )


def aggregate(
    period: str,
    *,
    year: str | None = None,
    month: str | None = None,
    day: str | None = None,
    week_year: str | None = None,
    week: str | None = None,
    weekday: str | None = None,
) -> None:
    y = int(year) if year is not None else None
    m = int(month) if month is not None else None
    d = int(day) if day is not None else None
    wy = int(week_year) if week_year is not None else None
    w = int(week) if week is not None else None
    wd = int(weekday) if weekday is not None else None

    if period == "year":
        start, end = calendar_bounds("year", year=y)
        total_days = total_days_in_period(y)
        path_base = f"years.{year}"
    elif period == "month":
        path_base = f"years.{year}.months.{month}"
        start, end = calendar_bounds("month", year=y, month=m)
        total_days = total_days_in_period(y, m)
    elif period == "week":
        path_base = f"years.{week_year}.weeks.{week}"
        start, end = calendar_bounds("week", year=wy, week=w)
        total_days = total_days_in_period()
    else:
        start, end = calendar_bounds("day", year=y, month=m, day=d)
        total_days = None
        if period == "day":
            path_base = f"years.{year}.months.{month}.days.{day}"
        elif period == "weekday":
            path_base = f"years.{week_year}.weeks.{week}.weekdays.{weekday}"

    if total_days is not None:
        result = _sum_time_and_days_by_user(start, end)
        _write_activity_fields(result, path_base, total_days)
        return
    result = _sum_time_by_user(start, end)
    _write_time_field(result, path_base)
