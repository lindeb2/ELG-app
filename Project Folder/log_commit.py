"""Transactional log insert + DB-side incremental aggregation pipelines."""
from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.collection import Collection

from commit_pipeline import (
    COMBINED_BUCKET_PIPELINE,
    COMMIT_CONTEXT_PIPELINE,
    USER_BUCKET_PIPELINE,
)
from highscore_commit import update_highscores


def _fetch_commit_context(
    collection: Collection,
    let_vars: dict,
    session,
) -> tuple[dict, dict]:
    rows = list(
        collection.aggregate(
            COMMIT_CONTEXT_PIPELINE,
            let=let_vars,
            session=session,
        )
    )
    if not rows or rows[0].get("user") is None or rows[0].get("combined") is None:
        raise RuntimeError("commit_log: context pipeline returned incomplete rows")
    return rows[0]["user"], rows[0]["combined"]


def _apply_bucket_updates(
    aggregations: Collection,
    *,
    user: str,
    base_let: dict,
    user_ctx: dict,
    combined_ctx: dict,
    elapsed_time: int,
    session,
) -> None:
    """Apply user + combined bucket pipeline updates (separate let vars per doc)."""
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
    """Insert log, apply aggregation updates, and refresh highscores in one transaction."""
    new_id = ObjectId()
    elapsed_time = int(elapsed_time)

    def callback(session):
        doc = collection.find_one_and_update(
            {"_id": new_id},
            [{
                "$set": {
                    "name": name,
                    "user": user,
                    "description": description,
                    "elapsed_time": elapsed_time,
                    "timestamp": {"$subtract": ["$$NOW", ms_since_local_start]},
                }
            }],
            upsert=True,
            return_document=ReturnDocument.AFTER,
            projection={"timestamp": 1},
            session=session,
        )
        timestamp = doc["timestamp"]
        base_let = {
            "logId": new_id,
            "elapsed": elapsed_time,
            "logUser": user,
        }

        user_ctx, combined_ctx = _fetch_commit_context(
            collection,
            base_let,
            session,
        )
        _apply_bucket_updates(
            aggregations,
            user=user,
            base_let=base_let,
            user_ctx=user_ctx,
            combined_ctx=combined_ctx,
            elapsed_time=elapsed_time,
            session=session,
        )
        broken_records = update_highscores(
            aggregations,
            user,
            timestamp,
            session=session,
        )
        return timestamp, broken_records

    with client.start_session() as session:
        return session.with_transaction(callback)
