"""Transcription sources.

The pipeline asks for a mix transcript and one transcript per speaker. Which
engine does that work is selected here, not inside the pipeline.

To add a source: implement TranscriptSource, register it below, and give it
a short id. Select it with config.TRANSCRIPTION_SOURCE, a baked
TRANSCRIPTION_SOURCE in runtime_secrets.py, or ELG_TRANSCRIPTION_SOURCE.
"""
from __future__ import annotations

import os

from meeting_host import config
from meeting_host.host_secrets import baked_transcription_source
from meeting_host.transcription.base import TranscriptSource
from meeting_host.transcription.groq import GroqWhisperSource
from meeting_host.transcription.local import LocalWhisperSource

_SOURCES: dict[str, type[TranscriptSource]] = {
    LocalWhisperSource.id: LocalWhisperSource,
    GroqWhisperSource.id: GroqWhisperSource,
}


def known_sources() -> tuple[str, ...]:
    return tuple(sorted(_SOURCES))


def selected_source_id() -> str:
    """Env override, then the id baked into a build, then config."""
    env = os.environ.get("ELG_TRANSCRIPTION_SOURCE", "").strip().lower()
    if env:
        return env
    baked = baked_transcription_source()
    if baked:
        return baked
    return config.TRANSCRIPTION_SOURCE.strip().lower()


def open_source(source_id: str | None = None) -> TranscriptSource:
    chosen = (source_id or selected_source_id() or LocalWhisperSource.id).strip().lower()
    kind = _SOURCES.get(chosen)
    if kind is None:
        known = ", ".join(known_sources())
        raise RuntimeError(f"Unknown transcription source {chosen!r}. Known sources: {known}.")
    return kind()
