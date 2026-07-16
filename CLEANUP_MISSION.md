# Cleanup mission — `model-eu`

**One-line objective:** this is a working-but-messy research model; the job of this
session is **architecture cleanup** so that *extending* the model — new scenarios,
technologies, levers, regions — becomes fast and low-risk. It is **not** a rewrite and
**not** a change to any model numerics. Make the structure legible and the extension
points obvious.

> Read `CLAUDE.md` first for the map of the code and the run/convention rules.

---

## 1. How the model works today (the mental model)

The **scenario string** is the single source of configuration truth. Everything flows
from it:

```
scenario string  ──►  clever/constants.py (parse + assemble inputs)
                 ──►  clever/model.py      (build the POMMES linear program)
                 ──►  clever/runner.py     (solve with Gurobi + write NetCDF)
                 ──►  analysis / notebooks (read the NetCDF)
```

A run is launched by setting `CLEVER_SCENARIO=<string>` and executing
`scripts/run_adequacy.py` (itself generated from `notebooks/adequacy_clean.ipynb` via
`jupyter nbconvert`), typically through SLURM (`scripts/sbatch_clever.sh`).

**Flag vocabulary** (encoded in the string, parsed in `constants.py`):
`R0_v1` (base), `nuke` (nuclear on), `bioLow` (biomass level), `atr`, `el700` (H2 elec
price), `corr2x` (grid corridor headroom), `noGas` (methane ban), `elecX180` (high elec
demand), `h2HIGH` (industrial H2 demand), `vreEXT`/`vreXXL` (VRE ceiling normal/doubled),
`nukeXXL` (nuclear ceiling doubled), `nofloor`/`noElecFloor` (drop deployment floors),
`vreFree` (VRE fully cost-optimized), `ccsCap5` (CCS 5 GW/country cap), `co2150`,
`batt20`, `voll900`/`h2voll400` (demand-response VoLL), `menaOptim`/`menaCap200` (MENA
imports), `pipekm1000` (pipeline €/MW/km), `storPx1000` (storage-power ×10), `wyYYYY`
(weather year), `menaRisk..` (MENA WACC). **Extending the model almost always means
adding one of these flags** — so the parser is the architectural heart.

---

## 2. Why it's messy (the debt to pay down)

Concrete observations from this snapshot — **verify each before acting**:

1. **Duplicate nested package** `clever/clever/` shadows the top-level `clever/`
   (`constants.py`, `demand.py`, `fetch.py`, `runner.py`, `adequacy.py` appear in both).
   The live one is almost certainly the top-level `clever/` (that's what `import clever`
   resolves to under the cluster `PYTHONPATH`); the nested copy is likely dead. Confirm,
   then delete the dead one.
2. **`clever/constants.py` is a ~100 KB monolith** mixing four concerns: scenario-string
   parsing, input-data tables, per-country parameters, and override logic. This is where
   most of the friction lives.
3. **Adding a scenario is brittle.** New strings must be added by hand to a
   `_VALID_SCENARIOS` whitelist guarded by an `assert` in `run_adequacy.py`, and new
   flags need a `_parse_*` function *plus* wiring in several places. There is no single
   registry.
4. **Override layering is opaque.** Launcher / late-stage overrides can silently
   supersede values set in the modules, so tracing "where does this number come from" is
   hard. Cleanup should make the override order explicit and one-directional.
5. **Dead / duplicate drivers and cruft:**
   - `scripts/run_adequacy_staged.py` + `..._staged_v2.py` vs the generated
     `scripts/run_adequacy.py` — which are live?
   - many `scripts/_watchdog_*.sh` and one-off `verify_*.py` / `diagnose_*.py`.
   - stale notebooks (`notebooks/adequacy_2050.ipynb`, the `generate_observatory*.py`
     observatory generators) and LaTeX cruft (`notebooks/baseload_method.aux/.out`).
   - `notebooks/adequacy_clean.py` is a **build artifact** of the notebook — decide
     whether it belongs in git at all.

---

## 3. Target architecture (where to land)

A clean separation of the five concerns, each in its own place:

| concern | today | target |
|---|---|---|
| scenario spec | string + `_VALID_SCENARIOS` assert | a **scenario registry** (dataclass / dict) that parses once, validates, and is the only place flags are declared |
| input assembly | inside `constants.py` | its own module(s), pure functions returning tables |
| model build | `model.py` | unchanged in behaviour, but fed by explicit typed inputs |
| solve / IO | `runner.py` | unchanged |
| analysis | notebooks | out of scope for this repo |

Plus: **one authoritative `clever/` package**, a **flag-vocabulary table** in docs, an
**"how to add X" guide** (new tech / lever / region / scenario), and a **smoke test**
that builds the LP for one scenario *without solving* (fast, no license needed).

---

## 4. Phased plan (safe order — do them in sequence)

- **Phase 0 — inventory & safety net.** Map the import graph; confirm which modules are
  authoritative; pick one reference scenario string and capture its assembled inputs
  (shapes + key parameter values) as a **golden snapshot** to diff against after every
  later change.
- **Phase 1 — dead code & cruft removal** (lowest risk). Delete build artifacts, stale
  notebooks, `.aux`/`.out`, and duplicate launchers — but only after grepping that
  nothing imports/calls them.
- **Phase 2 — de-duplicate** the nested `clever/clever/` package.
- **Phase 3 — carve up `constants.py`** into `parse.py` (scenario string → typed spec),
  `inputs.py` (data tables), `overrides.py` (explicit, ordered).
- **Phase 4 — scenario registry** replacing the whitelist `assert`: declaring a flag once
  should be enough to make a scenario valid.
- **Phase 5 — document + test.** Flag table, extension guide, and the no-solve smoke test.

---

## 5. Guardrails (do not break)

- **Do not change model numerics** — no coefficient, cost, cap, or formulation changes.
  This is structural cleanup only.
- **Keep the scenario-string interface backward-compatible.** Existing strings must parse
  to *identical* inputs — the cluster result directories and the paper depend on exact
  string → directory names. The golden snapshot from Phase 0 is how you prove this.
- **Never commit secrets or data.** No `gurobi*.lic`, `*.nc`, `*.parquet`, `results*/`,
  `logs/` — `.gitignore` already covers these; keep it that way. ENTSO-E token stays in
  `ENTSOE_API_TOKEN`, never hardcoded.
- **You cannot run the full model here** (no Gurobi license, no data caches, no cluster).
  Validate by: importing modules, building the LP object, and diffing the golden input
  snapshot — **not** by full solves.
- **Small, reviewable commits**, one concern each. Prefer many tiny PRs over one big one.

---

## 6. Good first tasks (pick-up points)

1. Grep the import paths to confirm `clever/` (not `clever/clever/`) is authoritative;
   propose deleting the dead copy.
2. Generate the **flag-vocabulary table** by reading the `_parse_*` functions in
   `constants.py` — one row per flag: name, regex, what it changes, where it's wired.
3. Inventory `scripts/`: mark each file live vs dead (cross-reference `submit_*.sh`,
   `sbatch_clever.sh`, `run_scenarios_sequential.sh`).
4. Draft the Phase-0 golden-snapshot script (build inputs for one scenario, dump a hash
   of the key tables).

## 7. Explicitly out of scope

- Running or fetching data; changing the LP or any parameter; renaming scenario flags;
  touching `supplyforge/` or `demandforge/` internals beyond what the model imports.
