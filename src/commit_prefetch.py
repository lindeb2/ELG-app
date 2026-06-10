"""Single-round-trip prefetch for log commit (context + aggregation slices)."""
from __future__ import annotations

import json
from datetime import datetime

from bson import ObjectId, json_util
from pymongo.collection import Collection

from highscore_commit import (
    _empty_combined_scope,
    _empty_global_scope,
    _empty_user_highscores,
    _ensure_scope_shape,
)
from period_model import (
    active_inc_expr,
    had_activity_expr,
    period_key_set_stage,
    prior_day_activity_lookup_stage,
    prior_log_lookup_stage,
    prior_week_activity_lookup_stage,
    streak_key_set_stage,
)

AGGREGATIONS_COLLECTION = "Timetable Aggregations"

_CONTEXT_PROJECT = {
    "$project": {
        "_id": 0,
        "yearStr": 1,
        "monthStr": 1,
        "dayStr": 1,
        "weekdayStr": 1,
        "weekYearStr": 1,
        "weekStr": 1,
        "yearTotalDays": 1,
        "monthTotalDays": 1,
        "weekTotalDays": 1,
        "yearActiveInc": 1,
        "monthActiveInc": 1,
        "weekActiveInc": 1,
        "hadActivityYesterday": 1,
        "hadActivityPriorWeek": 1,
    }
}

_AGG_SLICE_PROJECT = {
    "year_bucket": {
        "$ifNull": [
            {
                "$getField": {
                    "field": "$$yearStr",
                    "input": {"$ifNull": ["$years", {}]},
                }
            },
            {},
        ]
    },
    "week_bucket": {
        "$let": {
            "vars": {
                "weekYearNode": {
                    "$ifNull": [
                        {
                            "$getField": {
                                "field": "$$weekYearStr",
                                "input": {"$ifNull": ["$years", {}]},
                            }
                        },
                        {},
                    ]
                }
            },
            "in": {
                "$ifNull": [
                    {
                        "$getField": {
                            "field": "$$weekStr",
                            "input": {"$ifNull": ["$$weekYearNode.weeks", {}]},
                        }
                    },
                    {},
                ]
            },
        }
    },
    "streaks": {"$ifNull": ["$streaks", {}]},
}

_HIGHSCORE_SLICE_PROJECT = {
    "userScope": {
        "$ifNull": [
            {
                "$getField": {
                    "field": "$$logUser",
                    "input": "$$ROOT",
                }
            },
            {},
        ]
    },
    "globalScope": {"$ifNull": ["$Global", {}]},
    "combinedScope": {"$ifNull": ["$Combined", {}]},
}


def empty_agg_slice() -> dict:
    return {"year_bucket": {}, "week_bucket": {}, "streaks": {}}


def slice_to_agg_doc(slice: dict, ctx: dict) -> dict:
    """Reconstruct a minimal years tree from a prefetch slice."""
    year_str = ctx["yearStr"]
    week_year_str = ctx["weekYearStr"]
    week_str = ctx["weekStr"]
    year_bucket = dict(slice.get("year_bucket") or {})
    week_bucket = dict(slice.get("week_bucket") or {})

    if week_year_str == year_str:
        weeks = dict(year_bucket.get("weeks") or {})
        weeks[week_str] = week_bucket
        year_bucket["weeks"] = weeks
        years = {year_str: year_bucket}
    else:
        years = {
            year_str: year_bucket,
            week_year_str: {"weeks": {week_str: week_bucket}},
        }

    return {"years": years, "streaks": dict(slice.get("streaks") or {})}


def _lookups_and_activity_stages(*, filter_user: bool) -> list[dict]:
    return [
        prior_log_lookup_stage(
            as_name="priorYear",
            period_start="$yearStart",
            period_end="$yearEnd",
            filter_user=filter_user,
        ),
        prior_log_lookup_stage(
            as_name="priorMonth",
            period_start="$monthStart",
            period_end="$monthEnd",
            filter_user=filter_user,
        ),
        prior_log_lookup_stage(
            as_name="priorWeek",
            period_start="$weekStart",
            period_end="$weekEnd",
            filter_user=filter_user,
        ),
        prior_day_activity_lookup_stage(
            as_name="priorDayActivity",
            filter_user=filter_user,
        ),
        prior_week_activity_lookup_stage(
            as_name="priorWeekActivity",
            filter_user=filter_user,
        ),
        {
            "$set": {
                "yearActiveInc": active_inc_expr("$priorYear"),
                "monthActiveInc": active_inc_expr("$priorMonth"),
                "weekActiveInc": active_inc_expr("$priorWeek"),
                "hadActivityYesterday": had_activity_expr("$priorDayActivity"),
                "hadActivityPriorWeek": had_activity_expr("$priorWeekActivity"),
            }
        },
    ]


def _context_facet_branch(*, filter_user: bool) -> list[dict]:
    return _lookups_and_activity_stages(filter_user=filter_user) + [_CONTEXT_PROJECT]


_AGG_LOOKUP = {
    "$lookup": {
        "from": AGGREGATIONS_COLLECTION,
        "let": {
            "logUser": "$$logUser",
            "yearStr": "$yearStr",
            "monthStr": "$monthStr",
            "dayStr": "$dayStr",
            "weekYearStr": "$weekYearStr",
            "weekStr": "$weekStr",
            "weekdayStr": "$weekdayStr",
        },
        "pipeline": [
            {
                "$match": {
                    "$expr": {
                        "$in": ["$_id", ["Highscores", "$$logUser", "Combined"]],
                    }
                }
            },
            {
                "$project": {
                    "k": "$_id",
                    "v": {
                        "$switch": {
                            "branches": [
                                {
                                    "case": {"$eq": ["$_id", "Highscores"]},
                                    "then": _HIGHSCORE_SLICE_PROJECT,
                                },
                                {
                                    "case": {"$eq": ["$_id", "Combined"]},
                                    "then": _AGG_SLICE_PROJECT,
                                },
                                {
                                    "case": {"$eq": ["$_id", "$$logUser"]},
                                    "then": _AGG_SLICE_PROJECT,
                                },
                            ],
                            "default": None,
                        }
                    },
                }
            },
        ],
        "as": "aggEntries",
    }
}

_PREFETCH_TAIL = [
    period_key_set_stage("$logTs"),
    {"$set": {"logUser": "$$logUser"}},
    streak_key_set_stage(),
    {
        "$facet": {
            "user": _context_facet_branch(filter_user=True),
            "combined": _context_facet_branch(filter_user=False),
            "root": [{"$project": {"_id": 0, "logTs": 1}}],
        }
    },
    {
        "$project": {
            "logTs": {"$arrayElemAt": ["$root.logTs", 0]},
            "user_ctx": {"$arrayElemAt": ["$user", 0]},
            "combined_ctx": {"$arrayElemAt": ["$combined", 0]},
            "yearStr": {"$arrayElemAt": ["$user.yearStr", 0]},
            "monthStr": {"$arrayElemAt": ["$user.monthStr", 0]},
            "dayStr": {"$arrayElemAt": ["$user.dayStr", 0]},
            "weekYearStr": {"$arrayElemAt": ["$user.weekYearStr", 0]},
            "weekStr": {"$arrayElemAt": ["$user.weekStr", 0]},
            "weekdayStr": {"$arrayElemAt": ["$user.weekdayStr", 0]},
        }
    },
    _AGG_LOOKUP,
    {
        "$project": {
            "_id": 0,
            "logTs": 1,
            "user_ctx": 1,
            "combined_ctx": 1,
            "aggEntries": 1,
        }
    },
]

PREFETCH_FOR_LOG_TS_PIPELINE = [
    {"$documents": [{}]},
    {
        "$set": {
            "logTs": "$$logTs",
            "logUser": "$$logUser",
            "logId": "$$logId",
        }
    },
    *_PREFETCH_TAIL,
]


def prefetch_digest(prefetch: dict) -> str:
    payload = {
        k: prefetch[k]
        for k in (
            "user_ctx",
            "combined_ctx",
            "user_agg_slice",
            "combined_agg_slice",
            "highscores_slice",
        )
    }
    return json.dumps(payload, default=json_util.default, sort_keys=True)


def _normalize_highscores_slice(slice: dict | None, user: str) -> dict:
    slice = slice or {}
    user_scope = dict(slice.get("userScope") or {})
    global_scope = dict(slice.get("globalScope") or {})
    combined_scope = dict(slice.get("combinedScope") or {})

    if not user_scope:
        user_scope = _empty_user_highscores()
    else:
        _ensure_scope_shape(user_scope, global_scope=False)

    if not global_scope:
        global_scope = _empty_global_scope()
    else:
        _ensure_scope_shape(global_scope, global_scope=True)

    if not combined_scope:
        combined_scope = _empty_combined_scope()
    else:
        _ensure_scope_shape(combined_scope, global_scope=False)

    return {
        "userScope": user_scope,
        "globalScope": global_scope,
        "combinedScope": combined_scope,
    }


def highscores_slice_to_doc(user: str, slice: dict) -> dict:
    """Build an in-memory highscores document from a prefetch slice."""
    normalized = _normalize_highscores_slice(slice, user)
    return {
        user: normalized["userScope"],
        "Global": normalized["globalScope"],
        "Combined": normalized["combinedScope"],
    }


def _parse_agg_entries(entries: list, user: str) -> tuple[dict, dict, dict]:
    docs_by_id = {entry["k"]: entry["v"] for entry in entries}
    highscores_slice = _normalize_highscores_slice(docs_by_id.get("Highscores"), user)
    user_agg_slice = docs_by_id.get(user) or empty_agg_slice()
    combined_agg_slice = docs_by_id.get("Combined") or empty_agg_slice()
    return highscores_slice, user_agg_slice, combined_agg_slice


def _parse_prefetch_row(row: dict, user: str, log_id: ObjectId) -> dict:
    if row.get("user_ctx") is None or row.get("combined_ctx") is None:
        raise RuntimeError("prefetch: incomplete context rows")
    highscores_slice, user_agg_slice, combined_agg_slice = _parse_agg_entries(
        row.get("aggEntries") or [], user
    )
    return {
        "logTs": row["logTs"],
        "logId": log_id,
        "user_ctx": row["user_ctx"],
        "combined_ctx": row["combined_ctx"],
        "highscores_slice": highscores_slice,
        "user_agg_slice": user_agg_slice,
        "combined_agg_slice": combined_agg_slice,
    }


def _run_prefetch(
    collection: Collection,
    pipeline: list,
    let_vars: dict,
    user: str,
    log_id: ObjectId,
    *,
    session=None,
) -> dict:
    rows = list(
        collection.database.aggregate(
            pipeline,
            let=let_vars,
            session=session,
        )
    )
    if not rows:
        raise RuntimeError("prefetch: pipeline returned no rows")
    return _parse_prefetch_row(rows[0], user, log_id)


def prefetch_for_log_ts(
    collection: Collection,
    user: str,
    log_ts: datetime,
    *,
    log_id: ObjectId | None = None,
    session=None,
) -> dict:
    """Prefetch commit context for a known log timestamp."""
    log_id = log_id or ObjectId()
    return _run_prefetch(
        collection,
        PREFETCH_FOR_LOG_TS_PIPELINE,
        {
            "logUser": user,
            "logTs": log_ts,
            "logId": log_id,
            "elapsed": 0,
        },
        user,
        log_id,
        session=session,
    )
