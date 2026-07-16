"""
clever.biomethane — ENSPRESO biomass loader + per-country biomethane potentials.

Pipeline:
  fetch_enspreso_biomass()  ──>  ENSPRESO_BIOMASS.xlsx
                                       │
                                       ▼
                    load_enspreso_workbook()  (cached)
                                       │
                                       ▼
            get_country_potentials(scope, scenario)  ─>  per-country DataFrame
                                       │
                                       ▼
                    summarize_eu_envelope()  ─>  6-cell summary

Feedstock scopes:
- bioLow   = manure + agri waste + landscape + municipal + sludge (Tier 1)
- bioMed   = bioLow + miscanthus/switchgrass/RCG  (Tier 1 + lignocellulosic)
- bioHigh  = bioMed + short-rotation woody crops (Willow + Poplar; Tier 3)

ENSPRESO availability scenarios pair with scope by convention:
- bioLow   → ENS_Low    (~ 476 TWh_th EU-27 in 2050)
- bioMed   → ENS_Med    (~1354 TWh_th)
- bioHigh  → ENS_High   (~2299 TWh_th)

Country-code conventions:
- ENSPRESO uses EL (Greece), UK (United Kingdom)
- Model uses GR, GB
- All returned DataFrames use MODEL codes
"""

from __future__ import annotations
import functools
import logging
from pathlib import Path
from typing import Literal

import pandas as pd

from pommes_eur.fetch import fetch_enspreso_biomass

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────

# Tier-1 biomethane feedstocks: waste / residue / by-product only
# (no land-use competition, no contested categories)
BIOLOW_CODES: frozenset[str] = frozenset({
    "MINBIOGAS1",     # Manure (solid + liquid)         — primary AD substrate
    "MINBIOAGRW1",    # Agricultural waste              — residues
    "MINBIOFRSR1a",   # Landscape-care residues
    "MINBIOMUN1",     # Municipal waste                 — biogas-via-AD or landfill
    "MINBIOSLU1",     # Sewage sludge                   — wastewater treatment
})

# Tier-2 adds lignocellulosic AD substrate (grass + perennials).
# Still defensible as "biomethane" but at the contested edge.
BIOMED_CODES: frozenset[str] = BIOLOW_CODES | frozenset({
    "MINBIOCRP31",    # Miscanthus, switchgrass, RCG
})

# Tier-3 adds short-rotation woody crops (SRC): willow + poplar.
# Suitable for biomethane via thermal gasification + methanation. Forest
# residues (MINBIOFRSR1, MINBIOWOOW1) are deliberately excluded — their
# biomethane pathway requires advanced gasification at TRL 4-5 by 2050 at
# scales not yet demonstrated commercially, and they are more typically
# used for solid-biomass combustion in district heating / power.
BIOHIGH_CODES: frozenset[str] = BIOMED_CODES | frozenset({
    "MINBIOCRP41",    # Willow      (short-rotation coppice)
    "MINBIOCRP41a",   # Poplar      (short-rotation coppice)
})

SCOPE_CODES: dict[str, frozenset[str]] = {
    "bioLow":  BIOLOW_CODES,
    "bioMed":  BIOMED_CODES,
    "bioHigh": BIOHIGH_CODES,
}

# ENSPRESO availability axis. Pair these with feedstock scope by convention:
# - "bioLow"  → "ENS_Low"   (sufficiency / CLEVER worldview)
# - "bioMed"  → "ENS_Med"   (mainstream / policy worldview)
# - "bioHigh" → "ENS_High"  (upper-credible-bound / DECARB HIGH worldview)
DEFAULT_ENSPRESO_SCENARIO: dict[str, str] = {
    "bioLow":  "ENS_Low",
    "bioMed":  "ENS_Med",
    "bioHigh": "ENS_High",
}

# ENSPRESO ↔ model country codes
_ENSPRESO_TO_MODEL: dict[str, str] = {"EL": "GR", "UK": "GB"}
_MODEL_TO_ENSPRESO: dict[str, str] = {v: k for k, v in _ENSPRESO_TO_MODEL.items()}

# The 30 model areas (= EU27 + UK + NO + CH) — in MODEL codes
MODEL_AREAS: frozenset[str] = frozenset({
    "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES",
    "FI", "FR", "GB", "GR", "HR", "HU", "IE", "IT", "LT", "LU",
    "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
})

# Unit conversion
_PJ_TO_TWH: float = 0.2778           # 1 PJ = 277.78 GWh = 0.27778 TWh
_GJ_TO_MWH: float = 1.0 / 3.6        # 1 GJ = 1/3.6 MWh ≈ 0.2778
_EURO2010_TO_2024_INFLATION: float = 1.40   # rough CPI uplift; configurable downstream

# Sheet names in ENSPRESO_BIOMASS.xlsx (frozen API contract)
_SHEET_POTENTIAL: str = "ENER - NUTS0 EnergyCom"
_SHEET_COST: str     = "COST - NUTS0 EnergyCom"
_SHEET_GLOSSARY: str = "Glossary"

# The cost column has a trailing space in the source — pinned here so we always strip
_COST_COL_RAW: str   = "NUTS0 Energy Commodity Cost "
_COST_COL: str       = "cost_eur2010_per_GJ"


# ──────────────────────────────────────────────────────────────────────────
# LOAD
# ──────────────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def load_enspreso_workbook(path: Path | str | None = None) -> dict[str, pd.DataFrame]:
    """Load (and cache) key ENSPRESO sheets. Auto-fetches the xlsx if missing.

    Returns a dict with keys ``potential``, ``cost``, ``glossary``.
    All DataFrames have:
      - Country code converted to MODEL convention (EL → GR, UK → GB)
      - Filtered to the 30 model areas
      - Sensible column names (no trailing whitespace)
    """
    if path is None:
        path = fetch_enspreso_biomass()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"ENSPRESO workbook not found at {path}")

    logger.info("Loading ENSPRESO workbook from %s", path)

    pot = pd.read_excel(path, sheet_name=_SHEET_POTENTIAL, header=0)
    cost = pd.read_excel(path, sheet_name=_SHEET_COST, header=0)
    gloss = pd.read_excel(path, sheet_name=_SHEET_GLOSSARY, header=1)

    # Strip whitespace from cost column name + rename
    cost = cost.rename(columns={_COST_COL_RAW: _COST_COL})

    # Map ENSPRESO → model codes + filter to 30 model areas
    pot["country"]  = pot["NUTS0"].map(lambda c: _ENSPRESO_TO_MODEL.get(c, c))
    cost["country"] = cost["NUTS0"].map(lambda c: _ENSPRESO_TO_MODEL.get(c, c))
    pot  = pot[pot["country"].isin(MODEL_AREAS)].reset_index(drop=True)
    cost = cost[cost["country"].isin(MODEL_AREAS)].reset_index(drop=True)

    return {"potential": pot, "cost": cost, "glossary": gloss}


# ──────────────────────────────────────────────────────────────────────────
# QUERY
# ──────────────────────────────────────────────────────────────────────────

ScopeName = Literal["bioLow", "bioMed", "bioHigh"]


def get_country_potentials(
    scope: ScopeName = "bioMed",
    enspreso_scenario: str | None = None,
    year: int = 2050,
    wb: dict[str, pd.DataFrame] | None = None,
    eur_year: Literal[2010, 2024] = 2024,
) -> pd.DataFrame:
    """Per-country biomethane potential + volume-weighted cost.

    Parameters
    ----------
    scope : "bioLow" | "bioMed" | "bioHigh"
        Which feedstock subset.
    enspreso_scenario : str, optional
        Which ENSPRESO availability scenario. Default follows scope:
        bioLow → ENS_Low, bioMed → ENS_Med, bioHigh → ENS_High.
    year : int
        Year for which to query potential (default 2050).
    wb : dict, optional
        Pre-loaded workbook (else load_enspreso_workbook is called).
    eur_year : 2010 | 2024
        Output currency vintage (defaults to 2024, applies CPI uplift).

    Returns
    -------
    DataFrame with columns:
      country, potential_TWh_th, potential_PJ, cost_eur_per_MWh_th, n_feedstocks
    Sorted descending by potential_TWh_th.
    """
    if wb is None:
        wb = load_enspreso_workbook()
    if enspreso_scenario is None:
        enspreso_scenario = DEFAULT_ENSPRESO_SCENARIO[scope]

    codes = SCOPE_CODES[scope]
    pot = wb["potential"]
    cost = wb["cost"]

    sub_pot = pot[
        (pot["Year"] == year)
        & (pot["Scenario"] == enspreso_scenario)
        & (pot["Energy Commodity"].isin(codes))
    ].copy()
    sub_cost = cost[
        (cost["Year"] == year)
        & (cost["Scenario"] == enspreso_scenario)
        & (cost["Energy Commodity"].isin(codes))
    ].copy()

    # Volume-weighted cost per country
    merged = sub_pot.merge(
        sub_cost[["country", "Energy Commodity", _COST_COL]],
        on=["country", "Energy Commodity"], how="left",
    )
    merged["cost_eur2010_per_GJ"] = merged[_COST_COL].fillna(0.0)
    merged["weighted_cost_x_pj"] = merged["Value"] * merged["cost_eur2010_per_GJ"]

    grouped = merged.groupby("country").agg(
        potential_PJ=("Value", "sum"),
        weighted_cost_sum=("weighted_cost_x_pj", "sum"),
        n_feedstocks=("Energy Commodity", "nunique"),
    ).reset_index()
    grouped["potential_TWh_th"] = grouped["potential_PJ"] * _PJ_TO_TWH
    # Weighted cost: Σ(potential × cost) / Σ(potential) in Euro2010/GJ
    grouped["cost_eur2010_per_GJ"] = (
        grouped["weighted_cost_sum"] / grouped["potential_PJ"].where(grouped["potential_PJ"] > 0)
    ).fillna(0.0)
    # Convert to €/MWh_th
    grouped["cost_eur2010_per_MWh_th"] = grouped["cost_eur2010_per_GJ"] * 3.6
    if eur_year == 2024:
        grouped["cost_eur_per_MWh_th"] = grouped["cost_eur2010_per_MWh_th"] * _EURO2010_TO_2024_INFLATION
    else:
        grouped["cost_eur_per_MWh_th"] = grouped["cost_eur2010_per_MWh_th"]

    out = grouped[
        ["country", "potential_TWh_th", "potential_PJ", "cost_eur_per_MWh_th", "n_feedstocks"]
    ].sort_values("potential_TWh_th", ascending=False).reset_index(drop=True)
    out.attrs["scope"] = scope
    out.attrs["enspreso_scenario"] = enspreso_scenario
    out.attrs["year"] = year
    out.attrs["eur_year"] = eur_year
    return out


def summarize_eu_envelope(
    year: int = 2050,
    wb: dict[str, pd.DataFrame] | None = None,
    eur_year: Literal[2010, 2024] = 2024,
) -> pd.DataFrame:
    """Envelope: per (scope × ENSPRESO scenario), the EU-wide total."""
    if wb is None:
        wb = load_enspreso_workbook()
    rows = []
    for scen in ("ENS_Low", "ENS_Med", "ENS_High"):
        for scope in ("bioLow", "bioMed", "bioHigh"):
            df = get_country_potentials(
                scope=scope, enspreso_scenario=scen, year=year, wb=wb, eur_year=eur_year
            )
            total_pj = float(df["potential_PJ"].sum())
            total_twh = float(df["potential_TWh_th"].sum())
            weighted_cost = (
                float((df["potential_TWh_th"] * df["cost_eur_per_MWh_th"]).sum() / total_twh)
                if total_twh > 0 else 0.0
            )
            rows.append({
                "enspreso_scenario": scen,
                "scope":             scope,
                "EU_potential_TWh_th": total_twh,
                "EU_potential_PJ":     total_pj,
                "EU_weighted_cost_eur_per_MWh_th": weighted_cost,
                "n_countries_with_supply": int((df["potential_TWh_th"] > 0.001).sum()),
            })
    return pd.DataFrame(rows)


def implied_ccgt_GW(potential_TWh_th: float,
                    ccgt_efficiency: float = 0.58,
                    capacity_factor: float = 0.80) -> float:
    """Convert biomethane potential (TWh thermal/yr) into the equivalent
    nameplate CCGT capacity at a target capacity factor.
    """
    twh_e = potential_TWh_th * ccgt_efficiency
    mwh_per_mw_yr = 8760.0 * capacity_factor
    return twh_e * 1e6 / mwh_per_mw_yr / 1000.0   # GW


# ──────────────────────────────────────────────────────────────────────────
# CLI: python -m clever.biomethane  →  Option A 6-cell envelope
# ──────────────────────────────────────────────────────────────────────────

def print_eu_envelope(year: int = 2050) -> None:
    """Print the 6-cell envelope to stdout."""
    df = summarize_eu_envelope(year=year)
    print(f"=== ENSPRESO biomethane envelope, year {year} ===")
    print(df.to_string(index=False))
    print()
    print(f"=== Implied CCGT nameplate (58% eff, 80% CF) ===")
    for _, r in df.iterrows():
        gw = implied_ccgt_GW(r["EU_potential_TWh_th"])
        print(f"  {r['enspreso_scenario']:10s} × {r['scope']:7s}: "
              f"{r['EU_potential_TWh_th']:6.0f} TWh_th  →  {gw:6.1f} GW CCGT  "
              f"(weighted cost {r['EU_weighted_cost_eur_per_MWh_th']:5.1f} €/MWh_th)")


# ──────────────────────────────────────────────────────────────────────────
# MODEL INTEGRATION — to be called from clever.model._add_country_components
# ──────────────────────────────────────────────────────────────────────────

# Sizing assumptions (tunable here, surfaced to article methodology section)
_AD_PLANT_CF: float          = 0.90    # AD plants run continuously
_BIOMETHANE_CCGT_CF: float   = 0.80    # design CF for CCGT capacity sizing
_ATR_CF: float               = 0.80    # design CF for ATR sizing
_CCGT_EFF: float             = 0.58    # MWh_electricity per MWh_th biomethane
_ATR_EFF: float              = 0.75    # MWh_H2 per MWh_th biomethane

# Biomass -> biomethane conversion efficiency (MWh biomethane per MWh PRIMARY biomass).
# ENSPRESO 'ENER' potentials are TECHNICAL PRIMARY-biomass energy (PJ of feedstock), NOT biomethane
# output -- confirmed against the workbook INFO sheet ("technical potentials ... for biomass",
# JRC-EU-TIMES bioenergy potentials) and the Glossary (commodities = manure, willow, ... in PJ).
# The biomass->biomethane step therefore has a real loss that MUST be applied; the legacy 1:1
# ("energy-preserving") was an error that ~doubled the biomethane envelope and halved its cost.
# Typical efficiencies: anaerobic digestion ~0.50-0.60, thermal gasification+methanation ~0.60-0.70;
# Per MWh biomethane (TYNDP2026): 75% TOTAL energy efficiency = biomethane / (biomass + electricity).
# Total input = 1/0.75 = 1.333 MWh; digester + upgrading ELECTRICITY = 0.027 MWh (drawn from the grid,
# factor entry below); so PRIMARY biomass = 1.333 - 0.027 = 1.31 MWh. (Legacy bug treated ENSPRESO
# primary biomass 1:1 as biomethane.) REVIEW / refine if TYNDP numbers change.
_BIOMASS_PER_BIOMETHANE_MWH: float = 1.31    # MWh PRIMARY biomass per MWh biomethane
_ELEC_PER_BIOMETHANE_MWH: float    = 0.027   # MWh electricity per MWh biomethane (digester + upgrading)
_BIOMASS_TO_BIOMETHANE_EFF: float  = 1.0 / _BIOMASS_PER_BIOMETHANE_MWH  # =0.763 biomass->biomethane yield (sizing)

# Tech parameters from EOLES
_AD_CAPEX_EUR_PER_KW: float  = 2875.0  # methanization plant overnight, €/kW_th output
_AD_FOM_EUR_PER_KW_YR: float = 117.875 # methanization plant FOM
_AD_LIFE_YR: int             = 25
_CCGT_CAPEX_EUR_PER_KW: float = 1015.0  # standard CCGT overnight, €/kW_e
_CCGT_FOM_EUR_PER_KW_YR: float = 47.0
_CCGT_VOM_EUR_PER_MWH: float  = 6.0
_CCGT_LIFE_YR: int            = 30
_ATR_CAPEX_EUR_PER_KW: float = 700.0   # ATR plant overnight, €/kW_H2 output
_ATR_FOM_EUR_PER_KW_YR: float = 35.0
_ATR_VOM_EUR_PER_MWH: float  = 5.0
_ATR_LIFE_YR: int            = 25
_DISCOUNT_RATE: float        = 0.04

# CSV path resolution (works on inari + local)
def _csv_path_for(scope: str, ens_scen: str, ws: Path | None = None) -> Path:
    if ws is None:
        ws = Path("/diskdata/cired/brigode/clever-work") if Path("/diskdata/cired/brigode/clever-work").exists() \
             else Path.home() / "Desktop" / "clever-work"
    return ws / "tables" / "enspreso" / f"potentials_{scope}_{ens_scen}_2050.csv"


def _load_country_potentials_cached(scope: str, ens_scen: str, ws: Path | None = None) -> pd.DataFrame:
    """Load per-country potentials CSV; generate on miss.

    Primary path: cached CSV at tables/enspreso/potentials_{scope}_{ens}_2050.csv
    (faster, audit-friendly, can be inspected/edited by hand).

    Fallback: compute via get_country_potentials() from the cached ENSPRESO
    workbook and write the CSV for next time. Lets new tiers like bioHigh
    work first-run without requiring notebooks/enspreso_biomethane_exploration.ipynb.
    """
    csv = _csv_path_for(scope, ens_scen, ws)
    if csv.exists():
        return pd.read_csv(csv)

    logger.info(
        "potentials CSV missing at %s — generating from workbook (scope=%s, ens=%s)",
        csv, scope, ens_scen,
    )
    df = get_country_potentials(
        scope=scope,  # type: ignore[arg-type]
        enspreso_scenario=ens_scen,
        year=2050,
    )
    csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv, index=False)
    return df


def add_biomethane_to_area(
    area,
    country_code: str,
    ws: Path | None = None,
) -> None:
    """Add the biomethane supply chain for one country (Phase 1.7).

    Reads SCENARIO from CLEVER_SCENARIO env var via clever.constants module-level
    parsers (_BIOMETHANE_SCOPE, _ATR_ENABLED). No-op when biomethane scope is None.

    Phase 1.7 — three-stage supply chain (was one bundled tech per consumer):

      raw_biomass (NetImport, capped at ENSPRESO potential, priced at feedstock cost)
          │
          ▼
      biomethane_plant (ConversionTechnology, AD plant CAPEX + FOM only; this is
                        the methanization step. Capacity-investable, life 25y.
                        Factor: {"raw_biomass": -1/eff, "biomethane": +1} — applies the
                        biomass→biomethane conversion loss (_BIOMASS_TO_BIOMETHANE_EFF);
                        ENSPRESO potentials are PRIMARY biomass, not biomethane.)
          │
          ▼
      biomethane (bus) ──► Biomethane_CCGT (CCGT-only CAPEX, factor consumes biomethane → electricity)
                       ╲
                        ─► ATR_biomethane    (ATR-only CAPEX,  factor consumes biomethane → hydrogen)

    AD plant CAPEX is no longer bundled with either consumer — the LP optimises
    AD plant capacity ONCE per country, naturally shared between BioCCGT and ATR
    dispatch. This fixes the post-Phase-1 result where bundled CAPEX double-counted
    the AD plant if both routes were deployed.

    When _atr is set, both consumers are added; otherwise only BioCCGT.
    """
    # Import here (not at module top) because clever.constants reads env vars
    # at import time, and we want late binding for tests.
    from pommes_eur.constants import _BIOMETHANE_SCOPE, _ATR_ENABLED, _BIOMETHANE_ENSPRESO_SCENARIO

    if _BIOMETHANE_SCOPE is None:
        return  # scenario doesn't have biomethane

    # ── CH4 slip of the chain (2026-06-11): leaked fraction l of PRODUCED
    # biomethane. Modelled as (a) input yield-loss — inputs scale by 1/(1-l)
    # so the DELIVERED unit keeps factor +1 (pommes finds the output via
    # conversion_factor == 1, so the loss must sit on the input side) — and
    # (b) a GHG variable_cost = l/(1-l) × kgCH4/MWh × GWP100_biogenic × CO2
    # price, mirroring the (unrebated) fossil leak adder in constants.
    from pommes_eur.constants import (
        _BIO_CH4_LEAK_RATE, CH4_KG_PER_MWH_TH, CH4_GWP100_BIOGENIC,
        _TARGET_MODEL_YEAR,
    )
    from pommes_eur.carbon_price import resolved_carbon_price
    _l = _BIO_CH4_LEAK_RATE
    _gross = 1.0 / (1.0 - _l)
    biomass_per_out_mwh = _BIOMASS_PER_BIOMETHANE_MWH * _gross
    elec_per_out_mwh = _ELEC_PER_BIOMETHANE_MWH * _gross
    leak_ghg_cost = (
        _l * _gross * CH4_KG_PER_MWH_TH * CH4_GWP100_BIOGENIC
        * resolved_carbon_price(year=_TARGET_MODEL_YEAR) / 1000.0
    )
    eff_sizing = (1.0 - _l) / _BIOMASS_PER_BIOMETHANE_MWH

    # Lazy import: pommes_craft is heavy; defer error to call-time.
    from pommes_craft import ConversionTechnology  # type: ignore

    # Load per-country data
    df = _load_country_potentials_cached(_BIOMETHANE_SCOPE, _BIOMETHANE_ENSPRESO_SCENARIO, ws)
    row = df[df["country"] == country_code]
    if row.empty:
        logger.info("No biomethane potential for %s — skipping.", country_code)
        return
    potential_TWh = float(row["potential_TWh_th"].iloc[0])
    feedstock_cost_eur_per_MWh_th = float(row["cost_eur_per_MWh_th"].iloc[0])
    if potential_TWh < 0.001:
        return

    potential_mwh_th = potential_TWh * 1e6

    # ── 1. Raw biomass supply (Phase 1.7, redesigned 2026-05-27).
    # Production-only ConversionTechnology on the "raw_biomass" bus:
    # factor={"raw_biomass": +1.0} → "produces" raw_biomass at variable_cost
    # = ENSPRESO gate price, capped at the JRC potential.
    #
    # WHY NOT NetImport? In POMMES, having ANY NetImport component anywhere in
    # the model flips `add_modules['net_import']=True`, which causes the
    # net_import module to materialise *full Cartesian-product* variables over
    # (area × hour × resource × year_op) — ~7.35 M variables and ~3.68 M
    # equality constraints, regardless of which (area, resource) pairs are
    # actually traded. We previously had to add max=0 "lock" NetImports to
    # close the unintended free-import paths this created. The CT path here
    # avoids the whole expansion: ConversionTechnology variables are masked
    # by `np.isfinite(conversion_factor) * (conversion_factor != 0)` and only
    # the (raw_biomass) slot is materialised. Semantics are identical to a
    # NetImport (priced supply, capped at yearly potential, no upstream
    # emissions), but the LP is ~7 M variables smaller per non-MENA scenario.
    # See pommes/model/conversion.py:127-131,165,175,238-244,311-321.
    #
    # Sizing:
    # - max_yearly_production = ENSPRESO MWh_th cap (binds dispatch annually)
    # - power_capacity_investment_max = 5× average power (gives shape slack
    #   without leaving the planning variable unbounded)
    # - invest_cost=fixed_cost=finance_rate=0 → annuity_cost = 0
    # - life_span=5 (minimum allowed; fits any single-year_op horizon)
    avg_power_mw = potential_mwh_th / 8760.0
    rb_cap_mw = avg_power_mw * 5.0
    with area.model.context():
        area.add_component(ConversionTechnology(
            name="raw_biomass_supply",
            factor={"raw_biomass": 1.0},
            # IMPORTANT 1/2: availability=1.0 must be set explicitly. The sanitizer
            # in clever.runner.sanitize_absent_conversions runs `avail.fillna(0.0)`
            # on EVERY conversion_availability cell, including for "present" techs.
            # If we don't set availability here, it defaults to NaN → filled with
            # 0 → operational constraint power ≤ capacity × availability = 0 →
            # the CT can't dispatch ANY biomass even though capacity is built.
            # (FR HIGH smoke test 2026-05-27 caught this: bio chain stayed at 0
            # with 477 GWh of ENS + 326 hours at VoLL.)
            availability=1.0,
            must_run=0.0,
            variable_cost=feedstock_cost_eur_per_MWh_th,
            max_yearly_production=potential_mwh_th,
            # IMPORTANT 2/2: power_capacity_max must equal power_capacity_investment_max.
            # sanitize_absent_conversions classifies a tech as "absent" when both
            # cap_min AND cap_max are NaN, and zeroes its investment_max +
            # max_yearly_production. A production-only CT with only investment_max
            # set silently gets disabled — same FR smoke test exposed this.
            power_capacity_max=rb_cap_mw,
            power_capacity_investment_max=rb_cap_mw,
            invest_cost=0.0,
            fixed_cost=0.0,
            finance_rate=0.0,
            life_span=5,
        ))

    # ── 2. Methanization plant (AD): converts raw_biomass → biomethane.
    # The AD plant CAPEX + FOM is the SHARED infrastructure cost for biomethane
    # production. Its capacity (MW_th of biomethane output) is investable by the
    # LP, capped at the physical maximum derivable from country potential.
    # raw_biomass is PRIMARY biomass energy (ENSPRESO technical potential, PJ of feedstock --
    # verified vs the workbook INFO/Glossary). The AD plant converts it to biomethane via the factor
    # below: 1.31 MWh biomass + 0.027 MWh grid electricity per MWh biomethane. So deliverable
    # biomethane = potential / 1.31, and the gate cost per MWh biomethane = feedstock x 1.31.
    ad_plant_max_mw_th = potential_mwh_th * eff_sizing / (8760.0 * _AD_PLANT_CF)
    ad_capex_per_mw_th = _AD_CAPEX_EUR_PER_KW * 1000.0
    ad_fom_per_mw_th   = _AD_FOM_EUR_PER_KW_YR * 1000.0
    with area.model.context():
        area.add_component(ConversionTechnology(
            name="biomethane_plant",
            factor={"raw_biomass": -biomass_per_out_mwh,
                    "electricity": -elec_per_out_mwh,
                    "biomethane": 1.0},
            availability=1.0,
            must_run=0.0,
            # feedstock cost is on the raw_biomass bus; this is the CH4-slip
            # GHG cost only (≈2.9 €/MWh at l=1%, GWP100, CO2 150)
            variable_cost=leak_ghg_cost,
            fixed_cost=ad_fom_per_mw_th,
            invest_cost=ad_capex_per_mw_th,
            finance_rate=_DISCOUNT_RATE,
            life_span=_AD_LIFE_YR,
            power_capacity_min=0.0,
            power_capacity_max=ad_plant_max_mw_th,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=ad_plant_max_mw_th,
            early_decommissioning=True,
        ))
    logger.info(
        "Added biomethane_plant for %s: potential=%.1f TWh_th, "
        "AD max_cap=%.2f GW_th, AD capex=%.2f M€/MW_th, feedstock=%.1f €/MWh_th",
        country_code, potential_TWh,
        ad_plant_max_mw_th / 1000.0,
        ad_capex_per_mw_th / 1e6,
        feedstock_cost_eur_per_MWh_th,
    )

    # ── 3. Biomethane CCGT (consumer): CCGT-only CAPEX. AD plant CAPEX is
    # now paid via the biomethane_plant ConversionTechnology and shared with
    # ATR. Added in BOTH _noGas and non-_noGas scenarios:
    #
    #   - _noGas:        the only methane→electricity path (no flex Gas).
    #   - non-_noGas:    coexists with the fuel-flex `Gas` CombinedTechnology.
    #                    The two represent different real-world planning
    #                    constraints — Biomethane_CCGT's headroom is the
    #                    PHYSICAL ENSPRESO bio envelope; flex Gas's is a
    #                    POLITICAL fossil-permitting ceiling. They're
    #                    orthogonal so the LP just optimises across both
    #                    (v10c showed that merging them under flex Gas's
    #                    political 10 GW cap loses 14.6 GW of bio-CCGT
    #                    peaking headroom → adequacy degrades). Keep both.
    max_yearly_mwh_e      = potential_mwh_th * eff_sizing * _CCGT_EFF
    power_capacity_max_mw = max_yearly_mwh_e / (8760.0 * _BIOMETHANE_CCGT_CF)
    ccgt_capex_per_mw_e   = _CCGT_CAPEX_EUR_PER_KW * 1000.0
    ccgt_fom_per_mw_e     = _CCGT_FOM_EUR_PER_KW_YR * 1000.0
    with area.model.context():
        area.add_component(ConversionTechnology(
            name="Biomethane_CCGT",
            factor={"electricity": 1.0, "biomethane": -1.0 / _CCGT_EFF},
            availability=1.0,
            must_run=0.0,
            variable_cost=_CCGT_VOM_EUR_PER_MWH,
            fixed_cost=ccgt_fom_per_mw_e,
            invest_cost=ccgt_capex_per_mw_e,
            finance_rate=_DISCOUNT_RATE,
            life_span=_CCGT_LIFE_YR,
            power_capacity_min=0.0,
            power_capacity_max=power_capacity_max_mw,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=power_capacity_max_mw,
            early_decommissioning=True,
        ))
    logger.info(
        "Added Biomethane_CCGT for %s: max_cap=%.2f GW_e, capex=%.2f M€/MW_e "
        "(CCGT only, AD plant unbundled), VOM=%.1f €/MWh_e",
        country_code,
        power_capacity_max_mw / 1000.0,
        ccgt_capex_per_mw_e / 1e6,
        _CCGT_VOM_EUR_PER_MWH,
    )

    if not _ATR_ENABLED:
        return

    # ── 4. ATR (consumer, optional): ATR-only CAPEX. Same AD plant unbundling.
    atr_max_yearly_mwh_h2 = potential_mwh_th * eff_sizing * _ATR_EFF
    atr_power_max_mw      = atr_max_yearly_mwh_h2 / (8760.0 * _ATR_CF)
    atr_capex_per_mw_h2   = _ATR_CAPEX_EUR_PER_KW * 1000.0
    atr_fom_per_mw_h2     = _ATR_FOM_EUR_PER_KW_YR * 1000.0
    with area.model.context():
        area.add_component(ConversionTechnology(
            name="ATR_biomethane",
            factor={"hydrogen": 1.0, "biomethane": -1.0 / _ATR_EFF},
            availability=1.0,
            must_run=0.0,
            variable_cost=_ATR_VOM_EUR_PER_MWH,
            fixed_cost=atr_fom_per_mw_h2,
            invest_cost=atr_capex_per_mw_h2,
            finance_rate=_DISCOUNT_RATE,
            life_span=_ATR_LIFE_YR,
            power_capacity_min=0.0,
            power_capacity_max=atr_power_max_mw,
            power_capacity_investment_min=0.0,
            power_capacity_investment_max=atr_power_max_mw,
            early_decommissioning=True,
        ))
    logger.info(
        "Added ATR_biomethane for %s: max_cap=%.2f GW_H2, capex=%.2f M€/MW_H2 "
        "(ATR only, AD plant unbundled), VOM=%.1f €/MWh_H2",
        country_code,
        atr_power_max_mw / 1000.0,
        atr_capex_per_mw_h2 / 1e6,
        _ATR_VOM_EUR_PER_MWH,
    )


if __name__ == "__main__":
    print_eu_envelope()
