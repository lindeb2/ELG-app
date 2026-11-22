"""Watches the shared Meeting Mike lock in MongoDB to gate the Timetable timer."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from period_model import as_utc

LOCK_DOC_ID = "Meeting Mike"
LOCK_STALE_SECONDS = 3 * 60
EXPIRY_CHECK_INTERVAL_SECONDS = 15.0

_WATCH_PIPELINE = [
    {"$match": {"documentKey._id": LOCK_DOC_ID}},
]


def is_lock_doc_active(
    doc: dict | None,
    *,
    now_utc: datetime,
    stale_seconds: float = LOCK_STALE_SECONDS,
) -> bool:
    """Return True if a Meeting Mike lock document represents a live meeting."""
    if not isinstance(doc, dict) or doc.get("recording") is not True:
        return False
    anchor = doc.get("heartbeat_at") or doc.get("started_at")
    if not isinstance(anchor, datetime):
        return False
    age = (as_utc(now_utc) - as_utc(anchor)).total_seconds()
    return age <= float(stale_seconds)


class MeetingLockWatcher:
    """Tracks whether a weekly status meeting is currently active across ELG."""

    def __init__(
        self,
        status_meeting_collection,
        *,
        stale_seconds: float = LOCK_STALE_SECONDS,
        expiry_check_interval: float = EXPIRY_CHECK_INTERVAL_SECONDS,
    ) -> None:
        self._collection = status_meeting_collection
        self._stale_seconds = float(stale_seconds)
        self._expiry_check_interval = float(expiry_check_interval)

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._listeners: list[Callable[[bool], None]] = []
        self._last_doc: dict | None = None
        self._clock_offset_seconds = 0.0  # server_utc - local_utc
        self._active = False
        self._started = False

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def add_listener(self, callback: Callable[[bool], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[bool], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def start(self) -> None:
        with self._lock:
            if self._started or self._stop.is_set():
                return
            self._started = True
        threading.Thread(
            target=self._watch_loop,
            name="meeting-lock-watch",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._expiry_loop,
            name="meeting-lock-expiry",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self._stop.set()

    # --- state evaluation ------------------------------------------------
    def _estimated_server_now(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=self._clock_offset_seconds)

    def _apply_doc(self, doc: dict | None, *, server_time: datetime | None = None) -> None:
        changed_to: bool | None = None
        with self._lock:
            if isinstance(server_time, datetime):
                local_now = datetime.now(timezone.utc)
                self._clock_offset_seconds = (as_utc(server_time) - local_now).total_seconds()
            self._last_doc = dict(doc) if isinstance(doc, dict) else None
            now_utc = self._estimated_server_now()
            new_active = is_lock_doc_active(
                self._last_doc,
                now_utc=now_utc,
                stale_seconds=self._stale_seconds,
            )
            if new_active != self._active:
                self._active = new_active
                changed_to = new_active
        if changed_to is not None:
            self._notify(changed_to)

    def _reevaluate_expiry(self) -> None:
        changed_to: bool | None = None
        with self._lock:
            if not self._active:
                return
            now_utc = self._estimated_server_now()
            still_active = is_lock_doc_active(
                self._last_doc,
                now_utc=now_utc,
                stale_seconds=self._stale_seconds,
            )
            if not still_active:
                self._active = False
                changed_to = False
        if changed_to is not None:
            self._notify(changed_to)

    def _notify(self, active: bool) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(active)
            except Exception:
                pass

    # --- background loops ------------------------------------------------
    def _refresh_from_db(self) -> None:
        rows = list(
            self._collection.aggregate(
                [
                    {"$match": {"_id": LOCK_DOC_ID}},
                    {
                        "$project": {
                            "recording": 1,
                            "started_at": 1,
                            "heartbeat_at": 1,
                            "host_username": 1,
                            "meeting_id": 1,
                            "server_time": "$$NOW",
                        }
                    },
                ]
            )
        )
        if rows:
            doc = rows[0]
            server_time = doc.get("server_time")
            self._apply_doc(doc, server_time=server_time)
        else:
            self._apply_doc(None)

    def _watch_loop(self) -> None:
        resume_token = None
        while not self._stop.is_set():
            try:
                self._refresh_from_db()
                with self._collection.watch(
                    _WATCH_PIPELINE,
                    resume_after=resume_token,
                    full_document="updateLookup",
                ) as stream:
                    for change in stream:
                        if self._stop.is_set():
                            return
                        resume_token = stream.resume_token
                        cluster_time = change.get("clusterTime")
                        server_time = (
                            cluster_time.as_datetime()
                            if hasattr(cluster_time, "as_datetime")
                            else None
                        )
                        op_type = change.get("operationType")
                        if op_type == "delete":
                            self._apply_doc(None, server_time=server_time)
                        else:
                            self._apply_doc(
                                change.get("fullDocument"),
                                server_time=server_time,
                            )
            except Exception:
                if self._stop.is_set():
                    return
                # Invalidate resume token on error so reconnect does a fresh read.
                resume_token = None
                self._stop.wait(2.0)

    def _expiry_loop(self) -> None:
        while not self._stop.wait(self._expiry_check_interval):
            self._reevaluate_expiry()
