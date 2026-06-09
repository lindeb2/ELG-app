# Atlas Functions — log commit (reference / historical)

> **Status (2025+):** MongoDB Atlas App Services reached **end-of-life** in September 2025.
> Custom HTTPS Endpoints and the Data API are **removed** — you cannot create new endpoints.
> **`commitLog.js` remains useful as documentation** of the co-located commit logic, but a
> desktop Python app should use the **local PyMongo path** in `log_commit.py` (already in production).

**Supported production path:** `log_commit.commit_log()` → PyMongo `with_transaction` over your Atlas cluster connection in `timetable_db.py`. Set `"atlas_commit": { "enabled": false }` in `config.json`.

See [REVIEW.md](./REVIEW.md) for the original engineering review.

---

## App Services deprecation — what this means for you

| Feature | Status |
|---------|--------|
| Custom HTTPS Endpoints | **Removed** (EOL Sep 2025) |
| Atlas Functions (App Services) | **Deprecated** — no supported external invoke path for desktop clients |
| PyMongo + `with_transaction` | **Supported** — what Timetable uses today |
| `commitLog.js` in this repo | Reference implementation only |

MongoDB’s official migration guidance is to use **native drivers** (PyMongo) from your application, not HTTP middleware to Atlas.

The co-located “one round-trip” idea was sound, but it required App Services hosting that no longer exists for new setups. Your existing Python pipelines (`commit_pipeline.py`, `highscore_commit.py`) already implement the same logic correctly.

### If you still want a single HTTP round-trip

You would need to **host your own** thin API (e.g. AWS Lambda, Azure Functions, Cloud Run) that runs the same logic with the MongoDB Node or Python driver next to the cluster. That is separate infrastructure MongoDB no longer provides. For a desktop Timetable app, **PyMongo in one transaction is the practical choice**.

---

## Historical architecture (App Services era)

**Architecture:** Python client makes **one** HTTPS call; the function runs insert → context → bucket updates → highscores inside a single `withTransaction` block co-located with the cluster.

---

## How Atlas Functions work (important)

Atlas App Services Functions follow these rules:

| Rule | Implication for this project |
|------|------------------------------|
| **One Function = one JavaScript file** | Filename must match the function name (e.g. `commitLog.js` → function `commitLog`). |
| **`require("./localFile")` is not supported** | You cannot import sibling function files the way you would in Node.js. |
| **`context.functions.execute("name", …)`** | Calls a **separate** Function invocation — each call is its own runtime context. |
| **`require("package")`** | Only for **npm packages added to the app** in App Services settings — not for local repo files. |

### Why everything is in one file

This commit path uses a **MongoDB transaction** (`session.withTransaction`). All reads and writes must share the same session. That cannot span multiple Functions called via `context.functions.execute`, because each execute is a separate invocation without your transaction session.

So the supported patterns here are:

1. **Recommended:** One Function file (`commitLog.js`) containing all helper code inline — **this is what we ship.**
2. **Not viable for commit:** Split helpers into separate Functions and `context.functions.execute` them.
3. **Alternative:** Extract shared code into an **npm package**, add it to the app, and `require("your-package")` from `commitLog.js` (more setup; only worth it if you reuse code across many functions).

---

## Files in this folder

| File | Deploy to Atlas? | Purpose |
|------|------------------|---------|
| **`commitLog.js`** | **Yes — this is the only file you paste/create in Atlas** | Generated bundle; function name must be `commitLog`. |
| `commit_pipelines.js` | No | Editable source — aggregation pipelines. |
| `highscore_logic.js` | No | Editable source — highscore compare logic. |
| `commit_log.js` | No | Editable source — transaction orchestrator. |
| `bundle.js` | No | Regenerates `commitLog.js` after you edit the source modules. |

**Python SSOT to keep in sync:**

| Python | JavaScript source |
|--------|-------------------|
| `log_commit.py` | `commit_log.js` |
| `commit_pipeline.py` | `commit_pipelines.js` |
| `highscore_commit.py` (commit path) | `highscore_logic.js` |
| `period_model.py` (pipeline helpers) | embedded in `commit_pipelines.js` |

After editing any source module, regenerate the deployable file:

```bash
cd "Project Folder/atlas_functions"
node bundle.js
```

Then copy the updated `commitLog.js` into Atlas (or re-paste in the UI).

---

## Atlas setup (step by step)

### 1. MongoDB data source

In **App Services → Linked Data Sources**, link your Atlas cluster (if not already linked). Note the **service name** (often `mongodb-atlas` or a custom name like `Cluster0`).

### 2. Create the Function

1. Go to **App Services → Functions**.
2. Click **Create New Function**.
3. Name it **`commitLog`** (must match filename).
4. Paste the contents of **`commitLog.js`** from this repo.
5. At the top of the file, set `MONGODB_SERVICE_NAME` to your linked service name (default for new apps is often `mongodb-atlas`; yours may differ, e.g. `Cluster0`):

```javascript
const MONGODB_SERVICE_NAME = "mongodb-atlas"; // ← must match Linked Data Source name
```

Transactions use `context.services.get(...).startSession()` — **not** `db.getMongo().startSession()` (that is the standalone Node driver API and does not exist in App Services).

`session.withTransaction()` in App Services does not reliably return the callback result — the function captures it in a local variable and returns that after commit.

Log insert uses `insertOne` with `new Date(Date.now() - ms_since_local_start)` because App Services `findOneAndUpdate` does not accept aggregation-pipeline updates (the Python path uses `$$NOW` via pipeline upsert).

Pipeline `let` variables (`$$logId`, etc.) are also unreliable in App Services — the function inlines them as literals via `buildCommitContextPipeline` and `applyLetVarsToPipeline` instead of passing `{ let: … }`.

Date strings for highscores use `$dateToString` in the context pipeline (`Europe/Stockholm`) — App Services does not provide the JavaScript `Intl` API.

6. Confirm these constants match your cluster:

```javascript
const DB_NAME = "ELG-Database";
const LOGS_COLLECTION = "Timetable";
const AGGREGATIONS_COLLECTION = "Timetable Aggregations";
```

7. Save.

Do **not** create separate functions for `commit_pipelines` or `highscore_logic` — they are not standalone entry points.

### 3. Authentication and authorization

- Configure **Authentication** providers your Python/desktop client will use (API key, custom JWT, etc.).
- Add a **Function access rule** (or custom rule on the endpoint) so only authenticated callers can invoke `commitLog`.
- **Do not trust `user` from the payload** without verifying it matches the authenticated identity (e.g. map `context.user.data.email` or a custom user field to your timetable username).

### 4. Expose the Function to your client

Depending on your client setup, invoke via one of:

- **HTTPS endpoint** — App Services → HTTPS Endpoints → route that calls `commitLog`.
- **Realm SDK** — `app.callFunction("commitLog", payload)` (or equivalent in your SDK version).
- **Admin API** — server-side only.

Check your App Services app’s **Clients** section for the app ID and endpoint URL.

### 5. Smoke test in Atlas

Use the Function editor **Run** panel with:

```json
{
  "user": "YourUsername",
  "name": "Test",
  "description": "Atlas function smoke test",
  "elapsed_time": 60,
  "ms_since_local_start": 0
}
```

Expect `{ "success": true, "broken_records": [...], "timestamp": "..." }` or a validation error if fields are wrong.

---

## Request / response

**Request** (same fields as Python `commit_log`):

```json
{
  "user": "Johan",
  "name": "Deep work",
  "description": "Feature X",
  "elapsed_time": 3600,
  "ms_since_local_start": 150
}
```

**Success:**

```json
{
  "success": true,
  "timestamp": "2026-06-08T12:00:00.000Z",
  "broken_records": []
}
```

`broken_records` matches the shape consumed by `Timetable.py` notifications.

**Failure:**

```json
{
  "success": false,
  "error": "…",
  "retryable": true
}
```

Retry the call when `retryable` is true (transient transaction error).

---

## Python client (Timetable app)

**Use the local path.** No config change needed beyond:

```json
{
  "user": "Johan_Dev",
  "atlas_commit": {
    "enabled": false
  }
}
```

`Timetable.py` calls `log_commit.commit_log()` → PyMongo transaction via `timetable_db.client`.

If `atlas_commit.enabled` is accidentally `true`, commit fails immediately with a message explaining App Services EOL — set it back to `false`.

The deprecated `atlas_commit.py` module is kept only so that misconfiguration fails clearly.

---

## What `commitLog` implements

Full parity with the Python commit transaction:

- Log insert with server timestamp minus client offset
- Period keys in `Europe/Stockholm` (via MongoDB date operators)
- User + combined bucket updates (year/month/week/day/weekday, active days, streaks)
- Highscores: personal, global, combined — time, activity ratio, consecutive days/weeks
- Transaction retries via `session.withTransaction`

Not included (still Python-only admin paths): `rebuild_highscores_from_logs`, `update_highscore`, recalculate scripts.

---

## Maintenance workflow

1. Change Python SSOT first (`commit_pipeline.py`, `highscore_commit.py`, `log_commit.py`).
2. Mirror the change in the matching **source** `.js` file under `atlas_functions/`.
3. Run `node bundle.js`.
4. Update `commitLog.js` in Atlas.
5. Run a parity smoke test against a dev database.

---

## What not to do

| Pattern | Why it fails here |
|---------|-------------------|
| `require("./commit_pipelines")` in Atlas | Not a documented/supported pattern for local function files. |
| Separate `commit_pipelines` Function + `execute` | Breaks the shared MongoDB transaction session. |
| Multiple small Functions for each pipeline step | Same — no shared `session` across invocations. |
