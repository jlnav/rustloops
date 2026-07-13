# rustloops Pattern Cookbook

Three leveled patterns derived from the poly-match worked example.
Each pattern has a Python-before and Rust-after snippet you can adapt directly.

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
numpy = "0.22"
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
numpy = "0.22"
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
| `pyo3` version mismatch with `numpy` crate | `numpy 0.22` requires `pyo3 0.22.*` | Pin both to same minor: `pyo3 = "0.22"`, `numpy = "0.22"` |

---

## Cargo.toml dependency quick-reference

```toml
[dependencies]
pyo3    = { version = "0.22", features = ["extension-module"] }
numpy   = "0.22"
ndarray = "0.16"

# Only if you need linear algebra (norm, solve, etc.):
ndarray-linalg = { version = "0.16", features = ["openblas-static"] }
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
