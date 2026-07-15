"""Step 2 — baseline transcription pipeline (see step-2-baseline-transcription-merge.md).

Turns a Step 1 recording into one correctly speaker-labeled, chronological
transcript: per-channel faster-whisper transcription + a plain-Python
timestamp merge. No audio mixing, no reconciliation LLM — since Discord
already separates speakers perfectly (one WAV per user), the cheapest
correct approach is to transcribe each channel independently and merge by
time. This is the baseline the later "quality upgrade" steps get compared
against, so it also records processing time and peak RAM for each run.

Fully local: faster-whisper (CTranslate2-backed) runs on-device, no API
calls, no server-side processing — consistent with meeting_recorder.py's
constraint.

Depends on meeting_recorder.py / meeting_recorder_paths.py (Step 1) for the
on-disk layout:
    meetings/{week}/audio/raw/{userId}_{username}.wav
    meetings/{week}/meta.json

Merge math: AlignedWaveSink (meeting_recorder.py) already silence-pads every
participant's WAV from byte 0 up to elapsed wall-clock time the moment it
first writes for that user, so every channel's local timestamp 0 already
lines up with the shared meeting-start origin — a participant's offset from
meta.json's join event is normally 0.0. This module still reads that offset
per-user (see _first_join_offset) rather than assuming 0.0, so the merge
stays correct if that padding behavior ever changes upstream.

Threading model: like meeting_recorder.py, this module has no Tk dependency
at all. transcribe_meeting() below is a plain (blocking, CPU-heavy)
function; TranscriptionJob runs it on its own daemon thread the same way
MeetingRecorder._run does, and callers marshal on_status/on_error/on_done
back to the Tk main loop themselves via self.after(0, ...) — the same
pattern meeting.py already uses for MeetingRecorder, and the same shape as
stats_viewer.py's _async_fetch/_apply_fetch worker-thread pattern.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
from faster_whisper import WhisperModel

from meeting_recorder_paths import (
    faster_whisper_model_dir,
    merged_transcript_path,
    meta_path,
    per_channel_transcript_path,
    raw_audio_dir,
    transcription_metrics_path,
)

# Only transcription pass in this step (see plan) — don't go as cheap as a
# "speaker ID only" pass would. compute_type="int8" targets CPU-only machines
# (Windows-first, no GPU assumed).
DEFAULT_MODEL_SIZE = "small"
DEFAULT_COMPUTE_TYPE = "int8"

_PEAK_RAM_POLL_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True)
class TranscriptionResult:
    merged_path: Path
    metrics_path: Path
    channels_transcribed: int
    processing_seconds: float
    peak_ram_bytes: int


class _PeakMemoryTracker:
    """Cross-platform peak RSS/working-set sampler for this process.

    On Windows, psutil.Process.memory_info() already returns an extended
    pmem tuple that includes peak_wset — the OS-tracked all-time-high
    working set for the process — so no sampling is needed there. Elsewhere
    (e.g. running this outside Windows during development), there's no
    OS-tracked peak available through psutil, so a background thread polls
    rss periodically and keeps the running max. Coarse, but good enough for
    the "roughly how much RAM did this step use" baseline number the plan
    asks for.
    """

    def __init__(self, *, poll_interval_seconds: float = _PEAK_RAM_POLL_INTERVAL_SECONDS) -> None:
        self._process = psutil.Process(os.getpid())
        self._poll_interval = poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sampled_peak_bytes = 0

    def __enter__(self) -> "_PeakMemoryTracker":
        if not hasattr(self._process.memory_info(), "peak_wset"):
            self._thread = threading.Thread(
                target=self._poll, name="peak_ram_sampler", daemon=True
            )
            self._thread.start()
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                self._sampled_peak_bytes = max(
                    self._sampled_peak_bytes, self._process.memory_info().rss
                )
            except Exception:
                pass
            self._stop.wait(self._poll_interval)

    @property
    def peak_bytes(self) -> int:
        mem = self._process.memory_info()
        peak_wset = getattr(mem, "peak_wset", None)
        if peak_wset is not None:
            return peak_wset
        return max(self._sampled_peak_bytes, mem.rss)


def _notify(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(message)
    except Exception:
        pass


def _load_meeting_meta(meeting_start_utc: datetime) -> dict:
    path = meta_path(meeting_start_utc)
    if not path.exists():
        raise RuntimeError(
            f"No meeting metadata found at {path} — was this meeting recorded (Step 1)?"
        )
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _first_join_offset(entry: dict) -> float:
    """Seconds from meeting start to this participant's WAV file origin.

    See module docstring — normally 0.0, kept dynamic for robustness.
    """
    for event in entry.get("events", []):
        if event.get("type") == "join":
            return float(event.get("offset_seconds") or 0.0)
    return 0.0


def _transcribe_channel(model: "WhisperModel", wav_path: Path) -> list[dict]:
    raw_segments, _info = model.transcribe(str(wav_path), word_timestamps=True)
    segments: list[dict] = []
    for seg in raw_segments:
        words = [
            {"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
            for w in (seg.words or [])
        ]
        segments.append(
            {"start": seg.start, "end": seg.end, "text": seg.text.strip(), "words": words}
        )
    return segments


def _atomic_write(path: Path, content: str) -> None:
    """tmp + os.replace, matching meeting_recorder_paths.write_meeting_meta's pattern."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp_path, path)


def _write_channel_json(
    meeting_start_utc: datetime, user_id: int, username: str, segments: list[dict]
) -> Path:
    path = per_channel_transcript_path(meeting_start_utc, user_id)
    payload = {"user_id": user_id, "username": username, "segments": segments}
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def _format_offset(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _write_merged_transcript(
    meeting_start_utc: datetime,
    meta: dict,
    all_segments: list[tuple[float, float, str, str]],
) -> Path:
    """all_segments: (absolute_start, absolute_end, speaker, text), already sorted."""
    lines = ["# Meeting Transcript", "", f"- Start (UTC): {meeting_start_utc.isoformat()}"]
    meeting_end = meta.get("meeting_end")
    if meeting_end:
        lines.append(f"- End (UTC): {meeting_end}")
    participants = meta.get("participants") or {}
    participant_names = sorted(
        entry.get("display_name") or entry.get("username") or user_id
        for user_id, entry in participants.items()
    )
    if participant_names:
        lines.append(f"- Participants: {', '.join(participant_names)}")
    lines.extend(["", "---", ""])

    if not all_segments:
        # Overlapping speech is preserved automatically by the global sort
        # above (see module docstring) — no reconstruction of exact word
        # interleaving is attempted, matching the plan's merge rule.
        lines.append("_No speech detected._")
    else:
        for start, _end, speaker, text in all_segments:
            lines.append(f"**[{_format_offset(start)}] {speaker}:** {text}")

    path = merged_transcript_path(meeting_start_utc)
    _atomic_write(path, "\n".join(lines) + "\n")
    return path


def _write_metrics(
    meeting_start_utc: datetime,
    *,
    model_size: str,
    compute_type: str,
    channels_transcribed: int,
    processing_seconds: float,
    peak_ram_bytes: int,
) -> Path:
    payload = {
        "model_size": model_size,
        "compute_type": compute_type,
        "channels_transcribed": channels_transcribed,
        "processing_seconds": round(processing_seconds, 3),
        "peak_ram_bytes": peak_ram_bytes,
        "peak_ram_mb": round(peak_ram_bytes / (1024 * 1024), 1),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path = transcription_metrics_path(meeting_start_utc)
    _atomic_write(path, json.dumps(payload, indent=2) + "\n")
    return path


def transcribe_meeting(
    meeting_start_utc: datetime,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    on_progress: Callable[[str], None] | None = None,
) -> TranscriptionResult:
    """Transcribe every recorded channel for one meeting and merge into merged.md.

    Blocking / CPU-heavy — see module docstring for the expected threading
    model. Raises RuntimeError if there's no meta.json (meeting wasn't
    recorded) or no channels with recorded audio.
    """
    audio_dir = raw_audio_dir(meeting_start_utc)
    meta = _load_meeting_meta(meeting_start_utc)
    participants: dict[str, dict] = meta.get("participants") or {}

    channels = [(user_id, entry) for user_id, entry in participants.items() if entry.get("file")]
    if not channels:
        raise RuntimeError("No recorded audio channels found for this meeting.")

    _notify(on_progress, f"Loading transcription model ({model_size})…")

    # Prefer a model meeting_recorder_setup.py already downloaded locally (see
    # meeting_recorder_paths.faster_whisper_model_dir) over letting faster-whisper
    # fall back to its own huggingface_hub download - a fresh install shouldn't
    # fetch anything until the feature has been explicitly enabled and set up.
    local_model_dir = faster_whisper_model_dir(model_size)
    model_path_or_size = (
        str(local_model_dir) if local_model_dir.is_dir() and any(local_model_dir.iterdir()) else model_size
    )

    start_perf = time.perf_counter()
    with _PeakMemoryTracker() as mem_tracker:
        model = WhisperModel(model_path_or_size, device="cpu", compute_type=compute_type)

        all_segments: list[tuple[float, float, str, str]] = []
        for i, (user_id_str, entry) in enumerate(channels, start=1):
            user_id = int(user_id_str)
            username = entry.get("display_name") or entry.get("username") or f"user{user_id}"
            _notify(on_progress, f"Transcribing {username} ({i}/{len(channels)})…")

            wav_path = audio_dir / entry["file"]
            segments = _transcribe_channel(model, wav_path)
            _write_channel_json(meeting_start_utc, user_id, username, segments)

            offset = _first_join_offset(entry)
            for seg in segments:
                if not seg["text"]:
                    continue
                all_segments.append((offset + seg["start"], offset + seg["end"], username, seg["text"]))

        peak_ram_bytes = mem_tracker.peak_bytes

    processing_seconds = time.perf_counter() - start_perf

    # Overlapping speech: both segments simply land in start-time order here
    # (tie-broken by speaker name for determinism) rather than being
    # word-interleaved — exactly what the plan's merge rule asks for.
    all_segments.sort(key=lambda item: (item[0], item[2]))

    merged_path = _write_merged_transcript(meeting_start_utc, meta, all_segments)
    metrics_path = _write_metrics(
        meeting_start_utc,
        model_size=model_size,
        compute_type=compute_type,
        channels_transcribed=len(channels),
        processing_seconds=processing_seconds,
        peak_ram_bytes=peak_ram_bytes,
    )

    _notify(on_progress, "Transcription complete.")

    return TranscriptionResult(
        merged_path=merged_path,
        metrics_path=metrics_path,
        channels_transcribed=len(channels),
        processing_seconds=processing_seconds,
        peak_ram_bytes=peak_ram_bytes,
    )


class TranscriptionJob:
    """Runs transcribe_meeting() on its own daemon thread.

    Mirrors MeetingRecorder's threading model (meeting_recorder.py): a
    dedicated background thread owns the work; this class does not marshal
    callbacks back to Tk itself — callers (see meeting.py) wrap
    on_status/on_error/on_done with self.after(0, ...) themselves.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(
        self,
        meeting_start_utc: datetime,
        *,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_done: Callable[[TranscriptionResult], None],
        model_size: str = DEFAULT_MODEL_SIZE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
    ) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run,
            name="meeting_transcriber",
            daemon=True,
            args=(meeting_start_utc, model_size, compute_type, on_status, on_error, on_done),
        )
        self._thread.start()

    def _run(
        self,
        meeting_start_utc: datetime,
        model_size: str,
        compute_type: str,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_done: Callable[[TranscriptionResult], None],
    ) -> None:
        try:
            result = transcribe_meeting(
                meeting_start_utc,
                model_size=model_size,
                compute_type=compute_type,
                on_progress=on_status,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller via on_error
            self._safe_call(on_error, exc)
        else:
            self._safe_call(on_done, result)
        finally:
            self._running.clear()

    @staticmethod
    def _safe_call(fn: Callable | None, *args: Any) -> None:
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            pass
