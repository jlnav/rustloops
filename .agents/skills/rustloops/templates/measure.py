"""
measure.py — rustloops timing + output-signature harness

Copy this file into the user's project directory and adapt the CONFIGURE section.
Run with:   python measure.py [--runs N]

Output (to stdout):
  MEAN_MS:<float>          mean wall-clock time across N runs
  SIGNATURE:sha256:<hex>   SHA-256 of the deterministic output
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import time

# ── CONFIGURE ─────────────────────────────────────────────────────────────────
# Replace this block with whatever sets up and calls the function under test.
# The function MUST produce deterministic, printable output written to `output_buf`.

def run_workload(output_buf: io.StringIO) -> None:
    """
    Execute the function under test once.  Write all result data to output_buf
    in a deterministic, human-readable form so the signature is meaningful.

    Example (poly-match):

        import numpy as np
        from poly_match import find_close_polygons, Polygon

        rng = np.random.default_rng(42)
        polygons = [Polygon(rng.random(5), rng.random(5)) for _ in range(2000)]
        query = np.array([0.5, 0.5])
        result = find_close_polygons(polygons, query, 0.5)

        for p in sorted(result, key=lambda p: tuple(p.center)):
            print(p.center, file=output_buf)
    """
    raise NotImplementedError("Edit run_workload() in measure.py")

# ── END CONFIGURE ─────────────────────────────────────────────────────────────


def _measure(runs: int) -> tuple[float, str]:
    """Returns (mean_ms, sha256_hex) over `runs` iterations."""
    times: list[float] = []
    sig = ""

    for i in range(runs):
        buf = io.StringIO()
        t0 = time.perf_counter()
        run_workload(buf)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0)
        if i == 0:
            raw = buf.getvalue().encode()
            sig = hashlib.sha256(raw).hexdigest()

    mean_ms = sum(times) / len(times)
    return mean_ms, sig


def main() -> None:
    parser = argparse.ArgumentParser(description="rustloops harness")
    parser.add_argument("--runs", type=int, default=3,
                        help="number of timed repetitions (default: 3)")
    args = parser.parse_args()

    mean_ms, sig = _measure(args.runs)
    # Machine-readable lines — the skill parses these:
    print(f"MEAN_MS:{mean_ms:.3f}")
    print(f"SIGNATURE:sha256:{sig}")


if __name__ == "__main__":
    main()
