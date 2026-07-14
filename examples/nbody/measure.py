"""
measure.py — rustloops timing + output-signature harness for nbody.

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

import numpy as np


def run_workload(output_buf: io.StringIO) -> None:
    from nbody import simulate

    rng = np.random.default_rng(42)
    n = 300  # bodies — gives ~0.5-2 s baseline on pure Python

    pos = rng.standard_normal((n, 3))
    vel = rng.standard_normal((n, 3)) * 0.1
    mass = rng.uniform(0.5, 2.0, n)

    simulate(pos, vel, mass, dt=0.01, n_steps=40)

    # Deterministic output: sort bodies by final x position
    order = np.argsort(pos[:, 0])
    for idx in order:
        print(f"{pos[idx, 0]:.6f} {pos[idx, 1]:.6f} {pos[idx, 2]:.6f}", file=output_buf)


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
    parser = argparse.ArgumentParser(description="rustloops harness — nbody")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    mean_ms, sig = _measure(args.runs)
    print(f"MEAN_MS:{mean_ms:.3f}")
    print(f"SIGNATURE:sha256:{sig}")


if __name__ == "__main__":
    main()
