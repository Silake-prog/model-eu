"""France-Belgium regional electricity + hydrogen network co-optimisation.

A multi-energy pommes_craft model over **FR + BE NUTS-2 nodes**:

* Electricity — hourly demand from **demandforge** ``project_load_curve`` (country profile,
  disaggregated to NUTS-2 by area share); generation from PECD ``nuts_2`` solar CF + national wind CF
  and the **OSM** installed fleet priced in merit order; intra-country adjacency links + the FR-BE
  cross-border Ember NTC.
* Hydrogen — industrial H2 demand from **demandforge** ``aggregate_h2_demand`` placed at 5 weighted
  nodes (FR: Dunkerque ``FRE1`` / Fos ``FRL0`` / Toulouse ``FRJ2``; BE: Antwerp ``BE21`` / Ghent
  ``BE23``), met by **investable electrolysers at every node** (the optimiser chooses siting/sizing),
  an **H2 pipeline network** along region adjacency, and H2 storage at the demand nodes.

The model co-optimises electricity dispatch + electrolysis siting + H2 transport. Flags:
``--solve`` ``--month=M`` (contiguous month) ``--days=N`` (first N days, default 7) ``--full``.

Run:  PYTHONPATH=. python scripts/fr_be_h2.py --days=7 --solve

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
import sys
import time
import logging
import warnings

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING, force=True)

import numpy as np
import pandas as pd
import polars as pl
import geopandas as gpd

from supplyforge import RESULTS_DIR
from supplyforge.process.pecd.capacity_factor import national_cf_from_frame
from supplyforge.process.pecd.hydro_inflow import find_pecd_csv
from supplyforge.fetch.ember_ntc import get_ntc_for_country
from supplyforge.fetch.osm.generation import fetch_power_plants, plants_to_region_capacity
from supplyforge.fetch.tools.visualisation import maps
from supplyforge.grid_model import build_grid_model, grid_flows


def _arg(flag, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(flag + "=")), default)


SOLVE = "--solve" in sys.argv
YEAR = 2050           # model / projection year
REF_YEAR = 2020       # demandforge reference load year
COUNTRIES = ["FR", "BE"]

# Hydrogen demand nodes (one country's industrial H2 split over a few weighted NUTS-2 regions).
H2_WEIGHTS = {
    "FR": {"FRE1": 0.45, "FRL0": 0.40, "FRJ2": 0.15},   # Dunkerque (steel) / Fos (chem) / Toulouse
    "BE": {"BE21": 0.70, "BE23": 0.30},                 # Antwerp (petrochem) / Ghent (steel)
}
H2_SECTORS = ["ammonia", "olefins", "steel"]            # country-differentiated industrial H2

# Short-run marginal cost (EUR/MWh) per OSM firm tech -> merit order (cf. regional_fr_de_be.py).
OSM_FIRM_COST = {"nuclear": 12, "hydro": 8, "geothermal": 8, "tidal": 0, "waste": 30,
                 "biomass": 35, "coal": 45, "gas": 80, "methane": 80, "other": 90, "oil": 150}
# Investable electrolyser + H2 storage config applied at every node (overnight EUR/MW; pommes annuitises).
ELECTROLYSER = dict(efficiency=0.70, electrolyser_invest_cost=700_000, electrolyser_life=25,
                    electrolyser_max_GW=50, variable_cost=2.0, storage=True,
                    store_invest_power=5_000, store_invest_energy=300)

config = {"res_source": "pecd", "hydro_source": "entsoe", "pecd": {
    "temporal_period": "future_projections", "origin": "ec_earth3", "emission_scenario": "ssp5_8_5",
    "spatial_resolution_solar": "nuts_2", "spatial_resolution_wind": "p2on",
    "energy_scenario": "resource_grade_b", "climate_years": [YEAR], "ror_source": "entsoe"}}

# Hours: one representative period (contiguous, so H2 storage couples correctly). On a small-RAM
# laptop, few hours over many nodes is the right trade for THIS model (the story is spatial — where
# electrolysis is sited + how H2 flows — not the hourly dispatch). --hours=N gives a tiny snapshot.
_idx = pd.date_range(f"{YEAR}-01-01", periods=8760, freq="h")
if _arg("--rep-hours"):
    # N hours spanning the year (seasonal/diurnal sample) — each weighted 8760/N. Non-contiguous, so
    # H2 storage is turned off (it needs adjacent hours). Lets the full 33-node model fit a big server.
    HOURS = sorted(set(np.linspace(0, 8759, int(_arg("--rep-hours")), dtype=int).tolist()))
elif _arg("--hours"):
    HOURS = list(range(int(_arg("--hours"))))
elif _arg("--month"):
    HOURS = [i for i, t in enumerate(_idx) if t.month == int(_arg("--month"))]
elif "--full" in sys.argv:
    HOURS = list(range(8760))
else:
    HOURS = list(range(int(_arg("--days") or 7) * 24))
STORAGE = "--no-storage" not in sys.argv and not _arg("--rep-hours")   # storage needs contiguous hours
print(f"period: {len(HOURS)} h ({'full year' if len(HOURS) == 8760 else _idx[HOURS[0]].strftime('%b %d')+' +'})")

# ── 1+2. Electricity load (hourly MW) + industrial H2 demand (annual MWh) per country.
# On the laptop these come from demandforge and are cached to an .npz; --demand-file loads that cache
# (used on the server, whose demandforge 0.2.2 lacks aggregate_h2_demand). No server env changes needed.
_demand_file = _arg("--demand-file")
if _demand_file:
    _d = np.load(_demand_file)
    load_profile = {c: _d[f"{c}_load"].astype(float) for c in COUNTRIES}
    h2_annual = {c: float(_d[f"{c}_h2"]) for c in COUNTRIES}
    print("demand from file:", {c: f"{load_profile[c].sum()/1e6:.0f} TWh elec / {h2_annual[c]/1e6:.1f} TWh H2"
                                 for c in COUNTRIES})
else:
    from demandforge.load_projection import project_load_curve, aggregate_h2_demand
    from demandforge.fetch.industry_data import fetch_ammonia_production

    load_profile = {}
    for c in COUNTRIES:
        df = project_load_curve(REF_YEAR, c, YEAR, baseload_yearly_growth_rate=0.005,
                                winter_thermosensitive_yearly_growth_rate=0.0,
                                summer_thermosensitive_yearly_growth_rate=0.01, ev_yearly_growth_rate=0.03)
        load_profile[c] = df["total_load_projected"].to_numpy(dtype=float)[:8760]   # leap-year guard
        print(f"  demandforge load {c}: {load_profile[c].sum()/1e6:.0f} TWh/yr, peak {load_profile[c].max()/1e3:.0f} GW")

    try:
        fetch_ammonia_production(countries=COUNTRIES, reference_year=2019)   # prime shared ammonia cache
    except Exception:
        pass

    def _h2_annual(c):
        for secs in (H2_SECTORS, ["olefins", "steel"], ["steel"]):   # fall back if a sector lacks data
            try:
                h = aggregate_h2_demand(c, reference_year=2019, target_year=YEAR, sectors=secs)
                return float(h[h["year"] == YEAR]["h2_demand_mwh_per_yr"].sum())
            except Exception:
                continue
        return 0.0
    h2_annual = {c: _h2_annual(c) for c in COUNTRIES}
    for c in COUNTRIES:
        print(f"  demandforge H2 {c}: {h2_annual[c]/1e6:.1f} TWh/yr -> nodes {H2_WEIGHTS[c]}")

    np.savez(RESULTS_DIR / "frbe_demand.npz",                          # cache for reuse / server transfer
             **{f"{c}_load": load_profile[c] for c in COUNTRIES}, **{f"{c}_h2": h2_annual[c] for c in COUNTRIES})
    print(f"  cached demand -> {RESULTS_DIR / 'frbe_demand.npz'}")

# ── 3. PECD nuts_2 solar CF + national onshore-wind CF ──
sp = sorted((RESULTS_DIR / "pecd_study").glob(f"pecd42_SPV_nuts_2_{YEAR}*.csv"))[0]
sol = pd.read_csv(sp, comment="#", index_col="Date")
solar_cf = {col: np.clip(np.nan_to_num(np.asarray(pd.to_numeric(sol[col], errors="coerce"), float), nan=0.0), 0.0, 1.0)
            for col in sol.columns}
won = pd.read_csv(find_pecd_csv("WON", YEAR), comment="#", index_col="Date")
wind = {}
for c in COUNTRIES:
    try:
        v = national_cf_from_frame("WON", c, won, config)
        wind[c] = np.asarray(v, float) if v is not None else None
    except Exception:
        wind[c] = None
    if wind[c] is None or not np.isfinite(np.nanmean(wind[c])):
        wind[c] = np.full(8760, 0.25)   # fallback flat onshore CF
        print(f"  [warn] no PECD wind for {c}; using flat 0.25")

# ── 4. GISCO NUTS-2 geometry (FR+BE metropolitan, with PECD solar) + area shares + adjacency ──
g = gpd.read_file(maps.fetch_nuts_geojson(level=2, scale="20M"))
g = g[g["CNTR_CODE"].isin(COUNTRIES) & ~g["NUTS_ID"].str.startswith("FRY") & g["NUTS_ID"].isin(solar_cf)].copy()
regions = list(g["NUTS_ID"])
country_of = dict(zip(g["NUTS_ID"], g["CNTR_CODE"]))
area = dict(zip(g["NUTS_ID"], g.to_crs(3035).geometry.area))
area_frac = {}
for c in COUNTRIES:
    tot = sum(area[r] for r in regions if country_of[r] == c)
    area_frac.update({r: area[r] / tot for r in regions if country_of[r] == c})
gb = g[["NUTS_ID", "geometry"]].copy()
gb["geometry"] = gb.geometry.buffer(0.05)
sj = gpd.sjoin(gb, g[["NUTS_ID", "geometry"]], predicate="intersects")
pairs = {tuple(sorted((a, b))) for a, b in zip(sj["NUTS_ID_left"], sj["NUTS_ID_right"]) if a != b}

# Optional node cap for laptop RAM: keep the H2-demand nodes + cross-border regions, fill the rest by
# size (largest area first). The full 33-node FR+BE model OOMs a laptop (2 resources + investment +
# H2 network); --max-nodes trims it while preserving the H2 story. (--full / a server runs all 33.)
MAX_NODES = int(_arg("--max-nodes") or 0)
if MAX_NODES and len(regions) > MAX_NODES:
    h2_nodes = {r for c in COUNTRIES for r in H2_WEIGHTS[c]}
    border = {r for (a, b) in pairs if country_of[a] != country_of[b] for r in (a, b)}
    must = [r for r in regions if r in (h2_nodes | border)]
    extra = sorted((r for r in regions if r not in set(must)), key=lambda r: -area_frac[r])
    keep = set((must + extra)[:max(MAX_NODES, len(must))])
    regions = [r for r in regions if r in keep]
    pairs = {(a, b) for (a, b) in pairs if a in keep and b in keep}
    # Keep area_frac on the FULL-country basis (do NOT re-normalise): each kept node carries its true
    # share of national electricity demand, so the modelled sub-region's demand balances against the
    # kept nodes' actual OSM fleet. (Re-normalising would impose full-country demand on partial
    # generation -> spurious electricity shedding.) H2 demand stays full (all 5 H2 nodes are kept).
print(f"nodes: {len(regions)} NUTS-2 (" + ", ".join(f"{c}={sum(country_of[r]==c for r in regions)}" for c in COUNTRIES) + f"); adjacencies: {len(pairs)}")

# ── 5. OSM installed fleet per region (the FR-metro bbox already covers Belgium; reuse the cache) ──
osm_cap = plants_to_region_capacity(
    fetch_power_plants((41, -5.5, 51.5, 10), cache_key="plants_fr_metro"), level=2)
print(f"OSM fleet: {sum(1 for r in regions if r in osm_cap)}/{len(regions)} regions populated")

# ── 6. per-node assumptions: hourly demand (country profile x area share), OSM firm fleet, H2 layer ──
assumptions, cf_by, inflow_by = {}, {}, {}
for r in regions:
    c, f = country_of[r], area_frac[r]
    oc = osm_cap.get(r, {})
    firm = {tech.capitalize(): {"GW": mw / 1000, "cost": OSM_FIRM_COST.get(tech, 90)}
            for tech, mw in oc.items() if mw > 0 and tech not in ("solar", "wind_onshore", "battery")}
    h2_dem_mw = (h2_annual[c] * H2_WEIGHTS[c].get(r, 0.0)) / 8760.0    # flat hourly H2 demand (MW)
    assumptions[r] = dict(
        demand_profile=load_profile[c] * f, wind_offshore_GW=0, hydro_turbine_GW=0, hydro_energy_GWh=0,
        solar_GW=oc.get("solar", 0.0) / 1000, wind_onshore_GW=oc.get("wind_onshore", 0.0) / 1000,
        firm=firm, h2=dict(ELECTROLYSER, demand_MW=h2_dem_mw, storage=STORAGE))
    cf_by[r] = {"Solar": solar_cf[r], "Wind Onshore": wind[c]}
    inflow_by[r] = None
print("H2 demand nodes:", {r: round(assumptions[r]["h2"]["demand_MW"]) for r in regions if assumptions[r]["h2"]["demand_MW"] > 0})

# ── 7. electricity links (intra-country adjacency + FR-BE Ember NTC) and H2 pipeline network ──
INTRA_MW = 10_000.0
try:
    ntc = get_ntc_for_country("FR")
    fr_be_ntc = float(ntc["BE"][0]) if "BE" in ntc.columns else 4300.0
except Exception:
    fr_be_ntc = 4300.0   # fallback FR-BE NTC (MW) if the Ember dataset is unavailable (e.g. offline server)
xb = [(a, b) for (a, b) in pairs if country_of[a] != country_of[b]]
elec_links = []
for (a, b) in pairs:
    if country_of[a] == country_of[b]:
        elec_links.append((a, b, INTRA_MW))
    else:
        elec_links.append((a, b, fr_be_ntc / max(len(xb), 1)))   # split country NTC over border-region pairs
# H2 pipelines: same adjacency graph, generous capacity (the model routes H2 to demand nodes).
h2_links = [(a, b, 20_000.0) for (a, b) in pairs]
print(f"links: {len(elec_links)} electricity (FR-BE NTC {fr_be_ntc:.0f} MW over {len(xb)} border pairs), {len(h2_links)} H2 pipelines")

model = build_grid_model(regions, YEAR, assumptions, cf_by, inflow_by, elec_links,
                         resources=["electricity", "hydrogen"], hours=HOURS, h2_links=h2_links, name="fr_be_h2")
print(f"built model: {len(model.areas)} areas, {len(model.links)} one-way links, {len(HOURS)} hours")

if SOLVE:
    t0 = time.time()
    lm = model.run(return_linopy_model=True)
    print(f"SOLVE {time.time()-t0:.0f}s status={lm.status} obj=EUR{float(lm.objective.value):,.0f}")
    op = model.get_results("operation", "power")
    scale = 8760.0 / len(HOURS)   # annualise: each modelled hour represents 8760/H real hours

    # electrolyser siting (invested electrical MW) + H2 produced
    pc = model.get_results("planning", "power_capacity").filter(
        pl.col("name").str.ends_with("_electrolyser") & pl.col("value").is_not_null())
    elec_cap = {r["name"].replace("_electrolyser", ""): r["value"] for r in pc.iter_rows(named=True) if r["value"] > 1}
    print("electrolyser capacity (MW elec):", {k: round(v) for k, v in sorted(elec_cap.items(), key=lambda x: -x[1])})

    # H2 pipeline net flows (TWh) between regions
    h2flow = grid_flows(model).filter(pl.col("name").str.starts_with("H2NTC_"))
    h2twh = {r["name"]: r["value"] * scale / 1e6 for r in
             h2flow.group_by("name").agg(pl.col("value").sum().alias("value")).iter_rows(named=True)}
    h2net = {(a, b): h2twh.get(f"H2NTC_{a}_{b}", 0) - h2twh.get(f"H2NTC_{b}_{a}", 0) for (a, b) in pairs}
    top = sorted(h2net.items(), key=lambda x: -abs(x[1]))[:8]
    print("top H2 pipeline net flows (TWh):", {f"{a}->{b}": round(v, 2) for (a, b), v in top})

    # Net H2 position per region: electrolyser output (elec_in x efficiency) minus local H2 demand (TWh/yr).
    h2_prod = {r["area"]: r["v"] * ELECTROLYSER["efficiency"] * scale / 1e6 for r in
               op.filter(pl.col("name").str.ends_with("_electrolyser"))
                 .group_by("area").agg(pl.col("value").sum().alias("v")).iter_rows(named=True)}
    h2_dem = {r: (h2_annual[country_of[r]] * H2_WEIGHTS[country_of[r]].get(r, 0.0)) / 1e6 for r in regions}
    net_h2 = {r: h2_prod.get(r, 0.0) - h2_dem.get(r, 0.0) for r in regions}
    print("net H2 position (TWh, + producer / - importer):",
          {r: round(v, 1) for r, v in sorted(net_h2.items(), key=lambda x: x[1]) if abs(v) > 0.1})

    # Electricity generation mix by technology (TWh/yr); the electrolyser appears as a load (negative).
    gen = {}
    for row in (op.filter(pl.col("component_class") == "ConversionTechnology")
                .group_by("name").agg((pl.col("value").sum() * scale / 1e6).alias("twh")).iter_rows(named=True)):
        nm, twh = row["name"], row["twh"]
        if "_lake_" in nm or abs(twh) < 1e-3:
            continue
        tech = nm.split("_", 1)[1] if "_" in nm else nm
        if tech == "electrolyser":
            gen["Electrolysis (load)"] = gen.get("Electrolysis (load)", 0.0) - twh
        else:
            gen[tech.replace("_", " ")] = gen.get(tech.replace("_", " "), 0.0) + twh
    print("electricity mix (TWh/yr):", {k: round(v) for k, v in sorted(gen.items(), key=lambda x: -x[1])})

    import os
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGDIR = _arg("--figdir") or "fr_be_figs"
    os.makedirs(FIGDIR, exist_ok=True)
    SITE_CMAP = "YlGn"            # sequential: electrolyser capacity (one consistent magnitude scale)
    TECH_COLORS = {"Nuclear": "#9b59b6", "Hydro": "#1abc9c", "Gas": "#7f8c8d", "Coal": "#2c3e50",
                   "Oil": "#e67e22", "Biomass": "#27ae60", "Waste": "#95a5a6", "Geothermal": "#16a085",
                   "Solar": "#f1c40f", "Wind Onshore": "#3498db", "Wind Offshore": "#2980b9",
                   "Tidal": "#48c9b0", "Other": "#bdc3c7", "Electrolysis (load)": "#e74c3c"}
    elec_gw = {r: elec_cap.get(r, 0) / 1000 for r in regions}

    # Figure 1 — OVERVIEW (the aggregate): where H2 is made (siting) and the net balance per region.
    fig, ax = plt.subplots(1, 2, figsize=(16, 8))
    maps.choropleth(elec_gw, level=2, scale="20M", ax=ax[0], region="fr_be", cmap=SITE_CMAP,
                    cbar_label="electrolyser capacity (GW)", title="Electrolyser siting")
    maps.choropleth(net_h2, level=2, scale="20M", ax=ax[1], region="fr_be", cmap="RdBu", vcenter=0.0,
                    cbar_label="net H2 (TWh/yr)", title="Net H2 position  (blue = exporter · red = importer)")
    fig.suptitle("FR–BE hydrogen network — overview", fontsize=15)
    fig.tight_layout(); fig.savefig(f"{FIGDIR}/overview.png", dpi=95); plt.close(fig)

    # Figure 2 — H2 NETWORK + ZOOMS: the pipeline flows, then a closer look at the industrial clusters.
    zooms = [("North corridor — Dunkerque · Antwerp · Ghent", (1.3, 49.3, 6.7, 51.6)),
             ("South-east — Fos · Rhône-Alpes", (3.4, 43.0, 7.8, 46.6))]
    fig, ax = plt.subplots(1, 3, figsize=(20, 7))
    maps.flow_map(h2net, level=2, scale="20M", ax=ax[0], region="fr_be", cmap="viridis", min_abs=0.5,
                  cbar_label="H2 flow (TWh/yr)", title="H2 pipeline flows (whole network)")
    for k, (ttl, bbox) in enumerate(zooms, start=1):
        maps.choropleth(net_h2, level=2, scale="10M", ax=ax[k], region=bbox, cmap="RdBu", vcenter=0.0,
                        cbar_label="net H2 (TWh/yr)", title=ttl)
    fig.suptitle("H2 network and zooms into the demand clusters", fontsize=15)
    fig.tight_layout(); fig.savefig(f"{FIGDIR}/network.png", dpi=95); plt.close(fig)

    # Figure 3 — THE MIX: electricity generation by technology, and H2 sourcing at each hub.
    fig, ax = plt.subplots(1, 2, figsize=(16, 6))
    techs = sorted(gen, key=lambda t: gen[t])
    ax[0].barh(techs, [gen[t] for t in techs], color=[TECH_COLORS.get(t, "#888888") for t in techs])
    ax[0].axvline(0, color="k", lw=.6)
    ax[0].set(xlabel="TWh/yr", title="Electricity mix — generation (+) and electrolysis load (–)")
    ax[0].grid(alpha=.3, axis="x")
    hubs = [r for c in COUNTRIES for r in H2_WEIGHTS[c] if r in regions]
    x = np.arange(len(hubs)); w = 0.4
    ax[1].bar(x - w / 2, [h2_prod.get(r, 0) for r in hubs], w, label="local electrolysis", color="#27ae60")
    ax[1].bar(x + w / 2, [h2_dem.get(r, 0) for r in hubs], w, label="H2 demand", color="#e74c3c")
    ax[1].set_xticks(x); ax[1].set_xticklabels(hubs, rotation=30, ha="right")
    ax[1].set(ylabel="TWh/yr", title="H2 at the demand hubs — local production vs demand")
    ax[1].legend(); ax[1].grid(alpha=.3, axis="y")
    fig.tight_layout(); fig.savefig(f"{FIGDIR}/mix.png", dpi=95); plt.close(fig)

    print(f"rendered {FIGDIR}/overview.png, network.png, mix.png")
print("DONE_FR_BE_H2")
