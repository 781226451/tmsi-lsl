"""Tests for the consumer-side LSL chunk recorder (no stream needed)."""

from __future__ import annotations

import pytest

from tmsi_lsl.lsl_probe import LslChunkRecorder


def record_chunks(recorder: LslChunkRecorder, sizes: list[int], rate: float = 100.0) -> None:
    """Feed synthetic chunk sizes with perfectly contiguous timestamps."""
    ts = 1000.0
    for size in sizes:
        recorder.record(size, ts, ts + (size - 1) / rate)
        ts += size / rate


def test_stats_over_uniform_chunks():
    recorder = LslChunkRecorder(expected_rate=100.0)
    record_chunks(recorder, [10] * 20)

    stats = recorder.stats()
    assert stats.pulls == 20
    assert stats.samples == 200
    assert stats.samples_min == 10
    assert stats.samples_max == 10
    assert stats.samples_mean == pytest.approx(10.0)
    assert stats.ts_step_mean == pytest.approx(0.1, rel=1e-6)
    assert stats.max_ts_error == pytest.approx(0.0, abs=1e-9)
    assert "LSL receive timing" in recorder.summary()


def test_stats_detect_dropped_samples():
    recorder = LslChunkRecorder(expected_rate=100.0)
    # Chunk sizes 10 then 30: the timestamp step jumps by 20 samples worth.
    record_chunks(recorder, [10, 30, 10])

    stats = recorder.stats()
    assert stats.samples_mean == pytest.approx(50 / 3, rel=1e-6)
    # 30 samples were reported but only 10 slots of time elapsed before the next
    # chunk -> the continuity error exposes the jump.
    assert stats.max_ts_error > 0.0


def test_stats_handle_missing_timestamps():
    recorder = LslChunkRecorder()
    recorder.record(5)
    recorder.record(5)
    stats = recorder.stats()
    assert stats.pulls == 2
    assert stats.ts_step_mean == 0.0
    assert stats.samples_max == 5


def test_csv_columns_and_values(tmp_path):
    path = tmp_path / "chunks.csv"
    recorder = LslChunkRecorder(path, expected_rate=100.0)
    recorder.record(10, 1.0, 1.09)
    recorder.record(10, 1.1, 1.19)
    recorder.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "index,pulled_at_s,gap_ms,samples,first_ts,last_ts,span_ms"
    assert len(lines) == 3
    fields = lines[1].split(",")
    assert fields[0] == "0"
    assert fields[3] == "10"
    assert fields[4] == "1.000000"
    assert fields[5] == "1.090000"
    assert float(fields[6]) == pytest.approx(90.0, abs=0.01)
    # Second row carries the gap since the first pull.
    assert lines[2].split(",")[2] != ""
    assert recorder.summary().endswith(f"csv           : {path}")


def test_empty_recorder_summary():
    recorder = LslChunkRecorder()
    assert recorder.count == 0
    assert "no chunks received" in recorder.summary()
