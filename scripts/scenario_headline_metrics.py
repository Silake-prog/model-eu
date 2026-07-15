"""scripts/scenario_headline_metrics.py — extract per-scenario headline metrics.

Reads each scenario's diagnostic netCDFs (solution + input_dataset) and
prints/exports a compact summary: objective, EU30 electrolyser GW, EU30 VRE
GW, H₂ load-shedding total, Nuclear deployment per country (where
applicable), top sovereignty-binding countries.

Usage:
  python scripts/scenario_headline_metrics.py <results_root> <scenario1> [scenario2 ...]
  python scripts/scenario_headline_metrics.py ~/Desktop/clever-work policy_re policy_nuke R0_v1_nuke

Writes:
  results/<scenario>/headline.json
  tables/synthesis/headline_by_scenario.csv  (one row per scenario)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import xarray as xr


def _open_solution(diag_dir: Path) -> xr.Dataset:
    return xr.open_dataset(diag_dir / "solution_2050.nc")


def _open_input(diag_dir: Path) -> xr.Dataset:
    return xr.open_dataset(diag_dir / "input_dataset_2050.nc")


def _scenario_layout(workspace: Path, scenario: str) -> Path:
    """Map a scenario name to its local diagnostics dir.

    Convention from launch_recipes.md:
      - R0_v1 baseline diagnostics live at $WS/R0_v1/diagnostics/
      - All other scenarios live at $WS/results_<scenario>/diagnostics/
    """
    if scenario == "R0_v1":
        return workspace / "R0_v1" / "diagnostics"
    return workspace / f"results_{scenario}" / "diagnostics"


def extract(workspace: Path, scenario: str) -> dict:
    diag = _scenario_layout(workspace, scenario)
    if not (diag / "solution_2050.nc").exists():
        raise FileNotFoundError(f"missing solution_2050.nc for {scenario}: {diag}")

    sol = _open_solution(diag)
    inp = _open_input(diag)

    metrics: dict = {"scenario": scenario, "diag_dir": str(diag)}

    # ── Objective ───────────────────────────────────────────────
    if "objective" in sol.attrs:
        metrics["objective_eur"] = float(sol.attrs["objective"])
    elif "objective_value" in sol.attrs:
        metrics["objective_eur"] = float(sol.attrs["objective_value"])
    else:
        # Sum of cost variables as a fallback proxy
        cost_vars = [v for v in sol.data_vars if "cost" in v.lower() and "_cost" not in v.lower()]
        metrics["objective_eur"] = float(sum(float(sol[v].sum()) for v in cost_vars))
        metrics["objective_note"] = f"summed from cost vars: {cost_vars[:5]}"

    # ── Conversion capacities ───────────────────────────────────
    # POMMES typically exposes `power_capacity` (built + existing) and
    # `power_capacity_investment` (newly invested). The total installed
    # capacity = max of capacity arrays per (area, tech, year_op).
    cap_var = None
    for cand in ("power_capacity", "operation_conversion_power_capacity", "conversion_power_capacity"):
        if cand in sol.data_vars:
            cap_var = cand
            break

    if cap_var is None:
        metrics["error_capacity"] = f"no recognised capacity var in {list(sol.data_vars)[:10]}"
    else:
        cap = sol[cap_var]
        # Mean over year_op/year_inv to get a single representative value
        reduce_dims = [d for d in cap.dims if d not in ("area", "conversion_tech")]
        cap_2d = cap.sum(dim=reduce_dims) if reduce_dims else cap

        techs_of_interest = {
            "Electrolysers": ["Electrolysers", "electrolysis"],
            "Solar": ["Solar"],
            "Wind_Onshore": ["Wind_Onshore"],
            "Wind_Offshore": ["Wind_Offshore"],
            "Nuclear": ["Nuclear"],
            "Hydrogen_power_plant": ["Hydrogen_power_plant", "hydrogen_power_plant"],
            "Gas": ["Gas"],
        }
        per_tech = {}
        for label, candidates in techs_of_interest.items():
            for c in candidates:
                if c in cap_2d.coords["conversion_tech"].values:
                    sel = cap_2d.sel(conversion_tech=c)
                    eu_gw = float(sel.sum()) / 1000.0
                    by_country = {
                        str(a): float(sel.sel(area=a)) / 1000.0
                        for a in sel.coords["area"].values
                    }
                    per_tech[label] = {
                        "EU30_GW": eu_gw,
                        "by_country_GW": by_country,
                    }
                    break
        metrics["installed_capacity"] = per_tech

    # ── Storage capacity ────────────────────────────────────────
    if "storage_energy_capacity" in sol.data_vars and "storage_tech" in sol.coords:
        sec = sol["storage_energy_capacity"]
        reduce_dims = [d for d in sec.dims if d not in ("area", "storage_tech")]
        sec2d = sec.sum(dim=reduce_dims) if reduce_dims else sec
        per_stor = {}
        for s in sec2d.coords["storage_tech"].values:
            if s in ("h2_storage", "Battery_4h", "Battery_1h", "Pumped_Hydro", "Reservoir_Hydro"):
                sel = sec2d.sel(storage_tech=s)
                per_stor[str(s)] = {
                    "EU30_TWh": float(sel.sum()) / 1e6,
                }
        metrics["storage_energy"] = per_stor

    if "storage_power_capacity" in sol.data_vars and "storage_tech" in sol.coords:
        spc = sol["storage_power_capacity"]
        reduce_dims = [d for d in spc.dims if d not in ("area", "storage_tech")]
        spc2d = spc.sum(dim=reduce_dims) if reduce_dims else spc
        per_stor_pow = {}
        for s in spc2d.coords["storage_tech"].values:
            if s in ("h2_storage", "Battery_4h", "Battery_1h", "Pumped_Hydro", "Reservoir_Hydro"):
                per_stor_pow[str(s)] = {"EU30_GW": float(spc2d.sel(storage_tech=s).sum()) / 1000.0}
        metrics["storage_power"] = per_stor_pow

    # ── Load shedding ───────────────────────────────────────────
    for cand in ("load_shedding", "operation_load_shedding"):
        if cand in sol.data_vars:
            ls = sol[cand]
            if "resource" in ls.dims:
                per_res = {}
                for r in ls.coords["resource"].values:
                    total_mwh = float(ls.sel(resource=r).sum())
                    per_res[str(r)] = total_mwh
                metrics["load_shedding_MWh_by_resource"] = per_res
            else:
                metrics["load_shedding_MWh_total"] = float(ls.sum())
            break

    sol.close()
    inp.close()
    return metrics


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: scenario_headline_metrics.py <workspace_root> <scenario1> [scenario2 ...]")
        return 2
    workspace = Path(argv[0]).expanduser().resolve()
    scenarios = argv[1:]

    out_root = workspace / "tables" / "synthesis"
    out_root.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []

    for sc in scenarios:
        print(f"\n=== {sc} ===")
        try:
            m = extract(workspace, sc)
        except FileNotFoundError as e:
            print(f"  SKIP: {e}")
            continue
        # Print a few headline numbers
        print(f"  objective: {m.get('objective_eur', float('nan')) / 1e9:.2f} G€/yr")
        if "installed_capacity" in m:
            for tech, d in m["installed_capacity"].items():
                print(f"  {tech:22s}: {d['EU30_GW']:8.1f} GW")
        if "storage_energy" in m:
            for s, d in m["storage_energy"].items():
                print(f"  storage {s:14s}: {d['EU30_TWh']:8.2f} TWh")
        if "load_shedding_MWh_by_resource" in m:
            for r, mwh in m["load_shedding_MWh_by_resource"].items():
                print(f"  load shed {r:18s}: {mwh / 1e6:8.4f} TWh/yr")

        # Write the rich JSON per-scenario
        per_scen_dir = (workspace / f"results_{sc}" / "diagnostics"
                        if sc != "R0_v1" else workspace / "R0_v1" / "diagnostics")
        (per_scen_dir / "headline.json").write_text(json.dumps(m, indent=2, default=str))

        # Build a flat row for the CSV
        row: dict = {
            "scenario": sc,
            "objective_GEUR_yr": m.get("objective_eur", float("nan")) / 1e9,
        }
        for tech, d in m.get("installed_capacity", {}).items():
            row[f"cap_{tech}_GW"] = d["EU30_GW"]
        for s, d in m.get("storage_energy", {}).items():
            row[f"storeE_{s}_TWh"] = d["EU30_TWh"]
        for r, mwh in m.get("load_shedding_MWh_by_resource", {}).items():
            row[f"shed_{r}_TWh"] = mwh / 1e6
        all_rows.append(row)

    if all_rows:
        df = pd.DataFrame(all_rows).set_index("scenario")
        csv_path = out_root / "headline_by_scenario.csv"
        df.to_csv(csv_path)
        print(f"\n→ wrote {csv_path}")
        print(df.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
