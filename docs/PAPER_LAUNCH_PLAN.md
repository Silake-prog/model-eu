# Paper launch plan — *Fully Renewable Systems under Adequacy Stress*

> Brigode & Girard, *Energy Systems and Climate Change*. This plan turns the paper's
> research question into a concrete, launch-ready run matrix that a **Mac session**
> (the one with INARI/aloret access via your local SSH keys) can execute on the cluster.
> It also records how POMMES works, what was changed in this repo to make the coupling
> possible, and what still has to be built — especially the **NUTS-2** node path.

---

## 0. The question, restated as things to compute

The paper asks, for a **fully renewable** European system (no fossil methane, no nuclear),
coupled electricity **and** hydrogen, EU30, hourly, over **2040–2060**, drawing renewable
capacity factors from **PECD 4.2** year-on-year and country-by-country:

1. **Under which climate-change scenarios does the system come under adequacy stress?**
   → sweep climate axis (GCM × SSP × weather-year) at fixed demand, read the adequacy metric.
2. **How does that stress respond to electricity and hydrogen demand?**
   → sweep the demand axis (elec level × H₂ level) at fixed climate.
3. **Under which conditions is the stress lifted?**
   → sweep the "relief levers" (VRE ceiling, storage, interconnection, H₂ storage, MENA
   imports) and find where load-shedding returns to zero.

"Adequacy stress" in POMMES = the model is forced to activate **load shedding / VoLL**
(electricity or H₂) because no feasible zero-shedding dispatch exists. The output metric is
the per-run `adequacy_dashboard_*.csv` (shed energy, hours, VoLL cost), which the sweep
driver already collects into one tidy `adequacy_sweep.csv`.

Two node resolutions are wanted:
- **National** (35-area default, trimmed to EU30) — *ready today*.
- **NUTS-2** — *data exists, model wiring does not yet* (see §6).

---

## 1. How POMMES works (pipeline, briefly)

POMMES-EUR is a linear-programming Energy-System Optimisation Model. One run is fully
defined by a **scenario string** (e.g. `R0_v1_noGas_noNuke_bioLow_atr_el700_...`). The
pipeline (`scripts/run_adequacy.py`, which is the committed form of
`notebooks/adequacy_clean.ipynb`):

1. **Scenario parse** (`pommes_eur/scenario/`) — the string → a `ScenarioSpec` of scalar
   levers (demand multipliers, CO₂ cap, VRE ceiling, bans, storage costs, …). The registry
   (`scenario/registry.py`) is the single source of truth for the flag vocabulary and
   validates every string.
2. **Inputs** (`pommes_eur/data/` + `providers/clever/`) — per-country demand (electricity
   from CLEVER; hydrogen **computed live** from DemandForge for the active bundle), installed
   capacities, hydro inflows, and **renewable capacity factors** loaded from the supplyforge
   data contract: `RESULTS_DIR/capacity_factors/capacity_factors_<AREA>_<year>.parquet`.
3. **Build** (`pommes_eur/model/build.py`) — assembles the POMMES LP on `pommes_craft`
   primitives (areas, conversion/transport techs, links): VRE, dispatchable (biomethane,
   ATR/SMR-CCS, hydro), electrolysers, H₂ storage + pipelines, batteries, cross-border NTCs.
4. **Calibration** (`providers/clever/calibration_inputs.py`) — three per-country policy CSVs
   under `tables/<SCENARIO>/` (electrolyser sovereignty floors, H₂ underground-storage CAPEX
   and caps) become `dataset_calibration_kwargs`.
5. **Solve** (`pommes_eur/solve/runner.py`) — Gurobi (barrier) or HiGHS; writes NetCDF +
   the adequacy dashboard.
6. **Adequacy** (`solve/adequacy.py`) — post-processes shed energy / VoLL into the dashboard.

The renewable-availability source is **loader-transparent**: whatever writes the
`capacity_factors_<AREA>_<year>.parquet` files decides the climate. PECD 4.2 writes the same
filename contract, so *selecting a climate scenario = pointing the run at a different
`RESULTS_DIR`* (`SUPPLYFORGE_DATA`). Nothing in the LP changes.

---

## 2. What was changed in this repo to make the coupling possible

All changes golden-gated (constants byte-identical across 4 reference scenarios); 22/22 tests
green.

- **M1 — CLEVER H₂ glue relocated** into `pommes_eur/providers/clever/h2_network.py`
  (interconnections, pipeline/storage costs) and `h2_demand.py` (DemandForge H₂ demand).
  This decouples the model from the generic supplyforge internals and fixed a latent import
  bug. The model now consumes supplyforge only through the generic loader + `RESULTS_DIR`.
- **M2 — PECD run coupling.** Two levers that make a PECD climate run behave correctly:
  - `POMMES_EUR_VRE_RAW=1` → `vre_raw` path: passes `target_load_factor=None` to the VRE
    rescaler so the **PECD annual level (the climate signal) is preserved** instead of being
    renormalised to a CLEVER target. Also restricts the weather-year fallback to the requested
    year only (no silent 2021–23 substitution).
  - `_noNuke` scenario flag → `add_dispatchable_from_non_enr(no_nuke=True)` drops Nuclear and
    skips the force-inject. **"Fully renewable" = `_noGas_noNuke`.**
- **M3 — sweep driver** `scripts/run_pecd_sweep.py`: expands `(gcm × ssp × year) × demand ×
  mix` into per-combo runs, each with its own `CLEVER_SCENARIO`, `SUPPLYFORGE_DATA`,
  `CLEVER_DATA`, `POMMES_EUR_VRE_RAW=1`; collects all dashboards into `adequacy_sweep.csv`.
  Supports `--dry-run`, `--collect-only`, `--plots`, `--max-runs`, `--areas`.
- **Merge + hygiene** — merged `origin/pecd-supplyforge` (flat supplyforge with the
  PECD4.2/ERA5 integration: `res_source ∈ {entsoe, era5, pecd}`, `process/pecd/`,
  `fetch/pecd/`); removed the old nested package that shadowed it; PYTHONPATH is now
  `"$ROOT:$ROOT/demandforge"`; removed a hard-coded ENTSO-E token; `SOLVER` is env-overridable
  via `CLEVER_SOLVER`.

New/relevant knobs for the paper:

| Axis | Mechanism | Values |
|---|---|---|
| Climate scenario | `SUPPLYFORGE_DATA=<root>/<gcm>_<ssp>_<year>` | GCM × SSP × weather-year |
| Preserve climate signal | `POMMES_EUR_VRE_RAW=1` | on for all PECD runs |
| Mix | scenario flags | `fullyRE`=`noGas_noNuke`, `nukeAllowed`=`noGas_nuke` |
| Electricity demand | `elecX###` flag | e.g. `elecX125`, `elecX180` |
| Hydrogen demand | `h2HIGH`/`h2central`/none | high_h2 ≈1400 / central ≈898 / low_h2 ≈456 TWh |
| Relief levers | flags | `vreEXT`/`vreXXL`, `batt##`, `pipekm####`, `storPx####`, `menaOptim` |
| Node set | `CLEVER_AREAS` | subset of the 35 default areas (trim to EU30) |

---

## 3. The launch matrix (national nodes — ready today)

Base + fixed context reused from the driver defaults:
`base = R0_v1`, `extra = bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_co2150_batt20`.
Fully-renewable mix = `noGas_noNuke`. All runs set `POMMES_EUR_VRE_RAW=1`.

**Block A — climate stress map (Q1).** Fix demand at the paper's headline
(`elecX180_h2HIGH`, high electrification + high H₂), sweep the climate:
- GCMs: start `ec_earth3` (in repo), add the others once fetched.
- SSPs: `ssp2_4_5`, `ssp5_8_5`.
- Weather years: the full **2040–2060** span (21 years) — this is the paper's horizon and the
  axis that reveals which climate years break adequacy.

**Block B — demand response (Q2).** Fix a median-stress climate year from Block A, sweep
demand: `{elecX125, elecX150, elecX180} × {—, h2central, h2HIGH}` (9 points).

**Block C — relief levers (Q3).** Fix the worst climate×demand corner from A/B, sweep one
lever at a time back toward zero shedding:
`vreEXT→vreXXL`, `batt20→batt40`, `pipekm1000→pipekm500`, `storPx1000`, `menaOptim`.

**Reference contrast.** Re-run Block A with `nukeAllowed` (`noGas_nuke`) to quantify how much
of the stress is specifically due to the *fully-renewable* (no-nuclear) constraint.

Driver invocation (from the Mac session, on INARI):

```bash
# Block A — climate stress map, fully-renewable, headline demand
python scripts/run_pecd_sweep.py \
  --datasets-root /diskdata/cired/brigode/clever-work/results/pecd_datasets \
  --out          /diskdata/cired/brigode/clever-work/results/paper_sweep/blockA \
  --gcms  ec_earth3 \
  --ssps  ssp2_4_5,ssp5_8_5 \
  --years 2040,2041,2042,2043,2044,2045,2046,2047,2048,2049,2050,2051,2052,2053,2054,2055,2056,2057,2058,2059,2060 \
  --demands elecX180_h2HIGH \
  --mixes  fullyRE,nukeAllowed \
  --dry-run          # inspect the grid first, then drop --dry-run to launch
```

`--dry-run` prints every combo, its scenario string, and its dataset dir without solving —
always run it first. `--collect-only` re-scans an output dir into `adequacy_sweep.csv`;
`--plots` renders the summary figures.

---

## 4. How to launch from the Mac session (checklist)

On the Mac session (it has INARI via your keys — this cloud session does not):

1. **Sync the branch:** `git fetch && git checkout claude/clean-up-brief-31atkf` (push signal
   pending — see §7), or copy the tree to `/diskdata/cired/brigode/clever-work`.
2. **Env:** `export PYTHONPATH="$ROOT:$ROOT/demandforge"` (ROOT = the work root),
   `export CLEVER_WORK_ROOT=$ROOT`, Gurobi license present.
3. **Data prerequisites on INARI:**
   - PECD datasets assembled per climate point: `results/pecd_datasets/<gcm>_<ssp>_<year>/
     capacity_factors/capacity_factors_<AREA>_<year>.parquet` (see §6 for building them).
   - `tables/<SCENARIO>/` calibration CSVs (already on the cluster).
4. **Smoke first (single combo, few countries):**
   ```bash
   CLEVER_AREAS=FR,DE,BE,NL,ES POMMES_EUR_VRE_RAW=1 \
   CLEVER_SCENARIO=R0_v1_noGas_noNuke_bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_co2150_batt20_h2HIGH \
   SUPPLYFORGE_DATA=$ROOT/results/pecd_datasets/ec_earth3_ssp5_8_5_2050 \
   CLEVER_DATA=$ROOT/results/smoke_fr_de \
   python scripts/run_adequacy.py
   ```
   Confirm it builds, solves, and writes an adequacy dashboard. Then scale to the full sweep.
5. **Batch:** wrap the sweep in `scripts/sbatch_clever.sh` (SLURM); each combo is independent
   and parallelises cleanly across nodes.
6. **Collect:** `python scripts/run_pecd_sweep.py --collect-only --out <dir>` →
   `adequacy_sweep.csv`, then `--plots`.

---

## 5. Reading the results

`adequacy_sweep.csv` is tidy: one row per `(gcm, ssp, year, demand, mix, [area])` with shed
energy / hours / VoLL cost. For the paper:
- **Q1 map:** heat-map shed-energy over (weather-year × SSP) at fixed demand, per GCM.
- **Q2 curves:** shed vs elec-demand, one line per H₂ level, at the chosen climate year.
- **Q3 relief:** bar/tornado of shed-energy reduction per lever from the worst corner.
- **Cross-border balancing** (the preliminary-results figure): the 920 TWh / 53-interconnection
  exchange map comes from the NTC flows in each run's NetCDF (national resolution).

---

## 6. What remains to be done

**(a) NUTS-2 node path — the main build item.** The PECD *data* already ships both
resolutions (`results/pecd_study/nuts_2/…` and `szon/…`, and the in-repo caches include
NUTS-level BE zones like `BE21`), but **`pommes_eur` consumes national area codes only**.
To run at NUTS-2 you need to wire:
  - **Area set:** teach `CLEVER_AREAS`/`DEFAULT_KEEP_AREAS` (and the demand/capacity/hydro
    loaders) to accept NUTS-2 codes and read `capacity_factors_<NUTS2>_<year>.parquet`.
  - **Demand disaggregation:** national electricity + H₂ demand → NUTS-2 (population / activity
    weights; DemandForge has population-weighting helpers to reuse).
  - **Topology:** an intra-national NUTS-2 adjacency + NTC/H₂-pipeline graph (today's
    `H2_ADJACENCY` and electrical NTCs are national-pair).
  - **Calibration:** the three `tables/` policy CSVs are keyed by national `area`; decide
    whether floors/storage caps stay national (aggregate constraint) or split to NUTS-2.
  Recommend: land national runs for the paper's headline results first, then add NUTS-2 as a
  spatial-resolution robustness section (it is a feature, not a config flag).

**(b) PECD dataset assembly for 2040–2060.** In-repo `results/pecd_study/` has only **2050,
ec_earth3, ssp2_4_5 + ssp5_8_5**. The paper needs the full weather-year span per GCM/SSP built
into per-point `capacity_factors_<AREA>_<year>.parquet`. Use the supplyforge PECD pipeline
(`fetch/pecd/study.py` → `process/pecd/capacity_factor.py`, `res_source: pecd`) on INARI (needs
a CDS account/`.cdsapirc`). One `SUPPLYFORGE_DATA` dir per `(gcm, ssp, year)`.

**(c) EU30 scoping.** The default area set is 35 (incl. GB, NO, CH, Balkans). Decide the exact
EU30 membership for the paper and pin it via `CLEVER_AREAS` (or trim `DEFAULT_KEEP_AREAS`).

**(d) GCM breadth.** Only `ec_earth3` is staged. Add the other PECD GCMs so Q1's "which climate
scenarios" spans the ensemble, not one model.

**(e) Verification once data lands.** Re-run the golden check and a single-combo smoke on INARI
to confirm the PECD path preserves the annual level end-to-end (M2 guarantee) before the full
sweep.

---

## 7. Repo status (this cloud session)

- Branch `claude/clean-up-brief-31atkf`: cleanup + PECD coupling committed **locally**,
  **not pushed** (awaiting your push signal). Tree clean, 22/22 tests green, golden identical.
- This cloud container has **no INARI access** (HTTPS-only egress, no SSH client/keys) — all
  cluster launches happen from your **Mac** session; this plan is the hand-off.
