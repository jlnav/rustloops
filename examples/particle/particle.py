"""
Particle system with a hot data class.

Hot target: the `Particle` class + `update(particles, dt)`
  `update` iterates over a list of Particle objects, reading and writing
  `.pos` and `.vel` numpy arrays on every step.  Python attribute lookup
  on a custom class dominates the runtime — the inner work per particle
  is cheap, so the overhead ratio is very high.

  This is the canonical Pattern B case: moving `Particle` to a Rust
  `#[pyclass]` eliminates attribute-lookup overhead and gives the Rust
  loop direct field access.

Target pattern : Pattern B (#[pyclass] data class)
Expected speedup: ~10-30x after moving Particle to Rust.

Demo prompt for rustloops skill:
  "Speed up the Particle class and `update` in examples/particle/particle.py
   — test with `python measure.py`"
"""

from __future__ import annotations

import numpy as np


class Particle:
    """A point particle with position, velocity, and mass."""

    def __init__(
        self,
        pos: np.ndarray,   # shape (3,), float64
        vel: np.ndarray,   # shape (3,), float64
        mass: float = 1.0,
    ) -> None:
        self.pos  = pos.astype(np.float64)
        self.vel  = vel.astype(np.float64)
        self.mass = float(mass)

    def kinetic_energy(self) -> float:
        """0.5 * m * |v|^2"""
        v = self.vel
        return 0.5 * self.mass * (v[0] * v[0] + v[1] * v[1] + v[2] * v[2])

    def advance(self, force: np.ndarray, dt: float) -> None:
        """Velocity-Verlet drift: update vel then pos."""
        ax = force[0] / self.mass
        ay = force[1] / self.mass
        az = force[2] / self.mass
        self.vel[0] += ax * dt
        self.vel[1] += ay * dt
        self.vel[2] += az * dt
        self.pos[0] += self.vel[0] * dt
        self.pos[1] += self.vel[1] * dt
        self.pos[2] += self.vel[2] * dt


# Constant downward gravity field (z-axis)
GRAVITY = np.array([0.0, 0.0, -9.81], dtype=np.float64)


def update(particles: list[Particle], dt: float) -> None:
    """
    Advance all particles by dt under a uniform gravity field.
    The hot loop: per-particle attribute read/write on a Python object.
    """
    for p in particles:
        force = GRAVITY * p.mass
        p.advance(force, dt)
