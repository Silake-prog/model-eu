#!/usr/bin/env python3
"""
Standalone analysis script for solution_2050.nc and dual_2050.nc.
Run in the user's conda environment that has xarray/netCDF4.
Writes results to ../results/analysis_report.txt
"""
import xarray as xr
import numpy as np
import pandas as pd
import json
from pathlib import Path
from collections import defaultdict

RESULTS = Path(__file__).parent / ".." / "results" / "diagnostics"
EXPORT  = Path(__file__).parent / ".." / "results" / "export"
OUT     = Path(__file__).parent / ".." / "results" / "analysis_report.json"

sol = xr.open_dataset(RESULTS / "solution_2050.nc")
dual = xr.open_dataset(RESULTS / "dual_2050.nc")
inp = xr.open_dataset(RESULTS / "input_dataset_2050.nc")

report = {}

# ── 1. Variable inventory ──
report["solution_vars"] = {}
for v in sorted(sol.data_vars):
    da = sol[v]
    vals = da.values.flatten()
    vals_clean = vals[~np.isnan(vals)]
    report["solution_vars"][v] = {
        "dims": list(da.dims),
        "shape": list(da.shape),
        "min": float(vals_clean.min()) if len(vals_clean) else None,
        "max": float(vals_clean.max()) if len(vals_clean) else None,
        "mean": float(vals_clean.mean()) if len(vals_clean) else None,
        "nonzero": int(np.sum(np.abs(vals_clean) > 0.01)) if len(vals_clean) else 0,
    }

report["dual_vars"] = {}
for v in sorted(dual.data_vars):
    da = dual[v]
    vals = da.values.flatten()
    vals_clean = vals[~np.isnan(vals)]
    report["dual_vars"][v] = {
        "dims": list(da.dims),
        "shape": list(da.shape),
        "min": float(vals_clean.min()) if len(vals_clean) else None,
        "max": float(vals_clean.max()) if len(vals_clean) else None,
        "mean": float(vals_clean.mean()) if len(vals_clean) else None,
        "nonzero": int(np.sum(np.abs(vals_clean) > 0.01)) if len(vals_clean) else 0,
        "n": int(len(vals_clean)),
    }

report["input_vars"] = {}
for v in sorted(inp.data_vars):
    da = inp[v]
    report["input_vars"][v] = {
        "dims": list(da.dims),
        "shape": list(da.shape),
    }

report["input_coords"] = {}
for c in sorted(inp.coords):
    vals = inp.coords[c].values
    report["input_coords"][c] = {
        "n": len(vals),
        "sample": [str(x) for x in vals[:10]],
    }

# ── 2. Balance / price duals ──
balance_vars = [v for v in dual.data_vars if "balance" in v.lower()]
report["balance_duals"] = {}
for v in balance_vars:
    da = dual[v]
    if "area" in da.dims:
        for area_val in da.coords["area"].values:
            sub = da.sel(area=area_val)
            # Handle extra dims (resource, year_op)
            if "resource" in sub.dims:
                for res in sub.coords["resource"].values:
                    sub2 = sub.sel(resource=res)
                    vals = sub2.values.flatten()
                    vals = vals[~np.isnan(vals)]
                    key = f"{v}|{area_val}|{res}"
                    report["balance_duals"][key] = {
                        "mean": float(vals.mean()),
                        "median": float(np.median(vals)),
                        "min": float(vals.min()),
                        "max": float(vals.max()),
                        "p95": float(np.percentile(vals, 95)),
                        "p99": float(np.percentile(vals, 99)),
                        "hours_at_voll": int(np.sum(vals >= 29999)),
                        "hours_negative": int(np.sum(vals < -0.01)),
                        "hours_gt_1000": int(np.sum(vals > 1000)),
                    }
            else:
                vals = sub.values.flatten()
                vals = vals[~np.isnan(vals)]
                key = f"{v}|{area_val}"
                report["balance_duals"][key] = {
                    "mean": float(vals.mean()),
                    "min": float(vals.min()),
                    "max": float(vals.max()),
                }

# ── 3. Dispatch / generation ──
report["dispatch"] = {}
for v in sol.data_vars:
    da = sol[v]
    if "hour" in da.dims and ("conversion_tech" in da.dims or "storage_tech" in da.dims):
        tech_dim = "conversion_tech" if "conversion_tech" in da.dims else "storage_tech"
        for area_val in (da.coords["area"].values if "area" in da.dims else ["all"]):
            for tech in da.coords[tech_dim].values:
                try:
                    sub = da.sel(**{tech_dim: tech})
                    if "area" in sub.dims:
                        sub = sub.sel(area=area_val)
                    # Squeeze remaining non-hour dims
                    extra = [d for d in sub.dims if d != "hour"]
                    for d in extra:
                        sub = sub.isel(**{d: 0})
                    vals = sub.values.flatten()
                    vals = vals[~np.isnan(vals)]
                    key = f"{v}|{area_val}|{tech}"
                    report["dispatch"][key] = {
                        "annual_gwh": float(vals.sum()) / 1e3,
                        "peak_mw": float(vals.max()),
                        "mean_mw": float(vals.mean()),
                        "hours_active": int(np.sum(vals > 0.1)),
                        "capacity_factor": float(vals.mean() / vals.max()) if vals.max() > 0 else 0,
                    }
                except Exception as e:
                    report["dispatch"][f"{v}|{area_val}|{tech}|ERROR"] = str(e)

# ── 4. Load shedding detail ──
report["load_shedding"] = {}
for v in sol.data_vars:
    if "shed" in v.lower():
        da = sol[v]
        if "area" in da.dims:
            for area_val in da.coords["area"].values:
                sub = da.sel(area=area_val)
                vals = sub.values.flatten()
                vals = vals[~np.isnan(vals)]
                nonzero = vals[vals > 0.1]

                # Seasonal breakdown
                seasonal = {}
                month_starts = {
                    "Jan": 0, "Feb": 744, "Mar": 1416, "Apr": 2160,
                    "May": 2880, "Jun": 3624, "Jul": 4344, "Aug": 5088,
                    "Sep": 5832, "Oct": 6552, "Nov": 7296, "Dec": 8016
                }
                for m_name, m_start in month_starts.items():
                    m_end = m_start + 744
                    m_vals = vals[m_start:min(m_end, len(vals))]
                    m_shed = m_vals[m_vals > 0.1]
                    if len(m_shed) > 0:
                        seasonal[m_name] = {
                            "hours": int(len(m_shed)),
                            "ens_gwh": float(m_shed.sum()/1e3),
                            "peak_mw": float(m_shed.max()),
                        }

                report["load_shedding"][f"{v}|{area_val}"] = {
                    "total_hours": int(len(nonzero)),
                    "total_ens_gwh": float(vals.sum()/1e3),
                    "peak_mw": float(vals.max()) if len(vals) > 0 else 0,
                    "avg_during_shedding_mw": float(nonzero.mean()) if len(nonzero) > 0 else 0,
                    "seasonal": seasonal,
                }

# ── 5. Ramping duals ──
report["ramp_duals"] = {}
for v in dual.data_vars:
    if "ramp" in v.lower():
        da = dual[v]
        vals = da.values.flatten()
        vals = vals[~np.isnan(vals)]
        nonzero = vals[np.abs(vals) > 0.01]
        report["ramp_duals"][v] = {
            "dims": list(da.dims),
            "total_elements": int(len(vals)),
            "binding_count": int(len(nonzero)),
            "binding_pct": float(100 * len(nonzero) / max(len(vals), 1)),
            "min": float(nonzero.min()) if len(nonzero) else 0,
            "max": float(nonzero.max()) if len(nonzero) else 0,
            "mean_when_binding": float(nonzero.mean()) if len(nonzero) else 0,
        }
        # Per-tech breakdown if possible
        if "conversion_tech" in da.dims:
            per_tech = {}
            for tech in da.coords["conversion_tech"].values:
                sub = da.sel(conversion_tech=tech)
                tv = sub.values.flatten()
                tv = tv[~np.isnan(tv)]
                tnz = tv[np.abs(tv) > 0.01]
                if len(tnz) > 0:
                    per_tech[str(tech)] = {
                        "binding_hours": int(len(tnz)),
                        "min": float(tnz.min()),
                        "max": float(tnz.max()),
                    }
            report["ramp_duals"][v + "_per_tech"] = per_tech

# ── 6. Transport / interconnection duals ──
report["transport_duals"] = {}
for v in dual.data_vars:
    if "transport" in v.lower() or "flow" in v.lower() or "link" in v.lower():
        da = dual[v]
        vals = da.values.flatten()
        vals = vals[~np.isnan(vals)]
        nonzero = vals[np.abs(vals) > 0.01]
        report["transport_duals"][v] = {
            "dims": list(da.dims),
            "total_elements": int(len(vals)),
            "congested_count": int(len(nonzero)),
            "congested_pct": float(100 * len(nonzero) / max(len(vals), 1)),
            "min": float(nonzero.min()) if len(nonzero) else 0,
            "max": float(nonzero.max()) if len(nonzero) else 0,
            "mean_when_congested": float(nonzero.mean()) if len(nonzero) else 0,
        }

# ── 7. Investment / capacity constraint duals ──
report["invest_duals"] = {}
for v in dual.data_vars:
    if "invest" in v.lower() or ("capacity" in v.lower() and "hour" not in dual[v].dims):
        da = dual[v]
        vals = da.values.flatten()
        vals = vals[~np.isnan(vals)]
        nonzero = vals[np.abs(vals) > 0.01]
        entry = {
            "dims": list(da.dims),
            "total_elements": int(len(vals)),
            "nonzero_count": int(len(nonzero)),
            "min": float(vals.min()) if len(vals) else 0,
            "max": float(vals.max()) if len(vals) else 0,
        }
        # Try to extract per-tech or per-area detail
        if "conversion_tech" in da.dims:
            detail = {}
            for tech in da.coords["conversion_tech"].values:
                sub = da.sel(conversion_tech=tech)
                sv = sub.values.flatten()
                sv = sv[~np.isnan(sv)]
                snz = sv[np.abs(sv) > 0.01]
                if len(snz) > 0:
                    if "area" in sub.dims:
                        for area_val in sub.coords["area"].values:
                            sub2 = sub.sel(area=area_val)
                            sv2 = sub2.values.flatten()
                            sv2 = sv2[~np.isnan(sv2)]
                            snz2 = sv2[np.abs(sv2) > 0.01]
                            if len(snz2) > 0:
                                detail[f"{tech}|{area_val}"] = {
                                    "value": float(snz2.sum()),
                                    "values": [float(x) for x in snz2[:5]],
                                }
                    else:
                        detail[str(tech)] = {"value": float(snz.sum()), "values": [float(x) for x in snz[:5]]}
            entry["detail"] = detail
        report["invest_duals"][v] = entry

# ── 8. Storage state-of-charge ──
report["storage"] = {}
for v in sol.data_vars:
    da = sol[v]
    if "storage_tech" in da.dims and "hour" in da.dims:
        for area_val in (da.coords["area"].values if "area" in da.dims else ["all"]):
            for tech in da.coords["storage_tech"].values:
                try:
                    sub = da.sel(storage_tech=tech)
                    if "area" in sub.dims:
                        sub = sub.sel(area=area_val)
                    extra = [d for d in sub.dims if d != "hour"]
                    for d in extra:
                        sub = sub.isel(**{d: 0})
                    vals = sub.values.flatten()
                    vals = vals[~np.isnan(vals)]
                    key = f"{v}|{area_val}|{tech}"
                    report["storage"][key] = {
                        "min": float(vals.min()),
                        "max": float(vals.max()),
                        "mean": float(vals.mean()),
                        "hours_empty": int(np.sum(vals < 0.01 * vals.max())) if vals.max() > 0 else 0,
                        "hours_full": int(np.sum(vals > 0.99 * vals.max())) if vals.max() > 0 else 0,
                    }
                except Exception as e:
                    report["storage"][f"{v}|{area_val}|{tech}|ERROR"] = str(e)

# ── 9. Transport flows from solution ──
report["transport_flows"] = {}
for v in sol.data_vars:
    if "transport" in v.lower() and "hour" in sol[v].dims:
        da = sol[v]
        vals = da.values.flatten()
        vals = vals[~np.isnan(vals)]
        report["transport_flows"][v] = {
            "dims": list(da.dims),
            "shape": list(da.shape),
            "min": float(vals.min()),
            "max": float(vals.max()),
            "mean": float(vals.mean()),
            "hours_at_cap": int(np.sum(vals > 3999)),  # 4 GW cap
        }
        # Per-link detail if possible
        if "link" in da.dims:
            for link in da.coords["link"].values:
                sub = da.sel(link=link)
                extra = [d for d in sub.dims if d != "hour"]
                for d in extra:
                    sub = sub.isel(**{d: 0})
                sv = sub.values.flatten()
                sv = sv[~np.isnan(sv)]
                report["transport_flows"][f"{v}|{link}"] = {
                    "min": float(sv.min()),
                    "max": float(sv.max()),
                    "mean": float(sv.mean()),
                    "hours_at_cap": int(np.sum(sv > 3999)),
                }

# ── 10. Flexibility (demand response) ──
report["flexibility"] = {}
for v in sol.data_vars:
    if "flex" in v.lower():
        da = sol[v]
        vals = da.values.flatten()
        vals = vals[~np.isnan(vals)]
        report["flexibility"][v] = {
            "dims": list(da.dims),
            "shape": list(da.shape),
            "min": float(vals.min()) if len(vals) else 0,
            "max": float(vals.max()) if len(vals) else 0,
            "mean": float(vals.mean()) if len(vals) else 0,
            "nonzero": int(np.sum(np.abs(vals) > 0.01)),
        }

# ── 11. Spillage ──
report["spillage"] = {}
for v in sol.data_vars:
    if "spill" in v.lower():
        da = sol[v]
        if "area" in da.dims:
            for area_val in da.coords["area"].values:
                sub = da.sel(area=area_val)
                vals = sub.values.flatten()
                vals = vals[~np.isnan(vals)]
                report["spillage"][f"{v}|{area_val}"] = {
                    "total_gwh": float(vals.sum()/1e3),
                    "peak_mw": float(vals.max()),
                    "hours": int(np.sum(vals > 0.1)),
                }

# ── 12. Input params check ──
report["input_check"] = {}
# Check conversion capacities in input
for v in inp.data_vars:
    if "capacity" in v.lower() and "conversion" in v.lower():
        da = inp[v]
        if "conversion_tech" in da.dims and "area" in da.dims:
            for area_val in da.coords["area"].values:
                for tech in da.coords["conversion_tech"].values:
                    try:
                        sub = da.sel(area=area_val, conversion_tech=tech)
                        vals = sub.values.flatten()
                        vals = vals[~np.isnan(vals)]
                        if len(vals) > 0 and vals.max() > 0:
                            report["input_check"][f"{v}|{area_val}|{tech}"] = {
                                "values": [float(x) for x in vals],
                            }
                    except:
                        pass

# Check ramp inputs
for v in inp.data_vars:
    if "ramp" in v.lower():
        da = inp[v]
        report["input_check"][f"RAMP|{v}"] = {
            "dims": list(da.dims),
            "values_sample": [float(x) for x in da.values.flatten()[:20] if not np.isnan(x)],
        }

# Write report
with open(OUT, "w") as f:
    json.dump(report, f, indent=2, default=str)

print(f"Analysis report written to {OUT}")
print(f"Sections: {list(report.keys())}")
for section, data in report.items():
    print(f"  {section}: {len(data)} entries")
