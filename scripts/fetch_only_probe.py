"""Measure SDK fetch timing *without* creating any LSL stream.

The SDK's sampling thread polls the device every 10 ms (``TMSiThread(pause=0.01)``).
In the full application an LSL outlet is created first, and liblsl raises the
Windows timer resolution to 1 ms — which hides the CPython 3.10 sleep granularity.
This script deliberately skips LSL, so the measurement shows what the SDK loop
really achieves on this interpreter:

    uv run python scripts/fetch_only_probe.py --duration 6
    uv run --python 3.10 --no-project python scripts/fetch_only_probe.py --duration 6

No third-party package is needed (no pylsl, no Qt): the SDK and ``tmsi_lsl`` are
pure standard library + ctypes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tmsi_lsl.saga_source import SagaSource, SourceError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--duration", type=float, default=6.0, help="seconds to acquire")
    parser.add_argument("--csv", metavar="PATH", help="write per-fetch records to CSV")
    parser.add_argument("--dr-interface", default="docked")
    parser.add_argument("--ds-interface", default="usb")
    parser.add_argument(
        "--channels",
        default=None,
        help="optional comma separated 1-based channel numbers to gate in "
        "(default: keep the device configuration)",
    )
    args = parser.parse_args()

    recorder = None
    source = SagaSource(dr_interface=args.dr_interface, ds_interface=args.ds_interface)
    try:
        summary = source.connect()
        print(
            f"python : {sys.version.split()[0]}\n"
            f"device : {summary.name} SN {summary.serial_number}, "
            f"{summary.num_active_channels}/{summary.num_channels} channels, "
            f"{summary.sampling_frequency} Hz (base {summary.base_sample_rate})"
        )

        if args.channels:
            from tmsi_lsl.channels import parse_spec, select_indices

            indices = select_indices(source.all_channels(), parse_spec(args.channels))
            source.set_active_channels(indices)

        recorder = source.enable_fetch_timing(args.csv)
        source.start()
        print(f"acquiring for {args.duration:g} s WITHOUT any LSL stream ...")
        time.sleep(args.duration)
        source.stop()
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            source.close()
        except Exception:  # noqa: BLE001 - teardown must not mask the result
            pass

    if recorder is not None:
        recorder.close()
        stats = recorder.stats()
        print()
        print(recorder.summary())
        if stats.count:
            non_empty = max(1, stats.count - stats.empty_fetches)
            print(
                f"  sets/fetch   : {stats.total_sets / stats.count:.1f} overall, "
                f"{stats.total_sets / non_empty:.1f} per non-empty fetch"
            )
            if stats.span > 0:
                print(f"  sets/s       : {stats.total_sets / stats.span:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
