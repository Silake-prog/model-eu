"""Olefins decarbonization pathway mix and H₂ demand computation.

This module handles the technology mix for olefins production pathways and
their associated hydrogen demands.  It supports four production routes:

    1. **MTO** (methanol-to-olefins): CO₂ + 3H₂ → CH₃OH → olefins (SAPO-34).
       H₂ intensity: ~0.42 t H₂ / t olefin.
    2. **Bio-naphtha** (lipid HVO → steam cracker):
       H₂ intensity: ~0.09 t H₂ / t olefin.
    3. **Chemical recycling** (plastic waste pyrolysis → hydrotreat → crack):
       H₂ intensity: ~0.22 t H₂ / t olefin.
    4. **Fossil** (conventional naphtha cracking, upstream HT only):
       H₂ intensity: ~0.003 t H₂ / t olefin.

The fossil share is always the complement of the three decarbonization
routes (mass balance enforced).  Each green pathway has independent ramp
timing to reflect technology readiness: bio-naphtha is closest to
commercial scale, chemical recycling is mid-TRL, and MTO at scale
requires CO₂ capture + large electrolysis capacity.

Naphtha supply constraint:
    The fossil route depends on naphtha from refineries.  When the CONCAWE
    scenario phases out refinery capacity, available naphtha shrinks.
    :func:`apply_naphtha_supply_constraint` endogenously caps fossil share
    against the naphtha supply trajectory from the refinery module, and
    redistributes excess production to the green pathways proportionally.

Authors:
    Simon Brigode — PERSEE Lab, Mines Paris PSL
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from demandforge.process._utils import linear_ramp

logger = logging.getLogger(__name__)

# Pathway names in canonical order
OLEFIN_PATHWAYS = ("mto", "bio_naphtha", "chem_recycling", "fossil")

# Map pathway key → H₂ intensity (t H₂ / t olefin).
# These values are duplicated from load_projection/constants.py to avoid
# a circular import (process → load_projection → hydrogen → process).
# See constants.py for full stoichiometric derivations.
_H2_INTENSITY: dict[str, float] = {
    "mto": 0.42,           # MTO: 0.19/0.45 ≈ 0.422, rounded
    "bio_naphtha": 0.09,   # HVO → cracker: 0.035/0.85/0.45 ≈ 0.092
    "chem_recycling": 0.22, # Pyrolysis oil → HT → crack: 0.06/0.60/0.45 ≈ 0.222
    "fossil": 0.003,        # Upstream naphtha HT only: 0.0012×1.14/0.45 ≈ 0.003
}


def build_olefins_pathway_mix(
    years: np.ndarray,
    *,
    mto_share_2050: float = 0.20,
    mto_ramp_start: int = 2030,
    mto_ramp_end: int = 2045,
    bio_naphtha_share_2050: float = 0.15,
    bio_naphtha_ramp_start: int = 2024,
    bio_naphtha_ramp_end: int = 2035,
    chem_recycling_share_2050: float = 0.10,
    chem_recycling_ramp_start: int = 2027,
    chem_recycling_ramp_end: int = 2038,
) -> pd.DataFrame:
    """Build annual olefins production pathway shares.

    Each green pathway ramps independently from 0 to its target share.
    The fossil share is the complement (1 − Σ green shares), enforced
    to be non-negative.

    The function validates that the sum of green pathway targets does not
    exceed 1.0, and that all shares remain in [0, 1] at every year.

    Args:
        years: Array of scenario years (must be sorted).
        mto_share_2050: Target MTO share of olefins production at ramp end.
        mto_ramp_start: Year MTO ramp begins.
        mto_ramp_end: Year MTO reaches target share.
        bio_naphtha_share_2050: Target bio-naphtha share at ramp end.
        bio_naphtha_ramp_start: Year bio-naphtha ramp begins.
        bio_naphtha_ramp_end: Year bio-naphtha reaches target share.
        chem_recycling_share_2050: Target chemical recycling share at ramp end.
        chem_recycling_ramp_start: Year chemical recycling ramp begins.
        chem_recycling_ramp_end: Year chemical recycling reaches target share.

    Returns:
        DataFrame with columns:
            year, mto_share, bio_naphtha_share, chem_recycling_share,
            fossil_share, h2_intensity_blend_t_per_t
            (blend H₂ intensity = Σ share_i × h2_intensity_i)

    Raises:
        ValueError: If years is empty; if the sum of green pathway targets
            exceeds 1.0; if any share is negative at any year; or if shares
            do not sum to 1.0 within tolerance.
    """
    if len(years) == 0:
        raise ValueError("years array cannot be empty")

    # --- Validate target shares ---
    green_sum = mto_share_2050 + bio_naphtha_share_2050 + chem_recycling_share_2050
    if green_sum > 1.0 + 1e-9:
        raise ValueError(
            f"Sum of green pathway targets ({green_sum:.3f}) exceeds 1.0. "
            f"MTO={mto_share_2050}, bio_naphtha={bio_naphtha_share_2050}, "
            f"chem_recycling={chem_recycling_share_2050}"
        )

    # --- Compute individual pathway ramps ---
    mto_shares = linear_ramp(
        years, mto_ramp_start, mto_ramp_end,
        start_value=0.0, end_value=mto_share_2050,
    )
    bio_naphtha_shares = linear_ramp(
        years, bio_naphtha_ramp_start, bio_naphtha_ramp_end,
        start_value=0.0, end_value=bio_naphtha_share_2050,
    )
    chem_recycling_shares = linear_ramp(
        years, chem_recycling_ramp_start, chem_recycling_ramp_end,
        start_value=0.0, end_value=chem_recycling_share_2050,
    )

    # --- Fossil = complement ---
    fossil_shares = 1.0 - mto_shares - bio_naphtha_shares - chem_recycling_shares

    # --- Clip to prevent floating-point noise from creating negatives ---
    fossil_shares = np.clip(fossil_shares, 0.0, 1.0)
    mto_shares = np.clip(mto_shares, 0.0, 1.0)
    bio_naphtha_shares = np.clip(bio_naphtha_shares, 0.0, 1.0)
    chem_recycling_shares = np.clip(chem_recycling_shares, 0.0, 1.0)

    # --- Validate share sum = 1.0 ---
    total_shares = mto_shares + bio_naphtha_shares + chem_recycling_shares + fossil_shares
    if not np.allclose(total_shares, 1.0, atol=1e-9):
        max_err = np.abs(total_shares - 1.0).max()
        raise ValueError(
            f"Pathway shares do not sum to 1.0: max error = {max_err:.3e}"
        )

    # --- Blend H₂ intensity ---
    h2_blend = (
        mto_shares * _H2_INTENSITY["mto"]
        + bio_naphtha_shares * _H2_INTENSITY["bio_naphtha"]
        + chem_recycling_shares * _H2_INTENSITY["chem_recycling"]
        + fossil_shares * _H2_INTENSITY["fossil"]
    )

    result = pd.DataFrame({
        "year": years.astype(int),
        "mto_share": mto_shares,
        "bio_naphtha_share": bio_naphtha_shares,
        "chem_recycling_share": chem_recycling_shares,
        "fossil_share": fossil_shares,
        "h2_intensity_blend_t_per_t": h2_blend,
    })

    logger.info(
        f"Built olefins pathway mix: "
        f"MTO {mto_share_2050:.0%} ({mto_ramp_start}-{mto_ramp_end}), "
        f"bio-naphtha {bio_naphtha_share_2050:.0%} "
        f"({bio_naphtha_ramp_start}-{bio_naphtha_ramp_end}), "
        f"chem-recyc {chem_recycling_share_2050:.0%} "
        f"({chem_recycling_ramp_start}-{chem_recycling_ramp_end}), "
        f"fossil residual at 2050: {fossil_shares[-1]:.0%}"
    )

    return result


def apply_naphtha_supply_constraint(
    pathway_mix: pd.DataFrame,
    eu_production_t: np.ndarray,
    naphtha_supply_mt: np.ndarray,
    cracker_yield: float = 0.45,
) -> pd.DataFrame:
    """Cap the fossil pathway share against available naphtha from refineries.

    The fossil olefins route (conventional naphtha steam cracking) is
    physically limited by the naphtha supply from refineries.  When the
    CONCAWE scenario phases out refinery capacity, available naphtha
    shrinks and the fossil share must be capped.

    If the fossil share exceeds the supply-constrained ceiling at any year,
    the excess is redistributed **proportionally** to the three green
    pathways (MTO, bio-naphtha, chemical recycling), preserving their
    relative ratios.

    The constraint is::

        fossil_share(t) ≤ naphtha_supply(t) × 1e6 × cracker_yield
                          / eu_production(t)

    Args:
        pathway_mix: Output from :func:`build_olefins_pathway_mix`.
            Modified in-place and returned.
        eu_production_t: Annual EU-wide total olefins production (t/yr),
            one value per year.  Computed as Σ countries' base × CAGR.
        naphtha_supply_mt: Annual petrochemical naphtha supply (Mt/yr),
            from :func:`~demandforge.process.refinery.get_naphtha_for_crackers`.
        cracker_yield: Steam cracker olefin yield (t olefin / t naphtha).

    Returns:
        The (potentially modified) ``pathway_mix`` DataFrame with updated
        shares and blend H₂ intensity.  Unchanged rows are not modified.

    Raises:
        ValueError: If array lengths don't match.
    """
    n = len(pathway_mix)
    if len(eu_production_t) != n or len(naphtha_supply_mt) != n:
        raise ValueError(
            f"Array length mismatch: pathway_mix={n}, "
            f"eu_production={len(eu_production_t)}, "
            f"naphtha_supply={len(naphtha_supply_mt)}"
        )

    # Max fossil olefin production from naphtha supply (Mt/yr → t/yr)
    max_fossil_t = naphtha_supply_mt * 1e6 * cracker_yield

    # Current fossil production
    fossil_prod = eu_production_t * pathway_mix["fossil_share"].values

    # Find years where fossil exceeds supply
    constrained = fossil_prod > max_fossil_t + 1e-3  # small tolerance

    n_constrained = constrained.sum()
    if n_constrained == 0:
        logger.info("Naphtha supply constraint: no years constrained.")
        return pathway_mix

    logger.warning(
        f"Naphtha supply constraint active for {n_constrained} years "
        f"({pathway_mix['year'].values[constrained][0]}–"
        f"{pathway_mix['year'].values[constrained][-1]}). "
        f"Fossil share will be capped and excess redistributed."
    )

    # Work on a copy of the arrays
    mix = pathway_mix.copy()
    fos = mix["fossil_share"].values.copy()
    mto = mix["mto_share"].values.copy()
    bio = mix["bio_naphtha_share"].values.copy()
    rec = mix["chem_recycling_share"].values.copy()

    for i in np.where(constrained)[0]:
        if eu_production_t[i] <= 0:
            continue

        # New fossil ceiling
        new_fos = max_fossil_t[i] / eu_production_t[i]
        new_fos = np.clip(new_fos, 0.0, 1.0)
        excess = fos[i] - new_fos

        if excess <= 0:
            continue

        # Redistribute excess to green pathways proportionally
        green_sum = mto[i] + bio[i] + rec[i]
        if green_sum > 1e-12:
            mto[i] += excess * (mto[i] / green_sum)
            bio[i] += excess * (bio[i] / green_sum)
            rec[i] += excess * (rec[i] / green_sum)
        else:
            # All green shares are zero — split evenly
            mto[i] += excess / 3.0
            bio[i] += excess / 3.0
            rec[i] += excess / 3.0

        fos[i] = new_fos

    # Write back and recompute blend H₂ intensity
    mix["fossil_share"] = fos
    mix["mto_share"] = mto
    mix["bio_naphtha_share"] = bio
    mix["chem_recycling_share"] = rec
    mix["h2_intensity_blend_t_per_t"] = (
        mto * _H2_INTENSITY["mto"]
        + bio * _H2_INTENSITY["bio_naphtha"]
        + rec * _H2_INTENSITY["chem_recycling"]
        + fos * _H2_INTENSITY["fossil"]
    )

    # Validate shares still sum to 1.0
    total = mto + bio + rec + fos
    max_err = np.abs(total - 1.0).max()
    if max_err > 1e-6:
        raise ValueError(
            f"Shares do not sum to 1.0 after naphtha constraint: "
            f"max error = {max_err:.3e}"
        )

    # Log the impact at 2050 (or last year)
    last_yr = mix["year"].values[-1]
    last_idx = len(mix) - 1
    logger.info(
        f"  Constrained fossil share at {last_yr}: "
        f"{pathway_mix['fossil_share'].values[last_idx]:.1%} → "
        f"{fos[last_idx]:.1%} "
        f"(naphtha ceiling: {max_fossil_t[last_idx]:,.0f} t/yr, "
        f"EU production: {eu_production_t[last_idx]:,.0f} t/yr)"
    )

    return mix


def compute_olefins_h2_demand(
    olefins_production_t: np.ndarray,
    pathway_mix: pd.DataFrame,
) -> pd.DataFrame:
    """Compute per-pathway H₂ demand from production volumes and pathway mix.

    Args:
        olefins_production_t: Annual total olefins production (t/yr),
            one value per year.
        pathway_mix: Output from :func:`build_olefins_pathway_mix`.

    Returns:
        DataFrame with columns:
            year, olefins_production_t_per_yr,
            mto_production_t, bio_naphtha_production_t,
            chem_recycling_production_t, fossil_production_t,
            h2_demand_mto_t, h2_demand_bio_naphtha_t,
            h2_demand_chem_recycling_t, h2_demand_fossil_t,
            h2_demand_total_t_per_yr

    Raises:
        ValueError: If production array length doesn't match pathway mix rows;
            if negative production or H₂ demand is detected; or if mass
            balance is violated.
    """
    if len(olefins_production_t) != len(pathway_mix):
        raise ValueError(
            f"Production array length ({len(olefins_production_t)}) "
            f"doesn't match pathway mix rows ({len(pathway_mix)})"
        )

    prod = olefins_production_t

    # Per-pathway production volumes
    mto_prod = prod * pathway_mix["mto_share"].values
    bio_prod = prod * pathway_mix["bio_naphtha_share"].values
    rec_prod = prod * pathway_mix["chem_recycling_share"].values
    fos_prod = prod * pathway_mix["fossil_share"].values

    # Per-pathway H₂ demand
    h2_mto = mto_prod * _H2_INTENSITY["mto"]
    h2_bio = bio_prod * _H2_INTENSITY["bio_naphtha"]
    h2_rec = rec_prod * _H2_INTENSITY["chem_recycling"]
    h2_fos = fos_prod * _H2_INTENSITY["fossil"]
    h2_total = h2_mto + h2_bio + h2_rec + h2_fos

    result = pd.DataFrame({
        "year": pathway_mix["year"].values,
        "olefins_production_t_per_yr": prod,
        "mto_production_t": mto_prod,
        "bio_naphtha_production_t": bio_prod,
        "chem_recycling_production_t": rec_prod,
        "fossil_production_t": fos_prod,
        "h2_demand_mto_t": h2_mto,
        "h2_demand_bio_naphtha_t": h2_bio,
        "h2_demand_chem_recycling_t": h2_rec,
        "h2_demand_fossil_t": h2_fos,
        "h2_demand_total_t_per_yr": h2_total,
    })

    # --- Validation ---
    # Mass balance: pathway production sums to total
    route_sum = mto_prod + bio_prod + rec_prod + fos_prod
    mass_err = np.abs(prod - route_sum).max()
    if mass_err > 1e-6:
        raise ValueError(
            f"Olefins mass balance violation: max error = {mass_err:.3e}"
        )

    # Non-negativity
    for col in ["h2_demand_total_t_per_yr", "mto_production_t",
                "bio_naphtha_production_t", "chem_recycling_production_t",
                "fossil_production_t"]:
        if (result[col] < -1e-9).any():
            raise ValueError(f"Negative values in {col}")

    # H₂ consistency
    h2_sum = h2_mto + h2_bio + h2_rec + h2_fos
    h2_err = np.abs(h2_total - h2_sum).max()
    if h2_err > 1e-9:
        raise ValueError(f"H₂ demand consistency error: {h2_err:.3e}")

    logger.info(
        f"Computed olefins H₂ demand: "
        f"total H₂ at final year = {h2_total[-1]:,.0f} t/yr "
        f"(MTO: {h2_mto[-1]:,.0f}, bio: {h2_bio[-1]:,.0f}, "
        f"rec: {h2_rec[-1]:,.0f}, fossil: {h2_fos[-1]:,.0f})"
    )

    return result
