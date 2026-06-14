"""Calendar period SSOT for Timetable aggregations (Europe/Stockholm)."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

APP_TIMEZONE = "Europe/Stockholm"
_TZ = ZoneInfo(APP_TIMEZONE)


@dataclass(frozen=True)
class PeriodKeys:
    year: str
    month: str
    day: str
    iso_week_year: str
    iso_week: str
    weekday: str


def as_utc(dt: datetime) -> datetime:
    """BSON/PyMongo naive datetimes are UTC instants."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(_TZ)


def add_calendar_days(dt: datetime, days: int) -> datetime:
    """Advance by calendar days in APP_TIMEZONE, preserving local wall-clock time."""
    local = to_local(dt)
    new_date = local.date() + timedelta(days=days)
    return local.replace(year=new_date.year, month=new_date.month, day=new_date.day)


def utc_naive_after_calendar_days(dt: datetime, days: int) -> datetime:
    """BSON naive UTC instant after calendar-day arithmetic in APP_TIMEZONE."""
    return as_utc(add_calendar_days(dt, days)).replace(tzinfo=None)


def monday_midnight_local(dt: datetime) -> datetime:
    """Monday 00:00 local for the week containing dt."""
    local = to_local(dt)
    monday = local.date() - timedelta(days=local.weekday())
    return local.replace(
        year=monday.year,
        month=monday.month,
        day=monday.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def period_keys(dt: datetime) -> PeriodKeys:
    local = to_local(dt)
    iso_year, iso_week, iso_weekday = local.isocalendar()
    return PeriodKeys(
        year=str(local.year),
        month=f"{local.month:02d}",
        day=f"{local.day:02d}",
        iso_week_year=str(iso_year),
        iso_week=str(iso_week),
        weekday=str(iso_weekday),
    )


def total_days(period: str, keys: PeriodKeys) -> int:
    if period == "day":
        return 1
    if period == "week":
        return 7
    if period == "month":
        return calendar.monthrange(int(keys.year), int(keys.month))[1]
    if period == "year":
        year = int(keys.year)
        return 366 if calendar.isleap(year) else 365
    raise ValueError(f"Unknown period: {period}")


def total_days_in_period(year: str | int | None = None, month: str | int | None = None) -> int:
    """Backward-compatible helper used by highscore code."""
    if year is not None and month is None:
        return total_days("year", PeriodKeys(str(int(year)), "01", "01", str(int(year)), "01", "1"))
    if year is not None and month is not None:
        y, m = str(int(year)), f"{int(month):02d}"
        return total_days("month", PeriodKeys(y, m, "01", y, "01", "1"))
    return 7


def active_day_expr() -> dict:
    return {
        "$dateToString": {
            "format": "%Y-%m-%d",
            "date": "$timestamp",
            "timezone": APP_TIMEZONE,
        }
    }


def format_date_str(dt: datetime) -> str:
    return to_local(dt).strftime("%Y-%m-%d %H:%M:%S")


def format_highscore_date(value) -> str:
    """Format a highscore BSON date for display."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return format_date_str(value)
    return str(value)


def coerce_highscore_datetime(value) -> datetime | None:
    """Normalize highscore date field to datetime for comparisons."""
    if isinstance(value, datetime):
        return value
    return None


# --- MongoDB aggregation expression helpers (commit pipeline SSOT) ---

def period_group_id(period: str) -> dict:
    """$group._id fields derived from $timestamp (Europe/Stockholm)."""
    ts = "$timestamp"
    year = _date_to_string("%Y", ts)
    month = _date_to_string("%m", ts)
    day = _date_to_string("%d", ts)
    week_year = {"$toString": {"$isoWeekYear": {"date": ts, "timezone": APP_TIMEZONE}}}
    week = {"$toString": {"$isoWeek": {"date": ts, "timezone": APP_TIMEZONE}}}
    weekday = _date_to_string("%u", ts)

    if period == "year":
        return {"year": year}
    if period == "month":
        return {"year": year, "month": month}
    if period == "week":
        return {"week_year": week_year, "week": week}
    if period == "day":
        return {"year": year, "month": month, "day": day}
    if period == "weekday":
        return {"week_year": week_year, "week": week, "weekday": weekday}
    raise ValueError(f"Unknown period: {period}")



def _trunc(unit: str, date_expr: str, *, start_of_week: str | None = None) -> dict:
    spec: dict = {"date": date_expr, "unit": unit, "timezone": APP_TIMEZONE}
    if start_of_week:
        spec["startOfWeek"] = start_of_week
    return {"$dateTrunc": spec}


def _date_to_string(fmt: str, date_expr: str) -> dict:
    return {
        "$dateToString": {"format": fmt, "date": date_expr, "timezone": APP_TIMEZONE}
    }


def period_key_set_stage(log_ts: str = "$$logTs") -> dict:
    """$set stage: derive period key strings and bounds from a BSON date (let var)."""
    week_trunc = _trunc("week", log_ts, start_of_week="monday")
    return {
        "$set": {
            "logTs": log_ts,
            "logId": "$$logId",
            "elapsed": "$$elapsed",
            "yearStr": _date_to_string("%Y", log_ts),
            "monthStr": _date_to_string("%m", log_ts),
            "dayStr": _date_to_string("%d", log_ts),
            "weekdayStr": _date_to_string("%u", log_ts),
            "weekYearStr": {"$toString": {"$isoWeekYear": {"date": log_ts, "timezone": APP_TIMEZONE}}},
            "weekStr": {"$toString": {"$isoWeek": {"date": log_ts, "timezone": APP_TIMEZONE}}},
            "dayStart": _trunc("day", log_ts),
            "yearStart": _trunc("year", log_ts),
            "yearEnd": {"$dateAdd": {"startDate": _trunc("year", log_ts), "unit": "year", "amount": 1}},
            "monthStart": _trunc("month", log_ts),
            "monthEnd": {"$dateAdd": {"startDate": _trunc("month", log_ts), "unit": "month", "amount": 1}},
            "weekStart": week_trunc,
            "weekEnd": {"$dateAdd": {"startDate": week_trunc, "unit": "week", "amount": 1}},
            "yearTotalDays": {
                "$dateDiff": {
                    "startDate": _trunc("year", log_ts),
                    "endDate": {"$dateAdd": {"startDate": _trunc("year", log_ts), "unit": "year", "amount": 1}},
                    "unit": "day",
                }
            },
            "monthTotalDays": {
                "$dateDiff": {
                    "startDate": _trunc("month", log_ts),
                    "endDate": {"$dateAdd": {"startDate": _trunc("month", log_ts), "unit": "month", "amount": 1}},
                    "unit": "day",
                }
            },
            "weekTotalDays": 7,
        }
    }


def prior_log_lookup_stage(
    *,
    as_name: str,
    period_start: str,
    period_end: str,
    filter_user: bool,
    logs_collection: str = "Timetable",
) -> dict:
    """$lookup: another log in period on same local calendar day (excludes logId)."""
    match_conditions = [
        {"$ne": ["$_id", "$$logId"]},
        {"$gte": ["$timestamp", "$$periodStart"]},
        {"$lt": ["$timestamp", "$$periodEnd"]},
        {
            "$eq": [
                _trunc("day", "$timestamp"),
                "$$dayStart",
            ]
        },
    ]
    if filter_user:
        match_conditions.append({"$eq": ["$user", "$$logUser"]})

    return {
        "$lookup": {
            "from": logs_collection,
            "let": {
                "periodStart": period_start,
                "periodEnd": period_end,
                "dayStart": "$dayStart",
                "logId": "$logId",
                "logUser": "$logUser",
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": match_conditions}}},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ],
            "as": as_name,
        }
    }


def active_inc_expr(prior_array: str) -> dict:
    return {"$cond": [{"$eq": [{"$size": prior_array}, 0]}, 1, 0]}


def had_activity_expr(prior_array: str) -> dict:
    return {"$gt": [{"$size": prior_array}, 0]}


def prior_day_activity_lookup_stage(
    *,
    as_name: str,
    filter_user: bool,
    logs_collection: str = "Timetable",
) -> dict:
    """$lookup: any log on yesterday's local calendar day (excludes logId)."""
    match_conditions = [
        {"$ne": ["$_id", "$$logId"]},
        {"$eq": [_trunc("day", "$timestamp"), "$$yesterdayStart"]},
    ]
    if filter_user:
        match_conditions.append({"$eq": ["$user", "$$logUser"]})

    return {
        "$lookup": {
            "from": logs_collection,
            "let": {
                "yesterdayStart": "$yesterdayStart",
                "logId": "$logId",
                "logUser": "$logUser",
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": match_conditions}}},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ],
            "as": as_name,
        }
    }


def prior_week_activity_lookup_stage(
    *,
    as_name: str,
    filter_user: bool,
    logs_collection: str = "Timetable",
) -> dict:
    """$lookup: any log in the prior ISO week (excludes logId)."""
    match_conditions = [
        {"$ne": ["$_id", "$$logId"]},
        {"$gte": ["$timestamp", "$$priorWeekStart"]},
        {"$lt": ["$timestamp", "$$weekStart"]},
    ]
    if filter_user:
        match_conditions.append({"$eq": ["$user", "$$logUser"]})

    return {
        "$lookup": {
            "from": logs_collection,
            "let": {
                "priorWeekStart": "$priorWeekStart",
                "weekStart": "$weekStart",
                "logId": "$logId",
                "logUser": "$logUser",
            },
            "pipeline": [
                {"$match": {"$expr": {"$and": match_conditions}}},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ],
            "as": as_name,
        }
    }


def streak_key_set_stage() -> dict:
    """$set stage: local day/week keys and prior period bounds for streak updates."""
    prior_week_start = {
        "$dateSubtract": {"startDate": "$weekStart", "unit": "day", "amount": 7},
    }
    yesterday_start = {
        "$dateSubtract": {"startDate": "$dayStart", "unit": "day", "amount": 1},
    }
    return {
        "$set": {
            "yesterdayStart": yesterday_start,
            "priorWeekStart": prior_week_start,
        }
    }


def day_key_from_dt(dt: datetime) -> str:
    return to_local(dt).strftime("%Y-%m-%d")


def week_key_from_dt(dt: datetime) -> str:
    local = to_local(dt)
    iso_year, iso_week, _ = local.isocalendar()
    return f"{iso_year}-W{iso_week}"
