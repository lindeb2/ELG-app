/**
 * Highscore compare/update from aggregation docs.
 * SSOT mirror of highscore_commit.py (commit path only).
 *
 * Repo source only — not deployed to Atlas directly.
 * Bundled into commitLog.js via: node bundle.js
 *
 * Uses APP_TIMEZONE from commit_pipelines.js and buildHighscoreFetchPipeline()
 * (same file after bundling).
 */

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

module.exports = {
  updateHighscores,
};
