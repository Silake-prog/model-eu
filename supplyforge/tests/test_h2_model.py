"""Tests for the hydrogen layer of grid_model: electrolyser, H2 demand/storage/transport, demand profile.

Build-only (no solve): they assert the right pommes components and links are created. The economic
behaviour (electrolyser siting, H2 balance) is validated separately by a small solve.
"""
import numpy as np
import polars as pl

from supplyforge import grid_model as gm

_INDEX_COLS = {"year_op", "year_inv", "year_dec", "year", "hour", "mode", "area", "resource", "name"}


def _val(x):
    """Extract the scalar value from a pommes parameter (scalar or a 1-row polars frame)."""
    if isinstance(x, pl.DataFrame):
        col = [c for c in x.columns if c not in _INDEX_COLS][0]
        return float(x[col][0])
    return float(x)


def _base(**extra):
    return dict(solar_GW=5, wind_onshore_GW=0, wind_offshore_GW=0, hydro_turbine_GW=0,
                hydro_energy_GWh=0, demand_TWh=10, firm={"Gas": {"GW": 5, "cost": 80}}, **extra)


def _links(model):
    if isinstance(model.links, dict):
        return model.links
    return {getattr(link, "name", i): link for i, link in enumerate(model.links)}


def test_hydrogen_components_and_links():
    cf = {"X": {"Solar": np.full(8760, 0.15)}, "Y": {"Solar": np.full(8760, 0.15)}}
    h2 = dict(efficiency=0.7, storage=True)
    ass = {"X": _base(h2=dict(h2)), "Y": _base(h2=dict(h2, demand_MW=500.0))}
    model = gm.build_grid_model(["X", "Y"], 2050, ass, cf, {"X": None, "Y": None}, [("X", "Y", 1000.0)],
                               resources=["electricity", "hydrogen"], h2_links=[("X", "Y", 2000.0)])
    cx, cy = model.areas["X"].components, model.areas["Y"].components

    # investable electrolyser at every node (capacity is an investment decision, not fixed)
    assert "X_electrolyser" in cx and "Y_electrolyser" in cy
    assert _val(cx["X_electrolyser"].power_capacity_investment_max) > 0

    # H2 demand only where demand_MW > 0
    assert "Y_h2_demand" in cy and "X_h2_demand" not in cx

    # H2 storage (storage=True) + feasibility valves
    assert "X_h2_store" in cx and "Y_h2_store" in cy
    assert "Y_h2_shedding" in cy and "Y_h2_spillage" in cy

    # links: one electricity border (2 one-way) + one H2 border (2 one-way) = 4, with distinct H2 names
    links = _links(model)
    assert len(links) == 4
    assert any(str(n).startswith("H2_") for n in links)


def test_electrolyser_factor_signs():
    """Electrolyser consumes electricity (negative) and produces hydrogen (positive, = efficiency)."""
    model = gm.build_grid_model(["X"], 2050, {"X": _base(h2=dict(efficiency=0.6))},
                                {"X": {"Solar": np.full(8760, 0.2)}}, {"X": None}, [],
                                resources=["electricity", "hydrogen"])
    el = model.areas["X"].components["X_electrolyser"]
    f = el.factor
    if isinstance(f, pl.DataFrame):
        f = {r["resource"]: r[[c for c in f.columns if c not in _INDEX_COLS][0]] for r in f.iter_rows(named=True)}
    assert f["electricity"] == -1.0 and abs(f["hydrogen"] - 0.6) < 1e-9
    # no H2 demand / storage when not configured
    assert "X_h2_demand" not in model.areas["X"].components
    assert "X_h2_store" not in model.areas["X"].components


def test_no_hydrogen_when_absent():
    """Without an h2 config, no electrolyser / H2 components are created (backward compatible)."""
    model = gm.build_grid_model(["X"], 2050, {"X": _base()}, {"X": {"Solar": np.full(8760, 0.2)}},
                                {"X": None}, [])
    comps = model.areas["X"].components
    assert not any("electrolyser" in n or "h2_" in n for n in comps)


def test_demand_profile_sampled():
    """An hourly demand_profile is sampled at the modelled hours (vs a flat demand_TWh)."""
    profile = np.arange(8760, dtype=float)   # distinct value per hour
    model = gm.build_grid_model(["X"], 2050, {"X": _base(demand_profile=profile)},
                                {"X": {"Solar": np.full(8760, 0.2)}}, {"X": None}, [],
                                resources=["electricity"], hours=list(range(24)))
    d = model.areas["X"].components["X_demand"].demand
    vals = d.sort("hour")["demand"].to_list()
    assert vals[:5] == [0.0, 1.0, 2.0, 3.0, 4.0] and len(vals) == 24
