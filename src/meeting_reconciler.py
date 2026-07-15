"""Step 6 — quality upgrade: LLM reconciliation of Step 2 + Step 5 transcripts
(see step-6-llm-reconciliation-upgrade.md).

Combines the two transcripts produced by Steps 2 and 5 into one best-of-both
transcript using the same local LLM Step 3 already set up for summarization:
Step 2's merged.md has correct per-speaker attribution (Discord gives us
ground-truth speaker separation per channel) but sometimes rougher wording
where whisper only had one speaker's own audio to work from; Step 5's
combined.json has better wording/context (full acoustic picture of the
conversation) but no speaker labels at all (it's one mixed-down track). This
step asks the LLM to reassign/correct merged.md's wording using combined.json
as a reference, while explicitly preserving merged.md's speaker attribution
and structure — it must not invent dialogue or restructure the transcript
beyond that narrow correction.

This is the most expensive and highest-risk step in the whole feature (an LLM
merging two imperfect sources with no ground truth to check the merge
against), which is why it's optional and gated behind its own preference,
nested under Step 5's high-quality mode (see app_config.py /
settings_frame.py) — turning it on only makes sense once high-quality mode's
combined.json is already being produced for a meeting.

Fully local: no API calls, no server-side processing (same constraint as
meeting_recorder.py / meeting_transcriber.py / meeting_summarizer.py). No new
dependencies or downloaded assets — this step is "just" a new prompt against
infrastructure Step 3 already set up. _generate_ollama/_generate_embedded are
imported from meeting_summarizer.py rather than duplicated (see that module's
docstring for why this one pair of helpers is imported rather than
duplicated, unlike the tiny _notify/_atomic_write helpers below, which follow
the rest of the codebase's per-module-duplication convention).

Threading model matches meeting_transcriber.py/meeting_summarizer.py/
meeting_combined_transcriber.py: reconcile_transcripts() below is a plain
(blocking) function; ReconciliationJob runs it on its own daemon thread the
same way TranscriptionJob/SummarizationJob/CombinedPassJob do, and callers
marshal on_status/on_error/on_done back to the Tk main loop themselves via
self.after(0, ...) — the same pattern meeting.py already uses for those.
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

from meeting_recorder_paths import (
    combined_transcript_path,
    merged_transcript_path,
    meta_path,
    reconciled_transcript_path,
    reconciliation_metrics_path,
)
from meeting_summarizer import (
    _generate_embedded,
    _generate_ollama,
    _ollama_running_model,
    get_embedded_model_path,
)


@dataclass(frozen=True)
class ReconciliationResult:
    reconciled_transcript_path: Path
    metrics_path: Path
    backend: str  # "ollama" or "embedded"
    model_name: str
    processing_seconds: float


def _notify(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(message)
    except Exception:
        pass


def _load_meeting_meta(meeting_start_utc: datetime) -> dict:
    """Duplicated from the other meeting_*.py modules' equivalents — see
    meeting_combined_transcriber.py's docstring for why small helpers like
    this are kept per-module rather than shared."""
    path = meta_path(meeting_start_utc)
    if not path.exists():
        return {}
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


def _flatten_combined_json(combined: dict) -> str:
    """Step 5's combined.json has no speaker labels (one mixed-down track) —
    flatten its segments into plain chronological text for the prompt, since
    all we want from it here is wording/context, not structure."""
    segments = combined.get("segments") or []
    lines = [seg.get("text", "").strip() for seg in segments if seg.get("text", "").strip()]
    return "\n".join(lines)


def _build_prompt(merged_transcript: str, combined_text: str) -> str:
    return f"""You are correcting the wording of a speaker-labeled meeting transcript using a second, higher-quality transcript of the same audio as a reference.

## Transcript A — correct speaker attribution, chronological (source of truth for WHO said something and WHEN)
{merged_transcript}

## Transcript B — same meeting, better wording/context, no speaker labels (source of truth for WHAT was actually said)
{combined_text}

---
Produce a corrected version of Transcript A. Rules:
- Keep every line's speaker label and position in Transcript A exactly as-is.
- Only change the wording of a line where Transcript B clearly shows Transcript A misheard or mis-transcribed something — use Transcript B's wording for that line instead.
- Do not add, remove, merge, split, or reorder lines.
- Do not invent any dialogue that isn't supported by either transcript.
- If you're unsure whether a difference is a real correction or just a paraphrase, keep Transcript A's original wording.
- Output ONLY the corrected transcript, in the exact same "**[HH:MM] Speaker:** text" line format as Transcript A. No preamble, no explanation, no extra commentary."""


def _format_header(meeting_start_utc: datetime, meta: dict, backend: str, model_name: str) -> str:
    lines = ["# Meeting Transcript (Reconciled)", "", f"- Start (UTC): {meeting_start_utc.isoformat()}"]
    participants = meta.get("participants") or {}
    participant_names = sorted(
        entry.get("display_name") or entry.get("username") or user_id
        for user_id, entry in participants.items()
    )
    if participant_names:
        lines.append(f"- Participants: {', '.join(participant_names)}")
    lines.append(f"- Generated by: {model_name} ({backend})")
    lines.append("- Sources: transcripts/merged.md (Step 2, speaker attribution) + transcripts/combined.json (Step 5, wording) — kept alongside this file for manual QA")
    lines.extend(["", "---", ""])
    return "\n".join(lines)


def _write_metrics(
    meeting_start_utc: datetime,
    *,
    backend: str,
    model_name: str,
    processing_seconds: float,
) -> Path:
    """Mirrors meeting_summarizer.py's simpler LLM-based metrics shape (no
    peak-RAM tracking) rather than the whisper-based modules' fuller one —
    see step-6-context-brief.md's note on why this step follows that module's
    convention instead."""
    payload = {
        "backend": backend,
        "model_name": model_name,
        "processing_seconds": round(processing_seconds, 3),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path = reconciliation_metrics_path(meeting_start_utc)
    _atomic_write(path, json.dumps(payload, indent=2) + "\n")
    return path


def reconcile_transcripts(
    meeting_start_utc: datetime,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> ReconciliationResult:
    """Merge Step 2's merged.md + Step 5's combined.json into reconciled.md.

    Blocking — see module docstring for the expected threading model. Raises
    RuntimeError if either source transcript is missing (Step 2 and/or Step 5
    wasn't run for this meeting), and SummarizerConfigError if neither local
    LLM backend is available.
    """
    merged_path = merged_transcript_path(meeting_start_utc)
    if not merged_path.exists():
        raise RuntimeError(
            f"No merged transcript found at {merged_path} — was this meeting transcribed (Step 2)?"
        )
    combined_path = combined_transcript_path(meeting_start_utc)
    if not combined_path.exists():
        raise RuntimeError(
            f"No combined-audio transcript found at {combined_path} — "
            "was the high-quality combined-audio pass run for this meeting (Step 5)?"
        )

    merged_transcript = merged_path.read_text(encoding="utf-8")
    combined_json = json.loads(combined_path.read_text(encoding="utf-8"))
    combined_text = _flatten_combined_json(combined_json)

    prompt = _build_prompt(merged_transcript, combined_text)

    ollama_model = _ollama_running_model()
    start_perf = time.perf_counter()
    if ollama_model:
        _notify(on_progress, f"Reconciling with local Ollama ({ollama_model})…")
        body = _generate_ollama(prompt, ollama_model)
        backend, model_name = "ollama", ollama_model
    else:
        model_path = get_embedded_model_path()
        _notify(on_progress, f"Loading embedded model ({model_path.name})…")
        body = _generate_embedded(prompt, model_path)
        backend, model_name = "embedded", model_path.name
    processing_seconds = time.perf_counter() - start_perf

    meta = _load_meeting_meta(meeting_start_utc)
    header = _format_header(meeting_start_utc, meta, backend, model_name)
    out_path = reconciled_transcript_path(meeting_start_utc)
    _atomic_write(out_path, header + "\n" + body.strip() + "\n")

    metrics_path = _write_metrics(
        meeting_start_utc,
        backend=backend,
        model_name=model_name,
        processing_seconds=processing_seconds,
    )

    _notify(on_progress, "Reconciliation complete.")

    return ReconciliationResult(
        reconciled_transcript_path=out_path,
        metrics_path=metrics_path,
        backend=backend,
        model_name=model_name,
        processing_seconds=processing_seconds,
    )


class ReconciliationJob:
    """Runs reconcile_transcripts() on its own daemon thread.

    Mirrors CombinedPassJob/SummarizationJob's shape: a dedicated background
    thread owns the work; this class does not marshal callbacks back to Tk
    itself — callers (see meeting.py) wrap on_status/on_error/on_done with
    self.after(0, ...) themselves.
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
        on_done: Callable[[ReconciliationResult], None],
    ) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._run,
            name="meeting_reconciler",
            daemon=True,
            args=(meeting_start_utc, on_status, on_error, on_done),
        )
        self._thread.start()

    def _run(
        self,
        meeting_start_utc: datetime,
        on_status: Callable[[str], None],
        on_error: Callable[[Exception], None],
        on_done: Callable[[ReconciliationResult], None],
    ) -> None:
        try:
            result = reconcile_transcripts(meeting_start_utc, on_progress=on_status)
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
