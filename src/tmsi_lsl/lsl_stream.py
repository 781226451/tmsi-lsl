"""LSL outlet built on the TMSi ``LSLWriter``.

The SDK ships a ``FileWriter`` in ``lsl`` mode that registers itself as a
consumer of the sample-data server and pushes every received block to a
``pylsl`` outlet.  We wrap it so that the application can additionally monitor
throughput and get decent error messages.

Note: the SDK writer publishes the stream with type ``EEG`` and source id
``tmsi-<serial>``; the stream *name* is configurable.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import logging
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path

from . import sdk_bootstrap

__all__ = [
    "LslStream",
    "LslStreamInfo",
    "StreamStats",
    "StreamError",
    "pylsl_version",
    "configure_pylsl_logging",
    "DEFAULT_LSL_OFFSET",
]

_LOG = logging.getLogger("tmsi_lsl")

#: Latency compensation published in the stream's ``synchronization`` metadata.
#: This is the value the TMSi SDK uses by default.
DEFAULT_LSL_OFFSET = 0.0335

_FILE_WRITER = None


class StreamError(RuntimeError):
    """Any failure while creating or closing the LSL outlet."""


def _load_file_writer(sdk_root: Path):
    """Import the SDK's ``FileWriter``/``FileFormat`` without the heavy extras.

    ``TMSiFileFormats/file_formats/__init__.py`` imports the Poly5, XDF and EDF
    writers, so touching it pulls in pandas, scipy and EDFlib even though this
    application only streams to LSL.  When those optional packages are missing
    we register a minimal package object for ``TMSiFileFormats.file_formats``
    so that only ``lsl_stream_writer`` (which needs pylsl only) is executed.

    If the full package does import, it is used unchanged.
    """
    global _FILE_WRITER
    if _FILE_WRITER is not None:
        return _FILE_WRITER

    package = "TMSiFileFormats.file_formats"
    if package not in sys.modules:
        try:
            importlib.import_module(package)
        except ImportError as exc:
            _LOG.debug("loading only the LSL format module (%s)", exc)
            stub = types.ModuleType(package)
            stub.__path__ = [str(sdk_root / "TMSiFileFormats" / "file_formats")]
            stub.__package__ = package
            sys.modules[package] = stub

    from TMSiFileFormats.file_writer import FileFormat, FileWriter  # noqa: PLC0415

    _FILE_WRITER = (FileWriter, FileFormat)
    return _FILE_WRITER


def pylsl_version() -> str:
    """Return the installed ``pylsl`` version (``"unavailable"`` if missing)."""
    try:
        import pylsl  # noqa: PLC0415

        return str(getattr(pylsl, "__version__", "unknown"))
    except Exception:  # pragma: no cover - depends on environment
        return "unavailable"


def configure_pylsl_logging(verbose: bool = False) -> None:
    """Reduce liblsl's network-interface logging where pylsl exposes the API.

    pylsl 1.18 (the version this project resolves) does not expose a log level,
    so this is a best-effort no-op there: liblsl prints a one-off banner when
    the first outlet is created.
    """
    try:
        import pylsl  # noqa: PLC0415

        level = getattr(
            pylsl,
            "log_level_debug" if verbose else "log_level_warning",
            None,
        )
        if level is not None:
            pylsl.set_loglevel(level)
    except Exception:  # pragma: no cover - depends on the pylsl build
        pass


@contextlib.contextmanager
def _quiet_sdk_console():
    """Route the SDK's ``print()`` output to the debug log instead of stdout."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield
    noise = buffer.getvalue().strip()
    if noise:
        _LOG.debug("SDK: %s", noise.replace("\n", " | "))


@dataclass(frozen=True)
class LslStreamInfo:
    """Metadata of the created outlet, useful for logging."""

    name: str
    source_id: str
    channel_count: int
    sample_rate: float
    channel_labels: tuple[str, ...]


@dataclass(frozen=True)
class StreamStats:
    """Throughput counters sampled at a point in time."""

    sample_sets: int
    samples: int
    age: float
    last_block_at: float | None
    active_age: float
    """Seconds since the first block arrived (0 before any data)."""

    @property
    def sample_sets_per_second(self) -> float:
        """Rate over the *data* window, so start-up latency does not dilute it."""
        span = self.active_age if self.active_age > 0 else self.age
        return self.sample_sets / span if span > 0 else 0.0


class _SampleCounter:
    """Consumer that only counts, so the application can report progress."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sample_sets = 0
        self._samples = 0
        self._first_block_at: float | None = None
        self._last_block_at: float | None = None

    def put(self, sample_data) -> None:
        with self._lock:
            now = time.monotonic()
            if self._first_block_at is None:
                self._first_block_at = now
            self._sample_sets += int(sample_data.num_sample_sets)
            self._samples += int(sample_data.num_sample_sets) * int(
                sample_data.num_samples_per_sample_set
            )
            self._last_block_at = now

    @property
    def sample_sets(self) -> int:
        with self._lock:
            return self._sample_sets

    @property
    def samples(self) -> int:
        with self._lock:
            return self._samples

    @property
    def first_block_at(self) -> float | None:
        with self._lock:
            return self._first_block_at

    @property
    def last_block_at(self) -> float | None:
        with self._lock:
            return self._last_block_at


class LslStream:
    """An LSL outlet fed by a SAGA device through the SDK data server."""

    def __init__(self, name: str = "TMSi SAGA", lsl_offset: float = DEFAULT_LSL_OFFSET) -> None:
        self._name = name or "TMSi SAGA"
        self._lsl_offset = float(lsl_offset)
        self._writer = None
        self._device = None
        self._counter: _SampleCounter | None = None
        self._info: LslStreamInfo | None = None
        self._started_at: float | None = None

    @property
    def info(self) -> LslStreamInfo | None:
        return self._info

    def open(self, device) -> LslStreamInfo:
        """Create the outlet and register it with the sample-data server.

        Must be called *after* the channel configuration is final and *before*
        the measurement starts.
        """
        if self._writer is not None:
            raise StreamError("the LSL stream is already open")

        sdk_root = sdk_bootstrap.ensure_sdk_importable()
        FileWriter, FileFormat = _load_file_writer(sdk_root)
        from TMSiSDK.sample_data_server.sample_data_server import SampleDataServer  # noqa: PLC0415
        from TMSiSDK.tmsi_errors.error import TMSiError  # noqa: PLC0415

        writer = FileWriter(FileFormat.lsl, self._name, lsl_offset=self._lsl_offset)
        try:
            with _quiet_sdk_console():
                writer.open(device)
        except TMSiError as exc:
            cause = exc.__context__
            detail = f"{type(cause).__name__}: {cause}" if cause is not None else str(exc)
            raise StreamError(f"could not create the LSL outlet: {detail}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise StreamError(f"could not create the LSL outlet: {exc}") from exc

        counter = _SampleCounter()
        try:
            SampleDataServer().register_consumer(device.get_id(), counter)
        except Exception:  # pragma: no cover - defensive
            writer.close()
            raise

        active_channels = device.get_device_active_channels()
        self._writer = writer
        self._device = device
        self._counter = counter
        self._started_at = time.monotonic()
        self._info = LslStreamInfo(
            name=self._name,
            source_id=f"tmsi-{device.get_device_serial_number()}",
            channel_count=len(active_channels),
            sample_rate=float(device.get_device_sampling_frequency()),
            channel_labels=tuple(str(ch.get_channel_name()).strip() for ch in active_channels),
        )
        return self._info

    def close(self) -> None:
        """Unregister the consumers and destroy the outlet."""
        if self._writer is None:
            return

        from TMSiSDK.sample_data_server.sample_data_server import SampleDataServer  # noqa: PLC0415

        error: Exception | None = None
        try:
            if self._counter is not None and self._device is not None:
                SampleDataServer().unregister_consumer(self._device.get_id(), self._counter)
        except Exception as exc:  # pragma: no cover - defensive
            error = exc
        finally:
            writer, self._writer = self._writer, None
            self._counter = None
            self._device = None
            try:
                with _quiet_sdk_console():
                    writer.close()
            except Exception as exc:  # pragma: no cover - defensive
                error = error or exc

        if error is not None:
            raise StreamError(f"could not close the LSL outlet: {error}") from error

    def stats(self) -> StreamStats | None:
        """Current throughput counters (``None`` if the stream is closed)."""
        counter = self._counter
        if counter is None or self._started_at is None:
            return None
        now = time.monotonic()
        first_block_at = counter.first_block_at
        return StreamStats(
            sample_sets=counter.sample_sets,
            samples=counter.samples,
            age=now - self._started_at,
            last_block_at=counter.last_block_at,
            active_age=0.0 if first_block_at is None else max(0.0, now - first_block_at),
        )

    def __enter__(self) -> "LslStream":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
