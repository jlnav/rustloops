# Skill: rustloops-revert

Restore a Python file to its original pre-Rust state.
Use this when you want to undo a rustloops translation, remove Rust wiring, or go back
to pure Python for any reason.

---

## Prerequisites

- `.rustloops/manifest.json` must exist with a `backup` path for the module.
- The backup file must exist at that path.

---

## Step 1 — Read the manifest

Load `.rustloops/manifest.json`.  If it does not exist or has no entries, stop
and tell the user there is nothing to revert.

If there are multiple entries, list them and ask the user which module to revert.

For the chosen entry, note:
- `source_file` — the Python file to restore
- `backup` — path to the timestamped backup
- `crate` — the Rust crate directory
- `test_command` — used for post-revert verification
- `baseline_ms` and `output_signature` — used to confirm the restoration is correct

---

## Step 2 — Confirm with the user

Print a summary and ask for confirmation before making changes:

```
Reverting <module> to backup: <backup>
This will:
  1. Overwrite <source_file> with the backup.
  2. Remove the rustloops import wiring.
  3. (Optional) Delete the <crate>/ crate directory.
  4. (Optional) Uninstall the built extension.

Proceed? (yes/no)
```

Stop if the user says no.

---

## Step 3 — Restore the Python file

Copy `<backup>` → `<source_file>`, overwriting the current Rust-wired version.

Verify the copy succeeded (file sizes match, no I/O error).

---

## Step 4 — Remove Rust wiring (defensive pass)

Open `<source_file>` and scan for any remaining rustloops wiring lines that the backup
might still contain from an earlier revert attempt.  Remove lines matching:

```
from <crate> import ...   # rustloops: Rust acceleration
```

Also remove any `try/except ImportError` blocks that were introduced by rustloops
(they contain the comment `# rustloops: Rust acceleration`).

Restore the original function name if it was renamed to `_<name>_py` during wiring.

If the backup already looks clean (no such lines), skip this step.

---

## Step 5 — (Optional) Delete the crate

Ask: "Delete the Rust crate directory `<crate>/`? (yes/no/keep)"

If yes:
```
rm -rf <crate>/
```

---

## Step 6 — (Optional) Uninstall the built extension

Ask: "Uninstall the built extension from the current Python environment? (yes/no)"

If yes, find and remove the compiled `.so` / `.pyd` file.  The extension name matches
`<crate>*.so` or `<crate>*.pyd` in the site-packages directory.  Use:

```python
import importlib.util, os
spec = importlib.util.find_spec("<crate>")
if spec and spec.origin:
    os.remove(spec.origin)
    print(f"Removed {spec.origin}")
else:
    print("Extension not found in sys.path — nothing to uninstall.")
```

Run this with the active Python interpreter.

---

## Step 7 — Verify restoration

Run `<test_command>` once.  Parse the output for:
- `MEAN_MS:<float>` (if using the measure.py harness)
- `SIGNATURE:sha256:<hex>`

Compare the signature against `output_signature` in the manifest.

Print result:

```
rustloops revert complete for <module>
────────────────────────────────────────
  source restored : <source_file>
  from backup     : <backup>
  output          : OK (matches baseline signature)
  crate deleted   : yes / no / kept
  extension       : uninstalled / kept
────────────────────────────────────────
```

If the signature does not match, warn the user and do NOT remove the manifest entry —
they may need to investigate.

---

## Step 8 — Update the manifest

Remove the entry for the reverted module from `.rustloops/manifest.json`.
If the entries array is now empty, offer to delete `.rustloops/manifest.json` entirely.

---

## Error handling

| Situation | Action |
|---|---|
| Backup file missing | Stop; tell user the backup is gone and they must restore manually or from git |
| Source file unwritable | Stop; report permissions error |
| Test command fails after restore | Warn; keep the manifest entry intact |
| Crate dir already absent | Note it and continue |
