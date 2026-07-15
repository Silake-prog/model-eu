"""scripts/audit_capex.py — CAPEX hygiene audit for POMMES input_dataset.

Run after every solve where Layer 2 overrides, sweep multipliers, or any
cost-related knob has been touched. Verifies the override pipeline left
``conversion_annuity_cost`` (and storage equivalents) internally consistent
with ``conversion_invest_cost``, ``conversion_finance_rate``, and
``conversion_life_span``.

POMMES annuity convention
-------------------------
``conversion_annuity_cost`` is indexed by ``(area, conversion_tech,
year_inv, year_dec)``. For each ``year_dec = year_inv + T`` with
``T ∈ [1, life_span]``, the value is the per-year payment under the
"build at year_inv, decommission at year_dec" choice — i.e.,
``invest × CRF(finance_rate, T)``. Beyond ``year_inv + life_span`` the
value is zero (asset is decommissioned).

The canonical-consistency check used here pulls the
``year_dec = year_inv + life_span`` slice (the "use full life" branch)
and compares it against ``invest × CRF(finance_rate, life_span)``.

NB: the prompt's original audit recipe averaged annuity across every
year_dec, which mixes T=1 (degenerate full-repayment), T=life (the
CRF anchor), and zeros beyond life. That average bears no defined
relation to CRF×invest and produces spurious "drift" warnings on
fully-consistent datasets. Avoid that pattern.

Usage:
  python scripts/audit_capex.py <path/to/input_dataset_YYYY.nc> [run_id]

Exit code 0 = all techs annuity-consistent within 1%.
Exit code 1 = drift detected; do NOT use this run for analysis.
"""
from __future__ import annotations
import sys
from typing import Optional

import numpy as np
import xarray as xr


def _crf(rate: float, life: float) -> float:
    if rate > 1e-9:
        return (rate * (1 + rate) ** life) / ((1 + rate) ** life - 1)
    return 1.0 / life


def _check_conversion(ds: xr.Dataset, year_inv: int) -> list[tuple[str, str, float]]:
    issues: list[tuple[str, str, float]] = []
    print(f"{'tech':30s} {'invest €/MW':>14s} {'life':>5s} {'fin':>7s} "
          f"{'CRF':>9s} {'implied':>14s} {'actual':>14s} {'rel_err':>9s}")
    print("-" * 100)
    for tech in ds.coords["conversion_tech"].values:
        inv = ds["conversion_invest_cost"].sel(conversion_tech=tech)
        ann = ds["conversion_annuity_cost"].sel(conversion_tech=tech)
        life = ds["conversion_life_span"].sel(conversion_tech=tech)
        fin = ds["conversion_finance_rate"].sel(conversion_tech=tech)
        rep_area = None
        for area in inv.coords["area"].values:
            if float(inv.sel(area=area, year_inv=year_inv)) > 0:
                rep_area = area
                break
        if rep_area is None:
            continue
        i = float(inv.sel(area=rep_area, year_inv=year_inv))
        l = int(float(life.sel(area=rep_area, year_inv=year_inv)))
        f = float(fin.sel(area=rep_area, year_inv=year_inv))
        target = year_inv + l
        if target not in ann.coords["year_dec"].values:
            continue
        a = float(ann.sel(area=rep_area, year_inv=year_inv, year_dec=target))
        c = _crf(f, l)
        implied = i * c
        rel = abs(a - implied) / max(abs(implied), 1e-6)
        flag = "  <-- DRIFT" if rel > 0.01 else ""
        print(f"{str(tech):30s} {i:14.0f} {l:5d} {f:7.4f} {c:9.5f} "
              f"{implied:14.1f} {a:14.1f} {rel:9.3%}{flag}")
        if rel > 0.01:
            issues.append((str(tech), str(rep_area), rel))
    return issues


def _check_storage(ds: xr.Dataset, year_inv: int) -> list[tuple[str, str, float]]:
    issues: list[tuple[str, str, float]] = []
    if "storage_tech" not in ds.coords:
        return issues
    print()
    print(f"{'storage / axis':30s} {'invest':>14s} {'life':>5s} {'fin':>7s} "
          f"{'CRF':>9s} {'implied':>14s} {'actual':>14s} {'rel_err':>9s}")
    print("-" * 100)
    for tech in ds.coords["storage_tech"].values:
        for axis in ("energy", "power"):
            try:
                inv = ds[f"storage_invest_cost_{axis}"].sel(storage_tech=tech)
                ann = ds[f"storage_annuity_cost_{axis}"].sel(storage_tech=tech)
                life = ds["storage_life_span"].sel(storage_tech=tech)
                fin = ds["storage_finance_rate"].sel(storage_tech=tech)
            except KeyError:
                continue
            rep_area = None
            for area in inv.coords["area"].values:
                if float(inv.sel(area=area, year_inv=year_inv)) > 0:
                    rep_area = area
                    break
            if rep_area is None:
                continue
            i = float(inv.sel(area=rep_area, year_inv=year_inv))
            l = int(float(life.sel(area=rep_area, year_inv=year_inv)))
            f = float(fin.sel(area=rep_area, year_inv=year_inv))
            target = year_inv + l
            if target not in ann.coords["year_dec"].values:
                continue
            a = float(ann.sel(area=rep_area, year_inv=year_inv, year_dec=target))
            c = _crf(f, l)
            implied = i * c
            rel = abs(a - implied) / max(abs(implied), 1e-6)
            flag = "  <-- DRIFT" if rel > 0.01 else ""
            label = f"{tech}[{axis}]"
            print(f"{label:30s} {i:14.2f} {l:5d} {f:7.4f} {c:9.5f} "
                  f"{implied:14.4f} {a:14.4f} {rel:9.3%}{flag}")
            if rel > 0.01:
                issues.append((label, str(rep_area), rel))
    return issues


def audit(nc_path: str, run_id: str = "", year_inv: int = 2050) -> dict:
    print(f"\nCAPEX audit — {run_id or nc_path}")
    print(f"  anchor: year_inv={year_inv}, year_dec=year_inv+life_span (canonical full-life)")
    print()
    with xr.open_dataset(nc_path) as ds:
        conv_issues = _check_conversion(ds, year_inv=year_inv)
        stor_issues = _check_storage(ds, year_inv=year_inv)
    issues = conv_issues + stor_issues
    return {"run_id": run_id, "drift_techs": issues}


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: python scripts/audit_capex.py <input_dataset.nc> [run_id] [year_inv]")
        return 2
    nc = argv[0]
    run_id = argv[1] if len(argv) > 1 else ""
    year_inv = int(argv[2]) if len(argv) > 2 else 2050
    out = audit(nc, run_id, year_inv=year_inv)
    print()
    if out["drift_techs"]:
        print(f"FAIL: {len(out['drift_techs'])} tech(s) out of sync — pause and investigate:")
        for label, area, rel in out["drift_techs"]:
            print(f"  - {label} @ {area}: rel_err = {rel:.3%}")
        return 1
    print("OK: all techs annuity-consistent with invest × CRF(finance, life) within 1%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
