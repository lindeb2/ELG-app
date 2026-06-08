"""Admin-only: rebuild aggregations, streaks, and highscores from raw Timetable logs."""
from aggregation_rebuild import rebuild_all_aggregations
from highscore_commit import rebuild_highscores_from_logs
from streak_recalculate import streaks_from_log_entries
from timetable_db import aggregations, collection


def _reset_aggregation_docs() -> None:
    for user_id in collection.distinct("user"):
        if user_id:
            aggregations.update_one({"_id": user_id}, {"$set": {"years": {}}}, upsert=True)
    aggregations.update_one({"_id": "Combined"}, {"$set": {"years": {}}}, upsert=True)


def recalculate_all_aggregations() -> None:
    """Recalculate all aggregations from scratch."""
    _reset_aggregation_docs()
    rebuild_all_aggregations()


def recalculate_all_streaks() -> None:
    """Rebuild lifetime streak counters on user and Combined aggregation docs."""
    for entry_user in collection.distinct("user"):
        if not entry_user:
            continue
        entries = list(collection.find({"user": entry_user}, {"timestamp": 1}))
        streaks = streaks_from_log_entries(entries)
        aggregations.update_one({"_id": entry_user}, {"$set": {"streaks": streaks}}, upsert=True)

    combined_entries = list(collection.find({}, {"timestamp": 1}))
    combined_streaks = streaks_from_log_entries(combined_entries)
    aggregations.update_one({"_id": "Combined"}, {"$set": {"streaks": combined_streaks}}, upsert=True)


def recalculate_all_highscores() -> None:
    """Recalculate all highscores from scratch using the raw data in the collection."""
    aggregations.delete_one({"_id": "Highscores"})
    rebuild_highscores_from_logs(collection, aggregations)


def recalculate_all() -> None:
    recalculate_all_aggregations()
    recalculate_all_streaks()
    recalculate_all_highscores()


if __name__ == "__main__":
    print("Starting recalculation")
    recalculate_all()
    print("Recalculation complete!")
