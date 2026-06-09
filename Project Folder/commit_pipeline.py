"""MongoDB aggregation stages for commit context prefetch."""
from period_model import active_inc_expr, prior_log_lookup_stage

_CONTEXT_PROJECT = {
    "$project": {
        "_id": 0,
        "yearStr": 1,
        "monthStr": 1,
        "dayStr": 1,
        "weekdayStr": 1,
        "weekYearStr": 1,
        "weekStr": 1,
        "dayKey": 1,
        "weekKey": 1,
        "yesterdayDayKey": 1,
        "priorWeekKey": 1,
        "yearTotalDays": 1,
        "monthTotalDays": 1,
        "weekTotalDays": 1,
        "yearActiveInc": 1,
        "monthActiveInc": 1,
        "weekActiveInc": 1,
    }
}


def _lookups_and_activity_stages(*, filter_user: bool) -> list[dict]:
    return [
        prior_log_lookup_stage(
            as_name="priorYear",
            period_start="$yearStart",
            period_end="$yearEnd",
            filter_user=filter_user,
        ),
        prior_log_lookup_stage(
            as_name="priorMonth",
            period_start="$monthStart",
            period_end="$monthEnd",
            filter_user=filter_user,
        ),
        prior_log_lookup_stage(
            as_name="priorWeek",
            period_start="$weekStart",
            period_end="$weekEnd",
            filter_user=filter_user,
        ),
        {
            "$set": {
                "yearActiveInc": active_inc_expr("$priorYear"),
                "monthActiveInc": active_inc_expr("$priorMonth"),
                "weekActiveInc": active_inc_expr("$priorWeek"),
            }
        },
    ]


def _context_facet_branch(*, filter_user: bool) -> list[dict]:
    return _lookups_and_activity_stages(filter_user=filter_user) + [_CONTEXT_PROJECT]
