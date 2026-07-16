# CLAUDE.md — orientation for Claude Code

> **This session's mission is in [`CLEANUP_MISSION.md`](CLEANUP_MISSION.md)** — read it
> first. Short version: architecture cleanup so the model is easier to extend; no numerics
> changes; keep scenario strings backward-compatible; never commit secrets/data.

This repo is a **code-only snapshot** of the CLEVER-2050 / POMMES model, copied from the
Aloret compute server for cleanup and version control. It does **not** run end-to-end here:
the solver outputs (`results/`, `*.nc`), the Gurobi license, and the data caches live only on
the cluster and are `.gitignore`d.

## What this is for
Cleaning up the model code: prune duplicate/dead scripts, consolidate the scenario parser,
document the flag vocabulary, and get a minimal reproducible env spec. See `README.md`.

## Map of the code
- `pommes_eur/` — the model package (formerly `clever`; `import clever` still works via the
  `clever/` compat shim). Concern-based subpackages:
  - `scenario/` — `parse.py` (flag parsers) + `registry.py` (flag vocabulary + structural
    validation) + `env.py` (`POMMES_EUR_SCENARIO`/`CLEVER_SCENARIO` resolution) +
    `resolved.py` (the scenario string resolved to per-run scalar globals).
  - `data/` — `inputs.py` facade over `_inputs_generic.py` (dataset-agnostic scalars) +
    `_inputs_clever.py` (CLEVER/EOLES/PEMMDB tables); `expansion.py` + `vre_limits.py`
    (scenario-gated derived tables); `overrides.py` (thin back-compat facade re-exporting
    resolved + expansion + vre_limits + fuel prices).
  - `costs/` — `carbon_price.py`, `fuel_prices.py`. `sources/` — `fetch.py`, `process.py`,
    `demand.py`, `data_fetchers.py` (data acquisition).
  - `model/` — `build.py` (POMMES LP; formerly model.py) + `techs/` (`biomethane.py`,
    `ccs.py`, `mena_imports.py`); `__init__` re-exports `build` lazily (PEP 562).
  - `solve/` — `runner.py` (solve + NetCDF), `adequacy.py`. `providers/` — `base.py`
    protocol, `eraa.py` stub, and `clever/` (the CLEVER case study: `provider.py`,
    `dataset_overrides.py` + `override_inputs.py` — formerly the `r0_*` pipeline).
  - `constants.py` — top-level back-compat facade re-exporting parse+inputs+overrides.
  - Every old top-level module name (`inputs`, `model`, `runner`, `fetch`, …) remains a
    one-line `sys.modules` alias shim, so old `from clever.X import Y` imports keep working.
    `pyproject.toml` declares the flat package (deps mirror `requirements.txt`).
- `notebooks/adequacy_clean.ipynb` — the live driver; `jupyter nbconvert` turns it into
  `scripts/run_adequacy.py`, which the SLURM job (`scripts/sbatch_clever.sh`) executes.
  NOTE: the committed `run_adequacy.py` has diverged from the notebook (hand edits); treat the
  committed script as authoritative until the notebook is re-synced.
- `docs/flags.md` (auto-generated flag vocabulary), `docs/extending.md` (how to add a
  lever/tech/region/dataset). `tests/` — no-solve safety net (golden reproduction, registry,
  smoke, rename-shim, provider-contract). Regenerate flags: `python docs/gen_flags_doc.py`.
- `supplyforge/`, `demandforge/` — vendored data packages (imported by the model).

## How a run is defined
Everything is encoded in the **scenario string** (parsed in `pommes_eur/scenario/`), e.g.
`R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_pipekm1000`.
Flags: `noGas` (methane ban), `elecX180` (high elec demand), `h2HIGH` (industrial H2 demand),
`vreEXT`/`vreXXL` (VRE ceiling, normal/doubled), `nukeXXL` (nuclear ceiling doubled),
`pipekm1000` (pipeline €/MW/km), `storPx1000` (storage-power ×10), `voll900`/`h2voll400`
(demand-response VoLL), `menaOptim` (MENA imports), `vreFree` (drop VRE deployment floor).

## Conventions
- **Never commit** secrets or solver outputs: no `gurobi*.lic`, `*.nc`, `*.parquet`, `logs/`,
  `results*/`. The `.gitignore` already covers these — keep it that way.
- The ENTSO-E fetchers read `ENTSOE_API_TOKEN` from the environment; never hardcode a token.
- Clear notebook outputs before committing (they bulk up the repo and can leak the Gurobi
  LicenseID banner).

## Cleanup status
Done (see `CLEANUP_MISSION.md` phases): dead code removed (staged launchers, one-off
`_watchdog_*`/`verify_*`/`diagnose_*`, dead notebooks, nested `clever/clever/`, `smr_ccs.py`);
`constants.py` carved into `scenario/parse.py` + `inputs.py` + `overrides.py` (facade);
scenario registry replaces the `_VALID_SCENARIOS` whitelist; package renamed to `pommes_eur`
with compat shim; provider seam added. Every step is guarded by the golden snapshot
(`tests/golden/`) — inputs are bit-identical, no numerics changed.

Still candidate for cleanup (verify before acting): observatory generators in `notebooks/`
(`generate_observatory.py`, `generate_scenario_observatory.py`) may be dead relative to the
current paper workflow; the `run_adequacy.py` ↔ `adequacy_clean.ipynb` divergence should be
reconciled (re-sync the notebook or make the script the committed source of truth).
