"""clever.scenario.parse — pure scenario-string parsers.

Every function here maps a raw scenario string (e.g. ``R0_v1_nuke_bioLow_...``) to a
single resolved value, with **no** dependency on module state or model data. They are
the vocabulary of scenario flags; ``clever.constants`` calls them once at import time to
materialise the per-run scenario globals, and the Phase-4 scenario registry indexes them.

Carved verbatim out of ``clever/constants.py`` — behaviour is byte-for-byte identical
(guarded by tests/golden). No numerics changed.
"""
from __future__ import annotations

import os as _os        # noqa: F401 - kept for parity with source (some parsers use it)
import re as _re
import re as _re_corr    # noqa: F401 - alias parity with constants.py

__all__ = [
    '_is_high_demand',
    '_is_nuclear_expandable',
    '_parse_biomethane_scope',
    '_parse_atr_enabled',
    '_parse_electrolyser_capex_override',
    '_parse_demandforge_bundle_override',
    '_parse_co2_price_override',
    '_parse_gas_floor_lifted',
    '_parse_ng_leak_rate',
    '_parse_bio_leak_rate',
    '_parse_ccs_cap_gw',
    '_parse_vre_xxl',
    '_parse_vre_free',
    '_parse_nuke_xxl',
    '_parse_voll_override',
    '_parse_pipeline_km_cost',
    '_parse_elec_demand_multiplier',
    '_parse_weather_year',
    '_parse_vre_extended',
    '_parse_no_elec_floor',
    '_parse_no_grid_exp',
    '_parse_h2_local_share',
    '_parse_no_gas',
    '_parse_mena_h2_cap',
    '_parse_mena_h2_cost',
    '_parse_mena_h2_cost_by_entry',
    '_parse_mena_optim_active',
    '_parse_mena_infra_global',
    '_parse_mena_infra_overrides',
    '_parse_mena_ng_wholesale_overrides',
    '_parse_mena_cap_scale',
    '_parse_mena_pref_scale',
    '_parse_mena_risk_overrides',
    '_parse_ng_price_override',
    '_parse_oil_price_override',
    '_parse_fuel_ramp_override',
]


# Inheritance gates (use prefix/substring matching so suffix variants
# like _corr2x and _noMin_corr2x automatically inherit the right gate
# from their base scenario).
def _is_high_demand(s: str) -> bool:
    """central H₂ bundle + +20% elec + VRE 0.30 — i.e. any policy_* scenario."""
    return s.startswith("policy_")


def _is_nuclear_expandable(s: str) -> bool:
    """Nuclear added to EXPANDABLE_MODEL_TECHS + force-injected as a tech."""
    return "_nuke" in s   # matches R0_v1_nuke, policy_nuke, and their *_corrNx variants


def _parse_biomethane_scope(s: str) -> str | None:
    """Return 'bioLow' / 'bioMed' / 'bioHigh' / None based on scenario suffix.

    The explicit `_bioOff` suffix forces None (overrides any `_bioLow` /
    `_bioMed` / `_bioHigh` that may also appear) — used for sensitivity
    runs that isolate the cost contribution of biomethane.
    """
    if _re.search(r"_bioOff(?:_|$)", s):
        return None
    m = _re.search(r"_bio(Low|Med|High)(?:_|$)", s)
    return ("bio" + m.group(1)) if m else None


def _parse_atr_enabled(s: str) -> bool:
    """Return True if scenario has `_atr` suffix AND biomethane is on.

    ATR needs biomethane feedstock; if `_bioOff` is set, ATR is forced off
    regardless of the `_atr` suffix.
    """
    if _re.search(r"_bioOff(?:_|$)", s):
        return False
    return bool(_re.search(r"_atr(?:_|$)", s))


def _parse_electrolyser_capex_override(s: str) -> float:
    """Return electrolyser CAPEX in €/kW. Default 500. Override via `_elNNN`."""
    m = _re.search(r"_el(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else 500.0


def _parse_demandforge_bundle_override(s: str) -> str | None:
    """Return a DemandForge bundle override based on scenario suffix, or None.

    `_h2HIGH` → use the `high_h2` bundle (per-sector recalibrated parameters:
    ammonia h2_route_share_end 0.80→1.0, esaf demand_growth 2%→3%, etc.).
    This is structurally different from a scalar multiplier — the bundle has
    different sectoral shares + transition timings, not just bigger totals.
    """
    if _re.search(r"_h2HIGH(?:_|$)", s):
        return "high_h2"
    if _re.search(r"_h2central(?:_|$)", s):
        return "central"
    return None


def _parse_co2_price_override(s: str) -> float:
    """Return the CO₂ price applied to fossil-gas CCGT, in €/tCO₂.

    Default 150 (current value implicit in `FUEL_ADDER_2050["ch4_ccgt"] = 125`).
    Override via `_co2NNN` suffix, e.g. `_co2250` → 250 €/tCO₂. The `_co2off`
    suffix sets the price to 0 (disables the CO₂ adder entirely).

    The Gas fuel adder is recomputed dynamically at the end of this module
    once the override has been parsed. For trajectory-based pricing see
    `clever.carbon_price.resolved_carbon_price()` which can interpolate
    along a published pathway.
    """
    if _re.search(r"_co2off(?:_|$)", s):
        return 0.0
    m = _re.search(r"_co2(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else 150.0


def _parse_gas_floor_lifted(s: str) -> bool:
    """Return True if the scenario lifts CLEVER's politically-retained Gas
    capacity floor (`_nofloor` suffix).

    When True, `clever.model.add_dispatchable_from_non_enr` overrides
    `existing_capacity_mw` to 0 for the Gas tech, allowing the LP to fully
    decommission instead of being floored at CLEVER's existing capacity
    (~22.8 GW EU-wide across 14 member states).
    """
    return bool(_re.search(r"_nofloor(?:_|$)", s))


def _parse_ng_leak_rate(s: str) -> float:
    """Upstream CH4 leakage of the fossil-gas supply chain, fraction of
    delivered energy. Default 2.5% (EU import mix, pipeline+LNG mid-range).
    Override `_ngLeakNN`, NN in per-mille: `_ngLeak25`→2.5%, `_ngLeak0`→off.
    Priced in natural_gas_import_price() at GWP100 x CO2 price (unrebated)."""
    m = _re.search(r"_ngLeak(\d+)(?:_|$)", s)
    return (float(m.group(1)) / 1000.0) if m else 0.025


def _parse_bio_leak_rate(s: str) -> float:
    """CH4 slip of the biomethane chain (digester + upgrading), fraction of
    produced biomethane. Default 1.0% (EBA/IEA modern-plant mid-range).
    Override `_bioLeakNN`, NN in per-mille: `_bioLeak10`→1.0%, `_bioLeak0`→off.
    Applied in clever.biomethane as an input yield-loss (biomethane output
    stays +1 for pommes reference detection) + a GHG variable_cost."""
    m = _re.search(r"_bioLeak(\d+)(?:_|$)", s)
    return (float(m.group(1)) / 1000.0) if m else 0.010


def _parse_ccs_cap_gw(s: str) -> float | None:
    """Per-country cap on CCS-H2 reformer capacity, GW_H2 (ATR_CCS + SMR_CCS
    COMBINED — each tech gets half so their sum respects the cap). None =
    legacy 50 GW per tech. `_ccsCapNNN` -> NNN GW/country. Framing: annual
    CO2-injection realism — 1 GW_H2-CCS stores ~2.25 MtCO2/yr at CF~1, so
    `_ccsCap5` = 5 GW/country (<= 11 Mt/yr/country; EU30 envelope ~340 Mt/yr,
    vs ~360 Mt/yr injected by the UNCAPPED elecX180_h2HIGH solve)."""
    m = _re.search(r"_ccsCap(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else None


def _parse_vre_xxl(s: str) -> bool:
    """`_vreXXL` (2026-06-16) -> lift VRE expansion headroom to ~2x the `_vreEXT`
    band (Solar +50%, Wind onshore +40%, Wind offshore +120% of CLEVER capacity).
    Activates the extended-VRE path (blanks conservative per-country overrides)
    with doubled fractions; supersedes `_vreEXT` when both are present."""
    return bool(_re.search(r"_vreXXL(?:_|$)", s))


def _parse_vre_free(s: str) -> bool:
    """`_vreFree` (2026-06-26) -> remove BOTH the CLEVER VRE floor and the
    expansion margin: downside band = 1.0 (capacity_min -> 0, LP free to build
    nothing) and expansion headroom = 0.0 (capacity_max = CLEVER level exactly,
    no sufficiency margin). The LP may then site VRE anywhere in [0, CLEVER].
    Used to test how far installed VRE falls below the 2192 GW sufficiency floor
    when it is no longer forced. Takes precedence over `_vreEXT`/`_vreXXL`."""
    return bool(_re.search(r"_vreFree(?:_|$)", s))


def _parse_nuke_xxl(s: str) -> bool:
    """`_nukeXXL` (2026-06-16) -> double every NON-zero per-country nuclear
    headroom and the default (FR 20->40, GB 15->30, PL 10->20, SE 6->12,
    default 2->4 GW). Policy hard-zeros (DE/IT/AT/PT/IE/DK/LU/GR) stay 0.
    Contains '_nuke' so it also makes nuclear expandable."""
    return bool(_re.search(r"_nukeXXL(?:_|$)", s))


def _parse_voll_override(s: str, token: str) -> float | None:
    """Override a load-shedding cost (VoLL) for one resource via a `_<token>NNN`
    suffix, NNN in €/MWh. `_voll3000` -> electricity VoLL = 3000; `_h2voll2000`
    -> hydrogen VoLL = 2000. Returns None when absent (keep the model default:
    electricity 30 000, hydrogen 10 000 €/MWh).

    Economic reading (the value IS the modelled collective choice):
      * VoLL BELOW the marginal dispatchable — the gas-free H2-CCGT peaker
        (~1300 €/MWh) for electricity, or the H2 marginal production cost
        (~500 €/MWh) for hydrogen — models the price at which that demand is
        REMUNERATED to curtail (effacement / interruptible contract): the LP
        prefers paying NNN to shed over running the expensive peaker.
      * VoLL ABOVE it is a pure scarcity / blackout backstop: firm capacity is
        used fully and only deep scarcity is capped.
    NB this is a FLAT per-carrier VoLL, so it makes the WHOLE demand curtailable
    at NNN -> an UPPER bound on the curtailable quantity (a PEMMDB-style band
    would cap the quantity to the genuinely-interruptible share)."""
    m = _re.search(rf"_{token}(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else None


# Distance-based H₂-pipeline CAPEX via `_pipekmNNN` (NNN = €/MW/km). When present,
# every H₂ pipeline's invest_cost becomes NNN × centroid-to-centroid great-circle
# km (EU h2_pipeline + MENA mena_h2_pipeline*), replacing the flat 500k€/MW and the
# hand-tuned MENA route dict. None ⇒ legacy flat costs. Applied in
# clever.runner.apply_distance_based_pipeline_capex (after enforce_uniform_wacc).
def _parse_pipeline_km_cost(s: str) -> float | None:
    m = _re.search(r"_pipekm(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else None


def _parse_elec_demand_multiplier(s: str) -> float:
    """Return a uniform multiplier applied to CLEVER's hourly electricity
    demand vector. Default 1.0 (no scaling). Override via `_elecXNNN`
    suffix where NNN is the % multiplier (e.g. `_elecX125` → ×1.25,
    `_elecX180` → ×1.80).
    Applied per hour × country, preserving the load shape.
    """
    m = _re.search(r"_elecX(\d+)(?:_|$)", s)
    return float(m.group(1)) / 100.0 if m else 1.0


def _parse_weather_year(s: str) -> int | None:
    """Return weather reference year if scenario has `_wyYYYY` suffix,
    else None (caller keeps its default, typically 2024).

    Overrides SupplyForge VRE chronology + DemandForge thermosensitive
    components for the chosen meteorological year. Examples: `_wy2010`
    (dunkelflaute), `_wy2018` (hydro drought), `_wy2022` (recent normal).

    Note: data availability is per-(country, year) at the parquet level
    and not guaranteed for every year. Missing years fall back to flat
    profiles per the existing supplyforge/demandforge fallback logic.
    """
    m = _re.search(r"_wy(\d{4})(?:_|$)", s)
    return int(m.group(1)) if m else None


def _parse_vre_extended(s: str) -> bool:
    """Return True if scenario has `_vreEXT` suffix — lifts per-tech VRE
    expansion headroom to JRC-ENSPRESO / national-pledge floor numbers:
    Solar +25 %, Wind_Onshore +20 %, Wind_Offshore +60 %.

    Justification:
    - Solar: IEA Solar Decade scenarios assume rooftop + agri-PV scaling well
      beyond +25 % of CLEVER baseline in EU.
    - Wind Onshore: EWEA 2050 vision allows +20 %; NIMBY bounded.
    - Wind Offshore: JRC TYNDP 2024 geophysical potential is ~600 GW
      bottom-fixed (vs ~280 GW CLEVER baseline) and GB/NL/DK declared 2050
      pledges already exceed +60 % of CLEVER baseline.

    Activating this also blanks `VRE_EXPANSION_HEADROOM_BY_COUNTRY` so the
    big-country overrides (DE/FR/ES/IT/GB at 10–15 %) don't bind below the
    extended default.
    """
    return bool(_re.search(r"_vreEXT(?:_|$)", s))


def _parse_no_elec_floor(s: str) -> bool:
    """Return True if the scenario disables the tech-specific electrolyser
    sovereignty min-bound floor (`_noElecFloor` suffix).

    The original CLEVER design pins per-country electrolyser capacity at
    a fraction of national H₂ demand via the sovereignty CSV
    (`sovereignty_factor × demand / load_factor / 8760`). For FR with HIGH
    H₂ demand this lands at ~13 GW — a tech-specific floor that becomes
    a stranded asset when alternative H₂ sources (BECCS via ATR_CCS /
    SMR_CCS bio_mode) are cheaper. The `_noElecFloor` suffix removes that
    tech-specific floor entirely; the LP is free to allocate H₂ production
    across electrolysis, biomethane-ATR, BECCS, and imports.

    For a tech-agnostic local-supply constraint (sovereignty without
    technology lock-in), use `_h2LocalNN` (parser stub below — implementation
    deferred to a follow-up patch).
    """
    return bool(_re.search(r"_noElecFloor(?:_|$)", s))


def _parse_no_grid_exp(s: str) -> bool:
    """Return True if the scenario blocks electricity-grid expansion
    (`_noGridExp` suffix).

    Disables the investable `electric_line_expansion` layer, pinning
    cross-border NTC at its existing (`electric_line`) capacity while the LP
    stays free on generation, storage and H₂ pipelines. Reproduces the
    "power-grid expansion blocked" counterfactual of Neumann et al. (2023),
    used here to benchmark the value-of-network against their reported figure.
    """
    return bool(_re.search(r"_noGridExp(?:_|$)", s))


def _parse_h2_local_share(s: str) -> float | None:
    """Return the local-H₂-supply target share (0.0–1.0) from `_h2LocalNN`.

    Format: `_h2LocalNN` where NN is the percentage of demand to cover
    locally (e.g. `_h2Local70` → 70 %). Default `None` = no constraint.

    Implementation is deferred: when the constraint is active, the
    intention is to cap each area's H₂ NetImport at `(1 − share) × demand`,
    leaving the LP free to choose any local H₂ tech (electrolysis,
    biomethane-ATR, BECCS, etc.). The constraint is meaningless in
    FR-only smoke runs (no imports anyway), so the parser ships now and
    the constraint application lands when the full-EU run uses it.
    """
    m = _re.search(r"_h2Local(\d+)(?:_|$)", s)
    if m is None:
        return None
    pct = int(m.group(1))
    return float(pct) / 100.0


def _parse_no_gas(s: str) -> bool:
    """Return True if the scenario bans all fossil-methane combustion
    (`_noGas` suffix). Forces cap_max=0 in every country for both Gas
    (CCGT) and any OCGT tech, leaving Nuclear / Hydro / Biomethane-CCGT /
    H2-CCGT / batteries / load shedding as the only firm options.
    """
    return bool(_re.search(r"_noGas(?:_|$)", s))


# ─── MENA H₂ imports (Phase 2.5) ─────────────────────────────────────
# Two variants behind separate suffixes. Both default OFF.
#
# Variant A (Production-only, exogenous price):
#   _menaH2NNN            → set annual import cap to NNN TWh/yr (EU-wide,
#                           allocated 50/50 to ES + IT entry-points).
#   _menaH2costNNN        → FLAT override of delivered cost (€/MWh_H2), applied
#                           to BOTH entry points uniformly. Use for sweep-style
#                           sensitivity analysis.
#   _menaH2cost{ENTRY}_NNN→ PER-ENTRY override (ENTRY ∈ ES, IT). Multiple may
#                           be combined: `_menaH2costES_95_menaH2costIT_120`.
#                           Default (no override): UPPER-BAND differentiated
#                           — ES=100, IT=115 €/MWh — see MENA_H2_ENTRY_POINTS
#                           in clever/mena_imports.py for the rationale.
#
# Variant B (per-country optimised MENA — full Areas with VRE+electrolyser
# +H2 storage +per-route transport):
#   _menaOptim            → activate all 4 countries (MA + DZ + TN + LY)
#   _menaOptimMA          → activate MA only (cheapest-route stress test)
#   _menaOptimMA_DZ       → activate MA + DZ only (Spain-routed subset)
#   _menaInfraNNN         → global pipeline CAPEX override (k€/MW), all routes
#   _menaInfra{SRC}_{DST}_NNN → per-route override (e.g. _menaInfraLY_IT_2500)
#   _menaRisk{CC}_NNN     → per-country sovereign-risk premium (pp × 10;
#                           e.g. _menaRiskLY_50 → 5.0 pp uplift on finance rate)
#   _menaCapNNN           → global multiplier on per-country production caps,
#                           percent (e.g. _menaCap150 = 1.5× literature caps).
#                           Default 100. Applies to solar/wind/electrolyser/storage.
#   _menaPrefNNN          → global multiplier on per-country domestic-H₂ demand
#                           reservation, percent. Default 100. _menaPref0 disables
#                           the reservation entirely (purely export-oriented).
#   _menaNGcost{CC}_NNN   → per-country NG wholesale-price override (€/MWh_th,
#                           pre-CO₂ adder). Default: MA=30, DZ=10, TN=28, LY=12
#                           (DZ/LY have cheap stranded gas, MA/TN pay LNG-like).
#                           CO₂ adder applied uniformly via clever.carbon_price
#                           (CBAM-equivalent assumption for the 2050 horizon).
#                           Blue H₂ techs (ATR_CCS, SMR_CCS) added at MENA areas
#                           when natural_gas + hydrogen are both in resources.
def _parse_mena_h2_cap(s: str) -> float:
    """Variant A: annual MENA→EU H₂ import cap, TWh/yr (default 0 = OFF)."""
    m = _re.search(r"_menaH2(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else 0.0


def _parse_mena_h2_cost(s: str) -> float | None:
    """Variant A: FLAT delivered LCOH override from MENA, €/MWh_H2.

    Returns None if no `_menaH2costNNN` suffix is present, in which case the
    per-entry upper-band defaults in MENA_H2_ENTRY_POINTS are used (or
    per-entry overrides from `_parse_mena_h2_cost_by_entry`).

    The regex deliberately requires DIGITS immediately after `_menaH2cost`
    so it does NOT collide with `_menaH2costES_NNN` / `_menaH2costIT_NNN`
    (which start with letters and are handled by the per-entry parser).
    """
    m = _re.search(r"_menaH2cost(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else None


def _parse_mena_h2_cost_by_entry(s: str) -> dict[str, float]:
    """Variant A: PER-ENTRY-POINT delivered LCOH overrides, €/MWh_H2.

    Format: `_menaH2cost{ENTRY}_{NNN}` where ENTRY ∈ {ES, IT}. Multiple
    overrides may be combined in one scenario name (e.g.
    `_menaH2costES_95_menaH2costIT_120`).

    Returns a dict mapping entry-point CC → override €/MWh_H2. Empty dict
    if no per-entry overrides are present. Unknown entries are silently
    dropped (caller filters against MENA_H2_ENTRY_POINTS).
    """
    out: dict[str, float] = {}
    for m in _re.finditer(r"_menaH2cost([A-Z]{2})_(\d+)", s):
        out[m.group(1)] = float(m.group(2))
    return out


def _parse_mena_optim_active(s: str) -> tuple[str, ...]:
    """Variant B: which MENA countries are activated.

    Returns a tuple of ISO2 country codes. Empty tuple = Variant B OFF.

      _menaOptim         → ("MA", "DZ", "TN", "LY")  (all four)
      _menaOptimMA       → ("MA",)
      _menaOptimMA_DZ    → ("MA", "DZ")
      _menaOptimMA_DZ_TN → ("MA", "DZ", "TN")
    """
    # Subset form: _menaOptim followed by an underscore-joined list of CCs.
    # Matches up to 4 trailing CCs; allow trailing _ or end.
    m = _re.search(r"_menaOptim((?:[A-Z]{2}_?)+)(?:_|$)", s)
    if m:
        subset_raw = m.group(1)
        ccs = tuple(p for p in subset_raw.split("_") if p)
        # Filter to known MENA-Variant-B countries; reject unknowns silently
        # (those are handled by other parsers, e.g. _itNuke).
        known = {"MA", "DZ", "TN", "LY"}
        return tuple(cc for cc in ccs if cc in known)
    # Bare _menaOptim → all four
    if _re.search(r"_menaOptim(?:_|$)", s):
        return ("MA", "DZ", "TN", "LY")
    return ()


def _parse_mena_infra_global(s: str) -> float | None:
    """Variant B: global pipeline CAPEX override (€/MW = NNN × 1000).
    Returns None when no global override (per-route table is used) or when
    a per-route override is set elsewhere — they take precedence.
    """
    # Per-route patterns take precedence — see _parse_mena_infra_overrides.
    if _re.search(r"_menaInfra[A-Z]{2}_[A-Z]{2}_\d+", s):
        return None
    m = _re.search(r"_menaInfra(\d+)(?:_|$)", s)
    return float(m.group(1)) * 1000.0 if m else None


def _parse_mena_infra_overrides(s: str) -> dict[tuple[str, str], float]:
    """Variant B: per-route pipeline CAPEX overrides.
    Format: _menaInfra{SRC}_{DST}_{NNN}, where NNN is k€/MW.
    Returns a dict[(src, dst)] -> €/MW. Empty if no per-route override.
    """
    out: dict[tuple[str, str], float] = {}
    for m in _re.finditer(r"_menaInfra([A-Z]{2})_([A-Z]{2})_(\d+)", s):
        out[(m.group(1), m.group(2))] = float(m.group(3)) * 1000.0
    return out


def _parse_mena_ng_wholesale_overrides(s: str) -> dict[str, float]:
    """Variant B (blue H₂): per-country NG wholesale price overrides.

    Format: ``_menaNGcost{CC}_{NNN}`` where NNN is €/MWh_th (e.g.
    ``_menaNGcostDZ_8`` → Algeria NG wholesale = 8 €/MWh_th, before CO₂
    adder). Empty dict = use built-in defaults from
    MENA_NG_WHOLESALE_EUR_PER_MWH_TH in clever.mena_imports.

    The wholesale is the pre-CO₂ price at the MENA bus; the CO₂ adder is
    applied uniformly via clever.carbon_price (CBAM-equivalent assumption).
    """
    out: dict[str, float] = {}
    for m in _re.finditer(r"_menaNGcost([A-Z]{2})_(\d+)", s):
        out[m.group(1)] = float(m.group(2))
    return out


def _parse_mena_cap_scale(s: str) -> float:
    """Variant B: global scaling factor on per-country production caps.
    Format: _menaCap{NN} where NN is percent (e.g. _menaCap150 = 1.5× caps).
    Default 1.0 (no change). Applied multiplicatively to solar / wind /
    electrolyser / H₂-storage caps in MENA_COUNTRY_CONFIG.
    """
    m = _re.search(r"_menaCap(\d+)(?:_|$)", s)
    return float(m.group(1)) / 100.0 if m else 1.0


def _parse_mena_pref_scale(s: str) -> float:
    """Variant B: global scaling factor on per-country domestic-H₂ demand.
    Format: _menaPref{NN} where NN is percent (e.g. _menaPref0 disables any
    domestic reservation; _menaPref200 doubles the default).
    Default 1.0. Per-country baseline values are set in MENA_COUNTRY_CONFIG.
    """
    m = _re.search(r"_menaPref(\d+)(?:_|$)", s)
    return float(m.group(1)) / 100.0 if m else 1.0


def _parse_mena_risk_overrides(s: str) -> dict[str, float]:
    """Variant B: per-country sovereign-risk premium overrides.
    Format: _menaRisk{CC}_{NNN} where NNN is pp × 10 (e.g. _menaRiskLY_50 → 5.0 pp).
    Returns a dict[CC] -> percentage points (additive on top of 4% base rate).
    """
    # Lookahead on the trailing boundary so consecutive risk overrides don't
    # consume each other's leading underscore: _menaRiskMA_5_menaRiskLY_50
    # must yield BOTH ("MA",5) and ("LY",50).
    out: dict[str, float] = {}
    for m in _re.finditer(r"_menaRisk([A-Z]{2})_(\d+)(?=_|$)", s):
        out[m.group(1)] = float(m.group(2)) / 10.0
    return out


def _parse_ng_price_override(s: str) -> float | None:
    """`_ngPriceNNN` → NNN €/MWh_th explicit natural_gas bus price."""
    m = _re.search(r"_ngPrice(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else None


def _parse_oil_price_override(s: str) -> float | None:
    """`_oilPriceNNN` → NNN €/MWh_th explicit oil bus price."""
    m = _re.search(r"_oilPrice(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else None


def _parse_fuel_ramp_override(s: str) -> float | None:
    """`_fuelRampNN` → NN/10 percent/year linear ramp on WB-fetched fuel prices
    (natural_gas + oil). Default `_DEFAULT_FUEL_ANNUAL_RAMP_PCT` applies when
    unset. Use `_fuelRamp0` to disable the ramp (current-market price only).

    Examples:
      _fuelRamp15 → 1.5 %/yr linear ramp
      _fuelRamp20 → 2.0 %/yr
      _fuelRamp30 → 3.0 %/yr
      _fuelRamp0  → no ramp (today's prices held flat to 2050)
    """
    m = _re.search(r"_fuelRamp(\d+)(?:_|$)", s)
    return float(m.group(1)) / 10.0 if m else None

