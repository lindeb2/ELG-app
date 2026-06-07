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


def to_bson_naive(dt_utc: datetime) -> datetime:
    return dt_utc.astimezone(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(_TZ)


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


def calendar_week_key(dt: datetime) -> tuple[str, str]:
    keys = period_keys(dt)
    return keys.iso_week_year, keys.iso_week


def agg_path(period: str, keys: PeriodKeys) -> str:
    if period == "year":
        return f"years.{keys.year}"
    if period == "month":
        return f"years.{keys.year}.months.{keys.month}"
    if period == "day":
        return f"years.{keys.year}.months.{keys.month}.days.{keys.day}"
    if period == "week":
        return f"years.{keys.iso_week_year}.weeks.{keys.iso_week}"
    if period == "weekday":
        return f"years.{keys.iso_week_year}.weeks.{keys.iso_week}.weekdays.{keys.weekday}"
    raise ValueError(f"Unknown period: {period}")


def period_bounds(
    period: str,
    *,
    year: int,
    month: int | None = None,
    day: int | None = None,
    week: int | None = None,
) -> tuple[datetime, datetime]:
    """Inclusive start, exclusive end as UTC-naive BSON instants (local calendar)."""
    if period == "day":
        start_local = datetime(year, month, day, tzinfo=_TZ)
        return to_bson_naive(start_local), to_bson_naive(start_local + timedelta(days=1))

    if period == "week":
        start_local = datetime.fromisocalendar(year, week, 1).replace(tzinfo=_TZ)
        return to_bson_naive(start_local), to_bson_naive(start_local + timedelta(weeks=1))

    if period == "month":
        start_local = datetime(year, month, 1, tzinfo=_TZ)
        if month == 12:
            end_local = datetime(year + 1, 1, 1, tzinfo=_TZ)
        else:
            end_local = datetime(year, month + 1, 1, tzinfo=_TZ)
        return to_bson_naive(start_local), to_bson_naive(end_local)

    if period == "year":
        start_local = datetime(year, 1, 1, tzinfo=_TZ)
        end_local = datetime(year + 1, 1, 1, tzinfo=_TZ)
        return to_bson_naive(start_local), to_bson_naive(end_local)

    raise ValueError(f"Unknown period: {period}")


def calendar_bounds(
    period: str,
    *,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
    week: int | None = None,
) -> tuple[datetime, datetime]:
    return period_bounds(period, year=year, month=month, day=day, week=week)


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


def active_day_trunc_expr(field: str = "$timestamp") -> dict:
    return {
        "$dateTrunc": {
            "date": field,
            "unit": "day",
            "timezone": APP_TIMEZONE,
        }
    }


def day_start_for_keys(keys: PeriodKeys) -> datetime:
    start, _ = period_bounds("day", year=int(keys.year), month=int(keys.month), day=int(keys.day))
    return start


def format_date_str(dt: datetime) -> str:
    return to_local(dt).strftime("%Y-%m-%d %H:%M:%S")


def timestamp_range_match(start: datetime, end: datetime) -> dict:
    return {"timestamp": {"$gte": start, "$lt": end}}


# --- MongoDB aggregation expression helpers (commit pipeline SSOT) ---

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
