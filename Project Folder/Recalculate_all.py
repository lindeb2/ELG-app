"""Admin-only: rebuild aggregations, streaks, and highscores from raw Timetable logs."""
from aggregation_rebuild import aggregate
from highscore_commit import rebuild_highscores_from_logs
from period_model import calendar_bounds, period_keys
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

    seen_days: set[tuple[str, str, str]] = set()
    seen_months: set[tuple[str, str]] = set()
    seen_years: set[str] = set()
    seen_weeks: set[tuple[str, str]] = set()

    for entry in collection.find({}):
        keys = period_keys(entry["timestamp"])
        seen_days.add((keys.year, keys.month, keys.day))
        seen_months.add((keys.year, keys.month))
        seen_years.add(keys.year)
        seen_weeks.add((keys.iso_week_year, keys.iso_week))

    for year in sorted(seen_years):
        aggregate("year", year=year)

    for year, month in sorted(seen_months):
        aggregate("month", year=year, month=month)

    for year, month, day in sorted(seen_days):
        aggregate("day", year=year, month=month, day=day)
        day_start, _ = calendar_bounds("day", year=int(year), month=int(month), day=int(day))
        day_keys = period_keys(day_start)
        aggregate(
            "weekday",
            year=day_keys.year,
            month=day_keys.month,
            day=day_keys.day,
            week_year=day_keys.iso_week_year,
            week=day_keys.iso_week,
            weekday=day_keys.weekday,
        )

    for week_year, week in sorted(seen_weeks):
        aggregate("week", week_year=week_year, week=week)


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
