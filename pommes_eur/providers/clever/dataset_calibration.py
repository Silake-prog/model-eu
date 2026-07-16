"""
pommes_eur.providers.clever.dataset_calibration — CLEVER per-country dataset calibration.

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

    from pommes_eur.providers.clever.dataset_calibration import apply_r0_overrides

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

import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Dictionary-anchored constants (R0 central)
# ═════════════════════════════════════════════════════════════════════


# ═════════════════════════════════════════════════════════════════════
# Override application
# ═════════════════════════════════════════════════════════════════════


# Override mutators + shared constants live in _appliers.py
from pommes_eur.providers.clever._appliers import (  # noqa: E402
    ELECTROLYSER_INVEST_COST_EUR_PER_KW,
    ELECTROLYSER_TECH,
    H2_STORAGE_TECH,
    HYDROGEN_LOAD_SHEDDING_COST_EUR_PER_MWH,
    _apply_hydrogen_load_shedding_cost,
    _apply_electrolyser_capex,
    _apply_electrolyser_min_bounds,
    _apply_storage_capex,
    _apply_storage_caps,
)


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


# ═════════════════════════════════════════════════════════════════════
# Private — individual override appliers
# ═════════════════════════════════════════════════════════════════════


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
