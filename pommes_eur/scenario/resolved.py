"""pommes_eur.scenario.resolved — the scenario string resolved to per-run globals.

Reads the active scenario once (POMMES_EUR_SCENARIO / CLEVER_SCENARIO) and materialises
the per-run scalar globals — the eager, process-global form of ScenarioSpec: flag bools,
prices, MENA settings, the corridor multiplier, the DemandForge bundle name, the ENSPRESO
coupling. The scenario-gated derived *tables* live in data/expansion.py + data/vre_limits.py;
the fuel-price functions in costs/fuel_prices.py; all are re-exported through pommes_eur.constants
(the aggregation facade). Carved out of the old constants.py monolith — values byte-identical, guarded by tests/golden. No numerics changed.
"""
from __future__ import annotations

from pommes_eur.scenario.env import current_scenario as _current_scenario

_SCENARIO: str = _current_scenario()

from pommes_eur.scenario.parse import (  # noqa: E402
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

_PIPELINE_EUR_PER_MW_PER_KM: float | None = _parse_pipeline_km_cost(_SCENARIO)


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


# Pre-Phase-3 dynamic recompute of FUEL_ADDER_2050["ch4_ccgt"/"ch4_ocgt"] on
# _co2NNN — REMOVED. The CO₂ tax now layers onto the natural_gas bus price
# via natural_gas_import_price(), so _co2NNN flows through automatically.


_NG_PRICE_OVERRIDE_EUR_PER_MWH_TH:  float | None = _parse_ng_price_override(_SCENARIO)
_OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH: float | None = _parse_oil_price_override(_SCENARIO)
_FUEL_RAMP_PCT_OVERRIDE:            float | None = _parse_fuel_ramp_override(_SCENARIO)
