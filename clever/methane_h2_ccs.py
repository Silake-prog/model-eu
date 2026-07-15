"""
clever.methane_h2_ccs — Fuel-flex H₂ production via SMR-CCS and ATR-CCS.

Each tech is a `pommes_craft.CombinedTechnology` with two operating modes:

  ng_mode   :  fossil natural_gas → H₂  + 10 % uncaptured CO₂  (pays full CO₂
              tax at the natural_gas bus, gets a CCS REBATE for the 90 %
              captured fraction)
  bio_mode  :  biomethane → H₂  + 90 % NET NEGATIVE biogenic CO₂  (no CO₂
              tax at the biomethane bus, gets a BECCS CREDIT for the 90 %
              captured biogenic carbon — durable negative emissions)

A CombinedTechnology has ONE shared CAPEX/FOM/capacity and TWO LP-decision
power streams (one per mode), constrained by:
    power[ng_mode] + power[bio_mode] ≤ shared_capacity   (sum over modes)

So the LP decides hour-by-hour how to allocate the physical plant's MW
between fuels, based on relative fuel costs at the gas bus vs the biomethane
bus. That's the right physical model: an SMR/ATR plant doesn't know what
molecule it's reforming.

CO₂ accounting
--------------
The natural_gas bus already includes the full CO₂ tax via
clever.constants.natural_gas_import_price() = bare_NG + 0.202·CO₂_price.
For CCS techs we apply a per-mode VARIABLE-COST adjustment that represents
the avoided / negative CO₂:

  ng_mode  variable_cost = SMR/ATR_VOM + CCS_OPCOST − 0.90·0.202·CO₂_price/eff
            (rebate: 90 % of the CO₂ tax we already paid at the bus is
             refunded because we captured it)
  bio_mode variable_cost = SMR/ATR_VOM + CCS_OPCOST − 0.90·0.202·CO₂_price/eff
            (credit: 90 % of the biogenic CO₂ that would have returned to
             the atmosphere is now permanently stored — a real negative
             emissions service that the policy framework should reward)

→ Same numeric rebate per mode. Different climate interpretation. Same
  impact on the LP's marginal cost.

Reference parameters (sourced in article methodology section)
-------------------------------------------------------------
  Efficiency (CCS incl.):  70 %  (drops from 75-80 % no-CCS due to CCS energy)
  CCS capture rate:        90 %  (industry-standard for large-scale H₂-CCS)
  SMR CAPEX:               1 100 €/kW_H₂  (IEA WEO 2024)
  ATR CAPEX:               1 000 €/kW_H₂  (autothermal slightly cheaper than SMR)
  FOM (both):              45 €/kW/yr
  SMR VOM:                  6 €/MWh_H₂
  ATR VOM:                  5 €/MWh_H₂
  CCS OPCOST:              12 €/MWh_H₂   (capture + transport + storage)
  Life span:               25 yr
  Finance rate:             4 % (EU benchmark, consistent with CLEVER)

These techs are added per country whenever the methane bus is active
(`not _NO_GAS`) AND "hydrogen" is in the model's resource list. The
biomethane mode is only included if "biomethane" is also a resource.
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Reference parameters ────────────────────────────────────────────────────
_CCS_EFFICIENCY: float            = 0.70    # H₂ output / fuel input (incl. CCS penalty)
_CCS_CAPTURE_RATE: float          = 0.90    # 90 % of combustion CO₂ captured

_SMR_CAPEX_EUR_PER_KW: float      = 1_100.0
_ATR_CAPEX_EUR_PER_KW: float      = 1_000.0
_CCS_FOM_EUR_PER_KW_YR: float     = 45.0
_SMR_VOM_EUR_PER_MWH: float       = 6.0
_ATR_VOM_EUR_PER_MWH: float       = 5.0
_CCS_OPCOST_EUR_PER_MWH_H2: float = 12.0

_CCS_LIFE_YR: int                 = 25
_FINANCE_RATE: float              = 0.04

# Per-country investment cap on each CCS H₂ tech (MW_H₂). Generous default —
# tighten via scenario suffix later if needed.
_CCS_MAX_MW_PER_COUNTRY: float    = 50_000.0


def _ccs_carbon_adjustment_per_mwh_h2(eff: float) -> float:
    """Per-mode CCS variable_cost adjustment (€/MWh_H₂).

    Same numeric value for both ng_mode and bio_mode (the rebate amount equals
    the BECCS credit amount per captured tonne of CO₂):

        Δvar_cost = (1/eff) × NG_CO2_intensity × capture_rate × CO2_price

    Negative because it reduces variable_cost (LP gets paid the avoided /
    negative CO₂ value per MWh_H₂ dispatched).

    Scales with whichever CO₂ trajectory is active via clever.carbon_price.
    """
    from clever.constants import (
        NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH,
        _TARGET_MODEL_YEAR,
    )
    from clever.carbon_price import resolved_carbon_price

    co2_price = resolved_carbon_price(year=_TARGET_MODEL_YEAR)
    return (
        (1.0 / eff)
        * NATURAL_GAS_CO2_INTENSITY_T_PER_MWH_TH
        * _CCS_CAPTURE_RATE
        * co2_price
    )


def _build_factor_and_var_cost(
    fuel_resources_in_model: set[str],
    smr_or_atr: str,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Construct the factor and variable_cost dicts for a CCS H₂ tech.

    Returns (factor, variable_cost), each keyed by mode name. Modes are
    only included if their fuel is in the model's resource list.

    For both modes, the CCS variable_cost adjustment is the SAME numeric
    value: −0.90 × 0.202 × CO₂_price × (1/eff). The interpretation differs
    (rebate vs credit) but the cost impact in the LP is identical.
    """
    vom = _SMR_VOM_EUR_PER_MWH if smr_or_atr == "SMR" else _ATR_VOM_EUR_PER_MWH
    ccs_adj = _ccs_carbon_adjustment_per_mwh_h2(_CCS_EFFICIENCY)
    fuel_per_h2 = 1.0 / _CCS_EFFICIENCY

    factor: dict[str, dict[str, float]] = {}
    variable_cost: dict[str, float] = {}

    if "natural_gas" in fuel_resources_in_model:
        factor["ng_mode"] = {
            "hydrogen":    +1.0,
            "natural_gas": -fuel_per_h2,
        }
        variable_cost["ng_mode"] = vom + _CCS_OPCOST_EUR_PER_MWH_H2 - ccs_adj

    if "biomethane" in fuel_resources_in_model:
        factor["bio_mode"] = {
            "hydrogen":  +1.0,
            "biomethane": -fuel_per_h2,
        }
        variable_cost["bio_mode"] = vom + _CCS_OPCOST_EUR_PER_MWH_H2 - ccs_adj

    return factor, variable_cost


def add_h2_ccs_techs_to_area(area: Any, country_code: str) -> None:
    """Add SMR_CCS and ATR_CCS (CombinedTechnology, fuel-flex with CCS) to
    a country area.

    Gated by:
      - `not _NO_GAS` (the entire methane chain is deactivated together;
        same gate as the natural_gas NetImport)
      - "hydrogen" in the model resources (the techs produce H₂; pointless
        without an H₂ bus)

    At least one of (natural_gas, biomethane) must be in resources; otherwise
    the tech has no fuel mode and would error.
    """
    from clever.constants import _NO_GAS
    if _NO_GAS:
        logger.debug(
            "add_h2_ccs_techs_to_area: _NO_GAS active — skipping SMR_CCS + ATR_CCS on %s",
            country_code,
        )
        return

    resources_in_model = set(getattr(area.model, "resources", []))
    if "hydrogen" not in resources_in_model:
        logger.info(
            "add_h2_ccs_techs_to_area: 'hydrogen' not in resources for %s — skipping",
            country_code,
        )
        return

    fuel_resources = resources_in_model & {"natural_gas", "biomethane"}
    if not fuel_resources:
        logger.info(
            "add_h2_ccs_techs_to_area: neither natural_gas nor biomethane in "
            "resources for %s — skipping H₂ CCS techs",
            country_code,
        )
        return

    # ── CCS deployment cap (2026-06-11): `_ccsCapNNN` caps ATR+SMR COMBINED
    # at NNN GW_H2 per country (each tech gets half so the SUM respects it —
    # under a binding single-tech cap the LP would simply overflow into the
    # other, near-identical reformer). Framed as annual CO2-injection
    # realism: 1 GW_H2-CCS stores ~2.25 MtCO2/yr at CF~1.
    from clever.constants import _CCS_CAP_GW_PER_COUNTRY
    if _CCS_CAP_GW_PER_COUNTRY is not None:
        ccs_cap_mw = _CCS_CAP_GW_PER_COUNTRY * 1000.0 / 2.0
    else:
        ccs_cap_mw = _CCS_MAX_MW_PER_COUNTRY

    # Lazy import — pommes_craft heavy
    from pommes_craft import CombinedTechnology  # type: ignore

    # ── SMR_CCS ────────────────────────────────────────────────────────────
    smr_factor, smr_var_cost = _build_factor_and_var_cost(fuel_resources, "SMR")
    with area.model.context():
        area.add_component(CombinedTechnology(
            name="SMR_CCS",
            factor=smr_factor,
            variable_cost=smr_var_cost,
            invest_cost=_SMR_CAPEX_EUR_PER_KW * 1000.0,
            fixed_cost=_CCS_FOM_EUR_PER_KW_YR * 1000.0,
            finance_rate=_FINANCE_RATE,
            life_span=_CCS_LIFE_YR,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=ccs_cap_mw,
            early_decommissioning=True,
        ))
    logger.info(
        "Added SMR_CCS (CombinedTechnology, modes=%s) for %s: "
        "cap_max=%.1f GW_H2, capex=%.2f M€/MW_H2, eff=%.0f%%, CCS=%.0f%%, "
        "var_cost=%s €/MWh_H2",
        list(smr_factor.keys()),
        country_code,
        ccs_cap_mw / 1000.0,
        _SMR_CAPEX_EUR_PER_KW / 1e3,
        _CCS_EFFICIENCY * 100,
        _CCS_CAPTURE_RATE * 100,
        {k: f"{v:.2f}" for k, v in smr_var_cost.items()},
    )

    # ── ATR_CCS ────────────────────────────────────────────────────────────
    atr_factor, atr_var_cost = _build_factor_and_var_cost(fuel_resources, "ATR")
    with area.model.context():
        area.add_component(CombinedTechnology(
            name="ATR_CCS",
            factor=atr_factor,
            variable_cost=atr_var_cost,
            invest_cost=_ATR_CAPEX_EUR_PER_KW * 1000.0,
            fixed_cost=_CCS_FOM_EUR_PER_KW_YR * 1000.0,
            finance_rate=_FINANCE_RATE,
            life_span=_CCS_LIFE_YR,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=ccs_cap_mw,
            early_decommissioning=True,
        ))
    logger.info(
        "Added ATR_CCS (CombinedTechnology, modes=%s) for %s: "
        "cap_max=%.1f GW_H2, capex=%.2f M€/MW_H2, eff=%.0f%%, CCS=%.0f%%, "
        "var_cost=%s €/MWh_H2",
        list(atr_factor.keys()),
        country_code,
        ccs_cap_mw / 1000.0,
        _ATR_CAPEX_EUR_PER_KW / 1e3,
        _CCS_EFFICIENCY * 100,
        _CCS_CAPTURE_RATE * 100,
        {k: f"{v:.2f}" for k, v in atr_var_cost.items()},
    )
