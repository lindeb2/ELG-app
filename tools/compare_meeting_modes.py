"""Step 7 — Evaluation Harness: compare transcript/summary modes for one meeting.

Not part of the packaged app — run manually during development to decide
which mode should be the default for the Settings toggle from Step 4 (see
step-7-evaluation-harness.md's Definition of Done). Follows the existing
`tools/` convention (argparse CLI, plain-Python script, not wired into
main.py or any settings toggle — see tools/test_commit.py).

Usage:
    python tools/compare_meeting_modes.py 2026-W29
    python tools/compare_meeting_modes.py "C:\\path\\to\\meetings\\2026-W29"

    # Skip summary comparison entirely (fast, transcripts + metrics only):
    python tools/compare_meeting_modes.py 2026-W29 --no-summaries

    # Force fresh summaries instead of reusing cached comparison files:
    python tools/compare_meeting_modes.py 2026-W29 --regenerate-summaries

    # Skip the merged.md vs reconciled.md diff section:
    python tools/compare_meeting_modes.py 2026-W29 --no-diff

    # Truncate long transcript/summary printouts:
    python tools/compare_meeting_modes.py 2026-W29 --max-chars 4000

Design notes (see step-7-context-brief.md for the full writeup this script
was built from):

- This tool is handed a meetings/{week} folder (or a week string like
  "2026-W29"), not a datetime, so it reads transcripts/*, summaries/*, and
  the metrics *.json files directly with pathlib/open() — it deliberately
  does NOT go through meeting_recorder_paths.py's datetime-keyed helpers for
  reading, since those exist to derive a week folder from a datetime, which
  is the reverse of what this tool has.

- Today only one summaries/summary.md ever exists per meeting, and it's
  always generated from merged.md (Step 3 summarization runs against
  merged.md the moment it's ready and never re-triggers against
  reconciled.md — see meeting.py's pipeline / meeting_summarizer.py). To
  give a genuine per-mode summary comparison, this tool calls
  meeting_summarizer.summarize_meeting() itself for the high-quality
  (combined.json, flattened) and best-quality (reconciled.md) transcripts
  when a comparison summary isn't already cached on disk, writing results to
  comparison-only files (summaries/summary_high_quality.md,
  summaries/summary_best_quality.md) — the real summaries/summary.md is
  read, backed up, and restored around each such call so it is never
  overwritten by this tool. This costs extra local LLM calls (slow, and
  requires either a running Ollama instance or an embedded GGUF model to be
  set up) and live MongoDB reads for goal status / activity logs / agenda —
  that data is queried live and scoped to the meeting's calendar week, so a
  comparison summary generated today reflects TODAY's goal/activity data for
  that week, not necessarily what was true when the meeting happened. Fine
  for a dev comparison, not a historical record.

- Fully local, same as every other meeting_*.py module: no API calls beyond
  what meeting_summarizer.py itself already makes (localhost Ollama or an
  embedded in-process model) and this app's own MongoDB.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meeting_recorder_paths import get_meetings_dir, week_folder  # noqa: E402
from meeting_reconciler import _flatten_combined_json  # noqa: E402
from meeting_summarizer import SummarizerConfigError, summarize_meeting  # noqa: E402

_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
_SEP = "=" * 78


# ---------------------------------------------------------------------------
# Folder / meta resolution
# ---------------------------------------------------------------------------

def resolve_week_folder(arg: str) -> Path:
    candidate = Path(arg)
    if candidate.is_dir():
        return candidate
    if _WEEK_RE.match(arg):
        folder = get_meetings_dir() / arg
        if folder.is_dir():
            return folder
        raise SystemExit(f"No meetings folder found for week '{arg}' at {folder}")
    raise SystemExit(
        f"'{arg}' is neither an existing folder nor a week string like '2026-W29'."
    )


def load_meta(folder: Path) -> dict:
    meta_file = folder / "meta.json"
    if not meta_file.is_file():
        return {}
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] could not read {meta_file}: {exc}")
        return {}


def parse_meeting_start(meta: dict) -> datetime | None:
    raw = meta.get("meeting_start")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def participant_names(meta: dict) -> list[str]:
    participants = meta.get("participants") or {}
    return sorted(
        entry.get("display_name") or entry.get("username") or user_id
        for user_id, entry in participants.items()
    )


# ---------------------------------------------------------------------------
# File loading helpers
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[warn] could not read {path}: {exc}")
        return None


def read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] could not read {path}: {exc}")
        return None


def strip_header(text: str) -> str:
    """Drop the '# Meeting Transcript ...' / metadata header, keeping just
    the transcript body after the '---' separator, so the diff view focuses
    on speaker-attribution/wording changes rather than header noise."""
    marker = "\n---\n"
    idx = text.find(marker)
    if idx == -1:
        return text
    return text[idx + len(marker):]


# ---------------------------------------------------------------------------
# Metrics formatting
# ---------------------------------------------------------------------------

_METRICS_FIELDS_OF_INTEREST = (
    "model_size",
    "compute_type",
    "backend",
    "model_name",
    "mixdown_seconds",
    "transcription_seconds",
    "processing_seconds",
    "peak_ram_mb",
    "recorded_at",
)


def format_metrics(metrics: dict | None) -> str:
    if metrics is None:
        return "  (no metrics file found)"
    lines = []
    for key in _METRICS_FIELDS_OF_INTEREST:
        if key in metrics:
            lines.append(f"  {key}: {metrics[key]}")
    # Catch any fields not in the known list, so nothing is silently dropped.
    for key, value in metrics.items():
        if key not in _METRICS_FIELDS_OF_INTEREST:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines) if lines else "  (empty metrics file)"


# ---------------------------------------------------------------------------
# Mode section printing
# ---------------------------------------------------------------------------

def print_section(title: str) -> None:
    print(f"\n{_SEP}\n{title}\n{_SEP}")


def print_mode_transcript(
    label: str,
    source_path: Path,
    text: str | None,
    metrics: dict | None,
    max_chars: int | None,
) -> None:
    print_section(label)
    print(f"Source: {source_path}")
    print("Stats:")
    print(format_metrics(metrics))
    print("Transcript:")
    if text is None:
        print("  (not found — this mode wasn't run for this meeting)")
        return
    body = text if max_chars is None else text[:max_chars]
    print(body)
    if max_chars is not None and len(text) > max_chars:
        print(f"  ... [truncated, {len(text) - max_chars} more characters — rerun with a larger --max-chars or omit it]")


def print_diff(merged_text: str | None, reconciled_text: str | None) -> None:
    print_section("DIFF: merged.md (fast, Step 2) vs reconciled.md (best-quality, Step 6)")
    if merged_text is None or reconciled_text is None:
        missing = "merged.md" if merged_text is None else "reconciled.md"
        print(f"  (skipped — {missing} not found)")
        return
    a = strip_header(merged_text).splitlines(keepends=True)
    b = strip_header(reconciled_text).splitlines(keepends=True)
    diff = list(difflib.unified_diff(a, b, fromfile="merged.md", tofile="reconciled.md"))
    if not diff:
        print("  (no differences — reconciliation made no changes)")
        return
    print("".join(diff))


# ---------------------------------------------------------------------------
# Summary handling (option 2: on-demand generation, cached to comparison files)
# ---------------------------------------------------------------------------

@dataclass
class SummaryOutcome:
    text: str | None = None
    note: str | None = None
    generated: bool = False
    backend: str | None = None
    model_name: str | None = None
    processing_seconds: float | None = None


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _generate_comparison_summary(
    meeting_start_utc: datetime,
    real_summary_path: Path,
    transcript_override_path: Path,
) -> tuple[str, str, str, float]:
    """Call summarize_meeting() and return (summary_text, backend, model_name,
    processing_seconds), restoring real_summary_path to its prior state
    afterwards (summarize_meeting always writes to the real summaries/summary.md
    internally — see meeting_summarizer.py — so this tool backs it up first and
    puts it back once it has copied out the freshly-generated text)."""
    backup = real_summary_path.read_bytes() if real_summary_path.is_file() else None
    try:
        result = summarize_meeting(
            meeting_start_utc,
            transcript_override_path=transcript_override_path,
        )
        generated_text = result.summary_path.read_text(encoding="utf-8")
        return generated_text, result.backend, result.model_name, result.processing_seconds
    finally:
        if backup is not None:
            real_summary_path.write_bytes(backup)
        elif real_summary_path.is_file():
            real_summary_path.unlink()


def ensure_default_summary(folder: Path, meeting_start_utc: datetime | None, merged_available: bool) -> SummaryOutcome:
    """Fast-mode (merged.md-derived) summary is just the real summaries/summary.md
    — Step 3 already generates this one automatically, so this only calls
    summarize_meeting() itself if it's missing entirely."""
    real_path = folder / "summaries" / "summary.md"
    text = read_text(real_path)
    if text is not None:
        return SummaryOutcome(text=text)
    if not merged_available:
        return SummaryOutcome(note="not found, and merged.md is missing so it can't be generated")
    if meeting_start_utc is None:
        return SummaryOutcome(note="not found, and meeting_start couldn't be read from meta.json to generate it")
    try:
        result = summarize_meeting(meeting_start_utc)
    except SummarizerConfigError as exc:
        return SummaryOutcome(note=f"not found, and generation failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - dev tool, surface any failure and continue
        return SummaryOutcome(note=f"not found, and generation raised {type(exc).__name__}: {exc}")
    return SummaryOutcome(
        text=result.summary_path.read_text(encoding="utf-8"),
        generated=True,
        backend=result.backend,
        model_name=result.model_name,
        processing_seconds=result.processing_seconds,
    )


def ensure_comparison_summary(
    *,
    folder: Path,
    meeting_start_utc: datetime | None,
    cache_filename: str,
    source_text: str | None,
    source_label: str,
    regenerate: bool,
) -> SummaryOutcome:
    summaries_dir = folder / "summaries"
    cache_path = summaries_dir / cache_filename
    real_summary_path = summaries_dir / "summary.md"

    if cache_path.is_file() and not regenerate:
        cached = read_text(cache_path)
        if cached is not None:
            return SummaryOutcome(text=cached)

    if source_text is None:
        return SummaryOutcome(note=f"not generated — {source_label} not found for this meeting")
    if meeting_start_utc is None:
        return SummaryOutcome(note="not generated — meeting_start couldn't be read from meta.json")

    override_path = None
    tmp_path = None
    try:
        if cache_filename == "summary_high_quality.md":
            # combined.json's flattened text has no file of its own on disk;
            # summarize_meeting() needs a plain-text path, so stage one.
            tmp_path = summaries_dir / "_tmp_high_quality_transcript.txt"
            summaries_dir.mkdir(parents=True, exist_ok=True)
            _write_text(tmp_path, source_text)
            override_path = tmp_path
        else:
            # reconciled.md is already a plain-text file on disk — reuse it
            # directly rather than copying.
            override_path = folder / "transcripts" / "reconciled.md"

        text, backend, model_name, processing_seconds = _generate_comparison_summary(
            meeting_start_utc, real_summary_path, override_path
        )
    except SummarizerConfigError as exc:
        return SummaryOutcome(note=f"generation failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - dev tool, surface any failure and continue
        return SummaryOutcome(note=f"generation raised {type(exc).__name__}: {exc}")
    finally:
        if tmp_path is not None and tmp_path.is_file():
            tmp_path.unlink()

    summaries_dir.mkdir(parents=True, exist_ok=True)
    _write_text(cache_path, text)
    return SummaryOutcome(
        text=text, generated=True, backend=backend, model_name=model_name,
        processing_seconds=processing_seconds,
    )


def print_summary_outcome(label: str, outcome: SummaryOutcome, max_chars: int | None) -> None:
    print_section(label)
    if outcome.generated:
        print(
            f"(generated just now — backend={outcome.backend} model={outcome.model_name} "
            f"processing_seconds={outcome.processing_seconds:.1f})"
        )
    if outcome.text is None:
        print(f"  ({outcome.note})")
        return
    body = outcome.text if max_chars is None else outcome.text[:max_chars]
    print(body)
    if max_chars is not None and len(outcome.text) > max_chars:
        print(f"  ... [truncated, {len(outcome.text) - max_chars} more characters]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "week",
        help="Week string (e.g. 2026-W29) or full path to a meetings/{week} folder",
    )
    parser.add_argument(
        "--no-summaries", action="store_true",
        help="Skip summary generation/display entirely (fast, transcripts + metrics only)",
    )
    parser.add_argument(
        "--regenerate-summaries", action="store_true",
        help="Force regenerating the high-quality/best-quality comparison summaries "
        "instead of reusing cached summary_high_quality.md/summary_best_quality.md",
    )
    parser.add_argument(
        "--no-diff", action="store_true",
        help="Skip the merged.md vs reconciled.md diff section",
    )
    parser.add_argument(
        "--max-chars", type=int, default=None,
        help="Truncate each printed transcript/summary to this many characters (default: print in full)",
    )
    args = parser.parse_args(argv)

    folder = resolve_week_folder(args.week)
    transcripts_dir = folder / "transcripts"

    meta = load_meta(folder)
    meeting_start_utc = parse_meeting_start(meta)

    print_section(f"Meeting comparison: {folder}")
    print(f"Participants: {', '.join(participant_names(meta)) or '(unknown — no meta.json / participants found)'}")
    print(f"Start (UTC): {meta.get('meeting_start', '(unknown)')}")
    print(f"End (UTC): {meta.get('meeting_end', '(unknown)')}")
    if meeting_start_utc is not None:
        expected_folder = week_folder(meeting_start_utc)
        if expected_folder.resolve() != folder.resolve():
            print(
                f"[warn] meta.json's meeting_start maps to a different week folder "
                f"({expected_folder}) than the one given ({folder}) — on-demand summary "
                "generation below may write into the wrong week if this meeting straddles "
                "a week boundary."
            )

    # --- Transcripts + metrics ---
    merged_text = read_text(transcripts_dir / "merged.md")
    baseline_metrics = read_json(transcripts_dir / "baseline_metrics.json")

    combined_json = read_json(transcripts_dir / "combined.json")
    combined_text = _flatten_combined_json(combined_json) if combined_json is not None else None
    combined_metrics = read_json(transcripts_dir / "combined_metrics.json")

    reconciled_text = read_text(transcripts_dir / "reconciled.md")
    reconciliation_metrics = read_json(transcripts_dir / "reconciliation_metrics.json")

    print_mode_transcript(
        "FAST MODE — merged.md (Step 2)", transcripts_dir / "merged.md",
        merged_text, baseline_metrics, args.max_chars,
    )
    print_mode_transcript(
        "HIGH-QUALITY MODE — combined.json, flattened (Step 5)", transcripts_dir / "combined.json",
        combined_text, combined_metrics, args.max_chars,
    )
    print_mode_transcript(
        "BEST-QUALITY MODE — reconciled.md (Step 6)", transcripts_dir / "reconciled.md",
        reconciled_text, reconciliation_metrics, args.max_chars,
    )

    if not args.no_diff:
        print_diff(merged_text, reconciled_text)

    # --- Summaries ---
    if not args.no_summaries:
        default_outcome = ensure_default_summary(folder, meeting_start_utc, merged_text is not None)
        print_summary_outcome("SUMMARY — fast mode (merged.md-derived, real summaries/summary.md)", default_outcome, args.max_chars)

        high_outcome = ensure_comparison_summary(
            folder=folder, meeting_start_utc=meeting_start_utc,
            cache_filename="summary_high_quality.md",
            source_text=combined_text, source_label="combined.json",
            regenerate=args.regenerate_summaries,
        )
        print_summary_outcome(
            "SUMMARY — high-quality mode (combined.json-derived, summaries/summary_high_quality.md)",
            high_outcome, args.max_chars,
        )

        best_outcome = ensure_comparison_summary(
            folder=folder, meeting_start_utc=meeting_start_utc,
            cache_filename="summary_best_quality.md",
            source_text=reconciled_text, source_label="reconciled.md",
            regenerate=args.regenerate_summaries,
        )
        print_summary_outcome(
            "SUMMARY — best-quality mode (reconciled.md-derived, summaries/summary_best_quality.md)",
            best_outcome, args.max_chars,
        )

    print_section("What to eyeball")
    print(
        "  - Speaker attribution / wording: see the DIFF section above (merged.md vs "
        "reconciled.md).\n"
        "  - Readability on cross-talk sections: compare the HIGH-QUALITY transcript "
        "(no speaker labels, full acoustic context) against FAST/BEST-QUALITY above.\n"
        "  - Action items / goal status drift: compare the three SUMMARY sections' "
        "'## Action Items' and '## Goal Status' blocks.\n"
        "  - Cost vs quality: compare each mode's 'Stats' block above (processing_seconds, "
        "peak_ram_mb where tracked)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
