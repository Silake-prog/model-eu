"""
clever.smr_ccs — Steam Methane Reforming with 90 % Carbon Capture & Storage.

Adds a `SMR_CCS` ConversionTechnology per country: methane → H₂ with 90 %
of combustion CO₂ captured. Wired analogously to `ATR_biomethane` in
clever/biomethane.py — same factor pattern, same dispatch logic — but draws
from the fossil natural_gas bus instead of the biomethane bus.

Why a separate module
---------------------
The entire methane chain (natural_gas NetImport, Gas/Oil CTs, and now
SMR_CCS) is gated by `not _NO_GAS`. Putting SMR-CCS in its own file keeps
the wiring clean and makes it trivial to deactivate by removing the call
site in clever/model.py if a scenario explicitly forbids fossil hydrogen.

Carbon accounting
-----------------
The natural_gas bus price (set by `natural_gas_import_price()`) already
includes the FULL combustion CO₂ tax:
    €68.5/MWh_th = €38.2 (commodity, WB Pink Sheet) + €30.3 (CO₂ @ 150)

SMR-CCS captures 90 % of that combustion CO₂, so it should not pay 90 %
of the tax. We model this via a NEGATIVE variable-cost adjustment: a
"carbon rebate" per MWh of H₂ produced. The rebate is computed at module
call time so it tracks the active CO₂ trajectory (scenarios with a
different `_co2trajNAME` or `_co2NNN` flow through naturally).

Reference numbers
-----------------
Source: IEA "The Future of Hydrogen" (2019) + IEA WEO 2024 ETP Vol. 2:
  Efficiency (CCS energy penalty included)  …  70 %
  CAPEX                                       …  1 100 €/kW_H₂
  Fixed O&M                                   …  45  €/kW/yr
  Variable O&M (SMR + reforming chem.)        …  6   €/MWh_H₂
  CCS operating cost (capture+transport+store) … 12  €/MWh_H₂  ≈ 40 €/tCO₂ × 0.30 t/MWh
  CCS capture rate                            …  90 %
  Life span                                   …  25 yrs

Resulting effective LCOE at CO₂=150 / NG=68.5 €/MWh_th delivered:
  Fuel + CO₂ tax:  1.43 × 68.5  =  97.9 €/MWh_H₂
  + SMR VOM:                          6.0
  + CCS OPCOST:                      12.0
  − CCS rebate:                     −39.0   (= 1.43 × 0.202 × 0.90 × 150)
  + Annuity + FOM @ 80 % CF:        +16.4
  ──────────────────────────────────────────
  ≈ 93.3 €/MWh_H₂ at CO₂=150  (vs ATR_biomethane ~102; competitive)
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Reference parameters (tunable here, sourced in article methodology) ────
_SMR_CCS_EFF: float                 = 0.70        # H₂ output / NG input (incl. CCS penalty)
_CCS_CAPTURE_RATE: float            = 0.90        # 90 % of combustion CO₂ captured
_SMR_CCS_CAPEX_EUR_PER_KW: float    = 1_100.0     # €/kW_H₂  (~+57 % vs ATR's 700)
_SMR_CCS_FOM_EUR_PER_KW_YR: float   = 45.0        # vs ATR 35
_SMR_VOM_EUR_PER_MWH: float         = 6.0         # vs ATR 5
_CCS_OPCOST_EUR_PER_MWH_H2: float   = 12.0        # capture+transport+store, IEA WEO 2024
_SMR_CCS_LIFE_YR: int               = 25
_DISCOUNT_RATE: float               = 0.04        # EU benchmark (consistent with ATR)

# Per-country upper bound on SMR_CCS power capacity. 50 GW_H₂ is generous
# but not unphysical for the largest member states (DE, FR, IT). Smaller
# countries are limited by their own H₂ demand + gas import infrastructure.
_SMR_CCS_MAX_MW_PER_COUNTRY: float  = 50_000.0


def _carbon_credit_per_mwh_h2() -> float:
    """Return the CO₂-tax rebate for SMR_CCS, in €/MWh_H₂ produced.

    The natural_gas bus price already includes the full CO₂ tax for
    combustion (via `clever.constants.natural_gas_import_price()`).
    SMR_CCS captures 90 % of those emissions, so we credit it for the
    avoided tax on the captured fraction.

    The rebate scales with the active CO₂ trajectory — read at call time
    via `clever.carbon_price.resolved_carbon_price()`, so scenarios with
    `_co2trajweoNZE`, `_co2300`, `_co2off`, etc. all flow through.
    """
    from clever.constants import (
        NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH,
        _TARGET_MODEL_YEAR,
    )
    from clever.carbon_price import resolved_carbon_price

    co2_price = resolved_carbon_price(year=_TARGET_MODEL_YEAR)
    # Per MWh H₂: NG consumed × intensity × capture rate × CO₂ price
    return (
        (1.0 / _SMR_CCS_EFF)
        * NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH
        * _CCS_CAPTURE_RATE
        * co2_price
    )


def add_smr_ccs_to_area(area: Any, country_code: str) -> None:
    """Add SMR_CCS to a country area if the methane bus is active.

    No-op when `_NO_GAS` (the entire methane chain is deactivated) or when
    "hydrogen" is not in the model's resource list (scenario without H₂
    demand — nothing to produce).
    """
    from clever.constants import _NO_GAS
    if _NO_GAS:
        logger.debug("add_smr_ccs_to_area: _NO_GAS active — skipping SMR_CCS on %s", country_code)
        return

    # H₂ must exist as a resource bus
    if "hydrogen" not in area.model.resources:
        logger.info(
            "add_smr_ccs_to_area: 'hydrogen' not in model resources for %s — skipping SMR_CCS",
            country_code,
        )
        return

    # Lazy import — pommes_craft is heavy; defer to call time so import
    # failures surface here rather than at module load.
    from pommes_craft import ConversionTechnology  # type: ignore

    credit = _carbon_credit_per_mwh_h2()
    effective_var_cost = (
        _SMR_VOM_EUR_PER_MWH
        + _CCS_OPCOST_EUR_PER_MWH_H2
        - credit
    )

    smr_capex_per_mw = _SMR_CCS_CAPEX_EUR_PER_KW * 1000.0
    smr_fom_per_mw   = _SMR_CCS_FOM_EUR_PER_KW_YR * 1000.0

    with area.model.context():
        area.add_component(ConversionTechnology(
            name="SMR_CCS",
            factor={"hydrogen": 1.0, "natural_gas": -1.0 / _SMR_CCS_EFF},
            availability=1.0,
            must_run=0.0,
            variable_cost=effective_var_cost,
            fixed_cost=smr_fom_per_mw,
            invest_cost=smr_capex_per_mw,
            finance_rate=_DISCOUNT_RATE,
            life_span=_SMR_CCS_LIFE_YR,
            power_capacity_min=0.0,
            power_capacity_max=_SMR_CCS_MAX_MW_PER_COUNTRY,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=_SMR_CCS_MAX_MW_PER_COUNTRY,
            early_decommissioning=True,
        ))

    logger.info(
        "Added SMR_CCS for %s: cap_max=%.1f GW_H2, capex=%.2f M€/MW_H2, "
        "eff=%.0f%%, CCS=%.0f%%, "
        "var_cost=%.2f €/MWh_H2 (= +%d VOM + %.0f CCS_OPEX − %.1f CO2_credit)",
        country_code,
        _SMR_CCS_MAX_MW_PER_COUNTRY / 1000.0,
        smr_capex_per_mw / 1e6,
        _SMR_CCS_EFF * 100,
        _CCS_CAPTURE_RATE * 100,
        effective_var_cost,
        _SMR_VOM_EUR_PER_MWH,
        _CCS_OPCOST_EUR_PER_MWH_H2,
        credit,
    )
