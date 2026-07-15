#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_observatory.py — CLEVER 2050 adequacy observatory regenerator
=======================================================================

Re-generates the 4.3 MB `index.html` adequacy observatory by:

  1. Loading POMMES results from NetCDF (solution_2050.nc, input_dataset_2050.nc)
     and CSV exports (adequacy_dashboard, conversion_capacity, prices, exante).
  2. Deriving one unified `country_stats` dictionary (30 countries).
  3. Rebuilding all 17 Matplotlib PNG figures, 6 SVG choropleth maps, the
     adequacy table §4.3 and the JavaScript canvas payloads (DISPATCH,
     COUNTRY_STATS, PRICE_DATA).
  4. Using `index_fresh.html` as a template: surgically replacing each data
     URI, SVG block, table and JS literal by its post-bugfix counterpart.
     The CSS, layout, text content and hand-rolled canvas logic are
     preserved byte-for-byte.

Usage
-----
    python notebooks/generate_observatory.py \
        --nc-dir results/diagnostics \
        --csv-dir results/export \
        --template index_fresh.html \
        --variant flex0pct \
        --output index.html

All arguments have sensible defaults relative to the repo root.  The
script is fully deterministic: same inputs -> byte-identical output.

Dependencies: pandas, numpy, xarray, matplotlib, pyshp.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import re
import sys
import textwrap
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    import xarray as xr
except ImportError:
    sys.stderr.write("ERROR: xarray is required (pip install xarray netCDF4).\n")
    raise

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# =====================================================================
# Helper function for robust coordinate matching
# =====================================================================

def _coord_match(coord_vals, target: str) -> Optional[str]:
    """Find *target* in an xarray coordinate, tolerant to bytes/case/whitespace.
    Returns the exact coordinate value as it appears in the dataset, or None."""
    t = target.strip().lower()
    for v in coord_vals:
        s = str(v).strip().lower()
        if s == t or s == f"b'{t}'" or s.strip("b'\"") == t:
            return v  # return original (not lowered) so .sel() works
    return None

# =====================================================================
# Constants — palette, labels, layout (kept in sync with index.html JS)
# =====================================================================

TECH_COLORS: Dict[str, str] = {
    "Solar":                  "#f6b419",
    "Solar_expansion":        "#fbbf24",
    "Wind_Onshore":           "#4caf50",
    "Wind_Onshore_expansion": "#86efac",
    "Wind_Offshore":          "#0d6efd",
    "Wind_Offshore_expansion":"#60a5fa",
    "RoR_Hydro":              "#17a2b8",
    "Reservoir_Hydro_Plant":  "#1e90ff",
    "Reservoir_Hydro_Inflow": "#3ab0ff",
    "Gas":                    "#ef4444",
    "Hydrogen_power_plant":   "#a855f7",
    "hydrogen_power_plant":   "#7c3aed",
    "electrolysis":           "#10b981",
    "Waste":                  "#64748b",
}

TECH_LABELS: Dict[str, str] = {
    "Solar":                  "Solaire",
    "Solar_expansion":        "Solaire (expansion)",
    "Wind_Onshore":           "Eolien terrestre",
    "Wind_Onshore_expansion": "Eolien terr. (exp.)",
    "Wind_Offshore":          "Eolien en mer",
    "Wind_Offshore_expansion":"Eolien mer (exp.)",
    "RoR_Hydro":              "Hydro au fil de l'eau",
    "Reservoir_Hydro_Plant":  "Hydro lac (turbinage)",
    "Reservoir_Hydro_Inflow": "Hydro lac (apports)",
    "Gas":                    "Gaz",
    "Hydrogen_power_plant":   "H2 CCGT (existant)",
    "hydrogen_power_plant":   "H2 CCGT (neuf)",
    "electrolysis":           "Electrolyse",
    "Waste":                  "Dechets",
}

STORAGE_COLORS: Dict[str, str] = {
    "H2_SaltCavern":         "#a855f7",
    "h2_storage":            "#a855f7",
    "Pumped_Hydro":          "#1e90ff",
    "Battery_4h":            "#fb923c",
    "Battery_1h":            "#fcd34d",
    "Reservoir_Hydro_Store": "#0891b2",
}

ALL_COUNTRIES: List[str] = [
    "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES",
    "FI", "FR", "GB", "GR", "HR", "HU", "IE", "IT", "LT", "LU",
    "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
]

ISO2_TO_ISO3: Dict[str, str] = {
    "AT": "AUT", "BE": "BEL", "BG": "BGR", "CH": "CHE", "CY": "CYP",
    "CZ": "CZE", "DE": "DEU", "DK": "DNK", "EE": "EST", "ES": "ESP",
    "FI": "FIN", "FR": "FRA", "GB": "GBR", "GR": "GRC", "HR": "HRV",
    "HU": "HUN", "IE": "IRL", "IT": "ITA", "LT": "LTU", "LU": "LUX",
    "LV": "LVA", "MT": "MLT", "NL": "NLD", "NO": "NOR", "PL": "POL",
    "PT": "PRT", "RO": "ROU", "SE": "SWE", "SI": "SVN", "SK": "SVK",
}

COUNTRY_NAMES_FR: Dict[str, str] = {
    "AT": "Autriche",    "BE": "Belgique",    "BG": "Bulgarie",  "CH": "Suisse",
    "CY": "Chypre",      "CZ": "Tchequie",    "DE": "Allemagne", "DK": "Danemark",
    "EE": "Estonie",     "ES": "Espagne",     "FI": "Finlande",  "FR": "France",
    "GB": "Royaume-Uni", "GR": "Grece",       "HR": "Croatie",   "HU": "Hongrie",
    "IE": "Irlande",     "IT": "Italie",      "LT": "Lituanie",  "LU": "Luxembourg",
    "LV": "Lettonie",    "MT": "Malte",       "NL": "Pays-Bas",  "NO": "Norvege",
    "PL": "Pologne",     "PT": "Portugal",    "RO": "Roumanie",  "SE": "Suede",
    "SI": "Slovenie",    "SK": "Slovaquie",
}

# Population 2023 (millions) — sources publiques Eurostat (EU+UK), SSB (NO), BFS (CH).
# Utilisee uniquement pour les metriques per-capita de la signature sufficiency §15.
POPULATION_M_2023: Dict[str, float] = {
    "AT":  9.10, "BE": 11.74, "BG":  6.45, "CH":  8.85, "CY":  0.92,
    "CZ": 10.83, "DE": 84.36, "DK":  5.93, "EE":  1.37, "ES": 48.09,
    "FI":  5.56, "FR": 68.07, "GB": 67.60, "GR": 10.41, "HR":  3.85,
    "HU":  9.60, "IE":  5.28, "IT": 58.85, "LT":  2.86, "LU":  0.66,
    "LV":  1.88, "MT":  0.56, "NL": 17.81, "NO":  5.53, "PL": 36.75,
    "PT": 10.47, "RO": 19.05, "SE": 10.55, "SI":  2.12, "SK":  5.43,
}

# VRE technos considered for Dunkelflaute & residual load
VRE_TECHS_CORE: Tuple[str, ...] = (
    "Solar", "Solar_expansion",
    "Wind_Onshore", "Wind_Onshore_expansion",
    "Wind_Offshore", "Wind_Offshore_expansion",
    "RoR_Hydro",
)
VRE_TECHS_EXTENDED: Tuple[str, ...] = VRE_TECHS_CORE + (
    "Reservoir_Hydro_Inflow",
)

# ---------------------------------------------------------------------
# External annuity reference (for the "LCOE reconstruit" section).
# Source: JRC ETRI 2014 + Danish Energy Agency Technology Catalogue
# projections towards 2050, flat WACC = 5 %.
#
#   annuity (EUR/kW/year) = overnight * (WACC / (1 - (1+WACC)^-lifetime))
#                           + fixed O&M (EUR/kW/year)
#
# These coefficients are intentionally hard-coded so that the LCOE
# reconstructed here is independent of the POMMES planning_* costs
# (which are zero in a fixed-capacity CLEVER 2050 run).
# ---------------------------------------------------------------------
ANNUITY_POWER_EUR_KW_YR: Dict[str, float] = {
    "solar":      33.7,   # Solar PV utility — 380 EUR/kW, 30 y,  9 FOM
    "wind_on":    73.6,   # Wind onshore     — 900 EUR/kW, 30 y, 15 FOM
    "wind_off":  144.2,   # Wind offshore    — 1600 EUR/kW, 30 y, 40 FOM
    "hydro":     150.0,   # Hydro (RoR + reservoir mix), long lifetime
    "gas":        71.3,   # CCGT gaz         — 750 EUR/kW, 25 y, 18 FOM
    "h2":         80.4,   # Turbine H2       — 850 EUR/kW, 25 y, 20 FOM
    "battery_p":  21.4,   # Battery power    — 170 EUR/kW, 15 y,  5 FOM
    "phs_p":      40.0,   # Pumped hydro power — long lifetime, 2.5 FOM
    "h2sto_p":    15.0,   # H2 salt cavern power (compressor+injection)
    "waste":     280.0,   # WtE              — 2800 EUR/kW, 25 y, 80 FOM
    "electrolysis": 58.0, # Electrolyser     — 500 EUR/kW, 20 y, 20 FOM
}
# NB: all values in EUR per kWh of energy storage capacity per year
# (battery: ~150 EUR/kWh * CRF15y/5% + 2 FOM ≈ 16.4;
#  PHS: reservoir is nearly free marginal cost — ~0.3 EUR/kWh/yr;
#  H2 salt cavern: ~2 EUR/kWh overnight * CRF40y ≈ 0.12 EUR/kWh/yr).
ANNUITY_ENERGY_EUR_KWH_YR: Dict[str, float] = {
    "battery":   16.4,    # Battery energy   — 150 EUR/kWh, 15 y, 2 FOM
    "phs":        0.30,   # PHS reservoir    — negligible annuity
    "h2sto":      0.12,   # H2 salt cavern   — ~2 EUR/kWh, 40 y
}

MPL_DPI = 110
MPL_BG = "#ffffff"
MPL_FG = "#1e293b"
MPL_MUTED = "#64748b"
MPL_GRID = "#e2e8f0"

plt.rcParams.update({
    "figure.dpi": MPL_DPI,
    "savefig.dpi": MPL_DPI,
    "savefig.bbox": "tight",
    "savefig.facecolor": MPL_BG,
    "figure.facecolor": MPL_BG,
    "axes.facecolor": MPL_BG,
    "axes.edgecolor": MPL_MUTED,
    "axes.labelcolor": MPL_FG,
    "axes.titlecolor": MPL_FG,
    "axes.grid": True,
    "grid.color": MPL_GRID,
    "grid.linewidth": 0.5,
    "text.color": MPL_FG,
    "xtick.color": MPL_MUTED,
    "ytick.color": MPL_MUTED,
    "font.family": "sans-serif",
    "font.size": 10,
})

# Representative weeks used for canvas price/dispatch zoom views
WEEK_WINTER_START_HOUR = 14 * 24   # Mon 15 Jan
WEEK_WINTER_END_HOUR   = 21 * 24   # Sun 21 Jan
WEEK_SUMMER_START_HOUR = 196 * 24  # Mon 16 Jul
WEEK_SUMMER_END_HOUR   = 203 * 24  # Sun 22 Jul


# =====================================================================
# CLI
# =====================================================================

def _resolve_default(root: Path, rel: str) -> Path:
    return (root / rel).resolve()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    repo = here.parent  # notebooks/ -> repo

    p = argparse.ArgumentParser(
        description="Regenerate the CLEVER 2050 adequacy observatory (index.html).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--nc-dir",    type=Path,
                   default=_resolve_default(repo, "results/diagnostics"))
    p.add_argument("--csv-dir",   type=Path,
                   default=_resolve_default(repo, "results/export"))
    p.add_argument("--template",  type=Path,
                   default=_resolve_default(repo, "index_fresh.html"),
                   help="Reference HTML file used as the iso-structure template")
    p.add_argument("--output",    type=Path,
                   default=_resolve_default(repo, "index.html"))
    p.add_argument("--variant",   choices=["flex0pct", "flex23pct"],
                   default="flex0pct")
    p.add_argument("--shapefile", type=Path,
                   default=_resolve_default(
                       repo, "ne_110m_admin_0_countries/ne_110m_admin_0_countries.shp"))
    p.add_argument("--year",      type=int, default=2050)
    p.add_argument("--figures-dir", type=Path, default=None,
                   help="Optional folder where all PNG figures, SVG maps and the "
                        "adequacy table are exported as standalone files "
                        "(in addition to the HTML observatory).")
    p.add_argument("--dry-run",   action="store_true")
    p.add_argument("--verbose",   "-v", action="count", default=0)
    return p.parse_args(argv)


def log(args: argparse.Namespace, level: int, *msg) -> None:
    if args.verbose >= level:
        print("  " * (level - 1) + " ".join(str(m) for m in msg), flush=True)


# =====================================================================
# Data loading
# =====================================================================

@dataclass
class RawData:
    sol:  xr.Dataset
    inp:  xr.Dataset
    dual: Optional[xr.Dataset] = None
    adequacy:      pd.DataFrame = field(default_factory=pd.DataFrame)
    conv_capacity: pd.DataFrame = field(default_factory=pd.DataFrame)
    prices:        pd.DataFrame = field(default_factory=pd.DataFrame)
    exante:        pd.DataFrame = field(default_factory=pd.DataFrame)
    year:          int = 2050
    variant:       str = "flex0pct"
    demand_hourly: Optional[pd.DataFrame] = None
    prices_wide:   Optional[pd.DataFrame] = None
    dispatch_twh:  Optional[pd.DataFrame] = None
    h2_demand_hourly: Optional[pd.DataFrame] = None
    h2_dispatch_twh:  Optional[pd.DataFrame] = None


def load_data(args: argparse.Namespace) -> RawData:
    suffix = f"noramp_{args.variant}"
    nc_sol = args.nc_dir / "solution_2050.nc"
    nc_inp = args.nc_dir / "input_dataset_2050.nc"
    nc_dual = args.nc_dir / "dual_2050.nc"

    for pth in (nc_sol, nc_inp):
        if not pth.exists():
            raise FileNotFoundError(f"Missing NetCDF file: {pth}")

    log(args, 1, f"Opening {nc_sol.name} ...")
    sol = xr.open_dataset(nc_sol)
    log(args, 1, f"Opening {nc_inp.name} ...")
    inp = xr.open_dataset(nc_inp)
    dual = None
    if nc_dual.exists():
        log(args, 2, f"Opening {nc_dual.name} (optional) ...")
        try:
            dual = xr.open_dataset(nc_dual)
        except Exception as e:
            log(args, 1, f"  (ignored, could not open dual file: {e})")

    def _csv(stub: str) -> pd.DataFrame:
        p = args.csv_dir / f"{stub}_{suffix}.csv"
        if not p.exists():
            raise FileNotFoundError(f"Missing CSV: {p}")
        log(args, 2, f"Reading {p.name}")
        return pd.read_csv(p)

    data = RawData(
        sol=sol, inp=inp, dual=dual,
        adequacy=_csv("adequacy_dashboard"),
        conv_capacity=_csv("conversion_capacity"),
        prices=_csv("prices"),
        exante=_csv("exante_adequacy"),
        year=args.year,
        variant=args.variant,
    )
    log(args, 1, "Loaded:",
        f"sol.vars={len(sol.data_vars)},",
        f"inp.vars={len(inp.data_vars)},",
        f"adequacy={len(data.adequacy)},",
        f"conv_cap={len(data.conv_capacity)},",
        f"prices rows={len(data.prices):,},",
        f"exante={len(data.exante)}")
    return data


# =====================================================================
# Country-level derived statistics
# =====================================================================

def _nc_demand_wide(inp: xr.Dataset) -> pd.DataFrame:
    """Return demand as a DataFrame: index=hour (0..8759), columns=area (MW)."""
    dem = inp["demand"]
    if "resource" in dem.dims:
        if "electricity" in list(dem["resource"].values):
            dem = dem.sel(resource="electricity")
        else:
            dem = dem.sum(dim="resource")
    if "year_op" in dem.dims:
        dem = dem.squeeze("year_op", drop=True)
    # Bring to (hour, area)
    df = dem.to_pandas()
    # xarray .to_pandas() returns DataFrame for 2D; order depends on dim order
    if df.index.name == "area" or (df.shape[0] == len(inp["area"]) and df.shape[1] == 8760):
        df = df.T
    df.index.name = "hour"
    df.columns.name = "area"
    return df.astype(float)


def _prices_wide(prices: pd.DataFrame) -> pd.DataFrame:
    df = prices
    if "resource" in df.columns:
        df = df[df["resource"] == "electricity"]
    wide = df.pivot_table(index="hour", columns="area", values="value", aggfunc="mean")
    wide = wide.sort_index()
    return wide.astype(float)


def _dispatch_twh(sol: xr.Dataset) -> pd.DataFrame:
    gen = sol["operation_conversion_power"]
    if "year_op" in gen.dims:
        gen = gen.squeeze("year_op", drop=True)
    annual = gen.sum(dim="hour") / 1e6  # MWh -> TWh
    return annual.to_pandas()  # rows=area, cols=conversion_tech


def compute_country_stats(data: RawData) -> Dict[str, Dict[str, float]]:
    sol, inp, adq, excap = data.sol, data.inp, data.adequacy, data.exante

    demand_wide = _nc_demand_wide(inp)
    prices_wide = _prices_wide(data.prices)
    dispatch    = _dispatch_twh(sol)

    data.demand_hourly = demand_wide
    data.prices_wide   = prices_wide
    data.dispatch_twh  = dispatch

    # ── Diagnostic: show available tech/resource names ──
    print(f"  [diag] dispatch techs ({len(dispatch.columns)}): {list(dispatch.columns)}")
    print(f"  [diag] demand dims: {list(inp['demand'].dims)}")
    if "resource" in inp["demand"].dims:
        print(f"  [diag] demand resources: {list(inp['demand']['resource'].values)}")

    # ── Hydrogen demand (if present in coupled model) ──
    h2_demand_wide = None
    if "resource" in inp["demand"].dims:
        _h2_res = _coord_match(inp["demand"]["resource"].values, "hydrogen")
        if _h2_res is not None:
            h2_dem = inp["demand"].sel(resource=_h2_res)
            if "year_op" in h2_dem.dims:
                h2_dem = h2_dem.squeeze("year_op", drop=True)
            h2_demand_wide = h2_dem.to_pandas()
            if h2_demand_wide.index.name == "area" or (h2_demand_wide.shape[0] == len(inp["area"]) and h2_demand_wide.shape[1] == 8760):
                h2_demand_wide = h2_demand_wide.T
            h2_demand_wide.index.name = "hour"
            h2_demand_wide.columns.name = "area"
            data.h2_demand_hourly = h2_demand_wide

    stor_pwr = sol["operation_storage_power_capacity"].squeeze("year_op", drop=True)
    stor_nrg = sol["operation_storage_energy_capacity"].squeeze("year_op", drop=True)
    stor_pwr_df = stor_pwr.to_pandas()
    stor_nrg_df = stor_nrg.to_pandas()

    conv_pwr = sol["operation_conversion_power_capacity"].squeeze("year_op", drop=True)
    conv_pwr_df = conv_pwr.to_pandas()

    # Diagnostic: show capacity techs available
    print(f"  [diag] capacity techs ({len(conv_pwr_df.columns)}): {list(conv_pwr_df.columns)}")

    # Resolve actual tech names in capacity (tolerant to case/encoding)
    _cap_cols = list(conv_pwr_df.columns)
    def _resolve_cap(target):
        m = _coord_match(_cap_cols, target)
        return m if m is not None else target
    _c_electrolysis = _resolve_cap("electrolysis")
    _c_h2pp_upper = _resolve_cap("Hydrogen_power_plant")
    _c_h2pp_lower = _resolve_cap("hydrogen_power_plant")
    _c_solar_exp = _resolve_cap("Solar_expansion")
    _c_windon_exp = _resolve_cap("Wind_Onshore_expansion")
    _c_windoff_exp = _resolve_cap("Wind_Offshore_expansion")

    # Transport flows: net import per country (TWh/year)
    net_import_twh: Dict[str, float] = {c: 0.0 for c in ALL_COUNTRIES}
    try:
        tpow = sol["operation_transport_power"]
        if "year_op" in tpow.dims:
            tpow = tpow.squeeze("year_op", drop=True)
        if "transport_tech" in tpow.dims:
            tpow = tpow.squeeze("transport_tech", drop=True)
        flow_sum = tpow.sum(dim="hour").to_pandas() / 1e6  # TWh per link
        for lk, val in flow_sum.items():
            parts = str(lk).split("_")
            if len(parts) >= 3:
                a, b = parts[1], parts[2]
                if a in net_import_twh:
                    net_import_twh[a] -= float(val)
                if b in net_import_twh:
                    net_import_twh[b] += float(val)
    except Exception:
        pass

    # H2 transport flows (h2_pipeline links)
    h2_net_import_twh: Dict[str, float] = {c: 0.0 for c in ALL_COUNTRIES}
    try:
        tpow_h2 = sol["operation_transport_power"]
        if "year_op" in tpow_h2.dims:
            tpow_h2 = tpow_h2.squeeze("year_op", drop=True)
        if "transport_tech" in tpow_h2.dims:
            _tt_h2 = _coord_match(tpow_h2["transport_tech"].values, "h2_pipeline")
            if _tt_h2 is not None:
                h2_tpow = tpow_h2.sel(transport_tech=_tt_h2)
                h2_flow_sum = h2_tpow.sum(dim="hour").to_pandas() / 1e6
                for lk, val in h2_flow_sum.items():
                    parts = str(lk).split("_")
                    if len(parts) >= 3:
                        a, b = parts[1], parts[2]
                        if a in h2_net_import_twh:
                            h2_net_import_twh[a] -= float(val)
                        if b in h2_net_import_twh:
                            h2_net_import_twh[b] += float(val)
    except Exception:
        pass

    # Resolve actual tech names in dispatch (tolerant to case/encoding)
    _disp_cols = list(dispatch.columns)
    def _resolve_tech(target):
        m = _coord_match(_disp_cols, target)
        return m if m is not None else target
    _t_electrolysis = _resolve_tech("electrolysis")
    _t_h2pp_upper = _resolve_tech("Hydrogen_power_plant")
    _t_h2pp_lower = _resolve_tech("hydrogen_power_plant")
    _t_solar_exp = _resolve_tech("Solar_expansion")
    _t_windon_exp = _resolve_tech("Wind_Onshore_expansion")
    _t_windoff_exp = _resolve_tech("Wind_Offshore_expansion")

    stats: Dict[str, Dict[str, float]] = {}
    for iso in ALL_COUNTRIES:
        if iso in demand_wide.columns:
            dem_series = demand_wide[iso].astype(float).values
        else:
            dem_series = np.zeros(8760)
        demand_twh = float(dem_series.sum() / 1e6)
        peak_mw    = float(dem_series.max()) if dem_series.size else 0.0

        if iso in dispatch.index:
            gen_row = dispatch.loc[iso]
        else:
            gen_row = pd.Series({t: 0.0 for t in dispatch.columns})
        solar_twh   = float(gen_row.get("Solar", 0.0))
        wind_on_twh = float(gen_row.get("Wind_Onshore", 0.0))
        wind_off_twh= float(gen_row.get("Wind_Offshore", 0.0))
        ror_twh     = float(gen_row.get("RoR_Hydro", 0.0))
        hydro_twh   = (float(gen_row.get("Reservoir_Hydro_Plant", 0.0))
                       + float(gen_row.get("Reservoir_Hydro_Inflow", 0.0))
                       + ror_twh)
        gas_twh     = float(gen_row.get("Gas", 0.0))
        h2_twh      = float(gen_row.get(_t_h2pp_upper, 0.0))
        h2_fresh_twh = float(gen_row.get(_t_h2pp_lower, 0.0))
        waste_twh   = float(gen_row.get("Waste", 0.0))
        electrolysis_twh = float(gen_row.get(_t_electrolysis, 0.0))
        solar_exp_twh = float(gen_row.get(_t_solar_exp, 0.0))
        wind_on_exp_twh = float(gen_row.get(_t_windon_exp, 0.0))
        wind_off_exp_twh = float(gen_row.get(_t_windoff_exp, 0.0))
        wind_twh    = wind_on_twh + wind_off_twh + wind_on_exp_twh + wind_off_exp_twh
        total_twh   = float(gen_row.sum())
        vre_twh     = solar_twh + solar_exp_twh + wind_twh + ror_twh
        vre_share   = (vre_twh / total_twh * 100.0) if total_twh > 1e-6 else 0.0
        # H2 demand
        h2_demand_twh = 0.0
        if h2_demand_wide is not None and iso in h2_demand_wide.columns:
            h2_demand_twh = float(h2_demand_wide[iso].sum() / 1e6)

        if iso in conv_pwr_df.index:
            cap_row = conv_pwr_df.loc[iso] / 1000.0
        else:
            cap_row = pd.Series({t: 0.0 for t in conv_pwr_df.columns})
        solar_gw   = float(cap_row.get("Solar", 0.0))
        wind_on_gw = float(cap_row.get("Wind_Onshore", 0.0))
        wind_off_gw= float(cap_row.get("Wind_Offshore", 0.0))
        wind_gw    = wind_on_gw + wind_off_gw
        hydro_gw   = float(cap_row.get("RoR_Hydro", 0.0)
                           + cap_row.get("Reservoir_Hydro_Plant", 0.0)
                           + cap_row.get("Reservoir_Hydro_Inflow", 0.0))
        gas_gw     = float(cap_row.get("Gas", 0.0))
        h2_gw      = float(cap_row.get(_c_h2pp_upper, 0.0))
        h2_fresh_gw = float(cap_row.get(_c_h2pp_lower, 0.0))
        electrolysis_gw = float(cap_row.get(_c_electrolysis, 0.0))
        solar_exp_gw = float(cap_row.get(_c_solar_exp, 0.0))
        wind_on_exp_gw = float(cap_row.get(_c_windon_exp, 0.0))
        wind_off_exp_gw = float(cap_row.get(_c_windoff_exp, 0.0))

        if iso in stor_pwr_df.index:
            spw = stor_pwr_df.loc[iso] / 1000.0
            snr = stor_nrg_df.loc[iso] / 1000.0
        else:
            spw = pd.Series(dtype=float)
            snr = pd.Series(dtype=float)

        # Resolve storage tech names in case of bytes/encoding issues
        _stor_cols = list(spw.index) if hasattr(spw, 'index') else []
        def _resolve_stor(target):
            m = _coord_match(_stor_cols, target) if _stor_cols else None
            return m if m is not None else target
        _s_h2_cavern = _resolve_stor("H2_SaltCavern")
        _s_h2_storage = _resolve_stor("h2_storage")

        batt_gw  = float(spw.get("Battery_1h", 0.0) + spw.get("Battery_4h", 0.0))
        phs_gw   = float(spw.get("Pumped_Hydro", 0.0))
        h2sto_gw = float(spw.get(_s_h2_cavern, 0.0) + spw.get(_s_h2_storage, 0.0))
        batt_gwh   = float(snr.get("Battery_1h", 0.0) + snr.get("Battery_4h", 0.0))
        phs_gwh    = float(snr.get("Pumped_Hydro", 0.0))
        h2sto_gwh  = float(snr.get(_s_h2_cavern, 0.0) + snr.get(_s_h2_storage, 0.0))
        storage_gw_total = float(spw.sum())
        storage_gwh_total = float(snr.sum())
        total_cap_gw = float(cap_row.sum())

        if iso in prices_wide.columns:
            px_series = prices_wide[iso].astype(float).values
        else:
            px_series = np.zeros(8760)
        mean_price_simple = float(px_series.mean())
        if iso in demand_wide.columns and dem_series.sum() > 0:
            dw_price = float(np.sum(px_series * dem_series) / dem_series.sum())
        else:
            dw_price = mean_price_simple

        adq_row = adq[adq["Country"] == iso] if "Country" in adq.columns else pd.DataFrame()
        if len(adq_row):
            ens_gwh  = float(adq_row["ENS (GWh)"].iloc[0])
            lole_h   = float(adq_row["LOLE (h)"].iloc[0])
            peak_ls  = float(adq_row["Peak LS (MW)"].iloc[0])
            max_dual = float(adq_row["Max dual (EUR/MWh)"].iloc[0])
        else:
            ens_gwh = lole_h = peak_ls = max_dual = 0.0

        ex_row = excap[excap["country"] == iso] if "country" in excap.columns else pd.DataFrame()
        if len(ex_row):
            peak_dem_exante = float(ex_row["peak_demand_mw"].iloc[0])
            disp_total      = float(ex_row["dispatchable_total_max_mw"].iloc[0])
            vre_credit      = float(ex_row["vre_credit_mw"].iloc[0])
            ade_margin      = float(ex_row["adequacy_margin_mw"].iloc[0])
        else:
            peak_dem_exante = peak_mw
            disp_total = 0.0
            vre_credit = 0.0
            ade_margin = 0.0

        stats[iso] = dict(
            name_fr=COUNTRY_NAMES_FR.get(iso, iso),
            iso3=ISO2_TO_ISO3.get(iso, iso),
            demand_twh=demand_twh,
            peak_gw=peak_mw / 1000.0,
            load_factor=(dem_series.mean() / peak_mw) if peak_mw > 0 else 0.0,
            gen_total_twh=total_twh,
            vre_twh=vre_twh,
            vre_share_pct=vre_share,
            solar_twh=solar_twh,
            wind_twh=wind_twh,
            wind_on_twh=wind_on_twh,
            wind_off_twh=wind_off_twh,
            hydro_twh=hydro_twh,
            ror_twh=ror_twh,
            gas_twh=gas_twh,
            h2_twh=h2_twh,
            waste_twh=waste_twh,
            solar_gw=solar_gw,
            wind_gw=wind_gw,
            wind_on_gw=wind_on_gw,
            wind_off_gw=wind_off_gw,
            hydro_gw=hydro_gw,
            gas_gw=gas_gw,
            h2_gw=h2_gw,
            total_cap_gw=total_cap_gw,
            batt_gw=batt_gw,
            phs_gw=phs_gw,
            h2sto_gw=h2sto_gw,
            batt_gwh=batt_gwh,
            phs_gwh=phs_gwh,
            h2sto_gwh=h2sto_gwh,
            storage_gw=storage_gw_total,
            storage_gwh=storage_gwh_total,
            price_mean=mean_price_simple,
            price_weighted=dw_price,
            lole=lole_h,
            ens_gwh=ens_gwh,
            peak_ls_mw=peak_ls,
            max_dual=max_dual,
            exante_peak_mw=peak_dem_exante,
            exante_disp_mw=disp_total,
            exante_vre_credit_mw=vre_credit,
            exante_margin_mw=ade_margin,
            net_import_twh=net_import_twh.get(iso, 0.0),
            # ── Hydrogen coupling fields ──
            electrolysis_twh=electrolysis_twh,
            electrolysis_gw=electrolysis_gw,
            h2_fresh_twh=h2_fresh_twh,
            h2_fresh_gw=h2_fresh_gw,
            h2_demand_twh=h2_demand_twh,
            h2_total_ccgt_gw=h2_gw + h2_fresh_gw,
            h2_total_ccgt_twh=h2_twh + h2_fresh_twh,
            solar_exp_gw=solar_exp_gw,
            wind_on_exp_gw=wind_on_exp_gw,
            wind_off_exp_gw=wind_off_exp_gw,
            h2_net_import_twh=h2_net_import_twh.get(iso, 0.0),
        )
    return stats


# =====================================================================
# Derived EU-level totals
# =====================================================================

def compute_eu_totals(data: RawData, stats: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    sol = data.sol
    totex = float(sol["annualised_totex"].sum().values)
    opex_conv = float(sol["operation_conversion_costs"].sum().values)
    opex_stor = float(sol["operation_storage_costs"].sum().values)
    capex_conv = float(sol["planning_conversion_costs"].sum().values)
    capex_stor = float(sol["planning_storage_costs"].sum().values)

    demand_twh = sum(s["demand_twh"] for s in stats.values())
    gen_twh    = sum(s["gen_total_twh"] for s in stats.values())
    vre_twh    = sum(s["vre_twh"] for s in stats.values())
    storage_gw = sum(s["storage_gw"] for s in stats.values())
    storage_gwh = sum(s["storage_gwh"] for s in stats.values())

    spill_twh = float(sol["operation_spillage_power"].sum().values) / 1e6

    # Hydrogen aggregates
    h2_demand_twh = sum(s.get("h2_demand_twh", 0.0) for s in stats.values())
    electrolysis_twh = sum(s.get("electrolysis_twh", 0.0) for s in stats.values())
    electrolysis_gw = sum(s.get("electrolysis_gw", 0.0) for s in stats.values())
    h2_ccgt_twh = sum(s.get("h2_total_ccgt_twh", 0.0) for s in stats.values())
    h2_ccgt_gw = sum(s.get("h2_total_ccgt_gw", 0.0) for s in stats.values())
    # H2 spillage
    h2_spill_twh = 0.0
    if "operation_spillage_power" in sol.data_vars:
        sp = sol["operation_spillage_power"]
        if "resource" in sp.dims and "hydrogen" in list(sp["resource"].values):
            h2_spill_twh = float(sp.sel(resource="hydrogen").sum().values) / 1e6

    dw_num = (data.prices_wide.values * data.demand_hourly.reindex_like(data.prices_wide).values)
    dw_den = data.demand_hourly.values.sum()
    dw_price = float(np.nansum(dw_num) / dw_den) if dw_den > 0 else float("nan")

    return dict(
        totex_eur=totex,
        totex_bn=totex / 1e9,
        opex_conv_bn=opex_conv / 1e9,
        opex_stor_bn=opex_stor / 1e9,
        capex_conv_bn=capex_conv / 1e9,
        capex_stor_bn=capex_stor / 1e9,
        demand_twh=demand_twh,
        gen_twh=gen_twh,
        vre_twh=vre_twh,
        vre_share=(vre_twh / gen_twh * 100.0) if gen_twh > 0 else 0.0,
        storage_gw=storage_gw,
        storage_gwh=storage_gwh,
        spill_twh=spill_twh,
        ens_gwh=sum(s["ens_gwh"] for s in stats.values()),
        lole_h=max((s["lole"] for s in stats.values()), default=0.0),
        price_simple=float(data.prices_wide.mean().mean()),
        price_weighted=dw_price,
        # Hydrogen
        h2_demand_twh=h2_demand_twh,
        electrolysis_twh=electrolysis_twh,
        electrolysis_gw=electrolysis_gw,
        h2_ccgt_twh=h2_ccgt_twh,
        h2_ccgt_gw=h2_ccgt_gw,
        h2_spill_twh=h2_spill_twh,
    )


# =====================================================================
# Advanced adequacy analytics (§13, §14, §15, §16)
# =====================================================================

def _eu_residual_load_hourly(data: "RawData") -> np.ndarray:
    """Residual load EU hourly (GW) = demand_EU - VRE_EU.

    VRE includes Solar, Wind_On, Wind_Off, RoR_Hydro (variable renewable).
    Reservoir_Hydro_Inflow is NOT counted (it is dispatchable from stored water).
    """
    dem_eu = data.demand_hourly.sum(axis=1).values / 1000.0  # MW -> GW
    conv_h = data.sol["operation_conversion_power"]
    if "year_op" in conv_h.dims:
        conv_h = conv_h.squeeze("year_op", drop=True)
    vre_eu = np.zeros(8760)
    techs_avail = list(conv_h["conversion_tech"].values)
    for t in VRE_TECHS_CORE:
        if t in techs_avail:
            vre_eu += (conv_h.sel(conversion_tech=t).sum(dim="area").values / 1000.0)
    return dem_eu - vre_eu  # GW


def compute_stress_metrics(data: "RawData",
                            n_peak: int = 100) -> Dict[str, object]:
    """Compute metrics describing what covers the top-n_peak residual load hours.

    Returns:
        - residual_gw:   (8760,) GW residual load, EU-aggregated
        - peak_hours:    indices of the n_peak highest residual-load hours
        - peak_mean_gw:  mean residual load during those peak hours (GW)
        - mix_contrib:   dict {'gas_gw','h2_gw','reservoir_hydro_gw','waste_gw',
                               'storage_out_gw','net_import_gw',
                               'unserved_gw','vre_gw','demand_gw'}
                         each = mean power (GW) during the peak hours
        - corr_matrix:   (N, N) Pearson correlation of hourly residual load
                         (per country) -- DataFrame
    """
    residual_eu = _eu_residual_load_hourly(data)
    peak_idx = np.argsort(residual_eu)[-n_peak:][::-1]  # top-n, descending
    peak_mask = np.zeros(8760, dtype=bool)
    peak_mask[peak_idx] = True

    conv_h = data.sol["operation_conversion_power"]
    if "year_op" in conv_h.dims:
        conv_h = conv_h.squeeze("year_op", drop=True)

    def _eu_tech_gw(tname: str) -> np.ndarray:
        if tname not in conv_h["conversion_tech"].values:
            return np.zeros(8760)
        return conv_h.sel(conversion_tech=tname).sum(dim="area").values / 1000.0

    gas_gw      = _eu_tech_gw("Gas")
    h2_gw       = _eu_tech_gw("Hydrogen_power_plant") + _eu_tech_gw("hydrogen_power_plant")
    electrolysis_load_gw = _eu_tech_gw("electrolysis")
    res_hydro_gw = (_eu_tech_gw("Reservoir_Hydro_Plant")
                    + _eu_tech_gw("Reservoir_Hydro_Inflow"))
    waste_gw    = _eu_tech_gw("Waste")
    vre_gw      = (_eu_tech_gw("Solar") + _eu_tech_gw("Solar_expansion")
                   + _eu_tech_gw("Wind_Onshore") + _eu_tech_gw("Wind_Onshore_expansion")
                   + _eu_tech_gw("Wind_Offshore") + _eu_tech_gw("Wind_Offshore_expansion")
                   + _eu_tech_gw("RoR_Hydro"))
    demand_gw   = data.demand_hourly.sum(axis=1).values / 1000.0

    # Storage net output (out - in)
    storage_out_gw = np.zeros(8760)
    if ("operation_storage_power_out" in data.sol.data_vars
            and "operation_storage_power_in" in data.sol.data_vars):
        so = data.sol["operation_storage_power_out"].squeeze("year_op", drop=True)
        si = data.sol["operation_storage_power_in"].squeeze("year_op", drop=True)
        storage_out_gw = ((so.sum(dim=["area", "storage_tech"])
                           - si.sum(dim=["area", "storage_tech"])).values / 1000.0)

    # Unserved energy (load shedding)
    unserved_gw = np.zeros(8760)
    ens_var = _try_var(data.sol, "operation_load_shedding_power",
                       "operation_loss_load_power")
    if ens_var is not None:
        e = ens_var
        if "resource" in e.dims:
            if "electricity" in list(e["resource"].values):
                e = e.sel(resource="electricity")
            else:
                e = e.sum(dim="resource")
        if "area" in e.dims and "hour" in e.dims:
            unserved_gw = e.sum(dim="area").values / 1000.0

    mix_contrib = dict(
        gas_gw              = float(gas_gw[peak_idx].mean()),
        h2_gw               = float(h2_gw[peak_idx].mean()),
        reservoir_hydro_gw  = float(res_hydro_gw[peak_idx].mean()),
        waste_gw            = float(waste_gw[peak_idx].mean()),
        storage_out_gw      = float(storage_out_gw[peak_idx].mean()),
        vre_gw              = float(vre_gw[peak_idx].mean()),
        demand_gw           = float(demand_gw[peak_idx].mean()),
        unserved_gw         = float(unserved_gw[peak_idx].mean()),
        electrolysis_load_gw= float(electrolysis_load_gw[peak_idx].mean()),
    )
    # Net import balance = demand - (local generation + storage_out)
    # It captures what trade must contribute on EU-wide closure: 0 by construction.
    # But per-country net import during stress is meaningful.
    mix_contrib["residual_gw"] = float(residual_eu[peak_idx].mean())

    # ---- Residual load per country (for cross-country correlation) ---
    countries = [c for c in ALL_COUNTRIES if c in data.demand_hourly.columns]
    resid_country = np.zeros((len(countries), 8760))
    for k, c in enumerate(countries):
        dem_c = data.demand_hourly[c].values / 1000.0  # GW
        vre_c = np.zeros(8760)
        if c in list(conv_h["area"].values):
            sub = conv_h.sel(area=c)
            for t in VRE_TECHS_CORE:
                if t in sub["conversion_tech"].values:
                    vre_c += sub.sel(conversion_tech=t).values / 1000.0
        resid_country[k] = dem_c - vre_c
    # Normalize each row to zero-mean unit-variance for correlation
    mu = resid_country.mean(axis=1, keepdims=True)
    sd = resid_country.std(axis=1, keepdims=True)
    sd[sd < 1e-9] = 1.0
    z = (resid_country - mu) / sd
    corr = (z @ z.T) / 8760.0
    corr_df = pd.DataFrame(corr, index=countries, columns=countries)

    # Per-country peak stats (scatter)
    peak_gw_per_country: Dict[str, float] = {}
    hours_pos_per_country: Dict[str, float] = {}
    for k, c in enumerate(countries):
        r = resid_country[k]
        peak_gw_per_country[c] = float(r.max())
        hours_pos_per_country[c] = float((r > 0).sum())

    return dict(
        residual_gw=residual_eu,
        peak_hours=peak_idx,
        peak_mean_gw=float(residual_eu[peak_idx].mean()),
        mix_contrib=mix_contrib,
        corr_matrix=corr_df,
        resid_country=resid_country,
        countries=countries,
        peak_gw_per_country=peak_gw_per_country,
        hours_pos_per_country=hours_pos_per_country,
    )


def _longest_run_below(mask: np.ndarray) -> int:
    """Return the longest consecutive-True run length in a boolean array."""
    if not mask.any():
        return 0
    # Classic diff-based run-length
    diffs = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    starts = np.where(diffs == 1)[0]
    ends   = np.where(diffs == -1)[0]
    if len(starts) == 0:
        return 0
    return int((ends - starts).max())


def _all_runs_below(mask: np.ndarray) -> List[int]:
    diffs = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    starts = np.where(diffs == 1)[0]
    ends   = np.where(diffs == -1)[0]
    return [int(e - s) for s, e in zip(starts, ends)]


def compute_dunkelflaute(data: "RawData",
                          stats: Dict[str, Dict[str, float]],
                          threshold_pct: float = 10.0) -> Dict[str, object]:
    """Per country, detect consecutive hours where VRE capacity factor is below
    `threshold_pct` % of installed VRE capacity.

    Returns:
        per_country: {ISO2: {"longest_h": int, "n_episodes": int,
                             "total_low_h": int, "runs": List[int],
                             "vre_cap_gw": float}}
        eu_weekly_deficit_gwh: (52,) weekly VRE-deficit in GWh
                                vs the annual mean VRE power.
    """
    conv_h = data.sol["operation_conversion_power"]
    if "year_op" in conv_h.dims:
        conv_h = conv_h.squeeze("year_op", drop=True)
    conv_pwr = data.sol["operation_conversion_power_capacity"].squeeze(
        "year_op", drop=True).to_pandas() / 1000.0  # GW

    per_country: Dict[str, Dict[str, object]] = {}
    eu_vre_gw = np.zeros(8760)
    for c in ALL_COUNTRIES:
        vre_cap = 0.0
        vre_out = np.zeros(8760)
        if c in conv_pwr.index:
            for t in VRE_TECHS_CORE:
                if t in conv_pwr.columns:
                    vre_cap += float(conv_pwr.loc[c, t])
        if c in list(conv_h["area"].values):
            sub = conv_h.sel(area=c)
            for t in VRE_TECHS_CORE:
                if t in sub["conversion_tech"].values:
                    vre_out += sub.sel(conversion_tech=t).values / 1000.0  # GW
        eu_vre_gw += vre_out
        if vre_cap > 0.1:
            cf = vre_out / vre_cap
            low = cf < (threshold_pct / 100.0)
        else:
            low = np.zeros(8760, dtype=bool)
        runs = _all_runs_below(low)
        per_country[c] = dict(
            longest_h   = _longest_run_below(low),
            n_episodes  = len(runs),
            total_low_h = int(low.sum()),
            runs        = runs,
            vre_cap_gw  = vre_cap,
            vre_mean_gw = float(vre_out.mean()),
        )

    # EU weekly deficit vs annual mean (positive = below average)
    annual_mean = float(eu_vre_gw.mean())
    weekly = eu_vre_gw[:8736].reshape(52, 168).mean(axis=1)  # GW
    weekly_deficit_gwh = (annual_mean - weekly) * 168.0  # GWh signed

    return dict(
        per_country=per_country,
        eu_vre_gw=eu_vre_gw,
        annual_mean_gw=annual_mean,
        weekly_deficit_gwh=weekly_deficit_gwh,
        threshold_pct=threshold_pct,
    )


def compute_full_cost_per_country(data: "RawData",
                                    stats: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """Per-country full system cost breakdown (EUR/year) and cost-per-MWh.

    Total cost = operation_conversion_costs + operation_storage_costs
               + planning_conversion_costs + planning_storage_costs
    aggregated over conversion_tech / storage_tech / year_op.

    In a fixed-capacity run the planning_* terms are 0, which is why the
    cost is labelled "cout complet (OPEX dominant dans ce run)".
    """
    sol = data.sol
    out: Dict[str, Dict[str, float]] = {}

    def _per_area(var_name: str, sum_dim: str) -> Dict[str, float]:
        if var_name not in sol.data_vars:
            return {c: 0.0 for c in ALL_COUNTRIES}
        v = sol[var_name]
        if "year_op" in v.dims:
            v = v.squeeze("year_op", drop=True)
        if "year_dec" in v.dims:
            v = v.sum(dim="year_dec")
        if "year_inv" in v.dims:
            v = v.sum(dim="year_inv")
        if sum_dim in v.dims:
            v = v.sum(dim=sum_dim)
        s = v.to_pandas()
        return {c: float(s.get(c, 0.0)) for c in ALL_COUNTRIES}

    opex_conv = _per_area("operation_conversion_costs", "conversion_tech")
    opex_stor = _per_area("operation_storage_costs",    "storage_tech")
    capex_conv = _per_area("planning_conversion_costs", "conversion_tech")
    capex_stor = _per_area("planning_storage_costs",    "storage_tech")

    for c in ALL_COUNTRIES:
        total_eur = (opex_conv.get(c, 0.0) + opex_stor.get(c, 0.0)
                     + capex_conv.get(c, 0.0) + capex_stor.get(c, 0.0))
        demand_twh = stats[c]["demand_twh"]
        gen_twh    = stats[c]["gen_total_twh"]
        eur_per_mwh_dem = (total_eur / (demand_twh * 1e6)) if demand_twh > 1e-6 else float("nan")
        eur_per_mwh_gen = (total_eur / (gen_twh    * 1e6)) if gen_twh    > 1e-6 else float("nan")
        out[c] = dict(
            opex_conv_bn = opex_conv.get(c, 0.0) / 1e9,
            opex_stor_bn = opex_stor.get(c, 0.0) / 1e9,
            capex_conv_bn= capex_conv.get(c, 0.0) / 1e9,
            capex_stor_bn= capex_stor.get(c, 0.0) / 1e9,
            total_bn     = total_eur / 1e9,
            eur_per_mwh_dem = eur_per_mwh_dem,
            eur_per_mwh_gen = eur_per_mwh_gen,
        )
    return out


def compute_lcoe_reconstructed(
        stats: Dict[str, Dict[str, float]],
        costs: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """Reconstruct a full LCOE per country by combining:
      - the OPEX (operation_conversion_costs + operation_storage_costs)
        returned by POMMES, which is the only cost the solver actually
        paid in a fixed-capacity run, and
      - external CAPEX annuities built from JRC ETRI 2014 + DEA 2050
        overnight costs and O&M (WACC 5 %), applied to the installed
        capacities held by POMMES.
    Returns per country: capex_bn/opex_bn/total_bn and EUR/MWh demand.
    """
    out: Dict[str, Dict[str, float]] = {}
    ap = ANNUITY_POWER_EUR_KW_YR
    ae = ANNUITY_ENERGY_EUR_KWH_YR
    for c in ALL_COUNTRIES:
        s = stats.get(c, {})
        if not s:
            continue
        # 1 GW * 1e6 kW/GW * EUR/kW/yr = EUR/yr
        capex_solar   = s.get("solar_gw",    0.0) * 1e6 * ap["solar"]
        capex_won     = s.get("wind_on_gw",  0.0) * 1e6 * ap["wind_on"]
        capex_woff    = s.get("wind_off_gw", 0.0) * 1e6 * ap["wind_off"]
        capex_hydro   = s.get("hydro_gw",    0.0) * 1e6 * ap["hydro"]
        capex_gas     = s.get("gas_gw",      0.0) * 1e6 * ap["gas"]
        capex_h2      = s.get("h2_gw",       0.0) * 1e6 * ap["h2"]
        # Storage power (battery + PHS + H2 cavern, each with its own annuity)
        capex_batt_p  = s.get("batt_gw",   0.0) * 1e6 * ap["battery_p"]
        capex_phs_p   = s.get("phs_gw",    0.0) * 1e6 * ap["phs_p"]
        capex_h2sto_p = s.get("h2sto_gw",  0.0) * 1e6 * ap["h2sto_p"]
        capex_stor_p  = capex_batt_p + capex_phs_p + capex_h2sto_p
        # Storage energy — separate rate per technology
        capex_batt_e  = s.get("batt_gwh",  0.0) * 1e6 * ae["battery"]
        capex_phs_e   = s.get("phs_gwh",   0.0) * 1e6 * ae["phs"]
        capex_h2sto_e = s.get("h2sto_gwh", 0.0) * 1e6 * ae["h2sto"]
        capex_stor_e  = capex_batt_e + capex_phs_e + capex_h2sto_e
        capex_elec    = s.get("electrolysis_gw", 0.0) * 1e6 * ap["electrolysis"]
        capex_total   = (capex_solar + capex_won + capex_woff + capex_hydro
                         + capex_gas + capex_h2 + capex_elec + capex_stor_p + capex_stor_e)

        opex_bn = costs.get(c, {}).get("opex_conv_bn", 0.0) \
                  + costs.get(c, {}).get("opex_stor_bn", 0.0)
        capex_bn = capex_total / 1e9
        total_bn = capex_bn + opex_bn

        demand_twh = s.get("demand_twh", 0.0)
        eur_per_mwh = (total_bn * 1e9 / (demand_twh * 1e6)
                       if demand_twh > 1e-6 else float("nan"))
        out[c] = dict(
            capex_bn            = capex_bn,
            capex_solar_bn      = capex_solar   / 1e9,
            capex_wind_bn       = (capex_won + capex_woff) / 1e9,
            capex_hydro_bn      = capex_hydro   / 1e9,
            capex_firm_bn       = (capex_gas + capex_h2) / 1e9,
            capex_storage_bn    = (capex_stor_p + capex_stor_e) / 1e9,
            opex_bn             = opex_bn,
            total_bn            = total_bn,
            eur_per_mwh         = eur_per_mwh,
        )
    return out


def compute_sufficiency(data: "RawData",
                         stats: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    """Per country, derive sufficiency-signature metrics.

    - load_factor        = mean / peak
    - mwh_per_capita     = demand_TWh * 1e6 / (pop_M * 1e6) = demand_TWh / pop_M
    - ratio_winter_summer = (sum dem JFM+OND) / (sum dem JJA+MA)
    """
    out: Dict[str, Dict[str, float]] = {}
    if data.demand_hourly is None:
        return out
    # Hour ranges for winter (Jan-Mar + Oct-Dec) and summer (Apr-Sep)
    months_hours = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]) * 24
    winter_hours = np.concatenate([np.arange(months_hours[0],  months_hours[3]),
                                   np.arange(months_hours[9],  months_hours[12])])
    summer_hours = np.arange(months_hours[3], months_hours[9])
    for c in ALL_COUNTRIES:
        if c not in data.demand_hourly.columns:
            continue
        d = data.demand_hourly[c].values
        mean_mw = float(d.mean())
        peak_mw = float(d.max()) if d.max() > 0 else 1.0
        lf = mean_mw / peak_mw if peak_mw > 0 else 0.0
        pop = POPULATION_M_2023.get(c, float("nan"))
        mwh_cap = (stats[c]["demand_twh"] * 1e6) / (pop * 1e6) if pop > 0 else float("nan")
        w_sum = float(d[winter_hours].sum())
        s_sum = float(d[summer_hours].sum())
        wssr = w_sum / s_sum if s_sum > 0 else float("nan")
        out[c] = dict(
            load_factor=lf,
            mean_gw=mean_mw / 1000.0,
            peak_gw=peak_mw / 1000.0,
            pop_m=pop,
            mwh_per_capita=mwh_cap,
            winter_share=w_sum / (w_sum + s_sum) if (w_sum + s_sum) > 0 else 0.0,
            winter_summer_ratio=wssr,
        )
    return out


# =====================================================================
# HTML §4.3 — adequacy table
# =====================================================================

def build_adequacy_table_html(stats: Dict[str, Dict[str, float]]) -> str:
    rows = []
    ordered = sorted(ALL_COUNTRIES, key=lambda c: stats[c]["total_cap_gw"], reverse=True)
    for iso in ordered:
        s = stats[iso]
        rows.append(
            "<tr>"
            f'<td><strong>{iso}</strong> {s["name_fr"]}</td>'
            f'<td>{s["solar_gw"]:.1f}</td>'
            f'<td>{s["wind_gw"]:.1f}</td>'
            f'<td>{s["hydro_gw"]:.1f}</td>'
            f'<td>{s["gas_gw"]:.2f}</td>'
            f'<td>{s["h2_total_ccgt_gw"]:.2f}</td>'
            f'<td>{s["electrolysis_gw"]:.1f}</td>'
            f'<td>{s["batt_gw"]:.1f}</td>'
            f'<td><strong>{s["total_cap_gw"]:.1f}</strong></td>'
            f'<td>{s["h2_demand_twh"]:.1f}</td>'
            f'<td>{s["price_mean"]:.2f}</td>'
            f'<td>{s["lole"]:.0f}</td>'
            "</tr>"
        )
    thead = (
        "<thead><tr>"
        "<th>Pays</th><th>Solaire</th><th>Eolien</th><th>Hydro</th>"
        "<th>Gaz</th><th>H2 CCGT</th><th>Electrol.</th><th>Batt.</th><th>Total</th>"
        "<th>H2 dem. (TWh)</th><th>Prix (EUR/MWh)</th><th>LOLE (h)</th>"
        "</tr></thead>"
    )
    tbody = "<tbody>" + "".join(rows) + "</tbody>"
    return (
        '<table class="adequacy-table" style="width:100%;border-collapse:collapse;'
        'font-size:0.85rem;margin-top:12px;">' + thead + tbody + "</table>"
    )


# =====================================================================
# JS payloads — DISPATCH, COUNTRY_STATS, PRICE_DATA
# =====================================================================

def _weekly_means(series: np.ndarray) -> List[float]:
    n = 52 * 168
    v = np.asarray(series[:n], dtype=float).reshape(52, 168)
    return [round(float(x), 3) for x in v.mean(axis=1)]


def _hourly_window(series: np.ndarray, start_h: int, end_h: int) -> List[float]:
    w = np.asarray(series[start_h:end_h], dtype=float)
    return [round(float(x), 3) for x in w]


def build_dispatch_payload(data: RawData, countries: Sequence[str] = ("FR", "DE")) -> str:
    gen = data.sol["operation_conversion_power"].squeeze("year_op", drop=True)
    tech_groups = {
        "solar":       ["Solar", "Solar_expansion"],
        "wind":        ["Wind_Onshore", "Wind_Onshore_expansion",
                        "Wind_Offshore", "Wind_Offshore_expansion"],
        "hydro":       ["RoR_Hydro", "Reservoir_Hydro_Plant", "Reservoir_Hydro_Inflow"],
        "gas":         ["Gas"],
        "h2":          ["Hydrogen_power_plant", "hydrogen_power_plant"],
        "waste":       ["Waste"],
        "electrolysis":["electrolysis"],
    }
    out: Dict[str, Dict[str, List[float]]] = {}
    for iso in countries:
        if iso not in gen["area"].values:
            out[iso] = {k: [0.0] * 52 for k in tech_groups}
            continue
        sub = gen.sel(area=iso)
        payload: Dict[str, List[float]] = {}
        for key, techs in tech_groups.items():
            techs_present = [t for t in techs if t in sub["conversion_tech"].values]
            if not techs_present:
                payload[key] = [0.0] * 52
                continue
            stacked = sub.sel(conversion_tech=techs_present).sum(dim="conversion_tech")
            series = stacked.values.astype(float) / 1000.0  # MW -> GW for display
            payload[key] = _weekly_means(series)
        out[iso] = payload
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def build_country_stats_payload(stats: Dict[str, Dict[str, float]]) -> str:
    out: Dict[str, Dict[str, float]] = {}
    for iso, s in stats.items():
        out[iso] = {
            "peak":    round(s["peak_gw"], 2),
            "vre":     round(s["vre_share_pct"], 1),
            "price":   round(s["price_mean"], 2),
            "solar":   round(s["solar_gw"], 1),
            "wind":    round(s["wind_gw"], 1),
            "hydro":   round(s["hydro_gw"], 1),
            "lole":    round(s["lole"], 1),
            "ens":     round(s["ens_gwh"], 3),
            "storage":      round(s["storage_gw"], 1),
            "electrolysis": round(s.get("electrolysis_gw", 0.0), 1),
            "h2_demand":    round(s.get("h2_demand_twh", 0.0), 1),
            "h2_ccgt":      round(s.get("h2_total_ccgt_gw", 0.0), 1),
        }
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


def build_price_data_payload(data: RawData) -> str:
    px = data.prices_wide
    out: Dict[str, Dict[str, List[float]]] = {"year": {}, "week_winter": {}, "week_summer": {}}
    for iso in ALL_COUNTRIES:
        if iso in px.columns:
            series = px[iso].astype(float).values
        else:
            series = np.zeros(8760)
        out["year"][iso]        = _weekly_means(series)
        out["week_winter"][iso] = _hourly_window(series, WEEK_WINTER_START_HOUR, WEEK_WINTER_END_HOUR)
        out["week_summer"][iso] = _hourly_window(series, WEEK_SUMMER_START_HOUR, WEEK_SUMMER_END_HOUR)
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


# =====================================================================
# Matplotlib figure builders (17 PNGs)
# First pass: each builder emits a compact panel showing the real
# headline figures for the section.  Real plot content (lines, stacked
# bars, heatmaps) is filled in by subsequent passes — the stubs below
# already carry the post-bugfix numbers so the pipeline can be validated
# end-to-end in one go.
# =====================================================================

def build_residual_payload(stress: Dict[str, object]) -> str:
    """Build a compact JSON payload of hourly residual load per country (GW, 2dp).

    Shape : {"FR": [8760 floats], "DE": [...], ...} — used by the §16
    interactive canvas explorer. Payload size ~1.5-2 MB.
    """
    resid = stress["resid_country"]     # (N, 8760)  GW
    countries = stress["countries"]
    obj: Dict[str, List[float]] = {}
    for k, c in enumerate(countries):
        obj[c] = [round(float(v), 2) for v in resid[k]]
    return json.dumps(obj, separators=(",", ":"))


def build_firm_capacity_payload(data: "RawData",
                                  stats: Dict[str, Dict[str, float]]) -> str:
    """Firm / dispatchable capacity per country (GW), for the §16 reference line."""
    obj: Dict[str, float] = {}
    for c in ALL_COUNTRIES:
        firm = (stats[c]["gas_gw"] + stats[c].get("h2_total_ccgt_gw", stats[c]["h2_gw"])
                + stats[c]["hydro_gw"] + stats[c]["storage_gw"])
        obj[c] = round(firm, 2)
    return json.dumps(obj, separators=(",", ":"))


def _fig_to_data_uri(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=MPL_BG, dpi=MPL_DPI)
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _panel(title: str, lines: Sequence[str], size: Tuple[float, float] = (9, 4.5)) -> str:
    fig, ax = plt.subplots(figsize=size)
    ax.axis("off")
    ax.set_facecolor(MPL_BG)
    ax.text(0.02, 0.92, title, ha="left", va="top",
            fontsize=18, fontweight="bold", color=MPL_FG, transform=ax.transAxes)
    y = 0.74
    for line in lines:
        ax.text(0.04, y, line, ha="left", va="top",
                fontsize=12, color=MPL_MUTED, transform=ax.transAxes,
                family="monospace")
        y -= 0.09
    return _fig_to_data_uri(fig)


# ---- Figure helpers --------------------------------------------------

# Canonical stack order (bottom -> top) for mix / dispatch charts
TECH_STACK_ORDER: List[str] = [
    "Solar", "Solar_expansion",
    "Wind_Onshore", "Wind_Onshore_expansion",
    "Wind_Offshore", "Wind_Offshore_expansion",
    "RoR_Hydro", "Reservoir_Hydro_Plant", "Reservoir_Hydro_Inflow",
    "Waste", "Gas", "Hydrogen_power_plant", "hydrogen_power_plant",
]
# Note: electrolysis is NOT in TECH_STACK_ORDER — it's a load (draws from electricity bus)


def _get_conv_hourly(data: "RawData") -> xr.DataArray:
    """Return hourly conversion power (MW) as DataArray (area, conversion_tech, hour)."""
    arr = data.sol["operation_conversion_power"]
    if "year_op" in arr.dims:
        arr = arr.squeeze("year_op", drop=True)
    return arr


def _try_var(sol: xr.Dataset, *names: str) -> Optional[xr.DataArray]:
    for n in names:
        if n in sol.data_vars:
            v = sol[n]
            if "year_op" in v.dims:
                v = v.squeeze("year_op", drop=True)
            return v
    return None


def _fig(size: Tuple[float, float] = (9, 4.5),
         title: Optional[str] = None) -> Tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=size)
    if title:
        ax.set_title(title, loc="left", fontsize=13, fontweight="bold", color=MPL_FG)
    return fig, ax


def _stacked_hourly(ax, hours, tech_series: Dict[str, np.ndarray],
                    order: Sequence[str] = TECH_STACK_ORDER) -> None:
    present = [t for t in order if t in tech_series and np.any(tech_series[t] > 1e-6)]
    stacks = [tech_series[t] for t in present]
    colors = [TECH_COLORS.get(t, "#888888") for t in present]
    labels = [TECH_LABELS.get(t, t) for t in present]
    if stacks:
        ax.stackplot(hours, *stacks, labels=labels, colors=colors, linewidth=0)


def build_figures(data: RawData,
                  stats: Dict[str, Dict[str, float]],
                  eu: Dict[str, float]) -> Dict[str, str]:
    figs: Dict[str, str] = {}

    # ---------- §4.1 — EU capacity overview (horizontal stacked bar) ---
    cap_rows = ("Solar", "Solar_expansion",
                "Wind_Onshore", "Wind_Onshore_expansion",
                "Wind_Offshore", "Wind_Offshore_expansion",
                "RoR_Hydro", "Reservoir_Hydro_Plant", "Reservoir_Hydro_Inflow",
                "Waste", "Gas", "Hydrogen_power_plant", "hydrogen_power_plant",
                "electrolysis")
    conv_pwr = data.sol["operation_conversion_power_capacity"].squeeze("year_op", drop=True).to_pandas() / 1000.0
    eu_cap = {t: float(conv_pwr[t].sum()) for t in cap_rows if t in conv_pwr.columns}
    fig, ax = _fig((10, 4.2), "Capacites installees EU 2050 (GW)")
    labels = [TECH_LABELS.get(t, t) for t in eu_cap]
    values = list(eu_cap.values())
    colors = [TECH_COLORS.get(t, "#888") for t in eu_cap]
    bars = ax.barh(labels, values, color=colors, edgecolor="white")
    for bar, v in zip(bars, values):
        ax.text(v + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{v:,.0f}".replace(",", " "),
                va="center", fontsize=9, color=MPL_FG)
    ax.set_xlabel("Capacite installee (GW)")
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.4)
    ax.set_axisbelow(True)
    total = sum(values)
    ax.text(0.99, 1.02, f"Total : {total:,.0f} GW".replace(",", " "),
            transform=ax.transAxes, ha="right", fontsize=10,
            color=MPL_MUTED, fontstyle="italic")
    figs["sec41_overview"] = _fig_to_data_uri(fig)

    # ---------- §4.2 — stacked bar per country (30) --------------------
    countries_sorted = sorted(ALL_COUNTRIES,
                              key=lambda c: stats[c]["total_cap_gw"], reverse=True)
    fig, ax = _fig((13, 5.5), "Mix de capacites par pays (GW)")
    bottom = np.zeros(len(countries_sorted))
    for t in cap_rows:
        if t not in conv_pwr.columns:
            continue
        vals = np.array([float(conv_pwr[t].get(c, 0.0)) if c in conv_pwr.index else 0.0
                         for c in countries_sorted])
        if vals.sum() < 1e-6:
            continue
        ax.bar(countries_sorted, vals, bottom=bottom,
               color=TECH_COLORS.get(t, "#888"),
               label=TECH_LABELS.get(t, t), edgecolor="white", linewidth=0.3)
        bottom += vals
    ax.set_ylabel("Capacite (GW)")
    ax.set_xticks(range(len(countries_sorted)))
    ax.set_xticklabels(countries_sorted, rotation=45, ha="right", fontsize=8)
    ax.legend(loc="upper right", ncol=3, fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.4)
    ax.set_axisbelow(True)
    figs["sec42_mix_per_country"] = _fig_to_data_uri(fig)

    # ---------- §6.1 — adequacy synthesis ------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.5),
                                    gridspec_kw={"width_ratios": [2, 1]})
    margins = np.array([stats[c]["exante_margin_mw"] / 1000.0 for c in ALL_COUNTRIES])
    order2 = np.argsort(margins)
    cts_o = [ALL_COUNTRIES[i] for i in order2]
    mg_o  = margins[order2]
    bar_colors = ["#ef4444" if m < 0 else "#16a34a" for m in mg_o]
    ax1.barh(cts_o, mg_o, color=bar_colors, edgecolor="white")
    ax1.axvline(0, color=MPL_FG, linewidth=0.8)
    ax1.set_title("Marges ex ante par pays (GW)",
                  loc="left", fontsize=12, fontweight="bold")
    ax1.set_xlabel("Marge (GW)")
    ax1.tick_params(axis="y", labelsize=7)
    ax1.grid(axis="x", alpha=0.4)
    ax1.set_axisbelow(True)

    ax2.axis("off")
    ax2.text(0.02, 0.95, "Synthese EU", fontsize=13, fontweight="bold",
             transform=ax2.transAxes)
    kpi_lines = [
        ("Demande elec. totale", f"{eu['demand_twh']:.0f} TWh"),
        ("Generation totale",    f"{eu['gen_twh']:.0f} TWh"),
        ("Part VRE",             f"{eu['vre_share']:.1f} %"),
        ("Curtailment",          f"{eu['spill_twh']:.1f} TWh"),
        ("Demande H2",           f"{eu.get('h2_demand_twh', 0):.0f} TWh"),
        ("Electrolyse",          f"{eu.get('electrolysis_gw', 0):.0f} GW"),
        ("H2 CCGT",              f"{eu.get('h2_ccgt_gw', 0):.0f} GW"),
        ("ENS totale",           f"{eu['ens_gwh']:.2f} GWh"),
        ("LOLE max (pays)",      f"{eu['lole_h']:.0f} h"),
        ("Totex annualise",      f"{eu['totex_bn']:.2f} Mrd EUR"),
        ("Prix moyen (DW)",      f"{eu['price_weighted']:.2f} EUR/MWh"),
    ]
    y = 0.82
    for k, v in kpi_lines:
        ax2.text(0.02, y, k, fontsize=10, color=MPL_MUTED, transform=ax2.transAxes)
        ax2.text(0.98, y, v, fontsize=10, color=MPL_FG, fontweight="bold",
                 ha="right", transform=ax2.transAxes)
        y -= 0.10
    figs["sec61_synthese"] = _fig_to_data_uri(fig)

    # ---------- §7.1 — price duration curves --------------------------
    fig, ax = _fig((10, 4.5), "Courbes monotones des prix (EUR/MWh)")
    x = np.arange(8760)
    highlight = {"FR": "#1e90ff", "DE": "#f6b419", "GB": "#16a34a",
                 "ES": "#ef4444", "IT": "#a855f7"}
    for c in ALL_COUNTRIES:
        if c in data.prices_wide.columns:
            sd = np.sort(data.prices_wide[c].values)[::-1]
            if c in highlight:
                ax.plot(x, sd, color=highlight[c], linewidth=1.3, label=c, zorder=3)
            else:
                ax.plot(x, sd, color="#cbd5e0", linewidth=0.6, zorder=1)
    ax.set_xlabel("Heures (triees)")
    ax.set_ylabel("Prix (EUR/MWh)")
    ax.set_xlim(0, 8760)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper right", fontsize=9, frameon=False)
    ax.grid(alpha=0.4)
    ax.set_axisbelow(True)
    figs["sec71_duration"] = _fig_to_data_uri(fig)

    # ---------- §7.2 — price histogram ---------------------------------
    fig, ax = _fig((10, 4.2), "Distribution horaire des prix EU")
    vals_all = data.prices_wide.values.flatten()
    vals_all = vals_all[np.isfinite(vals_all)]
    p99 = float(np.percentile(vals_all, 99.5)) if len(vals_all) else 50
    bins = np.arange(0, max(p99, 30) + 1.0, 1.0)
    ax.hist(vals_all, bins=bins, color="#1e90ff",
            edgecolor="white", linewidth=0.2)
    ax.set_xlabel("Prix (EUR/MWh)")
    ax.set_ylabel("Nombre d'occurrences (pays x heures)")
    ax.axvline(eu["price_simple"], color="#ef4444",
               linestyle="--", linewidth=1.2,
               label=f"Moyenne {eu['price_simple']:.1f}")
    ax.axvline(eu["price_weighted"], color="#16a34a",
               linestyle="--", linewidth=1.2,
               label=f"Pondere dem. {eu['price_weighted']:.1f}")
    ax.legend(loc="upper right", fontsize=9, frameon=False)
    ax.grid(alpha=0.4)
    ax.set_axisbelow(True)
    figs["sec72_histogram"] = _fig_to_data_uri(fig)

    # ---------- §7.3 — price heatmap (countries x hours) --------------
    fig, ax = _fig((12, 6), "Heatmap prix horaires par pays (EUR/MWh)")
    ctys_h = [c for c in ALL_COUNTRIES if c in data.prices_wide.columns]
    ctys_h = sorted(ctys_h, key=lambda c: -float(data.prices_wide[c].mean()))
    mat = np.vstack([data.prices_wide[c].values for c in ctys_h])
    im = ax.imshow(mat, aspect="auto", cmap="viridis",
                   vmin=0, vmax=min(float(np.nanpercentile(mat, 99)), 80),
                   interpolation="nearest", extent=(0, 8760, len(ctys_h), 0))
    ax.set_yticks(np.arange(len(ctys_h)) + 0.5)
    ax.set_yticklabels(ctys_h, fontsize=7)
    ax.set_xlabel("Heure de l'annee")
    month_h = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30]) * 24
    ax.set_xticks(month_h)
    ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    cb = fig.colorbar(im, ax=ax, pad=0.01, shrink=0.85)
    cb.set_label("EUR/MWh")
    figs["sec73_heatmap"] = _fig_to_data_uri(fig)

    # ---------- §7.4 — FR-DE-GB spread time series --------------------
    fig, ax = _fig((12, 4), "Ecart de prix FR / DE / GB (moyenne glissante 168 h)")
    for c, col in (("FR", "#1e90ff"), ("DE", "#f6b419"), ("GB", "#16a34a")):
        if c in data.prices_wide.columns:
            s = pd.Series(data.prices_wide[c].values).rolling(168, min_periods=1).mean()
            ax.plot(s.values, color=col, linewidth=1.1, label=c)
    ax.set_xlim(0, 8760)
    ax.set_xlabel("Heure de l'annee")
    ax.set_ylabel("EUR/MWh")
    ax.set_xticks(month_h)
    ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax.legend(loc="upper right", fontsize=9, frameon=False)
    ax.grid(alpha=0.4)
    ax.set_axisbelow(True)
    figs["sec74_spread"] = _fig_to_data_uri(fig)

    # ---------- §8.1 — EU annual dispatch (stacked area weekly) --------
    conv_h = _get_conv_hourly(data)              # MW, (area, conversion_tech, hour)
    eu_prod = conv_h.sum(dim="area")             # (tech, hour)
    eu_prod_df = eu_prod.to_pandas()             # index=tech, cols=hour
    fig, ax = _fig((13, 4.8), "Dispatch annuel EU (GW, moyenne glissante 7 j)")
    hours = np.arange(8760)
    series_smooth: Dict[str, np.ndarray] = {}
    for t in TECH_STACK_ORDER:
        if t in eu_prod_df.index:
            v = eu_prod_df.loc[t].values / 1000.0  # MW -> GW
            series_smooth[t] = pd.Series(v).rolling(168, min_periods=1).mean().values
    _stacked_hourly(ax, hours, series_smooth, TECH_STACK_ORDER)
    # demand line
    dem_eu = data.demand_hourly.sum(axis=1).values / 1000.0
    dem_smooth = pd.Series(dem_eu).rolling(168, min_periods=1).mean().values
    ax.plot(hours, dem_smooth, color="#111827", linewidth=1.0, label="Demande elec. EU")
    # Add electrolysis load line (elec demand + electrolyser draw)
    if "electrolysis" in eu_prod_df.index:
        elec_load = np.abs(eu_prod_df.loc["electrolysis"].values) / 1000.0
        dem_plus_elec = dem_eu + elec_load
        dem_plus_smooth = pd.Series(dem_plus_elec).rolling(168, min_periods=1).mean().values
        ax.plot(hours, dem_plus_smooth, color="#10b981", linewidth=0.9,
                linestyle="--", label="Dem. elec. + electrolyse")
    ax.set_xlim(0, 8760)
    ax.set_xlabel("Heure de l'annee")
    ax.set_ylabel("Puissance (GW)")
    ax.set_xticks(month_h)
    ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax.legend(loc="upper right", ncol=3, fontsize=8, frameon=False)
    ax.grid(alpha=0.4)
    ax.set_axisbelow(True)
    figs["sec81_annual_dispatch"] = _fig_to_data_uri(fig)

    # ---------- §8.2 / §8.3 — week 4 & week 30 for FR / DE -----------
    def _week_fig(start_h: int, end_h: int, title: str) -> str:
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)
        for ax, iso in zip(axes, ("FR", "DE")):
            if iso not in conv_h["area"].values:
                ax.set_title(f"{iso} (n/d)")
                continue
            sub = conv_h.sel(area=iso).isel(hour=slice(start_h, end_h))
            sub_df = sub.to_pandas()  # tech x hour
            hr = np.arange(end_h - start_h)
            series = {}
            for t in TECH_STACK_ORDER:
                if t in sub_df.index:
                    series[t] = sub_df.loc[t].values / 1000.0
            _stacked_hourly(ax, hr, series, TECH_STACK_ORDER)
            # demand line
            if iso in data.demand_hourly.columns:
                dem = data.demand_hourly[iso].values[start_h:end_h] / 1000.0
                ax.plot(hr, dem, color="#111827", linewidth=1.2, label="Demande")
            ax.set_title(iso, loc="left", fontsize=11, fontweight="bold")
            ax.set_xlabel("Heure (relative)")
            ax.set_ylabel("GW")
            ax.set_xlim(0, end_h - start_h - 1)
            ax.grid(alpha=0.4)
            ax.set_axisbelow(True)
        axes[0].legend(loc="upper left", fontsize=8, ncol=2, frameon=False)
        fig.suptitle(title, x=0.02, ha="left", fontsize=13, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        return _fig_to_data_uri(fig)

    figs["sec82_week4"]  = _week_fig(WEEK_WINTER_START_HOUR, WEEK_WINTER_END_HOUR,
                                     "Semaine d'hiver (15-21 janvier) — FR / DE")
    figs["sec83_week30"] = _week_fig(WEEK_SUMMER_START_HOUR, WEEK_SUMMER_END_HOUR,
                                     "Semaine d'ete (16-22 juillet) — FR / DE")

    # ---------- §9.1 / §9.2 — Storage SoC profiles --------------------
    soc = _try_var(data.sol, "operation_storage_level",
                   "operation_storage_soc", "operation_storage_state")
    fig, ax = _fig((13, 4.5), "Etat de charge des stockages EU (TWh)")
    if soc is not None and "storage_tech" in soc.dims and "hour" in soc.dims:
        soc_eu = soc.sum(dim="area") / 1e6  # MWh -> TWh  -> (hour, storage_tech)
        # Force layout (storage_tech, hour) for iteration
        soc_da = soc_eu.transpose("storage_tech", "hour")
        for tech in soc_da["storage_tech"].values:
            row = soc_da.sel(storage_tech=tech).values
            if float(np.nanmax(row)) < 1e-3:
                continue
            tname = str(tech)
            ax.plot(row, color=STORAGE_COLORS.get(tname, "#888"),
                    linewidth=1.0, label=tname.replace("_", " "))
        ax.set_xlim(0, 8760)
        ax.set_xticks(month_h)
        ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
        ax.set_ylabel("TWh")
        ax.legend(loc="upper right", fontsize=8, ncol=3, frameon=False)
        ax.grid(alpha=0.4)
        ax.set_axisbelow(True)
    else:
        ax.axis("off")
        ax.text(0.5, 0.5, "SoC non disponible dans solution_2050.nc",
                ha="center", va="center", fontsize=11, color=MPL_MUTED,
                transform=ax.transAxes)
    figs["sec91_soc"] = _fig_to_data_uri(fig)

    # §9.2 — storage capacity breakdown (power & energy bar pair)
    fig, (axP, axE) = plt.subplots(1, 2, figsize=(13, 4.5))
    stor_pwr_eu = data.sol["operation_storage_power_capacity"].squeeze(
        "year_op", drop=True).sum(dim="area").to_pandas() / 1000.0
    stor_nrg_eu = data.sol["operation_storage_energy_capacity"].squeeze(
        "year_op", drop=True).sum(dim="area").to_pandas() / 1e6  # MWh -> TWh
    techs = list(stor_pwr_eu.index)
    colsP = [STORAGE_COLORS.get(t, "#888") for t in techs]
    axP.bar([t.replace("_", "\n") for t in techs], stor_pwr_eu.values,
            color=colsP, edgecolor="white")
    axP.set_ylabel("GW")
    axP.set_title("Puissance installee", loc="left", fontsize=11, fontweight="bold")
    axP.grid(axis="y", alpha=0.4); axP.set_axisbelow(True)
    for b, v in zip(axP.patches, stor_pwr_eu.values):
        axP.text(b.get_x() + b.get_width() / 2, b.get_height(),
                 f"{v:.0f}", ha="center", va="bottom", fontsize=8)

    axE.bar([t.replace("_", "\n") for t in techs], stor_nrg_eu.values,
            color=colsP, edgecolor="white")
    axE.set_ylabel("TWh")
    axE.set_title("Energie stockable", loc="left", fontsize=11, fontweight="bold")
    axE.grid(axis="y", alpha=0.4); axE.set_axisbelow(True)
    for b, v in zip(axE.patches, stor_nrg_eu.values):
        axE.text(b.get_x() + b.get_width() / 2, b.get_height(),
                 f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    fig.suptitle("Analyse des stockages (EU 30)",
                 x=0.02, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    figs["sec92_soc_analysis"] = _fig_to_data_uri(fig)

    # ---------- §10.1 / §10.2 — Transport flows -----------------------
    try:
        tpow = data.sol["operation_transport_power"]
        if "year_op" in tpow.dims:
            tpow = tpow.squeeze("year_op", drop=True)
        if "transport_tech" in tpow.dims:
            tpow = tpow.squeeze("transport_tech", drop=True)
        link_twh = (tpow.sum(dim="hour") / 1e6).to_pandas()  # signed
    except Exception:
        link_twh = pd.Series(dtype=float)

    fig, ax = _fig((11, 5.5), "Top 20 liens d'interconnexion (TWh/an)")
    if len(link_twh):
        abs_sorted = link_twh.abs().sort_values(ascending=True).tail(20)
        signed = link_twh.loc[abs_sorted.index]
        clean_lbl = [str(lk).replace("link_", "").replace("_", " -> ")
                     for lk in abs_sorted.index]
        colsF = ["#1e90ff" if v >= 0 else "#ef4444" for v in signed.values]
        ax.barh(clean_lbl, signed.values, color=colsF, edgecolor="white")
        ax.axvline(0, color=MPL_FG, linewidth=0.8)
        ax.set_xlabel("Flux net (TWh/an)")
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.axis("off")
        ax.text(0.5, 0.5, "Flux de transport non disponibles",
                ha="center", va="center", transform=ax.transAxes)
    figs["sec101_flows_map"] = _fig_to_data_uri(fig)

    fig, ax = _fig((11, 4.8), "Import / export net par pays (TWh/an)")
    ni = np.array([stats[c]["net_import_twh"] for c in ALL_COUNTRIES])
    order = np.argsort(ni)
    cts_o = [ALL_COUNTRIES[i] for i in order]
    ni_o = ni[order]
    cols = ["#ef4444" if v < 0 else "#16a34a" for v in ni_o]
    ax.bar(cts_o, ni_o, color=cols, edgecolor="white")
    ax.axhline(0, color=MPL_FG, linewidth=0.8)
    ax.set_ylabel("Net import (TWh/an)  [+ : importeur, - : exportateur]")
    ax.set_xticks(range(len(cts_o)))
    ax.set_xticklabels(cts_o, rotation=45, ha="right", fontsize=8)
    ax.grid(axis="y", alpha=0.4); ax.set_axisbelow(True)
    figs["sec102_flows_analysis"] = _fig_to_data_uri(fig)

    # ---------- §11.1 — residual load duration curve ------------------
    fig, ax = _fig((11, 4.8), "Monotone de charge residuelle EU (GW)")
    dem_total = data.demand_hourly.sum(axis=1).values / 1000.0
    vre_total = np.zeros(8760)
    eu_prod_df2 = eu_prod_df
    for t in VRE_TECHS_CORE:
        if t in eu_prod_df2.index:
            vre_total += eu_prod_df2.loc[t].values / 1000.0
    residual = dem_total - vre_total
    sorted_r = np.sort(residual)[::-1]
    ax.fill_between(np.arange(8760), sorted_r, 0,
                    where=(sorted_r >= 0), color="#ef4444", alpha=0.55,
                    label="Residuelle > 0")
    ax.fill_between(np.arange(8760), sorted_r, 0,
                    where=(sorted_r < 0), color="#16a34a", alpha=0.55,
                    label="Surplus VRE")
    ax.axhline(0, color=MPL_FG, linewidth=0.8)
    ax.set_xlim(0, 8760)
    ax.set_xlabel("Heures (triees)")
    ax.set_ylabel("GW")
    pos_h = int((residual > 0).sum())
    neg_h = int((residual < 0).sum())
    ax.text(0.98, 0.95, f"h > 0 : {pos_h}\nh < 0 : {neg_h}",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=9, color=MPL_FG,
            bbox=dict(boxstyle="round", facecolor="white",
                      edgecolor=MPL_GRID))
    ax.legend(loc="upper right", fontsize=9, frameon=False,
              bbox_to_anchor=(1, 0.82))
    ax.grid(alpha=0.4); ax.set_axisbelow(True)
    figs["sec111_residual"] = _fig_to_data_uri(fig)

    # ---------- §12.1 / §12.2 — ENS (loss-of-load) heatmap + timeline -
    ens_var = _try_var(data.sol, "operation_load_shedding_power",
                       "operation_loss_load_power",
                       "operation_unserved_energy")
    ens_eu_hourly: Optional[np.ndarray] = None
    ens_mat: Optional[np.ndarray] = None
    if ens_var is not None:
        e = ens_var
        if "resource" in e.dims:
            if "electricity" in list(e["resource"].values):
                e = e.sel(resource="electricity")
            else:
                e = e.sum(dim="resource")
        # Expect (area, hour)
        if "area" in e.dims and "hour" in e.dims:
            e = e.transpose("area", "hour")
            df = e.to_pandas().reindex(ALL_COUNTRIES).fillna(0.0)
            ens_mat = df.values
            ens_eu_hourly = ens_mat.sum(axis=0)

    fig, ax = _fig((12, 5), "Heatmap delestage (MWh / pays-heure)")
    if ens_mat is not None and ens_mat.max() > 1e-6:
        im = ax.imshow(ens_mat, aspect="auto", cmap="Reds",
                       extent=(0, 8760, len(ALL_COUNTRIES), 0))
        ax.set_yticks(np.arange(len(ALL_COUNTRIES)) + 0.5)
        ax.set_yticklabels(ALL_COUNTRIES, fontsize=7)
        ax.set_xticks(month_h)
        ax.set_xticklabels(["J", "F", "M", "A", "M", "J",
                            "J", "A", "S", "O", "N", "D"])
        fig.colorbar(im, ax=ax, pad=0.01, shrink=0.85).set_label("MWh")
    else:
        ax.axis("off")
        ax.text(0.5, 0.5,
                f"Aucun delestage detecte (ENS totale = {eu['ens_gwh']:.2f} GWh)",
                ha="center", va="center", fontsize=12,
                color="#16a34a", fontweight="bold", transform=ax.transAxes)
    figs["sec121_shedding_heatmap"] = _fig_to_data_uri(fig)

    fig, ax = _fig((12, 3.8), "Timeline delestage EU (MWh / h)")
    if ens_eu_hourly is not None and ens_eu_hourly.max() > 1e-6:
        ax.plot(ens_eu_hourly, color="#ef4444", linewidth=0.8)
        ax.fill_between(np.arange(8760), ens_eu_hourly, 0,
                        color="#ef4444", alpha=0.3)
        ax.set_xlim(0, 8760)
        ax.set_xticks(month_h)
        ax.set_xticklabels(["J", "F", "M", "A", "M", "J",
                            "J", "A", "S", "O", "N", "D"])
        ax.set_ylabel("MWh deleste")
        ax.grid(alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.axis("off")
        ax.text(0.5, 0.5,
                f"Aucun evenement de delestage (ENS = {eu['ens_gwh']:.2f} GWh)",
                ha="center", va="center", fontsize=12,
                color="#16a34a", fontweight="bold", transform=ax.transAxes)
    figs["sec122_shedding_timeline"] = _fig_to_data_uri(fig)

    return figs


# =====================================================================
# SVG choropleth map builders (6 maps §5.1..§5.6)
# =====================================================================

def _load_shapefile(path: Path) -> Dict[str, List[List[Tuple[float, float]]]]:
    try:
        import shapefile  # pyshp
    except ImportError:
        sys.stderr.write(
            "WARNING: pyshp not installed -- SVG maps will be placeholders.\n"
            "         Install with `pip install pyshp`.\n")
        return {}

    # Prefer a more detailed shapefile when available (50m > 110m)
    candidates: List[Path] = []
    if path.exists():
        candidates.append(path)
    parent = path.parent.parent if path.parent.name.startswith("ne_") else path.parent
    for fname in ("ne_50m_admin_0_countries/ne_50m_admin_0_countries.shp",
                  "ne_10m_admin_0_countries/ne_10m_admin_0_countries.shp"):
        alt = parent / fname
        if alt.exists() and alt not in candidates:
            candidates.insert(0, alt)  # prefer higher resolution
    if not candidates:
        sys.stderr.write(f"WARNING: shapefile not found at {path}\n")
        return {}
    chosen = candidates[0]
    if chosen != path:
        sys.stderr.write(f"  -> using higher-resolution shapefile: {chosen.name}\n")

    out: Dict[str, List[List[Tuple[float, float]]]] = {}
    r = shapefile.Reader(str(chosen))
    fields = [f[0] for f in r.fields[1:]]
    iso3_idx = None
    for candidate in ("ADM0_A3", "ISO_A3", "ADM0_A3_IS"):
        if candidate in fields:
            iso3_idx = fields.index(candidate)
            break
    if iso3_idx is None:
        iso3_idx = 0
    for sr in r.shapeRecords():
        iso3 = sr.record[iso3_idx]
        rings: List[List[Tuple[float, float]]] = []
        pts = sr.shape.points
        parts = list(sr.shape.parts) + [len(pts)]
        for i in range(len(parts) - 1):
            ring = [(float(x), float(y)) for x, y in pts[parts[i]:parts[i + 1]]]
            if len(ring) >= 3:
                rings.append(ring)
        if rings:
            out[iso3] = rings
    return out


def _lambert_cc(lon: float, lat: float,
                lon0: float = 10.0, lat1: float = 35.0, lat2: float = 65.0) -> Tuple[float, float]:
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    lon0_r = math.radians(lon0)
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    if abs(lat1 - lat2) < 1e-9:
        n = math.sin(lat1_r)
    else:
        n = (math.log(math.cos(lat1_r) / math.cos(lat2_r))
             / math.log(math.tan(math.pi / 4 + lat2_r / 2)
                        / math.tan(math.pi / 4 + lat1_r / 2)))
    F = math.cos(lat1_r) * math.tan(math.pi / 4 + lat1_r / 2) ** n / n
    rho  = F / math.tan(math.pi / 4 + lat_r  / 2) ** n
    rho0 = F / math.tan(math.pi / 4 + lat1_r / 2) ** n
    theta = n * (lon_r - lon0_r)
    x =  rho * math.sin(theta)
    y = rho0 - rho * math.cos(theta)
    return x, y


def _viridis(t: float) -> str:
    t = max(0.0, min(1.0, t))
    stops = [
        (0.00, (68,   1,  84)),
        (0.25, (59,  82, 139)),
        (0.50, (33, 144, 141)),
        (0.75, (94, 201,  98)),
        (1.00, (253,231,  37)),
    ]
    for i in range(len(stops) - 1):
        a, ca = stops[i]
        b, cb = stops[i + 1]
        if a <= t <= b:
            u = (t - a) / (b - a)
            r = int(ca[0] + (cb[0] - ca[0]) * u)
            g = int(ca[1] + (cb[1] - ca[1]) * u)
            bl = int(ca[2] + (cb[2] - ca[2]) * u)
            return f"#{r:02x}{g:02x}{bl:02x}"
    return "#fde725"


def _choropleth_svg(polygons: Dict[str, List[List[Tuple[float, float]]]],
                    values:   Dict[str, float],
                    title:    str,
                    units:    str,
                    cmap_fn=_viridis,
                    width:    int = 760,
                    height:   int = 620) -> str:
    if not polygons:
        return (
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{width}" height="{height}" fill="#f1f5f9"/>'
            f'<text x="{width/2}" y="{height/2}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="16" fill="#64748b">'
            f'{title} — map unavailable'
            f'</text></svg>'
        )

    projected: Dict[str, List[List[Tuple[float, float]]]] = {}
    xs: List[float] = []
    ys: List[float] = []
    # Europe viewport — used to filter rings but NOT to clip their vertices
    LON_MIN, LON_MAX = -25.0, 45.0
    LAT_MIN, LAT_MAX = 33.0, 72.0
    for iso3, rings in polygons.items():
        p_rings = []
        for ring in rings:
            # Keep the ring intact if it has at least one point inside the window
            inside = any(LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX
                         for lon, lat in ring)
            if not inside:
                continue
            pr = [_lambert_cc(lon, lat) for lon, lat in ring]
            if len(pr) >= 3:
                p_rings.append(pr)
        if p_rings:
            projected[iso3] = p_rings
            # For the axis range, only use European countries' points
            if iso3 in {ISO2_TO_ISO3[c] for c in ALL_COUNTRIES}:
                for ring in p_rings:
                    for px, py in ring:
                        xs.append(px)
                        ys.append(py)
    if not xs:
        return ""
    pad = 20
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    sx = (width  - 2 * pad) / (xmax - xmin)
    sy = (height - 2 * pad - 40) / (ymax - ymin)  # reserve 40 px for title/legend
    s  = min(sx, sy)
    ox = pad + (width  - 2 * pad - s * (xmax - xmin)) / 2
    oy = 40  + (height - 40  - 2 * pad - s * (ymax - ymin)) / 2 + pad

    def _project(pt: Tuple[float, float]) -> Tuple[float, float]:
        x, y = pt
        return ox + (x - xmin) * s, oy + (ymax - y) * s

    iso2_set = set(ISO2_TO_ISO3[c] for c in values if c in ISO2_TO_ISO3)
    vals = [v for v in values.values() if isinstance(v, (int, float)) and not math.isnan(v)]
    if vals:
        vmin, vmax = min(vals), max(vals)
    else:
        vmin, vmax = 0.0, 1.0
    if vmax - vmin < 1e-9:
        vmax = vmin + 1.0

    parts: List[str] = []
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="font-family:sans-serif">'
    )
    parts.append(f'<rect width="{width}" height="{height}" fill="#f8fafc"/>')
    parts.append(
        f'<text x="{width/2}" y="24" text-anchor="middle" '
        f'font-size="13" fill="#1e293b" font-weight="600">{title}</text>'
    )
    iso3_to_iso2 = {v: k for k, v in ISO2_TO_ISO3.items()}
    label_anchors: List[Tuple[str, float, float]] = []
    for iso3, rings in projected.items():
        iso2 = iso3_to_iso2.get(iso3)
        if iso2 and iso2 in values and not math.isnan(values[iso2]):
            fill = cmap_fn((values[iso2] - vmin) / (vmax - vmin))
            stroke = "#0f172a"
        elif iso3 in iso2_set:
            fill, stroke = "#e2e8f0", "#94a3b8"
        else:
            fill, stroke = "#f1f5f9", "#cbd5e0"
        path_segments = []
        largest_ring = None
        largest_size = 0.0
        for ring in rings:
            if not ring:
                continue
            pts = [_project(p) for p in ring]
            d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z"
            path_segments.append(d)
            xs_r = [p[0] for p in pts]
            ys_r = [p[1] for p in pts]
            size = (max(xs_r) - min(xs_r)) * (max(ys_r) - min(ys_r))
            if size > largest_size:
                largest_size = size
                largest_ring = pts
        if path_segments:
            parts.append(
                f'<path d="{" ".join(path_segments)}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.4"/>'
            )
            if iso2 and iso2 in values and largest_ring is not None and largest_size > 120:
                cx = sum(p[0] for p in largest_ring) / len(largest_ring)
                cy = sum(p[1] for p in largest_ring) / len(largest_ring)
                label_anchors.append((iso2, cx, cy))
    for iso2, cx, cy in label_anchors:
        parts.append(
            f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
            f'font-size="9" fill="#0f172a" font-weight="600" '
            f'style="paint-order:stroke;stroke:#ffffff;stroke-width:2px;">'
            f'{iso2}</text>'
        )
    lg_x, lg_y, lg_w, lg_h = width - 180, height - 60, 160, 12
    for i in range(40):
        t = i / 39
        parts.append(
            f'<rect x="{lg_x + i * lg_w / 40:.1f}" y="{lg_y}" '
            f'width="{lg_w/40 + 0.5:.2f}" height="{lg_h}" '
            f'fill="{cmap_fn(t)}"/>'
        )
    parts.append(
        f'<text x="{lg_x}" y="{lg_y - 6}" font-size="10" fill="#334155">'
        f'{vmin:.1f} {units}</text>'
    )
    parts.append(
        f'<text x="{lg_x + lg_w}" y="{lg_y - 6}" font-size="10" '
        f'text-anchor="end" fill="#334155">{vmax:.1f} {units}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def build_svgs(stats: Dict[str, Dict[str, float]], shapefile_path: Path) -> Dict[str, str]:
    polygons = _load_shapefile(shapefile_path)
    out: Dict[str, str] = {}
    out["sec51_total_capacity"] = _choropleth_svg(
        polygons, {c: stats[c]["total_cap_gw"] for c in ALL_COUNTRIES},
        "5.1 — Capacite totale installee (GW)", "GW")
    out["sec52_vre_share"] = _choropleth_svg(
        polygons, {c: stats[c]["vre_share_pct"] for c in ALL_COUNTRIES},
        "5.2 — Part ENR variables (%)", "%")
    out["sec53_mean_price"] = _choropleth_svg(
        polygons, {c: stats[c]["price_mean"] for c in ALL_COUNTRIES},
        "5.3 — Prix moyen (EUR/MWh)", "EUR/MWh")
    out["sec54_gas_h2"] = _choropleth_svg(
        polygons, {c: stats[c]["gas_gw"] + stats[c]["h2_gw"] for c in ALL_COUNTRIES},
        "5.4 — CCGT Gaz + H2 (GW)", "GW")
    def _dominant(s: Mapping[str, float]) -> float:
        trio = [s["solar_gw"], s["wind_gw"], s["hydro_gw"]]
        return float(int(np.argmax(trio)))
    out["sec55_dominant_vre"] = _choropleth_svg(
        polygons, {c: _dominant(stats[c]) for c in ALL_COUNTRIES},
        "5.5 — Technologie ENR dominante (0=solaire,1=eolien,2=hydro)", "")
    out["sec56_tech_mix"] = _choropleth_svg(
        polygons, {c: stats[c]["storage_gw"] for c in ALL_COUNTRIES},
        "5.6 — Capacite stockage (composite placeholder)", "GW")
    out["sec57_electrolysis"] = _choropleth_svg(
        polygons, {c: stats[c].get("electrolysis_gw", 0.0) for c in ALL_COUNTRIES},
        "5.7 — Electrolyse (GW)", "GW")
    out["sec58_h2_demand"] = _choropleth_svg(
        polygons, {c: stats[c].get("h2_demand_twh", 0.0) for c in ALL_COUNTRIES},
        "5.8 — Demande H2 industrielle (TWh)", "TWh")
    return out


# =====================================================================
# Advanced figure builders (§13, §14, §15)
# =====================================================================

def build_figures_advanced(data: "RawData",
                            stats: Dict[str, Dict[str, float]],
                            eu: Dict[str, float],
                            stress: Dict[str, object],
                            dunkel: Dict[str, object],
                            suff: Dict[str, Dict[str, float]],
                            costs: Dict[str, Dict[str, float]],
                            lcoe: Dict[str, Dict[str, float]]) -> Dict[str, str]:
    """PNG figures for advanced sections 13..18 (stress, dunkelflaute,
    sufficiency, dispatch cost, LCOE reconstructed)."""
    figs: Dict[str, str] = {}

    month_h = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30]) * 24

    # ---------- §13.1 — Peak-hours coverage mix ----------
    mc = stress["mix_contrib"]
    peak_hours = stress["peak_hours"]
    n_peak = len(peak_hours)
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(13, 5),
                                   gridspec_kw={"width_ratios": [1.5, 1]})

    entries = [
        ("VRE (solar + eolien + RoR)", mc["vre_gw"],           "#22c55e"),
        ("Hydro reservoir (turbinage)", mc["reservoir_hydro_gw"], "#1e90ff"),
        ("Stockage (out - in)",         mc["storage_out_gw"],    "#a855f7"),
        ("Gaz",                          mc["gas_gw"],            "#ef4444"),
        ("Hydrogene",                    mc["h2_gw"],             "#ec4899"),
        ("Dechets",                      mc["waste_gw"],          "#64748b"),
    ]
    labels = [e[0] for e in entries]
    vals   = np.array([e[1] for e in entries])
    colors = [e[2] for e in entries]
    bars = axA.barh(labels, vals, color=colors, edgecolor="white")
    axA.invert_yaxis()
    axA.set_xlabel("Puissance moyenne lors des 100 h de pointe residuelle (GW)")
    axA.axvline(0, color=MPL_FG, linewidth=0.6)
    axA.grid(axis="x", alpha=0.4); axA.set_axisbelow(True)
    for b, v in zip(bars, vals):
        axA.text(v + max(abs(vals)) * 0.01, b.get_y() + b.get_height() / 2,
                 f"{v:+.1f}", va="center", fontsize=9, color=MPL_FG)

    axB.axis("off")
    axB.text(0.02, 0.96, "Lecture", fontsize=12, fontweight="bold",
             transform=axB.transAxes)
    narr = [
        f"Top {n_peak} h de residuelle EU",
        f"Demande moyenne : {mc['demand_gw']:.0f} GW",
        f"Residuelle moy. : {mc['residual_gw']:.0f} GW",
        f"Delestage moy.  : {mc['unserved_gw']:.2f} GW",
        "",
        "La somme des barres ci-contre + VRE",
        "doit couvrir la demande. Si une brique",
        "manque, le systeme recourt a l'ENS.",
    ]
    y = 0.86
    for line in narr:
        axB.text(0.02, y, line, fontsize=10,
                 color=MPL_FG if not line.startswith("Top") else MPL_MUTED,
                 transform=axB.transAxes, family="monospace")
        y -= 0.075
    fig.suptitle("Mix de couverture pendant les heures les plus tendues",
                 x=0.02, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    figs["sec131_peak_mix"] = _fig_to_data_uri(fig)

    # ---------- §13.2 — Residual load correlation matrix ----------
    corr: pd.DataFrame = stress["corr_matrix"]
    fig, ax = _fig((9.5, 8), "Correlation de la charge residuelle horaire entre pays")
    im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1, interpolation="nearest")
    ax.set_xticks(range(len(corr.columns)))
    ax.set_yticks(range(len(corr.index)))
    ax.set_xticklabels(corr.columns, rotation=90, fontsize=7)
    ax.set_yticklabels(corr.index, fontsize=7)
    cb = fig.colorbar(im, ax=ax, shrink=0.75, pad=0.02)
    cb.set_label("Correlation de Pearson")
    # Annotate strong correlations (|r| > 0.9) with a dot
    for i in range(len(corr)):
        for j in range(len(corr)):
            if i != j and abs(corr.values[i, j]) > 0.9:
                ax.plot(j, i, ".", color="white", markersize=3)
    ax.set_xlabel("Pays"); ax.set_ylabel("Pays")
    figs["sec132_corr_matrix"] = _fig_to_data_uri(fig)

    # ---------- §13.3 — Scatter peak residual vs hours > 0 ----------
    pc = stress["peak_gw_per_country"]
    hp = stress["hours_pos_per_country"]
    ctys = sorted(pc.keys())
    xs = np.array([pc[c] for c in ctys])
    ys = np.array([hp[c] for c in ctys])
    sizes = np.array([max(5.0, stats[c]["total_cap_gw"]) for c in ctys])
    fig, ax = _fig((11, 5.5),
                   "Pression adequacy par pays (taille = capacite installee)")
    sc = ax.scatter(xs, ys, s=sizes * 6, c=xs, cmap="Reds",
                    edgecolor="#1e293b", linewidth=0.5, alpha=0.85)
    for c, x, y in zip(ctys, xs, ys):
        if x > np.percentile(xs, 60) or y > np.percentile(ys, 60):
            ax.annotate(c, (x, y), fontsize=8, ha="left", va="center",
                        xytext=(4, 0), textcoords="offset points")
    ax.set_xlabel("Charge residuelle maximale (GW)")
    ax.set_ylabel("Heures avec residuelle > 0")
    ax.grid(alpha=0.4); ax.set_axisbelow(True)
    fig.colorbar(sc, ax=ax, shrink=0.85).set_label("Pic residuel (GW)")
    figs["sec133_stress_scatter"] = _fig_to_data_uri(fig)

    # ---------- §14.2 — Dunkelflaute episode duration histogram ----------
    per_c = dunkel["per_country"]
    all_runs_global: List[int] = []
    for c, d in per_c.items():
        all_runs_global.extend(d["runs"])
    fig, ax = _fig((11, 4.5),
                   f"Distribution des episodes < {dunkel['threshold_pct']:.0f} % CF-VRE (30 pays)")
    if all_runs_global:
        bins = np.arange(0, max(all_runs_global) + 6, 6)
        ax.hist(all_runs_global, bins=bins, color="#ef4444",
                edgecolor="white", alpha=0.85)
        ax.set_xlabel("Duree de l'episode (heures)")
        ax.set_ylabel("Nombre d'episodes (toutes especes, 30 pays)")
        ax.axvline(12, color="#1e90ff", linestyle="--", linewidth=1,
                   label="12 h")
        ax.axvline(72, color="#f59e0b", linestyle="--", linewidth=1,
                   label="72 h (3 jours)")
        ax.legend(loc="upper right", fontsize=9, frameon=False)
        ax.grid(alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.text(0.5, 0.5, "Pas d'episode detecte (capacites VRE nulles)",
                ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
    figs["sec142_dunkel_hist"] = _fig_to_data_uri(fig)

    # ---------- §14.3 — Top-10 worst Dunkelflaute weeks EU ----------
    wdef = dunkel["weekly_deficit_gwh"]
    order = np.argsort(wdef)[::-1][:10]
    weeks = np.arange(1, 53)
    fig, ax = _fig((11, 4.8),
                   "Top 10 semaines de deficit VRE EU (vs moyenne annuelle)")
    bars = ax.barh([f"S{weeks[i]:02d}" for i in order],
                   wdef[order], color="#ef4444", edgecolor="white")
    ax.invert_yaxis()
    ax.set_xlabel("Deficit VRE hebdomadaire vs moyenne annuelle (GWh)")
    ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    for b, v in zip(bars, wdef[order]):
        ax.text(v + max(wdef[order]) * 0.005,
                b.get_y() + b.get_height() / 2,
                f"{v:,.0f}".replace(",", " "), va="center", fontsize=9)
    figs["sec143_dunkel_top_weeks"] = _fig_to_data_uri(fig)

    # ---------- §15.1 — Sufficiency scatter (load factor vs MWh/capita) ----------
    suff_c = [c for c in ALL_COUNTRIES if c in suff and not math.isnan(suff[c].get("mwh_per_capita", float("nan")))]
    xs = np.array([suff[c]["mwh_per_capita"] for c in suff_c])
    ys = np.array([suff[c]["load_factor"]    for c in suff_c])
    sizes = np.array([max(5.0, stats[c]["demand_twh"]) for c in suff_c])
    fig, ax = _fig((11, 5.5),
                   "Signature sufficiency : facteur de charge vs demande par habitant")
    sc = ax.scatter(xs, ys, s=sizes * 6, c=xs, cmap="viridis",
                    edgecolor="#1e293b", linewidth=0.5, alpha=0.85)
    for c, x, y in zip(suff_c, xs, ys):
        ax.annotate(c, (x, y), fontsize=7, ha="left", va="center",
                    xytext=(4, 0), textcoords="offset points")
    ax.set_xlabel("Demande annuelle par habitant (MWh/cap)")
    ax.set_ylabel("Facteur de charge de la demande (moyenne / pointe)")
    ax.grid(alpha=0.4); ax.set_axisbelow(True)
    fig.colorbar(sc, ax=ax, shrink=0.85).set_label("MWh/cap")
    figs["sec151_suff_scatter"] = _fig_to_data_uri(fig)

    # ---------- §15.2 — Winter/summer demand ratio ----------
    ratios = [(c, suff[c]["winter_summer_ratio"]) for c in suff_c]
    ratios.sort(key=lambda kv: kv[1])
    fig, ax = _fig((11, 5.5), "Ratio demande hiver / demande ete")
    bar_colors = ["#0ea5e9" if r[1] >= 1 else "#f59e0b" for r in ratios]
    ax.barh([c for c, _ in ratios], [r for _, r in ratios],
            color=bar_colors, edgecolor="white")
    ax.axvline(1.0, color=MPL_FG, linewidth=0.8, linestyle="--")
    ax.set_xlabel("Ratio H/E (> 1 = pays thermo-sensible au chauffage)")
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    figs["sec152_winter_summer"] = _fig_to_data_uri(fig)

    # ---------- §17.1 — Cost breakdown per country (stacked bar, bn EUR) ----------
    cost_sorted = sorted(ALL_COUNTRIES,
                         key=lambda c: costs[c]["total_bn"], reverse=True)
    fig, ax = _fig((13, 6),
                   "Cout variable de dispatch par pays (Mrd EUR/an)")
    bottom = np.zeros(len(cost_sorted))
    comps = [
        ("opex_conv_bn",  "OPEX conversion", "#1e90ff"),
        ("opex_stor_bn",  "OPEX stockage",   "#a855f7"),
        ("capex_conv_bn", "CAPEX conversion","#0ea5e9"),
        ("capex_stor_bn", "CAPEX stockage",  "#6366f1"),
    ]
    for key, label, color in comps:
        vals = np.array([costs[c][key] for c in cost_sorted])
        if vals.sum() < 1e-9:
            continue
        ax.bar(cost_sorted, vals, bottom=bottom,
               color=color, label=label, edgecolor="white", linewidth=0.3)
        bottom += vals
    ax.set_ylabel("Cout annualise (Mrd EUR)")
    ax.set_xticks(range(len(cost_sorted)))
    ax.set_xticklabels(cost_sorted, rotation=45, ha="right", fontsize=8)
    ax.legend(loc="upper right", fontsize=9, frameon=False)
    ax.grid(axis="y", alpha=0.4); ax.set_axisbelow(True)
    for i, c in enumerate(cost_sorted):
        t = bottom[i]
        if t > 0:
            ax.text(i, t, f"{t:.1f}", ha="center", va="bottom",
                    fontsize=7, color=MPL_FG)
    total_all = sum(costs[c]["total_bn"] for c in ALL_COUNTRIES)
    ax.text(0.99, 1.02, f"Total EU : {total_all:.1f} Mrd EUR/an",
            transform=ax.transAxes, ha="right", fontsize=10,
            color=MPL_MUTED, fontstyle="italic")
    figs["sec171_cost_breakdown"] = _fig_to_data_uri(fig)

    # ---------- §17.2 — Ranking EUR/MWh (cost per MWh served) ----------
    eur_mwh = [(c, costs[c]["eur_per_mwh_dem"]) for c in ALL_COUNTRIES
               if not math.isnan(costs[c]["eur_per_mwh_dem"])]
    eur_mwh.sort(key=lambda kv: kv[1])
    fig, ax = _fig((11, 6.5),
                   "Cout variable de dispatch par MWh consomme (EUR/MWh)")
    ctys = [c for c, _ in eur_mwh]
    vals = [v for _, v in eur_mwh]
    mean_v = float(np.mean(vals)) if vals else 0.0
    bar_colors = ["#22c55e" if v < mean_v else "#ef4444" for v in vals]
    bars = ax.barh(ctys, vals, color=bar_colors, edgecolor="white")
    ax.axvline(mean_v, color=MPL_FG, linewidth=1.0, linestyle="--",
               label=f"Moyenne EU : {mean_v:.1f} EUR/MWh")
    ax.set_xlabel("Cout complet moyen (EUR/MWh consomme)")
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    ax.legend(loc="lower right", fontsize=9, frameon=False)
    for bar, v in zip(bars, vals):
        ax.text(v + max(vals) * 0.005, bar.get_y() + bar.get_height() / 2,
                f"{v:.1f}", va="center", fontsize=8, color=MPL_FG)
    figs["sec172_cost_per_mwh"] = _fig_to_data_uri(fig)

    # ---------- LCOE reconstructed: stacked CAPEX + OPEX per country ----
    lcoe_sorted = sorted(
        [c for c in ALL_COUNTRIES if c in lcoe and not math.isnan(lcoe[c].get("total_bn", float("nan")))],
        key=lambda c: lcoe[c]["total_bn"], reverse=True)
    fig, ax = _fig((13, 6),
                   "Cout complet reconstruit par pays — CAPEX (annuites externes) + OPEX (modele)")
    if lcoe_sorted:
        bottom = np.zeros(len(lcoe_sorted))
        comps = [
            ("capex_solar_bn",   "CAPEX solaire",        "#facc15"),
            ("capex_wind_bn",    "CAPEX eolien",         "#22d3ee"),
            ("capex_hydro_bn",   "CAPEX hydro",          "#3b82f6"),
            ("capex_firm_bn",    "CAPEX gaz + H2",       "#94a3b8"),
            ("capex_storage_bn", "CAPEX stockage",       "#a855f7"),
            ("opex_bn",          "OPEX dispatch (modele)","#0f766e"),
        ]
        for key, label, color in comps:
            vals = np.array([lcoe[c][key] for c in lcoe_sorted])
            if vals.sum() < 1e-9:
                continue
            ax.bar(lcoe_sorted, vals, bottom=bottom,
                   color=color, label=label, edgecolor="white", linewidth=0.3)
            bottom += vals
        ax.set_ylabel("Cout annualise (Mrd EUR/an)")
        ax.set_xticks(range(len(lcoe_sorted)))
        ax.set_xticklabels(lcoe_sorted, rotation=45, ha="right", fontsize=8)
        ax.legend(loc="upper right", fontsize=9, frameon=False, ncol=2)
        ax.grid(axis="y", alpha=0.4); ax.set_axisbelow(True)
        for i, c in enumerate(lcoe_sorted):
            t = bottom[i]
            if t > 0:
                ax.text(i, t, f"{t:.1f}", ha="center", va="bottom",
                        fontsize=7, color=MPL_FG)
        total_all = sum(lcoe[c]["total_bn"] for c in lcoe_sorted)
        ax.text(0.99, 1.02,
                f"Total EU reconstruit : {total_all:.1f} Mrd EUR/an",
                transform=ax.transAxes, ha="right", fontsize=10,
                color=MPL_MUTED, fontstyle="italic")
    figs["sec181_lcoe_breakdown"] = _fig_to_data_uri(fig)

    # ---------- LCOE ranking EUR/MWh ------------------------------------
    lcoe_mwh = [(c, lcoe[c]["eur_per_mwh"])
                for c in ALL_COUNTRIES
                if c in lcoe and not math.isnan(lcoe[c].get("eur_per_mwh", float("nan")))]
    lcoe_mwh.sort(key=lambda kv: kv[1])
    fig, ax = _fig((11, 6.5),
                   "LCOE reconstruit par MWh consomme (EUR/MWh)")
    if lcoe_mwh:
        ctys = [c for c, _ in lcoe_mwh]
        vals = [v for _, v in lcoe_mwh]
        mean_v = float(np.mean(vals))
        bar_colors = ["#22c55e" if v < mean_v else "#ef4444" for v in vals]
        bars = ax.barh(ctys, vals, color=bar_colors, edgecolor="white")
        ax.axvline(mean_v, color=MPL_FG, linewidth=1.0, linestyle="--",
                   label=f"Moyenne EU : {mean_v:.1f} EUR/MWh")
        ax.set_xlabel("Cout complet reconstruit (EUR/MWh consomme)")
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
        ax.legend(loc="lower right", fontsize=9, frameon=False)
        for bar, v in zip(bars, vals):
            ax.text(v + max(vals) * 0.005,
                    bar.get_y() + bar.get_height() / 2,
                    f"{v:.1f}", va="center", fontsize=8, color=MPL_FG)
    figs["sec182_lcoe_ranking"] = _fig_to_data_uri(fig)

    return figs


def build_svgs_advanced(dunkel: Dict[str, object],
                         costs: Dict[str, Dict[str, float]],
                         shapefile_path: Path) -> Dict[str, str]:
    """SVG choropleths for §14.1 (longest Dunkelflaute) and §17.3 (cost EUR/MWh)."""
    polygons = _load_shapefile(shapefile_path)
    out: Dict[str, str] = {}
    longest = {c: float(d["longest_h"]) for c, d in dunkel["per_country"].items()}

    # Invert palette: red = long, green = short, via custom cmap
    def _red_green(t: float) -> str:
        # t=0 -> green, t=1 -> red. linear in HSV-like RGB
        t = max(0.0, min(1.0, t))
        stops = [
            (0.00, (22, 163,  74)),   # green
            (0.50, (234, 179,   8)),  # amber
            (1.00, (220,  38,  38)),  # red
        ]
        for i in range(len(stops) - 1):
            a, ca = stops[i]; b, cb = stops[i + 1]
            if a <= t <= b:
                u = (t - a) / (b - a)
                r = int(ca[0] + (cb[0] - ca[0]) * u)
                g = int(ca[1] + (cb[1] - ca[1]) * u)
                bl = int(ca[2] + (cb[2] - ca[2]) * u)
                return f"#{r:02x}{g:02x}{bl:02x}"
        return "#dc2626"

    out["sec141_dunkel_map"] = _choropleth_svg(
        polygons, longest,
        f"14.1 — Plus long episode consecutif CF-VRE < {dunkel['threshold_pct']:.0f} % (h)",
        "h", cmap_fn=_red_green)

    eur_mwh_map = {c: costs[c]["eur_per_mwh_dem"]
                   for c in ALL_COUNTRIES
                   if not math.isnan(costs[c].get("eur_per_mwh_dem", float("nan")))}
    if eur_mwh_map:
        out["sec173_cost_map"] = _choropleth_svg(
            polygons, eur_mwh_map,
            "17.3 — Cout complet moyen par MWh consomme (EUR/MWh)",
            "EUR/MWh", cmap_fn=_red_green)
    else:
        out["sec173_cost_map"] = _choropleth_svg(
            polygons, {c: 0.0 for c in ALL_COUNTRIES},
            "17.3 — Cout complet moyen par MWh consomme (EUR/MWh)",
            "EUR/MWh", cmap_fn=_red_green)
    return out


# =====================================================================
# HTML surgical patcher
# =====================================================================

@dataclass
class Patcher:
    template: str

    @classmethod
    def from_file(cls, path: Path) -> "Patcher":
        return cls(template=path.read_text(encoding="utf-8"))

    def patch_pngs(self, figs: Dict[str, str]) -> None:
        order = [
            "sec41_overview", "sec42_mix_per_country", "sec61_synthese",
            "sec71_duration", "sec72_histogram", "sec73_heatmap",
            "sec74_spread", "sec81_annual_dispatch", "sec82_week4",
            "sec83_week30", "sec91_soc", "sec92_soc_analysis",
            "sec101_flows_map", "sec102_flows_analysis", "sec111_residual",
            "sec121_shedding_heatmap", "sec122_shedding_timeline",
        ]
        pattern = re.compile(r'src="data:image/png;base64,[^"]+"')
        idx = [0]
        def _repl(m: re.Match) -> str:
            i = idx[0]; idx[0] += 1
            if i < len(order):
                return f'src="{figs[order[i]]}"'
            return m.group(0)
        self.template = pattern.sub(_repl, self.template)

    def patch_svgs(self, svgs: Dict[str, str]) -> None:
        order = ["sec51_total_capacity", "sec52_vre_share", "sec53_mean_price",
                 "sec54_gas_h2", "sec55_dominant_vre", "sec56_tech_mix"]
        pattern = re.compile(r"<svg[\s\S]*?</svg>")
        idx = [0]
        def _repl(m: re.Match) -> str:
            i = idx[0]; idx[0] += 1
            if i < len(order):
                return svgs[order[i]]
            return m.group(0)
        self.template = pattern.sub(_repl, self.template)

    def patch_table_4_3(self, table_html: str) -> None:
        pattern = re.compile(r"<table[\s\S]*?</table>", re.IGNORECASE)
        self.template = pattern.sub(lambda m: table_html, self.template, count=1)

    def _find_js_value_span(self, name: str) -> Optional[Tuple[int, int]]:
        """Return (start, end) of a top-level JS literal assigned to `name`.
        Handles nested objects, arrays and strings (both quote kinds)."""
        m = re.search(rf"(const|let|var)\s+{re.escape(name)}\s*=\s*", self.template)
        if not m:
            return None
        i = m.end()
        s = self.template
        while i < len(s) and s[i] not in "{[":
            i += 1
        if i == len(s):
            return None
        open_ch = s[i]
        close_ch = "}" if open_ch == "{" else "]"
        start = i
        depth = 0
        in_str = False
        quote = ""
        while i < len(s):
            c = s[i]
            if in_str:
                if c == "\\":
                    i += 2; continue
                if c == quote:
                    in_str = False
            else:
                if c in ('"', "'", "`"):
                    in_str = True; quote = c
                elif c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        return (start, i)
            i += 1
        return None

    def patch_js_constant(self, name: str, new_literal: str) -> None:
        span = self._find_js_value_span(name)
        if not span:
            return
        start, end = span
        self.template = self.template[:start] + new_literal + self.template[end:]

    def inject_js_constant_after(self, name_after: str, new_const_name: str,
                                 new_literal: str) -> None:
        if re.search(rf"(const|let|var)\s+{re.escape(new_const_name)}\s*=",
                     self.template):
            return
        span = self._find_js_value_span(name_after)
        if not span:
            return
        _, end = span
        j = end
        s = self.template
        while j < len(s) and s[j] in ";\r\n":
            j += 1
        inject = f"\nconst {new_const_name} = {new_literal};\n"
        self.template = s[:j] + inject + s[j:]

    def patch_draw_price_chart(self) -> None:
        """Replace the synthetic per-country price generation block by a
        real lookup in PRICE_DATA."""
        start_re = re.search(
            r"active\.forEach\(iso\s*=>\s*\{[^{}]*const\s+base\s*=\s*COUNTRY_STATS\[iso\]\.price;",
            self.template)
        if not start_re:
            return
        # Walk from the opening `{` after `=>` to its matching `}`
        arrow_idx = self.template.rfind("=>", 0, start_re.end())
        body_open = self.template.find("{", arrow_idx)
        depth = 0
        i = body_open
        in_str = False
        quote = ""
        s = self.template
        while i < len(s):
            c = s[i]
            if in_str:
                if c == "\\":
                    i += 2; continue
                if c == quote:
                    in_str = False
            else:
                if c in ('"', "'", "`"):
                    in_str = True; quote = c
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
            i += 1
        # Consume the closing "`);`" of the forEach call
        while i < len(s) and s[i] in ") ;\r\n":
            i += 1
        replacement = textwrap.dedent("""\
            active.forEach(iso => {
              const key = priceView === 'year' ? 'year'
                         : priceView === 'week_summer' ? 'week_summer'
                         : 'week_winter';
              const src = (typeof PRICE_DATA !== 'undefined' && PRICE_DATA[key] && PRICE_DATA[key][iso])
                          ? PRICE_DATA[key][iso] : [];
              const pts = src.slice();
              weeklyData[iso] = pts;
              if (pts.length) yMax = Math.max(yMax, ...pts);
            });
            """)
        # Find start of 'active.forEach(' (before the `=>`)
        call_start = self.template.rfind("active.forEach(", 0, start_re.end())
        if call_start == -1:
            return
        self.template = s[:call_start] + replacement + s[i:]

    def append_advanced_sections(self,
                                   figs_adv: Dict[str, str],
                                   svgs_adv: Dict[str, str],
                                   stress: Dict[str, object],
                                   dunkel: Dict[str, object],
                                   suff: Dict[str, Dict[str, float]],
                                   costs: Dict[str, Dict[str, float]],
                                   lcoe: Dict[str, Dict[str, float]],
                                   residual_payload: str,
                                   firm_payload: str,
                                   h2_stats: Optional[Dict[str, object]] = None,
                                   h2_figs: Optional[Dict[str, str]] = None,
                                   eu: Optional[Dict[str, float]] = None) -> None:
        """Build HTML blocks for the new sections 13-18 and inject them just
        before the closing </body> tag. Also:
          - extracts the template's existing sections 13 (drivers), 14
            (coherence) and 15 (limits), renames them, and reorders them
            (14 -> 19, 15 -> 20, 13 -> "Conclusion" just above the footer);
          - rebuilds the Plan du Rapport TOC accordingly;
          - relocates the <footer> strictly at the bottom of the page.
        All styles are inline so no CSS dependency is introduced.
        """
        mc = stress["mix_contrib"]
        n_peak = len(stress["peak_hours"])
        peak_mean = stress["peak_mean_gw"]

        # §13 narrative paragraph built from actual numbers
        sum_firm = (mc["gas_gw"] + mc["h2_gw"] + mc["reservoir_hydro_gw"]
                    + mc["waste_gw"] + mc["storage_out_gw"])
        p13 = (
            f"Pendant les <b>{n_peak} heures</b> de plus forte charge residuelle EU, "
            f"la demande moyenne atteint <b>{mc['demand_gw']:.0f} GW</b> "
            f"et la residuelle moyenne est de <b>{peak_mean:.0f} GW</b>. "
            f"Elle est couverte par : gaz <b>{mc['gas_gw']:.1f} GW</b>, "
            f"hydrogene <b>{mc['h2_gw']:.1f} GW</b>, "
            f"hydro lac <b>{mc['reservoir_hydro_gw']:.1f} GW</b>, "
            f"stockage <b>{mc['storage_out_gw']:+.1f} GW</b> (dechargement net), "
            f"dechets <b>{mc['waste_gw']:.1f} GW</b>. "
            f"Somme dispatchable + storage : <b>{sum_firm:.1f} GW</b>. "
            f"Delestage moyen durant ces heures : <b>{mc['unserved_gw']:.2f} GW</b>."
        )

        # §14 summary
        per_c = dunkel["per_country"]
        worst = sorted(per_c.items(), key=lambda kv: kv[1]["longest_h"], reverse=True)[:5]
        worst_txt = ", ".join(
            f"{iso} <b>{int(d['longest_h'])} h</b>" for iso, d in worst
        )
        total_low = sum(d["total_low_h"] for d in per_c.values())
        p14 = (
            f"Seuil Dunkelflaute : CF-VRE &lt; <b>{dunkel['threshold_pct']:.0f} %</b> "
            f"de la capacite installee. Les 5 pays aux episodes les plus longs sont : "
            f"{worst_txt}. Heures totales cumulees sous seuil (30 pays) : "
            f"<b>{total_low:,} h</b>.".replace(",", " ")
        )

        # §15 summary
        suff_c = [c for c in ALL_COUNTRIES
                  if c in suff and not math.isnan(suff[c].get("mwh_per_capita", float("nan")))]
        if suff_c:
            min_cap = min(suff_c, key=lambda c: suff[c]["mwh_per_capita"])
            max_cap = max(suff_c, key=lambda c: suff[c]["mwh_per_capita"])
            max_lf  = max(suff_c, key=lambda c: suff[c]["load_factor"])
            p15 = (
                f"Demande par habitant la plus basse : <b>{min_cap}</b> "
                f"({suff[min_cap]['mwh_per_capita']:.1f} MWh/cap). "
                f"La plus haute : <b>{max_cap}</b> "
                f"({suff[max_cap]['mwh_per_capita']:.1f} MWh/cap). "
                f"Facteur de charge maximal : <b>{max_lf}</b> "
                f"({suff[max_lf]['load_factor']:.2f})."
            )
        else:
            p15 = "Signature sufficiency indisponible (population manquante)."

        def _img(key: str, figs: Dict[str, str]) -> str:
            return f'<img src="{figs.get(key, "")}" style="width:100%;max-width:1100px;display:block;margin:12px auto;"/>'

        # Wrapper style — matches the soft slate palette of the rest of the page
        SECT = ('style="max-width:1200px;margin:48px auto 0;padding:24px 32px;'
                'background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;'
                'box-shadow:0 1px 2px rgba(15,23,42,0.04);font-family:-apple-system,'
                'Segoe UI,Roboto,sans-serif;color:#1e293b;"')
        H2 = ('style="font-size:22px;font-weight:700;color:#0f172a;'
              'margin:0 0 8px 0;border-left:4px solid #1e90ff;padding-left:12px;"')
        H3 = ('style="font-size:16px;font-weight:600;color:#1e293b;'
              'margin:24px 0 8px 0;"')
        P  = ('style="font-size:13.5px;line-height:1.55;color:#334155;'
              'margin:0 0 12px 0;"')

        block_13 = f"""
<section id="sec-stress" {SECT}>
  <h2 {H2}>13. Stress adequacy</h2>
  <p {P}>Cette section analyse le systeme <b>aux heures ou il est le plus tendu</b>, c'est-a-dire
  quand la charge residuelle (demande moins production VRE variable) est la plus elevee.
  Les metriques ci-dessous sont calculees directement depuis <code>operation_conversion_power</code>
  et <code>operation_storage_power_out/in</code> sur l'horizon des {n_peak} heures de pointe EU.</p>
  <p {P}>{p13}</p>
  <h3 {H3}>Mix de couverture lors des heures les plus tendues</h3>
  {_img("sec131_peak_mix", figs_adv)}
  <h3 {H3}>Correlation de la charge residuelle entre pays</h3>
  <p {P}>Une correlation elevee entre deux pays signifie que leurs heures de tension arrivent
  simultanement : l'interconnexion perd alors son effet de foisonnement. Les points blancs
  marquent |r| &gt; 0.9.</p>
  {_img("sec132_corr_matrix", figs_adv)}
  <h3 {H3}>Pression adequacy par pays</h3>
  {_img("sec133_stress_scatter", figs_adv)}
</section>
"""

        svg_141 = svgs_adv.get("sec141_dunkel_map", "")
        block_14 = f"""
<section id="sec-dunkelflaute" {SECT}>
  <h2 {H2}>14. Dunkelflaute (episodes de faible VRE)</h2>
  <p {P}>{p14}</p>
  <h3 {H3}>Plus long episode consecutif sous seuil par pays</h3>
  <div style="max-width:800px;margin:0 auto;">{svg_141}</div>
  <h3 {H3}>Distribution des durees d'episodes</h3>
  {_img("sec142_dunkel_hist", figs_adv)}
  <h3 {H3}>Semaines de plus fort deficit VRE a l'echelle EU</h3>
  {_img("sec143_dunkel_top_weeks", figs_adv)}
</section>
"""

        block_15 = f"""
<section id="sec-sufficiency" {SECT}>
  <h2 {H2}>15. Signature sufficiency</h2>
  <p {P}>{p15}</p>
  <p {P}><i>Les populations utilisees sont les references Eurostat / ONS / SSB / BFS 2023,
  codees en dur dans le generateur. Seule la <b>demande</b> vient du modele.</i></p>
  <h3 {H3}>Facteur de charge vs demande par habitant</h3>
  {_img("sec151_suff_scatter", figs_adv)}
  <h3 {H3}>Ratio demande hiver / demande ete</h3>
  {_img("sec152_winter_summer", figs_adv)}
</section>
"""

        # Interactive canvas explorer
        options = "\n".join(
            f'      <option value="{c}"{" selected" if c == "FR" else ""}>{c} — {COUNTRY_NAMES_FR.get(c, c)}</option>'
            for c in ALL_COUNTRIES
        )

        block_16 = f"""
<section id="sec-explorer" {SECT}>
  <h2 {H2}>16. Exploration interactive de la charge residuelle</h2>
  <p {P}>Selectionnez un pays pour voir sa charge residuelle horaire (GW).
  La ligne rouge horizontale indique la capacite dispatchable + stockage
  installee dans ce pays. Le mode <b>monotone</b> trie les heures par residuelle
  decroissante; le mode <b>chronologique</b> montre la sequence annuelle.</p>
  <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:16px 0;">
    <label style="font-size:13px;color:#334155;">
      Pays :
      <select id="residualCountrySelect" style="margin-left:6px;padding:5px 8px;border:1px solid #cbd5e0;border-radius:6px;font-size:13px;background:#ffffff;">
{options}
      </select>
    </label>
    <div role="group" style="display:inline-flex;border:1px solid #cbd5e0;border-radius:6px;overflow:hidden;">
      <button id="residualViewChrono" class="residualBtn" data-mode="chrono" style="border:none;padding:6px 12px;background:#1e90ff;color:#ffffff;font-size:12px;cursor:pointer;">Chronologique</button>
      <button id="residualViewMono" class="residualBtn" data-mode="mono" style="border:none;padding:6px 12px;background:#ffffff;color:#334155;font-size:12px;cursor:pointer;border-left:1px solid #cbd5e0;">Monotone</button>
    </div>
    <label style="font-size:13px;color:#334155;">
      Lissage :
      <select id="residualSmooth" style="margin-left:6px;padding:5px 8px;border:1px solid #cbd5e0;border-radius:6px;font-size:13px;background:#ffffff;">
        <option value="1">1 h</option>
        <option value="24" selected>24 h</option>
        <option value="168">168 h (1 sem.)</option>
      </select>
    </label>
    <div id="residualMeta" style="font-size:12px;color:#64748b;margin-left:auto;"></div>
  </div>
  <canvas id="residualCanvas" width="1100" height="420"
          style="width:100%;max-width:1100px;height:auto;display:block;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;"></canvas>
  <p {P} style="margin-top:8px;"><i>Donnees : residuelle horaire = demande (input_dataset) - generation VRE (Solar + Wind on/off + RoR), directement depuis le NC solution.
  Ligne rouge : capacite dispatchable + stockage (GW) reellement installee par le modele dans ce pays.</i></p>
</section>
<script>
(function() {{
  const RESIDUAL_DATA = {residual_payload};
  const FIRM_CAPACITY = {firm_payload};
  const canvas = document.getElementById('residualCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let currentIso = 'FR';
  let currentMode = 'chrono';
  let currentSmooth = 24;

  function rollingMean(a, w) {{
    if (w <= 1) return a.slice();
    const out = new Array(a.length);
    let s = 0;
    for (let i = 0; i < a.length; i++) {{
      s += a[i];
      if (i >= w) s -= a[i - w];
      out[i] = s / Math.min(i + 1, w);
    }}
    return out;
  }}

  function draw() {{
    const data = RESIDUAL_DATA[currentIso] || [];
    if (!data.length) return;
    let series = rollingMean(data, currentSmooth);
    if (currentMode === 'mono') {{
      series = series.slice().sort((a, b) => b - a);
    }}
    const W = canvas.width, H = canvas.height;
    const PAD_L = 56, PAD_R = 20, PAD_T = 20, PAD_B = 40;
    const plotW = W - PAD_L - PAD_R;
    const plotH = H - PAD_T - PAD_B;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, W, H);

    const yMin = Math.min(0, ...series);
    const yMax = Math.max(...series, FIRM_CAPACITY[currentIso] || 0);
    const yRange = (yMax - yMin) || 1;
    const yToPx = y => PAD_T + plotH * (1 - (y - yMin) / yRange);
    const xToPx = i => PAD_L + plotW * (i / (series.length - 1));

    // Grid
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    ctx.font = '11px -apple-system,Segoe UI,Roboto,sans-serif';
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'right';
    const nYticks = 6;
    for (let k = 0; k <= nYticks; k++) {{
      const yv = yMin + (yMax - yMin) * k / nYticks;
      const py = yToPx(yv);
      ctx.beginPath(); ctx.moveTo(PAD_L, py); ctx.lineTo(W - PAD_R, py); ctx.stroke();
      ctx.fillText(yv.toFixed(0) + ' GW', PAD_L - 6, py + 4);
    }}

    // X ticks — months or decile
    ctx.textAlign = 'center';
    if (currentMode === 'chrono') {{
      const months = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
      const mh = [0, 744, 1416, 2160, 2880, 3624, 4344, 5088, 5832, 6552, 7296, 8016];
      for (let k = 0; k < 12; k++) {{
        const px = xToPx(mh[k]);
        ctx.beginPath(); ctx.moveTo(px, PAD_T); ctx.lineTo(px, H - PAD_B); ctx.stroke();
        ctx.fillText(months[k], px, H - PAD_B + 16);
      }}
    }} else {{
      for (let k = 0; k <= 10; k++) {{
        const px = PAD_L + plotW * k / 10;
        ctx.beginPath(); ctx.moveTo(px, PAD_T); ctx.lineTo(px, H - PAD_B); ctx.stroke();
        ctx.fillText((k * 10) + '%', px, H - PAD_B + 16);
      }}
    }}

    // Firm capacity line
    const firm = FIRM_CAPACITY[currentIso] || 0;
    if (firm > 0) {{
      const py = yToPx(firm);
      ctx.strokeStyle = '#ef4444';
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(PAD_L, py); ctx.lineTo(W - PAD_R, py); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#ef4444';
      ctx.textAlign = 'left';
      ctx.fillText('Capacite dispatchable + storage : ' + firm.toFixed(1) + ' GW',
                   PAD_L + 6, py - 4);
    }}

    // Zero line
    if (yMin < 0 && yMax > 0) {{
      const pz = yToPx(0);
      ctx.strokeStyle = '#94a3b8';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD_L, pz); ctx.lineTo(W - PAD_R, pz); ctx.stroke();
    }}

    // Residual line
    ctx.strokeStyle = '#1e90ff';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    for (let i = 0; i < series.length; i++) {{
      const px = xToPx(i);
      const py = yToPx(series[i]);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }}
    ctx.stroke();

    // Fill above firm line (deficit zone)
    if (firm > 0) {{
      ctx.fillStyle = 'rgba(239,68,68,0.15)';
      ctx.beginPath();
      ctx.moveTo(xToPx(0), yToPx(firm));
      let inRed = false;
      for (let i = 0; i < series.length; i++) {{
        if (series[i] > firm) {{
          if (!inRed) {{ ctx.moveTo(xToPx(i), yToPx(firm)); inRed = true; }}
          ctx.lineTo(xToPx(i), yToPx(series[i]));
        }} else if (inRed) {{
          ctx.lineTo(xToPx(i), yToPx(firm));
          ctx.closePath(); ctx.fill();
          ctx.beginPath();
          inRed = false;
        }}
      }}
      if (inRed) {{
        ctx.lineTo(xToPx(series.length - 1), yToPx(firm));
        ctx.closePath(); ctx.fill();
      }}
    }}

    // Axis frame
    ctx.strokeStyle = '#64748b';
    ctx.lineWidth = 1;
    ctx.strokeRect(PAD_L, PAD_T, plotW, plotH);

    // Meta
    const maxV = Math.max(...series);
    const hPos = data.filter(v => v > 0).length;
    const hDeficit = firm > 0 ? data.filter(v => v > firm).length : 0;
    const meta = document.getElementById('residualMeta');
    if (meta) {{
      meta.innerHTML = 'Pic : <b>' + maxV.toFixed(1) + ' GW</b> &nbsp; | &nbsp; ' +
                       'h &gt; 0 : <b>' + hPos + '</b> &nbsp; | &nbsp; ' +
                       'h &gt; firm : <b style="color:#ef4444;">' + hDeficit + '</b>';
    }}
  }}

  const sel = document.getElementById('residualCountrySelect');
  if (sel) sel.addEventListener('change', e => {{ currentIso = e.target.value; draw(); }});

  const smoothSel = document.getElementById('residualSmooth');
  if (smoothSel) smoothSel.addEventListener('change', e => {{
    currentSmooth = parseInt(e.target.value, 10) || 1;
    draw();
  }});

  document.querySelectorAll('.residualBtn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      currentMode = btn.dataset.mode;
      document.querySelectorAll('.residualBtn').forEach(b => {{
        b.style.background = b.dataset.mode === currentMode ? '#1e90ff' : '#ffffff';
        b.style.color      = b.dataset.mode === currentMode ? '#ffffff' : '#334155';
      }});
      draw();
    }});
  }});

  draw();
}})();
</script>
"""

        # Section 17 — variable dispatch cost (was the old §17)
        svg_173 = svgs_adv.get("sec173_cost_map", "")
        cost_vals = [v["total_bn"] for v in costs.values() if not math.isnan(v.get("total_bn", float("nan")))]
        total_eu_bn = sum(cost_vals) if cost_vals else 0.0
        eur_vals = [v["eur_per_mwh_dem"] for v in costs.values()
                    if not math.isnan(v.get("eur_per_mwh_dem", float("nan")))]
        mean_eur = (sum(eur_vals) / len(eur_vals)) if eur_vals else float("nan")
        p17 = (
            f"Somme des couts d'exploitation renvoyes par le modele "
            f"(<code>operation_conversion_costs</code> + <code>operation_storage_costs</code>), "
            f"agreges au niveau EU30 : <b>{total_eu_bn:.2f} Mrd EUR/an</b>. "
            f"Cout variable moyen par MWh consomme (moyenne non ponderee des pays) : "
            f"<b>{mean_eur:.1f} EUR/MWh</b>. "
            f"<i>Note : dans ce run a capacites fixees CLEVER 2050, "
            f"les variables d'investissement (<code>planning_*</code>) sont bornees a la flotte CLEVER "
            f"et leurs couts annualises (<code>planning_*_costs</code>) sont nuls dans le jeu de donnees. "
            f"Ce graphique ne represente donc que la partie variable du dispatch "
            f"(combustible gaz/H2, O&amp;M variable, CO2 le cas echeant). "
            f"La section suivante reconstruit un cout complet en injectant "
            f"des annuites d'investissement externes.</i>"
        )
        block_17 = f"""
<section id="sec-dispatch-cost" {SECT}>
  <h2 {H2}>17. Cout variable moyen de dispatch</h2>
  <p {P}>{p17}</p>
  <h3 {H3}>Decomposition du cout de dispatch par pays (Mrd EUR/an)</h3>
  {_img("sec171_cost_breakdown", figs_adv)}
  <h3 {H3}>Cout variable moyen par MWh consomme (classement EU30)</h3>
  {_img("sec172_cost_per_mwh", figs_adv)}
  <h3 {H3}>Cartographie du cout variable par MWh</h3>
  <div style="max-width:800px;margin:0 auto;">{svg_173}</div>
</section>
"""

        # Section 18 — LCOE reconstructed with external annuities
        lcoe_totals = [v["total_bn"] for v in lcoe.values()
                       if not math.isnan(v.get("total_bn", float("nan")))]
        lcoe_capex = [v["capex_bn"] for v in lcoe.values()
                      if not math.isnan(v.get("capex_bn", float("nan")))]
        lcoe_eurmwh = [v["eur_per_mwh"] for v in lcoe.values()
                       if not math.isnan(v.get("eur_per_mwh", float("nan")))]
        tot_lcoe_bn   = sum(lcoe_totals) if lcoe_totals else 0.0
        tot_lcoe_cx   = sum(lcoe_capex)  if lcoe_capex  else 0.0
        mean_lcoe_eur = (sum(lcoe_eurmwh) / len(lcoe_eurmwh)) if lcoe_eurmwh else float("nan")
        p18 = (
            f"On applique aux capacites installees par POMMES des annuites externes "
            f"(JRC ETRI 2014 + Danish Energy Agency Technology Catalogue 2050, WACC 5 %) "
            f"et on ajoute l'OPEX renvoye par le modele. "
            f"CAPEX total reconstruit : <b>{tot_lcoe_cx:.1f} Mrd EUR/an</b>. "
            f"Cout complet (CAPEX + OPEX) EU30 : <b>{tot_lcoe_bn:.1f} Mrd EUR/an</b>. "
            f"LCOE moyen par MWh consomme (moyenne non ponderee des pays) : "
            f"<b>{mean_lcoe_eur:.1f} EUR/MWh</b>. "
            f"<i>Ces chiffres sont une reconstruction indicative : ils ne proviennent pas "
            f"de l'optimisation POMMES (qui considere les capacites comme donnees), mais ils "
            f"permettent de comparer les pays sur une base LCOE homogene et transparente. "
            f"Annuites sur la puissance installee (EUR par kW-an) : solaire 33.7, eolien "
            f"terrestre 73.6, eolien en mer 144.2, hydro 150, gaz CCGT 71.3, turbine H2 80.4, "
            f"batterie 21.4, STEP 40, caverne H2 15. "
            f"Annuites sur l'energie stockee (EUR par kWh de capacite de stockage, par an) : "
            f"batterie 16.4, STEP 0.30, caverne saline H2 0.12 — le stock H2 a une annuite "
            f"tres faible car le cout specifique des cavernes salines est de l'ordre de "
            f"2 EUR par kWh overnight sur 40 ans.</i>"
        )
        block_18 = f"""
<section id="sec-lcoe" {SECT}>
  <h2 {H2}>18. Cout complet reconstruit (LCOE + annuites externes)</h2>
  <p {P}>{p18}</p>
  <h3 {H3}>Decomposition CAPEX reconstruit + OPEX dispatch par pays</h3>
  {_img("sec181_lcoe_breakdown", figs_adv)}
  <h3 {H3}>LCOE moyen par MWh consomme (classement EU30)</h3>
  {_img("sec182_lcoe_ranking", figs_adv)}
</section>
"""

        # ── §19 — Hydrogen section ──
        block_19 = ""
        if h2_stats is not None and h2_figs is not None and eu is not None:
            h2_dem_total = eu.get("h2_demand_twh", 0.0)
            h2_elec_gw   = eu.get("electrolysis_gw", 0.0)
            h2_ccgt_gw   = eu.get("h2_ccgt_gw", 0.0)
            h2_spill     = eu.get("h2_spill_twh", 0.0)
            # Compute EU-wide electrolyser load factor
            _elec_cf = (eu.get("electrolysis_twh", 0) /
                        (h2_elec_gw * 8.76) * 100) if h2_elec_gw > 0.01 else 0.0
            # H2 transport total
            _h2_flow_total = sum(abs(v) for v in h2_stats.get("h2_flows", {}).values())
            p19 = (
                f"Ce systeme couple electricite + hydrogene integre la demande "
                f"industrielle H2 issue de <b>DemandForge</b> (6 secteurs : ammoniaque, "
                f"raffinage, eSAF, maritime, olefines, acier) et une chaine "
                f"d'approvisionnement H2 injectee par <b>SupplyForge</b> (electrolyse, "
                f"stockage salt-cavern, re-electrification H2 CCGT). "
                f"<br><br>"
                f"<b>Chiffres cles EU30 :</b> "
                f"Demande H2 industrielle : <b>{h2_dem_total:.0f} TWh</b>. "
                f"Capacite electrolyse installee : <b>{h2_elec_gw:.0f} GW</b> "
                f"(facteur de charge moyen : {_elec_cf:.0f} %). "
                f"Capacite H2 CCGT (re-electrification) : <b>{h2_ccgt_gw:.0f} GW</b>. "
                f"Spillage H2 : <b>{h2_spill:.1f} TWh</b>. "
                f"Transport H2 par pipeline : <b>{_h2_flow_total:.1f} TWh</b>"
                + (" (les electrolyseurs locaux rendent le transport inter-zone non rentable)."
                   if _h2_flow_total < 1.0 else ".") +
                f" Stockage H2 salt-cavern : <b>0 GW</b> investi "
                f"(le surdimensionnement de l'electrolyse — {h2_elec_gw:.0f} GW pour "
                f"~{h2_dem_total / 8.76:.0f} GW de charge moyenne — fournit assez de "
                f"flexibilite intra-journaliere pour rendre le stockage non rentable "
                f"aux couts modelises)."
            )
            block_19 = f"""
<section id="sec-hydrogen" {SECT}>
  <h2 {H2}>19. Systeme hydrogene couple</h2>
  <p {P}>{p19}</p>
  <h3 {H3}>19.1 Demande H2 industrielle par pays</h3>
  {_img("sec191_h2_demand", h2_figs)}
  <h3 {H3}>19.2 Capacite electrolyse par pays</h3>
  {_img("sec192_electrolysis_cap", h2_figs)}
  <h3 {H3}>19.3 Bilan H2 par pays (demande + re-electrification + spillage)</h3>
  {_img("sec193_h2_balance", h2_figs)}
  <h3 {H3}>19.4 Bilan H2 horaire EU (production / consommation)</h3>
  {_img("sec194_h2_dispatch", h2_figs)}
  <h3 {H3}>19.5 Flux H2 par pipeline</h3>
  {_img("sec195_h2_flows", h2_figs)}
  <h3 {H3}>19.6 Capacite H2 CCGT : existant vs neuf</h3>
  {_img("sec196_h2_ccgt_split", h2_figs)}
  <h3 {H3}>19.7 Facteur de charge electrolyse par pays</h3>
  {_img("sec197_electrolyser_lf", h2_figs)}
  <h3 {H3}>19.8 Prix marginal H2 (valeur duale)</h3>
  {_img("sec198_h2_price", h2_figs)}
  <h3 {H3}>19.9 Stockage H2 — etat de charge</h3>
  {_img("sec199_h2_storage_soc", h2_figs)}
</section>
"""

        advanced = (block_13 + block_14 + block_15
                    + block_16 + block_17 + block_18 + block_19)

        # -----------------------------------------------------------------
        # Extract the template's existing sections 13/14/15 so we can put
        # them AFTER the new advanced content. Section 13 (drivers) becomes
        # the Conclusion and sits just above the footer.
        # -----------------------------------------------------------------
        def _extract_card(html: str, card_id: str) -> Tuple[str, str, Optional[Tuple[int, int]]]:
            pat = re.compile(
                r'<div class="section-card" id="' + re.escape(card_id) + r'">'
                r'[\s\S]*?</div>\s*(?=<!--|<div class="section-card"|</div><!-- \.container|</div>\s*</div>)',
                re.IGNORECASE)
            m = pat.search(html)
            if not m:
                return html, "", None
            block = m.group(0)
            return (html[:m.start()] + html[m.end():]), block, (m.start(), m.end())

        # Extract in order: drivers, coherence, limits.
        tpl, drivers_block, _ = _extract_card(self.template, "drivers")
        tpl, coherence_block, _ = _extract_card(tpl, "coherence")
        tpl, limits_block,    _ = _extract_card(tpl, "limits")
        self.template = tpl

        # Rename headings and strip the "13.X/14.X/15.X" prefixes from h3.
        def _strip_hprefix(block: str) -> str:
            block = re.sub(r'<h3([^>]*)>\s*\d+\.\d+\s*', r'<h3\1>', block)
            return block

        if coherence_block:
            coherence_block = re.sub(
                r'<h2[^>]*>[^<]*</h2>',
                '<h2>20. Verification de Coherence</h2>',
                coherence_block, count=1)
            coherence_block = _strip_hprefix(coherence_block)

        if limits_block:
            limits_block = re.sub(
                r'<h2[^>]*>[^<]*</h2>',
                '<h2>21. Limites &amp; Perspectives</h2>',
                limits_block, count=1)
            limits_block = _strip_hprefix(limits_block)

        if drivers_block:
            drivers_block = re.sub(
                r'<h2[^>]*>[^<]*</h2>',
                '<h2>Conclusion — Variables determinantes du systeme</h2>',
                drivers_block, count=1)
            drivers_block = _strip_hprefix(drivers_block)

        # -----------------------------------------------------------------
        # Rebuild the Plan du Rapport (TOC) to match the final order.
        # -----------------------------------------------------------------
        toc_new = (
            '<div class="toc-grid">\n'
            '    <div class="toc-item"><a href="#exec">1. Résumé Exécutif</a></div>\n'
            '    <div class="toc-item"><a href="#method">2. Méthodologie</a></div>\n'
            '    <div class="toc-item"><a href="#scenario">3. Hypothèses CLEVER</a></div>\n'
            '    <div class="toc-item"><a href="#capacities">4. Capacités Installées</a></div>\n'
            '    <div class="toc-item"><a href="#maps">5. Cartographie Européenne</a></div>\n'
            '    <div class="toc-item"><a href="#adequacy">6. Adéquation &amp; Marges</a></div>\n'
            '    <div class="toc-item"><a href="#prices">7. Analyse des Prix</a></div>\n'
            '    <div class="toc-item"><a href="#prix-interactif">7b. Prix Interactifs 30 pays</a></div>\n'
            '    <div class="toc-item"><a href="#dispatch">8. Dispatch &amp; Profils Hebdo</a></div>\n'
            '    <div class="toc-item"><a href="#dispatch-stacked">8.5 Dispatch Technologique</a></div>\n'
            '    <div class="toc-item"><a href="#storage">9. Stockage &amp; Flexibilité</a></div>\n'
            '    <div class="toc-item"><a href="#flows">10. Flux Transfrontaliers</a></div>\n'
            '    <div class="toc-item"><a href="#residual">11. Charge Résiduelle</a></div>\n'
            '    <div class="toc-item"><a href="#shedding">12. Délestage</a></div>\n'
            '    <div class="toc-item"><a href="#sec-stress">13. Stress adequacy</a></div>\n'
            '    <div class="toc-item"><a href="#sec-dunkelflaute">14. Dunkelflaute</a></div>\n'
            '    <div class="toc-item"><a href="#sec-sufficiency">15. Signature sufficiency</a></div>\n'
            '    <div class="toc-item"><a href="#sec-explorer">16. Exploration interactive</a></div>\n'
            '    <div class="toc-item"><a href="#sec-dispatch-cost">17. Coût variable de dispatch</a></div>\n'
            '    <div class="toc-item"><a href="#sec-lcoe">18. LCOE reconstruit</a></div>\n'
            '    <div class="toc-item"><a href="#sec-hydrogen">19. Systeme hydrogene couple</a></div>\n'
            '    <div class="toc-item"><a href="#coherence">20. Verification Coherence</a></div>\n'
            '    <div class="toc-item"><a href="#limits">21. Limites &amp; Perspectives</a></div>\n'
            '    <div class="toc-item"><a href="#drivers">Conclusion</a></div>\n'
            '</div>'
        )
        # Match the toc-grid and ALL its inner toc-items up to the closing
        # </div> of the toc-grid itself (each toc-item is a single-line div,
        # so a lazy match on [\s\S]*?</div> would stop at the first item).
        self.template = re.sub(
            r'<div class="toc-grid">\s*(?:<div class="toc-item">[\s\S]*?</div>\s*)*</div>',
            lambda _m: toc_new,
            self.template, count=1)

        # -----------------------------------------------------------------
        # Relocate <footer>...</footer> to the very bottom of the page.
        # -----------------------------------------------------------------
        footer_match = re.search(r"<footer[\s\S]*?</footer>", self.template)
        footer_html = ""
        if footer_match:
            footer_html = footer_match.group(0)
            self.template = (self.template[:footer_match.start()]
                             + self.template[footer_match.end():])

        inside_tail = (advanced
                       + "\n" + coherence_block
                       + "\n" + limits_block
                       + "\n" + drivers_block + "\n")
        container_close = '</div><!-- .container -->'
        if container_close in self.template:
            self.template = self.template.replace(
                container_close, inside_tail + container_close, 1)
        else:
            # fallback: inject before </body>
            if "</body>" in self.template:
                self.template = self.template.replace(
                    "</body>", inside_tail + "</body>", 1)
            else:
                self.template = self.template + inside_tail

        # Put the footer strictly at the very bottom, outside .container.
        if footer_html:
            if "</body>" in self.template:
                self.template = self.template.replace(
                    "</body>", footer_html + "\n</body>", 1)
            else:
                self.template = self.template + "\n" + footer_html

    def write(self, path: Path) -> int:
        path.write_text(self.template, encoding="utf-8")
        return len(self.template.encode("utf-8"))


# =====================================================================
# §19 — Hydrogen system analytics
# =====================================================================

def compute_hydrogen_stats(data: "RawData",
                            stats: Dict[str, Dict[str, float]]) -> Dict[str, object]:
    """Compute hydrogen-specific analytics for §19."""
    sol, inp = data.sol, data.inp

    print(f"  [diag-h2] stats keys sample: {list(list(stats.values())[0].keys())}")
    _sample_c = list(stats.keys())[0]
    print(f"  [diag-h2] {_sample_c}: h2_demand_twh={stats[_sample_c].get('h2_demand_twh', 'MISSING')}, "
          f"electrolysis_gw={stats[_sample_c].get('electrolysis_gw', 'MISSING')}")

    h2_demand = {c: stats[c].get("h2_demand_twh", 0.0) for c in ALL_COUNTRIES}
    electrolysis_cap = {c: stats[c].get("electrolysis_gw", 0.0) for c in ALL_COUNTRIES}
    h2_ccgt = {c: stats[c].get("h2_total_ccgt_twh", 0.0) for c in ALL_COUNTRIES}
    h2_ccgt_cap = {c: stats[c].get("h2_total_ccgt_gw", 0.0) for c in ALL_COUNTRIES}

    # H2 spillage per country
    h2_spillage: Dict[str, float] = {c: 0.0 for c in ALL_COUNTRIES}
    if "operation_spillage_power" in sol.data_vars:
        sp = sol["operation_spillage_power"]
        if "resource" in sp.dims:
            _sp_h2_res = _coord_match(sp["resource"].values, "hydrogen")
            if _sp_h2_res is not None:
                sp_h2 = sp.sel(resource=_sp_h2_res)
                if "year_op" in sp_h2.dims:
                    sp_h2 = sp_h2.squeeze("year_op", drop=True)
                if "area" in sp_h2.dims:
                    sp_sum = sp_h2.sum(dim="hour").to_pandas() / 1e6
                    for c in ALL_COUNTRIES:
                        h2_spillage[c] = float(sp_sum.get(c, 0.0))

    # Hourly profiles (EU aggregate) for dispatch chart
    conv_h = sol["operation_conversion_power"]
    if "year_op" in conv_h.dims:
        conv_h = conv_h.squeeze("year_op", drop=True)

    elec_hourly_eu = np.zeros(8760)
    _ct_elec = _coord_match(conv_h["conversion_tech"].values, "electrolysis")
    if _ct_elec is not None:
        elec_hourly_eu = np.abs(conv_h.sel(conversion_tech=_ct_elec).sum(dim="area").values) / 1000.0

    h2_ccgt_hourly_eu = np.zeros(8760)
    for tech in ["Hydrogen_power_plant", "hydrogen_power_plant"]:
        _ct_h2 = _coord_match(conv_h["conversion_tech"].values, tech)
        if _ct_h2 is not None:
            h2_ccgt_hourly_eu += conv_h.sel(conversion_tech=_ct_h2).sum(dim="area").values / 1000.0

    # H2 transport flows
    h2_flows: Dict[str, float] = {}
    try:
        tpow = sol["operation_transport_power"]
        if "year_op" in tpow.dims:
            tpow = tpow.squeeze("year_op", drop=True)
        if "transport_tech" in tpow.dims:
            _tt_h2p = _coord_match(tpow["transport_tech"].values, "h2_pipeline")
            if _tt_h2p is not None:
                h2_tpow = tpow.sel(transport_tech=_tt_h2p)
                h2_link_twh = (h2_tpow.sum(dim="hour") / 1e6).to_pandas()
                h2_flows = {str(k): float(v) for k, v in h2_link_twh.items()}
    except Exception:
        pass

    # H2 demand hourly EU aggregate (GW)
    h2_demand_hourly_eu = np.zeros(8760)
    if data.h2_demand_hourly is not None:
        h2_demand_hourly_eu = data.h2_demand_hourly.sum(axis=1).values / 1000.0  # MW -> GW

    # H2 storage hourly (charge/discharge) EU aggregate (GW)
    h2_sto_hourly_eu = np.zeros(8760)
    h2_sto_energy_eu = np.zeros(8760)  # state of charge (GWh)
    if "operation_storage_power" in sol.data_vars:
        sto_p = sol["operation_storage_power"]
        if "year_op" in sto_p.dims:
            sto_p = sto_p.squeeze("year_op", drop=True)
        for st_name in ["h2_storage", "H2_SaltCavern"]:
            _st = _coord_match(sto_p["storage_tech"].values, st_name)
            if _st is not None:
                h2_sto_hourly_eu += sto_p.sel(storage_tech=_st).sum(dim="area").values / 1000.0
    if "operation_storage_energy" in sol.data_vars:
        sto_e = sol["operation_storage_energy"]
        if "year_op" in sto_e.dims:
            sto_e = sto_e.squeeze("year_op", drop=True)
        for st_name in ["h2_storage", "H2_SaltCavern"]:
            _st = _coord_match(sto_e["storage_tech"].values, st_name)
            if _st is not None:
                h2_sto_energy_eu += sto_e.sel(storage_tech=_st).sum(dim="area").values / 1000.0  # MWh -> GWh

    # H2 spillage hourly EU (GW)
    h2_spill_hourly_eu = np.zeros(8760)
    if "operation_spillage_power" in sol.data_vars:
        sp_all = sol["operation_spillage_power"]
        if "resource" in sp_all.dims:
            _sp_res = _coord_match(sp_all["resource"].values, "hydrogen")
            if _sp_res is not None:
                sp_h2_hr = sp_all.sel(resource=_sp_res)
                if "year_op" in sp_h2_hr.dims:
                    sp_h2_hr = sp_h2_hr.squeeze("year_op", drop=True)
                h2_spill_hourly_eu = sp_h2_hr.sum(dim="area").values / 1000.0

    # H2 shadow price from dual file (EUR/MWh)
    h2_price_hourly_eu = None
    if data.dual is not None and "operation_adequacy_constraint" in data.dual.data_vars:
        dual_adeq = data.dual["operation_adequacy_constraint"]
        if "resource" in dual_adeq.dims:
            _dr = _coord_match(dual_adeq["resource"].values, "hydrogen")
            if _dr is not None:
                dp = dual_adeq.sel(resource=_dr)
                if "year_op" in dp.dims:
                    dp = dp.squeeze("year_op", drop=True)
                # Demand-weighted average across areas (or simple mean)
                h2_price_hourly_eu = dp.mean(dim="area").values  # EUR/MWh

    return dict(
        h2_demand=h2_demand,
        electrolysis_cap=electrolysis_cap,
        h2_ccgt=h2_ccgt,
        h2_ccgt_cap=h2_ccgt_cap,
        h2_spillage=h2_spillage,
        elec_hourly_eu=elec_hourly_eu,
        h2_ccgt_hourly_eu=h2_ccgt_hourly_eu,
        h2_flows=h2_flows,
        h2_demand_hourly_eu=h2_demand_hourly_eu,
        h2_sto_hourly_eu=h2_sto_hourly_eu,
        h2_sto_energy_eu=h2_sto_energy_eu,
        h2_spill_hourly_eu=h2_spill_hourly_eu,
        h2_price_hourly_eu=h2_price_hourly_eu,
    )


def build_hydrogen_figures(data: "RawData",
                            stats: Dict[str, Dict[str, float]],
                            eu: Dict[str, float],
                            h2: Dict[str, object]) -> Dict[str, str]:
    """PNG figures for §19 — dedicated hydrogen section."""
    figs: Dict[str, str] = {}
    month_h = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30]) * 24

    # ---------- §19.1 — H2 demand by country (TWh, horizontal bar) ----------
    dem = h2["h2_demand"]
    countries_h2 = sorted([c for c in ALL_COUNTRIES if dem.get(c, 0) > 0.01],
                          key=lambda c: dem[c], reverse=True)
    fig, ax = _fig((11, max(4.5, len(countries_h2) * 0.28)),
                   "Demande H2 industrielle par pays (TWh/an)")
    if countries_h2:
        vals = [dem[c] for c in countries_h2]
        bars = ax.barh(countries_h2, vals, color="#a855f7", edgecolor="white")
        ax.invert_yaxis()
        ax.set_xlabel("TWh/an")
        for b, v in zip(bars, vals):
            ax.text(v + max(vals) * 0.01, b.get_y() + b.get_height() / 2,
                    f"{v:.1f}", va="center", fontsize=9, color=MPL_FG)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.text(0.5, 0.5, "Aucune demande H2 detectee",
                ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
    figs["sec191_h2_demand"] = _fig_to_data_uri(fig)

    # ---------- §19.2 — Electrolysis capacity by country (GW) ----------
    ecap = h2["electrolysis_cap"]
    countries_el = sorted([c for c in ALL_COUNTRIES if ecap.get(c, 0) > 0.01],
                          key=lambda c: ecap[c], reverse=True)
    fig, ax = _fig((11, max(4.5, len(countries_el) * 0.28)),
                   "Capacite electrolyse installee par pays (GW)")
    if countries_el:
        vals = [ecap[c] for c in countries_el]
        bars = ax.barh(countries_el, vals, color="#10b981", edgecolor="white")
        ax.invert_yaxis()
        ax.set_xlabel("GW")
        for b, v in zip(bars, vals):
            ax.text(v + max(vals) * 0.01, b.get_y() + b.get_height() / 2,
                    f"{v:.1f}", va="center", fontsize=9, color=MPL_FG)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    figs["sec192_electrolysis_cap"] = _fig_to_data_uri(fig)

    # ---------- §19.3 — H2 balance: demand vs CCGT vs spillage per country ----------
    countries_bal = sorted([c for c in ALL_COUNTRIES
                            if dem.get(c, 0) > 0.01 or h2["h2_ccgt"].get(c, 0) > 0.01],
                           key=lambda c: dem.get(c, 0) + h2["h2_ccgt"].get(c, 0), reverse=True)
    if countries_bal:
        fig, ax = _fig((13, max(5, len(countries_bal) * 0.3)),
                       "Bilan H2 par pays (TWh/an)")
        y_pos = np.arange(len(countries_bal))
        demand_vals = [dem.get(c, 0) for c in countries_bal]
        ccgt_vals = [h2["h2_ccgt"].get(c, 0) for c in countries_bal]
        spill_vals = [h2["h2_spillage"].get(c, 0) for c in countries_bal]
        # Stacked horizontal: demand (purple) + CCGT (violet) + spillage (grey)
        ax.barh(y_pos, demand_vals, color="#a855f7", edgecolor="white",
                label="Demande industrielle H2")
        ax.barh(y_pos, ccgt_vals, left=demand_vals, color="#7c3aed",
                edgecolor="white", label="Re-electrification (H2 CCGT)")
        left2 = [d + c for d, c in zip(demand_vals, ccgt_vals)]
        ax.barh(y_pos, spill_vals, left=left2, color="#94a3b8",
                edgecolor="white", label="Spillage H2")
        ax.set_yticks(y_pos)
        ax.set_yticklabels(countries_bal, fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("TWh/an")
        ax.legend(loc="lower right", fontsize=8, frameon=False)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
        figs["sec193_h2_balance"] = _fig_to_data_uri(fig)
    else:
        fig, ax = _fig((11, 4))
        ax.text(0.5, 0.5, "Pas de bilan H2 disponible",
                ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
        figs["sec193_h2_balance"] = _fig_to_data_uri(fig)

    # ---------- §19.4 — Full H2 balance: electrolysis, demand, storage, CCGT, spillage ----------
    fig, ax = _fig((14, 6),
                   "Bilan H2 horaire EU (GW, moy. glissante 7 j)")
    hours = np.arange(8760)
    _win = 168  # 7-day rolling window
    elec_smooth   = pd.Series(h2["elec_hourly_eu"]).rolling(_win, min_periods=1).mean().values
    dem_smooth    = pd.Series(h2["h2_demand_hourly_eu"]).rolling(_win, min_periods=1).mean().values
    ccgt_smooth   = pd.Series(h2["h2_ccgt_hourly_eu"]).rolling(_win, min_periods=1).mean().values
    sto_smooth    = pd.Series(h2["h2_sto_hourly_eu"]).rolling(_win, min_periods=1).mean().values
    spill_smooth  = pd.Series(h2["h2_spill_hourly_eu"]).rolling(_win, min_periods=1).mean().values

    # Positive side: electrolysis production + storage discharge
    sto_discharge = np.maximum(sto_smooth, 0)
    sto_charge    = np.maximum(-sto_smooth, 0)  # charge is negative in model, show as consumption

    # Supply side (positive): electrolysis + storage discharge
    ax.fill_between(hours, 0, elec_smooth, color="#10b981", alpha=0.65,
                    label="Electrolyse (production H2)")
    ax.fill_between(hours, elec_smooth, elec_smooth + sto_discharge, color="#06b6d4", alpha=0.5,
                    label="Destock. H2")

    # Consumption side (negative): demand + CCGT + storage charge + spillage
    ax.fill_between(hours, 0, -dem_smooth, color="#a855f7", alpha=0.65,
                    label="Demande industrielle H2")
    ax.fill_between(hours, -dem_smooth, -dem_smooth - ccgt_smooth, color="#7c3aed", alpha=0.55,
                    label="H2 CCGT (re-electrification)")
    ax.fill_between(hours, -dem_smooth - ccgt_smooth,
                    -dem_smooth - ccgt_smooth - sto_charge, color="#0ea5e9", alpha=0.45,
                    label="Stockage H2 (charge)")
    ax.fill_between(hours, -dem_smooth - ccgt_smooth - sto_charge,
                    -dem_smooth - ccgt_smooth - sto_charge - spill_smooth,
                    color="#94a3b8", alpha=0.45, label="Spillage H2")

    ax.axhline(0, color=MPL_FG, linewidth=0.8)
    ax.set_xlim(0, 8760)
    ax.set_xlabel("Heure de l'annee")
    ax.set_ylabel("GW")
    ax.set_xticks(month_h)
    ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax.legend(loc="upper right", fontsize=8, frameon=True, facecolor="white",
              edgecolor="#cbd5e1", ncol=2)
    ax.grid(alpha=0.4); ax.set_axisbelow(True)
    figs["sec194_h2_dispatch"] = _fig_to_data_uri(fig)

    # ---------- §19.5 — H2 transport flows (top links) ----------
    h2_flows = h2.get("h2_flows", {})
    fig, ax = _fig((11, 5),
                   "Flux H2 par pipeline (TWh/an)")
    if h2_flows:
        flow_s = pd.Series(h2_flows)
        abs_sorted = flow_s.abs().sort_values(ascending=True).tail(15)
        signed = flow_s.loc[abs_sorted.index]
        clean_lbl = [str(lk).replace("link_", "").replace("_", " -> ")
                     for lk in abs_sorted.index]
        cols = ["#a855f7" if v >= 0 else "#ef4444" for v in signed.values]
        ax.barh(clean_lbl, signed.values, color=cols, edgecolor="white")
        ax.axvline(0, color=MPL_FG, linewidth=0.8)
        ax.set_xlabel("Flux net (TWh/an)")
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.text(0.5, 0.5, "Aucun flux H2 pipeline detecte",
                ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
    figs["sec195_h2_flows"] = _fig_to_data_uri(fig)

    # ---------- §19.6 — H2 CCGT capacity split (existing vs fresh) ----------
    fig, ax = _fig((13, 5.5),
                   "Capacite H2 CCGT par pays : existant (CLEVER) vs neuf (SupplyForge)")
    countries_ccgt = sorted([c for c in ALL_COUNTRIES
                             if stats[c].get("h2_total_ccgt_gw", 0) > 0.001],
                            key=lambda c: stats[c]["h2_total_ccgt_gw"], reverse=True)
    if countries_ccgt:
        y = np.arange(len(countries_ccgt))
        existing = [stats[c].get("h2_gw", 0.0) for c in countries_ccgt]
        fresh = [stats[c].get("h2_fresh_gw", 0.0) for c in countries_ccgt]
        ax.barh(y, existing, color="#a855f7", edgecolor="white",
                label="H2 CCGT existant (CLEVER)")
        ax.barh(y, fresh, left=existing, color="#7c3aed", edgecolor="white",
                label="H2 CCGT neuf (SupplyForge)")
        ax.set_yticks(y)
        ax.set_yticklabels(countries_ccgt, fontsize=8)
        ax.invert_yaxis()
        ax.set_xlabel("GW")
        ax.legend(loc="lower right", fontsize=9, frameon=False)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    figs["sec196_h2_ccgt_split"] = _fig_to_data_uri(fig)

    # ---------- §19.7 — Electrolyser load factor by country (%) ----------
    countries_lf = sorted([c for c in ALL_COUNTRIES if ecap.get(c, 0) > 0.01],
                          key=lambda c: ecap[c], reverse=True)
    fig, ax = _fig((11, max(4.5, len(countries_lf) * 0.28)),
                   "Facteur de charge electrolyse par pays (%)")
    if countries_lf:
        lf_vals = []
        for c in countries_lf:
            cap = ecap.get(c, 0)
            gen = abs(stats[c].get("electrolysis_twh", 0.0))
            lf = (gen / (cap * 8.76)) * 100 if cap > 0.01 else 0.0
            lf_vals.append(lf)
        colors_lf = ["#10b981" if v > 40 else "#f59e0b" if v > 20 else "#ef4444"
                      for v in lf_vals]
        bars = ax.barh(countries_lf, lf_vals, color=colors_lf, edgecolor="white")
        ax.invert_yaxis()
        ax.set_xlabel("Facteur de charge (%)")
        ax.set_xlim(0, 100)
        for b, v in zip(bars, lf_vals):
            ax.text(v + 1, b.get_y() + b.get_height() / 2,
                    f"{v:.1f}%", va="center", fontsize=9, color=MPL_FG)
        ax.grid(axis="x", alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.text(0.5, 0.5, "Pas d'electrolyseurs detectes",
                ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
    figs["sec197_electrolyser_lf"] = _fig_to_data_uri(fig)

    # ---------- §19.8 — H2 shadow price (dual) ----------
    h2_price = h2.get("h2_price_hourly_eu")
    fig, ax = _fig((14, 4.5),
                   "Prix marginal H2 (valeur duale, EUR/MWh, moy. glissante 7 j)")
    if h2_price is not None and np.any(np.abs(h2_price) > 1e-6):
        price_smooth = pd.Series(h2_price).rolling(_win, min_periods=1).mean().values
        ax.fill_between(hours, 0, price_smooth, color="#f59e0b", alpha=0.55)
        ax.plot(hours, price_smooth, color="#d97706", linewidth=0.8)
        ax.set_xlim(0, 8760)
        ax.set_xlabel("Heure de l'annee")
        ax.set_ylabel("EUR/MWh")
        ax.set_xticks(month_h)
        ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
        # Add stats annotation
        _p_mean = float(np.mean(h2_price[h2_price != 0])) if np.any(h2_price != 0) else 0
        _p_max = float(np.max(h2_price))
        _p_min = float(np.min(h2_price))
        ax.text(0.02, 0.95, f"Moy: {_p_mean:.1f}  Min: {_p_min:.1f}  Max: {_p_max:.1f} EUR/MWh",
                transform=ax.transAxes, fontsize=9, va="top", color=MPL_FG,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#cbd5e1", alpha=0.9))
        ax.grid(alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.text(0.5, 0.5, "Pas de dual H2 disponible (fichier dual_2050.nc absent ?)",
                ha="center", va="center", transform=ax.transAxes)
        ax.axis("off")
    figs["sec198_h2_price"] = _fig_to_data_uri(fig)

    # ---------- §19.9 — H2 storage state of charge (if any) ----------
    h2_soc = h2.get("h2_sto_energy_eu", np.zeros(8760))
    fig, ax = _fig((14, 4.5),
                   "Stockage H2 — etat de charge EU (GWh)")
    if np.any(np.abs(h2_soc) > 0.01):
        ax.fill_between(hours, 0, h2_soc, color="#06b6d4", alpha=0.55)
        ax.plot(hours, h2_soc, color="#0891b2", linewidth=0.8)
        ax.set_xlim(0, 8760)
        ax.set_xlabel("Heure de l'annee")
        ax.set_ylabel("GWh")
        ax.set_xticks(month_h)
        ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
        _soc_max = float(np.max(h2_soc))
        ax.text(0.02, 0.95, f"Max: {_soc_max:.0f} GWh ({_soc_max/1000:.1f} TWh)",
                transform=ax.transAxes, fontsize=9, va="top", color=MPL_FG,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#cbd5e1", alpha=0.9))
        ax.grid(alpha=0.4); ax.set_axisbelow(True)
    else:
        ax.text(0.5, 0.5, "Aucun stockage H2 investi (choix economique du solveur)",
                ha="center", va="center", transform=ax.transAxes, fontsize=11, color=MPL_FG)
        ax.axis("off")
    figs["sec199_h2_storage_soc"] = _fig_to_data_uri(fig)

    return figs


# =====================================================================
# Figure export helper (--figures-dir)
# =====================================================================

def _export_figures_to_disk(out_dir: Path,
                              figs: Dict[str, str],
                              svgs: Dict[str, str],
                              figs_adv: Dict[str, str],
                              svgs_adv: Dict[str, str],
                              table_html: str) -> None:
    """Decode the PNG data URIs produced by matplotlib and the SVG strings
    and write one standalone file per figure to ``out_dir``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def _write_png(key: str, uri: str) -> None:
        if not uri or not uri.startswith("data:image/png;base64,"):
            return
        b64 = uri.split(",", 1)[1]
        (out_dir / f"{key}.png").write_bytes(base64.b64decode(b64))

    def _write_svg(key: str, svg: str) -> None:
        if not svg:
            return
        (out_dir / f"{key}.svg").write_text(svg, encoding="utf-8")

    for k, v in figs.items():
        _write_png(k, v)
    for k, v in figs_adv.items():
        _write_png(k, v)
    for k, v in svgs.items():
        _write_svg(k, v)
    for k, v in svgs_adv.items():
        _write_svg(k, v)

    if table_html:
        (out_dir / "table_4_3.html").write_text(
            "<!doctype html><meta charset='utf-8'>" + table_html,
            encoding="utf-8")

    n_png = sum(1 for v in list(figs.values()) + list(figs_adv.values())
                if v and v.startswith("data:image/png;base64,"))
    n_svg = sum(1 for v in list(svgs.values()) + list(svgs_adv.values()) if v)
    print(f"  [figures-dir] {n_png} PNG + {n_svg} SVG + table_4_3.html -> {out_dir}")


# =====================================================================
# Main
# =====================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    t0 = datetime.now()
    data  = load_data(args)
    log(args, 1, "Deriving country stats ...")
    stats = compute_country_stats(data)
    eu    = compute_eu_totals(data, stats)

    print("=" * 72)
    print("CLEVER 2050 observatory - headline numbers")
    print("=" * 72)
    print(f"  Objective (totex) : {eu['totex_bn']:>10.2f} Mrd EUR")
    print(f"    OPEX conversion : {eu['opex_conv_bn']:>10.2f} Mrd EUR")
    print(f"    OPEX storage    : {eu['opex_stor_bn']:>10.2f} Mrd EUR")
    print(f"    CAPEX conv+stor : {eu['capex_conv_bn']+eu['capex_stor_bn']:>10.2f} Mrd EUR")
    print(f"  Demand (elec.)    : {eu['demand_twh']:>10.1f} TWh")
    print(f"  Demand (H2)       : {eu.get('h2_demand_twh', 0):>10.1f} TWh")
    print(f"  Electrolysis      : {eu.get('electrolysis_gw', 0):>10.1f} GW")
    print(f"  H2 CCGT           : {eu.get('h2_ccgt_gw', 0):>10.1f} GW")
    print(f"  Generation        : {eu['gen_twh']:>10.1f} TWh   (VRE {eu['vre_share']:.1f}%)")
    print(f"  Storage           : {eu['storage_gw']:>10.1f} GW  / {eu['storage_gwh']/1000:.0f} TWh")
    print(f"  Curtailment       : {eu['spill_twh']:>10.1f} TWh")
    print(f"  ENS / LOLE        : {eu['ens_gwh']:.1f} GWh / {eu['lole_h']:.0f} h")
    print(f"  Mean price        : {eu['price_simple']:>10.2f} EUR/MWh")
    print(f"  DW mean price     : {eu['price_weighted']:>10.2f} EUR/MWh")

    log(args, 1, "Building figures ...")
    figs = build_figures(data, stats, eu)
    log(args, 1, "Building SVG maps ...")
    svgs = build_svgs(stats, args.shapefile)
    log(args, 1, "Building table 4.3 ...")
    table_html = build_adequacy_table_html(stats)
    log(args, 1, "Building JS payloads ...")
    dispatch_js = build_dispatch_payload(data)
    cstats_js   = build_country_stats_payload(stats)
    price_js    = build_price_data_payload(data)

    log(args, 1, "Computing advanced analytics (stress / dunkelflaute / sufficiency / cost) ...")
    stress = compute_stress_metrics(data, n_peak=100)
    dunkel = compute_dunkelflaute(data, stats, threshold_pct=10.0)
    suff   = compute_sufficiency(data, stats)
    costs  = compute_full_cost_per_country(data, stats)
    lcoe   = compute_lcoe_reconstructed(stats, costs)
    h2_stats = compute_hydrogen_stats(data, stats)

    print()
    print("  §19 hydrogen: demand {d:.0f} TWh, electrolyse {e:.0f} GW, H2 CCGT {c:.0f} GW".format(
        d=eu.get("h2_demand_twh", 0), e=eu.get("electrolysis_gw", 0), c=eu.get("h2_ccgt_gw", 0)))
    print("  §13 stress : {n} peak hours, mean residual = {r:.1f} GW".format(
        n=len(stress["peak_hours"]), r=stress["peak_mean_gw"]))
    print("               firm contrib -> gas {g:.1f} / H2 {h:.1f} / hydro {hy:.1f} / storage {s:+.1f} GW".format(
        g=stress["mix_contrib"]["gas_gw"],
        h=stress["mix_contrib"]["h2_gw"],
        hy=stress["mix_contrib"]["reservoir_hydro_gw"],
        s=stress["mix_contrib"]["storage_out_gw"]))
    worst5 = sorted(dunkel["per_country"].items(),
                    key=lambda kv: kv[1]["longest_h"], reverse=True)[:5]
    print("  §14 dunkel : longest < {t:.0f}% CF-VRE episodes -> ".format(
        t=dunkel["threshold_pct"])
        + ", ".join(f"{c} {int(d['longest_h'])}h" for c, d in worst5))

    log(args, 1, "Building advanced figures (sections 13-19) ...")
    figs_adv = build_figures_advanced(data, stats, eu, stress, dunkel, suff,
                                      costs, lcoe)
    log(args, 1, "Building hydrogen figures (§19) ...")
    h2_figs = build_hydrogen_figures(data, stats, eu, h2_stats)
    log(args, 1, "Building advanced SVG (§14.1 dunkelflaute + §17.3 cost maps) ...")
    svgs_adv = build_svgs_advanced(dunkel, costs, args.shapefile)
    log(args, 1, "Building residual-load payload (§16) ...")
    residual_payload = build_residual_payload(stress)
    firm_payload     = build_firm_capacity_payload(data, stats)

    if args.dry_run:
        print()
        print(f"[dry-run] figures built: {len(figs)}, svgs built: {len(svgs)}")
        print(f"[dry-run] advanced figs: {len(figs_adv)}, adv svgs: {len(svgs_adv)}")
        print(f"[dry-run] dispatch_js : {len(dispatch_js):>10,} chars")
        print(f"[dry-run] cstats_js   : {len(cstats_js):>10,} chars")
        print(f"[dry-run] price_js    : {len(price_js):>10,} chars")
        print(f"[dry-run] residual_js : {len(residual_payload):>10,} chars")
        print(f"[dry-run] firm_js     : {len(firm_payload):>10,} chars")
        print(f"[dry-run] table_html  : {len(table_html):>10,} chars")
        return 0

    log(args, 1, f"Loading template {args.template} ...")
    patcher = Patcher.from_file(args.template)
    log(args, 1, "  patching PNGs ...");          patcher.patch_pngs(figs)
    log(args, 1, "  patching SVGs ...");          patcher.patch_svgs(svgs)
    log(args, 1, "  patching table 4.3 ...");     patcher.patch_table_4_3(table_html)
    log(args, 1, "  patching DISPATCH ...");      patcher.patch_js_constant("DISPATCH", dispatch_js)
    log(args, 1, "  patching COUNTRY_STATS ..."); patcher.patch_js_constant("COUNTRY_STATS", cstats_js)
    log(args, 1, "  injecting PRICE_DATA ...");   patcher.inject_js_constant_after("STACK_ORDER", "PRICE_DATA", price_js)
    log(args, 1, "  patching drawPriceChart ..."); patcher.patch_draw_price_chart()
    log(args, 1, "  appending sections 13-18 + relocating 14/15/Conclusion + rebuilding TOC ...")
    patcher.append_advanced_sections(figs_adv, svgs_adv, stress, dunkel, suff,
                                     costs, lcoe,
                                     residual_payload, firm_payload,
                                     h2_stats=h2_stats, h2_figs=h2_figs, eu=eu)

    if args.figures_dir is not None:
        log(args, 1, f"Exporting figures to {args.figures_dir} ...")
        # Merge h2_figs into figs_adv for export
        figs_adv_all = {**figs_adv, **h2_figs}
        _export_figures_to_disk(args.figures_dir, figs, svgs,
                                figs_adv_all, svgs_adv, table_html)

    log(args, 1, f"Writing {args.output} ...")
    nbytes = patcher.write(args.output)
    sha = hashlib.sha256(patcher.template.encode("utf-8")).hexdigest()[:16]
    dt = (datetime.now() - t0).total_seconds()
    print()
    print(f"[OK] Wrote {args.output}  ({nbytes/1024/1024:.2f} MB, sha256={sha}, {dt:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
