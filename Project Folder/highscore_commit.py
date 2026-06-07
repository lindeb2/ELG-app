"""Highscore compare/update from aggregation docs (single fetch + single write)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from pymongo.collection import Collection

from highscore_pipeline import HIGHSCORE_FETCH_PIPELINE
from period_model import PeriodKeys, format_date_str, period_keys

_PERIOD_TYPES = ("Year", "Month", "Week", "Day")


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
    }


def _default_highscores_doc(user: str) -> dict:
    return {
        "_id": "Highscores",
        user: _empty_user_highscores(),
        "Global": {
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
        },
        "Combined": {
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
        },
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


def _fetch_agg_docs(aggregations: Collection, user: str, session) -> tuple[dict, dict, dict]:
    rows = list(
        aggregations.aggregate(
            HIGHSCORE_FETCH_PIPELINE,
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

    user_agg = docs_by_id.get(user) or {}
    combined_agg = docs_by_id.get("Combined") or {}
    return highscores, user_agg, combined_agg


def update_highscores(
    aggregations: Collection,
    user: str,
    timestamp: datetime,
    *,
    session=None,
) -> list[dict]:
    """Compare period totals from agg docs against peaks; one write; return broken records."""
    keys = period_keys(timestamp)
    date_str = format_date_str(timestamp)
    highscores, user_agg, combined_agg = _fetch_agg_docs(aggregations, user, session)
    period_stats = _period_stats(user_agg, combined_agg, keys)

    all_broken: list[dict] = []
    for time_type in _PERIOD_TYPES:
        all_broken.extend(
            _apply_period_highscores(highscores, user, date_str, time_type, period_stats[time_type])
        )

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
    aggregations.replace_one({"_id": "Highscores"}, highscores, upsert=True)
    return broken
