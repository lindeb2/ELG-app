"""faster-whisper passes: heavy on the mix, light per speaker.

The mix transcript is the source of truth for wording; the per-speaker ones
exist so the merge step can attribute lines and repair overlapping speech.
"""
from __future__ import annotations

import json
from pathlib import Path

from meeting_host import config, models


def _load_model(model_dir: Path):
    """Prefer CUDA; fall back to CPU int8 so a weak host still works."""
    from faster_whisper import WhisperModel

    try:
        return WhisperModel(str(model_dir), device="cuda", compute_type="float16")
    except Exception:
        return WhisperModel(str(model_dir), device="cpu", compute_type="int8")


def _transcribe_file(model, audio_path: Path) -> dict:
    segments, info = model.transcribe(
        str(audio_path),
        language=config.WHISPER_LANGUAGE,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
        beam_size=5,
    )

    out_segments = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        words = []
        for word in getattr(segment, "words", None) or []:
            words.append(
                {
                    "start": round(float(word.start or 0.0), 3),
                    "end": round(float(word.end or 0.0), 3),
                    "word": str(word.word or "").strip(),
                }
            )
        out_segments.append(
            {
                "start": round(float(segment.start or 0.0), 3),
                "end": round(float(segment.end or 0.0), 3),
                "text": text,
                "words": words,
            }
        )

    return {
        "language": getattr(info, "language", config.WHISPER_LANGUAGE),
        "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 3),
        "segments": out_segments,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def transcribe_mix(base: Path, meeting_dir: Path, *, on_progress=None) -> dict:
    audio_path = meeting_dir / "audio" / "mix.wav"
    output = meeting_dir / "transcripts" / "mix.json"

    if not audio_path.is_file():
        payload = {"language": config.WHISPER_LANGUAGE, "duration": 0.0, "segments": []}
        _write_json(output, payload)
        return payload

    model_dir = models.ensure_whisper_model(base, config.MIX_MODEL, on_progress=on_progress)
    model = _load_model(model_dir)
    try:
        payload = _transcribe_file(model, audio_path)
    finally:
        del model

    _write_json(output, payload)
    return payload


def transcribe_users(base: Path, meeting_dir: Path, *, on_progress=None) -> dict[str, dict]:
    """Per-speaker transcripts keyed by Discord user id."""
    users_dir = meeting_dir / "audio" / "users"
    audio_files = sorted(users_dir.glob("*.wav")) if users_dir.is_dir() else []
    if not audio_files:
        return {}

    model_dir = models.ensure_whisper_model(base, config.USER_MODEL, on_progress=on_progress)
    model = _load_model(model_dir)

    results: dict[str, dict] = {}
    try:
        for index, audio_path in enumerate(audio_files):
            discord_id = audio_path.stem
            if on_progress is not None:
                on_progress(index / len(audio_files))
            payload = _transcribe_file(model, audio_path)
            results[discord_id] = payload
            _write_json(meeting_dir / "transcripts" / "users" / f"{discord_id}.json", payload)
    finally:
        del model

    return results


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
