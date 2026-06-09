"""Commit plan built from transactional prefetch at Done-click."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime

from bson import ObjectId
from pymongo.collection import Collection

from commit_context_local import project_agg_after_commit
from highscore_commit import update_highscores


@dataclass
class CommitPlan:
    log_id: ObjectId
    log_ts: datetime
    elapsed_time: int
    user_agg: dict
    combined_agg: dict
    highscores: dict
    broken_records: list


def _agg_doc_with_id(doc: dict, doc_id) -> dict:
    result = deepcopy(doc)
    result["_id"] = doc_id
    return result


def build_commit_plan(
    prefetch: dict,
    user: str,
    elapsed_time: int,
    *,
    aggregations: Collection | None = None,
) -> CommitPlan:
    """Build a write-ready commit plan from prefetch data."""
    log_id = prefetch.get("logId") or ObjectId()
    log_ts = prefetch["logTs"]
    elapsed_time = int(elapsed_time)
    user_ctx, combined_ctx = prefetch["user_ctx"], prefetch["combined_ctx"]

    projected_user = project_agg_after_commit(
        prefetch["user_agg"], user_ctx, elapsed_time
    )
    projected_combined = project_agg_after_commit(
        prefetch["combined_agg"], combined_ctx, elapsed_time
    )
    highscores = prefetch["highscores"]
    broken_records = update_highscores(
        aggregations,
        user,
        log_ts,
        highscores=highscores,
        user_agg=projected_user,
        combined_agg=projected_combined,
        skip_write=True,
    )

    return CommitPlan(
        log_id=log_id,
        log_ts=log_ts,
        elapsed_time=elapsed_time,
        user_agg=_agg_doc_with_id(projected_user, user),
        combined_agg=_agg_doc_with_id(projected_combined, "Combined"),
        highscores=_agg_doc_with_id(highscores, "Highscores"),
        broken_records=broken_records,
    )
