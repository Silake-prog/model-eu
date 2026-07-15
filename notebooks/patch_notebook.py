#!/usr/bin/env python3
"""
patch_notebook.py  —  Patch adequacy_2050.ipynb to add:
  1. Demand flexibility (EV load shifting) parametrisation
  2. CCGT H2 capacity expansion review
  3. Updated analysis cells using exact .nc variable names

Run from the clever-work directory:
    python patch_notebook.py

Or specify path:
    python patch_notebook.py /path/to/adequacy_2050.ipynb
"""

import json
import sys
import copy
from pathlib import Path
from datetime import datetime

# ─── Locate notebook ──────────────────────────────────────────────────
DEFAULT_PATH = Path.home() / "Desktop" / "clever-work" / "notebooks" / "adequacy_2050.ipynb"

if len(sys.argv) > 1:
    nb_path = Path(sys.argv[1])
else:
    nb_path = DEFAULT_PATH

if not nb_path.exists():
    print(f"ERROR: Notebook not found at {nb_path}")
    print("Usage: python patch_notebook.py [path/to/adequacy_2050.ipynb]")
    sys.exit(1)

with open(nb_path, "r", encoding="utf-8") as f:
    nb = json.load(f)

cells = nb["cells"]
print(f"Loaded notebook: {nb_path}")
print(f"  Current cells: {len(cells)}")

# ─── Helper ───────────────────────────────────────────────────────────
def make_cell(cell_type, source, cell_id=None):
    """Create a notebook cell dict."""
    c = {
        "cell_type": cell_type,
        "metadata": {},
        "source": source if isinstance(source, str) else source,
        "id": cell_id or f"patch_{datetime.now().strftime('%H%M%S')}_{id(source) % 10000:04d}",
    }
    if cell_type == "code":
        c["outputs"] = []
        c["execution_count"] = None
    return c


def find_cell_by_id(cells, cell_id):
    for i, c in enumerate(cells):
        if c.get("id") == cell_id:
            return i
    return None


def find_cell_containing(cells, text, start=0):
    for i in range(start, len(cells)):
        src = cells[i].get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        if text in src:
            return i
    return None


# ═══════════════════════════════════════════════════════════════════════
# PATCH 1: Insert flexibility parametrisation after model creation
# ═══════════════════════════════════════════════════════════════════════

# Find cell 19 (model creation) by its id or by content
model_cell = find_cell_by_id(cells, "33ddae6516b4ae7a")
if model_cell is None:
    model_cell = find_cell_containing(cells, "create_multi_country_model_from_clever")
if model_cell is None:
    print("WARNING: Could not find model creation cell. Skipping flexibility patch.")
    flex_insert_idx = None
else:
    flex_insert_idx = model_cell + 1
    print(f"  Model creation cell found at index {model_cell}")

if flex_insert_idx is not None:
    # Check if we already patched (avoid double-patching)
    already_patched = find_cell_containing(cells, "FLEX_DEMAND_FRACTION")
    if already_patched:
        print("  Flexibility patch already applied. Skipping.")
    else:
        flex_md = make_cell("markdown", """\
### 7.0.2 Parametrise demand flexibility (EV load shifting)

pommes_craft supports demand-side flexibility via 7 scalar parameters in the
input dataset. By default they are all zero/NaN (flexibility inactive).

Here we activate EV smart-charging flexibility. The parameters below are
**configurable** — change the values and re-run the notebook to explore
different flexibility scenarios.

| Parameter | Meaning | Value chosen |
|---|---|---|
| `flexibility_demand` | Fraction of total elec. demand that is flexible | 0.08 (≈ EV share) |
| `flexibility_conservation_hrs` | Hours within which shifted energy must be conserved | 6 |
| `flexibility_max_demand` | Max hourly demand multiplier of nominal | 1.15 |
| `flexibility_min_demand` | Min hourly demand multiplier of nominal | 0.85 |
| `flexibility_ramp_up` / `ramp_down` | Max demand ramp (MW/h), NaN = unlimited | NaN |
| `flexibility_variable_cost` | Activation cost of flex (EUR/MWh) | 10.0 |""",
            cell_id="patch_flex_md")

        flex_code = make_cell("code", """\
import numpy as np

# ══════════════════════════════════════════════════════════════════════
# FLEXIBILITY PARAMETERS — change these to explore scenarios
# ══════════════════════════════════════════════════════════════════════
FLEX_DEMAND_FRACTION    = 0.08     # fraction of total elec demand that is flexible (EV ≈ 8%)
FLEX_CONSERVATION_HRS   = 6        # shifted energy must be returned within this window
FLEX_MAX_DEMAND         = 1.15     # max hourly demand = 115% of nominal (flex-up cap)
FLEX_MIN_DEMAND         = 0.85     # min hourly demand = 85%  of nominal (flex-down cap)
FLEX_RAMP_UP            = np.nan   # MW/h ramp-up limit (NaN = unlimited)
FLEX_RAMP_DOWN          = np.nan   # MW/h ramp-down limit (NaN = unlimited)
FLEX_VARIABLE_COST      = 10.0     # EUR/MWh activation cost of flexibility

# ── Inject into model dataset ────────────────────────────────────────
ds = model.dataset
ds["flexibility_demand"]           = FLEX_DEMAND_FRACTION
ds["flexibility_conservation_hrs"] = FLEX_CONSERVATION_HRS
ds["flexibility_max_demand"]       = FLEX_MAX_DEMAND
ds["flexibility_min_demand"]       = FLEX_MIN_DEMAND
ds["flexibility_ramp_up"]          = FLEX_RAMP_UP
ds["flexibility_ramp_down"]        = FLEX_RAMP_DOWN
ds["flexibility_variable_cost"]    = FLEX_VARIABLE_COST

# Also set the master flexibility switch (1 = enabled)
ds["flexibility"] = 1

print("✓ Demand flexibility ACTIVATED")
print(f"  Flexible fraction : {FLEX_DEMAND_FRACTION:.0%} of total demand")
print(f"  Conservation window: {FLEX_CONSERVATION_HRS} hours")
print(f"  Demand range       : [{FLEX_MIN_DEMAND:.0%}, {FLEX_MAX_DEMAND:.0%}] of nominal")
print(f"  Variable cost      : {FLEX_VARIABLE_COST} EUR/MWh")

# Quick sanity: how much energy is flexible?
for area in COUNTRIES_MODELLED:
    if area in demand_dict:
        total_twh = demand_dict[area]["demand"].sum() / 1e6
        flex_twh = total_twh * FLEX_DEMAND_FRACTION
        print(f"  {area}: {flex_twh:.1f} TWh flexible out of {total_twh:.1f} TWh total")
""",
            cell_id="patch_flex_code")

        cells.insert(flex_insert_idx, flex_md)
        cells.insert(flex_insert_idx + 1, flex_code)
        print(f"  ✓ Inserted flexibility cells at index {flex_insert_idx}-{flex_insert_idx+1}")


# ═══════════════════════════════════════════════════════════════════════
# PATCH 2: Insert CCGT H2 capacity expansion review after model build
# ═══════════════════════════════════════════════════════════════════════

# Find model build verification cell or insert after flexibility
verify_cell = find_cell_containing(cells, "Model build verification")
if verify_cell is None:
    verify_cell = find_cell_containing(cells, "FLEX_DEMAND_FRACTION")
    if verify_cell is not None:
        verify_cell += 1

if verify_cell is not None:
    already_h2 = find_cell_containing(cells, "CCGT_H2_INV_MAX")
    if already_h2:
        print("  H2 expansion patch already applied. Skipping.")
    else:
        h2_md = make_cell("markdown", """\
### 7.0.3 Review & adjust CCGT H2 capacity expansion bounds

The optimizer can invest in new CCGT H2 (hydrogen-fired gas turbines) up to
`conversion_inv_max`. In the baseline run, inv_max = 55 GW system-wide, and
the optimizer chose ~45 GW — the cap is not binding but 1.79 TWh of load
shedding persists.

This suggests the bottleneck is temporal (H2 fuel availability, ramp
constraints, or simultaneous stress across countries) rather than purely a
capacity shortfall. Below we review and optionally raise the cap.""",
            cell_id="patch_h2_md")

        h2_code = make_cell("code", """\
# ══════════════════════════════════════════════════════════════════════
# CCGT H2 CAPACITY EXPANSION — review and adjust
# ══════════════════════════════════════════════════════════════════════
CCGT_H2_INV_MAX = None  # Set to a value (e.g. 80_000) to override, or None to keep current

ds = model.dataset

# ── Current bounds ───────────────────────────────────────────────────
inv_max_var = None
for v in ds.data_vars:
    if "inv_max" in v.lower() and "conversion" in v.lower():
        inv_max_var = v
        break
if inv_max_var is None:
    inv_max_var = "conversion_inv_max"  # fallback name

if inv_max_var in ds:
    inv_max_da = ds[inv_max_var]
    print(f"Variable: {inv_max_var}")
    print(f"  Dims: {inv_max_da.dims}, Shape: {inv_max_da.shape}")

    # Find H2 technologies
    for dim in inv_max_da.dims:
        coords = [str(c) for c in inv_max_da.coords[dim].values]
        h2_techs = [c for c in coords if "h2" in c.lower() or "hydrogen" in c.lower() or "ccgt" in c.lower()]
        if h2_techs:
            print(f"\\n  H2-related technologies in dim '{dim}':")
            for t in h2_techs:
                vals = inv_max_da.sel({dim: t})
                print(f"    {t}: inv_max = {vals.values} MW")

                # Override if requested
                if CCGT_H2_INV_MAX is not None:
                    old_val = float(vals.values.flatten()[0]) if vals.values.size > 0 else "N/A"
                    inv_max_da.loc[{dim: t}] = CCGT_H2_INV_MAX
                    print(f"      → OVERRIDDEN to {CCGT_H2_INV_MAX:,.0f} MW (was {old_val:,.0f})")

    # Also show gas technologies
    for dim in inv_max_da.dims:
        coords = [str(c) for c in inv_max_da.coords[dim].values]
        gas_techs = [c for c in coords if "gas" in c.lower() or "ocgt" in c.lower()]
        if gas_techs:
            print(f"\\n  Gas technologies in dim '{dim}':")
            for t in gas_techs:
                vals = inv_max_da.sel({dim: t})
                print(f"    {t}: inv_max = {vals.values} MW")
else:
    print(f"WARNING: {inv_max_var} not found in dataset")
    print(f"  Available vars with 'inv': {[v for v in ds.data_vars if 'inv' in v.lower()]}")

if CCGT_H2_INV_MAX is not None:
    print(f"\\n✓ CCGT H2 investment cap set to {CCGT_H2_INV_MAX:,.0f} MW")
else:
    print("\\nℹ CCGT_H2_INV_MAX = None → keeping current bounds")
""",
            cell_id="patch_h2_code")

        insert_idx = verify_cell + 1
        cells.insert(insert_idx, h2_md)
        cells.insert(insert_idx + 1, h2_code)
        print(f"  ✓ Inserted H2 expansion cells at index {insert_idx}-{insert_idx+1}")


# ═══════════════════════════════════════════════════════════════════════
# PATCH 3: Replace/update the adequacy analysis cell (cell 27)
#           to use exact .nc variable names
# ═══════════════════════════════════════════════════════════════════════

adequacy_cell = find_cell_by_id(cells, "103b0b8f72b62234")
if adequacy_cell is None:
    adequacy_cell = find_cell_containing(cells, "adequacy_results")
if adequacy_cell is not None:
    cells[adequacy_cell] = make_cell("code", """\
import xarray as xr
from pathlib import Path
from clever.constants import DEFAULT_LOAD_SHEDDING_COST

SHEDDING_THRESHOLD = DEFAULT_LOAD_SHEDDING_COST * 0.9   # 27,000 EUR/MWh
DIAG_DIR = Path("results/diagnostics")

# ── Load solution & dual datasets ─────────────────────────────────
sol_path = DIAG_DIR / f"solution_{MODEL_YEAR}.nc"
dual_path = DIAG_DIR / f"dual_{MODEL_YEAR}.nc"
inp_path = DIAG_DIR / f"input_dataset_{MODEL_YEAR}.nc"

sol_ds  = xr.open_dataset(sol_path)  if sol_path.exists()  else None
dual_ds = xr.open_dataset(dual_path) if dual_path.exists() else None
inp_ds  = xr.open_dataset(inp_path)  if inp_path.exists()  else None

# ── Extract load-shedding from solution.nc ────────────────────────
# Exact variable name: operation_load_shedding_power
# Dims: (area, hour, resource, year_op)
LS_VAR = "operation_load_shedding_power"

adequacy_results = {}
for area in COUNTRIES_MODELLED:
    result = {"area": area}

    # --- ENS from solution.nc (preferred) ---
    if sol_ds is not None and LS_VAR in sol_ds:
        ls_da = sol_ds[LS_VAR]
        try:
            ls_area = ls_da.sel(area=area)
            # Select electricity resource if dimension exists
            if "resource" in ls_area.dims:
                for r in ls_area.coords["resource"].values:
                    if "electr" in str(r).lower():
                        ls_area = ls_area.sel(resource=r)
                        break
            if "year_op" in ls_area.dims:
                ls_area = ls_area.sel(year_op=MODEL_YEAR)
            vals = ls_area.values.flatten()
            ens_mwh = float(vals[vals > 0].sum())
            lole_hrs = int((vals > 0).sum())
            peak_ls = float(vals.max())
            result["ens_gwh"] = ens_mwh / 1000
            result["lole_hours"] = lole_hrs
            result["peak_shedding_mw"] = peak_ls
            result["ens_source"] = "solution.nc"
        except Exception as e:
            result["ens_gwh"] = None
            result["error"] = str(e)
    else:
        result["ens_gwh"] = None
        result["ens_source"] = "not available"

    # --- Adequacy dual from dual.nc ---
    DUAL_VAR = "operation_adequacy_constraint"
    if dual_ds is not None and DUAL_VAR in dual_ds:
        try:
            dual_da = dual_ds[DUAL_VAR]
            dual_area = dual_da.sel(area=area)
            if "resource" in dual_area.dims:
                for r in dual_area.coords["resource"].values:
                    if "electr" in str(r).lower():
                        dual_area = dual_area.sel(resource=r)
                        break
            if "year_op" in dual_area.dims:
                dual_area = dual_area.sel(year_op=MODEL_YEAR)
            dvals = dual_area.values.flatten()
            result["dual_mean"] = float(dvals.mean())
            result["dual_max"]  = float(dvals.max())
            result["dual_gt1k"] = int((dvals > 1000).sum())
            result["dual_at_voll"] = int((dvals >= DEFAULT_LOAD_SHEDDING_COST * 0.99).sum())
        except Exception:
            pass

    # --- Price-based cross-check ---
    area_prices = prices_df[prices_df["area"] == area].copy()
    if not area_prices.empty:
        shedding_hours = area_prices["price"] >= SHEDDING_THRESHOLD
        result["price_lole_hours"] = int(shedding_hours.sum())
        result["max_price"] = float(area_prices["price"].max())
        result["mean_price"] = float(area_prices["price"].mean())

    adequacy_results[area] = result

# ── Display summary ───────────────────────────────────────────────
print("=" * 90)
print("ADEQUACY RESULTS  —  CLEVER Sufficiency Scenario 2050")
print(f"  VoLL = {DEFAULT_LOAD_SHEDDING_COST:,.0f} EUR/MWh")
print("=" * 90)
print(f"{'Country':>8}  {'ENS (GWh)':>10}  {'LOLE (h)':>8}  {'Peak LS':>10}  "
      f"{'Dual max':>10}  {'Dual≥VoLL':>10}  {'Source':>12}")
print("-" * 90)

total_ens = 0
total_lole = 0
for area in COUNTRIES_MODELLED:
    r = adequacy_results[area]
    ens = r.get("ens_gwh")
    lole = r.get("lole_hours", "—")
    peak = r.get("peak_shedding_mw", "—")
    dmax = r.get("dual_max", "—")
    dvoll = r.get("dual_at_voll", "—")
    src = r.get("ens_source", "—")

    ens_str = f"{ens:10.1f}" if ens is not None else "       N/A"
    lole_str = f"{lole:>8}" if isinstance(lole, int) else f"{lole:>8}"
    peak_str = f"{peak:10,.0f}" if isinstance(peak, (int, float)) else f"{peak:>10}"
    dmax_str = f"{dmax:10,.0f}" if isinstance(dmax, (int, float)) else f"{dmax:>10}"
    dvoll_str = f"{dvoll:>10}" if isinstance(dvoll, int) else f"{dvoll:>10}"

    print(f"{area:>8}  {ens_str}  {lole_str}  {peak_str}  {dmax_str}  {dvoll_str}  {src:>12}")

    if ens is not None:
        total_ens += ens
    if isinstance(lole, int):
        total_lole += lole

print("-" * 90)
print(f"{'TOTAL':>8}  {total_ens:10.1f}  {total_lole:>8}")
print()
print("Key: ENS = Energy Not Served | LOLE = Loss of Load Expectation (hours)")
print(f"     Dual≥VoLL = hours where adequacy shadow price hits {DEFAULT_LOAD_SHEDDING_COST:,.0f} EUR/MWh cap")
""", cell_id="103b0b8f72b62234")
    print(f"  ✓ Updated adequacy analysis cell at index {adequacy_cell}")


# ═══════════════════════════════════════════════════════════════════════
# PATCH 4: Add new analysis sections addressing supervisor remarks
#           Insert before the final cell
# ═══════════════════════════════════════════════════════════════════════

# Find insertion point — after the last existing cell or before a "Final" section
last_code_idx = len(cells) - 1

new_analysis_cells = [
    # ─── Section: Spillage / Curtailment per year ───────────────
    make_cell("markdown", """\
## 12. VRE Spillage / Curtailment Analysis

**Supervisor question**: *"Spillage pour chaque année / Est-ce que mon curtailment est excessif ?"*

We extract `operation_spillage_power` from `solution.nc` (dims: area, hour, resource, year_op).
A system with zero spillage and high load shedding is capacity-short, not over-supplied.""",
        cell_id="patch_spillage_md"),

    make_cell("code", """\
import xarray as xr
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

SPILL_VAR = "operation_spillage_power"

if sol_ds is not None and SPILL_VAR in sol_ds:
    spill_da = sol_ds[SPILL_VAR]
    print(f"Spillage variable: {SPILL_VAR}")
    print(f"  Dims: {spill_da.dims}, Shape: {spill_da.shape}")

    # Per-country annual spillage
    print("\\n── Annual VRE Spillage by Country ──")
    spill_by_country = {}
    for area in COUNTRIES_MODELLED:
        try:
            sp = spill_da.sel(area=area)
            if "resource" in sp.dims:
                for r in sp.coords["resource"].values:
                    if "electr" in str(r).lower():
                        sp = sp.sel(resource=r); break
            if "year_op" in sp.dims:
                sp = sp.sel(year_op=MODEL_YEAR)
            vals = sp.values.flatten()
            total_gwh = vals.sum() / 1000
            peak_mw = vals.max()
            hours_spill = (vals > 0).sum()
            spill_by_country[area] = total_gwh
            print(f"  {area}: {total_gwh:8.1f} GWh  |  {hours_spill:5d} hours  |  peak {peak_mw:,.0f} MW")
        except Exception as e:
            print(f"  {area}: error — {e}")
            spill_by_country[area] = 0

    total_spill = sum(spill_by_country.values())
    print(f"  TOTAL: {total_spill:,.1f} GWh")

    # Compare with total demand
    total_demand_twh = sum(demand_dict[a]["demand"].sum() / 1e6 for a in COUNTRIES_MODELLED if a in demand_dict)
    curtailment_pct = total_spill / (total_demand_twh * 1000) * 100
    print(f"\\n  Curtailment ratio: {curtailment_pct:.2f}% of total demand ({total_demand_twh:.1f} TWh)")
    if curtailment_pct < 1:
        print("  → Very low curtailment. System is capacity-short, NOT over-supplied with VRE.")
    elif curtailment_pct < 5:
        print("  → Moderate curtailment. Normal for high-VRE systems.")
    else:
        print("  → High curtailment. Consider additional storage or interconnection capacity.")

    # ── Time-series of hourly spillage (system-wide) ──────────────
    fig, axes = plt.subplots(2, 1, figsize=(16, 8), gridspec_kw={"height_ratios": [3, 1]})

    # Stacked area by country
    for area in COUNTRIES_MODELLED:
        try:
            sp = spill_da.sel(area=area)
            if "resource" in sp.dims:
                for r in sp.coords["resource"].values:
                    if "electr" in str(r).lower():
                        sp = sp.sel(resource=r); break
            if "year_op" in sp.dims:
                sp = sp.sel(year_op=MODEL_YEAR)
            vals = sp.values.flatten()
            if vals.sum() > 0:
                axes[0].plot(range(len(vals)), vals, label=area, alpha=0.7, linewidth=0.5)
        except:
            pass

    axes[0].set_ylabel("Spillage (MW)")
    axes[0].set_title("Hourly VRE Spillage by Country")
    axes[0].legend(ncol=3, fontsize=8)
    axes[0].yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x/1000:.0f}k" if x >= 1000 else f"{x:.0f}"))

    # Bar chart of annual spillage
    countries = list(spill_by_country.keys())
    values = [spill_by_country[c] for c in countries]
    colors = plt.cm.Set2(range(len(countries)))
    axes[1].bar(countries, values, color=colors)
    axes[1].set_ylabel("Annual spillage (GWh)")
    axes[1].set_title("Total Annual VRE Spillage by Country")

    plt.tight_layout()
    plt.savefig(DIAG_DIR / "spillage_analysis.png", dpi=150, bbox_inches="tight")
    plt.show()
else:
    print(f"WARNING: {SPILL_VAR} not found in solution.nc")
    if sol_ds is not None:
        spill_candidates = [v for v in sol_ds.data_vars if "spill" in v.lower()]
        print(f"  Candidates: {spill_candidates}")
""",
        cell_id="patch_spillage_code"),

    # ─── Section: LOLE detailed analysis ──────────────────────────
    make_cell("markdown", """\
## 13. LOLE & Load Shedding Detailed Analysis

**Supervisor questions**:
- *"LOLE / Afficher la loss of load → 30k EUR"*
- *"Combien d'heures de loss of load ?"*

We visualise when and where load shedding occurs, and show the shadow price
at the VoLL cap (30,000 EUR/MWh).""",
        cell_id="patch_lole_md"),

    make_cell("code", """\
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

LS_VAR = "operation_load_shedding_power"
DUAL_VAR = "operation_adequacy_constraint"

if sol_ds is not None and LS_VAR in sol_ds:
    fig, axes = plt.subplots(3, 1, figsize=(16, 14), gridspec_kw={"height_ratios": [2, 2, 1]})

    # ── Panel 1: Load-shedding heatmap (hour-of-day vs day-of-year) ──
    # Aggregate across all countries
    ls_da = sol_ds[LS_VAR]
    all_ls = np.zeros(8760)
    for area in COUNTRIES_MODELLED:
        try:
            ls_area = ls_da.sel(area=area)
            if "resource" in ls_area.dims:
                for r in ls_area.coords["resource"].values:
                    if "electr" in str(r).lower():
                        ls_area = ls_area.sel(resource=r); break
            if "year_op" in ls_area.dims:
                ls_area = ls_area.sel(year_op=MODEL_YEAR)
            vals = ls_area.values.flatten()[:8760]
            all_ls[:len(vals)] += vals
        except:
            pass

    # Reshape to (365, 24)
    ls_matrix = all_ls[:8760].reshape(365, 24) if len(all_ls) >= 8760 else all_ls.reshape(-1, 24)
    im = axes[0].imshow(ls_matrix.T, aspect="auto", cmap="Reds", origin="lower",
                         extent=[1, 365, 0, 24])
    axes[0].set_xlabel("Day of year")
    axes[0].set_ylabel("Hour of day")
    axes[0].set_title("System-wide load shedding (MW) — hour × day heatmap")
    plt.colorbar(im, ax=axes[0], label="Load shedding (MW)", shrink=0.8)

    # ── Panel 2: Per-country LOLE timeline ──
    for area in COUNTRIES_MODELLED:
        try:
            ls_area = ls_da.sel(area=area)
            if "resource" in ls_area.dims:
                for r in ls_area.coords["resource"].values:
                    if "electr" in str(r).lower():
                        ls_area = ls_area.sel(resource=r); break
            if "year_op" in ls_area.dims:
                ls_area = ls_area.sel(year_op=MODEL_YEAR)
            vals = ls_area.values.flatten()[:8760]
            if vals.max() > 0:
                axes[1].plot(range(len(vals)), vals, label=area, alpha=0.8, linewidth=0.5)
        except:
            pass

    axes[1].set_ylabel("Load shedding (MW)")
    axes[1].set_xlabel("Hour of year")
    axes[1].set_title("Per-country load shedding timeline")
    axes[1].legend(ncol=3, fontsize=8)

    # ── Panel 3: LOLE hours bar chart ──
    lole_data = {}
    for area in COUNTRIES_MODELLED:
        r = adequacy_results.get(area, {})
        lole_data[area] = r.get("lole_hours", 0)

    countries = list(lole_data.keys())
    hours = [lole_data[c] for c in countries]
    colors = ["#d32f2f" if h > 100 else "#ff9800" if h > 10 else "#4caf50" for h in hours]
    axes[2].bar(countries, hours, color=colors)
    axes[2].set_ylabel("LOLE (hours)")
    axes[2].set_title("Loss of Load Expectation by Country")
    for i, (c, h) in enumerate(zip(countries, hours)):
        if h > 0:
            axes[2].text(i, h + 5, str(h), ha="center", fontsize=9, fontweight="bold")

    # Horizontal line at typical adequacy standard (3h)
    axes[2].axhline(y=3, color="green", linestyle="--", linewidth=1, label="3h standard")
    axes[2].legend()

    plt.tight_layout()
    plt.savefig(DIAG_DIR / "lole_analysis.png", dpi=150, bbox_inches="tight")
    plt.show()

# ── Dual price at VoLL ──
if dual_ds is not None and DUAL_VAR in dual_ds:
    print("\\n── Adequacy shadow price at VoLL (30,000 EUR/MWh) ──")
    dual_da = dual_ds[DUAL_VAR]
    for area in COUNTRIES_MODELLED:
        try:
            d = dual_da.sel(area=area)
            if "resource" in d.dims:
                for r in d.coords["resource"].values:
                    if "electr" in str(r).lower():
                        d = d.sel(resource=r); break
            if "year_op" in d.dims:
                d = d.sel(year_op=MODEL_YEAR)
            dvals = d.values.flatten()
            at_voll = (dvals >= 30000 * 0.99).sum()
            gt_1k = (dvals > 1000).sum()
            mean_d = dvals.mean()
            print(f"  {area}: {at_voll:4d}h at VoLL cap  |  {gt_1k:4d}h > 1000 EUR  |  mean dual = {mean_d:,.0f} EUR/MWh")
        except Exception as e:
            print(f"  {area}: error — {e}")
""",
        cell_id="patch_lole_code"),

    # ─── Section: Sector coupling (electricity-hydrogen) ──────────
    make_cell("markdown", """\
## 14. Sector Coupling: Electricity ↔ Hydrogen

**Supervisor question**: *"Couplage"*

Analyse the bidirectional electricity-hydrogen coupling: electrolysis
(electricity → H2) and CCGT H2 (H2 → electricity). Key questions:
- How much H2 is produced / consumed?
- What is the utilisation rate of H2 plants?
- Is H2 storage sufficient to bridge stress periods?""",
        cell_id="patch_coupling_md"),

    make_cell("code", """\
import matplotlib.pyplot as plt

CONV_VAR = "operation_conversion_power"

if sol_ds is not None and CONV_VAR in sol_ds:
    conv_da = sol_ds[CONV_VAR]
    print(f"Conversion power variable: {CONV_VAR}")
    print(f"  Dims: {conv_da.dims}")

    # Find H2-related technologies
    tech_dim = None
    for dim in conv_da.dims:
        coords = [str(c) for c in conv_da.coords[dim].values]
        h2_techs = [c for c in coords if "h2" in c.lower() or "electrol" in c.lower() or "hydrogen" in c.lower()]
        if h2_techs:
            tech_dim = dim
            print(f"\\n  H2 technologies in '{dim}': {h2_techs}")
            break

    if tech_dim:
        all_techs = [str(c) for c in conv_da.coords[tech_dim].values]
        h2_techs = [t for t in all_techs if "h2" in t.lower() or "electrol" in t.lower() or "hydrogen" in t.lower()]

        fig, axes = plt.subplots(len(h2_techs), 1, figsize=(16, 4 * len(h2_techs)), squeeze=False)

        for idx, tech in enumerate(h2_techs):
            ax = axes[idx, 0]
            total_by_country = {}
            for area in COUNTRIES_MODELLED:
                try:
                    p = conv_da.sel({tech_dim: tech, "area": area})
                    if "resource" in p.dims:
                        for r in p.coords["resource"].values:
                            if "electr" in str(r).lower():
                                p = p.sel(resource=r); break
                    if "year_op" in p.dims:
                        p = p.sel(year_op=MODEL_YEAR)
                    vals = p.values.flatten()[:8760]
                    total_twh = abs(vals).sum() / 1e6
                    total_by_country[area] = total_twh
                    if abs(vals).max() > 0:
                        ax.plot(range(len(vals)), vals, label=f"{area} ({total_twh:.1f} TWh)", alpha=0.7, linewidth=0.5)
                except:
                    pass

            ax.set_title(f"{tech} — hourly output by country")
            ax.set_ylabel("Power (MW)")
            ax.set_xlabel("Hour")
            ax.legend(ncol=3, fontsize=7)
            total = sum(total_by_country.values())
            ax.text(0.02, 0.95, f"System total: {total:.1f} TWh", transform=ax.transAxes,
                    fontsize=10, verticalalignment="top", bbox=dict(boxstyle="round", facecolor="wheat"))

        plt.tight_layout()
        plt.savefig(DIAG_DIR / "sector_coupling_h2.png", dpi=150, bbox_inches="tight")
        plt.show()

    # ── H2 storage ──
    STOR_VAR = "operation_storage_level"
    if STOR_VAR in sol_ds:
        stor_da = sol_ds[STOR_VAR]
        print("\\n── H2 Storage ──")
        for dim in stor_da.dims:
            coords = [str(c) for c in stor_da.coords[dim].values]
            h2_stor = [c for c in coords if "h2" in c.lower() or "hydrogen" in c.lower()]
            if h2_stor:
                print(f"  H2 storage techs in '{dim}': {h2_stor}")
                for st in h2_stor:
                    for area in COUNTRIES_MODELLED:
                        try:
                            soc = stor_da.sel({dim: st, "area": area})
                            if "year_op" in soc.dims:
                                soc = soc.sel(year_op=MODEL_YEAR)
                            vals = soc.values.flatten()
                            if vals.max() > 0:
                                print(f"    {area}/{st}: max SoC = {vals.max():,.0f} MWh, "
                                      f"min SoC = {vals.min():,.0f} MWh, "
                                      f"mean = {vals.mean():,.0f} MWh")
                        except:
                            pass
else:
    print(f"WARNING: {CONV_VAR} not found in solution.nc")
""",
        cell_id="patch_coupling_code"),

    # ─── Section: EV demand flexibility results ──────────────────
    make_cell("markdown", """\
## 15. Demand Flexibility Results (EV Load Shifting)

**Supervisor questions**:
- *"Demande flexible des véhicules électriques"*
- *"Flexibilité de la demande (Loss of load évitée)"*
- *"Horizon de temps de la flexibilité, activer un prix"*
- *"Est-ce qu'il y a de la loss of load même avec de la flexibilité ?"*

If flexibility was activated, we examine the `operation_flexibility_power`
variable to see how much load was shifted and whether it reduced LOLE.""",
        cell_id="patch_evflex_md"),

    make_cell("code", """\
import matplotlib.pyplot as plt
import numpy as np

# ── Check if flexibility was activated ────────────────────────────
flex_active = False
if inp_ds is not None:
    fd = inp_ds.get("flexibility_demand")
    if fd is not None and float(fd.values) > 0:
        flex_active = True
        print(f"✓ Flexibility is ACTIVE (demand fraction = {float(fd.values):.2%})")
    else:
        print("✗ Flexibility is INACTIVE (flexibility_demand = 0)")
        print("  → Re-run the model with flexibility parameters set (see Section 7.0.2)")

# Reload solution.nc to pick up new results
sol_path_check = DIAG_DIR / f"solution_{MODEL_YEAR}.nc"
if sol_path_check.exists():
    sol_ds_fresh = xr.open_dataset(sol_path_check)
else:
    sol_ds_fresh = sol_ds

# ── Look for flexibility-related variables ────────────────────────
FLEX_CANDIDATES = ["operation_flexibility_power", "flexibility_power",
                   "operation_flex_up", "operation_flex_down"]

flex_var = None
if sol_ds_fresh is not None:
    for v in sol_ds_fresh.data_vars:
        if "flex" in v.lower():
            print(f"  Found flexibility variable: {v} — dims={sol_ds_fresh[v].dims}, shape={sol_ds_fresh[v].shape}")
            if flex_var is None:
                flex_var = v

if flex_var and flex_active:
    flex_da = sol_ds_fresh[flex_var]
    print(f"\\n── Flexibility dispatch: {flex_var} ──")

    fig, axes = plt.subplots(2, 1, figsize=(16, 10))

    flex_by_country = {}
    for area in COUNTRIES_MODELLED:
        try:
            f_area = flex_da.sel(area=area)
            if "resource" in f_area.dims:
                for r in f_area.coords["resource"].values:
                    if "electr" in str(r).lower():
                        f_area = f_area.sel(resource=r); break
            if "year_op" in f_area.dims:
                f_area = f_area.sel(year_op=MODEL_YEAR)
            vals = f_area.values.flatten()[:8760]
            shifted_gwh = abs(vals).sum() / 1000
            flex_by_country[area] = shifted_gwh
            if abs(vals).max() > 0:
                axes[0].plot(range(len(vals)), vals, label=f"{area} ({shifted_gwh:.1f} GWh)",
                           alpha=0.7, linewidth=0.5)
        except:
            pass

    axes[0].set_title(f"Hourly flexibility dispatch ({flex_var})")
    axes[0].set_ylabel("Power (MW)")
    axes[0].set_xlabel("Hour")
    axes[0].legend(ncol=3, fontsize=8)
    axes[0].axhline(y=0, color="gray", linewidth=0.5)

    # Bar chart
    countries = list(flex_by_country.keys())
    values = [flex_by_country[c] for c in countries]
    axes[1].bar(countries, values, color="steelblue")
    axes[1].set_ylabel("Total shifted energy (GWh)")
    axes[1].set_title("Annual flexibility activation by country")

    plt.tight_layout()
    plt.savefig(DIAG_DIR / "ev_flexibility.png", dpi=150, bbox_inches="tight")
    plt.show()

    # ── Compare LOLE with/without flexibility ──
    print("\\n── LOLE comparison (requires baseline without flex) ──")
    print("  Current LOLE with flexibility:")
    for area in COUNTRIES_MODELLED:
        r = adequacy_results.get(area, {})
        lole = r.get("lole_hours", "N/A")
        ens = r.get("ens_gwh", "N/A")
        print(f"    {area}: LOLE = {lole}h, ENS = {ens} GWh")
    print("\\n  To measure 'loss of load évitée', compare with the baseline run")
    print("  (run with FLEX_DEMAND_FRACTION = 0 and save results separately)")

elif not flex_active:
    print("\\n  ⚠ Flexibility parameters are all zero. The model has no demand flexibility.")
    print("  Set FLEX_DEMAND_FRACTION > 0 in Section 7.0.2 and re-solve to see flexibility results.")
else:
    print(f"\\n  No flexibility variables found in solution.nc.")
    if sol_ds_fresh is not None:
        print(f"  Available vars: {sorted([v for v in sol_ds_fresh.data_vars if 'flex' in v.lower() or 'demand' in v.lower()])}")
""",
        cell_id="patch_evflex_code"),

    # ─── Section: Peak power management & storage ─────────────────
    make_cell("markdown", """\
## 16. Peak Power Management & Storage Adequacy

**Supervisor questions**:
- *"Gestion sur la puissance max"*
- *"Le stockage"*
- *"Capacité maximale"*
- *"Besoin de flexibilité plus long terme"*

Peak demand management: what happens during the top-50 peak hours?
Storage adequacy: are batteries / hydro / H2 storage correctly sized?""",
        cell_id="patch_peak_md"),

    make_cell("code", """\
import matplotlib.pyplot as plt
import numpy as np

LS_VAR = "operation_load_shedding_power"
CONV_VAR = "operation_conversion_power"

if sol_ds is not None and LS_VAR in sol_ds:
    ls_da = sol_ds[LS_VAR]

    # ── Top-50 stress hours analysis ─────────────────────────────
    print("── Top-50 system stress hours (highest load shedding) ──")

    # Aggregate load shedding across countries
    sys_ls = np.zeros(8760)
    country_ls = {}
    for area in COUNTRIES_MODELLED:
        try:
            ls_area = ls_da.sel(area=area)
            if "resource" in ls_area.dims:
                for r in ls_area.coords["resource"].values:
                    if "electr" in str(r).lower():
                        ls_area = ls_area.sel(resource=r); break
            if "year_op" in ls_area.dims:
                ls_area = ls_area.sel(year_op=MODEL_YEAR)
            vals = ls_area.values.flatten()[:8760]
            country_ls[area] = vals
            sys_ls[:len(vals)] += vals
        except:
            pass

    top50_idx = np.argsort(sys_ls)[-50:][::-1]
    print(f"  Peak system load shedding: {sys_ls.max():,.0f} MW at hour {sys_ls.argmax()}")
    print(f"  Top-50 stress hours span: hours {top50_idx.min()}-{top50_idx.max()}")
    print(f"  Top-50 by month:")

    # Month distribution of top-50 hours
    months = (top50_idx // 730).clip(0, 11) + 1  # approximate
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    for m in range(1, 13):
        count = (months == m).sum()
        if count > 0:
            print(f"    {month_names[m-1]}: {count} hours")

    # ── Dispatch during top-50 hours ──
    if CONV_VAR in sol_ds:
        conv_da = sol_ds[CONV_VAR]
        tech_dim = None
        for dim in conv_da.dims:
            if "tech" in dim.lower() or "conversion" in dim.lower():
                tech_dim = dim; break

        if tech_dim:
            all_techs = [str(c) for c in conv_da.coords[tech_dim].values]
            print(f"\\n  Dispatch during top-50 stress hours (system-wide):")
            tech_during_stress = {}
            for tech in all_techs:
                total = 0
                for area in COUNTRIES_MODELLED:
                    try:
                        p = conv_da.sel({tech_dim: tech, "area": area})
                        if "resource" in p.dims:
                            for r in p.coords["resource"].values:
                                if "electr" in str(r).lower():
                                    p = p.sel(resource=r); break
                        if "year_op" in p.dims:
                            p = p.sel(year_op=MODEL_YEAR)
                        vals = p.values.flatten()[:8760]
                        total += vals[top50_idx].mean()
                    except:
                        pass
                if abs(total) > 1:
                    tech_during_stress[tech] = total
                    print(f"    {tech:30s}: avg {total:10,.0f} MW")

    # ── Storage state during stress ──
    STOR_VAR = "operation_storage_level"
    if STOR_VAR in sol_ds:
        stor_da = sol_ds[STOR_VAR]
        print(f"\\n── Storage state during top-50 stress hours ──")
        for dim in stor_da.dims:
            if "tech" in dim.lower() or "storage" in dim.lower():
                for tech in [str(c) for c in stor_da.coords[dim].values]:
                    for area in COUNTRIES_MODELLED:
                        try:
                            soc = stor_da.sel({dim: tech, "area": area})
                            if "year_op" in soc.dims:
                                soc = soc.sel(year_op=MODEL_YEAR)
                            vals = soc.values.flatten()[:8760]
                            if vals.max() > 0:
                                stress_soc = vals[top50_idx]
                                capacity = vals.max()
                                fill_pct = stress_soc.mean() / capacity * 100 if capacity > 0 else 0
                                print(f"    {area}/{tech}: avg fill during stress = {fill_pct:.1f}% "
                                      f"(mean SoC = {stress_soc.mean():,.0f} / {capacity:,.0f} MWh)")
                        except:
                            pass
                break

    # ── Figure: Load shedding duration curve ──
    fig, axes = plt.subplots(1, 2, figsize=(16, 5))

    # System LS duration curve
    sorted_ls = np.sort(sys_ls)[::-1]
    axes[0].plot(range(len(sorted_ls)), sorted_ls / 1000, color="red", linewidth=1.5)
    axes[0].fill_between(range(len(sorted_ls)), sorted_ls / 1000, alpha=0.2, color="red")
    axes[0].set_xlabel("Hour (sorted by severity)")
    axes[0].set_ylabel("System load shedding (GW)")
    axes[0].set_title("Load Shedding Duration Curve (system)")
    axes[0].set_xlim(0, max(1, (sorted_ls > 0).sum() * 1.1))

    # Per-country LS sorted
    for area in COUNTRIES_MODELLED:
        if area in country_ls:
            vals = np.sort(country_ls[area])[::-1]
            if vals.max() > 0:
                n_pos = (vals > 0).sum()
                axes[1].plot(range(n_pos), vals[:n_pos] / 1000, label=area, linewidth=1)

    axes[1].set_xlabel("Hour (sorted)")
    axes[1].set_ylabel("Load shedding (GW)")
    axes[1].set_title("Load Shedding Duration Curve (per country)")
    axes[1].legend(fontsize=8)

    plt.tight_layout()
    plt.savefig(DIAG_DIR / "peak_management.png", dpi=150, bbox_inches="tight")
    plt.show()

    # ── Long-term flexibility needs ──
    print("\\n── Long-term flexibility needs ──")
    print("  Consecutive stress hour streaks (system LS > 0):")
    is_stress = sys_ls > 0
    streaks = []
    current = 0
    for h in range(8760):
        if is_stress[h]:
            current += 1
        else:
            if current > 0:
                streaks.append((h - current, current))
            current = 0
    if current > 0:
        streaks.append((8760 - current, current))
    streaks.sort(key=lambda x: -x[1])
    for start, length in streaks[:10]:
        end = start + length
        day_start = start // 24
        day_end = end // 24
        print(f"    Hours {start}-{end} (day {day_start}-{day_end}): {length} consecutive hours")
    if streaks:
        print(f"  Longest stress event: {streaks[0][1]} hours ({streaks[0][1]/24:.1f} days)")
        print(f"  This defines the 'Dunkelflaute' duration the system must withstand.")
""",
        cell_id="patch_peak_code"),

    # ─── Section: Final adequacy dashboard ─────────────────────────
    make_cell("markdown", """\
## 17. Final Adequacy Dashboard

Summary table and key conclusions for the CLEVER Sufficiency Scenario 2050.""",
        cell_id="patch_dashboard_md"),

    make_cell("code", """\
import pandas as pd

# ── Build summary DataFrame ──────────────────────────────────────
rows = []
for area in COUNTRIES_MODELLED:
    r = adequacy_results.get(area, {})
    row = {
        "Country": area,
        "ENS (GWh)": r.get("ens_gwh", 0),
        "LOLE (h)": r.get("lole_hours", 0),
        "Peak LS (MW)": r.get("peak_shedding_mw", 0),
        "Max dual (EUR/MWh)": r.get("dual_max", 0),
        "Hours at VoLL": r.get("dual_at_voll", 0),
        "Mean price (EUR/MWh)": r.get("mean_price", 0),
    }
    rows.append(row)

summary_df = pd.DataFrame(rows)

# Add totals row
totals = summary_df.select_dtypes(include="number").sum()
totals["Country"] = "TOTAL"
totals["Mean price (EUR/MWh)"] = summary_df["Mean price (EUR/MWh)"].mean()
summary_df = pd.concat([summary_df, pd.DataFrame([totals])], ignore_index=True)

print("=" * 100)
print("FINAL ADEQUACY DASHBOARD  —  CLEVER Sufficiency Scenario 2050")
print("=" * 100)
print(summary_df.to_string(index=False, float_format=lambda x: f"{x:,.1f}"))

# ── Flexibility status ──
print("\\n── Flexibility Status ──")
if inp_ds is not None:
    fd = inp_ds.get("flexibility_demand")
    if fd is not None:
        fd_val = float(fd.values)
        if fd_val > 0:
            print(f"  ✓ ACTIVE: {fd_val:.0%} of demand flexible, "
                  f"conservation window = {int(inp_ds['flexibility_conservation_hrs'].values)}h, "
                  f"cost = {float(inp_ds['flexibility_variable_cost'].values)} EUR/MWh")
        else:
            print("  ✗ INACTIVE: Set FLEX_DEMAND_FRACTION > 0 in Section 7.0.2")

# ── Key conclusions ──
total_ens = sum(r.get("ens_gwh", 0) or 0 for r in adequacy_results.values())
total_lole = sum(r.get("lole_hours", 0) or 0 for r in adequacy_results.values())
worst_country = max(adequacy_results.items(), key=lambda x: x[1].get("ens_gwh", 0) or 0)

print(f"\\n── Key Findings ──")
print(f"  Total system ENS: {total_ens:,.1f} GWh ({total_ens/1000:.2f} TWh)")
print(f"  Total LOLE hours: {total_lole:,d}")
print(f"  Worst country: {worst_country[0]} ({worst_country[1].get('ens_gwh', 0):,.1f} GWh ENS, "
      f"{worst_country[1].get('lole_hours', 0)} LOLE hours)")

if total_ens > 0:
    print("\\n  ⚠ ADEQUACY CONCERN: Significant load shedding persists.")
    print("    Possible mitigations:")
    print("    1. Activate/increase demand flexibility (EV smart charging)")
    print("    2. Raise CCGT H2 investment cap or add new dispatchable capacity")
    print("    3. Increase interconnection capacity (import from surplus countries)")
    print("    4. Add more battery/H2 storage for Dunkelflaute periods")
else:
    print("\\n  ✓ System is adequate: no load shedding detected.")

# ── Save summary to CSV ──
summary_df.to_csv(DIAG_DIR / "adequacy_summary.csv", index=False)
print(f"\\nSummary saved to {DIAG_DIR / 'adequacy_summary.csv'}")
""",
        cell_id="patch_dashboard_code"),
]

# Insert the new analysis cells before the last cell
# Find the best insertion point
cap_mix_cell = find_cell_containing(cells, "Optimal capacity mix")
if cap_mix_cell is not None:
    insert_point = cap_mix_cell + 2  # After the capacity mix code cell
else:
    insert_point = len(cells)

# Check if patches already exist
already_patched_analysis = find_cell_containing(cells, "patch_spillage_md") or \
                           find_cell_containing(cells, "VRE Spillage / Curtailment Analysis")
if already_patched_analysis:
    print("  Analysis patches already applied. Skipping.")
else:
    for i, cell in enumerate(new_analysis_cells):
        cells.insert(insert_point + i, cell)
    print(f"  ✓ Inserted {len(new_analysis_cells)} analysis cells at index {insert_point}+")


# ═══════════════════════════════════════════════════════════════════════
# Save patched notebook
# ═══════════════════════════════════════════════════════════════════════

# Create backup
backup_path = nb_path.with_suffix(".ipynb.bak")
with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(json.loads(open(nb_path).read()), f, indent=1, ensure_ascii=False)
print(f"\n  Backup saved to {backup_path}")

# Save patched version
with open(nb_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)

print(f"\n✓ Patched notebook saved: {nb_path}")
print(f"  Total cells: {len(cells)}")
print("\nNew sections added:")
print("  7.0.2  Demand flexibility parametrisation (EV load shifting)")
print("  7.0.3  CCGT H2 capacity expansion review")
print("  12.    VRE spillage / curtailment analysis")
print("  13.    LOLE & load shedding detailed analysis")
print("  14.    Sector coupling (electricity-hydrogen)")
print("  15.    Demand flexibility results")
print("  16.    Peak power management & storage adequacy")
print("  17.    Final adequacy dashboard")
print("\nNext steps:")
print("  1. Open the notebook in Jupyter")
print("  2. Run cells 0-19 (model building) — unchanged")
print("  3. Run the new flexibility + H2 cells (7.0.2, 7.0.3)")
print("  4. Run the solve cell (7.1)")
print("  5. Run all analysis cells")
