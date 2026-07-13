"""
measure.py — rustloops timing + output-signature harness for particle.

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
    from particle import Particle, update

    rng = np.random.default_rng(42)
    n = 2_000   # particles
    n_steps = 50

    particles = [
        Particle(
            pos=rng.standard_normal(3),
            vel=rng.standard_normal(3) * 0.5,
            mass=rng.uniform(0.5, 2.0),
        )
        for _ in range(n)
    ]

    for _ in range(n_steps):
        update(particles, dt=0.01)

    # Deterministic output: sort by final x position, print KE and pos
    particles.sort(key=lambda p: p.pos[0])
    for p in particles:
        ke = p.kinetic_energy()
        print(
            f"{p.pos[0]:.6f} {p.pos[1]:.6f} {p.pos[2]:.6f} {ke:.6f}",
            file=output_buf,
        )


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
    parser = argparse.ArgumentParser(description="rustloops harness — particle")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    mean_ms, sig = _measure(args.runs)
    print(f"MEAN_MS:{mean_ms:.3f}")
    print(f"SIGNATURE:sha256:{sig}")


if __name__ == "__main__":
    main()
