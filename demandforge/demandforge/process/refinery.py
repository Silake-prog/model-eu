"""CONCAWE unit-feed allocation, H₂ specific consumption, and boundary
corrections for the refinery sector.

This module handles capacity interpolation, unit share computation, and
hydrogen demand calculation for refinery units based on CONCAWE Low Carbon
Pathways data (2020).  Two structural scenarios are embedded as package
defaults: ``CONCAWE_MORE_MOLECULE`` (hydroprocessing focus) and
``CONCAWE_MAX_ELECTRON`` (electrification focus).

CONCAWE data structure::

    units_config = {
        "Unit Name": {
            "spec_cons_wt": 2.5,           # H₂ specific consumption (wt%)
            "utilized_capacity_mton": {     # EU-27 throughput (Mt/yr)
                2024: 100, 2030: 95, 2040: 80, 2050: 60,
            },
        },
    }

Key logic:
    1. Interpolate each unit's capacity annually between anchor years.
    2. Compute unit shares = cap_unit(t) / total_cap(t).
    3. Level factor = total_cap(t) / total_cap(ref_year), forced to 1.0
       before ref_year.
    4. For each unit: unit_feed = base_output × level_factor × unit_share.
    5. h2_demand = unit_feed × (spec_cons_wt / 100) × (1 + inefficiency).

Boundary correction:
    The CONCAWE "Naphtha Hydrotreater" includes throughput for both
    catalytic-reformer feed (fuel-side, ~40%) and steam-cracker feed
    (petrochem-side, ~60%).  When the olefins module is active, it
    carries its own H₂ accounting for fossil naphtha hydrotreating.
    :func:`apply_petrochem_naphtha_correction` subtracts the petrochem
    share to avoid double-counting.  This correction is applied in
    ``scenarios.py`` before dispatching to ``project_refinery_h2_demand()``.

Public API:
    apply_capacity_delay()              -- Shift CONCAWE anchor trajectories forward
    build_refinery_unit_allocation()    -- Per-unit H2 demand trajectories
    get_naphtha_for_crackers()          -- Petrochem naphtha supply trajectory
    apply_petrochem_naphtha_correction() -- Remove cracker-bound naphtha HT
    CONCAWE_MORE_MOLECULE               -- More-molecule scenario defaults
    CONCAWE_MAX_ELECTRON                -- Max-electron scenario defaults

Authors:
    Simon Brigode — PERSEE Lab, Mines Paris PSL
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── CONCAWE default unit configurations ──────────────────────────────
# Source: CONCAWE Low Carbon Pathways project (2020).
# Two scenarios are provided: more-molecule (hydroprocessing focus) and
# max-electron (electrification focus).  Each dict maps unit name →
# {spec_cons_wt, utilized_capacity_mton} where spec_cons_wt is the
# H₂ specific consumption in wt% and utilized_capacity_mton maps anchor
# years to EU-27 aggregate throughput in megatonnes per year.
#
# These are the package defaults.  Callers may override by passing their
# own ``units_config`` dict.

CONCAWE_MORE_MOLECULE: dict[str, dict[str, Any]] = {
    "Residue Hydrocracker": {
        "spec_cons_wt": 4.07,
        "utilized_capacity_mton": {2024: 0.97, 2030: 0.71, 2040: 0.00, 2050: 0.81},
    },
    "VGO Hydrocracker": {
        "spec_cons_wt": 2.90,
        "utilized_capacity_mton": {2024: 71.60, 2030: 55.27, 2040: 29.69, 2050: 15.20},
    },
    "Resid Hydrotreater": {
        "spec_cons_wt": 1.43,
        "utilized_capacity_mton": {2024: 0.12, 2030: 0.01, 2040: 0.00, 2050: 0.00},
    },
    "VGO Hydrotreater": {
        "spec_cons_wt": 1.20,
        "utilized_capacity_mton": {2024: 29.63, 2030: 12.48, 2040: 3.99, 2050: 0.19},
    },
    "Diesel Hydrotreater": {
        "spec_cons_wt": 0.80,
        "utilized_capacity_mton": {2024: 149.70, 2030: 94.35, 2040: 38.99, 2050: 13.15},
    },
    "FCC Gasoline Hydrotreater": {
        "spec_cons_wt": 0.24,
        "utilized_capacity_mton": {2024: 10.49, 2030: 5.18, 2040: 5.26, 2050: 1.14},
    },
    "Kero Hydrotreater": {
        "spec_cons_wt": 0.20,
        "utilized_capacity_mton": {2024: 11.02, 2030: 13.13, 2040: 6.23, 2050: 2.99},
    },
    "Naphtha Hydrotreater": {
        "spec_cons_wt": 0.12,
        "utilized_capacity_mton": {2024: 76.67, 2030: 52.97, 2040: 14.52, 2050: 3.78},
    },
    "Naphtha Isomerization": {
        "spec_cons_wt": 0.09,
        "utilized_capacity_mton": {2024: 18.38, 2030: 14.99, 2040: 2.86, 2050: 0.51},
    },
}

CONCAWE_MAX_ELECTRON: dict[str, dict[str, Any]] = {
    "Residue Hydrocracker": {
        "spec_cons_wt": 4.07,
        "utilized_capacity_mton": {2024: 0.97, 2030: 0.51, 2040: 0.00, 2050: 0.00},
    },
    "VGO Hydrocracker": {
        "spec_cons_wt": 2.90,
        "utilized_capacity_mton": {2024: 71.60, 2030: 44.22, 2040: 15.20, 2050: 0.00},
    },
    "Resid Hydrotreater": {
        "spec_cons_wt": 1.43,
        "utilized_capacity_mton": {2024: 0.12, 2030: 0.01, 2040: 0.00, 2050: 0.00},
    },
    "VGO Hydrotreater": {
        "spec_cons_wt": 1.20,
        "utilized_capacity_mton": {2024: 29.63, 2030: 9.98, 2040: 0.19, 2050: 0.00},
    },
    "Diesel Hydrotreater": {
        "spec_cons_wt": 0.80,
        "utilized_capacity_mton": {2024: 149.70, 2030: 75.48, 2040: 13.15, 2050: 0.00},
    },
    "FCC Gasoline Hydrotreater": {
        "spec_cons_wt": 0.24,
        "utilized_capacity_mton": {2024: 10.49, 2030: 4.15, 2040: 1.14, 2050: 0.00},
    },
    "Kero Hydrotreater": {
        "spec_cons_wt": 0.20,
        "utilized_capacity_mton": {2024: 11.02, 2030: 10.51, 2040: 2.99, 2050: 0.00},
    },
    "Naphtha Hydrotreater": {
        "spec_cons_wt": 0.12,
        "utilized_capacity_mton": {2024: 76.67, 2030: 42.38, 2040: 3.78, 2050: 0.00},
    },
    "Naphtha Isomerization": {
        "spec_cons_wt": 0.09,
        "utilized_capacity_mton": {2024: 18.38, 2030: 12.00, 2040: 0.51, 2050: 0.00},
    },
}


def apply_capacity_delay(
    units_config: dict[str, dict[str, Any]],
    delay_years: int,
    reference_year: int = 2024,
) -> dict[str, dict[str, Any]]:
    """Shift CONCAWE capacity trajectories forward by ``delay_years``.

    Produces a new ``units_config`` where each unit's capacity at year *t*
    equals the original capacity at ``max(t - delay_years, reference_year)``.
    The effect is:

    * Capacity stays flat at its ``reference_year`` value until
      ``reference_year + delay_years``.
    * After that, the decline follows the original shape but shifted.
    * At the horizon (e.g. 2050), capacity equals the original value at
      ``2050 - delay_years`` (e.g. 2040 for a 10-year delay).

    This addresses the observation that CONCAWE Low Carbon Pathways (2020)
    assume a near-term refinery decline pace (~5-8 %/yr before 2030) that
    exceeds historical closure rates (~1-2 %/yr).  A 10-year delay produces
    a more realistic transition while preserving the long-run structural
    narrative.

    The function resamples the original piecewise-linear curve at shifted
    query points and rebuilds anchor dicts at the original anchor years,
    so downstream interpolation logic is unchanged.

    Args:
        units_config: CONCAWE unit configuration dict (not mutated).
        delay_years: Number of years to shift the trajectory forward.
            A value of 0 returns a deep copy with no change.
        reference_year: Year at which observed capacity is anchored.
            Capacity is clamped to this value for all years before
            ``reference_year + delay_years``.

    Returns:
        A new config dict with delayed capacity anchors.  All other
        fields (``spec_cons_wt``, etc.) are preserved unchanged.

    Raises:
        ValueError: If delay_years is negative.
    """
    import copy

    if delay_years < 0:
        raise ValueError(f"delay_years must be >= 0, got {delay_years}")

    if delay_years == 0:
        return copy.deepcopy(units_config)

    delayed = copy.deepcopy(units_config)

    for unit_name, config in delayed.items():
        if "utilized_capacity_mton" not in config:
            continue

        anchors = config["utilized_capacity_mton"]
        if not anchors:
            continue

        # Build the original piecewise-linear curve
        sorted_yrs = sorted(anchors.keys())
        sorted_vals = [anchors[y] for y in sorted_yrs]

        # Resample: for each original anchor year t, read original at t - delay
        # clamped to reference_year (so we never read before observed data)
        new_anchors = {}
        for yr in sorted_yrs:
            query_yr = max(yr - delay_years, reference_year)
            # Piecewise-linear interpolation of original curve at query_yr
            new_val = float(np.interp(query_yr, sorted_yrs, sorted_vals))
            new_anchors[yr] = round(new_val, 4)

        config["utilized_capacity_mton"] = new_anchors

    n_units = len([u for u in delayed if "utilized_capacity_mton" in delayed[u]])
    logger.info(
        f"Applied {delay_years}-year capacity delay to {n_units} units "
        f"(reference_year={reference_year})."
    )

    return delayed


def get_naphtha_for_crackers(
    units_config: dict[str, dict[str, Any]],
    years: np.ndarray,
    petrochem_fraction: float = 0.60,
) -> np.ndarray:
    """Return annual petrochemical naphtha supply available for steam crackers.

    Extracts the Naphtha Hydrotreater capacity trajectory from the CONCAWE
    config and multiplies by ``petrochem_fraction`` to obtain the naphtha
    volume flowing to steam crackers (as opposed to catalytic reformers).

    This function reads the **original** (pre-correction) ``units_config``.
    It must be called **before** :func:`apply_petrochem_naphtha_correction`
    to get the full naphtha HT capacity.

    Args:
        units_config: CONCAWE unit configuration dict (not mutated).
        years: Array of scenario years (must be sorted).
        petrochem_fraction: Fraction of naphtha HT throughput going to
            steam crackers (default 0.60, i.e. 60%).

    Returns:
        Array of annual petrochemical naphtha supply in Mt/yr,
        one value per year.  If no Naphtha Hydrotreater unit is found,
        returns an array of zeros.
    """
    naphtha_ht_key = "Naphtha Hydrotreater"
    if naphtha_ht_key not in units_config:
        logger.warning(
            f"No '{naphtha_ht_key}' unit in config — "
            f"naphtha supply for crackers assumed zero."
        )
        return np.zeros(len(years))

    anchors = units_config[naphtha_ht_key]["utilized_capacity_mton"]
    total_naphtha_mt = _piecewise_linear_annual(years, anchors)
    petrochem_naphtha_mt = total_naphtha_mt * petrochem_fraction

    logger.info(
        f"Naphtha for crackers: {petrochem_naphtha_mt[0]:.2f} Mt "
        f"({int(years[0])}) → {petrochem_naphtha_mt[-1]:.2f} Mt "
        f"({int(years[-1])})"
    )
    return petrochem_naphtha_mt


def apply_petrochem_naphtha_correction(
    units_config: dict[str, dict[str, Any]],
    petrochem_fraction: float = 0.60,
) -> dict[str, dict[str, Any]]:
    """Remove the petrochemical share from the Naphtha Hydrotreater capacity.

    The CONCAWE "Naphtha Hydrotreater" unit includes throughput for both
    catalytic reformer feed (fuel-side) and steam cracker feed (petrochem-side).
    Since the olefins module carries its own H₂ accounting for naphtha
    hydrotreating, the petrochem fraction must be subtracted here to avoid
    double-counting.

    Args:
        units_config: CONCAWE unit configuration dict (will NOT be mutated).
        petrochem_fraction: Fraction of naphtha HT throughput going to
            steam crackers (default 0.60, i.e. 60%).

    Returns:
        A new config dict with the Naphtha Hydrotreater capacity reduced
        by ``petrochem_fraction``.  All other units are unchanged.
    """
    import copy
    corrected = copy.deepcopy(units_config)

    naphtha_ht_key = "Naphtha Hydrotreater"
    if naphtha_ht_key in corrected:
        fuel_fraction = 1.0 - petrochem_fraction
        original_caps = corrected[naphtha_ht_key]["utilized_capacity_mton"]
        corrected[naphtha_ht_key]["utilized_capacity_mton"] = {
            yr: cap * fuel_fraction
            for yr, cap in original_caps.items()
        }
        logger.info(
            f"Applied petrochem naphtha correction: Naphtha HT capacity "
            f"reduced by {petrochem_fraction:.0%} (keeping reformer-feed "
            f"portion only)."
        )
    else:
        logger.debug(
            f"No '{naphtha_ht_key}' unit found in config — "
            f"petrochem correction not applied."
        )

    return corrected


def _piecewise_linear_annual(
    years: np.ndarray,
    anchors: dict[int, float],
) -> np.ndarray:
    """Interpolate capacity values between anchor years using piecewise linear.

    Args:
        years: Array of scenario years (must be sorted).
        anchors: Dict mapping anchor year to capacity value.

    Returns:
        Array of interpolated capacity values, one per year in ``years``.
    """
    if not anchors:
        raise ValueError("anchors dict cannot be empty")

    # Sort anchor years
    sorted_anchors = sorted(anchors.items())
    anchor_years = np.array([y for y, _ in sorted_anchors])
    anchor_values = np.array([v for _, v in sorted_anchors])

    # Interpolate using numpy's interp
    # Points before the first anchor are set to the first value
    # Points after the last anchor are set to the last value
    interpolated = np.interp(years, anchor_years, anchor_values)

    return interpolated


def _compute_unit_shares(
    unit_capacities_annual: dict[str, np.ndarray],
    years: np.ndarray,
) -> pd.DataFrame:
    """Compute annual unit shares from annual unit capacities.

    Args:
        unit_capacities_annual: Dict mapping unit name to array of annual capacities.
        years: Array of scenario years.

    Returns:
        DataFrame with columns: year, unit, capacity_share.
    """
    rows = []
    for year_idx, year in enumerate(years):
        total_cap = sum(caps[year_idx] for caps in unit_capacities_annual.values())
        if total_cap > 0:
            for unit_name, caps in unit_capacities_annual.items():
                share = caps[year_idx] / total_cap
                rows.append({
                    "year": year,
                    "unit": unit_name,
                    "capacity_share": share,
                })
        else:
            # Fallback: distribute equally if total is zero
            n_units = len(unit_capacities_annual)
            for unit_name in unit_capacities_annual.keys():
                rows.append({
                    "year": year,
                    "unit": unit_name,
                    "capacity_share": 1.0 / n_units if n_units > 0 else 0.0,
                })

    return pd.DataFrame(rows)


def _compute_level_factor(
    unit_capacities_annual: dict[str, np.ndarray],
    years: np.ndarray,
    ref_year: int | None = None,
) -> np.ndarray:
    """Compute level factor = cap(t) / cap(ref_year), forced to 1.0 before ref_year.

    Args:
        unit_capacities_annual: Dict mapping unit name to array of annual capacities.
        years: Array of scenario years.
        ref_year: Reference year for normalization. If None, uses minimum year.

    Returns:
        Array of level factors, one per year.
    """
    if ref_year is None:
        ref_year = int(years[0])

    # Compute total capacity per year
    total_caps = np.zeros(len(years))
    for caps in unit_capacities_annual.values():
        total_caps += caps

    # Find index of ref_year
    ref_idx = None
    for i, y in enumerate(years):
        if y == ref_year:
            ref_idx = i
            break

    if ref_idx is None:
        # Interpolate ref_year capacity
        sorted_years = sorted(years)
        sorted_caps = np.interp(ref_year, sorted_years, total_caps)
        ref_cap = sorted_caps
    else:
        ref_cap = total_caps[ref_idx]

    if ref_cap <= 0:
        logger.warning(f"Reference year capacity is {ref_cap} <= 0; using all 1.0")
        return np.ones(len(years))

    level_factor = total_caps / ref_cap

    # Force level_factor to 1.0 before ref_year using vectorized numpy operation
    level_factor = np.where(np.array(years) < ref_year, 1.0, total_caps / ref_cap)

    return level_factor


def build_refinery_unit_allocation(
    base_output_t_per_yr: float,
    units_config: dict[str, dict[str, Any]],
    years: np.ndarray,
    inefficiency_share: float = 0.14,
) -> pd.DataFrame:
    """Build annual unit-feed and H2-demand trajectories for one CONCAWE scenario.

    Computes:
        1. Annual capacity for each unit (piecewise linear interpolation).
        2. Unit shares = cap_unit(t) / total_cap(t).
        3. Level factor = total_cap(t) / total_cap(ref_year).
        4. Unit feed = base_output × level_factor × unit_share.
        5. H2 demand = unit_feed × (spec_cons_wt / 100) × (1 + inefficiency_share).

    Args:
        base_output_t_per_yr: Base-year refinery output for this country (t/yr).
        units_config: Dict of unit_name -> {spec_cons_wt, utilized_capacity_mton: {year: value}}.
        years: Array of scenario years (must be sorted).
        inefficiency_share: H2 overconsumption factor (default 0.14).

    Returns:
        DataFrame with columns:
            - year
            - unit
            - unit_capacity_share
            - unit_feed_t_per_yr
            - spec_cons_wt
            - h2_demand_t_per_yr
            - refinery_output_total_t_per_yr
            - level_factor

    Raises:
        ValueError: If base_output_t_per_yr <= 0, units_config is empty, years array is empty,
                    a unit is missing 'utilized_capacity_mton' in config, a unit has empty
                    capacity anchors, a unit is missing 'spec_cons_wt' in config, result is empty,
                    result contains NaN values, or negative values are detected in shares/feed/demand.
    """
    if base_output_t_per_yr <= 0:
        raise ValueError(f"base_output_t_per_yr must be > 0, got {base_output_t_per_yr}")

    if not units_config:
        raise ValueError("units_config cannot be empty")

    if len(years) == 0:
        raise ValueError("years array cannot be empty")

    # Ensure years is sorted
    years_sorted = np.sort(years)

    # Step 1: Interpolate annual capacities for each unit
    unit_capacities_annual: dict[str, np.ndarray] = {}
    for unit_name, config in units_config.items():
        if "utilized_capacity_mton" not in config:
            raise ValueError(
                f"Unit '{unit_name}' missing 'utilized_capacity_mton' in config"
            )
        anchors = config["utilized_capacity_mton"]
        if not anchors:
            raise ValueError(f"Unit '{unit_name}' has empty capacity anchors")
        unit_capacities_annual[unit_name] = _piecewise_linear_annual(
            years_sorted, anchors
        )

    # Step 2: Compute total capacity per year
    total_capacities = np.zeros(len(years_sorted))
    for caps in unit_capacities_annual.values():
        total_capacities += caps

    if np.all(total_capacities <= 0):
        logger.warning("All total capacities are <= 0")

    # Step 3: Compute unit shares
    shares_df = _compute_unit_shares(unit_capacities_annual, years_sorted)

    # Step 4: Compute level factor
    ref_year = int(years_sorted[0])
    level_factor = _compute_level_factor(unit_capacities_annual, years_sorted, ref_year)

    # Step 5: Compute unit feed and H2 demand
    rows = []
    for year_idx, year in enumerate(years_sorted):
        year_shares = shares_df[shares_df["year"] == year]
        lf = level_factor[year_idx]
        refinery_output = base_output_t_per_yr * lf

        for _, share_row in year_shares.iterrows():
            unit_name = share_row["unit"]
            capacity_share = share_row["capacity_share"]

            # Get spec_cons_wt for this unit with explicit error checking
            if "spec_cons_wt" not in units_config[unit_name]:
                raise ValueError(f"Unit '{unit_name}' missing 'spec_cons_wt' in config")
            spec_cons_wt = float(units_config[unit_name]["spec_cons_wt"])

            # Unit feed = base_output × level_factor × unit_share
            unit_feed = refinery_output * capacity_share

            # H2 demand = unit_feed × (spec_cons_wt / 100) × (1 + inefficiency_share)
            h2_demand = unit_feed * (spec_cons_wt / 100.0) * (1.0 + inefficiency_share)

            rows.append({
                "year": year,
                "unit": unit_name,
                "unit_capacity_share": capacity_share,
                "unit_feed_t_per_yr": unit_feed,
                "spec_cons_wt": spec_cons_wt,
                "h2_demand_t_per_yr": h2_demand,
                "refinery_output_total_t_per_yr": refinery_output,
                "level_factor": lf,
            })

    result = pd.DataFrame(rows)

    # Data quality checks
    if len(result) == 0:
        raise ValueError("Result DataFrame is empty")
    if result.isna().any().any():
        raise ValueError("Result contains NaN values")
    if not (result["unit_capacity_share"] >= 0).all():
        raise ValueError("Negative capacity shares detected")
    if not (result["unit_feed_t_per_yr"] >= 0).all():
        raise ValueError("Negative unit feed detected")
    if not (result["h2_demand_t_per_yr"] >= 0).all():
        raise ValueError("Negative H2 demand detected")

    logger.info(
        f"Built refinery allocation for {len(units_config)} units and {len(years_sorted)} years"
    )

    return result
