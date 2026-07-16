"""Small regional (NUTS-2) FR/DE/BE cross-border optimisation demonstrator.

Real:   solar CF per region (PECD nuts_2), region geometry/adjacency (GISCO).
Assumed (flagged): wind = each country's national CF per region; demand + fleet split
across a country's regions by area; intra-country links generous; cross-border links carry
the Ember country NTC split over the adjacent border-region pairs.

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
import sys, time, logging
logging.basicConfig(level=logging.WARNING, force=True)
import numpy as np, pandas as pd, polars as pl, geopandas as gpd
from supplyforge import RESULTS_DIR
from supplyforge.process.pecd.capacity_factor import national_cf_from_frame
from supplyforge.process.pecd.hydro_inflow import find_pecd_csv
from supplyforge.fetch.ember_ntc import get_ntc_for_country
from supplyforge.fetch.tools.visualisation import maps
from supplyforge.grid_model import build_grid_model, grid_flows

SOLVE = "--solve" in sys.argv
YEAR = 2050
# Strided representative hours: the regional model has no storage, so hours are independent and a
# strided sample (time-weighted by 8760/H) is exact and ~9x smaller — keeps the full 28-node interface.
def _arg(flag, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(flag + "=")), default)


COUNTRIES = (_arg("--countries") or "FR,DE,BE").split(",")
# Hours: one representative month (--month=1..12, contiguous) else a strided full-year sample
# (--stride=N). No storage -> hours independent, so a monthly/strided sample is exact per-hour
# and time-weighted by 8760/len(HOURS) inside build_grid_model.
_month = _arg("--month")
if _month:
    _idx = pd.date_range(f"{YEAR}-01-01", periods=8760, freq="h")
    HOURS = [i for i, t in enumerate(_idx) if t.month == int(_month)]
else:
    HOURS = list(range(0, 8760, int(_arg("--stride") or 9)))
config = {"res_source": "pecd", "hydro_source": "entsoe", "pecd": {
    "temporal_period": "future_projections", "origin": "ec_earth3", "emission_scenario": "ssp5_8_5",
    "spatial_resolution_solar": "nuts_2", "spatial_resolution_wind": "p2on",
    "energy_scenario": "resource_grade_b", "climate_years": [YEAR], "ror_source": "entsoe"}}
NAT = {  # illustrative national fleets (GW / TWh), as in notebook §9
    "FR": dict(solar_GW=100, wind_onshore_GW=70, gas_GW=40, demand_TWh=480),
    "DE": dict(solar_GW=150, wind_onshore_GW=120, gas_GW=60, demand_TWh=550),
    "BE": dict(solar_GW=15, wind_onshore_GW=6, gas_GW=12, demand_TWh=90),
}
# Short-run marginal cost (EUR/MWh) per OSM firm technology -> sets the dispatch merit order
# (cheap nuclear/hydro before gas/oil). Illustrative. Battery is excluded (storage, not firm gen);
# hydro is treated as cheap firm WITHOUT a per-region energy limit (a known simplification -- the
# NUTS-2 reservoir energy budget isn't in PECD, which is country-level for hydro).
OSM_FIRM_COST = {
    "nuclear": 12, "hydro": 8, "geothermal": 8, "tidal": 0, "waste": 30, "biomass": 35,
    "coal": 45, "gas": 80, "methane": 80, "other": 90, "oil": 150,
}

# 1. regional solar CF (PECD nuts_2)
sp = sorted((RESULTS_DIR / "pecd_study").glob(f"pecd42_SPV_nuts_2_{YEAR}*.csv"))[0]
sol = pd.read_csv(sp, comment="#", index_col="Date")
solar_cf = {c: np.clip(np.nan_to_num(np.asarray(pd.to_numeric(sol[c], errors="coerce"), float), nan=0.0), 0.0, 1.0)
            for c in sol.columns}   # sanitise: NaN/empty cells -> 0, clip to [0,1] (avoids NaN coeffs in the LP)

# 2. national onshore-wind CF per country (applied to every region of that country)
wind = {}
won = pd.read_csv(find_pecd_csv("WON", YEAR), comment="#", index_col="Date")
for c in COUNTRIES:
    wind[c] = np.asarray(national_cf_from_frame("WON", c, won, config), float)

# 3. GISCO NUTS-2 geometry for FR/DE/BE (metropolitan, with solar data) + adjacency
g = gpd.read_file(maps.fetch_nuts_geojson(level=2, scale="20M"))
g = g[g["CNTR_CODE"].isin(COUNTRIES) & ~g["NUTS_ID"].str.startswith("FRY") & g["NUTS_ID"].isin(solar_cf)].copy()
regions = list(g["NUTS_ID"])
country_of = dict(zip(g["NUTS_ID"], g["CNTR_CODE"]))
area = dict(zip(g["NUTS_ID"], g.to_crs(3035).geometry.area))   # equal-area m^2
# adjacency: small buffer + intersects (robust to topology gaps)
gb = g[["NUTS_ID", "geometry"]].copy()
gb["geometry"] = gb.geometry.buffer(0.03)
sj = gpd.sjoin(gb, g[["NUTS_ID", "geometry"]], predicate="intersects")
pairs = {tuple(sorted((a, b))) for a, b in zip(sj["NUTS_ID_left"], sj["NUTS_ID_right"]) if a != b}

# 4. per-region inputs: split national fleet + demand by area fraction
area_frac = {}
for c in COUNTRIES:
    tot = sum(area[r] for r in regions if country_of[r] == c)
    area_frac.update({r: area[r] / tot for r in regions if country_of[r] == c})
# Per-region fleet: real OSM installed capacity (--capacities=osm) or national fleet x area-share.
USE_OSM = _arg("--capacities") == "osm"
osm_cap = {}
if USE_OSM:
    from supplyforge.fetch.osm.generation import fetch_power_plants, plants_to_region_capacity
    mb = g.total_bounds   # (minlon, minlat, maxlon, maxlat)
    bb = (mb[1] - 0.5, mb[0] - 0.5, mb[3] + 0.5, mb[2] + 0.5)
    ck = "plants_" + "".join(c.lower() for c in sorted(COUNTRIES)) + "_metro"
    osm_cap = plants_to_region_capacity(fetch_power_plants(bb, cache_key=ck), level=2)
    print(f"OSM capacities: {sum(1 for r in regions if r in osm_cap)}/{len(regions)} regions populated")
assumptions, cf_by, inflow_by = {}, {}, {}
for r in regions:
    c, f = country_of[r], area_frac[r]
    if USE_OSM:
        oc = osm_cap.get(r, {})
        solar_gw = oc.get("solar", 0.0) / 1000
        wind_gw = oc.get("wind_onshore", 0.0) / 1000
        # real per-tech firm fleet, each priced by merit order (nuclear cheap -> gas/oil dear);
        # skip solar/wind (intermittent, handled above) and battery (storage, not firm generation).
        firm = {tech.capitalize(): {"GW": mw / 1000, "cost": OSM_FIRM_COST.get(tech, 90)}
                for tech, mw in oc.items() if mw > 0 and tech not in ("solar", "wind_onshore", "battery")}
        extra = dict(firm=firm)
    else:
        solar_gw = NAT[c]["solar_GW"] * f
        wind_gw = NAT[c]["wind_onshore_GW"] * f
        extra = dict(gas_GW=NAT[c]["gas_GW"] * f, gas_cost=80)
    assumptions[r] = dict(solar_GW=solar_gw, wind_onshore_GW=wind_gw, wind_offshore_GW=0,
                          hydro_turbine_GW=0, hydro_energy_GWh=0,
                          demand_TWh=NAT[c]["demand_TWh"] * f, **extra)   # demand: area-split (OSM has no demand)
    cf_by[r] = {"Solar": solar_cf[r], "Wind Onshore": wind[c]}
    inflow_by[r] = None

if USE_OSM:
    _agg = {}
    for _r in regions:
        for _lbl, _sp in assumptions[_r].get("firm", {}).items():
            _agg[_lbl] = _agg.get(_lbl, 0.0) + _sp["GW"]
    print("OSM firm fleet (GW @ EUR/MWh):",
          {k: f"{v:.1f}@{OSM_FIRM_COST.get(k.lower(), 90)}" for k, v in sorted(_agg.items(), key=lambda x: -x[1])})

# 5. NTC + a tractable node subset. The full 71-node LP (~5M vars) OOMs here, so model the
#    cross-border INTERFACE: border regions + 1-hop neighbours, capped for memory (--full to override).
ntc = {}
for a, b in [("FR", "DE"), ("FR", "BE"), ("BE", "DE")]:
    d = get_ntc_for_country(a)
    ntc[tuple(sorted((a, b)))] = float(d[b][0]) if b in d.columns else 0.0

MAX_NODES = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--max=")), 28))
border = sorted({r for (r1, r2) in pairs if country_of[r1] != country_of[r2] for r in (r1, r2)})
neigh = []
for (a, b) in sorted(pairs):
    for x, y in ((a, b), (b, a)):
        if x in border and y not in border and y not in neigh:
            neigh.append(y)
_has_xborder = any(country_of[r1] != country_of[r2] for (r1, r2) in pairs)
if "--full" in sys.argv or not _has_xborder:          # single-country -> model all its regions
    SUBSET = set(regions)
else:
    SUBSET = set(list(dict.fromkeys(border + neigh))[:MAX_NODES])
regions = [r for r in regions if r in SUBSET]

pairs_s = {(a, b) for (a, b) in pairs if a in SUBSET and b in SUBSET}
xb_pairs = {}
for (r1, r2) in pairs_s:
    if country_of[r1] != country_of[r2]:
        xb_pairs.setdefault(tuple(sorted((country_of[r1], country_of[r2]))), []).append((r1, r2))
INTRA_MW = 10000.0
links = []
for (r1, r2) in pairs_s:
    c1, c2 = country_of[r1], country_of[r2]
    if c1 == c2:
        links.append((r1, r2, INTRA_MW))
    else:
        cp = tuple(sorted((c1, c2)))
        links.append((r1, r2, ntc.get(cp, 0.0) / max(len(xb_pairs[cp]), 1)))
links = [(a, b, mw) for a, b, mw in links if mw > 0]

print(f"subset regions: {len(regions)}  (" + ", ".join(f"{c}={sum(country_of[r]==c for r in regions)}" for c in COUNTRIES) + ")")
print(f"links: {len(links)}; cross-border region-pairs:", {f"{a}-{b}": len(v) for (a, b), v in xb_pairs.items()})
model = build_grid_model(regions, YEAR, assumptions, cf_by, inflow_by, links,
                         name="regional_FR_DE_BE", resources=["electricity"], hours=HOURS)
print(f"built model: {len(model.areas)} areas, {len(model.links)} one-way links, {len(HOURS)} hours")

if SOLVE:
    t0 = time.time()
    lm = model.run(return_linopy_model=True)
    print(f"SOLVE {time.time()-t0:.0f}s status={lm.status} obj=EUR{float(lm.objective.value):,.0f}")
    fl = grid_flows(model)
    _scale = 8760.0 / len(HOURS)   # each modelled hour represents 8760/H real hours
    twh = {r["name"]: r["value"] * _scale / 1e6 for r in
           fl.group_by("name").agg(pl.col("value").sum().alias("value")).iter_rows(named=True)}
    single = len({country_of[r] for r in regions}) == 1
    flow_pairs = pairs_s if single else {(a, b) for (a, b) in pairs_s if country_of[a] != country_of[b]}
    net = {(r1, r2): twh.get(f"NTC_{r1}_{r2}", 0) - twh.get(f"NTC_{r2}_{r1}", 0) for (r1, r2) in flow_pairs}
    label = "intra-country regional" if single else "cross-border regional"
    print(f"top {label} net flows (TWh):",
          {f"{a}->{b}": round(v, 3) for (a, b), v in sorted(net.items(), key=lambda x: -abs(x[1]))[:10]})
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    region_crop = "france" if single and COUNTRIES == ["FR"] else "cwe"
    fig, ax = plt.subplots(figsize=(11, 11))
    maps.flow_map(net, level=2, scale="20M", ax=ax, region=region_crop, cmap="magma", min_abs=0.01,
                  title=f"{'/'.join(COUNTRIES)} NUTS-2 — net {label} electricity flows (TWh)")
    png = "/tmp/regional_flow.png"
    fig.savefig(png, dpi=85)
    print(f"rendered {png} ({__import__('os').path.getsize(png) // 1024} kB)")
print("DONE_REGIONAL")
