"""scripts/nuclear_quick_check.py — minimal H1/H2/H3 verdict.

Loads only the smallish input_dataset_2050.nc + a focused slice of
solution_2050.nc (Nuclear conv_tech only) — skips the 300 MB dual_2050.nc
that was making nuclear_investigation.py slow.

Verdict logic:
  H1 — flip silently failed:  invest_max[Nuclear] == invest_min[Nuclear]
  H2 — flip took, uneconomic: invest_max[Nuclear] > invest_min[Nuclear] AND
                              power_capacity[Nuclear, year=2050] ≤ floor
  H3 — Nuclear deployed:       power_capacity > floor
"""
from __future__ import annotations
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import xarray as xr

WS = Path("~/Desktop/clever-work").expanduser()
SCENARIOS = {
    "R0_v1":       WS / "R0_v1"               / "diagnostics",
    "policy_re":   WS / "results_policy_re"   / "diagnostics",
    "R0_v1_nuke":  WS / "results_R0_v1_nuke"  / "diagnostics",
    "policy_nuke": WS / "results_policy_nuke" / "diagnostics",
}

print(f"{'scenario':14s}  {'in_coord':>9s}  {'invest_min_GW':>14s}  {'invest_max_GW':>14s}  "
      f"{'deployed_GW':>13s}  {'invest_cost_M€/MW':>18s}  {'verdict':>9s}")
print("-" * 110)

rows = []
for scen, diag in SCENARIOS.items():
    inp_path = diag / "input_dataset_2050.nc"
    sol_path = diag / "solution_2050.nc"
    if not inp_path.exists():
        print(f"{scen:14s}  SKIP (no input_dataset)")
        continue
    inp = xr.open_dataset(inp_path)
    if "Nuclear" not in inp.coords["conversion_tech"].values:
        rows.append({"scenario": scen, "in_coord": False, "verdict": "no Nuclear in model"})
        print(f"{scen:14s}  {'False':>9s}  {'—':>14s}  {'—':>14s}  {'—':>13s}  {'—':>18s}  {'N/A':>9s}")
        inp.close()
        continue

    # Sum invest_min / invest_max over (area, year_inv) for Nuclear
    inv_min = inp["conversion_power_capacity_investment_min"].sel(conversion_tech="Nuclear")
    inv_max = inp["conversion_power_capacity_investment_max"].sel(conversion_tech="Nuclear")
    invest_cost = float(inp["conversion_invest_cost"].sel(conversion_tech="Nuclear").mean())

    min_eu_gw = float(inv_min.sum()) / 1000.0
    max_eu_gw = float(inv_max.sum()) / 1000.0

    # Deployed: from solution
    deployed_gw = float("nan")
    if sol_path.exists():
        sol = xr.open_dataset(sol_path)
        if "Nuclear" in sol.coords.get("conversion_tech", []) and "power_capacity" in sol.data_vars:
            cap = sol["power_capacity"].sel(conversion_tech="Nuclear")
            # Pick the latest year_op (or sum if single year)
            cap2d = cap.sum(dim=[d for d in cap.dims if d not in ("area",)])
            deployed_gw = float(cap2d.sum()) / 1000.0
        sol.close()

    # Verdict
    if max_eu_gw <= min_eu_gw + 0.01:
        verdict = "H1 (flip failed)"
    elif deployed_gw <= min_eu_gw + 0.01:
        verdict = "H2 (uneconomic)"
    else:
        verdict = "H3 (deployed)"

    print(f"{scen:14s}  {'True':>9s}  {min_eu_gw:>14.2f}  {max_eu_gw:>14.2f}  "
          f"{deployed_gw:>13.2f}  {invest_cost/1e6:>18.2f}  {verdict:>9s}")
    rows.append({
        "scenario": scen,
        "in_coord": True,
        "invest_min_GW": min_eu_gw,
        "invest_max_GW": max_eu_gw,
        "deployed_GW": deployed_gw,
        "invest_cost_M€/MW": invest_cost / 1e6,
        "verdict": verdict,
    })
    inp.close()

print()
df = pd.DataFrame(rows).set_index("scenario")
out_path = WS / "tables" / "synthesis" / "nuclear_quick_check.csv"
out_path.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(out_path)
print(f"→ wrote {out_path}")

# Per-country Nuclear breakdown for the nuke variants
print()
print("Per-country Nuclear (GW) — R0_v1_nuke, policy_nuke (existing CLEVER + invested):")
for scen in ("R0_v1_nuke", "policy_nuke"):
    diag = SCENARIOS[scen]
    if not (diag / "solution_2050.nc").exists():
        continue
    sol = xr.open_dataset(diag / "solution_2050.nc")
    if "Nuclear" not in sol.coords.get("conversion_tech", []):
        sol.close()
        continue
    cap = sol["power_capacity"].sel(conversion_tech="Nuclear")
    cap2d = cap.sum(dim=[d for d in cap.dims if d not in ("area",)])
    by_country = pd.Series({str(a): float(cap2d.sel(area=a))/1000 for a in cap2d.coords["area"].values})
    nz = by_country[by_country > 0.01].sort_values(ascending=False)
    print(f"\n  === {scen} (EU30 total {by_country.sum():.1f} GW) ===")
    if len(nz):
        for area, gw in nz.items():
            print(f"    {area}: {gw:6.2f} GW")
    else:
        print(f"    (no country has Nuclear > 0.01 GW)")
    sol.close()
