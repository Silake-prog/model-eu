"""Belgium NUTS-2 regionalisation of the national power fleet, demand and grid.

This is the data layer for the *regional* market-design study — the sibling of the
national `price_study` model. Where the national model treats Belgium as one zone fed
by the national `installed_capacities_BE_{year}.parquet` row, this module **sites that
same national capacity into the 11 Belgian NUTS-2 provinces** using the real fleet
(named, georeferenced plants) and documented regional renewable registries, then
reconciles every plant type back to the national total (so the regional table sums to
the national one — no capacity is invented or lost).

It also provides the two other regional ingredients the national model does not need:
- per-region **demand shares** (the load is concentrated in the Antwerp port/chemical
  cluster and Brussels, not spread by area);
- a **reduced internal network** — inter-province NTC on Belgium's 380/220 kV backbone,
  whose binding corridor is the coast→inland axis (the real Stevin/Ventilus bottleneck
  that carries North-Sea offshore wind to the load centres).

Sources for the plant siting are given inline; capacities are the ENTSO-E year-start
2023 national totals (the bucket) split by each site's nameplate. Distributed renewables
(roof-top PV, onshore wind, waste-to-energy) are split by the Flanders/Wallonia/Brussels
regional registries (VREG / CWaPE / Brugel orders of magnitude).

All shares are dimensionless and sum to 1 per plant type, so `site_capacities(year)`
is exact by construction against `installed_capacities_BE_{year}`.
"""
from __future__ import annotations

import polars as pl
import pandas as pd

from supplyforge.utils import _get_input_data_file

# ---------------------------------------------------------------------------
# The 11 Belgian NUTS-2 provinces (+ Brussels), with a short label.
# ---------------------------------------------------------------------------
BE_NUTS2 = {
    "BE10": "Brussels",
    "BE21": "Antwerpen",
    "BE22": "Limburg",
    "BE23": "Oost-Vlaanderen",
    "BE24": "Vlaams-Brabant",
    "BE25": "West-Vlaanderen",
    "BE31": "Brabant wallon",
    "BE32": "Hainaut",
    "BE33": "Liège",
    "BE34": "Luxembourg (BE)",
    "BE35": "Namur",
}
REGION = "region"  # name of the zone column produced everywhere below

# ---------------------------------------------------------------------------
# 1. Sited plants — real Belgian fleet (name, province, nameplate MW).
#    These are the big, located units; their MW set the intra-type split, then
#    the split is rescaled so the type sums to the national bucket total.
# ---------------------------------------------------------------------------
# Nuclear — two sites only: Doel on the Scheldt (Antwerpen) and Tihange on the
# Meuse (Liège). Year-start 2023 units (Doel 3 already shut 2022; Tihange 2 shut
# Jan-2023 but in the year-start installed total). [WNA / Engie Electrabel]
NUCLEAR = [
    ("Doel 1",    "BE21", 445), ("Doel 2", "BE21", 445), ("Doel 4", "BE21", 1039),
    ("Tihange 1", "BE33", 962), ("Tihange 2", "BE33", 1008), ("Tihange 3", "BE33", 1046),
]
# Gas CCGT/OCGT + industrial gas CHP — the located fleet (Elia / operator data).
GAS = [
    ("Zandvliet Power",        "BE21", 395), ("Antwerp CHP cluster", "BE21", 430),
    ("Drogenbos",              "BE24", 460), ("Vilvoorde",           "BE24", 385),
    ("Ringvaart (Gent)",       "BE23", 357), ("Knippegroen (Gent)",  "BE23", 305),
    ("Rodenhuize",             "BE23", 290),
    ("Herdersbrug (Brugge)",   "BE25", 480),
    ("Amercoeur (Roselies)",   "BE32", 451), ("Saint-Ghislain",      "BE32", 350),
    ("Marcinelle Energie",     "BE32", 413),
    ("Seraing",                "BE33", 485), ("Flémalle/Angleur",    "BE33", 250),
    ("T-Power (Tessenderlo)",  "BE22", 425),
]
# Biomass — Max Green/Rodenhuize (Gent) is the dominant unit; Les Awirs (Liège).
BIOMASS = [
    ("Max Green (Gent)", "BE23", 205), ("Les Awirs", "BE33", 80),
    ("BEE Power Gent",   "BE23", 215), ("misc CHP", "BE22", 120), ("misc CHP", "BE35", 110),
]
# Pumped hydro — Coo-Trois-Ponts (Liège) + La Plate-Taille / Eau-d'Heure (Hainaut).
PUMPED = [("Coo-Trois-Ponts", "BE33", 1164), ("La Plate-Taille", "BE32", 144)]
# Run-of-river — Meuse barrages (Liège) + Sambre/Meuse around Namur + Ardennes.
ROR = [("Meuse (Liège)", "BE33", 100), ("Namur Sambre/Meuse", "BE35", 60),
       ("Ardennes (Lux)", "BE34", 26)]

# All offshore wind is in the North-Sea concession off the West-Vlaanderen coast,
# grid-connected at Zeebrugge/Oostende → 100 % BE25.
OFFSHORE_REGION = "BE25"

# ---------------------------------------------------------------------------
# 2. Distributed generation — split by regional registries (shares sum to 1).
#    Roof-top PV is Flanders-heavy; onshore wind splits Flanders/Wallonia with
#    West-Vlaanderen + Hainaut + Liège the windiest; waste-to-energy is urban.
#    (VREG / CWaPE / Brugel regional capacity shares, rounded.)
# ---------------------------------------------------------------------------
SOLAR_SHARE = {  # ~63 % Flanders / 35 % Wallonia / 2 % Brussels
    "BE21": 0.17, "BE22": 0.13, "BE23": 0.14, "BE24": 0.10, "BE25": 0.09,
    "BE10": 0.02, "BE31": 0.04, "BE32": 0.10, "BE33": 0.09, "BE34": 0.05, "BE35": 0.07,
}
WIND_ONSHORE_SHARE = {  # West-Vlaanderen, Hainaut, Liège, Antwerpen lead
    "BE21": 0.12, "BE22": 0.10, "BE23": 0.12, "BE24": 0.06, "BE25": 0.18,
    "BE10": 0.00, "BE31": 0.04, "BE32": 0.16, "BE33": 0.12, "BE34": 0.06, "BE35": 0.04,
}
WASTE_SHARE = {  # municipal incinerators: Antwerp, Brussels, Gent, Liège, …
    "BE21": 0.22, "BE10": 0.16, "BE23": 0.16, "BE33": 0.12, "BE25": 0.10,
    "BE24": 0.08, "BE32": 0.08, "BE22": 0.04, "BE35": 0.02, "BE31": 0.01, "BE34": 0.01,
}
# Oil/Other peakers — co-located with the heavy-industry/refinery zone (Antwerp).
OIL_SHARE = {"BE21": 0.55, "BE32": 0.15, "BE33": 0.15, "BE23": 0.15}
OTHER_SHARE = {"BE21": 0.5, "BE23": 0.3, "BE33": 0.2}

# Map the national bucket plant-type column -> how it is sited.
_SITED = {  # type column -> list of (name, region, mw)
    "Nuclear": NUCLEAR, "Fossil Gas": GAS, "Biomass": BIOMASS,
    "Hydro Pumped Storage": PUMPED, "Hydro Run-of-river and poundage": ROR,
}
_DISTRIBUTED = {  # type column -> {region: share}
    "Solar": SOLAR_SHARE, "Wind Onshore": WIND_ONSHORE_SHARE, "Waste": WASTE_SHARE,
    "Fossil Oil": OIL_SHARE, "Other": OTHER_SHARE,
}


def _shares_from_plants(plants):
    """{region: share} from a [(name, region, mw)] list (shares sum to 1)."""
    s: dict[str, float] = {}
    for _, reg, mw in plants:
        s[reg] = s.get(reg, 0.0) + float(mw)
    tot = sum(s.values())
    return {r: v / tot for r, v in s.items()}


def site_capacities(year: int = 2023) -> pd.DataFrame:
    """National BE installed capacity → a NUTS-2 × plant-type table (MW).

    Reconciled by construction: for every plant type the regional rows sum to the
    national `installed_capacities_BE_{year}` value.

    Returns a tidy frame ``[region, plant_type, mw]`` (zero rows dropped).
    """
    nat = _get_input_data_file("BE", year, "installed_capacities").to_pandas().iloc[0]
    rows = []
    for ptype in nat.index:
        if ptype == "index":
            continue
        total = float(nat[ptype] or 0.0)
        if total <= 0:
            continue
        if ptype == "Wind Offshore":
            shares = {OFFSHORE_REGION: 1.0}
        elif ptype in _SITED:
            shares = _shares_from_plants(_SITED[ptype])
        elif ptype in _DISTRIBUTED:
            shares = _DISTRIBUTED[ptype]
        else:
            shares = {"BE21": 1.0}  # fallback: heavy-industry zone
        ssum = sum(shares.values())
        for reg, sh in shares.items():
            mw = total * sh / ssum
            if mw > 0.1:
                rows.append({REGION: reg, "plant_type": ptype, "mw": round(mw, 1)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Reduced internal network — inter-province NTC (MW) on the 380/220 kV grid.
#    Edges follow province adjacency; capacities scale with the real backbone.
#    The coast (BE25) reaches the system only via BE23 and BE32 — the
#    Stevin (BE25–BE23) and the planned Ventilus (BE25–BE32) corridors — so that
#    cut is the binding constraint for North-Sea offshore wind. Brussels (BE10)
#    is an enclave fed only through BE24.
# ---------------------------------------------------------------------------
# (region_a, region_b, ntc_mw) — undirected; symmetric capacity each way.
INTERNAL_LINES = [
    ("BE25", "BE23", 3000),   # Stevin axis (coast → Gent) — main wind evacuation
    ("BE25", "BE32", 1500),   # Ventilus axis (coast → Hainaut), planned reinforcement
    ("BE23", "BE21", 4000),   # Gent → Antwerp (strong 380 kV)
    ("BE23", "BE24", 3000),
    ("BE23", "BE32", 2500),
    ("BE21", "BE22", 3500),   # Antwerp → Limburg
    ("BE21", "BE24", 4000),   # Antwerp → Flemish Brabant (load core)
    ("BE22", "BE24", 2500),
    ("BE22", "BE33", 3000),   # Limburg → Liège
    ("BE24", "BE10", 2500),   # → Brussels (enclave, single feed)
    ("BE24", "BE31", 2000),
    ("BE24", "BE33", 2500),
    ("BE24", "BE32", 2500),
    ("BE31", "BE32", 1800),
    ("BE31", "BE33", 1800),
    ("BE31", "BE35", 1500),
    ("BE32", "BE35", 1800),
    ("BE33", "BE35", 1800),
    ("BE33", "BE34", 1200),   # Liège → Belgian Luxembourg (rural, weak)
    ("BE34", "BE35", 1000),
]

# Which provinces carry Belgium's *external* interconnectors (to the national
# model's neighbours). The cross-border NTC from the national study attaches here.
EXTERNAL_BORDERS = {
    "NL": ["BE21", "BE22", "BE23"],   # Netherlands: Antwerp/Limburg/Zeeland axis
    "FR": ["BE32", "BE34"],           # France: Hainaut + Belgian Luxembourg
    "DE": ["BE33"],                   # Germany: ALEGrO (Liège–Oberzier)
    "LU": ["BE34"],                   # Luxembourg
    "UK": ["BE25"],                   # Nemo Link, lands at Zeebrugge (coast)
}


def internal_network() -> pd.DataFrame:
    """The reduced internal grid as a tidy frame ``[region_a, region_b, ntc_mw]``."""
    return pd.DataFrame(INTERNAL_LINES, columns=["region_a", "region_b", "ntc_mw"])


# ---------------------------------------------------------------------------
# 4. Per-region demand shares.
#    Preferred source: demandforge's bottom-up NUTS-2 electricity demand (captures
#    the Antwerp port/chemical load). If a cached `be_nuts2_demand_shares.parquet`
#    is present it is used; otherwise a documented fallback that still puts the
#    industrial weight on Antwerp/Gent rather than spreading load by population.
# ---------------------------------------------------------------------------
# Fallback shares: residential/tertiary ~ population, plus an industrial uplift on
# the Antwerp (BE21) port/chemical cluster and the Gent (BE23) port. Rounded so as
# to sum to 1.0. Replace with demandforge `evaluate_regional_demand` when wired.
_DEMAND_SHARE_FALLBACK = {
    "BE21": 0.215, "BE10": 0.095, "BE23": 0.130, "BE24": 0.105, "BE22": 0.085,
    "BE25": 0.090, "BE33": 0.110, "BE32": 0.085, "BE31": 0.030, "BE35": 0.035,
    "BE34": 0.020,
}


def demand_shares(cache: str | None = None) -> dict[str, float]:
    """{region: share of national electricity demand}; sums to 1.

    Uses the cached demandforge NUTS-2 parquet when available, else the documented
    industry-weighted fallback.
    """
    if cache:
        try:
            df = pl.read_parquet(cache)
            col = "demand_mwh_per_yr" if "demand_mwh_per_yr" in df.columns else df.columns[-1]
            reg = "nuts2_id" if "nuts2_id" in df.columns else df.columns[0]
            agg = df.group_by(reg).agg(pl.col(col).sum()).to_pandas()
            tot = agg[col].sum()
            return {r: v / tot for r, v in zip(agg[reg], agg[col])}
        except Exception:
            pass
    s = dict(_DEMAND_SHARE_FALLBACK)
    tot = sum(s.values())
    return {r: v / tot for r, v in s.items()}
