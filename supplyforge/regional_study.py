"""Regional (NUTS-2) electricity market-design model for Belgium.

The sibling of the national `price_study` model. Where the national model prices
Belgium as **one** zone, this prices the **11 Belgian NUTS-2 provinces** as separate
zones on a reduced internal grid, so the solve produces a *locational* price per
province and the internal flows that drive the spread. It answers the bidding-zone /
"Stevin-Ventilus congestion" question: where would a single Belgian price break apart
if the country were split, and what does internal congestion cost?

Construction (mirrors `price_study.build_current_model`, but the zones are provinces):
- capacity per province from :func:`supplyforge.regional_be.site_capacities` — the
  national fleet sited into provinces and reconciled to the national total;
- the SAME national observed capacity factors / forced-outage availability applied to
  each province (Belgium is small and weather-correlated; offshore CF only on the coast);
- demand = the national hourly load × each province's demand share;
- a reduced internal network (province-to-province NTC), and an external hub `EXT`
  priced at Belgium's real neighbours' day-ahead series (via `add_imports`), so the
  provinces import/export with the outside world up to the real border capacities.

Per-zone price is the dual of the electricity adequacy constraint (same as the national
model). Pumped storage and reservoir hydro are out of scope for this first version
(~1.3 GW, flagged); the price story is nuclear/wind/gas + the network.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import polars as pl

from supplyforge import price_study as ps
from supplyforge import regional_be as rb
from supplyforge import RESULTS_DIR
from supplyforge.utils import _get_input_data_file

logger = logging.getLogger(__name__)
H = ps.HOURS_PER_YEAR

# external border NTC (MW) to Belgium's modelled neighbours (from the national links).
_BORDER_NTC = {"NL": 5400.0, "FR": 3800.0, "DE": 1000.0, "LU": 680.0}


def _stage_region_inputs(region: str, year: int) -> None:
    """Make BE's national availability / capacity-factor files visible under a region
    code, so the proven `add_*_tech` adders (which fetch by ``area.name``) work for a
    NUTS-2 zone. Belgium is small and weather-correlated, so each province inherits the
    national observed CF / forced-outage series (offshore CF is only sited on BE25)."""
    import shutil
    for kind in ("availability", "capacity_factors"):
        src = RESULTS_DIR / kind / f"{kind}_BE_{year}.parquet"
        dst = RESULTS_DIR / kind / f"{kind}_{region}_{year}.parquet"
        if not dst.is_file() and src.is_file():
            shutil.copy(src, dst)


def _avail_frame(raw: pl.DataFrame, plant_type: str, col: str, hours, year_op,
                 is_cf: bool) -> pl.DataFrame:
    """Build a pommes availability frame (8760 h) for one plant type.

    Mirrors the adders: filter the national series for ``plant_type``, clip to [0,1]
    (CF series also get NaN/null→0 to avoid pommes reading NaN as full nameplate),
    leap-pad/trim to 8760, stamp ``hour``/``year_op``.
    """
    s = raw.filter(pl.col("plant_type") == plant_type).select(col).rename({col: "availability"})
    if is_cf:
        s = s.with_columns(pl.col("availability").fill_nan(0.0).fill_null(0.0).clip(0.0, 1.0))
    else:
        s = s.with_columns(pl.col("availability").clip(0.0, 1.0))
    if s.is_empty():
        s = pl.DataFrame({"availability": [1.0 if not is_cf else 0.0] * H})
    if s.height < H:
        s = pl.concat([s, s[-(H - s.height):]])
    elif s.height > H:
        s = s[:H]
    return s.with_columns(hour=pl.Series(list(hours)),
                          year_op=pl.lit(year_op).cast(pl.Int64))


def build_regional_be_model(cfg, scenario: dict, demand: dict, year: int = 2023,
                            external: str = "full"):
    """Build the 11-province + EXT pommes model for one scenario. Returns (em, hours).

    ``external``: "full" (EXT hub priced at neighbours + frontier links),
    "imports_only" (EXT + add_imports, no manual frontier links), or "none"
    (islanded provinces — internal network only, for isolating build issues).

    Scenario levers (optional): ``co2_eur_per_t``/``gas_eur_per_gj`` (→ SRMC),
    ``demand_scale``, ``ntc_scale`` (internal + external), ``internal_scale``
    (internal corridors only — the congestion lever), ``apply_to`` (region subset),
    ``demand_cache`` (path to a demandforge NUTS-2 demand parquet).
    """
    from supplyforge.create_pommes_craft_model import (
        add_dispatchable_tech, add_intermittent_tech, COUNTRY_BIDDING_ZONE_MAPPING,
        DISPATCHABLE_TECH_DICT, INTERMITTENT_TECH_DICT, DISPATCHABLE_RAMP_RATES,
        FIXED_COSTS, INVEST_COSTS)
    from supplyforge.utils import get_electricity_import_prices
    from supplyforge.grid_model import _add_bidirectional_link
    from pommes_craft import (EnergyModel, Area, EconomicHypothesis, TimeStepManager,
                              Demand, Spillage, LoadShedding, NetImport)

    costs = ps.dispatch_costs(ps.fuels_for_year(cfg, scenario))
    cap = rb.site_capacities(year)
    # 2030 offshore build-out lever: override the coast (BE25) Wind-Offshore capacity
    # (Princess Elisabeth Zone ≈ 5.8 GW). All BE offshore is on the BE25 coast.
    offshore_gw = scenario.get("offshore_gw")
    if offshore_gw is not None:
        mask = (cap[rb.REGION] == rb.OFFSHORE_REGION) & (cap["plant_type"] == "Wind Offshore")
        cap = cap.copy()
        cap.loc[mask, "mw"] = float(offshore_gw) * 1000.0
    dshare = rb.demand_shares(scenario.get("demand_cache"))
    nat_load = np.nan_to_num(ps._to_8760(np.asarray(demand["BE"], dtype=float)))
    demand_scale = float(scenario.get("demand_scale", 1.0))
    ntc_scale = float(scenario.get("ntc_scale", 1.0))
    internal_scale = float(scenario.get("internal_scale", 1.0)) * ntc_scale
    coast_ntc_scale = float(scenario.get("coast_ntc_scale", 1.0))   # Stevin/Ventilus lever
    # tiny negative SRMC on renewables: the LP then exports available RES rather than
    # curtailing it for free (which collapses interior duals to 0), and curtails only
    # when a corridor PHYSICALLY forces it — giving clean locational prices and a small
    # negative price exactly where congestion bites.
    res_vc = float(scenario.get("res_incentive", -0.1))
    targets = set(scenario.get("apply_to") or rb.BE_NUTS2)
    LIFE = 25
    hours = list(range(H))

    em = EnergyModel(name=f"regional_be_{scenario.get('_name', 'base')}",
                     hours=hours, year_ops=[year], year_invs=[year],
                     year_decs=[year + LIFE], modes=["base"], resources=["electricity"])
    areas: dict[str, object] = {}
    with em.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=year, planning_step=25)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)
        for z in rb.BE_NUTS2:
            areas[z] = Area(z)

    # ---- per-province generation + demand (reusing the PROVEN national adders) ----
    for region in rb.BE_NUTS2:
        _stage_region_inputs(region, year)
        sub = cap[cap[rb.REGION] == region]
        # synthetic 1-row capacity frame for this province (the adders read caps[ptype][0])
        caps_row = {r["plant_type"]: float(r["mw"]) for _, r in sub.iterrows()}
        caps = pl.DataFrame([caps_row]) if caps_row else pl.DataFrame()
        for tech, ptype in DISPATCHABLE_TECH_DICT.items():
            if ptype in caps.columns and float(caps[ptype][0] or 0) > 0:
                add_dispatchable_tech(areas[region], tech, ptype, caps, costs[tech], year,
                    ramp_rate=DISPATCHABLE_RAMP_RATES[tech], fixed_cost=FIXED_COSTS[tech],
                    investment_cost=INVEST_COSTS[tech], lifetime=LIFE)
        for tech, ptype in INTERMITTENT_TECH_DICT.items():
            if ptype in caps.columns and float(caps[ptype][0] or 0) > 0:
                add_intermittent_tech(areas[region], tech, ptype, float(caps[ptype][0]), year,
                    fixed_cost=FIXED_COSTS[tech], investment_cost=INVEST_COSTS[tech], lifetime=LIFE,
                    variable_cost=res_vc)
        # pumped storage / reservoir hydro: out of scope for v1 (~1.3 GW, flagged in docs)
        with em.context():
            scale = demand_scale if region in targets else 1.0
            dem = nat_load * dshare.get(region, 0.0) * scale
            areas[region].add_component(Demand(name=f"{region}_demand", resource="electricity",
                demand=pl.DataFrame({"demand": dem.tolist(), "hour": hours,
                                     "year_op": [year] * H})))
            areas[region].add_component(LoadShedding(name=f"{region}_ls", resource="electricity",
                                                     cost=float(scenario.get("voll", 15000.0))))
            # NB: NO zero-cost Spillage here. Intermittent/dispatchable can already curtail
            # implicitly (availability is a max, must_run=0), and a free local spill lets a
            # surplus province dump power at €0 instead of exporting it through the frontier —
            # which collapses every interior dual to zero. Surplus must flow out to be priced.

    # ---- external coupling. Belgium's national 380 kV grid is well meshed, so every
    # province reaches the European market at ~the real price — EXCEPT the coast (BE25),
    # whose North-Sea offshore wind can only leave through the Stevin + Ventilus corridors.
    # So we anchor every province to the market with a priced NetImport (real 2023
    # day-ahead) and deliberately leave BE25 with NO external escape: its price then = the
    # market price when the corridors have room, and falls (toward curtailment) when the
    # offshore build-out floods a constrained cut. That coast-vs-rest gap is the locational
    # signal and the Ventilus cost-benefit. (A bid-ask spread kills the otherwise-degenerate
    # circular re-export arbitrage between the many priced nodes.) ----
    if external in ("full", "imports_only"):
        bzn = COUNTRY_BIDDING_ZONE_MAPPING.get("BE", ["BE"])[0]
        imp = get_electricity_import_prices("BE", year, year, hours, bzn)
        exp = (imp.rename({"import_price": "export_price"})
                  .with_columns((pl.col("export_price") - 1.0).clip(0.0)))
        anchored = [r for r in rb.BE_NUTS2 if r != rb.OFFSHORE_REGION]   # all but the coast
        with em.context():
            for reg in anchored:
                areas[reg].add_component(NetImport(
                    name=f"{reg}_market", resource="electricity",
                    import_price=imp, export_price=exp))

    # ---- internal reduced network (MW-capped corridors → the congestion that matters) ----
    # The coast cut (BE25→BE23 Stevin, BE25→BE32 Ventilus) carries North-Sea wind inland;
    # `coast_ntc_scale` is the Ventilus lever (e.g. 0.5 = "Ventilus not built").
    coast = {"BE25"}
    with em.context():
        for a, b, ntc in rb.INTERNAL_LINES:
            sc = internal_scale if (a in targets or b in targets) else 1.0
            if a in coast or b in coast:
                sc *= coast_ntc_scale
            _add_bidirectional_link(areas[a], areas[b], float(ntc) * sc, 0.01)
    return em, hours


def run_regional_be(cfg, name: str, demand: dict, year: int = 2023):
    """Build, solve and price one scenario. Returns a ScenarioResult whose ``prices``
    has a ``country`` column holding the NUTS-2 region code (and ``EXT``)."""
    scenario = dict(cfg.scenarios.get(name, {})) if hasattr(cfg, "scenarios") else {}
    scenario["_name"] = name
    em, hours = build_regional_be_model(cfg, scenario, demand, year)
    lm = em.run(return_linopy_model=True)
    prices = ps.marginal_price(lm, hours)
    logger.info("regional '%s' solved (%s)", name, lm.status)
    return ps.ScenarioResult(name=name, prices=prices, model=em, status=str(lm.status))
