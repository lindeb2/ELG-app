"""Post-recording pipeline: mix, transcribe, attribute speakers, summarize.

Written as a pure function of a meeting directory so it can be re-run later
with `--resume <meeting_dir>` if the app closed mid-processing.
"""
from __future__ import annotations

import json
from pathlib import Path

from meeting_host import audio_mix, config, facts as facts_module, merge, models, summarize, transcribe
from meeting_host import protocol
from meeting_host.llama_server import LlamaServer, LlmUnavailable


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_status(meeting_dir: Path, **fields) -> None:
    path = meeting_dir / "status.json"
    current = _read_json(path, {})
    if not isinstance(current, dict):
        current = {}
    current.update(fields)
    try:
        path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def speaker_labels_from_attendance(attendance: dict) -> dict[str, str]:
    labels: dict[str, str] = {}
    for participant in attendance.get("participants") or []:
        discord_id = str(participant.get("discord_user_id") or "")
        if not discord_id:
            continue
        labels[discord_id] = str(
            participant.get("elg_username")
            or participant.get("discord_nick")
            or participant.get("discord_global_name")
            or participant.get("discord_username")
            or discord_id
        )
    return labels


def mix_audio(meeting_dir: Path) -> float:
    users_dir = meeting_dir / "audio" / "users"
    sources = sorted(users_dir.glob("*.wav")) if users_dir.is_dir() else []
    destination = meeting_dir / "audio" / "mix.wav"
    return audio_mix.mix_wavs(sources, destination)


def run(
    base_dir: Path,
    meeting_dir: Path,
    events: protocol.EventWriter,
    *,
    skip_existing: bool = False,
) -> Path:
    """Run every post-recording step. Returns the summary path."""
    attendance = _read_json(meeting_dir / "attendance.json", {})
    if not isinstance(attendance, dict):
        attendance = {}
    labels = speaker_labels_from_attendance(attendance)

    events.phase(protocol.PHASE_MIX, 0.0)
    mix_path = meeting_dir / "audio" / "mix.wav"
    if not (skip_existing and mix_path.is_file()):
        duration = mix_audio(meeting_dir)
    else:
        duration = audio_mix.wav_duration_seconds(mix_path)
    write_status(meeting_dir, mix_seconds=round(duration, 2), pipeline="running")
    events.phase(protocol.PHASE_MIX, 1.0)

    if skip_existing and (meeting_dir / "transcripts" / "mix.json").is_file():
        mix_transcript, user_transcripts = transcribe.load_saved(meeting_dir)
    else:
        events.phase(protocol.PHASE_TRANSCRIBE_MIX, 0.0)
        mix_transcript = transcribe.transcribe_mix(
            base_dir,
            meeting_dir,
            on_progress=lambda detail: events.phase(
                protocol.PHASE_TRANSCRIBE_MIX, 0.0, detail=str(detail)
            ),
        )
        events.phase(protocol.PHASE_TRANSCRIBE_MIX, 1.0)

        events.phase(protocol.PHASE_TRANSCRIBE_USERS, 0.0)
        user_transcripts = transcribe.transcribe_users(
            base_dir,
            meeting_dir,
            on_progress=lambda value: events.phase(
                protocol.PHASE_TRANSCRIBE_USERS,
                value if isinstance(value, float) else 0.0,
            ),
        )
        events.phase(protocol.PHASE_TRANSCRIBE_USERS, 1.0)

    # Anyone who only shows up in audio still needs a label.
    for discord_id in user_transcripts:
        labels.setdefault(discord_id, discord_id)

    meeting_facts = facts_module.load_facts_safe()

    llm: LlamaServer | None = None
    llm_error = ""
    try:
        try:
            llm = LlamaServer(model_path=models.ensure_llm(
                base_dir,
                on_progress=lambda detail: events.emit(
                    protocol.EVENT_NEED_MODELS, detail=str(detail)
                ),
            ))
            llm.start()
        except LlmUnavailable as exc:
            llm = None
            llm_error = str(exc)
        except Exception as exc:
            llm = None
            llm_error = f"Local model unavailable: {exc}"

        events.phase(protocol.PHASE_MERGE, 0.0)
        diarized = merge.merge_transcripts(
            meeting_dir,
            mix_transcript,
            user_transcripts,
            labels,
            llm=llm,
            on_progress=lambda value: events.phase(protocol.PHASE_MERGE, value),
        )
        events.phase(protocol.PHASE_MERGE, 1.0)

        events.phase(protocol.PHASE_SUMMARY, 0.0)
        content = summarize.build_summary(
            meeting_facts,
            attendance,
            merge.transcript_as_text(diarized),
            llm=llm,
        )
        summary_path = summarize.write_summary(meeting_dir, content)
        events.phase(protocol.PHASE_SUMMARY, 1.0)
    finally:
        if llm is not None:
            llm.stop()

    write_status(
        meeting_dir,
        pipeline="done",
        speaker_attribution=diarized.get("speaker_attribution"),
        llm_error=llm_error,
        segments=len(diarized.get("segments") or []),
    )
    return summary_path


def resume(base_dir: Path, meeting_dir: Path, events: protocol.EventWriter) -> Path:
    return run(base_dir, meeting_dir, events, skip_existing=True)


def latest_pending(base_dir: Path) -> Path | None:
    """Most recent meeting whose pipeline never finished."""
    meetings_root = config.mike_dir(base_dir) / "meetings"
    if not meetings_root.is_dir():
        return None
    candidates = sorted(
        (path for path in meetings_root.glob("*/*") if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in candidates:
        status = _read_json(path / "status.json", {})
        if not isinstance(status, dict) or status.get("pipeline") != "done":
            return path
    return None
