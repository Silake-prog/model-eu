"""pommes_eur.data._inputs_generic — dataset-agnostic model input scalars.

Physics/chemistry constants, numerical guards, unit scalars, default profiles and generic
model-config defaults — nothing tied to the CLEVER case study. Split verbatim out of
data/inputs.py (values byte-identical, guarded by tests/golden); re-exported via the
data.inputs facade. No numerics changed.
"""
from __future__ import annotations

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


# ── Methane-leakage pricing + CCS deployment cap (2026-06-11) ───────────────
# CH4 leakage is a real GHG cost the CO2-combustion adder misses. It is priced
# UNREBATED (CCS cannot capture upstream/fugitive losses) and scales with
# resolved_carbon_price(), which is what makes carbon pricing differentiate
# green from blue H2 again (the 90% capture rebate otherwise shields blue).
CH4_KG_PER_MWH_TH: float = 71.94       # 1 MWh_th LHV / 13.9 kWh/kg


CH4_GWP100_FOSSIL: float = 29.8        # IPCC AR6 (fossil methane)


CH4_GWP100_BIOGENIC: float = 27.0      # IPCC AR6 (biogenic methane)


# ═══════════════════════════════════════════════════════════════════
# 1. COUNTRY MAPPING
# ═══════════════════════════════════════════════════════════════════
AREA_MAP: dict[str, str] = {
    "EL": "GR",
    "UK": "GB",
}


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


# ═══════════════════════════════════════════════════════════════════
# 4.1 HYDRO VARIABLE COSTS (EUR/MWh)
# ═══════════════════════════════════════════════════════════════════
ROR_HYDRO_VARIABLE_COST: float = 5.0


RESERVOIR_HYDRO_VARIABLE_COST: float = 7.0


PUMPED_HYDRO_ROUNDTRIP_EFF: float = 0.80


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
# DemandForge conventions
# ═══════════════════════════════════════════════════════════════════
HOURS_PER_YEAR: float = 8760.0


TWH_TO_MWH: float = 1e6


# Default weather reference year for SupplyForge profiles
DEFAULT_WEATHER_REF_YEAR: int = 2021


# Solver defaults
PRICE_NUMERICAL_TOL: float = 1e-9

