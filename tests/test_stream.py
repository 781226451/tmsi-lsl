"""End-to-end test: simulated SAGA -> SDK data server -> LSL outlet -> inlet.

Runs entirely in-process and needs no hardware.  If the environment cannot
resolve LSL streams (no multicast on the loopback interface) the test is
skipped instead of failing.
"""

from __future__ import annotations

import time
import uuid

import pytest

pylsl = pytest.importorskip("pylsl")

from tmsi_lsl.channels import parse_spec, select_indices  # noqa: E402
from tmsi_lsl.lsl_stream import LslStream  # noqa: E402
from tmsi_lsl.saga_source import SagaSource  # noqa: E402
from tmsi_lsl.simulator import SimulatedSaga  # noqa: E402


def test_simulated_pipeline_reaches_an_lsl_inlet():
    stream_name = f"tmsi-lsl-test-{uuid.uuid4().hex[:8]}"

    source = SagaSource()
    device = SimulatedSaga(num_uni=8)
    device.open()
    source.attach(device)

    stream = None
    try:
        # Indices: 0 = CREF, 1..8 = UNI 01..08, 9 = TRIGGER (status, cannot be disabled)
        channels = source.all_channels()
        assert len(channels) == 10
        report = source.set_active_channels(select_indices(channels, parse_spec("2-4")))
        assert report.active == [1, 2, 3, 9]
        assert report.missing == []
        assert 9 in report.extra  # the status channel stays active

        stream = LslStream(stream_name)
        info = stream.open(device)
        assert info.channel_count == 4
        assert info.sample_rate == 4000.0
        assert info.channel_labels == ("UNI 01", "UNI 02", "UNI 03", "TRIGGER")

        source.start()

        resolved = pylsl.resolve_byprop("name", stream_name, timeout=5.0)
        if not resolved:
            pytest.skip("LSL stream discovery is unavailable in this environment")

        inlet = pylsl.StreamInlet(resolved[0], max_buflen=20)
        try:
            rows: list[list[float]] = []
            deadline = time.monotonic() + 5.0
            while len(rows) < 200 and time.monotonic() < deadline:
                samples, _ = inlet.pull_chunk(timeout=0.5, max_samples=200)
                rows.extend(samples)
        finally:
            inlet.close_stream()

        assert len(rows) >= 1, "no samples received from the LSL outlet"
        assert len(rows[0]) == 4
        assert any(abs(value) > 0.0 for row in rows for value in row)

        stats = stream.stats()
        assert stats is not None
        assert stats.sample_sets > 0
        assert stats.samples == stats.sample_sets * 4
    finally:
        source.stop()
        if stream is not None:
            stream.close()
        source.close()


def test_open_close_is_repeatable():
    source = SagaSource()
    device = SimulatedSaga(num_uni=2)
    device.open()
    source.attach(device)

    stream = LslStream(f"tmsi-lsl-reopen-{uuid.uuid4().hex[:8]}")
    try:
        info = stream.open(device)
        stream.close()
        stream.close()  # closing twice must be harmless
        assert stream.info is None or stream.stats() is None
    finally:
        source.close()
    assert info.channel_count == 4  # CREF + 2 UNI + TRIGGER


def test_status_channels_cannot_be_disabled():
    source = SagaSource()
    device = SimulatedSaga(num_uni=4)
    device.open()
    source.attach(device)
    try:
        channels = source.all_channels()
        report = source.set_active_channels(select_indices(channels, parse_spec("1")))
        assert report.requested == [0]
        assert 5 in report.active  # TRIGGER
        assert [ch.name for ch in source.active_channels()] == ["CREF", "TRIGGER"]
    finally:
        source.close()
