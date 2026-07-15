#!/usr/bin/env python
"""Weather-ensemble solve that REUSES clever.runner's proven pipeline.

Per user directive (2026-07-10): do NOT re-implement the solve — the runner
accumulated months of convergence fixes (sanitize passes, NaN sweeps, solver
options, IIS diagnostics). This script only adds the year_op stacking; every
solve-related decision is imported from clever.runner.

Inputs: the per-weather-year `input_dataset_2050.nc` files dumped by
CLEVER_BUILD_ONLY=1 runs of scripts/run_adequacy.py. Those dumps are taken at
the very END of run_model_without_ramping's pre-solve pipeline (check_inputs,
case-variant tech merge, R0 overrides, sanitize_*, CAPEX-in-objective fix,
blanket NaN sweep) — i.e. they are exactly what run_adequacy itself hands to
build_model. No transformation is re-guessed here.

Stacking (validated on identical-years: objective ratio 1.0005, fleet 1.00001):
  year_inv=[2050] shared fleet; year_op=[2050..2050+N-1] one per weather year;
  discount_factor=1/N each; operation_year_duration=8760 each.

Alignment: files from different builds store coordinates in different ORDER
(sets verified identical). We normalise every year to the master's coordinate
order with label-based reindexing BEFORE concatenation, then concat with
join="exact" so any residual mismatch fails loudly instead of outer-joining.

On solve failure the runner's own _export_gurobi_iis is invoked, and a
DualReductions=0 retry distinguishes infeasible from unbounded.

Env:
  CLEVER_ENS_BASE    base scenario name WITHOUT the _wyYYYY suffix   (required)
  CLEVER_ENS_YEARS   comma list, default "2017,2018,2024"
  CLEVER_DIAG_ROOT   default results/diagnostics
  CLEVER_ENS_OUT     default results_ensemble/<TAG or BASE>
  CLEVER_ENS_TAG     short output-dir tag
  CLEVER_GRB_THREADS threads (runner reads this too)
  CLEVER_ENS_SUBSET_HOURS  smoke test: first K hours only
"""
from __future__ import annotations
import os
import re
import json
import logging
from pathlib import Path

import numpy as np
import xarray as xr

from pommes.model.build_model import build_model
from pommes.model.data_validation.dataset_check import check_inputs

# ── THE pipeline: everything solve-related comes from the proven runner ──
from clever.runner import build_solver_options, _export_gurobi_iis

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("run_ensemble")


# ═══════════════════════════════════════════════════════════════════
# 1. Load + strictly align the per-year dumps
# ═══════════════════════════════════════════════════════════════════

def _find_year_nc(diag_root: Path, base: str, year: int) -> Path:
    cands = [diag_root / f"{base}_wy{year}" / "input_dataset_2050.nc"]
    if year == 2024:  # 2024 is the default weather year
        cands.append(diag_root / base / "input_dataset_2050.nc")
    for c in cands:
        if c.exists():
            return c
    raise FileNotFoundError(f"No input_dataset for weather year {year}: " +
                            " ; ".join(map(str, cands)))


def load_aligned(diag_root: Path, base: str, years: list[int]) -> dict[int, xr.Dataset]:
    """Load per-year dumps and normalise them onto the FIRST year's coordinate
    order. Sets must be identical (verified with a hard assert per dimension);
    ordering differences — which triggered xarray's outer-join fallback and the
    downstream corruption — are eliminated by label-based reindexing."""
    srcs: dict[int, xr.Dataset] = {}
    master: xr.Dataset | None = None
    for y in years:
        nc = _find_year_nc(diag_root, base, y)
        ds = xr.open_dataset(nc).load()
        logger.info("loaded %s  (hours=%d, areas=%d)", nc, ds.sizes["hour"], ds.sizes["area"])
        if master is None:
            master = ds
        else:
            # 1) identical variable sets — no silent NaN-fill through concat
            mv, dv = set(master.data_vars), set(ds.data_vars)
            if mv != dv:
                raise ValueError(f"data_vars differ for wy{y}: only_master={sorted(mv-dv)} "
                                 f"only_{y}={sorted(dv-mv)}")
            # 2) identical coordinate SETS per dim, then reindex to master ORDER
            for dim in master.dims:
                if dim not in master.coords or dim not in ds.coords:
                    continue
                sm = set(map(str, master[dim].values))
                sy = set(map(str, ds[dim].values))
                if sm != sy:
                    raise ValueError(f"coord set differs on '{dim}' for wy{y}: "
                                     f"only_master={sorted(sm-sy)[:8]} only_{y}={sorted(sy-sm)[:8]}")
            reorder = {d: master[d].values for d in master.dims
                       if d in master.coords and d in ds.coords}
            ds = ds.reindex(reorder)  # label-based; cannot invent entries (sets equal)
            for d, vals in reorder.items():
                assert list(ds[d].values) == list(vals), f"reindex failed on {d}"
        srcs[y] = ds
    return srcs


def build_ensemble(srcs: dict[int, xr.Dataset], years: list[int]):
    """Stack aligned per-year datasets along year_op. join='exact' everywhere —
    after load_aligned there is nothing left to align, and any surprise aborts."""
    N = len(years)
    labels = [2050 + i for i in range(N)]
    ref = srcs[years[0]]
    yo_vars = [v for v in ref.data_vars if "year_op" in ref[v].dims]

    yo = xr.concat(
        [srcs[y][yo_vars].assign_coords(year_op=[labels[i]]) for i, y in enumerate(years)],
        dim="year_op", data_vars="all", coords="minimal", join="exact",
    )
    shared = ref.drop_vars(yo_vars)
    if "year_op" in shared.coords:
        shared = shared.drop_vars("year_op")
    p = xr.merge([shared, yo], join="exact")

    p["discount_factor"] = ("year_op", np.full(N, 1.0 / N))
    p["operation_year_duration"] = ("year_op", np.full(N, 8760.0))
    return p, {labels[i]: years[i] for i in range(N)}


def audit_merged(p: xr.Dataset, srcs: dict[int, xr.Dataset], years: list[int]) -> None:
    """Fail-loud audit: no variable may gain NaNs relative to its sources, and
    cost/bound-critical vars must be NaN-free where their sources were."""
    bad = []
    for v in p.data_vars:
        n = int(np.isnan(p[v].values).sum()) if np.issubdtype(p[v].dtype, np.floating) else 0
        if "year_op" in p[v].dims:
            exp = sum(int(np.isnan(s[v].values).sum()) for s in srcs.values())
        else:
            exp = int(np.isnan(srcs[years[0]][v].values).sum()) \
                if np.issubdtype(srcs[years[0]][v].dtype, np.floating) else 0
        if n > exp:
            bad.append(f"{v}: merged={n} > sources={exp}")
    if bad:
        raise ValueError("MERGE-INTRODUCED NaNs — aborting before solve:\n  " + "\n  ".join(bad))
    logger.info("merged-audit clean: no variable gained NaNs (%d vars checked)", len(p.data_vars))


# ═══════════════════════════════════════════════════════════════════
# 1b. Schema-conform + validate (pommes check_inputs on the merged set)
# ═══════════════════════════════════════════════════════════════════
# The runner's post-check_inputs transformations (e.g. enforce_capex_in_objective)
# broadcast some flags over year_op because their masks (cap_max > 0) are
# year_op-indexed. Harmless as a singleton in single-year runs, but in the merged
# ensemble those flags MUST be year_op-free per pommes' schema — and if a flag
# differed across weather years, the CAPEX accounting would silently corrupt
# (a plausible cause of the "infeasible or unbounded" full-scale failures).
# Policy: booleans/0-1 flags reduce with any() over year_op (shared fleet: CAPEX
# is in the objective if the tech is active in ANY weather year); numerics must
# be IDENTICAL across years (assert, then collapse) — a true difference aborts.

_SCHEMA_ERR = re.compile(r"For (\w+): \[(.*?)\] not in \[(.*?)\]")

# Capacity-bound vars that the per-year builds derive from WEATHER (hydro inflow
# caps: e.g. Reservoir_Hydro_Inflow differs -15%..+113% across 2017/2018/2024).
# The per-year builds PIN all four bounds to that year's inflow value. In the
# merged ensemble the fleet is shared, so all four must be pinned to ONE value.
# CRITICAL (root cause of the 2026-07-10 full-scale infeasibility): the two
# investment_* vars are schema-ILLEGAL with year_op, so check_inputs flags them
# and an error-driven fix harmonises them — but capacity_max/min are schema-
# LEGAL with year_op, stay per-year, and the harmonised investment FLOOR then
# exceeds the dry years' capacity_max (e.g. DE 603>105, AT 2102>1580, ES
# 13360>6052) -> presolve-detected INFEASIBLE. Hence: proactive harmonisation
# of the WHOLE quartet, not error-driven patching.
# Exactness: shared cap S = max over years; each year's conversion_availability
# at the affected (area, tech) is rescaled by cap_y / S — because each dump
# normalises availability to its OWN year's cap, S x avail_y x cap_y/S
# reproduces every year's true inflow time-series to the MWh.
_BOUND_VARS = {
    "conversion_power_capacity_investment_max", "conversion_power_capacity_investment_min",
    "conversion_power_capacity_max", "conversion_power_capacity_min",
}


def harmonise_weather_capacity(p: xr.Dataset) -> xr.Dataset:
    """Proactively pin weather-dependent capacity bounds to a shared value and
    rescale availability. Runs BEFORE check_inputs so the schema-legal per-year
    capacity_max/min are harmonised too (the error-driven loop cannot see them)."""
    cm = p["conversion_power_capacity_max"]           # (area, tech, year_op)
    if "year_op" not in cm.dims:
        return p
    vmax = cm.max("year_op", skipna=True)
    vmin = cm.min("year_op", skipna=True)
    aff = (vmax - vmin) > 1e-6
    aff = aff & np.isfinite(vmax.values) & np.isfinite(vmin.values)
    n = int(aff.sum())
    if n == 0:
        logger.info("harmonise_weather_capacity: no weather-dependent bounds — nothing to do")
        return p
    # report + sanity: at affected entries the quartet should be pinned per year
    idx = np.argwhere(aff.values)
    for ij in idx:
        sel = {d: aff.coords[d].values[k] for d, k in zip(aff.dims, ij)}
        per_year = {int(y): round(float(cm.sel(**sel).sel(year_op=y)), 1) for y in cm["year_op"].values}
        logger.warning("weather-dependent capacity at %s: per-year=%s -> pinned to max, "
                       "availability rescaled by cap_y/S", sel, per_year)
    # 1) rescale availability by cap_y / S at affected entries (ratio from the
    #    ORIGINAL per-year caps, before any overwrite)
    ratio = (cm / vmax)
    ratio = ratio.where(np.isfinite(ratio), 1.0).where(aff, 1.0)
    if "conversion_availability" in p:
        p["conversion_availability"] = p["conversion_availability"] * ratio
    # 2) pin the whole quartet to its own year_op-max at affected entries
    for v in _BOUND_VARS:
        if v not in p or "year_op" not in p[v].dims:
            continue
        da = p[v]
        mx = da.max("year_op", skipna=True)
        p[v] = da.where(~aff, mx)  # aff broadcasts over year_inv/year_op
    logger.warning("harmonise_weather_capacity: pinned %d (area,tech) entries to shared max "
                   "+ rescaled availability (inflow series preserved exactly).", n)
    return p


def _harmonise_weather_bounds(p: xr.Dataset, var: str, rescaled: set) -> xr.Dataset:
    da = p[var]
    if "year_inv" in da.dims:
        assert da.sizes["year_inv"] == 1, "multi-year_inv not supported here"
    vmax = da.max("year_op", skipna=True)
    vmin = da.min("year_op", skipna=True)
    diff = (vmax - vmin) > max(1e-6, 0.0)
    diff = diff & np.isfinite(vmax) & np.isfinite(vmin)
    d2 = diff.squeeze("year_inv", drop=True) if "year_inv" in diff.dims else diff
    n_aff = int(d2.sum())
    if n_aff:
        # log the affected entries with their per-year values
        idx = np.argwhere(d2.values)
        for ij in idx[:12]:
            sel = {dim: d2.coords[dim].values[k] for dim, k in zip(d2.dims, ij)}
            per_year = {int(y): float(da.sel(**sel).sel(year_op=y).squeeze())
                        for y in da["year_op"].values}
            logger.warning("weather-dependent bound %s at %s: per-year=%s -> shared=max, "
                           "availability rescaled", var, sel, per_year)
        # rescale availability ONCE per (area, tech) — not once per bound var
        pairs = {(str(sel[0]), str(sel[1])) for sel in
                 (tuple(d2.coords[dim].values[k] for dim, k in zip(d2.dims, ij)) for ij in idx)}
        new_pairs = pairs - rescaled
        if new_pairs and "conversion_availability" in p:
            ratio = (da / vmax).where(np.isfinite(da / vmax), 1.0)
            if "year_inv" in ratio.dims:
                ratio = ratio.squeeze("year_inv", drop=True)
            mask = xr.zeros_like(d2, dtype=bool)
            for a, t in new_pairs:
                mask.loc[{"area": a, "conversion_tech": t}] = True
            ratio = ratio.where(mask, 1.0)
            p["conversion_availability"] = p["conversion_availability"] * ratio
            rescaled |= new_pairs
            logger.warning("rescaled conversion_availability for %d (area,tech) pairs: %s",
                           len(new_pairs), sorted(new_pairs)[:8])
    p[var] = vmax
    logger.warning("schema-conform: '%s' -> max over year_op (%d weather-dependent entries).",
                   var, n_aff)
    return p


def conform_and_validate(p: xr.Dataset, max_fixes: int = 60) -> xr.Dataset:
    rescaled: set = set()
    for _ in range(max_fixes):
        try:
            return check_inputs(p)
        except ValueError as e:
            m = _SCHEMA_ERR.search(str(e))
            if not m:
                raise
            var = m.group(1)
            actual = set(re.findall(r"'([^']+)'", m.group(2)))
            allowed = set(re.findall(r"'([^']+)'", m.group(3)))
            extra = actual - allowed
            if extra != {"year_op"} or var not in p:
                raise  # not the known broadcast pattern — surface it
            if var in _BOUND_VARS:
                p = _harmonise_weather_bounds(p, var, rescaled)
                continue
            da = p[var]
            vals = da.values
            is_flag = da.dtype == bool or (
                np.issubdtype(da.dtype, np.number)
                and np.all(np.isin(vals[~np.isnan(vals)] if np.issubdtype(da.dtype, np.floating) else vals, (0, 1)))
            )
            if is_flag:
                p[var] = da.max("year_op").astype(da.dtype)
                logger.warning("schema-conform: '%s' had a broadcast year_op dim — "
                               "reduced with any() over weather years (shared fleet).", var)
            else:
                first = da.isel(year_op=0, drop=True)
                for i in range(1, da.sizes["year_op"]):
                    other = da.isel(year_op=i, drop=True)
                    a = np.nan_to_num(first.values, nan=-9.9e99)
                    b = np.nan_to_num(other.values, nan=-9.9e99)
                    if not np.allclose(a, b, rtol=1e-9, atol=1e-9):
                        raise ValueError(
                            f"'{var}': pommes schema forbids year_op but its values DIFFER "
                            f"across weather years — real cross-year inconsistency, aborting."
                        ) from e
                p[var] = first
                logger.warning("schema-conform: '%s' identical across year_op — collapsed.", var)
    raise RuntimeError("conform_and_validate: exceeded max schema fixes — inspect the dataset.")


# ═══════════════════════════════════════════════════════════════════
# 2. Solve — runner's options, runner's failure diagnostics
# ═══════════════════════════════════════════════════════════════════

def solve(p: xr.Dataset, outdir: Path):
    m = build_model(p)
    opts = build_solver_options("gurobi")     # Method/Crossover/BarHomogeneous/NumericFocus/Threads/NodefileStart
    opts["OutputFlag"] = 1
    logger.info("solving with runner options: %s", opts)
    m.solve(solver_name="gurobi", **opts)

    term = str(getattr(m, "termination_condition", "")).lower()
    if term != "optimal":
        logger.error("solve not optimal (termination=%s) — running runner diagnostics", term)
        # (a) disambiguate infeasible vs unbounded exactly as the runner would
        diag_opts = dict(opts)
        diag_opts.update({"DualReductions": 0, "InfUnbdInfo": 1, "TimeLimit": 3600})
        try:
            m.solve(solver_name="gurobi", **diag_opts)
            logger.error("DualReductions=0 termination: %s",
                         getattr(m, "termination_condition", "?"))
        except Exception as e:  # noqa: BLE001
            logger.error("diagnostic re-solve failed: %s", e)
        # (b) runner's IIS/feasrelax export (names the offending constraint rows).
        # GATED: IIS on the full 88M-row model OOM-killed job 76623 at 311 GB.
        # Diagnose at reduced scale instead (CLEVER_ENS_SUBSET_HOURS<=5000).
        n_hours = int(p.sizes.get("hour", 0))
        if n_hours <= 5000 or os.environ.get("CLEVER_ENS_FORCE_IIS") == "1":
            try:
                _export_gurobi_iis(m, 2050, outdir)
            except Exception as e:  # noqa: BLE001
                logger.error("IIS export failed: %s", e)
        else:
            logger.error("IIS skipped at full scale (hour=%d; OOM risk) — rerun with "
                         "CLEVER_ENS_SUBSET_HOURS=4380 to diagnose, or CLEVER_ENS_FORCE_IIS=1.",
                         n_hours)
        raise RuntimeError(f"ensemble solve failed: termination={term}; "
                           f"diagnostics in {outdir}")
    return m


# ═══════════════════════════════════════════════════════════════════
# 3. Multi-year_op summary (per-year ENS / LOLE / VoLL cost)
# ═══════════════════════════════════════════════════════════════════

def summarise(sol: xr.Dataset, weather_of: dict[int, int]) -> dict:
    out = {"weather_of_label": {int(k): int(v) for k, v in weather_of.items()}, "per_year": {}}
    ls = sol.get("operation_load_shedding_power")
    ls_cost = sol.get("operation_load_shedding_costs")
    resources = [str(r) for r in sol["resource"].values] if "resource" in sol.coords else []
    elec = next((r for r in resources if "electric" in r.lower()), None)
    h2 = next((r for r in resources if "hydrogen" in r.lower()), None)
    for lbl in map(int, sol["year_op"].values):
        rec = {"weather_year": int(weather_of.get(lbl, lbl))}
        if ls is not None:
            for tag, res in (("e", elec), ("h", h2)):
                if res is None:
                    continue
                a = ls.sel(year_op=lbl, resource=res)
                rec[f"ENS_{tag}_GWh"] = round(float(a.sum()) / 1000.0, 3)
                rec[f"LOLE_{tag}_h"] = int((a.sum("area") > 1.0).sum())
        if ls_cost is not None:
            rec["c_voll_bn"] = round(float(ls_cost.sel(year_op=lbl).sum()) / 1e9, 4)
        out["per_year"][lbl] = rec
    keys = [k for k in next(iter(out["per_year"].values())) if k.startswith(("ENS_", "LOLE_", "c_voll"))]
    for k in keys:
        vals = [out["per_year"][l][k] for l in out["per_year"]]
        out[f"E_{k}"] = round(float(np.mean(vals)), 4)
        out[f"max_{k}"] = round(float(np.max(vals)), 4)
    return out


def main():
    base = os.environ["CLEVER_ENS_BASE"]
    years = [int(y) for y in os.environ.get("CLEVER_ENS_YEARS", "2017,2018,2024").split(",")]
    diag_root = Path(os.environ.get("CLEVER_DIAG_ROOT", "results/diagnostics"))
    tag = os.environ.get("CLEVER_ENS_TAG", base[:60])
    outdir = Path(os.environ.get("CLEVER_ENS_OUT", f"results_ensemble/{tag}"))
    outdir.mkdir(parents=True, exist_ok=True)

    logger.info("ensemble base=%s years=%s", base, years)
    srcs = load_aligned(diag_root, base, years)
    p, weather_of = build_ensemble(srcs, years)

    # fail-loud data audit, then the runner's OWN structural validator on the
    # merged multi-year_op dataset (audit gap #2: check_inputs was never re-run
    # post-merge in the old script).
    audit_merged(p, srcs, years)
    p = harmonise_weather_capacity(p)   # BEFORE check_inputs: pins the schema-legal per-year caps too
    p = conform_and_validate(p)
    logger.info("check_inputs on merged ensemble: OK")

    subset = os.environ.get("CLEVER_ENS_SUBSET_HOURS")
    if subset:
        K = int(subset)
        p = p.isel(hour=slice(0, K))
        p["operation_year_duration"] = ("year_op", np.full(p.sizes["year_op"], float(K)))
        logger.info("SMOKE TEST: first %d hours only", K)

    logger.info("merged: year_op=%s discount_factor=%s",
                list(p["year_op"].values), list(np.round(p["discount_factor"].values, 4)))

    m = solve(p, outdir)
    obj = float(m.objective.value)
    logger.info("objective (CAPEX + E_weather[OPEX]) = %s", f"{obj:,.1f}")

    p.to_netcdf(outdir / "input_dataset_ens.nc")
    m.solution.to_netcdf(outdir / "solution_ens.nc")
    try:
        m.dual.to_netcdf(outdir / "dual_ens.nc")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not write duals: %s", e)

    summary = summarise(m.solution, weather_of)
    summary.update({"objective": obj, "objective_bn": round(obj / 1e9, 3),
                    "base": base, "years": years})
    if "E_c_voll_bn" in summary:
        summary["resource_cost_bn"] = round(summary["objective_bn"] - summary["E_c_voll_bn"], 3)
    (outdir / "ensemble_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    logger.info("wrote -> %s", outdir)


if __name__ == "__main__":
    main()
