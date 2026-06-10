"""Admin-only: rebuild highscores from raw logs or explicit period totals."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pymongo.collection import Collection

from highscore_commit import (
    _apply_consecutive_highscores,
    _apply_period_highscores,
    _bucket_doc,
    _default_highscores_doc,
    _empty_combined_scope,
    _empty_global_scope,
    _empty_user_highscores,
    _ensure_scope_shape,
)
from period_model import PeriodKeys, day_key_from_dt, period_keys, to_local, total_days_in_period, week_key_from_dt
from streak_model import project_streak

_PERIOD_NAME = {"Year": "year", "Month": "month", "Week": "week", "Day": "day"}


def _yesterday_day_key(dt: datetime) -> str:
    return (to_local(dt) - timedelta(days=1)).strftime("%Y-%m-%d")


def _prior_week_key(dt: datetime) -> str:
    return week_key_from_dt(to_local(dt) - timedelta(days=7))


@dataclass
class _StreakReplay:
    day_current: int = 0
    week_current: int = 0
    active_days: set[str] = field(default_factory=set)
    active_weeks: set[str] = field(default_factory=set)

    def apply_log(self, dt: datetime) -> tuple[bool, bool]:
        """Update running streak; return (day_gate, week_gate) for this log."""
        day_key = day_key_from_dt(dt)
        week_key = week_key_from_dt(dt)
        day_gate = day_key not in self.active_days
        week_gate = week_key not in self.active_weeks

        self.day_current = project_streak(
            self.day_current,
            active_inc=1 if day_gate else 0,
            prior_period_active=_yesterday_day_key(dt) in self.active_days,
        )
        self.week_current = project_streak(
            self.week_current,
            active_inc=1 if week_gate else 0,
            prior_period_active=_prior_week_key(dt) in self.active_weeks,
        )

        if day_gate:
            self.active_days.add(day_key)
        if week_gate:
            self.active_weeks.add(week_key)
        return day_gate, week_gate

    def as_agg(self) -> dict:
        return {
            "streaks": {
                "days": {"current": self.day_current},
                "weeks": {"current": self.week_current},
            }
        }


def update_highscore(
    user: str,
    time_type: str,
    time_value: int,
    record_ts: datetime,
    aggregations: Collection,
    *,
    is_global: bool = False,
    activity_data: dict | None = None,
) -> list[dict]:
    """Admin/recalculate path: update one period from explicit totals."""
    highscores = aggregations.find_one({"_id": "Highscores"})
    if not highscores:
        highscores = _default_highscores_doc(user)
    else:
        highscores = deepcopy(highscores)
        if user not in highscores:
            highscores[user] = _empty_user_highscores()
        else:
            _ensure_scope_shape(highscores[user], global_scope=False)
    _ensure_scope_shape(highscores.setdefault("Global", _empty_global_scope()), global_scope=True)
    _ensure_scope_shape(highscores.setdefault("Combined", _empty_combined_scope()), global_scope=False)

    keys = period_keys(record_ts)
    combined_bucket = _bucket_doc(
        aggregations.find_one({"_id": "Combined"}) or {},
        keys,
        _PERIOD_NAME[time_type],
    )
    combined_activity = None
    if time_type != "Day":
        combined_activity = {
            "active_days": int(combined_bucket.get("active_days") or 0),
            "total_days": int(combined_bucket.get("total_days") or 0),
            "activity_ratio": float(combined_bucket.get("activity_ratio") or 0),
        }

    stats = {
        "user_time": int(time_value),
        "combined_time": int(combined_bucket.get("time") or 0) if is_global else 0,
        "user_activity": activity_data,
        "combined_activity": combined_activity if is_global else None,
    }

    broken = _apply_period_highscores(
        highscores,
        user,
        record_ts,
        time_type,
        stats,
        global_scope=is_global,
        combined_scope=is_global,
    )
    if is_global:
        user_agg = aggregations.find_one({"_id": user}) or {}
        combined_agg = aggregations.find_one({"_id": "Combined"}) or {}
        broken.extend(
            _apply_consecutive_highscores(
                highscores,
                user,
                record_ts,
                user_agg,
                combined_agg,
                global_scope=True,
                combined_scope=True,
            )
        )
    aggregations.replace_one({"_id": "Highscores"}, highscores, upsert=True)
    return broken


@dataclass
class _PeriodTotals:
    year: str | None = None
    month: tuple[str, str] | None = None
    week: tuple[str, str] | None = None
    day: tuple[str, str, str] | None = None
    year_total: int = 0
    month_total: int = 0
    week_total: int = 0
    day_total: int = 0
    year_active_days: set[str] = field(default_factory=set)
    month_active_days: set[str] = field(default_factory=set)
    week_active_days: set[str] = field(default_factory=set)


def _activity_data(active_days: set[str], total_days: int) -> dict:
    return {
        "active_days": len(active_days),
        "total_days": total_days,
        "activity_ratio": len(active_days) / total_days,
    }


def _flush_user_period(
    highscores: dict,
    user: str,
    record_ts: datetime,
    time_type: str,
    totals: _PeriodTotals,
) -> None:
    if time_type == "Year":
        user_time = totals.year_total
        user_activity = _activity_data(totals.year_active_days, total_days_in_period(totals.year))
    elif time_type == "Month":
        user_time = totals.month_total
        user_activity = _activity_data(totals.month_active_days, total_days_in_period(*totals.month))
    elif time_type == "Week":
        user_time = totals.week_total
        user_activity = _activity_data(totals.week_active_days, total_days_in_period())
    else:
        user_time = totals.day_total
        user_activity = None

    _apply_period_highscores(
        highscores,
        user,
        record_ts,
        time_type,
        {
            "user_time": user_time,
            "combined_time": 0,
            "user_activity": user_activity,
            "combined_activity": None,
        },
        global_scope=True,
        combined_scope=False,
    )


def _flush_combined_period(
    highscores: dict,
    anchor_user: str,
    record_ts: datetime,
    time_type: str,
    combined: _PeriodTotals,
) -> None:
    if time_type == "Year":
        combined_time = combined.year_total
        combined_activity = _activity_data(
            combined.year_active_days,
            total_days_in_period(combined.year),
        )
    elif time_type == "Month":
        combined_time = combined.month_total
        combined_activity = _activity_data(
            combined.month_active_days,
            total_days_in_period(*combined.month),
        )
    elif time_type == "Week":
        combined_time = combined.week_total
        combined_activity = _activity_data(combined.week_active_days, total_days_in_period())
    else:
        combined_time = combined.day_total
        combined_activity = None

    _apply_period_highscores(
        highscores,
        anchor_user,
        record_ts,
        time_type,
        {
            "user_time": 0,
            "combined_time": combined_time,
            "user_activity": None,
            "combined_activity": combined_activity,
        },
        global_scope=False,
        combined_scope=True,
    )


def _flush_user_periods(
    highscores: dict,
    user: str,
    record_ts: datetime,
    totals: _PeriodTotals,
    keys: PeriodKeys,
) -> None:
    if totals.year is not None and totals.year != keys.year:
        _flush_user_period(highscores, user, record_ts, "Year", totals)
        totals.year = None
        totals.year_total = 0
        totals.year_active_days = set()

    if totals.month is not None and totals.month != (keys.year, keys.month):
        _flush_user_period(highscores, user, record_ts, "Month", totals)
        totals.month = None
        totals.month_total = 0
        totals.month_active_days = set()

    if totals.week is not None and totals.week != (keys.iso_week_year, keys.iso_week):
        _flush_user_period(highscores, user, record_ts, "Week", totals)
        totals.week = None
        totals.week_total = 0
        totals.week_active_days = set()

    if totals.day is not None and totals.day != (keys.year, keys.month, keys.day):
        _flush_user_period(highscores, user, record_ts, "Day", totals)
        totals.day = None
        totals.day_total = 0


def _flush_combined_periods(
    highscores: dict,
    anchor_user: str,
    record_ts: datetime,
    combined: _PeriodTotals,
    keys: PeriodKeys,
) -> None:
    if combined.year is not None and combined.year != keys.year:
        _flush_combined_period(highscores, anchor_user, record_ts, "Year", combined)
        combined.year = None
        combined.year_total = 0
        combined.year_active_days = set()

    if combined.month is not None and combined.month != (keys.year, keys.month):
        _flush_combined_period(highscores, anchor_user, record_ts, "Month", combined)
        combined.month = None
        combined.month_total = 0
        combined.month_active_days = set()

    if combined.week is not None and combined.week != (keys.iso_week_year, keys.iso_week):
        _flush_combined_period(highscores, anchor_user, record_ts, "Week", combined)
        combined.week = None
        combined.week_total = 0
        combined.week_active_days = set()

    if combined.day is not None and combined.day != (keys.year, keys.month, keys.day):
        _flush_combined_period(highscores, anchor_user, record_ts, "Day", combined)
        combined.day = None
        combined.day_total = 0


def _accumulate_periods(totals: _PeriodTotals, keys: PeriodKeys, elapsed: int) -> None:
    day_label = f"{keys.year}-{keys.month}-{keys.day}"

    if totals.year != keys.year:
        totals.year = keys.year
        totals.year_total = 0
        totals.year_active_days = set()
    totals.year_total += elapsed
    totals.year_active_days.add(day_label)

    month_key = (keys.year, keys.month)
    if totals.month != month_key:
        totals.month = month_key
        totals.month_total = 0
        totals.month_active_days = set()
    totals.month_total += elapsed
    totals.month_active_days.add(day_label)

    week_key = (keys.iso_week_year, keys.iso_week)
    if totals.week != week_key:
        totals.week = week_key
        totals.week_total = 0
        totals.week_active_days = set()
    totals.week_total += elapsed
    totals.week_active_days.add(day_label)

    day_key = (keys.year, keys.month, keys.day)
    if totals.day != day_key:
        totals.day = day_key
        totals.day_total = 0
    totals.day_total += elapsed


def _flush_open_user_periods(
    highscores: dict,
    user: str,
    record_ts: datetime,
    totals: _PeriodTotals,
) -> None:
    if totals.year is not None:
        _flush_user_period(highscores, user, record_ts, "Year", totals)
    if totals.month is not None:
        _flush_user_period(highscores, user, record_ts, "Month", totals)
    if totals.week is not None:
        _flush_user_period(highscores, user, record_ts, "Week", totals)
    if totals.day is not None:
        _flush_user_period(highscores, user, record_ts, "Day", totals)


def _flush_open_combined_periods(
    highscores: dict,
    anchor_user: str,
    record_ts: datetime,
    combined: _PeriodTotals,
) -> None:
    if combined.year is not None:
        _flush_combined_period(highscores, anchor_user, record_ts, "Year", combined)
    if combined.month is not None:
        _flush_combined_period(highscores, anchor_user, record_ts, "Month", combined)
    if combined.week is not None:
        _flush_combined_period(highscores, anchor_user, record_ts, "Week", combined)
    if combined.day is not None:
        _flush_combined_period(highscores, anchor_user, record_ts, "Day", combined)


def _replay_consecutive_highscores(
    highscores: dict,
    entries: list[tuple[datetime, dict]],
    log_users: list[str],
) -> None:
    """Replay logs chronologically to set consecutive peaks in highscores."""
    user_streaks: dict[str, _StreakReplay] = {u: _StreakReplay() for u in log_users}
    combined_streak = _StreakReplay()

    for dt, entry in entries:
        user = entry["user"]
        if user not in user_streaks:
            user_streaks[user] = _StreakReplay()
            if user not in highscores:
                highscores[user] = _empty_user_highscores()

        user_day_gate, user_week_gate = user_streaks[user].apply_log(dt)
        combined_day_gate, combined_week_gate = combined_streak.apply_log(dt)

        _apply_consecutive_highscores(
            highscores,
            user,
            dt,
            user_streaks[user].as_agg(),
            combined_streak.as_agg(),
            user_day_gate=user_day_gate,
            user_week_gate=user_week_gate,
            combined_day_gate=combined_day_gate,
            combined_week_gate=combined_week_gate,
            global_scope=True,
            combined_scope=True,
        )


def rebuild_highscores_from_logs(collection: Collection, aggregations: Collection) -> None:
    """Rebuild Highscores by replaying logs with explicit combined period totals."""
    log_users = [user for user in collection.distinct("user") if user]
    if not log_users:
        return

    anchor_user = log_users[0]
    highscores = _default_highscores_doc(anchor_user)
    for user in log_users[1:]:
        highscores[user] = _empty_user_highscores()

    entries = [(doc["timestamp"], doc) for doc in collection.find({})]
    entries.sort(key=lambda item: item[0])

    combined = _PeriodTotals()
    users = {user: _PeriodTotals() for user in log_users}
    last_ts = datetime.now()

    for dt, entry in entries:
        user = entry["user"]
        if user not in users:
            users[user] = _PeriodTotals()
            highscores[user] = _empty_user_highscores()

        last_ts = dt
        keys = period_keys(dt)
        elapsed = int(entry["elapsed_time"])

        _flush_combined_periods(highscores, anchor_user, dt, combined, keys)
        _flush_user_periods(highscores, user, dt, users[user], keys)

        _accumulate_periods(combined, keys, elapsed)
        _accumulate_periods(users[user], keys, elapsed)

    _flush_open_combined_periods(highscores, anchor_user, last_ts, combined)
    for user in log_users:
        _flush_open_user_periods(highscores, user, last_ts, users[user])

    _replay_consecutive_highscores(highscores, entries, log_users)

    aggregations.replace_one({"_id": "Highscores"}, highscores, upsert=True)
