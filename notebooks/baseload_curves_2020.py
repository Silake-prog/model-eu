"""baseload_curves_2020.py

Extract non-thermosensitive (baseload) electricity demand curves for Spain,
Germany and Italy in 2020 by DIRECTLY CALLING the demandforge pipeline.

This script is a thin orchestration wrapper: it does NOT reimplement the
thermosensitivity grid search or the load decomposition, it calls
`demandforge.thermosensitivity.analyze_thermosensitivity` and
`demandforge.thermosensitive_share.process_thermosensitive_share` so that the
results are bit-for-bit identical to the rest of the POMMES/demandforge
production chain and stay in sync with any future update of the library.

For each country the pipeline is:

  1. Load ENTSO-E hourly load (MW) and population-weighted ERA5 temperature
     (deg C) for year 2020. Both are fetched via the demandforge helpers
     `entsoe_load_curves.fetch_entsoe_load` and
     `population_weighted_temperature.compute_weighted_temperature` if the
     parquet files are not already cached under RESULTS_DIR.

  2. Run `thermosensitivity.analyze_thermosensitivity` which performs, per
     hour of day:
       - a grid search over pairs of winter/summer temperature thresholds,
       - two linear regressions load ~ alpha + beta * T on either side,
       - selects the (T_w, T_s) maximising the sum of the two R^2,
     and returns a CSV with the 24 hourly (heating rate, cooling rate,
     winter threshold, summer threshold, R^2) rows plus a PDF report.

  3. Run `thermosensitive_share.process_thermosensitive_share` which
     consumes the CSV from step 2 and the raw load + temperature series
     and returns a parquet with columns (total_load, baseload,
     winter_thermosensitive, summer_thermosensitive) plus a summary dict
     and a PDF report.

  4. Collect the three baseload series, plot them overlaid (daily mean GW)
     and export a one-row-per-country summary CSV with the country-level
     aggregates (mean heating rate, mean cooling rate, median thresholds,
     baseload / thermosensitive shares).

Run:
    python notebooks/baseload_curves_2020.py \
        --countries ES DE IT \
        --year 2020 \
        --out-dir results/baseload/

By default the script uses demandforge.RESULTS_DIR for cached intermediates
(entsoe/, weighted_temp/, thermosensitivity/, thermosensitive_share/); pass
`--no-cache` to force a full re-run.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

# ---------------------------------------------------------------------
# demandforge is expected to be importable in the runtime environment.
# It bundles ENTSO-E fetchers, ERA5 + GHSL population-weighted temperature,
# the thermosensitivity grid search and the load decomposition.
# ---------------------------------------------------------------------
from demandforge import RESULTS_DIR
from demandforge.entsoe_load_curves import fetch_entsoe_load
from demandforge.population_weighted_temperature import (
    compute_weighted_temperature,
)
from demandforge.thermosensitivity import analyze_thermosensitivity
from demandforge.thermosensitive_share import process_thermosensitive_share

logger = logging.getLogger("baseload_curves_2020")

COLOURS = {"ES": "#e63946", "DE": "#1d3557", "IT": "#2a9d8f"}
R2_THRESHOLD = 0.1  # same default as demandforge.thermosensitive_share


# ---------------------------------------------------------------------
# Helpers that either reuse cached parquets or call demandforge fetchers.
# ---------------------------------------------------------------------

def ensure_load_parquet(country: str, year: int, force: bool = False) -> Path:
    path = RESULTS_DIR / "entsoe" / f"entsoe_load_{country}_{year}.parquet"
    if force or not path.is_file():
        logger.info(f"[{country}] fetching ENTSO-E load via demandforge")
        df = fetch_entsoe_load(country, year)   # returns polars DataFrame
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)
    else:
        logger.info(f"[{country}] reusing cached {path.name}")
    return path


def ensure_temp_parquet(country: str, year: int, force: bool = False) -> Path:
    path = RESULTS_DIR / "weighted_temp" / f"weighted_temp_{country}_{year}.parquet"
    if force or not path.is_file():
        logger.info(f"[{country}] computing population-weighted temperature via demandforge")
        compute_weighted_temperature(country=country, year=year, output_path=path)
    else:
        logger.info(f"[{country}] reusing cached {path.name}")
    return path


# ---------------------------------------------------------------------
# Plot helper
# ---------------------------------------------------------------------

def plot_baseload_curves(decompositions: Dict[str, pd.DataFrame],
                         year: int,
                         output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(13, 5.5))
    for cc, df in decompositions.items():
        daily = (df.set_index("timestamp")[["baseload", "total_load"]]
                   .resample("D").mean() / 1000.0)  # GW
        ax.plot(daily.index, daily["baseload"],
                label=f"{cc} — baseload", color=COLOURS.get(cc, "#444"),
                linewidth=1.8)
        ax.plot(daily.index, daily["total_load"],
                label=f"{cc} — total", color=COLOURS.get(cc, "#444"),
                linewidth=0.9, linestyle="--", alpha=0.55)

    ax.set_title(
        f"Demande électrique non-thermosensible (baseload) — {year}\n"
        "Décomposition demandforge.thermosensitive_share, moyennes journalières"
    )
    ax.set_ylabel("Puissance moyenne (GW)")
    ax.set_xlabel("Date")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=3, fontsize=9, frameon=False)
    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Plot saved to {output_path}")


# ---------------------------------------------------------------------
# Country-level summary from the hourly thermosensitivity CSV
# ---------------------------------------------------------------------

def summarise_country(country: str,
                      year: int,
                      thermo_csv: Path,
                      decomposition: pd.DataFrame,
                      r2_threshold: float = R2_THRESHOLD) -> Dict:
    thermo = pd.read_csv(thermo_csv)
    thermo = thermo[thermo["best_regression"] == True]

    winter_ok = thermo[(thermo["season"] == "winter")
                       & (thermo["r_squared"] >= r2_threshold)]
    summer_ok = thermo[(thermo["season"] == "summer")
                       & (thermo["r_squared"] >= r2_threshold)]

    tot_e = decomposition["total_load"].sum() / 1e6   # TWh
    base_e = decomposition["baseload"].sum() / 1e6
    w_e = decomposition["winter_thermosensitive_load"].sum() / 1e6
    s_e = decomposition["summer_thermosensitive_load"].sum() / 1e6

    return dict(
        country=country,
        year=year,
        annual_energy_twh=float(tot_e),
        baseload_energy_twh=float(base_e),
        winter_thermosensitive_twh=float(w_e),
        summer_thermosensitive_twh=float(s_e),
        thermosensitive_share_pct=float(100.0 * (w_e + s_e) / tot_e) if tot_e > 0 else float("nan"),
        mean_heating_rate_mw_per_c=float(winter_ok["thermosensitivity_mw_per_c"].mean()),
        mean_cooling_rate_mw_per_c=float(summer_ok["thermosensitivity_mw_per_c"].mean()),
        median_winter_threshold_c=float(winter_ok["threshold"].median()),
        median_summer_threshold_c=float(summer_ok["threshold"].median()),
        mean_r2_winter=float(winter_ok["r_squared"].mean()),
        mean_r2_summer=float(summer_ok["r_squared"].mean()),
    )


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def run_country(country: str,
                year: int,
                out_dir: Path,
                force: bool = False) -> tuple[pd.DataFrame, Path, Dict]:
    """Run the full demandforge pipeline for one (country, year)."""
    # Step 1: raw inputs
    load_path = ensure_load_parquet(country, year, force=force)
    temp_path = ensure_temp_parquet(country, year, force=force)

    # Step 2: thermosensitivity grid search (delegated to demandforge)
    thermo_dir = out_dir / "thermosensitivity" / country
    thermo_dir.mkdir(parents=True, exist_ok=True)
    csv_path, _, _ = analyze_thermosensitivity(
        country=country,
        year=year,
        load_path=load_path,
        temp_path=temp_path,
        output_dir=thermo_dir,
        min_samples=30,
    )

    # Step 3: load decomposition (delegated to demandforge)
    share_dir = out_dir / "thermosensitive_share" / country
    share_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = share_dir / f"baseload_{country}_{year}.parquet"
    pdf_path = share_dir / f"baseload_{country}_{year}.pdf"
    result_df, _summary = process_thermosensitive_share(
        thermo_csv=str(csv_path),
        load_parquet=str(load_path),
        temp_parquet=str(temp_path),
        output_parquet=str(parquet_path),
        output_pdf=str(pdf_path),
        country=country,
        year=year,
        r2_threshold=R2_THRESHOLD,
    )

    # Step 4: country-level aggregates
    summary = summarise_country(country, year, csv_path, result_df)
    return result_df, csv_path, summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--countries", nargs="+", default=["ES", "DE", "IT"])
    parser.add_argument("--year", type=int, default=2020)
    parser.add_argument("--out-dir", type=Path,
                        default=Path("results/baseload"))
    parser.add_argument("--no-cache", action="store_true",
                        help="Force re-fetch of load and temperature inputs")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    decompositions: Dict[str, pd.DataFrame] = {}
    summaries: List[Dict] = []

    for cc in args.countries:
        logger.info(f"=== {cc} {args.year} ===")
        result_df, _, summary = run_country(
            country=cc, year=args.year, out_dir=args.out_dir, force=args.no_cache,
        )
        decompositions[cc] = result_df
        summaries.append(summary)

    summary_df = pd.DataFrame(summaries)
    summary_path = args.out_dir / f"thermosensitivity_summary_{args.year}.csv"
    summary_df.to_csv(summary_path, index=False)
    logger.info(f"Country summary -> {summary_path}")
    print(summary_df.to_string(index=False))

    plot_path = args.out_dir / f"baseload_curves_{args.year}.png"
    plot_baseload_curves(decompositions, args.year, plot_path)


if __name__ == "__main__":
    main()
