"""Device lifecycle and configuration for a TMSi SAGA system.

Wraps ``TMSiSDK`` discovery/open/configure/measure calls in a small object with
predictable ordering and friendlier error messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import sdk_bootstrap
from .channels import ChannelInfo
from .timing import FetchTimingRecorder, instrument_device

__all__ = [
    "SagaSource",
    "DeviceSummary",
    "SelectionReport",
    "SourceError",
    "DeviceNotFoundError",
    "DriverUnavailableError",
    "DR_INTERFACES",
    "DS_INTERFACES",
]

#: Interface between data recorder and docking station.
DR_INTERFACES = ("docked", "wifi", "optical")
#: Interface between docking station and computer (``none`` when not docked).
DS_INTERFACES = ("usb", "network", "none")


class SourceError(RuntimeError):
    """Any failure while talking to the SAGA device."""


class DeviceNotFoundError(SourceError):
    """No matching SAGA device was discovered."""


class DriverUnavailableError(SourceError):
    """The TMSi SAGA driver is missing, broken or held by another process."""


_DRIVER_HINTS = {
    "missing_dll": (
        "the TMSi SAGA driver is not installed or cannot be loaded "
        "(expected C:\\Program Files\\TMSi\\Saga\\TMSiSagaDeviceLib.dll); "
        "install the TMSi SAGA driver and retry"
    ),
    "already_in_use_dll": (
        "another application is already using the TMSi SAGA driver; "
        "close the TMSi software (or any other acquisition program) and retry"
    ),
    "api_no_driver": (
        "the TMSi SAGA driver could not be initialised; reinstall the TMSi SAGA driver"
    ),
}


@dataclass(frozen=True)
class DeviceSummary:
    """Static facts about the connected device."""

    name: str
    serial_number: int
    dr_interface: str
    ds_interface: str
    num_channels: int
    num_active_channels: int
    base_sample_rate: int
    sampling_frequency: int


@dataclass
class SelectionReport:
    """Outcome of applying a channel selection.

    ``missing`` lists channels that were requested but are not active
    afterwards, ``extra`` lists active channels that were not requested; both
    are usually caused by status/counter channels, which the firmware refuses
    to disable.
    """

    requested: list[int] = field(default_factory=list)
    active: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    extra: list[int] = field(default_factory=list)


def _import_sdk():
    """Import the SDK symbols used by this module (after path bootstrap)."""
    sdk_bootstrap.ensure_sdk_importable()
    from TMSiSDK.device.tmsi_device_enums import (  # noqa: PLC0415
        ChannelType,
        DeviceInterfaceType,
        DeviceState,
        DeviceType,
        MeasurementType,
    )
    from TMSiSDK.tmsi_errors.error import TMSiError  # noqa: PLC0415
    from TMSiSDK.tmsi_sdk import TMSiSDK  # noqa: PLC0415

    return {
        "TMSiSDK": TMSiSDK,
        "TMSiError": TMSiError,
        "ChannelType": ChannelType,
        "DeviceInterfaceType": DeviceInterfaceType,
        "DeviceState": DeviceState,
        "DeviceType": DeviceType,
        "MeasurementType": MeasurementType,
    }


def _tmsi_error_code(exc: Exception) -> str:
    code = getattr(exc, "error_code", None)
    return getattr(code, "name", str(code))


class SagaSource:
    """A single SAGA device, from discovery to measurement.

    Typical use::

        src = SagaSource()
        src.connect()
        src.set_active_channels(indices)
        src.start()
        ...
        src.stop()
        src.close()
    """

    def __init__(
        self,
        dr_interface: str = "docked",
        ds_interface: str = "usb",
        num_retries: int = 3,
        device_index: int = 0,
    ) -> None:
        if dr_interface not in DR_INTERFACES:
            raise ValueError(
                f"dr_interface must be one of {DR_INTERFACES}, got {dr_interface!r}"
            )
        if ds_interface not in DS_INTERFACES:
            raise ValueError(
                f"ds_interface must be one of {DS_INTERFACES}, got {ds_interface!r}"
            )

        self._dr_interface = dr_interface
        self._ds_interface = ds_interface
        self._num_retries = num_retries
        self._device_index = device_index

        self._sdk = _import_sdk()
        self._device = None
        self._summary: DeviceSummary | None = None
        self._sampling = False
        self._discovered = 0
        self._fetch_recorder: FetchTimingRecorder | None = None

    # ------------------------------------------------------------------ state

    @property
    def device(self):
        """The underlying ``SagaDevice`` (``None`` before :meth:`connect`)."""
        return self._device

    @property
    def is_sampling(self) -> bool:
        return self._sampling

    @property
    def discovered_devices(self) -> int:
        return self._discovered

    # ---------------------------------------------------------------- connect

    def discover(self) -> list:
        """Run device discovery and return the matching device objects."""
        sdk = self._sdk
        dr_interface = sdk["DeviceInterfaceType"][self._dr_interface]
        ds_interface = sdk["DeviceInterfaceType"][self._ds_interface]

        try:
            sdk["TMSiSDK"]().discover(
                dev_type=sdk["DeviceType"].saga,
                dr_interface=dr_interface,
                ds_interface=ds_interface,
                num_retries=self._num_retries,
            )
        except sdk["TMSiError"] as exc:
            code = _tmsi_error_code(exc)
            hint = _DRIVER_HINTS.get(code)
            if hint is not None:
                raise DriverUnavailableError(hint) from exc
            raise SourceError(f"SAGA discovery failed ({code}): {exc}") from exc

        devices = [
            dev
            for dev in sdk["TMSiSDK"]().get_device_list(sdk["DeviceType"].saga)
            if dev.get_dr_interface() == dr_interface
        ]
        self._discovered = len(devices)
        return devices

    def connect(self) -> DeviceSummary:
        """Discover and open a SAGA device."""
        if self._device is not None:
            raise RuntimeError("already connected")

        devices = self.discover()
        if not devices:
            raise DeviceNotFoundError(
                f"no SAGA device found on the '{self._dr_interface}' data-recorder "
                f"interface (docking station: '{self._ds_interface}'). "
                "Check the USB cable, the TMSi driver and whether another "
                "application already holds the device."
            )
        if self._device_index >= len(devices):
            raise DeviceNotFoundError(
                f"device index {self._device_index} requested but only "
                f"{len(devices)} device(s) found"
            )

        device = devices[self._device_index]
        try:
            device.open()
        except self._sdk["TMSiError"] as exc:
            raise SourceError(
                f"could not open the SAGA device ({_tmsi_error_code(exc)}): {exc}"
            ) from exc

        self._device = device
        self._summary = self._read_summary()
        return self._summary

    def close(self) -> None:
        """Stop a running measurement (if any) and close the device."""
        if self._device is None:
            return
        try:
            if self._sampling:
                self.stop()
        finally:
            try:
                if self._device.get_device_state() != self._sdk["DeviceState"].disconnected:
                    self._device.close()
            except self._sdk["TMSiError"] as exc:
                raise SourceError(
                    f"could not close the SAGA device ({_tmsi_error_code(exc)}): {exc}"
                ) from exc
            finally:
                self._device = None

    def attach(self, device) -> DeviceSummary:
        """Take ownership of an already open device object.

        Intended for the simulator and for tests; the object must implement the
        ``SagaDevice`` methods used by this class.
        """
        if self._device is not None:
            raise RuntimeError("a device is already attached")
        self._device = device
        self._summary = self._read_summary()
        return self._summary

    # ------------------------------------------------------------- inspection

    def _require_device(self):
        if self._device is None:
            raise SourceError("device is not connected; call connect() first")
        return self._device

    def _base_sample_rate(self) -> int:
        device = self._require_device()
        try:
            info = device.get_device_sampling_frequency(detailed=True)
        except self._sdk["TMSiError"] as exc:
            raise SourceError(f"could not read the sample rate: {exc}") from exc
        return int(info["base_sampling_rate"])

    def _channel_infos(self, channels: Sequence, base_sample_rate: int) -> list[ChannelInfo]:
        infos: list[ChannelInfo] = []
        for index, channel in enumerate(channels):
            divider = int(channel.get_channel_divider())
            channel_type = channel.get_channel_type()
            type_name = getattr(channel_type, "name", str(channel_type))
            infos.append(
                ChannelInfo(
                    index=index,
                    name=str(channel.get_channel_name()).strip(),
                    channel_type=str(type_name).upper(),
                    unit=str(channel.get_channel_unit_name()).strip(),
                    divider=divider,
                    sample_rate=(base_sample_rate / (2 ** divider)) if divider >= 0 else None,
                    is_active=divider != -1,
                )
            )
        return infos

    def all_channels(self) -> list[ChannelInfo]:
        """Every channel of the device, in device order (0-based indices)."""
        device = self._require_device()
        try:
            channels = device.get_device_channels()
        except self._sdk["TMSiError"] as exc:
            raise SourceError(f"could not read the channel list: {exc}") from exc
        return self._channel_infos(channels, self._base_sample_rate())

    def active_channels(self) -> list[ChannelInfo]:
        """Channels the device is currently sampling."""
        return [ch for ch in self.all_channels() if ch.is_active]

    def summary(self) -> DeviceSummary:
        if self._summary is None:
            raise SourceError("device is not connected; call connect() first")
        return self._summary

    def _read_summary(self) -> DeviceSummary:
        device = self._require_device()
        try:
            base_rate = self._base_sample_rate()
            return DeviceSummary(
                name=str(device.get_device_name()),
                serial_number=int(device.get_device_serial_number()),
                dr_interface=self._dr_interface,
                ds_interface=self._ds_interface,
                num_channels=int(device.get_num_channels()),
                num_active_channels=int(device.get_num_active_channels()),
                base_sample_rate=base_rate,
                sampling_frequency=int(device.get_device_sampling_frequency()),
            )
        except self._sdk["TMSiError"] as exc:
            raise SourceError(f"could not read the device configuration: {exc}") from exc

    # ------------------------------------------------------------ configuration

    def set_active_channels(self, indices: Sequence[int]) -> SelectionReport:
        """Make exactly ``indices`` (0-based) the active channel set.

        Channels the firmware refuses to disable (status/counter) stay active;
        the returned report records that.
        """
        device = self._require_device()
        if self._sampling:
            raise SourceError("cannot change channels while sampling")

        channels = device.get_device_channels()
        total = len(channels)
        requested = sorted({int(index) for index in indices})
        invalid = [index for index in requested if not 0 <= index < total]
        if invalid:
            raise SourceError(
                f"channel index out of range: {invalid} (device has {total} channels, 0-based)"
            )
        if not requested:
            raise SourceError("selection would disable every channel")

        requested_set = set(requested)
        to_enable = [index for index in requested if channels[index].get_channel_divider() == -1]
        to_disable = [
            index
            for index in range(total)
            if index not in requested_set and channels[index].get_channel_divider() != -1
        ]

        try:
            if to_enable:
                device.set_device_active_channels(to_enable, True)
            if to_disable:
                device.set_device_active_channels(to_disable, False)
        except self._sdk["TMSiError"] as exc:
            raise SourceError(
                f"could not apply the channel selection ({_tmsi_error_code(exc)}): {exc}"
            ) from exc

        active = [index for index, channel in enumerate(device.get_device_channels())
                  if channel.get_channel_divider() != -1]
        active_set = set(active)
        self._summary = self._read_summary()
        return SelectionReport(
            requested=requested,
            active=active,
            missing=[index for index in requested if index not in active_set],
            extra=[index for index in active if index not in requested_set],
        )

    def set_sampling(
        self,
        base_sample_rate: int | None = None,
        divider: int | None = None,
    ) -> None:
        """Configure the base sample rate and/or the decimation divider.

        :param base_sample_rate: ``4000`` (decimal) or ``4096`` (binary) Hz.
        :param divider: ``1``, ``2``, ``4`` or ``8`` (sample rate = base / divider).
        """
        device = self._require_device()
        if self._sampling:
            raise SourceError("cannot change the sampling configuration while sampling")

        if divider is None and base_sample_rate is None:
            return

        try:
            if divider is not None:
                device.set_device_sampling_config(
                    channel_type=self._sdk["ChannelType"].all_types,
                    channel_divider=int(divider),
                )
            if base_sample_rate is not None:
                from TMSiSDK.device.devices.saga.saga_API_enums import (  # noqa: PLC0415
                    SagaBaseSampleRate,
                )

                device.set_device_sampling_config(
                    base_sample_rate=SagaBaseSampleRate(int(base_sample_rate))
                )
        except ValueError as exc:
            raise SourceError(
                f"invalid sampling configuration (base rate {base_sample_rate}, "
                f"divider {divider}): {exc}"
            ) from exc
        except self._sdk["TMSiError"] as exc:
            raise SourceError(
                f"could not change the sampling configuration "
                f"({_tmsi_error_code(exc)}): {exc}"
            ) from exc

        self._summary = self._read_summary()

    # -------------------------------------------------------------- measurement

    def start(self) -> None:
        """Start signal acquisition (samples are pushed to the data server)."""
        device = self._require_device()
        if self._sampling:
            raise SourceError("already sampling")
        if device.get_num_active_channels() == 0:
            raise SourceError("no active channels: select at least one channel")

        try:
            device.start_measurement(self._sdk["MeasurementType"].SAGA_SIGNAL)
        except self._sdk["TMSiError"] as exc:
            raise SourceError(
                f"could not start the measurement ({_tmsi_error_code(exc)}): {exc}"
            ) from exc
        self._sampling = True

    def enable_fetch_timing(
        self,
        path: str | Path | None = None,
        keep: int = 200_000,
    ) -> FetchTimingRecorder:
        """Record the timing of every ``get_device_data`` call made by the SDK.

        Must be called after :meth:`connect` and before :meth:`start`; the
        vendored SDK is not modified (only the device instance's attribute is
        wrapped).  :paramref:`path` receives a CSV row per call.
        """
        device = self._require_device()
        if self._sampling:
            raise SourceError("enable fetch timing before start()")
        if not hasattr(device, "get_device_data"):
            raise SourceError(
                "this device object has no get_device_data(); the simulator does "
                "not fetch through the SDK either, so there is nothing to record"
            )
        recorder = FetchTimingRecorder(path, keep)
        instrument_device(device, recorder)
        self._fetch_recorder = recorder
        return recorder

    @property
    def fetch_recorder(self) -> FetchTimingRecorder | None:
        """The recorder created by :meth:`enable_fetch_timing`, if any."""
        return self._fetch_recorder

    def stop(self) -> None:
        """Stop signal acquisition."""
        device = self._require_device()
        if not self._sampling:
            return
        try:
            device.stop_measurement()
        except self._sdk["TMSiError"] as exc:
            raise SourceError(
                f"could not stop the measurement ({_tmsi_error_code(exc)}): {exc}"
            ) from exc
        finally:
            self._sampling = False
