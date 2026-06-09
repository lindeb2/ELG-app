"""Highscore compare/update from aggregation docs (single fetch + single write)."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from pymongo.collection import Collection

from period_model import PeriodKeys, format_date_str, period_keys, total_days_in_period

_HIGHSCORE_FETCH_PIPELINE = [
    {
        "$match": {
            "$expr": {"$in": ["$_id", ["Highscores", "$$logUser", "Combined"]]},
        }
    },
    {
        "$group": {
            "_id": None,
            "docs": {"$push": {"k": "$_id", "v": "$$ROOT"}},
        }
    },
]

_PERIOD_TYPES = ("Year", "Month", "Week", "Day")
_LIFETIME_TYPE = "Lifetime"


def _empty_consecutive() -> dict:
    return {
        "days": {"value": 0, "date": None},
        "weeks": {"value": 0, "date": None},
    }


def _empty_user_highscores() -> dict:
    return {
        "Year": {
            "time": {"value": 0, "date": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None},
        },
        "Month": {
            "time": {"value": 0, "date": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None},
        },
        "Week": {
            "time": {"value": 0, "date": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None},
        },
        "Day": {"time": {"value": 0, "date": None}},
        "consecutive": _empty_consecutive(),
    }


def _empty_global_scope() -> dict:
    return {
        "Year": {
            "time": {"value": 0, "date": None, "user": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None, "user": None},
        },
        "Month": {
            "time": {"value": 0, "date": None, "user": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None, "user": None},
        },
        "Week": {
            "time": {"value": 0, "date": None, "user": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None, "user": None},
        },
        "Day": {"time": {"value": 0, "date": None, "user": None}},
        "consecutive": {
            "days": {"value": 0, "date": None, "user": None},
            "weeks": {"value": 0, "date": None, "user": None},
        },
    }


def _empty_combined_scope() -> dict:
    return {
        "Year": {
            "time": {"value": 0, "date": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None},
        },
        "Month": {
            "time": {"value": 0, "date": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None},
        },
        "Week": {
            "time": {"value": 0, "date": None},
            "activity": {"value": 0, "active_days": 0, "total_days": 0, "date": None},
        },
        "Day": {"time": {"value": 0, "date": None}},
        "consecutive": _empty_consecutive(),
    }


def _ensure_scope_shape(scope: dict, *, global_scope: bool) -> None:
    if "consecutive" not in scope:
        scope["consecutive"] = (
            {
                "days": {"value": 0, "date": None, "user": None},
                "weeks": {"value": 0, "date": None, "user": None},
            }
            if global_scope
            else _empty_consecutive()
        )


def _default_highscores_doc(user: str) -> dict:
    return {
        "_id": "Highscores",
        user: _empty_user_highscores(),
        "Global": _empty_global_scope(),
        "Combined": _empty_combined_scope(),
    }


def _streak_value(agg: dict, streak_kind: str) -> int:
    return int(((agg.get("streaks") or {}).get(streak_kind) or {}).get("best") or 0)


def _consecutive_value(highscore: dict, streak_kind: str) -> int:
    return int((highscore.get("consecutive") or {}).get(streak_kind, {}).get("value") or 0)


def _streak_broken_value(streak: int) -> dict:
    return {
        "streak": streak,
        "total_time": None,
        "active_days": None,
        "total_days": None,
        "percentage": None,
    }


def _bucket_doc(agg: dict, keys: PeriodKeys, period: str) -> dict:
    years = agg.get("years") or {}
    if period == "year":
        return years.get(keys.year) or {}
    if period == "month":
        return ((years.get(keys.year) or {}).get("months") or {}).get(keys.month) or {}
    if period == "week":
        return ((years.get(keys.iso_week_year) or {}).get("weeks") or {}).get(keys.iso_week) or {}
    if period == "day":
        month_bucket = (years.get(keys.year) or {}).get("months") or {}
        day_bucket = (month_bucket.get(keys.month) or {}).get("days") or {}
        return day_bucket.get(keys.day) or {}
    raise ValueError(period)


def _period_stats(user_agg: dict, combined_agg: dict, keys: PeriodKeys) -> dict[str, dict]:
    mapping = {
        "Year": "year",
        "Month": "month",
        "Week": "week",
        "Day": "day",
    }
    stats: dict[str, dict] = {}
    for time_type, period in mapping.items():
        user_bucket = _bucket_doc(user_agg, keys, period)
        combined_bucket = _bucket_doc(combined_agg, keys, period)
        entry = {
            "user_time": int(user_bucket.get("time") or 0),
            "combined_time": int(combined_bucket.get("time") or 0),
            "user_activity": None,
            "combined_activity": None,
        }
        if time_type != "Day":
            entry["user_activity"] = {
                "active_days": int(user_bucket.get("active_days") or 0),
                "total_days": int(user_bucket.get("total_days") or 0),
                "activity_ratio": float(user_bucket.get("activity_ratio") or 0),
            }
            entry["combined_activity"] = {
                "active_days": int(combined_bucket.get("active_days") or 0),
                "total_days": int(combined_bucket.get("total_days") or 0),
                "activity_ratio": float(combined_bucket.get("activity_ratio") or 0),
            }
        stats[time_type] = entry
    return stats


def _broken_pair(scope: str, time_type: str, metric: str, old_value: dict, new_value: dict, old_date, new_date: str, old_user=None):
    old_record = {
        "scope": scope,
        "time_type": time_type,
        "metric": metric,
        "value": old_value,
        "date": old_date,
    }
    new_record = {
        "scope": scope,
        "time_type": time_type,
        "metric": metric,
        "value": new_value,
        "date": new_date,
    }
    if scope == "global" and old_user is not None:
        old_record["user"] = old_user
    return {"old_record": old_record, "new_record": new_record}


def _apply_period_highscores(
    highscores: dict,
    user: str,
    date_str: str,
    time_type: str,
    stats: dict,
    *,
    global_scope: bool = True,
    combined_scope: bool = True,
) -> list[dict]:
    broken_records: list[dict] = []
    user_time = stats["user_time"]
    combined_time = stats["combined_time"]
    user_activity = stats.get("user_activity")
    combined_activity = stats.get("combined_activity")

    if user_time > highscores[user][time_type]["time"]["value"]:
        broken_records.append(_broken_pair(
            "personal",
            time_type,
            "total_time",
            {
                "total_time": highscores[user][time_type]["time"]["value"],
                "active_days": None,
                "total_days": None,
                "percentage": None,
            },
            {
                "total_time": user_time,
                "active_days": None,
                "total_days": None,
                "percentage": None,
            },
            highscores[user][time_type]["time"]["date"],
            date_str,
        ))
        highscores[user][time_type]["time"] = {"value": user_time, "date": date_str}

        if global_scope and user_time > highscores["Global"][time_type]["time"]["value"]:
            broken_records.append(_broken_pair(
                "global",
                time_type,
                "total_time",
                {
                    "total_time": highscores["Global"][time_type]["time"]["value"],
                    "active_days": None,
                    "total_days": None,
                    "percentage": None,
                },
                {
                    "total_time": user_time,
                    "active_days": None,
                    "total_days": None,
                    "percentage": None,
                },
                highscores["Global"][time_type]["time"]["date"],
                date_str,
                highscores["Global"][time_type]["time"].get("user"),
            ))
            highscores["Global"][time_type]["time"] = {
                "value": user_time,
                "date": date_str,
                "user": user,
            }

    if (
        user_activity
        and time_type != "Day"
        and user_activity["activity_ratio"] > highscores[user][time_type]["activity"]["value"]
    ):
        broken_records.append(_broken_pair(
            "personal",
            time_type,
            "days_active",
            {
                "total_time": None,
                "active_days": highscores[user][time_type]["activity"]["active_days"],
                "total_days": highscores[user][time_type]["activity"]["total_days"],
                "percentage": highscores[user][time_type]["activity"]["value"],
            },
            {
                "total_time": None,
                "active_days": user_activity["active_days"],
                "total_days": user_activity["total_days"],
                "percentage": user_activity["activity_ratio"],
            },
            highscores[user][time_type]["activity"]["date"],
            date_str,
        ))
        highscores[user][time_type]["activity"] = {
            "value": user_activity["activity_ratio"],
            "active_days": user_activity["active_days"],
            "total_days": user_activity["total_days"],
            "date": date_str,
        }

        if global_scope and user_activity["activity_ratio"] > highscores["Global"][time_type]["activity"]["value"]:
            broken_records.append(_broken_pair(
                "global",
                time_type,
                "days_active",
                {
                    "total_time": None,
                    "active_days": highscores["Global"][time_type]["activity"]["active_days"],
                    "total_days": highscores["Global"][time_type]["activity"]["total_days"],
                    "percentage": highscores["Global"][time_type]["activity"]["value"],
                },
                {
                    "total_time": None,
                    "active_days": user_activity["active_days"],
                    "total_days": user_activity["total_days"],
                    "percentage": user_activity["activity_ratio"],
                },
                highscores["Global"][time_type]["activity"]["date"],
                date_str,
                highscores["Global"][time_type]["activity"].get("user"),
            ))
            highscores["Global"][time_type]["activity"] = {
                "value": user_activity["activity_ratio"],
                "active_days": user_activity["active_days"],
                "total_days": user_activity["total_days"],
                "date": date_str,
                "user": user,
            }

    if combined_scope and combined_time > highscores["Combined"][time_type]["time"]["value"]:
        broken_records.append(_broken_pair(
            "combined",
            time_type,
            "total_time",
            {
                "total_time": highscores["Combined"][time_type]["time"]["value"],
                "active_days": None,
                "total_days": None,
                "percentage": None,
            },
            {
                "total_time": combined_time,
                "active_days": None,
                "total_days": None,
                "percentage": None,
            },
            highscores["Combined"][time_type]["time"]["date"],
            date_str,
        ))
        highscores["Combined"][time_type]["time"] = {"value": combined_time, "date": date_str}

    if (
        combined_scope
        and combined_activity
        and time_type != "Day"
        and combined_activity["activity_ratio"] > highscores["Combined"][time_type]["activity"]["value"]
    ):
        broken_records.append(_broken_pair(
            "combined",
            time_type,
            "days_active",
            {
                "total_time": None,
                "active_days": highscores["Combined"][time_type]["activity"]["active_days"],
                "total_days": highscores["Combined"][time_type]["activity"]["total_days"],
                "percentage": highscores["Combined"][time_type]["activity"]["value"],
            },
            {
                "total_time": None,
                "active_days": combined_activity["active_days"],
                "total_days": combined_activity["total_days"],
                "percentage": combined_activity["activity_ratio"],
            },
            highscores["Combined"][time_type]["activity"]["date"],
            date_str,
        ))
        highscores["Combined"][time_type]["activity"] = {
            "value": combined_activity["activity_ratio"],
            "active_days": combined_activity["active_days"],
            "total_days": combined_activity["total_days"],
            "date": date_str,
        }

    return broken_records


def _apply_consecutive_highscores(
    highscores: dict,
    user: str,
    date_str: str,
    user_agg: dict,
    combined_agg: dict,
    *,
    global_scope: bool = True,
    combined_scope: bool = True,
) -> list[dict]:
    broken_records: list[dict] = []
    user_days = _streak_value(user_agg, "days")
    user_weeks = _streak_value(user_agg, "weeks")
    combined_days = _streak_value(combined_agg, "days")
    combined_weeks = _streak_value(combined_agg, "weeks")

    for metric, streak_kind, user_value, combined_value in (
        ("consecutive_days", "days", user_days, combined_days),
        ("consecutive_weeks", "weeks", user_weeks, combined_weeks),
    ):
        if user_value > _consecutive_value(highscores[user], streak_kind):
            broken_records.append(_broken_pair(
                "personal",
                _LIFETIME_TYPE,
                metric,
                _streak_broken_value(_consecutive_value(highscores[user], streak_kind)),
                _streak_broken_value(user_value),
                highscores[user]["consecutive"][streak_kind]["date"],
                date_str,
            ))
            highscores[user]["consecutive"][streak_kind] = {
                "value": user_value,
                "date": date_str,
            }

            if global_scope and user_value > _consecutive_value(highscores["Global"], streak_kind):
                broken_records.append(_broken_pair(
                    "global",
                    _LIFETIME_TYPE,
                    metric,
                    _streak_broken_value(_consecutive_value(highscores["Global"], streak_kind)),
                    _streak_broken_value(user_value),
                    highscores["Global"]["consecutive"][streak_kind]["date"],
                    date_str,
                    highscores["Global"]["consecutive"][streak_kind].get("user"),
                ))
                highscores["Global"]["consecutive"][streak_kind] = {
                    "value": user_value,
                    "date": date_str,
                    "user": user,
                }

        if combined_scope and combined_value > _consecutive_value(highscores["Combined"], streak_kind):
            broken_records.append(_broken_pair(
                "combined",
                _LIFETIME_TYPE,
                metric,
                _streak_broken_value(_consecutive_value(highscores["Combined"], streak_kind)),
                _streak_broken_value(combined_value),
                highscores["Combined"]["consecutive"][streak_kind]["date"],
                date_str,
            ))
            highscores["Combined"]["consecutive"][streak_kind] = {
                "value": combined_value,
                "date": date_str,
            }

    return broken_records


def _fetch_agg_docs(aggregations: Collection, user: str, session) -> tuple[dict, dict, dict]:
    rows = list(
        aggregations.aggregate(
            _HIGHSCORE_FETCH_PIPELINE,
            let={"logUser": user},
            session=session,
        )
    )
    docs_by_id: dict = {}
    if rows and rows[0].get("docs"):
        docs_by_id = {entry["k"]: entry["v"] for entry in rows[0]["docs"]}

    highscores = docs_by_id.get("Highscores")
    if not highscores:
        highscores = _default_highscores_doc(user)
    else:
        highscores = deepcopy(highscores)
        if user not in highscores:
            highscores[user] = _empty_user_highscores()
        else:
            _ensure_scope_shape(highscores[user], global_scope=False)

    if "Global" not in highscores:
        highscores["Global"] = _empty_global_scope()
    else:
        _ensure_scope_shape(highscores["Global"], global_scope=True)

    if "Combined" not in highscores:
        highscores["Combined"] = _empty_combined_scope()
    else:
        _ensure_scope_shape(highscores["Combined"], global_scope=False)

    user_agg = docs_by_id.get(user) or {}
    combined_agg = docs_by_id.get("Combined") or {}
    return highscores, user_agg, combined_agg


def update_highscores(
    aggregations: Collection,
    user: str,
    timestamp: datetime,
    *,
    highscores: dict | None = None,
    user_agg: dict | None = None,
    combined_agg: dict | None = None,
    session=None,
    skip_write: bool = False,
) -> list[dict]:
    """Compare period totals from agg docs against peaks; one write; return broken records."""
    keys = period_keys(timestamp)
    date_str = format_date_str(timestamp)
    if highscores is None or user_agg is None or combined_agg is None:
        highscores, user_agg, combined_agg = _fetch_agg_docs(aggregations, user, session)
    period_stats = _period_stats(user_agg, combined_agg, keys)

    all_broken: list[dict] = []
    for time_type in _PERIOD_TYPES:
        all_broken.extend(
            _apply_period_highscores(highscores, user, date_str, time_type, period_stats[time_type])
        )
    all_broken.extend(
        _apply_consecutive_highscores(
            highscores,
            user,
            date_str,
            user_agg,
            combined_agg,
        )
    )

    if not skip_write:
        aggregations.replace_one({"_id": "Highscores"}, highscores, upsert=True, session=session)
    return all_broken


_PERIOD_NAME = {"Year": "year", "Month": "month", "Week": "week", "Day": "day"}


def update_highscore(
    user: str,
    time_type: str,
    time_value: int,
    date_str: str,
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

    keys = period_keys(datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S"))
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
        date_str,
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
                date_str,
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
    date_str: str,
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
        date_str,
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
    date_str: str,
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
        date_str,
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
    date_str: str,
    totals: _PeriodTotals,
    keys: PeriodKeys,
) -> None:
    if totals.year is not None and totals.year != keys.year:
        _flush_user_period(highscores, user, date_str, "Year", totals)
        totals.year = None
        totals.year_total = 0
        totals.year_active_days = set()

    if totals.month is not None and totals.month != (keys.year, keys.month):
        _flush_user_period(highscores, user, date_str, "Month", totals)
        totals.month = None
        totals.month_total = 0
        totals.month_active_days = set()

    if totals.week is not None and totals.week != (keys.iso_week_year, keys.iso_week):
        _flush_user_period(highscores, user, date_str, "Week", totals)
        totals.week = None
        totals.week_total = 0
        totals.week_active_days = set()

    if totals.day is not None and totals.day != (keys.year, keys.month, keys.day):
        _flush_user_period(highscores, user, date_str, "Day", totals)
        totals.day = None
        totals.day_total = 0


def _flush_combined_periods(
    highscores: dict,
    anchor_user: str,
    date_str: str,
    combined: _PeriodTotals,
    keys: PeriodKeys,
) -> None:
    if combined.year is not None and combined.year != keys.year:
        _flush_combined_period(highscores, anchor_user, date_str, "Year", combined)
        combined.year = None
        combined.year_total = 0
        combined.year_active_days = set()

    if combined.month is not None and combined.month != (keys.year, keys.month):
        _flush_combined_period(highscores, anchor_user, date_str, "Month", combined)
        combined.month = None
        combined.month_total = 0
        combined.month_active_days = set()

    if combined.week is not None and combined.week != (keys.iso_week_year, keys.iso_week):
        _flush_combined_period(highscores, anchor_user, date_str, "Week", combined)
        combined.week = None
        combined.week_total = 0
        combined.week_active_days = set()

    if combined.day is not None and combined.day != (keys.year, keys.month, keys.day):
        _flush_combined_period(highscores, anchor_user, date_str, "Day", combined)
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
    date_str: str,
    totals: _PeriodTotals,
) -> None:
    if totals.year is not None:
        _flush_user_period(highscores, user, date_str, "Year", totals)
    if totals.month is not None:
        _flush_user_period(highscores, user, date_str, "Month", totals)
    if totals.week is not None:
        _flush_user_period(highscores, user, date_str, "Week", totals)
    if totals.day is not None:
        _flush_user_period(highscores, user, date_str, "Day", totals)


def _flush_open_combined_periods(
    highscores: dict,
    anchor_user: str,
    date_str: str,
    combined: _PeriodTotals,
) -> None:
    if combined.year is not None:
        _flush_combined_period(highscores, anchor_user, date_str, "Year", combined)
    if combined.month is not None:
        _flush_combined_period(highscores, anchor_user, date_str, "Month", combined)
    if combined.week is not None:
        _flush_combined_period(highscores, anchor_user, date_str, "Week", combined)
    if combined.day is not None:
        _flush_combined_period(highscores, anchor_user, date_str, "Day", combined)


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
    last_date_str = format_date_str(datetime.now())

    for dt, entry in entries:
        user = entry["user"]
        if user not in users:
            users[user] = _PeriodTotals()
            highscores[user] = _empty_user_highscores()

        date_str = format_date_str(dt)
        last_date_str = date_str
        keys = period_keys(dt)
        elapsed = int(entry["elapsed_time"])

        _flush_combined_periods(highscores, anchor_user, date_str, combined, keys)
        _flush_user_periods(highscores, user, date_str, users[user], keys)

        _accumulate_periods(combined, keys, elapsed)
        _accumulate_periods(users[user], keys, elapsed)

    _flush_open_combined_periods(highscores, anchor_user, last_date_str, combined)
    for user in log_users:
        _flush_open_user_periods(highscores, user, last_date_str, users[user])

    for user in log_users:
        user_agg = aggregations.find_one({"_id": user}) or {}
        combined_agg = aggregations.find_one({"_id": "Combined"}) or {}
        _apply_consecutive_highscores(
            highscores,
            user,
            last_date_str,
            user_agg,
            combined_agg,
            global_scope=True,
            combined_scope=True,
        )

    aggregations.replace_one({"_id": "Highscores"}, highscores, upsert=True)
