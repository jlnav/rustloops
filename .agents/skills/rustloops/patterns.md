# rustloops Pattern Cookbook

Four patterns for accelerating hot Python loops in Rust.
Each pattern has a Python-before and Rust-after snippet you can adapt directly.

- **Patterns A / B / C** are leveled: A (function wrap) → B (`#[pyclass]` data class)
  → C (allocation avoidance). Apply them in order for compounding speedups.
- **Pattern D** (rayon data parallelism) is *orthogonal*: it layers on top of A/C to
  spread an embarrassingly-parallel loop across CPU cores. Reach for it only after the
  serial Rust version is correct, and only when loop iterations are independent.

---

## Pattern A — Function wrap (naive translation, v1)

**When to use:** The hot function accepts numpy arrays and/or Python objects and returns
a list of Python objects. No persistent Rust state needed.

**Speedup ballpark:** 5–15x over pure Python for numeric loops.

### Python before

```python
import numpy as np

def find_close_polygons(
    polygon_list: list["Polygon"],
    query: np.ndarray,          # shape (2,) center point
    max_dist: float,
) -> list["Polygon"]:
    result = []
    for poly in polygon_list:
        dist = np.linalg.norm(poly.center - query)
        if dist < max_dist:
            result.append(poly)
    return result
```

### Rust after (`lib.rs`)

```rust
use numpy::{PyReadonlyArray1, PyReadonlyArray2};
use pyo3::prelude::*;
use pyo3::types::PyList;

/// Returns polygons whose center is within max_dist of query.
#[pyfunction]
fn find_close_polygons<'py>(
    py: Python<'py>,
    polygon_list: &Bound<'py, PyList>,
    query: PyReadonlyArray1<f64>,
    max_dist: f64,
) -> PyResult<Vec<PyObject>> {
    let query = query.as_array();
    let mut result = Vec::new();
    for poly in polygon_list.iter() {
        let center = poly.getattr("center")?.extract::<PyReadonlyArray1<f64>>()?;
        let center = center.as_array();
        let dist = ((center[0] - query[0]).powi(2) + (center[1] - query[1]).powi(2)).sqrt();
        if dist < max_dist {
            result.push(poly.into());
        }
    }
    Ok(result)
}
```

**Key points:**
- `PyReadonlyArray1<f64>` — 1-D numpy array, read-only borrow; call `.as_array()` to get an `ArrayView1<f64>`.
- `poly.getattr("center")?` — fetch a Python attribute by name.
- `.extract::<T>()?` — convert a Python object to a Rust type.
- Return `Vec<PyObject>` (heap-allocated list of Python objects).
- The function must be registered in the `#[pymodule]` block.

**`Cargo.toml` deps needed:**
```toml
numpy = "0.29"
ndarray = "0.16"
```

---

## Pattern B — `#[pyclass]` data class (v2)

**When to use:** A Python data class (fields accessed in the inner loop) is the main
bottleneck. Moving it to Rust eliminates attribute lookup overhead and enables
zero-copy array access.

**Speedup ballpark:** additional 2–4x on top of Pattern A.

### Python before

```python
class Polygon:
    def __init__(self, x: np.ndarray, y: np.ndarray) -> None:
        self.x = x
        self.y = y
        self.center = np.array([x.mean(), y.mean()])

    def area(self) -> float:
        # Shoelace formula
        return 0.5 * abs(
            np.dot(self.x, np.roll(self.y, 1))
            - np.dot(self.y, np.roll(self.x, 1))
        )
```

### Rust after (`lib.rs`)

```rust
use numpy::{PyArray1, PyReadonlyArray1, ToPyArray};
use pyo3::prelude::*;
use ndarray::Array1;

#[pyclass(subclass)]   // subclass = Python can extend it
pub struct Polygon {
    x: Array1<f64>,
    y: Array1<f64>,
    #[pyo3(get)]       // exposes self.center to Python
    center: Py<PyArray1<f64>>,
}

#[pymethods]
impl Polygon {
    #[new]
    fn new(
        py: Python<'_>,
        x: PyReadonlyArray1<f64>,
        y: PyReadonlyArray1<f64>,
    ) -> Self {
        let x = x.as_array().to_owned();
        let y = y.as_array().to_owned();
        let cx = x.mean().unwrap_or(0.0);
        let cy = y.mean().unwrap_or(0.0);
        let center = Array1::from(vec![cx, cy]).to_pyarray(py).into();
        Polygon { x, y, center }
    }

    /// Read-only view of x as a numpy array.
    fn x<'py>(&self, py: Python<'py>) -> &Bound<'py, PyArray1<f64>> {
        self.x.to_pyarray(py)
    }

    /// Shoelace area.
    fn area(&self) -> f64 {
        let n = self.x.len();
        let mut acc = 0.0f64;
        for i in 0..n {
            let j = (i + 1) % n;
            acc += self.x[i] * self.y[j];
            acc -= self.y[i] * self.x[j];
        }
        0.5 * acc.abs()
    }
}
```

### Python compatibility shim

```python
# Keep Python subclass for any remaining pure-Python methods:
from poly_match_rs import Polygon as _PolygonRs

class Polygon(_PolygonRs):
    pass          # add any Python-only methods here
```

**Key points:**
- `#[pyo3(get)]` exposes a field as a read-only Python property.
- `#[pyo3(subclass)]` lets Python inherit from the Rust class.
- `Py<PyArray1<f64>>` is a reference-counted handle; cloning it is O(1) (no array copy).
- `to_pyarray(py)` converts an `Array1` to a numpy array.

**`Cargo.toml` deps needed:**
```toml
numpy = "0.29"
ndarray = "0.16"
```

---

## Pattern C — Allocation avoidance (v3)

**When to use:** The inner loop allocates `Vec` or calls `.to_owned()` on arrays
that are only read. Eliminate these to cut GC pressure and cache thrash.

**Speedup ballpark:** additional 1.5–2x on top of Pattern B.

### Before (Pattern A/B inner loop)

```rust
// BAD: allocates a new Vec on every iteration
let center = poly.getattr("center")?.extract::<PyReadonlyArray1<f64>>()?;
let center = center.as_array().to_owned();   // <-- heap alloc every loop
let dist = ((center[0] - query[0]).powi(2) + (center[1] - query[1]).powi(2)).sqrt();
```

### After (borrow-scoped, no alloc)

```rust
// GOOD: borrow the array view; no heap alloc
let center_obj = poly.getattr("center")?;
let center = center_obj.extract::<PyReadonlyArray1<f64>>()?;  // borrow
let cv = center.as_array();  // ArrayView1 — zero-copy
let dist = ((cv[0] - query[0]).powi(2) + (cv[1] - query[1]).powi(2)).sqrt();
// `center` and `center_obj` drop here, releasing the borrow
```

**Tricks:**
- Scope the `extract` + `as_array` tightly so the borrow ends before any mutation.
- Prefer manual math (`cv[0] - qv[0]`) over `&cv - &qv` to avoid intermediate array allocation.
- If you need to store results, accumulate `Py<PyObject>` (refcount clone, O(1)) not cloned arrays.

**`Cargo.toml` deps needed:** same as Pattern A (no additional deps for this pattern).

---

## Pattern D — Data parallelism with rayon (v4)

**When to use:** The hot loop is *embarrassingly parallel* — each iteration is
independent and can run in any order. Layer this on top of a working Pattern A/C
translation to spread iterations across all CPU cores.

**Speedup ballpark:** near-linear in core count on top of A/C (e.g. 4–8x on a typical
laptop), heavily dependent on workload size — tiny loops lose to thread overhead.

### Is the loop actually embarrassingly parallel?

Check every box before parallelizing. If any fails, the loop needs a rewrite first
(see the N-body caveat below) or is not a candidate for Pattern D:

- [ ] Each iteration writes to a **disjoint** output slot (e.g. its own row/pixel/index).
- [ ] Iterations only **read** shared inputs; they never mutate shared state.
- [ ] There is **no cross-iteration dependency** (iteration `i` does not depend on `i-1`).
- [ ] No accumulation into a shared variable (or the accumulation is a reduction —
      use `.par_iter().sum()` / `.reduce(...)` instead of a shared `+=`).

### Python before (mandelbrot escape-time, per-row loop)

```python
def escape_counts(width, height, max_iter=200,
                  x_min=-2.5, x_max=1.0, y_min=-1.25, y_max=1.25):
    counts = np.empty((height, width), dtype=np.uint32)
    x_step = (x_max - x_min) / width
    y_step = (y_max - y_min) / height
    for row in range(height):            # <-- rows are independent
        ci = y_min + row * y_step
        for col in range(width):
            cr = x_min + col * x_step
            zr = zi = 0.0
            n = 0
            while n < max_iter:
                zr2, zi2 = zr * zr, zi * zi
                if zr2 + zi2 > 4.0:
                    break
                zi = 2.0 * zr * zi + ci
                zr = zr2 - zi2 + cr
                n += 1
            counts[row, col] = n
    return counts
```

### Rust after (`lib.rs`) — serial inner loop, parallel over rows

```rust
use numpy::{PyArray2, ToPyArray};
use ndarray::Array2;
use pyo3::prelude::*;
use rayon::prelude::*;

#[pyfunction]
#[pyo3(signature = (width, height, max_iter=200,
                    x_min=-2.5, x_max=1.0, y_min=-1.25, y_max=1.25))]
fn escape_counts<'py>(
    py: Python<'py>,
    width: usize,
    height: usize,
    max_iter: u32,
    x_min: f64,
    x_max: f64,
    y_min: f64,
    y_max: f64,
) -> Bound<'py, PyArray2<u32>> {
    let x_step = (x_max - x_min) / width as f64;
    let y_step = (y_max - y_min) / height as f64;

    // Flat output buffer; each row is a disjoint chunk of `width` elements.
    let mut counts = vec![0u32; width * height];

    // Release the GIL so rayon threads run truly in parallel, then split the
    // buffer into per-row chunks and process each row on its own thread.
    py.allow_threads(|| {
        counts
            .par_chunks_mut(width)          // one &mut [u32] per row — disjoint
            .enumerate()
            .for_each(|(row, row_buf)| {
                let ci = y_min + row as f64 * y_step;
                for (col, slot) in row_buf.iter_mut().enumerate() {
                    let cr = x_min + col as f64 * x_step;
                    let (mut zr, mut zi) = (0.0f64, 0.0f64);
                    let mut n = 0u32;
                    while n < max_iter {
                        let (zr2, zi2) = (zr * zr, zi * zi);
                        if zr2 + zi2 > 4.0 {
                            break;
                        }
                        zi = 2.0 * zr * zi + ci;
                        zr = zr2 - zi2 + cr;
                        n += 1;
                    }
                    *slot = n;
                }
            });
    });

    Array2::from_shape_vec((height, width), counts)
        .expect("shape matches buffer length")
        .to_pyarray(py)
}
```

**Key points:**
- `use rayon::prelude::*;` brings the parallel iterator methods into scope.
- `par_chunks_mut(width)` splits the flat buffer into non-overlapping `&mut [u32]`
  slices, one per row. Because the slices are disjoint, Rust's borrow checker proves
  the parallel writes are data-race-free **at compile time** — no `unsafe` needed.
- Swapping `.iter()`→`.par_iter()` or `.chunks_mut()`→`.par_chunks_mut()` is usually
  the entire change: keep the inner loop body identical to the serial version.
- Wrap the parallel region in `py.allow_threads(|| { ... })` to **release the GIL**.
  Without this, only one rayon thread can hold the GIL and you get no speedup. Do all
  Python object access *outside* the `allow_threads` closure.
- For a shared accumulation (a sum, min/max, etc.) do **not** use `par_chunks_mut` with
  a shared variable — use a reduction: `(0..n).into_par_iter().map(...).sum()` or
  `.reduce(|| identity, |a, b| combine(a, b))`.

### Caveat: not every loop is parallel as written (the N-body case)

The N-body `step()` inner loop applies a *symmetric* update:

```python
acc[i] += mass[j] * a      # writes body i
acc[j] -= mass[i] * a      # AND writes body j  <-- conflict under parallelism
```

Parallelizing the outer `i` loop directly is a data race: two threads can write the same
`acc[j]`. To make it Pattern-D-safe, **drop the `j > i` symmetry** and compute each
body's full acceleration independently (each thread writes only its own `acc[i]`):

```rust
acc.par_chunks_mut(3).enumerate().for_each(|(i, ai)| {
    for j in 0..n {
        if i == j { continue; }
        // ... compute pull of j on i, write only ai[0..3] ...
    }
});
```

This does ~2x the arithmetic (no reuse of the symmetric pair) but removes the write
conflict, and the parallel speedup more than pays for it. **Lesson:** when iterations
write to shared slots, transform the loop to make each iteration's writes disjoint
*before* reaching for rayon.

### Benchmarking note

Parallel timings are noisier than serial ones (thread scheduling, turbo boost, other
processes). Take the median of several runs, and control thread count with the
`RAYON_NUM_THREADS` environment variable when comparing — e.g. `RAYON_NUM_THREADS=1`
isolates the serial cost, higher values show scaling.

**`Cargo.toml` deps needed:**
```toml
rayon = "1"
```

---

## Gotchas table

| Symptom | Cause | Fix |
|---|---|---|
| Rust is slower than Python | Built without `--release` | `maturin develop --release` |
| `E0308` type mismatch on array | Python sends `float32`, Rust expects `f64` | Cast on Python side: `arr.astype(np.float64)` |
| `cannot find type PyReadonlyArray1` | Missing `use numpy::PyReadonlyArray1` | Add the use statement |
| `PyObject` vs `Py<T>` confusion | `PyObject = Py<PyAny>` — use `Py<PyArray1<f64>>` for typed handles | Use `Py<PyAny>` when type is unknown |
| Missing GIL token | Function needs `py: Python<'_>` to call back into Python | Add `py` as first arg, annotate lifetime |
| `ndarray-linalg` linker error (macOS) | OpenBLAS not found | Use `features = ["openblas-static"]` or `brew install openblas && OPENBLAS_PATH=$(brew --prefix openblas) maturin develop --release` |
| Output signature mismatch | Floating-point ordering or precision difference | Sort results before hashing; use `f64` throughout |
| `ImportError` at runtime | Extension not installed or stale `.so` | Re-run `maturin develop --release` in the crate dir |
| `pyo3` version mismatch with `numpy` crate | `numpy 0.29` requires `pyo3 0.29.*` | Pin both to same minor: `pyo3 = "0.29"`, `numpy = "0.29"` |
| Pattern D: no speedup from rayon | GIL still held during the parallel loop | Wrap the parallel region in `py.allow_threads(\|\| { ... })` |
| Pattern D: `cannot borrow ... as mutable more than once` | Trying to share one mutable buffer across threads | Split into disjoint slices with `par_chunks_mut` / `par_iter_mut` |
| Pattern D: results differ per run | Shared `+=` accumulation across threads (race) | Use a reduction (`.sum()`, `.reduce(...)`) instead of a shared variable |
| Pattern D: slower than serial | Loop too small; thread overhead dominates | Only parallelize when the workload is large; fall back to serial for small N |

---

## Cargo.toml dependency quick-reference

```toml
[dependencies]
pyo3    = { version = "0.29", features = ["extension-module"] }
numpy   = "0.29"
ndarray = "0.16"

# Only if you need linear algebra (norm, solve, etc.):
ndarray-linalg = { version = "0.16", features = ["openblas-static"] }

# Only for Pattern D (data parallelism across CPU cores):
rayon = "1"
```

> **Version alignment rule:** `numpy` crate minor must equal `pyo3` minor.
> When in doubt, use the latest matching pair from crates.io.

---

## `#[pymodule]` registration template

```rust
#[pymodule]
fn YOUR_MODULE_NAME(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(find_close_polygons, m)?)?;
    m.add_class::<Polygon>()?;
    Ok(())
}
```

Every `#[pyfunction]` and `#[pyclass]` must be registered here or Python will not see it.
