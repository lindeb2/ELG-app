"""Compare legacy, pipeline, and plan-based commit paths on a test user."""
from __future__ import annotations

import argparse
import copy
import json
import sys

from bson import json_util

from commit_log_legacy import commit_log_legacy
from commit_prefetch import prefetch_commit_context
from log_commit import commit_log, commit_log_via_plan
from timetable_db import aggregations, client, collection, user as config_user

TEST_USER = "__parity_test_user__"
TEST_NAME = "parity"
TEST_DESC = "parity commit"


def _snapshot(user: str) -> dict:
    return {
        "user_agg": copy.deepcopy(aggregations.find_one({"_id": user})),
        "combined_agg": copy.deepcopy(aggregations.find_one({"_id": "Combined"})),
        "highscores": copy.deepcopy(aggregations.find_one({"_id": "Highscores"})),
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


def _strip_volatile(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    return json.loads(json_util.dumps(doc))


def _final_state(user: str) -> dict:
    return {
        "user_agg": _strip_volatile(aggregations.find_one({"_id": user})),
        "combined_agg": _strip_volatile(aggregations.find_one({"_id": "Combined"})),
        "highscores": _strip_volatile(aggregations.find_one({"_id": "Highscores"})),
        "log_count": collection.count_documents({"user": user}),
    }


def _run_pipeline_commit(user: str, elapsed: int, ms_since: int) -> dict:
    commit_log(
        collection,
        aggregations,
        client,
        name=TEST_NAME,
        user=user,
        description=TEST_DESC,
        elapsed_time=elapsed,
        ms_since_local_start=ms_since,
    )
    return _final_state(user)


def _run_plan_commit(user: str, elapsed: int, ms_since: int) -> dict:
    prefetch = prefetch_commit_context(collection, user, ms_since)
    commit_log_via_plan(
        collection,
        aggregations,
        client,
        name=TEST_NAME,
        user=user,
        description=TEST_DESC,
        log_ts=prefetch["logTs"],
        elapsed_time=elapsed,
    )
    return _final_state(user)


def _run_legacy_commit(user: str, elapsed: int, ms_since: int) -> dict:
    commit_log_legacy(
        collection,
        aggregations,
        client,
        name=TEST_NAME,
        user=user,
        description=TEST_DESC,
        elapsed_time=elapsed,
        ms_since_local_start=ms_since,
    )
    return _final_state(user)


def _compare(label_a: str, state_a: dict, label_b: str, state_b: dict, keys) -> bool:
    ok = True
    for key in keys:
        if state_a[key] != state_b[key]:
            print(f"[FAIL] {label_a} vs {label_b}: differs {key}")
            ok = False
    if ok:
        print(f"[PASS] {label_a} matches {label_b} ({', '.join(keys)}).")
    return ok


def run_parity(user: str | None = None, elapsed: int = 120, runs: int = 2) -> bool:
    user = user or TEST_USER
    baseline = _snapshot(user)
    ms_since = 5000
    all_ok = True
    agg_keys = ("user_agg", "combined_agg", "log_count")

    try:
        legacy_state = None
        pipeline_state = None
        plan_state = None

        for i in range(runs):
            _restore(baseline, user)
            legacy_state = _run_legacy_commit(user, elapsed + i, ms_since + i * 1000)

            _restore(baseline, user)
            pipeline_state = _run_pipeline_commit(user, elapsed + i, ms_since + i * 1000)

            _restore(baseline, user)
            plan_state = _run_plan_commit(user, elapsed + i, ms_since + i * 1000)

            all_ok &= _compare(f"legacy run {i}", legacy_state, f"pipeline run {i}", pipeline_state, agg_keys)
            all_ok &= _compare(f"pipeline run {i}", pipeline_state, f"plan run {i}", plan_state, agg_keys + ("highscores",))

        if all_ok:
            print(f"All paths match after {runs} isolated commit(s).")
        return all_ok
    finally:
        _restore(baseline, user)


def main() -> int:
    parser = argparse.ArgumentParser(description="Parity test for log commit paths")
    parser.add_argument("--user", default=TEST_USER, help="Test user id in aggregations")
    parser.add_argument("--elapsed", type=int, default=120, help="Elapsed seconds per commit")
    parser.add_argument("--runs", type=int, default=2, help="Number of isolated commits per path")
    parser.add_argument("--config-user", action="store_true", help="Use user from config.json")
    args = parser.parse_args()
    test_user = config_user if args.config_user else args.user
    ok = run_parity(test_user, elapsed=args.elapsed, runs=args.runs)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
