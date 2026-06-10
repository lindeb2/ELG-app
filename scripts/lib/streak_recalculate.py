"""Rebuild streak fields on aggregation docs from raw logs."""
from __future__ import annotations
from datetime import datetime
from period_model import day_key_from_dt, week_key_from_dt

def _parse_day_key(day_key: str) -> datetime:
    return datetime.strptime(day_key, "%Y-%m-%d")


def _best_run(sorted_keys: list[str]) -> int:
    if not sorted_keys:
        return 0
    best = 1
    run = 1
    prev = _parse_day_key(sorted_keys[0])
    for key in sorted_keys[1:]:
        current = _parse_day_key(key)
        if (current - prev).days == 1:
            run += 1
        else:
            run = 1
        best = max(best, run)
        prev = current
    return best


def _current_run(sorted_keys: list[str]) -> int:
    if not sorted_keys:
        return 0
    run = 1
    prev = _parse_day_key(sorted_keys[-1])
    for key in reversed(sorted_keys[:-1]):
        current = _parse_day_key(key)
        if (prev - current).days == 1:
            run += 1
            prev = current
        else:
            break
    return run


def _parse_week_key(key: str) -> tuple[int, int]:
    year_str, week_str = key.split("-W", 1)
    return int(year_str), int(week_str)


def _sort_week_keys(week_keys: set[str]) -> list[str]:
    return sorted(week_keys, key=_parse_week_key)


def _best_run_weeks(sorted_keys: list[str]) -> int:
    if not sorted_keys:
        return 0

    def is_consecutive(prev_key: str, key: str) -> bool:
        py, pw = _parse_week_key(prev_key)
        y, w = _parse_week_key(key)
        prev_monday = datetime.fromisocalendar(py, pw, 1)
        monday = datetime.fromisocalendar(y, w, 1)
        return (monday - prev_monday).days == 7

    best = 1
    run = 1
    prev = sorted_keys[0]
    for key in sorted_keys[1:]:
        if is_consecutive(prev, key):
            run += 1
        else:
            run = 1
        best = max(best, run)
        prev = key
    return best


def _current_run_weeks(sorted_keys: list[str]) -> int:
    if not sorted_keys:
        return 0

    run = 1
    py, pw = _parse_week_key(sorted_keys[-1])
    prev_monday = datetime.fromisocalendar(py, pw, 1)
    for key in reversed(sorted_keys[:-1]):
        y, w = _parse_week_key(key)
        monday = datetime.fromisocalendar(y, w, 1)
        if (prev_monday - monday).days == 7:
            run += 1
            prev_monday = monday
        else:
            break
    return run


def streaks_from_day_keys(day_keys: set[str], week_keys: set[str]) -> dict:
    sorted_days = sorted(day_keys)
    sorted_weeks = _sort_week_keys(week_keys)
    return {
        "days": {"current": _current_run(sorted_days)},
        "weeks": {"current": _current_run_weeks(sorted_weeks)},
    }


def peak_streaks_from_day_keys(day_keys: set[str], week_keys: set[str]) -> dict[str, int]:
    sorted_days = sorted(day_keys)
    sorted_weeks = _sort_week_keys(week_keys)
    day_current = _current_run(sorted_days)
    week_current = _current_run_weeks(sorted_weeks)
    return {
        "days": max(_best_run(sorted_days), day_current),
        "weeks": max(_best_run_weeks(sorted_weeks), week_current),
    }


def streaks_from_log_entries(entries: list[dict]) -> dict:
    day_keys: set[str] = set()
    week_keys: set[str] = set()
    for entry in entries:
        ts = entry["timestamp"]
        day_keys.add(day_key_from_dt(ts))
        week_keys.add(week_key_from_dt(ts))
    return streaks_from_day_keys(day_keys, week_keys)
