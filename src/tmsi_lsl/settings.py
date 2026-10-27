"""User settings, persisted as TOML.

The file is meant to be human editable: it is written with comments and a fixed
layout, and loading tolerates unknown keys (forward compatibility) while still
rejecting values that cannot be used.

Example::

    schema_version = 1

    [device]
    dr_interface = "docked"
    ds_interface = "usb"
    device_index = 0

    [channels]
    names = ["UNI 01", "UNI 02"]
    indices = [1, 2]

    [sampling]
    base_rate = 4000
    divider = 1

    [lsl]
    name = "TMSi SAGA"
    offset = 0.0335
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = [
    "AppSettings",
    "ChannelSettings",
    "DeviceSettings",
    "LslSettings",
    "SamplingSettings",
    "SettingsError",
    "BASE_RATES",
    "DIVIDERS",
    "DR_INTERFACES",
    "DS_INTERFACES",
    "SCHEMA_VERSION",
    "DEFAULT_SETTINGS_FILENAME",
    "default_settings_path",
]

SCHEMA_VERSION = 1
DEFAULT_SETTINGS_FILENAME = "settings.toml"

DR_INTERFACES = ("docked", "wifi", "optical")
DS_INTERFACES = ("usb", "network", "none")
BASE_RATES = (4000, 4096)
DIVIDERS = (1, 2, 4, 8)


class SettingsError(ValueError):
    """The settings file exists but cannot be used."""


# --------------------------------------------------------------------- helpers


def _toml_string(value: str) -> str:
    """Encode ``value`` as a TOML basic string (JSON escapes are compatible)."""
    return json.dumps(str(value), ensure_ascii=False)


def default_settings_path(directory: Path | None = None) -> Path:
    """``<cwd>/settings.toml`` unless an explicit path is used."""
    return Path(directory or Path.cwd()) / DEFAULT_SETTINGS_FILENAME


def _expect_table(data: dict, key: str, source: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise SettingsError(f"{source}: [{key}] must be a table, got {type(value).__name__}")
    return value


def _expect_str(data: dict, key: str, default: str, source: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise SettingsError(
            f"{source}: {key} must be a string, got {type(value).__name__}"
        )
    return value


def _expect_int(data: dict, key: str, default: int, source: str) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"{source}: {key} must be an integer, got {value!r}")
    return value


def _expect_float(data: dict, key: str, default: float, source: str) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{source}: {key} must be a number, got {value!r}")
    return float(value)


def _expect_str_list(data: dict, key: str, source: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SettingsError(f"{source}: {key} must be an array of strings")
    return tuple(item.strip() for item in value if item.strip())


def _expect_int_list(data: dict, key: str, source: str) -> tuple[int, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise SettingsError(f"{source}: {key} must be an array of integers")
    return tuple(int(item) for item in value)


def _expect_choice(value: str, allowed: Sequence[str], what: str, source: str) -> str:
    if value not in allowed:
        raise SettingsError(
            f"{source}: {what} must be one of {', '.join(allowed)}; got {value!r}"
        )
    return value


# ------------------------------------------------------------------ dataclasses


@dataclass(frozen=True)
class DeviceSettings:
    """How to reach the amplifier."""

    dr_interface: str = "docked"
    ds_interface: str = "usb"
    device_index: int = 0


@dataclass(frozen=True)
class ChannelSettings:
    """Which channels are gated in.

    Channel *names* are the durable identity (they survive a firmware update
    that reorders channels); ``indices`` are kept as a fallback for channels
    that were renamed afterwards.
    """

    names: tuple[str, ...] = ()
    indices: tuple[int, ...] = ()

    def is_empty(self) -> bool:
        return not self.names and not self.indices


@dataclass(frozen=True)
class SamplingSettings:
    """Base rate and decimation divider."""

    base_rate: int = 4000
    divider: int = 1

    @property
    def effective_rate(self) -> int:
        """Per-channel sample rate in Hz."""
        return self.base_rate // self.divider


@dataclass(frozen=True)
class LslSettings:
    """Outgoing LSL stream properties."""

    name: str = "TMSi SAGA"
    offset: float = 0.0335


@dataclass(frozen=True)
class AppSettings:
    """Everything the GUI persists."""

    schema_version: int = SCHEMA_VERSION
    device: DeviceSettings = field(default_factory=DeviceSettings)
    channels: ChannelSettings = field(default_factory=ChannelSettings)
    sampling: SamplingSettings = field(default_factory=SamplingSettings)
    lsl: LslSettings = field(default_factory=LslSettings)

    # ------------------------------------------------------------------ render

    def to_toml(self) -> str:
        """Render the settings as commented TOML."""
        lines = [
            "# tmsi-lsl settings",
            "# Written by the tmsi-lsl GUI; safe to edit by hand.",
            "",
            f"schema_version = {int(self.schema_version)}",
            "",
            "[device]",
            '# Data-recorder interface: "docked", "wifi" or "optical".',
            f"dr_interface = {_toml_string(self.device.dr_interface)}",
            '# Docking-station interface: "usb", "network" or "none".',
            f"ds_interface = {_toml_string(self.device.ds_interface)}",
            "# 0 = first discovered device.",
            f"device_index = {int(self.device.device_index)}",
            "",
            "[channels]",
            "# Channel names that are gated in. Empty = keep the device setting.",
            "names = [" + ", ".join(_toml_string(n) for n in self.channels.names) + "]",
            "# 0-based indices, used as a fallback when a name no longer exists.",
            "indices = [" + ", ".join(str(int(i)) for i in self.channels.indices) + "]",
            "",
            "[sampling]",
            f"# Base sample rate in Hz: {BASE_RATES[0]} or {BASE_RATES[1]}.",
            f"base_rate = {int(self.sampling.base_rate)}",
            f"# Decimation divider: {' / '.join(str(d) for d in DIVIDERS)}.",
            f"divider = {int(self.sampling.divider)}",
            "",
            "[lsl]",
            "# Name of the published LSL stream.",
            f"name = {_toml_string(self.lsl.name)}",
            "# Latency offset advertised in the stream metadata (seconds).",
            f"offset = {float(self.lsl.offset):g}",
            "",
        ]
        return "\n".join(lines)

    # -------------------------------------------------------------------- load

    @classmethod
    def from_toml(cls, text: str, source: str = "<string>") -> "AppSettings":
        """Parse and validate TOML text."""
        try:
            data = tomllib.loads(text)
        except Exception as exc:  # tomllib.TOMLDecodeError
            raise SettingsError(f"{source}: not valid TOML ({exc})") from exc

        if not isinstance(data, dict):
            raise SettingsError(f"{source}: expected a table at the top level")

        version = data.get("schema_version", SCHEMA_VERSION)
        if isinstance(version, bool) or not isinstance(version, int):
            raise SettingsError(f"{source}: schema_version must be an integer")
        if version > SCHEMA_VERSION:
            raise SettingsError(
                f"{source}: schema_version {version} is newer than this program "
                f"supports ({SCHEMA_VERSION})"
            )

        device_data = _expect_table(data, "device", source)
        channel_data = _expect_table(data, "channels", source)
        sampling_data = _expect_table(data, "sampling", source)
        lsl_data = _expect_table(data, "lsl", source)

        dr_interface = _expect_choice(
            _expect_str(device_data, "dr_interface", "docked", source),
            DR_INTERFACES,
            "dr_interface",
            source,
        )
        ds_interface = _expect_choice(
            _expect_str(device_data, "ds_interface", "usb", source),
            DS_INTERFACES,
            "ds_interface",
            source,
        )
        device_index = _expect_int(device_data, "device_index", 0, source)
        if device_index < 0:
            raise SettingsError(f"{source}: device_index must be >= 0")

        base_rate = _expect_int(sampling_data, "base_rate", 4000, source)
        if base_rate not in BASE_RATES:
            raise SettingsError(
                f"{source}: base_rate must be one of {BASE_RATES}, got {base_rate}"
            )
        divider = _expect_int(sampling_data, "divider", 1, source)
        if divider not in DIVIDERS:
            raise SettingsError(
                f"{source}: divider must be one of {DIVIDERS}, got {divider}"
            )

        lsl_name = _expect_str(lsl_data, "name", "TMSi SAGA", source).strip()
        if not lsl_name:
            raise SettingsError(f"{source}: lsl.name must not be empty")
        offset = _expect_float(lsl_data, "offset", 0.0335, source)
        if offset < 0.0 or offset > 5.0:
            raise SettingsError(f"{source}: lsl.offset must be between 0 and 5 seconds")

        indices = _expect_int_list(channel_data, "indices", source)
        if any(index < 0 for index in indices):
            raise SettingsError(f"{source}: channels.indices must be >= 0")

        return cls(
            schema_version=version,
            device=DeviceSettings(
                dr_interface=dr_interface,
                ds_interface=ds_interface,
                device_index=device_index,
            ),
            channels=ChannelSettings(
                names=_expect_str_list(channel_data, "names", source),
                indices=indices,
            ),
            sampling=SamplingSettings(base_rate=base_rate, divider=divider),
            lsl=LslSettings(name=lsl_name, offset=offset),
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "AppSettings":
        """Read settings from ``path``.

        :raises SettingsError: unreadable file or invalid content.
        """
        settings_path = Path(path)
        try:
            text = settings_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SettingsError(f"cannot read {settings_path}: {exc}") from exc
        return cls.from_toml(text, source=str(settings_path))

    # -------------------------------------------------------------------- save

    def save(self, path: str | os.PathLike[str]) -> Path:
        """Write the settings atomically and return the path written."""
        settings_path = Path(path)
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SettingsError(f"cannot create {settings_path.parent}: {exc}") from exc

        text = self.to_toml()
        try:
            fd, temporary = tempfile.mkstemp(
                dir=str(settings_path.parent), prefix=".settings-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                os.replace(temporary, settings_path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
                raise
        except OSError as exc:
            raise SettingsError(f"cannot write {settings_path}: {exc}") from exc
        return settings_path

    # ------------------------------------------------------------------ update

    def with_device(self, **kwargs: Any) -> "AppSettings":
        return replace(self, device=replace(self.device, **kwargs))

    def with_channels(self, names: Sequence[str], indices: Sequence[int]) -> "AppSettings":
        return replace(
            self,
            channels=ChannelSettings(tuple(names), tuple(indices)),
        )

    def with_sampling(self, base_rate: int, divider: int) -> "AppSettings":
        return replace(self, sampling=SamplingSettings(base_rate, divider))

    def with_lsl(self, **kwargs: Any) -> "AppSettings":
        return replace(self, lsl=replace(self.lsl, **kwargs))
