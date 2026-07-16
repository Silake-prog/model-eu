"""pommes_eur.constants — backwards-compatible aggregation facade.

Historically the single ~100 KB source of truth; the content is now distributed across
focused modules and this facade re-exports their union so every existing
``from pommes_eur.constants import X`` (and ``from clever.constants import X``) keeps
resolving unchanged:

  * ``scenario/parse.py``   — pure scenario-string parsers.
  * ``scenario/resolved.py``— the scenario resolved to per-run scalar globals.
  * ``data/inputs.py``      — static input tables.
  * ``data/expansion.py`` + ``data/vre_limits.py`` — scenario-gated derived tables.
  * ``costs/fuel_prices.py``— delivered fuel-bus price functions.

New code should import from the specific module. Values are byte-identical (tests/golden).
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


# Fuel-price functions now live in pommes_eur/costs/fuel_prices.py
from pommes_eur.costs.fuel_prices import (  # noqa: E402
    _resolved_fuel_ramp_pct,
    _fuel_ramp_multiplier,
    natural_gas_import_price,
    oil_import_price,
)


# Scenario-gated derived tables now live in data/expansion.py + data/vre_limits.py.
# Namespace-copied (not explicit import) so conditional/scratch locals resolve
# exactly as when this code ran inline.
from pommes_eur.data import expansion as _expansion  # noqa: E402
from pommes_eur.data import vre_limits as _vre_limits  # noqa: E402
globals().update({k: v for k, v in vars(_expansion).items() if not k.startswith("__")})
globals().update({k: v for k, v in vars(_vre_limits).items() if not k.startswith("__")})
del _expansion, _vre_limits
