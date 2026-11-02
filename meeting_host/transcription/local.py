"""faster-whisper: heavy model on the mix, light model per speaker."""
from __future__ import annotations

from pathlib import Path

from meeting_host import config, models
from meeting_host.transcription.base import Progress, TranscriptSource


class LocalWhisperSource(TranscriptSource):
    id = "local"

    def __init__(self) -> None:
        self._model = None
        self._label = ""

    def prepare(self, base: Path, *, role: str, on_progress: Progress | None = None) -> str:
        model_name = config.MIX_MODEL if role == "mix" else config.USER_MODEL
        model_dir = models.ensure_whisper_model(
            base,
            model_name,
            on_progress=on_progress,
        )
        self._model, device = _load_model(model_dir)
        self._label = f"{model_name} · {device}" if device else model_name
        return self._label

    def transcribe(
        self,
        audio_path: Path,
        *,
        base: Path,
        on_progress: Progress | None = None,
    ) -> dict:
        if self._model is None:
            raise RuntimeError("Local transcription was used before its model was loaded.")
        return _transcribe_file(self._model, audio_path)

    def close(self) -> None:
        model = self._model
        self._model = None
        if model is not None:
            del model


def _load_model(model_dir: Path):
    """Prefer CUDA; fall back to CPU int8 so a weak host still works."""
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(str(model_dir), device="cuda", compute_type="float16")
        return model, "CUDA float16"
    except Exception:
        model = WhisperModel(str(model_dir), device="cpu", compute_type="int8")
        return model, "CPU int8"


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
