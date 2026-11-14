"""Groq Whisper. Mix uses large-v3; each speaker uses large-v3-turbo.

Uploads stay under the file-size cap, and the seconds of audio sent are
kept under the hourly cap across runs of this host.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from meeting_host import config
from meeting_host.host_secrets import get_groq_api_key
from meeting_host.transcription.audio import uploads_for_wav
from meeting_host.transcription.base import Progress, TranscriptSource

_LANGUAGE_NAMES = {"english": "en", "en": "en"}


class GroqUnavailable(RuntimeError):
    """Groq did not return a transcript. Local Whisper may take over."""


class HourlyAudioBudget:
    """Remember audio seconds sent in the last hour and wait when the next one would not fit."""

    def __init__(
        self,
        path: Path,
        *,
        limit_seconds: float,
        window_seconds: float = 3600.0,
        now=None,
        sleep=None,
    ) -> None:
        self.path = path
        self.limit_seconds = float(limit_seconds)
        self.window_seconds = float(window_seconds)
        self._now = now or time.time
        self._sleep = sleep or time.sleep

    def acquire(self, seconds: float) -> float:
        """Block until `seconds` fits. Returns how long the call waited."""
        needed = max(0.0, float(seconds))
        if needed > self.limit_seconds:
            raise RuntimeError(
                f"One transcription piece is {needed:.0f}s of audio, above the "
                f"{self.limit_seconds:.0f}s Groq allows per hour."
            )
        waited = 0.0
        while True:
            now = float(self._now())
            events = [
                (stamp, amount)
                for stamp, amount in self._load()
                if now - stamp < self.window_seconds
            ]
            used = sum(amount for _, amount in events)
            if used + needed <= self.limit_seconds + 1e-6:
                if needed > 0:
                    events.append((now, needed))
                    self._save(events)
                return waited
            oldest = min(stamp for stamp, _ in events)
            delay = max(0.5, self.window_seconds - (now - oldest) + 0.05)
            self._sleep(delay)
            waited += delay

    def _load(self) -> list[tuple[float, float]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        events = []
        for item in payload.get("events") or []:
            try:
                events.append((float(item["at"]), float(item["seconds"])))
            except (KeyError, TypeError, ValueError):
                continue
        return events

    def _save(self, events: list[tuple[float, float]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "events": [{"at": stamp, "seconds": amount} for stamp, amount in events],
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(body), encoding="utf-8")
        temporary.replace(self.path)


class GroqWhisperSource(TranscriptSource):
    id = "groq"

    def __init__(self, *, post=None, budget: HourlyAudioBudget | None = None, api_key: str | None = None) -> None:
        self._post = post or _post_transcription
        self._budget = budget
        self._api_key = api_key
        self._role = "mix"
        self._label = ""

    def prepare(self, base: Path, *, role: str, on_progress: Progress | None = None) -> str:
        self._role = role
        if self._api_key is None:
            try:
                self._api_key = get_groq_api_key()
            except RuntimeError as exc:
                raise GroqUnavailable(str(exc)) from exc
        model = _model_for(role)
        self._label = f"{model} · Groq"
        if self._budget is None:
            self._budget = HourlyAudioBudget(
                config.mike_dir(base) / "groq_audio_budget.json",
                limit_seconds=config.GROQ_AUDIO_SECONDS_PER_HOUR,
            )
        return self._label

    def transcribe(
        self,
        audio_path: Path,
        *,
        base: Path,
        on_progress: Progress | None = None,
    ) -> dict:
        if not self._api_key:
            raise RuntimeError("Groq transcription was used before the API key was loaded.")
        budget = self._budget
        if budget is None:
            raise RuntimeError("Groq transcription was used before it was prepared.")

        limit = config.GROQ_MAX_FILE_BYTES - config.GROQ_UPLOAD_MARGIN_BYTES
        uploads = uploads_for_wav(
            audio_path,
            max_bytes=limit,
            max_seconds=config.GROQ_AUDIO_SECONDS_PER_HOUR,
            stem=audio_path.stem,
        )
        if not uploads:
            return {"language": config.WHISPER_LANGUAGE, "duration": 0.0, "segments": []}

        model = _model_for(self._role)
        parts: list[dict] = []
        total = len(uploads)
        for index, upload in enumerate(uploads, start=1):
            prefix = f"Part {index}/{total}: " if total > 1 else ""
            if on_progress is not None:
                on_progress(f"{prefix}waiting for Groq audio budget")
            try:
                waited = budget.acquire(upload.duration_seconds)
            except RuntimeError as exc:
                raise GroqUnavailable(str(exc)) from exc
            if waited >= 1 and on_progress is not None:
                on_progress(f"{prefix}waited {int(waited)}s for the hourly audio budget")
            if on_progress is not None:
                on_progress(f"{prefix}uploading {upload.filename}")
            body = self._post(
                url=config.GROQ_TRANSCRIBE_URL,
                api_key=self._api_key,
                model=model,
                upload=upload,
            )
            parts.append(parse_groq_transcript(body, offset_seconds=upload.offset_seconds))

        return combine_transcripts(parts, duration=sum(item.duration_seconds for item in uploads))

    def close(self) -> None:
        self._api_key = None


def _model_for(role: str) -> str:
    return config.GROQ_MIX_MODEL if role == "mix" else config.GROQ_USER_MODEL


def parse_groq_transcript(body: dict, *, offset_seconds: float = 0.0) -> dict:
    """Map a verbose_json body onto the local transcript shape, shifted onto the meeting timeline."""
    language = str(body.get("language") or config.WHISPER_LANGUAGE).strip().lower()
    language = _LANGUAGE_NAMES.get(language, language or config.WHISPER_LANGUAGE)
    segments_in = body.get("segments") or []
    loose_words = [_word(item, offset_seconds) for item in body.get("words") or []]
    loose_words = [word for word in loose_words if word["word"]]

    segments = []
    for item in segments_in:
        text = str(item.get("text") or "").strip()
        words = [_word(word, offset_seconds) for word in item.get("words") or []]
        words = [word for word in words if word["word"]]
        start = round(float(item.get("start") or 0.0) + offset_seconds, 3)
        end = round(float(item.get("end") or item.get("start") or 0.0) + offset_seconds, 3)
        if not words and loose_words:
            words = [word for word in loose_words if start - 0.05 <= word["start"] < end + 0.05]
        if not text and words:
            text = " ".join(word["word"] for word in words).strip()
        if not text:
            continue
        if not words:
            words = _words_from_text(text, start, end)
        segments.append(
            {
                "start": start,
                "end": max(end, start),
                "text": text,
                "words": words,
            }
        )

    if not segments and loose_words:
        segments.append(
            {
                "start": loose_words[0]["start"],
                "end": loose_words[-1]["end"],
                "text": " ".join(word["word"] for word in loose_words).strip(),
                "words": loose_words,
            }
        )

    duration = float(body.get("duration") or 0.0)
    if segments:
        duration = max(duration, segments[-1]["end"] - offset_seconds)
    return {
        "language": language,
        "duration": round(duration, 3),
        "segments": segments,
    }


def combine_transcripts(parts: list[dict], *, duration: float) -> dict:
    segments = []
    language = config.WHISPER_LANGUAGE
    for part in parts:
        language = str(part.get("language") or language)
        segments.extend(part.get("segments") or [])
    return {
        "language": language,
        "duration": round(float(duration), 3),
        "segments": segments,
    }


def _word(item: dict, offset_seconds: float) -> dict:
    start = round(float(item.get("start") or 0.0) + offset_seconds, 3)
    end = round(float(item.get("end") or item.get("start") or 0.0) + offset_seconds, 3)
    return {
        "start": start,
        "end": max(end, start),
        "word": str(item.get("word") or "").strip(),
    }


def _words_from_text(text: str, start: float, end: float) -> list[dict]:
    """Spread a segment that arrived without word times so speaker assignment still has a timeline."""
    tokens = text.split()
    if not tokens:
        return []
    span = max(0.0, end - start)
    step = span / len(tokens)
    words = []
    for index, token in enumerate(tokens):
        word_start = round(start + index * step, 3)
        word_end = round(start + (index + 1) * step, 3)
        words.append({"start": word_start, "end": max(word_end, word_start), "word": token})
    return words


def _post_transcription(*, url: str, api_key: str, model: str, upload) -> dict:
    import requests

    headers = {"Authorization": f"Bearer {api_key}"}
    form = [
        ("model", model),
        ("language", config.WHISPER_LANGUAGE),
        ("response_format", "verbose_json"),
        ("temperature", "0"),
        ("timestamp_granularities[]", "word"),
        ("timestamp_granularities[]", "segment"),
    ]
    files = {"file": (upload.filename, upload.content, upload.media_type)}
    last_error = "Groq transcription failed."
    for attempt in range(3):
        try:
            response = requests.post(
                url,
                headers=headers,
                data=form,
                files=files,
                timeout=config.GROQ_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_error = _scrub(str(exc), api_key)
            time.sleep(min(8, 2 ** attempt))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            last_error = _scrub(_error_text(response), api_key)
            delay = _retry_after(response) if response.status_code == 429 else min(8, 2 ** attempt)
            time.sleep(delay)
            continue
        if response.status_code >= 400:
            raise GroqUnavailable(_scrub(_error_text(response), api_key))
        try:
            body = response.json()
        except ValueError as exc:
            raise GroqUnavailable("Groq returned a transcription that was not JSON.") from exc
        if not isinstance(body, dict):
            raise GroqUnavailable("Groq returned a transcription that was not an object.")
        return body
    raise GroqUnavailable(last_error)


def _retry_after(response) -> float:
    raw = response.headers.get("retry-after", "")
    try:
        return max(0.5, float(raw))
    except (TypeError, ValueError):
        return 2.0


def _error_text(response) -> str:
    detail = (response.text or "").strip().replace("\n", " ")
    if len(detail) > 300:
        detail = detail[:300] + "..."
    return f"Groq transcription failed ({response.status_code}): {detail}"


def _scrub(text: str, api_key: str) -> str:
    cleaned = re.sub(r"Bearer\s+\S+", "Bearer [redacted]", text)
    if api_key:
        cleaned = cleaned.replace(api_key, "[redacted]")
    return cleaned
