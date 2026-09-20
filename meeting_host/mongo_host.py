"""Mongo access for Meeting Host: identity, meeting context and the lock."""
from __future__ import annotations

import threading
from datetime import timedelta

from meeting_host import config
from meeting_host.host_secrets import get_mongodb_uri
from meeting_host.period import utc_now

NOTIFICATION_PREFS_DOC_ID = "Notification Preferences"

# Imported lazily so the offline pipeline logic does not require pymongo.
_client = None
_client_lock = threading.Lock()


def client():
    global _client
    with _client_lock:
        if _client is None:
            from pymongo import MongoClient
            from pymongo.server_api import ServerApi

            _client = MongoClient(
                get_mongodb_uri(),
                server_api=ServerApi("1"),
                serverSelectionTimeoutMS=15_000,
            )
        return _client


def _db():
    return client()[config.MONGO_DB_NAME]


def status_meeting():
    return _db()[config.STATUS_MEETING_COLLECTION]


def timetable():
    return _db()[config.TIMETABLE_COLLECTION]


def aggregations():
    return _db()[config.AGGREGATIONS_COLLECTION]


def close() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
            _client = None


def fetch_notification_prefs_map() -> dict:
    """Whole `data` map: {elg_username: {discord_user_id, discordname, ...}}."""
    doc = status_meeting().find_one({"_id": NOTIFICATION_PREFS_DOC_ID}) or {}
    data = doc.get("data")
    return data if isinstance(data, dict) else {}


def acquire_lock(*, host_username: str, channel_id: str, meeting_dir: str) -> tuple[bool, str]:
    """Take the recording lock unless a fresh one is held elsewhere.

    Returns (acquired, holder). A lock older than LOCK_STALE_SECONDS is
    treated as a crashed host and stolen.
    """
    from pymongo import ReturnDocument

    now = utc_now()
    stale_before = now - timedelta(seconds=config.LOCK_STALE_SECONDS)

    doc = status_meeting().find_one_and_update(
        {
            "_id": config.LOCK_DOC_ID,
            "$or": [
                {"recording": {"$ne": True}},
                {"started_at": None},
                {"started_at": {"$lt": stale_before}},
            ],
        },
        {
            "$set": {
                "recording": True,
                "host_username": host_username,
                "started_at": now,
                "channel_id": str(channel_id),
                "meeting_dir": meeting_dir,
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if doc is not None:
        return True, host_username

    held = status_meeting().find_one({"_id": config.LOCK_DOC_ID}) or {}
    return False, str(held.get("host_username") or "another machine")


def release_lock(*, host_username: str) -> None:
    try:
        status_meeting().update_one(
            {"_id": config.LOCK_DOC_ID},
            {"$set": {"recording": False, "released_at": utc_now(), "host_username": host_username}},
            upsert=True,
        )
    except Exception:
        pass


def fetch_meeting_week() -> tuple[str, str]:
    """ISO (year, week) the meeting app is currently showing."""
    from meeting_host.period import iso_week_of, utc_now as _now

    doc = status_meeting().find_one({"_id": "State"}, projection={"week": 1}) or {}
    week_start = doc.get("week")
    if week_start is not None:
        return iso_week_of(week_start)
    return iso_week_of(_now())
