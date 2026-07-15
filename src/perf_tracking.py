"""Cross-platform peak RSS/working-set sampler.

Extracted from meeting_transcriber.py's Step 2 pass so meeting_combined_transcriber.py's
Step 5 pass (see step-5-combined-audio-upgrade.md) can report the same
"roughly how much RAM did this step use" baseline number without duplicating
the sampling logic - both are CPU-heavy local-model passes that want the
same kind of before/after measurement.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import psutil

DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class PeakMemoryTracker:
    """Cross-platform peak RSS/working-set sampler for this process.

    On Windows, psutil.Process.memory_info() already returns an extended
    pmem tuple that includes peak_wset — the OS-tracked all-time-high
    working set for the process — so no sampling is needed there. Elsewhere
    (e.g. running this outside Windows during development), there's no
    OS-tracked peak available through psutil, so a background thread polls
    rss periodically and keeps the running max. Coarse, but good enough for
    the "roughly how much RAM did this step use" baseline number the plans
    ask for.
    """

    def __init__(self, *, poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self._process = psutil.Process(os.getpid())
        self._poll_interval = poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sampled_peak_bytes = 0

    def __enter__(self) -> "PeakMemoryTracker":
        if not hasattr(self._process.memory_info(), "peak_wset"):
            self._thread = threading.Thread(
                target=self._poll, name="peak_ram_sampler", daemon=True
            )
            self._thread.start()
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                self._sampled_peak_bytes = max(
                    self._sampled_peak_bytes, self._process.memory_info().rss
                )
            except Exception:
                pass
            self._stop.wait(self._poll_interval)

    @property
    def peak_bytes(self) -> int:
        mem = self._process.memory_info()
        peak_wset = getattr(mem, "peak_wset", None)
        if peak_wset is not None:
            return peak_wset
        return max(self._sampled_peak_bytes, mem.rss)
