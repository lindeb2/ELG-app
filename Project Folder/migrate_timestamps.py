"""Backfill BSON timestamp on Timetable logs and ensure index (ELG-Dev by default)."""
import argparse
import json
import os

from datetime import datetime, timezone
from pymongo import MongoClient, UpdateOne

MONGO_URI = (
    "mongodb+srv://johan:baLlbeTtertRacer@elg-timetable.txhpj.mongodb.net/"
    "?retryWrites=true&w=majority&appName=ELG-timetable"
)

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
config_path = os.path.join(project_root, "config.json")


def legacy_timestamp(doc: dict) -> datetime | None:
    """Derive timestamp from pre-SSOT log fields (migration only)."""
    ts = doc.get("timestamp")
    if ts is not None:
        return ts
    start_unix = doc.get("start_time")
    if isinstance(start_unix, int):
        return datetime.fromtimestamp(start_unix, tz=timezone.utc).replace(tzinfo=None)
    try:
        return datetime(
            int(doc["start_year"]),
            int(doc["start_month"]),
            int(doc["start_day"]),
            *map(int, doc.get("start_time", "00:00:00").split(":")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def resolve_db_name(cli_name: str | None) -> str:
    if cli_name:
        return cli_name
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f).get("database", "ELG-Dev")
    except FileNotFoundError:
        return "ELG-Dev"


def main():
    parser = argparse.ArgumentParser(description="Backfill timestamp field on Timetable collection.")
    parser.add_argument("--database", type=str, help="Database name (default from config or ELG-Dev).")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write.")
    args = parser.parse_args()

    db_name = resolve_db_name(args.database)
    client = MongoClient(MONGO_URI)
    db = client[db_name]
    collection = db["Timetable"]

    ops = []
    unmigratable = 0
    already_ok = 0

    for doc in collection.find({}):
        if doc.get("timestamp") is not None:
            already_ok += 1
            continue
        ts = legacy_timestamp(doc)
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
