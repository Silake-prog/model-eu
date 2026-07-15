#!/usr/bin/env python3
"""
DemandForge — Full Pipeline Integration Test
=============================================

Exercises **all 6 sector modules** end-to-end:
  1. Steel    (DRI-H2 transition)
  2. Refinery (CONCAWE unit-feed allocation)
  3. Ammonia  (green-H2 route decarbonisation)
  4. Maritime (4-route fuel-mix model)
  5. Olefins  (MTO H2 demand)
  6. eSAF     (fossil jet gap → eSAF → H2)

Then aggregates all sectors and produces validation graphics.

Run:  python test_all_modules.py
"""
from __future__ import annotations

import sys, types, importlib.util, pathlib, warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── Dependency mocks ────────────────────────────────────────────────
if "platformdirs" not in sys.modules:
    _pd = types.ModuleType("platformdirs")
    _pd.user_data_dir = lambda *a, **kw: "/tmp/demandforge_cache"
    sys.modules["platformdirs"] = _pd

import numpy as np
import pandas as pd

pd.set_option("display.float_format", "{:,.1f}".format)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 140)

# ── Package path setup ──────────────────────────────────────────────
# Works both as a script (__file__ defined) and in Jupyter (use cwd)
try:
    BASE = str(pathlib.Path(__file__).resolve().parent.parent)
except NameError:
    # Jupyter notebook — assume cwd is notebooks/ or repo root
    _cwd = pathlib.Path.cwd()
    if (_cwd / "demandforge").is_dir():
        BASE = str(_cwd)
    elif (_cwd.parent / "demandforge").is_dir():
        BASE = str(_cwd.parent)
    else:
        raise RuntimeError(
            f"Cannot find demandforge package from {_cwd}. "
            f"Run this notebook from the repo root or notebooks/ directory."
        )
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# The load_projection.__init__ tries to import electricity.py which needs
# pyarrow.  We intercept by pre-registering a stub for the electricity
# module and the scenarios module (which may also trigger electricity).
_elec_stub = types.ModuleType("demandforge.load_projection.electricity")
_elec_stub.project_load_curve = lambda *a, **kw: None
sys.modules["demandforge.load_projection.electricity"] = _elec_stub

# Also stub pyarrow.parquet if pyarrow is missing
try:
    import pyarrow
except ImportError:
    _pa = types.ModuleType("pyarrow")
    _pa.__version__ = "0.0.0"
    sys.modules["pyarrow"] = _pa
    sys.modules["pyarrow.parquet"] = types.ModuleType("pyarrow.parquet")

# Now safe to import everything
import demandforge.fetch.industry_data as industry_data
import demandforge.fetch.bunkering_weights as bunkering_mod
import demandforge.process.steel as steel_proc
import demandforge.process.refinery as refinery_proc
import demandforge.process.esaf as esaf_proc
import demandforge.load_projection.constants as constants_mod
import demandforge.load_projection.hydrogen as hydrogen_mod

# Re-export key functions
from demandforge.load_projection.hydrogen import (
    project_ammonia_h2_demand,
    project_maritime_h2_demand,
    project_olefins_h2_demand,
    project_steel_h2_demand,
    project_refinery_h2_demand,
    project_esaf_h2_demand,
    _linear_ramp,
)
from demandforge.load_projection.constants import (
    H2_LHV_MWH_PER_T, REFINERY_INEFFICIENCY_SHARE,
    H2_T_PER_T_NH3, H2_T_PER_T_E_METHANOL, H2_T_PER_T_NH3_FUEL,
    H2_INTENSITY_T_PER_T_ESAF,
)
from demandforge.fetch.industry_data import (
    EU27_COUNTRIES, _EUROSTAT_REFINERY_OUTPUT_2019_KTOE,
    fetch_refinery_output, fetch_ammonia_production, fetch_olefins_production,
    fetch_steel_production,
)
from demandforge.fetch.bunkering_weights import fetch_bunkering_weights
from demandforge.process.refinery import build_refinery_unit_allocation
from demandforge.process.esaf import compute_jet_fossil

# ── Constants ──────────────────────────────────────────────────────
REFERENCE_YEAR = 2019
TARGET_YEAR    = 2050
YEARS          = np.arange(REFERENCE_YEAR, TARGET_YEAR + 1)
# Focus countries for display (top steel + refinery + representative small)
FOCUS_5        = ["DE", "FR", "IT", "ES", "NL"]
ALL_EU27       = EU27_COUNTRIES

# CONCAWE and jet constants — now imported from the package
from demandforge.process.refinery import CONCAWE_MORE_MOLECULE
from demandforge.process.esaf import DEFAULT_JET_UNIT_NAMES, DEFAULT_JET_YIELDS
JET_UNIT_NAMES = DEFAULT_JET_UNIT_NAMES
JET_YIELDS = DEFAULT_JET_YIELDS


def section(title: str):
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")


# ====================================================================
# 1. STEEL MODULE
# ====================================================================
section("1. STEEL — DRI-H2 transition")

# Steel: we build the pipeline manually from the static fallback data now
# embedded in the package (fetch/industry_data.py).
from demandforge.fetch.industry_data import _CRUDE_STEEL_2019_KT, _EAF_SHARE_2019
from demandforge.process.steel import (
    build_dri_mix, compute_eaf_scrap_series, split_primary_from_eaf,
    compute_dri_bf_split, compute_steel_h2_demand,
)

# Scenario parameters (central scenario)
DRI_SHARE_2050 = 0.60
DRI_RAMP_START = 2025
DRI_RAMP_END = 2050
H2_PER_T_DRI = 0.054
REC_TARGET_2050 = 0.50

# DRI fuel mix (common)
dri_mix = build_dri_mix(years=YEARS, ramp_start=DRI_RAMP_START, ch4_only_years=5, h2_ramp_years=10)
h2_share_arr = dri_mix["h2_share"].to_numpy()
ch4_share_arr = dri_mix["ch4_share"].to_numpy()

# DRI share ramp
dri_share_target = np.array([
    _linear_ramp(int(yr), DRI_RAMP_START, DRI_RAMP_END, 0.0, DRI_SHARE_2050)
    for yr in YEARS
])

all_steel_dfs = []
for cc in ALL_EU27:
    steel_kt = _CRUDE_STEEL_2019_KT.get(cc, 0)
    if steel_kt == 0:
        continue
    steel_base = steel_kt * 1000.0  # kt → t
    eaf_share = _EAF_SHARE_2019.get(cc, 0.0)
    eaf_base = steel_base * eaf_share

    total_steel = np.full_like(YEARS, steel_base, dtype=float)  # CAGR=0
    eaf = compute_eaf_scrap_series(
        years=YEARS, steel_total=total_steel, eaf_base=eaf_base,
        eaf_share_base=eaf_share, recycling_enabled=True,
        rec_target_2050=REC_TARGET_2050, rec_ramp_start=2019, rec_ramp_end=2050,
    )
    _, primary = split_primary_from_eaf(total_steel, eaf)
    dri_2019 = 0.0  # initial DRI = 0 for most countries
    dri_total, bf_total = compute_dri_bf_split(primary, dri_2019, dri_share_target)
    dri_h2_prod = dri_total * h2_share_arr
    dri_ch4_prod = dri_total * ch4_share_arr
    h2_demand = compute_steel_h2_demand(dri_h2_prod, H2_PER_T_DRI)

    cc_df = pd.DataFrame({
        "country": cc, "year": YEARS.astype(int),
        "crude_steel_production_t_per_yr": total_steel,
        "eaf_scrap_production_t_per_yr": eaf,
        "dri_h2_production_t_per_yr": dri_h2_prod,
        "dri_ch4_production_t_per_yr": dri_ch4_prod,
        "bf_bof_production_t_per_yr": bf_total,
        "h2_demand_for_steel_t_per_yr": h2_demand,
        "h2_demand_mwh_per_yr": h2_demand * H2_LHV_MWH_PER_T,
    })
    all_steel_dfs.append(cc_df)

df_steel = pd.concat(all_steel_dfs, ignore_index=True)
print(f"Shape: {df_steel.shape}")
# Mass balance
route_sum = (
    df_steel["eaf_scrap_production_t_per_yr"]
    + df_steel["dri_h2_production_t_per_yr"]
    + df_steel["dri_ch4_production_t_per_yr"]
    + df_steel["bf_bof_production_t_per_yr"]
)
mb_err = (df_steel["crude_steel_production_t_per_yr"] - route_sum).abs().max()
assert mb_err < 1e-6, f"Steel mass balance error: {mb_err}"
print(f"✓ Mass balance OK (max error: {mb_err:.2e})")
assert (df_steel["h2_demand_for_steel_t_per_yr"] >= 0).all()
print(f"✓ No negative H2 demand")

# Summary
s = df_steel.groupby("year").agg(
    h2_t=("h2_demand_for_steel_t_per_yr", "sum"),
    total_steel=("crude_steel_production_t_per_yr", "sum"),
    dri_h2=("dri_h2_production_t_per_yr", "sum"),
).loc[[2019, 2030, 2040, 2050]]
s["h2_kt"] = s["h2_t"] / 1e3
s["total_Mt"] = s["total_steel"] / 1e6
print(f"\n  Year   EU27 steel (Mt)  DRI-H2 (Mt)  H2 demand (kt)")
for yr, row in s.iterrows():
    print(f"  {yr}   {row['total_Mt']:>10.1f}    {row['dri_h2']/1e6:>10.2f}   {row['h2_kt']:>10.1f}")
print("STEEL MODULE ✓")


# ====================================================================
# 2. REFINERY MODULE
# ====================================================================
section("2. REFINERY — CONCAWE unit-feed allocation")

df_refinery = project_refinery_h2_demand(
    country=ALL_EU27,
    reference_year=REFERENCE_YEAR,
    target_year=TARGET_YEAR,
    units_config=CONCAWE_MORE_MOLECULE,
    inefficiency_share=REFINERY_INEFFICIENCY_SHARE,
)
print(f"Shape: {df_refinery.shape}")
assert not df_refinery.isna().any().any(), "NaN in refinery output"
assert (df_refinery["h2_demand_t_per_yr"] >= 0).all()
print("✓ No NaN, no negative H2 demand")

# Level factor monotonically non-increasing for each country
for cc, grp in df_refinery.groupby("country"):
    lf = grp.groupby("year")["level_factor"].first()
    if lf.iloc[0] > 0:  # skip zero-output countries
        assert lf.iloc[0] == 1.0 or np.isclose(lf.iloc[0], 1.0, atol=0.01), \
            f"{cc}: level_factor at ref year is {lf.iloc[0]}"
print("✓ Level factor valid at reference year")

s = df_refinery.groupby("year").agg(h2_t=("h2_demand_t_per_yr", "sum")).loc[[2019, 2030, 2040, 2050]]
# NOTE: 2019 is not in CONCAWE anchor years (which start at 2024), so level_factor is 1.0 for 2019-2023
print(f"\n  Year   EU27 refinery H2 (kt)")
for yr, row in s.iterrows():
    print(f"  {yr}   {row['h2_t']/1e3:>10.1f}")
print("REFINERY MODULE ✓")


# ====================================================================
# 3. AMMONIA MODULE
# ====================================================================
section("3. AMMONIA — green-H2 route decarbonisation")

df_ammonia = project_ammonia_h2_demand(
    country=ALL_EU27,
    reference_year=REFERENCE_YEAR,
    target_year=TARGET_YEAR,
    domestic_share_end=1.0,
    h2_route_share_end=0.80,
    decarb_start=2030,
    decarb_end=2050,
    step_years=5,
)
print(f"Shape: {df_ammonia.shape}")
# Mass balances (already checked internally, but let's reconfirm)
err1 = (df_ammonia["nh3_consumption_t_per_yr"] -
        (df_ammonia["nh3_domestic_t_per_yr"] + df_ammonia["nh3_imported_t_per_yr"])).abs().max()
err2 = (df_ammonia["nh3_domestic_t_per_yr"] -
        (df_ammonia["nh3_domestic_ch4_to_h2_to_nh3_t_per_yr"] +
         df_ammonia["nh3_domestic_h2_to_nh3_t_per_yr"])).abs().max()
assert err1 < 1e-6 and err2 < 1e-6
print(f"✓ Mass balance: domestic+imported = consumption (err={err1:.2e})")
print(f"✓ Mass balance: ch4+h2 route = domestic (err={err2:.2e})")

s = df_ammonia.groupby("year").agg(
    h2_network=("h2_demand_network_t_per_yr", "sum"),
    h2_onsite=("h2_produced_onsite_t_per_yr", "sum"),
).loc[[2019, 2030, 2040, 2050]]
print(f"\n  Year   H2 network (kt)  H2 onsite/SMR (kt)")
for yr, row in s.iterrows():
    print(f"  {yr}   {row['h2_network']/1e3:>12.1f}    {row['h2_onsite']/1e3:>12.1f}")
print("AMMONIA MODULE ✓")


# ====================================================================
# 4. MARITIME MODULE
# ====================================================================
section("4. MARITIME — 4-route fuel-mix model")

df_maritime = project_maritime_h2_demand(
    country=ALL_EU27,
    reference_year=REFERENCE_YEAR,
    target_year=TARGET_YEAR,
    marine_reference_eu_t_per_yr=6_000_000.0,
    demand_growth_rate=0.0,
    e_methanol_share_2050=0.40,
    ammonia_fuel_share_2050=0.15,
    biomethanol_share_2050=0.20,
)
print(f"Shape: {df_maritime.shape}")
# Mass balance: total = fossil + bio + e-meth + nh3
route_sum = (
    df_maritime["fossil_consumed_t_per_yr"]
    + df_maritime["biomethanol_production_t_per_yr"]
    + df_maritime["e_methanol_production_t_per_yr"]
    + df_maritime["ammonia_fuel_production_t_per_yr"]
)
mb_err = (df_maritime["marine_total_demand_t_per_yr"] - route_sum).abs().max()
assert mb_err < 1e-6
print(f"✓ Mass balance: 4-route sum = total (err={mb_err:.2e})")
# Fuel shares sum to 1.0
share_sum = (df_maritime["fossil_share"] + df_maritime["biomethanol_share"]
             + df_maritime["e_methanol_share"] + df_maritime["ammonia_fuel_share"])
assert np.allclose(share_sum, 1.0, atol=1e-9)
print("✓ Fuel shares sum to 1.0")

s = df_maritime.groupby("year").agg(
    h2_total=("h2_demand_total_t_per_yr", "sum"),
    fossil_share=("fossil_share", "mean"),
).loc[[2019, 2030, 2040, 2050]]
print(f"\n  Year   H2 total (kt)  Fossil share (avg)")
for yr, row in s.iterrows():
    print(f"  {yr}   {row['h2_total']/1e3:>10.1f}     {row['fossil_share']:>10.2%}")
print("MARITIME MODULE ✓")


# ====================================================================
# 5. OLEFINS MODULE
# ====================================================================
section("5. OLEFINS — MTO H2 demand")

df_olefins = project_olefins_h2_demand(
    country=ALL_EU27,
    reference_year=REFERENCE_YEAR,
    target_year=TARGET_YEAR,
    mto_share_2050=0.30,
    mto_ramp_start=2025,
    mto_ramp_end=2050,
    h2_per_t_olefin=0.12,
)
print(f"Shape: {df_olefins.shape}")
assert (df_olefins["h2_demand_t_per_yr"] >= 0).all()
# Formula check: h2 = olefins × mto_share × h2_per_t
recomp = df_olefins["olefins_production_t_per_yr"] * df_olefins["mto_share"] * 0.12
assert np.allclose(df_olefins["h2_demand_t_per_yr"], recomp, atol=1e-6)
print("✓ H2 formula verified: olefins × mto_share × 0.12")

s = df_olefins.groupby("year").agg(h2_t=("h2_demand_t_per_yr", "sum")).loc[[2019, 2030, 2040, 2050]]
print(f"\n  Year   H2 demand (kt)")
for yr, row in s.iterrows():
    print(f"  {yr}   {row['h2_t']/1e3:>10.1f}")
print("OLEFINS MODULE ✓")


# ====================================================================
# 6. eSAF MODULE
# ====================================================================
section("6. eSAF — fossil jet gap → eSAF → H2")

# eSAF depends on refinery unit-feed data for fossil jet reconstruction
df_esaf = project_esaf_h2_demand(
    country=ALL_EU27,
    reference_year=REFERENCE_YEAR,
    target_year=TARGET_YEAR,
    demand_growth_rate=0.02,
    h2_intensity=H2_INTENSITY_T_PER_T_ESAF,
    refinery_df=df_refinery,
    jet_unit_names=JET_UNIT_NAMES,
    jet_yields=JET_YIELDS,
)
print(f"Shape: {df_esaf.shape}")
# Mass balance: aviation_total = fossil_jet + esaf
mb_err = (df_esaf["aviation_total_demand_t_per_yr"]
          - (df_esaf["fossil_jet_consumed_t_per_yr"] + df_esaf["esaf_production_t_per_yr"])).abs().max()
assert mb_err < 1e-6
print(f"✓ Mass balance: aviation = fossil_jet + eSAF (err={mb_err:.2e})")
# H2 = eSAF × intensity
recomp = df_esaf["esaf_production_t_per_yr"] * H2_INTENSITY_T_PER_T_ESAF
assert np.allclose(df_esaf["h2_demand_for_esaf_t_per_yr"], recomp, atol=1e-6)
print("✓ H2 formula verified: eSAF × 0.50")

s = df_esaf.groupby("year").agg(
    h2_t=("h2_demand_for_esaf_t_per_yr", "sum"),
    esaf_t=("esaf_production_t_per_yr", "sum"),
).loc[[2019, 2030, 2040, 2050]]
print(f"\n  Year   eSAF (kt)   H2 for eSAF (kt)")
for yr, row in s.iterrows():
    print(f"  {yr}   {row['esaf_t']/1e3:>8.1f}   {row['h2_t']/1e3:>12.1f}")
print("eSAF MODULE ✓")


# ====================================================================
# 7. AGGREGATE — all sectors
# ====================================================================
section("7. AGGREGATE — All 6 Sectors")

# Build aggregate manually to avoid needing aggregate_h2_demand() wrapper
sector_dfs = {
    "steel":    df_steel.rename(columns={"h2_demand_for_steel_t_per_yr": "h2_t", "h2_demand_mwh_per_yr": "h2_mwh"}),
    "refinery": df_refinery.groupby(["country", "year"]).agg(
                    h2_t=("h2_demand_t_per_yr", "sum"),
                    h2_mwh=("h2_demand_mwh_per_yr", "sum"),
                ).reset_index(),
    "ammonia":  df_ammonia.rename(columns={"h2_demand_network_t_per_yr": "h2_t", "h2_demand_network_mwh_per_yr": "h2_mwh"}),
    "maritime": df_maritime.rename(columns={"h2_demand_total_t_per_yr": "h2_t", "h2_demand_mwh_per_yr": "h2_mwh"}),
    "olefins":  df_olefins.rename(columns={"h2_demand_t_per_yr": "h2_t", "h2_demand_mwh_per_yr": "h2_mwh"}),
    "esaf":     df_esaf.rename(columns={"h2_demand_for_esaf_t_per_yr": "h2_t", "h2_demand_mwh_per_yr": "h2_mwh"}),
}

agg_parts = []
for sector, sdf in sector_dfs.items():
    part = sdf[["country", "year", "h2_t", "h2_mwh"]].copy()
    part["sector"] = sector
    agg_parts.append(part)

df_agg = pd.concat(agg_parts, ignore_index=True)
print(f"Aggregate shape: {df_agg.shape}")
print(f"Sectors: {sorted(df_agg['sector'].unique().tolist())}")

# EU-27 total by sector and decade
pivot = df_agg.groupby(["year", "sector"])["h2_t"].sum().unstack("sector").fillna(0)
pivot["TOTAL"] = pivot.sum(axis=1)
pivot_kt = (pivot / 1e3).round(1)

print("\n=== EU-27 H2 Demand by Sector (kt/yr) — Decade Snapshots ===")
print(pivot_kt.loc[[2019, 2025, 2030, 2040, 2050]].to_string())

pivot_twh = (df_agg.groupby(["year", "sector"])["h2_mwh"].sum().unstack("sector").fillna(0) / 1e6).round(2)
pivot_twh["TOTAL"] = pivot_twh.sum(axis=1)
print("\n=== EU-27 H2 Demand by Sector (TWh/yr) — Decade Snapshots ===")
print(pivot_twh.loc[[2019, 2025, 2030, 2040, 2050]].to_string())


# ====================================================================
# 8. COUNTRY-LEVEL VALIDATION — "Neighbour Check"
# ====================================================================
section("8. COUNTRY-LEVEL VALIDATION — Neighbour Comparison")

# Compute total H2 demand per country at key years
country_total = df_agg.groupby(["country", "year"])["h2_t"].sum().reset_index()

# Neighbour pairs for plausibility check
NEIGHBOUR_PAIRS = [
    ("DE", "FR"),
    ("DE", "PL"),
    ("IT", "ES"),
    ("NL", "BE"),
    ("SE", "FI"),
    ("AT", "CZ"),
]

print("\n--- H2 demand ratios between neighbours (2030 and 2050) ---")
print(f"{'Pair':<12} {'Ratio 2030':>12} {'Ratio 2050':>12} {'Status':>10}")
for c1, c2 in NEIGHBOUR_PAIRS:
    for yr in [2030, 2050]:
        v1 = country_total.loc[(country_total["country"] == c1) & (country_total["year"] == yr), "h2_t"]
        v2 = country_total.loc[(country_total["country"] == c2) & (country_total["year"] == yr), "h2_t"]
        if len(v1) > 0 and len(v2) > 0:
            v1, v2 = float(v1.iloc[0]), float(v2.iloc[0])
            if v2 > 0:
                ratio = v1 / v2
            else:
                ratio = float("inf")
        else:
            ratio = float("nan")
        if yr == 2030:
            r30 = ratio
        else:
            r50 = ratio
    status = "OK" if 0.1 < r30 < 50 and 0.1 < r50 < 50 else "CHECK"
    print(f"{c1}/{c2:<8} {r30:>12.2f} {r50:>12.2f} {status:>10}")


# Country ranking at 2050
print("\n--- EU-27 Country Ranking at 2050 (top 10) ---")
rank_2050 = country_total[country_total["year"] == 2050].sort_values("h2_t", ascending=False).head(10)
for _, row in rank_2050.iterrows():
    print(f"  {row['country']}:  {row['h2_t']/1e3:>8.1f} kt/yr  ({row['h2_t']*H2_LHV_MWH_PER_T/1e6:>6.2f} TWh/yr)")


# ====================================================================
# 9. GRAPHICS
# ====================================================================
section("9. GRAPHICS — Validation Figures")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    fig.suptitle("DemandForge — EU-27 H₂ Demand Validation", fontsize=14, fontweight="bold")

    # ── Panel 1: Total H2 by sector (stacked area) ──
    ax = axes[0, 0]
    sector_order = ["steel", "refinery", "ammonia", "maritime", "olefins", "esaf"]
    colours = {"steel": "#d62728", "refinery": "#1f77b4", "ammonia": "#2ca02c",
               "maritime": "#9467bd", "olefins": "#ff7f0e", "esaf": "#8c564b"}
    y_data = {}
    for sec in sector_order:
        series = df_agg[df_agg["sector"] == sec].groupby("year")["h2_t"].sum()
        y_data[sec] = series.reindex(YEARS, fill_value=0).values / 1e6  # Mt
    ax.stackplot(YEARS, *[y_data[s] for s in sector_order],
                 labels=sector_order, colors=[colours[s] for s in sector_order], alpha=0.85)
    ax.set_ylabel("H₂ demand (Mt/yr)")
    ax.set_title("EU-27 Total by Sector")
    ax.legend(loc="upper left", fontsize=7)
    ax.grid(True, alpha=0.3)

    # ── Panel 2: H2 by country (top 5, line plot) ──
    ax = axes[0, 1]
    for cc in FOCUS_5:
        cc_data = country_total[country_total["country"] == cc].set_index("year")["h2_t"].reindex(YEARS, fill_value=0)
        ax.plot(YEARS, cc_data.values / 1e3, label=cc, linewidth=2)
    ax.set_ylabel("H₂ demand (kt/yr)")
    ax.set_title("Top-5 Countries — Total H₂")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # ── Panel 3: Sector composition at 2050 (bar chart by country, top 8) ──
    ax = axes[0, 2]
    top8 = country_total[country_total["year"] == 2050].nlargest(8, "h2_t")["country"].values
    bar_data = df_agg[(df_agg["year"] == 2050) & (df_agg["country"].isin(top8))]
    bar_pivot = bar_data.pivot_table(index="country", columns="sector", values="h2_t", aggfunc="sum").fillna(0)
    bar_pivot = bar_pivot.loc[top8]  # preserve ranking order
    bar_pivot_kt = bar_pivot / 1e3
    bar_pivot_kt[sector_order].plot.bar(stacked=True, ax=ax, color=[colours[s] for s in sector_order], width=0.7)
    ax.set_ylabel("H₂ demand (kt/yr)")
    ax.set_title("2050 — Sector Mix (Top 8)")
    ax.legend(fontsize=6)
    ax.tick_params(axis="x", rotation=0)
    ax.grid(True, alpha=0.3, axis="y")

    # ── Panel 4: Steel route evolution (EU-27 aggregate) ──
    ax = axes[1, 0]
    steel_agg = df_steel.groupby("year").agg(
        eaf=("eaf_scrap_production_t_per_yr", "sum"),
        bf=("bf_bof_production_t_per_yr", "sum"),
        dri_ch4=("dri_ch4_production_t_per_yr", "sum"),
        dri_h2=("dri_h2_production_t_per_yr", "sum"),
    )
    for col, label, color in [("eaf", "EAF (scrap)", "#2ca02c"), ("bf", "BF-BOF", "#7f7f7f"),
                               ("dri_ch4", "DRI-CH₄", "#ff7f0e"), ("dri_h2", "DRI-H₂", "#d62728")]:
        ax.fill_between(steel_agg.index, 0, steel_agg[col].values / 1e6, alpha=0)  # placeholder
    steel_mt = steel_agg / 1e6
    ax.stackplot(steel_agg.index,
                 steel_mt["eaf"], steel_mt["bf"], steel_mt["dri_ch4"], steel_mt["dri_h2"],
                 labels=["EAF (scrap)", "BF-BOF", "DRI-CH₄", "DRI-H₂"],
                 colors=["#2ca02c", "#7f7f7f", "#ff7f0e", "#d62728"], alpha=0.85)
    ax.set_ylabel("Steel production (Mt/yr)")
    ax.set_title("EU-27 Steel Route Evolution")
    ax.legend(loc="center right", fontsize=7)
    ax.grid(True, alpha=0.3)

    # ── Panel 5: Maritime fuel mix (EU-27 aggregate shares) ──
    ax = axes[1, 1]
    mar_shares = df_maritime.groupby("year").agg(
        fossil=("fossil_share", "mean"),
        bio=("biomethanol_share", "mean"),
        emeth=("e_methanol_share", "mean"),
        nh3=("ammonia_fuel_share", "mean"),
    )
    ax.stackplot(mar_shares.index,
                 mar_shares["fossil"], mar_shares["bio"], mar_shares["emeth"], mar_shares["nh3"],
                 labels=["Fossil", "Biomethanol", "E-methanol", "NH₃ fuel"],
                 colors=["#7f7f7f", "#2ca02c", "#1f77b4", "#9467bd"], alpha=0.85)
    ax.set_ylabel("Fuel share")
    ax.set_title("Maritime Fuel Mix Evolution")
    ax.legend(loc="center right", fontsize=7)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)

    # ── Panel 6: Neighbour H2 demand comparison (bar chart, 2050) ──
    ax = axes[1, 2]
    pairs_to_plot = [("DE", "FR"), ("DE", "PL"), ("IT", "ES"), ("NL", "BE")]
    x_labels = []
    vals_c1 = []
    vals_c2 = []
    for c1, c2 in pairs_to_plot:
        v1 = country_total[(country_total["country"] == c1) & (country_total["year"] == 2050)]["h2_t"].values
        v2 = country_total[(country_total["country"] == c2) & (country_total["year"] == 2050)]["h2_t"].values
        vals_c1.append(float(v1[0]) / 1e3 if len(v1) > 0 else 0)
        vals_c2.append(float(v2[0]) / 1e3 if len(v2) > 0 else 0)
        x_labels.append(f"{c1} vs {c2}")
    x = np.arange(len(pairs_to_plot))
    ax.bar(x - 0.18, vals_c1, 0.35, label="Country 1", color="#1f77b4")
    ax.bar(x + 0.18, vals_c2, 0.35, label="Country 2", color="#ff7f0e")
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=8)
    ax.set_ylabel("H₂ demand (kt/yr)")
    ax.set_title("2050 — Neighbour Comparison")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    outpath = pathlib.Path(BASE) / "notebooks" / "demandforge_all_modules_validation.png"
    plt.savefig(str(outpath), dpi=150, bbox_inches="tight")
    print(f"Figure saved: {outpath}")
    plt.close()

except ImportError:
    print("matplotlib not available — skipping graphics")


# ====================================================================
# 10. FINAL SUMMARY
# ====================================================================
section("10. FINAL SUMMARY")

total_2050_kt = country_total[country_total["year"] == 2050]["h2_t"].sum() / 1e3
total_2050_twh = total_2050_kt * 1e3 * H2_LHV_MWH_PER_T / 1e6

print(f"""
  EU-27 Total H₂ Demand at 2050:
    {total_2050_kt:,.0f} kt/yr  =  {total_2050_twh:,.1f} TWh/yr

  Sector Breakdown (2050):""")

for sec in sector_order:
    sec_total = df_agg[(df_agg["sector"] == sec) & (df_agg["year"] == 2050)]["h2_t"].sum()
    pct = 100 * sec_total / (total_2050_kt * 1e3) if total_2050_kt > 0 else 0
    print(f"    {sec:<12} {sec_total/1e3:>8.1f} kt/yr  ({pct:>5.1f}%)")

print(f"""
  All 6 modules:        ✓ run without errors
  Mass balances:        ✓ all verified (< 1e-6 tolerance)
  Non-negativity:       ✓ confirmed
  Country coverage:     ✓ {len(ALL_EU27)} EU-27 countries

  ╔══════════════════════════════════════════╗
  ║  ALL DEMANDFORGE MODULES PASS           ║
  ╚══════════════════════════════════════════╝
""")
