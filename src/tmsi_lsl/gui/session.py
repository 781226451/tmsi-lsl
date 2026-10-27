"""Worker object that owns the SDK device and the LSL outlet.

Discovery, channel configuration and measurement all block inside the TMSi
driver DLL.  To keep the UI responsive, this object lives in a :class:`QThread`
and every public slot is invoked through a queued connection.  Results travel
back to the GUI as signals carrying plain dicts.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..lsl_stream import LslStream
from ..saga_source import SagaSource

__all__ = ["DeviceSession"]

_MANDATORY_TYPES = frozenset({"STATUS", "COUNTER"})


class DeviceSession(QObject):
    """Owns one ``SagaSource`` (+ optional ``LslStream``) inside a worker thread."""

    deviceConnected = Signal(object)  # {"summary": {...}, "channels": [...]}
    deviceDisconnected = Signal()
    streamStarted = Signal(object)  # {"name", "source_id", "channel_count", "sample_rate"}
    streamStopped = Signal()
    statsUpdated = Signal(object)  # {"frames", "rate", "since_last"}
    errorOccurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: SagaSource | None = None
        self._stream: LslStream | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._emit_stats)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _describe(exc: BaseException) -> str:
        message = str(exc).strip()
        name = type(exc).__name__
        if name in {"SourceError", "StreamError", "DeviceNotFoundError", "DriverUnavailableError"}:
            return message or name
        return f"{name}: {message}" if message else name

    @staticmethod
    def _channel_payload(channel) -> dict:
        return {
            "index": int(channel.index),
            "name": str(channel.name),
            "type": str(channel.channel_type).upper(),
            "unit": str(channel.unit),
            "divider": int(channel.divider),
            "rate": None if channel.sample_rate is None else float(channel.sample_rate),
            "active": bool(channel.is_active),
            "mandatory": str(channel.channel_type).upper() in _MANDATORY_TYPES,
        }

    def _teardown(self) -> None:
        self._timer.stop()
        stream, self._stream = self._stream, None
        source, self._source = self._source, None
        if source is not None:
            try:
                if source.is_sampling:
                    source.stop()
            except Exception:  # noqa: BLE001 - closing must not raise
                pass
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        if source is not None:
            try:
                source.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ slots

    @Slot(object)
    def connect_device(self, config: dict) -> None:
        """Discover/open a device (or attach the simulator)."""
        self._teardown()
        source: SagaSource | None = None
        try:
            mode = str(config.get("mode", "hardware"))
            source = SagaSource(
                dr_interface=str(config.get("dr_interface", "docked")),
                ds_interface=str(config.get("ds_interface", "usb")),
                device_index=int(config.get("device_index", 0)),
                num_retries=int(config.get("num_retries", 2)),
            )

            if mode == "simulate":
                from ..simulator import SimulatedSaga  # noqa: PLC0415

                device = SimulatedSaga(num_uni=int(config.get("sim_channels", 32)))
                device.open()
                summary = source.attach(device)
            else:
                summary = source.connect()

            channels = source.all_channels()
            self._source = source
            self.deviceConnected.emit(
                {
                    "mode": mode,
                    "summary": {
                        "name": summary.name,
                        "serial": summary.serial_number,
                        "num_channels": summary.num_channels,
                        "num_active": summary.num_active_channels,
                        "base_rate": summary.base_sample_rate,
                        "sampling_rate": summary.sampling_frequency,
                        "dr_interface": summary.dr_interface,
                        "ds_interface": summary.ds_interface,
                    },
                    "channels": [self._channel_payload(channel) for channel in channels],
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            if source is not None:
                try:
                    source.close()
                except Exception:  # noqa: BLE001
                    pass
            self._source = None
            self.errorOccurred.emit(self._describe(exc))

    @Slot()
    def disconnect_device(self) -> None:
        self._teardown()
        self.deviceDisconnected.emit()

    @Slot(object)
    def start_stream(self, config: dict) -> None:
        """Apply the channel/sampling configuration and start streaming."""
        if self._source is None:
            self.errorOccurred.emit("尚未连接设备，无法开始推流")
            return

        source = self._source
        stream: LslStream | None = None
        try:
            indices = [int(index) for index in config.get("indices", [])]
            if not indices:
                raise RuntimeError("请先选通至少一个通道")

            source.set_active_channels(indices)

            base_rate = int(config.get("base_rate") or 0)
            divider = int(config.get("divider") or 0)
            if base_rate or divider:
                source.set_sampling(
                    base_sample_rate=base_rate or None,
                    divider=divider or None,
                )

            stream = LslStream(
                name=str(config.get("lsl_name") or "TMSi SAGA"),
                lsl_offset=float(config.get("lsl_offset", 0.0335)),
            )
            info = stream.open(source.device)
            source.start()

            self._stream = stream
            self._timer.start()

            # Report what the device actually runs at now, so the UI can refresh
            # its "device sampling rate" instead of showing the value read at
            # connect time.
            summary = source.summary()
            self.streamStarted.emit(
                {
                    "name": info.name,
                    "source_id": info.source_id,
                    "channel_count": info.channel_count,
                    "sample_rate": info.sample_rate,
                    "device_sampling_rate": float(summary.sampling_frequency),
                    "device_base_rate": int(summary.base_sample_rate),
                    "device_num_active": int(summary.num_active_channels),
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            if source is not None and source.is_sampling:
                try:
                    source.stop()
                except Exception:  # noqa: BLE001
                    pass
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            self._stream = None
            self.errorOccurred.emit(self._describe(exc))

    def _stop_current_stream(self) -> str | None:
        """Stop measuring and close the outlet; returns an error message if any."""
        self._timer.stop()
        stream, self._stream = self._stream, None
        source = self._source
        message = None
        try:
            if source is not None and source.is_sampling:
                source.stop()
        except Exception as exc:  # noqa: BLE001 - still close the outlet
            message = self._describe(exc)
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception as exc:  # noqa: BLE001
                    message = message or self._describe(exc)
        return message

    @Slot()
    def stop_stream(self) -> None:
        """Stop the measurement and destroy the outlet."""
        message = self._stop_current_stream()
        self.streamStopped.emit()
        if message:
            self.errorOccurred.emit(message)

    @Slot(object)
    def restart_stream(self, config: dict) -> None:
        """Stop, apply a new configuration and start again.

        The SAGA API only accepts configuration writes while the device is in
        its open (not sampling) state, and an LSL outlet's sample rate cannot
        change after it was created — so a rate change always means a short
        break and a new outlet.
        """
        if self._source is None:
            self.errorOccurred.emit("尚未连接设备，无法重启推流")
            return
        message = self._stop_current_stream()
        self.streamStopped.emit()
        if message:
            self.errorOccurred.emit(message)
        self.start_stream(config)

    @Slot()
    def shutdown(self) -> None:
        """Release everything before the worker thread stops."""
        self._teardown()

    # -------------------------------------------------------------- statistics

    def _emit_stats(self) -> None:
        stream = self._stream
        if stream is None:
            return
        stats = stream.stats()
        if stats is None:
            return
        since_last = None
        if stats.last_block_at is not None:
            since_last = max(0.0, time.monotonic() - stats.last_block_at)
        self.statsUpdated.emit(
            {
                "frames": int(stats.sample_sets),
                "rate": float(stats.sample_sets_per_second),
                "since_last": since_last,
            }
        )
