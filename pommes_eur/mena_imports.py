"""MENA → EU hydrogen imports (Phase 2.5 refactor).

Two variants behind separate scenario suffixes; both default OFF.

Variant A — exogenous delivered cost
====================================
Single aggregate MENA supply expressed as a NetImport on the EU side (ES + IT
entry-points, 50/50 split). Use cases:

  * Cost-side sensitivity sweeps ("if MENA delivers H₂ at X €/MWh, what
    does EU do?") defensible against IRENA literature.
  * Quick robustness checks where MENA-side detail is collapsed.

Activated by `_menaH2NNN` (annual cap in TWh) + optional `_menaH2costNNN`
(delivered €/MWh_H₂; default 75).

Variant B — per-country optimised MENA
======================================
Four MENA countries (MA + DZ + TN + LY) added as separate POMMES Areas, each
with Solar + Wind_Onshore + Electrolyser + H₂ storage and a per-route H₂
pipeline to ES or IT. The LP optimises MENA-side build vs intra-EU H₂ supply.

  * Captures resource heterogeneity (MA Atlas wind ~38% CF vs LY Sahara solar ~30% CF).
  * Captures per-route economics (MA→ES Gibraltar cheapest ~0.9 M€/MW;
    LY→IT longest with political-risk premium ~1.7 M€/MW).

Activated by `_menaOptim` (all four) or subset suffixes
`_menaOptimMA`, `_menaOptimMA_DZ`, `_menaOptimMA_DZ_TN`.

Egypt deferred — see TODO(EG-NH3). Ammonia deferred — see TODO(ammonia).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pommes_craft import Area, EnergyModel  # noqa: F401

logger = logging.getLogger(__name__)


# ─── Variant A: per-entry-point cap share + delivered cost ──────────────────
# H2Med (Pyrenees corridor to ES) and SoutH2 (TN/LY→IT) are the two credible
# physical landing points by 2050. Extensible to {"GR": ...} once an ammonia
# route is wired in.
#
# Default cap shares: 50/50 (route-capacity-symmetric — ES has 22 GW pipeline
# headroom, IT has 22 GW). Use `_menaH2shareES_NN`/`_menaH2shareIT_NN` to skew.
#
# Default delivered cost: UPPER-BAND, differentiated by entry point.
# Rationale (2026-05-28): Variant A is the *headline* result; Variant B is a
# sanity check. The headline must price MENA H₂ conservatively because:
#   1. IRENA 2023 central LCOH (~€75/MWh) assumes 4-7 % WACC; real MENA WACC
#      is 8-12 % for sovereign-risk-exposed projects (S&P MA BBB-, DZ BB-,
#      TN B+, LY unrated). Adds €10-15/MWh.
#   2. Pipeline CAPEX overruns +25-40 % typical for large submarine projects
#      (Nord Stream, Galsi precedent). Adds €5-10/MWh.
#   3. Climate-related water stress + carbon tariffs on host energy.
#      Adds €5/MWh.
#   4. Entry-point differentiation: ES draws from MA (Gibraltar, BBB-) and
#      DZ — short, lower-risk; IT draws from DZ + TN + LY (longer routes,
#      LY 3 pp risk premium + 520 km submarine).
#
# Override hierarchy (most specific wins):
#   1. Per-entry: `_menaH2costES_NNN`, `_menaH2costIT_NNN`
#   2. Flat: `_menaH2costNNN` — overrides BOTH entry points uniformly
#   3. Defaults below
MENA_H2_ENTRY_POINTS: dict[str, dict[str, float]] = {
    "ES": {
        "share":                       0.5,
        "delivered_cost_eur_per_mwh":  100.0,   # MA + DZ via Pyrenees corridor
    },
    "IT": {
        "share":                       0.5,
        "delivered_cost_eur_per_mwh":  115.0,   # DZ + TN + LY via SoutH2
    },
}

# Back-compat alias: legacy code may still reference MENA_H2_ENTRY_SHARES.
MENA_H2_ENTRY_SHARES: dict[str, float] = {
    cc: cfg["share"] for cc, cfg in MENA_H2_ENTRY_POINTS.items()
}


# ─── Variant B: per-country supply node config ──────────────────────────────
# Each MENA country becomes a POMMES Area. CAPEX, finance rate uplift, and
# resource ref-points all per-country; sensible upper bounds prevent the LP
# from over-investing into unphysical scale.
# Capacity caps (2026-05-27 redesign): tightened to literature-supported
# plausible 2050 export-oriented ambitions. Previous values (250-400 GW solar,
# 100-150 GW electrolyser per country) implied 400-800 TWh_H₂/yr per country,
# 5-10× above the credible IEA / IRENA / national-strategy envelopes. The new
# defaults assume a 60% electrolyser CF and target ~70-150 TWh_H₂/yr export
# capacity per country (consistent with IEA "Africa's Hydrogen Future" 2024,
# IRENA "Green Hydrogen Cost Reduction" 2023, the Morocco H₂ Roadmap 2023,
# and the Algeria National Hydrogen Strategy 2023). Use the _menaCap{NN}
# scenario suffix to stress-test alternative caps.
#
# `domestic_h2_demand_twh` (added 2026-05-27): the share of national H₂
# production reserved for domestic use (ammonia, refining, steel, transport)
# before any export, in TWh_H₂/yr. Defaults are deliberately conservative
# (MENA scenarios are designed as export hubs); the _menaPref{NN} suffix
# scales these globally to test less-export-oriented futures.
MENA_COUNTRY_CONFIG: dict[str, dict[str, Any]] = {
    "MA": {
        "name":                  "Morocco",
        "solar_max_mw":           60_000.0,   # Saharan southern provinces
        "wind_max_mw":            20_000.0,   # Tarfaya + Atlantic coast (excellent)
        "electrolyser_max_mw":    20_000.0,   # ~100 TWh/yr @ 60% CF
        "h2_storage_max_mwh":  5_000_000.0,   # 5 TWh_H2 (limited salt-cavern geology)
        "h2_storage_max_mw":       5_000.0,
        "domestic_h2_demand_twh":      5.0,   # MA H₂ Roadmap industrial-use floor
        "solar_ref_point":      (28.4, -11.1),    # Tan-Tan
        "wind_ref_point":       (27.9, -12.9),    # Tarfaya
        "risk_premium_pct":          0.5,         # low risk
    },
    "DZ": {
        "name":                  "Algeria",
        "solar_max_mw":           80_000.0,   # vast Saharan area
        "wind_max_mw":            15_000.0,
        "electrolyser_max_mw":    30_000.0,   # ~158 TWh/yr @ 60% CF — Algeria H₂ Strategy 2050 ambition
        "h2_storage_max_mwh": 10_000_000.0,   # excellent salt-cavern potential
        "h2_storage_max_mw":       8_000.0,
        "domestic_h2_demand_twh":      7.0,   # ammonia refurb + Sonatrach refining
        "solar_ref_point":      (27.2,   2.5),    # In Salah
        "wind_ref_point":       (35.7,   0.1),    # Oran coast
        "risk_premium_pct":          1.0,
    },
    "TN": {
        "name":                  "Tunisia",
        "solar_max_mw":           15_000.0,
        "wind_max_mw":             6_000.0,
        "electrolyser_max_mw":     8_000.0,   # ~42 TWh/yr
        "h2_storage_max_mwh":  1_500_000.0,
        "h2_storage_max_mw":       2_000.0,
        "domestic_h2_demand_twh":      2.0,   # modest industrial base
        "solar_ref_point":      (34.7,   8.8),    # Gafsa
        "wind_ref_point":       (37.3,  10.0),    # Cap Bon
        "risk_premium_pct":          0.5,
    },
    "LY": {
        "name":                  "Libya",
        "solar_max_mw":           40_000.0,   # large Saharan area, but high risk
        "wind_max_mw":             4_000.0,   # poor wind
        "electrolyser_max_mw":    15_000.0,   # ~79 TWh/yr — heavily risk-discounted
        "h2_storage_max_mwh":  3_000_000.0,
        "h2_storage_max_mw":       3_000.0,
        "domestic_h2_demand_twh":      3.0,
        "solar_ref_point":      (27.0,  14.4),    # Sebha
        "wind_ref_point":       (32.9,  21.9),    # Benghazi
        "risk_premium_pct":          3.0,         # significant political risk
    },
    # TODO(EG-NH3): Egypt — ~1500 km GR pipeline route impractical for H₂ by
    # 2050 without an NH₃-cracking endpoint. Add when ammonia route lands.
}


# ─── MENA blue H₂ — natural-gas wholesale prices per country ────────────────
# Country-specific wholesale gas price (pre-CO₂-adder), €/MWh_th, anchored to
# IEA WEO 2024 + national balance sheets. EU CBAM is applied at the MENA NG
# bus on top (added in mena_natural_gas_import_price): this assumes the 2050
# regulatory horizon fully extends CBAM to embedded carbon in H₂ imports.
#
# Pre-CO₂ wholesale:
#   - DZ: world's #6 gas exporter (~3.4 tcm proved). Domestic wholesale anchored
#     to Sonatrach's stated export-equivalent ~$3/MMBtu floor (€10/MWh_th).
#   - LY: world's #10 gas reserves but limited 2050 ramp due to political risk.
#     Modest premium over DZ (€12/MWh_th); the rest of the risk lives in the
#     finance-rate uplift, not the fuel cost.
#   - MA: net gas importer (Maghreb-Europe pipeline cut 2021); pays LNG-spot-like
#     price by 2050 (€30/MWh_th, similar to EU WB-Pink-Sheet anchor).
#   - TN: small producer (Miskar), increasingly importer. Pays close to LNG (€28).
#
# Override via _menaNGcost{CC}_NNN suffix (parsed in clever.constants).
MENA_NG_WHOLESALE_EUR_PER_MWH_TH: dict[str, float] = {
    "MA": 30.0,
    "DZ": 10.0,
    "TN": 28.0,
    "LY": 12.0,
}


def mena_natural_gas_import_price(country: str) -> float:
    """Return delivered NG price at a MENA NetImport in €/MWh_th.

    Formula: wholesale_anchor + EU CO₂ adder (CBAM-equivalent).
    The CO₂ adder mirrors clever.constants.natural_gas_import_price() so the
    same trajectory (_co2NNN, _co2trajNAME) flows through both EU and MENA buses.

    Override the bare wholesale via _menaNGcost{CC}_NNN scenario suffix
    (parsed in clever.constants).
    """
    from pommes_eur.constants import (
        NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH,
        _TARGET_MODEL_YEAR,
        _MENA_NG_WHOLESALE_OVERRIDES,
    )
    from pommes_eur.carbon_price import resolved_carbon_price

    bare = _MENA_NG_WHOLESALE_OVERRIDES.get(
        country,
        MENA_NG_WHOLESALE_EUR_PER_MWH_TH.get(country, 30.0),
    )
    co2 = NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH * resolved_carbon_price(year=_TARGET_MODEL_YEAR)
    final = bare + co2
    logger.info(
        "MENA-%s NG bus: bare=%.1f + CO2=%.1f = %.1f €/MWh_th (CBAM-equivalent)",
        country, bare, co2, final,
    )
    return final


# ─── MENA blue H₂ — ATR/SMR-CCS reference parameters ────────────────────────
# CAPEX matches the EU side (large-scale modular SMR/ATR is internationally-traded
# equipment) — see clever.methane_h2_ccs for the EU values. The CCS variable_cost
# adjustment uses the SAME formula as the EU CCS techs, so captured CO₂ is valued
# at the resolved carbon_price (the 90% rebate offsets the EU CBAM at the bus,
# producing a small net cost on the 10% uncaptured fraction — the right
# "blue H₂ pays for what it doesn't capture" signal).
_MENA_BLUE_H2_CAP_MW_PER_COUNTRY: float = 2_400.0    # per-country PER-TECH (SMR_CCS & ATR_CCS each); 4 countries × 2 techs × 2.4 GW ≈ 19 GW blue ≈ 170 TWh/yr at baseload — realistic blue-export ceiling (2026-06-23; was 30_000 "plenty of headroom" → drove a 1343 TWh blue flood)

# Single-mode fossil CCGT at MENA areas (same NG bus as blue H₂). Lets the LP
# meet local electricity demand from gas when RE + storage + H₂-to-power aren't
# enough. The CO₂ adder at the NG bus makes this self-suppressing at high
# carbon prices — works just like EU flex Gas, minus the bio_mode (no
# biomethane infrastructure modeled at MENA).
MENA_CCGT_EFF:                  float = 0.58
MENA_CCGT_CAPEX_EUR_PER_KW:     float = 800.0
MENA_CCGT_FIXED_COST_EUR_PER_MW_YR: float = 20_000.0
MENA_CCGT_VOM_EUR_PER_MWH:      float = 5.0
MENA_CCGT_LIFE_YR:              int   = 25
MENA_CCGT_CAP_MW_PER_COUNTRY:   float = 30_000.0


# ─── MENA gas-free firming kit (2026-06-20) ─────────────────────────────────
# The EU areas get H₂-CCGT (Hydrogen_power_plant) + batteries as gas-free
# firming; MENA previously had ONLY the gas-CCGT above, which structurally
# FORCED fossil gas to serve its baseload-shaped night demand — no battery to
# shift solar forward, no way to burn its own H₂ back to electricity. That
# asymmetry (Europe handed the tools to firm without gas, MENA not) was the
# real reason MENA "needed" gas. We give MENA the same toolkit so its gas use
# becomes an economic outcome, not a modelling artifact.
#
# H₂-to-power: SAME turbine class as the MENA gas-CCGT (a CCGT combusts H₂ or
# CH₄ at comparable efficiency), so η = MENA_CCGT_EFF and the LP picks gas vs
# H₂ firming purely on fuel cost + carbon. It draws hydrogen from the local bus
# (factor), so the H₂ fuel cost IS the bus price — no separate fuel adder.
MENA_H2CCGT_EFF:                      float = MENA_CCGT_EFF   # 0.58, same turbine
MENA_H2CCGT_CAPEX_EUR_PER_KW:         float = 900.0           # gas-CCGT 800 + H₂-readiness premium
MENA_H2CCGT_FIXED_COST_EUR_PER_MW_YR: float = 20_000.0
MENA_H2CCGT_VOM_EUR_PER_MWH:          float = 5.0
MENA_H2CCGT_LIFE_YR:                  int   = 25
MENA_H2CCGT_CAP_MW_PER_COUNTRY:       float = 30_000.0        # = gas-CCGT cap

# Batteries (diurnal solar shifting). Cost basis = EU EOLES battery_1h/battery_4h
# (globally-traded equipment) via storage_costs_from_eoles, but with MENA's
# risk-adjusted finance_rate. η_rt + duration come from clever.constants.BESS_SPECS
# (0.92 @ 1h / 0.88 @ 4h). Per-country power caps (MW) — generous "option" levels;
# the LP invests only what is economic.
MENA_BESS_POWER_CAP_MW_PER_COUNTRY: dict[str, float] = {
    "Battery_1h": 10_000.0,
    "Battery_4h": 20_000.0,
}


# ─── MENA exogenous demand — annual TWh per (country, bundle) ────────────────
# Sources: IEA WEO 2024 (Africa Outlook), IRENA WETO 2024, country strategies:
#   MA: PERG/PNE 2030 + Morocco H₂ Roadmap 2023
#   DZ: Sonelgaz long-term plan + Algeria H₂ Strategy 2023
#   TN: Tunisia 2050 Vision (Plan Solaire)
#   LY: post-conflict baseline IRENA 2024 (lower bound)
#
# Bundles match DemandForge's three-tier scheme (low / central / high_h2 ≡ EU
# scenario bundles). The LP selects the bundle via clever.constants.DEMANDFORGE_BUNDLE
# (which is set from the scenario suffix in run_adequacy.py).
#
# Defensibility caveat: these are anchor estimates, not stochastic ranges. For a
# headline scenario these are the assumed 2050 demands; sensitivity could be
# explored via a future _menaDemand{CC}_NNN suffix.
MENA_ELECTRICITY_DEMAND_TWH_PER_YR: dict[str, dict[str, float]] = {
    "MA": {"low_h2":  70.0, "central": 100.0, "high_h2": 150.0},
    "DZ": {"low_h2": 130.0, "central": 180.0, "high_h2": 260.0},
    "TN": {"low_h2":  25.0, "central":  35.0, "high_h2":  55.0},
    "LY": {"low_h2":  25.0, "central":  35.0, "high_h2":  50.0},
}

MENA_H2_DEMAND_TWH_PER_YR: dict[str, dict[str, float]] = {
    "MA": {"low_h2":  5.0, "central": 15.0, "high_h2": 30.0},
    "DZ": {"low_h2":  7.0, "central": 25.0, "high_h2": 50.0},
    "TN": {"low_h2":  2.0, "central":  5.0, "high_h2": 10.0},
    "LY": {"low_h2":  3.0, "central": 10.0, "high_h2": 15.0},
}


def mena_local_demand_twh(country: str, resource: str, bundle: str) -> float:
    """Return per-country annual demand (TWh/yr) for the selected bundle.

    Parameters
    ----------
    country : str    ISO2 MENA code (MA / DZ / TN / LY).
    resource : str   "electricity" or "hydrogen".
    bundle : str     "low_h2" / "central" / "high_h2" (matches DEMANDFORGE_BUNDLE).

    Falls back to the "low_h2" value if the bundle isn't recognised, with a
    loud warning.
    """
    table = (MENA_ELECTRICITY_DEMAND_TWH_PER_YR if resource == "electricity"
             else MENA_H2_DEMAND_TWH_PER_YR if resource == "hydrogen"
             else None)
    if table is None:
        raise ValueError(f"Unknown resource {resource!r}; expected electricity or hydrogen")
    country_row = table.get(country, {})
    val = country_row.get(bundle)
    if val is None:
        fallback = country_row.get("low_h2", 0.0)
        logger.warning(
            "MENA-%s %s demand: bundle %r not in anchor table — falling back to low_h2=%s TWh",
            country, resource, bundle, fallback,
        )
        return float(fallback)
    return float(val)


# ─── Variant B: base CAPEX (pre-finance-rate-uplift) ─────────────────────────
# Anchored to IRENA Renewable Power Generation Costs 2023 + Wood Mackenzie 2024.
# Per-country risk is applied through finance_rate uplift via mena_finance_rate.
MENA_SOLAR_CAPEX_EUR_PER_KW:        float = 300.0     # vs EU ~700
MENA_WIND_ONSHORE_CAPEX_EUR_PER_KW: float = 900.0     # vs EU ~1100
MENA_ELECTROLYSER_CAPEX_EUR_PER_KW: float = 400.0     # vs EU 500-900
MENA_H2_STORAGE_CAPEX_EUR_PER_MWH:  float = 5.0       # salt cavern
MENA_H2_STORAGE_CAPEX_EUR_PER_MW:   float = 80_000.0  # power side (compression)
MENA_VOM_EUR_PER_MWH:               float = 1.5

MENA_SOLAR_FIXED_COST_EUR_PER_MW_YR:    float = 8_000.0    # 1% CAPEX/yr ish
MENA_WIND_FIXED_COST_EUR_PER_MW_YR:     float = 28_000.0
MENA_ELECTROLYSER_FIXED_COST_EUR_PER_MW_YR: float = 12_000.0
MENA_H2_STORAGE_FIXED_COST_EUR_PER_MW_YR:   float = 1_000.0

MENA_SOLAR_LIFE_YR:        int = 30
MENA_WIND_LIFE_YR:         int = 25
MENA_ELECTROLYSER_LIFE_YR: int = 20
MENA_H2_STORAGE_LIFE_YR:   int = 40

# Desalination cost adder for MENA electrolysis (all four are mostly arid
# with coastal/inland access). 9 L water per kg H₂; desal ~1-2 €/m3 →
# ~3 €/MWh_H₂. Inland sites would pay slightly more; this is a documented
# simplification (handoff §7.2.5(b)).
MENA_DESALINATION_COST_EUR_PER_MWH_H2: float = 3.0

# Electrolyser efficiency: 1.85 MWh_e per MWh_H₂ (matches supplyforge default).
MENA_ELECTROLYSER_FACTOR_E_PER_H2: float = 1.85

# Base finance rate (EU benchmark, applied to MENA before risk-premium uplift)
MENA_BASE_FINANCE_RATE: float = 0.04


def mena_finance_rate(country: str) -> float:
    """Return the all-in finance rate for a MENA country.

    Base 4% (EU benchmark) + sovereign-risk premium from MENA_COUNTRY_CONFIG,
    further overridable per-scenario via `_menaRisk{CC}_{NNN}` suffix.
    """
    # Avoid circular import on module load — read overrides lazily.
    from pommes_eur.constants import _MENA_RISK_PER_COUNTRY_OVERRIDES

    cfg = MENA_COUNTRY_CONFIG.get(country)
    if cfg is None:
        return MENA_BASE_FINANCE_RATE
    premium_pp = _MENA_RISK_PER_COUNTRY_OVERRIDES.get(
        country, cfg["risk_premium_pct"]
    )
    return MENA_BASE_FINANCE_RATE + premium_pp / 100.0


# ─── Variant B: per-route H₂ pipeline economics ─────────────────────────────
# Anchored to H2Med (ES-FR submarine, €1.25 M/MW), SoutH2 Corridor (€1.7 M/MW
# averaged route), and IEA Global H₂ Review 2024 ("€1.0-1.5 M/MW typical").
# Per-route differentiation reflects submarine length × depth × overland geology.
# All entries are physical-route only — pipelines are unidirectional (MENA → EU).
MENA_H2_ROUTES: list[tuple[str, str]] = [
    ("MA", "ES"),  # Gibraltar (~15 km submarine, GME refurb candidate)
    ("DZ", "ES"),  # Medgaz analog (~200 km submarine, deep)
    ("DZ", "IT"),  # Galsi route (via Sardinia, ~600 km submarine)
    ("TN", "IT"),  # ELMED scale (~150 km Sicily Channel)
    ("LY", "IT"),  # GreenStream route (~520 km submarine + risk premium)
]

# Per-route base CAPEX (€/MW). Overridable per-route via
# _menaInfra{SRC}_{DST}_NNN or globally via _menaInfraNNN.
MENA_H2_PIPELINE_CAPEX_EUR_PER_MW: dict[tuple[str, str], float] = {
    ("MA", "ES"):  900_000.0,
    ("DZ", "ES"): 1_300_000.0,
    ("DZ", "IT"): 1_600_000.0,
    ("TN", "IT"): 1_200_000.0,
    ("LY", "IT"): 1_700_000.0,
}

# Compressor + pumping cost on the pipeline (€/MWh_H₂ transported)
MENA_H2_PIPELINE_HURDLE_EUR_PER_MWH: dict[tuple[str, str], float] = {
    ("MA", "ES"): 2.0,    # shortest, fewest compressor stations
    ("DZ", "ES"): 3.5,
    ("DZ", "IT"): 5.5,    # longest submarine compression
    ("TN", "IT"): 3.5,
    ("LY", "IT"): 5.0,
}

# Per-route capacity ceiling (MW). Reflects permitting / political constraint
# for high-risk corridors (e.g. LY tighter cap).
MENA_H2_PIPELINE_CAPACITY_INVESTMENT_MAX_MW: dict[tuple[str, str], float] = {
    ("MA", "ES"): 12_000.0,
    ("DZ", "ES"): 10_000.0,
    ("DZ", "IT"):  8_000.0,
    ("TN", "IT"):  8_000.0,
    ("LY", "IT"):  6_000.0,
}

MENA_H2_PIPELINE_LIFE_SPAN_YR:        int   = 40
MENA_H2_PIPELINE_FINANCE_RATE:        float = 0.04
MENA_H2_PIPELINE_FIXED_COST_EUR_PER_MW_YR: float = 5_000.0


def get_route_capex(src: str, dst: str) -> float:
    """Return per-route H₂ pipeline CAPEX (€/MW) honouring scenario overrides.

    Precedence:
      1. Per-route override `_menaInfra{SRC}_{DST}_{NNN}`
      2. Global override `_menaInfra{NNN}`
      3. Base value from MENA_H2_PIPELINE_CAPEX_EUR_PER_MW
    """
    from pommes_eur.constants import (
        _MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW,
        _MENA_INFRA_PER_ROUTE_OVERRIDES,
    )
    if (src, dst) in _MENA_INFRA_PER_ROUTE_OVERRIDES:
        return _MENA_INFRA_PER_ROUTE_OVERRIDES[(src, dst)]
    if _MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW is not None:
        return _MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW
    return MENA_H2_PIPELINE_CAPEX_EUR_PER_MW[(src, dst)]


def resolve_mena_variants() -> dict[str, Any]:
    """Inspect the current CLEVER_SCENARIO and return a resolved MENA spec.

    Returns a dict with:
      "variant_a_active": bool   — _menaH2NNN with NNN > 0
      "variant_a_cap_twh": float
      "variant_a_cost_eur_per_mwh": float
      "variant_b_active": bool   — _menaOptim[subset]
      "variant_b_countries": tuple[str, ...]
    """
    from pommes_eur.constants import (
        _MENA_H2_IMPORT_CAP_TWH,
        _MENA_H2_DELIVERED_COST_EUR_PER_MWH,
        _MENA_H2_DELIVERED_COST_BY_ENTRY,
        _MENA_OPTIM_ACTIVE_COUNTRIES,
    )

    # Build the resolved per-entry cost dict (per-entry override > flat
    # override > built-in upper-band default).
    cost_by_entry: dict[str, float] = {}
    for cc, cfg in MENA_H2_ENTRY_POINTS.items():
        if cc in _MENA_H2_DELIVERED_COST_BY_ENTRY:
            cost_by_entry[cc] = _MENA_H2_DELIVERED_COST_BY_ENTRY[cc]
        elif _MENA_H2_DELIVERED_COST_EUR_PER_MWH is not None:
            cost_by_entry[cc] = _MENA_H2_DELIVERED_COST_EUR_PER_MWH
        else:
            cost_by_entry[cc] = cfg["delivered_cost_eur_per_mwh"]

    return {
        "variant_a_active":             _MENA_H2_IMPORT_CAP_TWH > 0.0,
        "variant_a_cap_twh":            _MENA_H2_IMPORT_CAP_TWH,
        "variant_a_cost_by_entry":      cost_by_entry,
        "variant_b_active":             bool(_MENA_OPTIM_ACTIVE_COUNTRIES),
        "variant_b_countries":          _MENA_OPTIM_ACTIVE_COUNTRIES,
    }


def mena_imports_enabled() -> bool:
    """True if any MENA variant is active. Used by clever.model._create_empty_energy_model
    to decide whether to include "hydrogen" in the EnergyModel resources list when no
    other H₂-active suffix (e.g. _atr, _h2HIGH) is present."""
    spec = resolve_mena_variants()
    return spec["variant_a_active"] or spec["variant_b_active"]


# ────────────────────────────────────────────────────────────────────────────
# Variant A: production-only ConversionTechnology on EU entry-points
# ────────────────────────────────────────────────────────────────────────────
def add_variant_a_imports_to_area(area: Any, country_code: str) -> None:
    """Variant A: add a production-only ConversionTechnology(hydrogen) on
    this EU entry-point.

    Only fires when:
      - _MENA_H2_IMPORT_CAP_TWH > 0 (Variant A active), AND
      - country_code is in MENA_H2_ENTRY_SHARES (currently ES and IT).

    The per-area cap is split proportional to MENA_H2_ENTRY_SHARES from the
    EU-wide cap. Delivered price is uniform across entry points (the cost
    differential is a Variant B concern; Variant A is "single landed cost").

    Implementation note (2026-05-27): was a NetImport originally, switched to
    a production-only ConversionTechnology for the same reason as
    raw_biomass_supply in clever/biomethane.py — a single NetImport flips
    `add_modules['net_import']=True` and triggers POMMES to materialise full
    Cartesian-product variables across (area × hour × resource × year_op),
    ~5–7 M extra LP variables in our 30-area / 8760-hour setup. The CT path
    is masked on `np.isfinite(conversion_factor) * (conversion_factor != 0)`
    and only materialises the (hydrogen) slot per (area, hour, year_op).
    Semantics identical: priced delivered H₂ capped at the per-area cap.
    See pommes/model/conversion.py:127-131, 238-244, 311-321.
    """
    spec = resolve_mena_variants()
    if not spec["variant_a_active"]:
        return
    entry_cfg = MENA_H2_ENTRY_POINTS.get(country_code)
    if entry_cfg is None or entry_cfg["share"] <= 0.0:
        return
    share = entry_cfg["share"]
    delivered_cost = spec["variant_a_cost_by_entry"][country_code]

    # Lazy import — pommes_craft is heavy and we want construction failures
    # surfaced at call-time, not at module load.
    from pommes_craft import ConversionTechnology  # type: ignore

    cap_mwh = spec["variant_a_cap_twh"] * 1e6 * share
    avg_power_mw = cap_mwh / 8760.0
    h2_cap_mw = avg_power_mw * 5.0

    with area.model.context():
        area.add_component(ConversionTechnology(
            name="mena_h2_import",
            factor={"hydrogen": 1.0},
            # availability=1.0 explicit: same sanitizer interaction as
            # raw_biomass_supply in clever/biomethane.py — without this,
            # avail.fillna(0.0) zeroes hourly availability and the CT can't
            # dispatch any H2 despite having capacity.
            availability=1.0,
            must_run=0.0,
            variable_cost=delivered_cost,
            max_yearly_production=cap_mwh,
            # power_capacity_max must equal investment_max — see biomethane.py
            # for the sanitize_absent_conversions "absent" classification trap.
            power_capacity_max=h2_cap_mw,
            power_capacity_investment_max=h2_cap_mw,
            invest_cost=0.0,
            fixed_cost=0.0,
            finance_rate=0.0,
            life_span=5,
        ))
    logger.info(
        "MENA Variant A: ConversionTechnology(mena_h2_import) added to %s "
        "@ %.0f €/MWh_H2, cap=%.1f TWh/yr (share=%.2f of EU-wide %.0f TWh/yr)",
        country_code,
        delivered_cost,
        cap_mwh / 1e6, share,
        spec["variant_a_cap_twh"],
    )


# ────────────────────────────────────────────────────────────────────────────
# Variant B: per-country MENA Area + supply stack + transport routes
# ────────────────────────────────────────────────────────────────────────────
# Per-country flat-CF fallbacks (used when both ninja AND PVGIS unreachable).
# Anchored to published resource assessments. Same values as the original
# Phase 2.5 flat estimates; they are now ONLY a last-resort fallback.
_MENA_FLAT_SOLAR_CF: dict[str, float] = {
    "MA": 0.25, "DZ": 0.27, "TN": 0.24, "LY": 0.27,
}
_MENA_FLAT_WIND_CF: dict[str, float] = {
    "MA": 0.37,  # Tarfaya elite Atlantic coast
    "DZ": 0.23,  # Oran coast
    "TN": 0.25,  # Cap Bon
    "LY": 0.18,  # Benghazi (weaker than MA/TN)
}


def _solar_availability(country: str, hours: list[int], year_op: int):
    """Return a polars DataFrame with columns (hour, year_op, availability).

    Fetch order (each step caches on first success so subsequent runs are
    zero-API-call):
      1. renewables.ninja MERRA-2 (preferred — same source as wind for
         coherent intra-day shape)
      2. PVGIS v5.2 TMY (no auth required; reliable EU fetcher with MENA
         coverage)
      3. Flat country-specific CF from _MENA_FLAT_SOLAR_CF (loud warning)
    """
    import polars as pl
    lat, lon = MENA_COUNTRY_CONFIG[country]["solar_ref_point"]
    tag = f"mena_{country}_solar"

    # 1. renewables.ninja (cached after first call)
    try:
        from pommes_eur.data_fetchers import fetch_renewables_ninja_hourly
        cf = fetch_renewables_ninja_hourly(lat, lon, tag=tag, kind="pv")
        if str(cf.attrs.get("fallback", "true")).lower() == "false":
            logger.info(
                "MENA-%s solar: using renewables.ninja MERRA-2 (mean CF=%.3f, year=%s)",
                country, float(cf.mean()), cf.attrs.get("year", "?"),
            )
            return pl.DataFrame({
                "availability": list(cf.values[:8760]),
                "hour":         hours,
                "year_op":      [year_op] * 8760,
            })
    except Exception as exc:
        logger.warning("MENA-%s solar: ninja path raised %s — trying PVGIS", country, exc)

    # 2. PVGIS (no auth, also cached)
    try:
        from pommes_eur.data_fetchers import fetch_pvgis_solar_hourly
        cf = fetch_pvgis_solar_hourly(lat, lon, tag=tag)
        if str(cf.attrs.get("fallback", "true")).lower() == "false":
            logger.info(
                "MENA-%s solar: using PVGIS TMY (mean CF=%.3f)",
                country, float(cf.mean()),
            )
            return pl.DataFrame({
                "availability": list(cf.values[:8760]),
                "hour":         hours,
                "year_op":      [year_op] * 8760,
            })
    except Exception as exc:
        logger.warning("MENA-%s solar: PVGIS path raised %s — using flat fallback", country, exc)

    # 3. Flat fallback
    flat = _MENA_FLAT_SOLAR_CF.get(country, 0.25)
    logger.warning(
        "MENA-%s solar: ALL FETCHERS FAILED — using flat CF=%.2f (literature anchor)",
        country, flat,
    )
    return pl.DataFrame({
        "availability": [flat] * 8760,
        "hour":         hours,
        "year_op":      [year_op] * 8760,
    })


def _wind_availability(country: str, hours: list[int], year_op: int):
    """Return per-hour wind CF.

    Fetch order:
      1. renewables.ninja MERRA-2 wind (Vestas V112-3000 @ 100m hub — see
         clever.data_fetchers.NINJA_DEFAULT_TURBINE)
      2. Flat country-specific CF from _MENA_FLAT_WIND_CF (loud warning)
    """
    import polars as pl
    lat, lon = MENA_COUNTRY_CONFIG[country]["wind_ref_point"]
    tag = f"mena_{country}_wind"

    try:
        from pommes_eur.data_fetchers import fetch_renewables_ninja_hourly
        cf = fetch_renewables_ninja_hourly(lat, lon, tag=tag, kind="wind")
        if str(cf.attrs.get("fallback", "true")).lower() == "false":
            logger.info(
                "MENA-%s wind: using renewables.ninja MERRA-2 / %s @ %dm "
                "(mean CF=%.3f, year=%s)",
                country, cf.attrs.get("turbine", "?"),
                int(cf.attrs.get("hub_height_m", 100)),
                float(cf.mean()), cf.attrs.get("year", "?"),
            )
            return pl.DataFrame({
                "availability": list(cf.values[:8760]),
                "hour":         hours,
                "year_op":      [year_op] * 8760,
            })
    except Exception as exc:
        logger.warning("MENA-%s wind: ninja path raised %s — using flat fallback", country, exc)

    flat = _MENA_FLAT_WIND_CF.get(country, 0.25)
    logger.warning(
        "MENA-%s wind: ninja unavailable — using flat CF=%.2f (literature anchor)",
        country, flat,
    )
    return pl.DataFrame({
        "availability": [flat] * 8760,
        "hour":         hours,
        "year_op":      [year_op] * 8760,
    })


# ── §3.5: baseload-shaped MENA demand ──────────────────────────────────
# MENA has no DemandForge load curves, so each MENA country borrows an EU
# country's *electricity baseload* temporal SHAPE (industrial + services +
# residential baseline — excludes the thermosensitive EU-climate heating
# peak and EV charging) and rescales it to the MENA country's annual elec /
# H₂ TWh. Latitude-banded donor: MA/TN ride the ES shape, DZ/LY the IT
# shape. Falls back to a flat profile if the donor baseload is unavailable
# (e.g. the donor country is not modelled in this run). This replaces the
# earlier flat-scalar demand; documented as a known simplification (no
# MENA-specific summer A/C peak) in the methods section.
_MENA_SHAPE_DONOR = {"MA": "ES", "TN": "ES", "DZ": "IT", "LY": "IT"}
_mena_baseload_cache: "dict[str, Any]" = {}


def _mena_baseload_shape(donor_cc: str, year_op: int) -> Any:
    """Memoised EU electricity-baseload profile for a donor country (or None)."""
    if donor_cc not in _mena_baseload_cache:
        from pommes_eur import DEMAND_DIR
        from pommes_eur.model import extract_baseload_profile
        csv = str(DEMAND_DIR / "hourly_electricity_demand.csv")
        _mena_baseload_cache[donor_cc] = extract_baseload_profile(
            electricity_demand_csv_path=csv,
            country_code=donor_cc,
            year_op=year_op,
        )
    return _mena_baseload_cache[donor_cc]


def add_mena_country_area(energy_model: Any, country: str, eoles_costs: dict | None = None) -> Any:
    """Create the MENA Area for `country` and populate its supply stack.

    Returns the Area (so callers can wire transport links from it).

    Components added:
      * Solar           — ConversionTechnology, factor={"electricity": 1}, PVGIS profile
      * Wind_Onshore    — ConversionTechnology, factor={"electricity": 1}, ERA5/flat profile
      * MENA_electrolysis — factor={"hydrogen": +1, "electricity": -1.85}, with
        country-specific finance rate + desalination cost adder on VOM
      * h2_storage      — StorageTechnology (salt cavern: invest_cost_energy = 5 €/MWh)
      * Spillage(electricity) — large cap, so excess VRE can be curtailed
      * Spillage(hydrogen)    — large cap, so over-produced H₂ doesn't strand the LP

    NO electricity transmission to/from EU; NO demand; NO load shedding.
    """
    from pommes_craft import (
        Area,
        ConversionTechnology,
        Demand,
        LoadShedding,
        NetImport,
        Spillage,
        StorageTechnology,
    )
    # Lazy import — clever.constants must read CLEVER_SCENARIO before this fires.
    from pommes_eur.constants import (
        DEFAULT_LOAD_SHEDDING_COST,
        _MENA_CAP_SCALE,
        _MENA_PREF_SCALE,
    )

    cfg = MENA_COUNTRY_CONFIG[country]
    finance_rate = mena_finance_rate(country)
    year_op = energy_model.year_ops[0]
    hours = list(energy_model.hours)

    # Scale caps + reservation per scenario suffix (_menaCapNN, _menaPrefNN).
    cap_scale  = _MENA_CAP_SCALE
    pref_scale = _MENA_PREF_SCALE
    solar_max_mw        = cfg["solar_max_mw"]        * cap_scale
    wind_max_mw         = cfg["wind_max_mw"]         * cap_scale
    electrolyser_max_mw = (cfg["solar_max_mw"] + cfg["wind_max_mw"]) * cap_scale  # lifted to the RE nameplate so GREEN H2 is RE-limited, not electrolyser-limited (2026-06-23)
    h2_storage_max_mwh  = cfg["h2_storage_max_mwh"]  * cap_scale
    h2_storage_max_mw   = cfg["h2_storage_max_mw"]   * cap_scale

    # ── Bundle-driven exogenous local demand ──────────────────────
    # MENA serves its own electricity + H₂ demand from the bundle anchors
    # in MENA_*_DEMAND_TWH_PER_YR. The pref_scale (_menaPrefNN suffix) lets
    # us sensitivity-test the "MENA exports everything" boundary by setting
    # pref=0; default 100% keeps the anchor values intact.
    from pommes_eur.constants import DEMANDFORGE_BUNDLE  # set in run_adequacy.py
    elec_twh = mena_local_demand_twh(country, "electricity", DEMANDFORGE_BUNDLE)
    h2_twh   = mena_local_demand_twh(country, "hydrogen",    DEMANDFORGE_BUNDLE)

    # §3.5: baseload-SHAPED hourly demand (was flat scalar). Borrow the donor
    # EU country's electricity-baseload shape and rescale to the annual TWh,
    # scaled by the national-preference factor (_menaPrefNN suffix).
    from pommes_eur.model import shape_h2_demand_from_baseload, shape_h2_demand_flat
    annual_elec_mwh = elec_twh * 1e6 * pref_scale
    annual_h2_mwh   = h2_twh   * 1e6 * pref_scale
    donor = _MENA_SHAPE_DONOR.get(country, "ES")
    _bl = _mena_baseload_shape(donor, year_op)

    def _shape(annual_mwh: float):
        if annual_mwh <= 0:
            return None
        if _bl is not None:
            return shape_h2_demand_from_baseload(
                baseload_profile_pl=_bl,
                annual_h2_demand_mwh=annual_mwh,
                year_op=year_op,
            )
        return shape_h2_demand_flat(annual_h2_mwh=annual_mwh, year_op=year_op)

    elec_profile = _shape(annual_elec_mwh)
    h2_profile   = _shape(annual_h2_mwh)
    _src = f"{donor}-baseload" if _bl is not None else "FLAT(no donor baseload)"
    logger.info(
        "MENA-%s demand shaped via %s: elec %.1f TWh (peak %.0f MW), "
        "H2 %.1f TWh (peak %.0f MW)",
        country, _src, elec_twh * pref_scale,
        float(elec_profile["demand"].max()) if elec_profile is not None else 0.0,
        h2_twh * pref_scale,
        float(h2_profile["demand"].max()) if h2_profile is not None else 0.0,
    )

    with energy_model.context():
        area = Area(country)

        # ── Solar ──────────────────────────────────────────────────
        area.add_component(ConversionTechnology(
            name="Solar",
            factor={"electricity": 1.0},
            availability=_solar_availability(country, hours, year_op),
            must_run=0.0,
            variable_cost=0.0,
            fixed_cost=MENA_SOLAR_FIXED_COST_EUR_PER_MW_YR,
            invest_cost=MENA_SOLAR_CAPEX_EUR_PER_KW * 1000.0,
            finance_rate=finance_rate,
            life_span=MENA_SOLAR_LIFE_YR,
            power_capacity_max=solar_max_mw,
            power_capacity_investment_max=solar_max_mw,
            power_capacity_investment_min=0.0,
            early_decommissioning=True,
        ))

        # ── Wind onshore ───────────────────────────────────────────
        area.add_component(ConversionTechnology(
            name="Wind_Onshore",
            factor={"electricity": 1.0},
            availability=_wind_availability(country, hours, year_op),
            must_run=0.0,
            variable_cost=0.0,
            fixed_cost=MENA_WIND_FIXED_COST_EUR_PER_MW_YR,
            invest_cost=MENA_WIND_ONSHORE_CAPEX_EUR_PER_KW * 1000.0,
            finance_rate=finance_rate,
            life_span=MENA_WIND_LIFE_YR,
            power_capacity_max=wind_max_mw,
            power_capacity_investment_max=wind_max_mw,
            power_capacity_investment_min=0.0,
            early_decommissioning=True,
        ))

        # ── Electrolyser ───────────────────────────────────────────
        # VOM in €/MWh_H₂ = MENA VOM + desalination adder (per output H₂).
        electrolyser_vom = MENA_VOM_EUR_PER_MWH + MENA_DESALINATION_COST_EUR_PER_MWH_H2
        area.add_component(ConversionTechnology(
            name="MENA_electrolysis",
            factor={"hydrogen": 1.0, "electricity": -MENA_ELECTROLYSER_FACTOR_E_PER_H2},
            availability=1.0,
            must_run=0.0,
            variable_cost=electrolyser_vom,
            fixed_cost=MENA_ELECTROLYSER_FIXED_COST_EUR_PER_MW_YR,
            invest_cost=MENA_ELECTROLYSER_CAPEX_EUR_PER_KW * 1000.0,
            finance_rate=finance_rate,
            life_span=MENA_ELECTROLYSER_LIFE_YR,
            power_capacity_max=electrolyser_max_mw,
            power_capacity_investment_max=electrolyser_max_mw,
            power_capacity_investment_min=0.0,
            early_decommissioning=True,
        ))

        # ── H₂ storage (salt cavern) ───────────────────────────────
        area.add_component(StorageTechnology(
            name="h2_storage",
            factor_in={"hydrogen": -1.02},   # 2% compression loss on charge
            factor_keep={"hydrogen": 0.0},
            factor_out={"hydrogen": 1.0},
            invest_cost_energy=MENA_H2_STORAGE_CAPEX_EUR_PER_MWH,
            invest_cost_power=MENA_H2_STORAGE_CAPEX_EUR_PER_MW,
            fixed_cost_power=MENA_H2_STORAGE_FIXED_COST_EUR_PER_MW_YR,
            finance_rate=finance_rate,
            life_span=MENA_H2_STORAGE_LIFE_YR,
            dissipation=0.0,
            energy_capacity_investment_min=0.0,
            energy_capacity_investment_max=h2_storage_max_mwh,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=h2_storage_max_mw,
            early_decommissioning=True,
        ))

        # ── H₂-to-power (gas-free firming) ─────────────────────────
        # Burn locally-produced H₂ back into electricity to firm the grid at
        # night — the gas-free alternative to the Gas CCGT. Same turbine class
        # (η = MENA_CCGT_EFF); draws hydrogen from the local bus so the LP picks
        # gas vs H₂ firming on fuel + carbon economics. Added unconditionally
        # (independent of the natural_gas / blue-H₂ block below).
        area.add_component(ConversionTechnology(
            name="Hydrogen_power_plant",
            factor={"electricity": 1.0, "hydrogen": -1.0 / MENA_H2CCGT_EFF},
            availability=1.0,
            must_run=0.0,
            variable_cost=MENA_H2CCGT_VOM_EUR_PER_MWH,
            fixed_cost=MENA_H2CCGT_FIXED_COST_EUR_PER_MW_YR,
            invest_cost=MENA_H2CCGT_CAPEX_EUR_PER_KW * 1000.0,
            finance_rate=finance_rate,
            life_span=MENA_H2CCGT_LIFE_YR,
            # power_capacity_max MUST equal investment_max — sanitize_absent_
            # conversions zeroes the cap otherwise (see Gas CCGT block below).
            power_capacity_max=MENA_H2CCGT_CAP_MW_PER_COUNTRY,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=MENA_H2CCGT_CAP_MW_PER_COUNTRY,
            early_decommissioning=True,
        ))

        # ── Batteries (gas-free diurnal firming) ───────────────────
        # Shift midday solar into the evening/night instead of relying on the
        # Gas CCGT. EU EOLES battery_1h/4h cost basis, MENA risk-adjusted
        # finance_rate. Skipped (with a warning) if eoles_costs wasn't plumbed
        # through — keeps any non-standard caller backward-compatible.
        if eoles_costs is not None:
            from pommes_eur.constants import BESS_SPECS
            from pommes_eur.model import storage_costs_from_eoles
            for _bess, _spec in BESS_SPECS.items():
                _cap_mw = MENA_BESS_POWER_CAP_MW_PER_COUNTRY.get(_bess, 0.0)
                if _cap_mw <= 0.0:
                    continue
                _bc = storage_costs_from_eoles(_spec["eoles_tech"], eoles_costs)
                _eta = float(_spec["roundtrip_efficiency"])
                _dur = float(_spec["duration_hours"])
                area.add_component(StorageTechnology(
                    name=_bess,
                    factor_in={"electricity": -1.0},
                    factor_out={"electricity": _eta},
                    factor_keep={"electricity": 0.0},
                    fixed_cost_power=_bc["fixed_cost_power"],
                    invest_cost_power=_bc["invest_cost_power"],
                    invest_cost_energy=_bc["invest_cost_energy"],
                    finance_rate=finance_rate,   # MENA risk-adjusted, not EU 4%
                    life_span=_bc["life_span"],
                    dissipation=0.0,
                    energy_capacity_investment_min=0.0,
                    energy_capacity_investment_max=_cap_mw * _dur,
                    power_capacity_investment_min=0.0,
                    power_capacity_investment_max=_cap_mw,
                    early_decommissioning=True,
                ))
            logger.info(
                "MENA-%s gas-free firming: H2-CCGT ≤%.0f GW (η=%.0f%%) + "
                "batteries 1h≤%.0f GW / 4h≤%.0f GW added",
                country, MENA_H2CCGT_CAP_MW_PER_COUNTRY / 1000.0,
                MENA_H2CCGT_EFF * 100.0,
                MENA_BESS_POWER_CAP_MW_PER_COUNTRY.get("Battery_1h", 0.0) / 1000.0,
                MENA_BESS_POWER_CAP_MW_PER_COUNTRY.get("Battery_4h", 0.0) / 1000.0,
            )
        else:
            logger.warning(
                "MENA-%s: eoles_costs not provided — batteries skipped "
                "(only H2-CCGT added; gas-free diurnal firming reduced).", country,
            )

        # ── Local electricity demand (bundle-driven, baseload-shaped) ──
        if elec_profile is not None:
            area.add_component(Demand(
                name="electricity_demand",
                resource="electricity",
                demand=elec_profile,
            ))
            area.add_component(LoadShedding(
                name="electricity_load_shedding",
                resource="electricity",
                cost=DEFAULT_LOAD_SHEDDING_COST,
                # per-hour valve: size to the shaped peak so it can fully open
                max_capacity=float(elec_profile["demand"].max()),
            ))

        # ── Domestic H₂ demand (bundle-driven, baseload-shaped) ──
        if h2_profile is not None:
            area.add_component(Demand(
                name="domestic_h2_demand",
                resource="hydrogen",
                demand=h2_profile,
            ))
            area.add_component(LoadShedding(
                name="hydrogen_load_shedding",
                resource="hydrogen",
                cost=DEFAULT_LOAD_SHEDDING_COST,
                max_capacity=float(h2_profile["demand"].max()),
            ))


        # ── Spillage (electricity + hydrogen) ──────────────────────
        # MENA has tiny domestic demand vs huge RE potential → if Solar/Wind
        # produces more than electrolyser can consume (and storage is full),
        # the LP MUST curtail. Set explicit large caps so the LP can dispatch
        # surplus when storage is saturated. Cost = 1.0 €/MWh: low-but-nonzero
        # so the LP only spills when there's no productive use.
        area.add_component(Spillage(
            name="mena_electricity_spillage",
            resource="electricity",
            cost=1.0,
            max_capacity=solar_max_mw + wind_max_mw,
        ))
        area.add_component(Spillage(
            name="mena_hydrogen_spillage",
            resource="hydrogen",
            cost=1.0,
            max_capacity=electrolyser_max_mw,
        ))

        # ── Blue-H₂ supply: natural_gas NetImport + ATR_CCS / SMR_CCS ──
        # Added 2026-05-29: lets the LP build blue H₂ in MENA from local NG +
        # CCS, competing with green H₂ from electrolysis. Crucial because DZ
        # and LY have world-class stranded gas at ~€10-12/MWh_th wholesale,
        # far below the EU NG bus price. With 90% CCS capture and the same
        # carbon-price rebate as the EU side (CBAM-equivalent), blue H₂ from
        # MENA can deliver below green H₂ from the same site.
        #
        # The wholesale price + CBAM-equivalent CO₂ adder are resolved per
        # country in mena_natural_gas_import_price(); the CCS rebate uses the
        # same formula as clever.methane_h2_ccs, so when carbon_price changes
        # both EU and MENA blue H₂ track each other.
        em_resources = list(energy_model.resources) if hasattr(energy_model, "resources") else []
        _has_natural_gas = "natural_gas" in em_resources
        _has_hydrogen    = "hydrogen"    in em_resources

        if _has_natural_gas and _has_hydrogen:
            ng_price = mena_natural_gas_import_price(country)
            area.add_component(NetImport(
                name="natural_gas_supply",
                resource="natural_gas",
                import_price=ng_price,
                # No annual import cap — let production be the constraint.
            ))

            # Blue H₂ techs (single ng_mode — no biomethane in MENA). Same
            # reference parameters as clever.methane_h2_ccs, but using
            # ConversionTechnology (not CombinedTechnology) since there's
            # only one fuel mode here.
            from pommes_eur.methane_h2_ccs import (
                _CCS_EFFICIENCY, _CCS_CAPTURE_RATE,
                _SMR_CAPEX_EUR_PER_KW, _ATR_CAPEX_EUR_PER_KW,
                _CCS_FOM_EUR_PER_KW_YR, _SMR_VOM_EUR_PER_MWH,
                _ATR_VOM_EUR_PER_MWH, _CCS_OPCOST_EUR_PER_MWH_H2,
                _CCS_LIFE_YR, _ccs_carbon_adjustment_per_mwh_h2,
            )
            ccs_rebate = _ccs_carbon_adjustment_per_mwh_h2(_CCS_EFFICIENCY)
            fuel_per_h2 = 1.0 / _CCS_EFFICIENCY

            for tech_name, vom, capex in (
                ("SMR_CCS", _SMR_VOM_EUR_PER_MWH, _SMR_CAPEX_EUR_PER_KW),
                ("ATR_CCS", _ATR_VOM_EUR_PER_MWH, _ATR_CAPEX_EUR_PER_KW),
            ):
                area.add_component(ConversionTechnology(
                    name=tech_name,
                    factor={
                        "hydrogen":    +1.0,
                        "natural_gas": -fuel_per_h2,
                    },
                    availability=1.0,
                    must_run=0.0,
                    variable_cost=vom + _CCS_OPCOST_EUR_PER_MWH_H2 - ccs_rebate,
                    fixed_cost=_CCS_FOM_EUR_PER_KW_YR * 1000.0,
                    invest_cost=capex * 1000.0,
                    finance_rate=finance_rate,
                    life_span=_CCS_LIFE_YR,
                    # IMPORTANT: power_capacity_max MUST equal investment_max.
                    # sanitize_absent_conversions classifies a tech as "absent"
                    # when BOTH cap_min AND cap_max are NaN, and zeroes its
                    # investment_max — silently disabling the tech. Same trap
                    # that hit raw_biomass_supply (see biomethane.py:466-470).
                    power_capacity_max=_MENA_BLUE_H2_CAP_MW_PER_COUNTRY,
                    power_capacity_investment_min=0.0,
                    power_capacity_investment_max=_MENA_BLUE_H2_CAP_MW_PER_COUNTRY,
                    early_decommissioning=True,
                ))
            # MENA Gas CCGT (single-mode, fossil only) — lets the LP serve
            # local elec demand from cheap domestic gas when RE shape doesn't
            # match load. CO₂ adder at NG bus self-suppresses at high carbon
            # price. No bio_mode (no biomethane infrastructure modeled at MENA).
            area.add_component(ConversionTechnology(
                name="Gas",
                factor={
                    "electricity": +1.0,
                    "natural_gas": -1.0 / MENA_CCGT_EFF,
                },
                availability=1.0,
                must_run=0.0,
                variable_cost=MENA_CCGT_VOM_EUR_PER_MWH,
                fixed_cost=MENA_CCGT_FIXED_COST_EUR_PER_MW_YR,
                invest_cost=MENA_CCGT_CAPEX_EUR_PER_KW * 1000.0,
                finance_rate=finance_rate,
                life_span=MENA_CCGT_LIFE_YR,
                # IMPORTANT: power_capacity_max MUST equal investment_max
                # to defeat sanitize_absent_conversions zero-ing the cap.
                # See SMR_CCS / ATR_CCS block above for context.
                power_capacity_max=MENA_CCGT_CAP_MW_PER_COUNTRY,
                power_capacity_investment_min=0.0,
                power_capacity_investment_max=MENA_CCGT_CAP_MW_PER_COUNTRY,
                early_decommissioning=True,
            ))
            logger.info(
                "MENA-%s blue chain: NG bus @ %.1f €/MWh_th, "
                "ATR_CCS+SMR_CCS each cap %.0f GW, CCS rebate %.1f €/MWh_H₂, "
                "Gas CCGT cap %.0f GW, eff %.0f%%",
                country, ng_price,
                _MENA_BLUE_H2_CAP_MW_PER_COUNTRY / 1000.0, ccs_rebate,
                MENA_CCGT_CAP_MW_PER_COUNTRY / 1000.0, MENA_CCGT_EFF * 100,
            )

        # ── Free-import lockdown (matches _add_eu_resource_locks in clever/model.py) ──
        # MENA areas must NOT enjoy free external NetImport on any resource
        # EXCEPT natural_gas (where the blue-H₂ chain needs it, priced above).
        # All H₂ goes out via mena_h2_pipeline TransportTechnology.
        # Lock every other resource at max=0 to defeat POMMES's auto-NaN default.
        for resource in em_resources:
            if resource == "natural_gas" and _has_natural_gas and _has_hydrogen:
                continue  # already added as a real supply above
            area.add_component(NetImport(
                name=f"{resource}_lock",
                resource=resource,
                import_price=0.0,
                max_yearly_energy_import=0.0,
                max_yearly_energy_export=0.0,
            ))

    logger.info(
        "MENA Variant B: built area %s (%s) — solar≤%.0f GW, wind≤%.0f GW, "
        "electrolyser≤%.0f GW, h2_storage≤%.1f TWh / %.0f GW, finance_rate=%.2f%%",
        country, cfg["name"],
        cfg["solar_max_mw"] / 1000.0, cfg["wind_max_mw"] / 1000.0,
        cfg["electrolyser_max_mw"] / 1000.0,
        cfg["h2_storage_max_mwh"] / 1e6, cfg["h2_storage_max_mw"] / 1000.0,
        finance_rate * 100.0,
    )
    return area


def add_mena_h2_routes(
    energy_model: Any,
    areas: dict[str, Any],
    active_countries: tuple[str, ...],
) -> None:
    """For each active MENA→EU route, add a unidirectional H₂ pipeline.

    A route is built only when:
      * src in active_countries, AND
      * both src and dst Areas exist in `areas`.

    Pipelines are unidirectional (MENA → EU) — Europe never exports H₂
    to MENA in this model.
    """
    from pommes_craft import Link, TransportTechnology

    for (src, dst) in MENA_H2_ROUTES:
        if src not in active_countries:
            continue
        if src not in areas or dst not in areas:
            logger.warning(
                "MENA H₂ route %s→%s: skipping (area missing — src=%s, dst=%s)",
                src, dst, src in areas, dst in areas,
            )
            continue

        capex = get_route_capex(src, dst)
        hurdle = MENA_H2_PIPELINE_HURDLE_EUR_PER_MWH[(src, dst)]
        cap_mw = MENA_H2_PIPELINE_CAPACITY_INVESTMENT_MAX_MW[(src, dst)]

        pipe_kwargs = {
            # SINGLE shared transport_tech "h2_pipeline" — the SAME tech the EU
            # intra-EU H₂ grid uses (supplyforge.add_h2_interconnections). Using a
            # SEPARATE "mena_h2_pipeline" tech made pommes build a (transport_tech ×
            # link) grid in which that tech, registered on only the 5 MENA links,
            # spawned ~74 OFF-DIAGONAL cells (mena_h2_pipeline × every non-MENA
            # link): area_from/to fill to None (inert — carry no flow to any bus)
            # yet power_capacity_investment_max fills to NaN, which pommes
            # (transport.py: isfinite mask) treats as UNBOUNDED → ~74 zero-cost,
            # zero-benefit, unbounded DEGENERATE free LP columns that polluted the
            # barrier and let Crossover=0 park the real corridor at 0 GW (the EU
            # grid tolerates only ~6 such cells and solves fine; ~74 is an order of
            # magnitude worse). Collapsing onto "h2_pipeline" makes the MENA links
            # real DIAGONAL cells of the working tech and creates NO new
            # off-diagonal cells. Per-route capex/cap/hurdle survive because they
            # ride on the (tech, link) row and each MENA link is a distinct `link`
            # (pommes' duplicate-name guard is per-Link, so reusing the tech name
            # across distinct links is exactly how the EU grid works). Build-order
            # independent (verified). `early_decommissioning` dropped to match the
            # EU h2_pipeline exactly (it sets neither). Re-diagnosed 2026-06-22.
            "name":          "h2_pipeline",
            "resource":      "hydrogen",
            "invest_cost":   capex,
            "fixed_cost":    MENA_H2_PIPELINE_FIXED_COST_EUR_PER_MW_YR,
            "hurdle_costs":  hurdle,
            "finance_rate":  MENA_H2_PIPELINE_FINANCE_RATE,
            "life_span":     MENA_H2_PIPELINE_LIFE_SPAN_YR,
            # Investable: LP sizes the corridor between 0 and the route cap.
            "power_capacity_investment_min": 0.0,
            "power_capacity_investment_max": cap_mw,
        }
        with energy_model.context():
            tt = TransportTechnology(**pipe_kwargs)
            link = Link(
                name=f"mena_h2_link_{src}_{dst}",
                area_from=areas[src],
                area_to=areas[dst],
            )
            link.add_transport_technology(tt)
        logger.info(
            "MENA H₂ route %s→%s: capex=%.2f M€/MW, hurdle=%.1f €/MWh_H2, "
            "cap≤%.0f GW",
            src, dst, capex / 1e6, hurdle, cap_mw / 1000.0,
        )


def add_mena_to_model(
    energy_model: Any,
    areas: dict[str, Any],
    eoles_costs: dict | None = None,
) -> dict[str, Any]:
    """Entry point: build all active MENA Variant-B areas + pipelines.

    No-op when Variant B is not active (returns input `areas` unchanged).

    Modifies `areas` in place to add the MENA areas; returns the updated dict
    for chaining.
    """
    spec = resolve_mena_variants()
    if not spec["variant_b_active"]:
        return areas

    active = spec["variant_b_countries"]
    logger.info("MENA Variant B active for: %s", ", ".join(active))
    for country in active:
        if country not in MENA_COUNTRY_CONFIG:
            logger.warning("Skipping MENA country %r — not in MENA_COUNTRY_CONFIG", country)
            continue
        areas[country] = add_mena_country_area(energy_model, country, eoles_costs=eoles_costs)

    add_mena_h2_routes(energy_model, areas, active)
    return areas
