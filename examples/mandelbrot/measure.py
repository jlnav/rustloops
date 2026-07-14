"""
measure.py — rustloops timing + output-signature harness for mandelbrot.

Run with:  python measure.py [--runs N]

Output (stdout, parsed by rustloops skill):
  MEAN_MS:<float>
  SIGNATURE:sha256:<hex>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import time


def run_workload(output_buf: io.StringIO) -> None:
    from mandelbrot import escape_counts

    counts = escape_counts(width=400, height=400, max_iter=200)

    # Deterministic output: histogram of escape counts (stable, compact)
    hist = [0] * 201
    for val in counts.flat:
        hist[val] += 1
    for i, h in enumerate(hist):
        if h > 0:
            print(f"{i} {h}", file=output_buf)


# ── harness (do not edit below) ───────────────────────────────────────────────


def _measure(runs: int) -> tuple[float, str]:
    times: list[float] = []
    sig = ""
    for i in range(runs):
        buf = io.StringIO()
        t0 = time.perf_counter()
        run_workload(buf)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0)
        if i == 0:
            sig = hashlib.sha256(buf.getvalue().encode()).hexdigest()
    return sum(times) / len(times), sig


def main() -> None:
    parser = argparse.ArgumentParser(description="rustloops harness — mandelbrot")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    mean_ms, sig = _measure(args.runs)
    print(f"MEAN_MS:{mean_ms:.3f}")
    print(f"SIGNATURE:sha256:{sig}")


if __name__ == "__main__":
    main()
