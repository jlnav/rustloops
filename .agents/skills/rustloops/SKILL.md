---
name: rustloops
description: Accelerate a hot Python loop or function by translating it to Rust via PyO3/maturin.
---

# Skill: rustloops

Accelerate a hot Python loop or function by translating it to Rust via PyO3/maturin.

---

## Inputs to gather (ask the user if not provided)

1. **Target file** — path to the Python source file (e.g. `poly_match.py`).
2. **Target function(s) or loop(s)** — name or line-number range of the hot code.
3. **Test command** — a command that exercises the function and produces deterministic output (e.g. `python measure.py` or `pytest tests/test_poly.py`). If the user does not have one, generate a `measure.py` (see below).
4. **Module name** — Python import name for the Rust extension (default: `<source_stem>_rs`, e.g. `poly_match_rs`).

### Generating measure.py when no test command exists

If the user has no test command, generate a `measure.py` in the same directory as the
target file.  Use `templates/measure.py` as the base and fill in `run_workload()`:

- Import the target function/class from the target module.
- Construct realistic, **seeded** inputs using `numpy.random.default_rng(42)` (or
  equivalent deterministic construction if numpy is not used).
- Call the target function once per invocation of `run_workload()`.
- Write output to `output_buf` in a **sorted, stable** form (e.g. sort results by a
  key before printing) so the SHA-256 signature is deterministic across runs.
- Size the workload so the pure-Python baseline runs in roughly **0.5–3 s**.

After generating `measure.py`, confirm with the user that the inputs and workload size
look reasonable before proceeding.  The test command becomes `python measure.py`.

---

## Step 1 — Check toolchain

Run each of the following and verify it exits 0:

```
cargo --version
maturin --version
py-spy --version      # optional; skip if absent, note it in output
```

If `cargo` or `maturin` is missing, print install instructions and **stop**:

```
cargo/maturin not found. Install options:
  pixi:   pixi add rust maturin
  conda:  conda install -c conda-forge rust maturin
  pip:    pip install maturin
  rustup: curl https://sh.rustup.rs -sSf | sh  &&  pip install maturin
```

---

## Step 2 — Baseline measurement

Run the test command **3 times**, record wall-clock milliseconds for each run, compute the mean.
Capture stdout/stderr, compute a SHA-256 of stdout (the "output signature").

Write or update `.rustloops/manifest.json` (create `.rustloops/` if needed):

```json
{
  "entries": [{
    "module": "<module>",
    "source_file": "<file>",
    "targets": [{ "name": "<fn>", "lines": [<start>, <end>] }],
    "crate": "<module>_rs",
    "backup": ".rustloops/backups/<module>.<YYYYMMDD-HHMMSS>.py",
    "test_command": "<cmd>",
    "baseline_ms": <mean>,
    "output_signature": "sha256:<hex>",
    "history": []
  }]
}
```

If an entry for this module already exists, reuse `baseline_ms` and `output_signature` from it (do not overwrite the baseline).

---

## Step 3 — (Optional) Profile with py-spy

If `py-spy` is available, run:

```
py-spy record --native --output profile.svg -- python <script>
```

Confirm the target function appears hot (>10 % of total time).
If not, warn the user that this function may not be the actual bottleneck.

---

## Step 4 — Analyze the target

Read the target function. Determine:

| Question | Informs |
|---|---|
| Does it accept/return numpy arrays? | Use `PyReadonlyArray` / `PyArray` (Pattern A) |
| Is there a hot data class whose fields are accessed many times? | Move class to Rust `#[pyclass]` (Pattern B) |
| Are temporary allocations (`Vec`, `.to_owned()`) inside the inner loop? | Apply alloc-avoidance (Pattern C) |

Consult `patterns.md` (same directory as this file) for before/after templates.

---

## Step 5 — Generate Rust code

### 5a. Create the crate (first run only)

Copy `.agents/skills/rustloops/templates/Cargo.toml` → `<crate>/Cargo.toml`.
Copy `.agents/skills/rustloops/templates/lib.rs`     → `<crate>/src/lib.rs`.

Edit `Cargo.toml`:
- Set `name = "<crate>"` (e.g. `poly_match_rs`).
- Set `version = "0.1.0"`.
- Keep all deps and the `[profile.release]` block unchanged.

Edit `lib.rs`:
- Replace `YOUR_MODULE_NAME` with the crate name.
- Add the translated function(s) (see patterns.md).
- Register each function in the `#[pymodule]` body.

### 5b. Subsequent runs (crate already exists)

Add new functions to the existing `lib.rs`; do not touch unrelated functions.

---

## Step 6 — Back up Python and rewire imports

1. Copy `<source_file>` → `.rustloops/backups/<module>.<YYYYMMDD-HHMMSS>.py`.
   Update `manifest.json` with the backup path.

2. In `<source_file>`, **at the top** (after existing stdlib/third-party imports), add:

```python
try:
    from <crate> import <fn1>, <fn2>   # rustloops: Rust acceleration
except ImportError:
    pass  # Rust extension not built; original functions remain
```

3. Remove (or comment out) the original Python function bodies **only** if the import
   above succeeded at module load time. Preferred pattern: keep originals as fallbacks
   by renaming them `_<name>_py` and guarding with the import:

```python
def _find_close_polygons_py(polygons, query, max_dist):
    ...  # original body

try:
    from poly_match_rs import find_close_polygons   # rustloops: Rust acceleration
except ImportError:
    find_close_polygons = _find_close_polygons_py
```

---

## Step 7 — Build

```
cd <crate> && maturin develop --release
```

If the build fails, diagnose the error, fix it, and retry. Common failures:

| Error | Fix |
|---|---|
| `E0308` type mismatch on array element | Check `f64` vs `f32`; use `.mapv(|x| x as f64)` on Python side if needed |
| `cannot borrow ... as mutable` | Wrap borrow in a tighter scope (see Pattern C) |
| Missing `linalg` feature | Ensure `ndarray-linalg = { ..., features = ["openblas-static"] }` |
| Linker error on macOS (`-lopenblas`) | Use `features = ["openblas-static"]` or `brew install openblas` |

---

## Step 8 — Verify and report

Run the test command 3 times, record mean, compare output signature to baseline.

**Output signature must match.** If it does not:
- Diff stdout to find the discrepancy.
- Check floating-point ordering (sort results before hashing if order is non-deterministic).
- Fix the Rust implementation and rebuild before reporting results.

Print a report. Use ANSI escape codes to color the baseline measurement red and
the after measurement green:

```
rustloops result for <fn> in <file>
────────────────────────────────────────
  baseline  : \e[31m<baseline_ms> ms\e[0m
  after     : \e[32m<after_ms> ms\e[0m
  speedup   : <multiplier>x
  output    : OK (signature matches)
────────────────────────────────────────
Next steps:
  - Pattern B: move Polygon to #[pyclass] for another 2-3x
  - Pattern C: eliminate inner-loop allocations for another 1.5x
  Run rustloops-compare to re-benchmark after further changes.
```

Append to `manifest.json` history:

```json
{ "ts": "<ISO8601>", "ms": <after_ms>, "note": "v1 naive" }
```

---

## Reference files

- **patterns.md** — Pattern A / B / C before-after templates, gotchas table
- **templates/Cargo.toml** — copy-ready Cargo manifest
- **templates/lib.rs** — `#[pymodule]` skeleton
- **templates/measure.py** — timing + output-signature harness; used as the base when the skill generates a measure.py for the user
