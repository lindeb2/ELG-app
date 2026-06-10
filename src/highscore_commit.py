"""Highscore compare/update from aggregation docs (single fetch + single write)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pymongo.collection import Collection
from period_model import PeriodKeys, period_keys

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


def _current_streak_value(agg: dict, streak_kind: str) -> int:
    return int(((agg.get("streaks") or {}).get(streak_kind) or {}).get("current") or 0)


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


def _broken_pair(
    scope: str,
    time_type: str,
    metric: str,
    old_value: dict,
    new_value: dict,
    old_date,
    new_date: datetime,
    old_user=None,
):
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


def _record_highscore_set(set_ops: dict, scope_key: str, path: str, value: dict) -> None:
    set_ops[f"{scope_key}.{path}"] = value


def _apply_period_highscores(
    highscores: dict,
    user: str,
    record_ts: datetime,
    time_type: str,
    stats: dict,
    *,
    global_scope: bool = True,
    combined_scope: bool = True,
    set_ops: dict | None = None,
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
            record_ts,
        ))
        new_time = {"value": user_time, "date": record_ts}
        highscores[user][time_type]["time"] = new_time
        if set_ops is not None:
            _record_highscore_set(set_ops, user, f"{time_type}.time", new_time)

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
                record_ts,
                highscores["Global"][time_type]["time"].get("user"),
            ))
            new_global_time = {
                "value": user_time,
                "date": record_ts,
                "user": user,
            }
            highscores["Global"][time_type]["time"] = new_global_time
            if set_ops is not None:
                _record_highscore_set(set_ops, "Global", f"{time_type}.time", new_global_time)

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
            record_ts,
        ))
        new_activity = {
            "value": user_activity["activity_ratio"],
            "active_days": user_activity["active_days"],
            "total_days": user_activity["total_days"],
            "date": record_ts,
        }
        highscores[user][time_type]["activity"] = new_activity
        if set_ops is not None:
            _record_highscore_set(set_ops, user, f"{time_type}.activity", new_activity)

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
                record_ts,
                highscores["Global"][time_type]["activity"].get("user"),
            ))
            new_global_activity = {
                "value": user_activity["activity_ratio"],
                "active_days": user_activity["active_days"],
                "total_days": user_activity["total_days"],
                "date": record_ts,
                "user": user,
            }
            highscores["Global"][time_type]["activity"] = new_global_activity
            if set_ops is not None:
                _record_highscore_set(
                    set_ops, "Global", f"{time_type}.activity", new_global_activity
                )

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
            record_ts,
        ))
        new_combined_time = {"value": combined_time, "date": record_ts}
        highscores["Combined"][time_type]["time"] = new_combined_time
        if set_ops is not None:
            _record_highscore_set(set_ops, "Combined", f"{time_type}.time", new_combined_time)

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
            record_ts,
        ))
        new_combined_activity = {
            "value": combined_activity["activity_ratio"],
            "active_days": combined_activity["active_days"],
            "total_days": combined_activity["total_days"],
            "date": record_ts,
        }
        highscores["Combined"][time_type]["activity"] = new_combined_activity
        if set_ops is not None:
            _record_highscore_set(
                set_ops, "Combined", f"{time_type}.activity", new_combined_activity
            )

    return broken_records


def _streak_gates_from_ctx(user_ctx: dict, combined_ctx: dict) -> dict[str, bool]:
    """Derive consecutive check gates from prefetch context."""
    return {
        "user_day_gate": int(user_ctx.get("yearActiveInc") or 0) == 1,
        "user_week_gate": int(user_ctx.get("weekActiveInc") or 0) == 1,
        "combined_day_gate": int(combined_ctx.get("yearActiveInc") or 0) == 1,
        "combined_week_gate": int(combined_ctx.get("weekActiveInc") or 0) == 1,
    }


def _apply_consecutive_highscores(
    highscores: dict,
    user: str,
    record_ts: datetime,
    user_agg: dict,
    combined_agg: dict,
    *,
    user_day_gate: bool = True,
    user_week_gate: bool = True,
    combined_day_gate: bool = True,
    combined_week_gate: bool = True,
    global_scope: bool = True,
    combined_scope: bool = True,
    set_ops: dict | None = None,
) -> list[dict]:
    broken_records: list[dict] = []
    user_days = _current_streak_value(user_agg, "days")
    user_weeks = _current_streak_value(user_agg, "weeks")
    combined_days = _current_streak_value(combined_agg, "days")
    combined_weeks = _current_streak_value(combined_agg, "weeks")

    checks = (
        ("consecutive_days", "days", user_days, combined_days, user_day_gate, combined_day_gate),
        ("consecutive_weeks", "weeks", user_weeks, combined_weeks, user_week_gate, combined_week_gate),
    )
    for metric, streak_kind, user_value, combined_value, u_gate, c_gate in checks:
        if u_gate and user_value > _consecutive_value(highscores[user], streak_kind):
            broken_records.append(_broken_pair(
                "personal",
                _LIFETIME_TYPE,
                metric,
                _streak_broken_value(_consecutive_value(highscores[user], streak_kind)),
                _streak_broken_value(user_value),
                highscores[user]["consecutive"][streak_kind]["date"],
                record_ts,
            ))
            new_user_streak = {"value": user_value, "date": record_ts}
            highscores[user]["consecutive"][streak_kind] = new_user_streak
            if set_ops is not None:
                _record_highscore_set(
                    set_ops, user, f"consecutive.{streak_kind}", new_user_streak
                )

            if global_scope and user_value > _consecutive_value(highscores["Global"], streak_kind):
                broken_records.append(_broken_pair(
                    "global",
                    _LIFETIME_TYPE,
                    metric,
                    _streak_broken_value(_consecutive_value(highscores["Global"], streak_kind)),
                    _streak_broken_value(user_value),
                    highscores["Global"]["consecutive"][streak_kind]["date"],
                    record_ts,
                    highscores["Global"]["consecutive"][streak_kind].get("user"),
                ))
                new_global_streak = {
                    "value": user_value,
                    "date": record_ts,
                    "user": user,
                }
                highscores["Global"]["consecutive"][streak_kind] = new_global_streak
                if set_ops is not None:
                    _record_highscore_set(
                        set_ops,
                        "Global",
                        f"consecutive.{streak_kind}",
                        new_global_streak,
                    )

        if combined_scope and c_gate and combined_value > _consecutive_value(highscores["Combined"], streak_kind):
            broken_records.append(_broken_pair(
                "combined",
                _LIFETIME_TYPE,
                metric,
                _streak_broken_value(_consecutive_value(highscores["Combined"], streak_kind)),
                _streak_broken_value(combined_value),
                highscores["Combined"]["consecutive"][streak_kind]["date"],
                record_ts,
            ))
            new_combined_streak = {"value": combined_value, "date": record_ts}
            highscores["Combined"]["consecutive"][streak_kind] = new_combined_streak
            if set_ops is not None:
                _record_highscore_set(
                    set_ops,
                    "Combined",
                    f"consecutive.{streak_kind}",
                    new_combined_streak,
                )

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


def build_highscore_update_ops(
    user: str,
    timestamp: datetime,
    user_ctx: dict,
    combined_ctx: dict,
    *,
    highscores: dict,
    user_agg: dict,
    combined_agg: dict,
) -> tuple[list[dict], dict]:
    """Compare projected stats against peaks; return broken records and $set ops."""
    keys = period_keys(timestamp)
    period_stats = _period_stats(user_agg, combined_agg, keys)
    gates = _streak_gates_from_ctx(user_ctx, combined_ctx)
    set_ops: dict = {}

    all_broken: list[dict] = []
    for time_type in _PERIOD_TYPES:
        all_broken.extend(
            _apply_period_highscores(
                highscores,
                user,
                timestamp,
                time_type,
                period_stats[time_type],
                set_ops=set_ops,
            )
        )
    all_broken.extend(
        _apply_consecutive_highscores(
            highscores,
            user,
            timestamp,
            user_agg,
            combined_agg,
            set_ops=set_ops,
            **gates,
        )
    )

    update: dict = {}
    if set_ops:
        update["$set"] = set_ops
    return all_broken, update


def update_highscores(
    aggregations: Collection,
    user: str,
    timestamp: datetime,
    user_ctx: dict,
    combined_ctx: dict,
    *,
    highscores: dict | None = None,
    user_agg: dict | None = None,
    combined_agg: dict | None = None,
    session=None,
    skip_write: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """Compare period totals from agg docs against peaks; return broken records."""
    if highscores is None or user_agg is None or combined_agg is None:
        highscores, user_agg, combined_agg = _fetch_agg_docs(aggregations, user, session)

    broken_records, update = build_highscore_update_ops(
        user,
        timestamp,
        user_ctx,
        combined_ctx,
        highscores=highscores,
        user_agg=user_agg,
        combined_agg=combined_agg,
    )

    if not skip_write:
        if update:
            aggregations.update_one(
                {"_id": "Highscores"},
                update,
                upsert=True,
                session=session,
            )
        else:
            aggregations.update_one(
                {"_id": "Highscores"},
                {"$setOnInsert": {"_id": "Highscores"}},
                upsert=True,
                session=session,
            )
        return broken_records
    return broken_records, update
