"""
N-body gravitational simulation.

Hot function: step(pos, vel, mass, dt)
  O(N^2) pairwise force accumulation over N bodies.
  Each body pair requires a distance calculation and force update —
  a classic Python-level loop that numpy cannot easily vectorize without
  broadcasting the full NxN distance matrix.

Target pattern : Pattern A (function wrap) + Pattern C (alloc avoidance)
Expected speedup: ~20-50x after Rust translation.

Demo prompt for rustloops skill:
  "Speed up `step` in examples/nbody/nbody.py — test with `python measure.py`"
"""

from __future__ import annotations

import numpy as np

# Gravitational constant (arbitrary units)
G = 1.0
SOFTENING = 0.1  # softening length to avoid singularities


def step(
    pos: np.ndarray,  # shape (N, 3), float64 — positions
    vel: np.ndarray,  # shape (N, 3), float64 — velocities
    mass: np.ndarray,  # shape (N,),   float64 — masses
    dt: float,
) -> None:
    """Advance the N-body system by one timestep (in-place, leapfrog)."""
    n = len(mass)

    # Accumulate accelerations
    acc = np.zeros_like(pos)
    for i in range(n):
        for j in range(i + 1, n):
            dx = pos[j, 0] - pos[i, 0]
            dy = pos[j, 1] - pos[i, 1]
            dz = pos[j, 2] - pos[i, 2]
            dist2 = dx * dx + dy * dy + dz * dz + SOFTENING * SOFTENING
            inv_dist3 = dist2 ** (-1.5)

            ax = G * dx * inv_dist3
            ay = G * dy * inv_dist3
            az = G * dz * inv_dist3

            acc[i, 0] += mass[j] * ax
            acc[i, 1] += mass[j] * ay
            acc[i, 2] += mass[j] * az

            acc[j, 0] -= mass[i] * ax
            acc[j, 1] -= mass[i] * ay
            acc[j, 2] -= mass[i] * az

    # Leapfrog kick-drift
    vel += acc * dt
    pos += vel * dt


def simulate(
    pos: np.ndarray,
    vel: np.ndarray,
    mass: np.ndarray,
    dt: float = 0.01,
    n_steps: int = 50,
) -> None:
    """Run the simulation for n_steps timesteps (in-place)."""
    for _ in range(n_steps):
        step(pos, vel, mass, dt)
