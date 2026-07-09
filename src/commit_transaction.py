"""Open commit transaction at Done; write at Log Entry; retry on conflict."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from bson import ObjectId
from pymongo import UpdateOne
from pymongo.collection import Collection
from pymongo.errors import OperationFailure, PyMongoError

from commit_plan import CommitPlan, build_plan_from_log_ts

_MAX_COMMIT_RETRIES = 3

# MongoDB default transactionLifetimeLimitSeconds is 60; stay just under it.
TXN_LIFETIME_MS = 59_000
# Start the successor transaction this many ms before the current one expires.
TXN_REFRESH_LEAD_MS = 12_000
TXN_REFRESH_INTERVAL_MS = TXN_LIFETIME_MS - TXN_REFRESH_LEAD_MS


@dataclass
class OpenCommit:
    session: object
    plan: CommitPlan
    prefetch_digest: str
    user: str
    log_ts: datetime
    elapsed_time: int
    collection: Collection
    aggregations: Collection


def _is_retryable_transaction_error(exc: Exception) -> bool:
    if isinstance(exc, OperationFailure) and exc.code == 112:
        return True
    if isinstance(exc, PyMongoError) and hasattr(exc, "has_error_label"):
        return (
            exc.has_error_label("TransientTransactionError")
            or exc.has_error_label("UnknownTransactionCommitResult")
        )
    return False


def begin_commit(
    client,
    collection: Collection,
    aggregations: Collection,
    user: str,
    log_ts: datetime,
    elapsed_time: int,
    *,
    log_id: ObjectId | None = None,
) -> OpenCommit:
    """Start a transaction and prefetch (call at Done)."""
    log_id = log_id or ObjectId()
    session = client.start_session()
    session.start_transaction()
    try:
        plan, digest = build_plan_from_log_ts(
            collection,
            user,
            log_ts,
            int(elapsed_time),
            session=session,
            log_id=log_id,
        )
    except Exception:
        session.abort_transaction()
        session.end_session()
        raise
    return OpenCommit(
        session=session,
        plan=plan,
        prefetch_digest=digest,
        user=user,
        log_ts=log_ts,
        elapsed_time=int(elapsed_time),
        collection=collection,
        aggregations=aggregations,
    )


def _write_plan(
    open_commit: OpenCommit,
    *,
    name: str,
    description: str,
) -> tuple[datetime, list[dict]]:
    plan = open_commit.plan
    user = open_commit.user
    open_commit.collection.insert_one(
        {
            "_id": plan.log_id,
            "name": name,
            "user": user,
            "description": description,
            "elapsed_time": plan.elapsed_time,
            "timestamp": plan.log_ts,
        },
        session=open_commit.session,
    )
    open_commit.aggregations.bulk_write(
        [
            UpdateOne({"_id": user}, plan.user_agg_update, upsert=True),
            UpdateOne({"_id": "Combined"}, plan.combined_agg_update, upsert=True),
            UpdateOne({"_id": "Highscores"}, plan.highscore_update, upsert=True),
        ],
        session=open_commit.session,
    )
    return plan.log_ts, plan.broken_records


def finalize_commit(
    open_commit: OpenCommit,
    *,
    name: str,
    description: str,
) -> tuple[datetime, list[dict]]:
    """Write precomputed plan and commit (call at Log Entry). Retries on conflict."""
    session = open_commit.session
    last_error: Exception | None = None

    for attempt in range(_MAX_COMMIT_RETRIES):
        try:
            result = _write_plan(
                open_commit, name=name, description=description
            )
            session.commit_transaction()
            session.end_session()
            return result
        except Exception as exc:
            last_error = exc
            if not _is_retryable_transaction_error(exc) or attempt >= _MAX_COMMIT_RETRIES - 1:
                try:
                    if session.in_transaction:
                        session.abort_transaction()
                finally:
                    session.end_session()
                raise

            session.abort_transaction()
            session.start_transaction()
            new_plan, new_digest = build_plan_from_log_ts(
                open_commit.collection,
                open_commit.user,
                open_commit.log_ts,
                open_commit.elapsed_time,
                session=session,
                log_id=open_commit.plan.log_id,
            )
            if new_digest != open_commit.prefetch_digest:
                open_commit.plan = new_plan
                open_commit.prefetch_digest = new_digest

    assert last_error is not None
    raise last_error


def refresh_commit(
    client,
    current: OpenCommit,
    log_ts: datetime,
    elapsed_time: int,
) -> tuple[OpenCommit, bool]:
    """Open a successor transaction, then swap and end the current one.

    Returns (new_open, digest_changed). The old session is aborted only after
    the successor prefetch succeeds so the overlay keeps a valid plan.
    """
    new_open = begin_commit(
        client,
        current.collection,
        current.aggregations,
        current.user,
        log_ts,
        elapsed_time,
        log_id=current.plan.log_id,
    )
    digest_changed = new_open.prefetch_digest != current.prefetch_digest
    abort_commit(current)
    return new_open, digest_changed


def abort_commit(open_commit: OpenCommit | None) -> None:
    if open_commit is None:
        return
    session = open_commit.session
    try:
        if session.in_transaction:
            session.abort_transaction()
    finally:
        session.end_session()


def commit_log(
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
    """One-shot begin + finalize; for scripts and integration tests."""
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


class CommitTransactionManager:
    """Single worker thread owns the open ClientSession (not thread-safe)."""

    def __init__(
        self,
        collection: Collection,
        aggregations: Collection,
        client,
        user: str,
    ) -> None:
        self._collection = collection
        self._aggregations = aggregations
        self._client = client
        self._user = user
        self._open: OpenCommit | None = None
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="commit_transaction", daemon=True
        )
        self._thread.start()

    @property
    def has_open_commit(self) -> bool:
        return self._open is not None

    def set_user(self, user: str) -> None:
        self._user = user

    def begin_async(
        self,
        log_ts: datetime,
        elapsed_time: int,
        on_ready: Callable[[CommitPlan], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self._queue.put(("begin", log_ts, int(elapsed_time), on_ready, on_error))

    def refresh_async(
        self,
        log_ts: datetime,
        elapsed_time: int,
        on_ready: Callable[[CommitPlan, bool], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self._queue.put(("refresh", log_ts, int(elapsed_time), on_ready, on_error))

    def finalize_async(
        self,
        name: str,
        description: str,
        on_success: Callable[[datetime, list], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self._queue.put(("finalize", name, description, on_success, on_error))

    def abort_async(self) -> None:
        self._queue.put(("abort",))

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            op = item[0]
            try:
                if op == "abort":
                    abort_commit(self._open)
                    self._open = None
                elif op == "begin":
                    _, log_ts, elapsed, on_ready, on_error = item
                    try:
                        abort_commit(self._open)
                        self._open = begin_commit(
                            self._client,
                            self._collection,
                            self._aggregations,
                            self._user,
                            log_ts,
                            elapsed,
                        )
                        on_ready(self._open.plan)
                    except Exception as exc:
                        self._open = None
                        on_error(exc)
                elif op == "refresh":
                    _, log_ts, elapsed, on_ready, on_error = item
                    try:
                        if self._open is None:
                            raise RuntimeError("no open commit transaction")
                        self._open, digest_changed = refresh_commit(
                            self._client,
                            self._open,
                            log_ts,
                            elapsed,
                        )
                        on_ready(self._open.plan, digest_changed)
                    except Exception as exc:
                        on_error(exc)
                elif op == "finalize":
                    _, name, description, on_success, on_error = item
                    try:
                        if self._open is None:
                            raise RuntimeError("no open commit transaction")
                        result = finalize_commit(
                            self._open,
                            name=name,
                            description=description,
                        )
                        self._open = None
                        on_success(*result)
                    except Exception as exc:
                        abort_commit(self._open)
                        self._open = None
                        on_error(exc)
            except Exception:
                pass
            finally:
                self._queue.task_done()
