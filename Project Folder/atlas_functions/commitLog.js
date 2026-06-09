/**
 * Atlas App Services Function — DEPLOY THIS FILE ONLY.
 *
 * In Atlas: App Services → Functions → create function named "commitLog"
 * The filename must match the function name (commitLog.js).
 *
 * Do not use require("./…") for sibling files — Atlas does not support that pattern.
 * This file is generated from the source modules below. Regenerate after edits:
 *   node bundle.js
 *
 * Source modules (repo only, not deployed):
 *   - commit_pipelines.js
 *   - highscore_logic.js
 *   - commit_log.js
 */

/* --- commit_pipelines --- */

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

/* --- highscore_logic --- */

const PERIOD_TYPES = ["Year", "Month", "Week", "Day"];
const LIFETIME_TYPE = "Lifetime";

function deepCopy(value) {
  return JSON.parse(JSON.stringify(value));
}

function emptyConsecutive() {
  return {
    days: { value: 0, date: null },
    weeks: { value: 0, date: null },
  };
}

function emptyUserHighscores() {
  return {
    Year: {
      time: { value: 0, date: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null },
    },
    Month: {
      time: { value: 0, date: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null },
    },
    Week: {
      time: { value: 0, date: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null },
    },
    Day: { time: { value: 0, date: null } },
    consecutive: emptyConsecutive(),
  };
}

function emptyGlobalScope() {
  return {
    Year: {
      time: { value: 0, date: null, user: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null, user: null },
    },
    Month: {
      time: { value: 0, date: null, user: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null, user: null },
    },
    Week: {
      time: { value: 0, date: null, user: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null, user: null },
    },
    Day: { time: { value: 0, date: null, user: null } },
    consecutive: {
      days: { value: 0, date: null, user: null },
      weeks: { value: 0, date: null, user: null },
    },
  };
}

function emptyCombinedScope() {
  return {
    Year: {
      time: { value: 0, date: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null },
    },
    Month: {
      time: { value: 0, date: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null },
    },
    Week: {
      time: { value: 0, date: null },
      activity: { value: 0, active_days: 0, total_days: 0, date: null },
    },
    Day: { time: { value: 0, date: null } },
    consecutive: emptyConsecutive(),
  };
}

function ensureScopeShape(scope, globalScope) {
  if (!scope.consecutive) {
    scope.consecutive = globalScope
      ? {
          days: { value: 0, date: null, user: null },
          weeks: { value: 0, date: null, user: null },
        }
      : emptyConsecutive();
  }
}

function defaultHighscoresDoc(user) {
  return {
    _id: "Highscores",
    [user]: emptyUserHighscores(),
    Global: emptyGlobalScope(),
    Combined: emptyCombinedScope(),
  };
}

function periodKeysFromContext(ctx) {
  return {
    year: ctx.yearStr,
    month: ctx.monthStr,
    day: ctx.dayStr,
    iso_week_year: ctx.weekYearStr,
    iso_week: ctx.weekStr,
    weekday: ctx.weekdayStr,
  };
}

function streakValue(agg, streakKind) {
  return parseInt((((agg.streaks || {})[streakKind] || {}).best || 0), 10);
}

function consecutiveValue(highscore, streakKind) {
  return parseInt((((highscore.consecutive || {})[streakKind] || {}).value || 0), 10);
}

function streakBrokenValue(streak) {
  return {
    streak,
    total_time: null,
    active_days: null,
    total_days: null,
    percentage: null,
  };
}

function bucketDoc(agg, keys, period) {
  const years = agg.years || {};
  if (period === "year") return years[keys.year] || {};
  if (period === "month") {
    return (((years[keys.year] || {}).months || {})[keys.month]) || {};
  }
  if (period === "week") {
    return (((years[keys.iso_week_year] || {}).weeks || {})[keys.iso_week]) || {};
  }
  if (period === "day") {
    const monthBucket = (years[keys.year] || {}).months || {};
    const dayBucket = (monthBucket[keys.month] || {}).days || {};
    return dayBucket[keys.day] || {};
  }
  throw new Error(`Unknown period: ${period}`);
}

function periodStats(userAgg, combinedAgg, keys) {
  const mapping = {
    Year: "year",
    Month: "month",
    Week: "week",
    Day: "day",
  };
  const stats = {};
  for (const [timeType, period] of Object.entries(mapping)) {
    const userBucket = bucketDoc(userAgg, keys, period);
    const combinedBucket = bucketDoc(combinedAgg, keys, period);
    const entry = {
      user_time: parseInt(userBucket.time || 0, 10),
      combined_time: parseInt(combinedBucket.time || 0, 10),
      user_activity: null,
      combined_activity: null,
    };
    if (timeType !== "Day") {
      entry.user_activity = {
        active_days: parseInt(userBucket.active_days || 0, 10),
        total_days: parseInt(userBucket.total_days || 0, 10),
        activity_ratio: parseFloat(userBucket.activity_ratio || 0),
      };
      entry.combined_activity = {
        active_days: parseInt(combinedBucket.active_days || 0, 10),
        total_days: parseInt(combinedBucket.total_days || 0, 10),
        activity_ratio: parseFloat(combinedBucket.activity_ratio || 0),
      };
    }
    stats[timeType] = entry;
  }
  return stats;
}

function brokenPair(scope, timeType, metric, oldValue, newValue, oldDate, newDate, oldUser) {
  const oldRecord = {
    scope,
    time_type: timeType,
    metric,
    value: oldValue,
    date: oldDate,
  };
  const newRecord = {
    scope,
    time_type: timeType,
    metric,
    value: newValue,
    date: newDate,
  };
  if (scope === "global" && oldUser !== undefined && oldUser !== null) {
    oldRecord.user = oldUser;
  }
  return { old_record: oldRecord, new_record: newRecord };
}

function applyPeriodHighscores(
  highscores,
  user,
  dateStr,
  timeType,
  stats,
  { globalScope = true, combinedScope = true } = {}
) {
  const brokenRecords = [];
  const userTime = stats.user_time;
  const combinedTime = stats.combined_time;
  const userActivity = stats.user_activity;
  const combinedActivity = stats.combined_activity;

  if (userTime > highscores[user][timeType].time.value) {
    brokenRecords.push(
      brokenPair(
        "personal",
        timeType,
        "total_time",
        {
          total_time: highscores[user][timeType].time.value,
          active_days: null,
          total_days: null,
          percentage: null,
        },
        {
          total_time: userTime,
          active_days: null,
          total_days: null,
          percentage: null,
        },
        highscores[user][timeType].time.date,
        dateStr
      )
    );
    highscores[user][timeType].time = { value: userTime, date: dateStr };

    if (globalScope && userTime > highscores.Global[timeType].time.value) {
      brokenRecords.push(
        brokenPair(
          "global",
          timeType,
          "total_time",
          {
            total_time: highscores.Global[timeType].time.value,
            active_days: null,
            total_days: null,
            percentage: null,
          },
          {
            total_time: userTime,
            active_days: null,
            total_days: null,
            percentage: null,
          },
          highscores.Global[timeType].time.date,
          dateStr,
          highscores.Global[timeType].time.user
        )
      );
      highscores.Global[timeType].time = {
        value: userTime,
        date: dateStr,
        user,
      };
    }
  }

  if (
    userActivity &&
    timeType !== "Day" &&
    userActivity.activity_ratio > highscores[user][timeType].activity.value
  ) {
    brokenRecords.push(
      brokenPair(
        "personal",
        timeType,
        "days_active",
        {
          total_time: null,
          active_days: highscores[user][timeType].activity.active_days,
          total_days: highscores[user][timeType].activity.total_days,
          percentage: highscores[user][timeType].activity.value,
        },
        {
          total_time: null,
          active_days: userActivity.active_days,
          total_days: userActivity.total_days,
          percentage: userActivity.activity_ratio,
        },
        highscores[user][timeType].activity.date,
        dateStr
      )
    );
    highscores[user][timeType].activity = {
      value: userActivity.activity_ratio,
      active_days: userActivity.active_days,
      total_days: userActivity.total_days,
      date: dateStr,
    };

    if (
      globalScope &&
      userActivity.activity_ratio > highscores.Global[timeType].activity.value
    ) {
      brokenRecords.push(
        brokenPair(
          "global",
          timeType,
          "days_active",
          {
            total_time: null,
            active_days: highscores.Global[timeType].activity.active_days,
            total_days: highscores.Global[timeType].activity.total_days,
            percentage: highscores.Global[timeType].activity.value,
          },
          {
            total_time: null,
            active_days: userActivity.active_days,
            total_days: userActivity.total_days,
            percentage: userActivity.activity_ratio,
          },
          highscores.Global[timeType].activity.date,
          dateStr,
          highscores.Global[timeType].activity.user
        )
      );
      highscores.Global[timeType].activity = {
        value: userActivity.activity_ratio,
        active_days: userActivity.active_days,
        total_days: userActivity.total_days,
        date: dateStr,
        user,
      };
    }
  }

  if (combinedScope && combinedTime > highscores.Combined[timeType].time.value) {
    brokenRecords.push(
      brokenPair(
        "combined",
        timeType,
        "total_time",
        {
          total_time: highscores.Combined[timeType].time.value,
          active_days: null,
          total_days: null,
          percentage: null,
        },
        {
          total_time: combinedTime,
          active_days: null,
          total_days: null,
          percentage: null,
        },
        highscores.Combined[timeType].time.date,
        dateStr
      )
    );
    highscores.Combined[timeType].time = { value: combinedTime, date: dateStr };
  }

  if (
    combinedScope &&
    combinedActivity &&
    timeType !== "Day" &&
    combinedActivity.activity_ratio > highscores.Combined[timeType].activity.value
  ) {
    brokenRecords.push(
      brokenPair(
        "combined",
        timeType,
        "days_active",
        {
          total_time: null,
          active_days: highscores.Combined[timeType].activity.active_days,
          total_days: highscores.Combined[timeType].activity.total_days,
          percentage: highscores.Combined[timeType].activity.value,
        },
        {
          total_time: null,
          active_days: combinedActivity.active_days,
          total_days: combinedActivity.total_days,
          percentage: combinedActivity.activity_ratio,
        },
        highscores.Combined[timeType].activity.date,
        dateStr
      )
    );
    highscores.Combined[timeType].activity = {
      value: combinedActivity.activity_ratio,
      active_days: combinedActivity.active_days,
      total_days: combinedActivity.total_days,
      date: dateStr,
    };
  }

  return brokenRecords;
}

function applyConsecutiveHighscores(
  highscores,
  user,
  dateStr,
  userAgg,
  combinedAgg,
  { globalScope = true, combinedScope = true } = {}
) {
  const brokenRecords = [];
  const userDays = streakValue(userAgg, "days");
  const userWeeks = streakValue(userAgg, "weeks");
  const combinedDays = streakValue(combinedAgg, "days");
  const combinedWeeks = streakValue(combinedAgg, "weeks");

  const checks = [
    ["consecutive_days", "days", userDays, combinedDays],
    ["consecutive_weeks", "weeks", userWeeks, combinedWeeks],
  ];

  for (const [metric, streakKind, userValue, combinedValue] of checks) {
    if (userValue > consecutiveValue(highscores[user], streakKind)) {
      brokenRecords.push(
        brokenPair(
          "personal",
          LIFETIME_TYPE,
          metric,
          streakBrokenValue(consecutiveValue(highscores[user], streakKind)),
          streakBrokenValue(userValue),
          highscores[user].consecutive[streakKind].date,
          dateStr
        )
      );
      highscores[user].consecutive[streakKind] = { value: userValue, date: dateStr };

      if (globalScope && userValue > consecutiveValue(highscores.Global, streakKind)) {
        brokenRecords.push(
          brokenPair(
            "global",
            LIFETIME_TYPE,
            metric,
            streakBrokenValue(consecutiveValue(highscores.Global, streakKind)),
            streakBrokenValue(userValue),
            highscores.Global.consecutive[streakKind].date,
            dateStr,
            highscores.Global.consecutive[streakKind].user
          )
        );
        highscores.Global.consecutive[streakKind] = {
          value: userValue,
          date: dateStr,
          user,
        };
      }
    }

    if (combinedScope && combinedValue > consecutiveValue(highscores.Combined, streakKind)) {
      brokenRecords.push(
        brokenPair(
          "combined",
          LIFETIME_TYPE,
          metric,
          streakBrokenValue(consecutiveValue(highscores.Combined, streakKind)),
          streakBrokenValue(combinedValue),
          highscores.Combined.consecutive[streakKind].date,
          dateStr
        )
      );
      highscores.Combined.consecutive[streakKind] = {
        value: combinedValue,
        date: dateStr,
      };
    }
  }

  return brokenRecords;
}

async function fetchAggDocs(aggColl, user, session) {
  const rows = await aggColl
    .aggregate(buildHighscoreFetchPipeline(user), { session })
    .toArray();

  let docsById = {};
  if (rows.length && rows[0].docs) {
    docsById = Object.fromEntries(rows[0].docs.map((entry) => [entry.k, entry.v]));
  }

  let highscores = docsById.Highscores;
  if (!highscores) {
    highscores = defaultHighscoresDoc(user);
  } else {
    highscores = deepCopy(highscores);
    if (!highscores[user]) {
      highscores[user] = emptyUserHighscores();
    } else {
      ensureScopeShape(highscores[user], false);
    }
  }

  if (!highscores.Global) {
    highscores.Global = emptyGlobalScope();
  } else {
    ensureScopeShape(highscores.Global, true);
  }

  if (!highscores.Combined) {
    highscores.Combined = emptyCombinedScope();
  } else {
    ensureScopeShape(highscores.Combined, false);
  }

  const userAgg = docsById[user] || {};
  const combinedAgg = docsById.Combined || {};
  return { highscores, userAgg, combinedAgg };
}

async function updateHighscores(aggColl, user, contextUser, session) {
  const keys = periodKeysFromContext(contextUser);
  const dateStr = contextUser.dateStr;
  if (!dateStr) {
    throw new Error("commit_log: context missing dateStr");
  }
  const { highscores, userAgg, combinedAgg } = await fetchAggDocs(aggColl, user, session);
  const stats = periodStats(userAgg, combinedAgg, keys);

  const allBroken = [];
  for (const timeType of PERIOD_TYPES) {
    allBroken.push(
      ...applyPeriodHighscores(highscores, user, dateStr, timeType, stats[timeType])
    );
  }
  allBroken.push(
    ...applyConsecutiveHighscores(highscores, user, dateStr, userAgg, combinedAgg)
  );

  await aggColl.replaceOne({ _id: "Highscores" }, highscores, { session, upsert: true });
  return allBroken;
}

/* --- commit orchestrator --- */

const DB_NAME = "ELG-Database";
const AGGREGATIONS_COLLECTION = "Timetable Aggregations";

// Replace with your linked MongoDB service name in Atlas App Services.
const MONGODB_SERVICE_NAME = "mongodb-atlas";

function validateArg(arg) {
  if (!arg || typeof arg !== "object") {
    throw new Error("payload must be an object");
  }
  const { user, name, description, elapsed_time, ms_since_local_start } = arg;
  if (typeof user !== "string" || !user.trim()) {
    throw new Error("user is required");
  }
  if (typeof name !== "string") {
    throw new Error("name is required");
  }
  if (typeof description !== "string") {
    throw new Error("description is required");
  }
  const elapsed = Number(elapsed_time);
  if (!Number.isInteger(elapsed) || elapsed < 0) {
    throw new Error("elapsed_time must be a non-negative integer");
  }
  const msOffset = Number(ms_since_local_start);
  if (!Number.isInteger(msOffset) || msOffset < 0) {
    throw new Error("ms_since_local_start must be a non-negative integer");
  }
  return {
    user: user.trim(),
    name: name.trim(),
    description: description.trim(),
    elapsed_time: elapsed,
    ms_since_local_start: msOffset,
  };
}

async function withTransaction(client, fn) {
  // App Services: session lives on the linked cluster client, not db.getMongo().
  // withTransaction() often does not return the callback value — capture it explicitly.
  const session = client.startSession();
  let result;
  try {
    await session.withTransaction(
      async () => {
        result = await fn(session);
      },
      {
        readPreference: "primary",
        readConcern: { level: "local" },
        writeConcern: { w: "majority" },
      }
    );
    return result;
  } finally {
    await session.endSession();
  }
}

async function commitLogTransaction(client, payload) {
  const { user, name, description, elapsed_time, ms_since_local_start } = payload;
  const db = client.db(DB_NAME);
  const logsColl = db.collection(LOGS_COLLECTION);
  const aggColl = db.collection(AGGREGATIONS_COLLECTION);

  return await withTransaction(client, async (session) => {
    const newId = new BSON.ObjectId();
    // App Services findOneAndUpdate only accepts a plain update object, not a pipeline.
    // Python uses $$NOW via pipeline upsert; here we compute the same instant at commit time.
    const timestamp = new Date(Date.now() - ms_since_local_start);

    await logsColl.insertOne(
      {
        _id: newId,
        name,
        user,
        description,
        elapsed_time,
        timestamp,
      },
      { session }
    );

    const baseLet = {
      logId: newId,
      elapsed: elapsed_time,
      logUser: user,
    };

    const contextRows = await logsColl
      .aggregate(buildCommitContextPipeline(newId, elapsed_time, user), { session })
      .toArray();

    if (!contextRows.length || !contextRows[0].user || !contextRows[0].combined) {
      throw new Error("commit_log: context pipeline returned incomplete rows");
    }

    const userCtx = contextRows[0].user;
    const combinedCtx = contextRows[0].combined;

    await aggColl.updateOne(
      { _id: user },
      applyLetVarsToPipeline(USER_BUCKET_PIPELINE, { ...baseLet, ...userCtx }),
      { session, upsert: true }
    );

    await aggColl.updateOne(
      { _id: "Combined" },
      applyLetVarsToPipeline(COMBINED_BUCKET_PIPELINE, {
        elapsed: elapsed_time,
        ...combinedCtx,
      }),
      { session, upsert: true }
    );

    const brokenRecords = await updateHighscores(aggColl, user, userCtx, session);

    return {
      success: true,
      timestamp: timestamp.toISOString(),
      broken_records: brokenRecords,
    };
  });
}

exports = async function commitLog(arg) {
  try {
    const payload = validateArg(arg);
    const client = context.services.get(MONGODB_SERVICE_NAME);
    if (!client) {
      throw new Error(`MongoDB service not found: ${MONGODB_SERVICE_NAME}`);
    }
    return await commitLogTransaction(client, payload);
  } catch (error) {
    console.error("commitLog failed:", error.message);
    const retryable =
      Array.isArray(error.errorLabels) &&
      (error.errorLabels.includes("TransientTransactionError") ||
        error.errorLabels.includes("UnknownTransactionCommitResult"));
    return {
      success: false,
      error: error.message,
      retryable,
    };
  }
};

