"""
pommes_eur.providers.clever.calibration_inputs — Glue between R0 per-country CSV
tables, DemandForge H₂ demand, and the override kwargs that the runner consumes.

Role in pipeline
----------------
Reads the three R0-policy CSV tables under ``tables/R0/`` and the live
DemandForge H₂ demand for the current bundle/year, computes the sovereignty-
anchored electrolyser min bounds, and returns a single ``dataset_calibration_kwargs``
dict ready to pass to ``clever.runner.run_model_without_ramping``.

Usage in adequacy_clean.ipynb
-----------------------------
.. code-block:: python

    from pommes_eur.providers.clever.calibration_inputs import build_calibration_inputs

    calibration_kwargs = build_calibration_inputs(
        bundle_name=DEMANDFORGE_BUNDLE,
        countries=COUNTRIES_MODELLED,
        model_year=MODEL_YEAR,
        tables_dir=_WS_ROOT / "tables" / "R0",
    )

    linopy_model = run_model_without_ramping(
        ...,
        dataset_calibration_kwargs=calibration_kwargs,
    )

Design notes
~~~~~~~~~~~~
* Sovereignty factors are STATIC POLICY HYPOTHESES, stored in CSV. H₂ demand
  is COMPUTED LIVE from DemandForge so the bound moves with the bundle.
* The CSV columns are reviewed/edited by Simon row-by-row; this module never
  guesses values.
* If a country has both ``sovereignty_factor`` and ``absolute_floor_mw`` set,
  the larger of (factor × demand / LF / 8760) and absolute_floor wins.
* If a country has ONLY ``absolute_floor_mw`` (sovereignty_factor blank), the
  absolute floor is used unconditionally — for very small countries where
  fractional bounds aren't meaningful.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════


# Helpers split into sibling modules
from pommes_eur.providers.clever._h2_demand import _fetch_annual_h2_demand  # noqa: E402
from pommes_eur.providers.clever._min_bounds import _compute_electrolyser_min_bounds  # noqa: E402


def build_calibration_inputs(
    bundle_name: str,
    countries: list[str],
    model_year: int,
    tables_dir: Path,
    *,
    load_factor: Optional[float] = None,
    electrolyser_invest_cost_eur_per_kw: float = 500.0,
    hydrogen_load_shedding_cost_eur_per_mwh: float = 30_000.0,
) -> dict:
    """
    Build the ``dataset_calibration_kwargs`` payload from CSVs + DemandForge.

    Parameters
    ----------
    bundle_name : str
        DemandForge bundle name (e.g. ``"low_h2"``). Forwarded to
        ``pommes_eur.providers.clever.h2_demand.fetch_h2_demand_from_demandforge``
        to retrieve per-country annual H₂ demand for ``model_year``.
    countries : list[str]
        Country codes (e.g. ``["FR", "DE", ...]``) matching the POMMES bundle's
        ``area`` coord.
    model_year : int
        Operating year (typically 2050).
    tables_dir : Path
        Directory containing the three R0 CSVs:
          - ``electrolyser_sovereignty_factors.csv``
          - ``h2_underground_storage_capex_by_country.csv``
          - ``h2_underground_storage_capacity_caps_by_country.csv``
    load_factor : float, optional
        Electrolyser load factor used to convert annual H₂ demand to min
        capacity. If None, read per-row from the sovereignty CSV's
        ``load_factor_assumption`` column.
    electrolyser_invest_cost_eur_per_kw : float
        Pass-through; default 500 €/kW. See ``dataset_calibration`` units comment for
        the POMMES-H₂-output convention.
    hydrogen_load_shedding_cost_eur_per_mwh : float
        Pass-through; default 30 000 €/MWh (matches electricity VOLL).

    Returns
    -------
    dict
        Ready to splat into ``run_model_without_ramping(dataset_calibration_kwargs=...)``.
    """
    # ── Fallback for sensitivity-suffix scenarios ────────────────────
    # New SCENARIO suffixes (_bioLow, _bioMed, _bioHigh, _atr, _elNNN, _h2HIGH)
    # don't change the underlying per-country sovereignty / H₂ storage
    # tables — they only modify how those values are USED downstream.
    # Empirically `diff tables/policy_re tables/policy_re_noMin` returns
    # nothing — per-scenario dirs are redundant copies of the same 3 CSVs.
    #
    # Rather than require an explicit `tables/<new_scenario>/` to be created
    # for every sensitivity variant, fall back to the canonical base scenario
    # by stripping all sensitivity-suffix axes. The fallback only kicks in
    # when the per-scenario dir literally doesn't exist; scenarios that DO
    # have their own dir keep using it.
    if not tables_dir.exists():
        import re as _re_local
        base_name = _re_local.sub(
            r"_(?:bioLow|bioMed|bioHigh|bioOff|atr|el\d+|h2HIGH|h2central|co2\d+|co2off|co2traj[a-zA-Z0-9]+|nofloor|noGas|noElecFloor|fuelRamp\d+|ngPrice\d+|oilPrice\d+|ngLeak\d+|bioLeak\d+|ccsCap\d+|elecX\d+|corr\dx|vreEXT|vreXXL|vreFree|nukeXXL|wy\d+|menaH2cost[A-Z]{2}_\d+|menaH2(?:cost)?\d+|menaOptim(?:[A-Z]{2}_?)*|menaInfra(?:[A-Z]{2}_[A-Z]{2}_)?\d+|menaRisk[A-Z]{2}_\d+|menaNGcost[A-Z]{2}_\d+|menaCap\d+|menaPref\d+|batt\d+|h2voll\d+|voll\d+|pipekm\d+|storPx\d+|noGridExp|(?:it|at|pt|ie|dk|lu|gr)Nuke\d+)",
            "",
            tables_dir.name,
        )
        fallback = tables_dir.parent / base_name
        if fallback.exists() and fallback != tables_dir:
            import logging as _lg_local
            _lg_local.getLogger(__name__).info(
                "calibration_inputs: tables_dir %s missing — falling back to %s",
                tables_dir, fallback,
            )
            tables_dir = fallback

    sov_path = tables_dir / "electrolyser_sovereignty_factors.csv"
    capex_path = tables_dir / "h2_underground_storage_capex_by_country.csv"
    caps_path = tables_dir / "h2_underground_storage_capacity_caps_by_country.csv"
    # _storPxNNN scenario suffix: multiply storage POWER caps by NNN/100 (sensitivity on
    # the 316 GW deliverability constraint; energy caps untouched).
    import re as _re
    _mm = _re.search(r"_storPx(\d+)", __import__("pommes_eur.scenario.env", fromlist=["current_scenario"]).current_scenario())
    _storp_mult = (int(_mm.group(1)) / 100.0) if _mm else 1.0

    for p in (sov_path, capex_path, caps_path):
        if not p.exists():
            raise FileNotFoundError(f"R0 input table missing: {p}")

    # ── 1. DemandForge annual H₂ demand per country ─────────────────
    annual_h2_demand_mwh = _fetch_annual_h2_demand(
        bundle_name=bundle_name,
        countries=countries,
        model_year=model_year,
    )

    # ── 2. Electrolyser min bounds (sub-task 1) ─────────────────────
    sov_df = pd.read_csv(sov_path)
    electrolyser_min_bounds_gw = _compute_electrolyser_min_bounds(
        sov_df=sov_df,
        annual_demand_mwh=annual_h2_demand_mwh,
        countries=countries,
        load_factor_override=load_factor,
    )

    # ── 3. Storage tables (sub-task 3) ──────────────────────────────
    capex_df = pd.read_csv(capex_path)
    caps_df = pd.read_csv(caps_path)
    if _storp_mult != 1.0:
        _c = [c for c in ["cap_gw_power"] if c in caps_df.columns]
        caps_df[_c] = caps_df[_c] * _storp_mult

    # POMMES override-applier expects exactly these columns from the caps df.
    # Map cap_twh_realistic → the value the override consumes.
    if "cap_twh_realistic" not in caps_df.columns:
        raise ValueError(
            f"{caps_path} missing required column 'cap_twh_realistic'"
        )

    # Filter both tables to the countries actually modelled
    capex_df = capex_df[capex_df["area"].isin(countries)].copy()
    caps_df = caps_df[caps_df["area"].isin(countries)].copy()

    # ── 4. Assemble kwargs ──────────────────────────────────────────
    kwargs = dict(
        electrolyser_invest_cost_eur_per_kw=electrolyser_invest_cost_eur_per_kw,
        electrolyser_min_bounds_gw=electrolyser_min_bounds_gw,
        storage_capex_by_area=capex_df,
        storage_caps_by_area=caps_df,
        hydrogen_load_shedding_cost_eur_per_mwh=hydrogen_load_shedding_cost_eur_per_mwh,
    )

    logger.info(
        "calibration_inputs: built kwargs for %d countries — "
        "%d electrolyser min bounds, %d storage CAPEX rows, %d cap rows",
        len(countries),
        len(electrolyser_min_bounds_gw),
        len(capex_df),
        len(caps_df),
    )
    return kwargs


# ═════════════════════════════════════════════════════════════════════
# Private helpers
# ═════════════════════════════════════════════════════════════════════


