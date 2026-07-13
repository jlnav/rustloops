"""
Mandelbrot set escape-time computation.

Hot function: escape_counts(width, height, max_iter)
  For each pixel on a grid, iterates z = z^2 + c until |z| > 2 or
  max_iter is reached.  Pure scalar complex arithmetic — no numpy
  ufunc can help here.  This is the strongest possible case for Rust:
  the per-pixel loop body is trivially expressible in Rust and the
  Python interpreter overhead dwarfs the actual arithmetic.

Target pattern : Pattern A (scalar-heavy function wrap)
Expected speedup: ~50-100x after Rust translation.

Demo prompt for rustloops skill:
  "Speed up `escape_counts` in examples/mandelbrot/mandelbrot.py
   — test with `python measure.py`"
"""

from __future__ import annotations

import numpy as np


def escape_counts(
    width: int,
    height: int,
    max_iter: int = 200,
    x_min: float = -2.5,
    x_max: float = 1.0,
    y_min: float = -1.25,
    y_max: float = 1.25,
) -> np.ndarray:
    """
    Return an (height, width) uint32 array of per-pixel iteration counts.

    Each entry is the number of iterations before |z| > 2, or max_iter
    if the point did not escape (i.e. it is in the Mandelbrot set).
    """
    counts = np.empty((height, width), dtype=np.uint32)

    x_step = (x_max - x_min) / width
    y_step = (y_max - y_min) / height

    for row in range(height):
        ci = y_min + row * y_step
        for col in range(width):
            cr = x_min + col * x_step

            zr = 0.0
            zi = 0.0
            n = 0
            while n < max_iter:
                zr2 = zr * zr
                zi2 = zi * zi
                if zr2 + zi2 > 4.0:
                    break
                zi = 2.0 * zr * zi + ci
                zr = zr2 - zi2 + cr
                n += 1

            counts[row, col] = n

    return counts
