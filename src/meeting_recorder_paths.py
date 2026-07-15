"""Filesystem layout for meeting recordings, under the app's per-user data dir.

Mirrors storage.py's use of platformdirs.user_data_dir (not a path inside the
repo or next to the executable) and reuses period_model.period_keys as the
single source of truth for ISO-week folder naming, so this never drifts from
the week math the rest of the app (Timetable aggregations, meeting.py's
current/next week) already relies on.

Layout produced here:
    meetings/2026-W29/
      audio/
        raw/{userId}_{username}.wav
        combined_mixdown.wav        # Step 5: ffmpeg amix+loudnorm of all raw tracks
      transcripts/
        per_channel/{userId}.json   # Step 2: faster-whisper word-level output
        merged.md                   # Step 2: chronological, speaker-labeled transcript
        baseline_metrics.json       # Step 2: processing time / peak RAM for this run
        combined.json               # Step 5: faster-whisper output on the mixdown
        combined_metrics.json       # Step 5: processing time / peak RAM for that pass
        reconciled.md                # Step 6: LLM merge of merged.md + combined.json
        reconciliation_metrics.json  # Step 6: processing time for that LLM call
      summaries/
        summary.md                  # Step 3: structured, agenda-based meeting summary
      meta.json     # meeting start/end, participants, join/leave offsets

models/
  faster-whisper-{model_size}/  # see faster_whisper_model_dir
  ffmpeg/                       # Step 5: static ffmpeg binary, see ffmpeg_binary_path

Every later step (merging, transcription, etc.) adds subfolders under the
same meetings/{week}/ directory.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from platformdirs import user_data_dir

from period_model import period_keys
from storage import APP_AUTHOR, APP_NAME

_INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def get_meetings_dir() -> Path:
    path = Path(user_data_dir(APP_NAME, APP_AUTHOR)) / "meetings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_models_dir() -> Path:
    """Local models directory for downloaded Meeting Recorder models (the
    faster-whisper CT2 files meeting_transcriber.py uses, the GGUF file
    meeting_summarizer.py uses) - a sibling of meetings/ under the same
    per-user platformdirs data directory, not next to the executable and
    never bundled into the installer (see step-4-packaging.md). Populated by
    meeting_recorder_setup.py's one-time setup, not on every fresh install.
    """
    path = Path(user_data_dir(APP_NAME, APP_AUTHOR)) / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def faster_whisper_model_dir(model_size: str) -> Path:
    """Where a locally-downloaded faster-whisper CT2 model for model_size
    lives, if meeting_recorder_setup.py has fetched it. Doesn't create the
    directory - callers check .is_dir() to tell 'downloaded' from 'not yet'.
    """
    return get_models_dir() / f"faster-whisper-{model_size}"


def ffmpeg_binary_path() -> Path:
    """Where the static ffmpeg binary downloaded by meeting_recorder_setup.py's
    high-quality-mode setup lives (see faster_whisper_model_dir above for the
    same idea applied to the transcription model) - see
    meeting_combined_transcriber.py (Step 5). Doesn't create the directory -
    callers check .is_file() to tell 'downloaded' from 'not yet'.
    """
    filename = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    return get_models_dir() / "ffmpeg" / filename


def week_folder(dt: datetime) -> Path:
    keys = period_keys(dt)  # reuse existing SSOT for calendar/week logic
    folder = get_meetings_dir() / f"{keys.iso_week_year}-W{keys.iso_week}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def audio_dir(dt: datetime) -> Path:
    path = week_folder(dt) / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_audio_dir(dt: datetime) -> Path:
    path = audio_dir(dt) / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def combined_mixdown_path(dt: datetime) -> Path:
    """ffmpeg amix+loudnorm output of every raw_audio_dir() channel for this
    meeting (see meeting_combined_transcriber.mixdown_meeting_audio, Step 5) -
    a sibling of raw/, not inside it.
    """
    return audio_dir(dt) / "combined_mixdown.wav"


def transcripts_dir(dt: datetime) -> Path:
    path = week_folder(dt) / "transcripts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def per_channel_transcripts_dir(dt: datetime) -> Path:
    path = transcripts_dir(dt) / "per_channel"
    path.mkdir(parents=True, exist_ok=True)
    return path


def per_channel_transcript_path(dt: datetime, user_id: int) -> Path:
    return per_channel_transcripts_dir(dt) / f"{user_id}.json"


def merged_transcript_path(dt: datetime) -> Path:
    return transcripts_dir(dt) / "merged.md"


def transcription_metrics_path(dt: datetime) -> Path:
    return transcripts_dir(dt) / "baseline_metrics.json"


def combined_transcript_path(dt: datetime) -> Path:
    """faster-whisper output on combined_mixdown_path()'s mixdown (Step 5) -
    a second, differently-flavored transcript alongside Step 2's
    per_channel/merged.md, not a replacement for either.
    """
    return transcripts_dir(dt) / "combined.json"


def combined_metrics_path(dt: datetime) -> Path:
    return transcripts_dir(dt) / "combined_metrics.json"


def reconciled_transcript_path(dt: datetime) -> Path:
    """Step 6 output (see meeting_reconciler.py) - the local LLM's best-of-both
    merge of merged_transcript_path()'s speaker attribution and
    combined_transcript_path()'s wording/context, a sibling of both under the
    same transcripts/ folder rather than a replacement for either."""
    return transcripts_dir(dt) / "reconciled.md"


def reconciliation_metrics_path(dt: datetime) -> Path:
    return transcripts_dir(dt) / "reconciliation_metrics.json"


def summaries_dir(dt: datetime) -> Path:
    path = week_folder(dt) / "summaries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def summary_path(dt: datetime) -> Path:
    return summaries_dir(dt) / "summary.md"


def meta_path(dt: datetime) -> Path:
    return week_folder(dt) / "meta.json"


def safe_filename_component(text: str) -> str:
    """Strip characters that are unsafe in Windows filenames."""
    cleaned = "".join(c for c in text if c not in _INVALID_FILENAME_CHARS).strip()
    cleaned = cleaned.rstrip(". ")  # trailing dot/space is invalid on Windows
    return cleaned or "user"


def user_wav_filename(user_id: int, username: str) -> str:
    return f"{user_id}_{safe_filename_component(username)}.wav"


def write_meeting_meta(dt: datetime, data: dict) -> Path:
    """Atomically write meta.json for the week containing dt (tmp + os.replace,
    matching storage.py's save_data pattern)."""
    file_path = meta_path(dt)
    tmp_path = file_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, default=str)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp_path, file_path)
    return file_path
