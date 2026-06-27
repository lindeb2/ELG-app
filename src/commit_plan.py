"""Commit plan: partial aggregation updates and write-ready MongoDB ops."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection

from commit_prefetch import (
    highscores_slice_to_doc,
    prefetch_digest,
    prefetch_for_log_ts,
    slice_to_agg_doc,
)
from highscore_commit import build_highscore_update_ops
from streak_model import ctx_bool, project_streak


@dataclass
class CommitPlan:
    log_id: ObjectId
    log_ts: datetime
    elapsed_time: int
    user_agg_update: dict[str, Any]
    combined_agg_update: dict[str, Any]
    highscore_update: dict[str, Any]
    broken_records: list
    projected_user: dict[str, Any]
    projected_combined: dict[str, Any]


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


def _project_streaks(streaks: dict, ctx: dict) -> dict:
    day_current = int((streaks.get("days") or {}).get("current") or 0)
    week_current = int((streaks.get("weeks") or {}).get("current") or 0)
    return {
        "days": {
            "current": project_streak(
                day_current,
                active_inc=int(ctx.get("yearActiveInc") or 0),
                prior_period_active=ctx_bool(ctx.get("hadActivityYesterday")),
            ),
        },
        "weeks": {
            "current": project_streak(
                week_current,
                active_inc=int(ctx.get("weekActiveInc") or 0),
                prior_period_active=ctx_bool(ctx.get("hadActivityPriorWeek")),
            ),
        },
    }


def project_agg_from_slice(slice: dict, ctx: dict, elapsed: int) -> dict:
    """Project full aggregation document from a slice after applying this log entry."""
    agg = deepcopy(slice_to_agg_doc(slice, ctx))
    elapsed = int(elapsed)
    years = dict(agg.get("years") or {})

    year_str = ctx["yearStr"]
    month_str = ctx["monthStr"]
    day_str = ctx["dayStr"]
    week_year_str = ctx["weekYearStr"]
    week_str = ctx["weekStr"]
    weekday_str = ctx["weekdayStr"]

    year_existing = dict(years.get(year_str) or {})
    years[year_str] = _activity_merge(
        year_existing,
        int(ctx.get("yearActiveInc") or 0),
        int(ctx.get("yearTotalDays") or 0),
        elapsed,
    )

    year_doc = dict(years.get(year_str) or {})
    months = dict(year_doc.get("months") or {})
    month_existing = dict(months.get(month_str) or {})
    months[month_str] = _activity_merge(
        month_existing,
        int(ctx.get("monthActiveInc") or 0),
        int(ctx.get("monthTotalDays") or 0),
        elapsed,
    )

    month_doc = dict(months.get(month_str) or {})
    days = dict(month_doc.get("days") or {})
    day_existing = dict(days.get(day_str) or {})
    days[day_str] = {**day_existing, "time": int(day_existing.get("time") or 0) + elapsed}
    month_doc["days"] = days
    months[month_str] = month_doc
    year_doc["months"] = months
    years[year_str] = year_doc

    week_year_existing = dict(years.get(week_year_str) or {})
    weeks = dict(week_year_existing.get("weeks") or {})
    week_existing = dict(weeks.get(week_str) or {})
    weeks[week_str] = _activity_merge(
        week_existing,
        int(ctx.get("weekActiveInc") or 0),
        int(ctx.get("weekTotalDays") or 0),
        elapsed,
    )

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
    agg["streaks"] = _project_streaks(agg.get("streaks") or {}, ctx)
    return agg


def build_agg_update_ops(ctx: dict, elapsed: int, slice: dict) -> dict[str, Any]:
    """Build a MongoDB update document ($inc/$set) for one aggregation doc."""
    elapsed = int(elapsed)
    year_str = ctx["yearStr"]
    month_str = ctx["monthStr"]
    day_str = ctx["dayStr"]
    week_year_str = ctx["weekYearStr"]
    week_str = ctx["weekStr"]
    weekday_str = ctx["weekdayStr"]

    year_active_inc = int(ctx.get("yearActiveInc") or 0)
    month_active_inc = int(ctx.get("monthActiveInc") or 0)
    week_active_inc = int(ctx.get("weekActiveInc") or 0)
    year_total_days = int(ctx.get("yearTotalDays") or 0)
    month_total_days = int(ctx.get("monthTotalDays") or 0)
    week_total_days = int(ctx.get("weekTotalDays") or 0)

    year_bucket = slice.get("year_bucket") or {}
    week_bucket = slice.get("week_bucket") or {}
    streaks = slice.get("streaks") or {}

    year_merged = _activity_merge(year_bucket, year_active_inc, year_total_days, elapsed)
    month_existing = ((year_bucket.get("months") or {}).get(month_str) or {})
    month_merged = _activity_merge(month_existing, month_active_inc, month_total_days, elapsed)
    week_merged = _activity_merge(week_bucket, week_active_inc, week_total_days, elapsed)
    projected_streaks = _project_streaks(streaks, ctx)

    inc: dict[str, int] = {}
    set_fields: dict[str, Any] = {}

    def add_activity_paths(prefix: str, merged: dict, active_inc: int) -> None:
        inc[f"{prefix}.time"] = elapsed
        if active_inc:
            inc[f"{prefix}.active_days"] = active_inc
        set_fields[f"{prefix}.total_days"] = merged["total_days"]
        set_fields[f"{prefix}.activity_ratio"] = merged["activity_ratio"]

    add_activity_paths(f"years.{year_str}", year_merged, year_active_inc)
    add_activity_paths(f"years.{year_str}.months.{month_str}", month_merged, month_active_inc)
    inc[f"years.{year_str}.months.{month_str}.days.{day_str}.time"] = elapsed
    add_activity_paths(
        f"years.{week_year_str}.weeks.{week_str}",
        week_merged,
        week_active_inc,
    )
    inc[f"years.{week_year_str}.weeks.{week_str}.weekdays.{weekday_str}.time"] = elapsed
    set_fields["streaks.days.current"] = projected_streaks["days"]["current"]
    set_fields["streaks.weeks.current"] = projected_streaks["weeks"]["current"]

    update: dict[str, Any] = {}
    if inc:
        update["$inc"] = inc
    if set_fields:
        update["$set"] = set_fields
    return update


def _with_set_on_insert(update: dict[str, Any], doc_id) -> dict[str, Any]:
    result = dict(update)
    set_on_insert = dict(result.get("$setOnInsert") or {})
    set_on_insert["_id"] = doc_id
    result["$setOnInsert"] = set_on_insert
    return result


def build_commit_plan(
    prefetch: dict,
    user: str,
    elapsed_time: int,
) -> CommitPlan:
    """Build a write-ready commit plan from prefetch data."""
    log_id = prefetch.get("logId") or ObjectId()
    log_ts = prefetch["logTs"]
    elapsed_time = int(elapsed_time)
    user_ctx, combined_ctx = prefetch["user_ctx"], prefetch["combined_ctx"]

    user_agg_update = _with_set_on_insert(
        build_agg_update_ops(user_ctx, elapsed_time, prefetch["user_agg_slice"]),
        user,
    )
    combined_agg_update = _with_set_on_insert(
        build_agg_update_ops(combined_ctx, elapsed_time, prefetch["combined_agg_slice"]),
        "Combined",
    )

    projected_user = project_agg_from_slice(
        prefetch["user_agg_slice"], user_ctx, elapsed_time
    )
    projected_combined = project_agg_from_slice(
        prefetch["combined_agg_slice"], combined_ctx, elapsed_time
    )
    highscores = highscores_slice_to_doc(user, prefetch["highscores_slice"])
    broken_records, highscore_update = build_highscore_update_ops(
        user,
        log_ts,
        user_ctx,
        combined_ctx,
        highscores=highscores,
        user_agg=projected_user,
        combined_agg=projected_combined,
    )
    highscore_update = _with_set_on_insert(highscore_update, "Highscores")

    return CommitPlan(
        log_id=log_id,
        log_ts=log_ts,
        elapsed_time=elapsed_time,
        user_agg_update=user_agg_update,
        combined_agg_update=combined_agg_update,
        highscore_update=highscore_update,
        broken_records=broken_records,
        projected_user=projected_user,
        projected_combined=projected_combined,
    )


def build_plan_from_log_ts(
    collection: Collection,
    user: str,
    log_ts: datetime,
    elapsed_time: int,
    *,
    session=None,
    log_id: ObjectId | None = None,
) -> tuple[CommitPlan, str]:
    """Prefetch inside a transaction and build a plan plus digest."""
    log_id = log_id or ObjectId()
    prefetch = prefetch_for_log_ts(
        collection, user, log_ts, log_id=log_id, session=session
    )
    plan = build_commit_plan(prefetch, user, elapsed_time)
    return plan, prefetch_digest(prefetch)
