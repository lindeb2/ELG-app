"""Shared persistence for broken records in Status Meeting > Records."""
from __future__ import annotations

from datetime import datetime

from period_model import to_local


def persist_broken_records(
    status_meeting,
    user: str,
    broken_records: list[dict],
    timestamp: datetime,
) -> None:
    """Write broken records to Status Meeting > Records.

    Uses $mergeObjects so old_value is preserved on subsequent breaks of the
    same slot within the same week: the first write sets old_value; later
    writes only update new_value and last_broken_ts.
    """
    if not broken_records:
        return
    local = to_local(timestamp)
    iso_year, iso_week, _ = local.isocalendar()

    set_fields = {}
    for record in broken_records:
        old = record["old_record"]
        new = record["new_record"]
        scope = old["scope"]
        time_type = old["time_type"]
        metric = old["metric"]

        if scope == "personal":
            slot = f"data.{iso_year}.{iso_week}.personal.{user}.{time_type}.{metric}"
        elif scope == "global":
            slot = f"data.{iso_year}.{iso_week}.global.{time_type}.{metric}"
        else:  # combined
            slot = f"data.{iso_year}.{iso_week}.combined.{time_type}.{metric}"

        defaults = {
            "old_value": old["value"],
            "scope": scope,
            "time_type": time_type,
            "metric": metric,
            "broken_by": user,
        }
        if scope == "global":
            defaults["old_holder"] = old.get("user")

        set_fields[slot] = {
            "$mergeObjects": [
                defaults,
                {"$ifNull": [f"${slot}", {}]},
                {"new_value": new["value"], "last_broken_ts": timestamp},
            ]
        }

    try:
        status_meeting.update_one(
            {"_id": "Records"},
            [{"$set": set_fields}],
            upsert=True,
        )
    except Exception as exc:
        print(f"Failed to persist broken records: {exc}")
