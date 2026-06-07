"""Admin-only: rebuild aggregations and highscores from raw Timetable logs."""
from datetime import datetime

from period_model import calendar_bounds, period_keys
from streak_recalculate import streaks_from_log_entries
from Timetable import (
    aggregate,
    aggregations,
    collection,
    format_date_str,
    total_days_in_period,
    update_highscore,
    user,
)


def recalculate_all_aggregations():
    """
    Recalculate all aggregations from scratch.
    This is useful if you need to rebuild all summaries.
    """
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
        aggregate("year", user, year=year)

    for year, month in sorted(seen_months):
        aggregate("month", user, year=year, month=month)

    for year, month, day in sorted(seen_days):
        aggregate("day", user, year=year, month=month, day=day)
        day_start, _ = calendar_bounds("day", year=int(year), month=int(month), day=int(day))
        day_keys = period_keys(day_start)
        aggregate(
            "weekday",
            user,
            year=day_keys.year,
            month=day_keys.month,
            day=day_keys.day,
            week_year=day_keys.iso_week_year,
            week=day_keys.iso_week,
            weekday=day_keys.weekday,
        )

    for week_year, week in sorted(seen_weeks):
        aggregate("week", user, week_year=week_year, week=week)


def recalculate_all_streaks():
    """Rebuild lifetime streak counters on user and Combined aggregation docs."""
    for entry_user in collection.distinct("user"):
        entries = list(collection.find({"user": entry_user}, {"timestamp": 1}))
        streaks = streaks_from_log_entries(entries)
        aggregations.update_one({"_id": entry_user}, {"$set": {"streaks": streaks}}, upsert=True)

    combined_entries = list(collection.find({}, {"timestamp": 1}))
    combined_streaks = streaks_from_log_entries(combined_entries)
    aggregations.update_one({"_id": "Combined"}, {"$set": {"streaks": combined_streaks}}, upsert=True)


def recalculate_all_highscores():
    """
    Recalculate all highscores from scratch using the raw data in the collection.
    This is useful when the highscores document gets corrupted or needs to be reset.
    """
    aggregations.delete_one({"_id": "Highscores"})

    entries = []
    for entry in collection.find({}):
        entries.append((entry["timestamp"], entry))
    entries.sort(key=lambda item: item[0])

    current_periods: dict = {}
    last_date_str = ""

    for dt, entry in entries:
        entry_user = entry["user"]
        date_str = format_date_str(dt)
        last_date_str = date_str
        keys = period_keys(dt)
        day_key = (keys.year, keys.month, keys.day)
        month_key = (keys.year, keys.month)
        year_key = keys.year
        week_key = (keys.iso_week_year, keys.iso_week)
        day_label = f"{keys.year}-{keys.month}-{keys.day}"

        if entry_user not in current_periods:
            current_periods[entry_user] = {
                "year": None,
                "month": None,
                "week": None,
                "day": None,
                "year_total": 0,
                "month_total": 0,
                "week_total": 0,
                "day_total": 0,
                "year_active_days": set(),
                "month_active_days": set(),
                "week_active_days": set(),
            }

        periods = current_periods[entry_user]

        if periods["year"] != year_key:
            if periods["year"] is not None:
                year_total_days = total_days_in_period(periods["year"])
                year_activity_data = {
                    "active_days": len(periods["year_active_days"]),
                    "total_days": year_total_days,
                    "activity_ratio": len(periods["year_active_days"]) / year_total_days,
                }
                update_highscore(entry_user, "Year", periods["year_total"], date_str, True, year_activity_data)
            periods["year"] = year_key
            periods["year_total"] = 0
            periods["year_active_days"] = set()
        periods["year_total"] += entry["elapsed_time"]
        periods["year_active_days"].add(day_label)

        if periods["month"] != month_key:
            if periods["month"] is not None:
                month_total_days = total_days_in_period(periods["month"][0], periods["month"][1])
                month_activity_data = {
                    "active_days": len(periods["month_active_days"]),
                    "total_days": month_total_days,
                    "activity_ratio": len(periods["month_active_days"]) / month_total_days,
                }
                update_highscore(entry_user, "Month", periods["month_total"], date_str, True, month_activity_data)
            periods["month"] = month_key
            periods["month_total"] = 0
            periods["month_active_days"] = set()
        periods["month_total"] += entry["elapsed_time"]
        periods["month_active_days"].add(day_label)

        if periods["week"] != week_key:
            if periods["week"] is not None:
                week_activity_data = {
                    "active_days": len(periods["week_active_days"]),
                    "total_days": total_days_in_period(),
                    "activity_ratio": len(periods["week_active_days"]) / total_days_in_period(),
                }
                update_highscore(entry_user, "Week", periods["week_total"], date_str, True, week_activity_data)
            periods["week"] = week_key
            periods["week_total"] = 0
            periods["week_active_days"] = set()
        periods["week_total"] += entry["elapsed_time"]
        periods["week_active_days"].add(day_label)

        if periods["day"] != day_key:
            if periods["day"] is not None:
                update_highscore(entry_user, "Day", periods["day_total"], date_str, True)
            periods["day"] = day_key
            periods["day_total"] = 0
        periods["day_total"] += entry["elapsed_time"]

    for entry_user, periods in current_periods.items():
        date_str = last_date_str or format_date_str(datetime.now())

        if periods["year"] is not None:
            year_total_days = total_days_in_period(periods["year"])
            year_activity_data = {
                "active_days": len(periods["year_active_days"]),
                "total_days": year_total_days,
                "activity_ratio": len(periods["year_active_days"]) / year_total_days,
            }
            update_highscore(entry_user, "Year", periods["year_total"], date_str, True, year_activity_data)

        if periods["month"] is not None:
            month_total_days = total_days_in_period(periods["month"][0], periods["month"][1])
            month_activity_data = {
                "active_days": len(periods["month_active_days"]),
                "total_days": month_total_days,
                "activity_ratio": len(periods["month_active_days"]) / month_total_days,
            }
            update_highscore(entry_user, "Month", periods["month_total"], date_str, True, month_activity_data)

        if periods["week"] is not None:
            week_activity_data = {
                "active_days": len(periods["week_active_days"]),
                "total_days": total_days_in_period(),
                "activity_ratio": len(periods["week_active_days"]) / total_days_in_period(),
            }
            update_highscore(entry_user, "Week", periods["week_total"], date_str, True, week_activity_data)

        if periods["day"] is not None:
            update_highscore(entry_user, "Day", periods["day_total"], date_str, True)
