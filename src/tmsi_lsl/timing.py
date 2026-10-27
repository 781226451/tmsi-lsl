"""Timing instrumentation for the SDK sampling loop.

The SDK's sampling thread calls ``device.get_device_data()`` in a loop with a
10 ms pause (``TMSiThread``).  On Windows that pause is only accurate from
Python 3.11 onwards — ``time.sleep(0.01)`` costs ~15.6 ms on 3.10 because it
rounds up to the system timer tick.  This module measures what actually happens
on the machine that is running now:

* wrap the device object's ``get_device_data`` without touching the vendored SDK,
* record the start/end of every call plus the interval since the previous one,
* optionally stream the records to a CSV file and summarise them at the end.

Usage from Python::

    recorder = source.enable_fetch_timing("timing.csv")
    ...
    print(recorder.summary())
"""

from __future__ import annotations

import csv
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

__all__ = [
    "FetchRecord",
    "FetchStats",
    "FetchTimingRecorder",
    "instrument_device",
]

#: The pause the SDK sleeps between two fetches (``TMSiThread(pause=0.01)``).
SDK_FETCH_PAUSE_S = 0.01


@dataclass(frozen=True)
class FetchRecord:
    """One ``get_device_data`` call."""

    index: int
    t_start: float
    t_end: float
    interval: float | None  # seconds since the previous call started
    num_sets: int | None  # sample sets returned, None when unreadable

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


@dataclass(frozen=True)
class FetchStats:
    """Aggregate view over the recorded calls."""

    count: int
    span: float
    interval_mean: float
    interval_min: float
    interval_max: float
    interval_p50: float
    interval_p95: float
    interval_p99: float
    duration_mean: float
    duration_p95: float
    duration_max: float
    total_sets: int
    empty_fetches: int

    @property
    def fetch_rate_hz(self) -> float:
        return (self.count - 1) / self.span if self.span > 0 else 0.0

    def as_summary(self) -> str:
        if self.count == 0:
            return "SDK data fetch timing: no calls recorded"

        def ms(value: float) -> str:
            return f"{value * 1000:.2f}"

        return "\n".join(
            [
                "SDK data fetch timing",
                f"  fetches       : {self.count:,} over {self.span:.3f} s "
                f"({self.fetch_rate_hz:.1f} Hz)",
                f"  interval (ms) : mean {ms(self.interval_mean)}  "
                f"p50 {ms(self.interval_p50)}  p95 {ms(self.interval_p95)}  "
                f"p99 {ms(self.interval_p99)}  max {ms(self.interval_max)}  "
                f"min {ms(self.interval_min)}",
                f"  call (ms)     : mean {ms(self.duration_mean)}  "
                f"p95 {ms(self.duration_p95)}  max {ms(self.duration_max)}",
                f"  sample sets   : {self.total_sets:,} total "
                f"({self.empty_fetches:,} empty fetches)",
            ]
        )


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    position = min(len(sorted_values) - 1, max(0, int(round(fraction * (len(sorted_values) - 1)))))
    return sorted_values[position]


class FetchTimingRecorder:
    """Thread-safe recorder for ``get_device_data`` calls.

    :param path: optional CSV file that receives every call as it happens.
    :param keep: how many records to keep in memory for the statistics.
    """

    def __init__(self, path: str | Path | None = None, keep: int = 200_000) -> None:
        self._lock = threading.Lock()
        self._records: list[FetchRecord] = []
        self._keep = max(1, int(keep))
        self._count = 0
        self._previous_start: float | None = None
        self._first_start: float | None = None
        self._last_start: float | None = None
        self._path = Path(path) if path else None
        self._handle = None
        self._writer = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._handle)
            self._writer.writerow(
                ["index", "t_start_s", "duration_ms", "interval_ms", "num_sets"]
            )

    # ------------------------------------------------------------------ record

    def record(self, t_start: float, t_end: float, num_sets: int | None) -> None:
        """Store one fetch. Called from the SDK sampling thread."""
        with self._lock:
            index = self._count
            self._count += 1
            if self._first_start is None:
                self._first_start = t_start
            interval = None if self._last_start is None else t_start - self._last_start
            self._last_start = t_start

            record = FetchRecord(
                index=index,
                t_start=t_start,
                t_end=t_end,
                interval=interval,
                num_sets=num_sets,
            )
            self._records.append(record)
            if len(self._records) > self._keep:
                del self._records[: len(self._records) - self._keep]

            if self._writer is not None:
                self._writer.writerow(
                    [
                        index,
                        f"{t_start:.6f}",
                        f"{record.duration * 1000:.3f}",
                        "" if interval is None else f"{interval * 1000:.3f}",
                        "" if num_sets is None else num_sets,
                    ]
                )

    # ------------------------------------------------------------------- stats

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def records(self) -> list[FetchRecord]:
        with self._lock:
            return list(self._records)

    def stats(self) -> FetchStats:
        with self._lock:
            records = list(self._records)
            count = self._count
            first = self._first_start
            last = self._last_start

        intervals = sorted(r.interval for r in records if r.interval is not None)
        durations = sorted(r.duration for r in records)
        total_sets = sum(r.num_sets or 0 for r in records)
        empty = sum(1 for r in records if r.num_sets == 0)
        span = 0.0 if first is None or last is None else max(0.0, last - first)

        return FetchStats(
            count=count,
            span=span,
            interval_mean=statistics.fmean(intervals) if intervals else 0.0,
            interval_min=intervals[0] if intervals else 0.0,
            interval_max=intervals[-1] if intervals else 0.0,
            interval_p50=_percentile(intervals, 0.50),
            interval_p95=_percentile(intervals, 0.95),
            interval_p99=_percentile(intervals, 0.99),
            duration_mean=statistics.fmean(durations) if durations else 0.0,
            duration_p95=_percentile(durations, 0.95),
            duration_max=durations[-1] if durations else 0.0,
            total_sets=total_sets,
            empty_fetches=empty,
        )

    def summary(self) -> str:
        text = self.stats().as_summary()
        if self._path is not None:
            text += f"\n  csv           : {self._path}"
        return text

    # ------------------------------------------------------------------- close

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.flush()
                self._handle.close()
                self._handle = None
                self._writer = None

    def __enter__(self) -> "FetchTimingRecorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def instrument_device(
    device,
    recorder: FetchTimingRecorder,
) -> Callable[[], None]:
    """Wrap ``device.get_device_data`` so every fetch is recorded.

    The vendored SDK stays untouched: only the instance attribute is replaced.
    :return: a callable that restores the original method.
    """
    original = device.get_device_data

    def timed_get_device_data(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            end = time.perf_counter()
            num_sets = None
            # The received-sample-set counter is the 3rd positional argument.
            if len(args) >= 3 and hasattr(args[2], "contents"):
                try:
                    num_sets = int(args[2].contents.value)
                except Exception:  # noqa: BLE001 - instrumentation must not fail
                    num_sets = None
            recorder.record(start, end, num_sets)

    device.get_device_data = timed_get_device_data

    def restore() -> None:
        try:
            del device.get_device_data
        except AttributeError:  # pragma: no cover - nothing to restore
            pass

    return restore
