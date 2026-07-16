r"""
supplyforge.biomethane — ENSPRESO biomass → per-country **biomethane** availability.

Turns the JRC ENSPRESO biomass workbook (staged by :mod:`supplyforge.fetch.enspreso`) into per-country
biomethane potentials + feedstock costs, by **feedstock scope** and **ENSPRESO availability scenario**:

    fetch_enspreso_biomass()  ─>  ENSPRESO_BIOMASS.xlsx
                                       │
                                       ▼
                    load_enspreso_workbook()  (cached, structure-verified)
                                       │
                                       ▼
            get_country_potentials(scope, scenario)  ─>  per-country DataFrame
                                       │
                                       ▼
                    summarize_eu_envelope()  ─>  3×3 (scope × scenario) EU table

Feedstock scopes (nested; ENSPRESO ``MINBIO*`` commodity codes — verified against the workbook):
- bioLow  = manure + agri waste + landscape + municipal + sludge          (waste/residue only)
- bioMed  = bioLow + miscanthus/switchgrass/RCG                            (+ lignocellulosic AD)
- bioHigh = bioMed + short-rotation woody crops (willow + poplar)          (+ SRC, via gasification)

Scope ↔ ENSPRESO availability-scenario pairing (by convention; override with ``enspreso_scenario``):
- bioLow → ENS_Low ·  bioMed → ENS_Med ·  bioHigh → ENS_High

IMPORTANT — primary biomass vs biomethane. ENSPRESO ``ENER`` is **primary-biomass technical potential**
(PJ of feedstock), *not* biomethane output. We apply a biomass→biomethane conversion yield to report
**biomethane** potential. ``potential_PJ_primary`` keeps the raw primary figure (used as the raw-biomass
cap in :func:`supplyforge.grid_model._populate_biomethane`); ``cost_eur_per_MWh_th`` is the **feedstock**
gate cost per MWh of *primary* biomass.

Country codes: ENSPRESO uses EL (Greece) / UK (United Kingdom); this module returns the model codes
GR / GB.

The biomass→biomethane yield, conversion efficiencies and tech costs below are **PROVISIONAL** — a future
``industryforge`` project will own the careful process descriptions and ratios. They are isolated in one
labelled block so they can be replaced without touching the data logic or the supply-chain wiring.

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Literal

import pandas as pd

from supplyforge.fetch.enspreso import fetch_enspreso_biomass

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────
# FEEDSTOCK SCOPES  (ENSPRESO MINBIO* commodity codes — verified vs workbook)
# ──────────────────────────────────────────────────────────────────────────
BIOLOW_CODES: frozenset[str] = frozenset({
    "MINBIOGAS1",    # Manure (solid + liquid)        — primary AD substrate
    "MINBIOAGRW1",   # Agricultural waste             — residues
    "MINBIOFRSR1a",  # Landscape-care residues
    "MINBIOMUN1",    # Municipal waste                — biogas via AD / landfill
    "MINBIOSLU1",    # Sewage sludge                  — wastewater treatment
})
BIOMED_CODES: frozenset[str] = BIOLOW_CODES | frozenset({
    "MINBIOCRP31",   # Miscanthus, switchgrass, RCG   — lignocellulosic AD
})
BIOHIGH_CODES: frozenset[str] = BIOMED_CODES | frozenset({
    "MINBIOCRP41",   # Willow  (short-rotation coppice) — biomethane via gasification + methanation
    "MINBIOCRP41a",  # Poplar  (short-rotation coppice)
})
SCOPE_CODES: dict[str, frozenset[str]] = {
    "bioLow": BIOLOW_CODES, "bioMed": BIOMED_CODES, "bioHigh": BIOHIGH_CODES,
}
DEFAULT_ENSPRESO_SCENARIO: dict[str, str] = {
    "bioLow": "ENS_Low", "bioMed": "ENS_Med", "bioHigh": "ENS_High",
}
ScopeName = Literal["bioLow", "bioMed", "bioHigh"]

# ENSPRESO ↔ model country codes; the 30 modelled areas (EU27 + GB + NO + CH).
_ENSPRESO_TO_MODEL: dict[str, str] = {"EL": "GR", "UK": "GB"}
MODEL_AREAS: frozenset[str] = frozenset({
    "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR", "HR",
    "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
})

# Unit conversions
_PJ_TO_TWH: float = 1.0 / 3.6          # 1 PJ = 1/3.6 TWh ≈ 0.2778
_GJ_PER_MWH: float = 3.6               # 1 MWh = 3.6 GJ  → €/GJ × 3.6 = €/MWh
_EUR2010_TO_2024: float = 1.40         # rough CPI uplift (provisional)

# Workbook contract (verified against ENSPRESO_BIOMASS.xlsx, JRC)
_SHEET_POTENTIAL: str = "ENER - NUTS0 EnergyCom"
_SHEET_COST: str = "COST - NUTS0 EnergyCom"
_SHEET_GLOSSARY: str = "Glossary"
_COST_COL_RAW: str = "NUTS0 Energy Commodity Cost "   # trailing space is in the source
_COST_COL: str = "cost_eur2010_per_GJ"

# ──────────────────────────────────────────────────────────────────────────
# ░░ PROVISIONAL PROCESS PARAMETERS — to be owned by the future `industryforge` ░░
# These conversion yields / efficiencies / costs are PLACEHOLDERS seeded from CLEVER + EOLES. They are
# deliberately isolated here so industryforge can replace them without touching the availability logic
# or the supply-chain wiring. DO NOT scatter process numbers elsewhere — add them here.
# ──────────────────────────────────────────────────────────────────────────
# Biomass → biomethane: ENER is PRIMARY biomass; deliverable biomethane = primary × YIELD.
BIOMASS_TO_BIOMETHANE_YIELD: float = 0.763   # MWh biomethane per MWh primary biomass (≈ 1/1.31)
ELEC_PER_BIOMETHANE_MWH: float = 0.027       # grid electricity per MWh biomethane (digester + upgrading)
# Downstream conversion efficiencies
CCGT_EFF: float = 0.58                        # MWh_e per MWh biomethane
ATR_EFF: float = 0.75                         # MWh_H2 per MWh biomethane
# Design capacity factors (for sizing investment-max headroom)
AD_PLANT_CF: float = 0.90
CCGT_CF: float = 0.80
ATR_CF: float = 0.80
# Overnight CAPEX / FOM / VOM / life (provisional)
AD_CAPEX_EUR_PER_KW: float = 2875.0          # methanisation plant, €/kW_th biomethane output
AD_FOM_EUR_PER_KW_YR: float = 117.875
AD_LIFE_YR: int = 25
CCGT_CAPEX_EUR_PER_KW: float = 1015.0        # €/kW_e
CCGT_FOM_EUR_PER_KW_YR: float = 47.0
CCGT_VOM_EUR_PER_MWH: float = 6.0
CCGT_LIFE_YR: int = 30
ATR_CAPEX_EUR_PER_KW: float = 700.0          # €/kW_H2
ATR_FOM_EUR_PER_KW_YR: float = 35.0
ATR_VOM_EUR_PER_MWH: float = 5.0
ATR_LIFE_YR: int = 25
DISCOUNT_RATE: float = 0.04
# ──────────────────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=2)
def load_enspreso_workbook(path: Path | str | None = None) -> dict[str, pd.DataFrame]:
    """Load + cache the key ENSPRESO sheets, with the structure **verified** (not assumed).

    Asserts the expected sheets/columns exist (the workbook is a frozen open-data API, but we check so a
    silent schema change fails loudly), maps ENSPRESO country codes to model codes (EL→GR, UK→GB), and
    filters to the 30 modelled areas. Returns ``{"potential", "cost", "glossary"}``.
    """
    if path is None:
        path = fetch_enspreso_biomass()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"ENSPRESO workbook not found at {path}")
    logger.info("Loading ENSPRESO workbook from %s", path)

    xl = pd.ExcelFile(path)
    for sheet in (_SHEET_POTENTIAL, _SHEET_COST):
        if sheet not in xl.sheet_names:
            raise ValueError(f"ENSPRESO workbook missing expected sheet '{sheet}'; "
                             f"sheets present: {xl.sheet_names}")

    pot = pd.read_excel(path, sheet_name=_SHEET_POTENTIAL, header=0)
    cost = pd.read_excel(path, sheet_name=_SHEET_COST, header=0)
    gloss = (pd.read_excel(path, sheet_name=_SHEET_GLOSSARY, header=1)
             if _SHEET_GLOSSARY in xl.sheet_names else pd.DataFrame())

    need_pot = {"Year", "Scenario", "NUTS0", "Energy Commodity", "Value"}
    if not need_pot.issubset(pot.columns):
        raise ValueError(f"ENSPRESO potential sheet missing columns {need_pot - set(pot.columns)}")
    if _COST_COL_RAW not in cost.columns:
        raise ValueError(f"ENSPRESO cost sheet missing cost column '{_COST_COL_RAW}'; "
                         f"columns: {list(cost.columns)}")
    units = set(pot["units"].dropna().unique()) if "units" in pot.columns else set()
    if units and units != {"PJ"}:
        logger.warning("ENSPRESO potential units are %s (expected PJ) — check conversions", units)

    cost = cost.rename(columns={_COST_COL_RAW: _COST_COL})
    for df in (pot, cost):
        df["country"] = df["NUTS0"].map(lambda c: _ENSPRESO_TO_MODEL.get(c, c))
    pot = pot[pot["country"].isin(MODEL_AREAS)].reset_index(drop=True)
    cost = cost[cost["country"].isin(MODEL_AREAS)].reset_index(drop=True)
    logger.info("ENSPRESO loaded: %d potential rows, %d cost rows, %d countries",
                len(pot), len(cost), pot["country"].nunique())
    return {"potential": pot, "cost": cost, "glossary": gloss}


def get_country_potentials(
    scope: ScopeName = "bioMed",
    enspreso_scenario: str | None = None,
    year: int = 2050,
    wb: dict[str, pd.DataFrame] | None = None,
    eur_year: Literal[2010, 2024] = 2024,
) -> pd.DataFrame:
    """Per-country **biomethane** potential + volume-weighted feedstock cost.

    Sums the scope's ENSPRESO primary-biomass commodities per country, applies
    :data:`BIOMASS_TO_BIOMETHANE_YIELD` to report deliverable biomethane, and volume-weights the
    feedstock cost (€2010/GJ → €/MWh of *primary* biomass, optional CPI uplift to 2024).

    Args:
        scope: ``"bioLow" | "bioMed" | "bioHigh"`` feedstock subset.
        enspreso_scenario: ENSPRESO availability scenario; default follows ``scope`` (see
            :data:`DEFAULT_ENSPRESO_SCENARIO`).
        year: ENSPRESO horizon year (default 2050).
        wb: pre-loaded workbook (else :func:`load_enspreso_workbook`).
        eur_year: output currency vintage (2024 applies the provisional CPI uplift).

    Returns:
        DataFrame ``[country, potential_TWh, potential_PJ_primary, cost_eur_per_MWh_th, n_feedstocks]``
        sorted by ``potential_TWh`` descending. ``potential_TWh`` is **biomethane** (yield applied);
        ``cost_eur_per_MWh_th`` is the feedstock cost per MWh of *primary* biomass.
    """
    if wb is None:
        wb = load_enspreso_workbook()
    if enspreso_scenario is None:
        enspreso_scenario = DEFAULT_ENSPRESO_SCENARIO[scope]
    codes = SCOPE_CODES[scope]

    def _filter(df: pd.DataFrame) -> pd.DataFrame:
        return df[(df["Year"] == year) & (df["Scenario"] == enspreso_scenario)
                  & (df["Energy Commodity"].isin(codes))].copy()

    sub_pot, sub_cost = _filter(wb["potential"]), _filter(wb["cost"])
    merged = sub_pot.merge(sub_cost[["country", "Energy Commodity", _COST_COL]],
                           on=["country", "Energy Commodity"], how="left")
    merged[_COST_COL] = merged[_COST_COL].fillna(0.0)
    merged["w_cost"] = merged["Value"] * merged[_COST_COL]

    g = merged.groupby("country").agg(
        potential_PJ_primary=("Value", "sum"),
        w_cost_sum=("w_cost", "sum"),
        n_feedstocks=("Energy Commodity", "nunique"),
    ).reset_index()

    # Biomethane (deliverable) = primary × yield; cost = volume-weighted feedstock €2010/GJ → €/MWh.
    g["potential_TWh"] = g["potential_PJ_primary"] * _PJ_TO_TWH * BIOMASS_TO_BIOMETHANE_YIELD
    cost_gj = (g["w_cost_sum"] / g["potential_PJ_primary"].where(g["potential_PJ_primary"] > 0)).fillna(0.0)
    cost_mwh = cost_gj * _GJ_PER_MWH
    g["cost_eur_per_MWh_th"] = cost_mwh * (_EUR2010_TO_2024 if eur_year == 2024 else 1.0)

    out = g[["country", "potential_TWh", "potential_PJ_primary", "cost_eur_per_MWh_th",
             "n_feedstocks"]].sort_values("potential_TWh", ascending=False).reset_index(drop=True)
    out.attrs.update(scope=scope, enspreso_scenario=enspreso_scenario, year=year, eur_year=eur_year)
    return out


def summarize_eu_envelope(year: int = 2050, wb: dict[str, pd.DataFrame] | None = None,
                          eur_year: Literal[2010, 2024] = 2024) -> pd.DataFrame:
    """The 3×3 (feedstock scope × ENSPRESO scenario) EU-wide biomethane envelope."""
    if wb is None:
        wb = load_enspreso_workbook()
    rows = []
    for scen in ("ENS_Low", "ENS_Med", "ENS_High"):
        for scope in ("bioLow", "bioMed", "bioHigh"):
            df = get_country_potentials(scope, scen, year, wb, eur_year)  # type: ignore[arg-type]
            twh = float(df["potential_TWh"].sum())
            wcost = (float((df["potential_TWh"] * df["cost_eur_per_MWh_th"]).sum() / twh)
                     if twh > 0 else 0.0)
            rows.append({
                "enspreso_scenario": scen, "scope": scope,
                "EU_biomethane_TWh": twh,
                "EU_primary_PJ": float(df["potential_PJ_primary"].sum()),
                "EU_weighted_feedstock_cost_eur_per_MWh_th": wcost,
                "n_countries": int((df["potential_TWh"] > 0.001).sum()),
            })
    return pd.DataFrame(rows)


def implied_ccgt_GW(potential_TWh: float, ccgt_eff: float = CCGT_EFF,
                    capacity_factor: float = CCGT_CF) -> float:
    """Equivalent nameplate CCGT capacity (GW_e) if all biomethane ran a CCGT at ``capacity_factor``."""
    twh_e = potential_TWh * ccgt_eff
    return twh_e * 1e6 / (8760.0 * capacity_factor) / 1000.0


def print_eu_envelope(year: int = 2050) -> None:
    """Print the 3×3 EU biomethane envelope + implied CCGT to stdout."""
    df = summarize_eu_envelope(year=year)
    print(f"=== ENSPRESO biomethane envelope, year {year} (biomethane = primary × "
          f"{BIOMASS_TO_BIOMETHANE_YIELD:.3f} yield) ===")
    print(df.to_string(index=False))
    print("\n=== Implied CCGT nameplate (58% eff, 80% CF) ===")
    for _, r in df.iterrows():
        print(f"  {r['enspreso_scenario']:9s} × {r['scope']:7s}: {r['EU_biomethane_TWh']:6.0f} TWh "
              f"→ {implied_ccgt_GW(r['EU_biomethane_TWh']):6.1f} GW CCGT  "
              f"(feedstock {r['EU_weighted_feedstock_cost_eur_per_MWh_th']:5.1f} €/MWh_th)")


__all__ = [
    "BIOLOW_CODES", "BIOMED_CODES", "BIOHIGH_CODES", "SCOPE_CODES", "DEFAULT_ENSPRESO_SCENARIO",
    "MODEL_AREAS", "BIOMASS_TO_BIOMETHANE_YIELD", "load_enspreso_workbook", "get_country_potentials",
    "summarize_eu_envelope", "implied_ccgt_GW", "print_eu_envelope",
]

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    print_eu_envelope()
