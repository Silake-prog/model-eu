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
    "ch4_ccgt": 125.0,      # 40/0.58 + 0.37×150 ≈ 125 EUR/MWh
    "ch4_ocgt": 170.0,      # 40/0.38 + 0.56×150, conservative peaker
    "coal": 45.0,           # Legacy fuel cost only (investment blocked by lifetime=1yr)
    "waste": 5.0,           # Minimal fuel cost (waste-to-energy)
    "biomass_coge": 35.0,   # Biomass procurement cost (~35 EUR/MWh_th / 100% biogenic)
    "h2_ccgt": 150.0,       # Green H₂ at ~2.5 EUR/kg (IEA Net Zero 2050 target)
}

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
# Caps are set as plausible maximum deployment, not targets. The optimiser
# will invest less if economically justified.
BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY: dict[str, dict[str, float]] = {
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
DEFAULT_BESS_POWER_INVESTMENT_MAX_MW: dict[str, float] = {
    "Battery_1h": 5_000.0,
    "Battery_4h": 10_000.0,
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
}

# Country-specific headroom overrides (MW).
# Only relevant when CLEVER declares non-zero energy for the technology.
EXPANSION_HEADROOM_BY_COUNTRY: dict[str, dict[str, float]] = {
    "DE": {"Gas": 10_000.0, "Hydrogen_power_plant": 40_000.0},
    "FR": {"Gas": 10_000.0, "Hydrogen_power_plant": 10_000.0},
    "ES": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0},
    "GB": {"Gas": 10_000.0, "Hydrogen_power_plant": 15_000.0},
    "IT": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0},
    "NL": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0},
    "BE": {"Gas":  3_000.0, "Hydrogen_power_plant":  5_000.0},
    "CH": {"Gas":  2_000.0, "Hydrogen_power_plant":  3_000.0},
    "AT": {"Gas":  2_000.0, "Hydrogen_power_plant":  3_000.0},
}

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

DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: float = 0.15

# Country-specific overrides (same semantics: fraction of CLEVER capacity).
VRE_EXPANSION_HEADROOM_BY_COUNTRY: dict[str, dict[str, float]] = {
    "DE": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "FR": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "ES": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "IT": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "GB": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
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
