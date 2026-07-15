# rustloops

Make Python hot loops faster by translating them to Rust.

rustloops is a set of AI agent skills that wrap the
[PyO3](https://pyo3.rs) / [maturin](https://maturin.rs) workflow into three simple commands.
Inspired by ["Making Python 100x faster with less than 100 lines of Rust"](https://ohadravid.github.io/posts/2023-03-rusty-python/).

---

## What it does

| Skill | When to use |
|---|---|
| `rustloops` | Translate a hot Python function to Rust and wire it back in |
| `rustloops-compare` | Re-benchmark a previously accelerated function |
| `rustloops-revert` | Undo the translation and restore the original Python |

The skills handle the full cycle: baseline measurement → Rust code generation → build →
output-equivalence verification → before/after report.  All changes are tracked in a
manifest and the original Python is backed up, so reverting is safe.

---

## Prerequisites

You need `cargo`, `maturin`, and (optionally) `py-spy`.  Install them with whichever
method fits your environment:

```bash
# pixi
pixi add rust maturin

# conda
conda install -c conda-forge rust maturin

# pip + rustup
curl https://sh.rustup.rs -sSf | sh
pip install maturin

# py-spy (any method)
pip install py-spy
```

The skills will check these tools and print install instructions if anything is missing.

---

## Quickstart

1. Open this repo in your agent.
2. Try one of the included examples (see [Examples](#examples) below), or point the
   skill at a slow loop in your own code.  Tell your agent what to speed up:

   > "Speed up `find_close_polygons` in `poly_match.py` — test with `python measure.py`"

   Your agent will load the `rustloops` skill and take it from there.

3. After changes, re-benchmark at any time:

   > "Re-run the rustloops comparison for poly_match"

4. To undo everything:

   > "Revert poly_match back to Python"

---

## Examples

The `examples/` directory contains three self-contained scientific Python demos,
each targeting a different pattern.  They are the fastest way to see the skills in action.

| Example | Hot target | Pattern | Expected speedup |
|---|---|---|---|
| `examples/nbody/` | `step()` — O(N²) pairwise gravity | A + C | ~20–50x (D after rewrite) |
| `examples/particle/` | `Particle` class + `update()` | B | ~10–30x |
| `examples/mandelbrot/` | `escape_counts()` — per-pixel escape time | A (scalar) + D (rayon) | ~50–100x, near-linear in core count with D |

Each example has a ready-to-paste prompt in `examples/README.md`.

Run any baseline manually after activating the environment:

```bash
pixi shell
cd examples/mandelbrot
python measure.py
# MEAN_MS:1842.3
# SIGNATURE:sha256:3a7f...
```

---

## How it works

```
your project/
  poly_match.py             ← original file, wired to import from Rust
  poly_match_rs/            ← generated Rust crate (created by the skill, one per module)
    Cargo.toml
    src/lib.rs
  .rustloops/
    manifest.json           ← timing history + backup paths + output signatures
    backups/
      poly_match.20260713-101500.py   ← timestamped backup of original Python
  examples/                 ← demo examples (this repo)
    nbody/
    particle/
    mandelbrot/
```

The Python file is modified minimally: a `try/except ImportError` block is added at the
top so the file falls back to pure Python automatically if the Rust extension is not built.

---

## Worked example — poly-match

Source: the blog post above.  The hot function iterates over 2 000 polygons and filters
by distance from a query point.

**Before (pure Python, ~290 ms):**

```python
def find_close_polygons(polygon_list, query, max_dist):
    result = []
    for poly in polygon_list:
        if np.linalg.norm(poly.center - query) < max_dist:
            result.append(poly)
    return result
```

**After `rustloops` (Pattern A naive translation, ~23 ms, ~12x faster):**
The same call signature, now dispatching to a Rust function via PyO3.

**After applying Pattern B** (move `Polygon` class to `#[pyclass]`, ~8 ms, ~36x faster).

**After applying Pattern C** (eliminate inner-loop allocations, ~6 ms, ~48x faster).

**After applying Pattern D** (parallelize with rayon, sub-2 ms on multi-core).

---

## Pattern cookbook

The skills include a cookbook at `.agents/skills/rustloops/patterns.md` with four
patterns:

- **Pattern A** — Translate a function that takes numpy arrays (5–15x speedup).
- **Pattern B** — Move a hot data class to a Rust `#[pyclass]` (additional 2–4x).
- **Pattern C** — Eliminate per-iteration heap allocations (additional 1.5–2x).
- **Pattern D** — Parallelize independent iterations across CPU cores with
  [rayon](https://docs.rs/rayon) (near-linear scaling in core count, orthogonal to
  A/B/C — layers on top of a correct serial translation).

Each pattern has a Python-before / Rust-after snippet and a gotchas table.

---

## Manifest schema

`.rustloops/manifest.json` tracks everything the skills need:

```json
{
  "entries": [{
    "module": "poly_match",
    "source_file": "poly_match.py",
    "targets": [{ "name": "find_close_polygons", "lines": [12, 24] }],
    "crate": "poly_match_rs",
    "backup": ".rustloops/backups/poly_match.20260713-101500.py",
    "test_command": "python measure.py",
    "baseline_ms": 293.41,
    "output_signature": "sha256:...",
    "history": [
      { "ts": "2026-07-13T10:15:00Z", "ms": 23.44, "note": "v1 naive" }
    ]
  }]
}
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Rust is slower than Python | You built without `--release`. Run `maturin develop --release` in the crate dir. |
| `ImportError` at runtime | Extension not built or stale. Re-run `maturin develop --release`. |
| Type mismatch `f64` vs `f32` | Cast on Python side: `arr.astype(np.float64)` before passing. |
| Linker error (`-lopenblas`) on macOS | Use `features = ["openblas-static"]` or `brew install openblas`. |
| Output signature mismatch | Results are in non-deterministic order. Sort before hashing. |
| `pyo3` / `numpy` version conflict | Pin both to the same minor version: `pyo3 = "0.29"`, `numpy = "0.29"`. |
| Parallel (rayon) slower than serial | Workload too small for thread overhead; fall back to serial for tiny N. |
| Rayon parallel loop has no speedup | GIL still held; wrap the parallel region in `py.allow_threads(\|\| { ... })`. |
| Parallel results differ per run | Shared accumulation (`+=`) across threads; rewrite each iteration to write a disjoint output slot. |

---

## Scope

- **numpy-focused.** scipy patterns (e.g. `cdist`, `ConvexHull`) are noted as future work.
- **One crate per Python module.** Multiple hot functions in the same file share a crate.
- **Data parallelism via rayon.** Embarrassingly-parallel loops can be spread across
  all available CPU cores with near-linear scaling (Pattern D in the cookbook).
- Works with any Python environment (pixi, conda, virtualenv, system Python).
