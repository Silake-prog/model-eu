# Golden snapshot — reproduction safety net

This directory locks down the **strict-reproduction guarantee**: existing scenario
strings must assemble to bit-identical model inputs across every cleanup phase. It is
the contract referenced by `CLEANUP_MISSION.md` (no numerics changes) and is re-run
after each phase of the POMMES-EUR restructure.

## What it checks

- **Tier A (`snapshot_constants.py`)** — always runs; needs only stdlib + `platformdirs`.
  Imports `clever.constants` in a **fresh subprocess per scenario** (the scenario is an
  import-time process global keyed on `CLEVER_SCENARIO`) and SHA-256s every public data
  global. Compared against `baseline_hashes.json`. Covers four reference scenarios that
  exercise distinct parser branches (baseline R0, `policy_*` prefix, a deep flag stack,
  and the MENA Variant-B path).
- **Tier B (`synthetic_inputs.py`)** — skips unless `pandas`/`polars`/`numpy`/`pommes_craft`
  are importable. Verifies the model builder stays importable with a stable signature.
  The full `parameter_tables` fingerprint is a documented, deferred hook (needs a
  deps-equipped environment — see the module docstring).

## Running

```bash
pytest tests/golden/                          # under pytest (Tier B skips without deps)
python tests/golden/test_golden_snapshot.py   # plain runner, no pytest required
python tests/golden/snapshot_constants.py --check   # Tier A only, exit 1 on drift
```

## Regenerating the baseline

Only with an explicit, reviewed commit — regenerating changes the reproduction contract:

```bash
python tests/golden/snapshot_constants.py --write
```
