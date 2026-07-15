# Prompt: Build a Clean Adequacy Notebook for the CLEVER Sufficiency Scenario

## Context & Objective

You are tasked with creating a **new, clean, well-structured Jupyter notebook** that assesses the **adequacy of the European electricity system** under the CLEVER sufficiency scenario at horizon 2050. The scenario assumes a fully renewable energy mix (full ENR) combined with sufficiency measures (demand reduction via sobriety). The notebook must answer one core question:

> **Can the electricity system reliably meet demand at every hour of the year under a 100% renewable + sufficiency pathway, and if not, when, where, and why does it fail?**

This new notebook replaces the existing `adequacy_2050.ipynb` (74 cells, 18+ sections), which grew organically and suffers from structural issues. The goal is a notebook that is **more logical in flow, more modular in code, more transparent in assumptions, and more rigorous in its treatment of the two key problems: supply-side ramping and demand-side flexibility.**

---

## 1. What Has Been Done (Current State)

### 1.1 The Modelling Pipeline

The current system chains several modules into a pipeline:

1. **CLEVER spreadsheet** (external Excel file) provides country-level capacity targets, energy balances, and fuel mixes for the 2050 sufficiency scenario.
2. **`clever.process`** parses the CLEVER spreadsheet into structured CSV tables (capacity by tech, VRE load factors, fuel data).
3. **`clever.demand` + DemandForge`** builds hourly electricity demand profiles for ~10 European countries. Demand is decomposed into four components: baseload, winter thermosensitive (heating), summer thermosensitive (cooling), and EV charging.
4. **`clever.model`** assembles a multi-country `pommes_craft.EnergyModel` from the processed data. Each country becomes an `Area` with `ConversionTechnology` (solar, wind, nuclear, gas, H2), `StorageTechnology` (batteries, pumped hydro, reservoir hydro), `Demand`, `LoadShedding`, and `Spillage` components. Countries are connected via `Link` components representing NTC-based interconnections.
5. **`clever.runner`** converts the high-level model to POMMES format, sanitises inputs, builds the linopy optimisation problem, and solves it with Gurobi.
6. **Post-processing** extracts prices (dual variables), capacities, storage dispatch, cross-border flows, and computes adequacy metrics (LOLE, ENS).

### 1.2 Code Architecture

The code is split across several modules:

| Module | Location | Role | ~Lines |
|--------|----------|------|--------|
| `clever.constants` | `PYCHARM_PROJECTS--clever/constants.py` | Single source of truth: tech mappings, VRE profiles, hydro specs, costs, NTC values, expansion headrooms | 800+ |
| `clever.model` | `PYCHARM_PROJECTS--clever/model.py` | Build `EnergyModel` from CLEVER data: capacities, profiles, costs, multi-country assembly | 2000+ |
| `clever.runner` | `PYCHARM_PROJECTS--clever/runner.py` | Solver interface: sanitisation, POMMES build, Gurobi solve, result extraction | 1300+ |
| `clever.demand` | `PYCHARM_PROJECTS--clever/demand.py` | DemandForge integration: hourly profiles, thermosensitivity, EV decomposition | 1200+ |
| `clever.adequacy` | `PYCHARM_PROJECTS--clever/adequacy.py` | Adequacy assessment: ex-ante peak margin, ex-post LOLE/ENS | 400+ |
| `clever.process` | `PYCHARM_PROJECTS--clever/process.py` | CLEVER XLSX parsing: capacity, VRE, fuel extraction | 650+ |
| `pommes_craft` | `tp_pommes_kraft/pommes_craft/` | High-level model API: Area, ConversionTechnology, StorageTechnology, FlexibleDemand, Link, etc. | Library |
| `pommes` | `tp_pommes_kraft/pommes/` | Core optimisation model: constraint generation (conversion, storage, transport, flex demand, load shedding) | Library |

### 1.3 The Optimisation Problem

**Objective:** Minimise total annualised system cost:
```
min  SUM[ CAPEX_annualised + FOM + VOM + fuel_costs
       + 30,000 EUR/MWh * load_shedding
       + spillage_cost
       + 10 EUR/MWh * flexible_demand_activation ]
```

**Key decision variables:**
- `conversion_power_capacity_op[area, tech, hour]` — hourly dispatch of each generator
- `conversion_power_capacity_invest[area, tech]` — new capacity investment
- `storage_level[area, tech, hour]` — state of charge
- `operation_load_shedding_power[area, hour]` — unserved energy (penalty variable)
- `operation_spillage_power[area, hour]` — VRE curtailment
- `transport_power[link, hour]` — cross-border flows

**Key constraints:**
1. **Energy balance** per area per hour: generation + imports + storage_discharge = demand + exports + storage_charge + load_shedding
2. **Capacity bounds**: dispatch <= installed capacity * availability factor
3. **Storage dynamics**: level(h+1) = level(h) * efficiency - discharge + charge
4. **Transport**: flow <= NTC capacity (bidirectional)
5. **Flexible demand**: conservation window (6h), bounds (85%–115% of flex profile)
6. **Ramping**: `output(h) - output(h-1) <= ramp_rate * capacity` — **currently disabled (see below)**

**Solver configuration:** Gurobi, interior-point method (Method=2), NumericFocus=3 (high precision). Problem size: ~500k–1M variables, ~1–2M constraints for 10-15 countries at 8760 hourly timesteps.

### 1.4 Input Data

- **EOLES cost assumptions** (`eoles_inputs/`): CAPEX, FOM, VOM, storage costs, lifetimes, discount rates — all for reference year 2026, extrapolated to 2050 via learning curves
- **CLEVER scenario data**: Country-level installed capacities (GW), annual energy by fuel (TWh), VRE capacity factors
- **Demand profiles**: Generated by DemandForge, accounting for thermosensitivity and EV charging patterns
- **VRE profiles**: Hourly solar/wind capacity factors from SupplyForge (or flat fallback values)
- **Interconnections**: NTC values from ENTSO-E ERAA, stored in `clever.constants`

### 1.5 Key Outputs (from current notebook)

The current notebook produces 18+ sections of analysis:
- Ex-ante adequacy screening (peak margin check)
- Price analysis (means, distributions, weekly profiles, heatmaps)
- Optimal capacity mix (investment decisions)
- VRE curtailment/spillage volumes
- LOLE and ENS by country
- Storage dynamics (SOC profiles)
- Cross-border flow patterns
- Residual load duration curves
- Demand flexibility activation
- Final adequacy dashboard

---

## 2. The Two Main Problems

### 2.1 Problem 1: Ramping Constraints Are Disabled

**What it is:** Ramping refers to the physical limitation on how quickly a power plant can increase or decrease its output. A nuclear plant cannot go from 0 to full power in one hour; a gas turbine can ramp faster but not instantaneously.

**Current treatment in POMMES:**
In `runner.py`, the function `sanitize_absent_conversions()` sets:
```python
p["conversion_ramp_up"] = xr.full_like(template_op, 1.0, dtype=float)
p["conversion_ramp_down"] = xr.full_like(template_op, 1.0, dtype=float)
```
This means **every technology can ramp from 0 to 100% capacity in a single hour** — effectively no ramping constraint at all. The solve function is even named `run_model_without_ramping()`.

**Why it matters for adequacy:**
In a system dominated by variable renewables, residual demand (demand minus VRE) can swing dramatically between consecutive hours (e.g., solar cliff at sunset, wind ramp-down events). If dispatchable plants (nuclear, gas, H2 turbines, hydro) cannot physically ramp fast enough, the system faces **ramping-induced adequacy failures** even when total installed capacity is sufficient. Disabling ramping constraints produces an **optimistic bias** — the model never sees hours where generation cannot follow the load because of inertia.

**Typical ramping rates:**
- Nuclear: 1–5% of capacity per minute (full ramp in 20–100 min)
- CCGT: 5–8% per minute
- OCGT: 10–20% per minute
- Hydro reservoir: near-instantaneous
- Batteries: instantaneous
- Run-of-river: not dispatchable (must-run)

**What the new notebook should do:**
- Run a **baseline without ramping** (as today) and a **sensitivity with realistic ramping rates**
- Compare LOLE, ENS, and price distributions between both runs
- Identify the hours and countries where ramping constraints become binding
- Quantify how much additional flexibility (storage, demand response, interconnection) is needed to compensate

### 2.2 Problem 2: Demand-Side Flexibility Is Simplistic

**What it is:** Demand-side flexibility means shifting electricity consumption in time (e.g., charging EVs at night instead of evening peak) or reducing it temporarily (e.g., industrial load shedding). In a sufficiency scenario, the very nature of demand changes (lower overall, more electrified transport and heating), making flexibility both more important and differently structured.

**Current treatment in POMMES:**
The `FlexibleDemand` component is added post-hoc to the model with these parameters:
```python
FLEX_DEMAND_FRACTION = 0.08      # 8% of demand is flexible (EV share)
FLEX_CONSERVATION_HRS = 6        # energy must be returned within 6 hours
FLEX_MAX_MULTIPLIER = 1.15       # flex load can rise to 115% of nominal
FLEX_MIN_MULTIPLIER = 0.85       # flex load can drop to 85% of nominal
FLEX_RAMP_UP = NaN               # unlimited ramp (no constraint)
FLEX_RAMP_DOWN = NaN             # unlimited ramp
FLEX_VARIABLE_COST = 10 EUR/MWh  # activation cost
```

**Why it matters:**
- **Only 8%** of demand is flexible. In a 2050 sufficiency scenario with massive electrification of transport and heat pumps, the true flexible share could be 15–30%.
- The **conservation window is fixed at 6 hours** — but EV charging could shift over 12–24h, while heat pump flexibility might be only 2–4h.
- **Ramp limits on flexibility are disabled** (NaN) — meaning flexible demand can jump instantaneously, which is unrealistic for coordinating millions of distributed loads.
- **No distinction between flexibility sources**: EVs, heat pumps, industrial loads, and household appliances have very different shifting capabilities, costs, and constraints.
- **No seasonal variation**: flexibility availability changes with season (more EV in summer? more heat pump in winter?).
- The activation cost of 10 EUR/MWh is a single flat value — in reality, flexibility cost increases with the magnitude and duration of the shift.

**What the new notebook should do:**
- Decompose flexible demand into **distinct categories** (EV, heat pump, industrial, other) with separate conservation windows, ramp limits, and costs
- Run sensitivities on the **total flexible fraction** (8%, 15%, 25%) and **conservation window** (4h, 6h, 12h, 24h)
- Show **when and how much** flexibility is activated, and whether it reduces LOLE
- Cross-analyse flexibility with ramping: does demand flexibility compensate for supply-side ramping limitations?

---

## 3. Structural Problems with the Current Notebook

1. **Organic growth**: 74 cells, 18+ sections accumulated over iterative development. No clear separation between data preparation, model building, solving, and analysis.
2. **Imports scattered everywhere**: Libraries are imported in the cell where first needed rather than at the top. Makes dependencies hard to track.
3. **Redundant data loading**: `xarray` datasets are opened multiple times in different cells. Some cells re-read `solution_2050.nc` from disk even though it is already loaded.
4. **No configuration cell**: Parameters are embedded in code cells. Changing a scenario requires hunting through multiple cells.
5. **No modular functions for analysis**: Each analysis section (prices, LOLE, spillage, etc.) is written inline rather than in reusable functions.
6. **Outputs not saved**: Figures and tables are displayed but not exported to files for reproducibility.
7. **No scenario comparison**: The notebook runs a single scenario. There is no structure for comparing with/without ramping, different flexibility levels, etc.
8. **Error handling is fragile**: Many cells use `try/except` with broad catches, hiding real issues.

---

## 4. Specification for the New Notebook

### 4.1 Structure (Suggested Sections)

```
0. Header & Objective
   - Research question, scenario description, key assumptions

1. Configuration
   - All parameters in one place: year, solver, countries, paths
   - Ramping scenario (on/off, rates by tech)
   - Flexibility scenario (fraction, window, categories)

2. Data Ingestion
   - Fetch and process CLEVER spreadsheet
   - Load EOLES cost assumptions
   - Build hourly demand profiles
   - Load VRE capacity factor profiles
   - Summary tables of inputs (capacity by country, demand by country)

3. Model Construction
   - Build multi-country EnergyModel
   - Add flexibility components (parameterised from Section 1)
   - Add ramping constraints (parameterised from Section 1)
   - Ex-ante adequacy check (peak margin)
   - Model summary: variables, constraints, technologies per country

4. Solve
   - Solver configuration
   - Solve and capture diagnostics
   - Feasibility check

5. Adequacy Analysis (Core Results)
   - LOLE and ENS by country
   - Load shedding time series (when and where)
   - Adequacy dashboard

6. Dispatch & Price Analysis
   - Stacked dispatch plots (generation by tech)
   - Price duration curves
   - Price statistics and heatmaps

7. Flexibility & Ramping Deep-Dive
   - Demand flexibility activation (when, how much, which category)
   - Ramping constraint activation (which techs are binding, when)
   - Interaction between flexibility and ramping
   - Residual load analysis

8. Storage & Interconnection
   - Storage SOC profiles
   - Cross-border flow analysis
   - Contribution of storage/trade to adequacy

9. Sensitivity & Scenario Comparison
   - Side-by-side: with vs without ramping
   - Side-by-side: low vs high flexibility
   - Tornado chart of parameter sensitivity

10. Conclusions & Export
    - Summary table
    - Export results to CSV/Excel
    - Save figures
```

### 4.2 Design Principles

- **All imports at the top** of the notebook (Cell 0 or 1)
- **Single configuration cell** with all tuneable parameters, clearly documented
- **Helper functions** in dedicated cells (or imported from a `utils.py` module), not inline code
- **Consistent variable naming**: `sol_ds` for solution dataset, `dual_ds` for duals, `inp_ds` for inputs — defined once, used everywhere
- **Figures saved to disk** with consistent naming (`figures/{section}_{name}.png`)
- **Scenario-aware**: The notebook should accept a scenario dictionary and produce results for that scenario. Running two scenarios = running the notebook twice with different config, then comparing.
- **Clear markdown cells** explaining the "why" before each code cell
- **No redundant code**: If two sections need the same data extraction, factor it into a function

### 4.3 Ramping Treatment (New) — POMMES-Compatible Implementation

#### How POMMES handles ramping internally

POMMES expects three xarray DataArrays for ramping, all with the same dimensions as `conversion_power_capacity_max` (i.e., dims: `area`, `conversion_tech`, `year_op`):

| Parameter | Type | Meaning |
|-----------|------|---------|
| `conversion_ramp_up` | float | Max ramp-up per hour as fraction of capacity (0.0–1.0) |
| `conversion_ramp_down` | float | Max ramp-down per hour as fraction of capacity (0.0–1.0) |
| `conversion_ramp_relative_to_capacity` | bool | Always True in current code (absolute MW/h NOT supported) |

#### The critical problem: `sanitize_absent_conversions()` overwrites everything

In `runner.py` (lines 381–387), the sanitisation step **unconditionally overwrites** all ramp values:
```python
template_op = p["conversion_power_capacity_max"]
p["conversion_ramp_up"] = xr.full_like(template_op, 1.0, dtype=float)
p["conversion_ramp_down"] = xr.full_like(template_op, 1.0, dtype=float)
p["conversion_ramp_relative_to_capacity"] = xr.full_like(template_op, True, dtype=bool)
```

This means any per-technology values set by pommes_craft's `to_pommes_model()` are **discarded before `build_model()` is called**. The function `run_model_without_ramping()` enforces this by design.

#### Implementation strategy: patch ramp values AFTER sanitisation

The notebook must **not** modify `runner.py`. Instead, create a wrapper function that:
1. Calls `run_model_without_ramping()` as usual — but intercepts the xarray dataset `p` after sanitisation and before `build_model()`.
2. **OR** (simpler approach): call `run_model_without_ramping()` as-is for the baseline, then for the ramping scenario, write a **`run_model_with_ramping()`** function in the notebook that duplicates the solve pipeline but replaces the global 1.0 values with per-technology values.

The recommended approach is **Option B** — a notebook-local function:

```python
def run_model_with_ramping(
    model, solver_name, solver_options, year_op, ramp_rates,
    write_lp=False, diagnostics_dir=None,
):
    """
    Same as run_model_without_ramping(), but applies per-tech ramp rates
    AFTER sanitisation and BEFORE build_model().

    ramp_rates: dict mapping tech_name -> (ramp_up_frac, ramp_down_frac)
        Values are fractions of capacity per hour (0.0 to 1.0).
        Techs not in the dict keep the default 1.0 (unrestricted).
    """
    from pommes.io.build_input_dataset import build_input_parameters
    from pommes.model.build_model import build_model
    from pommes.model.data_validation.dataset_check import check_inputs
    from clever.runner import (
        sanitize_absent_conversions,
        sanitize_storage_inputs,
        sanitize_transport_inputs,
        build_solver_options,
    )

    # Step 1: Convert pommes_craft model -> POMMES parameter tables
    pommes_model = model.to_pommes_model()
    p = build_input_parameters(pommes_model.config, pommes_model.parameter_tables)

    # Step 2: Standard sanitisation (sets all ramps to 1.0)
    check_inputs(p)
    sanitize_absent_conversions(p)
    sanitize_storage_inputs(p)
    sanitize_transport_inputs(p)

    # Step 3: OVERRIDE ramp values per technology <<<< THIS IS THE KEY STEP
    tech_dim = "conversion_tech"  # verify exact dim name from p.dims
    for tech_name, (ramp_up, ramp_down) in ramp_rates.items():
        if tech_name in p["conversion_ramp_up"].coords[tech_dim].values:
            p["conversion_ramp_up"].loc[{tech_dim: tech_name}] = ramp_up
            p["conversion_ramp_down"].loc[{tech_dim: tech_name}] = ramp_down
        else:
            print(f"  WARNING: tech '{tech_name}' not found in model dims")

    # Step 4: Build and solve
    linopy_model = build_model(p)
    linopy_model.solve(solver_name=solver_name, **solver_options)

    # Step 5: Write diagnostics if requested
    if diagnostics_dir:
        p.to_netcdf(diagnostics_dir / f"input_dataset_{year_op}.nc")
        # ... etc

    return linopy_model
```

**IMPORTANT NOTES:**
- The exact dimension name for technologies must be verified at runtime (likely `"conversion_tech"` but could be `"conversion_technology"`). Print `p["conversion_ramp_up"].dims` to check.
- Technology names in POMMES may differ from display names (e.g., `"nuclear"` not `"Nuclear"`, `"ch4_ccgt"` not `"CH4_CCGT"`). Print `p["conversion_ramp_up"].coords` to get exact names.
- Ramp values are **fractions of installed capacity per hour** (relative, not absolute). A value of 0.05 means the plant can change output by at most 5% of its nameplate capacity per hour.
- VRE technologies (solar, wind) should keep ramp=1.0 because their output is limited by the resource profile, not by physical ramping.

#### Configuration block

```python
RAMPING_ENABLED = True  # Toggle: False = baseline (all 1.0), True = per-tech ramps

# Values are (ramp_up_frac_per_hour, ramp_down_frac_per_hour)
# IMPORTANT: Use the EXACT technology names from the POMMES model.
# Run: print(p["conversion_ramp_up"].coords) to discover them.
RAMP_RATES = {
    # Slow thermal
    "nuclear":          (0.05, 0.05),   # 5%/h — conservative French fleet estimate
    # Gas
    "ch4_ccgt":         (0.50, 0.50),   # 50%/h — modern CCGT
    "ch4_ocgt":         (1.00, 1.00),   # 100%/h — fast peaker
    # Hydrogen
    "h2_ccgt":          (0.50, 0.50),   # assumed similar to gas CCGT
    # Hydro
    "reservoir_hydro":  (1.00, 1.00),   # near-instantaneous
    "run_of_river":     (1.00, 1.00),   # must-run but ramp not relevant (profile-limited)
    # VRE — always 1.0 (resource-limited, not ramp-limited)
    "solar":            (1.00, 1.00),
    "wind_onshore":     (1.00, 1.00),
    "wind_offshore":    (1.00, 1.00),
}
```

### 4.4 Flexibility Treatment (New) — POMMES-Compatible Implementation

#### How POMMES FlexibleDemand works

`pommes_craft.FlexibleDemand` is a component added to an `Area`. Its constructor expects:

| Parameter | Type | Required | Meaning |
|-----------|------|----------|---------|
| `name` | str | Yes | Unique component name (e.g., `"ev_flex_FR"`) |
| `resource` | str | Yes | Always `"electricity"` |
| `demand` | pl.DataFrame | Yes | Hourly flex demand profile. Columns: `hour` (0–8759), `year_op`, `demand` (MW) |
| `conservation_hrs` | int | Yes | Rolling window (hours) within which shifted energy must net to zero |
| `ramp_up` | float | No | Max ramp-up rate. `np.nan` = unlimited |
| `ramp_down` | float | No | Max ramp-down rate. `np.nan` = unlimited |
| `max_demand` | pl.DataFrame | Yes | Hourly upper bound. Columns: `hour`, `year_op`, `max_demand` (MW) |
| `min_demand` | pl.DataFrame | Yes | Hourly lower bound. Columns: `hour`, `year_op`, `min_demand` (MW) |
| `variable_cost` | float | No | Activation cost in EUR/MWh. `np.nan` = 0 |

The flex demand profile is **subtracted from the main Demand** component — it represents the portion of demand that the optimiser is allowed to shift within the conservation window, subject to hourly min/max bounds.

#### Can you have multiple FlexibleDemand components per Area?

**Status: UNTESTED and RISKY.** The current code only ever adds one FlexibleDemand per area. There is no explicit prohibition, but:
- Parameter table keys may collide when `to_pommes_model()` serialises multiple FlexibleDemand components
- The energy balance constraint may not correctly account for multiple flex components
- The conservation window constraint may produce infeasibilities if two flex components overlap

#### Recommended approach: SINGLE aggregated FlexibleDemand per area (safe)

Instead of multiple `FlexibleDemand` components, **aggregate the flexible categories into a single component** with blended parameters. This is the safe, POMMES-compatible approach:

```python
# ── Configuration: define categories for documentation, then aggregate ──

FLEX_CATEGORIES = {
    "ev": {
        "fraction_of_total_demand": 0.08,
        "conservation_hours": 12,
        "max_multiplier": 1.30,
        "min_multiplier": 0.50,
        "variable_cost": 5.0,
    },
    "heat_pump": {
        "fraction_of_total_demand": 0.10,
        "conservation_hours": 4,
        "max_multiplier": 1.20,
        "min_multiplier": 0.80,
        "variable_cost": 15.0,
    },
    "industrial": {
        "fraction_of_total_demand": 0.05,
        "conservation_hours": 8,
        "max_multiplier": 1.10,
        "min_multiplier": 0.70,
        "variable_cost": 25.0,
    },
}

# ── Aggregate into a single FlexibleDemand per area ──
# Total flex fraction
FLEX_TOTAL_FRACTION = sum(c["fraction_of_total_demand"] for c in FLEX_CATEGORIES.values())
# Weighted-average conservation window (weighted by demand fraction)
FLEX_CONSERVATION_HRS = int(round(
    sum(c["fraction_of_total_demand"] * c["conservation_hours"] for c in FLEX_CATEGORIES.values())
    / FLEX_TOTAL_FRACTION
))
# Weighted-average multipliers
FLEX_MAX_MULTIPLIER = (
    sum(c["fraction_of_total_demand"] * c["max_multiplier"] for c in FLEX_CATEGORIES.values())
    / FLEX_TOTAL_FRACTION
)
FLEX_MIN_MULTIPLIER = (
    sum(c["fraction_of_total_demand"] * c["min_multiplier"] for c in FLEX_CATEGORIES.values())
    / FLEX_TOTAL_FRACTION
)
# Weighted-average cost
FLEX_VARIABLE_COST = (
    sum(c["fraction_of_total_demand"] * c["variable_cost"] for c in FLEX_CATEGORIES.values())
    / FLEX_TOTAL_FRACTION
)
```

This produces a single set of parameters that can be passed directly to `create_multi_country_model_from_clever()` via its existing kwargs:
```python
model = create_multi_country_model_from_clever(
    ...,
    flex_demand_fraction=FLEX_TOTAL_FRACTION,       # 0.23
    flex_conservation_hrs=FLEX_CONSERVATION_HRS,     # ~8h (weighted)
    flex_max_multiplier=FLEX_MAX_MULTIPLIER,         # ~1.21
    flex_min_multiplier=FLEX_MIN_MULTIPLIER,         # ~0.66
    flex_variable_cost=FLEX_VARIABLE_COST,           # ~13.5 EUR/MWh
    flex_ramp_up=np.nan,                             # keep unlimited for now
    flex_ramp_down=np.nan,
)
```

#### Alternative (advanced, requires validation): multiple FlexibleDemand via post-hoc addition

If multi-category flex is essential, add components **post-hoc** after model creation but before solving. Each category gets a unique name and its own demand slice:

```python
# WARNING: This approach is UNTESTED with POMMES.
# Validate that: (a) parameter tables don't collide, (b) energy balance is correct,
# (c) conservation constraints don't conflict.

with model.context():
    for cat_name, cat_params in FLEX_CATEGORIES.items():
        for country_code, area in model.areas.items():
            flex_profile = [d * cat_params["fraction_of_total_demand"] for d in demand_vals]
            area.add_component(FlexibleDemand(
                name=f"{cat_name}_flex_{country_code}",
                resource="electricity",
                demand=build_flex_df(flex_profile, year_op),
                conservation_hrs=cat_params["conservation_hours"],
                max_demand=build_flex_max_df(flex_profile, cat_params["max_multiplier"], year_op),
                min_demand=build_flex_min_df(flex_profile, cat_params["min_multiplier"], year_op),
                variable_cost=cat_params["variable_cost"],
                ramp_up=np.nan,
                ramp_down=np.nan,
            ))
```

**Before using this approach, run a small test** (e.g., single country, 168 hours) to verify POMMES handles it correctly. Check:
1. Does `model.to_pommes_model()` produce distinct parameter tables for each flex component?
2. Does the energy balance correctly account for all flex components?
3. Does the solver find a feasible solution?

#### Sensitivity runs on flexibility

The cleanest way to explore flexibility scenarios is to **run the notebook multiple times with different aggregated parameters**:

| Scenario | Flex fraction | Conservation (h) | Max mult | Min mult | Cost (EUR/MWh) |
|----------|--------------|-------------------|----------|----------|----------------|
| No flexibility | 0.00 | — | — | — | — |
| EV only (baseline) | 0.08 | 6 | 1.15 | 0.85 | 10.0 |
| EV + heat pumps | 0.18 | ~8 | ~1.24 | ~0.67 | ~10.6 |
| Full (EV + HP + industrial) | 0.23 | ~8 | ~1.21 | ~0.66 | ~13.5 |
| High flexibility | 0.30 | 12 | 1.30 | 0.50 | 8.0 |

---

## 5. POMMES Code Paths — Exact Reference for Implementation

This section documents the exact code flow so the notebook author knows where each parameter enters the pipeline and where interventions are safe.

### 5.1 Full solve pipeline (annotated)

```
NOTEBOOK
  │
  ├─ create_multi_country_model_from_clever(...)
  │   └─ _add_country_components()
  │       ├─ ConversionTechnology(...)      # NO ramp params set here
  │       ├─ StorageTechnology(...)
  │       ├─ Demand(...)
  │       ├─ FlexibleDemand(...)            # IF flex_demand_fraction > 0
  │       ├─ LoadShedding(...)
  │       └─ Spillage(...)
  │   → Returns: pommes_craft.EnergyModel
  │
  ├─ run_model_without_ramping(model, ...)
  │   │
  │   ├─ model.to_pommes_model()            # Serialise to POMMES format
  │   │   → Creates parameter_tables (dict of DataFrames)
  │   │
  │   ├─ build_input_parameters(config, parameter_tables)
  │   │   → Converts DataFrames to xarray Dataset `p`
  │   │
  │   ├─ check_inputs(p)                    # Validation
  │   │
  │   ├─ sanitize_absent_conversions(p)     # <<<< OVERWRITES ALL RAMP TO 1.0
  │   ├─ sanitize_storage_inputs(p)
  │   ├─ sanitize_transport_inputs(p)
  │   │
  │   │   ════════════════════════════════════════════════════
  │   │   INTERVENTION POINT FOR RAMPING:
  │   │   After sanitisation, before build_model(), modify:
  │   │     p["conversion_ramp_up"]
  │   │     p["conversion_ramp_down"]
  │   │   with per-technology values via .loc[{tech_dim: name}]
  │   │   ════════════════════════════════════════════════════
  │   │
  │   ├─ build_model(p)                     # Creates linopy Model
  │   │   → Reads conversion_ramp_up/down → builds ramping constraints
  │   │   → Reads flexible_demand_* → builds flex constraints
  │   │
  │   ├─ linopy_model.solve(solver_name, **solver_options)
  │   │
  │   └─ model.set_all_results(linopy_model)
  │       → Writes solution back to EnergyModel
  │
  └─ export_prices(), export_conversion_capacity(), etc.
```

### 5.2 Key dimension names to verify at runtime

Before setting per-technology ramp values, print these to discover the exact coordinate names:
```python
# After build_input_parameters(), print:
print("Ramp dims:", p["conversion_ramp_up"].dims)
print("Tech names:", list(p["conversion_ramp_up"].coords["conversion_tech"].values))
print("Area names:", list(p["conversion_ramp_up"].coords["area"].values))
```

Technology names in POMMES are typically lowercase with underscores and may include the area suffix (e.g., `"nuclear_FR"`, `"ch4_ccgt_DE"`). The notebook must discover these dynamically, not hardcode them.

### 5.3 FlexibleDemand data format (polars DataFrames)

```python
import polars as pl
import numpy as np

def build_flex_dataframes(demand_vals, fraction, max_mult, min_mult, year_op):
    """Build the 3 polars DataFrames expected by FlexibleDemand."""
    n = len(demand_vals)  # must be 8760
    flex_profile = [d * fraction for d in demand_vals]

    demand_df = pl.DataFrame({
        "hour": list(range(n)),
        "year_op": [year_op] * n,
        "demand": flex_profile,
    })
    max_df = pl.DataFrame({
        "hour": list(range(n)),
        "year_op": [year_op] * n,
        "max_demand": [d * max_mult for d in flex_profile],
    })
    min_df = pl.DataFrame({
        "hour": list(range(n)),
        "year_op": [year_op] * n,
        "min_demand": [d * min_mult for d in flex_profile],
    })
    return demand_df, max_df, min_df
```

---

## 6. Key Questions the Notebook Must Answer

1. **Is the system adequate?** How many hours of load shedding (LOLE) and how much energy not served (ENS) across the modelled countries?
2. **Where and when does inadequacy occur?** Which countries, which seasons, which hours of day?
3. **Does ramping matter?** How much does LOLE increase when realistic ramping constraints are applied?
4. **Does flexibility help?** How much does LOLE decrease with demand-side flexibility? Which flexibility categories contribute most?
5. **What is the interaction between ramping and flexibility?** Can demand flexibility compensate for ramping limitations?
6. **How much storage and interconnection is needed?** What are the marginal values of additional battery capacity and NTC expansion?
7. **What is the cost of adequacy?** What is the system cost difference between a system that is barely adequate vs. one with comfortable margins?
8. **How sensitive are results to key assumptions?** VRE capacity factors, demand level, flexible fraction, H2 cost, etc.

---

## 7. File Locations Reference

```
Working directories:
  notebooks/               → Jupyter notebooks (this is where the new notebook goes)
  PYCHARM_PROJECTS--clever/ → clever module source code
  tp_pommes_kraft/          → pommes and pommes_craft libraries
  eoles_inputs/             → cost data CSVs
  diagnostics/              → solver output (.nc files)
  model/                    → original POMMES model files
```

---

## 8. How to Use This Prompt

This document serves as the **complete specification** for building the new notebook. An LLM or developer should be able to:

1. Read this prompt in full
2. Understand the existing codebase without needing to explore it
3. Build the new notebook section by section, following the structure in 4.1
4. Correctly implement ramping (4.3) and multi-category flexibility (4.4)
5. Produce all required analyses (Section 5)
6. Avoid the structural pitfalls of the current notebook (Section 3)
