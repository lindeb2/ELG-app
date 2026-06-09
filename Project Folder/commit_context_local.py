"""Local commit context extraction and post-commit aggregation projection."""
from __future__ import annotations
from copy import deepcopy


def _activity_merge(existing: dict, active_inc: int, total_days: int, elapsed: int) -> dict:
    merged = dict(existing)
    new_active = int(existing.get("active_days") or 0) + active_inc
    merged.update({
        "time": int(existing.get("time") or 0) + elapsed,
        "active_days": new_active,
        "total_days": total_days,
        "activity_ratio": new_active / total_days if total_days else 0.0,
    })
    return merged


def _apply_streaks(agg: dict, ctx: dict) -> None:
    streaks = dict(agg.get("streaks") or {})
    day_existing = dict(streaks.get("days") or {"current": 0, "best": 0, "last_active_day": None})
    week_existing = dict(streaks.get("weeks") or {"current": 0, "best": 0, "last_active_week": None})

    year_active_inc = int(ctx.get("yearActiveInc") or 0)
    week_active_inc = int(ctx.get("weekActiveInc") or 0)

    if year_active_inc == 0:
        new_day_current = int(day_existing.get("current") or 0)
        new_day_last = day_existing.get("last_active_day")
    else:
        last_day = day_existing.get("last_active_day")
        day_key = ctx["dayKey"]
        yesterday = ctx["yesterdayDayKey"]
        if last_day == yesterday:
            new_day_current = int(day_existing.get("current") or 0) + 1
        elif last_day == day_key:
            new_day_current = int(day_existing.get("current") or 0)
        else:
            new_day_current = 1
        new_day_last = day_key

    streaks["days"] = {
        "current": new_day_current,
        "best": max(int(day_existing.get("best") or 0), new_day_current),
        "last_active_day": new_day_last,
    }

    if week_active_inc == 0:
        new_week_current = int(week_existing.get("current") or 0)
        new_week_last = week_existing.get("last_active_week")
    else:
        last_week = week_existing.get("last_active_week")
        week_key = ctx["weekKey"]
        prior_week = ctx["priorWeekKey"]
        if last_week == prior_week:
            new_week_current = int(week_existing.get("current") or 0) + 1
        elif last_week == week_key:
            new_week_current = int(week_existing.get("current") or 0)
        else:
            new_week_current = 1
        new_week_last = week_key

    streaks["weeks"] = {
        "current": new_week_current,
        "best": max(int(week_existing.get("best") or 0), new_week_current),
        "last_active_week": new_week_last,
    }
    agg["streaks"] = streaks


def project_agg_after_commit(agg_doc: dict, ctx: dict, elapsed: int) -> dict:
    """Project aggregation document state after bucket pipeline updates."""
    agg = deepcopy(agg_doc or {})
    elapsed = int(elapsed)
    years = dict(agg.get("years") or {})

    year_str = ctx["yearStr"]
    month_str = ctx["monthStr"]
    day_str = ctx["dayStr"]
    week_year_str = ctx["weekYearStr"]
    week_str = ctx["weekStr"]
    weekday_str = ctx["weekdayStr"]

    # Year bucket
    year_existing = dict(years.get(year_str) or {})
    years[year_str] = _activity_merge(
        year_existing,
        int(ctx.get("yearActiveInc") or 0),
        int(ctx.get("yearTotalDays") or 0),
        elapsed,
    )

    # Month bucket (under calendar year)
    year_doc = dict(years.get(year_str) or {})
    months = dict(year_doc.get("months") or {})
    month_existing = dict(months.get(month_str) or {})
    months[month_str] = _activity_merge(
        month_existing,
        int(ctx.get("monthActiveInc") or 0),
        int(ctx.get("monthTotalDays") or 0),
        elapsed,
    )

    # Day bucket (time only)
    month_doc = dict(months.get(month_str) or {})
    days = dict(month_doc.get("days") or {})
    day_existing = dict(days.get(day_str) or {})
    days[day_str] = {**day_existing, "time": int(day_existing.get("time") or 0) + elapsed}
    month_doc["days"] = days
    months[month_str] = month_doc
    year_doc["months"] = months
    years[year_str] = year_doc

    # Week bucket (under ISO week year, may differ from calendar year)
    week_year_existing = dict(years.get(week_year_str) or {})
    weeks = dict(week_year_existing.get("weeks") or {})
    week_existing = dict(weeks.get(week_str) or {})
    weeks[week_str] = _activity_merge(
        week_existing,
        int(ctx.get("weekActiveInc") or 0),
        int(ctx.get("weekTotalDays") or 0),
        elapsed,
    )

    # Weekday bucket (time only)
    week_doc = dict(weeks.get(week_str) or {})
    weekdays = dict(week_doc.get("weekdays") or {})
    weekday_existing = dict(weekdays.get(weekday_str) or {})
    weekdays[weekday_str] = {
        **weekday_existing,
        "time": int(weekday_existing.get("time") or 0) + elapsed,
    }
    week_doc["weekdays"] = weekdays
    weeks[week_str] = week_doc
    week_year_existing["weeks"] = weeks
    years[week_year_str] = week_year_existing

    agg["years"] = years
    _apply_streaks(agg, ctx)
    return agg
