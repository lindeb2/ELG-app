"""Integration smoke test and parity checks for the production commit path."""
from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import path_setup  # noqa: F401, E402

from commit_plan import (
    build_agg_update_ops,
    build_commit_plan,
    project_agg_from_slice,
)
from commit_prefetch import highscores_slice_to_doc, prefetch_for_log_ts, slice_to_agg_doc
from commit_transaction import commit_log
from highscore_commit import build_highscore_update_ops
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


def _set_nested(doc: dict, path: str, value) -> None:
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _inc_nested(doc: dict, path: str, value: int) -> None:
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = int(cur.get(parts[-1]) or 0) + int(value)


def apply_agg_update(base_doc: dict | None, update: dict) -> dict:
    doc = copy.deepcopy(base_doc or {})
    for path, value in (update.get("$set") or {}).items():
        if path == "_id":
            continue
        _set_nested(doc, path, value)
    for path, value in (update.get("$inc") or {}).items():
        _inc_nested(doc, path, value)
    return doc


def _build_expected_docs(prefetch: dict, user: str, elapsed: int) -> tuple[dict, dict, dict]:
    projected_user = project_agg_from_slice(
        prefetch["user_agg_slice"], prefetch["user_ctx"], elapsed
    )
    projected_combined = project_agg_from_slice(
        prefetch["combined_agg_slice"], prefetch["combined_ctx"], elapsed
    )
    highscores = highscores_slice_to_doc(user, prefetch["highscores_slice"])
    build_highscore_update_ops(
        user,
        prefetch["logTs"],
        prefetch["user_ctx"],
        prefetch["combined_ctx"],
        highscores=highscores,
        user_agg=projected_user,
        combined_agg=projected_combined,
    )
    return projected_user, projected_combined, highscores


def run_agg_ops_parity(user: str, log_ts: datetime, elapsed: int) -> bool:
    prefetch = prefetch_for_log_ts(collection, user, log_ts)
    ctx = prefetch["user_ctx"]
    slice_ = prefetch["user_agg_slice"]

    projected = project_agg_from_slice(slice_, ctx, elapsed)
    ops = build_agg_update_ops(ctx, elapsed, slice_)
    ops = {k: v for k, v in ops.items() if k != "$setOnInsert"}
    base = slice_to_agg_doc(slice_, ctx)
    applied = apply_agg_update(base, ops)

    if applied != projected:
        print("[FAIL] agg ops parity: applied update != projected doc")
        return False
    print("[PASS] agg ops parity")
    return True


def run_plan_parity(user: str, log_ts: datetime, elapsed: int) -> bool:
    prefetch = prefetch_for_log_ts(collection, user, log_ts)
    plan = build_commit_plan(prefetch, user, elapsed)
    expected_user, expected_combined, expected_highscores = _build_expected_docs(
        prefetch, user, elapsed
    )

    user_base = slice_to_agg_doc(prefetch["user_agg_slice"], prefetch["user_ctx"])
    user_base["streaks"] = prefetch["user_agg_slice"].get("streaks") or {}
    combined_base = slice_to_agg_doc(
        prefetch["combined_agg_slice"], prefetch["combined_ctx"]
    )
    combined_base["streaks"] = prefetch["combined_agg_slice"].get("streaks") or {}

    user_update = {
        k: v
        for k, v in plan.user_agg_update.items()
        if k != "$setOnInsert"
    }
    combined_update = {
        k: v
        for k, v in plan.combined_agg_update.items()
        if k != "$setOnInsert"
    }
    applied_user = apply_agg_update(user_base, user_update)
    applied_combined = apply_agg_update(combined_base, combined_update)

    high_base = highscores_slice_to_doc(user, prefetch["highscores_slice"])
    high_update = {
        k: v
        for k, v in plan.highscore_update.items()
        if k != "$setOnInsert"
    }
    high_applied = apply_agg_update(high_base, high_update)

    ok = True
    if applied_user != expected_user:
        print("[FAIL] plan parity: user agg mismatch")
        ok = False
    if applied_combined != expected_combined:
        print("[FAIL] plan parity: combined agg mismatch")
        ok = False
    for scope_key in (user, "Global", "Combined"):
        if high_applied.get(scope_key) != expected_highscores.get(scope_key):
            print(f"[FAIL] plan parity: highscores.{scope_key} mismatch")
            ok = False
    if ok:
        print("[PASS] plan parity")
    return ok


def run_slice_size_check(user: str, log_ts: datetime) -> bool:
    prefetch = prefetch_for_log_ts(collection, user, log_ts)
    full_user = aggregations.find_one({"_id": user}) or {}
    ctx = prefetch["user_ctx"]
    slice_ = prefetch["user_agg_slice"]
    full_size = len(bson.BSON.encode(full_user)) if full_user else 0
    slice_size = len(bson.BSON.encode({"years": slice_to_agg_doc(slice_, ctx)["years"], "streaks": slice_.get("streaks") or {}}))
    print(f"[INFO] user agg full={full_size}B slice={slice_size}B")
    if full_user and slice_size > full_size:
        print("[FAIL] slice larger than full doc")
        return False
    print("[PASS] slice size check")
    return True


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
    parser.add_argument("--parity-only", action="store_true", help="Run parity checks only")
    args = parser.parse_args()
    test_user = config_user if args.config_user else args.user
    log_ts = datetime.now(timezone.utc)

    parity_ok = (
        run_agg_ops_parity(test_user, log_ts, args.elapsed)
        and run_plan_parity(test_user, log_ts, args.elapsed)
        and run_slice_size_check(test_user, log_ts)
    )
    if args.parity_only:
        return 0 if parity_ok else 1

    smoke_ok = run_smoke(test_user, elapsed=args.elapsed, runs=args.runs)
    return 0 if parity_ok and smoke_ok else 1


if __name__ == "__main__":
    sys.exit(main())
