"""Pull an LSL stream and log what every chunk contains.

Records, per ``pull_chunk`` call: number of samples, LSL timestamps of the first
and last sample, the gap since the previous pull, and the timestamp step between
consecutive chunks (which reveals dropped samples).  Prints a summary and can
write the raw records to CSV.

    # terminal 1 – streamer
    uv run tmsi-lsl --duration 12

    # terminal 2 – what a consumer actually receives
    uv run python scripts/lsl_receive_probe.py --duration 8 --csv receive.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tmsi_lsl.lsl_probe import LslChunkRecorder, ProbeError, run_probe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", default="TMSi SAGA", help="LSL stream name to resolve")
    parser.add_argument("--duration", type=float, default=5.0, help="seconds to pull")
    parser.add_argument("--csv", metavar="PATH", help="write per-chunk records to CSV")
    parser.add_argument(
        "--max-samples",
        type=int,
        default=1024,
        help="max samples per pull; small values reveal the sender's chunking",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=1,
        help="return as soon as this many samples are available (1 = sender's "
        "granularity, 0 = let liblsl accumulate up to --max-samples)",
    )
    parser.add_argument(
        "--resolve-timeout",
        type=float,
        default=10.0,
        help="how long to wait for the stream to appear",
    )
    args = parser.parse_args()

    recorder = LslChunkRecorder(args.csv)
    try:
        pulls = run_probe(
            args.name,
            args.duration,
            recorder,
            resolve_timeout=args.resolve_timeout,
            max_samples=args.max_samples,
            min_samples=args.min_samples,
        )
    except ProbeError as exc:
        recorder.close()
        print(str(exc), file=sys.stderr)
        return 3

    recorder.close()
    print(f"python {sys.version.split()[0]}")
    print(recorder.summary())
    if pulls == 0:
        print("warning: no samples arrived during the window", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
