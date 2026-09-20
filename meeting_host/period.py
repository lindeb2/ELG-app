"""Timezone/week helpers. Mirrors src/period_model.py (Europe/Stockholm)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

APP_TIMEZONE = "Europe/Stockholm"
_TZ = ZoneInfo(APP_TIMEZONE)


def as_utc(dt: datetime) -> datetime:
    """BSON/PyMongo naive datetimes are UTC instants."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(_TZ)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_naive(dt: datetime) -> datetime:
    return as_utc(dt).replace(tzinfo=None)


def iso_week_of(dt: datetime) -> tuple[str, str]:
    iso_year, iso_week, _ = to_local(dt).isocalendar()
    return str(iso_year), str(iso_week)


def week_bounds_utc_naive(week_start: datetime) -> tuple[datetime, datetime]:
    """[start, end) UTC-naive bounds for the ISO week containing week_start."""
    local = to_local(week_start)
    monday = local.date() - timedelta(days=local.weekday())
    start_local = local.replace(
        year=monday.year,
        month=monday.month,
        day=monday.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end_local = start_local + timedelta(days=7)
    return utc_naive(start_local), utc_naive(end_local)
