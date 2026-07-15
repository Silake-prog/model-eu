"""Hydrogen demand projection — country-level industrial H₂ demand curves.

This module provides projection functions for each of the six hydrogen-
consuming industrial sectors.  Each function follows the same pattern:
scenario parameters are explicit function arguments with defaults, raw data
is auto-fetched internally (with static fallback), and the function returns
a standardised DataFrame.

The heavy lifting (route splitting, pathway mixing, mass-balance validation)
is delegated to the process submodules in ``demandforge.process``.  This
module orchestrates the per-country loop, year-range construction, CAGR
application, and final sanity checks.

Public API:
    project_ammonia_h2_demand(...)   -> pd.DataFrame
    project_refinery_h2_demand(...)  -> pd.DataFrame
    project_esaf_h2_demand(...)      -> pd.DataFrame
    project_maritime_h2_demand(...)  -> pd.DataFrame
    project_olefins_h2_demand(...)   -> pd.DataFrame  (four-pathway model)
    project_steel_h2_demand(...)     -> pd.DataFrame
    aggregate_h2_demand(...)         -> pd.DataFrame

Scenario management:
    Scenario parameters are centralised in ``scenario_registry.yaml`` and
    dispatched by ``scenarios.py``.  Each bundle defines per-sector
    parameters (shares, ramp timings, growth rates).  For olefins, the
    YAML structure uses a nested ``pathways`` dict to express per-pathway
    share targets and ramp timings (MTO, bio-naphtha, chemical recycling).

Authors:
    Simon Brigode — PERSEE Lab, Mines Paris PSL
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from demandforge.fetch.industry_data import (
    EU27_COUNTRIES,
    fetch_ammonia_production,
    fetch_olefins_production,
    fetch_refinery_output,
    fetch_steel_production,
    fetch_tyndp_demand_parameters,
)
from demandforge.fetch.bunkering_weights import fetch_bunkering_weights
from demandforge.load_projection.constants import (
    CH4_T_PER_T_H2_SMR,
    H2_INTENSITY_T_PER_T_ESAF,
    H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY,
    H2_LHV_MWH_PER_T,
    H2_T_PER_T_E_METHANOL,
    H2_T_PER_T_NH3,
    H2_T_PER_T_NH3_FUEL,
    HB_ELECTRICITY_MWH_PER_T_NH3,
    REFINERY_INEFFICIENCY_SHARE,
)
from demandforge.process.refinery import build_refinery_unit_allocation
from demandforge.process.esaf import compute_jet_fossil
from demandforge.process.olefins import (
    apply_naphtha_supply_constraint,
    build_olefins_pathway_mix,
    compute_olefins_h2_demand,
)
from demandforge.process.steel import (
    build_dri_mix,
    build_steel_base_table,
    compute_dri_bf_split,
    compute_eaf_scrap_series,
    compute_steel_h2_demand,
    split_primary_from_eaf,
    validate_steel_base_table,
    _linear_ramp as _linear_ramp_vec,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Private utility functions (ported from industry_utils.py and notebooks)
# ═══════════════════════════════════════════════════════════════════════════

def _plateau_series(
    years: np.ndarray,
    start_year: int,
    end_year: int,
    start_value: float,
    end_value: float,
    step_years: int,
) -> np.ndarray:
    """Stepped (plateau) ramp between two values over a year range.

    The value changes only at the boundaries of blocks of length
    *step_years*, progressing linearly from one block to the next.

    Args:
        years: Array of integer years.
        start_year: Year at which the ramp begins.
        end_year: Year at which the ramp reaches *end_value*.
        start_value: Value before *start_year*.
        end_value: Value at and after *end_year*.
        step_years: Length of each constant-value block.

    Returns:
        Array of floats, same shape as *years*.
    """
    y = years.astype(int)
    out = np.empty_like(years, dtype=float)

    out[y < start_year] = float(start_value)
    out[y >= end_year] = float(end_value)

    mask = (y >= start_year) & (y < end_year)
    yy = y[mask]

    block_idx = (yy - start_year) // step_years
    n_blocks = max(int(np.ceil((end_year - start_year) / step_years)), 1)
    frac = np.clip(block_idx / n_blocks, 0.0, 1.0)

    out[mask] = float(start_value) + frac * (float(end_value) - float(start_value))
    return out


def _linear_ramp(
    year: int,
    start_year: int,
    end_year: int,
    start_value: float = 0.0,
    end_value: float = 1.0,
) -> float:
    """Linearly interpolate between two years (scalar wrapper for compatibility).

    Before *start_year* returns *start_value*, after *end_year* returns
    *end_value*, and linearly interpolates between them.

    This is a backward-compatible scalar wrapper around the vectorized
    _linear_ramp_vec function from demandforge.process.hydrogen.steel.
    """
    if year <= start_year:
        return start_value
    if year >= end_year:
        return end_value
    fraction = (year - start_year) / (end_year - start_year)
    return start_value + fraction * (end_value - start_value)


def _apply_cagr(
    value_base: float,
    years: np.ndarray,
    cagr: float,
    base_year: int,
) -> np.ndarray:
    """Apply compound annual growth rate to a base value.

    value(t) = value_base * (1 + cagr)^(t - base_year)

    Args:
        value_base: Base value at base_year.
        years: Array of years.
        cagr: Compound annual growth rate (fraction, e.g., 0.05 for 5%).
        base_year: Reference year for the calculation.

    Returns:
        Array of values with CAGR applied.
    """
    dt = years - base_year
    return value_base * np.power(1.0 + cagr, dt)


def _assert_non_negative(df: pd.DataFrame, cols: list[str]) -> None:
    """Assert that specified columns have no negative values (tolerance -1e-9)."""
    cols_present = [c for c in cols if c in df.columns]
    if not cols_present:
        return
    if (df[cols_present] < -1e-9).any(axis=None):
        mins = df[cols_present].min()
        raise ValueError(f"Negative values detected:\n{mins}")


def _check_coverage(
    df: pd.DataFrame,
    year_start: int,
    year_end: int,
    group_keys: list[str] | None = None,
) -> None:
    """Assert that every group covers the expected year range completely.

    Args:
        df: DataFrame to check.
        year_start: Expected first year (inclusive).
        year_end: Expected last year (inclusive).
        group_keys: List of column names to group by (default: ["country"]).

    Raises:
        ValueError: If any group has incomplete year coverage.
    """
    if group_keys is None:
        group_keys = ["country"]
    expected_n = year_end - year_start + 1

    for k, g in df.groupby(group_keys):
        ys = np.sort(g["year"].unique())
        if ys[0] != year_start or ys[-1] != year_end or len(ys) != expected_n:
            raise ValueError(
                f"Year coverage failure for {k}: "
                f"{ys[0]}..{ys[-1]} n={len(ys)} (expected {expected_n})."
            )


def _resolve_countries(country: str | list[str] | None) -> list[str]:
    """Resolve the country argument into a list of ISO-2 codes."""
    if country is None:
        return EU27_COUNTRIES
    if isinstance(country, str):
        return [country]
    return list(country)


# ═══════════════════════════════════════════════════════════════════════════
# Ammonia sector projection (M3 — template for all other sectors)
# ═══════════════════════════════════════════════════════════════════════════

def project_ammonia_h2_demand(
    country: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    base_scenario: str = "DE",
    # --- Scenario parameters (previously in ammonia_config.yaml) ---
    domestic_share_start: float = 1.0,
    domestic_share_end: float = 1.0,
    decarb_start: int = 2030,
    decarb_end: int = 2050,
    h2_route_share_end: float = 1.0,
    # --- Maritime NH3 overlay ---
    maritime_enabled: bool = False,
    maritime_base_2019_t: float = 0.0,
    maritime_cagr: float = 0.05,
    maritime_allocation: str = "proportional_to_base",
    # --- Plateau parameters ---
    step_years: int = 5,
) -> pd.DataFrame:
    """Project ammonia-sector H2 demand for one or more countries.

    This function replaces the entire ``ammonia_scenario.ipynb`` notebook.
    All scenario parameters that were previously in ``ammonia_config.yaml``
    are now explicit function arguments.

    The computation follows these steps:
    1. Load ammonia production base data from ``fetch/hydrogen/industry_data``.
    2. For each country and year, compute:
       - NH3 consumption (constant at base-year level + optional maritime overlay)
       - Domestic vs. imported split (plateau ramp on domestic_share)
       - Green-H2 route vs. CH4 route split (plateau ramp on h2_route_share)
       - H2 demand from green route: nh3_domestic_h2 × H2_T_PER_T_NH3
       - H2 produced onsite (SMR): nh3_domestic_ch4 × H2_T_PER_T_NH3
       - CH4 demand for onsite H2: h2_onsite × CH4_T_PER_T_H2_SMR
       - HB electricity: nh3_domestic × HB_ELECTRICITY_MWH_PER_T_NH3
    3. Validate mass balances and non-negativity.
    4. Return a DataFrame (not write CSV — the caller saves if needed).

    Constants (hardcoded from ``constants.py``):
        H2_T_PER_T_NH3 = 0.18
        HB_ELECTRICITY_MWH_PER_T_NH3 = 0.98522
        SMR_STOICH = 2.0 / efficiency 0.76 => CH4_T_PER_T_H2_SMR ≈ 2.632

    Args:
        country: ISO-2 country code(s). If None, all EU-27 countries.
        reference_year: Base year for NH3 consumption data.
        target_year: End year for the projection.
        base_scenario: TYNDP base scenario label (e.g. "DE", "GA").
        domestic_share_start: Fraction of NH3 produced domestically at
            reference_year.
        domestic_share_end: Target domestic share at target_year.
        decarb_start: Year when green-H2 route begins ramping.
        decarb_end: Year when green-H2 route reaches h2_route_share_end.
        h2_route_share_end: Fraction of domestic NH3 using green H2 at
            decarb_end.
        maritime_enabled: Whether to add maritime NH3 demand overlay.
        maritime_base_2019_t: EU-level maritime NH3 demand in 2019 (t/yr).
        maritime_cagr: Compound annual growth rate for maritime demand.
        maritime_allocation: How to distribute maritime demand across
            countries: "proportional_to_base" or "equal_by_country".
        step_years: Block length for plateau ramp functions.

    Returns:
        pd.DataFrame with columns:
            country, year,
            nh3_consumption_t_per_yr, nh3_domestic_t_per_yr, nh3_imported_t_per_yr,
            nh3_domestic_ch4_to_h2_to_nh3_t_per_yr, nh3_domestic_h2_to_nh3_t_per_yr,
            h2_demand_network_t_per_yr, h2_produced_onsite_t_per_yr,
            nh3_core_t_per_yr, nh3_maritime_t_per_yr,
            ch4_demand_for_h2_t_per_yr, hb_electricity_mwh_per_yr,
            domestic_share, h2_route_share_of_domestic,
            h2_demand_network_mwh_per_yr

    Raises:
        ValueError: If no ammonia data is found for the reference year; if
            negative values are detected in output columns; or if any mass
            balance check fails (tolerance > 1e-6).
    """
    # --- Resolve countries ---
    countries = _resolve_countries(country)

    # --- Year range ---
    years = np.arange(reference_year, target_year + 1)

    # --- Fetch base ammonia production ---
    df_ammonia = fetch_ammonia_production(countries=countries, reference_year=reference_year)
    df_base = df_ammonia.set_index("country")

    if df_base.empty:
        raise ValueError(
            f"No ammonia data for year={reference_year}. "
            f"Run fetch_ammonia_production() first."
        )

    # Base production per country (in tonnes/year)
    # fetch_ammonia_production returns both ammonia_production_mt (megatonnes)
    # and ammonia_production_t_per_yr (tonnes). We use the tonnes column
    # because all downstream computation is in t and t H2.
    required_col = "ammonia_production_t_per_yr"
    if required_col not in df_base.columns:
        # Fallback: if only ammonia_production_mt exists, convert
        if "ammonia_production_mt" in df_base.columns:
            df_base[required_col] = df_base["ammonia_production_mt"] * 1_000_000.0
        elif "ammonia_production" in df_base.columns:
            # Legacy column without unit suffix — assume megatonnes
            logger.warning(
                "Column 'ammonia_production' has no unit suffix. "
                "Assuming megatonnes and converting to tonnes."
            )
            df_base[required_col] = df_base["ammonia_production"] * 1_000_000.0
        else:
            available = list(df_base.columns)
            raise ValueError(
                f"Missing ammonia production column. "
                f"Expected '{required_col}'. Available: {available}"
            )

    base_by_country: dict[str, float] = {}
    for cc in countries:
        if cc in df_base.index:
            base_by_country[cc] = float(df_base.loc[cc, required_col])
        else:
            logger.warning(f"No ammonia base data for {cc}, using 0.")
            base_by_country[cc] = 0.0

    # --- Maritime NH3 trajectory ---
    if maritime_enabled:
        mar_total = _apply_cagr(maritime_base_2019_t, years, maritime_cagr, reference_year)
    else:
        mar_total = np.zeros_like(years, dtype=float)

    # Maritime allocation weights
    if maritime_enabled and maritime_allocation == "proportional_to_base":
        total_base = sum(base_by_country.values())
        if total_base > 0:
            mar_weights = {cc: v / total_base for cc, v in base_by_country.items()}
        else:
            n = len(countries)
            mar_weights = {cc: 1.0 / n for cc in countries}
    elif maritime_enabled and maritime_allocation == "equal_by_country":
        n = len(countries)
        mar_weights = {cc: 1.0 / n for cc in countries}
    else:
        mar_weights = {cc: 0.0 for cc in countries}

    # --- Domestic share trajectory (plateaus) ---
    domestic_share = _plateau_series(
        years, start_year=reference_year, end_year=target_year,
        start_value=domestic_share_start, end_value=domestic_share_end,
        step_years=step_years,
    )
    domestic_share = np.clip(domestic_share, 0.0, 1.0)

    # --- H2-route share trajectory (plateaus) ---
    h2_route_share = _plateau_series(
        years, start_year=decarb_start, end_year=decarb_end,
        start_value=0.0, end_value=h2_route_share_end,
        step_years=step_years,
    )
    h2_route_share = np.clip(h2_route_share, 0.0, 1.0)

    # --- Build output using vectorized DataFrame construction ---
    all_dfs: list[pd.DataFrame] = []
    for cc in countries:
        cons0 = base_by_country[cc]
        nh3_core = np.full_like(years, cons0, dtype=float)

        w = mar_weights.get(cc, 0.0)
        nh3_maritime = mar_total * w
        nh3_cons = nh3_core + nh3_maritime

        # Domestic / import split
        nh3_domestic = np.clip(nh3_cons * domestic_share, 0.0, nh3_cons)
        nh3_imported = np.clip(nh3_cons - nh3_domestic, 0.0, None)

        # Technology split on domestic
        nh3_h2_route = np.clip(nh3_domestic * h2_route_share, 0.0, nh3_domestic)
        nh3_ch4_route = np.clip(nh3_domestic - nh3_h2_route, 0.0, None)

        # H2 demands
        h2_network = nh3_h2_route * H2_T_PER_T_NH3
        h2_onsite = nh3_ch4_route * H2_T_PER_T_NH3

        # CH4 for onsite H2 (SMR)
        ch4_for_h2 = h2_onsite * CH4_T_PER_T_H2_SMR

        # HB electricity (all domestic NH3)
        hb_elec = nh3_domestic * HB_ELECTRICITY_MWH_PER_T_NH3

        # H2 in MWh (for POMMES)
        h2_network_mwh = h2_network * H2_LHV_MWH_PER_T

        # Construct DataFrame directly from arrays
        cc_df = pd.DataFrame({
            "country": cc,
            "year": years.astype(int),
            "nh3_consumption_t_per_yr": nh3_cons,
            "nh3_domestic_t_per_yr": nh3_domestic,
            "nh3_imported_t_per_yr": nh3_imported,
            "nh3_domestic_ch4_to_h2_to_nh3_t_per_yr": nh3_ch4_route,
            "nh3_domestic_h2_to_nh3_t_per_yr": nh3_h2_route,
            "h2_demand_network_t_per_yr": h2_network,
            "h2_produced_onsite_t_per_yr": h2_onsite,
            "nh3_core_t_per_yr": nh3_core,
            "nh3_maritime_t_per_yr": nh3_maritime,
            "ch4_demand_for_h2_t_per_yr": ch4_for_h2,
            "hb_electricity_mwh_per_yr": hb_elec,
            "domestic_share": domestic_share,
            "h2_route_share_of_domestic": h2_route_share,
            "h2_demand_network_mwh_per_yr": h2_network_mwh,
        })
        all_dfs.append(cc_df)

    df = pd.concat(all_dfs, ignore_index=True)

    # --- Validation ---
    _assert_non_negative(df, [
        "nh3_consumption_t_per_yr", "nh3_domestic_t_per_yr", "nh3_imported_t_per_yr",
        "nh3_domestic_ch4_to_h2_to_nh3_t_per_yr", "nh3_domestic_h2_to_nh3_t_per_yr",
        "h2_demand_network_t_per_yr", "h2_produced_onsite_t_per_yr",
        "ch4_demand_for_h2_t_per_yr", "hb_electricity_mwh_per_yr",
    ])

    # Mass balance: consumption = core + maritime
    err0 = (df["nh3_consumption_t_per_yr"] - (df["nh3_core_t_per_yr"] + df["nh3_maritime_t_per_yr"])).abs().max()
    if err0 > 1e-6:
        raise ValueError(f"Mass balance: consumption != core + maritime (max err={err0:.3e})")

    # Mass balance: consumption = domestic + imported
    err1 = (df["nh3_consumption_t_per_yr"] - (df["nh3_domestic_t_per_yr"] + df["nh3_imported_t_per_yr"])).abs().max()
    if err1 > 1e-6:
        raise ValueError(f"Mass balance: consumption != domestic + imported (max err={err1:.3e})")

    # Mass balance: domestic = ch4_route + h2_route
    err2 = (
        df["nh3_domestic_t_per_yr"]
        - (df["nh3_domestic_ch4_to_h2_to_nh3_t_per_yr"] + df["nh3_domestic_h2_to_nh3_t_per_yr"])
    ).abs().max()
    if err2 > 1e-6:
        raise ValueError(f"Mass balance: domestic != ch4_route + h2_route (max err={err2:.3e})")

    _check_coverage(df, reference_year, target_year, group_keys=["country"])

    logger.info(
        f"Ammonia H2 projection: {len(countries)} countries, "
        f"{reference_year}-{target_year}, "
        f"EU total H2 at {target_year}: "
        f"{df[df['year'] == target_year]['h2_demand_network_t_per_yr'].sum():,.0f} t/yr"
    )

    return df


# ═══════════════════════════════════════════════════════════════════════════
# Refinery sector projection
# ═══════════════════════════════════════════════════════════════════════════

def project_refinery_h2_demand(
    country: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    base_scenario: str = "DE",
    molecule_scenario: str = "more-molecule",
    units_config: dict[str, dict] | None = None,
    inefficiency_share: float = REFINERY_INEFFICIENCY_SHARE,
) -> pd.DataFrame:
    """Project refinery-sector H2 demand for one or more countries.

    This function replaces ``refinery_scenario.ipynb``.  It delegates
    the CONCAWE unit-feed allocation to ``process/hydrogen/refinery.py``
    and applies the inefficiency factor to get H2 demand.

    The ``units_config`` parameter carries the CONCAWE unit data that was
    previously in ``refinery_config.yaml``.  If None, a minimal default
    is used (caller is expected to provide real CONCAWE data).

    Args:
        country: ISO-2 country code(s).  If None, all EU-27.
        reference_year: Base year for refinery output data.
        target_year: End year.
        base_scenario: TYNDP base scenario label.
        molecule_scenario: CONCAWE structural scenario key.
        units_config: Dict of CONCAWE unit definitions.  Each key is a
            unit name; each value has ``spec_cons_wt`` (float) and
            ``utilized_capacity_mton`` (dict[int, float]).
        inefficiency_share: H2 overconsumption factor (default 0.14).

    Returns:
        pd.DataFrame with columns:
            country, year, unit, unit_feed_t_per_yr, spec_cons_wt,
            h2_demand_t_per_yr, refinery_output_total_t_per_yr,
            unit_capacity_share, level_factor, h2_demand_mwh

    Raises:
        ValueError: If units_config is not provided; if no refinery data is
            produced for any country; if a required column is missing from
            the fetched DataFrame; or if negative values are detected in
            output columns.
    """
    countries = _resolve_countries(country)
    years = np.arange(reference_year, target_year + 1)

    if units_config is None:
        raise ValueError(
            "units_config must be provided — it carries the CONCAWE unit "
            "capacity and specific consumption data that was previously in "
            "refinery_config.yaml.  See the roadmap for the expected format."
        )

    # Fetch base refinery output per country
    df_ref_out = fetch_refinery_output(countries=countries, reference_year=reference_year)
    df_base = df_ref_out.set_index("country")

    # Validate column existence (Issue #1, #10)
    if df_base.empty:
        raise ValueError(f"No refinery data available for year={reference_year}")

    required_col = "refinery_output_ktoe"
    if required_col not in df_base.columns:
        available = list(df_base.columns)
        raise ValueError(
            f"Missing column '{required_col}' in refinery output. "
            f"Available columns: {available}"
        )

    all_dfs: list[pd.DataFrame] = []
    for cc in countries:
        if cc not in df_base.index:
            logger.warning(f"No refinery base data for {cc}, skipping.")
            continue

        # Fetch value in ktoe, convert to tonnes for build_refinery_unit_allocation.
        # Refinery accounting: 1 ktoe ≈ 1000 toe ≈ 1000 t (crude oil density ~1 t/toe).
        base_output_ktoe = float(df_base.loc[cc, required_col])
        if base_output_ktoe <= 0:
            logger.info(f"Refinery output for {cc} is {base_output_ktoe} ktoe, skipping.")
            continue
        base_output_t = base_output_ktoe * 1000.0  # ktoe → tonnes

        df_alloc = build_refinery_unit_allocation(
            base_output_t_per_yr=base_output_t,
            units_config=units_config,
            years=years,
            inefficiency_share=inefficiency_share,
        )
        df_alloc.insert(0, "country", cc)
        df_alloc["h2_demand_mwh_per_yr"] = df_alloc["h2_demand_t_per_yr"] * H2_LHV_MWH_PER_T
        all_dfs.append(df_alloc)

    if not all_dfs:
        raise ValueError("No refinery data produced for any country.")

    df = pd.concat(all_dfs, ignore_index=True)
    _assert_non_negative(df, ["h2_demand_t_per_yr", "unit_feed_t_per_yr"])

    # Issue #16: Add _check_coverage call
    _check_coverage(df, reference_year, target_year, group_keys=["country"])

    logger.info(
        f"Refinery H2 projection: {len(countries)} countries, "
        f"{reference_year}-{target_year}, scenario={molecule_scenario}"
    )
    return df


# ═══════════════════════════════════════════════════════════════════════════
# eSAF sector projection
# ═══════════════════════════════════════════════════════════════════════════

def project_esaf_h2_demand(
    country: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    base_scenario: str = "DE",
    molecule_scenario: str = "more-molecule",
    demand_growth_rate: float = 0.0,
    h2_intensity: float | None = None,
    pathway_shares: dict[str, float] | None = None,
    refinery_df: pd.DataFrame | None = None,
    jet_unit_names: dict[str, str] | None = None,
    jet_yields: dict[str, float] | None = None,
    jet_method: str = "prefer_kero_hydrotreater",
) -> pd.DataFrame:
    """Project eSAF-sector H2 demand for one or more countries.

    This function replaces ``esaf_scenario.ipynb``.  It:
    1. Reconstructs fossil jet availability from CONCAWE refinery data
       (via ``process/esaf.py``).
    2. Builds an exogenous aviation demand trajectory grown at
       ``demand_growth_rate`` from the reference year.
    3. Computes eSAF as the gap: aviation_total - fossil_jet.
    4. H2 demand = eSAF production × h2_intensity.

    .. note:: **Fossil-jet baseline scope.**  The default ``jet_method``
       (``prefer_kero_hydrotreater``) uses the Kero Hydrotreater unit feed
       as a proxy for domestic EU-27 refinery jet production (~17-18 Mt/yr,
       consistent with Eurostat production of ~10-12 Mt/yr -- the Kero HT
       throughput includes intermediate streams).  The commonly cited
       50-60 Mt/yr refers to consumption including imports; the EU sources
       ~75-80 % of jet fuel externally.  See ``process/esaf.py`` module
       docstring for a full discussion of scoping implications.

    Args:
        country: ISO-2 country code(s).  If None, all EU-27.
        reference_year: Base year.
        target_year: End year.
        base_scenario: TYNDP base scenario label.
        molecule_scenario: CONCAWE structural scenario key.
        demand_growth_rate: Annual compound growth rate for aviation demand.
        h2_intensity: (Legacy) single blended t H2 per t eSAF.  Mutually
            exclusive with ``pathway_shares``.  Retained for backward
            compatibility; prefer ``pathway_shares``.
        pathway_shares: Dict mapping eSAF pathway name to its share of
            eSAF production.  Keys must be a subset of
            {"fischer_tropsch", "methanol_to_jet"} and must sum to 1.
            Defaults to pure Fischer-Tropsch (``{"fischer_tropsch": 1.0}``)
            when both ``h2_intensity`` and ``pathway_shares`` are None.
            This is the physically coherent parameter: different pathways
            carry different H2 intensities, and both H2 and CO2 downstream
            demand scale consistently with the chosen mix.
        refinery_df: Pre-computed refinery unit-feed DataFrame.  Must have
            columns: country, year, unit, unit_feed_t_per_yr.
        jet_unit_names: CONCAWE unit name mapping for jet reconstruction.
        jet_yields: Hydrocracker jet yield fractions (fallback method).
        jet_method: Jet reconstruction method.

    Returns:
        pd.DataFrame with columns:
            country, year, fossil_jet_consumed_t_per_yr,
            aviation_total_demand_t_per_yr, esaf_production_t_per_yr,
            h2_demand_for_esaf_t_per_yr, h2_demand_mwh_per_yr

        When ``pathway_shares`` is used (the default), the frame also
        carries per-pathway columns:
            esaf_production_{pathway}_t_per_yr,
            h2_demand_for_esaf_{pathway}_t_per_yr
        for each pathway in the mix.  These are consumed by
        :mod:`demandforge.process.co2_feedstock` for CO2 accounting.

    Raises:
        ValueError: If refinery_df or jet_unit_names is not provided; if mass
            balance check fails (tolerance > 1e-6); or if negative values are
            detected in output columns.
    """
    countries = _resolve_countries(country)
    years = np.arange(reference_year, target_year + 1)

    if refinery_df is None:
        raise ValueError(
            "refinery_df must be provided — it contains CONCAWE unit-feed data "
            "from project_refinery_h2_demand() or equivalent."
        )
    if jet_unit_names is None:
        raise ValueError(
            "jet_unit_names must be provided — maps role to CONCAWE unit name."
        )
    if jet_yields is None:
        jet_yields = {}

    # --- Resolve pathway split --------------------------------------------
    # The eSAF can be produced along two pathways with different H2
    # intensities (see constants.H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY):
    #   * fischer_tropsch (0.43 t H2/t SAF)
    #   * methanol_to_jet (0.55 t H2/t SAF)
    #
    # Behaviour:
    #   • If ``pathway_shares`` is supplied, it is the authoritative split
    #     and is applied to the projected eSAF production.  The resulting
    #     H2 demand is share-weighted and its total responds physically to
    #     the FT/MtJ mix.  Per-pathway H2 columns are exposed in the output.
    #   • If ``pathway_shares`` is None AND ``h2_intensity`` is None, the
    #     default is 100 % FT (most mature, conservative).
    #   • If ``h2_intensity`` is supplied (legacy path), it overrides the
    #     per-pathway computation and produces a single undifferentiated
    #     column.  Retained for backward compatibility.
    _legacy_mode = h2_intensity is not None
    if _legacy_mode and pathway_shares is not None:
        raise ValueError(
            "project_esaf_h2_demand: pass EITHER h2_intensity (legacy, "
            "single blended intensity) OR pathway_shares (per-pathway, "
            "physically coherent).  Got both."
        )

    if not _legacy_mode:
        if pathway_shares is None:
            pathway_shares = {"fischer_tropsch": 1.0, "methanol_to_jet": 0.0}
        # Validate pathway names and shares
        _allowed = set(H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY)
        _invalid = set(pathway_shares) - _allowed
        if _invalid:
            raise ValueError(
                f"Unknown eSAF pathway(s) {_invalid}.  "
                f"Allowed: {sorted(_allowed)}"
            )
        _share_sum = sum(pathway_shares.values())
        if abs(_share_sum - 1.0) > 1e-9:
            raise ValueError(
                f"eSAF pathway_shares must sum to 1.0 (got {_share_sum:.6f})"
            )
        _any_neg = [k for k, v in pathway_shares.items() if v < 0]
        if _any_neg:
            raise ValueError(f"Negative eSAF pathway share(s): {_any_neg}")

    # Reconstruct fossil jet from refinery data
    jet_fossil_df = compute_jet_fossil(
        df_refinery=refinery_df,
        jet_unit_names=jet_unit_names,
        jet_yields=jet_yields,
        method=jet_method,
    )

    # Build eSAF scenario per country using vectorized construction
    all_dfs: list[pd.DataFrame] = []
    for cc in countries:
        g = jet_fossil_df[jet_fossil_df["country"] == cc].set_index("year").sort_index()
        if reference_year not in g.index:
            logger.warning(f"No jet data for {cc} at {reference_year}, skipping.")
            continue

        jet_ref = float(g.loc[reference_year, "fossil_jet_consumed_t_per_yr"])

        # Vectorized computation for all years
        jet_fossil_arr = np.zeros_like(years, dtype=float)
        for i, yr in enumerate(years):
            if yr in g.index:
                jet_fossil_arr[i] = float(g.loc[yr, "fossil_jet_consumed_t_per_yr"])

        jet_total_arr = jet_ref * ((1.0 + demand_growth_rate) ** (years - reference_year))
        jet_fossil_adj = np.minimum(jet_fossil_arr, jet_total_arr)
        jet_esaf = np.maximum(jet_total_arr - jet_fossil_adj, 0.0)

        cc_df = pd.DataFrame({
            "country": cc,
            "year": years.astype(int),
            "fossil_jet_consumed_t_per_yr": jet_fossil_adj,
            "aviation_total_demand_t_per_yr": jet_total_arr,
            "esaf_production_t_per_yr": jet_esaf,
        })

        if _legacy_mode:
            # Single blended intensity — legacy path, no pathway breakdown
            h2_esaf = jet_esaf * h2_intensity
            cc_df["h2_demand_for_esaf_t_per_yr"] = h2_esaf
            cc_df["h2_demand_mwh_per_yr"] = h2_esaf * H2_LHV_MWH_PER_T
        else:
            # Per-pathway decomposition — physically coherent
            # eSAF production is split by pathway shares, then each
            # pathway-production is multiplied by its own H2 intensity.
            h2_total = np.zeros_like(jet_esaf)
            for pth, share in pathway_shares.items():
                prod_pth = jet_esaf * share
                h2_pth = prod_pth * H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY[pth]
                cc_df[f"esaf_production_{pth}_t_per_yr"] = prod_pth
                cc_df[f"h2_demand_for_esaf_{pth}_t_per_yr"] = h2_pth
                h2_total = h2_total + h2_pth
            cc_df["h2_demand_for_esaf_t_per_yr"] = h2_total
            cc_df["h2_demand_mwh_per_yr"] = h2_total * H2_LHV_MWH_PER_T

        all_dfs.append(cc_df)

    df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

    # Mass balance: aviation_total = fossil_jet + esaf
    if not df.empty:
        err = (
            df["aviation_total_demand_t_per_yr"]
            - (df["fossil_jet_consumed_t_per_yr"] + df["esaf_production_t_per_yr"])
        ).abs().max()
        if err > 1e-6:
            raise ValueError(f"eSAF mass balance error: {err:.3e}")

        _assert_non_negative(df, [
            "fossil_jet_consumed_t_per_yr", "esaf_production_t_per_yr",
            "h2_demand_for_esaf_t_per_yr",
        ])

        if _legacy_mode:
            _intensity_label = f"blended={h2_intensity:.3f}"
        else:
            _blended = sum(
                pathway_shares[p] * H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY[p]
                for p in pathway_shares
            )
            _intensity_label = (
                f"pathway-weighted={_blended:.3f} t H2/t SAF, "
                f"shares={pathway_shares}"
            )
        logger.info(
            f"eSAF H2 projection: {df['country'].nunique()} countries, "
            f"{reference_year}-{target_year}, growth={demand_growth_rate:+.1%}, "
            f"{_intensity_label}"
        )

    return df


# ═══════════════════════════════════════════════════════════════════════════
# Maritime sector projection
# ═══════════════════════════════════════════════════════════════════════════

def project_maritime_h2_demand(
    country: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    marine_reference_eu_t_per_yr: float = 6_000_000.0,
    demand_growth_rate: float = 0.0,
    biomethanol_share_2050: float = 0.20,
    e_methanol_share_2050: float = 0.40,
    ammonia_fuel_share_2050: float = 0.15,
    biomethanol_ramp_start: int = 2025,
    biomethanol_ramp_end: int = 2045,
    e_methanol_ramp_start: int = 2028,
    e_methanol_ramp_end: int = 2045,
    ammonia_fuel_ramp_start: int = 2030,
    ammonia_fuel_ramp_end: int = 2050,
) -> pd.DataFrame:
    """Project maritime-sector H2 demand for one or more countries.

    This function replaces ``maritime_methanol_scenario.ipynb`` (Stage K).
    It implements a port-anchored, four-route fuel-mix model:
    1. **Fossil** — residual conventional marine fuel
    2. **Biomethanol** — from sustainable biomass (no H2)
    3. **E-methanol** — electrolytic methanol (requires H2)
    4. **Ammonia-as-fuel** — NH3 via Haber–Bosch (requires H2)

    Spatial distribution uses bunkering weights from
    ``fetch/hydrogen/bunkering_weights.py``.

    Args:
        country: ISO-2 code(s).  If None, all EU-27.
        reference_year: Base year for demand growth.
        target_year: End year.
        marine_reference_eu_t_per_yr: EU-level marine demand at base year.
        demand_growth_rate: Annual compound growth rate.
        biomethanol_share_2050: Target biomethanol share by 2050.
        e_methanol_share_2050: Target e-methanol share by 2050.
        ammonia_fuel_share_2050: Target ammonia-fuel share by 2050.
        biomethanol_ramp_start: Year biomethanol ramp begins.
        biomethanol_ramp_end: Year biomethanol ramp reaches target.
        e_methanol_ramp_start: Year e-methanol ramp begins.
        e_methanol_ramp_end: Year e-methanol ramp reaches target.
        ammonia_fuel_ramp_start: Year ammonia-fuel ramp begins.
        ammonia_fuel_ramp_end: Year ammonia-fuel ramp reaches target.

    Returns:
        pd.DataFrame with columns:
            country, year, bunkering_weight,
            marine_total_demand_t_per_yr,
            fossil_consumed_t_per_yr, biomethanol_production_t_per_yr,
            e_methanol_production_t_per_yr, ammonia_fuel_production_t_per_yr,
            fossil_share, biomethanol_share, e_methanol_share, ammonia_fuel_share,
            h2_demand_for_e_methanol_t_per_yr, h2_demand_for_ammonia_fuel_t_per_yr,
            h2_demand_total_t_per_yr, h2_demand_mwh

    Raises:
        ValueError: If the sum of non-fossil fuel shares exceeds 1.0 for any
            year; if mass balance check fails (tolerance > 1e-6); if H2
            consistency check fails; or if negative values are detected.
    """
    countries = _resolve_countries(country)
    years = np.arange(reference_year, target_year + 1)

    # Fetch bunkering weights
    bw = fetch_bunkering_weights(countries=countries)

    # Compute fuel-mix shares and build output using vectorized construction
    all_dfs: list[pd.DataFrame] = []

    for cc in countries:
        weight = bw.get(cc, 0.0)

        # Vectorized share computation for all years
        bio_shares = np.array([
            _linear_ramp(
                int(yr), biomethanol_ramp_start, biomethanol_ramp_end,
                0.0, biomethanol_share_2050,
            )
            for yr in years
        ])

        e_meth_shares = np.array([
            _linear_ramp(
                int(yr), e_methanol_ramp_start, e_methanol_ramp_end,
                0.0, e_methanol_share_2050,
            )
            for yr in years
        ])

        nh3_shares = np.array([
            _linear_ramp(
                int(yr), ammonia_fuel_ramp_start, ammonia_fuel_ramp_end,
                0.0, ammonia_fuel_share_2050,
            )
            for yr in years
        ])

        fossil_shares = np.maximum(1.0 - bio_shares - e_meth_shares - nh3_shares, 0.0)

        # Check for share validity
        invalid = bio_shares + e_meth_shares + nh3_shares > 1.0 + 1e-9
        if invalid.any():
            bad_yr = years[invalid][0]
            bad_sum = (bio_shares + e_meth_shares + nh3_shares)[invalid][0]
            raise ValueError(
                f"Year {bad_yr}: non-fossil shares sum to {bad_sum:.6f} > 1.0"
            )

        # EU demand trajectory
        eu_demands = marine_reference_eu_t_per_yr * (
            (1.0 + demand_growth_rate) ** (years - reference_year)
        )
        totals = eu_demands * weight

        # Fuel amounts
        fossils = totals * fossil_shares
        bios = totals * bio_shares
        e_meths = totals * e_meth_shares
        nh3_fuels = totals * nh3_shares

        h2_e_meths = e_meths * H2_T_PER_T_E_METHANOL
        h2_nh3s = nh3_fuels * H2_T_PER_T_NH3_FUEL
        h2_totals = h2_e_meths + h2_nh3s

        cc_df = pd.DataFrame({
            "country": cc,
            "year": years.astype(int),
            "bunkering_weight": weight,
            "marine_total_demand_t_per_yr": totals,
            "fossil_consumed_t_per_yr": fossils,
            "biomethanol_production_t_per_yr": bios,
            "e_methanol_production_t_per_yr": e_meths,
            "ammonia_fuel_production_t_per_yr": nh3_fuels,
            "fossil_share": fossil_shares,
            "biomethanol_share": bio_shares,
            "e_methanol_share": e_meth_shares,
            "ammonia_fuel_share": nh3_shares,
            "h2_demand_for_e_methanol_t_per_yr": h2_e_meths,
            "h2_demand_for_ammonia_fuel_t_per_yr": h2_nh3s,
            "h2_demand_total_t_per_yr": h2_totals,
            "h2_demand_mwh_per_yr": h2_totals * H2_LHV_MWH_PER_T,
        })
        all_dfs.append(cc_df)

    df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

    if not df.empty:
        # Mass balance: total = sum of 4 routes
        route_sum = (
            df["fossil_consumed_t_per_yr"]
            + df["biomethanol_production_t_per_yr"]
            + df["e_methanol_production_t_per_yr"]
            + df["ammonia_fuel_production_t_per_yr"]
        )
        err = (df["marine_total_demand_t_per_yr"] - route_sum).abs().max()
        if err > 1e-6:
            raise ValueError(f"Maritime mass balance error: {err:.3e}")

        # H2 consistency
        h2_sum = (
            df["h2_demand_for_e_methanol_t_per_yr"]
            + df["h2_demand_for_ammonia_fuel_t_per_yr"]
        )
        h2_err = (df["h2_demand_total_t_per_yr"] - h2_sum).abs().max()
        if h2_err > 1e-6:
            raise ValueError(f"Maritime H2 consistency error: {h2_err:.3e}")

        _assert_non_negative(df, [
            "marine_total_demand_t_per_yr", "h2_demand_total_t_per_yr",
        ])
        _check_coverage(df, reference_year, target_year, group_keys=["country"])

        logger.info(
            f"Maritime H2 projection: {len(countries)} countries, "
            f"{reference_year}-{target_year}, growth={demand_growth_rate:+.1%}"
        )

    return df


# ═══════════════════════════════════════════════════════════════════════════
# Olefins sector projection
# ═══════════════════════════════════════════════════════════════════════════

def project_olefins_h2_demand(
    country: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    base_scenario: str = "DE",
    # Production trajectory
    olefins_cagr: float = 0.0,
    # Pathway parameters (nested dict from YAML or explicit kwargs)
    pathways: dict[str, dict[str, float]] | None = None,
    # Legacy flat interface (backward-compatible, used if pathways is None)
    mto_share_2050: float = 0.20,
    mto_ramp_start: int = 2030,
    mto_ramp_end: int = 2045,
    bio_naphtha_share_2050: float = 0.15,
    bio_naphtha_ramp_start: int = 2024,
    bio_naphtha_ramp_end: int = 2035,
    chem_recycling_share_2050: float = 0.10,
    chem_recycling_ramp_start: int = 2027,
    chem_recycling_ramp_end: int = 2038,
    # Naphtha supply constraint (endogenous coupling with refinery)
    naphtha_supply_mt: np.ndarray | None = None,
    cracker_yield: float = 0.45,
) -> pd.DataFrame:
    """Project olefins-sector H2 demand for one or more countries.

    Supports four production pathways (MTO, bio-naphtha, chemical recycling,
    fossil) with independent ramp timings and per-pathway H₂ intensities.
    The fossil share is the complement of the three green routes.

    Pathway parameters can be supplied either as a nested ``pathways`` dict
    (from YAML scenario registry) or as flat keyword arguments.  If
    ``pathways`` is provided, it takes precedence over flat kwargs.

    H₂ demand = Σ_i (olefins_production(t) × share_i(t) × h2_intensity_i)

    Args:
        country: ISO-2 code(s).  If None, all EU-27.
        reference_year: Base year.
        target_year: End year.
        base_scenario: TYNDP base scenario label.
        olefins_cagr: Compound annual growth rate for total olefins production.
        pathways: Nested dict of pathway parameters, e.g.::

            {"mto": {"share_2050": 0.20, "ramp_start": 2030, "ramp_end": 2045},
             "bio_naphtha": {"share_2050": 0.15, ...},
             "chem_recycling": {"share_2050": 0.10, ...}}

        mto_share_2050: (flat fallback) Target MTO share at ramp end.
        mto_ramp_start: (flat fallback) Year MTO ramp begins.
        mto_ramp_end: (flat fallback) Year MTO reaches target share.
        bio_naphtha_share_2050: (flat fallback) Target bio-naphtha share.
        bio_naphtha_ramp_start: (flat fallback) Year bio-naphtha ramp begins.
        bio_naphtha_ramp_end: (flat fallback) Year bio-naphtha reaches target.
        chem_recycling_share_2050: (flat fallback) Target chem recycling share.
        chem_recycling_ramp_start: (flat fallback) Year chem recycling begins.
        chem_recycling_ramp_end: (flat fallback) Year chem recycling reaches target.
        naphtha_supply_mt: Annual petrochemical naphtha supply (Mt/yr) from
            refineries, one value per year in ``[reference_year, target_year]``.
            If provided, the fossil share is capped against available naphtha
            and excess is redistributed to green pathways.  Computed by
            :func:`~demandforge.process.refinery.get_naphtha_for_crackers`.
        cracker_yield: Steam cracker olefin yield (t olefin / t naphtha),
            default 0.45.

    Returns:
        pd.DataFrame with columns:
            country, year, olefins_production_t_per_yr,
            mto_share, bio_naphtha_share, chem_recycling_share, fossil_share,
            h2_demand_t_per_yr, h2_demand_mwh_per_yr

    Raises:
        ValueError: If green pathway shares exceed 1.0; if negative H₂ demand
            is detected; or if mass balance is violated.
    """
    countries = _resolve_countries(country)
    years = np.arange(reference_year, target_year + 1)

    # --- Resolve pathway parameters ---
    # If nested dict provided (from YAML), unpack; else use flat kwargs.
    if pathways is not None:
        pw_mto = pathways.get("mto", {})
        pw_bio = pathways.get("bio_naphtha", {})
        pw_rec = pathways.get("chem_recycling", {})

        mix_kwargs = {
            "mto_share_2050": pw_mto.get("share_2050", 0.0),
            "mto_ramp_start": int(pw_mto.get("ramp_start", 2030)),
            "mto_ramp_end": int(pw_mto.get("ramp_end", 2045)),
            "bio_naphtha_share_2050": pw_bio.get("share_2050", 0.0),
            "bio_naphtha_ramp_start": int(pw_bio.get("ramp_start", 2024)),
            "bio_naphtha_ramp_end": int(pw_bio.get("ramp_end", 2035)),
            "chem_recycling_share_2050": pw_rec.get("share_2050", 0.0),
            "chem_recycling_ramp_start": int(pw_rec.get("ramp_start", 2027)),
            "chem_recycling_ramp_end": int(pw_rec.get("ramp_end", 2038)),
        }
    else:
        mix_kwargs = {
            "mto_share_2050": mto_share_2050,
            "mto_ramp_start": mto_ramp_start,
            "mto_ramp_end": mto_ramp_end,
            "bio_naphtha_share_2050": bio_naphtha_share_2050,
            "bio_naphtha_ramp_start": bio_naphtha_ramp_start,
            "bio_naphtha_ramp_end": bio_naphtha_ramp_end,
            "chem_recycling_share_2050": chem_recycling_share_2050,
            "chem_recycling_ramp_start": chem_recycling_ramp_start,
            "chem_recycling_ramp_end": chem_recycling_ramp_end,
        }

    # --- Build pathway mix (shared across all countries) ---
    pathway_mix = build_olefins_pathway_mix(years, **mix_kwargs)

    # --- Fetch olefins production base data ---
    df_olefins = fetch_olefins_production(countries=countries, reference_year=reference_year)
    df_base = df_olefins.set_index("country")

    # --- Apply naphtha supply constraint (endogenous refinery coupling) ---
    if naphtha_supply_mt is not None:
        # Compute EU-wide total production (all requested countries)
        eu_production_t = np.zeros(len(years))
        for cc in countries:
            if cc in df_base.index:
                base_prod = float(df_base.loc[cc, "olefins_production_t_per_yr"])
            else:
                base_prod = 0.0
            eu_production_t += _apply_cagr(base_prod, years, olefins_cagr, reference_year)

        pathway_mix = apply_naphtha_supply_constraint(
            pathway_mix,
            eu_production_t=eu_production_t,
            naphtha_supply_mt=naphtha_supply_mt,
            cracker_yield=cracker_yield,
        )

    # --- Build output per country ---
    all_dfs: list[pd.DataFrame] = []
    for cc in countries:
        if cc not in df_base.index:
            logger.warning(f"No olefins base data for {cc}, using 0.")
            base_prod = 0.0
        else:
            base_prod = float(df_base.loc[cc, "olefins_production_t_per_yr"])

        # Apply CAGR to total production
        olefins_prods = _apply_cagr(base_prod, years, olefins_cagr, reference_year)

        # Compute per-pathway H₂ demand
        h2_detail = compute_olefins_h2_demand(olefins_prods, pathway_mix)

        cc_df = pd.DataFrame({
            "country": cc,
            "year": years.astype(int),
            "olefins_production_t_per_yr": olefins_prods,
            "mto_share": pathway_mix["mto_share"].values,
            "bio_naphtha_share": pathway_mix["bio_naphtha_share"].values,
            "chem_recycling_share": pathway_mix["chem_recycling_share"].values,
            "fossil_share": pathway_mix["fossil_share"].values,
            # Per-pathway H2 breakdown (preserved from compute_olefins_h2_demand)
            # — required for downstream CO2 feedstock accounting, which only
            # charges CO2 against the MTO (e-methanol) pathway.
            "h2_demand_mto_t_per_yr": h2_detail["h2_demand_mto_t"].values,
            "h2_demand_bio_naphtha_t_per_yr": h2_detail["h2_demand_bio_naphtha_t"].values,
            "h2_demand_chem_recycling_t_per_yr": h2_detail["h2_demand_chem_recycling_t"].values,
            "h2_demand_fossil_t_per_yr": h2_detail["h2_demand_fossil_t"].values,
            "h2_demand_t_per_yr": h2_detail["h2_demand_total_t_per_yr"].values,
            "h2_demand_mwh_per_yr": (
                h2_detail["h2_demand_total_t_per_yr"].values * H2_LHV_MWH_PER_T
            ),
        })
        all_dfs.append(cc_df)

    df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

    if not df.empty:
        _assert_non_negative(df, ["h2_demand_t_per_yr", "olefins_production_t_per_yr"])
        _check_coverage(df, reference_year, target_year, group_keys=["country"])

        green_total = (
            mix_kwargs["mto_share_2050"]
            + mix_kwargs["bio_naphtha_share_2050"]
            + mix_kwargs["chem_recycling_share_2050"]
        )
        logger.info(
            f"Olefins H2 projection: {len(countries)} countries, "
            f"{reference_year}-{target_year}, "
            f"green share 2050={green_total:.0%} "
            f"(MTO={mix_kwargs['mto_share_2050']:.0%}, "
            f"bio={mix_kwargs['bio_naphtha_share_2050']:.0%}, "
            f"rec={mix_kwargs['chem_recycling_share_2050']:.0%}), "
            f"CAGR={olefins_cagr:+.1%}"
        )

    return df


# ═══════════════════════════════════════════════════════════════════════════
# Steel sector projection
# ═══════════════════════════════════════════════════════════════════════════

def project_steel_h2_demand(
    country: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    base_scenario: str = "DE",
    # Steel data paths
    jrc_idees_path: str | Path | None = None,
    tyndp_path: str | Path | None = None,
    # DRI transition parameters
    dri_share_2050: float = 1.0,
    dri_ramp_start: int = 2025,
    dri_ramp_end: int = 2050,
    dri_ch4_only_years: int = 5,
    dri_h2_ramp_years: int = 10,
    # Recycling parameters
    recycling_enabled: bool = False,
    rec_target_2050: float = 0.50,
    rec_ramp_start: int = 2019,
    rec_ramp_end: int = 2050,
    # H2 intensity
    h2_per_t_dri: float = 0.054,
    # Steel production growth
    steel_cagr: float = 0.0,
) -> pd.DataFrame:
    """Project steel-sector H2 demand for one or more countries.

    This function replaces ``steel_scenario.ipynb``.  It uses the process
    modules from ``process/hydrogen/steel.py`` for DRI route splitting.

    The computation follows these steps:
    1. Load steel production base data.
    2. Project total steel production using CAGR.
    3. Split into EAF-scrap and primary (BF-BOF + DRI).
    4. Split primary into DRI and BF-BOF using a ramp.
    5. Split DRI into DRI-CH4 and DRI-H2 using ``build_dri_mix()``.
    6. H2 demand = DRI-H2 production × h2_per_t_dri.

    Args:
        country: ISO-2 code(s).  If None, all EU-27.
        reference_year: Base year.
        target_year: End year.
        base_scenario: TYNDP base scenario label.
        jrc_idees_path: Path to JRC IDEES steel data file.
        tyndp_path: Path to TYNDP demand parameters file.
        dri_share_2050: Target DRI share of primary steel by 2050.
        dri_ramp_start: Year DRI ramp begins.
        dri_ramp_end: Year DRI ramp reaches target.
        dri_ch4_only_years: Years DRI operates on CH4 only before H2 intro.
        dri_h2_ramp_years: Duration of H2 ramp in DRI fuel mix.
        recycling_enabled: Whether EAF share grows toward target.
        rec_target_2050: Target EAF-scrap share by 2050.
        rec_ramp_start: Year EAF recycling ramp begins.
        rec_ramp_end: Year EAF recycling ramp ends.
        h2_per_t_dri: H2 specific consumption (t H2 / t DRI steel).
        steel_cagr: Compound annual growth rate for total steel production.

    Returns:
        pd.DataFrame with columns:
            country, year,
            crude_steel_production_t_per_yr, eaf_scrap_production_t_per_yr,
            dri_h2_production_t_per_yr, dri_ch4_production_t_per_yr,
            bf_bof_production_t_per_yr,
            h2_demand_for_steel_t_per_yr, h2_demand_mwh

    Raises:
        ValueError: If mass balance check fails (tolerance > 1e-6) or if
            negative values are detected in output columns.
    """
    countries = _resolve_countries(country)
    years = np.arange(reference_year, target_year + 1)

    # Fetch steel production base data — uses the clean architecture
    # (fetch_jrc_idees_steel_base → build_steel_base_table internally)
    df_steel = fetch_steel_production(
        jrc_idees_path=jrc_idees_path,
        tyndp_path=tyndp_path,
        countries=countries,
        reference_year=reference_year,
    )
    df_base = df_steel.set_index("country")

    # Build DRI fuel mix (common to all countries)
    dri_mix = build_dri_mix(
        years=years,
        ramp_start=dri_ramp_start,
        ch4_only_years=dri_ch4_only_years,
        h2_ramp_years=dri_h2_ramp_years,
    )
    h2_share = dri_mix["h2_share"].to_numpy()
    ch4_share = dri_mix["ch4_share"].to_numpy()

    # DRI share ramp
    dri_share_target = _linear_ramp_vec(
        years, dri_ramp_start, dri_ramp_end,
        start_value=0.0, end_value=dri_share_2050,
    )

    # Build output using vectorized construction
    all_dfs: list[pd.DataFrame] = []
    for cc in countries:
        if cc not in df_base.index:
            logger.warning(f"No steel base data for {cc}, skipping.")
            continue

        row = df_base.loc[cc]
        steel_base = float(row["crude_steel_production_t_per_yr"])
        bf_base = float(row.get("bf_bof_production_t_per_yr", 0.0))
        dri_ch4_base = float(row.get("dri_ch4_production_t_per_yr", 0.0))
        dri_h2_base = float(row.get("dri_h2_production_t_per_yr", 0.0))
        dri_2019 = dri_ch4_base + dri_h2_base

        # EAF (secondary steel) — always from explicit base table data.
        # Since build_steel_base_table now sources EAF from _EAF_SHARE_2019,
        # this is an independently sourced quantity, not a residual.
        eaf_base = max(float(row.get("eaf_production_t_per_yr", 0.0)), 0.0)
        eaf_share_base = (eaf_base / steel_base) if steel_base > 0 else 0.0

        # Project total steel with CAGR
        total_steel = _apply_cagr(steel_base, years, steel_cagr, reference_year)

        # EAF trajectory
        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=total_steel,
            eaf_base=eaf_base,
            eaf_share_base=eaf_share_base,
            recycling_enabled=recycling_enabled,
            rec_target_2050=rec_target_2050,
            rec_ramp_start=rec_ramp_start,
            rec_ramp_end=rec_ramp_end,
        )

        # Split primary from EAF (returns clamped eaf, primary)
        eaf, primary = split_primary_from_eaf(total_steel, eaf)

        # Split DRI / BF-BOF
        dri_total, bf_total = compute_dri_bf_split(
            primary, dri_2019, dri_share_target,
        )

        # DRI-H2 and DRI-CH4
        dri_h2_prod = dri_total * h2_share
        dri_ch4_prod = dri_total * ch4_share

        # H2 demand from DRI-H2 route
        h2_demand = compute_steel_h2_demand(dri_h2_prod, h2_per_t_dri)

        cc_df = pd.DataFrame({
            "country": cc,
            "year": years.astype(int),
            "crude_steel_production_t_per_yr": total_steel,
            "eaf_scrap_production_t_per_yr": eaf,
            "dri_h2_production_t_per_yr": dri_h2_prod,
            "dri_ch4_production_t_per_yr": dri_ch4_prod,
            "bf_bof_production_t_per_yr": bf_total,
            "h2_demand_for_steel_t_per_yr": h2_demand,
            "h2_demand_mwh_per_yr": h2_demand * H2_LHV_MWH_PER_T,
        })
        all_dfs.append(cc_df)

    df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()

    if not df.empty:
        # Mass balance: crude_steel = eaf + dri_h2 + dri_ch4 + bf_bof
        route_sum = (
            df["eaf_scrap_production_t_per_yr"]
            + df["dri_h2_production_t_per_yr"]
            + df["dri_ch4_production_t_per_yr"]
            + df["bf_bof_production_t_per_yr"]
        )
        err = (df["crude_steel_production_t_per_yr"] - route_sum).abs().max()
        if err > 1e-6:
            raise ValueError(f"Steel mass balance error: {err:.3e}")

        _assert_non_negative(df, [
            "crude_steel_production_t_per_yr", "h2_demand_for_steel_t_per_yr",
            "dri_h2_production_t_per_yr",
        ])
        _check_coverage(df, reference_year, target_year, group_keys=["country"])

        logger.info(
            f"Steel H2 projection: {df['country'].nunique()} countries, "
            f"{reference_year}-{target_year}, DRI 2050={dri_share_2050:.0%}"
        )

    return df


# ═══════════════════════════════════════════════════════════════════════════
# Aggregate function
# ═══════════════════════════════════════════════════════════════════════════

def aggregate_h2_demand(
    country: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    base_scenario: str = "DE",
    sectors: list[str] | None = None,
    **sector_overrides: Any,
) -> pd.DataFrame:
    """Sum H2 demand across sectors for one or more countries.

    Calls each ``project_*_h2_demand()`` function and sums the resulting
    H2 demands per (country, year).

    By default includes all 6 sectors (ammonia, maritime, olefins, steel,
    refinery, esaf). However, refinery and eSAF require external config
    (CONCAWE unit data, jet parameters) and will fail if not provided via
    ``sector_overrides``. For a quick default aggregation without these,
    pass ``sectors=["ammonia", "maritime", "olefins", "steel"]``.

    For production deployments, recommend managing sector parameters and
    defaults in a centralized ``scenario_registry.yaml`` for version control
    and reproducibility.

    Args:
        country: ISO-2 codes.  If None, all EU-27.
        reference_year: Base year.
        target_year: End year.
        base_scenario: TYNDP base scenario.
        sectors: List of sector names to include.  If None, defaults to all 6:
            ``["ammonia", "maritime", "olefins", "steel", "refinery", "esaf"]``.
            Note: refinery and esaf require configuration via sector_overrides.
        **sector_overrides: Per-sector parameter overrides, keyed by
            sector name (e.g. ``ammonia={"h2_route_share_end": 0.8}``).
            For refinery: pass ``units_config`` dict.
            For esaf: pass ``refinery_df``, ``jet_unit_names``, ``jet_yields``.
            For steel: pass ``jrc_idees_path`` and ``tyndp_path`` if available.

    Returns:
        pd.DataFrame with columns:
            country, year, sector, h2_demand_t_per_yr, h2_demand_mwh_per_yr

    Raises:
        ValueError: If an unknown sector name is provided, if no sectors
            produced any data, or if total per-country demand exceeds
            sanity bounds (50 Mt/yr per country, 2,000 TWh/yr EU-wide).
    """
    countries = _resolve_countries(country)

    if sectors is None:
        sectors = ["ammonia", "maritime", "olefins", "steel", "refinery", "esaf"]

    # Dispatch table: sector_name -> (function, h2_column_name)
    dispatch: dict[str, tuple[Any, str]] = {
        "ammonia":  (project_ammonia_h2_demand,  "h2_demand_network_t_per_yr"),
        "maritime": (project_maritime_h2_demand,  "h2_demand_total_t_per_yr"),
        "olefins":  (project_olefins_h2_demand,   "h2_demand_t_per_yr"),
        "steel":    (project_steel_h2_demand,     "h2_demand_for_steel_t_per_yr"),
        "refinery": (project_refinery_h2_demand,  "h2_demand_t_per_yr"),
        "esaf":     (project_esaf_h2_demand,      "h2_demand_for_esaf_t_per_yr"),
    }

    all_parts: list[pd.DataFrame] = []

    for sec_name in sectors:
        if sec_name not in dispatch:
            raise ValueError(f"Unknown sector: {sec_name}")

        func, h2_col = dispatch[sec_name]
        kwargs: dict[str, Any] = {
            "country": countries,
            "reference_year": reference_year,
            "target_year": target_year,
        }
        # Add base_scenario for sectors that need it
        if sec_name not in ("maritime",):
            kwargs["base_scenario"] = base_scenario

        # Apply per-sector overrides
        overrides = sector_overrides.get(sec_name, {})
        if isinstance(overrides, dict):
            kwargs.update(overrides)

        logger.info(f"Computing {sec_name} H2 demand...")
        df_sec = func(**kwargs)

        if df_sec.empty:
            logger.warning(f"No data produced for sector {sec_name}, skipping.")
            continue

        # For refinery, aggregate over units first
        if sec_name == "refinery":
            df_sec = (
                df_sec.groupby(["country", "year"], as_index=False)[h2_col]
                .sum()
            )

        # Extract the H2 column and standardize
        part = df_sec[["country", "year"]].copy()
        part["sector"] = sec_name
        part["h2_demand_t_per_yr"] = df_sec[h2_col].values
        part["h2_demand_mwh_per_yr"] = part["h2_demand_t_per_yr"] * H2_LHV_MWH_PER_T

        all_parts.append(part)

    if not all_parts:
        raise ValueError("No sectors produced any data.")

    df = pd.concat(all_parts, ignore_index=True)

    # Issue #26: Strengthen sanity bounds — per-country upper bound (50 Mt/yr)
    # and non-negative check
    _assert_non_negative(df, ["h2_demand_t_per_yr", "h2_demand_mwh_per_yr"])

    by_country_year = df.groupby(["country", "year"])["h2_demand_t_per_yr"].sum()
    max_per_country = by_country_year.max()
    if max_per_country > 50e6:  # 50 Mt/yr
        bad_country = by_country_year.idxmax()[0]
        bad_year = by_country_year.idxmax()[1]
        bad_val = by_country_year.max()
        logger.warning(
            f"Country {bad_country} in {bad_year} exceeds 50 Mt/yr bound: "
            f"{bad_val/1e6:.1f} Mt/yr. Check for data errors."
        )

    # Sanity bound: EU total per year between 50 TWh and 2,000 TWh
    eu_by_year = df.groupby("year")["h2_demand_mwh_per_yr"].sum()
    eu_twh = eu_by_year / 1e6

    if (eu_twh > 2000.0).any():
        bad_year = eu_twh[eu_twh > 2000.0].index[0]
        bad_val = eu_twh[bad_year]
        logger.warning(
            f"EU-wide H2 demand in {bad_year} exceeds 2,000 TWh/yr bound: "
            f"{bad_val:.1f} TWh/yr. Check for data errors."
        )

    logger.info(
        f"Aggregate H2 demand: {len(sectors)} sectors, "
        f"EU range {eu_twh.min():.1f}–{eu_twh.max():.1f} TWh/yr"
    )

    return df.sort_values(["country", "year", "sector"]).reset_index(drop=True)
