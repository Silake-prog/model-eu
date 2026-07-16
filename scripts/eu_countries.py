"""Single-node-per-country European greenfield capacity-expansion model.

One pommes_craft :class:`Area` per country (a copper-plate national node). Generation is
**greenfield** — the optimiser *sizes* solar / onshore-wind / nuclear / gas / biomass at each node,
priced with supplyforge's own cost basis (``create_pommes_craft_model``) — to meet a **hypothetical
2050 electricity demand** plus **industrial hydrogen demand** from **demandforge**, the latter served
by **local electrolysis** (an investable electrolyser per country). Weather comes from **PECD 4.2**
under the *median* climate scenario (``ssp2_4_5``, EC-Earth3, 2050); countries are linked by **Ember**
net-transfer capacities. The model co-optimises the capacity mix + electrolyser siting for 2050.

This is a demonstrator (copper-plate nodes, illustrative demand, a representative-hour sample), not a
study. Flags:
    --solve             build *and* solve (default: build only)
    --rep-hours=N       N representative hours spanning the year (default 96; each weighted 8760/N)
    --full              all 8760 hours (heavy)
    --max-countries=N   keep the N highest-demand countries (laptop RAM)
    --no-h2             skip the hydrogen layer
    --figdir=DIR        where to write figures (default: eu_figs)

Run:  PYTHONPATH=. python scripts/eu_countries.py --rep-hours=96 --solve

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
import sys
import time
import glob
import logging
import warnings

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.WARNING, force=True)

import numpy as np
import pandas as pd
import polars as pl

from supplyforge import RESULTS_DIR
from supplyforge.process.pecd.capacity_factor import national_cf_from_frame
from supplyforge.fetch.ember_ntc import get_ntc_for_country
from supplyforge.grid_model import build_grid_model, grid_flows


def _arg(flag, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv if a.startswith(flag + "=")), default)


SOLVE = "--solve" in sys.argv
WITH_H2 = "--no-h2" not in sys.argv
YEAR = 2050
SCENARIO = "ssp2_4_5"          # PECD median climate-change scenario
ORIGIN = "ec_earth3"

# Core continental-EU set (PECD codes == ISO-2 here, so no code mapping). Ordered by ~2050 demand so
# --max-countries keeps the largest systems. CH/NO are non-EU but interconnected + in PECD.
COUNTRIES = ["DE", "FR", "IT", "ES", "PL", "SE", "NL", "NO", "BE", "FI",
             "AT", "CZ", "PT", "CH", "DK", "IE"]

# Hypothetical 2050 electricity demand (TWh/yr) — illustrative *electrified* levels (heat + transport
# electrification lift these well above today's). These are the "dict of hypothetical demand" inputs.
DEMAND_2050_TWH = {
    "DE": 700, "FR": 580, "IT": 400, "ES": 380, "PL": 230, "SE": 175, "NL": 175, "NO": 165,
    "BE": 120, "FI": 110, "AT": 100, "CZ": 95, "PT": 75, "CH": 85, "DK": 60, "IE": 55,
}

# Greenfield build limits (GW) — generous national upper bounds; the optimiser picks the cost-optimal
# subset. Costs/lifetimes come from supplyforge's create_pommes_craft_model (via grid_model).
# Biomass is feedstock-limited in reality (EU power-sector biomass is ~45 GW today), so it is capped
# low — otherwise, as a cheap dispatchable, it would crowd out renewables wherever nuclear is banned.
GREENFIELD_BASE_GW = {"Solar": 600.0, "Wind_Onshore": 400.0, "Gas": 300.0, "Biomass": 5.0}
# Nuclear is special-cased per country: with supplyforge's cheap nuclear (10 EUR/MWh, 60-yr life) an
# unconstrained cost-minimiser builds *only* nuclear. Real 2050 fleets are policy-bounded, so we cap
# nuclear at plausible national levels (FR keeps a large fleet; a few keep/expand; the rest phase out).
# Countries absent here build no nuclear -> their mix is renewables + gas/biomass backup.
NUCLEAR_MAX_GW = {"FR": 63.0, "SE": 10.0, "PL": 9.0, "CZ": 6.0, "FI": 6.0, "NL": 5.0, "CH": 3.0}

# Investable electrolyser per node: industrial H2 (demandforge) met by local electrolysis.
ELECTROLYSER = dict(efficiency=0.70, electrolyser_invest_cost=700_000, electrolyser_life=25,
                    electrolyser_max_GW=80, variable_cost=2.0)
H2_SECTORS = ["ammonia", "olefins", "steel"]

config = {"res_source": "pecd", "hydro_source": "entsoe", "pecd": {
    "temporal_period": "future_projections", "origin": ORIGIN, "emission_scenario": SCENARIO,
    "spatial_resolution_solar": "szon", "spatial_resolution_wind": "p2on",
    "energy_scenario": "resource_grade_b", "climate_years": [YEAR], "ror_source": "entsoe"}}

MAXC = int(_arg("--max-countries") or 0)
if MAXC and MAXC < len(COUNTRIES):
    COUNTRIES = COUNTRIES[:MAXC]

# Hours: a representative sample spanning the year (seasonal + diurnal). Non-contiguous, so the
# (hour-coupling) H2 storage is off — electrolysers run against the sampled hours.
if _arg("--rep-hours"):
    HOURS = sorted(set(np.linspace(0, 8759, int(_arg("--rep-hours")), dtype=int).tolist()))
elif "--full" in sys.argv:
    HOURS = list(range(8760))
else:
    HOURS = sorted(set(np.linspace(0, 8759, 96, dtype=int).tolist()))
print(f"countries: {len(COUNTRIES)}  ({', '.join(COUNTRIES)})")
print(f"period: {len(HOURS)} representative hours (each ~{8760/len(HOURS):.0f} real hours)")


# ── 1. PECD ssp2_4_5 capacity factors (solar szon + onshore wind p2on), aggregated to country ──
def _read_pecd(code_full):
    files = sorted(glob.glob(str(RESULTS_DIR / "pecd_study" /
                                 f"pecd42_{code_full}_{YEAR}_{ORIGIN}_{SCENARIO}_*.csv")))
    return pd.read_csv(files[0], comment="#", index_col="Date") if files else None


spv = _read_pecd("SPV_szon")
won = _read_pecd("WON_p2on")
if spv is None:
    sys.exit(f"missing PECD solar (SPV_szon {SCENARIO}); run the fetch first")
cf_by = {}
for c in COUNTRIES:
    s = national_cf_from_frame("SPV", c, spv, config)
    w = national_cf_from_frame("WON", c, won, config) if won is not None else None
    s = np.asarray(s, float) if s is not None else np.full(8760, 0.13)
    w = np.asarray(w, float) if w is not None else np.full(8760, 0.25)
    cf_by[c] = {"Solar": np.clip(np.nan_to_num(s), 0, 1), "Wind Onshore": np.clip(np.nan_to_num(w), 0, 1)}
print("PECD CF (annual mean): " +
      ", ".join(f"{c} S{cf_by[c]['Solar'].mean():.2f}/W{cf_by[c]['Wind Onshore'].mean():.2f}" for c in COUNTRIES[:6]) + " …")
if won is None:
    print("  [warn] no PECD onshore-wind ssp2_4_5 yet; using flat 0.25")


# ── 2. Industrial hydrogen demand per country (demandforge) → flat hourly MW ──
h2_annual = {c: 0.0 for c in COUNTRIES}
if WITH_H2:
    _hf = _arg("--demand-file")
    if _hf:
        _d = np.load(_hf)
        h2_annual = {c: float(_d[f"{c}_h2"]) for c in COUNTRIES if f"{c}_h2" in _d}
    else:
        try:
            from demandforge.load_projection import aggregate_h2_demand
            from demandforge.fetch.industry_data import fetch_ammonia_production
            try:
                fetch_ammonia_production(countries=COUNTRIES, reference_year=2019)   # prime shared cache
            except Exception:
                pass

            def _h2(c):
                for secs in (H2_SECTORS, ["olefins", "steel"], ["steel"]):
                    try:
                        h = aggregate_h2_demand(c, reference_year=2019, target_year=YEAR, sectors=secs)
                        return float(h[h["year"] == YEAR]["h2_demand_mwh_per_yr"].sum())
                    except Exception:
                        continue
                return 0.0
            h2_annual = {c: _h2(c) for c in COUNTRIES}
            np.savez(RESULTS_DIR / "eu_h2_demand.npz", **{f"{c}_h2": h2_annual[c] for c in COUNTRIES})
        except Exception as e:
            print(f"  [warn] demandforge H2 unavailable ({e}); H2 layer empty")
    print("H2 demand (TWh/yr): " +
          ", ".join(f"{c} {h2_annual.get(c,0)/1e6:.1f}" for c in COUNTRIES if h2_annual.get(c, 0) > 0))


# ── 3. Per-country assumptions: greenfield generation + flat demand + local electrolysis ──
assumptions, inflow_by = {}, {}
for c in COUNTRIES:
    gf = dict(GREENFIELD_BASE_GW)
    if NUCLEAR_MAX_GW.get(c, 0) > 0:
        gf["Nuclear"] = NUCLEAR_MAX_GW[c]
    A = dict(greenfield=gf, demand_TWh=DEMAND_2050_TWH.get(c, 50),
             hydro_turbine_GW=0, hydro_energy_GWh=0)
    if WITH_H2 and h2_annual.get(c, 0) > 0:
        A["h2"] = dict(ELECTROLYSER, demand_MW=h2_annual[c] / 8760.0, storage=False)
    assumptions[c] = A
    inflow_by[c] = None


# ── 4. Cross-border links from Ember NTC (country level) ──
def _ember_links(countries):
    seen, links = set(), []
    for a in countries:
        try:
            d = get_ntc_for_country(a)
        except Exception:
            continue
        for b in countries:
            if b == a or b not in getattr(d, "columns", []):
                continue
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            mw = float(d[b][0])
            if mw > 0:
                seen.add(key)
                links.append((key[0], key[1], mw))
    return links


elec_links = _ember_links(COUNTRIES)
print(f"Ember NTC: {len(elec_links)} cross-border links")

# Hydrogen backbone: pipelines along the same country adjacency as the electricity grid (a "European
# Hydrogen Backbone" proxy), generous capacity. H2 transport is cheap, so this lets electrolysis
# migrate to the best wind/solar countries and pipe H2 to the demand centres, rather than every
# country electrolysing its own demand locally. --no-h2-grid keeps H2 local (no pipelines).
H2_PIPE_MW = 30_000.0
h2_links = None
if WITH_H2 and "--no-h2-grid" not in sys.argv:
    h2_links = [(a, b, H2_PIPE_MW) for a, b, _ in elec_links]
    print(f"H2 backbone: {len(h2_links)} pipelines ({H2_PIPE_MW/1000:.0f} GW each)")

resources = ["electricity", "hydrogen"] if WITH_H2 else ["electricity"]
model = build_grid_model(COUNTRIES, YEAR, assumptions, cf_by, inflow_by, elec_links,
                         resources=resources, hours=HOURS, h2_links=h2_links, name="eu_countries")
print(f"built model: {len(model.areas)} areas, {len(model.links)} one-way links, {len(HOURS)} hours")


# ── 5. Solve + extract the capacity mix / electrolyser siting / cross-border flows ──
if SOLVE:
    t0 = time.time()
    lm = model.run(return_linopy_model=True)
    print(f"SOLVE {time.time()-t0:.0f}s  status={lm.status}  obj=EUR{float(lm.objective.value):,.0f}/yr")
    scale = 8760.0 / len(HOURS)

    # invested capacity (GW) per country per tech
    pc = model.get_results("planning", "power_capacity").filter(
        (pl.col("component_class") == "ConversionTechnology") & pl.col("value").is_not_null())
    cap_gw = {}                  # {country: {tech: GW}}
    elec_gw = {}                 # {country: electrolyser GW}
    for r in pc.iter_rows(named=True):
        nm, v = r["name"], float(r["value"] or 0.0)
        if v <= 1:
            continue
        c, tech = nm.split("_", 1)
        if tech == "electrolyser":
            elec_gw[c] = v / 1000.0
        else:
            cap_gw.setdefault(c, {})[tech.replace("_", " ")] = v / 1000.0
    print("\ninstalled capacity (GW):")
    for c in COUNTRIES:
        if cap_gw.get(c):
            print(f"  {c}: " + ", ".join(f"{t} {g:.0f}" for t, g in sorted(cap_gw[c].items(), key=lambda x: -x[1])))

    # generation mix (TWh/yr) and electrolysis load
    op = model.get_results("operation", "power")
    gen = {}
    for row in (op.filter(pl.col("component_class") == "ConversionTechnology")
                .group_by("name").agg((pl.col("value").sum() * scale / 1e6).alias("twh")).iter_rows(named=True)):
        nm, twh = row["name"], row["twh"]
        if abs(twh) < 1e-3:
            continue
        tech = nm.split("_", 1)[1].replace("_", " ")
        if tech == "electrolyser":
            gen["Electrolysis (load)"] = gen.get("Electrolysis (load)", 0.0) - twh
        else:
            gen[tech] = gen.get(tech, 0.0) + twh
    print("\nEU electricity mix (TWh/yr):", {k: round(v) for k, v in sorted(gen.items(), key=lambda x: -x[1])})

    # cross-border net flows (TWh/yr)
    flow = grid_flows(model).filter(pl.col("name").str.starts_with("NTC_"))
    twh = {r["name"]: r["value"] * scale / 1e6 for r in
           flow.group_by("name").agg(pl.col("value").sum().alias("value")).iter_rows(named=True)}
    net = {}
    for a, b, _ in elec_links:
        net[(a, b)] = twh.get(f"NTC_{a}_{b}", 0) - twh.get(f"NTC_{b}_{a}", 0)
    top = sorted(net.items(), key=lambda x: -abs(x[1]))[:8]
    print("top cross-border net flows (TWh/yr):", {f"{a}->{b}": round(v, 1) for (a, b), v in top})

    # hydrogen: pipeline net flows + each country's net H2 position (local electrolysis - demand)
    h2net, h2_prod, net_h2 = {}, {}, {}
    if WITH_H2:
        h2flow = grid_flows(model).filter(pl.col("name").str.starts_with("H2NTC_"))
        h2twh = {r["name"]: r["value"] * scale / 1e6 for r in
                 h2flow.group_by("name").agg(pl.col("value").sum().alias("value")).iter_rows(named=True)}
        for a, b, _ in elec_links:
            h2net[(a, b)] = h2twh.get(f"H2NTC_{a}_{b}", 0) - h2twh.get(f"H2NTC_{b}_{a}", 0)
        for r in (op.filter(pl.col("name").str.ends_with("_electrolyser"))
                  .group_by("area").agg(pl.col("value").sum().alias("v")).iter_rows(named=True)):
            h2_prod[r["area"]] = r["v"] * ELECTROLYSER["efficiency"] * scale / 1e6   # electricity in -> H2 out
        h2_dem = {c: h2_annual.get(c, 0) / 1e6 for c in COUNTRIES}
        net_h2 = {c: h2_prod.get(c, 0) - h2_dem.get(c, 0) for c in COUNTRIES
                  if h2_dem.get(c, 0) > 0 or h2_prod.get(c, 0) > 0.1}
        if h2net:
            toph2 = sorted(h2net.items(), key=lambda x: -abs(x[1]))[:8]
            print("top H2 pipeline net flows (TWh/yr):", {f"{a}->{b}": round(v, 1) for (a, b), v in toph2})
        print("net H2 position (TWh, + producer / - importer):",
              {c: round(v, 1) for c, v in sorted(net_h2.items(), key=lambda x: x[1]) if abs(v) > 0.2})

    # ── 6. Figures ──
    import os
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from supplyforge.fetch.tools.visualisation import maps

    FIGDIR = _arg("--figdir") or "eu_figs"
    os.makedirs(FIGDIR, exist_ok=True)
    TECH_COLORS = {"Nuclear": "#9b59b6", "Gas": "#7f8c8d", "Biomass": "#27ae60",
                   "Solar": "#f1c40f", "Wind Onshore": "#3498db", "Electrolysis (load)": "#e74c3c"}
    re_share = {c: (sum(cap_gw[c].get(t, 0) for t in ("Solar", "Wind Onshore"))
                    / max(sum(cap_gw[c].values()), 1e-9)) for c in cap_gw}

    # Figure 1 — capacity map: total installed GW + RE share per country
    total_gw = {c: sum(g.values()) for c, g in cap_gw.items()}
    fig, ax = plt.subplots(1, 2, figsize=(18, 8))
    maps.choropleth(total_gw, ax=ax[0], region="europe", cmap="YlGn",
                    cbar_label="installed capacity (GW)", title="Total generation capacity built")
    maps.choropleth(re_share, ax=ax[1], region="europe", cmap="BuGn", vmin=0, vmax=1,
                    cbar_label="solar+wind share of capacity", title="Renewable share of the mix")
    fig.suptitle(f"EU greenfield 2050 — capacity ({SCENARIO}, median climate)", fontsize=15)
    fig.tight_layout(); fig.savefig(f"{FIGDIR}/capacity.png", dpi=95); plt.close(fig)

    # Figure 2 — generation mix (stacked bar per country) + EU total
    fig, ax = plt.subplots(1, 2, figsize=(18, 7))
    techs = ["Nuclear", "Gas", "Biomass", "Solar", "Wind Onshore"]
    bottom = np.zeros(len(COUNTRIES))
    for t in techs:
        vals = np.array([cap_gw.get(c, {}).get(t, 0) for c in COUNTRIES])
        ax[0].bar(COUNTRIES, vals, bottom=bottom, label=t, color=TECH_COLORS.get(t, "#888"))
        bottom += vals
    ax[0].set(ylabel="GW", title="Installed capacity by country")
    ax[0].legend(fontsize=8); ax[0].grid(alpha=.3, axis="y")
    ax[0].tick_params(axis="x", rotation=45)
    order = sorted(gen, key=lambda t: gen[t])
    ax[1].barh(order, [gen[t] for t in order], color=[TECH_COLORS.get(t, "#888") for t in order])
    ax[1].axvline(0, color="k", lw=.6)
    ax[1].set(xlabel="TWh/yr", title="EU electricity mix — generation (+) / electrolysis (–)")
    ax[1].grid(alpha=.3, axis="x")
    fig.suptitle("EU greenfield 2050 — generation mix", fontsize=15)
    fig.tight_layout(); fig.savefig(f"{FIGDIR}/mix.png", dpi=95); plt.close(fig)

    # Figure 3 — cross-border flows: electricity (left) and the hydrogen backbone (right)
    fig, ax = plt.subplots(1, 2, figsize=(18, 8))
    maps.flow_map(net, ax=ax[0], region="europe", cmap="plasma", min_abs=1.0,
                  cbar_label="net flow (TWh/yr)", title="Cross-border electricity flows")
    if h2net and any(abs(v) > 0.2 for v in h2net.values()):
        maps.flow_map(h2net, ax=ax[1], region="europe", cmap="viridis", min_abs=0.2,
                      cbar_label="H2 flow (TWh/yr)", title="Hydrogen pipeline flows")
    else:
        ax[1].axis("off"); ax[1].set_title("(no H2 pipeline flows)")
    fig.suptitle("EU greenfield 2050 — electricity & hydrogen exchange", fontsize=15)
    fig.tight_layout(); fig.savefig(f"{FIGDIR}/flows.png", dpi=95); plt.close(fig)

    # Figure 4 — hydrogen: electrolyser siting (bubbles) + each country's net H2 balance
    if elec_gw:
        fig, ax = plt.subplots(1, 2, figsize=(18, 8))
        maps.bubble_map(elec_gw, ax=ax[0], region="europe", cmap="GnBu",
                        cbar_label="electrolyser capacity (GW)", title="Electrolyser siting")
        maps.choropleth(net_h2, ax=ax[1], region="europe", cmap="RdBu", vcenter=0.0,
                        cbar_label="net H2 (TWh/yr)", title="Net H2 position  (blue = exporter · red = importer)")
        fig.suptitle("EU greenfield 2050 — hydrogen production & trade", fontsize=15)
        fig.tight_layout(); fig.savefig(f"{FIGDIR}/hydrogen.png", dpi=95); plt.close(fig)
        print(f"\nrendered {FIGDIR}/capacity.png, mix.png, flows.png, hydrogen.png")
    else:
        print(f"\nrendered {FIGDIR}/capacity.png, mix.png, flows.png")

print("DONE_EU_COUNTRIES")
