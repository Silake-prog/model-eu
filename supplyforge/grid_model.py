r"""
Build a multi-area pommes_craft dispatch model from PECD data + Ember NTCs.

Each country is an :class:`~pommes_craft.Area` (a grid node) carrying PECD-derived
wind/solar capacity factors and reservoir inflow; countries are connected by inter-area
links holding the **Ember net-transfer-capacity (NTC)** as a
:class:`~pommes_craft.TransportTechnology`. A pommes ``Link`` is *one-directional*, so each
border becomes **two opposing links** (a->b and b->a), each capped at the NTC.

The fleet, demand and storage sizing per country are **scenario assumptions you pass in** —
PECD provides weather only. PECD itself ships no grid topology/NTC, so the interconnections
come from supplyforge's Ember NTC module (country-level). For finer PECD SZON bidding-zone
nodes you would supply ERAA/PEMMDB zone-level NTCs; :func:`build_grid_model` accepts any node
set + link list, so that path is open.

Example
-------
>>> from supplyforge.grid_model import build_european_grid
>>> countries = ["FR", "DE", "BE", "NL", "CH"]
>>> model = build_european_grid(countries, year=2050, model_year=2050,
...                             assumptions=ASSUMPTIONS_BY_COUNTRY, config=config)
>>> model.run()                                  # needs an LP solver (HiGHS)
>>> flows = grid_flows(model)                    # hourly cross-border flows (MW)

Author: Simon Brigode <simon.brigode@ehess.fr>. Part of supplyforge (Yassine Abdelouadoud).
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# (component-name suffix, assumptions key, PECD CF label)
INTERMITTENT = [
    ("Solar", "solar_GW", "Solar"),
    ("Wind_Onshore", "wind_onshore_GW", "Wind Onshore"),
    ("Wind_Offshore", "wind_offshore_GW", "Wind Offshore"),
]


def pecd_country_inputs(countries, year, config):
    """National capacity factors + reservoir inflow per country, from staged PECD files.

    The PECD products are pan-European, so every country is aggregated from the same staged
    files (null-aware zonal aggregation; see :mod:`supplyforge.process.pecd`).

    Returns ``{country: {"cf": {label: hourly np.ndarray}, "inflow": np.ndarray | None}}``.
    """
    import pandas as pd
    from supplyforge.process.pecd.capacity_factor import national_cf_from_frame
    from supplyforge.process.pecd.hydro_inflow import find_pecd_csv, pecd_reservoir_inflow

    cache: dict = {}

    def _read(code):
        if code not in cache:
            p = find_pecd_csv(code, year)
            cache[code] = pd.read_csv(p, comment="#", index_col="Date") if p else None
        return cache[code]

    out = {}
    for cc in countries:
        cf = {}
        for code, label in [("SPV", "Solar"), ("WON", "Wind Onshore"), ("WOF", "Wind Offshore")]:
            df = _read(code)
            if df is not None:
                v = national_cf_from_frame(code, cc, df, config)
                if v is not None:
                    cf[label] = np.asarray(v, dtype=float)
        inflow = None
        hri = _read("HRI")
        if hri is not None:
            r = pecd_reservoir_inflow(hri, cc, year)
            if not r.is_empty():
                inflow = r["inflow_MW"].to_numpy()
        out[cc] = {"cf": cf, "inflow": inflow}
        logger.info("PECD inputs %s: techs=%s inflow=%s", cc, sorted(cf), "yes" if inflow is not None else "no")
    return out


def ember_ntc_links(countries, *, min_mw=1.0):
    """De-duplicated inter-country links among ``countries`` from the Ember NTC dataset.

    Returns ``[(country_a, country_b, capacity_MW), ...]`` (each border once, capacity =
    Ember's max-of-both-directions). Borders leaving the set, or below ``min_mw``, are dropped.
    """
    from supplyforge.fetch.ember_ntc import get_ntc_for_country

    cset = set(countries)
    seen, links = set(), []
    for a in countries:
        try:
            d = get_ntc_for_country(a)
        except Exception as exc:  # pragma: no cover - network/data dependent
            logger.warning("Ember NTC fetch failed for %s: %s", a, exc)
            continue
        for b in d.columns:
            if b not in cset or b == a:
                continue
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            mw = float(d[b][0])
            if mw >= min_mw:
                seen.add(key)
                links.append((key[0], key[1], mw))
    logger.info("Ember NTC: %d intra-set links among %d countries", len(links), len(countries))
    return links


def _avail_df(values, hour_index, year_op):
    """Availability frame: sample `values` (full hourly series) at the model's source hours.

    NaN-guarded and clipped to [0, 1] — availability must be a finite coefficient or the LP
    build fails with ``LinearExpression contains nan's in field(s) ['coeffs']``.
    """
    import polars as pl
    sampled = np.asarray(values, dtype=float)[np.asarray(hour_index)]
    sampled = np.clip(np.nan_to_num(sampled, nan=0.0), 0.0, 1.0)
    H = len(hour_index)
    return pl.DataFrame({"availability": sampled, "hour": list(range(H)), "year_op": [year_op] * H})


def _populate_area(area, cf, inflow, A, hours, year_op):
    """Add demand + intermittent RE + reservoir hydro + gas + load-shedding/spillage to an Area."""
    import polars as pl
    from pommes_craft import ConversionTechnology, Demand, LoadShedding, Spillage, StorageTechnology

    cc = area.name
    H = len(hours)
    # Electricity demand: an hourly profile (full-8760 MW array, e.g. from demandforge) sampled at the
    # modelled hours, or a flat level from demand_TWh if no profile is given.
    profile = A.get("demand_profile")
    if profile is not None:
        demand_series = np.nan_to_num(np.asarray(profile, dtype=float)[np.asarray(hours)], nan=0.0).tolist()
    else:
        demand_series = [A.get("demand_TWh", 0.0) * 1e6 / 8760] * H
    area.add_component(Demand(name=f"{cc}_demand", resource="electricity",
        demand=pl.DataFrame({"demand": demand_series, "hour": list(range(H)), "year_op": [year_op] * H})))

    # Greenfield expansion: the optimiser *sizes* investable generation (using supplyforge's cost
    # basis); otherwise the fixed scenario fleet (intermittent RE here, firm dispatchable below).
    gf = A.get("greenfield")
    if gf:
        _populate_greenfield(area, gf, cf, hours, year_op)
    else:
        for tech, key, label in INTERMITTENT:
            cap = A.get(key, 0.0) * 1000.0
            if cap <= 0 or label not in cf:
                continue
            area.add_component(ConversionTechnology(name=f"{cc}_{tech}", factor={"electricity": 1.0},
                availability=_avail_df(cf[label], hours, year_op), must_run=0.0, variable_cost=0.0, life_span=25,
                invest_cost=0.0, finance_rate=0.04,
                power_capacity_investment_max=cap, power_capacity_investment_min=cap,
                early_decommissioning=True))

    if inflow is not None and A.get("hydro_turbine_GW", 0) > 0 and float(np.nanmax(inflow)) > 0:
        peak = float(np.nanmax(inflow))
        area.add_component(StorageTechnology(name=f"{cc}_lake_store",
            factor_in={"reservoir_water": -1.0}, factor_out={"reservoir_water": 1.0}, factor_keep={"reservoir_water": 0.0},
            invest_cost_power=10.0, life_span=25,
            energy_capacity_investment_max=A["hydro_energy_GWh"] * 1000.0,
            energy_capacity_investment_min=A["hydro_energy_GWh"] * 1000.0, early_decommissioning=True))
        area.add_component(ConversionTechnology(name=f"{cc}_lake_inflow", factor={"reservoir_water": 1.0},
            availability=_avail_df(inflow / peak, hours, year_op), must_run=1.0, life_span=25,
            power_capacity_max=peak, power_capacity_min=peak, early_decommissioning=True))
        area.add_component(ConversionTechnology(name=f"{cc}_lake_plant", factor={"reservoir_water": -1.0, "electricity": 1.0},
            availability=1.0, life_span=25, power_capacity_max=A["hydro_turbine_GW"] * 1000.0,
            power_capacity_min=A["hydro_turbine_GW"] * 1000.0, early_decommissioning=True))

    # Firm dispatchable fleet: {label: {"GW": x, "cost": EUR/MWh}} — each an always-available
    # ConversionTechnology priced by short-run marginal cost, so HiGHS dispatches them in merit
    # order (e.g. Nuclear cheap before Gas). ``gas_GW``/``gas_cost`` is kept as legacy shorthand.
    firm = {} if gf else dict(A.get("firm") or {})
    if not gf and A.get("gas_GW", 0) > 0:
        firm.setdefault("Gas", {"GW": A["gas_GW"], "cost": A.get("gas_cost", 80.0)})
    for label, spec in firm.items():
        gw = float(spec.get("GW", 0.0) or 0.0)
        if gw <= 0:
            continue
        area.add_component(ConversionTechnology(name=f"{cc}_{label}", factor={"electricity": 1.0},
            availability=1.0, must_run=0.0, variable_cost=float(spec.get("cost", 80.0)), life_span=25,
            invest_cost=0.0, finance_rate=0.04,
            power_capacity_investment_max=gw * 1000.0, power_capacity_investment_min=0.0,
            early_decommissioning=True))

    _populate_hydrogen(area, A.get("h2"), hours, year_op)
    _populate_biomethane(area, A.get("biomethane"), year_op)

    area.add_component(LoadShedding(name=f"{cc}_load_shedding", resource="electricity", cost=A.get("voll", 30_000.0)))
    area.add_component(Spillage(name=f"{cc}_spillage", resource="electricity", max_capacity=1.0e6))
    if inflow is not None:
        area.add_component(Spillage(name=f"{cc}_water_spillage", resource="reservoir_water", max_capacity=5.0e4))
        area.add_component(LoadShedding(name=f"{cc}_water_shedding", resource="reservoir_water", max_capacity=0.0))


def _populate_hydrogen(area, h2, hours, year_op):
    """Add a hydrogen layer to ``area`` when it carries an ``h2`` config dict.

    Components: an **investable electrolyser** (electricity -> H2, capacity decided by the model so
    electrolysis siting/sizing is optimised), optional H2 ``Demand`` (when ``demand_MW`` > 0),
    optional H2 ``StorageTechnology`` (shift production to cheap/windy hours), and H2
    load-shedding/spillage valves. ``h2`` keys (all optional): efficiency (MWh H2 per MWh elec,
    default 0.70), demand_MW (per-hour H2 demand), electrolyser_invest_cost (EUR/MW-elec/yr),
    electrolyser_max_GW, storage (bool), variable_cost, h2_voll.
    """
    if not h2:
        return
    import polars as pl
    from pommes_craft import ConversionTechnology, Demand, LoadShedding, Spillage, StorageTechnology

    cc, H = area.name, len(hours)
    eff = float(h2.get("efficiency", 0.70))          # MWh H2 (LHV) produced per MWh electricity in
    area.add_component(ConversionTechnology(
        name=f"{cc}_electrolyser", factor={"electricity": -1.0, "hydrogen": eff}, availability=1.0,
        variable_cost=float(h2.get("variable_cost", 1.0)),
        invest_cost=float(h2.get("electrolyser_invest_cost", 90_000.0)),
        # life_span must be >= the model's year_dec horizon (25) or the tech has no valid decommission
        # year within its life and pommes can't invest in it (capacity stays 0).
        life_span=int(h2.get("electrolyser_life", 25)), finance_rate=0.04,
        power_capacity_investment_min=0.0,
        power_capacity_investment_max=float(h2.get("electrolyser_max_GW", 50.0)) * 1000.0,
        early_decommissioning=True))

    dem_mw = float(h2.get("demand_MW", 0.0) or 0.0)
    if dem_mw > 0:
        area.add_component(Demand(name=f"{cc}_h2_demand", resource="hydrogen",
            demand=pl.DataFrame({"demand": [dem_mw] * H, "hour": list(range(H)), "year_op": [year_op] * H})))
    if h2.get("storage"):
        area.add_component(StorageTechnology(
            name=f"{cc}_h2_store", factor_in={"hydrogen": -1.0}, factor_out={"hydrogen": 1.0},
            factor_keep={"hydrogen": 0.0}, invest_cost_power=float(h2.get("store_invest_power", 5_000.0)),
            invest_cost_energy=float(h2.get("store_invest_energy", 300.0)), life_span=25, finance_rate=0.04,
            power_capacity_investment_min=0.0, power_capacity_investment_max=1.0e5,
            energy_capacity_investment_min=0.0, energy_capacity_investment_max=1.0e7,
            early_decommissioning=True))
    area.add_component(LoadShedding(name=f"{cc}_h2_shedding", resource="hydrogen",
                                    cost=float(h2.get("h2_voll", 50_000.0))))
    area.add_component(Spillage(name=f"{cc}_h2_spillage", resource="hydrogen", max_capacity=1.0e6))


# Greenfield expansion: tech key -> PECD CF label for intermittent RE (all other techs are firm
# dispatchable, priced by short-run variable cost). Keys match the cost dicts in
# create_pommes_craft_model (Solar, Wind_Onshore, Wind_Offshore, Nuclear, Gas, Biomass, ...).
GREENFIELD_RE = {"Solar": "Solar", "Wind_Onshore": "Wind Onshore", "Wind_Offshore": "Wind Offshore"}


def _greenfield_costs():
    """(INVEST €/MW, FIXED €/MW/yr, VARIABLE €/MWh, LIFE yr) cost dicts for greenfield techs.

    Sourced from supplyforge's ``create_pommes_craft_model`` (the project's own cost basis); falls
    back to inline EU-2050 estimates if that module can't be imported.
    """
    try:
        from supplyforge.create_pommes_craft_model import (
            INVEST_COSTS, FIXED_COSTS, DISPATCHABLE_DEFAULT_COSTS, LIFETIMES)
        return INVEST_COSTS, FIXED_COSTS, DISPATCHABLE_DEFAULT_COSTS, LIFETIMES
    except Exception:  # pragma: no cover - defensive fallback
        inv = {"Nuclear": 7.5e6, "Gas": 9e5, "Solar": 7e5, "Wind_Onshore": 1.3e6, "Wind_Offshore": 3.2e6,
               "Biomass": 3.5e6, "Coal": 1.8e6, "Oil": 5e5}
        fix = {"Nuclear": 120_000.0, "Gas": 20_000.0, "Solar": 12_000.0, "Wind_Onshore": 35_000.0,
               "Wind_Offshore": 85_000.0, "Biomass": 60_000.0, "Coal": 45_000.0, "Oil": 25_000.0}
        var = {"Nuclear": 10.0, "Gas": 200.0, "Biomass": 50.0, "Coal": 250.0, "Oil": 300.0}
        life = {"Nuclear": 60, "Gas": 30, "Solar": 25, "Wind_Onshore": 25, "Wind_Offshore": 25,
                "Biomass": 25, "Coal": 40, "Oil": 30}
        return inv, fix, var, life


def _crf(n, r=0.04):
    """Capital-recovery factor: the annuity per unit of overnight cost over life ``n`` at rate ``r``."""
    return r * (1 + r) ** n / ((1 + r) ** n - 1)


def _populate_greenfield(area, gf, cf, hours, year_op):
    """Investable generation (greenfield capacity expansion) sized by the optimiser.

    ``gf`` maps a tech key (Solar, Wind_Onshore, Nuclear, Gas, ...) to its max buildable GW (a float
    or a ``{"max_GW": ...}`` spec). Costs come from supplyforge's create_pommes_craft_model. The
    intermittent RE techs (see ``GREENFIELD_RE``) use the PECD capacity factor as availability and
    have no fuel cost; the rest are firm (availability 1, priced by short-run variable cost).

    Decommission-year trick: build_grid_model uses a *single* year_dec (= model_year + 25) to keep
    the LP small, but real lives differ (Nuclear 60, Solar 25, Gas 30...). To still annualise each
    tech over its true life, the overnight CAPEX is rescaled by ``CRF(real_life)/CRF(25)`` so pommes'
    25-yr annuity equals the real-life annuity, while life_span stays 25 (so the tech remains
    investable under the single year_dec).
    """
    from pommes_craft import ConversionTechnology
    INVEST, FIXED, VARCOST, LIFE = _greenfield_costs()
    cc = area.name
    for tech, spec in gf.items():
        max_gw = float(spec.get("max_GW", 0.0) if isinstance(spec, dict) else spec)
        if max_gw <= 0:
            continue
        life = int(LIFE.get(tech, 25))
        invest = float(INVEST.get(tech, 1.0e6)) * _crf(life) / _crf(25)
        kwargs = dict(name=f"{cc}_{tech}", factor={"electricity": 1.0},
                      invest_cost=invest, fixed_cost=float(FIXED.get(tech, 20_000.0)),
                      finance_rate=0.04, life_span=25, must_run=0.0,
                      power_capacity_investment_min=0.0, power_capacity_investment_max=max_gw * 1000.0,
                      early_decommissioning=True)
        label = GREENFIELD_RE.get(tech)
        if label is not None:                    # intermittent RE: availability = PECD capacity factor
            if label not in cf:
                logger.warning("greenfield %s in %s: no CF '%s' available; skipping", tech, cc, label)
                continue
            kwargs.update(availability=_avail_df(cf[label], hours, year_op), variable_cost=0.0)
        else:                                    # firm dispatchable: priced by short-run marginal cost
            kwargs.update(availability=1.0, variable_cost=float(VARCOST.get(tech, 80.0)))
        area.add_component(ConversionTechnology(**kwargs))


def _populate_biomethane(area, bm, year_op):
    """Add an ENSPRESO-fed biomethane supply chain to ``area`` when it carries a ``biomethane`` config.

    Three-stage chain on the ``raw_biomass`` + ``biomethane`` resources, fed by an ENSPRESO per-country
    potential (see :mod:`supplyforge.biomethane`):

        raw_biomass_supply  (priced at feedstock cost, capped at the ENSPRESO primary-biomass potential)
            -> biomethane_plant   (AD/methanisation: raw_biomass + a little electricity -> biomethane)
                -> Biomethane_CCGT (biomethane -> electricity)
                -> ATR_biomethane  (biomethane -> hydrogen; only when ``atr`` is set)

    The AD plant is unbundled from the consumers, so its capacity is sized once and shared between the
    CCGT and ATR routes. ``bm`` keys: ``potential_TWh`` (deliverable biomethane), ``cost_eur_per_MWh_th``
    (feedstock gate cost per MWh of *primary* biomass), ``atr`` (bool — add the H2 route).

    Conventions: factor sign = consumed negative / produced positive; capacities are MW of each tech's
    output resource; the binding annual cap is ``max_yearly_production`` on raw_biomass (primary). All
    process ratios/costs come from the **PROVISIONAL** block in :mod:`supplyforge.biomethane` (to be
    owned by the future industryforge); life_span values are >= 25 for the single-year_dec convention.
    """
    if not bm:
        return
    from pommes_craft import ConversionTechnology
    from supplyforge import biomethane as B

    potential_TWh = float(bm.get("potential_TWh", 0.0) or 0.0)
    if potential_TWh < 1e-3:
        return
    feedstock_cost = float(bm.get("cost_eur_per_MWh_th", 0.0) or 0.0)
    cc = area.name
    yld = B.BIOMASS_TO_BIOMETHANE_YIELD
    bm_mwh = potential_TWh * 1e6            # deliverable biomethane (MWh/yr)
    primary_mwh = bm_mwh / yld             # primary biomass required (MWh/yr) — the raw-biomass cap

    # 1. raw_biomass supply — production-only CT priced at feedstock cost, capped at the primary
    #    potential via max_yearly_production. Free to build (invest_cost=0) up to a 5x-average power cap
    #    (intra-year shape slack); the annual energy is what binds.
    rb_cap_mw = (primary_mwh / 8760.0) * 5.0
    area.add_component(ConversionTechnology(
        name=f"{cc}_raw_biomass_supply", factor={"raw_biomass": 1.0}, availability=1.0, must_run=0.0,
        variable_cost=feedstock_cost, max_yearly_production=primary_mwh,
        power_capacity_max=rb_cap_mw, power_capacity_investment_max=rb_cap_mw,
        invest_cost=0.0, fixed_cost=0.0, finance_rate=0.0, life_span=25, early_decommissioning=True))

    # life_span is pinned to 25 (the single year_dec horizon, like every other grid_model tech); the
    # overnight CAPEX is rescaled so the 25-yr annuity equals the real-life annuity (same trick as
    # _populate_greenfield). real lives live in the PROVISIONAL block of supplyforge.biomethane.
    def _annuitised(capex_per_kw, real_life):
        return capex_per_kw * 1000.0 * _crf(real_life) / _crf(25)

    # 2. AD / methanisation plant — raw_biomass + electricity -> biomethane (investable, shared).
    ad_max_mw_th = bm_mwh / (8760.0 * B.AD_PLANT_CF)
    area.add_component(ConversionTechnology(
        name=f"{cc}_biomethane_plant",
        factor={"raw_biomass": -1.0 / yld, "electricity": -B.ELEC_PER_BIOMETHANE_MWH, "biomethane": 1.0},
        availability=1.0, must_run=0.0, variable_cost=0.0,
        invest_cost=_annuitised(B.AD_CAPEX_EUR_PER_KW, B.AD_LIFE_YR), fixed_cost=B.AD_FOM_EUR_PER_KW_YR * 1000.0,
        finance_rate=B.DISCOUNT_RATE, life_span=25,
        power_capacity_min=0.0, power_capacity_max=ad_max_mw_th,
        power_capacity_investment_min=0.0, power_capacity_investment_max=ad_max_mw_th,
        early_decommissioning=True))

    # 3. Biomethane CCGT — biomethane -> electricity.
    ccgt_max_mw_e = bm_mwh * B.CCGT_EFF / (8760.0 * B.CCGT_CF)
    area.add_component(ConversionTechnology(
        name=f"{cc}_Biomethane_CCGT", factor={"electricity": 1.0, "biomethane": -1.0 / B.CCGT_EFF},
        availability=1.0, must_run=0.0, variable_cost=B.CCGT_VOM_EUR_PER_MWH,
        invest_cost=_annuitised(B.CCGT_CAPEX_EUR_PER_KW, B.CCGT_LIFE_YR), fixed_cost=B.CCGT_FOM_EUR_PER_KW_YR * 1000.0,
        finance_rate=B.DISCOUNT_RATE, life_span=25,
        power_capacity_min=0.0, power_capacity_max=ccgt_max_mw_e,
        power_capacity_investment_min=0.0, power_capacity_investment_max=ccgt_max_mw_e,
        early_decommissioning=True))

    # 4. ATR (optional) — biomethane -> hydrogen (ties into the H2 layer).
    if bm.get("atr"):
        atr_max_mw_h2 = bm_mwh * B.ATR_EFF / (8760.0 * B.ATR_CF)
        area.add_component(ConversionTechnology(
            name=f"{cc}_ATR_biomethane", factor={"hydrogen": 1.0, "biomethane": -1.0 / B.ATR_EFF},
            availability=1.0, must_run=0.0, variable_cost=B.ATR_VOM_EUR_PER_MWH,
            invest_cost=_annuitised(B.ATR_CAPEX_EUR_PER_KW, B.ATR_LIFE_YR), fixed_cost=B.ATR_FOM_EUR_PER_KW_YR * 1000.0,
            finance_rate=B.DISCOUNT_RATE, life_span=25,
            power_capacity_min=0.0, power_capacity_max=atr_max_mw_h2,
            power_capacity_investment_min=0.0, power_capacity_investment_max=atr_max_mw_h2,
            early_decommissioning=True))


def _add_bidirectional_link(area_a, area_b, mw, hurdle, *, resource="electricity",
                            tech_prefix="NTC", link_prefix=""):
    """Two one-way pommes Links (a->b and b->a), each capped at ``mw`` MW of ``resource``.

    A pommes ``Link`` carries flow in one direction only, so a bidirectional interconnector = one
    Link per direction. The *Link* names are directional and unique (``A->B`` / ``H2_A->B``), but
    the *TransportTechnology* name is a single shared token per resource (``tech_prefix``: ``NTC``
    for electricity, ``H2NTC`` for hydrogen). This is deliberate: pommes indexes transport by the
    ``(link, transport_tech)`` grid, so giving each link a *distinct* tech name would make every
    off-diagonal (link, other-link's-tech) cell NaN and poison the objective. One shared tech name
    keeps the grid ``n_links x 1`` and fully defined.
    """
    from pommes_craft import Link, TransportTechnology

    for src, dst in ((area_a, area_b), (area_b, area_a)):
        link = Link(name=f"{link_prefix}{src.name}->{dst.name}", area_from=src, area_to=dst)
        link.add_transport_technology(TransportTechnology(
            name=tech_prefix, resource=resource,
            invest_cost=0.0, fixed_cost=0.0, finance_rate=0.04,
            hurdle_costs=hurdle, life_span=25,
            power_capacity_investment_min=float(mw), power_capacity_investment_max=float(mw),
            early_decommissioning=True))


def build_grid_model(countries, model_year, assumptions, cf_by_country, inflow_by_country,
                     ntc_links, *, name=None, hurdle_costs=0.01,
                     resources=("electricity", "reservoir_water"), hours=None, h2_links=None):
    """Build a multi-area pommes_craft model: one Area per node + bidirectional transport links.

    Args:
        countries: node codes (countries or NUTS regions — the model is node-agnostic).
        model_year: operational + investment year.
        assumptions: ``{node: {solar_GW, wind_onshore_GW, wind_offshore_GW, hydro_turbine_GW,
            hydro_energy_GWh, gas_GW, gas_cost, demand_TWh[, voll][, firm={label: {GW, cost}}]
            [, h2={...}][, greenfield={tech: max_GW}]}}``. ``firm`` adds dispatchable techs priced
            by merit order (``gas_GW``/``gas_cost`` is shorthand); ``h2`` adds an investable
            electrolyser + optional H2 demand/storage (see :func:`_populate_hydrogen`).
            ``greenfield`` instead makes generation *investable* — the optimiser sizes each tech up
            to its ``max_GW`` using supplyforge's cost basis (see :func:`_populate_greenfield`);
            when present it replaces the fixed RE + firm fleet for that node. ``biomethane`` adds an
            ENSPRESO-fed biomethane supply chain (raw_biomass -> AD plant -> CCGT [+ ATR->H2]); its
            ``{potential_TWh, cost_eur_per_MWh_th, atr}`` come from :mod:`supplyforge.biomethane`
            (see :func:`_populate_biomethane`).
        cf_by_country: ``{node: {label: hourly CF array}}`` (from :func:`pecd_country_inputs`).
        inflow_by_country: ``{node: hourly inflow MW array | None}``.
        ntc_links: ``[(a, b, mw), ...]`` electricity inter-node capacities (from :func:`ember_ntc_links`).
        hurdle_costs: small per-MWh cost on transported flow (wheeling / tie-breaker).
        resources: energy carriers — include ``"hydrogen"`` for the H2 layer. The biomethane carriers
            (``"raw_biomass"``, ``"biomethane"``) are auto-added when any node has a ``biomethane`` config.
        h2_links: ``[(a, b, mw), ...]`` hydrogen pipeline capacities between nodes (optional).

    Returns the **unsolved** EnergyModel; call ``.run()`` (needs an LP solver such as HiGHS).
    """
    from pommes_craft import Area, EconomicHypothesis, EnergyModel, TimeStepManager

    missing = [c for c in countries if c not in assumptions]
    if missing:
        raise ValueError(f"build_grid_model: no assumptions for {missing}")

    # Auto-enable the biomethane carriers when any node carries a `biomethane` config, so callers don't
    # have to remember to add them to `resources` (the chain also needs `electricity`, always present,
    # and `hydrogen` when the ATR route is used).
    resources = list(resources)
    if any(assumptions[c].get("biomethane") for c in countries):
        for r in ("raw_biomass", "biomethane"):
            if r not in resources:
                resources.append(r)
        if any((assumptions[c].get("biomethane") or {}).get("atr") for c in countries) \
                and "hydrogen" not in resources:
            resources.append("hydrogen")

    hour_index = list(range(8760)) if hours is None else list(hours)
    H = len(hour_index)
    # Single decommission horizon (= the 25-yr planning step) instead of a per-lifetime range: with
    # investment (electrolysers, storage, transport) the year_dec axis multiplies every planning
    # variable, so one value keeps the LP within laptop RAM. (CLEVER uses the full range on a server.)
    # NB: every investable tech must have life_span >= 25, else it has no valid decommission year
    # within its life and can't be invested (capacity stays 0). All grid_model techs use life_span=25.
    model = EnergyModel(name=name or f"grid_{'_'.join(countries[:4])}_{model_year}",
                        hours=list(range(H)), year_ops=[model_year], year_invs=[model_year],
                        year_decs=[model_year + 25],
                        modes=["base"], resources=list(resources))
    with model.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=model_year, planning_step=25)
        # Power balance is enforced at face value each modelled hour (time_step_duration=1.0); costs
        # and energy are annualised over operation_year_duration=8760, so a representative sample of
        # H<8760 hours is weighted up to the full year (each stands for 8760/H of them). Read annual
        # energy from results by scaling power by 8760/H. (Matches CLEVER's pommes_craft setup.)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)
        areas = {}
        for cc in countries:
            area = Area(cc)
            areas[cc] = area
            _populate_area(area, cf_by_country.get(cc, {}), inflow_by_country.get(cc),
                           assumptions[cc], hour_index, model_year)
        n_links = 0
        for a, b, mw in ntc_links:
            if a in areas and b in areas:
                _add_bidirectional_link(areas[a], areas[b], mw, hurdle_costs)
                n_links += 1
        n_h2 = 0
        for a, b, mw in (h2_links or []):
            if a in areas and b in areas and mw > 0:
                _add_bidirectional_link(areas[a], areas[b], mw, hurdle_costs,
                                        resource="hydrogen", tech_prefix="H2NTC", link_prefix="H2_")
                n_h2 += 1
    logger.info("Built grid model: %d areas, %d electricity links, %d H2 links", len(areas), n_links, n_h2)
    return model


def build_european_grid(countries, year, model_year, assumptions, config, *, hurdle_costs=0.01):
    """Convenience wrapper: PECD inputs + Ember NTC links + :func:`build_grid_model`, in one call."""
    inputs = pecd_country_inputs(countries, year, config)
    links = ember_ntc_links(countries)
    return build_grid_model(
        countries, model_year, assumptions,
        {c: inputs[c]["cf"] for c in countries},
        {c: inputs[c]["inflow"] for c in countries},
        links, hurdle_costs=hurdle_costs)


def grid_flows(model):
    """After ``model.run()``: the hourly cross-border flows as a polars DataFrame.

    One row per (link, hour); ``value`` is the directional flow in MW (>= 0 on link
    ``"A->B"``). Net A->B exchange = flow(A->B) - flow(B->A).
    """
    import polars as pl
    power = model.get_results("operation", "power")
    return power.filter(pl.col("component_class") == "TransportTechnology")


__all__ = ["pecd_country_inputs", "ember_ntc_links", "build_grid_model",
           "build_european_grid", "grid_flows", "INTERMITTENT"]
