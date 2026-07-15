"""
clever.constants — Single source of truth for all mappings and parameters.

This module consolidates every constant, mapping, and lookup table used
across the CLEVER modelling pipeline.  Nothing here is duplicated
elsewhere in the package.

Sections
--------
1. Country mapping
2. CLEVER → model technology mappings
3. CLEVER VRE specifications + profile fallbacks
4. EOLES cost mappings (model-tech → EOLES-tech, lifetimes, fuel adders)
5. PEMMDB hydro 2050/NationalTrends capacities
6. SupplyForge hydro input candidates
7. Battery storage (BESS) specifications
8. Interconnections (NTC)
9. Capacity expansion / adequacy parameters
10. Numerical guards
11. Dispatchable assumed full-load hours (single definition)
"""
from __future__ import annotations

import os as _os

# Scenario knob read from environment — drives per-scenario gating below.
# Notebook cell 1.1 sets the same env var; for non-notebook entrypoints
# (scripts/run_sweep.py, ad-hoc imports) the env var must be set BEFORE
# `import clever` for the gates to take effect. Default empty = R0-style
# behaviour (no Nuclear expansion, VRE upper band 0.15).
_SCENARIO: str = _os.environ.get("CLEVER_SCENARIO", "")
# Inheritance gates (use prefix/substring matching so suffix variants
# like _corr2x and _noMin_corr2x automatically inherit the right gate
# from their base scenario).
def _is_high_demand(s: str) -> bool:
    """central H₂ bundle + +20% elec + VRE 0.30 — i.e. any policy_* scenario."""
    return s.startswith("policy_")

def _is_nuclear_expandable(s: str) -> bool:
    """Nuclear added to EXPANDABLE_MODEL_TECHS + force-injected as a tech."""
    return "_nuke" in s   # matches R0_v1_nuke, policy_nuke, and their *_corrNx variants

# Frozen sets kept for backwards-compat callers; computed eagerly.
_HIGH_DEMAND_SCENARIOS = {s for s in (
    "policy_re", "policy_nuke", "policy_re_noMin",
    "policy_re_corr2x", "policy_re_corr3x",
    "policy_nuke_corr2x", "policy_nuke_corr3x",
    "policy_re_noMin_corr2x", "policy_re_noMin_corr3x",
)}
_NUCLEAR_EXPANDABLE_SCENARIOS = {s for s in (
    "policy_nuke", "R0_v1_nuke",
    "policy_nuke_corr2x", "policy_nuke_corr3x",
    "R0_v1_nuke_corr2x", "R0_v1_nuke_corr3x",
)}

# Corridor-expansion gating (electrons vs molecules trade-off variant).
# When SCENARIO is one of the *_corrNx values, the LP gains the ability
# to invest in electricity transmission expansion on 4 specified corridors
# at 1 M€/MW HVDC central CAPEX (ENTSO-E TYNDP 2024 / IRENA 2020 anchor).
# Floor = current NTC (free), ceiling = N × current NTC.
# Use a regex anchored on word-boundary (followed by _ or end-of-string) so
# that scenarios with axes appended AFTER `_corr2x` (e.g. `_corr2x_elecX125_h2central`)
# still match.
import re as _re_corr
if _re_corr.search(r"_corr3x(?:_|$)", _SCENARIO):
    _CORRIDOR_MULT: float = 3.0
elif _re_corr.search(r"_corr2x(?:_|$)", _SCENARIO):
    _CORRIDOR_MULT: float = 2.0
else:
    _CORRIDOR_MULT: float = 1.0  # pinned NTC (current behaviour)

# The 13 corridors that gain investable expansion. Stored as frozensets so
# direction (DK→DE vs DE→DK) doesn't matter — both entries in
# MANUAL_INTERCONNECTIONS are recognised.
# Phase 2.7 (2026-05-25) added 5 corridors from ENTSO-E TYNDP 2024 Project
# Bundle priorities — particularly Italy-AT (Brenner) which materially
# relieves Italy's structural adequacy bottleneck in DECARB HIGH.
INVESTABLE_CORRIDOR_PAIRS: set[frozenset[str]] = {
    # ── Original 8 ────────────────────────────────────────────────────
    frozenset({"DK", "DE"}),    # Kontek + Kriegers Flak (current 4 GW)
    frozenset({"NO", "DK"}),    # Skagerrak (current 1.6 GW)
    frozenset({"NL", "DE"}),    # current ~4.5 GW
    frozenset({"FR", "ES"}),    # Pyrenees + Bay of Biscay (current 3.9 GW)
    # ── UK-continent (TYNDP 2024 announced projects) ─────────────────
    frozenset({"FR", "GB"}),    # LionLink + IFA3 (current 4 GW)
    frozenset({"NL", "GB"}),    # NeuConnect (current 1 GW)
    # ── Italy continental + Norway-Germany ──────────────────────────
    frozenset({"IT", "FR"}),    # Savoie-Piémont commissioning 2025 (current 4.2 GW)
    frozenset({"NO", "DE"}),    # NordLink + potential 2nd link (current 1.4 GW)
    # ── Phase 2.7 additions (TYNDP 2024 priority bundle) ────────────
    frozenset({"IT", "AT"}),    # Brenner — IT structural import need (current 360 MW)
    frozenset({"IT", "CH"}),    # Mendrisio + Greenconnector planned (current 4.5 GW)
    frozenset({"DE", "PL"}),    # Coal-exit hedge; Polish wind export (current 3 GW)
    frozenset({"DE", "CH"}),    # Alpine hydro flex access (current 4 GW)
    frozenset({"IT", "GR"}),    # GRITA + planned 2× upgrade — Greek wind export (current 500 MW)
}

# HVDC corridor anchor: 0.75 M€/MW per directional MW (= 1.5 M€/MW
# physical HVDC bipole). Mid-range choice on 2026-05-19, picked over
# the conservative 1.0 M€/MW per-direction (= 2.0 M€/MW physical) to
# correct the NTC double-counting bias that would otherwise penalise
# bidirectional corridors. Anchors:
#   - NorNed (NO→NL 2008): ~0.9 M€/MW physical
#   - NordLink (NO→DE 2021): ~1.0 M€/MW physical
#   - Bay of Biscay (ES→FR 2028 plan): ~1.2 M€/MW physical
#   - ENTSO-E TYNDP 2024 central: ~1.0-1.5 M€/MW physical
# So 1.5 M€/MW physical sits at the upper-mid of the literature band,
# defensibly conservative. Per-direction value (0.75 M€/MW) is what
# POMMES uses, because each link direction has its own decision var.
# Annualised at finance_rate=0.04, life_span=40 yr → CRF=0.0505 →
# ~37.9 k€/MW/yr per direction. Total annual cost for 1 GW physical
# bidirectional HVDC ≈ 2 × 1000 MW × 37.9 = 75.8 M€/yr.
INVESTABLE_CORRIDOR_CAPEX_EUR_PER_MW: float = 750_000.0
INVESTABLE_CORRIDOR_LIFE_SPAN_YR: int = 40
INVESTABLE_CORRIDOR_FINANCE_RATE: float = 0.04
INVESTABLE_CORRIDOR_MULT: float = _CORRIDOR_MULT  # exposed for model.py

# ═══════════════════════════════════════════════════════════════════
# Biomethane / ATR / H₂-demand-multiplier / electrolyser-CAPEX scenario suffixes
# ═══════════════════════════════════════════════════════════════════
# These additional suffix axes stack with the base scenario name:
#   _bioLow            → Tier 1 biomethane feedstocks × ENSPRESO ENS_Low
#   _bioMed            → Tier 1+2 biomethane feedstocks × ENSPRESO ENS_Med
#   _bioHigh           → Tier 1+2+3 (SRC: willow + poplar) × ENSPRESO ENS_High
#   _atr               → enable ATR_biomethane tech alongside Biomethane_CCGT
#   _elNNN             → override electrolyser CAPEX to NNN €/kW (e.g. _el700, _el900)
#   _h2HIGH            → scale H₂ demand by ~1.56× (898 → ~1400 TWh, TYNDP-aligned)
#
# Suffixes are parsed independently and can stack in any order:
#   policy_nuke_bioMed_el900_atr  → all four flags active
#   R0_v1_bioLow                  → biomethane only
import re as _re

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

# ── Methane-leakage pricing + CCS deployment cap (2026-06-11) ───────────────
# CH4 leakage is a real GHG cost the CO2-combustion adder misses. It is priced
# UNREBATED (CCS cannot capture upstream/fugitive losses) and scales with
# resolved_carbon_price(), which is what makes carbon pricing differentiate
# green from blue H2 again (the 90% capture rebate otherwise shields blue).
CH4_KG_PER_MWH_TH: float = 71.94       # 1 MWh_th LHV / 13.9 kWh/kg
CH4_GWP100_FOSSIL: float = 29.8        # IPCC AR6 (fossil methane)
CH4_GWP100_BIOGENIC: float = 27.0      # IPCC AR6 (biogenic methane)

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

_NG_UPSTREAM_LEAK_RATE: float = _parse_ng_leak_rate(_SCENARIO)
_BIO_CH4_LEAK_RATE: float = _parse_bio_leak_rate(_SCENARIO)
_CCS_CAP_GW_PER_COUNTRY: float | None = _parse_ccs_cap_gw(_SCENARIO)
_VRE_XXL: bool = _parse_vre_xxl(_SCENARIO)
_VRE_FREE: bool = _parse_vre_free(_SCENARIO)
_NUKE_XXL: bool = _parse_nuke_xxl(_SCENARIO)
# Demand-side load-shedding price overrides (effacement / VoLL sensitivity).
# `_vollNNN` -> electricity, `_h2vollNNN` -> hydrogen (NNN €/MWh). None = default.
_ELECTRICITY_VOLL_OVERRIDE: float | None = _parse_voll_override(_SCENARIO, "voll")
_H2_VOLL_OVERRIDE: float | None = _parse_voll_override(_SCENARIO, "h2voll")

# Distance-based H₂-pipeline CAPEX via `_pipekmNNN` (NNN = €/MW/km). When present,
# every H₂ pipeline's invest_cost becomes NNN × centroid-to-centroid great-circle
# km (EU h2_pipeline + MENA mena_h2_pipeline*), replacing the flat 500k€/MW and the
# hand-tuned MENA route dict. None ⇒ legacy flat costs. Applied in
# clever.runner.apply_distance_based_pipeline_capex (after enforce_uniform_wacc).
def _parse_pipeline_km_cost(s: str) -> float | None:
    m = _re.search(r"_pipekm(\d+)(?:_|$)", s)
    return float(m.group(1)) if m else None
_PIPELINE_EUR_PER_MW_PER_KM: float | None = _parse_pipeline_km_cost(_SCENARIO)


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


# Eagerly evaluated at module-load (env var must be set before `import clever`):
_BIOMETHANE_SCOPE: str | None = _parse_biomethane_scope(_SCENARIO)
_ATR_ENABLED:      bool        = _parse_atr_enabled(_SCENARIO)
_ELECTROLYSER_CAPEX_EUR_PER_KW: float = _parse_electrolyser_capex_override(_SCENARIO)
_DEMANDFORGE_BUNDLE_OVERRIDE: str | None = _parse_demandforge_bundle_override(_SCENARIO)

# Resolved DemandForge bundle name — single source of truth for both EU and
# MENA demand wiring. Same precedence as scripts/run_adequacy.py:
#   1. _h2HIGH suffix → "high_h2"
#   2. _h2central suffix → "central"
#   3. R0_v1* prefix → "low_h2"
#   4. else (policy_*, etc.) → "central"
if "_h2HIGH" in _SCENARIO:
    DEMANDFORGE_BUNDLE: str = "high_h2"
elif "_h2central" in _SCENARIO:
    DEMANDFORGE_BUNDLE = "central"
elif _SCENARIO.startswith("R0_v1"):
    DEMANDFORGE_BUNDLE = "low_h2"
else:
    DEMANDFORGE_BUNDLE = "central"
_CO2_PRICE_EUR_PER_TONNE: float = _parse_co2_price_override(_SCENARIO)
_GAS_FLOOR_LIFTED: bool = _parse_gas_floor_lifted(_SCENARIO)
_ELEC_DEMAND_MULTIPLIER: float = _parse_elec_demand_multiplier(_SCENARIO)
_NO_GAS: bool = _parse_no_gas(_SCENARIO)
_NO_ELEC_FLOOR: bool = _parse_no_elec_floor(_SCENARIO)
_NO_GRID_EXPANSION: bool = _parse_no_grid_exp(_SCENARIO)
_H2_LOCAL_SHARE: float | None = _parse_h2_local_share(_SCENARIO)
_VRE_EXTENDED: bool = _parse_vre_extended(_SCENARIO) or _VRE_XXL
_WEATHER_YEAR_OVERRIDE: int | None = _parse_weather_year(_SCENARIO)

# MENA H₂ imports — Variant A (exogenous) and Variant B (optimised). Default OFF.
_MENA_H2_IMPORT_CAP_TWH: float = _parse_mena_h2_cap(_SCENARIO)
# Flat delivered-cost override (€/MWh_H2). None = use per-entry upper-band
# defaults from MENA_H2_ENTRY_POINTS, unless per-entry overrides below apply.
_MENA_H2_DELIVERED_COST_EUR_PER_MWH: float | None = _parse_mena_h2_cost(_SCENARIO)
# Per-entry-point delivered-cost overrides, €/MWh_H2 (e.g. {"ES": 95.0, "IT": 120.0}).
# Empty dict = no per-entry override.
_MENA_H2_DELIVERED_COST_BY_ENTRY: dict[str, float] = _parse_mena_h2_cost_by_entry(_SCENARIO)
_MENA_OPTIM_ACTIVE_COUNTRIES: tuple[str, ...] = _parse_mena_optim_active(_SCENARIO)
_MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW: float | None = _parse_mena_infra_global(_SCENARIO)
_MENA_INFRA_PER_ROUTE_OVERRIDES: dict[tuple[str, str], float] = _parse_mena_infra_overrides(_SCENARIO)
_MENA_RISK_PER_COUNTRY_OVERRIDES: dict[str, float] = _parse_mena_risk_overrides(_SCENARIO)
_MENA_CAP_SCALE: float = _parse_mena_cap_scale(_SCENARIO)
_MENA_PREF_SCALE: float = _parse_mena_pref_scale(_SCENARIO)
# Per-country MENA NG wholesale-price overrides (€/MWh_th, pre-CO₂ adder).
# Empty dict = use built-in defaults in mena_imports.MENA_NG_WHOLESALE_EUR_PER_MWH_TH.
_MENA_NG_WHOLESALE_OVERRIDES: dict[str, float] = _parse_mena_ng_wholesale_overrides(_SCENARIO)

# ENSPRESO scenario coupling (paired by design — see article narrative):
_BIOMETHANE_ENSPRESO_SCENARIO: str | None = {
    "bioLow":  "ENS_Low",
    "bioMed":  "ENS_Med",
    "bioHigh": "ENS_High",
}.get(_BIOMETHANE_SCOPE)


# ═══════════════════════════════════════════════════════════════════
# 1. COUNTRY MAPPING
# ═══════════════════════════════════════════════════════════════════
AREA_MAP: dict[str, str] = {
    "EL": "GR",
    "UK": "GB",
}

# Reverse mapping: model codes → SupplyForge codes.
# SupplyForge uses "UK" where the model uses "GB".
MODEL_TO_SUPPLYFORGE: dict[str, str] = {
    "GB": "UK",
}


# ═══════════════════════════════════════════════════════════════════
# 2. CLEVER → MODEL TECHNOLOGY MAPPINGS
# ═══════════════════════════════════════════════════════════════════
CLEVER_CAPACITY_TO_MODEL: dict[str, str] = {
    "pv": "Solar",
    "onshore": "Wind_Onshore",
    "offshore": "Wind_Offshore",
    "gas": "Gas",
    "coal": "Coal",
    "oil": "Oil",
    "biomass": "Biomass",
    "waste": "Waste",
    "nuke": "Nuclear",
    "nuclear": "Nuclear",
    "other": "Other",
    "geothermal": "Other",
    "hydro": "Hydro",
    "ror": "RoR_Pondage",
    "reservoir_hydro": "Reservoir_Hydro",
    "phs": "Pumped_Hydro",
    "ccg-h2": "Hydrogen_power_plant",
}

CLEVER_NON_ENR_TO_MODEL: dict[str, str] = {
    "gas": "Gas",
    "coal": "Coal",
    "oil": "Oil",
    "biomass": "Biomass",
    "waste": "Waste",
    "nuke": "Nuclear",
    "ccg-h2": "Hydrogen_power_plant",
    "other": "Other",
}


# ═══════════════════════════════════════════════════════════════════
# 3. CLEVER VRE SPECIFICATIONS
# ═══════════════════════════════════════════════════════════════════
CLEVER_VRE_SPECS: dict[str, dict] = {
    "pv": {
        "model_tech": "Solar",
        "default_profile_candidates": ["Solar", "pv_ground_S", "pv", "Solar PV"],
        "eoles_tech": "pv_ground_S",
    },
    "onshore": {
        "model_tech": "Wind_Onshore",
        "default_profile_candidates": ["Wind Onshore", "Wind_Onshore", "onshore"],
        "eoles_tech": "onshore",
    },
    "offshore": {
        "model_tech": "Wind_Offshore",
        "default_profile_candidates": [
            "Wind Offshore",
            "Wind_Offshore",
            "offshore_ground",
            "offshore_float",
        ],
        "eoles_tech": "offshore_ground",
    },
}

VRE_PROFILE_FALLBACK_BY_COUNTRY: dict[str, dict[str, list[str]]] = {
    # Original 9
    "CH": {
        "pv": ["FR", "DE", "AT", "IT"],
        "onshore": ["AT", "DE", "FR", "IT"],
        "offshore": ["DE", "NL", "BE", "FR"],
    },
    "FR": {"offshore": ["BE", "NL", "DE", "ES"]},
    "IT": {"offshore": ["ES", "FR", "GR"]},
    "AT": {"offshore": ["DE", "NL", "BE"]},
    "GB": {
        "pv": ["FR", "NL", "BE", "DE"],
        "onshore": ["FR", "NL", "DE", "BE"],
        "offshore": ["NL", "DE", "BE", "FR"],
    },
    "BE": {
        "pv": ["NL", "FR", "DE"],
        "onshore": ["NL", "FR", "DE"],
        "offshore": ["NL", "DE", "FR", "GB"],
    },
    # Landlocked — fallback offshore to nearest coastal neighbour
    "CZ": {"offshore": ["DE", "PL"]},
    "SK": {"offshore": ["PL", "DE"]},
    "HU": {"offshore": ["HR", "RO", "PL"]},
    "LU": {"pv": ["BE", "DE", "FR"], "onshore": ["BE", "DE", "FR"], "offshore": ["BE", "NL", "DE"]},
    "SI": {"offshore": ["HR", "IT"]},
    "RS": {"offshore": ["HR", "RO", "BG"]},
    # Nordic / Baltic — often share wind regimes
    "DK": {"pv": ["DE", "NL"]},
    "NO": {"pv": ["SE", "DK", "DE"]},
    "SE": {"pv": ["DK", "DE", "FI"]},
    "FI": {"pv": ["SE", "EE"]},
    "EE": {"pv": ["FI", "LV", "SE"]},
    "LV": {"pv": ["LT", "EE"]},
    "LT": {"pv": ["LV", "PL"]},
    # IE — close to GB
    "IE": {"pv": ["GB"], "onshore": ["GB"], "offshore": ["GB"]},
    # Balkan — proxy via neighbours
    "BA": {"pv": ["HR", "RS"], "onshore": ["HR", "RS"], "offshore": ["HR"]},
    "ME": {"pv": ["RS", "AL", "HR"], "onshore": ["RS", "HR"], "offshore": ["HR", "IT"]},
    "MK": {"pv": ["GR", "BG", "RS"], "onshore": ["GR", "BG"], "offshore": ["GR"]},
    "AL": {"pv": ["GR", "IT", "ME"], "onshore": ["GR", "ME"], "offshore": ["GR", "IT"]},
    # Islands
    "CY": {"onshore": ["GR"], "offshore": ["GR"]},
    "MT": {"pv": ["IT"], "onshore": ["IT"], "offshore": ["IT"]},
}

# Default capacity factors for VRE flat-profile fallback (last resort).
# Used when no SupplyForge profile is found for any country/year combination.
# These represent conservative annual-average capacity factors from:
#   - Solar: JRC PVGIS 5.2, country-weighted averages
#   - Wind onshore: WindEurope Annual Statistics 2023, IEA Wind TCP
#   - Wind offshore: IRENA 2023, ENTSO-E ERAA 2023
# Note: flat profiles produce conservative results because they lack
# temporal correlation with demand (no solar peak, no wind variability).
DEFAULT_VRE_FLAT_CF: dict[str, dict[str, float]] = {
    # Original 9
    "FR": {"Solar": 0.14, "Wind_Onshore": 0.24, "Wind_Offshore": 0.39},
    "DE": {"Solar": 0.11, "Wind_Onshore": 0.22, "Wind_Offshore": 0.42},
    "GB": {"Solar": 0.11, "Wind_Onshore": 0.27, "Wind_Offshore": 0.42},
    "IT": {"Solar": 0.16, "Wind_Onshore": 0.22, "Wind_Offshore": 0.32},
    "ES": {"Solar": 0.18, "Wind_Onshore": 0.25, "Wind_Offshore": 0.35},
    "BE": {"Solar": 0.11, "Wind_Onshore": 0.22, "Wind_Offshore": 0.40},
    "NL": {"Solar": 0.11, "Wind_Onshore": 0.24, "Wind_Offshore": 0.43},
    "CH": {"Solar": 0.12, "Wind_Onshore": 0.18, "Wind_Offshore": 0.0},  # landlocked
    "AT": {"Solar": 0.12, "Wind_Onshore": 0.22, "Wind_Offshore": 0.0},  # landlocked
    # Nordics
    "DK": {"Solar": 0.10, "Wind_Onshore": 0.28, "Wind_Offshore": 0.45},
    "SE": {"Solar": 0.09, "Wind_Onshore": 0.28, "Wind_Offshore": 0.42},
    "NO": {"Solar": 0.08, "Wind_Onshore": 0.32, "Wind_Offshore": 0.45},
    "FI": {"Solar": 0.09, "Wind_Onshore": 0.28, "Wind_Offshore": 0.40},
    # Iberian
    "PT": {"Solar": 0.18, "Wind_Onshore": 0.26, "Wind_Offshore": 0.38},
    # Central Europe
    "PL": {"Solar": 0.11, "Wind_Onshore": 0.24, "Wind_Offshore": 0.40},
    "CZ": {"Solar": 0.11, "Wind_Onshore": 0.22, "Wind_Offshore": 0.0},  # landlocked
    "SK": {"Solar": 0.12, "Wind_Onshore": 0.22, "Wind_Offshore": 0.0},  # landlocked
    "HU": {"Solar": 0.13, "Wind_Onshore": 0.22, "Wind_Offshore": 0.0},  # landlocked
    "SI": {"Solar": 0.13, "Wind_Onshore": 0.20, "Wind_Offshore": 0.30},
    "HR": {"Solar": 0.14, "Wind_Onshore": 0.22, "Wind_Offshore": 0.30},
    "LU": {"Solar": 0.11, "Wind_Onshore": 0.20, "Wind_Offshore": 0.0},  # landlocked
    # South-East Europe
    "RO": {"Solar": 0.14, "Wind_Onshore": 0.25, "Wind_Offshore": 0.35},
    "BG": {"Solar": 0.15, "Wind_Onshore": 0.22, "Wind_Offshore": 0.32},
    "GR": {"Solar": 0.18, "Wind_Onshore": 0.24, "Wind_Offshore": 0.35},
    # Baltics
    "EE": {"Solar": 0.09, "Wind_Onshore": 0.26, "Wind_Offshore": 0.40},
    "LV": {"Solar": 0.09, "Wind_Onshore": 0.25, "Wind_Offshore": 0.38},
    "LT": {"Solar": 0.09, "Wind_Onshore": 0.25, "Wind_Offshore": 0.38},
    # Ireland
    "IE": {"Solar": 0.10, "Wind_Onshore": 0.30, "Wind_Offshore": 0.42},
    # Islands
    "CY": {"Solar": 0.20, "Wind_Onshore": 0.22, "Wind_Offshore": 0.35},
    "MT": {"Solar": 0.19, "Wind_Onshore": 0.18, "Wind_Offshore": 0.30},
}
# Fallback for countries not in the above dict
DEFAULT_VRE_FLAT_CF_GENERIC: dict[str, float] = {
    "Solar": 0.13,
    "Wind_Onshore": 0.23,
    "Wind_Offshore": 0.38,
}

# SupplyForge fallback reference years: ordered list of years to try
# when the requested weather year returns 404. Year 2021 is the only
# confirmed year with complete VRE and hydro profiles on the GCS bucket.
SUPPLYFORGE_FALLBACK_YEARS: list[int] = [2021, 2022, 2023]


# ═══════════════════════════════════════════════════════════════════
# 4. EOLES COST MAPPINGS
# ═══════════════════════════════════════════════════════════════════
MODELTECH_TO_EOLES: dict[str, str | None] = {
    "Coal": "coal",
    "Gas": "ch4_ccgt",
    "Oil": "ch4_ocgt",
    "Biomass": "biomass_coge",
    "Waste": "waste",
    "Nuclear": "nuclear",
    "Hydrogen_power_plant": "h2_ccgt",
    "Other": None,
}

# Technical lifetimes for annuity computation (years).
# Sources: IEA WEO 2023, IRENA Renewable Power Generation Costs 2023, JRC ETRI 2024.
#
# Rationale for differences with EOLES lifetime.csv (which uses 40yr uniformly):
#   - offshore_ground: 30yr — IEA/IRENA consensus for bottom-fixed offshore wind;
#     40yr exceeds observed/warranted lifetimes. Foundations may last 40yr but
#     turbines require major overhaul or replacement at ~25-30yr.
#   - offshore_float: 25yr — floating foundations are less mature technology;
#     mooring and dynamic cable fatigue limits lifetime (DNV-GL 2023).
#   - ch4_ccgt: 30yr — standard gas turbine economic lifetime (IEA WEO 2023).
#     40yr is possible physically but rarely achieved without major refurbishment.
#   - coal: 1yr — intentional modelling choice for CLEVER decarbonisation scenario.
#     Setting lifetime=1yr makes coal CAPEX annuity extremely high, effectively
#     preventing new coal investment. This is consistent with EU coal phase-out
#     targets (all EU member states committed to phase-out by 2038 at latest).
#     Existing coal capacity is handled via non-expandable cap_min=cap_max.
#   - natural_gas: 1yr — fuel commodity, not a plant (no CAPEX to annuitise).
EOLES_LIFETIME: dict[str, int] = {
    "offshore_float": 25,
    "offshore_ground": 30,
    "onshore": 25,
    "pv_ground_S": 30,
    "pv_ground_EW": 30,
    "pv_roof_com_S": 30,
    "pv_roof_indiv_S": 30,
    "pv_roof_com_EW": 30,
    "pv_roof_indiv_EW": 30,
    "river": 80,
    "lake": 80,
    "nuclear": 60,
    "ch4_ocgt": 30,
    "ch4_ccgt": 30,
    "phs": 80,
    "electrolysis": 20,
    "h2_saltcavern": 40,
    "h2_ccgt": 30,
    "methanization": 20,
    "battery_1h": 15,
    "battery_4h": 15,
    "ch4_reservoir": 40,
    "methanation": 20,
    "pyrogazification": 20,
    "natural_gas": 1,
    "coal": 1,
    "marine": 25,
    "waste": 30,
    "biomass_coge": 25,
    "geothermal_coge": 30,
    "ocgt_coge": 25,
    "str_dummy": 1,
    "rsv_dummy": 1,
}

# Fuel adders: variable fuel + CO₂ cost (EUR/MWh_e), added to EOLES VOM.
# These represent the SHORT-RUN marginal cost of fuel combustion and are
# critical for merit-order positioning.
#
# Methodology: fuel_cost/efficiency + CO₂_intensity × CO₂_price
# 2050 projections (CLEVER sufficiency scenario, high-ambition decarbonisation):
#   - Natural gas price: ~40 EUR/MWh_th (IEA WEO 2023 Net Zero, EU import price)
#   - CO₂ price: ~150 EUR/tCO₂ (EU ETS — EC "Fit for 55" central estimate for 2050)
#   - CCGT efficiency: ~58% (state-of-the-art combined cycle)
#   - CCGT CO₂ intensity: 0.37 tCO₂/MWh_e (IPCC AR6, natural gas at 58% eff.)
#   - CCGT total: 40/0.58 + 0.37×150 ≈ 69 + 55.5 ≈ 125 EUR/MWh → rounded to 125
#   - OCGT efficiency: ~38% → 40/0.38 + 0.56×150 ≈ 105 + 84 ≈ 189 → 170 (conservative)
#   - Coal: 15 EUR/MWh_th, 42% eff., 0.82 tCO₂/MWh_e → 36 + 123 ≈ 159 → 45 (legacy
#     plants only, no CO₂ adder because coal=1yr lifetime already prevents investment)
#   - H2 CCGT: green H₂ at ~2.5 EUR/kg ≈ 75 EUR/MWh_th, 58% eff. → 129 + electrolysis
#     losses → ~150 EUR/MWh (no CO₂ component)
FUEL_ADDER_2050: dict[str, float] = {
    "nuclear": 8.0,         # Fuel cycle cost only (no CO₂)
    # ch4_ccgt + ch4_ocgt REMOVED (Phase 3 refactor): fossil methane is an
    # endogenous resource bus now. Fuel cost (WB Pink Sheet) + CO₂ tax flow
    # through NetImport(natural_gas) — see natural_gas_import_price() and
    # the Gas/Oil factor in clever/model.py:add_dispatchable_from_non_enr.
    "coal": 45.0,           # Legacy fuel cost only (investment blocked by lifetime=1yr)
    "waste": 5.0,           # Minimal fuel cost (waste-to-energy)
    "biomass_coge": 35.0,   # Biomass procurement cost (~35 EUR/MWh_th / 100% biogenic)
    "h2_ccgt": 150.0,       # Green H₂ at ~2.5 EUR/kg (IEA Net Zero 2050 target)
}

# Plant efficiencies for the fossil-methane consumer techs (used both here
# for documentation and in model.py:add_dispatchable_from_non_enr to set
# the natural_gas factor):
_CCGT_EFF: float = 0.58   # CCGT electrical efficiency (state-of-the-art 2050)
_OCGT_EFF: float = 0.38   # OCGT peaker efficiency
# Pre-Phase-3 dynamic recompute of FUEL_ADDER_2050["ch4_ccgt"/"ch4_ocgt"] on
# _co2NNN — REMOVED. The CO₂ tax now layers onto the natural_gas bus price
# via natural_gas_import_price(), so _co2NNN flows through automatically.

# ─── Phase 3: Fossil-methane + oil resource buses ──────────────────────────
# Methane and oil are POMMES resources with per-country NetImport supply at a
# fetched market-anchored price + CO₂ adder. Consumers:
#   - "Gas" (ch4_ccgt, eff 0.58) — consumes natural_gas
#   - "Oil" (ch4_ocgt, eff 0.38) — consumes natural_gas
#     (CLEVER's "Oil" model_tech is routed through EOLES's ch4_ocgt mapping →
#      OCGT peakers, methane-fired. The "Oil" label is preserved from CLEVER;
#      see MODELTECH_TO_EOLES.)
# An "oil" resource bus is added for future-facing capability — a NetImport
# at Brent + refining margin exists, but no current tech consumes from it.
#
# Price source: World Bank Pink Sheet monthly XLSX (clever/data_fetchers.py).
# Default: trailing 12-month average from the WB fetcher as the 2050 cost
# anchor (mean-reversion assumption; documented in methods section).
# Override per-scenario via:
#   _ngPriceNNN   → explicit natural_gas import price in €/MWh_th (bypasses ramp)
#   _oilPriceNNN  → explicit oil import price in €/MWh_th (bypasses ramp)
#   _fuelRampNN   → optional linear ramp on WB-fetched bare prices (NG + oil),
#                   pct/yr × 10. DEFAULT 0 (no ramp) — the WB Pink Sheet 12-mo
#                   trailing mean (~38 €/MWh_th for NG) already aligns with
#                   TYNDP 2026's ~35 €/MWh_th 2050 projection, so no forward
#                   projection is needed. Use this knob for sensitivity tests
#                   like `_fuelRamp20` (+2 %/yr → 48 % cumulative uplift over
#                   24 yrs) when stress-testing escalating geopolitical premium.
#   _co2NNN       → flat €/tCO₂ override (bypasses trajectory). Default 150.
#   _co2off       → disable CO₂ adder entirely (sets price to 0).
#   _co2trajNAME  → select a published CO₂ price trajectory by name (camelCase
#                   so the suffix regex stops cleanly at `_`). NAME ∈
#                   {ff55, tyndpNT, tyndpDE, tyndpGA, weoSTEPS, weoAPS,
#                    weoNZE, bnef}. See clever/carbon_price.py for the full
#                   anchor points and citations. Default trajectory is `ff55`
#                   (EC Fit-for-55 IA, 150 €/tCO₂ at 2050).
#                   Examples: `_co2trajweoNZE` → IEA Net-Zero (250 in 2050);
#                             `_co2trajtyndpDE` → TYNDP 2024 Distributed
#                             Energy (290 in 2050).
# WB fetcher always tries first; falls back to IEA WEO 2024 STEPS 2050
# hardcoded anchor if unreachable (~21 €/MWh_th NG, ~30 €/MWh_th Brent+15%).
NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH: float = 0.202   # IPCC AR6, NG combustion
OIL_CO2_INTENSITY_T_PER_MWH_TH:         float = 0.267   # IPCC AR6, fuel oil combustion


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


_NG_PRICE_OVERRIDE_EUR_PER_MWH_TH:  float | None = _parse_ng_price_override(_SCENARIO)
_OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH: float | None = _parse_oil_price_override(_SCENARIO)
_FUEL_RAMP_PCT_OVERRIDE:            float | None = _parse_fuel_ramp_override(_SCENARIO)

# ─── Fuel-price ramp infrastructure (added 2026-05-27) ───────────────────────
# DEFAULT = 0 %/yr (no ramp). Methodological rationale:
#
#   WB Pink Sheet 12-month trailing mean for natural gas (NGAS_EUR / TTF) is
#   currently ~35-38 €/MWh_th. TYNDP 2026 (ENTSOG-ENTSO-E joint scenarios,
#   published 2025) projects ~35 €/MWh_th for the EU natural-gas import price
#   in 2050 across its central scenarios. Today's WB-fetched price is
#   therefore *already aligned* with the most recent EU peer-reviewed 2050
#   anchor — no forward projection needed. Using the live fetcher gives us
#   the additional benefit of automatic refresh as the WB Pink Sheet
#   updates (and any future divergence from TYNDP 2026 will surface
#   loudly via the logged delivered price).
#
#   Alternative anchors (IEA WEO 2024 STEPS = ~22 €/MWh_th, TYNDP 2024 DE =
#   ~18-20 €/MWh_th) systematically under-price relative to the post-2022
#   structural reset. We deliberately do NOT use them as defaults.
#
# The linear-ramp infrastructure remains in place as an optional sensitivity
# knob (e.g. `_fuelRamp30` for a +3 %/yr stress test of escalating geopolitical
# premium). Override via _fuelRampNN suffix.
#
# Methodology when active:
#   bare_target_year = bare_today × (1 + ramp_pct/100 × years_to_target)
# Applies symmetrically to natural_gas and oil.
_DEFAULT_FUEL_ANNUAL_RAMP_PCT: float = 0.0
_TARGET_MODEL_YEAR: int = 2050  # CLEVER's central modelling year


def _resolved_fuel_ramp_pct() -> float:
    """Return the active linear annual ramp factor (%/yr) for fuel prices."""
    if _FUEL_RAMP_PCT_OVERRIDE is not None:
        return _FUEL_RAMP_PCT_OVERRIDE
    return _DEFAULT_FUEL_ANNUAL_RAMP_PCT


def _fuel_ramp_multiplier() -> float:
    """Multiplier applied to bare WB Pink Sheet prices to reflect the
    geopolitical-premium ramp from today's market to MODEL_YEAR.

    Linear (not compounding): 1 + ramp_pct/100 × years_to_target.
    Safe for ramp=0 (returns 1.0 → no change).
    """
    from datetime import datetime
    years_to_target = max(0, _TARGET_MODEL_YEAR - datetime.now().year)
    return 1.0 + (_resolved_fuel_ramp_pct() / 100.0) * years_to_target


def natural_gas_import_price() -> float:
    """Delivered price on the natural_gas bus (€/MWh_th), including CO₂ tax.

    Bare commodity anchor: WB Pink Sheet 12-month trailing mean (NGAS_EUR /
    TTF benchmark). Currently ~38 €/MWh_th — methodologically aligned with
    TYNDP 2026's 2050 EU natural-gas import projection (~35 €/MWh_th), so
    no forward-projection ramp is applied by default.

    Precedence:
      1. Scenario override `_ngPriceNNN` (skips both fetcher AND ramp)
      2. Live WB Pink Sheet fetcher × `_fuelRamp` multiplier (default 1.0)
      3. IEA WEO 2024 STEPS 2050 hardcoded fallback × ramp (if fetcher fails)

    Optional ramp: `_fuelRampNN` applies a linear pct/yr uplift for stress
    testing escalating geopolitical premium. Default 0 %/yr (no ramp).
    CO₂ adder resolved via `clever.carbon_price.resolved_carbon_price()`,
    which honours `_co2off`, `_co2NNN` (flat override) and `_co2trajNAME`
    (named trajectory at MODEL_YEAR). Default trajectory `ff55` (EC FF55)
    gives 150 €/tCO₂ at 2050. CO₂ adder = CO₂ price × 0.202 t/MWh_th (NG).
    """
    import logging
    logger = logging.getLogger(__name__)

    if _NG_PRICE_OVERRIDE_EUR_PER_MWH_TH is not None:
        # Explicit override: take the user's number as-is, no ramp.
        bare_price = _NG_PRICE_OVERRIDE_EUR_PER_MWH_TH
        ramp_mult = 1.0
        logger.info(
            "natural_gas_import_price: explicit override %.1f €/MWh_th (no ramp applied)",
            bare_price,
        )
    else:
        from clever.data_fetchers import fetch_natural_gas_price_eur_per_mwh_th
        bare_today = fetch_natural_gas_price_eur_per_mwh_th()
        ramp_mult = _fuel_ramp_multiplier()
        bare_price = bare_today * ramp_mult
        logger.info(
            "natural_gas_import_price: WB Pink Sheet %.1f €/MWh_th × ramp %.3f = %.1f €/MWh_th "
            "(geopolitical-premium ramp @ %.1f %%/yr linear)",
            bare_today, ramp_mult, bare_price, _resolved_fuel_ramp_pct(),
        )

    # CO₂ adder via the dedicated carbon_price module (trajectory-based).
    # Lazy import to avoid a constants.py ↔ carbon_price.py circular dep
    # (carbon_price imports _CO2_PRICE_EUR_PER_TONNE from this module).
    from clever.carbon_price import resolved_carbon_price
    co2_price = resolved_carbon_price(year=_TARGET_MODEL_YEAR)
    co2_adder = NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH * co2_price
    # Upstream CH4 leakage: leaked share of delivered energy, priced at
    # GWP100 x CO2 price. Unrebated by CCS (the leak happens before capture).
    leak_adder = (
        _NG_UPSTREAM_LEAK_RATE * CH4_KG_PER_MWH_TH * CH4_GWP100_FOSSIL
        * co2_price / 1000.0
    )
    final = bare_price + co2_adder + leak_adder
    logger.info(
        "natural_gas_import_price: + CO2 %.1f (%.3f t/MWh × %.0f €/tCO2) "
        "+ CH4 leak %.1f (%.1f%% × GWP100 %.1f) = %.1f €/MWh_th delivered",
        co2_adder, NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH, co2_price,
        leak_adder, _NG_UPSTREAM_LEAK_RATE * 100.0, CH4_GWP100_FOSSIL, final,
    )
    return final


def oil_import_price() -> float:
    """Delivered price on the oil bus (€/MWh_th), including CO₂ tax.

    Bare commodity anchor: WB Pink Sheet 12-month trailing mean (CRUDE_BRENT)
    × 1.15 refining margin for heating-oil-grade equivalence. Same default
    methodology as natural_gas_import_price (today's price ≈ 2050 anchor,
    no ramp); the `_fuelRamp` knob applies symmetrically to crude.

    The oil bus exists for future-facing capability — currently no
    ConversionTechnology in CLEVER's stack consumes from it. Phase 3
    keeps CLEVER's "Oil" tech (which is OCGT, methane-fired per EOLES) on
    the natural_gas bus. A true oil-fired tech would consume from this
    bus instead.
    """
    import logging
    logger = logging.getLogger(__name__)

    if _OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH is not None:
        bare_price = _OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH
        ramp_mult = 1.0
        logger.info(
            "oil_import_price: explicit override %.1f €/MWh_th (no ramp applied)",
            bare_price,
        )
    else:
        from clever.data_fetchers import fetch_brent_crude_price_eur_per_mwh_th
        bare_today = fetch_brent_crude_price_eur_per_mwh_th(refining_margin=True)
        ramp_mult = _fuel_ramp_multiplier()
        bare_price = bare_today * ramp_mult
        logger.info(
            "oil_import_price: WB Brent %.1f €/MWh_th × ramp %.3f = %.1f €/MWh_th "
            "(geopolitical-premium ramp @ %.1f %%/yr linear)",
            bare_today, ramp_mult, bare_price, _resolved_fuel_ramp_pct(),
        )

    # CO₂ adder via the dedicated carbon_price module (trajectory-based).
    from clever.carbon_price import resolved_carbon_price
    co2_price = resolved_carbon_price(year=_TARGET_MODEL_YEAR)
    co2_adder = OIL_CO2_INTENSITY_T_PER_MWH_TH * co2_price
    final = bare_price + co2_adder
    logger.info(
        "oil_import_price: + CO2 %.1f (%.3f t/MWh × %.0f €/tCO2) = %.1f €/MWh_th delivered",
        co2_adder, OIL_CO2_INTENSITY_T_PER_MWH_TH, co2_price, final,
    )
    return final

# ═══════════════════════════════════════════════════════════════════
# 4.1 HYDRO VARIABLE COSTS (EUR/MWh)
# ═══════════════════════════════════════════════════════════════════
ROR_HYDRO_VARIABLE_COST: float = 5.0
RESERVOIR_HYDRO_VARIABLE_COST: float = 7.0
PUMPED_HYDRO_ROUNDTRIP_EFF: float = 0.80

# Flat-profile fallback capacity factors for RoR hydro when SupplyForge
# hourly chronologies are unavailable (404 / missing parquet).
# Sources: ENTSO-E Transparency Platform average 2018-2022 capacity factors,
# cross-checked against Eurostat hydro generation / installed capacity ratios.
# A flat profile is a standard adequacy-assessment fallback (see ERAA 2023
# methodology, §4.3.2: "Where hourly profiles are unavailable, flat
# availability factors based on historical averages may be used").
HYDRO_ROR_FLAT_CF: dict[str, float] = {
    "FR": 0.42,   # ~50 TWh / 13.6 GW — Eurostat avg 2018-2022
    "DE": 0.45,   # ~15.5 TWh / 3.9 GW
    "ES": 0.30,   # ~9 TWh / 3.4 GW — lower due to semi-arid hydrology
    "IT": 0.40,   # ~22 TWh / ~6 GW (Terna data)
    "AT": 0.55,   # ~26 TWh / 5.4 GW — Alpine, high yield
    "CH": 0.55,   # ~22 TWh / 4.5 GW — Alpine
    "BE": 0.35,   # low-head RoR, small fleet
    "NL": 0.30,   # marginal RoR, small fleet
    "GB": 0.35,   # ~7.7 TWh / 2.5 GW
    # Extended countries
    "NO": 0.55,   # ~105 TWh / ~33 GW — dominant hydro system
    "SE": 0.50,   # ~65 TWh / ~16 GW
    "FI": 0.40,   # ~13 TWh / ~3 GW
    "PT": 0.35,   # ~7 TWh / ~4 GW
    "PL": 0.35,   # ~2 TWh / small fleet
    "RO": 0.35,   # ~16 TWh / ~6 GW (Carpathian rivers)
    "HR": 0.35,   # ~5 TWh / ~2 GW (Dinaric Alps)
    "SI": 0.40,   # ~4 TWh / ~1 GW (Alpine)
    "BG": 0.30,   # ~3 TWh / ~2 GW
    "GR": 0.25,   # ~5 TWh / ~3 GW — dry Mediterranean
    "SK": 0.40,   # ~4 TWh / ~2 GW (Carpathians)
    "CZ": 0.35,   # ~2 TWh / ~1 GW
    "HU": 0.30,   # marginal (~0.2 TWh)
    "IE": 0.35,   # ~0.9 TWh / ~0.5 GW
    "LT": 0.30,
    "LV": 0.40,   # ~3 TWh / ~1.5 GW (Daugava)
    "EE": 0.25,   # negligible
    "LU": 0.35,   # ~0.1 TWh
    "DK": 0.0,    # no hydro
}
DEFAULT_HYDRO_ROR_FLAT_CF: float = 0.40

# Flat-profile fallback for reservoir hydro — expresses what fraction of
# turbine capacity is available on average across the year.
# Lower than RoR because reservoirs are peaking assets (high capacity,
# constrained yearly energy). Values approximate energy_gwh / (turbine_mw × 8760h).
HYDRO_RESERVOIR_FLAT_CF: dict[str, float] = {
    "FR": 0.12,   # 10,000 GWh / (9,847 MW × 8.76) ≈ 0.116
    "DE": 0.033,  # 237 GWh / (819 MW × 8.76) ≈ 0.033
    "ES": 0.15,   # 14,775 GWh / (11,414 MW × 8.76) ≈ 0.148
    "IT": 0.12,   # estimated — large alpine reservoir fleet
    "AT": 0.25,   # (769 + 5) GWh / (2787 + 1149 MW × 8.76) ≈ low, but seasonal
    "CH": 0.10,   # 8,377 GWh / (9,291 MW × 8.76) ≈ 0.103
    "BE": 0.0,    # no reservoir
    "NL": 0.0,    # no reservoir
    "GB": 0.0,    # no reservoir in PEMMDB
    # Extended countries
    "NO": 0.45,   # massive reservoir fleet (~87 TWh storage, ~33 GW)
    "SE": 0.30,   # large reservoir fleet (~34 TWh)
    "FI": 0.15,   # moderate (~5 GW)
    "PT": 0.15,   # moderate (~7 GW)
    "RO": 0.15,   # ~6 GW reservoirs
    "HR": 0.15,   # ~1.2 GW
    "SI": 0.15,   # ~0.8 GW
    "BG": 0.12,   # ~2 GW
    "GR": 0.12,   # ~3 GW
    "SK": 0.10,   # ~2 GW
    "PL": 0.05,   # small (mostly pumped)
    "CZ": 0.05,   # small
    "HU": 0.0,    # marginal
    "IE": 0.10,   # ~0.3 GW
    "LV": 0.15,   # Daugava reservoirs
    "LT": 0.05,   # Kruonis PHS
    "EE": 0.0,    # negligible
    "LU": 0.10,   # Vianden PHS (counts as closed-loop)
    "DK": 0.0,    # no reservoir
}
DEFAULT_HYDRO_RESERVOIR_FLAT_CF: float = 0.10


# ═══════════════════════════════════════════════════════════════════
# 5. PEMMDB HYDRO 2050 / NationalTrends
#    Capacities in MW, reservoir volumes in GWh.
#
#    NOTE: Italy (IT) has ALL ZEROS in PEMMDB — this is a known data
#    gap in the 2050/NationalTrends dataset.  If Italy hydro is needed,
#    an external data source must be provided.
# ═══════════════════════════════════════════════════════════════════
HYDRO_PEMMDB: dict[str, dict[str, float]] = {
    "AT": {
        "ror_mw": 5350.12576,
        "pondage_gwh": 5.332957,
        "pondage_mw": 1149.307,
        "reservoir_gwh": 769.036568,
        "reservoir_mw": 2787.129,
        "phs_open_gwh": 1962.653,
        "phs_open_turbine_mw": 8082.5,
        "phs_open_pump_mw": 7432.92,
        "phs_closed_gwh": 3.6,
        "phs_closed_turbine_mw": 450.0,
        "phs_closed_pump_mw": 450.0,
    },
    "BE": {
        "ror_mw": 165.002,
        "pondage_gwh": 0.0,
        "pondage_mw": 0.0,
        "reservoir_gwh": 0.0,
        "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0,
        "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 11.6,
        "phs_closed_turbine_mw": 1305.0,
        "phs_closed_pump_mw": 1226.76,
    },
    "CH": {
        "ror_mw": 4484.368266,
        "pondage_gwh": 0.0,
        "pondage_mw": 0.0,
        "reservoir_gwh": 8377.030031,
        "reservoir_mw": 9291.278101,
        "phs_open_gwh": 1442.22504,
        "phs_open_turbine_mw": 4363.549179,
        "phs_open_pump_mw": 3822.792715,
        "phs_closed_gwh": 56.0,
        "phs_closed_turbine_mw": 1900.0,
        "phs_closed_pump_mw": 1900.0,
    },
    "DE": {
        "ror_mw": 3933.9,
        "pondage_gwh": 0.0,
        "pondage_mw": 0.0,
        "reservoir_gwh": 237.2170338,
        "reservoir_mw": 819.0,
        "phs_open_gwh": 438.159,
        "phs_open_turbine_mw": 3194.1,
        "phs_open_pump_mw": 2911.0,
        "phs_closed_gwh": 276.1497772,
        "phs_closed_turbine_mw": 7529.84,
        "phs_closed_pump_mw": 7661.6,
    },
    "ES": {
        "ror_mw": 3424.665616,
        "pondage_gwh": 0.0,
        "pondage_mw": 0.0,
        "reservoir_gwh": 14775.0725,
        "reservoir_mw": 11414.19,
        "phs_open_gwh": 5763.86,
        "phs_open_turbine_mw": 4823.32,
        "phs_open_pump_mw": 4564.005633,
        "phs_closed_gwh": 153.471,
        "phs_closed_turbine_mw": 8908.485333,
        "phs_closed_pump_mw": 8758.59,
    },
    "FR": {
        "ror_mw": 13600.0,
        "pondage_gwh": 0.0,
        "pondage_mw": 0.0,
        "reservoir_gwh": 10000.0,
        "reservoir_mw": 9847.0,
        "phs_open_gwh": 90.0,
        "phs_open_turbine_mw": 2960.906863,
        "phs_open_pump_mw": 2960.906863,
        "phs_closed_gwh": 10.0,
        "phs_closed_turbine_mw": 3120.955882,
        "phs_closed_pump_mw": 3120.955882,
    },
    "IT": {
        # Source: Terna Statistical Data 2022 + IRENA Renewable Capacity Statistics 2023.
        # Italy has ~22 GW total hydro installed capacity (third largest in Europe).
        # Breakdown: ~13 GW run-of-river, ~8.5 GW reservoir (lakes + pondage),
        # ~7.7 GW pumped hydro storage (mostly open-loop with natural inflows).
        # 2050 projection: assumes stable capacity (hydro sites are geographically
        # constrained; NECP 2024 projects minimal expansion).
        # Annual generation: ~49 TWh (Terna 2018-2022 avg), split ~60% RoR / ~40% reservoir.
        "ror_mw": 13_100.0,         # Terna 2022: 13,142 MW RoR installed
        "pondage_gwh": 450.0,       # Small pondage reservoirs (~1.5 GW × ~300h)
        "pondage_mw": 1_500.0,      # Terna: short-term regulation basins
        "reservoir_gwh": 7_500.0,   # Large seasonal reservoirs (Alpine + Apennine)
        "reservoir_mw": 7_000.0,    # Terna 2022: ~7 GW reservoir turbine capacity
        "phs_open_gwh": 2_100.0,    # Open-loop PHS (connected to natural basins)
        "phs_open_turbine_mw": 4_200.0,  # Terna: ~4.2 GW open-loop turbine
        "phs_open_pump_mw": 3_800.0,     # Slightly less pump than turbine capacity
        "phs_closed_gwh": 800.0,    # Closed-loop PHS (artificial upper reservoir)
        "phs_closed_turbine_mw": 3_500.0, # Terna: ~3.5 GW closed-loop
        "phs_closed_pump_mw": 3_500.0,
    },
    "NL": {
        "ror_mw": 37.0,
        "pondage_gwh": 0.0,
        "pondage_mw": 0.0,
        "reservoir_gwh": 0.0,
        "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0,
        "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0,
        "phs_closed_pump_mw": 0.0,
    },
    "GB": {
        "ror_mw": 2513.494062,
        "pondage_gwh": 0.0,
        "pondage_mw": 0.0,
        "reservoir_gwh": 0.0,
        "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0,
        "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 26.38,
        "phs_closed_turbine_mw": 2744.0,
        "phs_closed_pump_mw": 2684.0,
    },
    # ── Extended countries (ENTSO-E PEMMDB 2050 + national TSO data) ──
    "NO": {
        "ror_mw": 15_000.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 87_000.0,  # ~87 TWh — largest in Europe
        "reservoir_mw": 33_000.0,
        "phs_open_gwh": 1_500.0,
        "phs_open_turbine_mw": 1_400.0, "phs_open_pump_mw": 1_200.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "SE": {
        "ror_mw": 6_000.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 34_000.0,  # ~34 TWh
        "reservoir_mw": 16_200.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "FI": {
        "ror_mw": 1_500.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 5_500.0,
        "reservoir_mw": 3_100.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "PT": {
        "ror_mw": 2_600.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 3_500.0,
        "reservoir_mw": 4_300.0,
        "phs_open_gwh": 1_000.0,
        "phs_open_turbine_mw": 3_500.0, "phs_open_pump_mw": 3_200.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "PL": {
        "ror_mw": 400.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 50.0,
        "reservoir_mw": 350.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 10.0,
        "phs_closed_turbine_mw": 1_750.0, "phs_closed_pump_mw": 1_750.0,
    },
    "RO": {
        "ror_mw": 2_000.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 5_000.0,
        "reservoir_mw": 4_500.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "GR": {
        "ror_mw": 500.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 3_000.0,
        "reservoir_mw": 2_500.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 5.0,
        "phs_closed_turbine_mw": 700.0, "phs_closed_pump_mw": 700.0,
    },
    "BG": {
        "ror_mw": 800.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 1_500.0,
        "reservoir_mw": 1_800.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 4.0,
        "phs_closed_turbine_mw": 864.0, "phs_closed_pump_mw": 788.0,
    },
    "HR": {
        "ror_mw": 400.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 1_200.0,
        "reservoir_mw": 1_700.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 3.0,
        "phs_closed_turbine_mw": 276.0, "phs_closed_pump_mw": 276.0,
    },
    "SI": {
        "ror_mw": 800.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 200.0,
        "reservoir_mw": 400.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 2.0,
        "phs_closed_turbine_mw": 180.0, "phs_closed_pump_mw": 180.0,
    },
    "SK": {
        "ror_mw": 1_000.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 600.0,
        "reservoir_mw": 1_200.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 3.0,
        "phs_closed_turbine_mw": 916.0, "phs_closed_pump_mw": 916.0,
    },
    "CZ": {
        "ror_mw": 350.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 200.0,
        "reservoir_mw": 750.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 4.0,
        "phs_closed_turbine_mw": 1_145.0, "phs_closed_pump_mw": 1_145.0,
    },
    "HU": {
        "ror_mw": 57.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 0.0,
        "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "IE": {
        "ror_mw": 216.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 100.0,
        "reservoir_mw": 292.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 2.0,
        "phs_closed_turbine_mw": 292.0, "phs_closed_pump_mw": 292.0,
    },
    "LV": {
        "ror_mw": 1_536.0,   # Daugava cascade
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 200.0,
        "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "LT": {
        "ror_mw": 117.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 0.0,
        "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 8.0,
        "phs_closed_turbine_mw": 900.0, "phs_closed_pump_mw": 900.0,
    },
    "LU": {
        "ror_mw": 34.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 0.0,
        "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 5.0,
        "phs_closed_turbine_mw": 1_296.0, "phs_closed_pump_mw": 1_296.0,
    },
    # Countries with zero/negligible hydro — entry needed to avoid KeyError
    "DK": {
        "ror_mw": 0.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 0.0, "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
    "EE": {
        "ror_mw": 8.0,
        "pondage_gwh": 0.0, "pondage_mw": 0.0,
        "reservoir_gwh": 0.0, "reservoir_mw": 0.0,
        "phs_open_gwh": 0.0,
        "phs_open_turbine_mw": 0.0, "phs_open_pump_mw": 0.0,
        "phs_closed_gwh": 0.0,
        "phs_closed_turbine_mw": 0.0, "phs_closed_pump_mw": 0.0,
    },
}


# ═══════════════════════════════════════════════════════════════════
# 6. SUPPLYFORGE HYDRO INPUT CANDIDATES
# ═══════════════════════════════════════════════════════════════════
# NOTE: RoR profiles now come from "capacity_factors" (plant_type filter),
# matching the official SupplyForge/pommes_craft nomenclature.
# Reservoir inflows come from "inflow" (column "inflow_MW").
# The old candidate lists below are kept only for reference / documentation.
SUPPLYFORGE_HYDRO_INPUT_CANDIDATES: dict[str, list[str]] = {
    "ror": [
        # RoR is now fetched from "capacity_factors" with plant_type
        # "Hydro Run-of-river and poundage" — see model.py add_ror_hydro_from_supplyforge
    ],
    "reservoir_inflow": [
        "inflow",  # correct SupplyForge name (column: inflow_MW)
    ],
}


# ═══════════════════════════════════════════════════════════════════
# 7. BATTERY STORAGE (BESS) SPECIFICATIONS
# ═══════════════════════════════════════════════════════════════════
BESS_SPECS: dict[str, dict] = {
    "Battery_1h": {
        "eoles_tech": "battery_1h",
        "duration_hours": 1.0,
        "roundtrip_efficiency": 0.92,
    },
    "Battery_4h": {
        "eoles_tech": "battery_4h",
        "duration_hours": 4.0,
        "roundtrip_efficiency": 0.88,
    },
}

# Country-specific BESS power investment caps (MW), by battery type.
# Sources:
#   - ENTSO-E TYNDP 2024 Scenarios Report: 540 GW storage across Europe
#     (Germany, Italy, Netherlands identified as top-3 countries)
#   - EASE Energy Storage Targets 2030/2050 Report: ES ~30 GW, FR ~30 GW
#   - Frontier Economics / Fluence (2024): DE ~60 GW total storage by 2050
#   - National NECPs 2024 updates (ES: 22.5 GW by 2030)
#   - Conservative assumption: 4h batteries dominate at high RES penetration
#     (61% of installed BESS by 2050 per BloombergNEF)
# Base caps (~220 GW EU-wide) are BNEF lower-bound for 2050. Phase 2.6
# refactor (2026-05-25) applies a DECARB-aware multiplier:
#   non-DECARB / policy_* scenarios → 1.0× (220 GW EU)
#   DECARB CENTRAL / LOW            → 1.5× (~330 GW EU, mid-BNEF)
#   DECARB HIGH (carries _h2HIGH)   → 2.0× (~440 GW EU, mid-TYNDP)
# Override per-scenario via `_battNNN` suffix (NNN = scale × 10, e.g.
# _batt25 → 2.5×).
#
# Caps are plausible maximum deployment, not targets — the optimiser
# invests less if economically justified.
_BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY_BASE: dict[str, dict[str, float]] = {
    "FR": {"Battery_1h":  8_000.0, "Battery_4h": 20_000.0},
    "DE": {"Battery_1h": 15_000.0, "Battery_4h": 40_000.0},
    "ES": {"Battery_1h": 10_000.0, "Battery_4h": 20_000.0},
    "IT": {"Battery_1h": 10_000.0, "Battery_4h": 25_000.0},
    "GB": {"Battery_1h": 10_000.0, "Battery_4h": 20_000.0},
    "BE": {"Battery_1h":  2_000.0, "Battery_4h":  5_000.0},
    "CH": {"Battery_1h":  1_500.0, "Battery_4h":  4_000.0},
    "AT": {"Battery_1h":  1_500.0, "Battery_4h":  4_000.0},
    "NL": {"Battery_1h":  5_000.0, "Battery_4h": 15_000.0},
}

# Fallback for countries not in the above dict
_DEFAULT_BESS_POWER_INVESTMENT_MAX_MW_BASE: dict[str, float] = {
    "Battery_1h": 5_000.0,
    "Battery_4h": 10_000.0,
}

# ─── DECARB-aware scale (Phase 2.6) ─────────────────────────────────────────
# Tier order checked: explicit _battNNN > h2HIGH (DECARB HIGH) > DECARB > 1.0×.
# Pure regex / substring tests on _SCENARIO so the section is self-contained
# (does not depend on _IS_DECARB_* flags computed later in this file).
_BATTERY_SCALE: float = 1.0
if bool(_re.search(r"_noGas_corr2x", _SCENARIO)):
    # Any DECARB run
    _BATTERY_SCALE = 1.5
if "h2HIGH" in _SCENARIO:
    # DECARB HIGH overrides upward — heat pumps + electrolysers add huge flex load
    _BATTERY_SCALE = 2.0
_m_batt = _re.search(r"_batt(\d+)(?:_|$)", _SCENARIO)
if _m_batt:
    _BATTERY_SCALE = float(_m_batt.group(1)) / 10.0
del _m_batt

BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY: dict[str, dict[str, float]] = {
    country: {tech: cap * _BATTERY_SCALE for tech, cap in techs.items()}
    for country, techs in _BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY_BASE.items()
}
DEFAULT_BESS_POWER_INVESTMENT_MAX_MW: dict[str, float] = {
    tech: cap * _BATTERY_SCALE
    for tech, cap in _DEFAULT_BESS_POWER_INVESTMENT_MAX_MW_BASE.items()
}


# ═══════════════════════════════════════════════════════════════════
# 8. MANUAL INTERCONNECTIONS (NTC, MW)
# ═══════════════════════════════════════════════════════════════════
# Sources: ENTSO-E TYNDP 2024, ERAA 2024 reference grid, and existing
# NTC values from ENTSO-E Transparency Platform (reference NTC 2023-2024).
# Capacities are *symmetric* (same value A→B and B→A) unless noted.
# Only borders between countries that might appear in the CLEVER model
# are listed; add_manual_interconnections() silently skips pairs where
# one or both countries are absent from the model.
#
# Western Europe — original 9-country mesh
# GB interconnections: IFA (2 GW), IFA2 (1 GW), ElecLink (1 GW),
#   Nemo Link (1 GW), BritNed (1 GW) — rounded for 2050 projection.
# AT-DE: merged bidding zone reality (~5 GW effective transfer capacity).
MANUAL_INTERCONNECTIONS: dict[tuple[str, str], dict] = {
    # ── Western & Central Europe (original 9-country mesh) ──────────
    ("FR", "DE"): {"capacity": 4_000.0, "investment_cost": 0.0},
    ("FR", "BE"): {"capacity": 3_500.0, "investment_cost": 0.0},
    ("FR", "ES"): {"capacity": 3_885.0, "investment_cost": 0.0},
    ("FR", "IT"): {"capacity": 4_184.0, "investment_cost": 0.0},
    ("FR", "CH"): {"capacity": 1_800.0, "investment_cost": 0.0},
    ("FR", "GB"): {"capacity": 4_000.0, "investment_cost": 0.0},
    ("BE", "NL"): {"capacity": 1_931.0, "investment_cost": 0.0},
    ("BE", "GB"): {"capacity": 1_000.0, "investment_cost": 0.0},
    ("BE", "DE"): {"capacity": 1_000.0, "investment_cost": 0.0},  # ALEGrO
    ("BE", "LU"): {"capacity":   700.0, "investment_cost": 0.0},
    ("CH", "DE"): {"capacity": 4_000.0, "investment_cost": 0.0},
    ("CH", "IT"): {"capacity": 4_505.0, "investment_cost": 0.0},
    ("AT", "CH"): {"capacity": 1_200.0, "investment_cost": 0.0},
    ("AT", "IT"): {"capacity":   360.0, "investment_cost": 0.0},
    ("AT", "DE"): {"capacity": 5_000.0, "investment_cost": 0.0},
    ("NL", "DE"): {"capacity": 4_450.0, "investment_cost": 0.0},
    ("NL", "GB"): {"capacity": 1_000.0, "investment_cost": 0.0},
    ("NL", "NO"): {"capacity": 1_400.0, "investment_cost": 0.0},  # NorNed
    ("NL", "DK"): {"capacity":   700.0, "investment_cost": 0.0},  # COBRAcable
    ("DE", "LU"): {"capacity":   700.0, "investment_cost": 0.0},
    # ── Scandinavia & Nordics ───────────────────────────────────────
    ("NO", "SE"): {"capacity": 3_500.0, "investment_cost": 0.0},
    ("NO", "DK"): {"capacity": 1_632.0, "investment_cost": 0.0},  # Skagerrak
    ("NO", "GB"): {"capacity": 1_400.0, "investment_cost": 0.0},  # North Sea Link
    ("NO", "DE"): {"capacity": 1_400.0, "investment_cost": 0.0},  # NordLink
    ("SE", "DK"): {"capacity": 2_640.0, "investment_cost": 0.0},  # SE3-DK1 + SE4-DK2
    ("SE", "FI"): {"capacity": 2_200.0, "investment_cost": 0.0},
    ("SE", "PL"): {"capacity":   600.0, "investment_cost": 0.0},  # SwePol
    ("SE", "LT"): {"capacity":   700.0, "investment_cost": 0.0},  # NordBalt
    ("SE", "DE"): {"capacity":   615.0, "investment_cost": 0.0},  # Baltic Cable
    ("DK", "DE"): {"capacity": 4_000.0, "investment_cost": 0.0},  # Kontek + Kriegers Flak
    ("FI", "EE"): {"capacity": 1_000.0, "investment_cost": 0.0},  # EstLink 1+2
    # ── Iberian Peninsula ───────────────────────────────────────────
    ("ES", "PT"): {"capacity": 4_200.0, "investment_cost": 0.0},
    # ── Central Europe ──────────────────────────────────────────────
    ("DE", "PL"): {"capacity": 3_000.0, "investment_cost": 0.0},
    ("DE", "CZ"): {"capacity": 2_600.0, "investment_cost": 0.0},
    ("PL", "CZ"): {"capacity":   900.0, "investment_cost": 0.0},
    ("PL", "SK"): {"capacity": 1_100.0, "investment_cost": 0.0},
    ("PL", "LT"): {"capacity":   500.0, "investment_cost": 0.0},  # LitPol
    ("CZ", "SK"): {"capacity": 2_000.0, "investment_cost": 0.0},
    ("CZ", "AT"): {"capacity":   900.0, "investment_cost": 0.0},
    ("SK", "HU"): {"capacity": 1_200.0, "investment_cost": 0.0},
    ("AT", "HU"): {"capacity": 1_200.0, "investment_cost": 0.0},
    ("AT", "SI"): {"capacity":   950.0, "investment_cost": 0.0},
    ("HU", "RO"): {"capacity": 1_100.0, "investment_cost": 0.0},
    ("HU", "HR"): {"capacity": 2_000.0, "investment_cost": 0.0},
    # ── South-East Europe ───────────────────────────────────────────
    ("IT", "SI"): {"capacity":   600.0, "investment_cost": 0.0},
    ("IT", "GR"): {"capacity":   500.0, "investment_cost": 0.0},  # planned GRITA
    ("IT", "MT"): {"capacity":   200.0, "investment_cost": 0.0},
    ("HR", "SI"): {"capacity": 2_000.0, "investment_cost": 0.0},
    ("HR", "RS"): {"capacity":   600.0, "investment_cost": 0.0},
    ("HR", "BA"): {"capacity":   500.0, "investment_cost": 0.0},
    ("RO", "BG"): {"capacity":   600.0, "investment_cost": 0.0},
    ("RO", "RS"): {"capacity":   600.0, "investment_cost": 0.0},
    ("BG", "GR"): {"capacity":   800.0, "investment_cost": 0.0},
    ("BG", "RS"): {"capacity":   400.0, "investment_cost": 0.0},
    ("GR", "AL"): {"capacity":   250.0, "investment_cost": 0.0},
    ("RS", "BA"): {"capacity":   600.0, "investment_cost": 0.0},
    ("RS", "ME"): {"capacity":   400.0, "investment_cost": 0.0},
    ("RS", "MK"): {"capacity":   300.0, "investment_cost": 0.0},
    ("MK", "GR"): {"capacity":   300.0, "investment_cost": 0.0},
    ("AL", "ME"): {"capacity":   200.0, "investment_cost": 0.0},
    # ── Baltics ─────────────────────────────────────────────────────
    ("EE", "LV"): {"capacity": 1_500.0, "investment_cost": 0.0},
    ("LV", "LT"): {"capacity": 1_500.0, "investment_cost": 0.0},
    # ── Ireland ─────────────────────────────────────────────────────
    ("IE", "GB"): {"capacity": 1_000.0, "investment_cost": 0.0},  # EWIC + Celtic
}


# ═══════════════════════════════════════════════════════════════════
# 9. CAPACITY EXPANSION / ADEQUACY PARAMETERS
# ═══════════════════════════════════════════════════════════════════
EXPANDABLE_MODEL_TECHS: set[str] = {
    "Gas",
    "Hydrogen_power_plant",
}

# Nuclear becomes investable for the nuke variants (policy_nuke, R0_v1_nuke).
# Costs come from EOLES via techno_costs_from_eoles("nuclear", eoles_costs)
# — no hand-written cost dict. Per-country investment caps live in
# EXPANSION_HEADROOM_BY_COUNTRY / DEFAULT_EXPANSION_HEADROOM_MW below.
if _is_nuclear_expandable(_SCENARIO):
    EXPANDABLE_MODEL_TECHS.add("Nuclear")

# Default per-technology headroom (MW) used ONLY when CLEVER declares
# non-zero energy for that technology in a given country.
# If CLEVER says 0 TWh for a fuel in a country, the technology is NOT created
# (see add_dispatchable_from_non_enr).
# Sources: ENTSO-E TYNDP 2024 Distributed Energy / Global Ambition scenarios;
#   Germany H2-ready study (Frontier Economics 2024): ~53 GW total backup.
#   Conservative cap: allow up to 2× the CLEVER-implied capacity.
DEFAULT_EXPANSION_HEADROOM_MW: dict[str, float] = {
    "Gas": 10_000.0,
    "Hydrogen_power_plant": 15_000.0,
    "Nuclear":               2_000.0,  # conservative for non-nuclear states (policy_nuke/R0_v1_nuke)
}

# Country-specific headroom overrides (MW).
# Only relevant when CLEVER declares non-zero energy for the technology.
# Nuclear caps are sourced from announced national plans (EPR2, PEP2040,
# Sizewell C, Dukovany II, Paks II, Tidö, etc.) — see
# tables/<scenario>/nuclear_headroom_by_country.csv for per-country sources.
EXPANSION_HEADROOM_BY_COUNTRY: dict[str, dict[str, float]] = {
    "DE": {"Gas": 10_000.0, "Hydrogen_power_plant": 40_000.0, "Nuclear":      0.0},  # phase-out, hard zero
    "FR": {"Gas": 10_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear": 20_000.0},  # EPR2 programme target
    "ES": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear":  3_000.0},  # life extension
    "GB": {"Gas": 10_000.0, "Hydrogen_power_plant": 15_000.0, "Nuclear": 15_000.0},  # Hinkley + Sizewell C
    "IT": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear":      0.0},  # 1990 referendum, 2011 reaffirmed
    "NL": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear":  5_000.0},  # Coalition Plan 2× units
    "BE": {"Gas":  3_000.0, "Hydrogen_power_plant":  5_000.0, "Nuclear":  2_000.0},  # Doel/Tihange life extension
    "CH": {"Gas":  2_000.0, "Hydrogen_power_plant":  3_000.0},
    "AT": {"Gas":  2_000.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # constitutional ban (1999)
    # ── Hard-zero nuclear (Phase 2.9): countries with current bans or
    # constitutional restrictions. Without explicit "Nuclear": 0.0 these
    # silently fell through to DEFAULT_EXPANSION_HEADROOM_MW["Nuclear"]=2 GW.
    "PT": {"Gas":  1_500.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # no nuclear policy
    "IE": {"Gas":  1_500.0, "Hydrogen_power_plant":  2_000.0, "Nuclear":      0.0},  # Electricity Regulation Act 1999 ban
    "DK": {"Gas":  1_500.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # 1985 parliamentary resolution
    "LU": {"Gas":    500.0, "Hydrogen_power_plant":  1_000.0, "Nuclear":      0.0},  # no nuclear policy
    "GR": {"Gas":  2_500.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # no nuclear policy
    "PL": {"Nuclear": 10_000.0},   # PEP2040 nuclear programme
    "CZ": {"Nuclear":  5_000.0},   # Dukovany II + Temelin uprate
    "FI": {"Nuclear":  3_000.0},   # Olkiluoto + new
    "HU": {"Nuclear":  4_000.0},   # Paks II
    "SK": {"Nuclear":  3_000.0},   # Mochovce + new
    "BG": {"Nuclear":  3_000.0},   # Kozloduy + Belene
    "RO": {"Nuclear":  3_000.0},   # Cernavoda + SMR
    "SE": {"Nuclear":  6_000.0},   # Tidö nuclear ambition
}

# ─── Phase 2.9: parametric per-country nuclear re-entry ─────────────────────
# Format: _ccNuke{NNN}  (cc = ISO2 lowercase, NNN = MW headroom)
# Example: _itNuke10000 → Italy gets 10 GW nuclear investment headroom,
# reflecting the 2024 Italian Ministry of Environment SMR roadmap.
#
# Methods-section disclosure rule: if any of these suffixes are active in a
# published scenario, cite (a) the baseline ban (e.g. 1990 IT, 1999 AT/IE,
# 1985 DK), (b) the specific policy / roadmap motivating the override,
# (c) the chosen NNN value with rationale.
_NUCLEAR_REENTRY_COUNTRIES: tuple[str, ...] = ("it", "at", "pt", "ie", "dk", "lu", "gr")
for _cc in _NUCLEAR_REENTRY_COUNTRIES:
    _m_nuke = _re.search(rf"_{_cc}Nuke(\d+)(?:_|$)", _SCENARIO)
    if _m_nuke:
        EXPANSION_HEADROOM_BY_COUNTRY.setdefault(_cc.upper(), {})["Nuclear"] = float(_m_nuke.group(1))
del _cc  # avoid leaking loop variable into module namespace
try:
    del _m_nuke
except NameError:
    pass

# `_nukeXXL` (2026-06-16): moderate ~2x lift of nuclear headroom. Double every
# NON-zero per-country cap and the default; policy hard-zeros (DE/IT/AT/PT/IE/
# DK/LU/GR) stay 0. Only bites when nuclear is expandable ('_nuke' present).
if _NUKE_XXL:
    DEFAULT_EXPANSION_HEADROOM_MW["Nuclear"] = DEFAULT_EXPANSION_HEADROOM_MW["Nuclear"] * 2.0
    for _cc_n in list(EXPANSION_HEADROOM_BY_COUNTRY):
        _nuc_cap = EXPANSION_HEADROOM_BY_COUNTRY[_cc_n].get("Nuclear")
        if _nuc_cap:  # non-zero only — preserves hard-zero policy bans
            EXPANSION_HEADROOM_BY_COUNTRY[_cc_n]["Nuclear"] = _nuc_cap * 2.0
    del _cc_n, _nuc_cap

# VRE expansion headroom: fraction of CLEVER-installed capacity that can be
# added on top.  E.g. 0.15 → the optimizer may build up to 115 % of the
# CLEVER capacity for each VRE technology (CLEVER level is the floor).
# Set to 0.0 to revert to the previous behaviour (pinned capacity, no CAPEX
# signal in marginal prices).
# Fold annualised CAPEX of CLEVER-fixed VRE into variable_cost so that
# LP duals (electricity prices) reflect full LCOE, not just VOM.
# When True, a preprocessing step computes:
#   capex_adder = annuity_per_MW / (CF × 8760)  [EUR/MWh]
# and adds it to variable_cost before the technology enters the LP.
# Set to False to keep pure short-run marginal cost pricing.
VRE_CAPEX_IN_VARIABLE_COST: bool = True

# Scenario-gated default VRE upper band:
#   R0_v1 / R0_v1_nuke      → 0.15 (CLEVER sufficiency-aligned)
#   policy_re / policy_nuke → 0.30 (non-sufficiency counter-factual; raised from
#                                    +20% on 2026-05-19 — Simon's call, motivated by
#                                    article-readability: +20% gave ~190 GW marginal
#                                    headroom above the demand-implied ~685 GW
#                                    additional VRE need under the central H₂ bundle,
#                                    risking 5+ countries binding on the cap and
#                                    suppressing the "VRE needed without sufficiency"
#                                    headline. +30% removes this risk.)
# Detect DECARB-style scenarios (final-paper runs) by the `_noGas_corr2x` signature.
# These get more flexible VRE bands so the LP can right-size the fleet to demand:
#   downside  = −40 % for LOW (bioLow + no h2HIGH); −25 % for CENTRAL / HIGH
#   upside    = per-tech (25/20/60) for HIGH (_vreEXT); uniform +25 % for LOW & CENTRAL
_IS_DECARB_RUN: bool = bool(_re.search(r"_noGas_corr2x", _SCENARIO))
_IS_DECARB_LOW: bool = _IS_DECARB_RUN and "bioLow" in _SCENARIO and "h2HIGH" not in _SCENARIO

if _VRE_FREE:
    # `_vreFree` (2026-06-26): cap = CLEVER level exactly (no expansion margin).
    # Combined with the downside band = 1.0 below, the LP sites VRE in [0, CLEVER].
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.0,
        "Wind_Onshore":  0.0,
        "Wind_Offshore": 0.0,
    }
elif _VRE_XXL:
    # `_vreXXL` (2026-06-16): ~2x the `_vreEXT` band — moderate headroom lift.
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.50,
        "Wind_Onshore":  0.40,
        "Wind_Offshore": 1.20,
    }
elif _VRE_EXTENDED:
    # `_vreEXT` scenarios: per-tech default reflects JRC-ENSPRESO / national-pledge floors.
    # The big-country overrides below are blanked so they don't bind below these.
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.25,
        "Wind_Onshore":  0.20,
        "Wind_Offshore": 0.60,
    }
elif _IS_DECARB_RUN:
    # DECARB CENTRAL / LOW: uniform +25 % upside (lifted from default +15 %)
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.25,
        "Wind_Onshore":  0.25,
        "Wind_Offshore": 0.25,
    }
else:
    # Backward compat: a single scalar value, replicated per VRE tech so
    # consumers can always use a .get(tech, fallback) pattern.
    _vre_scalar = 0.30 if _is_high_demand(_SCENARIO) else 0.15
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         _vre_scalar,
        "Wind_Onshore":  _vre_scalar,
        "Wind_Offshore": _vre_scalar,
    }

# Country-specific overrides (same semantics: fraction of CLEVER capacity).
# Blanked for DECARB runs (LOW/CENTRAL/HIGH) so the conservative
# DE/FR/ES/IT/GB caps don't bind below the +25 % uniform default.
VRE_EXPANSION_HEADROOM_BY_COUNTRY: dict[str, dict[str, float]] = {} if (_VRE_EXTENDED or _IS_DECARB_RUN or _VRE_FREE) else {
    "DE": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "FR": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "ES": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "IT": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "GB": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
}

# ── VRE downside band (symmetric ±X% around CLEVER, added 2026-05-12) ─
# Fraction by which the LP may DECOMMISSION CLEVER's announced capacity.
# Reframes CLEVER's role from "ambition floor" to "central estimate with
# uncertainty band" — consistent with Girard 2025's "delivered vs announced"
# deployment-gap finding. Set to 0.0 to revert to the legacy floor-only
# behaviour (CLEVER as hard minimum).
#
# Free-decommissioning: no cost penalty for the LP picking below CLEVER —
# the CAPEX-into-variable-cost adder (clever.model._add_vre_tech_to_area) is
# scaled by `capacity_floor / capacity_mw` (Option β), so the variable-cost
# burden only covers the guaranteed floor portion. Anything above the floor
# is paid via conversion_invest_cost × annuity in the normal LP investment
# objective. Anything below the original CLEVER value is implicitly written
# off (CLEVER's value reinterpreted as upper-bound ambition rather than
# committed deployment).
# DECARB-aware downside band:
#   LOW (bioLow + no h2HIGH) gets −50 % so the LP can right-size to the lower
#   sufficiency demand without paying for CLEVER's "over-supplied" VRE floor.
#   CENTRAL / HIGH get −30 % (was −25 %; loosened 2026-05-25 because the LP was
#   binding exactly at the −25 % floor in every big country, indicating the
#   true cost-optimal sits below).
#   Pre-DECARB scenarios keep the legacy −15 %.
if _VRE_FREE:
    # `_vreFree` (2026-06-26): floor -> 0 (no CLEVER minimum; LP may build nothing).
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 1.0
elif _IS_DECARB_LOW:
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 0.50
elif _IS_DECARB_RUN:
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 0.30
else:
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 0.15

# Country-specific overrides for the downside band. Same dict shape as
# VRE_EXPANSION_HEADROOM_BY_COUNTRY. Empty by default — all countries get
# DEFAULT_VRE_DOWNSIDE_BAND_FRACTION unless overridden here. If you want
# CLEVER's value to be a hard floor for a particular country/tech (e.g.,
# strongly committed national plan), set 0.0.
VRE_DOWNSIDE_BAND_BY_COUNTRY: dict[str, dict[str, float]] = {
    # Example: lock FR Wind_Offshore as a hard floor (no downside relief)
    # "FR": {"Wind_Offshore": 0.0},
}

DEFAULT_LOAD_SHEDDING_COST: float = 30_000.0
DEFAULT_WATER_SPILLAGE_MAX: float = 10_000.0

# Conservative VRE capacity credits for ex-ante adequacy diagnostics.
# These are used ONLY for the pre-solve adequacy screening (compute_country_adequacy_metrics),
# NOT in the optimisation itself. Low values ensure the screening does not overestimate
# firm capacity from variable renewables.
# Sources: ENTSO-E ERAA 2023 capacity de-rating factors (Table 4.2).
# Note: single-year analysis (reference_year=2021 or 2024) means these credits
# reflect one weather year only; multi-year ensemble analysis would be needed
# for robust capacity adequacy conclusions.
VRE_CAPACITY_CREDIT: dict[str, float] = {
    "Solar": 0.00,
    "Wind_Onshore": 0.10,
    "Wind_Offshore": 0.15,
}


# ═══════════════════════════════════════════════════════════════════
# 10. NUMERICAL GUARDS
# ═══════════════════════════════════════════════════════════════════
EPS_MW: float = 1e-3
EPS_MWH: float = 1.0

# Default seasonal shapes when SupplyForge data is unavailable
DEFAULT_ROR_MONTHLY: list[float] = [
    1.10, 1.15, 1.20, 1.15, 1.05, 0.95, 0.85, 0.80, 0.85, 0.95, 1.00, 1.05,
]
DEFAULT_RESERVOIR_INFLOW_MONTHLY: list[float] = [
    1.15, 1.20, 1.20, 1.10, 1.00, 0.90, 0.80, 0.75, 0.80, 0.90, 1.00, 1.10,
]


# ═══════════════════════════════════════════════════════════════════
# 11. ASSUMED FULL-LOAD HOURS (single consolidated definition)
#
#     Used both for deriving MW capacity from CLEVER TWh production
#     data and for ex-ante adequacy diagnostics.  Previously duplicated
#     in add_dispatchable_from_non_enr() and _compute_country_adequacy_metrics().
# ═══════════════════════════════════════════════════════════════════
ASSUMED_FLH: dict[str, float] = {
    "nuclear": 7000.0,
    "coal": 5000.0,
    "ch4_ccgt": 3500.0,
    "ch4_ocgt": 1000.0,
    "waste": 7000.0,
    "biomass_coge": 5000.0,
    "h2_ccgt": 2000.0,
}

# Default FLH for technologies not explicitly listed above
DEFAULT_FLH: float = 4000.0

# ═══════════════════════════════════════════════════════════════════
# 12. EOLES CAPEX
# ═══════════════════════════════════════════════════════════════════

_EOLES_CAPEX_2026 = {
    "offshore_float": 6035, "offshore_ground": 3970, "onshore": 1673,
    "pv_ground_S": 808, "pv_roof_com_S": 941, "pv_roof_indiv_S": 1810,
    "pv_ground_EW": 808, "pv_roof_com_EW": 941, "pv_roof_indiv_EW": 1810,
    "river": 1400, "lake": 2500, "nuclear": 8250,
    "ch4_ocgt": 814, "ch4_ccgt": 1015, "phs": 2500,
    "electrolysis": 633.75, "h2_saltcavern": 0, "h2_ccgt": 1083,
    "methanization": 2875, "battery_1h": 0, "battery_4h": 0,
    "ch4_reservoir": 0, "methanation": 1609.5, "pyrogazification": 3468.75,
    "natural_gas": 0, "coal": 0, "marine": 2375,
    "waste": 0, "biomass_coge": 3900, "geothermal_coge": 7537.5,
    "ocgt_coge": 1212.5, "str_dummy": 10000, "rsv_dummy": 10000,
}

_EOLES_FOM_2026 = {
    "offshore_float": 118, "offshore_ground": 94, "onshore": 40,
    "pv_ground_S": 21, "pv_roof_com_S": 18, "pv_roof_indiv_S": 59,
    "pv_ground_EW": 21, "pv_roof_com_EW": 18, "pv_roof_indiv_EW": 59,
    "river": 53, "lake": 74, "nuclear": 103,
    "ch4_ocgt": 23, "ch4_ccgt": 47, "phs": 74,
    "electrolysis": 15, "h2_saltcavern": 2.5, "h2_ccgt": 50,
    "methanization": 117.875, "battery_1h": 14, "battery_4h": 24,
    "ch4_reservoir": 0, "methanation": 97.125, "pyrogazification": 312.1875,
    "natural_gas": 0, "coal": 0, "marine": 343.75,
    "waste": 0, "biomass_coge": 78, "geothermal_coge": 150.75,
    "ocgt_coge": 47.2875, "str_dummy": 1250, "rsv_dummy": 1250,
}

_EOLES_VOM_2026 = {
    "offshore_float": 10, "offshore_ground": 15, "onshore": 12,
    "pv_ground_S": 2, "pv_roof_com_S": 4, "pv_roof_indiv_S": 3,
    "pv_ground_EW": 2, "pv_roof_com_EW": 4, "pv_roof_indiv_EW": 3,
    "river": 6, "lake": 0, "nuclear": 0.0083,
    "ch4_ocgt": 7, "ch4_ccgt": 6, "phs": 0,
    "battery_1h": 0, "battery_4h": 0, "methanization": 0.003875,
    "electrolysis": 0, "h2_ccgt": 7, "h2_saltcavern": 0.00625,
    "ch4_reservoir": 0.0025, "methanation": 0, "pyrogazification": 0.0444,
    "natural_gas": 0, "coal": 0, "marine": 0,
    "waste": 0, "biomass_coge": 0.004125, "geothermal_coge": 0,
    "ocgt_coge": 0.005, "str_dummy": 1.25, "rsv_dummy": 1.25,
}

_EOLES_DISCOUNT_RATE_UNIFORM = {
    "offshore_float": 0.04, "offshore_ground": 0.04, "onshore": 0.04,
    "pv_ground_S": 0.04, "pv_roof_com_S": 0.04, "pv_roof_indiv_S": 0.04,
    "pv_ground_EW": 0.04, "pv_roof_com_EW": 0.04, "pv_roof_indiv_EW": 0.04,
    "river": 0.04, "lake": 0.04, "nuclear": 0.04,
    "ch4_ocgt": 0.04, "ch4_ccgt": 0.04, "phs": 0.04,
    "electrolysis": 0.04, "h2_saltcavern": 0.04, "h2_ccgt": 0.04,
    "methanization": 0.04, "battery_1h": 0.04, "battery_4h": 0.04,
    "ch4_reservoir": 0.04, "methanation": 0.04, "pyrogazification": 0.04,
    "natural_gas": 0.04, "coal": 0.04, "marine": 0.04,
    "waste": 0.04, "biomass_coge": 0.04, "geothermal_coge": 0.04,
    "ocgt_coge": 0.04, "str_dummy": 0.04, "rsv_dummy": 0.04,
}

_EOLES_STORAGE_CAPEX_2026 = {
    "phs": 85, "battery_1h": 297, "battery_4h": 224,
    "h2_saltcavern": 0.4375, "ch4_reservoir": 0, "str_dummy": 1000,
}


# ═══════════════════════════════════════════════════════════════════
# DemandForge conventions
# ═══════════════════════════════════════════════════════════════════
HOURS_PER_YEAR: float = 8760.0
TWH_TO_MWH: float = 1e6

# Map CLEVER/other area codes to TYNDP/DemandForge country codes
AREA_TO_TYNDP: dict[str, str] = {
    "EL": "GR",
    "UK": "GB",
}

# Keep-set of modelled countries.
# Full CLEVER scope: EU-27 + GB + NO + CH + Western Balkans.
# Countries without a matching sheet in Data_CLEVER.xlsx will be
# skipped gracefully at model-build time (see model.py).
DEFAULT_KEEP_AREAS: set[str] = {
    # Original 9
    "FR", "DE", "ES", "IT", "GB", "BE", "CH", "AT", "NL",
    # Nordics
    "DK", "SE", "NO", "FI",
    # Iberian
    "PT",
    # Central Europe
    "PL", "CZ", "SK", "HU", "SI", "HR", "LU",
    # South-East Europe
    "RO", "BG", "GR",
    # Baltics
    "EE", "LV", "LT",
    # Ireland
    "IE",
    # Other EU (may have limited data — model will skip if absent)
    "CY", "MT",
    # Western Balkans (if CLEVER includes them)
    "RS", "BA", "ME", "MK", "AL",
}

# Default weather reference year for SupplyForge profiles
DEFAULT_WEATHER_REF_YEAR: int = 2021

# Solver defaults
PRICE_NUMERICAL_TOL: float = 1e-9
