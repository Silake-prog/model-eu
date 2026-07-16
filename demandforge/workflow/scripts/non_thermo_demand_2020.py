#!/usr/bin/env python3
"""Non-thermosensitive demand curves for ES, DE, IT — year 2020.

Builds, for Spain (ES), Germany (DE) and Italy (IT), the 2020 hourly
electricity demand decomposed into its thermosensitive and
non-thermosensitive (``baseload``) components, using the
``demandforge`` pipeline.

Deliverables
------------
    1. A 3-panel figure (one per country) comparing raw hourly demand
       and the non-thermosensitive component. Y axis in GW, X axis in
       calendar months.
    2. A country-level summary table (heating rate, cooling rate,
       threshold temperatures, R², annual thermosensitive share).
    3. A per-hour detail table (24 rows per country) with heating /
       cooling slopes, thresholds and R² — intended for an annex.
    4. Console sanity-checks (annual energy, mean population-weighted
       temperature, share, slopes) compared with expected ranges for
       2020 (COVID year, ~5–8 % below trend).

Two execution modes
-------------------
    ``--mode full``  (default)
        Runs the whole demandforge pipeline from scratch:
        country borders → ENTSO-E load → ERA5 temperature →
        GHSL population raster → population-weighted temperature →
        thermosensitivity grid search → baseload decomposition.

        Requires:
            * ``ENTSOE_API_KEY`` in a ``.env`` next to the project root
              (or in the environment).
            * ``~/.cdsapirc`` with valid Copernicus CDS credentials.
            * Network access to ghsl.jrc.ec.europa.eu, gisco, ENTSO-E
              Transparency and CDS.

    ``--mode bucket``
        Short-circuits the heavy acquisition/fit steps by downloading
        the pre-computed ``combined_load_{country}_{year}.parquet``
        files from the public demandforge GCS bucket
        (``https://storage.googleapis.com/demandforge/``). Still runs
        ``analyze_thermosensitivity`` locally to produce the summary
        + hourly-detail tables, because those regression parameters
        are not published in the bucket. Requires credentials for
        ENTSO-E and CDS only if the thermosensitivity CSVs are not
        already cached in ``RESULTS_DIR/thermosensitivity/``.

Usage
-----
    # Full run, default locations
    python scripts/non_thermo_demand_2020.py

    # Use the GCS bucket for the decomposed time series (faster)
    python scripts/non_thermo_demand_2020.py --mode bucket

    # Custom output folder
    python scripts/non_thermo_demand_2020.py --output-dir ./out

Outputs
-------
    Written under ``RESULTS_DIR/non_thermo_demand_2020/`` (or the
    ``--output-dir`` you pass):

        * ``demand_vs_baseload_2020.png`` — the 3-panel figure
        * ``summary_table.csv``           — country-level KPIs
        * ``hourly_detail.csv``           — per-hour slopes/R²/seuils
        * ``sanity_checks.log``           — plain-text sanity report

Notes on rigour
---------------
    * All energy calculations stay in MW / MWh until the final display
      step which converts to GW / TWh.
    * Weighted means across the 24 hours use ``n_samples`` as weights,
      following the mission brief.
    * Threshold temperatures reported in the summary table are the
      *mode* of the 24 hourly best-fit thresholds (``winter_threshold``
      and ``summer_threshold`` columns in the thermosensitivity CSV).
      The mode is chosen because the grid is discrete (5 winter values,
      5 summer values) and the mean would fall on non-grid points,
      which is misleading for interpretation. The full distribution is
      available in the hourly detail CSV.
    * Hours with R² < 0.3 are flagged in the sanity log as potentially
      weak fits (the ``r2_threshold=0.1`` used inside
      ``process_thermosensitive_share`` is the demandforge default).
    * Any sanity check that falls outside the expected ranges prints a
      warning but does not abort — inspect the log before trusting the
      figures.

Author
------
    Written by Simon Brigode (CIRED) — April 2026.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Credentials — must be set in the environment (or a .env file) BEFORE running
# this script so the ENTSO-E client picks them up at module import time.
# NEVER hardcode the token here: this file is committed. (A previously
# hardcoded key was removed — if you used it, revoke it on the ENTSO-E
# Transparency Platform and generate a new one.)
# ---------------------------------------------------------------------------
if not (os.environ.get("ENTSOE_API_KEY") or os.environ.get("ENTSOE_API_TOKEN")):
    raise SystemExit(
        "ENTSOE_API_KEY (or ENTSOE_API_TOKEN) is not set. Export it or put it in a "
        ".env file before running this script — do not hardcode it."
    )
os.environ.setdefault("ENTSOE_API_KEY", os.environ.get("ENTSOE_API_TOKEN", ""))

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from demandforge import RESULTS_DIR
from demandforge.fetch.country_borders import download_eu_borders
from demandforge.fetch.entsoe_load_curves import save_load_data_to_parquet
from demandforge.fetch.era5 import fetch_era5_temperature_by_country
from demandforge.fetch.ghsl_population_europe import download_ghsl_europe
from demandforge.process.population_weighted_temperature import (
    calculate_population_weighted_temperature,
)
from demandforge.process.thermosensitive_share import (
    process_thermosensitive_share,
)
from demandforge.process.thermosensitivity import analyze_thermosensitivity

# ---------------------------------------------------------------------------
# Pandas ≥2.2 compatibility shim for demandforge.process.thermosensitive_share
# ---------------------------------------------------------------------------
# demandforge's define_seasons uses the uppercase 'H' alias for hourly
# frequency, which was deprecated in pandas 2.0 and removed in 2.2. Monkey-
# patch the function in-place with a version that uses the lowercase 'h'
# alias. The rest of the logic is preserved verbatim.
from demandforge.process import thermosensitive_share as _ts_share_mod


def _define_seasons_pd22(  # noqa: D401
    data: "pd.DataFrame",
    weekly_temp_threshold: float = 12.0,
    rolling_window: int = 4,
) -> "pd.DataFrame":
    """Drop-in replacement for ``demandforge.process.thermosensitive_share.define_seasons``.

    Replaces the deprecated 'H' hourly alias with 'h' so it runs on
    pandas ≥ 2.2. Semantics are identical.
    """
    import pandas as pd  # local import to avoid forward reference issues

    _ts_share_mod.logger.info(
        f"Defining seasons with a weekly temperature threshold of "
        f"{weekly_temp_threshold}°C and a {rolling_window}-week rolling "
        f"average."
    )

    data = data.set_index("timestamp")
    weekly_temp = data["temperature"].resample("W-MON").mean()
    smoothed_weekly_temp = weekly_temp.rolling(
        window=rolling_window, min_periods=1
    ).mean()

    weekly_season = pd.Series("summer", index=smoothed_weekly_temp.index)
    weekly_season[smoothed_weekly_temp < weekly_temp_threshold] = "winter"

    # Pandas ≥ 2.2: use lowercase 'h' instead of the removed 'H' alias.
    data["season"] = weekly_season.resample(data.index.freq or "h").ffill()
    data["season"] = data["season"].bfill()

    _ts_share_mod.logger.info(
        f"Seasons defined: "
        f"{(data['season'] == 'winter').sum() / len(data) * 100:.1f}% "
        f"winter, "
        f"{(data['season'] == 'summer').sum() / len(data) * 100:.1f}% "
        f"summer."
    )

    return data.reset_index()


_ts_share_mod.define_seasons = _define_seasons_pd22

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COUNTRIES: Tuple[str, ...] = ("ES", "DE", "IT")
YEAR: int = 2020
POPULATION_YEARS = [2000, 2005, 2010, 2015, 2020, 2025]

# Colour-blind-friendly palette (ColorBrewer "Dark2"), consistent across
# all three panels.
COLORS: Dict[str, str] = {
    "ES": "#d95f02",  # orange
    "DE": "#1b9e77",  # teal
    "IT": "#7570b3",  # purple
}

# Sanity-check ranges for 2020 (COVID year). Sources: ENTSO-E statistical
# factsheets, RTE Bilan Prévisionnel, ERAA 2021. Used only to flag gross
# errors, not as ground truth.
EXPECTED_RANGES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "ES": {
        "annual_energy_twh": (230.0, 260.0),
        "mean_pop_weighted_temp_c": (14.0, 17.0),
        "thermosensitive_share": (0.15, 0.25),
        "heating_mw_per_c": (-400.0, -200.0),
        "cooling_mw_per_c": (300.0, 600.0),
    },
    "DE": {
        "annual_energy_twh": (460.0, 510.0),
        "mean_pop_weighted_temp_c": (8.0, 11.0),
        "thermosensitive_share": (0.15, 0.20),
        "heating_mw_per_c": (-700.0, -400.0),
        "cooling_mw_per_c": (100.0, 200.0),
    },
    "IT": {
        "annual_energy_twh": (280.0, 320.0),
        "mean_pop_weighted_temp_c": (13.0, 16.0),
        "thermosensitive_share": (0.15, 0.25),
        "heating_mw_per_c": (-500.0, -300.0),
        "cooling_mw_per_c": (400.0, 800.0),
    },
}

BUCKET_URL = "https://storage.googleapis.com/demandforge"

logger = logging.getLogger("non_thermo_demand_2020")


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def ensure_borders() -> Path:
    """Download the EU country-borders GPKG if missing.

    Returns:
        Path to ``RESULTS_DIR/country_borders.gpkg``.
    """
    borders_path = RESULTS_DIR / "country_borders.gpkg"
    if not borders_path.exists():
        logger.info("Downloading EU country borders (GISCO)")
        download_eu_borders()
    if not borders_path.exists():
        raise FileNotFoundError(
            f"country_borders.gpkg still missing after download_eu_borders() "
            f"— check GISCO availability ({borders_path})"
        )
    return borders_path


def ensure_ghsl_raster(pop_year: int) -> Path:
    """Download the GHSL-POP raster for ``pop_year`` if missing.

    Args:
        pop_year: GHSL release year (must match one of the files the
            JRC portal exposes).

    Returns:
        Path to ``RESULTS_DIR/ghsl/ghsl_pop_europe_{pop_year}.tif``.
    """
    ghsl_dir = RESULTS_DIR / "ghsl"
    ghsl_dir.mkdir(parents=True, exist_ok=True)
    tif_path = ghsl_dir / f"ghsl_pop_europe_{pop_year}.tif"
    if not tif_path.exists():
        logger.info(f"Downloading GHSL-POP raster for {pop_year}")
        download_ghsl_europe(year=pop_year, output_path=tif_path)
    if not tif_path.exists():
        raise FileNotFoundError(
            f"GHSL raster {tif_path} still missing after download_ghsl_europe()"
        )
    return tif_path


def ensure_entsoe_load(country: str, year: int) -> Path:
    """Fetch and cache the ENTSO-E load parquet for ``(country, year)``.

    Returns:
        Path to ``RESULTS_DIR/entsoe/entsoe_load_{country}_{year}.parquet``.
    """
    load_path = RESULTS_DIR / "entsoe" / f"entsoe_load_{country}_{year}.parquet"
    if not load_path.exists():
        logger.info(f"[{country}] Fetching ENTSO-E hourly load for {year}")
        save_load_data_to_parquet(country, year)
    return load_path


def ensure_era5(country: str, year: int, borders_path: Path) -> Path:
    """Fetch and cache the ERA5 temperature NetCDF for ``(country, year)``.

    Returns:
        Path to ``RESULTS_DIR/era5/era5_temp_{country}_{year}.nc``.
    """
    era5_dir = RESULTS_DIR / "era5"
    era5_dir.mkdir(parents=True, exist_ok=True)
    era5_path = era5_dir / f"era5_temp_{country}_{year}.nc"
    if not era5_path.exists():
        logger.info(f"[{country}] Fetching ERA5 2m_temperature for {year}")
        fetch_era5_temperature_by_country(
            country_id=country,
            year=year,
            gpkg_path=borders_path,
            output_file=era5_path,
            europe_only=True,
        )
    return era5_path


def ensure_weighted_temp(
    country: str,
    year: int,
    borders_path: Path,
    era5_path: Path,
) -> Path:
    """Compute and cache the population-weighted mean temperature.

    Returns:
        Path to ``RESULTS_DIR/weighted_temp/weighted_temp_{country}_{year}.parquet``.
    """
    out_dir = RESULTS_DIR / "weighted_temp"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"weighted_temp_{country}_{year}.parquet"
    if not out_path.exists():
        logger.info(f"[{country}] Computing population-weighted temperature")
        # Make sure *at least* the closest GHSL year is available. We
        # download every year in POPULATION_YEARS lazily — cheap if
        # already on disk, skipped if present.
        for py in POPULATION_YEARS:
            ensure_ghsl_raster(py)
        calculate_population_weighted_temperature(
            country_code=country,
            year=year,
            population_years=POPULATION_YEARS,
            borders_path=borders_path,
            era5_path=era5_path,
            ghsl_base_path=RESULTS_DIR / "ghsl",
            output_path=out_path,
        )
    return out_path


def ensure_thermosensitivity_csv(
    country: str,
    year: int,
    load_path: Path,
    temp_path: Path,
) -> Path:
    """Run the thermosensitivity grid search and return the CSV path.

    Returns:
        Path to ``RESULTS_DIR/thermosensitivity/thermosensitivity_results_{country}_{year}.csv``.
    """
    out_dir = RESULTS_DIR / "thermosensitivity"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"thermosensitivity_results_{country}_{year}.csv"
    if not csv_path.exists():
        logger.info(f"[{country}] Running thermosensitivity analysis")
        analyze_thermosensitivity(
            country=country,
            year=year,
            load_path=load_path,
            temp_path=temp_path,
            output_dir=out_dir,
        )
    return csv_path


def ensure_share_parquet(
    country: str,
    year: int,
    csv_path: Path,
    load_path: Path,
    temp_path: Path,
    r2_threshold: float = 0.1,
) -> Path:
    """Decompose load into baseload + winter_TS + summer_TS.

    Returns:
        Path to ``RESULTS_DIR/thermosensitive_share/share_{country}_{year}.parquet``.
    """
    out_dir = RESULTS_DIR / "thermosensitive_share"
    out_dir.mkdir(parents=True, exist_ok=True)
    share_parquet = out_dir / f"share_{country}_{year}.parquet"
    share_pdf = out_dir / f"share_{country}_{year}.pdf"
    if not share_parquet.exists():
        logger.info(f"[{country}] Computing thermosensitive share (r2>{r2_threshold})")
        process_thermosensitive_share(
            thermo_csv=str(csv_path),
            load_parquet=str(load_path),
            temp_parquet=str(temp_path),
            output_parquet=str(share_parquet),
            output_pdf=str(share_pdf),
            country=country,
            year=year,
            r2_threshold=r2_threshold,
        )
    return share_parquet


def try_download_thermo_csv_from_bucket(country: str, year: int) -> Optional[Path]:
    """Try to download the thermosensitivity CSV from the GCS bucket.

    Returns the local path if the download succeeds, ``None`` if the
    bucket does not expose the file (HTTP 404) so the caller can fall
    back to running the fit locally.
    """
    out_dir = RESULTS_DIR / "thermosensitivity"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"thermosensitivity_results_{country}_{year}.csv"
    if out_path.exists():
        return out_path
    url = f"{BUCKET_URL}/thermosensitivity_results_{country}_{year}.csv"
    logger.info(f"[{country}] Trying {url}")
    try:
        urllib.request.urlretrieve(url, out_path)
        return out_path
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.info(
                f"[{country}] thermosensitivity CSV not on bucket "
                f"(HTTP 404) — will fall back to local fit"
            )
            out_path.unlink(missing_ok=True)
            return None
        raise
    except urllib.error.URLError as exc:
        logger.warning(f"[{country}] Bucket unreachable for thermo CSV: {exc}")
        out_path.unlink(missing_ok=True)
        return None


def ensure_combined_load_from_bucket(country: str, year: int) -> Path:
    """Download the pre-computed combined_load parquet from the GCS bucket.

    The file contains (at least) ``total_load``, ``baseload``,
    ``winter_thermosensitive_load`` and ``summer_thermosensitive_load``
    — everything we need for the figure and the thermosensitive share.

    Returns:
        Path to ``RESULTS_DIR/combined_load/combined_load_{country}_{year}.parquet``.
    """
    out_dir = RESULTS_DIR / "combined_load"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"combined_load_{country}_{year}.parquet"
    if not out_path.exists():
        url = f"{BUCKET_URL}/combined_load_{country}_{year}.parquet"
        logger.info(f"[{country}] Downloading {url}")
        try:
            urllib.request.urlretrieve(url, out_path)
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Failed to download {url}. Check network access or fall "
                f"back to --mode full."
            ) from exc
    return out_path


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def _read_decomposed(path: Path) -> pd.DataFrame:
    """Load a decomposed-load parquet and normalise its schema.

    Both the bucket ``combined_load_*.parquet`` and the local
    ``share_*.parquet`` share the same four columns of interest.

    Args:
        path: Path to the parquet file.

    Returns:
        DataFrame with a sorted UTC ``DatetimeIndex`` and columns
        ``total_load``, ``baseload``, ``winter_thermosensitive_load``,
        ``summer_thermosensitive_load``.
    """
    df = pd.read_parquet(path)

    # Handle both "timestamp column" and "DatetimeIndex" schemas.
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
    elif not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(
            f"{path} has neither a 'timestamp' column nor a DatetimeIndex"
        )
    else:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")

    required = {
        "total_load",
        "baseload",
        "winter_thermosensitive_load",
        "summer_thermosensitive_load",
    }
    missing = required - set(df.columns)
    if missing:
        raise KeyError(f"{path} is missing required columns: {missing}")

    df = df.sort_index()

    # The bucket's combined_load_DE_2020.parquet actually contains multiple
    # years of hourly data stacked (observed: 35136 hourly rows ≈ 4 years).
    # Filter on YEAR so the summary reflects the target year only. This is a
    # no-op on ES/IT files which already contain exactly 8784 hourly rows
    # for 2020.
    n_before = len(df)
    df = df[df.index.year == YEAR]
    n_after = len(df)
    if n_before != n_after:
        logger.warning(
            "Filtered %s from %d → %d rows to isolate year %d "
            "(file contained %.1f years of data)",
            path.name,
            n_before,
            n_after,
            YEAR,
            n_before / max(n_after, 1),
        )

    # Second guard: collapse any remaining duplicate/sub-hourly timestamps
    # by flooring to the hour. Idempotent on well-formed hourly series.
    n_pre_floor = len(df)
    df = df.groupby(df.index.floor("h")).mean(numeric_only=True)
    if len(df) != n_pre_floor:
        logger.warning(
            "Collapsed %s from %d → %d rows via hourly-floor groupby",
            path.name,
            n_pre_floor,
            len(df),
        )

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    logger.info(
        "Loaded %s → %d hourly rows for %d, total_load sum = %.2f TWh",
        path.name,
        len(df),
        YEAR,
        df["total_load"].sum() / 1e6,
    )

    return df


def _weighted_mean(
    values: pd.Series, weights: pd.Series
) -> float:
    """Weighted mean with NaN- and zero-weight-safe handling."""
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    mask = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not mask.any():
        return float("nan")
    return float(np.average(v[mask], weights=w[mask]))


def summarise_thermosensitivity(
    csv_path: Path,
    r2_min: float = 0.1,
) -> Dict[str, float]:
    """Aggregate the per-hour regression results into a country summary.

    Rules (from the mission brief):
        * Heating rate = n_samples-weighted mean of
          ``thermosensitivity_mw_per_c`` over rows with
          ``best_regression=True``, ``season='winter'``,
          ``r_squared >= r2_min``.
        * Cooling rate = idem for ``season='summer'``.
        * Threshold temperatures = mode of ``winter_threshold`` /
          ``summer_threshold`` across the 24 best-fit rows.
        * R² reported as n_samples-weighted means, split by season.

    Args:
        csv_path: Path to ``thermosensitivity_results_*.csv``.
        r2_min: Minimum R² to include a row in the weighted mean of
            slopes. Defaults to the demandforge ``r2_threshold`` of 0.1.

    Returns:
        Dict of summary statistics.
    """
    df = pd.read_csv(csv_path)
    best = df[df["best_regression"] == True].copy()  # noqa: E712

    # R² gate (mission brief rule).
    winter_r2 = best[
        (best["season"] == "winter") & (best["r_squared"] >= r2_min)
    ]
    summer_r2 = best[
        (best["season"] == "summer") & (best["r_squared"] >= r2_min)
    ]

    # Physical sign gate: heating MUST reduce load as T rises (slope < 0);
    # cooling MUST increase load as T rises (slope > 0). Any hour violating
    # this is dominated by noise and would corrupt the weighted mean.
    winter = winter_r2[winter_r2["thermosensitivity_mw_per_c"] < 0]
    summer = summer_r2[summer_r2["thermosensitivity_mw_per_c"] > 0]

    n_winter_wrong_sign = int(len(winter_r2) - len(winter))
    n_summer_wrong_sign = int(len(summer_r2) - len(summer))
    if n_winter_wrong_sign:
        logger.warning(
            "%s: dropped %d winter hours with positive slope (non-physical)",
            csv_path.name,
            n_winter_wrong_sign,
        )
    if n_summer_wrong_sign:
        logger.warning(
            "%s: dropped %d summer hours with negative slope (non-physical)",
            csv_path.name,
            n_summer_wrong_sign,
        )

    heating = _weighted_mean(
        winter["thermosensitivity_mw_per_c"], winter["n_samples"]
    )
    cooling = _weighted_mean(
        summer["thermosensitivity_mw_per_c"], summer["n_samples"]
    )
    r2_winter = _weighted_mean(winter["r_squared"], winter["n_samples"])
    r2_summer = _weighted_mean(summer["r_squared"], summer["n_samples"])

    winter_mode = winter["winter_threshold"].mode()
    summer_mode = summer["summer_threshold"].mode()

    return {
        "heating_mw_per_c": heating,
        "cooling_mw_per_c": cooling,
        "winter_threshold_c_mode": (
            float(winter_mode.iloc[0]) if not winter_mode.empty else float("nan")
        ),
        "summer_threshold_c_mode": (
            float(summer_mode.iloc[0]) if not summer_mode.empty else float("nan")
        ),
        "r2_winter_mean": r2_winter,
        "r2_summer_mean": r2_summer,
        "n_winter_hours_used": int(len(winter)),
        "n_summer_hours_used": int(len(summer)),
        "n_winter_hours_r2_low": int((winter["r_squared"] < 0.3).sum()),
        "n_summer_hours_r2_low": int((summer["r_squared"] < 0.3).sum()),
        "n_winter_hours_wrong_sign": n_winter_wrong_sign,
        "n_summer_hours_wrong_sign": n_summer_wrong_sign,
    }


def build_hourly_detail(csv_path: Path) -> pd.DataFrame:
    """Return a 24-row-per-country table of best-fit parameters.

    Columns: ``hour``, ``heating_mw_per_c``, ``heating_r2``,
    ``winter_threshold_c``, ``heating_n_samples``, ``cooling_mw_per_c``,
    ``cooling_r2``, ``summer_threshold_c``, ``cooling_n_samples``.
    """
    df = pd.read_csv(csv_path)
    best = df[df["best_regression"] == True]  # noqa: E712

    w = (
        best[best["season"] == "winter"]
        .set_index("hour")[
            ["thermosensitivity_mw_per_c", "r_squared", "winter_threshold", "n_samples"]
        ]
        .rename(
            columns={
                "thermosensitivity_mw_per_c": "heating_mw_per_c",
                "r_squared": "heating_r2",
                "winter_threshold": "winter_threshold_c",
                "n_samples": "heating_n_samples",
            }
        )
    )
    s = (
        best[best["season"] == "summer"]
        .set_index("hour")[
            ["thermosensitivity_mw_per_c", "r_squared", "summer_threshold", "n_samples"]
        ]
        .rename(
            columns={
                "thermosensitivity_mw_per_c": "cooling_mw_per_c",
                "r_squared": "cooling_r2",
                "summer_threshold": "summer_threshold_c",
                "n_samples": "cooling_n_samples",
            }
        )
    )

    full = pd.concat([w, s], axis=1)
    full = full.reindex(range(24))  # guarantee 24 rows
    full.index.name = "hour"
    return full


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------


def _flag(value: float, lo: float, hi: float) -> str:
    if not np.isfinite(value):
        return "NA"
    return "OK " if lo <= value <= hi else "!! "


def sanity_check_country(
    country: str,
    df: pd.DataFrame,
    summary: Dict[str, float],
    weighted_temp_path: Optional[Path],
) -> str:
    """Compare observed statistics with EXPECTED_RANGES and return a log block."""
    annual_energy_twh = df["total_load"].sum() / 1e6  # MW·h → TWh
    ts = (
        df["winter_thermosensitive_load"]
        + df["summer_thermosensitive_load"]
    )
    thermo_share = ts.sum() / df["total_load"].sum()

    mean_temp: float = float("nan")
    if weighted_temp_path is not None and Path(weighted_temp_path).exists():
        try:
            tdf = pd.read_parquet(weighted_temp_path)
            mean_temp = float(tdf["temperature"].mean())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{country}] Could not read {weighted_temp_path}: {exc}")

    exp = EXPECTED_RANGES[country]
    lines = [
        f"=== {country} {YEAR} sanity checks ===",
        f"  Annual energy     : {annual_energy_twh:8.2f} TWh      "
        f"[exp {exp['annual_energy_twh'][0]:>3.0f}–{exp['annual_energy_twh'][1]:>3.0f}]  "
        f"{_flag(annual_energy_twh, *exp['annual_energy_twh'])}",
    ]
    if np.isfinite(mean_temp):
        lines.append(
            f"  Mean pop-w temp   : {mean_temp:8.2f} °C       "
            f"[exp {exp['mean_pop_weighted_temp_c'][0]:>3.0f}–"
            f"{exp['mean_pop_weighted_temp_c'][1]:>3.0f}]  "
            f"{_flag(mean_temp, *exp['mean_pop_weighted_temp_c'])}"
        )
    else:
        lines.append("  Mean pop-w temp   :    (weighted_temp parquet not found)")
    lines.extend(
        [
            f"  Thermo share      : {thermo_share * 100:8.2f} %        "
            f"[exp {exp['thermosensitive_share'][0] * 100:>3.0f}–"
            f"{exp['thermosensitive_share'][1] * 100:>3.0f}]  "
            f"{_flag(thermo_share, *exp['thermosensitive_share'])}",
            f"  Heating rate      : {summary['heating_mw_per_c']:8.1f} MW/°C   "
            f"[exp {exp['heating_mw_per_c'][0]:>4.0f}–{exp['heating_mw_per_c'][1]:>4.0f}]  "
            f"{_flag(summary['heating_mw_per_c'], *exp['heating_mw_per_c'])}",
            f"  Cooling rate      : {summary['cooling_mw_per_c']:8.1f} MW/°C   "
            f"[exp {exp['cooling_mw_per_c'][0]:>4.0f}–{exp['cooling_mw_per_c'][1]:>4.0f}]  "
            f"{_flag(summary['cooling_mw_per_c'], *exp['cooling_mw_per_c'])}",
            f"  Winter seuil mode : {summary['winter_threshold_c_mode']:8.1f} °C",
            f"  Summer seuil mode : {summary['summer_threshold_c_mode']:8.1f} °C",
            f"  R² winter (mean)  : {summary['r2_winter_mean']:8.3f}   "
            f"(hours used: {summary['n_winter_hours_used']}/24, "
            f"R²<0.3: {summary['n_winter_hours_r2_low']})",
            f"  R² summer (mean)  : {summary['r2_summer_mean']:8.3f}   "
            f"(hours used: {summary['n_summer_hours_used']}/24, "
            f"R²<0.3: {summary['n_summer_hours_r2_low']})",
        ]
    )
    block = "\n".join(lines)
    logger.info("\n" + block)
    return block


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------


def make_figure(
    decomposed: Dict[str, pd.DataFrame], output_path: Path
) -> None:
    """3-panel figure: total demand vs. non-thermosensitive demand."""
    fig, axes = plt.subplots(
        nrows=3, ncols=1, figsize=(12, 10), sharex=True
    )

    for ax, country in zip(axes, COUNTRIES):
        df = decomposed[country]
        total_gw = df["total_load"] / 1_000.0       # MW → GW
        baseload_gw = df["baseload"] / 1_000.0
        colour = COLORS[country]

        ax.plot(
            df.index,
            total_gw,
            color=colour,
            alpha=0.35,
            linewidth=0.6,
            label="Demande totale",
        )
        ax.plot(
            df.index,
            baseload_gw,
            color=colour,
            alpha=1.0,
            linewidth=0.8,
            label="Demande hors thermosensible",
        )
        ax.set_ylabel("Puissance [GW]")
        ax.set_title(f"{country} — {YEAR}", loc="left", fontsize=11)
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

    axes[-1].set_xlabel(f"Mois de l'année {YEAR}")
    fig.suptitle(
        "Courbes horaires de demande électrique — totale vs. hors thermosensible",
        fontsize=13,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.97))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"Figure saved to {output_path}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def run(mode: str, output_dir: Path, r2_threshold: float) -> None:
    """Execute the pipeline and emit all deliverables."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sanity_log_path = output_dir / "sanity_checks.log"
    sanity_blocks: list[str] = []

    # Shared resources (full mode only).
    borders_path: Optional[Path] = None
    if mode == "full":
        borders_path = ensure_borders()

    decomposed: Dict[str, pd.DataFrame] = {}
    summaries: Dict[str, Dict[str, float]] = {}
    hourly_tables: Dict[str, pd.DataFrame] = {}

    for country in COUNTRIES:
        # --- time series (figure input) -------------------------------
        if mode == "full":
            assert borders_path is not None  # for mypy
            load_path = ensure_entsoe_load(country, YEAR)
            era5_path = ensure_era5(country, YEAR, borders_path)
            temp_path = ensure_weighted_temp(
                country, YEAR, borders_path, era5_path
            )
            csv_path = ensure_thermosensitivity_csv(
                country, YEAR, load_path, temp_path
            )
            share_path = ensure_share_parquet(
                country,
                YEAR,
                csv_path,
                load_path,
                temp_path,
                r2_threshold=r2_threshold,
            )
            decomposed[country] = _read_decomposed(share_path)
        else:  # bucket
            combined = ensure_combined_load_from_bucket(country, YEAR)
            decomposed[country] = _read_decomposed(combined)

            # Try to fetch the thermosensitivity CSV from the bucket
            # first. If the bucket does not publish it, fall back to
            # running the fit locally (which requires ENTSO-E + ERA5
            # + GHSL — and therefore valid CDS credentials + accepted
            # licences).
            csv_path = try_download_thermo_csv_from_bucket(country, YEAR)
            if csv_path is None:
                logger.warning(
                    f"[{country}] Falling back to local fit — this "
                    f"requires CDS credentials and the accepted ERA5 "
                    f"licence (see https://cds.climate.copernicus.eu/"
                    f"datasets/reanalysis-era5-single-levels"
                    f"?tab=download#manage-licences)"
                )
                if borders_path is None:
                    borders_path = ensure_borders()
                load_path = ensure_entsoe_load(country, YEAR)
                era5_path = ensure_era5(country, YEAR, borders_path)
                temp_path = ensure_weighted_temp(
                    country, YEAR, borders_path, era5_path
                )
                csv_path = ensure_thermosensitivity_csv(
                    country, YEAR, load_path, temp_path
                )
            temp_path = RESULTS_DIR / "weighted_temp" / (
                f"weighted_temp_{country}_{YEAR}.parquet"
            )

        # --- summary + hourly detail ---------------------------------
        summaries[country] = summarise_thermosensitivity(
            csv_path, r2_min=r2_threshold
        )
        hourly_tables[country] = build_hourly_detail(csv_path)

        # --- sanity checks -------------------------------------------
        weighted_temp_path = RESULTS_DIR / "weighted_temp" / (
            f"weighted_temp_{country}_{YEAR}.parquet"
        )
        sanity_blocks.append(
            sanity_check_country(
                country,
                decomposed[country],
                summaries[country],
                weighted_temp_path,
            )
        )

    # ---- figure ------------------------------------------------------
    make_figure(decomposed, output_dir / "demand_vs_baseload_2020.png")

    # ---- summary table ----------------------------------------------
    summary_df = pd.DataFrame(summaries).T
    summary_df.index.name = "country"
    # Add a couple of convenience columns derived from the time series.
    energies = []
    shares = []
    for country in COUNTRIES:
        df = decomposed[country]
        energies.append(df["total_load"].sum() / 1e6)  # TWh
        ts = (
            df["winter_thermosensitive_load"]
            + df["summer_thermosensitive_load"]
        )
        shares.append(ts.sum() / df["total_load"].sum())
    summary_df["annual_energy_twh"] = energies
    summary_df["thermosensitive_share"] = shares

    summary_df = summary_df[
        [
            "annual_energy_twh",
            "thermosensitive_share",
            "heating_mw_per_c",
            "cooling_mw_per_c",
            "winter_threshold_c_mode",
            "summer_threshold_c_mode",
            "r2_winter_mean",
            "r2_summer_mean",
            "n_winter_hours_used",
            "n_summer_hours_used",
            "n_winter_hours_r2_low",
            "n_summer_hours_r2_low",
        ]
    ]
    summary_path = output_dir / "summary_table.csv"
    summary_df.to_csv(summary_path, float_format="%.4f")
    logger.info(f"Summary table:\n{summary_df.round(3).to_string()}")
    logger.info(f"Summary table saved to {summary_path}")

    # ---- hourly detail -----------------------------------------------
    hourly_long = pd.concat(
        hourly_tables, names=["country", "hour"]
    ).reset_index()
    hourly_path = output_dir / "hourly_detail.csv"
    hourly_long.to_csv(hourly_path, index=False, float_format="%.4f")
    logger.info(f"Hourly detail saved to {hourly_path}")

    # ---- sanity log --------------------------------------------------
    sanity_log_path.write_text("\n\n".join(sanity_blocks) + "\n", encoding="utf-8")
    logger.info(f"Sanity log saved to {sanity_log_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Produce the non-thermosensitive demand curves for "
        "ES, DE and IT (year 2020) using demandforge.",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "bucket"),
        default="bucket",
        help="'bucket' (default) downloads combined_load_*.parquet and "
        "thermosensitivity_results_*.csv from the public demandforge GCS "
        "bucket when available, and only falls back to the full fit "
        "pipeline if any file is missing. 'full' always runs the entire "
        "demandforge pipeline from scratch (ENTSO-E, ERA5, GHSL, fit, "
        "decomposition).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "results" / "non_thermo_demand_2020",
        help="Folder where the figure, summary and hourly tables will "
        "be written. Defaults to ./results/non_thermo_demand_2020 "
        "relative to the current working directory (NOT RESULTS_DIR, "
        "which on some installs resolves inside site-packages).",
    )
    parser.add_argument(
        "--r2-threshold",
        type=float,
        default=0.1,
        help="Minimum R² for including a regression in weighted means "
        "and in the share decomposition (default: 0.1, the demandforge "
        "default).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logger.info(f"RESULTS_DIR = {RESULTS_DIR}")
    logger.info(f"Mode = {args.mode}   Output dir = {args.output_dir}")

    try:
        run(
            mode=args.mode,
            output_dir=args.output_dir,
            r2_threshold=args.r2_threshold,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Pipeline failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
