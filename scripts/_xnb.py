#!/usr/bin/env python
# coding: utf-8

# # Cross-scenario analysis — DemandForge × POMMES CLEVER 2050
# 
# Loads every solved scenario's diagnostics and computes comparative
# tables/figures. Designed to run on inari where data + deps live, but also
# works on a local Mac with the SMB mount.
# 
# Sections:
# 1. Config + imports
# 2. Load per-scenario diagnostics
# 3. Headline capacity per scenario
# 4. Comparative tables → tables/synthesis/*.csv
# 5. Comparative figures → figures/synthesis/*.{png,svg}
# 6. Nuclear investigation (H1/H2/H3)
# 7. Corridor expansion analysis
# 8. System cost summary
# 9. Sanity assertions
# 

# ## 1. Config + imports

# In[ ]:


from __future__ import annotations
from pathlib import Path
import sys, os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

# Detect environment — inari vs local Mac
_INARI = Path("/diskdata/cired/brigode/clever-work").exists()
WS = Path("/diskdata/cired/brigode/clever-work") if _INARI else Path("~/Desktop/clever-work").expanduser()
print(f"Workspace: {WS}  (inari={_INARI})")

def diag_path(scenario):
    if _INARI:
        return WS / "results" / "diagnostics" / scenario
    if scenario == "R0_v1":
        return WS / "R0_v1" / "diagnostics"
    return WS / f"results_{scenario}" / "diagnostics"

ALL_KNOWN = [
    "R0_v1", "policy_re", "policy_nuke", "R0_v1_nuke", "policy_re_noMin",
    "R0_v1_corr2x", "R0_v1_corr3x",
    "R0_v1_nuke_corr2x", "R0_v1_nuke_corr3x",
    "policy_re_corr2x", "policy_re_corr3x",
    "policy_nuke_corr2x", "policy_nuke_corr3x",
    "policy_re_noMin_corr2x", "policy_re_noMin_corr3x",
]
SCENARIOS = [s for s in ALL_KNOWN if (diag_path(s) / "solution_2050.nc").exists()]
print(f"Found solved scenarios ({len(SCENARIOS)}): {SCENARIOS}")

TABLES_OUT = WS / "tables" / "synthesis"
FIGS_OUT   = WS / "figures" / "synthesis"
TABLES_OUT.mkdir(parents=True, exist_ok=True)
FIGS_OUT.mkdir(parents=True, exist_ok=True)


# ## 2. Load per-scenario diagnostics
# 
# Direct xarray load of `solution_2050.nc` + `input_dataset_2050.nc`.
# POMMES vars used downstream:
# 
# - `operation_conversion_power_capacity[area, conversion_tech, year_op]` — installed capacity per area (MW)
# - `operation_conversion_net_generation[area, conversion_tech, hour, resource, year_op]` — hourly dispatch (MW)
# - `operation_storage_energy_capacity[area, storage_tech, year_op]` — storage energy (MWh)
# - `operation_storage_power_capacity[area, storage_tech, year_op]` — storage power (MW)
# - `operation_transport_power_capacity[link, transport_tech, year_op]` — built corridor capacity (MW)
# - `operation_load_shedding_power[area, hour, resource, year_op]` — shed (MW per h)
# - `annualised_totex[area, year_op]` — system cost component per area (€)
# - `conversion_power_capacity_investment_min/max` in input — for the H1/H2/H3 Nuclear test
# 

# In[ ]:


scenarios_data = {}
for scen in SCENARIOS:
    d = diag_path(scen)
    try:
        scenarios_data[scen] = {
            "sol": xr.open_dataset(d / "solution_2050.nc"),
            "inp": xr.open_dataset(d / "input_dataset_2050.nc"),
        }
        print(f"  loaded {scen}")
    except Exception as e:
        print(f"  FAIL {scen}: {type(e).__name__}: {e}")

print(f"\n→ {len(scenarios_data)} scenarios in memory")
if scenarios_data:
    sample = next(iter(scenarios_data.values()))
    print(f"  conv_tech in sample:    {list(map(str, sample['inp'].coords['conversion_tech'].values))}")
    print(f"  storage_tech in sample: {list(map(str, sample['sol'].coords['storage_tech'].values))}")
    print(f"  transport_tech:         {list(map(str, sample['inp'].coords['transport_tech'].values))}")


# ## 3. Headline capacity per scenario
# 
# Per-tech installed capacity (GW), summed over all 30 areas.

# In[ ]:


TECH_LABELS = {
    "Solar":                "solar_GW",
    "Wind_Onshore":         "wind_onshore_GW",
    "Wind_Offshore":        "wind_offshore_GW",
    "Nuclear":              "nuclear_GW",
    "Gas":                  "gas_GW",
    "Hydrogen_power_plant": "h2_pp_GW",
    "Waste":                "waste_GW",
    "electrolysis":         "electrolyser_GW",
    "Reservoir_Hydro_Plant":"reservoir_hydro_GW",
    "RoR_Hydro":            "ror_hydro_GW",
}

def headline(scen, d):
    out = {"scenario": scen}
    sol = d["sol"]
    cap = sol["operation_conversion_power_capacity"]  # MW, dims (area, conversion_tech, year_op)
    techs_present = list(map(str, cap.coords["conversion_tech"].values))
    for tech, label in TECH_LABELS.items():
        if tech not in techs_present:
            out[label] = 0.0
            continue
        sel = cap.sel(conversion_tech=tech)
        out[label] = float(sel.sum()) / 1000.0  # MW → GW

    # H₂ load shed (TWh)
    ls = sol["operation_load_shedding_power"]  # MW, (area, hour, resource, year_op)
    for r in ("hydrogen", "electricity"):
        if r in list(map(str, ls.coords["resource"].values)):
            out[f"shed_{r}_TWh"] = float(ls.sel(resource=r).sum()) / 1e6

    # Storage energy capacity (TWh)
    sec = sol["operation_storage_energy_capacity"]  # MWh, (area, storage_tech, year_op)
    for stech in ("h2_storage", "Battery_4h", "Battery_1h", "Pumped_Hydro"):
        if stech in list(map(str, sec.coords["storage_tech"].values)):
            out[f"storeE_{stech}_TWh"] = float(sec.sel(storage_tech=stech).sum()) / 1e6

    # System cost (€)
    if "annualised_totex" in sol.data_vars:
        out["totex_Geur"] = float(sol["annualised_totex"].sum()) / 1e9
    return out

rows = [headline(s, d) for s, d in scenarios_data.items()]
headline_df = pd.DataFrame(rows).set_index("scenario") if rows else pd.DataFrame()
if not headline_df.empty:
    # Δ vs R0_v1
    if "R0_v1" in headline_df.index:
        base = headline_df.loc["R0_v1"]
        for col in [c for c in headline_df.columns if c.endswith("_GW") or c == "totex_Geur"]:
            headline_df[f"Δ_{col}_vs_R0_v1"] = headline_df[col] - base[col]
    headline_df.to_csv(TABLES_OUT / "headline_by_scenario.csv")
    # show the non-Δ cols first
    base_cols = [c for c in headline_df.columns if not c.startswith("Δ_")]
    print(headline_df[base_cols].round(2).to_string())
else:
    print("No scenarios loaded.")


# ## 4. Comparative tables

# In[ ]:


# 4.1 Capacity per country × tech (GW)
rows = []
for scen, d in scenarios_data.items():
    cap = d["sol"]["operation_conversion_power_capacity"]
    techs_present = list(map(str, cap.coords["conversion_tech"].values))
    for tech in TECH_LABELS:
        if tech not in techs_present: continue
        sel = cap.sel(conversion_tech=tech)  # (area, year_op)
        for area in sel.coords["area"].values:
            v = float(sel.sel(area=area).sum()) / 1000.0
            if v > 0.001:
                rows.append({"scenario": scen, "country": str(area), "tech": tech, "GW": v})

cap_df = pd.DataFrame(rows)
if not cap_df.empty:
    cap_pivot = cap_df.pivot_table(index=["country", "tech"], columns="scenario", values="GW", fill_value=0)
    cap_pivot.to_csv(TABLES_OUT / "capacity_by_country_tech.csv")
    print(cap_pivot.head(25).to_string())
else:
    print("No capacity rows.")


# In[ ]:


# 4.2 Electrolyser redistribution per country
elec_rows = []
for scen, d in scenarios_data.items():
    cap = d["sol"]["operation_conversion_power_capacity"]
    techs_present = list(map(str, cap.coords["conversion_tech"].values))
    if "electrolysis" not in techs_present: continue
    sel = cap.sel(conversion_tech="electrolysis")
    for area in sel.coords["area"].values:
        elec_rows.append({"scenario": scen, "country": str(area),
                          "electrolyser_GW": float(sel.sel(area=area).sum()) / 1000.0})

if elec_rows:
    elec_df = pd.DataFrame(elec_rows).pivot(index="country", columns="scenario", values="electrolyser_GW").fillna(0)
    base_col = "R0_v1" if "R0_v1" in elec_df.columns else elec_df.columns[0]
    for col in elec_df.columns:
        if col != base_col:
            elec_df[f"Δ_{col}_vs_{base_col}"] = elec_df[col] - elec_df[base_col]
    elec_df.to_csv(TABLES_OUT / "electrolyser_redistribution.csv")
    print(elec_df.sort_values(by=base_col, ascending=False).head(15).round(2).to_string())


# ## 5. Comparative figures

# In[ ]:


if not headline_df.empty:
    vre_cols = [c for c in ("solar_GW", "wind_onshore_GW", "wind_offshore_GW") if c in headline_df.columns]
    disp_cols = [c for c in ("nuclear_GW", "gas_GW", "h2_pp_GW") if c in headline_df.columns]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    headline_df[vre_cols].plot.bar(stacked=True, ax=axes[0])
    axes[0].set_title("VRE capacity (GW)"); axes[0].grid(axis="y", alpha=0.3); axes[0].tick_params(axis="x", rotation=45)
    headline_df[disp_cols].plot.bar(stacked=True, ax=axes[1])
    axes[1].set_title("Dispatchable capacity (GW)"); axes[1].grid(axis="y", alpha=0.3); axes[1].tick_params(axis="x", rotation=45)
    if "electrolyser_GW" in headline_df.columns:
        headline_df["electrolyser_GW"].plot.bar(ax=axes[2], color="seagreen")
        axes[2].set_title("Electrolyser (GW)"); axes[2].grid(axis="y", alpha=0.3); axes[2].tick_params(axis="x", rotation=45)
    plt.tight_layout()
    out = FIGS_OUT / "headline_capacity_by_scenario"
    fig.savefig(f"{out}.png", dpi=120); fig.savefig(f"{out}.svg"); plt.close()
    print(f"→ wrote {out}.{{png,svg}}")
else:
    print("Skipped — no headline_df.")


# ## 6. Nuclear investigation (H1 / H2 / H3)
# 
# - **H1**: Nuclear is not in `conversion_tech` coord → flip didn't take effect (or scenario by design excludes it)
# - **H2**: Nuclear is in coord but LP didn't invest above its `investment_min` floor → uneconomic given prices/CF
# - **H3**: Nuclear is in coord and LP built capacity above the floor → competitive
# 

# In[ ]:


nuke_verdict = []
for scen, d in scenarios_data.items():
    inp = d["inp"]; sol = d["sol"]
    techs = list(map(str, inp.coords["conversion_tech"].values))
    if "Nuclear" not in techs:
        nuke_verdict.append({"scenario": scen, "in_techs": False,
                             "invest_min_GW": None, "invest_max_GW": None,
                             "deployed_GW": None,
                             "verdict": "H1 (Nuclear not in model)"})
        continue
    inv_min = float(inp["conversion_power_capacity_investment_min"].sel(conversion_tech="Nuclear").sum()) / 1000
    inv_max = float(inp["conversion_power_capacity_investment_max"].sel(conversion_tech="Nuclear").sum()) / 1000
    deployed = float(sol["operation_conversion_power_capacity"].sel(conversion_tech="Nuclear").sum()) / 1000
    if inv_max <= inv_min + 0.01:
        verdict = "H1 (no headroom — min ≈ max)"
    elif deployed <= inv_min + 0.01:
        verdict = "H2 (uneconomic — at floor)"
    else:
        verdict = f"H3 (deployed {deployed:.1f} GW)"
    nuke_verdict.append({"scenario": scen, "in_techs": True,
                         "invest_min_GW": inv_min, "invest_max_GW": inv_max,
                         "deployed_GW": deployed, "verdict": verdict})

nuke_df = pd.DataFrame(nuke_verdict).set_index("scenario")
nuke_df.to_csv(TABLES_OUT / "nuclear_verdict_by_scenario.csv")
print(nuke_df.to_string())
print()
# Per-country breakdown where Nuclear deployed
for scen, d in scenarios_data.items():
    if "Nuclear" not in list(map(str, d["inp"].coords["conversion_tech"].values)): continue
    sel = d["sol"]["operation_conversion_power_capacity"].sel(conversion_tech="Nuclear")
    by_c = {str(a): float(sel.sel(area=a).sum())/1000 for a in sel.coords["area"].values}
    nz = {k: v for k, v in by_c.items() if v > 0.01}
    if nz:
        print(f"  {scen}: " + ", ".join(f"{k}={v:.1f}GW" for k,v in sorted(nz.items(), key=lambda x: -x[1])))


# ## 7. Corridor expansion analysis
# 
# POMMES models transport as `transport_tech ∈ {electric_line, h2_pipeline}` with
# per-link invest_max. Corridor multipliers inflate invest_max on selected links.
# We compare built `operation_transport_power_capacity` per link, focusing on the
# four corridor pairs targeted by the policy: DK↔DE, NO↔DK, NL↔DE, FR↔ES.
# 

# In[ ]:


# Show built electric_line capacity on the corridor links across scenarios
CORRIDOR_LINKS = ["link_DK_DE", "link_NO_DK", "link_NL_DE", "link_FR_ES"]
corr_rows = []
for scen, d in scenarios_data.items():
    tpc = d["sol"]["operation_transport_power_capacity"]  # MW, (link, transport_tech, year_op)
    links_present = list(map(str, tpc.coords["link"].values))
    techs_present = list(map(str, tpc.coords["transport_tech"].values))
    if "electric_line" not in techs_present: continue
    sel = tpc.sel(transport_tech="electric_line")
    for link in CORRIDOR_LINKS:
        if link in links_present:
            mw = float(sel.sel(link=link).sum())
            corr_rows.append({"scenario": scen, "link": link, "built_GW": mw / 1000.0})

if corr_rows:
    corr_df = pd.DataFrame(corr_rows).pivot(index="link", columns="scenario", values="built_GW").fillna(0)
    base_col = next((c for c in ("R0_v1", "policy_re", "policy_nuke") if c in corr_df.columns), corr_df.columns[0])
    for col in corr_df.columns:
        if col != base_col and "_corr" in col:
            corr_df[f"Δ_{col}_vs_{base_col}"] = corr_df[col] - corr_df[base_col]
    corr_df.to_csv(TABLES_OUT / "corridor_built_capacity.csv")
    print(corr_df.round(2).to_string())
else:
    print("No corridor data (transport_tech mismatch).")


# ## 8. System cost summary
# 
# `annualised_totex` is the LP objective contribution per area + year_op (€).
# Summing across all 30 areas gives total system cost per scenario.
# 

# In[ ]:


cost_rows = []
for scen, d in scenarios_data.items():
    sol = d["sol"]
    row = {"scenario": scen}
    for var in ("annualised_totex",
                "operation_load_shedding_costs", "operation_spillage_costs",
                "operation_conversion_costs", "operation_storage_costs",
                "operation_transport_costs",
                "planning_conversion_costs", "planning_storage_costs",
                "planning_transport_costs"):
        if var in sol.data_vars:
            row[f"{var}_Geur"] = float(sol[var].sum()) / 1e9
    cost_rows.append(row)
cost_df = pd.DataFrame(cost_rows).set_index("scenario")
cost_df.to_csv(TABLES_OUT / "cost_breakdown.csv")
print(cost_df.round(2).to_string())


# ## 9. Sanity assertions

# In[ ]:


def check(cond, msg):
    print(("  OK:  " if cond else "  FAIL: ") + msg)

if not headline_df.empty:
    for scen in headline_df.index:
        if "electrolyser_GW" in headline_df.columns:
            e = headline_df.loc[scen, "electrolyser_GW"]
            check(e > 0, f"{scen}: electrolyser_GW = {e:.1f} > 0")
        if "shed_hydrogen_TWh" in headline_df.columns:
            shed = headline_df.loc[scen, "shed_hydrogen_TWh"]
            check(shed < 0.01, f"{scen}: H₂ shed = {shed:.4f} TWh < 0.01")

    if "policy_re" in headline_df.index and "R0_v1" in headline_df.index:
        check(headline_df.loc["policy_re", "electrolyser_GW"] >= headline_df.loc["R0_v1", "electrolyser_GW"],
              "policy_re electrolyser ≥ R0_v1")

print()
print("=== DONE ===")
print(f"  CSVs in {TABLES_OUT}")
print(f"  Figures in {FIGS_OUT}")
print(f"  Loaded scenarios: {SCENARIOS}")

