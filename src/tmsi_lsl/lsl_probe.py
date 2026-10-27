"""Measure what an LSL consumer actually receives.

:class:`~tmsi_lsl.timing.FetchTimingRecorder` looks at the SDK side (how often the
device is polled); this module looks at the *consumer* side: every ``pull_chunk``
on an inlet is recorded with the number of samples it returned and the LSL
timestamps that came with them.  That is what LabRecorder / MNE-LSL / PsychoPy
see, including any coalescing liblsl does internally.

Typical use::

    recorder = LslChunkRecorder("receive.csv")
    run_probe("TMSi SAGA", duration=5.0, recorder=recorder)
    print(recorder.summary())

or from the command line: ``scripts/lsl_receive_probe.py``.
"""

from __future__ import annotations

import csv
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "LslChunkRecord",
    "LslChunkStats",
    "LslChunkRecorder",
    "run_probe",
    "ProbeError",
]


class ProbeError(RuntimeError):
    """The stream could not be found or the inlet could not be opened."""


@dataclass(frozen=True)
class LslChunkRecord:
    """One ``pull_chunk`` result."""

    index: int
    pulled_at: float  # time.perf_counter()
    gap: float | None  # since the previous pull returned data
    samples: int
    first_ts: float | None  # LSL local clock of the first sample
    last_ts: float | None  # LSL local clock of the last sample

    @property
    def span(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 0.0
        return max(0.0, self.last_ts - self.first_ts)


@dataclass(frozen=True)
class LslChunkStats:
    """Aggregate view over the recorded pulls."""

    pulls: int
    samples: int
    duration: float
    expected_rate: float
    samples_mean: float
    samples_min: int
    samples_max: int
    samples_p50: float
    samples_p95: float
    gap_mean: float
    gap_p95: float
    gap_max: float
    ts_step_mean: float  # mean delta between consecutive chunk timestamps
    ts_step_p95: float
    ts_step_max: float
    max_ts_error: float  # |delta_ts - previous_samples/rate|, worst case

    @property
    def pulls_per_second(self) -> float:
        return self.pulls / self.duration if self.duration > 0 else 0.0

    @property
    def samples_per_second(self) -> float:
        return self.samples / self.duration if self.duration > 0 else 0.0

    def as_summary(self) -> str:
        if self.pulls == 0:
            return "LSL receive timing: no chunks received"

        def ms(value: float) -> str:
            return f"{value * 1000:.2f}"

        return "\n".join(
            [
                "LSL receive timing",
                f"  pulls         : {self.pulls:,} over {self.duration:.3f} s "
                f"({self.pulls_per_second:.1f} pull/s), "
                f"{self.samples:,} samples "
                f"({self.samples_per_second:,.0f} samples/s, "
                f"stream says {self.expected_rate:g} Hz)",
                f"  samples/pull  : mean {self.samples_mean:.1f}  "
                f"p50 {self.samples_p50:.1f}  p95 {self.samples_p95:.1f}  "
                f"min {self.samples_min}  max {self.samples_max}",
                f"  pull gap (ms) : mean {ms(self.gap_mean)}  "
                f"p95 {ms(self.gap_p95)}  max {ms(self.gap_max)}",
                f"  ts step (ms)  : mean {ms(self.ts_step_mean)}  "
                f"p95 {ms(self.ts_step_p95)}  max {ms(self.ts_step_max)}"
                if self.ts_step_mean
                else "  ts step (ms)  : no timestamps",
                f"  worst ts gap  : {ms(self.max_ts_error)} (vs. expected sample count)",
            ]
        )


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    position = min(len(sorted_values) - 1, max(0, int(round(fraction * (len(sorted_values) - 1)))))
    return sorted_values[position]


class LslChunkRecorder:
    """Thread-safe recorder for inlet pulls, optionally mirrored to CSV."""

    def __init__(
        self,
        path: str | Path | None = None,
        expected_rate: float = 0.0,
    ) -> None:
        self._lock = threading.Lock()
        self._records: list[LslChunkRecord] = []
        self._count = 0
        self._samples = 0
        self._first_pull: float | None = None
        self._last_pull: float | None = None
        self._expected_rate = float(expected_rate)
        self._path = Path(path) if path else None
        self._handle = None
        self._writer = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._handle)
            self._writer.writerow(
                [
                    "index",
                    "pulled_at_s",
                    "gap_ms",
                    "samples",
                    "first_ts",
                    "last_ts",
                    "span_ms",
                ]
            )

    # ------------------------------------------------------------------ record

    def record(
        self,
        samples: int,
        first_ts: float | None = None,
        last_ts: float | None = None,
    ) -> None:
        """Store one pull (call it after ``inlet.pull_chunk`` returned data)."""
        with self._lock:
            now = time.perf_counter()
            if self._first_pull is None:
                self._first_pull = now
            gap = None if self._last_pull is None else now - self._last_pull
            self._last_pull = now

            record = LslChunkRecord(
                index=self._count,
                pulled_at=now,
                gap=gap,
                samples=int(samples),
                first_ts=first_ts,
                last_ts=last_ts,
            )
            self._count += 1
            self._samples += int(samples)
            self._records.append(record)

            if self._writer is not None:
                self._writer.writerow(
                    [
                        record.index,
                        f"{now:.6f}",
                        "" if gap is None else f"{gap * 1000:.3f}",
                        record.samples,
                        "" if first_ts is None else f"{first_ts:.6f}",
                        "" if last_ts is None else f"{last_ts:.6f}",
                        f"{record.span * 1000:.3f}",
                    ]
                )

    # ------------------------------------------------------------------- stats

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def records(self) -> list[LslChunkRecord]:
        with self._lock:
            return list(self._records)

    def stats(self) -> LslChunkStats:
        with self._lock:
            records = list(self._records)
            total_samples = self._samples
            first = self._first_pull
            last = self._last_pull
            rate = self._expected_rate

        sizes = sorted(float(r.samples) for r in records)
        gaps = sorted(r.gap for r in records if r.gap is not None)
        duration = 0.0 if first is None or last is None else max(0.0, last - first)

        # Continuity: the timestamp step between chunks should match the number
        # of samples in the previous chunk.
        steps: list[float] = []
        errors: list[float] = []
        previous: LslChunkRecord | None = None
        for record in records:
            if record.first_ts is not None and previous is not None and previous.first_ts is not None:
                step = record.first_ts - previous.first_ts
                steps.append(step)
                if rate > 0:
                    errors.append(abs(step - previous.samples / rate))
            previous = record
        steps.sort()
        errors.sort()

        return LslChunkStats(
            pulls=len(records),
            samples=total_samples,
            duration=duration,
            expected_rate=rate,
            samples_mean=statistics.fmean(sizes) if sizes else 0.0,
            samples_min=int(sizes[0]) if sizes else 0,
            samples_max=int(sizes[-1]) if sizes else 0,
            samples_p50=_percentile(sizes, 0.50),
            samples_p95=_percentile(sizes, 0.95),
            gap_mean=statistics.fmean(gaps) if gaps else 0.0,
            gap_p95=_percentile(gaps, 0.95),
            gap_max=gaps[-1] if gaps else 0.0,
            ts_step_mean=statistics.fmean(steps) if steps else 0.0,
            ts_step_p95=_percentile(steps, 0.95),
            ts_step_max=steps[-1] if steps else 0.0,
            max_ts_error=errors[-1] if errors else 0.0,
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

    def __enter__(self) -> "LslChunkRecorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


# --------------------------------------------------------------------- probing


def run_probe(
    name: str,
    duration: float,
    recorder: LslChunkRecorder,
    resolve_timeout: float = 10.0,
    max_samples: int = 1024,
    pull_timeout: float = 0.5,
    min_samples: int = 1,
) -> int:
    """Resolve ``name``, pull for ``duration`` seconds and record every chunk.

    ``min_samples=1`` makes each pull return as soon as the first sample is
    available (this exposes the sender's chunking); pylsl's own default keeps
    accumulating until ``max_samples`` or the timeout, which measures the
    receiver's batching instead.

    :return: number of pulls that returned data.
    """
    import pylsl  # noqa: PLC0415 - keeps the module importable without pylsl

    streams = pylsl.resolve_byprop("name", name, timeout=resolve_timeout)
    if not streams:
        raise ProbeError(
            f"no LSL stream named {name!r} (is the streamer running?)"
        )

    info = streams[0]
    recorder._expected_rate = float(info.nominal_srate())  # noqa: SLF001 - same module
    inlet = pylsl.StreamInlet(info, max_buflen=4)

    pulls = 0
    deadline = time.monotonic() + duration
    try:
        while time.monotonic() < deadline:
            samples, timestamps = inlet.pull_chunk(
                timeout=pull_timeout,
                max_samples=max_samples,
                min_samples=min_samples,
            )
            if not samples:
                continue
            recorder.record(
                len(samples),
                timestamps[0] if timestamps else None,
                timestamps[-1] if timestamps else None,
            )
            pulls += 1
    finally:
        close_stream = getattr(inlet, "close_stream", None)
        if close_stream is not None:
            close_stream()
    return pulls
