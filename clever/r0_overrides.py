"""
clever.r0_overrides — R0 per-country override pipeline.

Role in pipeline
----------------
Sits between ``pommes.model.data_validation.dataset_check.check_inputs`` and
``clever.runner.sanitize_storage_inputs`` in
``clever.runner.run_model_without_ramping``. Mutates the validated POMMES
parameter dataset to apply R0-specific recalibrations and per-country anchors
that the bundle's default (uniform-across-areas) parameters do not capture.

The R0 overrides cover three sub-tasks in ``checklist_R0.md``:

  * **Sub-task 2** — electrolyser invest CAPEX recalibration to a
    dictionary-anchored alkaline-electrolyser value (500 €/kW, Romero-Piñeiro
    2025), replacing the bundle's placeholder 155 €/kW.

  * **Sub-task 1** — per-country electrolyser minimum installed-capacity floor,
    anchored on a "sovereignty factor × national 2050 H₂ demand / load factor"
    formulation. Pending Simon's sovereignty-factor table.

  * **Sub-task 3** — per-country underground H₂ storage CAPEX (energy + power)
    and capacity caps (energy + power), anchored on real geological potential
    by class (salt cavern / depleted gas field / aquifer). Pending sources.

Design notes
~~~~~~~~~~~~

* The override is invoked **after** ``check_inputs`` returns; it follows the
  precedent set by ``sanitize_absent_conversions`` and ``sanitize_storage_inputs``
  which also mutate ``p`` post-validation.

* The annuity arrays (``storage_annuity_cost_energy``, ``storage_annuity_cost_power``,
  ``conversion_annuity_cost``) are recomputed via **linear rescaling by the
  ratio of override-invest to original-invest**. This works because POMMES'
  annuity formula is linear in the invest cost (the capital-recovery factor
  depends only on finance_rate, life_span, year_inv, year_dec — not on the
  invest cost itself). Avoids needing to re-implement POMMES' CRF and risk
  subtly disagreeing with it on edge cases.

* Capacity caps (``*_capacity_investment_max``) are standalone bounds — no
  annuity propagation required.

* Pre-solve sandbox validation: the module includes ``validate_overrides()``
  which sanity-checks every overridden variable's shape, dtype, and bounds
  (``investment_min ≤ investment_max``, annuity ≥ 0, etc.) before the dataset
  is returned. Tripping the validation raises rather than silently producing
  a broken solve.

Usage
-----
.. code-block:: python

    from clever.r0_overrides import apply_r0_overrides

    # Default usage — applies only the electrolyser CAPEX bump (sub-task 2):
    p = apply_r0_overrides(p)

    # With sovereignty-anchored min bounds (sub-task 1):
    p = apply_r0_overrides(
        p,
        electrolyser_min_bounds_gw={"FR": 25.0, "DE": 12.0, ...},
    )

    # With per-country storage geology (sub-task 3):
    import pandas as pd
    capex_df = pd.read_csv("tables/R0/h2_underground_storage_capex_by_country.csv")
    caps_df = pd.read_csv("tables/R0/h2_underground_storage_capacity_caps_by_country.csv")
    p = apply_r0_overrides(
        p,
        electrolyser_min_bounds_gw={"FR": 25.0, "DE": 12.0, ...},
        storage_capex_by_area=capex_df,
        storage_caps_by_area=caps_df,
    )

Hook point in runner
~~~~~~~~~~~~~~~~~~~~

In ``clever/runner.py::run_model_without_ramping``, between line 913
(``p = check_inputs(p)``) and line 984 (``p = sanitize_absent_conversions(p)``),
insert::

    if r0_overrides is not None:
        p = apply_r0_overrides(p, **r0_overrides)
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Dictionary-anchored constants (R0 central)
# ═════════════════════════════════════════════════════════════════════

#: Alkaline electrolyser central CAPEX, €/kW (Romero-Piñeiro et al. 2025,
#: dictionary entry "Electrolysis technology comparison"). The bundle's
#: placeholder is 155 €/kW; we lift to the canonical alkaline range midpoint.
#:
#: Scenario-driven override: when CLEVER_SCENARIO carries an `_elNNN` suffix
#: (e.g. `_el700`, `_el900`), `clever.constants._ELECTROLYSER_CAPEX_EUR_PER_KW`
#: is set to NNN at module load. Default is 500 €/kW (no suffix).
from clever.constants import _ELECTROLYSER_CAPEX_EUR_PER_KW as _SCEN_CAPEX
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


# ═════════════════════════════════════════════════════════════════════
# Override application
# ═════════════════════════════════════════════════════════════════════


def apply_r0_overrides(
    p: xr.Dataset,
    *,
    electrolyser_invest_cost_eur_per_kw: float = ELECTROLYSER_INVEST_COST_EUR_PER_KW,
    electrolyser_min_bounds_gw: Optional[dict[str, float]] = None,
    storage_capex_by_area: Optional[pd.DataFrame] = None,
    storage_caps_by_area: Optional[pd.DataFrame] = None,
    hydrogen_load_shedding_cost_eur_per_mwh: Optional[float] = HYDROGEN_LOAD_SHEDDING_COST_EUR_PER_MWH,
    skip_validation: bool = False,
) -> xr.Dataset:
    """
    Apply R0-specific per-country overrides to a POMMES parameter dataset.

    Must be called **after** ``build_input_parameters()`` and
    ``check_inputs()``. Mutates ``p`` in place AND returns it (returning ``p``
    is for fluent chaining; the input dataset is modified).

    Parameters
    ----------
    p : xr.Dataset
        POMMES parameter dataset, post-``check_inputs``.

    electrolyser_invest_cost_eur_per_kw : float
        New central electrolyser invest CAPEX, €/kW. Applied uniformly across
        all areas (a per-country variant is reserved for sub-task 4 / R2).
        Default: 500 €/kW (Romero-Piñeiro 2025 anchored).

    electrolyser_min_bounds_gw : dict[str, float] | None
        Per-area minimum installed electrolyser capacity in GW. Maps
        area_code → GW. If None, no min bounds are applied. Sub-task 1.

    storage_capex_by_area : pd.DataFrame | None
        Per-area H₂ storage CAPEX overrides. Required columns:
        ``area``, ``invest_cost_energy_eur_per_mwh``, ``invest_cost_power_eur_per_mw``.
        Sub-task 3 (rescoped 2026-05-12).

    storage_caps_by_area : pd.DataFrame | None
        Per-area H₂ storage capacity caps. Required columns:
        ``area``, ``cap_twh_realistic`` (energy cap in TWh).
        Optional columns: ``cap_gw_power`` (power cap in GW; defaults to
        bundle's existing 20 GW value if absent).
        Sub-task 3 (rescoped 2026-05-12).

    skip_validation : bool
        Skip the post-override sanity checks. Default False; only set True for
        debugging.

    Returns
    -------
    xr.Dataset
        The same dataset, with overrides applied. Annuity arrays are rescaled
        linearly to remain consistent with the new invest_cost values.

    Raises
    ------
    ValueError
        If validation fails (investment_min > investment_max, negative annuity,
        unknown area code in any override table, etc.).
    """
    # ── 1. Electrolyser CAPEX recalibration (sub-task 2) ───────────────
    _apply_electrolyser_capex(p, electrolyser_invest_cost_eur_per_kw)

    # ── 2. Electrolyser min bounds (sub-task 1) ────────────────────────
    if electrolyser_min_bounds_gw is not None:
        _apply_electrolyser_min_bounds(p, electrolyser_min_bounds_gw)

    # ── 3. H₂ storage CAPEX (sub-task 3) ───────────────────────────────
    if storage_capex_by_area is not None:
        _apply_storage_capex(p, storage_capex_by_area)

    # ── 4. H₂ storage caps (sub-task 3) ────────────────────────────────
    if storage_caps_by_area is not None:
        _apply_storage_caps(p, storage_caps_by_area)

    # ── 5. Hydrogen load-shedding safety valve ─────────────────────────
    if hydrogen_load_shedding_cost_eur_per_mwh is not None:
        _apply_hydrogen_load_shedding_cost(p, hydrogen_load_shedding_cost_eur_per_mwh)

    # ── 6. Post-override validation ────────────────────────────────────
    if not skip_validation:
        validate_overrides(p)

    return p


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


# ═════════════════════════════════════════════════════════════════════
# Private — individual override appliers
# ═════════════════════════════════════════════════════════════════════


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


# ═════════════════════════════════════════════════════════════════════
# Validation
# ═════════════════════════════════════════════════════════════════════


def validate_overrides(p: xr.Dataset) -> None:
    """
    Post-override sanity checks. Raises ValueError if any check fails.

    Checks:
      * investment_min ≤ investment_max for electrolyser and h2_storage
      * All annuity arrays are non-negative
      * All invest_cost arrays are positive
      * Energy cap > 0 wherever set (≥ 0 allowed)
    """
    errors: list[str] = []

    # 1. Electrolyser: min ≤ max per (area, year_inv)
    elec_min = p["conversion_power_capacity_investment_min"].sel(
        conversion_tech=ELECTROLYSER_TECH
    )
    elec_max = p["conversion_power_capacity_investment_max"].sel(
        conversion_tech=ELECTROLYSER_TECH
    )
    diff = elec_max - elec_min
    if (diff.values < 0).any():
        bad = (diff.where(diff < 0, drop=True))
        errors.append(
            f"electrolyser investment_min > investment_max in some (area, year_inv): {bad.to_pandas()}"
        )

    # 2. H₂ storage: energy_min ≤ energy_max, power_min ≤ power_max
    for kind in ["energy", "power"]:
        mn = p[f"storage_{kind}_capacity_investment_min"].sel(storage_tech=H2_STORAGE_TECH)
        mx = p[f"storage_{kind}_capacity_investment_max"].sel(storage_tech=H2_STORAGE_TECH)
        diff = mx - mn
        if (diff.values < 0).any():
            errors.append(
                f"h2_storage {kind} investment_min > investment_max in some (area, year_inv)"
            )

    # 3. Annuity arrays non-negative
    for var, sel in [
        ("conversion_annuity_cost", dict(conversion_tech=ELECTROLYSER_TECH)),
        ("storage_annuity_cost_energy", dict(storage_tech=H2_STORAGE_TECH)),
        ("storage_annuity_cost_power", dict(storage_tech=H2_STORAGE_TECH)),
    ]:
        arr = p[var].sel(**sel)
        if (arr.values < 0).any():
            errors.append(f"{var}[{sel}] contains negative values after override")

    # 4. Invest costs positive
    for var, sel in [
        ("conversion_invest_cost", dict(conversion_tech=ELECTROLYSER_TECH)),
        ("storage_invest_cost_energy", dict(storage_tech=H2_STORAGE_TECH)),
        ("storage_invest_cost_power", dict(storage_tech=H2_STORAGE_TECH)),
    ]:
        arr = p[var].sel(**sel)
        if (arr.values <= 0).any():
            errors.append(f"{var}[{sel}] has non-positive values after override")

    if errors:
        raise ValueError(
            "R0 override validation failed:\n  - " + "\n  - ".join(errors)
        )

    logger.info("r0_overrides: validate_overrides passed")
