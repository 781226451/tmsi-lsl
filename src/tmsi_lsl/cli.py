"""Command line interface: configure a SAGA device and stream it to LSL."""

from __future__ import annotations

import argparse
import logging
import platform
import sys
import time
from typing import Sequence

from . import __version__, sdk_bootstrap
from .channels import (
    PRESETS,
    ChannelInfo,
    SelectionError,
    format_channel_table,
    parse_channel_types,
    parse_spec,
    select_indices,
    select_preset,
    summarise_selection,
)
from .lsl_stream import (
    DEFAULT_LSL_OFFSET,
    LslStream,
    LslStreamInfo,
    StreamError,
    configure_pylsl_logging,
    pylsl_version,
)
from .saga_source import (
    DR_INTERFACES,
    DS_INTERFACES,
    DeviceNotFoundError,
    DeviceSummary,
    DriverUnavailableError,
    SagaSource,
    SelectionReport,
    SourceError,
)

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

_LOG = logging.getLogger("tmsi_lsl")

_EPILOG = """\
channel selection (--channels):
  a comma separated list of tokens, matched against --list-channels output
    3              channel number 3 (1-based)
    1-8            channel numbers 1 to 8
    type:UNI       all channels of a type (UNI, BIP, AUX, status, counter)
    name:BIP*      all channels whose name matches a glob
    BIP 01         bare token: a name glob

examples:
  tmsi-lsl --check
  tmsi-lsl --list-channels
  tmsi-lsl --preset ref64                 (reference + first 64 data channels)
  tmsi-lsl --channels 1-8
  tmsi-lsl --channels "type:UNI" --sampling-rate 4096 --divider 2
  tmsi-lsl --channels "name:BIP*,type:AUX" --lsl-name "EMG SAGA"
"""


# --------------------------------------------------------------------- output


def _configure_output_encoding() -> None:
    """Never crash on unencodable channel names (units contain non-ASCII)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except Exception:  # pragma: no cover - exotic streams
            pass


def _setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    # The SDK logs to its own "TMSi" logger; keep it quiet unless debugging,
    # because this program reports every SDK failure itself.
    logging.getLogger("TMSi").setLevel(logging.DEBUG if level <= logging.DEBUG else logging.ERROR)


def _print(message: str = "") -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmsi-lsl",
        description=(
            "Connect to a TMSi SAGA device, configure the channel selection, "
            "and stream the acquired signal to a Lab Streaming Layer (LSL) outlet."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"tmsi-lsl {__version__}")

    stream = parser.add_argument_group("LSL stream")
    stream.add_argument(
        "--lsl-name",
        default="TMSi SAGA",
        metavar="NAME",
        help="name of the published LSL stream (default: %(default)s)",
    )
    stream.add_argument(
        "--lsl-offset",
        type=float,
        default=DEFAULT_LSL_OFFSET,
        metavar="SECONDS",
        help="latency offset advertised in the stream metadata (default: %(default)s)",
    )

    device = parser.add_argument_group("device")
    device.add_argument(
        "--dr-interface",
        choices=DR_INTERFACES,
        default="docked",
        help="data recorder <-> docking station interface (default: %(default)s)",
    )
    device.add_argument(
        "--ds-interface",
        choices=DS_INTERFACES,
        default="usb",
        help="docking station <-> computer interface (default: %(default)s)",
    )
    device.add_argument(
        "--device-index",
        type=int,
        default=0,
        metavar="N",
        help="which discovered device to use, 0-based (default: %(default)s)",
    )
    device.add_argument(
        "--num-retries",
        type=int,
        default=3,
        metavar="N",
        help="discovery retries (default: %(default)s)",
    )

    config = parser.add_argument_group("acquisition")
    selection = config.add_mutually_exclusive_group()
    selection.add_argument(
        "--channels",
        metavar="SPEC",
        help="channels to enable; all other channels are disabled (see the examples below)",
    )
    selection.add_argument(
        "--channel-types",
        metavar="LIST",
        help="comma separated channel types to enable, e.g. 'uni,bip'",
    )
    selection.add_argument(
        "--preset",
        choices=PRESETS,
        help="named selection: 'ref64' = reference + first 64 UNI data channels, "
        "'uni' = all UNI channels, 'all' = every channel",
    )
    config.add_argument(
        "--base-sample-rate",
        "--sampling-rate",
        dest="base_sample_rate",
        type=int,
        choices=(4000, 4096),
        help="device base sample rate in Hz (default: keep the device setting)",
    )
    config.add_argument(
        "--divider",
        type=int,
        choices=(1, 2, 4, 8),
        help="decimation divider applied to every enabled channel "
        "(sample rate = base rate / divider)",
    )
    config.add_argument(
        "--duration",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="stop after SECONDS; 0 streams until Ctrl+C (default: %(default)s)",
    )
    config.add_argument(
        "--status-interval",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="how often to print throughput; 0 disables (default: %(default)s)",
    )
    config.add_argument(
        "--record-timing",
        metavar="CSV",
        help="log the interval between SDK data fetches to CSV and print a "
        "summary at exit (useful to compare Python versions / machines)",
    )
    config.add_argument(
        "--reference",
        choices=("common", "average"),
        default="common",
        help="reference applied to the UNI channels before measuring: 'common' "
        "(default) references them against the CREF electrode, 'average' applies "
        "a common average reference instead",
    )
    config.add_argument(
        "--auto-reference",
        choices=("fixed", "average"),
        default="fixed",
        help="what the device falls back to when the common (CREF) mode is "
        "disconnected: 'fixed' (default) never switches, 'average' switches to "
        "the common average reference",
    )

    tools = parser.add_argument_group("tools")
    tools.add_argument(
        "--list-channels",
        action="store_true",
        help="print the device channel list and exit",
    )
    tools.add_argument(
        "--check",
        action="store_true",
        help="report SDK/ driver/ LSL availability and exit",
    )
    tools.add_argument(
        "--simulate",
        action="store_true",
        help="do not touch hardware; stream synthetic data through the whole pipeline",
    )
    tools.add_argument(
        "--sim-channels",
        type=int,
        default=32,
        metavar="N",
        help="number of simulated channels (default: %(default)s)",
    )

    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="detail level of the diagnostic log (default: %(default)s)",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="shorthand for --log-level WARNING")
    return parser


# ------------------------------------------------------------------- reporting


def _describe_summary(summary: DeviceSummary) -> str:
    return (
        f"{summary.name} (serial {summary.serial_number}), "
        f"{summary.num_active_channels}/{summary.num_channels} channels active, "
        f"base rate {summary.base_sample_rate} Hz, "
        f"sampling at {summary.sampling_frequency} Hz, "
        f"interface {summary.dr_interface}/{summary.ds_interface}"
    )


def _describe_selection(
    report: SelectionReport,
    channels: Sequence[ChannelInfo],
) -> list[str]:
    by_index = {channel.index: channel for channel in channels}
    lines = [f"Selection applied: {summarise_selection(channels, report.requested)}"]
    lines.append(f"  active on the device: {len(report.active)} channel(s)")

    if report.extra:
        names = ", ".join(
            f"#{by_index[index].number} {by_index[index].name}"
            for index in report.extra
            if index in by_index
        )
        lines.append(
            f"  note: {len(report.extra)} channel(s) stay active because the firmware "
            f"does not allow disabling them ({names})"
        )
    if report.missing:
        names = ", ".join(
            f"#{by_index[index].number} {by_index[index].name}"
            for index in report.missing
            if index in by_index
        )
        lines.append(f"  WARNING: requested but not active: {names}")
    return lines


def _rate_note(active: Sequence[ChannelInfo]) -> str | None:
    """Warn when channels do not share one sample rate (LSL needs a single rate)."""
    rates = {channel.sample_rate for channel in active if channel.sample_rate is not None}
    if len(rates) <= 1:
        return None
    detail = ", ".join(f"{rate:g} Hz: {count} ch" for rate, count in sorted(
        (rate, sum(1 for ch in active if ch.sample_rate == rate)) for rate in rates
    ))
    return (
        "active channels use different sample rates ("
        + detail
        + "); the LSL stream advertises a single rate and consumers will interpret "
        "the data incorrectly. Use --divider to give every channel the same rate."
    )


def _print_status(stream: LslStream) -> None:
    stats = stream.stats()
    if stats is None:
        return
    if stats.last_block_at is None:
        last = "no data yet"
    else:
        last = f"last block {time.monotonic() - stats.last_block_at:.2f}s ago"
    _print(
        f"[{stats.age:7.1f}s] {stats.sample_sets:>10,d} frames  "
        f"{stats.sample_sets_per_second:8.1f} frames/s  {last}"
    )
    if stats.last_block_at is not None and time.monotonic() - stats.last_block_at > 2.0:
        _LOG.warning("no sample data received for over 2 s; is the device still connected?")


# ------------------------------------------------------------------ operations


def _run_check(args: argparse.Namespace) -> int:
    _print(f"tmsi-lsl {__version__}")
    _print(f"  python         : {platform.python_version()} ({platform.platform()})")

    healthy = True
    try:
        root = sdk_bootstrap.sdk_root()
        _print(f"  TMSi SDK       : {root}")
    except sdk_bootstrap.SdkNotFoundError as exc:
        healthy = False
        _print(f"  TMSi SDK       : NOT FOUND\n    {exc}")

    version = pylsl_version()
    _print(f"  pylsl          : {version}")
    if version == "unavailable":
        healthy = False

    try:
        dll = sdk_bootstrap.dll_information()
    except sdk_bootstrap.SdkNotFoundError:
        dll = {"available": False, "path": None, "locked": None}

    if dll["available"] and not dll.get("locked"):
        _print(f"  SAGA driver    : loaded ({dll['path']})")
    elif dll["available"]:
        _print(f"  SAGA driver    : present but locked by another process ({dll['path']})")
        healthy = False
    else:
        healthy = False
        _print(
            "  SAGA driver    : NOT FOUND\n"
            "    expected C:\\Program Files\\TMSi\\Saga\\TMSiSagaDeviceLib.dll\n"
            "    install the TMSi SAGA driver (it is not shipped with this program)"
        )

    if dll["available"] and not dll.get("locked"):
        try:
            source = SagaSource(
                dr_interface=args.dr_interface,
                ds_interface=args.ds_interface,
                num_retries=1,
            )
            devices = source.discover()
            if devices:
                _print(
                    f"  devices        : {len(devices)} found on "
                    f"{args.dr_interface}/{args.ds_interface}"
                )
            else:
                _print(
                    f"  devices        : none found on "
                    f"{args.dr_interface}/{args.ds_interface}"
                )
        except SourceError as exc:
            _print(f"  devices        : discovery failed ({exc})")

    _print("")
    _print("status: ready" if healthy else "status: NOT ready (see above)")
    return EXIT_OK if healthy else EXIT_ENVIRONMENT


def _make_source(args: argparse.Namespace) -> tuple[SagaSource, DeviceSummary]:
    source = SagaSource(
        dr_interface=args.dr_interface,
        ds_interface=args.ds_interface,
        num_retries=args.num_retries,
        device_index=args.device_index,
    )

    if not args.simulate:
        return source, source.connect()

    from .simulator import SimulatedSaga  # noqa: PLC0415 - only needed for demos

    device = SimulatedSaga(num_uni=args.sim_channels)
    device.open()
    _LOG.info("using a simulated SAGA device (%d channels)", args.sim_channels + 2)
    return source, source.attach(device)


def _select_channels(args: argparse.Namespace, source: SagaSource) -> None:
    channels = source.all_channels()

    if args.preset:
        indices, warnings = select_preset(channels, args.preset)
        for warning in warnings:
            _LOG.warning("%s", warning)
        if not indices:
            raise SelectionError(f"preset {args.preset!r} selected no channel")
        report = source.set_active_channels(indices)
        for line in _describe_selection(report, channels):
            _print(line)
        if report.missing:
            raise SourceError("the device did not activate the requested channels")
        return

    selectors = []
    if args.channels:
        selectors.extend(parse_spec(args.channels))
    if args.channel_types:
        selectors.extend(parse_channel_types(args.channel_types))

    if not selectors:
        _LOG.info(
            "no channel selection given; keeping the device configuration (%d active)",
            len([channel for channel in channels if channel.is_active]),
        )
        return

    indices = select_indices(channels, selectors)
    report = source.set_active_channels(indices)
    for line in _describe_selection(report, channels):
        _print(line)
    if report.missing:
        raise SourceError("the device did not activate the requested channels")


def _shutdown(source: SagaSource, stream: LslStream | None) -> None:
    """Stop streaming, then tear down device and outlet, never masking errors."""
    try:
        source.stop()
    except Exception as exc:  # noqa: BLE001 - cleanup must continue
        _LOG.warning("stopping the measurement failed: %s", exc)
    if stream is not None:
        try:
            stream.close()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("closing the LSL stream failed: %s", exc)
    try:
        source.close()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("closing the device failed: %s", exc)


def _stream_until_stopped(args: argparse.Namespace, stream: LslStream) -> bool:
    """Block until the duration elapses or Ctrl+C is pressed."""
    started = time.monotonic()
    deadline = started + args.duration if args.duration and args.duration > 0 else None
    interval = max(args.status_interval, 0.0)
    next_status = started + interval if interval else None
    interrupted = False

    try:
        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                break
            if next_status is not None and now >= next_status:
                _print_status(stream)
                next_status += interval
            time.sleep(0.05)
    except KeyboardInterrupt:
        interrupted = True
        _print("\nStopping (Ctrl+C)...")
    return interrupted


def _run(args: argparse.Namespace) -> int:
    source, summary = _make_source(args)
    stream: LslStream | None = None
    stream_info: LslStreamInfo | None = None
    recorder = None
    try:
        _print(f"Connected: {_describe_summary(summary)}")

        channels = source.all_channels()
        if args.list_channels:
            _print("")
            _print(format_channel_table(channels))
            _print("")
            _print(f"{len(channels)} channel(s), {sum(1 for ch in channels if ch.is_active)} active")
            return EXIT_OK

        _select_channels(args, source)

        if args.divider is not None or args.base_sample_rate is not None:
            source.set_sampling(
                base_sample_rate=args.base_sample_rate,
                divider=args.divider,
            )

        active = source.active_channels()
        if not active:
            raise SourceError(
                "no active channels after configuration; pass --channels to enable some"
            )

        note = _rate_note(active)
        if note:
            _LOG.warning("%s", note)

        try:
            references = source.ensure_reference(args.reference, auto=args.auto_reference)
        except SourceError as exc:
            raise SourceError(f"could not pin the reference: {exc}") from exc
        _print(
            f"Reference: {references['reference']} "
            f"(auto-reference: {references['autoreference']})"
        )

        if args.record_timing:
            if args.simulate:
                _LOG.warning(
                    "the simulated device never calls get_device_data; "
                    "--record-timing is ignored in simulation mode"
                )
            else:
                recorder = source.enable_fetch_timing(args.record_timing)
                _print(f"Recording SDK fetch timing to {args.record_timing}")

        stream = LslStream(name=args.lsl_name, lsl_offset=args.lsl_offset)
        stream_info = stream.open(source.device)
        _print(
            f"LSL stream '{stream_info.name}' published: "
            f"{stream_info.channel_count} channels @ {stream_info.sample_rate:g} Hz, "
            f"source id '{stream_info.source_id}'"
        )
        _LOG.info("channel labels: %s", ", ".join(stream_info.channel_labels))

        source.start()
        _print("Acquiring... press Ctrl+C to stop." if not args.duration else
               f"Acquiring for {args.duration:g} s...")

        interrupted = _stream_until_stopped(args, stream)

        stats = stream.stats()
        if stats is not None and stats.age > 0:
            _print(
                f"Streamed {stats.sample_sets:,} frames "
                f"({stats.sample_sets_per_second:,.0f} frames/s) "
                f"over {stats.age:.1f} s"
            )
        if stats is not None and stats.sample_sets == 0:
            _LOG.warning(
                "the LSL outlet received no data; check the device and its channel configuration"
            )
        if interrupted:
            _print("Stopped by user.")
        return EXIT_OK
    finally:
        _shutdown(source, stream)
        if recorder is not None:
            recorder.close()
            _print("")
            _print(recorder.summary())


# ------------------------------------------------------------------------ main


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    _configure_output_encoding()
    _setup_logging("WARNING" if args.quiet else args.log_level)
    configure_pylsl_logging(verbose=args.log_level == "DEBUG" and not args.quiet)

    try:
        if args.check:
            return _run_check(args)
        return _run(args)
    except SelectionError as exc:
        _LOG.error("%s", exc)
        return EXIT_USAGE
    except (sdk_bootstrap.SdkNotFoundError, DeviceNotFoundError, DriverUnavailableError) as exc:
        _LOG.error("%s", exc)
        return EXIT_ENVIRONMENT
    except (SourceError, StreamError) as exc:
        _LOG.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        _print("\nInterrupted.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
