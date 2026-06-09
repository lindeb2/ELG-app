"""Integration smoke test for the production commit path."""
from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import path_setup  # noqa: F401, E402

from commit_transaction import commit_log
from timetable_db import aggregations, client, collection, user as config_user

TEST_USER = "__parity_test_user__"
TEST_NAME = "commit test"
TEST_DESC = "integration smoke test"


def _snapshot(user: str) -> dict:
    return {
        "user_agg": copy.deepcopy(aggregations.find_one({"_id": user})),
        "combined_agg": copy.deepcopy(aggregations.find_one({"_id": "Combined"})),
        "highscores": copy.deepcopy(aggregations.find_one({"_id": "Highscores"})),
        "log_count": collection.count_documents({"user": user}),
    }


def _restore(snapshot: dict, user: str) -> None:
    collection.delete_many({"user": user})
    for doc_id, key in (
        (user, "user_agg"),
        ("Combined", "combined_agg"),
        ("Highscores", "highscores"),
    ):
        doc = snapshot[key]
        if doc is None:
            aggregations.delete_one({"_id": doc_id})
        else:
            aggregations.replace_one({"_id": doc_id}, doc, upsert=True)


def run_smoke(user: str | None = None, elapsed: int = 120, runs: int = 2) -> bool:
    user = user or TEST_USER
    baseline = _snapshot(user)
    log_ts = datetime.now(timezone.utc)

    try:
        for i in range(runs):
            _restore(baseline, user)
            before = _snapshot(user)
            commit_log(
                collection,
                aggregations,
                client,
                name=TEST_NAME,
                user=user,
                description=TEST_DESC,
                log_ts=log_ts + timedelta(seconds=i),
                elapsed_time=elapsed + i,
            )
            after = _snapshot(user)

            if after["log_count"] != before["log_count"] + 1:
                print(f"[FAIL] run {i}: expected log_count +1")
                return False
            if after["user_agg"] is None or after["combined_agg"] is None:
                print(f"[FAIL] run {i}: missing aggregation docs")
                return False
            if after["highscores"] is None:
                print(f"[FAIL] run {i}: missing highscores doc")
                return False
            print(f"[PASS] run {i}: commit succeeded.")

        print(f"All {runs} commit(s) succeeded.")
        return True
    finally:
        _restore(baseline, user)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for log commit")
    parser.add_argument("--user", default=TEST_USER, help="Test user id in aggregations")
    parser.add_argument("--elapsed", type=int, default=120, help="Elapsed seconds per commit")
    parser.add_argument("--runs", type=int, default=2, help="Number of isolated commits")
    parser.add_argument("--config-user", action="store_true", help="Use user from config.json")
    args = parser.parse_args()
    test_user = config_user if args.config_user else args.user
    return 0 if run_smoke(test_user, elapsed=args.elapsed, runs=args.runs) else 1


if __name__ == "__main__":
    sys.exit(main())
