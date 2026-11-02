"""Paths, model ids and tuning constants for Meeting Mike."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

APP_NAME = "ELG-app"
APP_AUTHOR = "ELG Studio"

MARKER_FILENAME = "meeting_host.json"
MARKER_SCHEMA_VERSION = 1

# Audio: one canonical timeline for every stream.
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
DISCORD_SAMPLE_RATE = 48_000
DISCORD_CHANNELS = 2
DECIMATION = DISCORD_SAMPLE_RATE // SAMPLE_RATE  # 3

# A Discord voice frame is 20 ms; only pad when a gap exceeds one frame.
FRAME_MS = 20
PAD_TOLERANCE_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

# Someone arriving more than this late is flagged as a late joiner.
LATE_JOIN_GRACE_SECONDS = 5.0
# Voice states sometimes arrive a beat after on_ready; wait this long.
VOICE_JOIN_WAIT_SECONDS = 15.0

FALLBACK_VOICE_CHANNEL_NAME = "\U0001F935boardroom"

MIX_MODEL = "distil-large-v3"
USER_MODEL = "small.en"
WHISPER_LANGUAGE = "en"

LLM_REPO = "Qwen/Qwen3-8B-GGUF"
LLM_FILENAME = "Qwen3-8B-Q4_K_M.gguf"
# hf_hub_download needs the file size plus a little slack (~5.4 GB).
LLM_BYTES = 5_000_000_000
LLM_CONTEXT = 32_768
LLM_PORT = 8765
LLM_TEMPERATURE = 0.2
# Leave headroom below the 32k native context for the response.
LLM_PROMPT_TOKEN_BUDGET = 24_000
LLM_STARTUP_TIMEOUT_SECONDS = 300.0
LLM_REQUEST_TIMEOUT_SECONDS = 900.0

# Mongo recording lock: older than this means a crashed host, so steal it.
LOCK_STALE_SECONDS = 20 * 60

MONGO_DB_NAME = "ELG-Database"
TIMETABLE_COLLECTION = "Timetable"
AGGREGATIONS_COLLECTION = "Timetable Aggregations"
STATUS_MEETING_COLLECTION = "Status Meeting"
LOCK_DOC_ID = "Meeting Mike"


def _env_data_dir() -> Path | None:
    raw = os.environ.get("ELG_DATA_DIR")
    return Path(raw) if raw else None


def data_dir(override: str | Path | None = None) -> Path:
    """ELG per-user data dir; must match src/storage.get_data_dir()."""
    if override:
        path = Path(override)
    else:
        env = _env_data_dir()
        if env is not None:
            path = env
        else:
            from platformdirs import user_data_dir

            path = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def mike_dir(base: Path) -> Path:
    path = base / "meeting_mike"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir(base: Path) -> Path:
    path = mike_dir(base) / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(base: Path) -> Path:
    path = mike_dir(base) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def meeting_dir(base: Path, started_at: datetime) -> Path:
    """{data}/meeting_mike/meetings/{iso_year}-W{iso_week}/{utc_start}/"""
    iso_year, iso_week, _ = started_at.isocalendar()
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    path = mike_dir(base) / "meetings" / f"{iso_year}-W{iso_week:02d}" / stamp
    (path / "audio" / "users").mkdir(parents=True, exist_ok=True)
    (path / "transcripts" / "users").mkdir(parents=True, exist_ok=True)
    return path
