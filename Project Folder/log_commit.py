"""Transactional log insert + aggregation writes."""
from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from pymongo import ReplaceOne
from pymongo.collection import Collection

from commit_context_local import build_commit_context
from commit_pipeline import COMBINED_BUCKET_PIPELINE, USER_BUCKET_PIPELINE
from commit_plan import build_commit_plan
from commit_prefetch import prefetch_commit_context
from commit_transaction import abort_commit, begin_commit, finalize_commit


def commit_log(
    collection: Collection,
    aggregations: Collection,
    client,
    *,
    name: str,
    user: str,
    description: str,
    elapsed_time: int,
    ms_since_local_start: int,
) -> tuple[datetime, list[dict]]:
    """Full commit path for tests: prefetch + plan + pipeline writes in one transaction."""
    new_id = ObjectId()
    elapsed_time = int(elapsed_time)

    def callback(session):
        prefetch = prefetch_commit_context(
            collection,
            user,
            ms_since_local_start,
            log_id=new_id,
            session=session,
        )
        plan = build_commit_plan(
            prefetch, user, elapsed_time, aggregations=aggregations
        )
        base_let = {
            "logId": new_id,
            "elapsed": elapsed_time,
            "logUser": user,
        }
        user_ctx, combined_ctx = build_commit_context(prefetch)

        collection.insert_one(
            {
                "_id": new_id,
                "name": name,
                "user": user,
                "description": description,
                "elapsed_time": elapsed_time,
                "timestamp": plan.log_ts,
            },
            session=session,
        )
        aggregations.update_one(
            {"_id": user},
            USER_BUCKET_PIPELINE,
            let={**base_let, **user_ctx},
            upsert=True,
            session=session,
        )
        aggregations.update_one(
            {"_id": "Combined"},
            COMBINED_BUCKET_PIPELINE,
            let={"elapsed": elapsed_time, **combined_ctx},
            upsert=True,
            session=session,
        )
        aggregations.replace_one(
            {"_id": "Highscores"},
            plan.highscores,
            upsert=True,
            session=session,
        )
        return plan.log_ts, plan.broken_records

    with client.start_session() as session:
        return session.with_transaction(callback)


def commit_log_via_plan(
    collection: Collection,
    aggregations: Collection,
    client,
    *,
    name: str,
    user: str,
    description: str,
    log_ts: datetime,
    elapsed_time: int,
) -> tuple[datetime, list[dict]]:
    """Begin + finalize in one flow (ReplaceOne path, for parity tests)."""
    open_commit = begin_commit(
        client, collection, aggregations, user, log_ts, elapsed_time
    )
    try:
        return finalize_commit(
            open_commit, name=name, description=description
        )
    except Exception:
        abort_commit(open_commit)
        raise
