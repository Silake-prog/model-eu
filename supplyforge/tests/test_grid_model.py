"""Tests for the multi-area grid builder (build-only; NTC mocked, no solve/network)."""
import numpy as np
import polars as pl
import pytest

from supplyforge import grid_model as gm


def _fake_ntc(monkeypatch, adjacency):
    """Monkeypatch get_ntc_for_country to return synthetic wide NTC frames."""
    import supplyforge.fetch.ember_ntc as e

    def fake(cc):
        d = adjacency.get(cc, {})
        return pl.DataFrame({k: [float(v)] for k, v in d.items()}) if d else pl.DataFrame()

    monkeypatch.setattr(e, "get_ntc_for_country", fake)


def test_ember_ntc_links_dedup_and_filter(monkeypatch):
    _fake_ntc(monkeypatch, {
        "FR": {"DE": 4800, "ES": 6500, "XX": 100},   # XX outside the set -> dropped
        "DE": {"FR": 4800, "PL": 2000},              # PL outside the set
        "ES": {"FR": 6500, "PT": 0},
    })
    links = gm.ember_ntc_links(["FR", "DE", "ES"])
    assert sorted((a, b) for a, b, _ in links) == [("DE", "FR"), ("ES", "FR")]   # each border once
    caps = {(a, b): mw for a, b, mw in links}
    assert caps[("DE", "FR")] == 4800.0 and caps[("ES", "FR")] == 6500.0


def test_ember_ntc_links_min_mw_drops_zero(monkeypatch):
    _fake_ntc(monkeypatch, {"FR": {"DE": 0.0}, "DE": {"FR": 0.0}})
    assert gm.ember_ntc_links(["FR", "DE"]) == []


def _inputs(countries, with_inflow):
    cf = {c: {"Solar": np.full(8760, 0.15), "Wind Onshore": np.full(8760, 0.25)} for c in countries}
    inflow = {c: (np.full(8760, 100.0) if with_inflow.get(c) else None) for c in countries}
    return cf, inflow


def _ass(countries):
    base = dict(solar_GW=10, wind_onshore_GW=10, wind_offshore_GW=0, hydro_turbine_GW=2,
                hydro_energy_GWh=1000, gas_GW=10, gas_cost=80, demand_TWh=50)
    return {c: dict(base) for c in countries}


def test_build_grid_model_areas_links_components():
    countries = ["AA", "BB"]
    cf, inflow = _inputs(countries, {"AA": True, "BB": False})
    model = gm.build_grid_model(countries, 2050, _ass(countries), cf, inflow, [("AA", "BB", 1000.0)])

    assert set(model.areas) == {"AA", "BB"}
    assert len(model.links) == 2                               # one border -> two one-way links
    aa = set(model.areas["AA"].components)
    assert {"AA_Solar", "AA_Wind_Onshore", "AA_Gas", "AA_demand", "AA_load_shedding"} <= aa
    assert "AA_lake_store" in aa                               # AA has inflow -> reservoir built
    assert "BB_lake_store" not in set(model.areas["BB"].components)   # BB has none


def test_build_grid_model_missing_assumptions_raises():
    with pytest.raises(ValueError):
        gm.build_grid_model(["AA", "ZZ"], 2050, _ass(["AA"]), {}, {}, [])


def test_build_grid_model_firm_merit_order():
    """A `firm` dict adds one cost-priced ConversionTechnology per tech; gas_GW stays as shorthand."""
    def _val(x):
        if isinstance(x, pl.DataFrame):
            return float(x[[c for c in x.columns if c != "year_op"][0]][0])
        return float(x)

    cf = {"X": {"Solar": np.full(8760, 0.15), "Wind Onshore": np.full(8760, 0.25)}}
    ass = {"X": dict(solar_GW=5, wind_onshore_GW=5, wind_offshore_GW=0, hydro_turbine_GW=0,
                     hydro_energy_GWh=0, demand_TWh=20,
                     firm={"Nuclear": {"GW": 10, "cost": 12}, "Gas": {"GW": 5, "cost": 80}})}
    comps = gm.build_grid_model(["X"], 2050, ass, cf, {"X": None}, []).areas["X"].components
    assert "X_Nuclear" in comps and "X_Gas" in comps
    assert _val(comps["X_Nuclear"].variable_cost) == 12.0        # cheap baseload dispatched first
    assert _val(comps["X_Nuclear"].power_capacity_max) == 10_000.0   # 10 GW -> MW
    assert _val(comps["X_Gas"].variable_cost) == 80.0

    # legacy gas_GW / gas_cost shorthand still builds a Gas tech
    ass2 = {"Y": dict(solar_GW=1, wind_onshore_GW=0, wind_offshore_GW=0, hydro_turbine_GW=0,
                      hydro_energy_GWh=0, demand_TWh=5, gas_GW=3, gas_cost=75)}
    comps2 = gm.build_grid_model(["Y"], 2050, ass2, {"Y": {"Solar": np.full(8760, 0.1)}},
                                 {"Y": None}, []).areas["Y"].components
    assert _val(comps2["Y_Gas"].variable_cost) == 75.0


def test_build_grid_model_greenfield_investable():
    """A `greenfield` config makes generation investable (optimiser-sized) instead of fixed.

    RE techs get a PECD-CF availability + zero fuel cost; firm techs get a variable cost; all carry
    an investment range (not a fixed power_capacity_min=max), and the fixed fleet is suppressed.
    """
    idx = {"year_op", "year_inv", "year_dec", "year", "hour", "mode", "area", "resource", "name"}

    def _val(x):
        if isinstance(x, pl.DataFrame):
            return float(x[[c for c in x.columns if c not in idx][0]][0])
        return float(x)

    cf = {"G": {"Solar": np.full(8760, 0.2), "Wind Onshore": np.full(8760, 0.3)}}
    ass = {"G": dict(demand_TWh=20, gas_GW=99,   # gas_GW shorthand must be ignored under greenfield
                     greenfield={"Solar": 30.0, "Wind_Onshore": 20.0, "Nuclear": 10.0, "Gas": 15.0})}
    comps = gm.build_grid_model(["G"], 2050, ass, cf, {"G": None}, [],
                                resources=["electricity"], hours=list(range(24))).areas["G"].components

    for t in ("G_Solar", "G_Wind_Onshore", "G_Nuclear", "G_Gas"):
        assert t in comps
        assert _val(comps[t].power_capacity_investment_max) > 0          # investable, not fixed
    # RE: availability from CF (Solar 0.2), no fuel cost; firm: priced by variable cost
    assert _val(comps["G_Solar"].variable_cost) == 0.0
    assert _val(comps["G_Nuclear"].variable_cost) > 0.0
    # greenfield suppresses the fixed gas_GW shorthand fleet (only the investable G_Gas exists)
    assert _val(comps["G_Gas"].power_capacity_investment_max) == 15_000.0
    # nuclear annuitised over its real 60-yr life is cheaper per year than a naive 25-yr annuity
    # would be: invest_cost is rescaled below the 7.5 M€/MW overnight cost.
    assert _val(comps["G_Nuclear"].invest_cost) < 7.5e6
