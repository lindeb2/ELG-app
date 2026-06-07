"""Backfill BSON timestamp on Timetable logs and ensure index (ELG-Dev by default)."""
import argparse
import json
import os

from pymongo import MongoClient, UpdateOne

from period_model import legacy_log_timestamp

MONGO_URI = (
    "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/"
    "?retryWrites=true&w=majority&appName=ELG-timetable"
)

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
config_path = os.path.join(project_root, "config.json")


def resolve_db_name(cli_name: str | None) -> str:
    if cli_name:
        return cli_name
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f).get("database", "ELG-Database")
    except FileNotFoundError:
        return "ELG-Database"


def main():
    parser = argparse.ArgumentParser(description="Backfill timestamp field on Timetable collection.")
    parser.add_argument("--database", type=str, help="Database name (default from config or ELG-Database).")
    parser.add_argument(
        "--rebuild-from-legacy",
        action="store_true",
        help="Recompute timestamp from start_* even when timestamp already exists.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write.")
    args = parser.parse_args()

    db_name = resolve_db_name(args.database)
    client = MongoClient(MONGO_URI)
    collection = client[db_name]["Timetable"]

    ops = []
    unmigratable = 0
    already_ok = 0

    for doc in collection.find({}):
        has_legacy = all(key in doc for key in ("start_year", "start_month", "start_day"))
        if doc.get("timestamp") is not None and not (args.rebuild_from_legacy and has_legacy):
            already_ok += 1
            continue
        ts = legacy_log_timestamp(doc)
        if ts is None:
            unmigratable += 1
            continue
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"timestamp": ts}}))

    print(f"Database: {db_name}")
    print(f"Already have timestamp: {already_ok}")
    print(f"To backfill: {len(ops)}")
    print(f"Unmigratable: {unmigratable}")

    if args.dry_run:
        return

    if ops:
        result = collection.bulk_write(ops, ordered=False)
        print(f"Updated: {result.modified_count}")

    index_name = collection.create_index([("timestamp", 1)])
    print(f"Index on timestamp: {index_name}")
    print("Run Recalculate_all.py against this database when backfill completes.")


if __name__ == "__main__":
    main()
