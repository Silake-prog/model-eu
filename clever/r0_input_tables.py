"""
clever.r0_input_tables — Glue between R0 per-country CSV tables, DemandForge
H₂ demand, and the override kwargs that clever.runner consumes.

Role in pipeline
----------------
Reads the three R0-policy CSV tables under ``tables/R0/`` and the live
DemandForge H₂ demand for the current bundle/year, computes the sovereignty-
anchored electrolyser min bounds, and returns a single ``r0_overrides_kwargs``
dict ready to pass to ``clever.runner.run_model_without_ramping``.

Usage in adequacy_clean.ipynb
-----------------------------
.. code-block:: python

    from clever.r0_input_tables import build_r0_overrides_kwargs

    r0_kwargs = build_r0_overrides_kwargs(
        bundle_name=DEMANDFORGE_BUNDLE,
        countries=COUNTRIES_MODELLED,
        model_year=MODEL_YEAR,
        tables_dir=_WS_ROOT / "tables" / "R0",
    )

    linopy_model = run_model_without_ramping(
        ...,
        r0_overrides_kwargs=r0_kwargs,
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


def build_r0_overrides_kwargs(
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
    Build the ``r0_overrides_kwargs`` payload from CSVs + DemandForge.

    Parameters
    ----------
    bundle_name : str
        DemandForge bundle name (e.g. ``"low_h2"``). Forwarded to
        ``supplyforge.h2_profiles.build_all_sector_profiles`` (or
        ``fetch_h2_demand_from_demandforge``) to retrieve per-country annual
        H₂ demand for ``model_year``.
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
        Pass-through; default 500 €/kW. See ``r0_overrides`` units comment for
        the POMMES-H₂-output convention.
    hydrogen_load_shedding_cost_eur_per_mwh : float
        Pass-through; default 30 000 €/MWh (matches electricity VOLL).

    Returns
    -------
    dict
        Ready to splat into ``run_model_without_ramping(r0_overrides_kwargs=...)``.
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
            r"_(?:bioLow|bioMed|bioHigh|bioOff|atr|el\d+|h2HIGH|h2central|co2\d+|co2off|co2traj[a-zA-Z0-9]+|nofloor|noGas|noElecFloor|h2Local\d+|fuelRamp\d+|ngPrice\d+|oilPrice\d+|ngLeak\d+|bioLeak\d+|ccsCap\d+|elecX\d+|corr\dx|vreEXT|vreXXL|vreFree|nukeXXL|wy\d+|menaH2cost[A-Z]{2}_\d+|menaH2(?:cost)?\d+|menaOptim(?:[A-Z]{2}_?)*|menaInfra(?:[A-Z]{2}_[A-Z]{2}_)?\d+|menaRisk[A-Z]{2}_\d+|menaNGcost[A-Z]{2}_\d+|menaCap\d+|menaPref\d+|batt\d+|h2voll\d+|voll\d+|pipekm\d+|storPx\d+|noGridExp|(?:it|at|pt|ie|dk|lu|gr)Nuke\d+)",
            "",
            tables_dir.name,
        )
        fallback = tables_dir.parent / base_name
        if fallback.exists() and fallback != tables_dir:
            import logging as _lg_local
            _lg_local.getLogger(__name__).info(
                "r0_input_tables: tables_dir %s missing — falling back to %s",
                tables_dir, fallback,
            )
            tables_dir = fallback

    sov_path = tables_dir / "electrolyser_sovereignty_factors.csv"
    capex_path = tables_dir / "h2_underground_storage_capex_by_country.csv"
    caps_path = tables_dir / "h2_underground_storage_capacity_caps_by_country.csv"
    # _storPxNNN scenario suffix: multiply storage POWER caps by NNN/100 (sensitivity on
    # the 316 GW deliverability constraint; energy caps untouched).
    import os as _os, re as _re
    _mm = _re.search(r"_storPx(\d+)", _os.environ.get("CLEVER_SCENARIO", ""))
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
        "r0_input_tables: built kwargs for %d countries — "
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


def _fetch_annual_h2_demand(
    bundle_name: str,
    countries: list[str],
    model_year: int,
) -> dict[str, float]:
    """
    Pull per-country annual H₂ demand from DemandForge for the given bundle.

    Returns
    -------
    dict[str, float]
        area_code → annual H₂ demand in MWh (energy of H₂).

    Notes
    -----
    Uses the supplyforge wrapper for compatibility with what
    ``adequacy_clean.ipynb`` already calls. The wrapper preserves the
    bundle's ``pathway_shares`` for eSAF — see ``r0_override_contract.md``
    and the dictionary entry "Coupled industry-electricity capacity-expansion".
    """
    try:
        from supplyforge.h2_profiles import fetch_h2_demand_from_demandforge
    except ImportError:
        # Fall back to direct DemandForge call (canonical path)
        from demandforge.load_projection.scenarios import load_bundle
        frame = load_bundle(
            bundle_name=bundle_name,
            countries=sorted(countries),
            target_year=model_year,
        )
        # Sum all sectors per country for the target year
        frame_y = frame[frame["year"] == model_year]
        out = (
            frame_y.groupby("country")["h2_demand_mwh_per_yr"].sum().to_dict()
        )
        return {str(k): float(v) for k, v in out.items() if str(k) in countries}

    sectoral = fetch_h2_demand_from_demandforge(
        bundle_name=bundle_name,
        countries=countries,
        model_year=model_year,
        sectoral=True,
    )
    return {
        cc: float(sum(v for v in (sectoral.get(cc, {}) or {}).values() if v > 0))
        for cc in countries
    }


def _compute_electrolyser_min_bounds(
    sov_df: pd.DataFrame,
    annual_demand_mwh: dict[str, float],
    countries: list[str],
    load_factor_override: Optional[float] = None,
) -> dict[str, float]:
    """
    Compute per-country electrolyser min capacity in GW.

    For each country:
      * If ``sovereignty_factor`` is set:
          min_capacity_MW = factor × demand_MWh / (load_factor × 8760)
      * If ``absolute_floor_mw`` is set:
          min_capacity_MW = max(min_capacity_MW, absolute_floor)
      * If only ``absolute_floor_mw`` is set (factor blank):
          min_capacity_MW = absolute_floor

    Returns dict in GW.
    """
    bounds: dict[str, float] = {}

    # Index sovereignty CSV by area
    sov_by_area = {row["area"]: row for _, row in sov_df.iterrows()}

    for area in countries:
        if area not in sov_by_area:
            logger.warning(
                "r0_input_tables: no sovereignty row for %r; min bound = 0", area
            )
            continue

        row = sov_by_area[area]
        factor = row.get("sovereignty_factor")
        abs_floor_mw = row.get("absolute_floor_mw")
        lf = load_factor_override or row.get("load_factor_assumption") or 0.6

        demand_mwh = annual_demand_mwh.get(area, 0.0)

        capacity_mw_from_factor = 0.0
        if pd.notna(factor) and factor > 0 and demand_mwh > 0:
            capacity_mw_from_factor = (
                float(factor) * float(demand_mwh) / (float(lf) * 8760.0)
            )

        capacity_mw_from_floor = 0.0
        if pd.notna(abs_floor_mw):
            capacity_mw_from_floor = float(abs_floor_mw)

        # Take the larger of the two (so absolute_floor acts as a true floor)
        capacity_mw = max(capacity_mw_from_factor, capacity_mw_from_floor)

        if capacity_mw > 0:
            bounds[area] = capacity_mw / 1000.0  # GW
            logger.info(
                "r0_input_tables: %s electrolyser min = %.2f GW "
                "(factor=%s, demand=%.1f TWh, LF=%.2f, abs_floor=%s MW)",
                area, bounds[area],
                f"{factor:.2f}" if pd.notna(factor) else "—",
                demand_mwh / 1e6,
                lf,
                f"{abs_floor_mw:.0f}" if pd.notna(abs_floor_mw) else "—",
            )

    return bounds
