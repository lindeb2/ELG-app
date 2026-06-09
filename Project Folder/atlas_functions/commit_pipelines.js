/**
 * MongoDB aggregation pipelines for incremental log commit.
 * SSOT mirror of commit_pipeline.py + period_model.py pipeline helpers.
 *
 * Repo source only — not deployed to Atlas directly.
 * Bundled into commitLog.js via: node bundle.js
 */

const APP_TIMEZONE = "Europe/Stockholm";
const LOGS_COLLECTION = "Timetable";

function trunc(unit, dateExpr, startOfWeek) {
  const spec = { date: dateExpr, unit, timezone: APP_TIMEZONE };
  if (startOfWeek) spec.startOfWeek = startOfWeek;
  return { $dateTrunc: spec };
}

function dateToString(fmt, dateExpr) {
  return { $dateToString: { format: fmt, date: dateExpr, timezone: APP_TIMEZONE } };
}

function activeIncExpr(priorArray) {
  return { $cond: [{ $eq: [{ $size: priorArray }, 0] }, 1, 0] };
}

function periodKeySetStage(logTs) {
  const weekTrunc = trunc("week", logTs, "monday");
  return {
    $set: {
      logTs,
      yearStr: dateToString("%Y", logTs),
      monthStr: dateToString("%m", logTs),
      dayStr: dateToString("%d", logTs),
      weekdayStr: dateToString("%u", logTs),
      weekYearStr: { $toString: { $isoWeekYear: { date: logTs, timezone: APP_TIMEZONE } } },
      weekStr: { $toString: { $isoWeek: { date: logTs, timezone: APP_TIMEZONE } } },
      dayStart: trunc("day", logTs),
      yearStart: trunc("year", logTs),
      yearEnd: { $dateAdd: { startDate: trunc("year", logTs), unit: "year", amount: 1 } },
      monthStart: trunc("month", logTs),
      monthEnd: { $dateAdd: { startDate: trunc("month", logTs), unit: "month", amount: 1 } },
      weekStart: weekTrunc,
      weekEnd: { $dateAdd: { startDate: weekTrunc, unit: "week", amount: 1 } },
      yearTotalDays: {
        $dateDiff: {
          startDate: trunc("year", logTs),
          endDate: { $dateAdd: { startDate: trunc("year", logTs), unit: "year", amount: 1 } },
          unit: "day",
        },
      },
      monthTotalDays: {
        $dateDiff: {
          startDate: trunc("month", logTs),
          endDate: { $dateAdd: { startDate: trunc("month", logTs), unit: "month", amount: 1 } },
          unit: "day",
        },
      },
      weekTotalDays: 7,
    },
  };
}

function streakKeySetStage() {
  const priorWeekStart = { $dateSubtract: { startDate: "$weekStart", unit: "day", amount: 7 } };
  return {
    $set: {
      dayKey: { $concat: ["$yearStr", "-", "$monthStr", "-", "$dayStr"] },
      weekKey: { $concat: ["$weekYearStr", "-W", "$weekStr"] },
      yesterdayDayKey: dateToString(
        "%Y-%m-%d",
        { $dateSubtract: { startDate: "$dayStart", unit: "day", amount: 1 } }
      ),
      priorWeekKey: {
        $concat: [
          { $toString: { $isoWeekYear: { date: priorWeekStart, timezone: APP_TIMEZONE } } },
          "-W",
          { $toString: { $isoWeek: { date: priorWeekStart, timezone: APP_TIMEZONE } } },
        ],
      },
    },
  };
}

function priorLogLookupStage({ asName, periodStart, periodEnd, filterUser }) {
  const matchConditions = [
    { $ne: ["$_id", "$$logId"] },
    { $gte: ["$timestamp", "$$periodStart"] },
    { $lt: ["$timestamp", "$$periodEnd"] },
    { $eq: [trunc("day", "$timestamp"), "$$dayStart"] },
  ];
  if (filterUser) {
    matchConditions.push({ $eq: ["$user", "$$logUser"] });
  }
  return {
    $lookup: {
      from: LOGS_COLLECTION,
      let: {
        periodStart,
        periodEnd,
        dayStart: "$dayStart",
        logId: "$logId",
        logUser: "$logUser",
      },
      pipeline: [
        { $match: { $expr: { $and: matchConditions } } },
        { $limit: 1 },
        { $project: { _id: 1 } },
      ],
      as: asName,
    },
  };
}

function timeInc(existingExpr) {
  return { $add: [{ $ifNull: [existingExpr, 0] }, "$$elapsed"] };
}

function activityMerge(existingVal, activeInc, totalDays) {
  const newActive = { $add: [{ $ifNull: [`${existingVal}.active_days`, 0] }, activeInc] };
  return {
    time: timeInc(`${existingVal}.time`),
    active_days: newActive,
    total_days: totalDays,
    activity_ratio: { $divide: [newActive, totalDays] },
  };
}

function upsertObjectMap(parentExpr, keyExpr, valueExpr) {
  return {
    $arrayToObject: {
      $concatArrays: [
        {
          $filter: {
            input: { $objectToArray: { $ifNull: [parentExpr, {}] } },
            as: "entry",
            cond: { $ne: ["$$entry.k", keyExpr] },
          },
        },
        [{ k: keyExpr, v: valueExpr }],
      ],
    },
  };
}

function existingMapValue(parentExpr, keyExpr) {
  return {
    $let: {
      vars: {
        hit: {
          $first: {
            $filter: {
              input: { $objectToArray: { $ifNull: [parentExpr, {}] } },
              as: "entry",
              cond: { $eq: ["$$entry.k", keyExpr] },
            },
          },
        },
      },
      in: { $ifNull: ["$$hit.v", {}] },
    },
  };
}

function setYearBucket(activeInc, totalDays) {
  return {
    $set: {
      years: {
        $let: {
          vars: { yearExisting: existingMapValue("$years", "$$yearStr") },
          in: upsertObjectMap("$years", "$$yearStr", {
            $mergeObjects: [
              "$$yearExisting",
              activityMerge("$$yearExisting", activeInc, totalDays),
            ],
          }),
        },
      },
    },
  };
}

function setMonthBucket(activeInc, totalDays) {
  return {
    $set: {
      years: {
        $let: {
          vars: { yearExisting: existingMapValue("$years", "$$yearStr") },
          in: {
            $let: {
              vars: {
                monthExisting: existingMapValue("$$yearExisting.months", "$$monthStr"),
              },
              in: upsertObjectMap("$years", "$$yearStr", {
                $mergeObjects: [
                  "$$yearExisting",
                  {
                    months: upsertObjectMap("$$yearExisting.months", "$$monthStr", {
                      $mergeObjects: [
                        "$$monthExisting",
                        activityMerge("$$monthExisting", activeInc, totalDays),
                      ],
                    }),
                  },
                ],
              }),
            },
          },
        },
      },
    },
  };
}

function setDayBucket() {
  return {
    $set: {
      years: {
        $let: {
          vars: { yearExisting: existingMapValue("$years", "$$yearStr") },
          in: {
            $let: {
              vars: {
                monthExisting: existingMapValue("$$yearExisting.months", "$$monthStr"),
              },
              in: {
                $let: {
                  vars: {
                    dayExisting: existingMapValue("$$monthExisting.days", "$$dayStr"),
                  },
                  in: upsertObjectMap("$years", "$$yearStr", {
                    $mergeObjects: [
                      "$$yearExisting",
                      {
                        months: upsertObjectMap("$$yearExisting.months", "$$monthStr", {
                          $mergeObjects: [
                            "$$monthExisting",
                            {
                              days: upsertObjectMap("$$monthExisting.days", "$$dayStr", {
                                $mergeObjects: [
                                  "$$dayExisting",
                                  { time: timeInc("$$dayExisting.time") },
                                ],
                              }),
                            },
                          ],
                        }),
                      },
                    ],
                  }),
                },
              },
            },
          },
        },
      },
    },
  };
}

function setWeekBucket(activeInc, totalDays) {
  return {
    $set: {
      years: {
        $let: {
          vars: { yearExisting: existingMapValue("$years", "$$weekYearStr") },
          in: {
            $let: {
              vars: {
                weekExisting: existingMapValue("$$yearExisting.weeks", "$$weekStr"),
              },
              in: upsertObjectMap("$years", "$$weekYearStr", {
                $mergeObjects: [
                  "$$yearExisting",
                  {
                    weeks: upsertObjectMap("$$yearExisting.weeks", "$$weekStr", {
                      $mergeObjects: [
                        "$$weekExisting",
                        activityMerge("$$weekExisting", activeInc, totalDays),
                      ],
                    }),
                  },
                ],
              }),
            },
          },
        },
      },
    },
  };
}

function setWeekdayBucket() {
  return {
    $set: {
      years: {
        $let: {
          vars: { yearExisting: existingMapValue("$years", "$$weekYearStr") },
          in: {
            $let: {
              vars: {
                weekExisting: existingMapValue("$$yearExisting.weeks", "$$weekStr"),
              },
              in: {
                $let: {
                  vars: {
                    weekdayExisting: existingMapValue("$$weekExisting.weekdays", "$$weekdayStr"),
                  },
                  in: upsertObjectMap("$years", "$$weekYearStr", {
                    $mergeObjects: [
                      "$$yearExisting",
                      {
                        weeks: upsertObjectMap("$$yearExisting.weeks", "$$weekStr", {
                          $mergeObjects: [
                            "$$weekExisting",
                            {
                              weekdays: upsertObjectMap("$$weekExisting.weekdays", "$$weekdayStr", {
                                $mergeObjects: [
                                  "$$weekdayExisting",
                                  { time: timeInc("$$weekdayExisting.time") },
                                ],
                              }),
                            },
                          ],
                        }),
                      },
                    ],
                  }),
                },
              },
            },
          },
        },
      },
    },
  };
}

function lookupsAndActivityStages(filterUser) {
  return [
    priorLogLookupStage({
      asName: "priorYear",
      periodStart: "$yearStart",
      periodEnd: "$yearEnd",
      filterUser,
    }),
    priorLogLookupStage({
      asName: "priorMonth",
      periodStart: "$monthStart",
      periodEnd: "$monthEnd",
      filterUser,
    }),
    priorLogLookupStage({
      asName: "priorWeek",
      periodStart: "$weekStart",
      periodEnd: "$weekEnd",
      filterUser,
    }),
    {
      $set: {
        yearActiveInc: activeIncExpr("$priorYear"),
        monthActiveInc: activeIncExpr("$priorMonth"),
        weekActiveInc: activeIncExpr("$priorWeek"),
      },
    },
  ];
}

function setStreaks() {
  return {
    $set: {
      streaks: {
        $let: {
          vars: {
            dayExisting: {
              $ifNull: ["$streaks.days", { current: 0, best: 0, last_active_day: null }],
            },
            weekExisting: {
              $ifNull: ["$streaks.weeks", { current: 0, best: 0, last_active_week: null }],
            },
          },
          in: {
            days: {
              $let: {
                vars: {
                  newCurrent: {
                    $cond: [
                      { $eq: ["$$yearActiveInc", 0] },
                      "$$dayExisting.current",
                      {
                        $cond: [
                          { $eq: ["$$dayExisting.last_active_day", "$$yesterdayDayKey"] },
                          { $add: ["$$dayExisting.current", 1] },
                          {
                            $cond: [
                              { $eq: ["$$dayExisting.last_active_day", "$$dayKey"] },
                              "$$dayExisting.current",
                              1,
                            ],
                          },
                        ],
                      },
                    ],
                  },
                  newLast: {
                    $cond: [
                      { $eq: ["$$yearActiveInc", 0] },
                      "$$dayExisting.last_active_day",
                      "$$dayKey",
                    ],
                  },
                },
                in: {
                  current: "$$newCurrent",
                  best: { $max: ["$$dayExisting.best", "$$newCurrent"] },
                  last_active_day: "$$newLast",
                },
              },
            },
            weeks: {
              $let: {
                vars: {
                  newCurrent: {
                    $cond: [
                      { $eq: ["$$weekActiveInc", 0] },
                      "$$weekExisting.current",
                      {
                        $cond: [
                          { $eq: ["$$weekExisting.last_active_week", "$$priorWeekKey"] },
                          { $add: ["$$weekExisting.current", 1] },
                          {
                            $cond: [
                              { $eq: ["$$weekExisting.last_active_week", "$$weekKey"] },
                              "$$weekExisting.current",
                              1,
                            ],
                          },
                        ],
                      },
                    ],
                  },
                  newLast: {
                    $cond: [
                      { $eq: ["$$weekActiveInc", 0] },
                      "$$weekExisting.last_active_week",
                      "$$weekKey",
                    ],
                  },
                },
                in: {
                  current: "$$newCurrent",
                  best: { $max: ["$$weekExisting.best", "$$newCurrent"] },
                  last_active_week: "$$newLast",
                },
              },
            },
          },
        },
      },
    },
  };
}

const CONTEXT_PROJECT = {
  $project: {
    _id: 0,
    yearStr: 1,
    monthStr: 1,
    dayStr: 1,
    weekdayStr: 1,
    weekYearStr: 1,
    weekStr: 1,
    dayKey: 1,
    weekKey: 1,
    yesterdayDayKey: 1,
    priorWeekKey: 1,
    yearTotalDays: 1,
    monthTotalDays: 1,
    weekTotalDays: 1,
    yearActiveInc: 1,
    monthActiveInc: 1,
    weekActiveInc: 1,
    dateStr: 1,
  },
};

function contextFacetBranch(filterUser) {
  return lookupsAndActivityStages(filterUser).concat([CONTEXT_PROJECT]);
}

function bucketUpdateStages() {
  return [
    setYearBucket("$$yearActiveInc", "$$yearTotalDays"),
    setMonthBucket("$$monthActiveInc", "$$monthTotalDays"),
    setWeekBucket("$$weekActiveInc", "$$weekTotalDays"),
    setDayBucket(),
    setWeekdayBucket(),
    setStreaks(),
  ];
}

const USER_BUCKET_PIPELINE = bucketUpdateStages();
const COMBINED_BUCKET_PIPELINE = bucketUpdateStages();

/** App Services does not reliably pass aggregate/update `let` vars — inline literals instead. */
function substituteLetVars(value, vars) {
  if (typeof value === "string" && value.startsWith("$$")) {
    const name = value.slice(2);
    if (Object.prototype.hasOwnProperty.call(vars, name)) {
      return vars[name];
    }
  }
  if (Array.isArray(value)) {
    return value.map((item) => substituteLetVars(item, vars));
  }
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, val] of Object.entries(value)) {
      result[key] = substituteLetVars(val, vars);
    }
    return result;
  }
  return value;
}

function applyLetVarsToPipeline(pipeline, vars) {
  return substituteLetVars(pipeline, vars);
}

function buildCommitContextPipeline(logId, elapsed, logUser) {
  return [
    { $match: { _id: logId } },
    {
      $set: {
        logTs: "$timestamp",
        logId,
        elapsed,
        logUser,
      },
    },
    periodKeySetStage("$logTs"),
    streakKeySetStage(),
    { $set: { dateStr: dateToString("%Y-%m-%d %H:%M:%S", "$logTs") } },
    {
      $facet: {
        user: contextFacetBranch(true),
        combined: contextFacetBranch(false),
      },
    },
    {
      $project: {
        user: { $arrayElemAt: ["$user", 0] },
        combined: { $arrayElemAt: ["$combined", 0] },
      },
    },
  ];
}

function buildHighscoreFetchPipeline(logUser) {
  return [
    { $match: { _id: { $in: ["Highscores", logUser, "Combined"] } } },
    {
      $group: {
        _id: null,
        docs: { $push: { k: "$_id", v: "$$ROOT" } },
      },
    },
  ];
}

module.exports = {
  APP_TIMEZONE,
  applyLetVarsToPipeline,
  buildCommitContextPipeline,
  buildHighscoreFetchPipeline,
  USER_BUCKET_PIPELINE,
  COMBINED_BUCKET_PIPELINE,
};
