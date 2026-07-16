"""pommes_eur.costs.fuel_prices — delivered fuel-bus prices (natural gas, oil).

The commodity price (WB Pink Sheet anchor × optional geopolitical-premium ramp) plus the
CO2 adder (via costs.carbon_price) and upstream-CH4-leak adder, resolved to a delivered
€/MWh_th price on the natural_gas / oil buses. Scenario overrides (_ngPriceNNN /
_oilPriceNNN / _fuelRampNN) come from scenario.resolved. Lives next to carbon_price.py;
re-exported through the data/overrides.py facade for back-compat. Carved verbatim out of the
old overrides.py — behaviour byte-identical. No numerics changed.
"""
from __future__ import annotations

from pommes_eur.scenario.resolved import (
    _FUEL_RAMP_PCT_OVERRIDE,
    _NG_PRICE_OVERRIDE_EUR_PER_MWH_TH,
    _OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH,
    _NG_UPSTREAM_LEAK_RATE,
)
from pommes_eur.data.inputs import (
    _DEFAULT_FUEL_ANNUAL_RAMP_PCT,
    _TARGET_MODEL_YEAR,
    NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH,
    OIL_CO2_INTENSITY_T_PER_MWH_TH,
    CH4_KG_PER_MWH_TH,
    CH4_GWP100_FOSSIL,
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
