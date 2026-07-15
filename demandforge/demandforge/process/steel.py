"""DRI route splitting and auxiliary fuel demand logic for steel production.

This module ports the DRI route disaggregation and auxiliary fuel demand logic
from the steel notebook. It handles:
    - DRI fuel mix (CH4 vs H2) for directed reduction of iron ore.
    - Blast furnace fuel mix (coal, biomass, H2) and CCS capture rates.
    - EAF-scrap vs primary steel split.
    - DRI-H2 vs DRI-CH4 vs BF-BOF production volumes.
    - H2 demand from DRI-H2 production.

Authors:
    Simon Brigode — PERSEE Lab, Mines Paris PSL
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from demandforge.process._utils import linear_ramp

logger = logging.getLogger(__name__)


# Backward-compatible alias
_linear_ramp = linear_ramp


def build_dri_mix(
    years: np.ndarray,
    ramp_start: int,
    ch4_only_years: int,
    h2_ramp_years: int,
) -> pd.DataFrame:
    """Build annual CH4/H2 fuel mix for DRI process.

    Implements a two-phase transition:
        1. CH4-only phase: [start, start + ch4_only_years)
        2. Ramp phase: [start + ch4_only_years, start + ch4_only_years + h2_ramp_years)

    Args:
        years: Array of scenario years.
        ramp_start: Year when DRI-H2 deployment begins.
        ch4_only_years: Duration of CH4-only phase (years).
        h2_ramp_years: Duration of linear H2 ramp-up (years).

    Returns:
        DataFrame with columns: year, ch4_share, h2_share.

    Raises:
        ValueError: If CH4 and H2 shares do not sum to 1.0.
    """
    ch4_end = ramp_start + ch4_only_years
    h2_end = ch4_end + h2_ramp_years

    # CH4 share: 1.0 until ch4_end, then linear ramp to 0.0
    ch4_shares = _linear_ramp(years, ch4_end, h2_end, start_value=1.0, end_value=0.0)

    # H2 share: complement of CH4
    h2_shares = 1.0 - ch4_shares

    # Enforce bounds
    ch4_shares = np.clip(ch4_shares, 0.0, 1.0)
    h2_shares = np.clip(h2_shares, 0.0, 1.0)

    rows = []
    for year_idx, year in enumerate(years):
        rows.append({
            "year": year,
            "ch4_share": ch4_shares[year_idx],
            "h2_share": h2_shares[year_idx],
        })

    result = pd.DataFrame(rows)
    if not np.allclose(result["ch4_share"] + result["h2_share"], 1.0):
        raise ValueError("CH4 and H2 shares do not sum to 1.0")

    logger.info(
        f"Built DRI mix: CH4-only until {ch4_end}, H2 ramp until {h2_end}"
    )

    return result


def build_bf_fuel_mix(
    years: np.ndarray,
    ramp_start: int,
    ramp_end: int,
    biomass_max: float,
    h2_max: float,
    ccs_capture_rate_start: float,
    ccs_capture_rate_end: float,
    ccs_ramp_start: int,
    ccs_ramp_end: int,
) -> pd.DataFrame:
    """Build annual fuel mix and CCS capture rate for blast furnace.

    Implements three fuel phases:
        1. Coal + Biomass ramp-up: [ramp_start, ramp_end).
        2. Biomass plateau: [ramp_end, ...) with optional H2 addition.
        3. CCS capture rate ramp: [ccs_ramp_start, ccs_ramp_end).

    Args:
        years: Array of scenario years.
        ramp_start: Year when biomass ramp begins.
        ramp_end: Year when biomass reaches plateau.
        biomass_max: Maximum biomass share (0.0–1.0).
        h2_max: Maximum H2 share (0.0–1.0).
        ccs_capture_rate_start: CCS capture rate at ccs_ramp_start.
        ccs_capture_rate_end: CCS capture rate at ccs_ramp_end.
        ccs_ramp_start: Year when CCS ramp begins.
        ccs_ramp_end: Year when CCS ramp ends.

    Returns:
        DataFrame with columns: year, coal_share, biomass_share, h2_share, ccs_capture_rate.

    Raises:
        ValueError: If fuel shares do not sum to 1.0.
    """
    # Biomass ramp: 0.0 until ramp_start, then linear to biomass_max at ramp_end
    biomass_shares = _linear_ramp(
        years, ramp_start, ramp_end, start_value=0.0, end_value=biomass_max
    )
    # Plateau after ramp_end
    biomass_shares[years >= ramp_end] = biomass_max

    # H2 share: ramp from 0.0 to h2_max over [ramp_start, ramp_end]
    r = _linear_ramp(years, ramp_start, ramp_end, 0.0, 1.0)
    h2_shares = r * float(h2_max)

    # Coal share: complement of (biomass + H2)
    coal_shares = 1.0 - biomass_shares - h2_shares

    # CCS capture rate ramp
    ccs_rates = _linear_ramp(
        years,
        ccs_ramp_start,
        ccs_ramp_end,
        start_value=ccs_capture_rate_start,
        end_value=ccs_capture_rate_end,
    )

    # Enforce bounds
    coal_shares = np.clip(coal_shares, 0.0, 1.0)
    biomass_shares = np.clip(biomass_shares, 0.0, 1.0)
    h2_shares = np.clip(h2_shares, 0.0, 1.0)
    ccs_rates = np.clip(ccs_rates, 0.0, 1.0)

    rows = []
    for year_idx, year in enumerate(years):
        rows.append({
            "year": year,
            "coal_share": coal_shares[year_idx],
            "biomass_share": biomass_shares[year_idx],
            "h2_share": h2_shares[year_idx],
            "ccs_capture_rate": ccs_rates[year_idx],
        })

    result = pd.DataFrame(rows)
    if not np.allclose(
        result["coal_share"] + result["biomass_share"] + result["h2_share"], 1.0
    ):
        raise ValueError("Fuel shares do not sum to 1.0")

    logger.info(
        f"Built BF fuel mix: biomass ramp {ramp_start}–{ramp_end}, "
        f"CCS ramp {ccs_ramp_start}–{ccs_ramp_end}"
    )

    return result


def compute_eaf_scrap_series(
    years: np.ndarray,
    steel_total: np.ndarray,
    eaf_base: float,
    eaf_share_base: float,
    recycling_enabled: bool,
    rec_target_2050: float,
    rec_ramp_start: int,
    rec_ramp_end: int,
) -> np.ndarray:
    """Compute EAF-scrap production trajectory.

    Combines a base EAF level (if recycling_enabled=False) with optional
    ramped growth to a 2050 recycling target.

    IMPORTANT: The effective target is floored at the country's base-year
    EAF share.  A country already above the global target will never be
    forced to *decrease* its EAF share.  This enforces:

        effective_target = max(rec_target_2050, eaf_share_base)

    Args:
        years: Array of scenario years.
        steel_total: Annual total steel production (t/yr).
        eaf_base: Base-year EAF scrap production (t/yr).
        eaf_share_base: Base-year EAF share of total steel.
        recycling_enabled: If True, allow EAF growth toward rec_target_2050.
        rec_target_2050: Target EAF share in 2050 (0.0–1.0).
        rec_ramp_start: Year when EAF ramp begins.
        rec_ramp_end: Year when EAF reaches target (usually 2050).

    Returns:
        Array of EAF scrap production, one per year.

    Raises:
        ValueError: If EAF production becomes negative.
    """
    eaf_production = np.zeros(len(years))

    if not recycling_enabled:
        # Fixed EAF level
        eaf_production[:] = eaf_base
    else:
        # Country-specific floor: never force a country already above
        # the global target to decrease its EAF share.
        effective_target = max(rec_target_2050, eaf_share_base)

        if effective_target > rec_target_2050:
            logger.info(
                f"EAF floor applied: base share {eaf_share_base:.1%} > "
                f"target {rec_target_2050:.1%}, using {effective_target:.1%}"
            )

        # Ramp EAF share from base to effective target
        eaf_shares = _linear_ramp(
            years,
            rec_ramp_start,
            rec_ramp_end,
            start_value=eaf_share_base,
            end_value=effective_target,
        )
        eaf_shares = np.clip(eaf_shares, 0.0, 1.0)
        eaf_production = eaf_shares * steel_total

    if not np.all(eaf_production >= 0):
        raise ValueError("Negative EAF production detected")

    logger.info(f"Computed EAF scrap production; recycling_enabled={recycling_enabled}")

    return eaf_production


def split_primary_from_eaf(
    total: np.ndarray,
    eaf: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Decompose total steel into EAF-scrap and primary (BF + DRI).

    Args:
        total: Annual total steel production (t/yr).
        eaf: Annual EAF-scrap production (t/yr).

    Returns:
        Tuple (eaf, primary) where primary = total - eaf.

    Raises:
        ValueError: If total and eaf arrays have different lengths, or if
            EAF/primary production is negative, or if mass balance is violated.
    """
    if len(total) != len(eaf):
        raise ValueError(
            f"total and eaf must have same length: "
            f"{len(total)} vs {len(eaf)}"
        )

    # Clamp EAF to total: with negative CAGR and fixed EAF, the base-year
    # EAF volume can exceed the shrinking total.  In that case, EAF is
    # capped at total (→ primary = 0), meaning the country has gone fully
    # secondary steelmaking.
    eaf = np.minimum(eaf, total)

    primary = total - eaf

    if not np.all(eaf >= -1e-6):
        raise ValueError("Negative EAF production detected")
    if not np.all(primary >= -1e-6):
        raise ValueError("Negative primary production detected")
    # Clean up floating-point residuals
    primary = np.maximum(primary, 0.0)
    eaf = np.maximum(eaf, 0.0)
    if not np.allclose(eaf + primary, total):
        raise ValueError("Mass balance violation: eaf + primary != total")

    return eaf, primary


def compute_dri_bf_split(
    primary_total: np.ndarray,
    dri_2019: float,
    dri_share_target: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute DRI and BF-BOF volumes from primary steel and DRI share target.

    Uses the DRI share target (pre-computed ramp array) with a floor at the
    2019 base-year level.

    Args:
        primary_total: Annual primary steel production (t/yr).
        dri_2019: Base-year (2019) DRI production (t/yr).
        dri_share_target: Target DRI share by year (array, 0.0–1.0).
            Pre-computed ramp from _linear_ramp_vec.

    Returns:
        Tuple (dri, bf_bof) where dri + bf_bof = primary_total.

    Raises:
        ValueError: If dri_2019 or dri_share_target values are invalid, or if
            mass balance is violated.
    """
    if dri_2019 < 0:
        raise ValueError(f"dri_2019={dri_2019} must be non-negative")

    if np.any(dri_share_target < 0) or np.any(dri_share_target > 1.0):
        raise ValueError(
            f"dri_share_target must be in [0, 1], got min={dri_share_target.min()}, "
            f"max={dri_share_target.max()}"
        )

    # Compute DRI share in 2019 (base year, index 0)
    dri_share_2019 = dri_2019 / primary_total[0] if primary_total[0] > 0 else 0.0

    # Floor target at 2019 level
    dri_target = np.maximum(dri_share_target, dri_share_2019)

    dri = primary_total * dri_target
    bf_bof = primary_total - dri

    if not np.all(dri >= 0):
        raise ValueError("Negative DRI production detected")
    if not np.all(bf_bof >= 0):
        raise ValueError("Negative BF-BOF production detected")
    if not np.allclose(dri + bf_bof, primary_total):
        raise ValueError("Mass balance violation: dri + bf_bof != primary_total")

    logger.info(
        f"Computed DRI/BF-BOF split: 2019 share {dri_share_2019:.1%}, "
        f"target range [{dri_share_target.min():.1%}, {dri_share_target.max():.1%}]"
    )

    return dri, bf_bof


def build_steel_base_table(
    jrc_df: pd.DataFrame,
    tyndp_df: pd.DataFrame,
    eaf_shares: dict[str, float] | pd.DataFrame | None = None,
    reference_year: int = 2019,
) -> pd.DataFrame:
    """Build base-year steel route table from JRC-IDEES + TYNDP shares.

    This function implements the "initial CSV generation" stage that was
    historically implicit in notebooks.  It combines raw JRC-IDEES
    steel production (kt) with explicit EAF (secondary steel) shares and
    TYNDP technology route shares for the primary steel fraction.

    **Key semantic**: EAF is secondary steelmaking (scrap-based) and is an
    independent, explicitly sourced quantity — NOT a residual.  The TYNDP
    BF-BOF/DRI shares partition the **primary** steel fraction only::

        primary_t = crude_steel_t - eaf_t
        bf_bof_t  = primary_t × bf_share_of_primary
        dri_ch4_t = primary_t × dri_ch4_share_of_primary
        dri_h2_t  = primary_t × dri_h2_share_of_primary

    Where ``bf_share + dri_ch4_share + dri_h2_share ≈ 1.0`` (they
    partition primary, summing within tolerance).

    Args:
        jrc_df: Raw JRC-IDEES steel data.
            Required columns: ``country``, ``steel_production_kt``.
        tyndp_df: TYNDP demand parameters.
            Required columns: ``country``, and at least the steel share
            columns (``industry_steel_blastfurnace_bof_share``, etc.).
            Shares must be in [0, 1] (fraction, not percentage).
            These shares are interpreted as shares of **primary** steel.
        eaf_shares: Country-specific EAF share of total crude steel.
            Can be a dict ``{country_code: fraction}`` or a DataFrame
            with columns ``country`` and ``eaf_share``.
            If None, falls back to ``_EAF_SHARE_2019`` from
            ``demandforge.fetch.industry_data``.
        reference_year: Base year label for the output.

    Returns:
        DataFrame with columns:
        - country (str)
        - year (int)
        - crude_steel_production_t_per_yr (float)
        - primary_production_t_per_yr (float)
        - bf_bof_production_t_per_yr (float)
        - dri_ch4_production_t_per_yr (float)
        - dri_h2_production_t_per_yr (float)
        - eaf_production_t_per_yr (float)
        - blastfurnace_bof_share (float) — share of primary
        - dri_natural_gas_share (float) — share of primary
        - dri_hydrogen_share (float) — share of primary
        - eaf_share (float) — share of total crude steel

    Raises:
        ValueError: If required columns are missing, shares are out of
            range, primary-route shares don't sum to ~1.0, or mass
            balance is violated.
    """
    # --- Resolve EAF shares ---
    if eaf_shares is None:
        from demandforge.fetch.industry_data import _EAF_SHARE_2019
        eaf_dict = _EAF_SHARE_2019
    elif isinstance(eaf_shares, pd.DataFrame):
        if "country" not in eaf_shares.columns or "eaf_share" not in eaf_shares.columns:
            raise ValueError(
                "eaf_shares DataFrame must have 'country' and 'eaf_share' columns"
            )
        eaf_dict = dict(zip(eaf_shares["country"], eaf_shares["eaf_share"]))
    else:
        eaf_dict = dict(eaf_shares)

    # --- Schema validation ---
    jrc_required = {"country", "steel_production_kt"}
    jrc_missing = jrc_required - set(jrc_df.columns)
    if jrc_missing:
        # Fallback: accept 'steel_production' as an alias
        if "steel_production" in jrc_df.columns and "steel_production_kt" not in jrc_df.columns:
            jrc_df = jrc_df.rename(columns={"steel_production": "steel_production_kt"})
            jrc_missing = jrc_required - set(jrc_df.columns)
        if jrc_missing:
            raise ValueError(
                f"JRC DataFrame missing required columns: {jrc_missing}. "
                f"Available: {list(jrc_df.columns)}"
            )

    share_cols = [
        "industry_steel_blastfurnace_bof_share",
        "industry_steel_dri_network_gas_share",
        "industry_steel_dri_hydrogen_share",
    ]
    tyndp_required = {"country"} | set(share_cols)
    tyndp_missing = tyndp_required - set(tyndp_df.columns)
    if tyndp_missing:
        raise ValueError(
            f"TYNDP DataFrame missing required columns: {tyndp_missing}. "
            f"Available: {list(tyndp_df.columns)}"
        )

    # --- Merge ---
    merged = jrc_df[["country", "steel_production_kt"]].merge(
        tyndp_df[["country"] + share_cols],
        on="country",
        how="left",
    )

    # --- Validate TYNDP shares ---
    for col in share_cols:
        vals = merged[col].dropna()
        if (vals < -1e-9).any() or (vals > 1.0 + 1e-9).any():
            raise ValueError(
                f"Share column '{col}' out of [0, 1]: "
                f"min={vals.min():.4f}, max={vals.max():.4f}. "
                f"Are values still in percentage form (0-100)?"
            )

    # Warn about missing TYNDP matches
    missing_tyndp = merged[merged[share_cols[0]].isna()]["country"].tolist()
    if missing_tyndp:
        logger.warning(
            f"No TYNDP shares for countries: {missing_tyndp}. "
            f"These will have NaN route volumes and must be handled downstream."
        )

    # --- Validate primary-route shares sum ≈ 1.0 ---
    primary_share_sum = (
        merged["industry_steel_blastfurnace_bof_share"]
        + merged["industry_steel_dri_network_gas_share"]
        + merged["industry_steel_dri_hydrogen_share"]
    )
    bad_sum = merged[primary_share_sum.notna() & ((primary_share_sum - 1.0).abs() > 0.05)]
    if len(bad_sum) > 0:
        for _, row in bad_sum.iterrows():
            s = (row["industry_steel_blastfurnace_bof_share"]
                 + row["industry_steel_dri_network_gas_share"]
                 + row["industry_steel_dri_hydrogen_share"])
            logger.warning(
                f"TYNDP primary-route shares for {row['country']} sum to {s:.3f} "
                f"(expected ~1.0). Shares will be renormalized."
            )

    # --- Resolve EAF share per country ---
    merged["eaf_share"] = merged["country"].map(eaf_dict).fillna(0.0)

    # Validate EAF shares
    bad_eaf = merged[(merged["eaf_share"] < -1e-9) | (merged["eaf_share"] > 1.0 + 1e-9)]
    if len(bad_eaf) > 0:
        raise ValueError(
            f"EAF shares out of [0, 1] for countries: "
            f"{bad_eaf[['country', 'eaf_share']].to_string()}"
        )

    # Warn if EAF share missing for countries with production
    for _, row in merged.iterrows():
        cc = row["country"]
        if row["steel_production_kt"] > 0 and cc not in eaf_dict:
            logger.warning(
                f"No EAF share for {cc} (steel={row['steel_production_kt']:.0f} kt). "
                f"Defaulting to 0.0 (all primary)."
            )

    # --- Compute route volumes ---
    merged["crude_steel_production_t_per_yr"] = merged["steel_production_kt"] * 1000.0

    # EAF (secondary) production — explicit, first-class
    merged["eaf_production_t_per_yr"] = (
        merged["crude_steel_production_t_per_yr"] * merged["eaf_share"]
    )

    # Primary = total - EAF
    merged["primary_production_t_per_yr"] = (
        merged["crude_steel_production_t_per_yr"] - merged["eaf_production_t_per_yr"]
    )

    # Renormalize primary-route shares to sum to 1.0 (defensive)
    raw_sum = (
        merged["industry_steel_blastfurnace_bof_share"]
        + merged["industry_steel_dri_network_gas_share"]
        + merged["industry_steel_dri_hydrogen_share"]
    )
    # Avoid division by zero for countries with no primary shares
    norm = np.where(raw_sum > 1e-9, raw_sum, 1.0)
    merged["_bf_norm"] = merged["industry_steel_blastfurnace_bof_share"] / norm
    merged["_dri_ch4_norm"] = merged["industry_steel_dri_network_gas_share"] / norm
    merged["_dri_h2_norm"] = merged["industry_steel_dri_hydrogen_share"] / norm

    # Apply normalized shares to PRIMARY (not total)
    merged["bf_bof_production_t_per_yr"] = (
        merged["primary_production_t_per_yr"] * merged["_bf_norm"]
    )
    merged["dri_ch4_production_t_per_yr"] = (
        merged["primary_production_t_per_yr"] * merged["_dri_ch4_norm"]
    )
    merged["dri_h2_production_t_per_yr"] = (
        merged["primary_production_t_per_yr"] * merged["_dri_h2_norm"]
    )

    # --- Mass balance validation ---
    # Check 1: primary routes sum to primary total
    primary_route_sum = (
        merged["bf_bof_production_t_per_yr"]
        + merged["dri_ch4_production_t_per_yr"]
        + merged["dri_h2_production_t_per_yr"]
    )
    primary_err = (merged["primary_production_t_per_yr"] - primary_route_sum).abs().max()
    if primary_err > 1e-6:
        raise ValueError(
            f"Primary mass balance violation: max error = {primary_err:.3e}"
        )

    # Check 2: total mass balance
    total_route_sum = (
        merged["eaf_production_t_per_yr"]
        + merged["bf_bof_production_t_per_yr"]
        + merged["dri_ch4_production_t_per_yr"]
        + merged["dri_h2_production_t_per_yr"]
    )
    total_err = (merged["crude_steel_production_t_per_yr"] - total_route_sum).abs().max()
    if total_err > 1e-6:
        raise ValueError(
            f"Total mass balance violation in steel base table: {total_err:.3e}"
        )

    # Check for negative production anywhere
    for col in ["eaf_production_t_per_yr", "primary_production_t_per_yr",
                 "bf_bof_production_t_per_yr", "dri_ch4_production_t_per_yr",
                 "dri_h2_production_t_per_yr"]:
        if (merged[col] < -1e-6).any():
            bad = merged[merged[col] < -1e-6][["country", col]]
            raise ValueError(
                f"Negative values in {col}:\n{bad.to_string()}"
            )

    # Store normalized shares (of primary) for output
    merged["blastfurnace_bof_share"] = merged["_bf_norm"]
    merged["dri_natural_gas_share"] = merged["_dri_ch4_norm"]
    merged["dri_hydrogen_share"] = merged["_dri_h2_norm"]

    merged["year"] = reference_year

    # Select output columns
    output_cols = [
        "country", "year",
        "crude_steel_production_t_per_yr",
        "primary_production_t_per_yr",
        "bf_bof_production_t_per_yr",
        "dri_ch4_production_t_per_yr",
        "dri_h2_production_t_per_yr",
        "eaf_production_t_per_yr",
        "blastfurnace_bof_share",
        "dri_natural_gas_share",
        "dri_hydrogen_share",
        "eaf_share",
    ]

    result = merged[output_cols].copy()

    logger.info(
        f"Built steel base table: {len(result)} countries, "
        f"total EU steel = {result['crude_steel_production_t_per_yr'].sum():,.0f} t/yr, "
        f"EAF share (avg) = {result['eaf_share'].mean():.1%}"
    )

    return result


def validate_steel_base_table(base_df: pd.DataFrame) -> None:
    """Validate a steel base table for schema, ranges, and consistency.

    Checks:
    - Required columns present
    - No duplicate countries
    - Non-negativity of all production columns
    - Total mass balance: crude_steel = EAF + BF + DRI_CH4 + DRI_H2
    - Primary mass balance: primary = BF + DRI_CH4 + DRI_H2 (if present)

    Args:
        base_df: Output from :func:`build_steel_base_table`.

    Raises:
        ValueError: On any validation failure.
    """
    required = {
        "country", "crude_steel_production_t_per_yr",
        "bf_bof_production_t_per_yr", "dri_ch4_production_t_per_yr",
        "dri_h2_production_t_per_yr", "eaf_production_t_per_yr",
    }
    missing = required - set(base_df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    # Uniqueness
    if base_df["country"].duplicated().any():
        dups = base_df[base_df["country"].duplicated()]["country"].tolist()
        raise ValueError(f"Duplicate countries: {dups}")

    # Non-negativity
    prod_cols = [
        "crude_steel_production_t_per_yr", "bf_bof_production_t_per_yr",
        "dri_ch4_production_t_per_yr", "dri_h2_production_t_per_yr",
        "eaf_production_t_per_yr",
    ]
    if "primary_production_t_per_yr" in base_df.columns:
        prod_cols.append("primary_production_t_per_yr")
    for col in prod_cols:
        if (base_df[col] < -1e-6).any():
            raise ValueError(f"Negative values in {col}")

    # Total mass balance: crude_steel = EAF + BF + DRI_CH4 + DRI_H2
    route_sum = (
        base_df["eaf_production_t_per_yr"]
        + base_df["bf_bof_production_t_per_yr"]
        + base_df["dri_ch4_production_t_per_yr"]
        + base_df["dri_h2_production_t_per_yr"]
    )
    err = (base_df["crude_steel_production_t_per_yr"] - route_sum).abs().max()
    if err > 1e-6:
        raise ValueError(f"Mass balance violation: max error = {err:.3e}")

    # Primary mass balance (if primary column present)
    if "primary_production_t_per_yr" in base_df.columns:
        primary_sum = (
            base_df["bf_bof_production_t_per_yr"]
            + base_df["dri_ch4_production_t_per_yr"]
            + base_df["dri_h2_production_t_per_yr"]
        )
        p_err = (base_df["primary_production_t_per_yr"] - primary_sum).abs().max()
        if p_err > 1e-6:
            raise ValueError(
                f"Primary mass balance violation: max error = {p_err:.3e}"
            )

    logger.info("Steel base table validation passed")


def compute_steel_h2_demand(
    dri_h2_production_t_per_yr: np.ndarray,
    h2_per_t_dri: float,
) -> np.ndarray:
    """Compute H2 demand from DRI-H2 route production volume.

    Args:
        dri_h2_production_t_per_yr: Annual DRI-H2 production (t/yr).
        h2_per_t_dri: H2 specific consumption (t H2 per t DRI iron).

    Returns:
        Array of H2 demand (t/yr), one per year.

    Raises:
        ValueError: If h2_per_t_dri is negative or if H2 demand becomes negative.
    """
    if h2_per_t_dri < 0:
        raise ValueError(f"h2_per_t_dri must be non-negative, got {h2_per_t_dri}")

    h2_demand = dri_h2_production_t_per_yr * h2_per_t_dri

    if not np.all(h2_demand >= 0):
        raise ValueError("Negative H2 demand detected")

    logger.info(f"Computed steel H2 demand using h2_per_t_dri={h2_per_t_dri}")

    return h2_demand
