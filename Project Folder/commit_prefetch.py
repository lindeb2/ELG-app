"""Single-round-trip prefetch for log commit (context + aggregation docs)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from bson import ObjectId
from pymongo.collection import Collection

from commit_pipeline import _context_facet_branch
from highscore_commit import _default_highscores_doc, _ensure_scope_shape
from period_model import period_key_set_stage, streak_key_set_stage

AGGREGATIONS_COLLECTION = "Timetable Aggregations"

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
        }
    },
    {
        "$lookup": {
            "from": AGGREGATIONS_COLLECTION,
            "let": {"logUser": "$$logUser"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$in": ["$_id", ["Highscores", "$$logUser", "Combined"]],
                        }
                    }
                },
                {"$project": {"k": "$_id", "v": "$$ROOT"}},
            ],
            "as": "aggEntries",
        }
    },
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


def _normalize_highscores(highscores: dict | None, user: str) -> dict:
    if not highscores:
        return _default_highscores_doc(user)
    doc = deepcopy(highscores)
    if user not in doc:
        doc[user] = _default_highscores_doc(user)[user]
    else:
        _ensure_scope_shape(doc[user], global_scope=False)
    if "Global" not in doc:
        doc["Global"] = _default_highscores_doc(user)["Global"]
    else:
        _ensure_scope_shape(doc["Global"], global_scope=True)
    if "Combined" not in doc:
        doc["Combined"] = _default_highscores_doc(user)["Combined"]
    else:
        _ensure_scope_shape(doc["Combined"], global_scope=False)
    return doc


def _parse_agg_entries(entries: list, user: str) -> tuple[dict, dict, dict]:
    docs_by_id = {entry["k"]: entry["v"] for entry in entries}
    highscores = _normalize_highscores(docs_by_id.get("Highscores"), user)
    user_agg = docs_by_id.get(user) or {}
    combined_agg = docs_by_id.get("Combined") or {}
    return highscores, user_agg, combined_agg


def _parse_prefetch_row(row: dict, user: str, log_id: ObjectId) -> dict:
    if row.get("user_ctx") is None or row.get("combined_ctx") is None:
        raise RuntimeError("prefetch: incomplete context rows")
    highscores, user_agg, combined_agg = _parse_agg_entries(
        row.get("aggEntries") or [], user
    )
    return {
        "logTs": row["logTs"],
        "logId": log_id,
        "user_ctx": row["user_ctx"],
        "combined_ctx": row["combined_ctx"],
        "highscores": highscores,
        "user_agg": user_agg,
        "combined_agg": combined_agg,
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
