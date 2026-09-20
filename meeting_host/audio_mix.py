"""Per-user WAV writers on one shared timeline, plus a clip-safe mixdown.

Discord delivers 48 kHz stereo PCM only while someone is speaking, so each
writer pads silence to keep every stream aligned to recording T0. That padding
is what makes late joiners and gaps line up in the mix.
"""
from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np

from meeting_host import config


def decode_discord_pcm(pcm: bytes) -> np.ndarray:
    """48 kHz stereo s16le -> 16 kHz mono int16.

    Averages each group of 3 samples, which both downsamples by the required
    factor and acts as a crude anti-alias filter.
    """
    if not pcm:
        return np.zeros(0, dtype=np.int16)

    # A truncated packet must never raise inside the voice callback.
    usable_bytes = len(pcm) - (len(pcm) % config.SAMPLE_WIDTH)
    if usable_bytes <= 0:
        return np.zeros(0, dtype=np.int16)

    samples = np.frombuffer(pcm[:usable_bytes], dtype="<i2")
    if samples.size == 0:
        return np.zeros(0, dtype=np.int16)

    if config.DISCORD_CHANNELS > 1:
        usable = samples.size - (samples.size % config.DISCORD_CHANNELS)
        if usable <= 0:
            return np.zeros(0, dtype=np.int16)
        stereo = samples[:usable].reshape(-1, config.DISCORD_CHANNELS)
        mono = stereo.astype(np.int32).mean(axis=1)
    else:
        mono = samples.astype(np.int32)

    usable = mono.size - (mono.size % config.DECIMATION)
    if usable <= 0:
        return np.zeros(0, dtype=np.int16)
    grouped = mono[:usable].reshape(-1, config.DECIMATION)
    return grouped.mean(axis=1).round().astype(np.int16)


class AlignedWavWriter:
    """Single stream, silence-padded so sample N is always N/16000 seconds in."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._samples_written = 0
        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(config.CHANNELS)
        self._wave.setsampwidth(config.SAMPLE_WIDTH)
        self._wave.setframerate(config.SAMPLE_RATE)

    @property
    def samples_written(self) -> int:
        with self._lock:
            return self._samples_written

    def write(self, samples: np.ndarray, *, offset_samples: int) -> None:
        """Append `samples`, padding silence when the stream fell behind."""
        if samples.size == 0:
            return
        with self._lock:
            if self._wave is None:
                return
            gap = offset_samples - self._samples_written
            if gap > config.PAD_TOLERANCE_SAMPLES:
                silence = np.zeros(gap, dtype=np.int16)
                self._wave.writeframes(silence.tobytes())
                self._samples_written += gap
            self._wave.writeframes(samples.astype(np.int16).tobytes())
            self._samples_written += int(samples.size)

    def pad_to(self, total_samples: int) -> None:
        with self._lock:
            if self._wave is None:
                return
            gap = total_samples - self._samples_written
            if gap > 0:
                self._wave.writeframes(np.zeros(gap, dtype=np.int16).tobytes())
                self._samples_written += gap

    def close(self) -> None:
        with self._lock:
            if self._wave is not None:
                try:
                    self._wave.close()
                finally:
                    self._wave = None


def read_wav_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        channels = handle.getnchannels()
    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        usable = samples.size - (samples.size % channels)
        samples = samples[:usable].reshape(-1, channels).astype(np.int32).mean(axis=1)
    return samples.astype(np.int32)


def write_wav_mono(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(config.CHANNELS)
        handle.setsampwidth(config.SAMPLE_WIDTH)
        handle.setframerate(config.SAMPLE_RATE)
        handle.writeframes(samples.astype(np.int16).tobytes())


def mix_wavs(sources: list[Path], destination: Path) -> float:
    """Sum aligned streams into one clip-safe mix. Returns duration seconds."""
    tracks = [read_wav_mono(path) for path in sources if path.is_file()]
    tracks = [track for track in tracks if track.size > 0]

    if not tracks:
        write_wav_mono(destination, np.zeros(0, dtype=np.int16))
        return 0.0

    length = max(track.size for track in tracks)
    accumulator = np.zeros(length, dtype=np.int32)
    for track in tracks:
        accumulator[: track.size] += track

    np.clip(accumulator, -32768, 32767, out=accumulator)
    write_wav_mono(destination, accumulator)
    return length / float(config.SAMPLE_RATE)


def wav_duration_seconds(path: Path) -> float:
    if not path.is_file():
        return 0.0
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate() or config.SAMPLE_RATE
            return handle.getnframes() / float(rate)
    except (OSError, wave.Error):
        return 0.0
