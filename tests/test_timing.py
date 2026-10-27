"""Tests for the SDK fetch-timing recorder (pure Python, no device)."""

from __future__ import annotations

import ctypes
import time

import pytest

from tmsi_lsl.timing import (
    FetchTimingRecorder,
    instrument_device,
    SDK_FETCH_PAUSE_S,
)


class FakeDevice:
    """Stands in for a SagaDevice's ``get_device_data`` call."""

    def __init__(self, sets_per_call: int = 40) -> None:
        self.sets_per_call = sets_per_call
        self.calls = 0

    def get_device_data(self, buffer, size, sets_pointer, type_pointer):
        self.calls += 1
        sets_pointer.contents.value = self.sets_per_call
        time.sleep(0.002)
        return 0


def call_fetch(device) -> None:
    sets = ctypes.c_uint(0)
    device.get_device_data(None, 4096, ctypes.pointer(sets), None)


def test_recorder_accumulates_stats():
    recorder = FetchTimingRecorder()
    base = 100.0
    for index in range(11):
        start = base + index * SDK_FETCH_PAUSE_S
        recorder.record(start, start + 0.0005, 20)

    stats = recorder.stats()
    assert recorder.count == 11
    assert stats.count == 11
    assert stats.total_sets == 220
    assert stats.empty_fetches == 0
    assert stats.interval_mean == pytest.approx(SDK_FETCH_PAUSE_S, rel=1e-6)
    assert stats.interval_p50 == pytest.approx(SDK_FETCH_PAUSE_S, rel=1e-6)
    assert stats.interval_max == pytest.approx(SDK_FETCH_PAUSE_S, rel=1e-6)
    assert stats.duration_mean == pytest.approx(0.0005, rel=1e-6)
    assert stats.fetch_rate_hz == pytest.approx(100.0, rel=1e-6)
    assert "SDK data fetch timing" in recorder.summary()


def test_recorder_marks_empty_fetches():
    recorder = FetchTimingRecorder()
    recorder.record(1.0, 1.001, 0)
    recorder.record(1.01, 1.011, 8)
    assert recorder.stats().empty_fetches == 1
    assert recorder.stats().total_sets == 8


def test_recorder_writes_csv(tmp_path):
    path = tmp_path / "timing.csv"
    recorder = FetchTimingRecorder(path)
    recorder.record(10.0, 10.002, 40)
    recorder.record(10.01, 10.012, 41)
    recorder.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "index,t_start_s,duration_ms,interval_ms,num_sets"
    assert len(lines) == 3
    assert lines[1].startswith("0,10.000000,2.000,")
    assert lines[1].endswith(",40")
    assert ",10.000,41" in lines[2]
    assert recorder.summary().endswith(f"csv           : {path}")


def test_instrument_device_wraps_and_restores():
    device = FakeDevice(sets_per_call=33)
    recorder = FetchTimingRecorder()

    restore = instrument_device(device, recorder)
    for _ in range(3):
        call_fetch(device)

    assert device.calls == 3
    assert recorder.count == 3
    records = recorder.records()
    assert [record.num_sets for record in records] == [33, 33, 33]
    assert records[0].interval is None  # no previous call
    assert records[1].interval is not None and records[1].interval > 0

    restore()
    call_fetch(device)
    assert device.calls == 4  # the device keeps working …
    assert recorder.count == 3  # … but nothing is recorded any more


def test_instrumentation_records_failures_and_reraises():
    class Exploding:
        def get_device_data(self, *args, **kwargs):
            raise RuntimeError("driver error")

    device = Exploding()
    recorder = FetchTimingRecorder()
    instrument_device(device, recorder)

    with pytest.raises(RuntimeError):
        device.get_device_data(None, 1, None, None)

    # The failed call is still recorded (the wrapper uses try/finally)…
    assert recorder.count == 1
    assert recorder.records()[0].num_sets is None


def test_stats_with_no_records():
    recorder = FetchTimingRecorder()
    stats = recorder.stats()
    assert stats.count == 0
    assert stats.fetch_rate_hz == 0.0
    assert "no calls recorded" in recorder.summary()
