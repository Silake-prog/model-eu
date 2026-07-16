#!/usr/bin/env python3
"""
clever.model — Core model-building module.

This module builds POMMES EnergyModel instances calibrated on the CLEVER scenario.
It is a refactored version of create_model_from_clever.py, with all constants
imported from clever.constants.

Public API
----------
- create_model_from_clever(...)
- create_multi_country_model_from_clever(...)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import logging
import numpy as np
import pandas as pd
import polars as pl

from pommes_craft import (
    Area,
    ConversionTechnology,
    Demand,
    EconomicHypothesis,
    EnergyModel,
    FlexibleDemand,
    Link,
    LoadShedding,
    Spillage,
    StorageTechnology,
    TimeStepManager,
    TransportTechnology,
)

from supplyforge.utils import _get_input_data_file

# Import all constants from clever.constants
from pommes_eur.constants import (
    AREA_MAP,
    MODEL_TO_SUPPLYFORGE,
    CLEVER_CAPACITY_TO_MODEL,
    CLEVER_NON_ENR_TO_MODEL,
    CLEVER_VRE_SPECS,
    VRE_PROFILE_FALLBACK_BY_COUNTRY,
    DEFAULT_VRE_FLAT_CF,
    DEFAULT_VRE_FLAT_CF_GENERIC,
    SUPPLYFORGE_FALLBACK_YEARS,
    MODELTECH_TO_EOLES,
    EOLES_LIFETIME,
    FUEL_ADDER_2050,
    ROR_HYDRO_VARIABLE_COST,
    RESERVOIR_HYDRO_VARIABLE_COST,
    PUMPED_HYDRO_ROUNDTRIP_EFF,
    HYDRO_ROR_FLAT_CF,
    DEFAULT_HYDRO_ROR_FLAT_CF,
    HYDRO_RESERVOIR_FLAT_CF,
    DEFAULT_HYDRO_RESERVOIR_FLAT_CF,
    HYDRO_PEMMDB,
    SUPPLYFORGE_HYDRO_INPUT_CANDIDATES,
    BESS_SPECS,
    BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY,
    DEFAULT_BESS_POWER_INVESTMENT_MAX_MW,
    MANUAL_INTERCONNECTIONS,
    EXPANDABLE_MODEL_TECHS,
    DEFAULT_EXPANSION_HEADROOM_MW,
    EXPANSION_HEADROOM_BY_COUNTRY,
    VRE_CAPEX_IN_VARIABLE_COST,
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION,
    VRE_EXPANSION_HEADROOM_BY_COUNTRY,
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION,
    VRE_DOWNSIDE_BAND_BY_COUNTRY,
    DEFAULT_LOAD_SHEDDING_COST,
    DEFAULT_WATER_SPILLAGE_MAX,
    VRE_CAPACITY_CREDIT,
    EPS_MW,
    EPS_MWH,
    ASSUMED_FLH,
    DEFAULT_FLH,
)

logger = logging.getLogger(__name__)

# =========================================================
# HELPERS
# =========================================================

def _load_first_available_supplyforge_input(
    country: str,
    reference_year: int,
    input_candidates: list[str],
    max_attempts: int = 3,
) -> tuple[Optional[pl.DataFrame], Optional[str]]:
    """Try loading hydro data: first the requested year, then fallback years."""
    # Try requested year across all candidates
    for input_file in input_candidates:
        df = try_load_supplyforge_parquet(
            country=country,
            reference_year=reference_year,
            input_file=input_file,
            max_attempts=max_attempts,
        )
        if df is not None and not df.is_empty():
            return df, input_file

    # Try fallback years across all candidates
    for fallback_year in SUPPLYFORGE_FALLBACK_YEARS:
        if fallback_year == reference_year:
            continue
        for input_file in input_candidates:
            df = try_load_supplyforge_parquet(
                country=country,
                reference_year=fallback_year,
                input_file=input_file,
                max_attempts=max_attempts,
            )
            if df is not None and not df.is_empty():
                logger.info(
                    "Hydro input %s for %s: year %d unavailable, using fallback year %d.",
                    input_file, country, reference_year, fallback_year,
                )
                return df, input_file

    return None, None


def _find_first_existing_column(df: pl.DataFrame, candidates: list[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _coerce_hourly_dataframe(
    raw_df: pl.DataFrame,
    hours: list[int],
    year_op: int,
    value_col_candidates: list[str],
    *,
    normalize_by_max: bool,
    clip_01: bool = True,
) -> pl.DataFrame:
    value_col = _find_first_existing_column(raw_df, value_col_candidates)
    if value_col is None:
        raise ValueError(
            f"Could not find any hourly value column among {value_col_candidates}. "
            f"Available columns={raw_df.columns}"
        )

    df = raw_df.select(value_col).rename({value_col: "availability"})

    if df.height < len(hours):
        missing = len(hours) - df.height
        fill_value = float(df["availability"][-1]) if df.height > 0 else 0.0
        df = pl.concat(
            [df, pl.DataFrame({"availability": [fill_value] * missing})],
            how="vertical",
        )
    elif df.height > len(hours):
        df = df[: len(hours)]

    values = np.asarray(df["availability"].to_list(), dtype=float)
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)

    if normalize_by_max:
        vmax = float(np.max(values)) if values.size else 0.0
        if vmax <= 0.0:
            values = np.zeros_like(values)
        else:
            values = values / vmax

    if clip_01:
        values = np.clip(values, 0.0, 1.0)

    return pl.DataFrame(
        {
            "hour": hours,
            "year_op": [year_op] * len(hours),
            "availability": values.tolist(),
        }
    )


# NOTE: EPS_MW, EPS_MWH, MANUAL_INTERCONNECTIONS, EXPANDABLE_MODEL_TECHS,
# DEFAULT_EXPANSION_HEADROOM_MW, DEFAULT_LOAD_SHEDDING_COST, DEFAULT_WATER_SPILLAGE_MAX,
# and VRE_CAPACITY_CREDIT are imported from clever.constants


# =========================================================
# READERS
# =========================================================
def read_clever_capacity_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"area", "year_op", "conversion_tech", "value"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns {sorted(missing)}")

    df = df.copy()
    df["area"] = df["area"].astype(str).str.upper().str.strip()
    df["year_op"] = pd.to_numeric(df["year_op"], errors="coerce").astype(int)
    df["conversion_tech"] = df["conversion_tech"].astype(str).str.strip().str.lower()
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)

    df["model_tech"] = df["conversion_tech"].map(CLEVER_CAPACITY_TO_MODEL)
    df = df.dropna(subset=["model_tech"]).copy()
    df["capacity_mw"] = df["value"] * 1e3

    return (
        df.groupby(["area", "year_op", "model_tech"], as_index=False)["capacity_mw"]
        .sum()
        .reset_index(drop=True)
    )


def read_clever_load_factor_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"area", "year_op", "conversion_tech", "load_factor"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns {sorted(missing)}")

    df = df.copy()
    df["area"] = df["area"].astype(str).str.upper().str.strip()
    df["year_op"] = pd.to_numeric(df["year_op"], errors="coerce").astype(int)
    df["conversion_tech"] = df["conversion_tech"].astype(str).str.strip().str.lower()
    df["load_factor"] = pd.to_numeric(df["load_factor"], errors="coerce").fillna(0.0)

    df["model_tech"] = df["conversion_tech"].map(CLEVER_CAPACITY_TO_MODEL)
    df = df.dropna(subset=["model_tech"]).copy()

    return (
        df.groupby(["area", "year_op", "model_tech"], as_index=False)["load_factor"]
        .mean()
        .reset_index(drop=True)
    )


def read_clever_non_enr_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"area", "year_op", "energy_source", "energyproducedTWH"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns {sorted(missing)}")

    df = df.copy()
    df["area"] = df["area"].astype(str).str.upper().str.strip()
    df["year_op"] = pd.to_numeric(df["year_op"], errors="coerce").astype(int)
    df["energy_source"] = df["energy_source"].astype(str).str.strip().str.lower()
    df["energyproducedTWH"] = pd.to_numeric(df["energyproducedTWH"], errors="coerce").fillna(0.0)

    df["model_tech"] = df["energy_source"].map(CLEVER_NON_ENR_TO_MODEL)
    df = df.dropna(subset=["model_tech"]).copy()
    df["max_yearly_production_mwh"] = df["energyproducedTWH"] * 1e6

    return (
        df.groupby(["area", "year_op", "model_tech"], as_index=False)["max_yearly_production_mwh"]
        .sum()
        .reset_index(drop=True)
    )


# =========================================================
# EOLES COSTS
# =========================================================
def read_two_col_cost_csv(path: str | Path) -> dict[str, float]:
    df = pd.read_csv(path, header=None, names=["technology", "value"])
    df["technology"] = df["technology"].astype(str).str.strip()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["technology", "value"])
    return dict(zip(df["technology"], df["value"]))


def load_eoles_cost_assumptions(eoles_dir: Path) -> dict:
    return {
        "fom": read_two_col_cost_csv(eoles_dir / "fOM_2026.csv"),
        "vom": read_two_col_cost_csv(eoles_dir / "vOM_2026.csv"),
        "capex": read_two_col_cost_csv(eoles_dir / "capex_2026.csv"),
        "discount": read_two_col_cost_csv(eoles_dir / "discount_rate_uniform.csv"),
        "storage_capex": read_two_col_cost_csv(eoles_dir / "storage_capex_2026.csv"),
    }


def techno_costs_from_eoles(eoles_tech: str, eoles_costs: dict) -> dict[str, float | int]:
    vom = float(eoles_costs["vom"].get(eoles_tech, 0.0))
    fuel = float(FUEL_ADDER_2050.get(eoles_tech, 0.0))
    return {
        "fixed_cost": float(eoles_costs["fom"].get(eoles_tech, 0.0)) * 1000,    # €/kW/yr → €/MW/yr
        "variable_cost": vom + fuel,                                              # déjà €/MWh
        "invest_cost": float(eoles_costs["capex"].get(eoles_tech, 0.0)) * 1000,  # €/kW → €/MW
        "finance_rate": float(eoles_costs["discount"].get(eoles_tech, 0.04)),
        "life_span": int(EOLES_LIFETIME.get(eoles_tech, 25)),
    }


def storage_costs_from_eoles(eoles_tech: str, eoles_costs: dict) -> dict[str, float | int]:
    return {
        "fixed_cost_power": float(eoles_costs["fom"].get(eoles_tech, 0.0)) * 1000,            # €/kW/yr → €/MW/yr
        "invest_cost_power": float(eoles_costs["capex"].get(eoles_tech, 0.0)) * 1000,          # €/kW → €/MW
        "invest_cost_energy": float(eoles_costs["storage_capex"].get(eoles_tech, 0.0)) * 1000,  # €/kWh → €/MWh
        "finance_rate": float(eoles_costs["discount"].get(eoles_tech, 0.04)),
        "life_span": int(EOLES_LIFETIME.get(eoles_tech, 25)),
    }


# =========================================================
# SMALL HELPERS
# =========================================================
def get_pemmdb_hydro(country_code: str) -> dict | None:
    return HYDRO_PEMMDB.get(normalize_country_code(country_code))

def normalize_country_code(country_code: str) -> str:
    raw = str(country_code).upper().strip()
    return AREA_MAP.get(raw, raw)


def to_supplyforge_code(country_code: str) -> str:
    """Convert model country code to SupplyForge country code (e.g. GB → UK)."""
    norm = normalize_country_code(country_code)
    return MODEL_TO_SUPPLYFORGE.get(norm, norm)


def _candidate_area_codes(country_code: str) -> list[str]:
    raw = str(country_code).upper().strip()
    norm = normalize_country_code(raw)
    return [raw] if raw == norm else [raw, norm]


def is_expandable_dispatchable(model_tech: str) -> bool:
    return model_tech in EXPANDABLE_MODEL_TECHS


# ─── Free-import lockdown helper (2026-05-26, redesigned 2026-05-27) ─────────
# POMMES auto-creates net_import_max_yearly_energy_import variables across
# every (area, resource) pair as soon as ANY NetImport exists in the model.
# Slots without an explicit NetImport default to NaN (unlimited) at price 0
# — an unlimited free supply path that the LP exploits aggressively
# (e.g. 3 TWh of free electricity imports / 900 TWh of free H₂ imports
# in Phase 1.7 baselines, masking all real LP economics).
#
# Two-level fix:
#
#   1. Eliminate the trigger when possible. The raw_biomass supply used to
#      be a NetImport — that single component flipped p.net_import=True for
#      every non-MENA scenario, materialising ~7 M extra LP variables (full
#      area × hour × resource × year_op grid) and ~3.7 M equality constraints.
#      As of 2026-05-27, raw_biomass is supplied by a production-only
#      ConversionTechnology in clever/biomethane.py — same semantics
#      (priced supply at the ENSPRESO gate cost, capped at JRC potential)
#      but does not trigger the net_import module.
#
#   2. Lock the remaining slots when net_import is unavoidable. If the
#      scenario still has a real NetImport (mena_h2_import for Variant A,
#      or natural_gas/oil supply when !_NO_GAS), POMMES will materialise
#      the full grid regardless. We then explicitly add NetImport(max=0,
#      export=0) for every (area, resource) that doesn't have a deliberate
#      supply. Resource trade between EU areas is preserved via
#      TransportTechnology (h2_pipeline + electric_line), which is a
#      separate POMMES component and unaffected.
def _add_eu_resource_locks(area, country_code: str) -> None:
    """Lock external NetImport (max=0) for resources on a CLEVER-EU area.

    Conditional on whether the model actually contains any **real** NetImport.
    POMMES's net_import module is gated by `add_modules['net_import'] = any(
    isinstance(c, NetImport) for c in components)`. When no NetImport exists,
    POMMES skips the module entirely and creates **zero** net_import
    variables — so the lockdown is unnecessary (and would itself re-trigger
    the expansion).

    A "real" NetImport here = a component carrying a deliberate priced supply:
      - mena_h2_import on Variant-A entry-points (ES, IT)
      - natural_gas_supply / oil_supply on every EU area (when !_NO_GAS)

    The raw_biomass supply is no longer a NetImport (2026-05-27 redesign): it
    became a production-only ConversionTechnology in biomethane.py. So if the
    scenario has no MENA Variant A *and* _noGas, there is no NetImport at all
    in the model — `p.net_import=False` → the entire net_import expansion is
    skipped → ~7 M variables and ~3.7 M equality constraints saved.

    When at least one real NetImport remains, POMMES materialises full
    Cartesian-product variables across (area × hour × resource × year_op).
    We then lock every (area, resource) slot that doesn't have a real supply:
      - electricity, reservoir_water:   always locked
      - biomethane, raw_biomass:        locked when biomethane scope active
      - hydrogen:                       locked except on Variant-A entry-points

    Called near the end of _add_country_components, after all other NetImports
    (mena_h2_import, natural_gas, oil) have been added by their respective
    code paths.
    """
    from pommes_craft import NetImport
    from pommes_eur.constants import _BIOMETHANE_SCOPE, _NO_GAS, _MENA_OPTIM_ACTIVE_COUNTRIES

    # Is there any real NetImport that will already flip p.net_import=True?
    # If not, skip locks entirely — adding them here would itself trigger the
    # net_import module expansion we're trying to avoid.
    #
    # After the 2026-05-27 CT redesign of raw_biomass_supply AND
    # mena_h2_import, the EU-side real NetImports are the Phase 3 fossil-methane
    # and oil supplies (added in _add_country_components when not _NO_GAS).
    # BUT MENA Variant B (_menaOptim) adds a natural_gas NetImport on every MENA
    # area (mena_imports.py:909) — present even under _noGas (see model.py:2320)
    # — which flips p.net_import=True and makes POMMES materialise the full
    # Cartesian-product net_import grid across ALL areas, EU included. Without
    # the locks below, every EU (area, electricity|hydrogen) slot becomes a FREE
    # unlimited zero-cost import path: the EU then "meets" its entire demand via
    # phantom imports (measured: 4574 TWh elec + 1388 TWh H2), its adequacy duals
    # collapse to 0, and its VRE dispatches nothing — a phantom optimum
    # (diagnosed 2026-06-20). So the guard must be True whenever ANY real
    # NetImport exists: EU gas/oil (!_NO_GAS) OR MENA Variant B.
    has_real_netimport = (not _NO_GAS) or bool(_MENA_OPTIM_ACTIVE_COUNTRIES)
    if not has_real_netimport:
        return

    # Resources always locked on EU areas.
    locks = ["electricity", "reservoir_water", "hydrogen"]
    if _BIOMETHANE_SCOPE is not None:
        # raw_biomass + biomethane are now CT-supplied (not NetImports) —
        # must be locked so the auto-generated NetImport slots don't become a
        # free supply path. Hydrogen is similarly locked even on MENA Variant
        # A entry-points because mena_h2_import is also now a CT, not a
        # NetImport — the lock just closes the NetImport side; the CT remains
        # the legitimate priced-supply path.
        locks.extend(["biomethane", "raw_biomass"])

    with area.model.context():
        for resource in locks:
            area.add_component(NetImport(
                name=f"{resource}_lock",
                resource=resource,
                import_price=0.0,
                max_yearly_energy_import=0.0,
                max_yearly_energy_export=0.0,
            ))


def _empty_hourly_profile(hours: list[int], year_op: int, value: float = 0.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "hour": hours,
            "year_op": [year_op] * len(hours),
            "availability": [float(value)] * len(hours),
        }
    )


def extract_baseload_profile(
    electricity_demand_csv_path: str,
    country_code: str,
    year_op: int,
) -> Optional[pl.DataFrame]:
    """Extract the DemandForge *baseload* component from the hourly **electricity** demand CSV.

    DemandForge decomposes **electricity** demand into four components:
    ``baseload``, ``winter`` (thermosensitive), ``summer`` (thermosensitive),
    and ``ev``.  The baseload component captures industrial + services +
    residential baseline load — exactly the right proxy for when H₂-consuming
    industries are active.

    .. note::

        This function reads **electricity** demand data and returns the
        **temporal shape only**.  The returned MW values are electricity MW
        — callers must use ``shape_h2_demand_from_baseload()`` to rescale
        the shape to the target annual H₂ demand (in MWh_H₂).

    Parameters
    ----------
    electricity_demand_csv_path : str
        Path to ``hourly_electricity_demand.csv`` (long format with columns
        ``area, year_op, datetime, component, load_mw``).  This is the
        **electricity** demand file from DemandForge — NOT a hydrogen file.
    country_code : str
        Two-letter ISO country code (e.g. "FR", "DE").
    year_op : int
        Operating year to filter.

    Returns
    -------
    pl.DataFrame or None
        Columns: [demand, hour, year_op], 8760 rows.
        None if the baseload component is not found for this country.
    """
    try:
        df = pl.read_csv(electricity_demand_csv_path)
    except Exception as err:
        logger.warning("Could not read electricity demand CSV %s: %s", electricity_demand_csv_path, err)
        return None

    # Normalise column names
    df = df.with_columns([
        pl.col("area").cast(pl.Utf8).str.to_uppercase().str.strip_chars().alias("area"),
        pl.col("component").cast(pl.Utf8).str.to_lowercase().str.strip_chars().alias("component"),
    ])

    cc = normalize_country_code(country_code)

    # Filter to baseload component for this country and year
    baseload = df.filter(
        (pl.col("area") == cc)
        & (pl.col("year_op") == year_op)
        & (pl.col("component") == "baseload")
    ).sort("datetime")

    if baseload.is_empty() or len(baseload) < 8760:
        logger.warning(
            "No baseload profile for %s-%d (got %d rows). "
            "H₂ demand will use flat fallback.",
            cc, year_op, len(baseload),
        )
        return None

    # Trim to exactly 8760 hours (leap year guard)
    baseload = baseload.head(8760)

    return pl.DataFrame({
        "demand": baseload["load_mw"].to_list(),
        "hour": list(range(8760)),
        "year_op": [year_op] * 8760,
    })


def shape_h2_demand_from_baseload(
    baseload_profile_pl: pl.DataFrame,
    annual_h2_demand_mwh: float,
    year_op: int,
) -> pl.DataFrame:
    """Scale the DemandForge baseload **electricity** shape to match annual H₂ demand.

    The baseload component of **electricity** demand is the best available proxy
    for industrial H₂ demand timing: it excludes thermosensitive heating/
    cooling and EV charging, retaining only the industrial + services +
    residential baseline that tracks when H₂-consuming plants are running.

    The function uses the electricity baseload as a **shape only** — it
    normalises the profile and rescales so that
    ``sum(h₂_hourly) == annual_h2_demand_mwh``.  The output is in **MWh_H₂**,
    not electricity MW.

    Parameters
    ----------
    baseload_profile_pl : pl.DataFrame
        DemandForge baseload **electricity** profile with columns ``demand``,
        ``hour``, ``year_op``.  Typically from ``extract_baseload_profile()``.
        The ``demand`` column is in electricity MW but is used only for its
        temporal shape — absolute values are discarded during normalisation.
    annual_h2_demand_mwh : float
        Total annual H₂ demand in MWh_H₂ (from DemandForge industrial
        demand, NOT from the electricity demand CSV).
    year_op : int
        Operating year.

    Returns
    -------
    pl.DataFrame
        Columns: [demand, hour, year_op] with ``demand`` in **MW_H₂** — ready
        for ``pommes_craft.Demand(resource="hydrogen", demand=...)``.
    """
    vals = baseload_profile_pl["demand"].to_list()
    n = len(vals)

    total = sum(vals)
    if total <= 0.0:
        # Flat fallback — baseload was all zeros
        h2_hourly = [annual_h2_demand_mwh / n] * n
    else:
        # Normalise shape, then scale to annual H₂ demand
        h2_hourly = [annual_h2_demand_mwh * (v / total) for v in vals]

    return pl.DataFrame({
        "demand": h2_hourly,
        "hour": list(range(n)),
        "year_op": [year_op] * n,
    })


def shape_h2_demand_flat(
    annual_h2_mwh: float,
    year_op: int,
    n_hours: int = 8760,
) -> pl.DataFrame:
    """Flat H₂ demand profile (legacy fallback).

    Only used when no baseload electricity profile is available.
    """
    hourly = annual_h2_mwh / n_hours
    return pl.DataFrame({
        "demand": [hourly] * n_hours,
        "hour": list(range(n_hours)),
        "year_op": [year_op] * n_hours,
    })


def _extract_available_plant_types(capacity_factors: Optional[pl.DataFrame]) -> list[str]:
    if capacity_factors is None or capacity_factors.is_empty():
        return []
    if "plant_type" not in capacity_factors.columns:
        return []
    return sorted(map(str, capacity_factors["plant_type"].unique().to_list()))


def _select_capacity_factor_slice(
    capacity_factors: Optional[pl.DataFrame],
    candidates: list[str],
) -> pl.DataFrame:
    if capacity_factors is None or capacity_factors.is_empty():
        return pl.DataFrame()
    if "plant_type" not in capacity_factors.columns:
        return pl.DataFrame()

    available = set(map(str, capacity_factors["plant_type"].unique().to_list()))
    for candidate in candidates:
        if candidate in available:
            return capacity_factors.filter(pl.col("plant_type") == candidate)

    return pl.DataFrame()


def _load_capacity_factor_slice_with_fallback(
    country_code: str,
    reference_year_weather: int,
    clever_tech: str,
    profile_candidates: list[str],
) -> tuple[pl.DataFrame, Optional[str], Optional[str]]:
    """Try loading VRE capacity factors with country and year fallbacks.

    Strategy:
    1. Try primary country with requested year
    2. Try primary country with fallback years (2021, 2022, 2023)
    3. Try neighbouring countries (from VRE_PROFILE_FALLBACK_BY_COUNTRY) with requested year
    4. Try neighbouring countries with fallback years
    """
    search_countries = [country_code]
    fallback_cfg = VRE_PROFILE_FALLBACK_BY_COUNTRY.get(country_code, {})
    search_countries.extend(fallback_cfg.get(clever_tech, []))

    # Build ordered list of (country, year) pairs to try
    years_to_try = [reference_year_weather] + [
        y for y in SUPPLYFORGE_FALLBACK_YEARS if y != reference_year_weather
    ]

    for candidate_country in search_countries:
        sf_candidate = to_supplyforge_code(candidate_country)
        for candidate_year in years_to_try:
            capacity_factors = try_load_supplyforge_parquet(
                sf_candidate,
                candidate_year,
                "capacity_factors",
                max_attempts=3,
            )

            cf_slice = _select_capacity_factor_slice(capacity_factors, profile_candidates)
            if cf_slice.is_empty():
                continue

            matched_plant_type = None
            if "plant_type" in cf_slice.columns and cf_slice.height > 0:
                matched_plant_type = str(cf_slice["plant_type"][0])

            if candidate_country != country_code or candidate_year != reference_year_weather:
                logger.info(
                    "VRE profile for %s/%s: using %s year %d (requested: %s year %d).",
                    country_code, clever_tech, candidate_country, candidate_year,
                    country_code, reference_year_weather,
                )

            return cf_slice, candidate_country, matched_plant_type

    return pl.DataFrame(), None, None


def purge_bad_supplyforge_parquet(country: str, year: int, input_file: str) -> None:
    from supplyforge import RESULTS_DIR

    file_name = f"{input_file}_{country}_{year}.parquet"
    file_path = RESULTS_DIR / input_file / file_name

    if not file_path.exists():
        return

    try:
        if file_path.stat().st_size < 32:
            logger.warning("Removing suspiciously small parquet: %s", file_path)
            file_path.unlink(missing_ok=True)
            return
        _ = pl.read_parquet(file_path)
    except Exception:
        logger.warning("Removing corrupted parquet: %s", file_path)
        file_path.unlink(missing_ok=True)


def load_supplyforge_parquet_with_retry(
    country: str,
    reference_year: int,
    input_file: str,
    max_attempts: int = 3,
) -> pl.DataFrame:
    from supplyforge import RESULTS_DIR

    file_name = f"{input_file}_{country}_{reference_year}.parquet"
    file_path = RESULTS_DIR / input_file / file_name
    last_err: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        purge_bad_supplyforge_parquet(country, reference_year, input_file)
        try:
            df = _get_input_data_file(country, reference_year, input_file)
            if df is None:
                raise ValueError(
                    f"_get_input_data_file returned None for {input_file} {country} {reference_year}"
                )
            if not isinstance(df, pl.DataFrame):
                raise TypeError(
                    f"_get_input_data_file returned {type(df)} instead of polars.DataFrame"
                )

            if file_path.exists():
                _ = pl.read_parquet(file_path)

            return df

        except Exception as err:
            last_err = err
            logger.warning(
                "Failed reading %s for %s-%s on attempt %s/%s: %s",
                input_file,
                country,
                reference_year,
                attempt,
                max_attempts,
                err,
            )
            try:
                if file_path.exists():
                    file_path.unlink(missing_ok=True)
            except Exception as unlink_err:
                logger.warning("Could not remove corrupted parquet %s: %s", file_path, unlink_err)

    raise RuntimeError(
        f"Unable to load valid parquet for input_file={input_file}, country={country}, "
        f"reference_year={reference_year} after {max_attempts} attempts. Last error: {last_err}"
    )

def _normalize_monthly_profile(values: list[float]) -> list[float]:
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    if mean <= 0:
        raise ValueError("monthly profile mean must be > 0")
    return (arr / mean).tolist()

def try_load_supplyforge_parquet(
    country: str,
    reference_year: int,
    input_file: str,
    max_attempts: int = 3,
) -> Optional[pl.DataFrame]:
    try:
        return load_supplyforge_parquet_with_retry(
            country=country,
            reference_year=reference_year,
            input_file=input_file,
            max_attempts=max_attempts,
        )
    except Exception as err:
        logger.warning(
            "Could not load SupplyForge input %s for %s-%s: %s",
            input_file,
            country,
            reference_year,
            err,
        )
        return None


def try_load_supplyforge_with_year_fallback(
    country: str,
    reference_year: int,
    input_file: str,
    max_attempts: int = 3,
) -> tuple[Optional[pl.DataFrame], int]:
    """Try loading SupplyForge data for the requested year, then fallback years.

    Returns (DataFrame_or_None, year_used). If no year works, returns (None, reference_year).
    """
    # Try requested year first
    df = try_load_supplyforge_parquet(country, reference_year, input_file, max_attempts)
    if df is not None and not df.is_empty():
        return df, reference_year

    # Try fallback years
    for fallback_year in SUPPLYFORGE_FALLBACK_YEARS:
        if fallback_year == reference_year:
            continue
        df = try_load_supplyforge_parquet(country, fallback_year, input_file, max_attempts)
        if df is not None and not df.is_empty():
            logger.info(
                "SupplyForge %s for %s: year %d unavailable, using fallback year %d.",
                input_file, country, reference_year, fallback_year,
            )
            return df, fallback_year

    return None, reference_year

def _build_flat_hourly_availability(hours: list[int], year_op: int, value: float = 1.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "hour": hours,
            "year_op": [year_op] * len(hours),
            "availability": [float(value)] * len(hours),
        }
    )


def _build_simple_hydro_seasonal_profile(
    hours: list[int],
    year_op: int,
    monthly_values: list[float],
) -> pl.DataFrame:
    if len(monthly_values) != 12:
        raise ValueError("monthly_values must have length 12")

    dt_index = pd.date_range(f"{year_op}-01-01 00:00:00", periods=len(hours), freq="h")
    values = [float(monthly_values[m - 1]) for m in dt_index.month]

    return pl.DataFrame(
        {
            "hour": hours,
            "year_op": [year_op] * len(hours),
            "availability": values,
        }
    )

def get_clever_capacity_mw(
    capacity_df: pd.DataFrame,
    area: str,
    year_op: int,
    model_tech: str,
) -> Optional[float]:
    for candidate in _candidate_area_codes(area):
        sub = capacity_df[
            (capacity_df["area"] == candidate)
            & (capacity_df["year_op"] == year_op)
            & (capacity_df["model_tech"] == model_tech)
        ]
        if not sub.empty:
            return float(sub["capacity_mw"].iloc[0])
    return None


def get_clever_load_factor(
    load_factor_df: pd.DataFrame,
    area: str,
    year_op: int,
    model_tech: str,
) -> Optional[float]:
    for candidate in _candidate_area_codes(area):
        sub = load_factor_df[
            (load_factor_df["area"] == candidate)
            & (load_factor_df["year_op"] == year_op)
            & (load_factor_df["model_tech"] == model_tech)
        ]
        if not sub.empty:
            return float(sub["load_factor"].iloc[0])
    return None


# =========================================================
# PROFILE SCALING
# =========================================================
def scale_vre_profile_to_target_lf(
    availability_df: pl.DataFrame,
    target_load_factor: Optional[float],
    hours: list[int],
    year_op: int,
) -> pl.DataFrame:
    if availability_df is None or availability_df.is_empty():
        return _empty_hourly_profile(hours, year_op, value=0.0)

    df = availability_df.clone()

    if "capacity_factor" in df.columns and "availability" not in df.columns:
        df = df.rename({"capacity_factor": "availability"})

    if "availability" not in df.columns:
        raise ValueError("availability_df must contain 'availability' or 'capacity_factor'")

    df = df.select("availability")

    if df.height < len(hours):
        missing = len(hours) - df.height
        fill_value = float(df["availability"][-1]) if df.height > 0 else 0.0
        df = pl.concat(
            [df, pl.DataFrame({"availability": [fill_value] * missing})],
            how="vertical",
        )
    elif df.height > len(hours):
        df = df[: len(hours)]

    raw = np.asarray(df["availability"].to_list(), dtype=float)
    raw = np.clip(np.nan_to_num(raw, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)

    raw_lf = float(np.mean(raw)) if raw.size else 0.0

    if target_load_factor is None or not np.isfinite(target_load_factor):
        scaled = raw
    elif target_load_factor <= 0.0:
        scaled = np.zeros_like(raw)
    elif raw_lf <= 0.0:
        scaled = np.zeros_like(raw)
    else:
        factor = target_load_factor / raw_lf
        scaled = np.clip(raw * factor, 0.0, 1.0)

        scaled_lf = float(np.mean(scaled))
        if scaled_lf > 0.0:
            gap = abs(scaled_lf - target_load_factor) / max(target_load_factor, 1e-9)
            if gap > 0.02:
                scaled = np.clip(scaled * (target_load_factor / scaled_lf), 0.0, 1.0)

    return pl.DataFrame(
        {
            "hour": hours,
            "year_op": [year_op] * len(hours),
            "availability": scaled.tolist(),
        }
    )


# =========================================================
# TECHNOLOGY BUILDERS
# =========================================================
def add_dispatchable_from_non_enr(
    area: Area,
    clever_non_enr_df: pd.DataFrame,
    country_code: str,
    model_year: int,
    eoles_costs: dict[str, dict[str, float]],
) -> None:
    sub = pd.DataFrame()
    for candidate in _candidate_area_codes(country_code):
        tmp = clever_non_enr_df[
            (clever_non_enr_df["area"] == candidate)
            & (clever_non_enr_df["year_op"] == model_year)
        ].copy()
        if not tmp.empty:
            sub = tmp
            break

    if sub.empty:
        logger.info("No non-ENR CLEVER data found for %s-%s.", country_code, model_year)
        # NB: do not return here — fall through to the Nuclear-injection block
        # below so _nuke scenarios still get Nuclear in countries that have
        # no CLEVER non-ENR data declared. The for loop below is a no-op on
        # an empty `sub`.

    for _, row in sub.iterrows():
        model_tech = str(row["model_tech"]).strip()
        max_yearly_production_mwh = float(row["max_yearly_production_mwh"])

        eoles_tech = MODELTECH_TO_EOLES.get(model_tech)
        if eoles_tech is None:
            logger.info(
                "Skipping dispatchable %s because no EOLES mapping is defined.",
                model_tech,
            )
            continue

        costs = techno_costs_from_eoles(eoles_tech, eoles_costs)

        flh = ASSUMED_FLH.get(eoles_tech, DEFAULT_FLH)
        existing_capacity_mw = max(max_yearly_production_mwh / flh, 0.0)
        if existing_capacity_mw <= EPS_MW:
            existing_capacity_mw = 0.0

        # ── Optional: lift CLEVER's politically-retained Gas capacity floor ──
        # When the scenario carries the `_nofloor` suffix, override the existing
        # Gas capacity to zero, letting the LP fully decommission instead of
        # being floored at CLEVER's residual ~22.8 GW EU-wide (14 countries).
        # Used to quantify the cost premium of political retention; see
        # methodology section. Applies ONLY to Gas — Nuclear / H2-PP / Waste
        # / etc. keep their CLEVER floors so cross-tech comparisons remain
        # internally consistent.
        if model_tech == "Gas":
            from pommes_eur.constants import _GAS_FLOOR_LIFTED
            if _GAS_FLOOR_LIFTED and existing_capacity_mw > 0:
                logger.info(
                    "Lifting Gas capacity floor for %s-%s (was %.1f MW): _nofloor scenario",
                    country_code, model_year, existing_capacity_mw,
                )
                existing_capacity_mw = 0.0

        # ── Ban fossil methane combustion entirely (_noGas scenarios) ──
        # When CLEVER_SCENARIO carries `_noGas`, skip the Gas (CCGT) and Oil
        # (OCGT) techs in every country — no pinned floor AND no expansion.
        # The LP has Nuclear / Hydro / Biomethane CCGT / H2-CCGT / batteries /
        # load shedding as the only firm options. See methodology section.
        if model_tech in ("Gas", "Oil"):
            from pommes_eur.constants import _NO_GAS
            if _NO_GAS:
                logger.info(
                    "Banning %s for %s-%s: _noGas scenario (no fossil methane)",
                    model_tech, country_code, model_year,
                )
                continue

        expandable = is_expandable_dispatchable(model_tech)

        if expandable:
            # Flex-CT override: post-v11, Gas/Oil are fuel-flex
            # CombinedTechnology with bio_mode + ng_mode. When biomethane is
            # active in the EnergyModel, bio_mode is sufficiency-aligned
            # (CLEVER's "zero Gas in country X" assumption was about FOSSIL
            # gas; biomethane-fired CCGT is consistent with sufficiency).
            # ng_mode dispatch is naturally suppressed by the CO2 tax on the
            # natural_gas import bus, so over-permitting is self-correcting.
            #
            # Countries WITHOUT an explicit Gas/Oil entry in
            # EXPANSION_HEADROOM_BY_COUNTRY do NOT get force-built flex Gas
            # (default = 0). This respects policy decisions encoded elsewhere
            # (e.g. SE, NO, FI's nuclear+hydro+VRE plans intentionally
            # exclude CCGT expansion). Only countries with an explicit
            # policy headroom (DE, FR, ES, IT, NL, BE, AT, CH, PT, IE, DK,
            # LU, GR, GB) are eligible for the flex-override path.
            _resources = getattr(area.model, "resources", [])
            _biomethane_active = "biomethane" in _resources
            _flex_override = (
                model_tech in ("Gas", "Oil")
                and _biomethane_active
                and max_yearly_production_mwh <= EPS_MWH
            )

            # CRITICAL: if CLEVER declares zero energy for this tech in this
            # country, do NOT create it.  The CLEVER sufficiency scenario
            # explicitly excludes this fuel; let LOLE/ENS reveal feasibility.
            # EXCEPT: flex Gas/Oil where biomethane unlocks bio_mode.
            if max_yearly_production_mwh <= EPS_MWH and not _flex_override:
                logger.info(
                    "Skipping expandable %s for %s-%s because CLEVER declares "
                    "zero energy (%.6f TWh). Respecting scenario boundary.",
                    model_tech, country_code, model_year,
                    max_yearly_production_mwh / 1e6,
                )
                continue

            # Use country-specific headroom if available, else default
            area_norm = normalize_country_code(country_code)
            country_headroom = EXPANSION_HEADROOM_BY_COUNTRY.get(area_norm, {})
            if _flex_override:
                # Flex-override path: explicit policy headroom only (default 0).
                # Countries with no explicit entry get no flex Gas force-built.
                invest_max_mw = country_headroom.get(model_tech, 0.0)
                if invest_max_mw <= EPS_MW:
                    logger.info(
                        "Skipping flex-override %s for %s-%s: no explicit "
                        "EXPANSION_HEADROOM_BY_COUNTRY entry (default 0). "
                        "No flex CT built; country policy excludes this tech.",
                        model_tech, country_code, model_year,
                    )
                    continue
                logger.info(
                    "Flex-override %s for %s-%s: CLEVER declares 0 but "
                    "EXPANSION_HEADROOM[%s][%s] = %.0f MW. Building flex CT "
                    "(bio_mode is sufficiency-aligned).",
                    model_tech, country_code, model_year,
                    area_norm, model_tech, invest_max_mw,
                )
            else:
                invest_max_mw = country_headroom.get(
                    model_tech,
                    DEFAULT_EXPANSION_HEADROOM_MW.get(model_tech, 10_000.0),
                )
                if invest_max_mw <= EPS_MW:
                    invest_max_mw = 0.0
            total_capacity_max_mw = existing_capacity_mw + invest_max_mw
            max_yearly_production = None
        else:
            if max_yearly_production_mwh <= EPS_MWH:
                logger.info(
                    "Skipping non-expandable dispatchable %s for %s-%s "
                    "because yearly production is ~zero (%.6f MWh).",
                    model_tech,
                    country_code,
                    model_year,
                    max_yearly_production_mwh,
                )
                continue

            invest_max_mw = 0.0
            total_capacity_max_mw = existing_capacity_mw
            max_yearly_production = max_yearly_production_mwh

        if (
            existing_capacity_mw <= EPS_MW
            and invest_max_mw <= EPS_MW
            and (max_yearly_production is None or max_yearly_production <= EPS_MWH)
        ):
            logger.info(
                "Skipping dispatchable %s for %s-%s because it is structurally null "
                "(existing=%.6f MW, invest_max=%.6f MW, yearly_cap=%s).",
                model_tech,
                country_code,
                model_year,
                existing_capacity_mw,
                invest_max_mw,
                "None" if max_yearly_production is None else f"{max_yearly_production:.6f}",
            )
            continue

        logger.info(
            "Adding dispatchable %s for %s-%s | existing=%.6f MW | expandable=%s | "
            "invest_max=%.6f MW | total_max=%.6f MW | yearly_cap=%s",
            model_tech,
            country_code,
            model_year,
            existing_capacity_mw,
            expandable,
            invest_max_mw,
            total_capacity_max_mw,
            "None" if max_yearly_production is None else f"{max_yearly_production:.6f}",
        )

        # Phase 3 / 2026-05-28: fossil-methane techs draw from the
        # "natural_gas" bus, with FUEL FLEXIBILITY if biomethane is also a
        # resource. Both Gas (CCGT, eff 0.58) and Oil (OCGT, eff 0.38 —
        # methane-fired peaker per EOLES mapping) physically combust either
        # fossil natural_gas or upgraded biomethane interchangeably (CH4
        # is CH4). So when biomethane is in the resource list, we build the
        # tech as a `CombinedTechnology` with `ng_mode` and `bio_mode` — one
        # shared CAPEX-bearing capacity, two LP-decided fuel streams that
        # share the capacity hourly. When biomethane isn't available, we
        # keep the original single-fuel ConversionTechnology shape.
        #
        # CO₂ accounting: full combustion CO₂ already in natural_gas
        # import_price; bio_mode pays nothing for CO₂ (biogenic). No tech-
        # side adjustment needed for non-CCS CCGT/OCGT.
        from pommes_eur.constants import _CCGT_EFF, _OCGT_EFF
        eff = _CCGT_EFF if model_tech == "Gas" else _OCGT_EFF if model_tech == "Oil" else None

        # Defensive: §8.1 probes use a minimal mock Area without a real
        # EnergyModel — `getattr` falls back to an empty list in that case,
        # routing through the original single-fuel ConversionTechnology path
        # (probe asserts that path's kwargs).
        _model_resources = getattr(area.model, "resources", [])
        biomethane_active = "biomethane" in _model_resources

        if model_tech in ("Gas", "Oil") and biomethane_active:
            # Fuel-flex CombinedTechnology
            from pommes_craft import CombinedTechnology  # type: ignore
            factor = {
                "ng_mode":  {"electricity": 1.0, "natural_gas": -1.0 / eff},
                "bio_mode": {"electricity": 1.0, "biomethane":  -1.0 / eff},
            }
            # Same VOM either way (the molecule reformed is identical);
            # fuel-cost differentiation flows through the bus prices.
            var_cost = {
                "ng_mode":  costs["variable_cost"],
                "bio_mode": costs["variable_cost"],
            }
            # Cap LIFTING (2026-05-28): the CLEVER baseline's
            # `total_capacity_max_mw` is a POLITICAL fossil-permitting
            # ceiling (e.g. FR Gas = 10 GW expansion). It's inappropriate to
            # apply that same ceiling to the bio_mode of a fuel-flex plant —
            # bio-CCGT permitting has its own ENSPRESO-bounded headroom
            # (~14-15 GW for FR bioHigh). Squeezing both modes through the
            # 10 GW political cap loses ~14 GW of bio-CCGT peaking and
            # creates artificial VoLL (v10c result, 29 hours-at-VoLL). For
            # the flex variant we lift the cap to a generous 50 GW: the LP
            # picks the optimal capacity given fuel costs and CO₂ pricing.
            # The fossil-permit ceiling is effectively enforced economically
            # via the CO₂ tax on natural_gas at the bus.
            FLEX_GAS_INV_MAX_MW = 50_000.0
            inv_max = max(total_capacity_max_mw, FLEX_GAS_INV_MAX_MW)
            with area.model.context():
                area.add_component(CombinedTechnology(
                    name=model_tech,
                    factor=factor,
                    variable_cost=var_cost,
                    fixed_cost=costs["fixed_cost"],
                    invest_cost=costs["invest_cost"],
                    finance_rate=costs["finance_rate"],
                    life_span=costs["life_span"],
                    # NB: CombinedTechnology only has investment_min/max,
                    # no operational power_capacity_min/max. CLEVER's
                    # existing-capacity floor lives in investment_min.
                    power_capacity_investment_min=existing_capacity_mw,
                    power_capacity_investment_max=inv_max,
                    early_decommissioning=True,
                ))
            logger.info(
                "Added %s (CombinedTechnology fuel-flex, eff=%.0f%%) for %s: "
                "inv_min=%.1f MW, inv_max=%.1f MW (lifted from political "
                "cap %.1f MW), modes=[ng_mode, bio_mode]",
                model_tech, eff * 100, country_code,
                existing_capacity_mw, inv_max, total_capacity_max_mw,
            )
        else:
            # Single-fuel ConversionTechnology (original shape, used in
            # _noGas or _bioOff scenarios where one fuel is absent)
            factor = {"electricity": 1.0}
            if model_tech == "Gas":
                factor["natural_gas"] = -1.0 / _CCGT_EFF
            elif model_tech == "Oil":
                factor["natural_gas"] = -1.0 / _OCGT_EFF

            kwargs = {
                "name": model_tech,
                "factor": factor,
                "availability": 1.0,
                "must_run": 0.0,
                "variable_cost": costs["variable_cost"],
                "fixed_cost": costs["fixed_cost"],
                "invest_cost": costs["invest_cost"],
                "finance_rate": costs["finance_rate"],
                "life_span": costs["life_span"],
                "power_capacity_min": existing_capacity_mw,
                "power_capacity_max": total_capacity_max_mw,
                "power_capacity_investment_min": existing_capacity_mw,
                "power_capacity_investment_max": total_capacity_max_mw,
                "early_decommissioning": True,
            }
            if max_yearly_production is not None:
                kwargs["max_yearly_production"] = max_yearly_production
            else:
                kwargs["max_yearly_production"] = float("inf")
            with area.model.context():
                area.add_component(ConversionTechnology(**kwargs))

    # ── Nuclear injection for _nuke scenarios ─────────────────────────
    # CLEVER 2050 declares ~0 nuclear in all countries, so the loop
    # above never creates a Nuclear ConversionTechnology. For SCENARIO
    # values in _NUCLEAR_EXPANDABLE_SCENARIOS (policy_nuke, R0_v1_nuke,
    # and their *_corrNx variants), force-instantiate Nuclear with the
    # per-country headroom from EXPANSION_HEADROOM_BY_COUNTRY so the LP
    # has a tech to invest in.
    #
    # Injection criteria:
    #   1. CLEVER_SCENARIO env var matches a nuke-expandable scenario.
    #   2. The country has an explicit Nuclear entry in
    #      EXPANSION_HEADROOM_BY_COUNTRY (so DE with 0 stays at 0;
    #      countries without a national nuclear plan don't get
    #      synthetic Nuclear).
    #   3. The CLEVER loop above didn't already add Nuclear (e.g. a
    #      future CLEVER variant that does declare nuclear).
    import os as _os
    from pommes_eur.constants import _is_nuclear_expandable
    from pommes_eur.scenario.env import current_scenario as _current_scenario
    _scen_env = _current_scenario()
    if _is_nuclear_expandable(_scen_env):
        area_norm = normalize_country_code(country_code)
        country_headroom = EXPANSION_HEADROOM_BY_COUNTRY.get(area_norm, {})
        nuke_cap_mw = float(country_headroom.get("Nuclear", 0.0))
        already_added = any(
            str(r["model_tech"]).strip() == "Nuclear"
            and float(r["max_yearly_production_mwh"]) > EPS_MWH
            for _, r in sub.iterrows()
        )
        if (not already_added) and nuke_cap_mw > EPS_MW:
            nuke_costs = techno_costs_from_eoles("nuclear", eoles_costs)
            logger.info(
                "Force-injecting Nuclear for %s-%s "
                "(SCENARIO=%s, CLEVER declares 0): existing=0, "
                "invest_max=%.0f MW, invest_cost=%.2f M€/MW",
                country_code, model_year, _scen_env,
                nuke_cap_mw, nuke_costs["invest_cost"] / 1e6,
            )
            with area.model.context():
                area.add_component(ConversionTechnology(
                    name="Nuclear",
                    factor={"electricity": 1.0},
                    availability=1.0,
                    must_run=0.0,
                    variable_cost=nuke_costs["variable_cost"],
                    fixed_cost=nuke_costs["fixed_cost"],
                    invest_cost=nuke_costs["invest_cost"],
                    finance_rate=nuke_costs["finance_rate"],
                    life_span=nuke_costs["life_span"],
                    power_capacity_min=0.0,
                    power_capacity_max=nuke_cap_mw,
                    power_capacity_investment_min=0.0,
                    power_capacity_investment_max=nuke_cap_mw,
                    early_decommissioning=True,
                    max_yearly_production=float("inf"),
                ))

def add_ror_hydro_from_supplyforge(
    area: Area,
    country: str,
    reference_year_weather: int,
    fixed_cost: float,
    investment_cost: float,
    finance_rate: float,
    lifetime: int,
) -> None:
    hydro = get_pemmdb_hydro(country)
    if hydro is None:
        logger.info("No hydro capacity metadata for %s", country)
        return

    ror_mw = float(hydro["ror_mw"])
    if ror_mw <= EPS_MW:
        return

    year_op = area.model.year_ops[0]
    hours = area.model.hours

    # ── SupplyForge nomenclature: RoR is an intermittent tech whose profile
    #    lives inside the "capacity_factors" dataset, filtered by plant_type
    #    "Hydro Run-of-river and poundage".  We try the same country/year
    #    fallback chain used for VRE profiles.
    ROR_PLANT_TYPES = [
        "Hydro Run-of-river and poundage",
        "RoR_Pondage",
        "Hydro Run-of-River",
    ]
    raw_df = None
    source_name = None
    years_to_try = [reference_year_weather] + [
        y for y in SUPPLYFORGE_FALLBACK_YEARS if y != reference_year_weather
    ]
    sf_country = to_supplyforge_code(country)
    for candidate_year in years_to_try:
        cf_df = try_load_supplyforge_parquet(
            sf_country, candidate_year, "capacity_factors", max_attempts=1,
        )
        if cf_df is not None and "plant_type" in cf_df.columns:
            for pt in ROR_PLANT_TYPES:
                subset = cf_df.filter(pl.col("plant_type") == pt)
                if not subset.is_empty():
                    raw_df = subset
                    source_name = f"capacity_factors/{pt}@{sf_country}-{candidate_year}"
                    break
        if raw_df is not None:
            break

    if raw_df is not None:
        availability = _coerce_hourly_dataframe(
            raw_df=raw_df,
            hours=hours,
            year_op=year_op,
            value_col_candidates=[
                "capacity_factor",
                "availability",
                "profile",
                "value",
            ],
            normalize_by_max=False,
            clip_01=True,
        )
        profile_source = source_name
    else:
        # Flat-profile fallback: constant CF based on historical averages.
        # Standard practice in adequacy assessment when hourly chronologies
        # are unavailable (cf. ERAA 2023 methodology §4.3.2).
        area_code = normalize_country_code(country)
        flat_cf = HYDRO_ROR_FLAT_CF.get(area_code, DEFAULT_HYDRO_ROR_FLAT_CF)
        n_hours = len(hours)
        availability = pl.DataFrame(
            {"hour": hours, "year_op": [year_op] * n_hours, "availability": [float(flat_cf)] * n_hours}
        )
        profile_source = f"FLAT_CF={flat_cf:.2f}"
        logger.warning(
            "No SupplyForge RoR chronology for %s-%s. "
            "Using flat availability=%.2f (historical avg). "
            "For better accuracy, provide hourly hydro profiles.",
            country, reference_year_weather, flat_cf,
        )

    with area.model.context():
        area.add_component(
            ConversionTechnology(
                name="RoR_Hydro",
                factor={"electricity": 1.0},
                availability=availability,
                must_run=1.0,
                variable_cost=ROR_HYDRO_VARIABLE_COST,
                fixed_cost=fixed_cost,
                invest_cost=investment_cost,
                finance_rate=finance_rate,
                life_span=lifetime,
                power_capacity_min=ror_mw,
                power_capacity_max=ror_mw,
                # POMMES: investment = total capacity; fixed stock.
                power_capacity_investment_min=ror_mw,
                power_capacity_investment_max=ror_mw,
                early_decommissioning=True,
            )
        )

    logger.info(
        "Added RoR hydro for %s | source=%s | cap=%.2f MW",
        country,
        profile_source,
        ror_mw,
    )

def add_reservoir_hydro_from_supplyforge(
    area: Area,
    country: str,
    reference_year_weather: int,
    fixed_cost: float,
    investment_cost: float,
    finance_rate: float,
    lifetime: int,
) -> None:
    hydro = get_pemmdb_hydro(country)
    if hydro is None:
        logger.info("No hydro capacity metadata for %s", country)
        return

    energy_gwh = float(hydro["pondage_gwh"]) + float(hydro["reservoir_gwh"])
    turbine_mw = float(hydro["pondage_mw"]) + float(hydro["reservoir_mw"])

    if energy_gwh <= 0.0 or turbine_mw <= EPS_MW:
        return

    energy_mwh = energy_gwh * 1e3
    year_op = area.model.year_ops[0]
    hours = area.model.hours

    # ── SupplyForge nomenclature: reservoir inflows live in the "inflow"
    #    dataset with column "inflow_MW".  We try year fallbacks.
    raw_df = None
    source_name = None
    years_to_try = [reference_year_weather] + [
        y for y in SUPPLYFORGE_FALLBACK_YEARS if y != reference_year_weather
    ]
    sf_country = to_supplyforge_code(country)
    for candidate_year in years_to_try:
        df = try_load_supplyforge_parquet(
            sf_country, candidate_year, "inflow", max_attempts=1,
        )
        if df is not None and not df.is_empty():
            raw_df = df
            source_name = f"inflow@{sf_country}-{candidate_year}"
            if candidate_year != reference_year_weather:
                logger.info(
                    "Reservoir inflow for %s: year %d unavailable, using fallback year %d.",
                    country, reference_year_weather, candidate_year,
                )
            break

    if raw_df is not None:
        # Cas 1 : la chronique est déjà normalisée / pseudo-disponibilité
        normalized_col = _find_first_existing_column(
            raw_df,
            ["availability", "capacity_factor", "profile"],
        )

        # Cas 2 : la chronique est un flux physique horaire (SupplyForge standard: "inflow_MW")
        flow_col = _find_first_existing_column(
            raw_df,
            ["inflow_MW", "inflow_mw", "value_mw", "inflow", "value"],
        )

        if normalized_col is not None:
            inflow_availability = _coerce_hourly_dataframe(
                raw_df=raw_df,
                hours=hours,
                year_op=year_op,
                value_col_candidates=[normalized_col],
                normalize_by_max=False,
                clip_01=True,
            )
            avg_inflow_mw = energy_mwh / 8760.0
            inflow_capacity_mw = avg_inflow_mw
        elif flow_col is not None:
            inflow_series = _coerce_hourly_dataframe(
                raw_df=raw_df,
                hours=hours,
                year_op=year_op,
                value_col_candidates=[flow_col],
                normalize_by_max=False,
                clip_01=False,
            )
            values = np.asarray(inflow_series["availability"].to_list(), dtype=float)
            values = np.clip(values, 0.0, None)
            inflow_capacity_mw = float(np.max(values)) if values.size else 0.0
            if inflow_capacity_mw <= EPS_MW:
                logger.warning(
                    "Reservoir inflow chronology for %s-%s is numerically null. Skipping.",
                    country, reference_year_weather,
                )
                return
            inflow_availability = pl.DataFrame(
                {
                    "hour": hours,
                    "year_op": [year_op] * len(hours),
                    "availability": (values / inflow_capacity_mw).tolist(),
                }
            )
        else:
            raise ValueError(
                "SupplyForge reservoir inflow dataframe does not contain a recognized column. "
                f"Columns={raw_df.columns}"
            )
        profile_source = source_name
    else:
        # Flat-profile fallback: distribute yearly energy uniformly across hours.
        # The inflow is set so that total yearly energy = energy_gwh (PEMMDB).
        # This preserves the correct yearly water budget but loses seasonality.
        area_code = normalize_country_code(country)
        flat_cf = HYDRO_RESERVOIR_FLAT_CF.get(area_code, DEFAULT_HYDRO_RESERVOIR_FLAT_CF)
        # Inflow capacity such that the reservoir fills its yearly budget at flat rate
        inflow_capacity_mw = energy_mwh / 8760.0  # average inflow = yearly energy / hours
        n_hours = len(hours)
        inflow_availability = pl.DataFrame(
            {"hour": hours, "year_op": [year_op] * n_hours, "availability": [1.0] * n_hours}
        )
        profile_source = f"FLAT_INFLOW (energy={energy_gwh:.0f} GWh, avg_inflow={inflow_capacity_mw:.1f} MW)"
        logger.warning(
            "No SupplyForge reservoir inflow for %s-%s. "
            "Using flat inflow=%.1f MW (yearly energy=%.0f GWh / 8760h). "
            "Seasonal reservoir dynamics are NOT modelled.",
            country, reference_year_weather, inflow_capacity_mw, energy_gwh,
        )

    if inflow_capacity_mw <= EPS_MW:
        logger.warning(
            "Computed reservoir inflow capacity is null for %s-%s. Skipping reservoir hydro.",
            country, reference_year_weather,
        )
        return

    storage_power_capacity_mw = turbine_mw

    with area.model.context():
        area.add_component(
            StorageTechnology(
                name="Reservoir_Hydro_Store",
                factor_in={"reservoir_water": -1.0},
                factor_out={"reservoir_water": 1.0},
                factor_keep={"reservoir_water": 0.0},
                life_span=lifetime,
                finance_rate=finance_rate,
                energy_capacity_investment_min=energy_mwh,
                energy_capacity_investment_max=energy_mwh,
                power_capacity_investment_min=storage_power_capacity_mw,
                power_capacity_investment_max=storage_power_capacity_mw,
                early_decommissioning=True,
            )
        )

        area.add_component(
            ConversionTechnology(
                name="Reservoir_Hydro_Inflow",
                factor={"reservoir_water": 1.0},
                availability=inflow_availability,
                must_run=1.0,
                variable_cost=0.0,
                life_span=lifetime,
                power_capacity_min=inflow_capacity_mw,
                power_capacity_max=inflow_capacity_mw,
                # POMMES: investment = total capacity; fixed stock.
                power_capacity_investment_min=inflow_capacity_mw,
                power_capacity_investment_max=inflow_capacity_mw,
                early_decommissioning=True,
            )
        )

        area.add_component(
            ConversionTechnology(
                name="Reservoir_Hydro_Plant",
                factor={"reservoir_water": -1.0, "electricity": 1.0},
                availability=1.0,
                must_run=0.0,
                variable_cost=RESERVOIR_HYDRO_VARIABLE_COST,
                fixed_cost=fixed_cost,
                invest_cost=investment_cost,
                finance_rate=finance_rate,
                life_span=lifetime,
                power_capacity_min=turbine_mw,
                power_capacity_max=turbine_mw,
                # POMMES: investment = total capacity; fixed stock.
                power_capacity_investment_min=turbine_mw,
                power_capacity_investment_max=turbine_mw,
                early_decommissioning=True,
            )
        )

    logger.info(
        "Added reservoir hydro for %s | source=%s | turbine=%.2f MW | "
        "energy=%.2f GWh | inflow_cap=%.2f MW | store_power=%.2f MW",
        country,
        profile_source,
        turbine_mw,
        energy_gwh,
        inflow_capacity_mw,
        storage_power_capacity_mw,
    )

def add_pumped_hydro_from_pemmdb(
    area: Area,
    country: str,
    fixed_cost_power: float,
    invest_cost_power: float,
    invest_cost_energy: float,
    finance_rate: float,
    lifetime: int,
) -> None:
    # PHS roundtrip efficiency: 0.80 (80%).
    # Source: JRC ETRI 2024 reference value for large-scale PHS.
    # Literature range: 0.75–0.85 depending on altitude head, penstock design,
    # and turbine-pump vintage. 0.80 is the central estimate also used by
    # ENTSO-E in ERAA 2023 and TYNDP 2024 adequacy studies.
    hydro = get_pemmdb_hydro(country)
    if hydro is None:
        return

    energy_gwh = float(hydro["phs_open_gwh"]) + float(hydro["phs_closed_gwh"])
    turbine_mw = float(hydro["phs_open_turbine_mw"]) + float(hydro["phs_closed_turbine_mw"])
    pump_mw = float(hydro["phs_open_pump_mw"]) + float(hydro["phs_closed_pump_mw"])

    if energy_gwh <= 0.0 or turbine_mw <= EPS_MW:
        return

    energy_mwh = energy_gwh * 1e3

    if pump_mw <= EPS_MW:
        logger.warning("Pumped hydro for %s has zero pumping power. Skipping.", country)
        return

    if abs(turbine_mw - pump_mw) / max(turbine_mw, 1.0) > 0.10:
        logger.warning(
            "Pumped hydro power asymmetry for %s | turbine=%.2f MW | pump=%.2f MW",
            country,
            turbine_mw,
            pump_mw,
        )

    with area.model.context():
        area.add_component(
            StorageTechnology(
                name="Pumped_Hydro",
                factor_in={"electricity": -1.0},
                factor_out={"electricity": PUMPED_HYDRO_ROUNDTRIP_EFF},
                factor_keep={"electricity": 0.0},
                fixed_cost_power=fixed_cost_power,
                invest_cost_power=invest_cost_power,
                invest_cost_energy=invest_cost_energy,
                finance_rate=finance_rate,
                energy_capacity_investment_min=energy_mwh,
                energy_capacity_investment_max=energy_mwh,
                power_capacity_investment_min=turbine_mw,
                power_capacity_investment_max=turbine_mw,
                life_span=lifetime,
                early_decommissioning=True,
            )
        )

    logger.info(
        "Added PEMMDB pumped hydro for %s | turbine=%.2f MW | pump=%.2f MW | energy=%.2f GWh",
        country,
        turbine_mw,
        pump_mw,
        energy_gwh,
    )


def add_bess_from_supplyforge_style(
    area: Area,
    eoles_costs: dict[str, dict[str, float]],
    power_investment_max_by_tech: Optional[dict[str, float]] = None,
) -> None:
    """
    Add expandable battery storage technologies.

    Design choice:
    - no exogenous installed BESS capacity is imposed;
    - batteries are purely endogenous investment options;
    - costs are taken from EOLES inputs;
    - formulation follows the storage modelling style used in SupplyForge / pommes_craft.
    """
    # Use country-specific BESS caps if available, else fallback to defaults
    if power_investment_max_by_tech is not None:
        power_caps = power_investment_max_by_tech
    else:
        area_code = str(area.name).upper().strip()
        power_caps = BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY.get(
            area_code, DEFAULT_BESS_POWER_INVESTMENT_MAX_MW
        )

    for tech_name, spec in BESS_SPECS.items():
        eoles_tech = spec["eoles_tech"]
        duration_hours = float(spec["duration_hours"])
        eta_rt = float(spec["roundtrip_efficiency"])

        if duration_hours <= 0.0:
            raise ValueError(f"Invalid duration_hours for {tech_name}: {duration_hours}")
        if eta_rt <= 0.0 or eta_rt > 1.0:
            raise ValueError(f"Invalid roundtrip_efficiency for {tech_name}: {eta_rt}")

        costs = storage_costs_from_eoles(eoles_tech, eoles_costs)

        power_investment_max_mw = float(power_caps.get(tech_name, 0.0))
        if power_investment_max_mw <= EPS_MW:
            logger.info("Skipping %s because investment headroom is null.", tech_name)
            continue

        energy_investment_max_mwh = power_investment_max_mw * duration_hours

        # Convention choisie:
        # - charge: 1 MWh elec -> 1 MWh stocké
        # - discharge: 1 MWh stocké -> eta_rt MWh elec restitué
        # Cela garde un round-trip simple et robuste dans POMMES.
        with area.model.context():
            area.add_component(
                StorageTechnology(
                    name=tech_name,
                    factor_in={"electricity": -1.0},
                    factor_out={"electricity": eta_rt},
                    factor_keep={"electricity": 0.0},
                    fixed_cost_power=costs["fixed_cost_power"],
                    invest_cost_power=costs["invest_cost_power"],
                    invest_cost_energy=costs["invest_cost_energy"],
                    finance_rate=costs["finance_rate"],
                    life_span=costs["life_span"],
                    energy_capacity_investment_min=0.0,
                    energy_capacity_investment_max=energy_investment_max_mwh,
                    power_capacity_investment_min=0.0,
                    power_capacity_investment_max=power_investment_max_mw,
                    early_decommissioning=True,
                )
            )

        logger.info(
            "Added expandable BESS %s for %s | max_power=%.2f MW | max_energy=%.2f MWh | duration=%.1f h",
            tech_name,
            area.name,
            power_investment_max_mw,
            energy_investment_max_mwh,
            duration_hours,
        )

def add_intermittent_tech_from_clever(
    area: Area,
    clever_tech: str,
    reference_year_weather: int,
    capacity_mw: float,
    target_load_factor: Optional[float],
    eoles_costs: dict[str, dict[str, float]],
    allow_profile_fallback_from_other_country: bool = True,
    skip_if_no_profile: bool = True,
) -> None:
    if clever_tech not in CLEVER_VRE_SPECS:
        raise ValueError(f"Unsupported CLEVER VRE technology: {clever_tech}")

    spec = CLEVER_VRE_SPECS[clever_tech]
    tech_name = spec["model_tech"]
    profile_candidates = spec["default_profile_candidates"]
    eoles_tech = spec["eoles_tech"]

    costs = techno_costs_from_eoles(eoles_tech, eoles_costs)

    year_op = area.model.year_ops[0]
    hours = area.model.hours

    cf_slice = pl.DataFrame()
    source_country = None
    matched_plant_type = None

    if allow_profile_fallback_from_other_country:
        cf_slice, source_country, matched_plant_type = _load_capacity_factor_slice_with_fallback(
            country_code=area.name,
            reference_year_weather=reference_year_weather,
            clever_tech=clever_tech,
            profile_candidates=profile_candidates,
        )
    else:
        capacity_factors = try_load_supplyforge_parquet(
            area.name,
            reference_year_weather,
            "capacity_factors",
            max_attempts=3,
        )
        cf_slice = _select_capacity_factor_slice(capacity_factors, profile_candidates)
        if not cf_slice.is_empty() and "plant_type" in cf_slice.columns:
            matched_plant_type = str(cf_slice["plant_type"][0])
            source_country = area.name

    if cf_slice.is_empty():
        # Last-resort flat-profile fallback using country-specific or generic CFs.
        # This ensures VRE capacity declared in CLEVER is always included in the
        # model, even when no hourly profile is available from SupplyForge.
        # The flat profile is conservative: it lacks temporal correlation with
        # demand and weather, but is far better than dropping the technology entirely
        # (which would cause extreme scarcity prices).
        area_code = normalize_country_code(str(area.name))
        country_cfs = DEFAULT_VRE_FLAT_CF.get(area_code, DEFAULT_VRE_FLAT_CF_GENERIC)
        flat_cf = country_cfs.get(tech_name, DEFAULT_VRE_FLAT_CF_GENERIC.get(tech_name, 0.0))

        if flat_cf <= 0.0:
            logger.warning(
                "No capacity-factor profile and no flat-CF fallback for %s in %s. Skipping.",
                tech_name, area.name,
            )
            return

        tech_availability = _build_flat_hourly_availability(hours, year_op, value=flat_cf)
        logger.warning(
            "No SupplyForge capacity-factor profile found for %s in %s-%s "
            "(tried candidates=%s, countries+years exhausted). "
            "Using flat availability=%.2f (country avg). "
            "For accurate results, provide hourly VRE profiles.",
            tech_name, area.name, reference_year_weather, profile_candidates, flat_cf,
        )
    else:
        logger.info(
            "Using capacity-factor profile '%s' from %s for %s in %s-%s.",
            matched_plant_type,
            source_country,
            tech_name,
            area.name,
            reference_year_weather,
        )
        tech_availability = scale_vre_profile_to_target_lf(
            cf_slice.select("capacity_factor")
            if "capacity_factor" in cf_slice.columns
            else cf_slice.select("availability"),
            target_load_factor=target_load_factor,
            hours=hours,
            year_op=year_op,
        )

    # ── VRE capacity band: CLEVER as central, ±X% as uncertainty band ─
    # (2026-05-12 change — was: CLEVER as hard floor only)
    #
    # CLEVER's role is reinterpreted from "ambition floor" to "central
    # estimate with symmetric ±X% uncertainty band", aligning with Girard
    # 2025's "delivered vs announced" deployment-gap framing. The LP can:
    #   - Decommission down to (1 − downside_frac) × CLEVER at no cost
    #     (free-decommissioning; CLEVER's value reinterpreted as upper-
    #     bound ambition for that portion)
    #   - Expand up to (1 + headroom_frac) × CLEVER, paying the normal
    #     invest_cost × annuity for the +ΔMW
    # See clever.constants.{DEFAULT_VRE_DOWNSIDE_BAND_FRACTION,
    # VRE_DOWNSIDE_BAND_BY_COUNTRY} to gate per-country overrides.
    area_code = normalize_country_code(str(area.name))
    country_vre_downside = VRE_DOWNSIDE_BAND_BY_COUNTRY.get(area_code, {})
    downside_frac = country_vre_downside.get(
        tech_name, DEFAULT_VRE_DOWNSIDE_BAND_FRACTION
    )
    capacity_floor_mw = capacity_mw * (1.0 - downside_frac)

    # ── CAPEX adder for CLEVER-anchored capacity (Option β, 2026-05-12) ─
    # Fold annualised investment cost into variable_cost so that LP duals
    # (electricity prices) reflect full LCOE for exogenous VRE. With the
    # symmetric band the adder is restricted to the floor portion only:
    # the variable_cost burden covers `capacity_floor_mw / capacity_mw`
    # of the annuity, and any expansion above the floor pays via the
    # normal conversion_invest_cost × annuity LP objective term. This
    # avoids double-counting CAPEX when the LP builds in the band.
    #   CRF = r / (1 − (1+r)^(−n))
    #   annuity = invest_cost × CRF          [EUR/MW/yr]
    #   capex_adder = annuity / (CF × 8760) × (floor / clever)  [EUR/MWh]
    # The capacity factor used is the profile mean (realised CF), not the
    # target_load_factor, to avoid division artefacts in low-CF countries.
    effective_variable_cost = costs["variable_cost"]
    capex_adder = 0.0

    if VRE_CAPEX_IN_VARIABLE_COST and costs["invest_cost"] > 0:
        r = costs["finance_rate"]
        n = costs["life_span"]
        if r > 0 and n > 0:
            crf = r / (1.0 - (1.0 + r) ** (-n))
        elif n > 0:
            crf = 1.0 / n
        else:
            crf = 1.0
        annuity_per_mw = costs["invest_cost"] * crf  # EUR/MW/yr

        # Compute realised CF from the availability profile
        if tech_availability is not None and isinstance(tech_availability, pl.DataFrame):
            profile_mean = float(tech_availability["availability"].mean())
        else:
            profile_mean = float(target_load_factor) if target_load_factor else 0.0

        if profile_mean > 0.01:  # guard against near-zero CF
            # Option β scaling: adder covers only the floor portion.
            floor_share = capacity_floor_mw / capacity_mw if capacity_mw > 0 else 1.0
            capex_adder = annuity_per_mw / (profile_mean * 8760.0) * floor_share
            effective_variable_cost += capex_adder
            logger.info(
                "  %s/%s CAPEX adder: annuity=%.0f EUR/MW/yr, CF=%.3f, "
                "adder=%.1f EUR/MWh → effective variable_cost=%.1f EUR/MWh",
                area.name, tech_name, annuity_per_mw, profile_mean,
                capex_adder, effective_variable_cost,
            )
        else:
            logger.warning(
                "  %s/%s CAPEX adder skipped: profile CF=%.6f too low.",
                area.name, tech_name, profile_mean,
            )

    # ── VRE expansion headroom ─────────────────────────────────────────
    # On top of the capacity_floor_mw (= CLEVER × (1 − downside_frac))
    # already computed above, the LP may expand up to (1 + headroom_frac)
    # × CLEVER.  Three regimes for the LP:
    #   - At/above capacity_mw (CLEVER's announced value):
    #       expansion paid via conversion_invest_cost × annuity
    #   - Between capacity_floor_mw and capacity_mw:
    #       no incremental cost (CAPEX covered by Option β scaled adder)
    #   - At capacity_floor_mw:
    #       hard floor; free decommissioning of the (capacity_mw − floor)
    #       portion that the LP didn't choose to retain
    # (Note: `area_code` is computed earlier in the function for the
    # downside-band lookup; reusing it here.)
    country_vre_hr = VRE_EXPANSION_HEADROOM_BY_COUNTRY.get(area_code, {})
    # DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION is now a per-tech dict
    # (Solar/Wind_Onshore/Wind_Offshore → float). Fall back to 0.15 if a
    # new VRE tech ever shows up that isn't in the dict.
    default_frac = DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION.get(tech_name, 0.15)
    headroom_frac = country_vre_hr.get(tech_name, default_frac)
    invest_max_mw = capacity_mw * headroom_frac
    total_capacity_max_mw = capacity_mw + invest_max_mw

    logger.info(
        "Adding intermittent %s for %s | CLEVER=%.0f MW | "
        "band=[%.0f, %.0f] MW (−%.0f%% / +%.0f%%) | "
        "var_cost=%.1f (VOM=%.1f + CAPEX_adder=%.1f, floor_share=%.2f) | target_LF=%s",
        tech_name,
        area.name,
        capacity_mw,
        capacity_floor_mw,
        total_capacity_max_mw,
        downside_frac * 100,
        headroom_frac * 100,
        effective_variable_cost,
        costs["variable_cost"],
        capex_adder,
        capacity_floor_mw / capacity_mw if capacity_mw > 0 else 1.0,
        f"{target_load_factor:.4f}" if target_load_factor is not None else "n/a",
    )

    with area.model.context():
        area.add_component(
            ConversionTechnology(
                name=tech_name,
                factor={"electricity": 1.0},
                availability=tech_availability,
                must_run=0.0,
                variable_cost=effective_variable_cost,
                fixed_cost=costs["fixed_cost"],
                invest_cost=costs["invest_cost"],
                finance_rate=costs["finance_rate"],
                life_span=costs["life_span"],
                # Symmetric band: LP must build between capacity_floor_mw
                # and total_capacity_max_mw, with the CAPEX-into-variable-
                # cost adder (Option β) carrying the floor-portion CAPEX
                # and conversion_invest_cost × annuity carrying any expansion
                # above capacity_mw.
                power_capacity_min=capacity_floor_mw,
                power_capacity_max=total_capacity_max_mw,
                power_capacity_investment_min=capacity_floor_mw,
                power_capacity_investment_max=total_capacity_max_mw,
                early_decommissioning=True,
            )
        )





# =========================================================
# ADEQUACY DIAGNOSTICS
# =========================================================
def _safe_peak_demand_mw(electricity_demand_pl: pl.DataFrame) -> float:
    if electricity_demand_pl.is_empty():
        return 0.0

    if "demand" in electricity_demand_pl.columns:
        return float(electricity_demand_pl.select(pl.col("demand").max()).item())

    candidate_cols = [c for c in electricity_demand_pl.columns if c not in {"hour", "year_op"}]
    if len(candidate_cols) == 1:
        return float(electricity_demand_pl.select(pl.col(candidate_cols[0]).max()).item())

    raise ValueError(
        f"Unable to infer demand column from hourly demand dataframe. Columns={electricity_demand_pl.columns}"
    )


def _compute_country_adequacy_metrics(
    country_code: str,
    model_year: int,
    clever_capacity_df: pd.DataFrame,
    clever_non_enr_df: pd.DataFrame,
    electricity_demand_pl: pl.DataFrame,
) -> dict[str, float]:
    peak_demand_mw = _safe_peak_demand_mw(electricity_demand_pl)

    vre_credit_mw = 0.0
    for clever_tech, spec in CLEVER_VRE_SPECS.items():
        model_tech = spec["model_tech"]
        cap = get_clever_capacity_mw(clever_capacity_df, country_code, model_year, model_tech) or 0.0
        vre_credit_mw += cap * VRE_CAPACITY_CREDIT.get(model_tech, 0.0)

    dispatchable_existing_mw = 0.0
    dispatchable_total_max_mw = 0.0

    sub = pd.DataFrame()
    for candidate in _candidate_area_codes(country_code):
        tmp = clever_non_enr_df[
            (clever_non_enr_df["area"] == candidate)
            & (clever_non_enr_df["year_op"] == model_year)
        ].copy()
        if not tmp.empty:
            sub = tmp
            break

    if not sub.empty:
        for _, row in sub.iterrows():
            model_tech = str(row["model_tech"]).strip()
            max_yearly_production_mwh = float(row["max_yearly_production_mwh"])

            eoles_tech = MODELTECH_TO_EOLES.get(model_tech)
            if eoles_tech is None:
                continue

            existing_capacity_mw = max(max_yearly_production_mwh / ASSUMED_FLH.get(eoles_tech, DEFAULT_FLH), 0.0)
            if existing_capacity_mw <= EPS_MW:
                existing_capacity_mw = 0.0

            dispatchable_existing_mw += existing_capacity_mw

            if is_expandable_dispatchable(model_tech):
                dispatchable_total_max_mw += existing_capacity_mw + DEFAULT_EXPANSION_HEADROOM_MW.get(model_tech, 0.0)
            elif max_yearly_production_mwh > EPS_MWH:
                dispatchable_total_max_mw += existing_capacity_mw

    adequacy_margin_mw = dispatchable_total_max_mw + vre_credit_mw - peak_demand_mw

    return {
        "peak_demand_mw": peak_demand_mw,
        "dispatchable_existing_mw": dispatchable_existing_mw,
        "dispatchable_total_max_mw": dispatchable_total_max_mw,
        "vre_credit_mw": vre_credit_mw,
        "adequacy_margin_mw": adequacy_margin_mw,
    }


def _log_country_adequacy_diagnostic(
    country_code: str,
    model_year: int,
    clever_capacity_df: pd.DataFrame,
    clever_non_enr_df: pd.DataFrame,
    electricity_demand_pl: pl.DataFrame,
) -> None:
    metrics = _compute_country_adequacy_metrics(
        country_code=country_code,
        model_year=model_year,
        clever_capacity_df=clever_capacity_df,
        clever_non_enr_df=clever_non_enr_df,
        electricity_demand_pl=electricity_demand_pl,
    )

    logger.info(
        "Adequacy diagnostic %s-%s | peak=%.0f MW | dispatchable_existing=%.0f MW | "
        "dispatchable_total_max=%.0f MW | vre_credit=%.0f MW | margin=%.0f MW",
        normalize_country_code(country_code),
        model_year,
        metrics["peak_demand_mw"],
        metrics["dispatchable_existing_mw"],
        metrics["dispatchable_total_max_mw"],
        metrics["vre_credit_mw"],
        metrics["adequacy_margin_mw"],
    )

    if metrics["adequacy_margin_mw"] < 0:
        logger.warning(
            "Potential adequacy issue for %s-%s: conservative margin is negative (%.0f MW).",
            normalize_country_code(country_code),
            model_year,
            metrics["adequacy_margin_mw"],
        )


# =========================================================
# INTERCONNECTIONS
# =========================================================
def add_manual_interconnections(
    energy_model: EnergyModel,
    areas: dict[str, Area],
    interconnections: dict[tuple[str, str], dict],
) -> None:
    from pommes_eur.constants import (
        INVESTABLE_CORRIDOR_PAIRS,
        INVESTABLE_CORRIDOR_CAPEX_EUR_PER_MW,
        INVESTABLE_CORRIDOR_LIFE_SPAN_YR,
        INVESTABLE_CORRIDOR_FINANCE_RATE,
        INVESTABLE_CORRIDOR_MULT,
        _NO_GRID_EXPANSION,
    )

    for countries, interco in interconnections.items():
        if len(countries) != 2:
            raise ValueError(f"Interconnection key must contain exactly 2 countries, got {countries}")

        c1 = normalize_country_code(countries[0])
        c2 = normalize_country_code(countries[1])

        if c1 == c2:
            continue
        if c1 not in areas or c2 not in areas:
            continue

        capacity = interco.get("capacity", 0.0)
        investment_cost = float(interco.get("investment_cost", 0.0))

        if isinstance(capacity, str):
            raise ValueError(
                f"'use_existing' is not supported in CLEVER manual mode for {(c1, c2)}. "
                "Pass an explicit float capacity."
            )

        capacity = float(capacity)
        if capacity <= 0:
            continue

        # Pinned existing capacity — free (invest_cost=0), invest_min=invest_max=capacity.
        # This is the baseline behaviour for ALL corridors.
        transport_tech_dict = {
            "name": "electric_line",
            "resource": "electricity",
            "life_span": 25.0,
            "invest_cost": investment_cost,
            "fixed_cost": 0.0,
            "hurdle_costs": 0.0,
            "finance_rate": 0.0,
            "power_capacity_investment_max": capacity,
            "power_capacity_investment_min": capacity,
        }

        # Investable-expansion layer — only for the 4 "investable corridor"
        # pairs AND when CORRIDOR_MULT > 1.0 (i.e. SCENARIO ends with _corrNx).
        # The LP gains an additional `delta` MW of headroom on top of the
        # pinned floor, at 1 M€/MW HVDC CAPEX.
        is_investable = (
            frozenset({c1, c2}) in INVESTABLE_CORRIDOR_PAIRS
            and INVESTABLE_CORRIDOR_MULT > 1.0
            and not _NO_GRID_EXPANSION   # _noGridExp: pin NTC at existing capacity (Neumann grid-blocked benchmark)
        )

        with energy_model.context():
            t_fw = TransportTechnology(**transport_tech_dict)
            l_fw = Link(name=f"link_{c1}_{c2}", area_from=areas[c1], area_to=areas[c2])
            l_fw.add_transport_technology(t_fw)

            t_bw = TransportTechnology(**transport_tech_dict)
            l_bw = Link(name=f"link_{c2}_{c1}", area_from=areas[c2], area_to=areas[c1])
            l_bw.add_transport_technology(t_bw)

            if is_investable:
                delta = capacity * (INVESTABLE_CORRIDOR_MULT - 1.0)
                # Italy structural elec-import bottleneck (Italy elec ENS fix):
                # double the investable HVDC headroom on any IT-touching corridor.
                if "IT" in (c1, c2):
                    delta *= 2.0
                exp_tech_dict = {
                    "name": "electric_line_expansion",
                    "resource": "electricity",
                    "life_span": float(INVESTABLE_CORRIDOR_LIFE_SPAN_YR),
                    "invest_cost": INVESTABLE_CORRIDOR_CAPEX_EUR_PER_MW,
                    "fixed_cost": 0.0,
                    "hurdle_costs": 0.0,
                    "finance_rate": INVESTABLE_CORRIDOR_FINANCE_RATE,
                    "power_capacity_investment_max": delta,
                    "power_capacity_investment_min": 0.0,
                }
                t_fw_exp = TransportTechnology(**exp_tech_dict)
                l_fw.add_transport_technology(t_fw_exp)
                t_bw_exp = TransportTechnology(**exp_tech_dict)
                l_bw.add_transport_technology(t_bw_exp)

        if is_investable:
            logger.info(
                "Added investable interconnection %s <-> %s : floor=%.0f MW (free), "
                "ceiling=%.0f MW (×%.1f, +%.0f MW at %.0f €/MW)",
                c1, c2, capacity, capacity * INVESTABLE_CORRIDOR_MULT,
                INVESTABLE_CORRIDOR_MULT,
                capacity * (INVESTABLE_CORRIDOR_MULT - 1.0),
                INVESTABLE_CORRIDOR_CAPEX_EUR_PER_MW,
            )
        else:
            logger.info("Added manual interconnection %s <-> %s : %.0f MW", c1, c2, capacity)


def add_h2_pipeline_interconnections(
    energy_model: EnergyModel,
    areas: dict[str, Area],
    interconnections: dict[tuple[str, str], dict],
    hurdle_cost: float = 0.5,
) -> None:
    """
    Add hydrogen pipeline links between all interconnected country pairs.

    By default the pipeline capacity is *unconstrained* (investment_max = NaN,
    which POMMES interprets as "no upper bound" via its ``np.isfinite`` mask).
    This lets the optimiser freely decide how much H₂ pipeline capacity to build
    based on cost trade-offs.

    Parameters
    ----------
    energy_model : EnergyModel
    areas : dict mapping country codes to Area objects
    interconnections : same dict used for electricity (only the country pairs
        are used — capacity and cost are pipeline-specific)
    hurdle_cost : float
        Compression energy + losses expressed as EUR/MWh_H₂ transported.
        Default 0.5 EUR/MWh ≈ 1-2 % loss at ~75 EUR/MWh_H₂.
    """
    for countries in interconnections:
        if len(countries) != 2:
            continue

        c1 = normalize_country_code(countries[0])
        c2 = normalize_country_code(countries[1])

        if c1 == c2 or c1 not in areas or c2 not in areas:
            continue

        h2_pipeline_dict = {
            "name": "h2_pipeline",
            "resource": "hydrogen",
            "life_span": 40.0,           # Steel pipeline lifetime
            "invest_cost": 0.0,          # Sunk cost (European Hydrogen Backbone)
            "fixed_cost": 0.0,
            "hurdle_costs": hurdle_cost,
            "finance_rate": 0.0,
            # NaN = unconstrained in POMMES (np.isfinite mask skips the constraint)
            "power_capacity_investment_min": 0.0,
            "power_capacity_investment_max": float("nan"),
        }

        with energy_model.context():
            t_fw = TransportTechnology(**h2_pipeline_dict)
            l_fw = Link(
                name=f"h2_link_{c1}_{c2}",
                area_from=areas[c1],
                area_to=areas[c2],
            )
            l_fw.add_transport_technology(t_fw)

            t_bw = TransportTechnology(**h2_pipeline_dict)
            l_bw = Link(
                name=f"h2_link_{c2}_{c1}",
                area_from=areas[c2],
                area_to=areas[c1],
            )
            l_bw.add_transport_technology(t_bw)

        logger.info("Added H₂ pipeline %s <-> %s (unconstrained capacity)", c1, c2)


# =========================================================
# OPTIONAL: helper for current POMMES ramping issue
# =========================================================
def neutralize_conversion_ramping_table(model: EnergyModel) -> None:
    model.to_pommes_model()

    target_key = None
    for key in model.parameter_tables:
        if "conversion_area_conversion_tech_year_op" in key:
            target_key = key
            break

    if target_key is None:
        return

    df = model.parameter_tables[target_key].copy()

    for col in ("conversion_ramp_up", "conversion_ramp_down"):
        if col in df.columns:
            df[col] = np.nan

    if "conversion_ramp_relative_to_capacity" in df.columns:
        df["conversion_ramp_relative_to_capacity"] = False

    model.parameter_tables[target_key] = df

# NOTE: sanitize_storage_inputs lives in clever.runner (single definition).

# =========================================================
# INTERNAL BUILD HELPERS
# =========================================================
def _create_empty_energy_model(
    name: str,
    model_year: int,
    include_hydrogen: bool = False,
    include_biomethane: Optional[bool] = None,
) -> EnergyModel:
    max_lifetime = max(EOLES_LIFETIME.values())
    hours = list(range(8760))

    resources = ["electricity", "reservoir_water"]
    if include_hydrogen:
        resources.append("hydrogen")

    # Biomethane supply chain (Phase 1.7 refactor) — three-stage:
    #   1. raw_biomass bus — supplied by NetImport (ENSPRESO feedstock cost), capped at potential
    #   2. biomethane_plant (AD plant CT) — converts raw_biomass → biomethane, shared CAPEX
    #   3. biomethane bus — drawn by BioCCGT (→ electricity) and ATR (→ hydrogen)
    # Auto-enabled whenever the scenario carries a _bioLow / _bioMed / _bioHigh suffix.
    if include_biomethane is None:
        from pommes_eur.constants import _BIOMETHANE_SCOPE
        include_biomethane = _BIOMETHANE_SCOPE is not None
    if include_biomethane:
        resources.append("biomethane")
        resources.append("raw_biomass")

    # MENA H₂ imports (Phase 2.5) — both variants need the "hydrogen" bus
    # even when no other H₂-active suffix (e.g. _atr, _h2HIGH) is present.
    if not include_hydrogen:
        try:
            from pommes_eur.model.techs.mena_imports import mena_imports_enabled
            if mena_imports_enabled():
                resources.append("hydrogen")
        except Exception:  # mena_imports unavailable at this import stage
            pass

    # Phase 3: fossil-methane + oil as endogenous resources. Both buses are
    # built unless _noGas is active (which also bans the consuming techs).
    # natural_gas is consumed by Gas (CCGT) and Oil (OCGT — methane-fired
    # peaker per EOLES). oil bus is future-facing — has supply (NetImport)
    # but no current consumer.
    from pommes_eur.constants import _NO_GAS, _MENA_OPTIM_ACTIVE_COUNTRIES
    # natural_gas stays in the model when MENA Variant B is active even under
    # _noGas: gas-free is an EU policy, not MENA's. MENA runs its own grid on
    # gas (its NG NetImport + CCGT are gated on this resource) and exports green
    # H2. Europe stays genuinely gas-free — every EU gas consumer is banned
    # explicitly under _NO_GAS (Gas/OCGT model.py:986-995; ATR/SMR-CCS
    # methane_h2_ccs.py:161-167; EU natural_gas NetImport ~2426) regardless of
    # whether the resource exists. oil stays EU-only (MENA does not use it).
    if (not _NO_GAS) or _MENA_OPTIM_ACTIVE_COUNTRIES:
        resources.append("natural_gas")
    if not _NO_GAS:
        resources.append("oil")

    return EnergyModel(
        name=f"{name}_{model_year}",
        hours=hours,
        year_ops=[model_year],
        year_invs=[model_year],
        year_decs=list(range(model_year, model_year + max_lifetime + 1)),
        # Modes registry:
        #   "base"     — default single-mode for ConversionTechnology and
        #                single-mode CombinedTechnology entries.
        #   "ng_mode"  — fossil natural_gas pathway in fuel-flex CCGT/OCGT
        #                and H₂-CCS CombinedTechnology (added 2026-05-28).
        #   "bio_mode" — biomethane pathway in the same fuel-flex techs.
        #                For CCS techs this is the BECCS pathway (net negative
        #                emissions via captured biogenic CO₂).
        modes=["base", "ng_mode", "bio_mode"],
        resources=resources,
    )


def _initialize_model_structure(energy_model: EnergyModel, model_year: int) -> None:
    with energy_model.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=model_year, planning_step=25)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)


def _add_country_components(
    energy_model: EnergyModel,
    area: Area,
    country_code: str,
    reference_year_weather: int,
    model_year: int,
    clever_capacity_df: pd.DataFrame,
    clever_load_factor_df: pd.DataFrame,
    clever_non_enr_df: pd.DataFrame,
    electricity_demand_pl: pl.DataFrame,
    eoles_costs: dict,
    add_hydro: bool,
    flex_demand_fraction: float = 0.0,
    flex_conservation_hrs: int = 0,
    flex_max_multiplier: float = 1.0,
    flex_min_multiplier: float = 1.0,
    flex_ramp_up: float = np.nan,
    flex_ramp_down: float = np.nan,
    flex_variable_cost: float = np.nan,
) -> None:
    normalized_country = normalize_country_code(country_code)

    _log_country_adequacy_diagnostic(
        country_code=country_code,
        model_year=model_year,
        clever_capacity_df=clever_capacity_df,
        clever_non_enr_df=clever_non_enr_df,
        electricity_demand_pl=electricity_demand_pl,
    )

    for clever_tech, spec in CLEVER_VRE_SPECS.items():
        model_tech = spec["model_tech"]

        clever_capacity = get_clever_capacity_mw(
            clever_capacity_df,
            country_code,
            model_year,
            model_tech,
        )
        if clever_capacity is None or clever_capacity <= 0:
            continue

        target_lf = get_clever_load_factor(
            clever_load_factor_df,
            country_code,
            model_year,
            model_tech,
        )

        add_intermittent_tech_from_clever(
            area=area,
            clever_tech=clever_tech,
            reference_year_weather=reference_year_weather,
            capacity_mw=clever_capacity,
            target_load_factor=target_lf,
            eoles_costs=eoles_costs,
        )

    add_dispatchable_from_non_enr(
        area=area,
        clever_non_enr_df=clever_non_enr_df,
        country_code=country_code,
        model_year=model_year,
        eoles_costs=eoles_costs,
    )

    # Biomethane bundle (Biomethane_CCGT + optional ATR_biomethane).
    # No-op if CLEVER_SCENARIO doesn't carry a _bioLow / _bioMed / _bioHigh suffix.
    # See clever/biomethane.py:add_biomethane_to_area for the bundled-tech
    # capex/FOM/fuel-cost computation and the article methodology section.
    from pommes_eur.model.techs.biomethane import add_biomethane_to_area
    add_biomethane_to_area(area=area, country_code=country_code)

    # MENA H₂ imports — Variant A (NetImport on EU entry-point). No-op when
    # _menaH2NNN is absent or country_code is not in MENA_H2_ENTRY_SHARES.
    # Variant B (per-country MENA Areas) is wired separately in
    # create_multi_country_model_from_clever — see add_mena_to_model.
    from pommes_eur.model.techs.mena_imports import add_variant_a_imports_to_area
    add_variant_a_imports_to_area(area=area, country_code=country_code)

    # Phase 3: fossil-methane + oil supply (per-country NetImports). Skipped
    # under _noGas. Prices come from clever/data_fetchers.py (WB Pink Sheet),
    # with CO₂ adder layered on by clever/carbon_price.py. Oil bus has supply
    # but no current consumer — future-facing capability per refactor scope.
    from pommes_eur.constants import _NO_GAS
    if not _NO_GAS:
        from pommes_craft import NetImport
        from pommes_eur.constants import natural_gas_import_price, oil_import_price
        with area.model.context():
            area.add_component(NetImport(
                name="natural_gas_supply",
                resource="natural_gas",
                import_price=natural_gas_import_price(),
                max_yearly_energy_export=0.0,
            ))
            area.add_component(NetImport(
                name="oil_supply",
                resource="oil",
                import_price=oil_import_price(),
                max_yearly_energy_export=0.0,
            ))

    # H₂ CCS techs (SMR_CCS + ATR_CCS) — 2026-05-28 refactor: both are now
    # fuel-flex `CombinedTechnology` with two modes (ng_mode + bio_mode),
    # sharing a single CAPEX-bearing capacity. The LP decides hour-by-hour
    # how to split the plant's MW between fossil natural_gas and biomethane.
    # bio_mode of either tech = BECCS (negative emissions).
    # Internally gated by `not _NO_GAS` + "hydrogen" in resources + at least
    # one of (natural_gas, biomethane) present.
    from pommes_eur.model.techs.ccs import add_h2_ccs_techs_to_area
    add_h2_ccs_techs_to_area(area=area, country_code=country_code)

    # ── Free-import lockdown (added 2026-05-26, redesigned 2026-05-27) ──
    # POMMES auto-creates net_import_max_yearly_energy_import variables once
    # ANY NetImport component exists in the model. The auto-generated slots
    # default to NaN (= unlimited) at price 0 — i.e., every (area, resource)
    # pair without an explicit NetImport becomes a free unlimited supply path.
    #
    # 2026-05-27 redesign: raw_biomass is now supplied by a production-only
    # ConversionTechnology (not a NetImport), so non-MENA / _noGas scenarios
    # carry ZERO NetImports — `p.net_import=False` and the whole expansion is
    # skipped. The lock helper below is a no-op in that case. When the
    # scenario does contain a real NetImport (mena_h2_import or fossil-gas/oil
    # supply), the helper explicitly locks every other resource slot.
    _add_eu_resource_locks(area=area, country_code=country_code)

    if add_hydro:
        river_costs = techno_costs_from_eoles("river", eoles_costs)
        lake_costs = techno_costs_from_eoles("lake", eoles_costs)
        phs_costs = storage_costs_from_eoles("phs", eoles_costs)

        try:
            add_ror_hydro_from_supplyforge(
                area=area,
                country=normalized_country,
                reference_year_weather=reference_year_weather,
                fixed_cost=river_costs["fixed_cost"],
                investment_cost=river_costs["invest_cost"],
                finance_rate=river_costs["finance_rate"],
                lifetime=river_costs["life_span"],
            )
        except Exception as err:
            logger.warning("PEMMDB RoR hydro could not be added for %s: %s", normalized_country, err)

        try:
            add_reservoir_hydro_from_supplyforge(
                area=area,
                country=normalized_country,
                reference_year_weather=reference_year_weather,
                fixed_cost=lake_costs["fixed_cost"],
                investment_cost=lake_costs["invest_cost"],
                finance_rate=lake_costs["finance_rate"],
                lifetime=lake_costs["life_span"],
            )
        except Exception as err:
            logger.warning("PEMMDB reservoir hydro could not be added for %s: %s", normalized_country, err)

        try:
            add_pumped_hydro_from_pemmdb(
                area=area,
                country=normalized_country,
                fixed_cost_power=phs_costs["fixed_cost_power"],
                invest_cost_power=phs_costs["invest_cost_power"],
                invest_cost_energy=phs_costs["invest_cost_energy"],
                finance_rate=phs_costs["finance_rate"],
                lifetime=phs_costs["life_span"],
            )
        except Exception as err:
            logger.warning("PEMMDB pumped hydro could not be added for %s: %s", normalized_country, err)
    add_bess_from_supplyforge_style(
        area=area,
        eoles_costs=eoles_costs,
    )
    peak_demand = _safe_peak_demand_mw(electricity_demand_pl)

    with energy_model.context():
        area.add_component(
            Demand(
                name="electricity_demand",
                resource="electricity",
                demand=electricity_demand_pl,
            )
        )
        area.add_component(
            Spillage(name="electricity_spillage", resource="electricity", max_capacity=2.0 * peak_demand)
        )
        area.add_component(
            LoadShedding(
                name="electricity_load_shedding",
                resource="electricity",
                cost=DEFAULT_LOAD_SHEDDING_COST,
                max_capacity=2.0 * peak_demand,
            )
        )
        area.add_component(
            Spillage(
                name="reservoir_water_spillage",
                resource="reservoir_water",
                max_capacity=DEFAULT_WATER_SPILLAGE_MAX,
            )
        )
        area.add_component(
            LoadShedding(
                name="reservoir_water_load_shedding",
                resource="reservoir_water",
                max_capacity=0.0,
            )
        )

        # ── Demand-side flexibility (EV load shifting) ──────────────
        if flex_demand_fraction > 0.0:
            # Extract hourly demand values from the polars DataFrame
            if "demand" in electricity_demand_pl.columns:
                demand_vals = electricity_demand_pl["demand"].to_list()
            else:
                # Fallback: take first numeric column that is not hour/year_op
                numeric_cols = [
                    c for c in electricity_demand_pl.columns
                    if c not in ("hour", "year_op")
                ]
                demand_vals = electricity_demand_pl[numeric_cols[0]].to_list()

            n_hours = len(demand_vals)

            # Build hourly profiles for the FlexibleDemand component
            flex_demand_profile = [d * flex_demand_fraction for d in demand_vals]
            flex_max_profile = [d * flex_max_multiplier for d in flex_demand_profile]
            flex_min_profile = [d * flex_min_multiplier for d in flex_demand_profile]

            hours = list(range(n_hours))
            year_ops = [model_year] * n_hours

            flex_demand_df = pl.DataFrame({
                "hour": hours, "year_op": year_ops,
                "demand": flex_demand_profile,
            })
            flex_max_df = pl.DataFrame({
                "hour": hours, "year_op": year_ops,
                "max_demand": flex_max_profile,
            })
            flex_min_df = pl.DataFrame({
                "hour": hours, "year_op": year_ops,
                "min_demand": flex_min_profile,
            })

            area.add_component(
                FlexibleDemand(
                    name=f"ev_flexibility_{country_code}",
                    resource="electricity",
                    demand=flex_demand_df,
                    conservation_hrs=flex_conservation_hrs,
                    ramp_up=flex_ramp_up,
                    ramp_down=flex_ramp_down,
                    max_demand=flex_max_df,
                    min_demand=flex_min_df,
                    variable_cost=flex_variable_cost,
                )
            )
            logger.info(
                "  %s: FlexibleDemand added — %.1f%% of demand, conservation=%dh",
                country_code, flex_demand_fraction * 100, flex_conservation_hrs,
            )


# =========================================================
# PUBLIC API — SINGLE COUNTRY
# =========================================================
def create_model_from_clever(
    country_code: str,
    reference_year_weather: int,
    model_year: int,
    clever_capacity_df: pd.DataFrame,
    clever_load_factor_df: pd.DataFrame,
    clever_non_enr_df: pd.DataFrame,
    electricity_demand_pl: pl.DataFrame,
    eoles_costs: dict,
    add_imports: bool = False,
    add_hydro: bool = True,
    flex_demand_fraction: float = 0.0,
    flex_conservation_hrs: int = 0,
    flex_max_multiplier: float = 1.0,
    flex_min_multiplier: float = 1.0,
    flex_ramp_up: float = np.nan,
    flex_ramp_down: float = np.nan,
    flex_variable_cost: float = np.nan,
) -> EnergyModel:
    logger.info(
        "Creating CLEVER-calibrated pommes_craft model for %s, weather ref=%s, model_year=%s",
        country_code,
        reference_year_weather,
        model_year,
    )

    area_code = normalize_country_code(country_code)

    energy_model = _create_empty_energy_model(
        name=f"model_{area_code}_clever",
        model_year=model_year,
    )
    _initialize_model_structure(energy_model, model_year)

    with energy_model.context():
        area = Area(area_code)

    _add_country_components(
        energy_model=energy_model,
        area=area,
        country_code=country_code,
        reference_year_weather=reference_year_weather,
        model_year=model_year,
        clever_capacity_df=clever_capacity_df,
        clever_load_factor_df=clever_load_factor_df,
        clever_non_enr_df=clever_non_enr_df,
        electricity_demand_pl=electricity_demand_pl,
        eoles_costs=eoles_costs,
        add_hydro=add_hydro,
        flex_demand_fraction=flex_demand_fraction,
        flex_conservation_hrs=flex_conservation_hrs,
        flex_max_multiplier=flex_max_multiplier,
        flex_min_multiplier=flex_min_multiplier,
        flex_ramp_up=flex_ramp_up,
        flex_ramp_down=flex_ramp_down,
        flex_variable_cost=flex_variable_cost,
    )

    if add_imports:
        logger.warning(
            "add_imports=True ignored in mono-country create_model_from_clever. "
            "Use create_multi_country_model_from_clever for interconnections."
        )

    return energy_model


# =========================================================
# PUBLIC API — MULTI COUNTRY
# =========================================================
def create_multi_country_model_from_clever(
    country_codes: list[str],
    reference_year_weather: int,
    model_year: int,
    clever_capacity_df: pd.DataFrame,
    clever_load_factor_df: pd.DataFrame,
    clever_non_enr_df: pd.DataFrame,
    electricity_demand_by_country: dict[str, pl.DataFrame],
    eoles_costs: dict,
    interconnections: Optional[dict[tuple[str, str], dict]] = None,
    add_interconnections: bool = True,
    add_hydro: bool = True,
    flex_demand_fraction: float = 0.0,
    flex_conservation_hrs: int = 0,
    flex_max_multiplier: float = 1.0,
    flex_min_multiplier: float = 1.0,
    flex_ramp_up: float = np.nan,
    flex_ramp_down: float = np.nan,
    flex_variable_cost: float = np.nan,
    include_hydrogen: bool = False,
) -> EnergyModel:
    countries = [normalize_country_code(c) for c in country_codes]

    logger.info(
        "Creating MULTI-country CLEVER-calibrated model for %s | weather ref=%s | model_year=%s",
        countries,
        reference_year_weather,
        model_year,
    )

    energy_model = _create_empty_energy_model(
        name="model_multi_clever",
        model_year=model_year,
        include_hydrogen=include_hydrogen,
    )
    _initialize_model_structure(energy_model, model_year)

    areas: dict[str, Area] = {}
    skipped_countries: list[str] = []

    for raw_country in country_codes:
        normalized_country = normalize_country_code(raw_country)

        # ── Check demand availability before creating the Area ──────
        demand_pl = electricity_demand_by_country.get(raw_country)
        if demand_pl is None:
            demand_pl = electricity_demand_by_country.get(normalized_country)
        if demand_pl is None:
            logger.warning(
                "SKIP %s — no hourly demand data available. "
                "The country will be excluded from the optimisation.",
                normalized_country,
            )
            skipped_countries.append(normalized_country)
            continue

        # ── Try to build the country; skip gracefully on failure ────
        try:
            with energy_model.context():
                if normalized_country not in areas:
                    areas[normalized_country] = Area(normalized_country)

            _add_country_components(
                energy_model=energy_model,
                area=areas[normalized_country],
                country_code=raw_country,
                reference_year_weather=reference_year_weather,
                model_year=model_year,
                clever_capacity_df=clever_capacity_df,
                clever_load_factor_df=clever_load_factor_df,
                clever_non_enr_df=clever_non_enr_df,
                electricity_demand_pl=demand_pl,
                eoles_costs=eoles_costs,
                add_hydro=add_hydro,
                flex_demand_fraction=flex_demand_fraction,
                flex_conservation_hrs=flex_conservation_hrs,
                flex_max_multiplier=flex_max_multiplier,
                flex_min_multiplier=flex_min_multiplier,
                flex_ramp_up=flex_ramp_up,
                flex_ramp_down=flex_ramp_down,
                flex_variable_cost=flex_variable_cost,
            )
            logger.info("Successfully built country %s", normalized_country)

        except Exception as exc:
            logger.error(
                "SKIP %s — failed to build country components: %s. "
                "The country will be excluded from the optimisation.",
                normalized_country,
                exc,
            )
            # Remove the Area from the dict so interconnections ignore it
            areas.pop(normalized_country, None)
            skipped_countries.append(normalized_country)
            continue

    if skipped_countries:
        logger.warning(
            "Countries skipped (%d): %s",
            len(skipped_countries),
            ", ".join(sorted(skipped_countries)),
        )

    if not areas:
        raise RuntimeError(
            "All countries were skipped — no areas in the model. "
            "Check CLEVER data availability and demand profiles."
        )

    # ── Interconnections (only between successfully-built areas) ────
    resolved_interconnections: Optional[dict[tuple[str, str], dict]] = None
    if add_interconnections:
        resolved_interconnections = interconnections or MANUAL_INTERCONNECTIONS

    if resolved_interconnections:
        add_manual_interconnections(
            energy_model=energy_model,
            areas=areas,
            interconnections=resolved_interconnections,
        )

        # NOTE: H₂ pipelines are NOT added here — the notebook adds them
        # separately via supplyforge.add_h2_interconnections() which uses
        # H2_ADJACENCY, H2_PIPELINE_COSTS, and the correct invest_cost.
        # Adding them here too would create duplicate TransportTechnology
        # entries with name="h2_pipeline", causing a non-unique MultiIndex
        # crash in xarray during build_input_parameters().

    # ── MENA Variant B: per-country Areas + per-route H₂ pipelines ──
    # No-op when no _menaOptim* suffix is active. The pipeline names use the
    # "mena_h2_pipeline_{src}_{dst}" prefix so they don't collide with the
    # intra-EU "h2_pipeline" components added by supplyforge.
    from pommes_eur.model.techs.mena_imports import add_mena_to_model
    add_mena_to_model(energy_model=energy_model, areas=areas, eoles_costs=eoles_costs)

    # Log build summary
    logger.info("=" * 72)
    logger.info("MODEL BUILD SUMMARY")
    logger.info("=" * 72)
    built_countries = sorted(areas.keys())
    logger.info(
        "Built: %s (%d) | Skipped: %s (%d) | Weather ref: %d | Model year: %d | Hydro: %s | Interco: %s",
        built_countries, len(built_countries),
        sorted(skipped_countries), len(skipped_countries),
        reference_year_weather, model_year, add_hydro, add_interconnections,
    )
    for country in built_countries:
        area = areas[country]
        components = getattr(area, "components", getattr(area, "_components", {}))
        if isinstance(components, dict):
            comp_names = sorted(components.keys())
        elif isinstance(components, list):
            comp_names = sorted(getattr(c, "name", str(c)) for c in components)
        else:
            comp_names = ["(unknown structure)"]
        logger.info("  %s: %d components — %s", country, len(comp_names), ", ".join(comp_names))
    logger.info("=" * 72)

    return energy_model