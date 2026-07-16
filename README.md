# pommes-eur

A general European power/hydrogen energy-system optimization model built on the POMMES
framework. **CLEVER-2050 is one activatable case study** of this model (the Joule paper on
hydrogen infrastructure under collective choices) — its data, cost assumptions, and
`R0_v1`/`policy_*` scenario semantics are supplied through a pluggable provider rather than
baked into the package.

> Formerly the `clever` package. `import clever` still works (a compatibility shim aliases
> `pommes_eur`), and the legacy `CLEVER_SCENARIO` / `CLEVER_DATA` env vars are still honored.

This is a **code-only snapshot** taken from the Aloret compute server for cleanup and
version control. Solver outputs (`results/`, `*.nc`), caches, logs and the Gurobi license
are intentionally excluded — see `.gitignore`.

## Layout

| path | what it is |
|------|------------|
| `pommes_eur/` | the model package, concern-based subpackages: `scenario/` (flag parsers + registry + env), `data/` (`inputs.py` over generic/CLEVER tables, `expansion.py`/`vre_limits.py` derived tables, `overrides.py` facade), `costs/` (carbon + fuel prices), `sources/` (fetch/process/demand/data_fetchers), `model/` (`build.py` + `techs/`), `solve/` (runner + adequacy), `scenario/resolved.py` (resolved scalars), `providers/` (adapters incl. `clever/` = provider + dataset/input overrides), `constants.py` (back-compat facade). Old top-level module names remain `sys.modules` alias shims. `pyproject.toml` declares the flat package. |
| `clever/` | compatibility shim — aliases `pommes_eur` so old `import clever` paths keep working |
| `notebooks/` | driver notebooks — `adequacy_clean.ipynb` (nbconvert → `scripts/run_adequacy.py`) |
| `scripts/` | cluster launchers — `sbatch_clever.sh` (SLURM), `submit_*.sh`, `run_scenarios_sequential.sh`, generated `run_adequacy.py` |
| `supplyforge/` | supply-side data package (VRE profiles, hydro, NTCs, ENTSO-E fetchers); its `create_pommes_craft_model.py` is the generic ERAA-driven reference builder |
| `demandforge/` | demand-side data package (load curves, ENTSO-E fetchers) |
| `docs/` | `flags.md` (auto-generated scenario-flag vocabulary), `extending.md` (how to add a lever/tech/region/dataset) |
| `tests/` | no-solve safety net — `golden/` (input reproduction), `test_registry.py`, `test_smoke_build.py`, `test_rename_shim.py` |

## Running (reference)

The full solve runs on the cluster under the `EOLES_POMMES` conda env (needs Gurobi +
`pommes`/`pommes_craft` + data caches). Key environment:

```bash
export CLEVER_WORK_ROOT=<repo root>
export PYTHONPATH="$CLEVER_WORK_ROOT/supplyforge:$CLEVER_WORK_ROOT:$CLEVER_WORK_ROOT/demandforge"
export GRB_LICENSE_FILE=<path to gurobi.lic>      # NOT in this repo
export ENTSOE_API_TOKEN=<your token>              # only needed for data fetching
export POMMES_EUR_SCENARIO=<scenario string>      # or legacy CLEVER_SCENARIO; e.g. R0_v1_nuke_..._pipekm1000
export CLEVER_GRB_THREADS=16
python scripts/run_adequacy.py
```

A scenario is fully described by its string (see `docs/flags.md`): flags like `noGas`,
`elecX180`, `h2HIGH`, `vreEXT`/`vreXXL`, `nukeXXL`, `pipekm1000`, `storPx1000`, … Validation
is structural — declaring a flag once in `pommes_eur/scenario/registry.py` makes any
well-formed scenario using it valid (no whitelist to edit).

## Verifying without a solver

The structural safety net runs with only `platformdirs` + the scientific stack — no Gurobi,
data caches, or `pommes_craft` needed:

```bash
python tests/golden/snapshot_constants.py --check   # inputs still bit-identical
python tests/test_registry.py                        # flags validate; spec == globals
python tests/test_smoke_build.py                     # LP builder contract (skips without deps)
python tests/test_rename_shim.py                     # clever -> pommes_eur alias identity
```

See `docs/extending.md` to add a lever, technology, region, or data source.
