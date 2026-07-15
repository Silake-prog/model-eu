# clever-model

Core model code for the CLEVER-2050 / POMMES European hydrogen-infrastructure study
(the Joule paper on hydrogen infrastructure under collective choices).

This is a **code-only snapshot** taken from the Aloret compute server for cleanup and
version control. Solver outputs (`results/`, `*.nc`), caches, logs and the Gurobi license
are intentionally excluded — see `.gitignore`.

## Layout

| path | what it is |
|------|------------|
| `clever/` | the model package — `constants.py` (scenario parser + inputs), `model.py` (POMMES LP build), `runner.py` (solve/extract), `demand.py`, `fetch.py`, `process.py`, `carbon_price.py`, `smr_ccs.py` … |
| `notebooks/` | driver notebooks — `adequacy_clean.ipynb` is the current launcher (nbconvert → `scripts/run_adequacy.py`) |
| `scripts/` | cluster launchers — `sbatch_clever.sh` (SLURM), `submit_*.sh`, `run_scenarios_sequential.sh`, generated `run_adequacy.py` |
| `supplyforge/` | supply-side data package (VRE profiles, hydro, NTCs, ENTSO-E fetchers) |
| `demandforge/` | demand-side data package (load curves, ENTSO-E fetchers) |

## Running (reference)

The model runs on the cluster under the `EOLES_POMMES` conda env. Key environment:

```bash
export CLEVER_WORK_ROOT=<repo root>
export PYTHONPATH="$CLEVER_WORK_ROOT/supplyforge:$CLEVER_WORK_ROOT:$CLEVER_WORK_ROOT/demandforge"
export GRB_LICENSE_FILE=<path to gurobi.lic>      # NOT in this repo
export ENTSOE_API_TOKEN=<your token>              # only needed for data fetching
export CLEVER_SCENARIO=<scenario string>          # e.g. R0_v1_nuke_..._pipekm1000
export CLEVER_GRB_THREADS=16
python scripts/run_adequacy.py
```

A scenario is fully described by its string (parsed in `clever/constants.py`): flags like
`noGas`, `elecX180`, `h2HIGH`, `vreEXT`/`vreXXL`, `nukeXXL`, `pipekm1000`, `storPx1000`, etc.

## Cleanup goals (this repo's purpose)

- Prune dead/duplicate scripts and stale notebooks.
- Consolidate the scenario-string parser and document the flag vocabulary.
- Add a minimal `requirements`/env spec and a runnable smoke test.
