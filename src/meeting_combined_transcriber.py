"""Step 5 — quality upgrade: combined-audio mixdown + second transcription pass
(see step-5-combined-audio-upgrade.md).

Adds an optional, more expensive second transcription pass on a downmixed
combined track, to test whether full acoustic context (natural turn-taking,
cross-talk) reads more naturally than Step 2's per-channel-only transcript
on meetings with heavy overlap. Purely additive: this module never reads or
rewrites meeting_transcriber.py's per_channel/*.json or merged.md, and
combined.json is not merged or reconciled with them here — that's Step 6.

Fully local: ffmpeg (a static binary downloaded by meeting_recorder_setup.py's
high-quality-mode setup, see meeting_recorder_paths.ffmpeg_binary_path) and
faster-whisper (same dependency as meeting_transcriber.py, already in
requirements.txt) both run on-device — no API calls, no server-side
processing, consistent with meeting_recorder.py's constraint.

Depends on meeting_recorder.py / meeting_recorder_paths.py (Step 1) for
meta.json + meetings/{week}/audio/raw/{userId}_{username}.wav, and on
meeting_recorder_setup.py's high-quality-mode setup (Settings -> Meeting
Recorder -> "High-quality mode") having already downloaded ffmpeg and the
stronger faster-whisper model referenced by DEFAULT_MODEL_SIZE below —
callers (see meeting.py) check meeting_recorder_setup.high_quality_assets_ready()
before starting this, same as they check models_ready()/is_recording
elsewhere rather than letting a background job fail loudly.

Threading model matches meeting_transcriber.py: mixdown_meeting_audio() and
run_combined_pass() below are plain (blocking) functions; CombinedPassJob
runs run_combined_pass() on its own daemon thread the same way
TranscriptionJob/SummarizationJob do, and callers marshal
on_status/on_error/on_done back to the Tk main loop themselves via
self.after(0, ...) — the same pattern meeting.py already uses for those.
"""
from __future__ import annotations

import json
import os
import subprocess
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
    combined_metrics_path,
    combined_mixdown_path,
    combined_transcript_path,
    faster_whisper_model_dir,
    ffmpeg_binary_path,
    meta_path,
    raw_audio_dir,
)

# Stronger than meeting_transcriber.DEFAULT_MODEL_SIZE ("small") - this pass exists
# to maximize quality, not speed (see module docstring / plan's "Stack" section).
# A distil-large variant would also fit here; "medium" is the plan's other suggestion
# and keeps the same faster_whisper_model_dir()/asset-name convention as Step 2.
DEFAULT_MODEL_SIZE = "medium"
DEFAULT_COMPUTE_TYPE = "int8"

# EBU R128 loudness normalization - see meeting_recorder_paths.combined_mixdown_path:
# this is the first point in the pipeline where normalization is actually needed,
# since a quiet speaker could otherwise get lost in the amix.
_LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"
_FFMPEG_TIMEOUT_SECONDS = 60 * 30
_PEAK_RAM_POLL_INTERVAL_SECONDS = 0.5


class CombinedPassError(Exception):
    """Raised with an actionable message if ffmpeg or the mixdown/transcription fails."""


@dataclass(frozen=True)
class CombinedPassResult:
    mixdown_path: Path
    combined_transcript_path: Path
    metrics_path: Path
    mixdown_seconds: float
    transcription_seconds: float
    peak_ram_bytes: int


def _notify(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(message)
    except Exception:
        pass


def _load_meeting_meta(meeting_start_utc: datetime) -> dict:
    """Duplicated from meeting_transcriber.py's equivalent - see that module's
    docstring for why small helpers like this are kept per-module rather than
    shared, here and below (_atomic_write, _PeakMemoryTracker)."""
    path = meta_path(meeting_start_utc)
    if not path.exists():
        raise RuntimeError(
            f"No meeting metadata found at {path} — was this meeting recorded (Step 1)?"
        )
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _atomic_write(path: Path, content: str) -> None:
    """tmp + os.replace, matching meeting_recorder_paths.write_meeting_meta's pattern."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp_path, path)


class _PeakMemoryTracker:
    """Cross-platform peak RSS/working-set sampler for this process - see
    meeting_transcriber.py's identical helper for the Windows-vs-elsewhere
    rationale (peak_wset is OS-tracked on Windows; a poll thread stands in
    for it elsewhere)."""

    def __init__(self, *, poll_interval_seconds: float = _PEAK_RAM_POLL_INTERVAL_SECONDS) -> None:
        self._process = psutil.Process(os.getpid())
        self._poll_interval = poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sampled_peak_bytes = 0

    def __enter__(self) -> "_PeakMemoryTracker":
        if not hasattr(self._process.memory_info(), "peak_wset"):
            self._thread = threading.Thread(
                target=self._poll, name="combined_peak_ram_sampler", daemon=True
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


def _channel_wav_paths(meeting_start_utc: datetime) -> list[Path]:
    meta = _load_meeting_meta(meeting_start_utc)
    participants: dict[str, dict] = meta.get("participants") or {}
    audio_dir = raw_audio_dir(meeting_start_utc)
    paths = [audio_dir / entry["file"] for entry in participants.values() if entry.get("file")]
    if not paths:
        raise CombinedPassError("No recorded audio channels found for this meeting.")
    return paths


def mixdown_meeting_audio(
    meeting_start_utc: datetime, *, on_progress: Callable[[str], None] | None = None
) -> Path:
    """ffmpeg amix + loudnorm of every raw_audio_dir() channel for this meeting into
    combined_mixdown_path(). Every channel is already the same length and aligned to
    one shared wall-clock origin (see meeting_recorder.AlignedWaveSink), so amix's
    duration=longest is a safety net rather than something actually relied on.

    Raises CombinedPassError if ffmpeg hasn't been downloaded yet (Settings ->
    Meeting Recorder -> High-quality mode) or if the ffmpeg process itself fails.
    """
    ffmpeg_path = ffmpeg_binary_path()
    if not ffmpeg_path.is_file():
        raise CombinedPassError(
            f"ffmpeg binary not found at {ffmpeg_path} — turn on High-quality mode in "
            "Settings -> Meeting Recorder to download it."
        )

    channel_paths = _channel_wav_paths(meeting_start_utc)
    out_path = combined_mixdown_path(meeting_start_utc)
    _notify(on_progress, f"Mixing down {len(channel_paths)} channels…")

    args = [str(ffmpeg_path), "-y"]
    for path in channel_paths:
        args += ["-i", str(path)]
    input_labels = "".join(f"[{i}:a]" for i in range(len(channel_paths)))
    filter_complex = (
        f"{input_labels}amix=inputs={len(channel_paths)}:duration=longest:dropout_transition=0[mixed];"
        f"[mixed]{_LOUDNORM_FILTER}[out]"
    )
    args += ["-filter_complex", filter_complex, "-map", "[out]", "-ar", "48000", "-ac", "2", str(out_path)]

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise CombinedPassError("ffmpeg timed out mixing down the meeting audio.") from exc
    except OSError as exc:
        raise CombinedPassError(f"Couldn't run ffmpeg at {ffmpeg_path}: {exc}") from exc

    if completed.returncode != 0 or not out_path.is_file():
        stderr_tail = completed.stderr.decode("utf-8", errors="replace")[-2000:]
        raise CombinedPassError(f"ffmpeg mixdown failed (exit {completed.returncode}): {stderr_tail}")

    return out_path


def _transcribe_mixdown(model: "WhisperModel", wav_path: Path) -> list[dict]:
    """Same per-segment/per-word shape as meeting_transcriber._transcribe_channel,
    just against one combined-audio file instead of one per-user channel."""
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


def _write_combined_json(meeting_start_utc: datetime, segments: list[dict]) -> Path:
    path = combined_transcript_path(meeting_start_utc)
    payload = {"segments": segments}
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def _write_metrics(
    meeting_start_utc: datetime,
    *,
    model_size: str,
    compute_type: str,
    mixdown_seconds: float,
    transcription_seconds: float,
    peak_ram_bytes: int,
) -> Path:
    payload = {
        "model_size": model_size,
        "compute_type": compute_type,
        "mixdown_seconds": round(mixdown_seconds, 3),
        "transcription_seconds": round(transcription_seconds, 3),
        "processing_seconds": round(mixdown_seconds + transcription_seconds, 3),
        "peak_ram_bytes": peak_ram_bytes,
        "peak_ram_mb": round(peak_ram_bytes / (1024 * 1024), 1),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path = combined_metrics_path(meeting_start_utc)
    _atomic_write(path, json.dumps(payload, indent=2) + "\n")
    return path


def run_combined_pass(
    meeting_start_utc: datetime,
    *,
    model_size: str = DEFAULT_MODEL_SIZE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    on_progress: Callable[[str], None] | None = None,
) -> CombinedPassResult:
    """Produce combined_mixdown.wav + combined.json + combined_metrics.json for one
    meeting, alongside (not replacing) Step 2's per_channel/*.json and merged.md.

    Blocking / CPU-heavy — see module docstring for the expected threading model.
    Raises RuntimeError if there's no meta.json (meeting wasn't recorded, Step 1),
    and CombinedPassError if ffmpeg is missing or the mixdown fails.
    """
    mixdown_start = time.perf_counter()
    mixdown_path = mixdown_meeting_audio(meeting_start_utc, on_progress=on_progress)
    mixdown_seconds = time.perf_counter() - mixdown_start

    _notify(on_progress, f"Loading transcription model ({model_size})…")

    # Prefer a model meeting_recorder_setup.py already downloaded locally (see
    # meeting_recorder_paths.faster_whisper_model_dir), same fallback rule as
    # meeting_transcriber.transcribe_meeting.
    local_model_dir = faster_whisper_model_dir(model_size)
    model_path_or_size = (
        str(local_model_dir) if local_model_dir.is_dir() and any(local_model_dir.iterdir()) else model_size
    )

    transcription_start = time.perf_counter()
    with _PeakMemoryTracker() as mem_tracker:
        model = WhisperModel(model_path_or_size, device="cpu", compute_type=compute_type)
        _notify(on_progress, "Transcribing combined mixdown…")
        segments = _transcribe_mixdown(model, mixdown_path)
        peak_ram_bytes = mem_tracker.peak_bytes
    transcription_seconds = time.perf_counter() - transcription_start

    transcript_path = _write_combined_json(meeting_start_utc, segments)
    metrics_path = _write_metrics(
        meeting_start_utc,
        model_size=model_size,
        compute_type=compute_type,
        mixdown_seconds=mixdown_seconds,
        transcription_seconds=transcription_seconds,
        peak_ram_bytes=peak_ram_bytes,
    )

    _notify(on_progress, "Combined-audio pass complete.")

    return CombinedPassResult(
        mixdown_path=mixdown_path,
        combined_transcript_path=transcript_path,
        metrics_path=metrics_path,
        mixdown_seconds=mixdown_seconds,
        transcription_seconds=transcription_seconds,
        peak_ram_bytes=peak_ram_bytes,
    )


class CombinedPassJob:
    """Runs run_combined_pass() on its own daemon thread.

    Mirrors meeting_transcriber.TranscriptionJob's shape: a dedicated
    background thread owns the work; this class does not marshal callbacks
    back to Tk itself — callers (see meeting.py) wrap
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
        on_done: Callable[[CombinedPassResult], None],
        model_size: str = DEFAULT_MODEL_SIZE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
    ) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run,
            name="meeting_combined_transcriber",
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
        on_done: Callable[[CombinedPassResult], None],
    ) -> None:
        try:
            result = run_combined_pass(
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
