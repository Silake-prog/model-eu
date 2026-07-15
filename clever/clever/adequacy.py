"""
clever.adequacy — Ex-ante and ex-post adequacy diagnostic functions.

This module provides functions for assessing supply adequacy at different
stages of model development:

- Ex-ante diagnostics: Check whether peak demand can be met by existing
  and expandable dispatchable capacity, accounting for VRE capacity credits.

- Ex-post diagnostics: Analyze solve results (hourly prices, demand) to
  compute realized adequacy metrics like LOLE and ENS.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd
import polars as pl

from clever.constants import (
    ASSUMED_FLH,
    CLEVER_VRE_SPECS,
    DEFAULT_EXPANSION_HEADROOM_MW,
    EXPANSION_HEADROOM_BY_COUNTRY,
    DEFAULT_FLH,
    DEFAULT_LOAD_SHEDDING_COST,
    EPS_MW,
    EPS_MWH,
    EXPANDABLE_MODEL_TECHS,
    MODELTECH_TO_EOLES,
    VRE_CAPACITY_CREDIT,
)
from clever.model import (
    _candidate_area_codes,
    get_clever_capacity_mw,
    is_expandable_dispatchable,
    normalize_country_code,
)

logger = logging.getLogger(__name__)


# =========================================================
# EX-ANTE DIAGNOSTICS
# =========================================================


def _safe_peak_demand_mw(electricity_demand_pl: pl.DataFrame) -> float:
    """
    Extract peak demand from hourly demand DataFrame.

    Tries "demand" column first, then falls back to the single remaining
    numeric column if available.

    Parameters
    ----------
    electricity_demand_pl : pl.DataFrame
        Hourly demand data (may contain "hour", "year_op", "demand", etc.)

    Returns
    -------
    float
        Peak demand in MW, or 0.0 if DataFrame is empty.

    Raises
    ------
    ValueError
        If unable to infer demand column from the DataFrame.
    """
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


def compute_country_adequacy_metrics(
    country_code: str,
    model_year: int,
    clever_capacity_df: pd.DataFrame,
    clever_non_enr_df: pd.DataFrame,
    electricity_demand_pl: pl.DataFrame,
) -> dict[str, float]:
    """
    Compute ex-ante adequacy metrics for a given country and year.

    Calculates peak demand, dispatchable capacity (existing and maximum
    expandable), VRE capacity credit, and adequacy margin.

    Parameters
    ----------
    country_code : str
        ISO country code (e.g., "FR", "DE").
    model_year : int
        Model year.
    clever_capacity_df : pd.DataFrame
        Capacity DataFrame from CLEVER (with columns: area, year_op,
        model_tech, capacity_mw, ...).
    clever_non_enr_df : pd.DataFrame
        Non-renewable CLEVER data (with columns: area, year_op, model_tech,
        max_yearly_production_mwh, ...).
    electricity_demand_pl : pl.DataFrame
        Hourly demand data (Polars DataFrame).

    Returns
    -------
    dict[str, float]
        Dictionary with keys:
        - peak_demand_mw: Peak hourly demand
        - dispatchable_existing_mw: Existing dispatchable capacity
        - dispatchable_total_max_mw: Existing + expandable dispatchable
        - vre_credit_mw: VRE capacity (weighted by capacity credits)
        - adequacy_margin_mw: total resources - peak demand
    """
    peak_demand_mw = _safe_peak_demand_mw(electricity_demand_pl)

    # VRE capacity credit contribution
    vre_credit_mw = 0.0
    for clever_tech, spec in CLEVER_VRE_SPECS.items():
        model_tech = spec["model_tech"]
        cap = get_clever_capacity_mw(clever_capacity_df, country_code, model_year, model_tech) or 0.0
        vre_credit_mw += cap * VRE_CAPACITY_CREDIT.get(model_tech, 0.0)

    # Dispatchable capacity from non-renewable technologies
    dispatchable_existing_mw = 0.0
    dispatchable_total_max_mw = 0.0

    # Find non-renewable data for this country
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

            # Derive MW capacity from MWh and ASSUMED_FLH
            existing_capacity_mw = max(max_yearly_production_mwh / ASSUMED_FLH.get(eoles_tech, DEFAULT_FLH), 0.0)
            if existing_capacity_mw <= EPS_MW:
                existing_capacity_mw = 0.0

            dispatchable_existing_mw += existing_capacity_mw

            # Add expansion headroom for expandable technologies
            # ONLY if CLEVER declares non-zero energy for this fuel
            if is_expandable_dispatchable(model_tech) and max_yearly_production_mwh > EPS_MWH:
                area_norm = normalize_country_code(country_code)
                country_hr = EXPANSION_HEADROOM_BY_COUNTRY.get(area_norm, {})
                headroom = country_hr.get(
                    model_tech,
                    DEFAULT_EXPANSION_HEADROOM_MW.get(model_tech, 0.0),
                )
                dispatchable_total_max_mw += existing_capacity_mw + headroom
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


def log_country_adequacy_diagnostic(
    country_code: str,
    model_year: int,
    clever_capacity_df: pd.DataFrame,
    clever_non_enr_df: pd.DataFrame,
    electricity_demand_pl: pl.DataFrame,
) -> None:
    """
    Log ex-ante adequacy diagnostics for a country.

    Computes adequacy metrics and logs them at INFO level. Warns if the
    adequacy margin is negative (potential supply shortage).

    Parameters
    ----------
    country_code : str
        ISO country code.
    model_year : int
        Model year.
    clever_capacity_df : pd.DataFrame
        Capacity DataFrame from CLEVER.
    clever_non_enr_df : pd.DataFrame
        Non-renewable CLEVER data.
    electricity_demand_pl : pl.DataFrame
        Hourly demand data.
    """
    metrics = compute_country_adequacy_metrics(
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
# EX-POST DIAGNOSTICS
# =========================================================


def compute_expost_adequacy(
    prices_df: pd.DataFrame,
    demand_df: pd.DataFrame,
    load_shedding_cost: float = DEFAULT_LOAD_SHEDDING_COST,
    dispatch_df: Optional[pd.DataFrame] = None,
) -> dict[str, Any]:
    """
    Compute ex-post adequacy metrics from solved model results.

    Given hourly prices and demand, identifies periods when the system
    faced scarcity (price >= load shedding cost) and quantifies LOLE
    (Loss of Load Events) and ENS (Energy Not Served).

    ENS computation:
    - If ``dispatch_df`` is provided: ENS = demand − total_generation during
      scarcity hours (correct definition per ENTSO-E ERAA methodology).
    - If ``dispatch_df`` is None: falls back to ENS = demand during scarcity
      hours (upper bound, overestimates by ignoring partial supply).

    Parameters
    ----------
    prices_df : pd.DataFrame
        Hourly nodal/area prices. Columns should be area codes (e.g., "FR", "DE").
        Index should be hour or datetime.
    demand_df : pd.DataFrame
        Hourly demand. Columns should match area codes in prices_df.
        Index should be hour or datetime.
    load_shedding_cost : float, optional
        Threshold price (in EUR/MWh) above which scarcity is declared.
        Default is 30,000 (from constants.DEFAULT_LOAD_SHEDDING_COST).
    dispatch_df : pd.DataFrame, optional
        Hourly dispatch (generation) by area and technology. Expected columns:
        "area", "hour", "tech", "value" (MW). If provided, used for precise
        ENS = demand − supply computation. If None, ENS is approximated as
        total demand during shedding hours (upper bound).

    Returns
    -------
    dict[str, Any]
        Per-area adequacy metrics with keys:
        - "lole_hours": Loss of Load Events (hours with scarcity)
        - "ens_mwh": Energy Not Served (MWh)
        - "ens_method": "dispatch" or "demand_upper_bound"
        - "adequacy_margin_pct": Percentage of hours without shedding
        - "scarcity_hours": Raw count of scarcity hours

        For example: {"FR": {"lole_hours": 5, "ens_mwh": 125.3, ...}, ...}
    """
    if prices_df.empty or demand_df.empty:
        return {}

    # Align indices
    common_index = prices_df.index.intersection(demand_df.index)
    prices_df = prices_df.loc[common_index]
    demand_df = demand_df.loc[common_index]

    # Pre-compute per-area hourly supply from dispatch if available
    supply_by_area: dict[str, np.ndarray] = {}
    if dispatch_df is not None and not dispatch_df.empty:
        for area in demand_df.columns:
            area_disp = dispatch_df[dispatch_df["area"] == area]
            if not area_disp.empty and "hour" in area_disp.columns and "value" in area_disp.columns:
                # Sum all tech generation per hour for this area
                hourly_supply = (
                    area_disp.groupby("hour")["value"]
                    .sum()
                    .reindex(common_index, fill_value=0.0)
                    .values.astype(float)
                )
                supply_by_area[area] = hourly_supply

    # Identify areas present in both dataframes
    common_areas = set(prices_df.columns) & set(demand_df.columns)

    metrics_by_area = {}
    for area in sorted(common_areas):
        prices = prices_df[area].values.astype(float)
        demand = demand_df[area].values.astype(float)

        # Scarcity flag: price at or above shedding cost
        is_scarce = prices >= load_shedding_cost
        lole_hours = int(np.sum(is_scarce))

        # ENS computation
        if area in supply_by_area:
            # Correct ENS: demand minus available supply during scarcity hours
            supply = supply_by_area[area]
            unserved = np.maximum(demand - supply, 0.0)  # clip negative (over-generation)
            ens_mwh = float(np.sum(unserved[is_scarce]))
            ens_method = "dispatch"
        else:
            # Fallback: ENS = total demand during scarcity hours (upper bound).
            # This overestimates because it ignores partial supply that was
            # delivered during scarcity. Use only when dispatch data is unavailable.
            ens_mwh = float(np.sum(demand[is_scarce]))
            ens_method = "demand_upper_bound"

        # Adequacy margin: percentage of non-scarcity hours
        total_hours = len(prices)
        adequacy_margin_pct = 100.0 * (1.0 - lole_hours / total_hours) if total_hours > 0 else 100.0

        metrics_by_area[area] = {
            "lole_hours": lole_hours,
            "ens_mwh": ens_mwh,
            "ens_method": ens_method,
            "adequacy_margin_pct": adequacy_margin_pct,
            "scarcity_hours": lole_hours,
        }

    return metrics_by_area


def summarize_adequacy(metrics_by_country: dict[str, dict]) -> pd.DataFrame:
    """
    Create a summary DataFrame from per-country/area adequacy metrics.

    Parameters
    ----------
    metrics_by_country : dict[str, dict]
        Per-country metrics, as returned by compute_expost_adequacy().
        Keys are area codes, values are dicts of metric names to values.

    Returns
    -------
    pd.DataFrame
        Summary with one row per area, columns for each metric.
        Index is area code.
    """
    if not metrics_by_country:
        return pd.DataFrame()

    records = []
    for area, metrics in metrics_by_country.items():
        row = {"area": area}
        row.update(metrics)
        records.append(row)

    return pd.DataFrame(records).set_index("area")
