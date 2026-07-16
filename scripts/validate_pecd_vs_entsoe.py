"""Cross-source validation: compare ENTSO-E vs PECD weather-derived inputs.

Soft reporting only (no hard asserts; cf. PROMPT §6.6) — different methods are
expected to broadly agree, not to be identical. For one country/year it reports,
per intermittent plant type, the annual-mean capacity factor, the monthly profile,
the Pearson correlation and the mean bias (pecd − entsoe); and for reservoir inflow
the seasonal profile + correlation of the shape-normalised series (only the shape
matters downstream).

Usage
-----
Produce the canonical files under each source into two separate directories, e.g.::

    snakemake --cores all --config res_source=entsoe hydro_source=entsoe \
        results/capacity_factors/capacity_factors_FR_2009.parquet \
        results/inflow/inflow_FR_2009.parquet
    mv results/capacity_factors results/_entsoe_cf && mv results/inflow results/_entsoe_inflow
    snakemake --cores all --config res_source=pecd hydro_source=pecd \
        results/capacity_factors/capacity_factors_FR_2009.parquet \
        results/inflow/inflow_FR_2009.parquet

then::

    python scripts/validate_pecd_vs_entsoe.py --country FR --year 2009 \
        --entsoe-dir results/_entsoe --pecd-dir results

(Each ``--*-dir`` is a results root containing ``capacity_factors/`` and ``inflow/``.)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

PLANT_TYPES = ["Solar", "Wind Onshore", "Wind Offshore", "Hydro Run-of-river and poundage"]


def _cf_series(path: Path, plant_type: str, n: int = 8760) -> np.ndarray | None:
    """Return the 8760-hour capacity-factor series for a plant type, or None."""
    if not path.is_file():
        return None
    df = pl.read_parquet(path)
    if "plant_type" not in df.columns:
        return None
    sub = df.filter(pl.col("plant_type") == plant_type)
    if "WS" in sub.columns and sub.height > n:  # single-WS selection if multiple
        first_ws = sub["WS"][0]
        sub = sub.filter(pl.col("WS") == first_ws)
    if sub.is_empty():
        return None
    vals = sub["capacity_factor"].to_numpy().astype(float)
    return np.resize(vals, n)


def _monthly_mean(values: np.ndarray, year: int) -> pd.Series:
    months = pd.date_range(f"{year}-01-01", periods=len(values), freq="h").month
    return pd.Series(values).groupby(months).mean()


def _corr_bias(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 2:
        return float("nan"), float("nan")
    corr = float(np.corrcoef(a[mask], b[mask])[0, 1])
    bias = float(np.mean(b[mask] - a[mask]))
    return corr, bias


def compare(country: str, year: int, entsoe_dir: Path, pecd_dir: Path) -> None:
    cf_name = f"capacity_factors/capacity_factors_{country}_{year}.parquet"
    inflow_name = f"inflow/inflow_{country}_{year}.parquet"

    print(f"\n=== {country} {year}: ENTSO-E vs PECD ===")
    print(f"{'plant_type':<34} {'entsoe_mean':>12} {'pecd_mean':>10} {'corr':>7} {'bias':>8}")
    for pt in PLANT_TYPES:
        a = _cf_series(entsoe_dir / cf_name, pt, 8760)
        b = _cf_series(pecd_dir / cf_name, pt, 8760)
        if a is None or b is None:
            print(f"{pt:<34} {'(missing in ' + ('entsoe' if a is None else 'pecd') + ')':>40}")
            continue
        corr, bias = _corr_bias(a, b)
        print(f"{pt:<34} {np.nanmean(a):>12.4f} {np.nanmean(b):>10.4f} {corr:>7.3f} {bias:>+8.4f}")
        ma, mb = _monthly_mean(a, year), _monthly_mean(b, year)
        print(f"{'  monthly entsoe':<34} " + " ".join(f"{v:.2f}" for v in ma))
        print(f"{'  monthly pecd':<34} " + " ".join(f"{v:.2f}" for v in mb))

    # Reservoir inflow: shape-normalised (the model normalises by max).
    ea = entsoe_dir / inflow_name
    pb = pecd_dir / inflow_name
    if ea.is_file() and pb.is_file():
        ia = pl.read_parquet(ea)["inflow_MW"].to_numpy().astype(float)
        ib = pl.read_parquet(pb)["inflow_MW"].to_numpy().astype(float)
        ia, ib = np.resize(ia, 8760), np.resize(ib, 8760)
        na = ia / np.nanmax(ia) if np.nanmax(ia) > 0 else ia
        nb = ib / np.nanmax(ib) if np.nanmax(ib) > 0 else ib
        corr, bias = _corr_bias(na, nb)
        print(f"\nReservoir inflow (shape-normalised): corr={corr:.3f}, bias={bias:+.4f}")
        print(f"  monthly entsoe " + " ".join(f"{v:.2f}" for v in _monthly_mean(na, year)))
        print(f"  monthly pecd   " + " ".join(f"{v:.2f}" for v in _monthly_mean(nb, year)))
    else:
        print("\nReservoir inflow: one or both files missing; skipped.")
    print("\nNote: broad agreement (not identity) is expected — different methods.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--country", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--entsoe-dir", type=Path, required=True, help="results root for the entsoe run")
    ap.add_argument("--pecd-dir", type=Path, required=True, help="results root for the pecd run")
    args = ap.parse_args()
    compare(args.country, args.year, args.entsoe_dir, args.pecd_dir)


if __name__ == "__main__":
    main()
