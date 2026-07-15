#!/usr/bin/env python
"""Merge N single-weather-year POMMES input datasets into ONE ERAA-style
multi-year_op ensemble and solve it.

Method (validated 2026-07-10: an ensemble of 3 *identical* years reproduces the
single-year objective to 5e-4 and the fleet to 1e-5 — i.e. CAPEX is counted once,
OPEX is the weather-weighted expectation):

    * year_inv = [2050]                          -> ONE shared 2050 fleet
    * year_op  = [2050, 2051, ...]  (N labels)   -> one operation year per weather year
    * discount_factor       = 1/N per year_op    -> objective = CAPEX + E_weather[OPEX]
    * operation_year_duration = 8760 per year_op -> operation_year_normalization = 1

Only `demand` and `conversion_availability` (and any other year_op-carrying var that
the per-year build made weather-dependent, e.g. hydro caps) differ across year_ops;
the investment side (indexed by year_inv / no year_op) is shared automatically.

Inputs are the solve-ready `input_dataset_2050.nc` dumped by each per-weather-year
build (run the base scenario with `_wyYYYY` and CLEVER_BUILD_ONLY=1).

Env:
  CLEVER_ENS_YEARS   comma list of weather years, e.g. "2017,2018,2024"
  CLEVER_ENS_BASE    full base scenario name WITHOUT the _wyYYYY suffix
  CLEVER_DIAG_ROOT   dir holding per-scenario diagnostics (default results/diagnostics)
  CLEVER_ENS_OUT     output dir (default results_ensemble/<BASE>_ENS<yy...>)
  CLEVER_GRB_THREADS gurobi threads (default 16)
  CLEVER_ENS_SUBSET_HOURS  optional: solve only the first K hours (smoke test)
  CLEVER_ENS_TIMELIMIT     optional: gurobi TimeLimit seconds (smoke test)
"""
from __future__ import annotations
import os, sys, json
from pathlib import Path
import numpy as np
import xarray as xr
from pommes.model.build_model import build_model


def _find_year_nc(diag_root: Path, base: str, year: int) -> Path:
    cands = [diag_root / f"{base}_wy{year}" / "input_dataset_2050.nc"]
    if year == 2024:  # 2024 is the default weather year -> base scenario has no suffix
        cands.append(diag_root / base / "input_dataset_2050.nc")
    for c in cands:
        if c.exists():
            return c
    raise FileNotFoundError(f"No input_dataset for weather year {year}. Looked in: "
                            + " ; ".join(str(c) for c in cands))


def build_ensemble(srcs: dict[int, xr.Dataset], years: list[int]):
    """srcs: {weather_year: dataset(year_op=[2050])}. Returns (p_merged, weather_of_label)."""
    N = len(years)
    labels = [2050 + i for i in range(N)]                 # year_op labels >= year_inv=2050
    ref = srcs[years[0]]
    yo_vars = [v for v in ref.data_vars if "year_op" in ref[v].dims]

    yo_ens = xr.concat(
        [srcs[y][yo_vars].assign_coords(year_op=[labels[i]]) for i, y in enumerate(years)],
        dim="year_op", data_vars="all", coords="minimal",
    )
    shared = ref.drop_vars(yo_vars)
    if "year_op" in shared.coords:
        shared = shared.drop_vars("year_op")
    p = xr.merge([shared, yo_ens])

    # Equal-weight the weather years: CAPEX once, OPEX = weather expectation.
    p["discount_factor"] = ("year_op", np.full(N, 1.0 / N))
    p["operation_year_duration"] = ("year_op", np.full(N, 8760.0))
    return p, {labels[i]: years[i] for i in range(N)}


def _resource_names(ds: xr.Dataset) -> list[str]:
    return [str(r) for r in ds["resource"].values]


def summarise(sol: xr.Dataset, weather_of: dict[int, int]) -> dict:
    """Per-year_op ENS + LOLE, shared capacity, and weather expectations."""
    out = {"weather_of_label": {int(k): int(v) for k, v in weather_of.items()}, "per_year": {}}
    ls = sol.get("operation_load_shedding_power")   # (area, hour, resource, year_op) MW, 1h steps
    ls_cost = sol.get("operation_load_shedding_costs")  # (area, year_op) EUR — model's actual VoLL term
    resources = _resource_names(sol)
    elec = next((r for r in resources if "electric" in r.lower()), None)
    h2 = next((r for r in resources if "hydrogen" in r.lower()), None)
    for lbl in sol["year_op"].values:
        lbl = int(lbl)
        rec = {"weather_year": int(weather_of.get(lbl, lbl))}
        if ls is not None:
            for tag, res in (("e", elec), ("h", h2)):
                if res is None:
                    continue
                a = ls.sel(year_op=lbl, resource=res)
                ens_gwh = float(a.sum()) / 1000.0                       # MWh->GWh
                # LOLE: hours where system-wide shedding of this resource > 1 MW
                sys_by_hour = a.sum("area")
                lole_h = int((sys_by_hour > 1.0).sum())
                rec[f"ENS_{tag}_GWh"] = round(ens_gwh, 3)
                rec[f"LOLE_{tag}_h"] = lole_h
        # load-shedding (VoLL) cost for this weather year, in bn EUR — uses the model's
        # own cost coefficients, so it is correct for both involuntary VoLL (30k/10k)
        # and demand-response willingness-to-pay (900/400) scenarios.
        if ls_cost is not None:
            rec["c_voll_bn"] = round(float(ls_cost.sel(year_op=lbl).sum()) / 1e9, 4)
        out["per_year"][lbl] = rec
    # weather expectations (equal weight) over per-year ENS / LOLE / VoLL cost
    pk = [k for k in next(iter(out["per_year"].values())).keys()
          if k.startswith(("ENS_", "LOLE_", "c_voll"))]
    for k in pk:
        vals = [out["per_year"][l][k] for l in out["per_year"]]
        out[f"E_{k}"] = round(float(np.mean(vals)), 4)
        out[f"max_{k}"] = round(float(np.max(vals)), 4)
    return out


def main():
    years = [int(y) for y in os.environ["CLEVER_ENS_YEARS"].split(",")]
    base = os.environ["CLEVER_ENS_BASE"]
    diag_root = Path(os.environ.get("CLEVER_DIAG_ROOT", "results/diagnostics"))
    yy = "".join(str(y)[-2:] for y in years)
    outdir = Path(os.environ.get("CLEVER_ENS_OUT", f"results_ensemble/{base}_ENS{yy}"))
    outdir.mkdir(parents=True, exist_ok=True)
    threads = int(os.environ.get("CLEVER_GRB_THREADS", "16"))

    print(f"[ensemble] base={base}")
    print(f"[ensemble] weather years={years}  -> year_op labels={[2050+i for i in range(len(years))]}")
    srcs = {}
    for y in years:
        nc = _find_year_nc(diag_root, base, y)
        ds = xr.open_dataset(nc).load()
        srcs[y] = ds
        print(f"[ensemble]   {y}: {nc}  (hours={ds.sizes['hour']}, areas={ds.sizes['area']})")

    p, weather_of = build_ensemble(srcs, years)

    subset = os.environ.get("CLEVER_ENS_SUBSET_HOURS")
    if subset:
        K = int(subset)
        p = p.isel(hour=slice(0, K))
        p["operation_year_duration"] = ("year_op", np.full(p.sizes["year_op"], float(K)))
        print(f"[ensemble] SMOKE TEST: subset to first {K} hours")

    print(f"[ensemble] merged: year_op={list(p['year_op'].values)}  "
          f"discount_factor={list(p['discount_factor'].values)}  "
          f"op_year_duration={list(p['operation_year_duration'].values)}")

    m = build_model(p)
    # Use the SAME solver options as the paper's single-year runs. Critically this
    # includes NumericFocus=3 + BarHomogeneous=1: this LP is degenerate / ill-
    # conditioned, and without high precision Gurobi takes the fast low-precision
    # path (~10x faster) but returns UNRELIABLE barrier duals — i.e. wrong H2 /
    # electricity prices. Matching build_solver_options keeps the ensemble
    # consistent with every other reported solve.
    os.environ.setdefault("CLEVER_GRB_THREADS", str(threads))
    from clever.runner import build_solver_options
    opts = build_solver_options("gurobi")   # Method=2, Crossover=0, BarHomogeneous=1, NumericFocus=3, NodefileStart=8, Threads
    opts["OutputFlag"] = 1
    tl = os.environ.get("CLEVER_ENS_TIMELIMIT")
    if tl:
        opts["TimeLimit"] = float(tl)
    print(f"[ensemble] solving with paper solver options {opts} ...")
    m.solve(solver_name="gurobi", **opts)

    obj = float(m.objective.value)
    print(f"[ensemble] objective (CAPEX + E_weather[OPEX]) = {obj:,.1f}")

    # dumps
    p.to_netcdf(outdir / "input_dataset_ens.nc")
    try:
        m.solution.to_netcdf(outdir / "solution_ens.nc")
    except Exception as e:
        print(f"[ensemble] WARN could not write solution: {e}")
    try:
        m.dual.to_netcdf(outdir / "dual_ens.nc")
    except Exception as e:
        print(f"[ensemble] WARN could not write dual: {e}")

    summary = summarise(m.solution, weather_of)
    summary["objective"] = obj
    summary["objective_bn"] = round(obj / 1e9, 3)
    # Resource cost (paper headline) = E[totex] minus the weather-expected VoLL term.
    # obj is already the discount-weighted E[totex]; E_c_voll_bn is the equal-weight
    # expectation of the per-year load-shedding cost -> same weighting.
    e_voll = summary.get("E_c_voll_bn")
    if e_voll is not None:
        summary["resource_cost_bn"] = round(summary["objective_bn"] - e_voll, 3)
    summary["base"] = base
    summary["years"] = years
    (outdir / "ensemble_summary.json").write_text(json.dumps(summary, indent=2))
    print("[ensemble] summary:")
    print(json.dumps(summary, indent=2))
    print(f"[ensemble] wrote -> {outdir}")


if __name__ == "__main__":
    main()
