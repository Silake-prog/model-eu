"""Live-data fetchers for resource profiles + benchmark prices (Phase 2.8).

Design rules (handoff §7.2.8):
  1. Cache results to clever/data_cache/ as .nc or .csv with a clear filename.
  2. Hard fallback to a literature-cited static value if the API fails.
  3. Log fetch attempts + fallbacks loudly (never silently fall back).
  4. NEVER invent a source that doesn't exist. There is no reliable hourly
     H₂ price API for MENA as of 2026 — modelled output only.

Currently implemented:
  * fetch_pvgis_solar_hourly(lat, lon, tag) — PVGIS v5.2 TMY, no auth
  * fetch_era5_wind_hourly(lat, lon, tag) — stub returning flat CF; full
    cdsapi implementation deferred to Phase 2.8 second iteration
  * fetch_eu_hydrogen_bank_lcoh_benchmark() — static literature value,
    documented in docstring
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)

# Cache lives next to this module so it travels with the repo (gitignored).
CACHE_DIR: Path = Path(__file__).parent / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)


# ────────────────────────────────────────────────────────────────────────────
# PVGIS solar (EU JRC, free, no auth)
# ────────────────────────────────────────────────────────────────────────────
def fetch_pvgis_solar_hourly(
    lat: float,
    lon: float,
    tag: str,
    *,
    fallback_cf: float = 0.20,
) -> xr.DataArray:
    """Fetch PVGIS TMY hourly solar capacity factor for one point.

    Returns a DataArray of length 8760 with CF in [0, 1].
    Caches to CACHE_DIR/pvgis_{tag}.nc on first success. Falls back to a
    flat CF with a loud logger.error if the API is unreachable.

    Parameters
    ----------
    lat, lon : float    location of representative point
    tag      : str      cache-filename tag (e.g. "mena_MA_solar")
    fallback_cf : float CF used when fetch fails (default 0.20)

    Notes
    -----
    PVGIS v5.2 endpoint:
      https://re.jrc.ec.europa.eu/api/v5_2/seriescalc

    The "P" field in the JSON is Watts per kWp installed; we divide by 1000
    to get a unitless CF. Coverage is global, so MENA reference points work.
    """
    cache = CACHE_DIR / f"pvgis_{tag}.nc"
    if cache.exists():
        try:
            return xr.open_dataarray(cache)
        except Exception as exc:  # corrupted cache — re-fetch
            logger.warning("PVGIS cache %s unreadable (%s); refetching", cache, exc)

    try:
        import requests  # late import — keep base module import cheap
        url = "https://re.jrc.ec.europa.eu/api/v5_2/seriescalc"
        params = {
            "lat": lat,
            "lon": lon,
            "startyear": 2020,
            "endyear":   2020,
            "pvcalculation": 1,
            "peakpower":  1.0,
            "loss":      14,
            "outputformat": "json",
        }
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        hourly = resp.json()["outputs"]["hourly"]
        cf_values = [h["P"] / 1000.0 for h in hourly][:8760]
        # Defensive: pad if shorter (leap-year edge case)
        if len(cf_values) < 8760:
            cf_values = cf_values + [cf_values[-1]] * (8760 - len(cf_values))
        da = xr.DataArray(
            np.asarray(cf_values, dtype="float32"),
            dims=["hour"],
            coords={"hour": np.arange(8760, dtype="int32")},
            attrs={
                "source": "PVGIS v5.2 TMY (2020)",
                "lat":    lat,
                "lon":    lon,
                "loss_pct": 14,
                "fallback": "false",
            },
        )
        try:
            da.to_netcdf(cache)
            logger.info("PVGIS solar cached to %s (mean CF=%.3f)", cache, float(da.mean()))
        except Exception as exc:  # cache write failure shouldn't block solve
            logger.warning("PVGIS cache write failed for %s: %s", cache, exc)
        return da
    except Exception as exc:
        logger.error(
            "PVGIS fetch failed for (%.2f, %.2f) [%s]: %s — falling back to flat CF=%.2f. "
            "If this appears in a production run, investigate and re-cache.",
            lat, lon, tag, exc, fallback_cf,
        )
        return xr.DataArray(
            np.full(8760, fallback_cf, dtype="float32"),
            dims=["hour"],
            coords={"hour": np.arange(8760, dtype="int32")},
            attrs={
                "source": f"fallback flat CF={fallback_cf} — PVGIS unreachable",
                "lat":    lat,
                "lon":    lon,
                "fallback": "true",
            },
        )


# ────────────────────────────────────────────────────────────────────────────
# ERA5 wind (Copernicus CDS — needs API key; STUB in Phase 2.5)
# ────────────────────────────────────────────────────────────────────────────
def fetch_era5_wind_hourly(
    lat: float,
    lon: float,
    tag: str,
    *,
    hub_height_m: int = 100,
    fallback_cf: float = 0.30,
) -> xr.DataArray:
    """Fetch ERA5 100m wind speed → CF via onshore power curve.

    NOTE: STUBBED in Phase 2.5. The full cdsapi implementation needs:
      * CDS API key from `https://cds.climate.copernicus.eu`
      * Polling (CDS jobs take minutes; needs queue handling)
      * Power-curve conversion (cut-in 3 m/s, rated 12 m/s, cut-out 25 m/s)
      * Hub-height correction from ERA5's 10m or 100m data

    Until then this returns a flat CF with a loud warning. Callers
    (clever.mena_imports) handle the fallback by using country-specific
    flat values anchored to published resource assessments.
    """
    logger.warning(
        "ERA5 wind fetcher is STUBBED (Phase 2.8 second iteration). "
        "Returning flat CF=%.2f for (%.2f, %.2f) [%s].",
        fallback_cf, lat, lon, tag,
    )
    return xr.DataArray(
        np.full(8760, fallback_cf, dtype="float32"),
        dims=["hour"],
        coords={"hour": np.arange(8760, dtype="int32")},
        attrs={
            "source": f"stub flat CF={fallback_cf} — ERA5 fetcher pending CDS setup",
            "lat":    lat,
            "lon":    lon,
            "hub_height_m": hub_height_m,
            "fallback": "true",
        },
    )


# ────────────────────────────────────────────────────────────────────────────
# Renewables.ninja — MERRA-2 reanalysis CF for PV and wind, free with token
# ────────────────────────────────────────────────────────────────────────────
# Cache strategy: once cached to disk, NEVER re-fetch automatically. The
# rate-limited API (50 req/hr free tier) is only hit on the *first* request
# for a given (kind, lat, lon, year). Subsequent runs read from
# CACHE_DIR/ninja_{kind}_{tag}_{year}.nc instantly. Delete the file by hand
# to force a re-fetch (or use a different `tag` / `year` to bypass).
#
# Documentation: https://www.renewables.ninja/documentation/api
# Registration: https://www.renewables.ninja/register (token required)
NINJA_API_BASE: str = "https://www.renewables.ninja/api/data"

# Default reanalysis year. MERRA-2 has a ~1-2 year lag; 2023 is the latest
# fully-validated year as of mid-2025. We try the requested year first and
# fall back to NINJA_FALLBACK_YEAR if the API rejects it (e.g. 2024 not yet
# ingested). Override per call via the `year` arg.
NINJA_DEFAULT_YEAR: int = 2023
NINJA_FALLBACK_YEAR: int = 2019

# Default turbine model — renewables.ninja's exact string. Vestas V112-3000
# @ 100m hub matches NREL ATB 2024 reference for onshore class IIA, used in
# most modern MENA H2 cost studies (IEA, IRENA).
NINJA_DEFAULT_TURBINE: str = "Vestas V112 3000"
NINJA_DEFAULT_HUB_HEIGHT_M: int = 100


def fetch_renewables_ninja_hourly(
    lat: float,
    lon: float,
    tag: str,
    *,
    kind: str,                       # "pv" or "wind"
    year: int | None = None,
    turbine: str | None = None,
    hub_height_m: int | None = None,
    tilt: float | None = None,       # PV only; default = lat
    tracking: int = 0,               # PV only; 0=fixed, 1=1-axis, 2=2-axis
    system_loss: float = 0.10,       # PV only; 10% typical
    fallback_cf: float = 0.25,
) -> xr.DataArray:
    """Fetch renewables.ninja MERRA-2 hourly capacity factor for one point.

    Returns a DataArray of length 8760 with CF in [0, 1] for the year. Cached
    to CACHE_DIR/ninja_{kind}_{tag}_{year}.nc on first success — subsequent
    calls read from disk without API contact.

    Parameters
    ----------
    lat, lon : float
        Site coordinates.
    tag : str
        Cache-filename tag, e.g. "mena_MA_solar" (must be unique per site).
    kind : str
        "pv" for solar PV, "wind" for onshore wind.
    year : int, optional
        MERRA-2 weather year. Defaults to NINJA_DEFAULT_YEAR (2023). Falls
        back to NINJA_FALLBACK_YEAR (2019) if the API rejects the request.
    turbine : str, optional
        Wind only. Exact renewables.ninja name (e.g. "Vestas V112 3000").
    hub_height_m : int, optional
        Wind only. Default 100m.
    tilt : float, optional
        PV only. Defaults to lat (rule-of-thumb optimum for fixed-tilt).
    tracking : int
        PV only. 0=fixed, 1=horizontal axis, 2=dual axis.
    system_loss : float
        PV only. Soiling/inverter/wiring/availability losses. Default 10%.
    fallback_cf : float
        CF used when fetch fails. Default 0.25.

    Requires
    --------
    Environment variable ``RENEWABLES_NINJA_TOKEN`` set to your API token
    (register at https://www.renewables.ninja/register).
    """
    import os
    if kind not in ("pv", "wind"):
        raise ValueError(f"kind must be 'pv' or 'wind', got {kind!r}")

    year = year if year is not None else NINJA_DEFAULT_YEAR
    cache = CACHE_DIR / f"ninja_{kind}_{tag}_{year}.nc"
    if cache.exists():
        try:
            return xr.open_dataarray(cache).load()
        except Exception as exc:
            logger.warning("Ninja cache %s unreadable (%s); refetching", cache, exc)

    token = os.environ.get("RENEWABLES_NINJA_TOKEN", "").strip()
    if not token:
        logger.error(
            "RENEWABLES_NINJA_TOKEN not set — falling back to flat CF=%.2f "
            "for (%.2f, %.2f) [%s, %s]. Register at "
            "https://www.renewables.ninja/register and export the token.",
            fallback_cf, lat, lon, tag, kind,
        )
        return _ninja_fallback_da(lat, lon, tag, kind, fallback_cf, reason="no_token")

    for try_year in (year, NINJA_FALLBACK_YEAR) if year != NINJA_FALLBACK_YEAR else (year,):
        da = _ninja_one_year_try(
            lat=lat, lon=lon, tag=tag, kind=kind, year=try_year, token=token,
            turbine=turbine or NINJA_DEFAULT_TURBINE,
            hub_height_m=hub_height_m or NINJA_DEFAULT_HUB_HEIGHT_M,
            tilt=tilt if tilt is not None else abs(lat),
            tracking=tracking,
            system_loss=system_loss,
        )
        if da is not None:
            # Cache to the REQUESTED year filename even if we fell back, so
            # the next caller of the same (kind, tag, year) reads it back.
            try:
                da.to_netcdf(cache)
                logger.info(
                    "Ninja %s cached to %s (mean CF=%.3f, year=%d effective)",
                    kind, cache, float(da.mean()), try_year,
                )
            except Exception as exc:
                logger.warning("Ninja cache write failed for %s: %s", cache, exc)
            return da

    # All tries failed
    logger.error(
        "Ninja %s fetch failed for (%.2f, %.2f) [%s] across years (%s, %s) — "
        "falling back to flat CF=%.2f.",
        kind, lat, lon, tag, year, NINJA_FALLBACK_YEAR, fallback_cf,
    )
    return _ninja_fallback_da(lat, lon, tag, kind, fallback_cf, reason="api_fail")


def _ninja_one_year_try(
    *,
    lat: float, lon: float, tag: str, kind: str, year: int, token: str,
    turbine: str, hub_height_m: int, tilt: float, tracking: int, system_loss: float,
) -> xr.DataArray | None:
    """One attempt at a single year. Returns None on failure (caller falls back)."""
    import requests
    url = f"{NINJA_API_BASE}/{kind}"
    params: dict[str, str | float | int] = {
        "lat": lat,
        "lon": lon,
        "date_from": f"{year}-01-01",
        "date_to":   f"{year}-12-31",
        "dataset":   "merra2",
        "capacity":  1.0,
        "format":    "json",
        "local_time": "false",
    }
    if kind == "pv":
        params.update({
            "system_loss": system_loss,
            "tracking":    tracking,
            "tilt":        tilt,
            "azim":        180,  # south-facing for N hemisphere; renewables.ninja accepts
        })
    else:  # wind
        params.update({
            "height":  hub_height_m,
            "turbine": turbine,
        })

    headers = {"Authorization": f"Token {token}"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=120)
        if resp.status_code == 429:
            logger.warning(
                "Ninja %s for [%s] rate-limited (429) — will not retry within this run",
                kind, tag,
            )
            return None
        if resp.status_code >= 400:
            logger.warning(
                "Ninja %s for [%s] year %d returned HTTP %d: %s",
                kind, tag, year, resp.status_code, resp.text[:200],
            )
            return None
        payload = resp.json()
        # API returns {"data": {"timestamp": {"electricity": float, ...}}, "metadata": {...}}
        data = payload.get("data", {})
        if not data:
            logger.warning("Ninja %s for [%s] year %d empty response", kind, tag, year)
            return None
        # data is a dict keyed by ISO timestamp. The value field is "electricity".
        records = sorted(data.items())
        cf_values = [float(v.get("electricity", 0.0)) for _, v in records]
        # Trim/pad to exactly 8760 (leap years return 8784)
        if len(cf_values) >= 8760:
            cf_values = cf_values[:8760]
        else:
            cf_values = cf_values + [cf_values[-1]] * (8760 - len(cf_values))
        return xr.DataArray(
            np.asarray(cf_values, dtype="float32"),
            dims=["hour"],
            coords={"hour": np.arange(8760, dtype="int32")},
            attrs={
                "source":      f"renewables.ninja MERRA-2 ({kind}, {year})",
                "lat":         lat,
                "lon":         lon,
                "kind":        kind,
                "year":        year,
                "turbine":     turbine if kind == "wind" else "",
                "hub_height_m": hub_height_m if kind == "wind" else 0,
                "tilt":        tilt if kind == "pv" else 0.0,
                "tracking":    tracking if kind == "pv" else -1,
                "system_loss": system_loss if kind == "pv" else 0.0,
                "fallback":    "false",
            },
        )
    except Exception as exc:
        logger.warning(
            "Ninja %s for [%s] year %d exception: %s", kind, tag, year, exc,
        )
        return None


def _ninja_fallback_da(
    lat: float, lon: float, tag: str, kind: str, fallback_cf: float, reason: str,
) -> xr.DataArray:
    return xr.DataArray(
        np.full(8760, fallback_cf, dtype="float32"),
        dims=["hour"],
        coords={"hour": np.arange(8760, dtype="int32")},
        attrs={
            "source":   f"fallback flat CF={fallback_cf} — ninja {reason}",
            "lat":      lat,
            "lon":      lon,
            "kind":     kind,
            "fallback": "true",
            "reason":   reason,
        },
    )


# ────────────────────────────────────────────────────────────────────────────
# World Bank Pink Sheet — monthly commodity prices, free, no auth required
# ────────────────────────────────────────────────────────────────────────────
# Source: https://www.worldbank.org/en/research/commodity-markets
# The XLSX URL hash rotates between releases, so we scrape the landing page
# to find the current download URL.

# Energy-content conversions
_USD_PER_MMBTU_TO_USD_PER_MWH_TH: float = 1.0 / 0.293071  # 1 MMBtu = 0.293071 MWh_th
_USD_PER_BBL_TO_USD_PER_MWH_TH:   float = 1.0 / 1.70      # 1 bbl crude ≈ 1.70 MWh_th
                                                          # (5.8 MMBtu thermal LHV per bbl)
# Constants on conversion losses + refining margin for fuel oil:
# Brent crude → heating-oil-grade fuel: refining margin typically adds
# 8-15 USD/bbl + losses (~10%). Use a flat +15% multiplicative adder to
# bbl-equivalent thermal price for "fuel oil delivered" valuation.
_FUEL_OIL_REFINING_MULTIPLIER: float = 1.15

# Documented fallbacks — used when the WB fetch fails or no live data is
# available. Anchored to IEA WEO 2024 STEPS scenario, 2050 (USD nominal,
# Table A.1 of WEO 2024).
_FALLBACK_NGAS_EUR_USD_PER_MMBTU: float = 7.0   # IEA WEO 2024 STEPS, EU TTF 2050 ≈ 7 $/MMBtu
_FALLBACK_CRUDE_BRENT_USD_PER_BBL: float = 70.0  # IEA WEO 2024 STEPS, 2050 ≈ 70 $/bbl
# FX assumption (constant, conservative): 1 EUR ≈ 1.08 USD (mid-2024 EUR/USD)
_USD_TO_EUR: float = 1.0 / 1.08


def _wb_pink_sheet_url() -> str | None:
    """Scrape the WB commodity markets landing page for the current
    Pink Sheet monthly XLSX download URL. Returns None on failure."""
    try:
        import requests
        import re
        resp = requests.get(
            "https://www.worldbank.org/en/research/commodity-markets",
            timeout=15,
        )
        resp.raise_for_status()
        # URL pattern: https://thedocs.worldbank.org/en/doc/{HASH}-{ID}/related/CMO-Historical-Data-Monthly.xlsx
        match = re.search(
            r"https://thedocs\.worldbank\.org/en/doc/[a-f0-9-]+/related/CMO-Historical-Data-Monthly\.xlsx",
            resp.text,
        )
        return match.group(0) if match else None
    except Exception as exc:
        logger.warning("WB Pink Sheet URL discovery failed: %s", exc)
        return None


def fetch_world_bank_pink_sheet():
    """Download (and cache) the World Bank monthly commodity prices XLSX.

    Returns the local Path to the cached file. Re-downloads if the cache
    is older than 30 days OR missing. Falls back to the existing cache
    if the network call fails (stale but usable).

    Raises FileNotFoundError only if no cache exists AND the fetch fails.
    """
    from pathlib import Path
    cache = CACHE_DIR / "wb_pink_sheet.xlsx"

    # Cache freshness: 30 days (WB publishes monthly)
    if cache.exists():
        import time as _t
        age_days = (_t.time() - cache.stat().st_mtime) / 86400.0
        if age_days < 30:
            logger.debug("WB Pink Sheet cache %.1f days old — using", age_days)
            return cache

    url = _wb_pink_sheet_url()
    if url is None:
        if cache.exists():
            logger.warning("WB Pink Sheet URL discovery failed; using stale cache (%s)", cache)
            return cache
        raise FileNotFoundError(
            "WB Pink Sheet: URL discovery failed AND no cache available. "
            "Check internet connectivity or hardcode the URL."
        )

    try:
        import requests
        logger.info("WB Pink Sheet: downloading from %s", url)
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        cache.write_bytes(resp.content)
        logger.info("WB Pink Sheet: cached %d bytes to %s", len(resp.content), cache)
        return cache
    except Exception as exc:
        if cache.exists():
            logger.error("WB Pink Sheet download failed (%s); using stale cache", exc)
            return cache
        raise FileNotFoundError(f"WB Pink Sheet fetch failed: {exc}") from exc


def _read_pink_sheet_monthly() -> "pd.DataFrame | None":
    """Read the Pink Sheet monthly-prices sheet into a tidy DataFrame.
    Returns None if the cache is missing or unreadable. Columns include
    CRUDE_BRENT (USD/bbl) + NGAS_EUR (USD/MMBtu)."""
    try:
        import pandas as pd
        cache = fetch_world_bank_pink_sheet()
        # Headers are on row index 6 (0-indexed); units on row 5; titles on row 4
        df = pd.read_excel(cache, sheet_name="Monthly Prices", header=6)
        df = df.rename(columns={df.columns[0]: "month"})
        df = df.dropna(subset=["month"])
        df["month"] = df["month"].astype(str)
        df = df[df["month"].str.match(r"\d{4}M\d{2}")]
        # "…" sentinel → NaN; coerce numeric
        for col in ("CRUDE_BRENT", "NGAS_EUR"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.reset_index(drop=True)
    except Exception as exc:
        logger.error("WB Pink Sheet read failed: %s", exc)
        return None


def fetch_natural_gas_price_eur_per_mwh_th(
    window_months: int = 12,
) -> float:
    """Trailing N-month average of WB Natural Gas (Europe) price, in EUR/MWh_th.

    Falls back to IEA WEO 2024 STEPS-anchored value if the fetcher fails.
    Loudly logs the fallback path.
    """
    df = _read_pink_sheet_monthly()
    if df is None or "NGAS_EUR" not in df.columns:
        fallback = _FALLBACK_NGAS_EUR_USD_PER_MMBTU * _USD_PER_MMBTU_TO_USD_PER_MWH_TH * _USD_TO_EUR
        logger.error(
            "Natural gas price: WB Pink Sheet unavailable — falling back to IEA WEO 2024 "
            "STEPS 2050 anchor (%.1f $/MMBtu → %.1f EUR/MWh_th). "
            "Investigate the fetch failure and re-cache.",
            _FALLBACK_NGAS_EUR_USD_PER_MMBTU, fallback,
        )
        return fallback

    recent = df["NGAS_EUR"].dropna().tail(window_months)
    if recent.empty:
        fallback = _FALLBACK_NGAS_EUR_USD_PER_MMBTU * _USD_PER_MMBTU_TO_USD_PER_MWH_TH * _USD_TO_EUR
        logger.error("Natural gas price: no recent observations — falling back to %.1f EUR/MWh_th", fallback)
        return fallback

    avg_usd_per_mmbtu = float(recent.mean())
    eur_per_mwh_th = avg_usd_per_mmbtu * _USD_PER_MMBTU_TO_USD_PER_MWH_TH * _USD_TO_EUR
    logger.info(
        "Natural gas price: WB Pink Sheet trailing %d-month avg = %.2f $/MMBtu "
        "= %.1f EUR/MWh_th (covering %s to %s)",
        window_months, avg_usd_per_mmbtu, eur_per_mwh_th,
        df.loc[recent.index[0], "month"], df.loc[recent.index[-1], "month"],
    )
    return eur_per_mwh_th


def fetch_brent_crude_price_eur_per_mwh_th(
    window_months: int = 12,
    refining_margin: bool = True,
) -> float:
    """Trailing N-month average of WB Brent crude price, in EUR/MWh_th equivalent.

    When `refining_margin=True` (default) applies a +15% multiplicative uplift
    to model the gap between bbl-equivalent crude thermal price and delivered
    heating-oil-grade fuel cost. Set False for raw-crude calibration.

    Falls back to IEA WEO 2024 STEPS-anchored value if the fetcher fails.
    """
    df = _read_pink_sheet_monthly()
    if df is None or "CRUDE_BRENT" not in df.columns:
        fallback_raw = _FALLBACK_CRUDE_BRENT_USD_PER_BBL * _USD_PER_BBL_TO_USD_PER_MWH_TH * _USD_TO_EUR
        fallback = fallback_raw * (_FUEL_OIL_REFINING_MULTIPLIER if refining_margin else 1.0)
        logger.error(
            "Brent crude price: WB Pink Sheet unavailable — falling back to IEA WEO 2024 "
            "STEPS 2050 anchor (%.0f $/bbl → %.1f EUR/MWh_th%s). "
            "Investigate the fetch failure and re-cache.",
            _FALLBACK_CRUDE_BRENT_USD_PER_BBL, fallback,
            " incl. refining margin" if refining_margin else "",
        )
        return fallback

    recent = df["CRUDE_BRENT"].dropna().tail(window_months)
    if recent.empty:
        fallback_raw = _FALLBACK_CRUDE_BRENT_USD_PER_BBL * _USD_PER_BBL_TO_USD_PER_MWH_TH * _USD_TO_EUR
        fallback = fallback_raw * (_FUEL_OIL_REFINING_MULTIPLIER if refining_margin else 1.0)
        logger.error("Brent crude price: no recent observations — falling back to %.1f EUR/MWh_th", fallback)
        return fallback

    avg_usd_per_bbl = float(recent.mean())
    raw_eur_per_mwh = avg_usd_per_bbl * _USD_PER_BBL_TO_USD_PER_MWH_TH * _USD_TO_EUR
    eur_per_mwh_th = raw_eur_per_mwh * (_FUEL_OIL_REFINING_MULTIPLIER if refining_margin else 1.0)
    logger.info(
        "Brent crude price: WB Pink Sheet trailing %d-month avg = %.1f $/bbl "
        "= %.1f EUR/MWh_th%s (covering %s to %s)",
        window_months, avg_usd_per_bbl, eur_per_mwh_th,
        " incl. +15%% refining" if refining_margin else "",
        df.loc[recent.index[0], "month"], df.loc[recent.index[-1], "month"],
    )
    return eur_per_mwh_th


# ────────────────────────────────────────────────────────────────────────────
# EU Hydrogen Bank LCOH benchmark (static — auction results are PDFs, no JSON)
# ────────────────────────────────────────────────────────────────────────────
def fetch_eu_hydrogen_bank_lcoh_benchmark() -> float:
    """EU Hydrogen Bank auction-clearing-price benchmark (€/MWh_H₂ delivered).

    Round 1 (Mar 2024): subsidy 0.37-0.48 €/kg, implied LCOH ~4-5 €/kg = 120-150 €/MWh.
    Round 2 (Feb 2025): subsidy 0.20-0.60 €/kg, implied LCOH ~3.5-5 €/kg = 105-150 €/MWh.

    These are EU-INTERNAL contracts (cheap-solar + electrolyser onshore).
    MENA delivered will be similar IF transport is correctly priced; see
    handoff §7.2.8(c).
    """
    return 130.0  # central Round-2 benchmark
