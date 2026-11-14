"""Interface every transcription source implements."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

Progress = Callable[[str], None]


class TranscriptSource:
    """One engine that can transcribe a mix file and a per-speaker file.

    `role` is "mix" or "user". Sources pick their own model from that.
    `transcribe` returns the transcript dict the rest of the pipeline already
    consumes: language, duration, and segments of {start, end, text, words}.
    Word times are seconds on the file's own timeline.
    """

    id: str = ""

    def prepare(self, base: Path, *, role: str, on_progress: Progress | None = None) -> str:
        """Get the engine ready. Returns the label shown in the status window."""
        raise NotImplementedError

    def transcribe(
        self,
        audio_path: Path,
        *,
        base: Path,
        on_progress: Progress | None = None,
    ) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        """Release anything prepare() held. Must be safe to call twice."""
