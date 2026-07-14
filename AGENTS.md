# AGENTS.md

- This is a Pixi-managed project called **rustloops**.
- Use `pixi run <task>` to execute tasks defined in `pixi.toml`.
- Dependency management is handled by `pixi`.

## Toolchain

- `cargo`, `rustc`, `maturin`, and `py-spy` are all available inside the pixi
  environment (installed via conda-forge packages `rust`, `maturin`, `py-spy`).
- They are **not** on the system PATH outside the environment. Always invoke them
  via `pixi run <cmd>` or from within `pixi shell`. Do not ask the user to install
  `cargo` separately — it ships with the `rust` conda-forge package.
- To verify the toolchain is ready: `pixi run cargo --version && pixi run maturin --version`.

## Examples

`examples/` contains three self-contained scientific Python demos for the rustloops skills:

| Directory | Hot target | Pattern |
|---|---|---|
| `examples/nbody/` | `step()` — O(N²) pairwise gravity | A + C (D after rewrite) |
| `examples/particle/` | `Particle` class + `update()` | B |
| `examples/mandelbrot/` | `escape_counts()` — per-pixel escape time | A (scalar) + D (rayon) |

Each example has:
- A Python module with a clearly-marked hot function/class.
- A `measure.py` harness (seeded, deterministic) that prints `MEAN_MS:` and
  `SIGNATURE:sha256:` exactly as the rustloops skill expects.

When adding new examples, follow the same conventions:
- Use `numpy.random.default_rng(42)` for reproducible inputs.
- Write output to `output_buf` in a **sorted, stable** form so the SHA-256 signature
  is deterministic across runs.
- Size the workload so the pure-Python baseline runs in roughly **0.5–3 s**.
- Do not add pixi tasks for examples — users run `python measure.py` directly after
  activating the environment.
- **Examples must contain only Python source** (the optimizable module + its
  `measure.py` harness). Do not commit hand-written Rust crates, `Cargo.toml`, or built
  extensions under `examples/`. The whole point of an example is to be a *pure-Python
  starting point* that the rustloops skill translates to Rust on demand; any Rust
  wiring is generated at run time into `<module_name>_rs/` at the repo root, not stored
  in the example. This applies to Pattern D too: the parallel Rust version is produced
  by the skill, while the example stays a plain Python loop.

## What belongs where

| Change | Location |
|---|---|
| New skill or skill update | `.agents/skills/<skill-name>/` |
| Pattern cookbook additions | `.agents/skills/rustloops/patterns.md` |
| New demo example | `examples/<name>/` |
| Python/Rust dependencies | `pixi.toml` (via `pixi add`) |
| Generated Rust crates (after skill runs) | `<module_name>_rs/` at repo root |
| Timing history and backups | `.rustloops/` (auto-managed by the skill) |

Skills provide specialized instructions and workflows for specific tasks.
Use the skill tool to load a skill when a task matches its description.
<available_skills>
  <skill>
    <name>rustloops</name>
    <description>Accelerate a hot Python loop or function by wrapping or translating it to Rust via PyO3/maturin. Use when the user wants to speed up a Python function, translate a numpy loop to Rust, or wrap Python code in a Rust extension module.</description>
    <location>.agents/skills/rustloops/SKILL.md</location>
  </skill>
  <skill>
    <name>rustloops-compare</name>
    <description>Re-run the before/after performance comparison for a previously accelerated function. Use when the user wants to benchmark, re-measure, or check timing after changes.</description>
    <location>.agents/skills/rustloops-compare/SKILL.md</location>
  </skill>
  <skill>
    <name>rustloops-revert</name>
    <description>Restore a Python file to its original pre-Rust state. Use when the user wants to undo a rustloops translation, remove Rust wiring, or go back to pure Python.</description>
    <location>.agents/skills/rustloops-revert/SKILL.md</location>
  </skill>
</available_skills>
