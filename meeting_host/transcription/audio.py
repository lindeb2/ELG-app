"""Turn a meeting WAV into uploads that fit a remote transcription cap.

The meeting audio is already 16 kHz mono, which is what Whisper resamples to.
FLAC keeps those samples and only shrinks the upload. A span is sized so the
WAV of that span is under the byte cap, which means the FLAC of it is too.
Each span is also kept under the hourly audio budget so one request can be
accepted on its own.
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

from meeting_host import config
from meeting_host.audio_mix import read_wav_mono


@dataclass(frozen=True)
class AudioUpload:
    filename: str
    content: bytes
    media_type: str
    offset_seconds: float
    duration_seconds: float


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        pcm = np.ascontiguousarray(samples.astype(np.int16))
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def encode_flac(samples: np.ndarray, sample_rate: int) -> bytes | None:
    """Lossless FLAC, or None when PyAV is not installed."""
    try:
        import av
    except ImportError:
        return None

    pcm = np.ascontiguousarray(samples.astype(np.int16))
    if pcm.size == 0:
        return wav_bytes(pcm, sample_rate)

    buffer = io.BytesIO()
    try:
        with av.open(buffer, "w", format="flac") as container:
            stream = container.add_stream("flac", rate=sample_rate)
            stream.layout = "mono"
            frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16p", layout="mono")
            frame.sample_rate = sample_rate
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
    except Exception:
        return None
    encoded = buffer.getvalue()
    return encoded or None


def _max_samples(sample_rate: int, *, max_bytes: int, max_seconds: float) -> int:
    # 44-byte header plus 16-bit mono PCM. Staying under this as WAV guarantees
    # the same span is under the cap as FLAC too.
    payload = max(2, max_bytes - 64)
    by_bytes = payload // 2
    by_time = int(max_seconds * sample_rate) if max_seconds > 0 else by_bytes
    return max(1, min(by_bytes, by_time))


def spans_for(sample_count: int, sample_rate: int, *, max_bytes: int, max_seconds: float) -> list[tuple[int, int]]:
    """Half-open sample ranges. The last span ends at sample_count."""
    if sample_count <= 0:
        return []
    step = _max_samples(sample_rate, max_bytes=max_bytes, max_seconds=max_seconds)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < sample_count:
        end = min(sample_count, start + step)
        spans.append((start, end))
        start = end
    return spans


def uploads_for_wav(
    path,
    *,
    max_bytes: int,
    max_seconds: float,
    stem: str = "audio",
) -> list[AudioUpload]:
    samples = read_wav_mono(path)
    rate = config.SAMPLE_RATE
    pieces = spans_for(int(samples.size), rate, max_bytes=max_bytes, max_seconds=max_seconds)
    if not pieces:
        return []

    flac_ok = encode_flac(samples[: min(int(samples.size), rate)], rate) is not None
    uploads: list[AudioUpload] = []
    multiple = len(pieces) > 1
    for index, (start, end) in enumerate(pieces, start=1):
        span = samples[start:end]
        if flac_ok:
            content = encode_flac(span, rate)
            suffix, media = "flac", "audio/flac"
        else:
            content = None
            suffix, media = "wav", "audio/wav"
        if content is None or len(content) > max_bytes:
            content = wav_bytes(span, rate)
            suffix, media = "wav", "audio/wav"
        if len(content) > max_bytes:
            raise RuntimeError(
                f"Transcription upload is {len(content)} bytes, above the {max_bytes} byte cap."
            )
        name = f"{stem}-{index}.{suffix}" if multiple else f"{stem}.{suffix}"
        uploads.append(
            AudioUpload(
                filename=name,
                content=content,
                media_type=media,
                offset_seconds=start / float(rate),
                duration_seconds=(end - start) / float(rate),
            )
        )
    return uploads
