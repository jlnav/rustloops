# Skill: rustloops-compare

Re-run the before/after performance comparison for a previously accelerated function.
Use this when you want to re-benchmark, check timing after further changes, or verify
output equivalence after a Rust update.

---

## Prerequisites

- `.rustloops/manifest.json` must exist (created by the `rustloops` skill).
- The Rust crate must have been built at least once (`maturin develop --release`).

---

## Step 1 — Read the manifest

Load `.rustloops/manifest.json`.  If it does not exist, stop and tell the user to run
the `rustloops` skill first.

Identify the entry or entries to compare.  If there are multiple entries, list them and
ask the user which module to benchmark (or benchmark all if they say "all").

For each entry, note:
- `test_command`
- `baseline_ms`
- `output_signature`
- `history` (previous timed runs)

---

## Step 2 — (Optional) Rebuild

Ask: "Do you want to rebuild the Rust extension before measuring? (yes/no)"
If yes (or if the `.so` / `.pyd` file is missing or older than any `.rs` source file):

```
cd <crate> && maturin develop --release
```

If the build fails, report the error and stop — do not record a bad timing.

---

## Step 3 — Run the test command

Run `<test_command>` **3 times**.  Record wall-clock milliseconds for each.
If the test command is `measure.py` or similar, parse:
- `MEAN_MS:<float>` line for timing
- `SIGNATURE:sha256:<hex>` line for output signature

Otherwise, record timing externally (wrap the command with `time`):
```
python -c "
import time, subprocess, sys
runs = []; times = []
for _ in range(3):
    t0 = time.perf_counter()
    r = subprocess.run(sys.argv[1:], capture_output=True)
    times.append((time.perf_counter() - t0) * 1000)
    runs.append(r.stdout)
print('MEAN_MS:', sum(times)/len(times))
" -- <test_command_tokens>
```

Compute SHA-256 of stdout for equivalence check.

---

## Step 4 — Verify output equivalence

Compare the current output signature against `output_signature` in the manifest.

- **Match** → proceed.
- **Mismatch** → print a warning:
  ```
  WARNING: output signature has changed since baseline.
  Baseline : sha256:<baseline_hex>
  Current  : sha256:<current_hex>
  The function may no longer be correct. Diff the outputs and investigate.
  ```
  Record the timing anyway but flag it as `"equivalence": "FAIL"` in history.

---

## Step 5 — Report

Print a table. Use ANSI escape codes to color the baseline measurement red and
the current measurement green:

```
rustloops comparison for <module>.<fn>
────────────────────────────────────────────────────
  baseline    : \e[31m<baseline_ms> ms\e[0m
  <prev_ts>   : <prev_ms> ms   (from history, most recent)
  now         : \e[32m<now_ms> ms\e[0m
  vs baseline : <now/baseline>x  (e.g. "12.4x faster")
  vs previous : <now/prev>x
  output      : OK / MISMATCH
────────────────────────────────────────────────────
```

If there is no previous history entry, omit the "vs previous" row.

---

## Step 6 — Update the manifest

Append a new history entry:

```json
{ "ts": "<ISO8601>", "ms": <now_ms>, "note": "re-benchmark", "equivalence": "OK" }
```

Write the updated manifest back to `.rustloops/manifest.json`.

---

## Hints to offer

After the report, suggest next steps if speedup is below 10x:
- "Consider Pattern B: move the hot data class to a `#[pyclass]`."
- "Consider Pattern C: eliminate per-iteration allocations."
- "Run `py-spy record --native -- python <script>` to find the next bottleneck."
