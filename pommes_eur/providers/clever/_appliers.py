"""pommes_eur.providers.clever._appliers — the R0 per-country override mutators.

The five in-place mutators applied to the assembled POMMES dataset by
``dataset_calibration.apply_r0_overrides`` (electrolyser CAPEX / sovereignty min-bounds,
H2 storage CAPEX / caps, hydrogen load-shedding cost), plus the shared tech-name and
default-cost constants. Split verbatim out of the CLEVER R0 calibration pipeline.
"""
from __future__ import annotations

import logging

import numpy as np  # noqa: F401 (used by the mutators)
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)

#: Alkaline electrolyser central CAPEX, €/kW (Romero-Piñeiro et al. 2025,
#: dictionary entry "Electrolysis technology comparison"). The bundle's
#: placeholder is 155 €/kW; we lift to the canonical alkaline range midpoint.
#:
#: Scenario-driven override: when CLEVER_SCENARIO carries an `_elNNN` suffix
#: (e.g. `_el700`, `_el900`), `clever.constants._ELECTROLYSER_CAPEX_EUR_PER_KW`
#: is set to NNN at module load. Default is 500 €/kW (no suffix).
from pommes_eur.constants import _ELECTROLYSER_CAPEX_EUR_PER_KW as _SCEN_CAPEX


ELECTROLYSER_INVEST_COST_EUR_PER_KW = _SCEN_CAPEX


#: Electrolyser conversion-tech name as registered in the POMMES bundle.
ELECTROLYSER_TECH = "electrolysis"


#: H₂ storage tech name as registered in the POMMES bundle.
H2_STORAGE_TECH = "h2_storage"


#: Hydrogen resource name (matches POMMES resource coord).
HYDROGEN_RESOURCE = "hydrogen"


#: Default safety-valve cost for unmet hydrogen demand (€/MWh of H₂).
#: Matched to clever.constants.DEFAULT_LOAD_SHEDDING_COST (30 000 €/MWh,
#: the bundle's electricity-VOLL anchor) so the LP only uses load-shedding
#: as a true infeasibility valve. Any non-zero shedding in the post-solve
#: diagnostics flags a parameter combination that's pinching too hard
#: somewhere — typically a min bound that exceeds local production
#: capability without import headroom. Worth perturbing in sub-task 6's
#: flatness probing to test sensitivity to the VOLL assumption.
HYDROGEN_LOAD_SHEDDING_COST_EUR_PER_MWH = 30_000.0


def _apply_hydrogen_load_shedding_cost(p: xr.Dataset, eur_per_mwh: float) -> None:
    """
    Set the cost of unmet hydrogen demand as a safety valve.

    POMMES carries ``load_shedding_cost`` per ``(area, resource, year_op)``.
    If this is NaN or unset for the hydrogen resource, the LP cannot relax
    H₂ balance and an aggressive min bound combined with tight storage caps
    can produce infeasibility. We set a high but finite cost so the LP only
    uses load-shedding when no other option exists; any non-zero shedding
    in the diagnostics post-solve is a flag, not a cost-optimal outcome.
    """
    if "load_shedding_cost" not in p:
        logger.warning(
            "r0_overrides: load_shedding_cost variable absent from dataset; "
            "skipping hydrogen safety-valve override"
        )
        return
    if HYDROGEN_RESOURCE not in p.coords["resource"].values:
        logger.warning(
            "r0_overrides: hydrogen not in resource coord; skipping safety-valve"
        )
        return

    sel = dict(resource=HYDROGEN_RESOURCE)
    p["load_shedding_cost"].loc[sel] = float(eur_per_mwh)
    logger.info(
        "r0_overrides: hydrogen load_shedding_cost set to %.0f €/MWh (safety valve)",
        eur_per_mwh,
    )


def _apply_electrolyser_capex(p: xr.Dataset, new_eur_per_kw: float) -> None:
    """
    Override conversion_invest_cost[electrolysis] uniformly to ``new_eur_per_kw * 1000``
    (€/MW), and rescale conversion_annuity_cost[electrolysis] by the same ratio.
    """
    new_eur_per_mw = float(new_eur_per_kw) * 1000.0
    sel = dict(conversion_tech=ELECTROLYSER_TECH)

    old_invest = p["conversion_invest_cost"].sel(**sel)
    # Robust extraction: use the FIRST POSITIVE-FINITE value in the flattened
    # array. flatten()[0] is fragile because MENA Variant B adds areas that
    # don't have "electrolysis" (they use "MENA_electrolysis"); those slots
    # are 0/NaN and may happen to sort to the front depending on insertion
    # order. The first positive value reliably grabs an EU area's CAPEX.
    arr = np.asarray(old_invest.values, dtype="float64").flatten()
    positive_finite = arr[np.isfinite(arr) & (arr > 0)]
    if positive_finite.size == 0:
        raise ValueError(
            f"Cannot rescale annuity for electrolysis: no positive-finite "
            f"invest_cost found across {arr.size} cells (all zero/NaN). "
            f"flatten[:5]={arr[:5].tolist()}"
        )
    old_mean = float(positive_finite[0])
    ratio = new_eur_per_mw / old_mean

    # Write new invest_cost uniformly across all (area, year_inv) cells.
    # MENA areas don't actually have an "electrolysis" tech (they use
    # "MENA_electrolysis"); POMMES gates presence on the conversion_factor,
    # not on invest_cost. So a non-zero invest_cost on a non-existent
    # area-tech slot is harmless — and required to pass validate_overrides()
    # which insists every cell be positive after override.
    p["conversion_invest_cost"].loc[sel] = new_eur_per_mw

    # Rescale annuity linearly (linear in invest_cost). For areas where the
    # baseline annuity was 0 (no electrolysis), the result stays 0 — fine,
    # since those cells are also gated out via conversion_factor.
    p["conversion_annuity_cost"].loc[sel] = (
        p["conversion_annuity_cost"].sel(**sel) * ratio
    )

    logger.info(
        "r0_overrides: electrolyser invest_cost %.0f → %.0f €/MW (ratio %.3f)",
        old_mean, new_eur_per_mw, ratio,
    )


def _apply_electrolyser_min_bounds(
    p: xr.Dataset, min_bounds_gw: dict[str, float]
) -> None:
    """Override conversion_power_capacity_investment_min[electrolysis, area, year_inv]."""
    sel_tech = dict(conversion_tech=ELECTROLYSER_TECH)
    known_areas = set(p["area"].values.tolist())

    unknown = set(min_bounds_gw) - known_areas
    if unknown:
        raise ValueError(f"Unknown area codes in electrolyser min bounds: {sorted(unknown)}")

    for area, gw in min_bounds_gw.items():
        if gw < 0:
            raise ValueError(f"Negative min bound {gw} GW for {area!r}")
        mw = float(gw) * 1000.0
        p["conversion_power_capacity_investment_min"].loc[
            dict(area=area, **sel_tech)
        ] = mw

    logger.info(
        "r0_overrides: electrolyser min bounds applied to %d areas: %s",
        len(min_bounds_gw), {a: f"{gw:.1f} GW" for a, gw in min_bounds_gw.items()},
    )


def _apply_storage_capex(p: xr.Dataset, capex_df: pd.DataFrame) -> None:
    """Override storage_invest_cost_energy / _power per area, rescale annuities."""
    required = {"area", "invest_cost_energy_eur_per_mwh", "invest_cost_power_eur_per_mw"}
    missing = required - set(capex_df.columns)
    if missing:
        raise ValueError(f"storage_capex_by_area missing columns: {sorted(missing)}")

    known_areas = set(p["area"].values.tolist())
    unknown = set(capex_df["area"]) - known_areas
    if unknown:
        raise ValueError(f"Unknown area codes in storage CAPEX overrides: {sorted(unknown)}")

    for _, row in capex_df.iterrows():
        area = row["area"]
        sel = dict(area=area, storage_tech=H2_STORAGE_TECH)

        # Energy invest cost
        new_e = float(row["invest_cost_energy_eur_per_mwh"])
        old_e = float(p["storage_invest_cost_energy"].sel(**sel).values.flatten()[0])
        if old_e <= 0:
            raise ValueError(
                f"storage_invest_cost_energy[{area}, h2_storage] = {old_e}; cannot rescale annuity"
            )
        ratio_e = new_e / old_e
        p["storage_invest_cost_energy"].loc[sel] = new_e
        p["storage_annuity_cost_energy"].loc[sel] = (
            p["storage_annuity_cost_energy"].sel(**sel) * ratio_e
        )

        # Power invest cost
        new_p = float(row["invest_cost_power_eur_per_mw"])
        old_p = float(p["storage_invest_cost_power"].sel(**sel).values.flatten()[0])
        if old_p <= 0:
            raise ValueError(
                f"storage_invest_cost_power[{area}, h2_storage] = {old_p}; cannot rescale annuity"
            )
        ratio_p = new_p / old_p
        p["storage_invest_cost_power"].loc[sel] = new_p
        p["storage_annuity_cost_power"].loc[sel] = (
            p["storage_annuity_cost_power"].sel(**sel) * ratio_p
        )

    logger.info(
        "r0_overrides: storage CAPEX overrides applied to %d areas", len(capex_df)
    )


def _apply_storage_caps(p: xr.Dataset, caps_df: pd.DataFrame) -> None:
    """Override storage_energy_capacity_investment_max and (optionally) _power_*."""
    required = {"area", "cap_twh_realistic"}
    missing = required - set(caps_df.columns)
    if missing:
        raise ValueError(f"storage_caps_by_area missing columns: {sorted(missing)}")

    known_areas = set(p["area"].values.tolist())
    unknown = set(caps_df["area"]) - known_areas
    if unknown:
        raise ValueError(f"Unknown area codes in storage cap overrides: {sorted(unknown)}")

    has_power_col = "cap_gw_power" in caps_df.columns

    for _, row in caps_df.iterrows():
        area = row["area"]
        sel = dict(area=area, storage_tech=H2_STORAGE_TECH)

        # Energy cap in MWh
        cap_mwh = float(row["cap_twh_realistic"]) * 1e6
        if cap_mwh < 0:
            raise ValueError(f"Negative energy cap for {area!r}: {cap_mwh} MWh")
        p["storage_energy_capacity_investment_max"].loc[sel] = cap_mwh

        if has_power_col and not pd.isna(row["cap_gw_power"]):
            cap_mw = float(row["cap_gw_power"]) * 1000.0
            if cap_mw < 0:
                raise ValueError(f"Negative power cap for {area!r}: {cap_mw} MW")
            p["storage_power_capacity_investment_max"].loc[sel] = cap_mw

    logger.info(
        "r0_overrides: storage capacity caps applied to %d areas", len(caps_df)
    )
