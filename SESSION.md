## User

We're going to be creating a set of agent SKILLs for auto-translating/wrapping common python+numpy+scipy or other numerical library loops and operations into rust.

Inspired by this blog post: https://ohadravid.github.io/posts/2023-03-rusty-python/

When the user invokes the skill, rustloops, the agent needs to ask the user for the relevant file, the relevant functions or loops (by line-number), and a script to run to test the changes.

The agent should then perform a "before" run and get timing information, then "after" and display a difference.

We'll also want to draft a README.md to help guide users.

One skill should wrap/translate the loops. One skill can re-run the comparision. One skill should unwrap/un-translate the loops.

Any questions. We're still developing this plan.

## Assistant

Let me research the inspiration blog post and understand the existing project structure before drafting a plan.

**Tool Call: https://ohadravid.github.io/posts/2023-03-rusty-python/ (text/html; charset=utf-8)**
Status: Completed

Making Python 100x faster with less than 100 lines of Rust

[Tea and Bits](https://ohadravid.github.io/)

[Home](https://ohadravid.github.io/)

[Posts](https://ohadravid.github.io/posts/)

[Projects](https://ohadravid.github.io/projects/)

[Tags](https://ohadravid.github.io/tags/)

[](https://github.com/ohadravid)

[](https://ohadravid.github.io/index.xml)

theme

menu

-   [Home](https://ohadravid.github.io/)
-   [Posts](https://ohadravid.github.io/posts/)
-   [Projects](https://ohadravid.github.io/projects/)
-   [Tags](https://ohadravid.github.io/tags/)
-   [](https://github.com/ohadravid)
-   [](https://ohadravid.github.io/index.xml)
-   theme

# Making Python 100x faster with less than 100 lines of Rust

March 28, 2023

-   [rust](https://ohadravid.github.io/tags/rust)
-   [python](https://ohadravid.github.io/tags/python)
-   [performance](https://ohadravid.github.io/tags/performance)

*Update: I gave a talk on this topic at [P99 CONF 2023](https://youtu.be/Jb_kdYEXVw8?si=buwUwuDzz5oRn3KA) and at [PyCon IL 2024 (Hebrew)](https://youtu.be/Tc9MlJN2y04?si=iE_Af883R6ublqbR).*

A while ago at [$work](https://www.trigoretail.com/), we had a performance issue with one of our core Python libraries.

This particular library forms the backbone of our 3D processing pipeline. It’s a rather big and complex library which uses NumPy and other scientific Python packages to do a wide range of mathematical and geometrical operations.

Our system also has to work on-prem with limited CPU resources, and while at first it performed well, as the number of concurrent physical users grew we started running into problems and our system struggled to keep up with the load.

We came to the conclusion that we had to make our system at least 50 times faster to handle the increased workload, and we figured that Rust could help us achieve that.

Because the performance problems we encountered are pretty common, we can recreate & solve them right here, in a (not-so-short) article.

So grab a cup of tea (or coffee) and I’ll walk you through (a) the basic underlying problem and (b) a few iterations of optimizations we can apply to solve this problem.

If you want to jump straight to the final code, just to go to [the summary](#summary).

## Our running example

Let’s create a small library, which will exhibit our original performance issues (but does completely arbitrary work).

Imagine you have a list of polygons and a of list points, all in 2D. For business reasons, we want to “match” each point to a single polygon.

Our imaginary library is going to:

1.  Start with an initial list of points and polygons (all in 2D).
2.  For each point, find a much smaller subset of polygons that are closest to it, based on distance from the center.
3.  Out of those polygons, select the “best” one (we are going to use “smallest area” as “best”).

In code, that’s going to look like this (The full code can be found [here](https://github.com/ohadravid/poly-match)):

```python
from typing import List, Tuple
import numpy as np
from dataclasses import dataclass
from functools import cached_property

Point = np.array

@dataclass
class Polygon:
    x: np.array
    y: np.array

    @cached_property
    def center(self) -> Point: ...
    def area(self) -> float: ...

def find_close_polygons(polygon_subset: List[Polygon], point: Point, max_dist: float) -> List[Polygon]:
    ...

def select_best_polygon(polygon_sets: List[Tuple[Point, List[Polygon]]]) -> List[Tuple[Point, Polygon]]:
    ...

def main(polygons: List[Polygon], points: np.ndarray) -> List[Tuple[Point, Polygon]]:
    ...
```

**The key difficulty (performance wise) is this mix of Python objects and numpy arrays.**

We are going to analyze this in depth in a minute.

It’s worth noting that converting parts of / everything to [vectorized](https://numpy.org/doc/stable/glossary.html#term-vectorization) numpy might be possible for this toy library, but will be nearly impossible for the real library while making the code much less readable and modifiable, and the gains are going to be limited ([here’s a partially vertorized version](https://github.com/ohadravid/poly-match/blob/main/poly_match_v1_5_vectorized.py), which is faster but far from the results we are going to achieve).

Also, using any JIT-based tricks (PyPy / numba) results in very small gains (as we will measure, just to make sure).

## Why not just Rewrite It (all) In Rust™?

As compelling as a complete rewrite was, it had a few problems:

1.  The library was already using numpy for a lot of its calculations, so why should we expect Rust to be better?
2.  It is big and complex and very business critical and highly algorithmic, so that would take ~months of work, and our poor on-prem server is dying **today**.
3.  A bunch of friendly researchers are actively working on said library, implementing better algorithms and doing a lot of experiments. They aren’t going to be very happy to learn a new programming language, waiting for things to compile and fighting with the borrow checker. They would appreciate us not moving their cheese too far.

## Dipping our toes

It is time to introduce our friend the profiler.

Python has a built in Profiler (`cProfile`), but in this case it’s not really the right tool for the job:

1.  It’ll introduce a lot of overhead to all the Python code, and none for native code, so our results might be biased.
2.  We won’t be able to see into native frames, meaning we aren’t going to be able to see into our Rust code.

We are going to use `py-spy` ([GitHub](https://github.com/benfred/py-spy)).

`py-spy` is a [sampling profiler](https://en.wikipedia.org/wiki/Profiling_\(computer_programming\)#Statistical_profilers) which can see into native frames.

They also mercifully publish pre-built wheels to pypi, so we can just `pip install py-spy` and get to work.

We also need something to measure.

```python
# measure.py
import time
import poly_match
import os
  
# Reduce noise, actually improve perf in our case.
os.environ["OPENBLAS_NUM_THREADS"] = "1"

polygons, points = poly_match.generate_example()

# We are going to increase this as the code gets faster and faster.
NUM_ITER = 10

t0 = time.perf_counter()
for _ in range(NUM_ITER):
    poly_match.main(polygons, points)
t1 = time.perf_counter()

took = (t1 - t0) / NUM_ITER
print(f"Took and avg of {took * 1000:.2f}ms per iteration")
```

It’s not very scientific, but it’s going to take us *very* far.

> “Good benchmarking is hard. Having said that, do not stress too much about having a perfect benchmarking setup, particularly when you start optimizing a program.”
> 
> ~ Nicholas Nethercote, in [“The Rust Performance Book”](https://nnethercote.github.io/perf-book/benchmarking.html)

Running this script will give us our baseline:

```bash
$ python measure.py
Took an avg of 293.41ms per iteration
```

For the original library, we used 50 different examples to make sure all cases are covered.

This matched the overall system perf, meaning we can start working on crushing this number.

Side note: We can also measure using PyPy (we’ll also add a warmup to allow the JIT to do its magic).

```bash
$ conda create -n pypyenv -c conda-forge pypy numpy && conda activate pypyenv
$ pypy measure_with_warmup.py
Took an avg of 1495.81ms per iteration
```

## Measure first

So, let’s find out what is so slow here.

```bash
$ py-spy record --native -o profile.svg -- python measure.py
py-spy> Sampling process 100 times a second. Press Control-C to exit.

Took an avg of 365.43ms per iteration

py-spy> Stopped sampling because process exited
py-spy> Wrote flamegraph data to 'profile.svg'. Samples: 391 Errors: 0
```

Already, we can see that the overhead is pretty small. Just for comparison, using `cProfile` we get this:

```bash
$ python -m cProfile measure.py
Took an avg of 546.47ms per iteration
         7551778 function calls (7409483 primitive calls) in 7.806 seconds
         ...
```

We get this nice, reddish graph called a [flamegraph](https://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html):

![Profiler output for the first version of the code](/2023-03-rusty-python-profs/poly_match_v1.svg)

Each box is a function, and we can see the relative time we spend in each function, including the functions it is calling to (going down the graph/stack). Try clicking on a the `norm` box to zoom into it.

Here, the main takeaways are:

1.  The vast majority of time is spent in `find_close_polygons`.
2.  Most of that time is spend doing `norm`, which is a numpy function.

So, let’s have a look at `find_close_polygons`:

```python
def find_close_polygons(
    polygon_subset: List[Polygon], point: np.array, max_dist: float
) -> List[Polygon]:
    close_polygons = []
    for poly in polygon_subset:
        if np.linalg.norm(poly.center - point) < max_dist:
            close_polygons.append(poly)

    return close_polygons
```

We are going to rewrite this function in Rust.

Before diving into the details, it’s important to notice a few things here:

1.  This function accepts & returns complex objects (`Polygon`, `np.array`).
2.  The size of the objects is non-trivial (so copying stuff might cost us).
3.  This function is called “a lot” (so overhead we introduce is probably going to matter).

## My first Rust module

`pyo3` is a crate for interacting between Python and Rust. It has exceptionally good documentation, and they explain the basic setup [here](https://pyo3.rs/v0.18.1/#using-rust-from-python).

We are going to call our crate `poly_match_rs`, and add function called `find_close_polygons`.

```bash
mkdir poly_match_rs && cd "$_"
pip install maturin
maturin init --bindings pyo3
maturin develop
```

Starting out, our crate is going to look like this:

```rust
use pyo3::prelude::*;

#[pyfunction]
fn find_close_polygons() -> PyResult<()> {
    Ok(())
}

#[pymodule]
fn poly_match_rs(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(find_close_polygons, m)?)?;
    Ok(())
}
```

We also need to remember to execute `maturin develop` every time we change the Rust library.

And thats it! Let’s call our new function and see what happens.

```python
>>> poly_match_rs.find_close_polygons(polygons, point, max_dist)
E TypeError: poly_match_rs.poly_match_rs.find_close_polygons() takes no arguments (3 given)
```

## v1 - A naive Rust translation

We’ll start with matching the expected API.

PyO3 is pretty smart about Python to Rust conversions, so that’s going to be pretty easy:

```rust
#[pyfunction]
fn find_close_polygons(polygons: Vec<PyObject>, point: PyObject, max_dist: f64) -> PyResult<Vec<PyObject>> {
    Ok(vec![])
}
```

`PyObject` is (as the name suggest) a generic “anything goes” Python object. We’ll try to interact with it in a bit.

This should make the program run (albeit incorrectly).

I’m going to just copy and paste the original Python function, and fix the syntax.

```rust
#[pyfunction]
fn find_close_polygons(polygons: Vec<PyObject>, point: PyObject, max_dist: f64) -> PyResult<Vec<PyObject>> {
    let mut close_polygons = vec![];
    
    for poly in polygons {
        if norm(poly.center - point) < max_dist {
            close_polygons.push(poly)
        }
    }
    
    Ok(close_polygons)
}
```

Cool, but this won’t compile:

```bash
% maturin develop
...

error[E0609]: no field `center` on type `Py<PyAny>`
 --> src/lib.rs:8:22
  |
8 |         if norm(poly.center - point) < max_dist {
  |                      ^^^^^^ unknown field


error[E0425]: cannot find function `norm` in this scope
 --> src/lib.rs:8:12
  |
8 |         if norm(poly.center - point) < max_dist {
  |            ^^^^ not found in this scope


error: aborting due to 2 previous errors ] 58/59: poly_match_rs
```

We need three crates to implement our function:

```toml
# For Rust-native array operations.
ndarray = "0.15"

# For a `norm` function for arrays.
ndarray-linalg = "0.16"  

# For accessing numpy-created objects, based on `ndarray`.
numpy = "0.18"
```

First, lets turn the opaque and generic `point: PyObject` into something we can work with.

Just like we asked PyO3 for a “`Vec` of `PyObject`s”, we can ask for a numpy-array, and it’ll auto-convert the argument for us.

```rust
use numpy::PyReadonlyArray1;

#[pyfunction]
fn find_close_polygons(
    // An object which says "I have the GIL", so we can access Python-managed memory.
    py: Python<'_>,
    polygons: Vec<PyObject>,
    // A reference to a numpy array we will be able to access.
    point: PyReadonlyArray1<f64>,
    max_dist: f64,
) -> PyResult<Vec<PyObject>> {
    // Convert to `ndarray::ArrayView1`, a fully operational native array.
    let point = point.as_array();
    ...
}
```

Because `point` is now an `ArrayView1`, we can actually use it. For example:

```rust
// Make the `norm` function available.
use ndarray_linalg::Norm;

assert_eq!((point.to_owned() - point).norm(), 0.);
```

Now we just need to get the center of each polygon, and “cast” it to an `ArrayView1`.

In PyO3, this looks like this:

```rust
let center = poly
  .getattr(py, "center")?                 // Python-style getattr, requires a GIL token (`py`).
  .extract::<PyReadonlyArray1<f64>>(py)?  // Tell PyO3 what to convert the result to.
  .as_array()                             // Like `point` before.
  .to_owned();                            // We need one of the sides of the `-` to be "owned".
```

It’s a bit of a mouthful, but overall the result is a pretty clear line-to-line translation of the original code:

```rust
 1use pyo3::prelude::*;
 2
 3use ndarray_linalg::Norm;
 4use numpy::PyReadonlyArray1;
 5
 6#[pyfunction]
 7fn find_close_polygons(
 8    py: Python<'_>,
 9    polygons: Vec<PyObject>,
10    point: PyReadonlyArray1<f64>,
11    max_dist: f64,
12) -> PyResult<Vec<PyObject>> {
13    let mut close_polygons = vec![];
14    let point = point.as_array();
15    for poly in polygons {
16        let center = poly
17            .getattr(py, "center")?
18            .extract::<PyReadonlyArray1<f64>>(py)?
19            .as_array()
20            .to_owned();
21
22        if (center - point).norm() < max_dist {
23            close_polygons.push(poly)
24        }
25    }
26
27    Ok(close_polygons)
28}
```

vs the original:

```python
def find_close_polygons(
    polygon_subset: List[Polygon], point: np.array, max_dist: float
) -> List[Polygon]:
    close_polygons = []
    for poly in polygon_subset:
        if np.linalg.norm(poly.center - point) < max_dist:
            close_polygons.append(poly)

    return close_polygons
```

We expect this version to have some advantage over the original function, but how much?

```bash
$ (cd ./poly_match_rs/ && maturin develop)
$ python measure.py
Took an avg of 609.46ms per iteration 
```

So.. Is Rust just super slow? No! We just forgot to ask for speed! If we run with `maturin develop --release` we get much better results:

```bash
$ (cd ./poly_match_rs/ && maturin develop --release)
$ python measure.py
Took an avg of 23.44ms per iteration
```

Now *that* is a nice speedup!

We also want to see into our native code, so we are going to enable debug symbols in release. While we are at it, we might as well ask for *maximum speed*.

```toml
# added to Cargo.toml
[profile.release]
debug = true       # Debug symbols for our profiler.
lto = true         # Link-time optimization.
codegen-units = 1  # Slower compilation but faster code. 
```

## v2 - Rewrite even more in Rust

Now, using the `--native` flag in `py-spy` is going to show us both Python and our new native code.

Running `py-spy` again

```bash
$ py-spy record --native -o profile.svg -- python measure.py
py-spy> Sampling process 100 times a second. Press Control-C to exit.
```

we get this flamegraph (non-red colors are added to so we can refer to them):

![Profiler output for the naive Rust version](/2023-03-rusty-python-profs/poly_match_v2_riir.svg)

Looking at the profiler output, we can see a few interesting things:

1.  The relative size of `find_close_polygons::...::trampoline` (the symbol Python directly calls) and `__pyfunction_find_close_polygons` (our actual implementation).
    -   Hovering, they are 95% vs 88% of samples, so the overhead is pretty small.
2.  The actual logic (`if (center - point).norm() < max_dist { ... }`) which is `lib_v1.rs:22` (very small box on the right), is about 9% of the total runtime.
    -   So x10 improvement should still be possible!
3.  Most of the time is spent in `lib_v1.rs:16`, which is `poly.getattr(...).extract(...)` and if we zoom in we can see is really just `getattr` and getting the underlying array using `as_array`.

The conclusion here is that we need to focus on solving the 3rd point, and the way to do that is to Rewrite `Polygon` in Rust.

Let’s look at our target:

```python
@dataclass
class Polygon:
    x: np.array
    y: np.array
    _area: float = None

    @cached_property
    def center(self) -> np.array:
        centroid = np.array([self.x, self.y]).mean(axis=1)
        return centroid

    def area(self) -> float:
        if self._area is None:
            self._area = 0.5 * np.abs(
                np.dot(self.x, np.roll(self.y, 1)) - np.dot(self.y, np.roll(self.x, 1))
            )
        return self._area
```

We’ll want to keep the existing API as much as possible, but we don’t really need `area` to be that fast (for now).

The actual class might have additional complex stuff, like a `merge` method which uses `ConvexHull` from `scipy.spatial`.

To cut costs (and limit the scope of this already long article), we will only move the “core” functionality of `Polygon` to Rust, and subclass that from Python to implement the rest of the API.

Our `struct` is going to look like this:

```rust
// `Array1` is a 1d array, and the `numpy` crate will play nicely with it.
use ndarray::Array1;

// `subclass` tells PyO3 to allow subclassing this in Python.
#[pyclass(subclass)]
struct Polygon {
    x: Array1<f64>,
    y: Array1<f64>,
    center: Array1<f64>,
}
```

Now we need to actually implement it. We want to expose `poly.{x, y, center}` as:

1.  Properties.
2.  numpy Arrays.

We also need a constructor so Python can create new `Polygon`s.

```rust
use numpy::{PyArray1, PyReadonlyArray1, ToPyArray};

#[pymethods]
impl Polygon {
    #[new]
    fn new(x: PyReadonlyArray1<f64>, y: PyReadonlyArray1<f64>) -> Polygon {
        let x = x.as_array();
        let y = y.as_array();
        let center = Array1::from_vec(vec![x.mean().unwrap(), y.mean().unwrap()]);

        Polygon {
            x: x.to_owned(),
            y: y.to_owned(),
            center,
        }
    }
    
    // the `Py<..>` in the return type is a way of saying "an Object owned by Python".
    #[getter]               
    fn x(&self, py: Python<'_>) -> PyResult<Py<PyArray1<f64>>> {
        Ok(self.x.to_pyarray(py).to_owned()) // Create a Python-owned, numpy version of `x`.
    }

    // Same for `y` and `center`.
}
```

We need to add our new struct as a class to the module:

```rust
#[pymodule]
fn poly_match_rs(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<Polygon>()?; // new.
    m.add_function(wrap_pyfunction!(find_close_polygons, m)?)?;
    Ok(())
}
```

And now we can update the Python code to use it:

```python
class Polygon(poly_match_rs.Polygon):
    _area: float = None

    def area(self) -> float:
        ...
```

We can compile it and it’ll actually work, but it’ll be much slower! (Remember that `x`, `y`, and `center` will now need to create a new numpy array **on each access**).

To actually improve performance, we need to `extract` our original Rust-based `Polygon` from the list of Python-`Polygon`s.

PyO3 is very flexible with this type of operation, so there are a few ways we could do it. One limit we have is that we also need to return Python-`Polygon`s, and we don’t want to do any cloning of the actual data.

It’s possible to manually call `.extract::<Polygon>(py)?` on each `PyObject`s, but we ask PyO3 to give us `Py<Polygon>` directly.

This is a reference to a Python-owned object, which we expect to contain an instance (or a subclass, in our case) of a native `pyclass` struct.

```rust
45#[pyfunction]
46fn find_close_polygons(
47    py: Python<'_>,
48    polygons: Vec<Py<Polygon>>,             // References to Python-owned objects.
49    point: PyReadonlyArray1<f64>,
50    max_dist: f64,
51) -> PyResult<Vec<Py<Polygon>>> {           // Return the same `Py` references, unmodified.
52    let mut close_polygons = vec![];
53    let point = point.as_array();
54    for poly in polygons {
55        let center = poly.borrow(py).center // Need to use the GIL (`py`) to borrow the underlying `Polygon`.
56            .to_owned();
57
58        if (center - point).norm() < max_dist {
59            close_polygons.push(poly)
60        }
61    }
62
63    Ok(close_polygons)
64}
```

Let’s see what we get using this code:

```bash
$ python measure.py
Took an avg of 6.29ms per iteration
```

We are nearly there! Just x2 to go!

## v3 - Avoid allocations

Let’s fire up the profiler one more time.

![Profiler output for the Polygon Rust version](/2023-03-rusty-python-profs/poly_match_v3_riir_polygon.svg)

1.  We start to see `select_best_polygon`, which now calls some Rust code (when it gets the `x` & `y` vectors)
    -   We could fix that, but that’s a very small potential improvement (maybe 10%)
2.  We see we spend about 20% the time on `extract_argument` (under `lib_v2.rs:48`), so we are still paying quite a lot on overhead!
    -   But most of the time is in `PyIterator::next` and `PyTypeInfo::is_type_of`, which aren’t easy to fix.
3.  We see a bunch of time spent allocating stuff!
    -   `lib_v2.rs:58` is our `if`, and we see `drop_in_place` and `to_owned`.
    -   The actual line is about 35% of the overall time, which is a lot more than we expect: this should be the “fast bit” with all the data in place.

Let’s tackle the last point.

This our problematic snippet:

```rust
let center = poly.borrow(py).center
    .to_owned();

if (center - point).norm() < max_dist { ... } 
```

What we want is to avoid that `to_owned`. But we need an owned object for `norm`, so we’ll have to implement that manually.

(The reason we can improve on `ndarray` here is that we know that our array is actually just 2 `f32`s).

This would look like this:

```rust
use ndarray_linalg::Scalar;

let center = &poly.as_ref(py).borrow().center;

if ((center[0] - point[0]).square() + (center[1] - point[1]).square()).sqrt() < max_dist {
    close_polygons.push(poly)
}
```

But, alas, the borrow checker is unhappy with us:

```rust
error[E0505]: cannot move out of `poly` because it is borrowed
  --> src/lib.rs:58:33
   |
55 |         let center = &poly.as_ref(py).borrow().center;
   |                       ------------------------
   |                       |
   |                       borrow of `poly` occurs here
   |                       a temporary with access to the borrow is created here ...
...
58 |             close_polygons.push(poly);
   |                                 ^^^^ move out of `poly` occurs here
59 |         }
60 |     }
   |     - ... and the borrow might be used here, when that temporary is dropped and runs the `Drop` code for type `PyRef`
```

As usual, the borrow checker is correct: we are doing memory crimes.

The simpler fix is to *Just Clone*, and `close_polygons.push(poly.clone())` compiles.

This is actually a very cheap clone, because we only `incr` the reference count of the Python object.

However, in this case we can also shorten the borrow by doing a classic Rust trick:

```rust
let norm = {
    let center = &poly.as_ref(py).borrow().center;

    ((center[0] - point[0]).square() + (center[1] - point[1]).square()).sqrt()
};

if norm < max_dist {
    close_polygons.push(poly)
}
```

Because `poly` is only borrowed in the inner scope, once we reach `close_polygons.push` the compiler can know that we no longer hold that reference, and will happily compile the new version.

And finally, we have

```bash
$ python measure.py
Took an avg of 2.90ms per iteration
```

Which is 100x improvement over the original code.

## Summary

We started out with this Python code:

```python
@dataclass
class Polygon:
    x: np.array
    y: np.array
    _area: float = None

    @cached_property
    def center(self) -> np.array:
        centroid = np.array([self.x, self.y]).mean(axis=1)
        return centroid

    def area(self) -> float:
        ...

def find_close_polygons(
    polygon_subset: List[Polygon], point: np.array, max_dist: float
) -> List[Polygon]:
    close_polygons = []
    for poly in polygon_subset:
        if np.linalg.norm(poly.center - point) < max_dist:
            close_polygons.append(poly)

    return close_polygons

# Rest of file (main, select_best_polygon).
```

We profiled it using `py-spy`, and even our most [naive, line-to-line translation](#v1-a-naive-rust-translation) of `find_close_polygons` resulted in more than x10 improvement.

We did a few profile-rewrite-measure iterations until we finally gained x100 improvement in runtime, while keeping the same API as the original library.

Version

Avg time per iteration (ms)

Multiplier

Baseline implementation (Python)

293.41

1x

Naive line-to-line Rust translation of `find_close_polygons`

23.44

12.50x

`Polygon` implementation in Rust

6.29

46.53x

Optimized allocation implementation in Rust

2.90

101.16x

The final python code looks like this

```python
import poly_match_rs
from poly_match_rs import find_close_polygons

class Polygon(poly_match_rs.Polygon):
    _area: float = None

    def area(self) -> float:
        ...

# Rest of file unchanged (main, select_best_polygon).
```

which calls this Rust code:

```rust
use pyo3::prelude::*;

use ndarray::Array1;
use ndarray_linalg::Scalar;
use numpy::{PyArray1, PyReadonlyArray1, ToPyArray};

#[pyclass(subclass)]
struct Polygon {
    x: Array1<f64>,
    y: Array1<f64>,
    center: Array1<f64>,
}

#[pymethods]
impl Polygon {
    #[new]
    fn new(x: PyReadonlyArray1<f64>, y: PyReadonlyArray1<f64>) -> Polygon {
        let x = x.as_array();
        let y = y.as_array();
        let center = Array1::from_vec(vec![x.mean().unwrap(), y.mean().unwrap()]);

        Polygon {
            x: x.to_owned(),
            y: y.to_owned(),
            center,
        }
    }

    #[getter]
    fn x(&self, py: Python<'_>) -> PyResult<Py<PyArray1<f64>>> {
        Ok(self.x.to_pyarray(py).to_owned())
    }

    // Same for `y` and `center`.
}

#[pyfunction]
fn find_close_polygons(
    py: Python<'_>,
    polygons: Vec<Py<Polygon>>,
    point: PyReadonlyArray1<f64>,
    max_dist: f64,
) -> PyResult<Vec<Py<Polygon>>> {
    let mut close_polygons = vec![];
    let point = point.as_array();
    for poly in polygons {
        let norm = {
            let center = &poly.as_ref(py).borrow().center;

            ((center[0] - point[0]).square() + (center[1] - point[1]).square()).sqrt()
        };

        if norm < max_dist {
            close_polygons.push(poly)
        }
    }

    Ok(close_polygons)
}

#[pymodule]
fn poly_match_rs(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<Polygon>()?;
    m.add_function(wrap_pyfunction!(find_close_polygons, m)?)?;
    Ok(())
}
```

## Takeaways

-   Rust (with the help of pyo3) unlocks true native performance for everyday Python code, with minimal compromises.
    
-   Python is a superb API for researchers, and crafting fast building blocks with Rust is an extremely powerful combination.
    
-   Profiling is super interesting, and it pushes you to truly understand everything that’s happening in your code.
    

And finally: computers are *crazy fast*. The next time you wait for something to complete, consider firing up a profiler, you might learn something new 🚀

-   -   [Our running example](#our-running-example)
    -   [Why not just Rewrite It (all) In Rust™?](#why-not-just-rewrite-it-all-in-rust)
    -   [Dipping our toes](#dipping-our-toes)
    -   [Measure first](#measure-first)
    -   [My first Rust module](#my-first-rust-module)
    -   [v1 - A naive Rust translation](#v1---a-naive-rust-translation)
    -   [v2 - Rewrite even more in Rust](#v2---rewrite-even-more-in-rust)
    -   [v3 - Avoid allocations](#v3---avoid-allocations)
    -   [Summary](#summary)
    -   [Takeaways](#takeaways)

© 2026 Made with ❤️ using [Gokarna](https://github.com/526avijitgupta/gokarna) [Teapot](https://icons8.com/icon/110035/teapot) icon by [Icons8](https://icons8.com)

**Tool Call: Users/jnavarro/software/rustloops**
Status: Completed

.gitattributes
.gitignore
.pixi/
AGENTS.md
pixi.lock
pixi.toml
PROMPT.md

**Tool Call: Loaded skill: customize-opencode**
Status: Completed

<skill_content name="customize-opencode">
# Skill: customize-opencode

<!--
  Built-in skill. Name and description are registered in code at
  packages/core/src/plugin/skill.ts
  and CUSTOMIZE_OPENCODE_SKILL_DESCRIPTION). The body below becomes the
  skill's content.
-->

# Customizing opencode

opencode validates its own config strictly and refuses to start when a field
is wrong. The shapes below cover the common surface area, but they are a
**summary, not the source of truth**.

## Full schema reference

The authoritative list of every config option — with field types, enums,
defaults, and descriptions — lives in the published JSON Schema:

**<https://opencode.ai/config.json>**

If a field is not documented in this skill, or you need to confirm an exact
shape before writing config, **fetch that URL and read the schema directly**
rather than guessing. opencode hard-fails on invalid config, so the cost of a
wrong shape is a broken startup.

Independently, every `opencode.json` should declare
`"$schema": "https://opencode.ai/config.json"` so the user's editor catches
mistakes as they type.

## Applying changes

Config is loaded once when opencode starts and is not hot-reloaded. After
saving changes to `opencode.json`, an agent file, a skill, a plugin, or any
other config-time file, **tell the user to quit and restart opencode** for
the changes to take effect. The running session will keep using the
already-loaded config until then.

## Where files live

| Scope                         | Path                                                                                                                      |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Project config                | `./opencode.json`, `./opencode.jsonc`, or `.opencode/opencode.json` (opencode walks up from the cwd to the worktree root) |
| Global config                 | `~/.config/opencode/opencode.json` (NOT `~/.opencode/`)                                                                   |
| Project agents                | `.opencode/agent/<name>.md` or `.opencode/agents/<name>.md`                                                               |
| Global agents                 | `~/.config/opencode/agent(s)/<name>.md`                                                                                   |
| Project commands              | `.opencode/command/<name>.md` or `.opencode/commands/<name>.md`                                                           |
| Global commands               | `~/.config/opencode/command(s)/<name>.md`                                                                                 |
| Project skills                | `.opencode/skill(s)/<name>/SKILL.md`                                                                                      |
| Global skills                 | `~/.config/opencode/skill(s)/<name>/SKILL.md`                                                                             |
| External skills (auto-loaded) | `~/.claude/skills/<name>/SKILL.md`, `~/.agents/skills/<name>/SKILL.md`                                                    |

Configs from each scope are deep-merged. Project overrides global. Unknown
top-level keys in `opencode.json` are rejected with `ConfigInvalidError`.

## opencode.json

Every field is optional.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "username": "string",
  "model": "provider/model-id",
  "small_model": "provider/model-id",
  "default_agent": "agent-name",
  "shell": "/bin/zsh",
  "logLevel": "DEBUG" | "INFO" | "WARN" | "ERROR",
  "share": "manual" | "auto" | "disabled",
  "autoupdate": true | false | "notify",
  "snapshot": true,
  "instructions": ["AGENTS.md", "docs/style.md"],

  "skills": {
    "paths": [".opencode/skills", "/abs/path/to/skills"],
    "urls": ["https://example.com/.well-known/skills/"]
  },

  "references": {
    "docs": {
      "path": "../docs",
      "description": "Use for product behavior and documentation conventions"
    },
    "sdk": {
      "repository": "owner/sdk",
      "branch": "main",
      "description": "Use for SDK implementation details",
      "hidden": true
    }
  },

  "agent": {
    "my-agent": {
      "model": "anthropic/claude-sonnet-4-6",
      "mode": "subagent",
      "description": "...",
      "permission": { "edit": "deny" }
    }
  },

  "command": {
    "deploy": { "description": "...", "template": "..." }
  },

  "provider": {
    "anthropic": { "options": { "apiKey": "..." } }
  },
  "disabled_providers": ["openai"],
  "enabled_providers": ["anthropic"],

  "mcp": {
    "playwright": {
      "type": "local",
      "command": ["npx", "-y", "@playwright/mcp"],
      "enabled": true,
      "env": {}
    },
    "remote-thing": {
      "type": "remote",
      "url": "https://...",
      "headers": { "Authorization": "Bearer ..." }
    }
  },

  "plugin": [
    "opencode-gemini-auth",
    "opencode-foo@1.2.3",
    "./local-plugin.ts",
    ["opencode-bar", { "option": "value" }]
  ],

  "permission": {
    "edit": "deny",
    "bash": { "git *": "allow", "*": "ask" }
  },

  "formatter": false,
  "lsp": false,

  "experimental": {
    "primary_tools": ["edit"],
    "mcp_timeout": 30000
  },

  "tool_output": { "max_lines": 200, "max_bytes": 8192 },

  "compaction": { "auto": true, "tail_turns": 15 }
}
```

Shape notes worth being explicit about:

- `model` always carries a provider prefix: `"anthropic/claude-sonnet-4-6"`.
- `skills` is an object with `paths` and/or `urls`, not an array.
- `references` is an object keyed by alias. Each value is a local path, Git repository, or string shorthand.
- `agent` is an object keyed by agent name, not an array.
- `command` is an object keyed by command name, not an array.
- `plugin` is an array of strings or `[name, options]` tuples, not an object.
- `mcp[name].command` is an array of strings, never a single string. `type` is required.
- `permission` is either a string action or an object keyed by tool name.

## Skills

opencode's skill loader scans for `**/SKILL.md` inside skill directories. The
file is named `SKILL.md` exactly, and lives in its own folder named after the
skill:

```
.opencode/skills/my-skill/SKILL.md
```

Frontmatter:

```markdown
---
name: my-skill
description: One sentence covering what this skill does AND when to trigger it. Front-load the literal keywords or filenames the user is likely to say.
---

# My Skill

(skill body in markdown: instructions, examples, references)
```

- `name` is required, lowercase hyphen-separated, up to 64 chars, and matches the folder name.
- `description` is effectively required: skills without one are filtered out and never surfaced to the model. Cover both _what_ the skill does and _when_ to use it. Write in third person ("Use when...", not "I help with..."). Front-load concrete trigger keywords and filenames; gate with "Use ONLY when..." if the skill should stay quiet on adjacent topics.
- Optional: `license`, `compatibility`, `metadata` (string-string map).

Register skills from non-default locations via `skills.paths` (scanned
recursively for `**/SKILL.md`) and `skills.urls` (each URL serves a list of
skills).

## References

References make local directories and Git repositories outside the active
project available as supporting context. Configure them under `references`,
keyed by the alias used in `@` autocomplete:

```json
{
  "references": {
    "docs": {
      "path": "../product-docs",
      "description": "Use for product behavior and terminology"
    },
    "effect": {
      "repository": "Effect-TS/effect",
      "branch": "main",
      "description": "Use for Effect implementation details"
    }
  }
}
```

Local `path` values may be relative to the declaring config, absolute, or use
`~/`. Git `repository` values accept Git URLs, host/path references, and GitHub
`owner/repo` shorthand; `branch` is optional. Both forms support optional
`description` and `hidden` fields.

- Only references with a `description` are advertised to agents in system context.
- `hidden: true` removes a reference from TUI `@` autocomplete only. It remains available to agents and by direct path.
- Reference directories are automatically allowed through the external-directory boundary; normal read/edit/tool permissions still apply.
- String shorthand is supported: use `"docs": "../docs"` for local paths or `"effect": "Effect-TS/effect"` for Git repositories.

## Agents

Two ways to define an agent. Use the file form for anything non-trivial.

### Inline (in `opencode.json`)

```json
{
  "agent": {
    "my-reviewer": {
      "description": "Reviews PRs for style violations.",
      "mode": "subagent",
      "model": "anthropic/claude-sonnet-4-6",
      "permission": { "edit": "deny", "bash": "ask" },
      "prompt": "You are a strict PR reviewer..."
    }
  }
}
```

### File

```
.opencode/agent/my-reviewer.md      OR     .opencode/agents/my-reviewer.md
```

```markdown
---
description: Reviews PRs for style violations.
mode: subagent
model: anthropic/claude-sonnet-4-6
permission:
  edit: deny
  bash: ask
---

You are a strict PR reviewer. Focus on...
```

The file body becomes the agent's `prompt`. Do not also put `prompt:` in the
frontmatter.

`mode` is one of `"primary"`, `"subagent"`, `"all"`.

Allowed top-level frontmatter fields: `name, model, variant, description, mode,
hidden, color, steps, options, permission, disable, temperature, top_p`. Any
unknown field is silently routed into `options`.

To disable a built-in agent: `agent: { build: { disable: true } }`, or in a
file, `disable: true` in frontmatter.

`default_agent` must point to a non-hidden, primary-mode agent.

### Built-in agents

opencode ships with `build`, `plan`, `general`, `explore`. Hidden internal agents:
`compaction`, `title`, `summary`. To override a built-in's fields, define the
same key in `agent: { <name>: { ... } }`.

## Commands

opencode's command loader scans for `**/*.md` inside command directories. The
file is named after the command, and lives directly inside the `command` folder:

```
.opencode/command/deploy.md
```

Frontmatter:

```markdown
---
description: One sentence describing what the command does.
agent: build
model: anthropic/claude-sonnet-4-6
---

(command body in markdown: the prompt opencode runs, with $ARGUMENTS for the user's input)
```

- `template` is the command body — everything below the frontmatter — and is required: it is the prompt opencode runs when the command is invoked. Do not also put a `template:` key in the frontmatter.
- `$ARGUMENTS` is replaced with everything the user typed after the command; `$1`, `$2`, … pull individual positional arguments.
- Optional: `description`, `agent`, `model`, `variant`, `subtask`.

## Plugins

`plugin:` is an array. Each entry is one of:

```json
"plugin": [
  "opencode-gemini-auth",            // npm spec, latest
  "opencode-foo@1.2.3",              // npm spec, pinned
  "./local-plugin.ts",               // file path, relative to the declaring config
  "file:///abs/path/plugin.js",      // file URL
  ["opencode-bar", { "key": "val" }] // tuple form with options
]
```

Auto-discovered plugins (no config entry needed): any `*.ts` or `*.js` file in
`.opencode/plugin/` or `.opencode/plugins/`.

A plugin module exports `default` (or any named export) of type
`Plugin = (input: PluginInput, options?) => Promise<Hooks>`. The export is a
function, not a plain object literal, and the function returns an object
(return `{}` if there is nothing to register).

```ts
import type { Plugin } from "@opencode-ai/plugin"

export default (async ({ client, project, directory, $ }) => {
  return {
    config: (cfg) => {
      // cfg is the live merged config; mutate fields here.
    },
    "tool.execute.before": async (input, output) => {
      // mutate output.args before the tool runs
    },
  }
}) satisfies Plugin
```

Hook surface (mutate `output` in place; return `void`):

- `event(input)`: every bus event
- `config(cfg)`: once on init with the merged config
- `chat.message`, `chat.params`, `chat.headers`
- `tool.execute.before`, `tool.execute.after`
- `tool.definition`
- `command.execute.before`
- `shell.env`
- `permission.ask`
- `experimental.chat.messages.transform`, `experimental.chat.system.transform`,
  `experimental.session.compacting`, `experimental.compaction.autocontinue`,
  `experimental.text.complete`

Special object-shaped (not callbacks): `tool: { my_tool: { ... } }`,
`auth: { ... }`, `provider: { ... }`.

## MCP servers

`mcp:` is an object keyed by server name. Each server is discriminated by
`type`:

```json
{
  "mcp": {
    "playwright": {
      "type": "local",
      "command": ["npx", "-y", "@playwright/mcp"],
      "enabled": true,
      "env": { "BROWSER": "chromium" }
    },
    "github": {
      "type": "remote",
      "url": "https://...",
      "enabled": true,
      "headers": { "Authorization": "Bearer {env:GITHUB_TOKEN}" }
    },
    "old-server": { "enabled": false }
  }
}
```

`command` is an array of strings. `type` is required. Use `enabled: false` to
disable a server inherited from a parent config. String values such as header
tokens support `{env:VAR}` interpolation (and `{file:path}`); the shell-style
`${VAR}` is not substituted.

## Permissions

```json
"permission": {
  "edit": "deny",
  "bash": { "git *": "allow", "rm *": "deny", "*": "ask" },
  "external_directory": { "~/secrets/**": "deny", "*": "allow" }
}
```

Actions: `"allow"`, `"ask"`, `"deny"`.

Per-tool value forms: `"allow"` shorthand (treated as `{"*": "allow"}`), or an
object `{ pattern: action }`. Within an object, **insertion order matters**.
opencode evaluates the LAST matching rule, so put broad rules first and narrow
rules last.

`permission: "allow"` (a string at the top level) is shorthand for "allow
everything" and is rarely what the user wants.

Known permission keys: `read, edit, glob, grep, list, bash, task,
external_directory, todowrite, question, webfetch, websearch, lsp, doom_loop,
skill`. Some of these (`todowrite,
question, webfetch, websearch, doom_loop`) only accept a flat
action, not a per-pattern object.

`external_directory` patterns are filesystem paths (use `~/`, absolute paths,
or globs like `~/projects/**`).

Per-agent `permission:` overrides top-level `permission:`. Plan Mode lives on
the `plan` agent's permission ruleset (`edit: deny *`).

## Escape hatches

When a user's config is broken and opencode won't start, these env vars help:

- `OPENCODE_DISABLE_PROJECT_CONFIG=1`: skip the project's local `opencode.json`
  and start from globals only. Run from the project directory, opencode loads,
  the user edits the broken file, then they restart without the flag.
- `OPENCODE_CONFIG=/path/to/file.json`: load an additional explicit config.
- `OPENCODE_CONFIG_CONTENT='{"$schema":"https://opencode.ai/config.json"}'`:
  inject inline JSON as a final local-scope merge.
- `OPENCODE_DISABLE_DEFAULT_PLUGINS=1`: skip default plugins.
- `OPENCODE_PURE=1`: skip external plugins entirely.
- `OPENCODE_DISABLE_EXTERNAL_SKILLS=1`,
  `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`: skip the external skill scans under
  `~/.claude/` and `~/.agents/`.

## When proposing edits

- Validate against the schema before writing. If you are unsure of a field's
  exact shape, or the field is not covered in this skill, fetch
  `https://opencode.ai/config.json` and read the schema rather than guessing.
- Preserve `$schema` and any existing fields the user did not ask to change.
- For agent, command, skill, and plugin definitions, prefer creating new files
  in the correct location over inlining everything in `opencode.json`.
- If the user's existing config is malformed, point them at the env-var escape
  hatches above so they can edit from inside opencode without breaking their
  session.
- After saving any config change, remind the user to quit and restart opencode
  — running sessions keep using the already-loaded config.

Base directory for this skill: .
Relative paths in this skill (e.g., scripts/, reference/) are relative to this base directory.
Note: file list is sampled.

<skill_files>
<file>/Users/jnavarro/software/rustloops/AGENTS.md</file>
<file>/Users/jnavarro/software/rustloops/.gitattributes</file>
<file>/Users/jnavarro/software/rustloops/.gitignore</file>
<file>/Users/jnavarro/software/rustloops/PROMPT.md</file>
<file>/Users/jnavarro/software/rustloops/pixi.lock</file>
<file>/Users/jnavarro/software/rustloops/.pixi/envs/default/share/doc/readline/readline.html</file>
<file>/Users/jnavarro/software/rustloops/.pixi/envs/default/share/doc/readline/history.html</file>
<file>/Users/jnavarro/software/rustloops/.pixi/envs/default/share/doc/readline/rluserman.html</file>
<file>/Users/jnavarro/software/rustloops/.pixi/envs/default/share/doc/readline/README</file>
<file>/Users/jnavarro/software/rustloops/.pixi/envs/default/share/doc/readline/CHANGES</file>
</skill_files>
</skill_content>

## Assistant

Let me read the project files to understand the current state.

**Tool Call: Users/jnavarro/software/rustloops/PROMPT.md**
Status: Completed

We're going to be creating a set of agent SKILLs for auto-translating/wrapping common python+numpy+scipy or other numerical library loops and operations into rust.

Inspired by this blog post: https://ohadravid.github.io/posts/2023-03-rusty-python/

When the user invokes the skill, rustloops, the agent needs to ask the user for the relevant file, the relevant functions or loops (by line-number), and a script to run to test the changes.

The agent should then perform a "before" run and get timing information, then "after" and display a difference.

We'll also want to draft a README.md to help guide users.

One skill should wrap/translate the loops. One skill can re-run the comparision. One skill should unwrap/un-translate the loops.

Any questions. We're still developing this plan.

**Tool Call: Users/jnavarro/software/rustloops/pixi.toml**
Status: Completed

[workspace]
authors = ["jlnav <jnavarro@anl.gov>"]
channels = ["conda-forge"]
name = "rustloops"
platforms = ["osx-arm64"]
version = "0.1.0"

[tasks]

[dependencies]
python = ">=3.14.6,<3.15"

**Tool Call: Users/jnavarro/software/rustloops/.gitignore**
Status: Completed

# pixi environments
.pixi/*
!.pixi/config.toml

## Assistant

I now have a clear understanding. Let me check the existing skills directory structure to know exactly where to place these skills.

**Tool Call: ls -la /Users/jnavarro/software/rustloops/.opencode 2>/dev/null; echo "---agents---"; ls -la /Users/jnavarro/.agents/skills 2>/dev/null; echo "---config---"; ls -la /Users/jnavarro/.config/opencode 2>/dev/null**
Status: Completed

---agents---
total 0
drwxr-xr-x@ 3 jnavarro  staff  96 Jun  2 17:55 .
drwxr-xr-x@ 3 jnavarro  staff  96 Jun  2 17:55 ..
drwxr-xr-x@ 3 jnavarro  staff  96 Jun  2 17:55 hnreview
---config---
total 64
drwxr-xr-x@  9 jnavarro  staff    288 Jun 17 07:45 .
drwx------  14 jnavarro  staff    448 May 14 08:54 ..
-rw-r--r--@  1 jnavarro  staff     45 Mar 30 11:59 .gitignore
-rw-r--r--@  1 jnavarro  staff    848 Apr  1 13:32 bun.lock
drwxr-xr-x@ 28 jnavarro  staff    896 Apr 20 11:48 node_modules
-rw-r--r--@  1 jnavarro  staff   3516 Jul  2 15:12 opencode.json
-rw-r--r--@  1 jnavarro  staff  13552 Apr 27 09:05 package-lock.json
-rw-r--r--@  1 jnavarro  staff     63 Apr 20 11:48 package.json
drwxr-xr-x@  2 jnavarro  staff     64 Jun 16 14:48 skills


## Assistant

I have everything I need. Here's my plan for review.

---

# Plan: `rustloops` — Python-to-Rust loop acceleration skills

## Background

Based on the Ohad Ravid blog post, the workflow is: profile-worthy Python/numpy/scipy loops get rewritten in Rust via **PyO3 + maturin**, keeping the Python API identical so the rest of the codebase (and researchers) are undisturbed. We wrap a hot function, measure before/after, and can revert cleanly.

The three skills the user requested map to a natural lifecycle:

1. **`rustloops`** — wrap/translate a Python loop/function into Rust (the main skill).
2. **`rustloops-compare`** — re-run the before/after timing comparison.
3. **`rustloops-revert`** — unwrap/un-translate, restoring the original Python.

## Key design decisions (want your input)

### 1. Location & scope of skills
Since this is a Pixi project and you want these usable *inside* it, I propose **project-local skills** at:
```
.opencode/skills/rustloops/SKILL.md
.opencode/skills/rustloops-compare/SKILL.md
.opencode/skills/rustloops-revert/SKILL.md
```
This keeps them versioned with the repo. (Alternative: global `~/.config/opencode/skills/` so they work in any project. I lean project-local given the `pixi.toml` + `AGENTS.md` setup.)

### 2. How reversibility works
For clean unwrap, `rustloops` will **preserve the original Python** rather than delete it. Proposed mechanism:
- Keep the original function body in the source file, renamed/guarded (e.g. `_original_find_close_polygons`) or moved to a sidecar file `<module>_rustloops_backup.py`.
- Store metadata in a manifest file `.rustloops/manifest.json` recording: source file, function/loop names, line ranges, the created Rust crate name, backup location, and baseline timings.
- `rustloops-revert` reads the manifest to restore Python and optionally remove the crate.

**Question:** Do you prefer a git-based backup (rely on the user's VCS/stash) or an explicit manifest + backup files managed by the skill? I recommend the manifest approach so it works even in non-git dirs (this repo isn't git).

### 3. Rust project layout
Following the post, each wrap creates a maturin/PyO3 crate. Proposed convention:
```
<project>/<module>_rs/
  Cargo.toml        # ndarray, ndarray-linalg, numpy, pyo3; release profile lto+codegen-units=1
  pyproject.toml
  src/lib.rs
```
Built with `maturin develop --release`. The Python side imports `from <module>_rs import <fn>`.

**Question:** Do you want one crate per fastified module (reused across multiple functions), or one crate per function? I recommend **one crate per module**, appending new `#[pyfunction]`s on subsequent runs.

### 4. Toolchain / dependencies
The skills require: `rust`/`cargo`, `maturin`, and ideally `py-spy` for profiling. These aren't in `pixi.toml` yet. Options:
- (a) Skill instructs the agent to add `maturin`, `rust`, `py-spy` to `pixi.toml` deps (conda-forge has all three) when first invoked.
- (b) Skill just checks for them and errors with install guidance.

I recommend **(a)** — the skill offers to add them to `pixi.toml`, matching the "Pixi-managed project" convention in AGENTS.md.

### 5. Timing / measurement
The user provides a **test script**. The skill will:
- Run it N times (or wrap it in a `time.perf_counter` harness if the script doesn't self-report), capture wall time → **baseline**.
- Also capture correctness output (stdout hash / return value) so before-vs-after can be checked for equivalence, not just speed.
- After wrapping + `maturin develop --release`, re-run → **new timing**, display a table (baseline ms, new ms, multiplier) mirroring the blog's summary table.

**Question:** Should the skill *require* the test script to also serve as a correctness check (compare outputs before/after), or is timing-only acceptable? I strongly recommend capturing output equivalence to catch translation bugs.

## What each skill does (detailed)

### Skill 1: `rustloops` (wrap/translate)
Frontmatter description front-loads triggers: "rustloops", "speed up python loop", "translate to rust", "make numpy faster".

Workflow the SKILL.md instructs the agent to follow:
1. **Gather inputs** (ask user): target file path; function name(s) or loop line-range(s); test/benchmark script + command.
2. **Check toolchain** (rust, maturin, py-spy); offer to add to `pixi.toml`.
3. **Baseline run**: execute test script, record avg time + output signature into `.rustloops/manifest.json`.
4. **(Optional) profile** with `py-spy record --native` to confirm the target is actually hot.
5. **Analyze** the target function: identify inputs/outputs, numpy arrays vs Python objects, whether a supporting `#[pyclass]` (like `Polygon`) is warranted.
6. **Generate Rust**: create/extend `<module>_rs` crate; naive line-to-line translation first (per the post's v1), using `ndarray`/`ndarray-linalg`/`numpy` + `pyo3`.
7. **Preserve original**: back up Python function + record in manifest.
8. **Rewire Python**: import the Rust function; keep identical signature.
9. **Build**: `maturin develop --release`.
10. **After run**: re-execute test script, verify output matches baseline signature, record new time.
11. **Report**: before/after table + multiplier; note further optimization opportunities (pyclass, avoid allocations — the post's v2/v3).

### Skill 2: `rustloops-compare` (re-run comparison)
- Reads `.rustloops/manifest.json`.
- Re-runs the recorded test script, shows current time vs baseline vs last-recorded.
- Useful after tweaks to the Rust code. Optionally re-`maturin develop --release` first.
- Updates the manifest history.

### Skill 3: `rustloops-revert` (unwrap/un-translate)
- Reads manifest; restores original Python from backup.
- Removes/uninstalls the Rust import wiring.
- Offers to delete the `<module>_rs` crate and `pip uninstall` the built module.
- Runs the test script once to confirm the Python-only version still works; reports timing (sanity check that we're back to baseline).

## README.md
Draft a top-level `README.md` covering: what rustloops is, the PyO3/maturin approach + credit to the blog post, prerequisites (pixi, rust, maturin, py-spy), how to invoke each of the three skills, the manifest/backup model, and a worked example (the poly-match case). 

## Files to be created (on approval)
```
.opencode/skills/rustloops/SKILL.md
.opencode/skills/rustloops-compare/SKILL.md
.opencode/skills/rustloops-revert/SKILL.md
README.md
```
Plus, at first invocation (runtime, not now), the skills manage `.rustloops/manifest.json`, the `<module>_rs` crate, and `pixi.toml` deps.

---

## Open questions summary
1. **Skill location**: project-local (`.opencode/skills/`) vs global? *(I recommend project-local.)*
2. **Backup mechanism**: manifest + backup files vs git-based? *(I recommend manifest.)*
3. **Crate granularity**: one per module vs one per function? *(I recommend per-module.)*
4. **Toolchain**: auto-add rust/maturin/py-spy to `pixi.toml` vs check-and-instruct? *(I recommend auto-add with confirmation.)*
5. **Correctness checking**: require output equivalence vs timing-only? *(I recommend requiring output equivalence.)*
6. Anything about **scipy** specifically — do you want the first skill to handle scipy operations (e.g. `ConvexHull`, `cdist`) beyond numpy, or start numpy-only and note scipy as future work?

Let me know your answers (or "go with your recommendations") and I'll implement.

## User

0. Lets rename the project to rustloops
1. Project-local for now, but under an .agents directory
2. manifest+backup is good. this will be a git repo later.
3. one crate per module
4. b. I'll add them for the dev environment. Not all users of the skills will use pixi, but the most important idea is the deps get installed, however the user prefers.
5. Output equivalence is a good idea.

Also, perhaps the SKILL for the actual translation should contain common patterns, so not-frontier models may still attempt basic translations. Thoughts?

## Assistant

Good answers. Let me incorporate them and address the pattern-library question, which is the most consequential design point.

## On embedding translation patterns in the SKILL

**Strongly agree — yes.** This is the highest-leverage part of the whole design. A SKILL that only says "translate this loop to Rust" leans entirely on the model's latent PyO3/ndarray knowledge, which weaker models get wrong (wrong crate versions, `PyObject` vs `Py<T>` confusion, missing GIL tokens, forgetting `--release`). Embedding a **pattern cookbook** turns the skill into a lookup-and-adapt task rather than a from-scratch generation task — much more reliable across model tiers.

The blog post already gives a natural progression I'll encode as leveled patterns:

- **Pattern A — Function wrap (naive, v1):** `PyReadonlyArray1<f64>` in, `Vec<Py<T>>` in/out, `.as_array()`, `getattr`/`extract`, `ndarray_linalg::Norm`. The line-to-line template.
- **Pattern B — `#[pyclass(subclass)]` (v2):** move a hot data class (like `Polygon`) to Rust with `#[new]`, `#[getter]`, subclass from Python for the rest of the API.
- **Pattern C — Allocation avoidance (v3):** borrow-scoping trick, manual math instead of `.to_owned()`, cheap `Py` clone (refcount only).
- **Boilerplate blocks:** `Cargo.toml` deps + release profile (`lto`, `codegen-units=1`, `debug=true`), `#[pymodule]` registration, `maturin develop --release` reminder, common `error[...]` → fix table (e.g. "slow? you forgot `--release`").

Each pattern gets a **Python-before / Rust-after** snippet the model can pattern-match against. I'll keep the SKILL body itself lean and put the full cookbook in a referenced file (`patterns.md`) in the skill directory, so the main flow stays readable and the patterns are loaded on demand.

**One decision for you:** should the cookbook live inline in `SKILL.md`, or as a separate `patterns.md` (plus maybe `templates/` with a ready-to-copy `Cargo.toml`, `lib.rs` skeleton, and a `measure.py` harness)? I recommend the **separate-files** approach — cleaner SKILL, and the templates can be copied verbatim rather than regenerated (again helping weaker models).

---

# Updated Plan: `rustloops`

## Decisions locked in
0. Project renamed **`rustloops`** (update `pixi.toml` `name`, `AGENTS.md`, and any references).
1. Skills live under an **`.agents`** directory, project-local: `.agents/skills/<name>/SKILL.md`.
2. **Manifest + backup** files for reversibility (git-friendly, works pre-git).
3. **One crate per module**, extended on subsequent runs.
4. Deps: skill **checks for** `rust`/`cargo`, `maturin`, `py-spy` and **instructs** the user to install them (any method — pixi, pip, rustup, conda); it does **not** assume pixi. You handle the dev env.
5. **Output equivalence** required — capture before/after output signature, not just timing.
6. Translation SKILL carries a **pattern cookbook** so non-frontier models can attempt basic translations.

## Proposed file layout
```
.agents/skills/
  rustloops/
    SKILL.md              # main wrap/translate skill; lean workflow + refs
    patterns.md           # Pattern A/B/C cookbook (before/after snippets)
    templates/
      Cargo.toml          # deps + release profile, copy-ready
      lib.rs              # #[pymodule] skeleton
      measure.py          # timing + output-signature harness
  rustloops-compare/
    SKILL.md              # re-run comparison from manifest
  rustloops-revert/
    SKILL.md              # unwrap/restore Python from backup
README.md                 # user guide
```
Runtime-managed (created by skills, not now): `.rustloops/manifest.json`, per-module backups (`.rustloops/backups/<module>.<ts>.py`), and the `<module>_rs/` crate.

## Skill behaviors (unchanged from prior plan except names)

**`rustloops`** — gather inputs (file, functions/loops by line-number, test command) → check toolchain (instruct-only) → baseline run (time + output signature → manifest) → optional `py-spy` profile → analyze target → generate Rust from **patterns.md/templates** → back up original Python + record manifest → rewire Python imports (identical API) → `maturin develop --release` → after run → **verify output equivalence** → report before/after table + multiplier + next-optimization hints.

**`rustloops-compare`** — read manifest, (optionally rebuild), re-run test, show baseline vs last vs current, re-verify output equivalence, append to manifest history.

**`rustloops-revert`** — read manifest, restore Python from backup, remove Rust wiring, offer to delete crate + uninstall built module, run test once to confirm parity with baseline.

## README.md
What rustloops is; PyO3/maturin approach + blog credit; prerequisites and install options (pixi/pip/rustup/conda — not pixi-only); the three skills and when to use each; manifest/backup model; a worked poly-match example; troubleshooting (the "forgot `--release`" gotcha, etc.).

## Manifest schema (sketch)
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
    "history": [{ "ts": "...", "ms": 23.44, "note": "v1 naive" }]
  }]
}
```

---

## Remaining questions before I implement
1. **Cookbook placement**: separate `patterns.md` + `templates/` (my recommendation) vs inline in `SKILL.md`?
2. **scipy scope**: start numpy-focused with scipy noted as future work, or include a couple scipy patterns (e.g. `cdist`, `ConvexHull`) in the cookbook now?

Answer these three (or say "go with recommendations") and I'll build it out once you switch me out of plan mode.

## User

1. patterns.md, plus templates, very good idea!

2. yeah, numpy-focused for starters

## Assistant

All locked in. Final plan below — no ambiguity remaining.

# Final Plan: `rustloops`

## Decisions
0. Project called **`rustloops`** everywhere (`pixi.toml` name, `AGENTS.md`, references).
1. Skills project-local under **`.agents/skills/`**.
2. **Manifest + backup** files for reversibility.
3. **One crate per module**, extended on later runs.
4. Toolchain: **check-and-instruct** only (`rust`/`cargo`, `maturin`, `py-spy`); no pixi assumption.
5. **Output equivalence** required (signature + timing).
6. Translation skill carries a **pattern cookbook** (`patterns.md`) + copy-ready **`templates/`**.
7. Naming: **`rustloops`** everywhere — three skills: `rustloops`, `rustloops-compare`, `rustloops-revert`.
8. Cookbook is **numpy-focused**; scipy noted as future work.

## Files to create
```
.agents/skills/
  rustloops/
    SKILL.md          # main wrap/translate: lean workflow, refs patterns/templates
    patterns.md       # Pattern A (fn wrap), B (#[pyclass]), C (alloc-avoidance) — before/after
    templates/
      Cargo.toml      # pyo3, numpy, ndarray, ndarray-linalg + release profile (lto, codegen-units=1, debug=true)
      lib.rs          # #[pymodule] skeleton
      measure.py      # timing + output-signature harness
  rustloops-compare/
    SKILL.md
  rustloops-revert/
    SKILL.md
README.md             # user guide + poly-match worked example + troubleshooting
```
Also update: `pixi.toml` (`name = "rustloops"`), `AGENTS.md` (rename refs).

Runtime-managed by the skills (not created now): `.rustloops/manifest.json`, `.rustloops/backups/<module>.<ts>.py`, `<module>_rs/` crate.

## Skill behaviors

**`rustloops`** (wrap/translate):
1. Gather inputs — target file; function(s)/loop(s) by line-number; test command.
2. Check toolchain (`cargo`, `maturin`, `py-spy`); if missing, instruct install (pixi/pip/rustup/conda) and stop.
3. Baseline run — execute test cmd, record avg time + output signature → manifest.
4. Optional `py-spy record --native` to confirm target is hot.
5. Analyze target (numpy arrays vs Python objects; whether a `#[pyclass]` is warranted).
6. Generate Rust from `patterns.md` + `templates/`; create/extend `<module>_rs`.
7. Back up original Python → `.rustloops/backups/`; record in manifest.
8. Rewire Python to import Rust fn (identical signature/API).
9. Build: `maturin develop --release`.
10. After run — re-execute test, **verify output signature matches baseline**, record new time.
11. Report before/after table + multiplier + next-step hints (Pattern B/C).

**`rustloops-compare`**: read manifest → optional rebuild → re-run test → show baseline vs last vs current → re-verify equivalence → append to manifest history.

**`rustloops-revert`**: read manifest → restore Python from backup → remove Rust import wiring → offer to delete crate + uninstall built module → run test once to confirm parity with baseline.

## Cookbook content (`patterns.md`, numpy-only)
- **Pattern A — Function wrap (v1):** `PyReadonlyArray1<f64>`, `.as_array()`, `Vec<Py<T>>`, `getattr`/`extract`, `ndarray_linalg::Norm`; line-to-line template.
- **Pattern B — `#[pyclass(subclass)]` (v2):** `#[new]`, `#[getter]`, subclass from Python for remaining API.
- **Pattern C — Alloc avoidance (v3):** borrow-scoping, manual math over `.to_owned()`, cheap refcount `Py` clone.
- **Gotchas table:** "slow → forgot `--release`", `PyObject` vs `Py<T>`, missing GIL token, crate versions.

## Manifest schema
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
    "history": [{ "ts": "...", "ms": 23.44, "note": "v1 naive" }]
  }]
}
```

---

Plan is complete and all questions resolved. I'm ready to implement as soon as you take me out of plan mode.

## User

Go for it! Let me know if you have questions

**Tool Call: 10 todos**
Status: Completed

[
  {
    "content": "Update pixi.toml: rename project to rustloops",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Update AGENTS.md: rename references to rustloops",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create .agents/skills/rustloops/SKILL.md",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create .agents/skills/rustloops/patterns.md",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create .agents/skills/rustloops/templates/Cargo.toml",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create .agents/skills/rustloops/templates/lib.rs",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create .agents/skills/rustloops/templates/measure.py",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create .agents/skills/rustloops-compare/SKILL.md",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create .agents/skills/rustloops-revert/SKILL.md",
    "status": "pending",
    "priority": "high"
  },
  {
    "content": "Create README.md",
    "status": "pending",
    "priority": "medium"
  }
]
