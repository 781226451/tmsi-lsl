"""A software stand-in for a SAGA device, for demos and automated tests.

``SimulatedSaga`` implements the subset of the ``SagaDevice`` API that this
application uses, and injects synthetic sample blocks into the SDK's
``SampleDataServer`` exactly like a real measurement does.  That means the
whole pipeline -- channel configuration, LSL outlet, throughput counters --
runs unchanged without a TMSi driver or hardware.
"""

from __future__ import annotations

import math
import threading
import time

from . import sdk_bootstrap

__all__ = ["SimulatedSaga", "SimulatedChannel"]


class SimulatedChannel:
    """Minimal stand-in for ``TMSiSDK`` channel objects."""

    def __init__(self, index: int, name: str, channel_type, unit: str = "uV", divider: int = 0):
        self._index = index
        self._name = name
        self._type = channel_type
        self._unit = unit
        self._divider = divider

    # -- API used by the application and by the SDK LSL writer --------------
    def get_channel_name(self) -> str:
        return self._name

    def get_channel_unit_name(self) -> str:
        return self._unit

    def get_channel_type(self):
        return self._type

    def get_channel_divider(self) -> int:
        return self._divider

    def get_channel_bandwidth(self) -> int:
        return 0

    def get_channel_exp(self) -> int:
        return 0

    def get_channel_format(self) -> int:
        return 0

    def get_sensor_information(self):
        return None

    # -- internal -----------------------------------------------------------
    def _set_divider(self, divider: int) -> None:
        self._divider = divider

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SimulatedChannel({self._index}, {self._name!r})"


class _Named:
    """Minimal stand-in for the SDK's ``RefMethod`` / ``AutoRefMethod`` enums."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name}>"


class SimulatedSaga:
    """Fake device producing sine waves and a square-wave trigger channel."""

    def __init__(
        self,
        num_uni: int = 32,
        base_sample_rate: int = 4000,
        block_seconds: float = 0.01,
    ) -> None:
        sdk_bootstrap.ensure_sdk_importable()
        from TMSiSDK.device.tmsi_device_enums import ChannelType, DeviceState  # noqa: PLC0415

        self._ChannelType = ChannelType
        self._DeviceState = DeviceState
        self._base_sample_rate = int(base_sample_rate)
        self._sampling_frequency = self._base_sample_rate
        self._block_seconds = float(block_seconds)
        self._started_at = 0.0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state = DeviceState.disconnected
        self._reference = "Common"
        self._auto_reference = "Fixed"

        self._channels: list[SimulatedChannel] = [
            SimulatedChannel(0, "CREF", ChannelType.UNI, "uV", divider=0)
        ]
        for number in range(1, num_uni + 1):
            self._channels.append(
                SimulatedChannel(number, f"UNI {number:02d}", ChannelType.UNI, "uV", divider=0)
            )
        # Status channels cannot be disabled on real hardware; mimic that.
        self._channels.append(
            SimulatedChannel(len(self._channels), "TRIGGER", ChannelType.status, "-", divider=0)
        )

    # ------------------------------------------------------------- device API
    def open(self) -> None:
        self._state = self._DeviceState.connected

    def close(self) -> None:
        if self._thread is not None:
            self.stop_measurement()
        self._state = self._DeviceState.disconnected

    def get_id(self) -> int:
        return id(self)

    def get_device_name(self) -> str:
        return "SAGA (simulated)"

    def get_device_type(self) -> str:
        return "SAGA"

    def get_device_serial_number(self) -> int:
        return 0

    def get_device_state(self):
        return self._state

    def get_num_channels(self) -> int:
        return len(self._channels)

    def get_num_active_channels(self) -> int:
        return sum(1 for ch in self._channels if ch.get_channel_divider() != -1)

    def get_device_channels(self) -> list[SimulatedChannel]:
        return list(self._channels)

    def get_device_active_channels(self) -> list[SimulatedChannel]:
        return [ch for ch in self._channels if ch.get_channel_divider() != -1]

    def get_device_sampling_frequency(self, detailed: bool = False):
        if not detailed:
            return self._sampling_frequency
        return {
            "device_sampling_rate": self._sampling_frequency,
            "base_sampling_rate": self._base_sample_rate,
        }

    def get_device_references(self) -> dict:
        return {
            "reference": _Named(self._reference),
            "autoreference": _Named(self._auto_reference),
        }

    def set_device_references(self, reference_method=None, auto_reference_method=None) -> None:
        if reference_method is not None:
            self._reference = self._reference_name(reference_method)
        if auto_reference_method is not None:
            self._auto_reference = self._reference_name(auto_reference_method)

    @staticmethod
    def _reference_name(value) -> str:
        name = getattr(value, "name", None)
        return str(name if name is not None else value).strip().capitalize()

    def set_device_active_channels(self, indices, activate: bool) -> None:
        for index in indices:
            channel = self._channels[index]
            # The firmware ignores requests to disable status channels.
            if not activate and channel.get_channel_type() == self._ChannelType.status:
                continue
            channel._set_divider(0 if activate else -1)
        self._update_sampling_frequency()

    def set_device_sampling_config(self, base_sample_rate=None, channel_type=None, channel_divider=1):
        if base_sample_rate is not None:
            value = getattr(base_sample_rate, "value", base_sample_rate)
            self._base_sample_rate = int(value)
        if channel_type is not None:
            divider = {1: 0, 2: 1, 4: 2, 8: 3}.get(int(channel_divider), 0)
            for channel in self._channels:
                if channel.get_channel_divider() != -1:
                    channel._set_divider(divider)
        self._update_sampling_frequency()

    def _update_sampling_frequency(self) -> None:
        enabled = [ch for ch in self._channels if ch.get_channel_divider() != -1]
        divider = enabled[-1].get_channel_divider() if enabled else 0
        self._sampling_frequency = int(self._base_sample_rate / (2 ** max(divider, 0)))

    # -------------------------------------------------------- measurement API
    def start_measurement(self, measurement_type=None, thread_refresh=None) -> None:
        if self._state == self._DeviceState.sampling:
            raise RuntimeError("already sampling")
        active = self.get_device_active_channels()
        if not active:
            raise RuntimeError("no active channels")
        self._state = self._DeviceState.sampling
        self._started_at = time.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._produce,
            name="SimulatedSaga",
            args=([ch.get_channel_name() for ch in active],),
            daemon=True,
        )
        self._thread.start()

    def stop_measurement(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._state = self._DeviceState.connected

    # ------------------------------------------------------------- data source
    def _produce(self, labels: list[str]) -> None:
        """Push sample blocks at the configured rate until stopped.

        Pacing is derived from the wall clock rather than accumulated sleeps,
        so the emitted sample count stays correct even though Windows' default
        timer resolution is coarse.
        """
        from TMSiSDK.sample_data_server.sample_data import SampleData  # noqa: PLC0415
        from TMSiSDK.sample_data_server.sample_data_server import SampleDataServer  # noqa: PLC0415

        channels = len(labels)
        rate = max(float(self._sampling_frequency), 1.0)
        block = max(1, int(round(rate * self._block_seconds)))
        amplitudes = [10.0 + 5.0 * (i % 4) for i in range(channels)]
        frequencies = [8.0 + 2.0 * i for i in range(channels)]
        status_mask = [
            label.upper().startswith(("TRIGGER", "STATUS", "COUNTER")) for label in labels
        ]

        server = SampleDataServer()
        two_pi = 2.0 * math.pi
        started = time.monotonic()
        produced = 0

        while not self._stop_event.is_set():
            due = int((time.monotonic() - started) * rate)
            while produced < due:
                count = min(block, due - produced)
                samples: list[float] = []
                for _ in range(count):
                    timestamp = produced / rate
                    for channel in range(channels):
                        if status_mask[channel]:
                            samples.append(1.0 if (produced // block) % 2 else 0.0)
                        else:
                            samples.append(
                                amplitudes[channel]
                                * math.sin(two_pi * frequencies[channel] * timestamp)
                            )
                    produced += 1
                server.put_sample_data(
                    self.get_id(),
                    SampleData(count, channels, samples),
                )
            self._stop_event.wait(0.002)
