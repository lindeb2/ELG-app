"""Speaker attribution for the mix transcript.

A deterministic timestamp-overlap pass always produces a usable transcript;
the local model then refines attribution and may splice in per-speaker text
where the mix is incoherent. If the model fails at any point the baseline is
what gets saved, so the pipeline cannot lose a meeting.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from meeting_host import config
from meeting_host.llama_server import LlmUnavailable

MIN_OVERLAP_SECONDS = 0.05

_SYSTEM_PROMPT = (
    "You assign speakers to a meeting transcript. "
    "The COMBINED transcript is the source of truth for wording. "
    "The PER-SPEAKER transcripts tell you who spoke when. "
    "Rules: "
    "1) Keep the combined text exactly as given; do not paraphrase, translate or re-punctuate. "
    "2) Set source to \"mix\" whenever you keep the combined wording. "
    "3) Only when a combined line is empty, repetitive gibberish or clearly garbled AND an "
    "overlapping per-speaker line is coherent, replace the text with the per-speaker wording "
    "and set source to \"user_fallback\". "
    "4) Pick exactly one speaker id per line from the candidates given for that line. "
    "5) Return every line id exactly once. Never invent line ids or speakers."
)

_MERGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "speaker_id", "text", "source"],
                "properties": {
                    "id": {"type": "integer"},
                    "speaker_id": {"type": "string"},
                    "text": {"type": "string"},
                    "source": {"type": "string", "enum": ["mix", "user_fallback"]},
                },
            },
        }
    },
}


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return min(a_end, b_end) - max(a_start, b_start)


def _segments(payload: dict) -> list[dict]:
    segments = payload.get("segments") if isinstance(payload, dict) else None
    return segments if isinstance(segments, list) else []


def candidate_snippets(
    mix_segment: dict,
    user_transcripts: dict[str, dict],
) -> list[dict]:
    """Per-speaker lines overlapping this mix line, best overlap first."""
    start = float(mix_segment.get("start") or 0.0)
    end = float(mix_segment.get("end") or 0.0)

    snippets: list[dict] = []
    for discord_id, payload in user_transcripts.items():
        best_overlap = 0.0
        texts: list[str] = []
        for segment in _segments(payload):
            seg_start = float(segment.get("start") or 0.0)
            seg_end = float(segment.get("end") or 0.0)
            overlap = _overlap(start, end, seg_start, seg_end)
            if overlap >= MIN_OVERLAP_SECONDS:
                text = str(segment.get("text") or "").strip()
                if text:
                    texts.append(text)
                best_overlap = max(best_overlap, overlap)
        if best_overlap >= MIN_OVERLAP_SECONDS:
            snippets.append(
                {
                    "discord_user_id": discord_id,
                    "overlap": round(best_overlap, 3),
                    "text": " ".join(texts).strip(),
                }
            )

    snippets.sort(key=lambda item: item["overlap"], reverse=True)
    return snippets


def baseline_merge(
    mix: dict,
    user_transcripts: dict[str, dict],
    speaker_labels: dict[str, str],
) -> list[dict]:
    """Deterministic attribution: most-overlapping speaker wins."""
    merged: list[dict] = []
    for segment in _segments(mix):
        snippets = candidate_snippets(segment, user_transcripts)
        discord_id = snippets[0]["discord_user_id"] if snippets else ""
        merged.append(
            {
                "start": round(float(segment.get("start") or 0.0), 3),
                "end": round(float(segment.get("end") or 0.0), 3),
                "speaker": speaker_labels.get(discord_id, "Unknown"),
                "elg_username": speaker_labels.get(discord_id),
                "discord_user_id": discord_id,
                "text": str(segment.get("text") or "").strip(),
                "source": "mix",
            }
        )
    return merged


def _estimate_tokens(text: str) -> int:
    # ~4 characters per token is close enough for budgeting.
    return math.ceil(len(text) / 4)


def _build_prompt(
    lines: list[dict],
    user_transcripts: dict[str, dict],
    speaker_labels: dict[str, str],
) -> str:
    roster = [
        {"speaker_id": discord_id, "name": label}
        for discord_id, label in sorted(speaker_labels.items(), key=lambda kv: kv[1])
    ]

    payload_lines = []
    for index, line in enumerate(lines):
        snippets = candidate_snippets(line, user_transcripts)
        candidates = [
            {
                "speaker_id": snippet["discord_user_id"],
                "name": speaker_labels.get(snippet["discord_user_id"], "Unknown"),
                "overlap_seconds": snippet["overlap"],
                "heard": snippet["text"][:400],
            }
            for snippet in snippets[:4]
        ]
        if not candidates:
            candidates = [
                {
                    "speaker_id": entry["speaker_id"],
                    "name": entry["name"],
                    "overlap_seconds": 0.0,
                    "heard": "",
                }
                for entry in roster
            ]
        payload_lines.append(
            {
                "id": index,
                "start": line["start"],
                "end": line["end"],
                "combined_text": line["text"],
                "candidates": candidates,
            }
        )

    return json.dumps(
        {"speakers": roster, "lines": payload_lines},
        ensure_ascii=False,
        indent=None,
    )


def _chunk_lines(lines: list[dict], user_transcripts: dict, speaker_labels: dict) -> list[list[int]]:
    """Split into prompt-sized windows; meetings are short so usually one."""
    chunks: list[list[int]] = []
    current: list[int] = []
    current_tokens = 0

    for index, line in enumerate(lines):
        line_tokens = _estimate_tokens(
            _build_prompt([line], user_transcripts, speaker_labels)
        )
        if current and current_tokens + line_tokens > config.LLM_PROMPT_TOKEN_BUDGET:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(index)
        current_tokens += line_tokens

    if current:
        chunks.append(current)
    return chunks


def _apply_response(
    response: dict,
    lines: list[dict],
    indices: list[int],
    speaker_labels: dict[str, str],
) -> int:
    """Copy valid decisions onto the baseline. Returns lines updated."""
    entries = response.get("lines")
    if not isinstance(entries, list):
        return 0

    local_to_global = {local: global_index for local, global_index in enumerate(indices)}
    updated = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            local_id = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        global_index = local_to_global.get(local_id)
        if global_index is None:
            continue

        speaker_id = str(entry.get("speaker_id") or "").strip()
        if speaker_id and speaker_id not in speaker_labels:
            speaker_id = ""

        source = entry.get("source")
        text = str(entry.get("text") or "").strip()
        line = lines[global_index]

        if speaker_id:
            line["discord_user_id"] = speaker_id
            line["elg_username"] = speaker_labels.get(speaker_id)
            line["speaker"] = speaker_labels.get(speaker_id, "Unknown")

        if source == "user_fallback" and text:
            line["text"] = text
            line["source"] = "user_fallback"
        elif text:
            # Trust the mix wording; the model may only relabel it.
            line["source"] = "mix"

        updated += 1

    return updated


def merge_transcripts(
    meeting_dir: Path,
    mix: dict,
    user_transcripts: dict[str, dict],
    speaker_labels: dict[str, str],
    *,
    llm=None,
    on_progress=None,
) -> dict:
    """Write transcripts/diarized.json and return its payload."""
    lines = baseline_merge(mix, user_transcripts, speaker_labels)
    used_llm = False

    if llm is not None and lines:
        chunks = _chunk_lines(lines, user_transcripts, speaker_labels)
        for position, indices in enumerate(chunks):
            if on_progress is not None:
                on_progress(position / max(1, len(chunks)))
            window = [lines[index] for index in indices]
            prompt = _build_prompt(window, user_transcripts, speaker_labels)
            try:
                response = llm.complete_json(
                    system=_SYSTEM_PROMPT,
                    user=prompt,
                    schema=_MERGE_SCHEMA,
                    max_tokens=min(8192, 256 + 120 * len(window)),
                )
            except LlmUnavailable:
                continue
            if _apply_response(response, lines, indices, speaker_labels):
                used_llm = True

    payload = {
        "speaker_attribution": "llm" if used_llm else "timestamp_overlap",
        "segments": lines,
    }
    path = meeting_dir / "transcripts" / "diarized.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def transcript_as_text(payload: dict) -> str:
    """Readable `[mm:ss] Speaker: text` rendering for the summary prompt."""
    out = []
    for segment in payload.get("segments") or []:
        start = float(segment.get("start") or 0.0)
        stamp = f"{int(start // 60):02d}:{int(start % 60):02d}"
        speaker = segment.get("speaker") or "Unknown"
        text = str(segment.get("text") or "").strip()
        if text:
            out.append(f"[{stamp}] {speaker}: {text}")
    return "\n".join(out)
