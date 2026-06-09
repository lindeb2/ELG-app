/**
 * Transaction orchestrator — repo source only.
 * Mirrors log_commit.py. Bundled into commitLog.js via: node bundle.js
 *
 * Deploy commitLog.js to Atlas (single file), not this file.
 *
 * Uses symbols from commit_pipelines.js and highscore_logic.js (same file after bundling).
 */

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
