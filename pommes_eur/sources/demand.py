"""
demand.py — DemandForge hourly demand builder.

Purpose
-------
Build hourly electricity demand profiles (MW) for each (area, year_op) by:
1) Reading CLEVER annual electricity demand totals from:
   - demand_by_sector_resource.csv (TWh)
2) Reading CLEVER end-use annual split from:
   - clever_end_use_electricity.csv (TWh), with columns: area, year_op, end_use, value
     where end_use in:
       - res_space_heating_elec
       - passenger_mobility_elec
       - res_cooling_total
3) Computing DemandForge targets (MWh):
   baseload = total_elec - heating - cooling - EV
4) Calling DemandForge to generate hourly load curves (MW)
5) Writing:
   - demand_targets.csv
   - hourly_electricity_demand.csv
   - (optional) per-area-year hourly CSVs

Notes
-----
- res_cooling_total is taken from CLEVER "toccfrescli" (cooling total FEC). It may be multi-carrier.
  If you later export an electricity-only cooling indicator, just map it to end_use=res_cooling_elec.

DemandForge
-----------
Installed demandforge==0.2.2 exposes demandforge.load_projection.project_load_curve.
"""

from __future__ import annotations

import argparse
import logging
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from demandforge.load_projection import project_load_curve
import demandforge.load_projection as lp
# Compat shim: refactored DemandForge moved RESULTS_DIR up to the top-level
# `demandforge` package (see demandforge/__init__.py).  The old API exposed
# it as `demandforge.load_projection.RESULTS_DIR`; preserve that access for
# this module so we don't have to touch every call site.
import demandforge as _df_root
if not hasattr(lp, "RESULTS_DIR") and hasattr(_df_root, "RESULTS_DIR"):
    lp.RESULTS_DIR = _df_root.RESULTS_DIR
from pyarrow.lib import ArrowInvalid

from pommes_eur.constants import HOURS_PER_YEAR, TWH_TO_MWH, AREA_TO_TYNDP

# ─────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Constants / conventions
# ─────────────────────────────────────────────────────────────────────
REQUIRED_DEMAND_COLS = {"area", "year_op", "sector", "resource", "value"}
REQUIRED_END_USE_COLS = {"area", "year_op", "end_use", "value"}

END_USE_MAP = {
    "res_space_heating_elec": "heat_twh",
    "passenger_mobility_elec": "ev_twh",
    "res_cooling_total": "cool_twh",
}
END_USE_REQUIRED = set(END_USE_MAP.keys())


# ─────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TargetsMWh:
    """
    Annual energy targets for DemandForge hourly demand builder (in MWh).
    """
    baseload_mwh: float
    winter_mwh: float
    summer_mwh: float
    ev_mwh: float
    total_mwh: float

    def as_demandforge_kwargs(self) -> Dict[str, float]:
        """Convert to keyword arguments for project_load_curve()."""
        return dict(
            total_baseload_energy_target=float(self.baseload_mwh),
            total_winter_thermosensitive_energy_target=float(self.winter_mwh),
            total_summer_thermosensitive_energy_target=float(self.summer_mwh),
            total_ev_energy_target=float(self.ev_mwh),
        )


# ─────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────

def twh_to_mwh(x: float) -> float:
    """Convert TWh to MWh."""
    return float(x) * TWH_TO_MWH


def ensure_cols(df: pd.DataFrame, required: set, name: str) -> None:
    """Raise if DataFrame is missing required columns."""
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"{name} missing columns {missing}. Got: {list(df.columns)}")


def normalize_str_cols(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """Normalize string columns to lowercase with stripped whitespace."""
    out = df.copy()
    for c in cols:
        out[c] = out[c].astype(str).str.strip().str.lower()
    return out


def purge_zero_byte_combined_load(country: str, reference_year: int) -> None:
    """Remove zero-byte DemandForge cache file if it exists."""
    p = Path(lp.RESULTS_DIR) / "combined_load" / f"combined_load_{country}_{reference_year}.parquet"
    if p.exists() and p.is_file() and p.stat().st_size == 0:
        logger.info(f"[DemandForge] Removing 0-byte cache file: {p}")
        p.unlink(missing_ok=True)


def ev_reference_energy_is_zero(country: str, ref_year: int) -> bool:
    """
    Check if EV profile in DemandForge reference parquet has zero annual energy.
    Returns False if parquet doesn't exist (DemandForge will try downloading).
    """
    p = Path(lp.RESULTS_DIR) / "combined_load" / f"combined_load_{country}_{ref_year}.parquet"
    if not p.exists():
        return False
    df = pd.read_parquet(p)
    ev_cols = [c for c in df.columns if "ev" in str(c).lower()]
    if not ev_cols:
        return True
    ev = pd.to_numeric(df[ev_cols[0]], errors="coerce").to_numpy()
    return float(np.nansum(ev)) == 0.0


def list_ev_load_years(country: str, reference_year: int) -> list[int]:
    """
    List all EV profile years available in the combined_load parquet for (country, reference_year).
    Uses module-level cache to avoid repeated parquet reads.
    """
    cache_key = (country, reference_year)
    if cache_key in _EV_YEARS_CACHE:
        return _EV_YEARS_CACHE[cache_key]

    p = Path(lp.RESULTS_DIR) / "combined_load" / f"combined_load_{country}_{reference_year}.parquet"
    if not p.exists() or p.stat().st_size == 0:
        _EV_YEARS_CACHE[cache_key] = []
        return []

    try:
        df = pd.read_parquet(p)
    except Exception:
        _EV_YEARS_CACHE[cache_key] = []
        return []

    years: list[int] = []
    for c in df.columns:
        s = str(c).lower()
        if s.startswith("ev_load_"):
            try:
                years.append(int(str(c).split("_")[-1]))
            except Exception:
                pass

    out = sorted(set(years))
    _EV_YEARS_CACHE[cache_key] = out
    return out


def pick_reference_ev_year(country: str, reference_year: int, requested_ev_year: int) -> int:
    """
    Pick the best available EV reference year from combined_load parquet.
    Prefers requested_ev_year if available, else falls back to max available.
    """
    years = list_ev_load_years(country, reference_year)
    if not years:
        return requested_ev_year
    if requested_ev_year in years:
        return requested_ev_year
    return max(years)


def _ensure_datetime(s: pd.Series) -> pd.Series:
    """Ensure Series is datetime64, coercing if needed."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    return pd.to_datetime(s, errors="coerce")


def _step_h_from_datetime_or_length(dt: pd.Series, n: int) -> float:
    """
    Infer timestep in hours from datetime Series.
    Falls back to HOURS_PER_YEAR / n if datetime is missing/broken.
    """
    if dt is None or n <= 1:
        return 1.0
    dt = _ensure_datetime(dt)
    dt = dt.dropna()
    if len(dt) >= 2:
        diffs = dt.sort_values().diff().dropna()
        if pd.api.types.is_timedelta64_dtype(diffs) and len(diffs) > 0:
            step_h = float(diffs.median() / np.timedelta64(1, "h"))
            if np.isfinite(step_h) and step_h > 0:
                return step_h
    # fallback: assume full-year coverage
    step_h = float(HOURS_PER_YEAR / n)
    return step_h if np.isfinite(step_h) and step_h > 0 else 1.0


def hourly_energy_mwh(df_dt_value: pd.DataFrame) -> float:
    """
    Compute annual energy in MWh from hourly load profile.
    Input: DataFrame with columns ["datetime", "v"] where v is in MW.
    """
    if df_dt_value is None or df_dt_value.empty:
        return 0.0

    df = df_dt_value.copy()
    df["datetime"] = _ensure_datetime(df["datetime"])
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    if len(df) <= 1:
        return 0.0

    step_h = _step_h_from_datetime_or_length(df["datetime"], len(df))
    v = pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy(dtype=float)
    return float(np.nansum(v * step_h))


def coalesce_duplicate_columns(df: pd.DataFrame, protected=("datetime",)) -> pd.DataFrame:
    """
    If df has duplicate column names, coalesce by summing row-wise (numeric).
    Protected columns are passed through unchanged (first occurrence kept).
    """
    if df.columns.is_unique:
        return df

    out = pd.DataFrame(index=df.index)
    seen = set()

    for col in df.columns:
        if col in seen:
            continue
        seen.add(col)

        block = df.loc[:, df.columns == col]  # DataFrame even if single col

        if col in protected:
            out[col] = block.iloc[:, 0]
        else:
            out[col] = block.apply(pd.to_numeric, errors="coerce").sum(axis=1)

    return out


def rebuild_total_from_components(wide: pd.DataFrame) -> pd.DataFrame:
    """Rebuild total column from available component columns."""
    wide = wide.copy()
    comps = [c for c in ["baseload", "winter_thermosensitive", "summer_thermosensitive", "ev"] if c in wide.columns]
    if comps:
        wide["total"] = wide[comps].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    return wide


def normalize_demandforge_output(proj: pd.DataFrame | pd.Series) -> pd.DataFrame:
    """
    Normalize DemandForge project_load_curve output.

    DemandForge typically returns columns like:
      - baseload_load, baseload_load_projected
      - winter_thermosensitive_load, winter_thermosensitive_load_projected
      - summer_thermosensitive_load, summer_thermosensitive_load_projected
      - ev_load, ev_load_projected
      - total_load, total_load_projected

    This function ALWAYS prefers the *_load_projected columns.

    Returns DataFrame with columns:
      datetime, baseload, winter_thermosensitive, summer_thermosensitive, ev, total
    """
    if isinstance(proj, pd.Series):
        df = proj.to_frame(name=proj.name or "total_load_projected")
    else:
        df = proj.copy()

    # ---- datetime
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df["datetime"] = df.index
    elif "datetime" in df.columns:
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    else:
        # try common time columns
        for cand in ("timestamp", "time", "date", "datetime"):
            if cand in (str(c).lower() for c in df.columns):
                col = [c for c in df.columns if str(c).lower() == cand][0]
                df = df.copy()
                df["datetime"] = pd.to_datetime(df[col], errors="coerce")
                break
        else:
            raise ValueError(f"DemandForge output has no datetime index/column. Columns={list(df.columns)}")

    df = df.dropna(subset=["datetime"]).sort_values("datetime")

    # ---- coalesce duplicates early (protect datetime)
    df = coalesce_duplicate_columns(df, protected=("datetime",))

    # ---- helper: pick projected column for a component
    def pick_projected_col(component: str) -> str | None:
        cols = list(df.columns)
        clower = {c: str(c).lower() for c in cols}

        # strongest: exact conventional names
        exact = [
            f"{component}_load_projected",
            f"{component}_projected",
        ]
        for e in exact:
            for c in cols:
                if clower[c] == e:
                    return c

        # next: contains component + 'projected'
        projected = [
            c for c in cols
            if component in clower[c] and "projected" in clower[c]
        ]
        if projected:
            return projected[0]

        return None

    out = pd.DataFrame({"datetime": df["datetime"]})

    mapping = {
        "baseload": "baseload",
        "winter_thermosensitive": "winter_thermosensitive",
        "summer_thermosensitive": "summer_thermosensitive",
        "ev": "ev",
        "total": "total",
    }

    # total: prefer total_load_projected
    total_col = None
    for cand in ("total_load_projected", "total_projected", "total"):
        for c in df.columns:
            if str(c).lower() == cand:
                total_col = c
                break
        if total_col:
            break
    if total_col is None:
        # fallback: any column containing 'total' and 'projected'
        for c in df.columns:
            s = str(c).lower()
            if "total" in s and "projected" in s:
                total_col = c
                break

    # components
    for comp in ("baseload", "winter_thermosensitive", "summer_thermosensitive", "ev"):
        c = pick_projected_col(comp)
        if c is not None:
            out[mapping[comp]] = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)

    # total
    if total_col is not None:
        out["total"] = pd.to_numeric(df[total_col], errors="coerce").to_numpy(dtype=float)
    else:
        # fallback: sum available components
        comps = [c for c in ("baseload", "winter_thermosensitive", "summer_thermosensitive", "ev") if c in out.columns]
        if not comps:
            raise ValueError(f"Cannot identify projected columns. Columns={list(df.columns)}")
        out["total"] = out[comps].sum(axis=1)

    return out


def rescale_profiles_to_target(wide: pd.DataFrame, target_mwh: float) -> tuple[pd.DataFrame, float]:
    """
    Rescale all available component profiles so that TOTAL annual energy equals target_mwh.
    Returns (wide_rescaled, applied_scale_factor).
    """
    wide = wide.copy()
    cols = [c for c in ["baseload", "winter_thermosensitive", "summer_thermosensitive", "ev", "total"] if c in wide.columns]

    current = hourly_energy_mwh(pd.DataFrame({"datetime": wide["datetime"], "v": wide["total"]}))
    if target_mwh <= 0 or current <= 0:
        return wide, 1.0

    scale = target_mwh / current

    for c in cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce") * scale

    # re-enforce total = sum of components if available
    wide = rebuild_total_from_components(wide)
    return wide, float(scale)


def energy_check_hourly(total_series: pd.DataFrame, target_mwh: float, tol_rel: float) -> None:
    """
    Verify that annual energy in hourly profile matches target within tolerance.
    Raises ValueError if mismatch exceeds tol_rel.

    Parameters
    ----------
    total_series : DataFrame with columns ['datetime','total'] where total is MW
    target_mwh : target annual energy in MWh
    tol_rel : relative tolerance (e.g., 0.001 for 0.1%)
    """
    if total_series is None or total_series.empty:
        raise ValueError("energy_check_hourly: empty series")

    df = total_series.copy()
    df["datetime"] = _ensure_datetime(df["datetime"])
    df = df.dropna(subset=["datetime"])
    if df.empty:
        raise ValueError("energy_check_hourly: datetime could not be parsed")

    df = df.sort_values("datetime")
    n = len(df)
    step_h = _step_h_from_datetime_or_length(df["datetime"], n)

    total_mw = pd.to_numeric(df["total"], errors="coerce").to_numpy(dtype=float)
    energy_mwh = float(np.nansum(total_mw * step_h))

    if target_mwh <= 0:
        return

    rel_err = abs(energy_mwh - target_mwh) / target_mwh
    if rel_err > tol_rel:
        raise ValueError(
            f"Annual energy check failed: {energy_mwh:.2f} MWh vs target {target_mwh:.2f} MWh "
            f"(rel_err={rel_err:.3%}, step_h≈{step_h})."
        )


def pick_reference_year(country: str, requested: int) -> int:
    """
    Return requested reference year (do NOT fallback based on local cache).
    Let DemandForge try downloading requested year first.
    Fallback will be handled in _safe_project_load_curve() on 404.
    """
    return int(requested)


# ─────────────────────────────────────────────────────────────────────
# Runtime caches (anti-spam)
# ─────────────────────────────────────────────────────────────────────
_REF_YEAR_CACHE: dict[str, int] = {}
_EV_YEARS_CACHE: dict[tuple[str, int], list[int]] = {}
_COUNTRY_404: set[tuple[str, int]] = set()  # (country, requested_reference_year)
_LOG_ONCE: set[str] = set()


def log_once(key: str, msg: str) -> None:
    """Log a message at most once per session."""
    if key in _LOG_ONCE:
        return
    _LOG_ONCE.add(key)
    logger.warning(msg)


def _safe_project_load_curve(
    *,
    country: str,
    requested_reference_year: int,
    target_year: int,
    requested_reference_ev_year: int,
    total_baseload_energy_target: float,
    total_winter_thermosensitive_energy_target: float,
    total_summer_thermosensitive_energy_target: float,
    total_ev_energy_target: float,
):
    """
    Safe wrapper around project_load_curve with comprehensive error handling.

    Handles:
    - 404 errors (missing remote data) with local fallback
    - NO_EV_PROFILE errors (no EV column in parquet)
    - ArrowInvalid errors (corrupted parquet cache)
    """
    if (country, int(requested_reference_year)) in _COUNTRY_404:
        return None

    ref_year = int(requested_reference_year)
    purge_zero_byte_combined_load(country, ref_year)

    base_kwargs = dict(
        country=country,
        reference_year=int(ref_year),
        target_year=int(target_year),
        total_baseload_energy_target=float(total_baseload_energy_target),
        total_winter_thermosensitive_energy_target=float(total_winter_thermosensitive_energy_target),
        total_summer_thermosensitive_energy_target=float(total_summer_thermosensitive_energy_target),
        total_ev_energy_target=float(total_ev_energy_target),
    )

    call_kwargs = dict(base_kwargs)

    # ---- IMPORTANT: DemandForge requires an ev_load_<reference_ev_year> column even if EV=0
    ev_years = list_ev_load_years(country, ref_year)

    if ev_years:
        if float(total_ev_energy_target) > 0:
            ev_year = pick_reference_ev_year(country, ref_year, int(requested_reference_ev_year))
            if ev_year != int(requested_reference_ev_year):
                log_once(
                    f"evyear:{country}:{ref_year}:{requested_reference_ev_year}",
                    f"[WARN] {country} ref={ref_year}: ev_load_{requested_reference_ev_year} missing -> using ev_load_{ev_year}"
                )
            call_kwargs["reference_ev_year"] = int(ev_year)
        else:
            call_kwargs["reference_ev_year"] = int(ev_years[0])  # pass DF validation
    # else: no ev columns at all; will catch on ValueError

    try:
        return project_load_curve(**call_kwargs)

    except ValueError as e:
        msg = str(e).lower()

        if "ev load column" in msg and "not found" in msg:
            ev_years = list_ev_load_years(country, ref_year)

            if ev_years:
                fallback_ev_year = pick_reference_ev_year(country, ref_year, int(requested_reference_ev_year))
                log_once(
                    f"evfallback:{country}:{ref_year}:{requested_reference_ev_year}",
                    f"[WARN] {country}-{ref_year}: missing ev_load_{requested_reference_ev_year} -> retry with ev_load_{fallback_ev_year}"
                )
                retry = dict(base_kwargs)
                retry["reference_ev_year"] = int(fallback_ev_year)
                return project_load_curve(**retry)

            log_once(
                f"evabsent:{country}:{ref_year}",
                f"[WARN] {country}-{ref_year}: no EV profile in combined_load parquet. Can't run DemandForge EV logic."
            )
            return ("NO_EV_PROFILE", base_kwargs)

        raise

    except urllib.error.HTTPError as e:
        if getattr(e, "code", None) == 404:
            data_dir = Path(lp.RESULTS_DIR) / "combined_load"

            # Fallback: try any local years we have for that country
            candidates = sorted(data_dir.glob(f"combined_load_{country}_*.parquet"))

            years_ok = []
            for p in candidates:
                try:
                    y = int(p.stem.split("_")[-1])
                    if p.stat().st_size > 0:
                        years_ok.append(y)
                except Exception:
                    pass

            if years_ok:
                fallback_year = max(years_ok)
                log_once(
                    f"ref_fallback:{country}:{requested_reference_year}",
                    f"[WARN] {country}-{requested_reference_year}: remote 404 -> fallback to local {fallback_year}"
                )
                call_kwargs["reference_year"] = int(fallback_year)
                purge_zero_byte_combined_load(country, int(fallback_year))
                return project_load_curve(**call_kwargs)

            log_once(
                f"404:{country}:{requested_reference_year}",
                f"[WARN] {country}-{requested_reference_year}: remote 404 and no local fallback -> skip country."
            )
            return None
        raise

    except ArrowInvalid as e:
        raise ArrowInvalid(
            f"{e}\n\nLikely corrupted parquet cache for country={country}, reference_year={ref_year}. "
            f"Delete combined_load_{country}_{ref_year}.parquet under {lp.RESULTS_DIR}/combined_load and retry."
        )


# ─────────────────────────────────────────────────────────────────────
# Input readers
# ─────────────────────────────────────────────────────────────────────

def read_total_electricity_twh(clever_dir: Path) -> pd.DataFrame:
    """
    Read total electricity demand from CLEVER exports.

    Returns DataFrame with columns: area, year_op, total_elec_twh, transport_elec_twh
    """
    demand_csv = clever_dir / "demand_by_sector_resource.csv"
    if not demand_csv.exists():
        raise FileNotFoundError(f"Missing {demand_csv}. Run export_clever_csv.py first.")

    demand = pd.read_csv(demand_csv)
    ensure_cols(demand, REQUIRED_DEMAND_COLS, "demand_by_sector_resource.csv")

    demand["year_op"] = demand["year_op"].astype(int)
    demand = normalize_str_cols(demand, ["sector", "resource"])

    elec = demand[demand["resource"] == "electricity"].copy()

    totals = (
        elec.groupby(["area", "year_op"], as_index=False)["value"]
        .sum()
        .rename(columns={"value": "total_elec_twh"})
    )

    transport = (
        elec[elec["sector"] == "transport"]
        .groupby(["area", "year_op"], as_index=False)["value"]
        .sum()
        .rename(columns={"value": "transport_elec_twh"})
    )

    base = totals.merge(transport, on=["area", "year_op"], how="left")
    base["transport_elec_twh"] = base["transport_elec_twh"].fillna(0.0)
    return base


def read_end_use_twh(end_use_csv: Path) -> pd.DataFrame:
    """
    Read end-use electricity split from CLEVER exports.

    Input: clever_end_use_electricity.csv with columns
      area, year_op, end_use, value (TWh)

    Returns wide DF with columns:
      area, year_op, heat_twh, cool_twh, ev_twh
    """
    eu = pd.read_csv(end_use_csv)
    ensure_cols(eu, REQUIRED_END_USE_COLS, end_use_csv.name)

    eu["year_op"] = eu["year_op"].astype(int)
    eu = normalize_str_cols(eu, ["end_use"])

    # Keep only known end uses (ignore extra rows if any)
    eu = eu[eu["end_use"].isin(END_USE_REQUIRED)].copy()

    wide = (
        eu.pivot_table(index=["area", "year_op"], columns="end_use", values="value", aggfunc="first")
        .reset_index()
    )

    # Rename end_use columns to canonical internal names
    for end_use, target_col in END_USE_MAP.items():
        if end_use not in wide.columns:
            wide[end_use] = np.nan
        wide = wide.rename(columns={end_use: target_col})

    return wide[["area", "year_op", "heat_twh", "cool_twh", "ev_twh"]]


# ─────────────────────────────────────────────────────────────────────
# Targets construction
# ─────────────────────────────────────────────────────────────────────

def compute_targets(
    base_totals: pd.DataFrame,
    end_use_wide: Optional[pd.DataFrame],
    allow_fallback: bool,
) -> pd.DataFrame:
    """
    Compute annual demand targets (MWh) for DemandForge from CLEVER data.

    Parameters
    ----------
    base_totals : DataFrame with area, year_op, total_elec_twh, transport_elec_twh
    end_use_wide : DataFrame with area, year_op, heat_twh, cool_twh, ev_twh (optional)
    allow_fallback : if False, raise on missing end-use values

    Returns DataFrame with columns:
      area, year_op,
      total_elec_twh, heat_twh, cool_twh, ev_twh,
      total_mwh, baseload_mwh, winter_mwh, summer_mwh, ev_mwh
    """
    df = base_totals.copy()

    if end_use_wide is not None:
        df = df.merge(end_use_wide, on=["area", "year_op"], how="left")
    else:
        df["heat_twh"] = np.nan
        df["cool_twh"] = np.nan
        df["ev_twh"] = np.nan

    # Determine where end-use is missing
    missing_mask = ~(np.isfinite(df["heat_twh"]) & np.isfinite(df["cool_twh"]) & np.isfinite(df["ev_twh"]))

    if missing_mask.any():
        if not allow_fallback:
            bad = df.loc[missing_mask, ["area", "year_op"]].astype(str).agg("-".join, axis=1).tolist()
            raise ValueError(
                "Missing end-use values for these (area-year): "
                + ", ".join(bad[:30]) + (" ..." if len(bad) > 30 else "")
            )

        # Fallback policy:
        # - heat=0, cool=0, ev = transport electricity proxy
        df.loc[missing_mask, "heat_twh"] = 0.0
        df.loc[missing_mask, "cool_twh"] = 0.0
        df.loc[missing_mask, "ev_twh"] = df.loc[missing_mask, "transport_elec_twh"].astype(float)

    # Compute targets in MWh
    df["total_mwh"] = df["total_elec_twh"].astype(float).map(twh_to_mwh)
    df["winter_mwh"] = df["heat_twh"].astype(float).map(twh_to_mwh)
    df["summer_mwh"] = df["cool_twh"].astype(float).map(twh_to_mwh)
    df["ev_mwh"] = df["ev_twh"].astype(float).map(twh_to_mwh)
    df["baseload_mwh"] = df["total_mwh"] - df["winter_mwh"] - df["summer_mwh"] - df["ev_mwh"]

    # Guardrail: no negative baseload
    neg = df["baseload_mwh"] < -1e-6
    if neg.any():
        bad = df.loc[neg, ["area", "year_op", "total_mwh", "winter_mwh", "summer_mwh", "ev_mwh", "baseload_mwh"]]
        raise ValueError(
            "Negative baseload detected for some (area,year). Example rows:\n"
            + bad.head(10).to_string(index=False)
        )
    df["baseload_mwh"] = df["baseload_mwh"].clip(lower=0.0)

    out_cols = [
        "area", "year_op",
        "total_elec_twh", "heat_twh", "cool_twh", "ev_twh",
        "total_mwh", "baseload_mwh", "winter_mwh", "summer_mwh", "ev_mwh",
    ]
    return df[out_cols].sort_values(["area", "year_op"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────
# Main hourly profile builder
# ─────────────────────────────────────────────────────────────────────

def build_hourly_profiles(
    targets_df: pd.DataFrame,
    out_dir: Path,
    per_area_files: bool,
    tol_rel: float,
    reference_year: int,
    reference_ev_year: int,
    skip_missing_reference: bool = True,
) -> pd.DataFrame:
    """
    Build hourly electricity demand profiles for each (area, year_op).

    Parameters
    ----------
    targets_df : DataFrame with demand targets (from compute_targets)
    out_dir : output directory
    per_area_files : if True, also write one hourly CSV per (area,year_op)
    tol_rel : relative tolerance for annual energy check
    reference_year : DemandForge reference year
    reference_ev_year : DemandForge EV profile year
    skip_missing_reference : if True, skip on 404 instead of raising

    Returns
    -------
    hourly_df : long-format DataFrame with columns:
      area, year_op, datetime, component, load_mw
    """
    rows = []

    for r in targets_df.itertuples(index=False):
        area = r.area
        year_op = int(r.year_op)
        area_code = str(area).upper()
        country = AREA_TO_TYNDP.get(area_code, area_code)

        targets = TargetsMWh(
            baseload_mwh=float(r.baseload_mwh),
            winter_mwh=float(r.winter_mwh),
            summer_mwh=float(r.summer_mwh),
            ev_mwh=float(r.ev_mwh),
            total_mwh=float(r.total_mwh),
        )

        # ---- EV non-scalable check and reallocation (BEFORE calling DemandForge)
        ref_year = pick_reference_year(country, int(reference_year))
        if targets.ev_mwh > 0:
            ev_years = list_ev_load_years(country, int(ref_year))
            if (not ev_years) or ev_reference_energy_is_zero(country, int(ref_year)):
                log_once(
                    f"evnotscalable:{country}:{ref_year}",
                    f"[WARN] {country}-{ref_year}: EV not scalable. Move EV energy into baseload."
                )
                targets = TargetsMWh(
                    baseload_mwh=targets.baseload_mwh + targets.ev_mwh,
                    winter_mwh=targets.winter_mwh,
                    summer_mwh=targets.summer_mwh,
                    ev_mwh=0.0,
                    total_mwh=targets.total_mwh,
                )

        # ---- Check if ANY EV year exists; let _safe_project_load_curve pick the best one
        if targets.ev_mwh > 0:
            ev_years = list_ev_load_years(country, int(ref_year))
            if not ev_years:
                # No EV columns at all → must reallocate to baseload
                log_once(
                    f"evmissingcol:{country}:{ref_year}:{reference_ev_year}",
                    f"[WARN] {country}-{ref_year}: no ev_load columns at all. Move EV energy into baseload."
                )
                targets = TargetsMWh(
                    baseload_mwh=targets.baseload_mwh + targets.ev_mwh,
                    winter_mwh=targets.winter_mwh,
                    summer_mwh=targets.summer_mwh,
                    ev_mwh=0.0,
                    total_mwh=targets.total_mwh,
                )
            elif int(reference_ev_year) not in ev_years:
                # Exact year missing but alternatives exist → pick_reference_ev_year
                # will handle the fallback inside _safe_project_load_curve
                best_ev = pick_reference_ev_year(country, int(ref_year), int(reference_ev_year))
                log_once(
                    f"evfallbackyear:{country}:{ref_year}:{reference_ev_year}",
                    f"[INFO] {country}-{ref_year}: ev_load_{reference_ev_year} unavailable, "
                    f"using ev_load_{best_ev} instead."
                )

        df_kwargs = targets.as_demandforge_kwargs()

        proj = _safe_project_load_curve(
            country=country,
            requested_reference_year=int(reference_year),
            target_year=int(year_op),
            requested_reference_ev_year=int(reference_ev_year),
            total_baseload_energy_target=float(df_kwargs["total_baseload_energy_target"]),
            total_winter_thermosensitive_energy_target=float(df_kwargs["total_winter_thermosensitive_energy_target"]),
            total_summer_thermosensitive_energy_target=float(df_kwargs["total_summer_thermosensitive_energy_target"]),
            total_ev_energy_target=float(df_kwargs["total_ev_energy_target"]),
        )

        # Handle hard case: DemandForge cannot run because EV profile doesn't exist at all
        if isinstance(proj, tuple) and proj[0] == "NO_EV_PROFILE":
            # Fallback: flat profile matching targets
            n = 8760
            dt = pd.date_range(f"{year_op}-01-01", f"{year_op+1}-01-01", freq="h", inclusive="left", tz="UTC")
            total_mwh = float(targets.total_mwh)
            flat_mw = total_mwh / HOURS_PER_YEAR  # MWh / h = MW

            wide = pd.DataFrame({
                "datetime": dt.tz_convert(None),
                "baseload": flat_mw,
                "winter_thermosensitive": 0.0,
                "summer_thermosensitive": 0.0,
                "ev": 0.0,
            })
            wide = rebuild_total_from_components(wide)

        elif proj is None:
            # 404 and no local fallback — use flat demand profile based on CLEVER annual total.
            # This is a standard adequacy fallback: preserves annual energy but loses
            # hourly shape. Acceptable for countries where DemandForge has no reference
            # data (e.g., GB which is outside the continental ENTSO-E perimeter).
            if targets.total_mwh > 0:
                n = 8760
                dt = pd.date_range(f"{year_op}-01-01", f"{year_op+1}-01-01", freq="h", inclusive="left", tz="UTC")
                flat_mw = float(targets.total_mwh) / HOURS_PER_YEAR
                wide = pd.DataFrame({
                    "datetime": dt[:n].tz_convert(None),
                    "baseload": flat_mw,
                    "winter_thermosensitive": 0.0,
                    "summer_thermosensitive": 0.0,
                    "ev": 0.0,
                })
                wide = rebuild_total_from_components(wide)
                logger.warning(
                    f"{country}-{year_op}: DemandForge 404, using flat demand profile "
                    f"({flat_mw:.0f} MW = {targets.total_mwh/1e6:.1f} TWh/yr). "
                    f"Hourly shape is NOT modelled."
                )
            elif skip_missing_reference:
                logger.warning(f"Skipping {country}-{year_op} (no reference data and zero demand)")
                continue
            else:
                raise ValueError(f"Cannot build profiles for {country}-{year_op}: no reference data")

        else:
            wide = normalize_demandforge_output(proj)
            wide = rebuild_total_from_components(wide)

        # ---- Energy check and potential rescaling
        try:
            energy_check_hourly(wide[["datetime", "total"]], float(targets.total_mwh), tol_rel)
        except ValueError as e:
            # If mismatch is small (e.g., leap year), rescale instead of failing
            current = hourly_energy_mwh(pd.DataFrame({"datetime": wide["datetime"], "v": wide["total"]}))
            rel_err = abs(current - float(targets.total_mwh)) / float(targets.total_mwh) if targets.total_mwh > 0 else 0.0

            if rel_err <= 0.01:  # 1% max (safety threshold)
                wide, scale = rescale_profiles_to_target(wide, float(targets.total_mwh))
                log_once(
                    f"rescale:{country}:{year_op}",
                    f"[WARN] {country}-{year_op}: annual energy mismatch (rel_err={rel_err:.3%}) -> rescaled profiles (x{scale:.6f})."
                )
            else:
                # Large mismatch -> debug and raise
                comps = [c for c in ["baseload", "winter_thermosensitive", "summer_thermosensitive", "ev", "total"] if c in wide.columns]
                logger.error(f"[DEBUG] Energy breakdown for {country}-{year_op}:")
                for c in comps:
                    emwh = hourly_energy_mwh(pd.DataFrame({"datetime": wide["datetime"], "v": wide[c]}))
                    logger.error(f"  - {c}: {emwh:,.2f} MWh")
                logger.error(f"[DEBUG] Target total: {targets.total_mwh:,.2f} MWh")
                raise

        wide["area"] = area
        wide["year_op"] = year_op

        long = wide.melt(
            id_vars=["area", "year_op", "datetime"],
            var_name="component",
            value_name="load_mw",
        ).sort_values(["area", "year_op", "component", "datetime"])

        rows.append(long)

        if per_area_files:
            p = out_dir / f"hourly_electricity_demand_{area}_{year_op}.csv"
            long.to_csv(p, index=False)

    hourly = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["area", "year_op", "datetime", "component", "load_mw"]
    )

    out_path = out_dir / "hourly_electricity_demand.csv"
    hourly.to_csv(out_path, index=False)
    logger.info(f"Hourly demand written: {out_path.resolve()}")
    logger.info(f"Rows: {len(hourly)}")
    return hourly


# ─────────────────────────────────────────────────────────────────────
# Convenience function: build_demand_for_countries
# ─────────────────────────────────────────────────────────────────────

def build_demand_for_countries(
    clever_csv_dir: str | Path,
    output_dir: Optional[str | Path] = None,
    countries: Optional[list[str]] = None,
    model_year: int = 2050,
    reference_year: int = 2021,
    reference_ev_year: int = 2021,
) -> pd.DataFrame:
    """
    Convenience wrapper to read CLEVER CSVs, compute targets, and build hourly profiles.

    Parameters
    ----------
    clever_csv_dir : directory containing CLEVER exported CSVs (demand_by_sector_resource.csv, etc.)
    output_dir : output directory for results. If None, uses clever.DEMAND_DIR
    countries : list of country codes to keep (e.g., ['DE', 'FR']). If None, keep all.
    model_year : operation year for projections (default: 2050)
    reference_year : DemandForge reference year (default: 2021)
    reference_ev_year : DemandForge EV profile year (default: 2021)

    Returns
    -------
    hourly_df : long-format DataFrame with hourly demand profiles
    """
    clever_csv_dir = Path(clever_csv_dir)
    if not clever_csv_dir.exists():
        raise FileNotFoundError(f"clever_csv_dir not found: {clever_csv_dir}")

    if output_dir is None:
        try:
            from pommes_eur import DEMAND_DIR
            output_dir = Path(DEMAND_DIR)
        except ImportError:
            output_dir = clever_csv_dir / "demand_output"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read CLEVER data
    base_totals = read_total_electricity_twh(clever_csv_dir)

    end_use_path = clever_csv_dir / "clever_end_use_electricity.csv"
    end_use_wide = read_end_use_twh(end_use_path) if end_use_path.exists() else None

    # Compute targets
    targets_df = compute_targets(
        base_totals=base_totals,
        end_use_wide=end_use_wide,
        allow_fallback=True,
    )

    # Filter by countries if requested
    if countries:
        countries_upper = [c.upper() for c in countries]
        before = len(targets_df)
        targets_df = targets_df[targets_df["area"].astype(str).str.upper().isin(countries_upper)].copy()
        after = len(targets_df)
        logger.info(f"Filtered to countries {countries_upper}: kept {after} / {before} rows")

    # Filter to model_year only
    targets_df = targets_df[targets_df["year_op"] == model_year].copy()
    logger.info(f"Filtered to year_op={model_year}: {len(targets_df)} rows")

    # Build hourly profiles
    hourly_df = build_hourly_profiles(
        targets_df=targets_df,
        out_dir=output_dir,
        per_area_files=False,
        tol_rel=1e-3,
        reference_year=reference_year,
        reference_ev_year=reference_ev_year,
        skip_missing_reference=True,
    )

    return hourly_df


# ─────────────────────────────────────────────────────────────────────
# CLI / orchestration
# ─────────────────────────────────────────────────────────────────────

def resolve_default_end_use(clever_dir: Path, user_path: Optional[str]) -> Optional[Path]:
    """Resolve end-use CSV path, with fallback to clever_end_use_electricity.csv in clever_dir."""
    if user_path:
        p = Path(user_path)
        if not p.exists():
            raise FileNotFoundError(f"End-use CSV not found: {p}")
        return p

    candidate = clever_dir / "clever_end_use_electricity.csv"
    return candidate if candidate.exists() else None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    ap = argparse.ArgumentParser(
        description="Build hourly electricity demand profiles using DemandForge."
    )
    ap.add_argument("clever_dir", type=str, help="Directory containing CLEVER exported CSVs")
    ap.add_argument("--out", type=str, default="hourly_output", help="Output directory")
    ap.add_argument(
        "--end_use_csv",
        type=str,
        default=None,
        help="Optional: clever_end_use_electricity.csv (auto-detected if omitted)",
    )
    ap.add_argument("--no_fallback", action="store_true", help="Fail if end-use values are missing")
    ap.add_argument("--per_area_files", action="store_true", help="Also write one hourly CSV per (area,year_op)")
    ap.add_argument("--tol_rel", type=float, default=1e-3, help="Relative tolerance for annual energy check")
    ap.add_argument("--reference_year", type=int, default=2021,
                    help="DemandForge reference year (combined_load_COUNTRY_YEAR.parquet)")
    ap.add_argument("--reference_ev_year", type=int, default=2021,
                    help="DemandForge EV profile year (ev_load_<year> inside parquet)")
    ap.add_argument(
        "--exclude_areas",
        type=str,
        default="",
        help="Comma-separated list of area codes to exclude (e.g. 'CY,MT')",
    )
    ap.add_argument(
        "--keep_areas",
        type=str,
        default="",
        help="Comma-separated list of area codes to keep (e.g. 'DE,ES,IT,GB,BE,CH,AT,NL')",
    )
    return ap.parse_args()


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    args = parse_args()

    clever_dir = Path(args.clever_dir)
    if not clever_dir.exists() or not clever_dir.is_dir():
        raise FileNotFoundError(f"clever_dir not found or not a directory: {clever_dir}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read CLEVER data
    base_totals = read_total_electricity_twh(clever_dir)

    end_use_path = resolve_default_end_use(clever_dir, args.end_use_csv)
    end_use_wide = read_end_use_twh(end_use_path) if end_use_path is not None else None

    # Compute targets
    targets_df = compute_targets(
        base_totals=base_totals,
        end_use_wide=end_use_wide,
        allow_fallback=(not args.no_fallback),
    )

    # Apply area filters
    exclude = [a.strip().upper() for a in str(args.exclude_areas).split(",") if a.strip()]
    if exclude:
        before = len(targets_df)
        targets_df = targets_df[~targets_df["area"].astype(str).str.upper().isin(exclude)].copy()
        after = len(targets_df)
        logger.info(f"Excluded areas {exclude}: removed {before - after} rows from targets.")

    keep = [a.strip().upper() for a in str(args.keep_areas).split(",") if a.strip()]
    if keep:
        before = len(targets_df)
        targets_df = targets_df[targets_df["area"].astype(str).str.upper().isin(keep)].copy()
        after = len(targets_df)
        logger.info(f"Kept areas {keep}: removed {before - after} rows from targets.")

    # Write targets
    targets_path = out_dir / "demand_targets.csv"
    targets_df.to_csv(targets_path, index=False)
    logger.info(f"Targets written: {targets_path.resolve()}")
    if end_use_path is None:
        logger.info("Note: no end-use CSV found; fallback policy applied where needed.")
    else:
        logger.info(f"End-use CSV: {end_use_path.resolve()}")

    # Build hourly profiles
    build_hourly_profiles(
        targets_df=targets_df,
        out_dir=out_dir,
        per_area_files=args.per_area_files,
        tol_rel=float(args.tol_rel),
        reference_year=int(args.reference_year),
        reference_ev_year=int(args.reference_ev_year),
    )


if __name__ == "__main__":
    main()
