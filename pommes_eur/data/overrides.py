"""
clever.overrides — scenario-resolved values and scenario-gated derived tables.

This is the **override / resolution layer** of what used to be the constants.py
monolith. It reads the scenario string once (``CLEVER_SCENARIO``) and, using the
pure parsers in ``clever/scenario/parse.py`` and the static tables in
``clever/inputs.py``, materialises the per-run scenario globals (``_NO_GAS``,
``_CO2_PRICE_EUR_PER_TONNE``, the ``_MENA_*`` values, …) and the scenario-gated
derived tables (``EXPANDABLE_MODEL_TECHS`` with nuclear injection,
``EXPANSION_HEADROOM_BY_COUNTRY`` with re-entry mutation, the VRE bands, the
BESS scaling, the corridor multiplier). The fuel-price functions
(``natural_gas_import_price`` / ``oil_import_price``) that combine these
scenario values live here too.

``clever/constants.py`` is a thin backwards-compatible facade that re-exports
everything from this module (plus parse + inputs), so existing
``from clever.constants import X`` imports keep working. Carved verbatim out of
the old constants.py; values are byte-for-byte identical (guarded by
tests/golden). No numerics changed.
"""
from __future__ import annotations

import os as _os

# Scenario knob read from environment — drives per-scenario gating below.
# Notebook cell 1.1 sets the same env var; for non-notebook entrypoints
# (ad-hoc imports) the env var must be set BEFORE `import pommes_eur` for the
# gates to take effect. Default empty = R0-style behaviour (no Nuclear
# expansion, VRE upper band 0.15). POMMES_EUR_SCENARIO wins over the legacy
# CLEVER_SCENARIO (see pommes_eur.scenario.env).
from pommes_eur.scenario.env import current_scenario as _current_scenario  # noqa: E402
_SCENARIO: str = _current_scenario()

# Pure scenario-string parsers now live in clever/scenario/parse.py
from pommes_eur.scenario.parse import (  # noqa: E402
    _is_high_demand,
    _is_nuclear_expandable,
    _parse_biomethane_scope,
    _parse_atr_enabled,
    _parse_electrolyser_capex_override,
    _parse_demandforge_bundle_override,
    _parse_co2_price_override,
    _parse_gas_floor_lifted,
    _parse_ng_leak_rate,
    _parse_bio_leak_rate,
    _parse_ccs_cap_gw,
    _parse_vre_xxl,
    _parse_vre_free,
    _parse_nuke_xxl,
    _parse_voll_override,
    _parse_pipeline_km_cost,
    _parse_elec_demand_multiplier,
    _parse_weather_year,
    _parse_vre_extended,
    _parse_no_elec_floor,
    _parse_no_grid_exp,
    _parse_h2_local_share,
    _parse_no_gas,
    _parse_mena_h2_cap,
    _parse_mena_h2_cost,
    _parse_mena_h2_cost_by_entry,
    _parse_mena_optim_active,
    _parse_mena_infra_global,
    _parse_mena_infra_overrides,
    _parse_mena_ng_wholesale_overrides,
    _parse_mena_cap_scale,
    _parse_mena_pref_scale,
    _parse_mena_risk_overrides,
    _parse_ng_price_override,
    _parse_oil_price_override,
    _parse_fuel_ramp_override,
)

# Static input tables now live in clever/inputs.py
from pommes_eur.data.inputs import (  # noqa: E402
    INVESTABLE_CORRIDOR_PAIRS,
    INVESTABLE_CORRIDOR_CAPEX_EUR_PER_MW,
    INVESTABLE_CORRIDOR_LIFE_SPAN_YR,
    INVESTABLE_CORRIDOR_FINANCE_RATE,
    CH4_KG_PER_MWH_TH,
    CH4_GWP100_FOSSIL,
    CH4_GWP100_BIOGENIC,
    AREA_MAP,
    MODEL_TO_SUPPLYFORGE,
    CLEVER_CAPACITY_TO_MODEL,
    CLEVER_NON_ENR_TO_MODEL,
    CLEVER_VRE_SPECS,
    VRE_PROFILE_FALLBACK_BY_COUNTRY,
    DEFAULT_VRE_FLAT_CF,
    DEFAULT_VRE_FLAT_CF_GENERIC,
    SUPPLYFORGE_FALLBACK_YEARS,
    MODELTECH_TO_EOLES,
    EOLES_LIFETIME,
    FUEL_ADDER_2050,
    _CCGT_EFF,
    _OCGT_EFF,
    NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH,
    OIL_CO2_INTENSITY_T_PER_MWH_TH,
    _DEFAULT_FUEL_ANNUAL_RAMP_PCT,
    _TARGET_MODEL_YEAR,
    ROR_HYDRO_VARIABLE_COST,
    RESERVOIR_HYDRO_VARIABLE_COST,
    PUMPED_HYDRO_ROUNDTRIP_EFF,
    HYDRO_ROR_FLAT_CF,
    DEFAULT_HYDRO_ROR_FLAT_CF,
    HYDRO_RESERVOIR_FLAT_CF,
    DEFAULT_HYDRO_RESERVOIR_FLAT_CF,
    HYDRO_PEMMDB,
    SUPPLYFORGE_HYDRO_INPUT_CANDIDATES,
    BESS_SPECS,
    _BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY_BASE,
    _DEFAULT_BESS_POWER_INVESTMENT_MAX_MW_BASE,
    MANUAL_INTERCONNECTIONS,
    _NUCLEAR_REENTRY_COUNTRIES,
    VRE_CAPEX_IN_VARIABLE_COST,
    VRE_DOWNSIDE_BAND_BY_COUNTRY,
    DEFAULT_LOAD_SHEDDING_COST,
    DEFAULT_WATER_SPILLAGE_MAX,
    VRE_CAPACITY_CREDIT,
    EPS_MW,
    EPS_MWH,
    DEFAULT_ROR_MONTHLY,
    DEFAULT_RESERVOIR_INFLOW_MONTHLY,
    ASSUMED_FLH,
    DEFAULT_FLH,
    _EOLES_CAPEX_2026,
    _EOLES_FOM_2026,
    _EOLES_VOM_2026,
    _EOLES_DISCOUNT_RATE_UNIFORM,
    _EOLES_STORAGE_CAPEX_2026,
    HOURS_PER_YEAR,
    TWH_TO_MWH,
    AREA_TO_TYNDP,
    DEFAULT_KEEP_AREAS,
    DEFAULT_WEATHER_REF_YEAR,
    PRICE_NUMERICAL_TOL,
)



import re as _re  # used by the BESS/expansion/VRE regex gating below

# Scenario-scalar resolution now lives in pommes_eur/scenario/resolved.py
from pommes_eur.scenario.resolved import (  # noqa: E402
    _HIGH_DEMAND_SCENARIOS,
    _NUCLEAR_EXPANDABLE_SCENARIOS,
    _CORRIDOR_MULT,
    INVESTABLE_CORRIDOR_MULT,
    _NG_UPSTREAM_LEAK_RATE,
    _BIO_CH4_LEAK_RATE,
    _CCS_CAP_GW_PER_COUNTRY,
    _VRE_XXL,
    _VRE_FREE,
    _NUKE_XXL,
    _ELECTRICITY_VOLL_OVERRIDE,
    _H2_VOLL_OVERRIDE,
    _PIPELINE_EUR_PER_MW_PER_KM,
    _BIOMETHANE_SCOPE,
    _ATR_ENABLED,
    _ELECTROLYSER_CAPEX_EUR_PER_KW,
    _DEMANDFORGE_BUNDLE_OVERRIDE,
    DEMANDFORGE_BUNDLE,
    _CO2_PRICE_EUR_PER_TONNE,
    _GAS_FLOOR_LIFTED,
    _ELEC_DEMAND_MULTIPLIER,
    _NO_GAS,
    _NO_ELEC_FLOOR,
    _NO_GRID_EXPANSION,
    _H2_LOCAL_SHARE,
    _VRE_EXTENDED,
    _WEATHER_YEAR_OVERRIDE,
    _MENA_H2_IMPORT_CAP_TWH,
    _MENA_H2_DELIVERED_COST_EUR_PER_MWH,
    _MENA_H2_DELIVERED_COST_BY_ENTRY,
    _MENA_OPTIM_ACTIVE_COUNTRIES,
    _MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW,
    _MENA_INFRA_PER_ROUTE_OVERRIDES,
    _MENA_RISK_PER_COUNTRY_OVERRIDES,
    _MENA_CAP_SCALE,
    _MENA_PREF_SCALE,
    _MENA_NG_WHOLESALE_OVERRIDES,
    _BIOMETHANE_ENSPRESO_SCENARIO,
    _NG_PRICE_OVERRIDE_EUR_PER_MWH_TH,
    _OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH,
    _FUEL_RAMP_PCT_OVERRIDE,
)


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
        from pommes_eur.sources.data_fetchers import fetch_natural_gas_price_eur_per_mwh_th
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
    from pommes_eur.costs.carbon_price import resolved_carbon_price
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
        from pommes_eur.sources.data_fetchers import fetch_brent_crude_price_eur_per_mwh_th
        bare_today = fetch_brent_crude_price_eur_per_mwh_th(refining_margin=True)
        ramp_mult = _fuel_ramp_multiplier()
        bare_price = bare_today * ramp_mult
        logger.info(
            "oil_import_price: WB Brent %.1f €/MWh_th × ramp %.3f = %.1f €/MWh_th "
            "(geopolitical-premium ramp @ %.1f %%/yr linear)",
            bare_today, ramp_mult, bare_price, _resolved_fuel_ramp_pct(),
        )

    # CO₂ adder via the dedicated carbon_price module (trajectory-based).
    from pommes_eur.costs.carbon_price import resolved_carbon_price
    co2_price = resolved_carbon_price(year=_TARGET_MODEL_YEAR)
    co2_adder = OIL_CO2_INTENSITY_T_PER_MWH_TH * co2_price
    final = bare_price + co2_adder
    logger.info(
        "oil_import_price: + CO2 %.1f (%.3f t/MWh × %.0f €/tCO2) = %.1f €/MWh_th delivered",
        co2_adder, OIL_CO2_INTENSITY_T_PER_MWH_TH, co2_price, final,
    )
    return final


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


# ═══════════════════════════════════════════════════════════════════
# 12. EOLES CAPEX
# ═══════════════════════════════════════════════════════════════════


