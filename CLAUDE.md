# CLAUDE.md — orientation for Claude Code

This repo is a **code-only snapshot** of the CLEVER-2050 / POMMES model, copied from the
Aloret compute server for cleanup and version control. It does **not** run end-to-end here:
the solver outputs (`results/`, `*.nc`), the Gurobi license, and the data caches live only on
the cluster and are `.gitignore`d.

## What this is for
Cleaning up the model code: prune duplicate/dead scripts, consolidate the scenario parser,
document the flag vocabulary, and get a minimal reproducible env spec. See `README.md`.

## Map of the code
- `clever/` — the model package. Start with `constants.py` (scenario-string parser + all input
  assembly), `model.py` (the POMMES linear program), `runner.py` (solve + NetCDF extraction).
- `notebooks/adequacy_clean.ipynb` — the live driver; `jupyter nbconvert` turns it into
  `scripts/run_adequacy.py`, which the SLURM job (`scripts/sbatch_clever.sh`) executes.
- `supplyforge/`, `demandforge/` — vendored data packages (imported by the model).

## How a run is defined
Everything is encoded in the **scenario string** (parsed in `clever/constants.py`), e.g.
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

## Things known to need cleanup (candidates, verify before deleting)
- Duplicate launchers in `scripts/` (`run_adequacy_staged.py`, `run_adequacy_staged_v2.py`,
  and the generated `run_adequacy.py`).
- Observatory generators in `notebooks/` (`generate_observatory.py`, `generate_scenario_observatory.py`)
  may be dead relative to the current paper workflow.
