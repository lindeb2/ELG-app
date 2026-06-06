"""Admin-only: rebuild aggregations and highscores from raw Timetable logs."""
from datetime import datetime

from Timetable import (
    aggregate,
    aggregations,
    calendar_week_key,
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
        dt = entry["timestamp"]
        y, m, d = dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d")
        seen_days.add((y, m, d))
        seen_months.add((y, m))
        seen_years.add(y)
        seen_weeks.add(calendar_week_key(dt))

    for year in sorted(seen_years):
        aggregate("year", user, year=year)

    for year, month in sorted(seen_months):
        aggregate("month", user, year=year, month=month)

    for year, month, day in sorted(seen_days):
        aggregate("day", user, year=year, month=month, day=day)
        dt = datetime(int(year), int(month), int(day))
        week_year, week = calendar_week_key(dt)
        aggregate(
            "weekday",
            user,
            year=year,
            month=month,
            day=day,
            week_year=week_year,
            week=week,
            weekday=dt.strftime("%u"),
        )

    for week_year, week in sorted(seen_weeks):
        aggregate("week", user, week_year=week_year, week=week)


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
        day_key = (dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d"))
        month_key = (dt.strftime("%Y"), dt.strftime("%m"))
        year_key = dt.strftime("%Y")
        week_key = calendar_week_key(dt)
        day_label = f"{day_key[0]}-{day_key[1]}-{day_key[2]}"

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
