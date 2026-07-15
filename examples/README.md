# rustloops examples

Three self-contained scientific Python examples for demonstrating the rustloops skills.
Each has a realistic hot loop, a seeded deterministic workload, and a `measure.py` harness
that the skills can drive directly.

---

## Examples at a glance

| Example | Hot target | Pattern | Expected speedup | Baseline target |
|---|---|---|---|---|
| `nbody/` | `step()` — O(N²) pairwise gravity | A + C | ~20–50x (D after rewrite) | ~0.5–2 s |
| `particle/` | `Particle` class + `update()` | B | ~10–30x | ~0.5–1.5 s |
| `mandelbrot/` | `escape_counts()` — per-pixel iteration | A (scalar) + D (rayon) | ~50–100x, near-linear in core count with D | ~1–3 s |

---

## How to use with the rustloops skill

Activate the environment, then paste the prompt for the example you want to demo:

```
pixi shell
```

### nbody — N-body gravitational simulation

> "Speed up `step` in `examples/nbody/nbody.py` — test with `python measure.py`"

Pattern A (numpy array function wrap) + Pattern C (eliminate per-iteration allocations).
Each body pair requires a distance calculation; the O(N²) Python loop is the bottleneck.
After the serial Rust version is working, the loop can be rewritten to drop the `j > i`
symmetry and parallelized with rayon (Pattern D) for near-linear scaling in core count.

### particle — Particle data class

> "Speed up the `Particle` class and `update` in `examples/particle/particle.py` — test with `python measure.py`"

Pattern B (`#[pyclass]` data class). Python attribute lookup on `p.pos` / `p.vel`
inside a 2 000-particle loop dominates runtime. Moving the class to Rust eliminates it.

### mandelbrot — escape-time fractal

> "Speed up `escape_counts` in `examples/mandelbrot/mandelbrot.py` — test with `python measure.py`"

Pattern A (scalar-heavy) + Pattern D (rayon). Pure scalar complex arithmetic in a double
loop — no numpy ufunc can help. Each pixel is independent, making this the canonical
candidate for Pattern D: after translating to Rust, a single swap from `.iter()`
to `.par_chunks_mut()` spreads rows across all CPU cores with near-linear scaling.

---

## Running baselines manually

Each `measure.py` is self-contained. From within an example directory:

```
python measure.py          # 3 runs (default)
python measure.py --runs 5 # more runs for a stable mean
```

Output lines parsed by the skill:
```
MEAN_MS:1423.881
SIGNATURE:sha256:3a7f...
```

---

## File layout

```
examples/
  nbody/
    nbody.py      <- module with hot function `step`
    measure.py    <- timing + signature harness
  particle/
    particle.py   <- module with hot class `Particle` and `update`
    measure.py
  mandelbrot/
    mandelbrot.py <- module with hot function `escape_counts`
    measure.py
```
