"""Tests against a real TMSi SAGA device.

Skipped unless ``TMSI_LSL_HARDWARE=1`` is set, because they need the TMSi
driver and a connected amplifier:

    $env:TMSI_LSL_HARDWARE = "1"
    uv run pytest -m hardware -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("TMSI_LSL_HARDWARE") != "1",
        reason="set TMSI_LSL_HARDWARE=1 to run tests against real hardware",
    ),
]


def test_connect_and_read_channels():
    from tmsi_lsl.saga_source import SagaSource

    source = SagaSource()
    try:
        summary = source.connect()
        assert summary.num_channels > 0
        assert summary.base_sample_rate in (4000, 4096)

        channels = source.all_channels()
        assert len(channels) == summary.num_channels
        assert any(channel.is_active for channel in channels)
    finally:
        source.close()


def test_short_measurement_delivers_samples():
    """Stream with the *current* device configuration (no config writes)."""
    from tmsi_lsl.lsl_stream import LslStream
    from tmsi_lsl.saga_source import SagaSource

    source = SagaSource()
    stream = LslStream("tmsi-lsl-hardware-test")
    try:
        source.connect()
        assert source.active_channels(), "device reports no active channels"
        info = stream.open(source.device)
        assert info.channel_count >= 1
        source.start()
        import time

        time.sleep(2.0)
        source.stop()

        stats = stream.stats()
        assert stats is not None
        assert stats.sample_sets > 0
        assert stats.sample_sets_per_second > 0
    finally:
        stream.close()
        source.close()
