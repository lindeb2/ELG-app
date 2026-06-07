"""Transactional log insert + DB-side incremental aggregation pipelines."""
from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.collection import Collection

from commit_pipeline import (
    COMBINED_BUCKET_PIPELINE,
    COMBINED_CONTEXT_PIPELINE,
    USER_BUCKET_PIPELINE,
    USER_CONTEXT_PIPELINE,
)
from highscore_commit import update_highscores


def _fetch_context(collection: Collection, pipeline: list, let_vars: dict, session) -> dict:
    rows = list(collection.aggregate(pipeline, let=let_vars, session=session))
    if not rows:
        raise RuntimeError("commit_log: context pipeline returned no rows")
    return rows[0]


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

        user_ctx = _fetch_context(
            collection,
            USER_CONTEXT_PIPELINE,
            base_let,
            session,
        )
        aggregations.update_one(
            {"_id": user},
            USER_BUCKET_PIPELINE,
            let={**base_let, **user_ctx},
            upsert=True,
            session=session,
        )

        combined_ctx = _fetch_context(
            collection,
            COMBINED_CONTEXT_PIPELINE,
            {"logId": new_id, "elapsed": elapsed_time},
            session,
        )
        aggregations.update_one(
            {"_id": "Combined"},
            COMBINED_BUCKET_PIPELINE,
            let={"elapsed": elapsed_time, **combined_ctx},
            upsert=True,
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
