"""MongoDB aggregation pipelines for incremental log commit."""
from period_model import (
    active_inc_expr,
    period_key_set_stage,
    prior_log_lookup_stage,
)

_TEMP_FIELDS = [
    "logTs",
    "logId",
    "elapsed",
    "logUser",
    "yearStr",
    "monthStr",
    "dayStr",
    "weekdayStr",
    "weekYearStr",
    "weekStr",
    "dayStart",
    "yearStart",
    "yearEnd",
    "monthStart",
    "monthEnd",
    "weekStart",
    "weekEnd",
    "yearTotalDays",
    "monthTotalDays",
    "weekTotalDays",
    "priorYear",
    "priorMonth",
    "priorWeek",
    "yearActiveInc",
    "monthActiveInc",
    "weekActiveInc",
]


def _time_inc(existing_expr: str) -> dict:
    return {"$add": [{"$ifNull": [existing_expr, 0]}, "$$elapsed"]}


def _activity_merge(existing_val: str, active_inc: str, total_days: str) -> dict:
    new_active = {"$add": [{"$ifNull": [f"{existing_val}.active_days", 0]}, active_inc]}
    return {
        "time": _time_inc(f"{existing_val}.time"),
        "active_days": new_active,
        "total_days": total_days,
        "activity_ratio": {"$divide": [new_active, total_days]},
    }


def _upsert_object_map(
    parent_expr: dict,
    key_expr: str,
    value_expr: dict,
) -> dict:
    return {
        "$arrayToObject": {
            "$concatArrays": [
                {
                    "$filter": {
                        "input": {"$objectToArray": {"$ifNull": [parent_expr, {}]}},
                        "as": "entry",
                        "cond": {"$ne": ["$$entry.k", key_expr]},
                    }
                },
                [{"k": key_expr, "v": value_expr}],
            ]
        }
    }


def _existing_map_value(parent_expr: dict, key_expr: str) -> dict:
    return {
        "$let": {
            "vars": {
                "hit": {
                    "$first": {
                        "$filter": {
                            "input": {"$objectToArray": {"$ifNull": [parent_expr, {}]}},
                            "as": "entry",
                            "cond": {"$eq": ["$$entry.k", key_expr]},
                        }
                    }
                }
            },
            "in": {"$ifNull": ["$$hit.v", {}]},
        }
    }


def _set_year_bucket(active_inc: str, total_days: str) -> dict:
    return {
        "$set": {
            "years": {
                "$let": {
                    "vars": {
                        "yearExisting": _existing_map_value("$years", "$$yearStr"),
                    },
                    "in": _upsert_object_map(
                        "$years",
                        "$$yearStr",
                        {
                            "$mergeObjects": [
                                "$$yearExisting",
                                _activity_merge("$$yearExisting", active_inc, total_days),
                            ]
                        },
                    ),
                }
            }
        }
    }


def _set_month_bucket(active_inc: str, total_days: str) -> dict:
    return {
        "$set": {
            "years": {
                "$let": {
                    "vars": {
                        "yearExisting": _existing_map_value("$years", "$$yearStr"),
                    },
                    "in": {
                        "$let": {
                            "vars": {
                                "monthExisting": _existing_map_value(
                                    "$$yearExisting.months", "$$monthStr"
                                ),
                            },
                            "in": _upsert_object_map(
                                "$years",
                                "$$yearStr",
                                {
                                    "$mergeObjects": [
                                        "$$yearExisting",
                                        {
                                            "months": _upsert_object_map(
                                                "$$yearExisting.months",
                                                "$$monthStr",
                                                {
                                                    "$mergeObjects": [
                                                        "$$monthExisting",
                                                        _activity_merge(
                                                            "$$monthExisting",
                                                            active_inc,
                                                            total_days,
                                                        ),
                                                    ]
                                                },
                                            )
                                        },
                                    ]
                                },
                            ),
                        }
                    },
                }
            }
        }
    }


def _set_day_bucket() -> dict:
    return {
        "$set": {
            "years": {
                "$let": {
                    "vars": {
                        "yearExisting": _existing_map_value("$years", "$$yearStr"),
                    },
                    "in": {
                        "$let": {
                            "vars": {
                                "monthExisting": _existing_map_value(
                                    "$$yearExisting.months", "$$monthStr"
                                ),
                            },
                            "in": {
                                "$let": {
                                    "vars": {
                                        "dayExisting": _existing_map_value(
                                            "$$monthExisting.days", "$$dayStr"
                                        ),
                                    },
                                    "in": _upsert_object_map(
                                        "$years",
                                        "$$yearStr",
                                        {
                                            "$mergeObjects": [
                                                "$$yearExisting",
                                                {
                                                    "months": _upsert_object_map(
                                                        "$$yearExisting.months",
                                                        "$$monthStr",
                                                        {
                                                            "$mergeObjects": [
                                                                "$$monthExisting",
                                                                {
                                                                    "days": _upsert_object_map(
                                                                        "$$monthExisting.days",
                                                                        "$$dayStr",
                                                                        {
                                                                            "$mergeObjects": [
                                                                                "$$dayExisting",
                                                                                {
                                                                                    "time": _time_inc(
                                                                                        "$$dayExisting.time"
                                                                                    )
                                                                                },
                                                                            ]
                                                                        },
                                                                    )
                                                                },
                                                            ]
                                                        },
                                                    )
                                                },
                                            ]
                                        },
                                    ),
                                }
                            },
                        }
                    },
                }
            }
        }
    }


def _set_week_bucket(active_inc: str, total_days: str) -> dict:
    return {
        "$set": {
            "years": {
                "$let": {
                    "vars": {
                        "yearExisting": _existing_map_value("$years", "$$weekYearStr"),
                    },
                    "in": {
                        "$let": {
                            "vars": {
                                "weekExisting": _existing_map_value(
                                    "$$yearExisting.weeks", "$$weekStr"
                                ),
                            },
                            "in": _upsert_object_map(
                                "$years",
                                "$$weekYearStr",
                                {
                                    "$mergeObjects": [
                                        "$$yearExisting",
                                        {
                                            "weeks": _upsert_object_map(
                                                "$$yearExisting.weeks",
                                                "$$weekStr",
                                                {
                                                    "$mergeObjects": [
                                                        "$$weekExisting",
                                                        _activity_merge(
                                                            "$$weekExisting",
                                                            active_inc,
                                                            total_days,
                                                        ),
                                                    ]
                                                },
                                            )
                                        },
                                    ]
                                },
                            ),
                        }
                    },
                }
            }
        }
    }


def _set_weekday_bucket() -> dict:
    return {
        "$set": {
            "years": {
                "$let": {
                    "vars": {
                        "yearExisting": _existing_map_value("$years", "$$weekYearStr"),
                    },
                    "in": {
                        "$let": {
                            "vars": {
                                "weekExisting": _existing_map_value(
                                    "$$yearExisting.weeks", "$$weekStr"
                                ),
                            },
                            "in": {
                                "$let": {
                                    "vars": {
                                        "weekdayExisting": _existing_map_value(
                                            "$$weekExisting.weekdays", "$$weekdayStr"
                                        ),
                                    },
                                    "in": _upsert_object_map(
                                        "$years",
                                        "$$weekYearStr",
                                        {
                                            "$mergeObjects": [
                                                "$$yearExisting",
                                                {
                                                    "weeks": _upsert_object_map(
                                                        "$$yearExisting.weeks",
                                                        "$$weekStr",
                                                        {
                                                            "$mergeObjects": [
                                                                "$$weekExisting",
                                                                {
                                                                    "weekdays": _upsert_object_map(
                                                                        "$$weekExisting.weekdays",
                                                                        "$$weekdayStr",
                                                                        {
                                                                            "$mergeObjects": [
                                                                                "$$weekdayExisting",
                                                                                {
                                                                                    "time": _time_inc(
                                                                                        "$$weekdayExisting.time"
                                                                                    )
                                                                                },
                                                                            ]
                                                                        },
                                                                    )
                                                                },
                                                            ]
                                                        },
                                                    )
                                                },
                                            ]
                                        },
                                    ),
                                }
                            },
                        }
                    },
                }
            }
        }
    }


def _lookups_and_activity_stages(*, filter_user: bool) -> list[dict]:
    stages = [
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
    return stages


def _bucket_update_stages() -> list[dict]:
    return [
        _set_year_bucket("$$yearActiveInc", "$$yearTotalDays"),
        _set_month_bucket("$$monthActiveInc", "$$monthTotalDays"),
        _set_week_bucket("$$weekActiveInc", "$$weekTotalDays"),
        _set_day_bucket(),
        _set_weekday_bucket(),
    ]


def _context_from_log_stages(*, include_log_user: bool) -> list[dict]:
    """Aggregate on Timetable: derive period keys + activity flags ($lookup allowed)."""
    stages: list[dict] = [
        {"$match": {"$expr": {"$eq": ["$_id", "$$logId"]}}},
        {"$set": {"logTs": "$timestamp", "logId": "$$logId"}},
        period_key_set_stage("$logTs"),
    ]
    if include_log_user:
        stages.append({"$set": {"logUser": "$$logUser"}})
    stages.extend(_lookups_and_activity_stages(filter_user=include_log_user))
    stages.append({
        "$project": {
            "_id": 0,
            "yearStr": 1,
            "monthStr": 1,
            "dayStr": 1,
            "weekdayStr": 1,
            "weekYearStr": 1,
            "weekStr": 1,
            "yearTotalDays": 1,
            "monthTotalDays": 1,
            "weekTotalDays": 1,
            "yearActiveInc": 1,
            "monthActiveInc": 1,
            "weekActiveInc": 1,
        }
    })
    return stages


USER_CONTEXT_PIPELINE = _context_from_log_stages(include_log_user=True)
COMBINED_CONTEXT_PIPELINE = _context_from_log_stages(include_log_user=False)

USER_BUCKET_PIPELINE = _bucket_update_stages()
COMBINED_BUCKET_PIPELINE = _bucket_update_stages()
