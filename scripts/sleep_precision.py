"""Measure how accurate ``time.sleep()`` is on the interpreter running this file.

Why it matters: the TMSi SDK sampling thread sleeps 10 ms between fetches
(``TMSiThread(pause=0.01)``).  On Windows, Python 3.10 (and older) rounds every
sleep up to the ~15.6 ms system timer tick, so a 10 ms pause really costs 15.6 ms
and the loop runs at ~64 Hz instead of 100 Hz.  Python 3.11 switched
``time.sleep()`` to a high-resolution waitable timer, which is why the same code
is much more accurate there.

Run it with each interpreter to compare:

    uv run python scripts/sleep_precision.py                     # project env
    uvx --python 3.11 --no-project python scripts/sleep_precision.py

"""

from __future__ import annotations

import argparse
import statistics
import sys
import time


def measure(seconds: float, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        time.sleep(seconds)
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument(
        "--durations",
        default="0.001,0.005,0.01,0.02",
        help="comma separated sleep durations in seconds",
    )
    parser.add_argument(
        "--outlet",
        action="store_true",
        help="create an LSL outlet first (liblsl raises the Windows timer "
        "resolution, which hides the 3.10 sleep problem)",
    )
    args = parser.parse_args()

    if args.outlet:
        import pylsl

        info = pylsl.StreamInfo("sleep-precision", "EEG", 4, 100.0, "float32", "sleep-probe")
        outlet = pylsl.StreamOutlet(info, 4)  # noqa: F841 - keeps liblsl threads alive
        print("LSL outlet created (timer resolution raised)")

    durations = [float(value) for value in args.durations.split(",") if value.strip()]
    print(f"python {sys.version.split()[0]} ({sys.platform})")

    for duration in durations:
        samples = measure(duration, args.iterations)
        overhead = statistics.mean(samples) - duration * 1000.0
        print(
            f"  sleep({duration * 1000:6.1f} ms): "
            f"mean={statistics.mean(samples):7.2f}  "
            f"p50={samples[len(samples) // 2]:7.2f}  "
            f"p95={samples[int(len(samples) * 0.95)]:7.2f}  "
            f"max={samples[-1]:7.2f} ms   "
            f"overhead={overhead:+6.2f} ms"
        )

    coarse = statistics.mean(measure(0.01, 20)) > 12.0
    modern = sys.version_info >= (3, 11)
    print()
    if coarse:
        print(
            "verdict: sleeps collapse to the ~15.6 ms timer tick. On CPython < 3.11 "
            "nothing in this process raised the timer; create an LSL stream (or run "
            "3.11+) to see the difference."
        )
    elif modern:
        print("verdict: ~1 ms resolution - provided by CPython >= 3.11 itself")
    elif args.outlet:
        print(
            "verdict: ~1 ms resolution - raised by the LSL outlet created above "
            "(CPython < 3.11 relies on that request)"
        )
    else:
        print(
            "verdict: ~1 ms resolution - something in this process raised the timer, "
            "although no LSL outlet was created here"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
