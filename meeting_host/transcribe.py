"""Transcribe the mix and each speaker with the configured source.

The mix transcript is the source of truth for wording. The per-speaker ones
exist so the merge step can attribute lines and repair overlapping speech.
Groq is the default. If it fails and local Whisper is already installed, the
rest of the meeting uses that. Otherwise ELG asks before downloading it.
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path

from meeting_host import config, models, protocol
from meeting_host.install_prompt import InstallPrompt
from meeting_host.transcription import open_source
from meeting_host.transcription.groq import GroqUnavailable

_state: ContextVar["TranscribeState | None"] = ContextVar("mike_transcribe_state", default=None)
_prompt: ContextVar[InstallPrompt | None] = ContextVar("mike_install_prompt", default=None)


class TranscriptUnavailable(RuntimeError):
    """Groq failed and local Whisper will not run."""


class TranscribeState:
    """Sticky for one pipeline run. A Groq failure switches the rest of the meeting."""

    def __init__(self) -> None:
        self.force_local = False


def activate(state: TranscribeState, prompt: InstallPrompt | None):
    state_token = _state.set(state)
    prompt_token = _prompt.set(prompt)
    return state_token, prompt_token


def deactivate(tokens) -> None:
    state_token, prompt_token = tokens
    _state.reset(state_token)
    _prompt.reset(prompt_token)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _stage(
    on_stage,
    name: str,
    which: str,
    detail: str = "",
    label: str = "",
    parent: str = "",
    model: str = "",
) -> None:
    if on_stage is not None:
        on_stage(name, which, detail, label, parent, model)


def _empty() -> dict:
    return {"language": config.WHISPER_LANGUAGE, "duration": 0.0, "segments": []}


def _run_state() -> TranscribeState:
    state = _state.get()
    if state is None:
        state = TranscribeState()
        _state.set(state)
    return state


def offer_local(base: Path, reason: str) -> bool:
    """Switch to local Whisper. Ask before downloading weights that are not here."""
    state = _run_state()
    if models.local_whisper_installed(base):
        state.force_local = True
        return True
    prompt = _prompt.get()
    if prompt is None or not prompt.ask(_install_detail(reason)):
        return False
    state.force_local = True
    return True


def _install_detail(reason: str) -> str:
    return (
        f"Groq transcription is unavailable ({reason}). "
        f"Local speech models are not on this PC ({config.LOCAL_WHISPER_DOWNLOAD}, downloaded once). "
        "Download them and finish this meeting locally?"
    )


def _open_for_run():
    state = _run_state()
    if state.force_local:
        return open_source("local")
    return open_source()


def _call(source, base: Path, audio_path: Path, *, role: str, on_progress) -> tuple[dict, str]:
    try:
        label = source.prepare(base, role=role, on_progress=on_progress)
        payload = source.transcribe(audio_path, base=base, on_progress=on_progress)
    except GroqUnavailable:
        raise
    return payload, label


def transcribe_mix(base: Path, meeting_dir: Path, *, on_progress=None, on_stage=None) -> dict:
    audio_path = meeting_dir / "audio" / "mix.wav"
    output = meeting_dir / "transcripts" / "mix.json"

    if not audio_path.is_file():
        payload = _empty()
        _write_json(output, payload)
        return payload

    payload = _transcribe_one(
        base,
        audio_path,
        role="mix",
        on_progress=on_progress,
        on_stage=on_stage,
        parent=protocol.PHASE_WHISPER_MIX,
        prepare=protocol.PHASE_WHISPER_MIX_PREPARE,
        infer=protocol.PHASE_WHISPER_MIX_INFER,
        parent_label="Transcribing meeting",
    )
    _write_json(output, payload)
    return payload


def transcribe_users(
    base: Path,
    meeting_dir: Path,
    *,
    on_progress=None,
    on_stage=None,
    labels: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Per-speaker transcripts keyed by Discord user id."""
    users_dir = meeting_dir / "audio" / "users"
    audio_files = sorted(users_dir.glob("*.wav")) if users_dir.is_dir() else []
    if not audio_files:
        return {}

    names = labels or {}
    source = None
    runtime = ""
    results: dict[str, dict] = {}
    parent_open = False
    try:
        index = 0
        while index < len(audio_files):
            audio_path = audio_files[index]
            if source is None:
                source = _open_for_run()
                runtime = source.id
                _stage(
                    on_stage,
                    protocol.PHASE_WHISPER_USERS,
                    "start",
                    label="Transcribing speakers",
                    model=runtime,
                )
                parent_open = True
                _stage(
                    on_stage,
                    protocol.PHASE_WHISPER_USERS_PREPARE,
                    "start",
                    label="Preparing",
                    parent=protocol.PHASE_WHISPER_USERS,
                    model=runtime,
                )
                try:
                    runtime = source.prepare(
                        base,
                        role="user",
                        on_progress=lambda detail: _note(
                            on_stage, on_progress, protocol.PHASE_WHISPER_USERS_PREPARE, detail
                        ),
                    )
                    _stage(on_stage, protocol.PHASE_WHISPER_USERS, "detail", model=runtime)
                    _stage(on_stage, protocol.PHASE_WHISPER_USERS_PREPARE, "detail", model=runtime)
                except GroqUnavailable as exc:
                    source.close()
                    source = None
                    _stage(on_stage, protocol.PHASE_WHISPER_USERS_PREPARE, "end")
                    _stage(on_stage, protocol.PHASE_WHISPER_USERS, "end")
                    parent_open = False
                    if not offer_local(base, str(exc)):
                        raise TranscriptUnavailable(str(exc)) from exc
                    continue
                finally:
                    if source is not None:
                        _stage(on_stage, protocol.PHASE_WHISPER_USERS_PREPARE, "end")

            discord_id = audio_path.stem
            speaker = str(names.get(discord_id) or "").strip()
            if not speaker or speaker == discord_id:
                speaker = f"Speaker {index + 1}"
            stage = f"whisper_user:{discord_id}"
            _stage(
                on_stage,
                stage,
                "start",
                label=speaker,
                parent=protocol.PHASE_WHISPER_USERS,
                model=runtime,
            )
            try:
                payload = source.transcribe(
                    audio_path,
                    base=base,
                    on_progress=lambda detail, stage=stage: _note(on_stage, on_progress, stage, detail),
                )
                results[discord_id] = payload
                _write_json(meeting_dir / "transcripts" / "users" / f"{discord_id}.json", payload)
                index += 1
            except GroqUnavailable as exc:
                source.close()
                source = None
                _stage(on_stage, stage, "end")
                _stage(on_stage, protocol.PHASE_WHISPER_USERS, "end")
                parent_open = False
                if not offer_local(base, str(exc)):
                    raise TranscriptUnavailable(str(exc)) from exc
                continue
            finally:
                if source is not None:
                    _stage(on_stage, stage, "end")
    finally:
        if source is not None:
            source.close()
        if parent_open:
            _stage(on_stage, protocol.PHASE_WHISPER_USERS, "end")

    return results


def _transcribe_one(
    base: Path,
    audio_path: Path,
    *,
    role: str,
    on_progress,
    on_stage,
    parent: str,
    prepare: str,
    infer: str,
    parent_label: str,
) -> dict:
    while True:
        source = _open_for_run()
        runtime = source.id
        _stage(on_stage, parent, "start", label=parent_label, model=runtime)
        try:
            _stage(on_stage, prepare, "start", label="Preparing", parent=parent, model=runtime)
            try:
                runtime = source.prepare(
                    base,
                    role=role,
                    on_progress=lambda detail: _note(on_stage, on_progress, prepare, detail),
                )
                _stage(on_stage, parent, "detail", model=runtime)
                _stage(on_stage, prepare, "detail", model=runtime)
            finally:
                _stage(on_stage, prepare, "end")

            _stage(on_stage, infer, "start", label="Transcribing", parent=parent, model=runtime)
            try:
                payload = source.transcribe(
                    audio_path,
                    base=base,
                    on_progress=lambda detail: _note(on_stage, on_progress, infer, detail),
                )
            finally:
                _stage(on_stage, infer, "end")
            return payload
        except GroqUnavailable as exc:
            if not offer_local(base, str(exc)):
                raise TranscriptUnavailable(str(exc)) from exc
        finally:
            source.close()
            _stage(on_stage, parent, "end")


def _note(on_stage, on_progress, stage: str, detail: str) -> None:
    if on_progress is not None:
        on_progress(detail)
    _stage(on_stage, stage, "detail", detail=detail)


def load_saved(meeting_dir: Path) -> tuple[dict, dict[str, dict]]:
    """Re-read transcripts from disk (used by --resume)."""
    mix_path = meeting_dir / "transcripts" / "mix.json"
    mix: dict = {"segments": []}
    if mix_path.is_file():
        try:
            mix = json.loads(mix_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            mix = {"segments": []}

    users: dict[str, dict] = {}
    users_dir = meeting_dir / "transcripts" / "users"
    if users_dir.is_dir():
        for path in sorted(users_dir.glob("*.json")):
            try:
                users[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return mix, users
