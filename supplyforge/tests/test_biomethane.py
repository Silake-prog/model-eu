"""Tests for the ENSPRESO biomethane layer: feedstock scopes, availability, and the grid_model chain.

The availability tests that need the 15.7 MB ENSPRESO workbook are skipped when it isn't cached locally;
the scope/logic and the grid_model supply-chain tests run without any download.
"""
import numpy as np
import polars as pl
import pytest

from supplyforge import biomethane as B
from supplyforge import grid_model as gm

_INDEX_COLS = {"year_op", "year_inv", "year_dec", "year", "hour", "mode", "area", "resource", "name"}


def _val(x):
    if isinstance(x, pl.DataFrame):
        return float(x[[c for c in x.columns if c not in _INDEX_COLS][0]][0])
    return float(x)


# ── feedstock scopes + provisional constants (no workbook needed) ──────────

def test_feedstock_scopes_nest():
    """bioLow ⊂ bioMed ⊂ bioHigh, and each scope maps to an ENSPRESO scenario."""
    assert B.BIOLOW_CODES < B.BIOMED_CODES < B.BIOHIGH_CODES
    assert "MINBIOGAS1" in B.BIOLOW_CODES                 # manure is a Tier-1 substrate
    assert "MINBIOCRP31" in B.BIOMED_CODES - B.BIOLOW_CODES   # lignocellulosic added at bioMed
    assert {"MINBIOCRP41", "MINBIOCRP41a"} <= B.BIOHIGH_CODES - B.BIOMED_CODES  # SRC added at bioHigh
    assert set(B.DEFAULT_ENSPRESO_SCENARIO) == {"bioLow", "bioMed", "bioHigh"}


def test_provisional_yield_and_helpers():
    """The biomass→biomethane yield is a real loss in (0,1); implied CCGT is positive."""
    assert 0.0 < B.BIOMASS_TO_BIOMETHANE_YIELD < 1.0     # primary biomass != biomethane
    assert B.implied_ccgt_GW(100.0) > 0.0
    # GR/GB are model codes; ENSPRESO's EL/UK are remapped to them.
    assert "GR" in B.MODEL_AREAS and "GB" in B.MODEL_AREAS
    assert "EL" not in B.MODEL_AREAS and "UK" not in B.MODEL_AREAS


# ── availability from the real workbook (skip if not cached) ───────────────

def _workbook_available() -> bool:
    from supplyforge.fetch.enspreso import ENSPRESO_BIOMASS_FILE
    return ENSPRESO_BIOMASS_FILE.exists()


@pytest.mark.skipif(not _workbook_available(), reason="ENSPRESO workbook not cached locally")
def test_country_potentials_and_envelope():
    """Per-country potentials: biomethane < primary (yield applied); envelope monotonic in scope."""
    df = B.get_country_potentials("bioMed")
    assert {"country", "potential_TWh", "potential_PJ_primary", "cost_eur_per_MWh_th"} <= set(df.columns)
    assert 25 <= df["country"].nunique() <= 30
    # biomethane (yield-applied) is strictly below the primary-biomass energy
    prim_twh = df["potential_PJ_primary"].sum() / 3.6
    assert 0.0 < df["potential_TWh"].sum() < prim_twh
    assert {"FR", "DE"} <= set(df.nlargest(8, "potential_TWh")["country"])   # big AD/biomass countries
    # envelope rises with feedstock scope (more feedstocks → more potential)
    env = B.summarize_eu_envelope().set_index(["enspreso_scenario", "scope"])["EU_biomethane_TWh"]
    assert env[("ENS_Med", "bioLow")] < env[("ENS_Med", "bioMed")] < env[("ENS_Med", "bioHigh")]


# ── grid_model supply chain (no workbook needed) ──────────────────────────

def _bm_assumptions(atr: bool):
    return {"X": dict(demand_TWh=10.0, solar_GW=0, wind_onshore_GW=0, wind_offshore_GW=0,
                      hydro_turbine_GW=0, hydro_energy_GWh=0,
                      biomethane=dict(potential_TWh=50.0, cost_eur_per_MWh_th=20.0, atr=atr))}


def test_populate_biomethane_components_and_resources():
    """The chain creates raw_biomass→AD→CCGT(+ATR) with the right factor signs + auto-added resources."""
    model = gm.build_grid_model(["X"], 2050, _bm_assumptions(atr=True), {"X": {}}, {"X": None}, [],
                                hours=list(range(24)), name="bm_test")
    comps = model.areas["X"].components
    for n in ("X_raw_biomass_supply", "X_biomethane_plant", "X_Biomethane_CCGT", "X_ATR_biomethane"):
        assert n in comps
    # biomethane carriers are auto-enabled from the per-node config
    assert {"raw_biomass", "biomethane", "hydrogen"} <= set(model.resources)

    # factor signs: consumed negative, produced positive
    def _factor(name, res):
        f = comps[name].factor
        if isinstance(f, pl.DataFrame):
            col = [c for c in f.columns if c not in _INDEX_COLS][0]
            return float(f.filter(pl.col("resource") == res)[col][0])
        return float(f[res])
    assert _factor("X_raw_biomass_supply", "raw_biomass") == 1.0
    assert _factor("X_biomethane_plant", "biomethane") == 1.0
    assert _factor("X_biomethane_plant", "raw_biomass") < 0          # consumes raw biomass
    assert _factor("X_Biomethane_CCGT", "electricity") == 1.0
    assert _factor("X_Biomethane_CCGT", "biomethane") < 0            # consumes biomethane
    assert _factor("X_ATR_biomethane", "hydrogen") == 1.0
    # raw_biomass priced at feedstock cost; AD CAPEX present
    assert _val(comps["X_raw_biomass_supply"].variable_cost) == 20.0
    assert _val(comps["X_biomethane_plant"].invest_cost) > 0.0


def test_no_atr_when_disabled():
    """Without `atr`, the H2 route (and ATR component) is not created."""
    model = gm.build_grid_model(["X"], 2050, _bm_assumptions(atr=False), {"X": {}}, {"X": None}, [],
                                hours=list(range(24)), name="bm_noatr")
    comps = model.areas["X"].components
    assert "X_Biomethane_CCGT" in comps and "X_ATR_biomethane" not in comps


def test_no_biomethane_when_absent():
    """A node without a `biomethane` config gets no biomethane components (backward compatible)."""
    ass = {"X": dict(demand_TWh=10.0, solar_GW=5, wind_onshore_GW=0, wind_offshore_GW=0,
                     hydro_turbine_GW=0, hydro_energy_GWh=0, firm={"Gas": {"GW": 10, "cost": 80}})}
    model = gm.build_grid_model(["X"], 2050, ass, {"X": {"Solar": np.full(8760, 0.15)}}, {"X": None}, [],
                                hours=list(range(24)))
    assert not any("biomass" in n or "iomethane" in n for n in model.areas["X"].components)
    assert "raw_biomass" not in model.resources


def test_biomethane_chain_solves():
    """A tiny solve: biomethane is the only supply → the CCGT serves demand with no load-shedding."""
    model = gm.build_grid_model(["X"], 2050, _bm_assumptions(atr=True), {"X": {}}, {"X": None}, [],
                                hours=list(range(24)), name="bm_solve")
    lm = model.run(return_linopy_model=True)
    assert lm.status == "ok"
    op = model.get_results("operation", "power")
    ccgt = op.filter(pl.col("name").str.contains("Biomethane_CCGT")).select(pl.col("value").sum()).item()
    shed = op.filter(pl.col("name").str.contains("shedding")
                     & (pl.col("resource") == "electricity")).select(pl.col("value").sum()).item()
    assert (ccgt or 0) > 0.0           # CCGT dispatches biomethane
    assert (shed or 0) < 1.0           # demand met, no shedding
