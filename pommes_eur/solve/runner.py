"""
clever.runner — Model solver and result export module.

Role in pipeline
----------------
This module handles the final stages of the CLEVER modelling pipeline:

  1. **Demand loading** — Read hourly electricity demand from CSV and coerce to 8760 hours
  2. **Model execution** — Solve the POMMES model with proper sanitization and diagnostics
  3. **Result export** — Extract prices, conversion capacities, and storage results

Design notes
~~~~~~~~~~~~
- All paths are parameterized via clever.RESULTS_DIR; no hardcoded paths
- Unified sanitize_storage_inputs combines best practices from both implementations:
    - Reservoir_Hydro_Store → "reservoir_water" (from create_model version)
    - Other storage → "electricity" (from create_model version)
    - Battery_1h → E/P ratio 1.0 (from create_model version)
    - Battery_4h → E/P ratio 4.0 (from create_model version)
  This differs from run_prices_from_clever which used empty string for non-Pumped storage
- Gurobi solver optimized for interior-point with numerical focus
- Diagnostics (dataset, LP, solution, duals) written to directory if requested
- Infeasible models export IIS (Gurobi) for debugging

Usage
-----
.. code-block:: python

    from pommes_eur.solve.runner import (
        load_hourly_total,
        build_demand_dict,
        build_solver_options,
        run_model_without_ramping,
        export_prices,
        export_conversion_capacity,
        export_storage_capacity,
        export_storage_power_capacity,
    )

    # Load demand
    demand_df = load_hourly_total(path_to_hourly_csv)

    # Build demand dict keyed by area
    demand_dict = build_demand_dict(demand_df.groupby('year_op'))

    # Build and solve model
    linopy_model = run_model_without_ramping(
        model=energy_model,
        solver_name="gurobi",
        solver_options=build_solver_options("gurobi"),
        year_op=2050,
        write_lp=False,
        diagnostics_dir=clever.RESULTS_DIR / "diagnostics"
    )

    # Export results
    prices = export_prices(energy_model, year_op=2050)
    conv_caps = export_conversion_capacity(energy_model, year_op=2050)
    stor_caps = export_storage_capacity(energy_model, year_op=2050)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import polars as pl
import xarray as xr

from pommes.io.build_input_dataset import build_input_parameters
from pommes.model.build_model import build_model
from pommes.model.data_validation.dataset_check import check_inputs

from pommes_eur.constants import PRICE_NUMERICAL_TOL, EOLES_LIFETIME
from pommes_eur.constants import _ELECTRICITY_VOLL_OVERRIDE, _H2_VOLL_OVERRIDE
from pommes_eur.constants import _PIPELINE_EUR_PER_MW_PER_KM

# Area centroids (lon, lat) — generated once from Natural Earth ne_110m
# (largest-sub-polygon centroid; Malta manual). Used by
# apply_distance_based_pipeline_capex to turn the `_pipekmNNN` €/MW/km into a
# per-link CAPEX = NNN × great-circle distance between connected areas.
AREA_CENTROID_LONLAT: dict[str, tuple[float, float]] = {
    "AT": (14.076, 47.614), "BE": (4.581, 50.652), "BG": (25.195, 42.753),
    "CH": (8.118, 46.792), "CY": (33.04, 34.907), "CZ": (15.335, 49.775),
    "DE": (10.288, 51.134), "DK": (9.311, 56.22), "DZ": (3.05, 36.75),  # coastal: Algiers (Medgaz/GALSI landfall), not Sahara centroid
    "EE": (25.825, 58.644), "ES": (-3.617, 40.349), "FI": (26.212, 64.504),
    "FR": (2.339, 46.606), "GB": (-2.658, 53.883), "GR": (22.564, 39.342),
    "HR": (16.566, 45.016), "HU": (19.358, 47.2), "IE": (-8.01, 53.181),
    "IT": (12.219, 43.472), "LT": (23.881, 55.284), "LU": (5.965, 49.766),
    "LV": (24.833, 56.807), "LY": (11.8, 33.0), "MA": (-5.8, 35.8),  # coastal: LY Mellitah (Greenstream), MA Tangier (Gibraltar), not Sahara centroids
    "MT": (14.4, 35.9), "NL": (5.512, 52.299), "NO": (14.245, 64.537),
    "PL": (19.311, 52.148), "PT": (-8.056, 39.634), "RO": (24.943, 45.857),
    "SE": (16.596, 62.811), "SI": (14.938, 46.125), "SK": (19.508, 48.727),
    "TN": (10.9, 36.9),  # coastal: Cap Bon (Elmed landfall), not central-TN centroid
}

# MENA H₂ corridor lengths (km) — ACTUAL coastal/subsea landfall routes, used in
# place of the great-circle between MENA centroids (which sit deep in the Sahara,
# ~1000 km inland of the landfall point, so the centroid great-circle over-states
# the route by ~2×). Sources: SoutH2 corridor / European Hydrogen Backbone +
# existing gas-route analogues (Medgaz, GALSI, Elmed, Greenstream). Symmetric key.
MENA_ROUTE_KM: dict[frozenset, float] = {
    frozenset({"MA", "ES"}): 300.0,    # Gibraltar strait crossing
    frozenset({"DZ", "ES"}): 550.0,    # Medgaz analogue
    frozenset({"DZ", "IT"}): 1050.0,   # via Sardinia (GALSI / SoutH2)
    frozenset({"TN", "IT"}): 500.0,    # via Sicily (Elmed)
    frozenset({"LY", "IT"}): 520.0,    # Greenstream analogue
}


def _haversine_km(lonlat_a: tuple[float, float], lonlat_b: tuple[float, float]) -> float:
    """Great-circle distance (km) between two (lon, lat) points."""
    lo1, la1 = lonlat_a
    lo2, la2 = lonlat_b
    R = 6371.0
    p1, p2 = np.radians(la1), np.radians(la2)
    dp = np.radians(la2 - la1)
    dl = np.radians(lo2 - lo1)
    h = np.sin(dp / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return float(2.0 * R * np.arcsin(np.sqrt(h)))

# ═════════════════════════════════════════════════════════════════════
# Logging
# ═════════════════════════════════════════════════════════════════════
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════
# DEMAND LOADING
# ═════════════════════════════════════════════════════════════════════


def load_hourly_total(path: Path) -> pd.DataFrame:
    """
    Load hourly electricity demand from CSV.

    Reads a CSV with columns: area, year_op, datetime, component, load_mw.
    Pivots wide by component, reconstructs 'total' if missing, and validates.

    Parameters
    ----------
    path : Path
        Path to hourly_electricity_demand.csv

    Returns
    -------
    pd.DataFrame
        Columns: area, year_op, datetime, total
        Sorted by (area, year_op, datetime).

    Raises
    ------
    ValueError
        If required columns are missing or if total cannot be reconstructed.
    """
    df = pd.read_csv(path)

    required = {"area", "year_op", "datetime", "component", "load_mw"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing columns {sorted(missing)}")

    df = df.copy()
    df["area"] = df["area"].astype(str).str.upper().str.strip()
    df["year_op"] = pd.to_numeric(df["year_op"], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df["component"] = df["component"].astype(str).str.lower().str.strip()
    df["load_mw"] = pd.to_numeric(df["load_mw"], errors="coerce").fillna(0.0)

    df = df.dropna(subset=["year_op", "datetime"]).copy()
    df["year_op"] = df["year_op"].astype(int)

    wide = (
        df.pivot_table(
            index=["area", "year_op", "datetime"],
            columns="component",
            values="load_mw",
            aggfunc="mean",
        )
        .reset_index()
        .sort_values(["area", "year_op", "datetime"])
        .reset_index(drop=True)
    )

    if "total" not in wide.columns:
        components = [
            c
            for c in ["baseload", "winter_thermosensitive", "summer_thermosensitive", "ev"]
            if c in wide.columns
        ]
        if not components:
            raise ValueError("Unable to reconstruct total demand from components")
        wide["total"] = wide[components].sum(axis=1)

    wide["total"] = pd.to_numeric(wide["total"], errors="coerce").fillna(0.0)

    return wide[["area", "year_op", "datetime", "total"]]


def to_8760(g: pd.DataFrame) -> pd.DataFrame:
    """
    Coerce a single (area, year_op) group to exactly 8760 hours.

    Removes February 29, and pads or trims as needed.

    Parameters
    ----------
    g : pd.DataFrame
        DataFrame with columns: area, year_op, datetime, total
        Must contain data for exactly one (area, year_op) pair.

    Returns
    -------
    pd.DataFrame
        Same columns, guaranteed 8760 rows, sorted by datetime.

    Raises
    ------
    ValueError
        If the group is empty or cannot be coerced to 8760 hours.
    """
    g = g.sort_values("datetime").copy()
    if g.empty:
        raise ValueError("Empty series, cannot convert to 8760 hours")

    g["datetime"] = pd.to_datetime(g["datetime"], errors="coerce")
    g = g.dropna(subset=["datetime"]).copy()

    # Remove Feb 29
    feb29 = (g["datetime"].dt.month == 2) & (g["datetime"].dt.day == 29)
    g = g.loc[~feb29].copy()

    # Trim to 8760 if longer
    if len(g) > 8760:
        g = g.iloc[:8760].copy()

    # Pad with last value if shorter
    if len(g) < 8760:
        missing = 8760 - len(g)
        last_dt = g["datetime"].iloc[-1]
        last_val = float(g["total"].iloc[-1])

        pad = pd.DataFrame(
            {
                "area": [g["area"].iloc[-1]] * missing,
                "year_op": [int(g["year_op"].iloc[-1])] * missing,
                "datetime": pd.date_range(
                    start=last_dt + pd.Timedelta(hours=1),
                    periods=missing,
                    freq="h",
                ),
                "total": [last_val] * missing,
            }
        )
        g = pd.concat([g, pad], ignore_index=True)

    if len(g) != 8760:
        raise ValueError(f"Series cannot be coerced to 8760 hours: {len(g)}")

    return g.reset_index(drop=True)


def build_demand_dict(g_year: pd.DataFrame) -> dict[str, pl.DataFrame]:
    """
    Convert year-grouped demand DataFrame to dict of polars DataFrames by area.

    Parameters
    ----------
    g_year : pd.DataFrame
        DataFrame with all demand data for one year_op, columns: area, year_op, datetime, total
        (Typically obtained from load_hourly_total().groupby('year_op'))

    Returns
    -------
    dict[str, pl.DataFrame]
        Keys: area codes (uppercase)
        Values: polars DataFrames with columns [hour, year_op, demand]
                 hour: 0-8759 (int)
                 year_op: int
                 demand: float (MW)

    Raises
    ------
    ValueError
        If any area group cannot be coerced to 8760 hours.
    """
    demand_dict: dict[str, pl.DataFrame] = {}

    for area, g_area in g_year.groupby("area", sort=True):
        g_area = to_8760(g_area)
        year_op = int(g_area["year_op"].iloc[0])

        demand_dict[area] = pl.DataFrame(
            {
                "hour": np.arange(8760, dtype=int),
                "year_op": np.full(8760, year_op, dtype=int),
                "demand": g_area["total"].to_numpy(dtype=float),
            }
        )

    return demand_dict


# ═════════════════════════════════════════════════════════════════════
# SOLVER OPTIONS
# ═════════════════════════════════════════════════════════════════════


def build_solver_options(solver_name: str = "gurobi") -> dict:
    """
    Build solver-specific options.

    Parameters
    ----------
    solver_name : str, default "gurobi"
        Solver to use: "gurobi" or "highs"

    Returns
    -------
    dict
        Solver-specific options:
        - For Gurobi: barrier (Method=2) with Crossover=1,
          BarHomogeneous=1, NumericFocus=3 (high precision),
          Threads=16, NodefileStart=8.
        - For HiGHS: presolve="on"
    """
    if solver_name == "gurobi":
        return {
            "Method": 2,              # Barrier (interior-point)
            "Crossover": 0,           # Skip crossover (barrier solution is sufficient)
            "BarHomogeneous": 1,      # Homogeneous barrier formulation
            "NumericFocus": 3,        # High precision
            "Threads": int(os.environ.get("CLEVER_GRB_THREADS", "16")),
                                      # Env-controllable (CLEVER_GRB_THREADS) for
                                      # multi-run parallelism: e.g. 16 for 2 runs on
                                      # a 32-core box, 8 for 2 runs on a 16-core box.
                                      # 16 is the barrier sweet-spot on this LP size;
                                      # lower it when the shared cpuset narrows.
            "NodefileStart": 8,       # Spill B&B nodes to disk after 8 GB
            # NOTE on 2026-05-27: tried loosening BarConvTol/FeasibilityTol/
            # OptimalityTol to terminate the post-lock-fix LP without numerical
            # stagnation (Primal-Inf was sticking at ~1.2). Loosening alone
            # didn't fix it: barrier stagnation is a CONDITIONING issue, not a
            # tolerance issue. Reverted to defaults pending root-cause analysis
            # on the LP structure itself (matrix range, lock-NetImport noise,
            # VRE-floor vertex effects).
            # DualReductions and InfUnbdInfo only needed for IIS debugging:
            # "DualReductions": 0,
            # "InfUnbdInfo": 1,
        }

    # HiGHS: conservative and robust
    return {
        "presolve": "on",
    }


# ═════════════════════════════════════════════════════════════════════
# SANITIZATION
# ═════════════════════════════════════════════════════════════════════


def sanitize_absent_conversions(p: xr.Dataset) -> xr.Dataset:
    """
    Sanitize conversion technology inputs.

    Handles absent technologies (NaN capacities), fixes investment bounds,
    and sets sensible defaults for ramping, availability, and costs.

    Parameters
    ----------
    p : xr.Dataset
        POMMES parameter dataset from build_input_parameters().

    Returns
    -------
    xr.Dataset
        Modified dataset with sanitized conversion variables.

    Raises
    ------
    ValueError
        If required conversion variables are missing.
    """
    required = [
        "conversion_power_capacity_min",
        "conversion_power_capacity_max",
        "conversion_power_capacity_investment_min",
        "conversion_power_capacity_investment_max",
    ]
    for v in required:
        if v not in p:
            raise ValueError(f"{v} missing from POMMES dataset")

    cap_min = p["conversion_power_capacity_min"]
    cap_max = p["conversion_power_capacity_max"]
    inv_min = p["conversion_power_capacity_investment_min"]
    inv_max = p["conversion_power_capacity_investment_max"]

    absent = np.isnan(cap_min) & np.isnan(cap_max)
    present = ~absent
    fixed_present = present & (cap_min.fillna(0.0) == cap_max.fillna(0.0))

    new_cap_min = cap_min.fillna(0.0)
    new_cap_max = cap_max.fillna(0.0)

    # Absent technology: investment bounds = 0/0
    new_inv_min = xr.where(absent, 0.0, inv_min)
    new_inv_max = xr.where(absent, 0.0, inv_max)

    # Present technology with missing investment bounds:
    # use operational bounds
    new_inv_min = new_inv_min.fillna(new_cap_min)
    new_inv_max = new_inv_max.fillna(new_cap_max)

    # Fixed technology: investment bounds must match operational bounds exactly
    new_inv_min = xr.where(fixed_present, new_cap_min, new_inv_min)
    new_inv_max = xr.where(fixed_present, new_cap_max, new_inv_max)

    p["conversion_power_capacity_min"] = new_cap_min
    p["conversion_power_capacity_max"] = new_cap_max
    p["conversion_power_capacity_investment_min"] = new_inv_min
    p["conversion_power_capacity_investment_max"] = new_inv_max

    if "conversion_max_yearly_production" in p:
        p["conversion_max_yearly_production"] = xr.where(
            absent,
            0.0,
            p["conversion_max_yearly_production"],
        )

    if "conversion_must_run" in p:
        p["conversion_must_run"] = xr.where(
            absent,
            0.0,
            p["conversion_must_run"].fillna(0.0),
        )

    # Set ramping for all technologies
    template_op = p["conversion_power_capacity_max"]
    p["conversion_ramp_up"] = xr.full_like(template_op, 1.0, dtype=float)
    p["conversion_ramp_down"] = xr.full_like(template_op, 1.0, dtype=float)
    p["conversion_ramp_relative_to_capacity"] = xr.full_like(
        template_op, True, dtype=bool
    )

    if "conversion_availability" in p:
        avail = p["conversion_availability"]
        absent_hourly = absent.broadcast_like(avail)
        p["conversion_availability"] = xr.where(
            absent_hourly,
            0.0,
            avail.fillna(0.0),
        )

    for v in [
        "conversion_fixed_cost",
        "conversion_variable_cost",
        "conversion_invest_cost",
        "conversion_annuity_cost",
    ]:
        if v in p:
            p[v] = p[v].fillna(0.0)

    if "conversion_finance_rate" in p:
        p["conversion_finance_rate"] = p["conversion_finance_rate"].fillna(0.0)

    if "conversion_life_span" in p:
        ls = p["conversion_life_span"].fillna(1.0)
        ls = xr.where(ls == 0, 1.0, ls)
        p["conversion_life_span"] = ls

    if "conversion_early_decommissioning" in p:
        p["conversion_early_decommissioning"] = p["conversion_early_decommissioning"].fillna(False)

    return p


def sanitize_storage_inputs(p: xr.Dataset) -> xr.Dataset:
    """
    Sanitize storage technology inputs (UNIFIED version).

    Combines best practices from both reference implementations:
    - Fills early_decommissioning and annuity_perfect_foresight with False
    - Sets main_resource: Reservoir_Hydro_Store → "reservoir_water",
                         others → "electricity"
    - Explicitly sets Battery E/P ratios: Battery_1h → 1.0, Battery_4h → 4.0,
                                          others → NaN
    - **Handles absent storage technologies**: detects techs where both
      energy and power investment bounds are NaN and zeros them out, preventing
      POMMES from generating invalid constraints from NaN bounds.

    Parameters
    ----------
    p : xr.Dataset
        POMMES parameter dataset from build_input_parameters().

    Returns
    -------
    xr.Dataset
        Modified dataset with sanitized storage variables.
    """
    stor_dim = "storage_tech" if "storage_tech" in p.dims else "storage_technology"

    # ── Handle absent storage technologies (NaN investment bounds) ──
    # POMMES storage techs do NOT have operational capacity min/max variables
    # (unlike conversion techs). They only have investment bounds.
    # Detect absent techs: both investment min AND max are NaN for power.
    inv_pmin_var = "storage_power_capacity_investment_min"
    inv_pmax_var = "storage_power_capacity_investment_max"
    inv_emin_var = "storage_energy_capacity_investment_min"
    inv_emax_var = "storage_energy_capacity_investment_max"

    if inv_pmin_var in p and inv_pmax_var in p:
        inv_pmin = p[inv_pmin_var]
        inv_pmax = p[inv_pmax_var]
        absent_power = np.isnan(inv_pmin) & np.isnan(inv_pmax)

        p[inv_pmin_var] = xr.where(absent_power, 0.0, inv_pmin).fillna(0.0)
        # POMMES: NaN in _max = unconstrained. Only fill absent techs (via xr.where),
        # do NOT fillna(0.0) on present techs — that would cap them at zero.
        p[inv_pmax_var] = xr.where(absent_power, 0.0, inv_pmax)

        # Log which (tech, area) combos were zeroed
        if stor_dim in absent_power.dims:
            other_dims = [d for d in absent_power.dims if d != stor_dim]
            if other_dims:
                any_absent = absent_power.any(dim=other_dims)
            else:
                any_absent = absent_power
            absent_techs = list(p[stor_dim].values[any_absent.values])
            if absent_techs:
                logger.info(
                    "sanitize_storage: zeroed NaN power investment bounds for: %s",
                    absent_techs,
                )

    if inv_emin_var in p and inv_emax_var in p:
        inv_emin = p[inv_emin_var]
        inv_emax = p[inv_emax_var]
        absent_energy = np.isnan(inv_emin) & np.isnan(inv_emax)

        p[inv_emin_var] = xr.where(absent_energy, 0.0, inv_emin).fillna(0.0)
        # POMMES: NaN in _max = unconstrained. Only fill absent techs.
        p[inv_emax_var] = xr.where(absent_energy, 0.0, inv_emax)

        if stor_dim in absent_energy.dims:
            other_dims = [d for d in absent_energy.dims if d != stor_dim]
            if other_dims:
                any_absent_e = absent_energy.any(dim=other_dims)
            else:
                any_absent_e = absent_energy
            absent_techs_e = list(p[stor_dim].values[any_absent_e.values])
            if absent_techs_e:
                logger.info(
                    "sanitize_storage: zeroed NaN energy investment bounds for: %s",
                    absent_techs_e,
                )

    # Also fill NaN in storage_factor_in/keep/out so POMMES does not
    # build constraints referencing NaN factors for absent techs.
    for fvar in ["storage_factor_in", "storage_factor_keep", "storage_factor_out"]:
        if fvar in p:
            p[fvar] = p[fvar].fillna(0.0)

    # ── Fill NaN for other storage scalars ──
    # POMMES uses split energy/power cost variables for storage
    for v in [
        "storage_fixed_cost",
        "storage_fixed_cost_energy",
        "storage_fixed_cost_power",
        "storage_variable_cost",
        "storage_invest_cost",
        "storage_invest_cost_energy",
        "storage_invest_cost_power",
        "storage_annuity_cost",
        "storage_annuity_cost_energy",
        "storage_annuity_cost_power",
        "storage_dissipation",
    ]:
        if v in p:
            p[v] = p[v].fillna(0.0)

    if "storage_finance_rate" in p:
        p["storage_finance_rate"] = p["storage_finance_rate"].fillna(0.0)

    if "storage_life_span" in p:
        ls = p["storage_life_span"].fillna(1.0)
        # Also replace 0 with 1.0 to avoid division-by-zero in POMMES
        # annuity calculations for absent storage techs.
        ls = xr.where(ls == 0, 1.0, ls)
        p["storage_life_span"] = ls

    if "storage_early_decommissioning" in p:
        p["storage_early_decommissioning"] = (
            p["storage_early_decommissioning"].fillna(False).astype(bool)
        )

    if "storage_annuity_perfect_foresight" in p:
        p["storage_annuity_perfect_foresight"] = (
            p["storage_annuity_perfect_foresight"].fillna(False).astype(bool)
        )

    # ── Fix storage_end_of_life and annuity_perfect_foresight for absent techs ──
    # Absent techs (0/0 investment bounds) with end_of_life = year_inv and
    # early_decommissioning=True + annuity_perfect_foresight=True can create
    # contradictory decommissioning constraints. Set annuity_perfect_foresight
    # to False for absent techs to prevent this.
    if inv_pmin_var in p and inv_pmax_var in p:
        inv_pmin_check = p[inv_pmin_var]
        inv_pmax_check = p[inv_pmax_var]
        absent_mask = (inv_pmin_check == 0) & (inv_pmax_check == 0)

        if "storage_annuity_perfect_foresight" in p:
            apf = p["storage_annuity_perfect_foresight"]
            # Where technology is absent, force annuity_perfect_foresight=False
            new_apf = xr.where(
                absent_mask.broadcast_like(apf),
                False,
                apf,
            )
            if not new_apf.equals(apf):
                logger.info(
                    "sanitize_storage: set annuity_perfect_foresight=False for absent techs"
                )
            p["storage_annuity_perfect_foresight"] = new_apf.astype(bool)

        if "storage_early_decommissioning" in p:
            ed = p["storage_early_decommissioning"]
            new_ed = xr.where(
                absent_mask.broadcast_like(ed),
                False,
                ed,
            )
            if not new_ed.equals(ed):
                logger.info(
                    "sanitize_storage: set early_decommissioning=False for absent techs"
                )
            p["storage_early_decommissioning"] = new_ed.astype(bool)

    if "storage_main_resource" in p and stor_dim in p.coords:
        vals = []
        for tech in p.coords[stor_dim].values:
            tech = str(tech)
            if tech == "Reservoir_Hydro_Store":
                vals.append("reservoir_water")
            else:
                vals.append("electricity")
        p["storage_main_resource"] = xr.DataArray(
            vals,
            coords={stor_dim: p.coords[stor_dim]},
            dims=(stor_dim,),
        )

    if "storage_energy_to_power_ratio" in p and stor_dim in p.coords:
        # Build a per-tech array: batteries get a fixed E/P ratio,
        # everything else gets NaN = independent sizing.
        #
        # POMMES convention: the constraint
        #   planning_storage_energy_capacity == ratio × planning_storage_power_capacity
        # uses mask=np.isfinite(ratio), so NaN entries are skipped entirely.
        # DO NOT fill NaN with 0.0 — that would force energy_capacity = 0.
        vals = []
        for tech in p.coords[stor_dim].values:
            tech = str(tech)
            if tech == "Battery_1h":
                vals.append(1.0)
            elif tech == "Battery_4h":
                vals.append(4.0)
            else:
                vals.append(np.nan)  # independent E/P sizing

        p["storage_energy_to_power_ratio"] = xr.DataArray(
            vals,
            coords={stor_dim: p.coords[stor_dim]},
            dims=(stor_dim,),
        )
        ratio = p["storage_energy_to_power_ratio"]
        n_nan = int(np.isnan(ratio.values).sum())
        n_finite = int(np.isfinite(ratio.values).sum())
        logger.info(
            "storage_energy_to_power_ratio: %d finite (constrained E/P), "
            "%d NaN (independent sizing). Values: %s",
            n_finite, n_nan, ratio.values.tolist(),
        )

    # ── Fill NaN in storage availability/inflow profiles ──
    if "storage_availability" in p:
        p["storage_availability"] = p["storage_availability"].fillna(0.0)

    return p


def sanitize_transport_inputs(p: xr.Dataset) -> xr.Dataset:
    """
    Sanitize transport (inter-area link) inputs.

    Fills NaN values in transport cost and annuity variables that would
    otherwise cause POMMES to inject NaN coefficients into the LP, making
    the model infeasible or numerically undefined.

    Parameters
    ----------
    p : xr.Dataset
        POMMES parameter dataset from build_input_parameters().

    Returns
    -------
    xr.Dataset
        Modified dataset with sanitized transport variables.
    """
    for v in [
        "transport_annuity_cost",
        "transport_fixed_cost",
        "transport_variable_cost",
        "transport_invest_cost",
        "transport_hurdle_costs",       # h2_pipeline hurdle cost (compression energy + losses)
        "transport_hurdle_cost",        # alternate naming convention
    ]:
        if v in p:
            nan_count = int(np.isnan(p[v].values).sum())
            if nan_count > 0:
                logger.info(
                    "sanitize_transport: filling %d NaN values in %s with 0.0",
                    nan_count, v,
                )
            p[v] = p[v].fillna(0.0)

    if "transport_finance_rate" in p:
        p["transport_finance_rate"] = p["transport_finance_rate"].fillna(0.0)

    if "transport_life_span" in p:
        ls = p["transport_life_span"]
        # Replace NaN with 1.0 (safe default), and also replace 0 with 1.0
        # to avoid division-by-zero in POMMES annuity calculations.
        ls = ls.fillna(1.0)
        ls = xr.where(ls == 0, 1.0, ls)
        p["transport_life_span"] = ls

    if "transport_early_decommissioning" in p:
        p["transport_early_decommissioning"] = (
            p["transport_early_decommissioning"].fillna(False).astype(bool)
        )

    if "transport_annuity_perfect_foresight" in p:
        tap = p["transport_annuity_perfect_foresight"]
        # If it's a scalar (0-dimensional), POMMES may fail when it tries to
        # index over (transport_tech, link, year_inv).  Broadcast to proper dims.
        if tap.ndim == 0:
            target_dims = []
            target_coords = {}
            for d in ["transport_tech", "link", "year_inv"]:
                if d in p.dims:
                    target_dims.append(d)
                    target_coords[d] = p.coords[d]
            if target_dims:
                val = bool(tap.values)
                shape = tuple(len(target_coords[d]) for d in target_dims)
                tap = xr.DataArray(
                    np.full(shape, val, dtype=bool),
                    coords=target_coords,
                    dims=target_dims,
                )
                logger.info(
                    "sanitize_transport: expanded scalar transport_annuity_perfect_foresight "
                    "to dims %s",
                    target_dims,
                )
        else:
            tap = tap.fillna(False).astype(bool)
        p["transport_annuity_perfect_foresight"] = tap

    # Fill NaN in transport capacity bounds (cover both naming conventions).
    # IMPORTANT: upper-bound variables (_max) must keep NaN — in POMMES,
    # NaN means "unconstrained" (np.isfinite mask skips the constraint).
    # Filling with 0.0 would cap capacity at zero and kill the link.
    _transport_fill_zero = [
        "transport_capacity_min",
        "transport_capacity_investment_min",
        "transport_power_capacity_min",
        "transport_power_capacity_investment_min",
    ]
    _transport_leave_nan = [
        "transport_capacity_max",
        "transport_capacity_investment_max",
        "transport_power_capacity_max",
        "transport_power_capacity_investment_max",
    ]
    for v in _transport_fill_zero:
        if v in p:
            nan_count = int(np.isnan(p[v].values).sum())
            if nan_count > 0:
                logger.info(
                    "sanitize_transport: filling %d NaN values in %s with 0.0",
                    nan_count, v,
                )
            p[v] = p[v].fillna(0.0)
    for v in _transport_leave_nan:
        if v in p:
            nan_count = int(np.isnan(p[v].values).sum())
            if nan_count > 0:
                logger.info(
                    "sanitize_transport: keeping %d NaN values in %s "
                    "(NaN = unconstrained in POMMES)",
                    nan_count, v,
                )

    return p


# ═════════════════════════════════════════════════════════════════════
# MODEL EXECUTION
# ═════════════════════════════════════════════════════════════════════


def _export_gurobi_iis(linopy_model, year_op: int, diagnostics_dir: Path) -> None:
    """
    Export Gurobi IIS (Irreducibly Infeasible Subset) for debugging.

    Attempts to compute and write .ilp and .lp files if the backend
    Gurobi model is available.  Also runs feasRelax to identify violated
    constraints and their magnitudes.

    Parameters
    ----------
    linopy_model
        Solved (or infeasible) linopy model.
    year_op : int
        Operating year (for filename).
    diagnostics_dir : Path
        Directory to write diagnostics.
    """
    try:
        solver_model = getattr(linopy_model, "solver_model", None)
        if solver_model is None:
            logger.warning("No backend Gurobi model available for IIS export")
            return

        diagnostics_dir.mkdir(parents=True, exist_ok=True)

        lp_path = diagnostics_dir / f"iis_model_{year_op}.lp"
        solver_model.write(str(lp_path))
        logger.info("Model LP exported to %s", lp_path)

        # ── feasRelax: find minimum-cost relaxation of infeasible constraints ──
        # This is MUCH faster than computeIIS on large models and tells us
        # exactly which constraints are violated and by how much.
        try:
            logger.info("Running feasRelax to identify violated constraints ...")
            # Gurobi feasRelax signature (7 args + self = 8 total):
            #   feasRelax(relaxobjtype, minrelax, vars, lbpen, ubpen,
            #             constrs, rhspen)
            # relaxobjtype=0: minimise sum of violations
            # minrelax=True: find the minimum relaxation
            # vars=None: don't relax variable bounds
            # lbpen/ubpen=None: no variable bound penalties
            # constrs: list of constraints to relax
            # rhspen: penalty per constraint (equal weight)
            constrs = solver_model.getConstrs()
            rhspen = [1.0] * len(constrs)
            feasobj = solver_model.feasRelax(
                0,        # relaxobjtype: minimise sum of violations
                True,     # minrelax
                None,     # vars: don't relax variable bounds
                None,     # lbpen: no variable bound penalties
                None,     # ubpen: no variable bound penalties
                constrs,  # constrs: constraints to relax
                rhspen,   # rhspen: relax all constraints equally
            )
            solver_model.optimize()

            # Collect violated constraints
            violations = []
            for c in solver_model.getConstrs():
                slack = c.Slack
                rhs = c.RHS
                sense = c.Sense
                # After feasRelax, ArtP (positive) and ArtN (negative) variables
                # indicate the violation. Check if slack is unusual.
                name = c.ConstrName
                # Actually, the easiest way is to look at the artificial variables
            # Check artificial variables added by feasRelax
            art_vars = [v for v in solver_model.getVars()
                        if v.VarName.startswith("ArtP_") or v.VarName.startswith("ArtN_")]
            violated = [(v.VarName, v.X) for v in art_vars if abs(v.X) > 1e-6]
            violated.sort(key=lambda x: abs(x[1]), reverse=True)

            feas_path = diagnostics_dir / f"feasrelax_{year_op}.txt"
            with open(feas_path, "w") as f:
                f.write(f"feasRelax objective (total violation): {feasobj}\n")
                f.write(f"Number of violated constraints: {len(violated)}\n\n")
                f.write("Top violated constraints (sorted by magnitude):\n")
                for vname, vval in violated[:500]:
                    # Try to find the original constraint name
                    orig_name = vname.replace("ArtP_", "").replace("ArtN_", "")
                    f.write(f"  {vname}: violation = {vval:.6f}  (constraint: {orig_name})\n")

            logger.info(
                "feasRelax done: total violation=%.6f, %d violated constraints. "
                "Details in %s",
                feasobj, len(violated), feas_path,
            )

            # Also log the top 20 to console
            for vname, vval in violated[:20]:
                logger.info("  VIOLATED: %s = %.6f", vname, vval)

        except Exception as feas_err:
            logger.warning("feasRelax failed: %s", feas_err)

        # ── IIS computation (may be slow on large models) ──
        try:
            logger.info("Computing IIS (may take a while on large models) ...")
            solver_model.computeIIS()
            ilp_path = diagnostics_dir / f"iis_{year_op}.ilp"
            solver_model.write(str(ilp_path))
            logger.info("IIS exported to %s", ilp_path)
        except Exception as iis_err:
            logger.warning("IIS computation failed: %s", iis_err)

    except Exception as e:
        logger.warning("Could not export diagnostics: %s", e)


def enforce_capex_in_objective(p: xr.Dataset) -> xr.Dataset:
    """
    Force `annuity_perfect_foresight=True` for all ACTIVE conversion / storage
    / transport entries — ensures annualised CAPEX enters the LP objective.

    Why this is needed
    ------------------
    pommes_craft defaults `annuity_perfect_foresight=False` for every
    ConversionTechnology / StorageTechnology / TransportTechnology unless the
    caller passes it explicitly. With apf=False, POMMES' planning-cost
    constraint uses ``annuity_cost.min("year_dec")``. The sanitize_*
    functions above call ``fillna(0.0)`` on ``*_annuity_cost``, which
    converts the NaN-for-beyond-lifetime entries (year_dec > year_inv +
    life_span) into 0. ``.min`` then picks the 0, multiplying through the
    investment decision and yielding 0 CAPEX in the objective for every
    apf=False entry. The LP ends up minimising OPEX only.

    With apf=True POMMES uses the per-vintage sum branch, which correctly
    multiplies planning_capacity by the matched-vintage annuity for each
    (year_inv, year_dec) pair.

    Absent techs (cap_max=0 / inv_max=0) are left at apf=False (as the
    existing sanitize logic sets them) so the LP does not attempt to pay
    annuity on zero capacity.

    Transport caveat (nan_active=True): H₂ pipelines are built with
    power_capacity_investment_max=NaN (unconstrained, NOT absent). NaN fails
    the (>0) active test, so before this fix every pipeline kept apf=False and
    its CAPEX (500 k€/MW) was silently dropped from the objective — the LP saw
    pipeline capacity as free and over-built it. For transport we therefore
    treat NaN invest_max as active; only invest_max==0 (forbidden link) stays
    inactive. (The 5 €/MWh hurdle OPEX is added to the objective directly in
    pommes/model/transport.py:158, independent of apf.)
    """
    def _flip(apf_name: str, active_var: str, nan_active: bool = False) -> None:
        if apf_name not in p or active_var not in p:
            return
        apf = p[apf_name]
        av = p[active_var]
        active = (av > 0)
        if nan_active:
            # Transport pipelines are built with power_capacity_investment_max=NaN,
            # which POMMES reads as "unconstrained / investable" (np.isfinite mask
            # skips the cap), NOT "absent". The plain (av > 0) test is False for NaN,
            # so without this the apf flip skips every pipeline, leaving apf=False and
            # silently dropping their CAPEX (POMMES then uses annuity_cost.min(year_dec),
            # which sanitize_transport fillna(0)'d to 0 — see transport.py:289). Treat
            # NaN invest_max as active so unconstrained links pay annualised CAPEX;
            # only invest_max==0 (a forbidden link) stays inactive.
            active = active | av.isnull()
        # active has dims (area, tech) typically; broadcast to apf dims
        active_b = active.broadcast_like(apf)
        new_apf = xr.where(active_b, True, apf)
        n_changed = int(((new_apf != apf).fillna(False)).sum())
        if n_changed > 0:
            logger.info("enforce_capex_in_objective: %s set True for %d entries",
                        apf_name, n_changed)
        p[apf_name] = new_apf.astype(bool)

    _flip("conversion_annuity_perfect_foresight", "conversion_power_capacity_max")
    _flip("storage_annuity_perfect_foresight",    "storage_power_capacity_investment_max")
    # Default ON: charge CAPEX that the base enforce-loop misses —
    #   (a) transport pipelines, built with invest_max=NaN (unconstrained), which
    #       the plain (>0) active test skips; and
    #   (b) combined techs (ATR_CCS / SMR_CCS blue-H2 reformers + fuel-flex Gas
    #       CCGT) — these had NO _flip at all, so their planning CAPEX was dropped
    #       entirely (apf stayed False -> annuity_cost.min()=0 after sanitize).
    # Both are apf-gated in pommes (transport.py:289 / combined.py:336).
    # Set CLEVER_CAPEX_FIX=0 to reproduce the pre-fix behaviour for A/B
    # sensitivity — see analysis/_ab_capexfix.py / _cost_wiring_audit.py.
    _capex_fix = os.environ.get("CLEVER_CAPEX_FIX", "1") != "0"
    _flip("transport_annuity_perfect_foresight",  "transport_power_capacity_investment_max",
          nan_active=_capex_fix)
    if _capex_fix:
        _flip("combined_annuity_perfect_foresight", "combined_power_capacity_investment_max",
              nan_active=True)
    return p


def enforce_uniform_wacc_on_h2_techs(p: xr.Dataset, wacc: float = 0.04) -> xr.Dataset:
    """
    Override finance_rate to ``wacc`` for the H₂-system techs that
    supplyforge instantiated without passing ``finance_rate=``: electrolysis,
    hydrogen_power_plant, and h2_storage. pommes_craft defaults
    ``finance_rate=0``, leaving these three under-priced relative to the
    CLEVER-side techs which correctly use 4%.

    For each (area, year_inv) slot where finance_rate is currently 0, the
    annuity_cost is rescaled to the proper CRF-based annuity. Slots that
    already have finance_rate=4% (typically because CLEVER's model.py
    overrode them) are left untouched.

    Scaling factor per (year_inv, year_dec) pair:
        new_annuity / old_annuity = crf(wacc, life) / crf(0, life)
                                  = crf(wacc, life) × life
    """
    def _crf(r: float, n: int) -> float:
        if n <= 0:
            return 0.0
        if r == 0:
            return 1.0 / n
        return r * (1 + r) ** n / ((1 + r) ** n - 1)

    if "year_inv" not in p.coords or "year_dec" not in p.coords:
        logger.warning("enforce_uniform_wacc: year_inv/year_dec missing, skip")
        return p

    yi = p["year_inv"].values
    yd = p["year_dec"].values
    scale_arr = np.zeros((len(yi), len(yd)))
    for i, y_inv in enumerate(yi):
        for j, y_dec in enumerate(yd):
            life = int(y_dec - y_inv)
            if life > 0:
                scale_arr[i, j] = _crf(wacc, life) * life
    scale_da = xr.DataArray(
        scale_arr, dims=("year_inv", "year_dec"),
        coords={"year_inv": p["year_inv"], "year_dec": p["year_dec"]},
    )

    # ── Conversion: electrolysis + hydrogen_power_plant (and case-variant alias) ──
    conv_techs_to_fix = ("electrolysis", "hydrogen_power_plant", "Hydrogen_power_plant")
    if "conversion_tech" in p.coords:
        present = [t for t in conv_techs_to_fix
                   if t in [str(x) for x in p["conversion_tech"].values]]
        for tech in present:
            sel = dict(conversion_tech=tech)
            if "conversion_finance_rate" not in p or "conversion_annuity_cost" not in p:
                continue
            cur_fr = p["conversion_finance_rate"].sel(**sel)
            is_zero = (cur_fr == 0)
            n_zero = int(is_zero.sum())
            if n_zero == 0:
                continue  # nothing to fix
            ann = p["conversion_annuity_cost"].sel(**sel)
            ann_scaled = ann * scale_da
            zero_b = is_zero.broadcast_like(ann)
            new_ann = xr.where(zero_b, ann_scaled, ann)
            p["conversion_annuity_cost"].loc[sel] = new_ann
            new_fr = xr.where(is_zero, wacc, cur_fr)
            p["conversion_finance_rate"].loc[sel] = new_fr
            logger.info(
                "enforce_uniform_wacc: conv %s — set finance_rate=%.2f, rescaled "
                "annuity_cost in %d (area, year_inv) slots",
                tech, wacc, n_zero,
            )

    # ── Storage: h2_storage ──
    if "storage_tech" in p.coords and "h2_storage" in [str(t) for t in p["storage_tech"].values]:
        sel = dict(storage_tech="h2_storage")
        if "storage_finance_rate" in p:
            cur_fr = p["storage_finance_rate"].sel(**sel)
            is_zero = (cur_fr == 0)
            n_zero = int(is_zero.sum())
            if n_zero > 0:
                for kind in ("energy", "power"):
                    var = f"storage_annuity_cost_{kind}"
                    if var not in p:
                        continue
                    ann = p[var].sel(**sel)
                    ann_scaled = ann * scale_da
                    zero_b = is_zero.broadcast_like(ann)
                    p[var].loc[sel] = xr.where(zero_b, ann_scaled, ann)
                new_fr = xr.where(is_zero, wacc, cur_fr)
                p["storage_finance_rate"].loc[sel] = new_fr
                logger.info(
                    "enforce_uniform_wacc: stor h2_storage — set finance_rate=%.2f, "
                    "rescaled annuity_cost in %d (area, year_inv) slots",
                    wacc, n_zero,
                )

    # ── Combined: ATR_CCS / SMR_CCS / Gas ──
    # CLEVER builds these at 4% so this is normally a no-op; the is_zero gate
    # makes it defensive (enforces uniform wacc on any 0% slot) without
    # double-applying to the slots already at 4%.
    if "combined_finance_rate" in p and "combined_annuity_cost" in p:
        cur_fr = p["combined_finance_rate"]
        is_zero = (cur_fr == 0)
        n_zero = int(is_zero.sum())
        if n_zero > 0:
            ann = p["combined_annuity_cost"]
            zero_b = is_zero.broadcast_like(ann)
            p["combined_annuity_cost"] = xr.where(zero_b, ann * scale_da, ann)
            p["combined_finance_rate"] = xr.where(is_zero, wacc, cur_fr)
            logger.info(
                "enforce_uniform_wacc: combined — set finance_rate=%.2f, "
                "rescaled annuity_cost in %d slots", wacc, n_zero,
            )

    # ── Transport: h2_pipeline ──
    # supplyforge instantiates pipelines with finance_rate=0 (annuity = invest/
    # life, NO cost of capital), leaving them ~2× under-priced vs every other
    # tech. Rescale the 0% slots to `wacc`. electric_line has invest_cost=0
    # (sunk) so its annuity is 0 and rescaling is a harmless no-op;
    # electric_line_expansion already carries 4% so the is_zero gate skips it.
    if "transport_tech" in p.coords and "h2_pipeline" in [str(t) for t in p["transport_tech"].values] \
       and "transport_finance_rate" in p and "transport_annuity_cost" in p:
        sel = dict(transport_tech="h2_pipeline")
        cur_fr = p["transport_finance_rate"].sel(**sel)
        is_zero = (cur_fr == 0)
        n_zero = int(is_zero.sum())
        if n_zero > 0:
            ann = p["transport_annuity_cost"].sel(**sel)
            zero_b = is_zero.broadcast_like(ann)
            p["transport_annuity_cost"].loc[sel] = xr.where(zero_b, ann * scale_da, ann)
            p["transport_finance_rate"].loc[sel] = xr.where(is_zero, wacc, cur_fr)
            logger.info(
                "enforce_uniform_wacc: transport h2_pipeline — set finance_rate=%.2f, "
                "rescaled annuity_cost in %d slots", wacc, n_zero,
            )

    return p


def apply_distance_based_pipeline_capex(
    p: xr.Dataset, eur_per_mw_per_km: float, detour: float = 1.0
) -> xr.Dataset:
    """Replace flat / hand-tuned H₂-pipeline invest_cost with a distance formula:
    ``invest_cost = eur_per_mw_per_km × great_circle_km(area_from, area_to) × detour``
    for EVERY transport tech whose name contains ``h2_pipeline`` (EU ``h2_pipeline``
    + MENA ``mena_h2_pipeline`` and its route-named variants).

    ``transport_annuity_cost`` is scaled by the SAME per-link ratio (new/old invest)
    so the finance_rate / WACC already set by enforce_uniform_wacc is preserved
    (annuity ∝ invest at fixed CRF). Must therefore run AFTER enforce_uniform_wacc.

    Links with an unknown endpoint (NaN / not in AREA_CENTROID_LONLAT) or zero
    invest_cost (phantom links, sunk electric lines) are left untouched.
    Gated by the ``_pipekmNNN`` suffix; only called when NNN is set.
    """
    if "transport_invest_cost" not in p or "transport_area_from" not in p:
        logger.warning("apply_distance_based_pipeline_capex: missing transport vars, skip")
        return p
    inv = p["transport_invest_cost"]
    af = p["transport_area_from"]
    at = p["transport_area_to"]
    ttd = [d for d in inv.dims if "transport_tech" in str(d)][0]
    ld = [d for d in af.dims if str(d) == "link" or str(d).endswith("link")][0]
    techs = [str(x) for x in inv[ttd].values]
    # Build new_invest over (transport_tech, link); NaN where not a priced pipeline link.
    ni = np.full((inv.sizes[ttd], af.sizes[ld]), np.nan, dtype="float64")
    links = [str(x) for x in af[ld].values]
    for ti, t in enumerate(techs):
        if "h2_pipeline" not in t:
            continue
        for li in range(len(links)):
            a = str(np.asarray(af.isel({ttd: ti, ld: li}).values).reshape(-1)[0])
            b = str(np.asarray(at.isel({ttd: ti, ld: li}).values).reshape(-1)[0])
            route = frozenset({a, b})
            if route in MENA_ROUTE_KM:
                # realistic coastal/subsea landfall route, not the Saharan-centroid
                # great-circle (detour not applied — these are actual route lengths).
                ni[ti, li] = eur_per_mw_per_km * MENA_ROUTE_KM[route]
            elif a in AREA_CENTROID_LONLAT and b in AREA_CENTROID_LONLAT:
                d_km = _haversine_km(AREA_CENTROID_LONLAT[a], AREA_CENTROID_LONLAT[b]) * detour
                ni[ti, li] = eur_per_mw_per_km * d_km
    new_invest = xr.DataArray(ni, dims=[ttd, ld], coords={ttd: inv[ttd], ld: af[ld]})
    inv0 = inv.isel(year_inv=0) if "year_inv" in inv.dims else inv  # (tech, link)
    cond = np.isfinite(new_invest) & (inv0 > 0)                     # (tech, link)
    scale = xr.where(cond, new_invest / xr.where(inv0 > 0, inv0, 1.0), 1.0)
    p["transport_invest_cost"] = xr.where(cond, new_invest, inv)
    if "transport_annuity_cost" in p:
        p["transport_annuity_cost"] = p["transport_annuity_cost"] * scale
    n = int(cond.values.sum())
    logger.info(
        "apply_distance_based_pipeline_capex: priced %d H₂-pipeline links @ %.0f €/MW/km "
        "(detour=%.2f); annuity scaled to match",
        n, eur_per_mw_per_km, detour,
    )
    return p


def run_model_without_ramping(
    model,
    solver_name: str,
    solver_options: dict,
    year_op: int,
    write_lp: bool = False,
    diagnostics_dir: Optional[Path] = None,
    r0_overrides_kwargs: Optional[dict] = None,
):
    """
    Build, sanitize, and solve the POMMES model.

    Converts EnergyModel to POMMES, sanitizes inputs, builds and solves,
    then sets results back on the model. Writes diagnostics if requested.

    Parameters
    ----------
    model
        EnergyModel instance (must have .to_pommes_model() and .set_all_results()).
    solver_name : str
        "gurobi" or "highs".
    solver_options : dict
        Solver-specific options (from build_solver_options()).
    year_op : int
        Operating year (for logging/diagnostics).
    write_lp : bool, default False
        If True, write LP file and input dataset netcdf to diagnostics.
    diagnostics_dir : Path, optional
        Directory for diagnostics. If None, diagnostics are not written.
    r0_overrides_kwargs : dict, optional
        If provided, the kwargs are forwarded to
        ``clever.r0_overrides.apply_r0_overrides`` after ``check_inputs`` and
        the case-variant merge, but before ``sanitize_*``. Supported kwargs
        documented in ``clever.r0_overrides`` — covers:

          * ``electrolyser_invest_cost_eur_per_kw`` (sub-task 2)
          * ``electrolyser_min_bounds_gw`` (sub-task 1)
          * ``storage_capex_by_area`` (sub-task 3)
          * ``storage_caps_by_area`` (sub-task 3)

        Pass ``None`` (default) to skip R0 overrides entirely — useful for
        running the bundle's bare baseline, and for R1/R2/R3 variants that
        define their own override pipeline.

    Returns
    -------
    linopy_model
        The solved linopy model with results.

    Raises
    ------
    RuntimeError
        If model is infeasible or does not converge.
    """
    model.to_pommes_model()

    p = build_input_parameters(model.config, model.parameter_tables)
    p = check_inputs(p)

    # ── Unify case-variant conversion tech names ──────────────────────
    # MUST run BEFORE sanitize_absent_conversions(), because sanitize
    # fills NaN cap_max/inv_max with 0.0 for absent techs.  The alias
    # countries (where supplyforge added lowercase 'hydrogen_power_plant')
    # have NaN in the canonical 'Hydrogen_power_plant' row.  If sanitize
    # runs first, those NaNs become 0.0, the merge sees isfinite(0.0)=True
    # and keeps the zero instead of the alias's real capacity bounds.
    conv_dim_early = "conversion_tech" if "conversion_tech" in p.dims else "conversion_technology"
    if conv_dim_early in p.dims and "conversion_power_capacity_max" in p:
        tech_names = [str(t) for t in p[conv_dim_early].values]
        seen_lower: dict[str, list[str]] = {}
        for tn in tech_names:
            lower = tn.lower()
            seen_lower.setdefault(lower, []).append(tn)

        for lower_key, variants in seen_lower.items():
            if len(variants) <= 1:
                continue
            cap_max_early = p["conversion_power_capacity_max"]
            best = variants[0]
            best_cap = float(np.nanmax(cap_max_early.sel({conv_dim_early: best}).values))
            if np.isnan(best_cap):
                best_cap = 0.0
            for v in variants[1:]:
                v_cap = float(np.nanmax(cap_max_early.sel({conv_dim_early: v}).values))
                if np.isnan(v_cap):
                    v_cap = 0.0
                if v[0].isupper() and not best[0].isupper():
                    best = v
                    best_cap = v_cap
                elif not v[0].isupper() and best[0].isupper():
                    pass
                elif v_cap > best_cap:
                    best = v
                    best_cap = v_cap
            aliases = [v for v in variants if v != best]
            logger.info(
                "MERGE (pre-sanitize): unifying case-variants %s → canonical '%s'",
                aliases, best,
            )

            for alias in aliases:
                for var_name in list(p.data_vars):
                    if conv_dim_early not in p[var_name].dims:
                        continue
                    canonical_slice = p[var_name].sel({conv_dim_early: best})
                    alias_slice = p[var_name].sel({conv_dim_early: alias})
                    if np.issubdtype(canonical_slice.dtype, np.floating):
                        merged = canonical_slice.where(
                            np.isfinite(canonical_slice), alias_slice
                        )
                    elif np.issubdtype(canonical_slice.dtype, np.integer):
                        merged = canonical_slice
                    else:
                        merged = canonical_slice
                    p[var_name].loc[{conv_dim_early: best}] = merged

                keep_mask = p[conv_dim_early].values != alias
                p = p.sel({conv_dim_early: p[conv_dim_early].values[keep_mask]})
                logger.info("  Merged '%s' into '%s' and dropped alias.", alias, best)

            # Rename components in the POMMES model object
            alias_set = set(aliases)
            for comp in getattr(model, "components", []):
                if getattr(comp, "name", None) in alias_set:
                    old_name = comp.name
                    comp.name = best
                    logger.info("Renamed component '%s' → '%s'", old_name, best)

    # ── R0 per-country overrides ────────────────────────────────────────
    # Hook point: after check_inputs validation and the case-variant merge,
    # before sanitize_*. Mutates p with R0-specific recalibrations (sub-tasks
    # 1, 2, 3 in checklist_R0.md). Opt-in via r0_overrides_kwargs; default
    # behaviour is unchanged from before R0 (no override applied).
    if r0_overrides_kwargs is not None:
        from pommes_eur.providers.clever.dataset_calibration import apply_r0_overrides
        logger.info("Applying R0 per-country overrides…")
        p = apply_r0_overrides(p, **r0_overrides_kwargs)

    p = sanitize_absent_conversions(p)
    p = sanitize_storage_inputs(p)
    p = sanitize_transport_inputs(p)

    # ── CRITICAL: ensure annualised CAPEX is in the LP objective ──
    # pommes_craft defaults annuity_perfect_foresight=False, and POMMES'
    # apf=False branch uses annuity_cost.min("year_dec"). Our sanitize_*
    # functions fillna(0) on *_annuity_cost, which converts the
    # NaN-for-beyond-lifetime entries into 0. The .min() then picks 0,
    # silently dropping ALL CAPEX from the objective and leaving the LP to
    # minimise OPEX only. Diagnosed and quantified: missing ~280 G€/yr of
    # conversion CAPEX + ~76 G€/yr of storage CAPEX. See
    # notes/wacc_investigation.md for the full trace.
    #
    # Fix: set apf=True for all ACTIVE techs (cap_max > 0 or inv_max > 0),
    # leaving the apf=False that sanitize_* sets on absent techs untouched
    # (so POMMES doesn't try to pay annuity on zero capacity).
    p = enforce_capex_in_objective(p)

    # ── Fix supplyforge's finance_rate=0 default for electrolysis,
    # hydrogen_power_plant (supplyforge-added areas), and h2_storage.
    # supplyforge instantiates these techs without passing `finance_rate=`,
    # so pommes_craft defaults them to 0. CLEVER-side techs (Solar, Wind,
    # Nuclear, Biomethane CCGT, ATR) correctly use 0.04. This patch
    # restores 4% WACC and rescales annuity_cost by the proper CRF.
    p = enforce_uniform_wacc_on_h2_techs(p, wacc=0.04)

    # ── Distance-based H₂-pipeline CAPEX (opt-in via `_pipekmNNN`) ──
    # Turn the flat 500k€/MW (EU) and the hand-tuned MENA route dict into a single
    # €/MW/km × great-circle-distance formula for every H₂ pipeline. Runs AFTER
    # enforce_uniform_wacc so it can scale the (4% WACC) annuity proportionally.
    if _PIPELINE_EUR_PER_MW_PER_KM is not None:
        p = apply_distance_based_pipeline_capex(p, _PIPELINE_EUR_PER_MW_PER_KM)

    if "conversion_power_capacity_max" not in p:
        raise ValueError("conversion_power_capacity_max missing from POMMES dataset")

    # ── Fix bogus end_of_life values ────────────────────────────────
    # When a tech has no explicit lifetime, NaN is cast to int64 →
    # -9223372036854775808 (int64 min).  This makes the planning mask
    # (year_op < year_dec) * (year_dec <= end_of_life) always False,
    # so no investment window exists.  If investment_min > 0, the model
    # must build the tech but has no valid year → structural infeasibility.
    #
    # Fix: derive end_of_life from EOLES_LIFETIME per tech.  If the tech
    # name can be mapped to an EOLES key, use year_inv + real lifetime.
    # Otherwise fall back to year_inv + max(EOLES_LIFETIME) as safety net.
    _year_inv = int(p.year_inv.values.flat[0])
    _eol_fallback = _year_inv + max(EOLES_LIFETIME.values())  # 2050 + 80 = 2130

    for eol_name, tech_dim in [
        ("conversion_end_of_life", "conversion_tech"),
        ("storage_end_of_life", "storage_tech"),
    ]:
        if eol_name not in p:
            continue
        eol = p[eol_name]
        bad_mask = eol.values < 0  # int64 min = NaN-cast artifact
        n_bad = int(bad_mask.sum())
        if n_bad > 0:
            # Try to compute per-tech end_of_life from EOLES_LIFETIME
            if tech_dim in p.dims:
                tech_names = list(p[tech_dim].values)
                # Build a per-tech fallback array
                from pommes_eur.constants import MODELTECH_TO_EOLES
                per_tech_eol = {}
                for tname in tech_names:
                    tstr = str(tname)
                    # Try direct match, then via MODELTECH_TO_EOLES
                    eoles_key = MODELTECH_TO_EOLES.get(tstr, tstr.lower())
                    lt = EOLES_LIFETIME.get(eoles_key, None)
                    per_tech_eol[tstr] = (_year_inv + lt) if lt else _eol_fallback

                logger.warning(
                    "%s: %d bogus entries (int64 min from NaN cast). "
                    "Replacing with per-tech end_of_life from EOLES_LIFETIME: %s",
                    eol_name, n_bad, per_tech_eol,
                )

                # Build replacement values — keep good values, replace bad ones
                new_eol = eol.copy()
                for tname, eol_val in per_tech_eol.items():
                    try:
                        sel = {tech_dim: tname}
                        tech_bad = new_eol.sel(**sel).values < 0
                        if tech_bad.any():
                            new_eol.loc[sel] = xr.where(
                                new_eol.sel(**sel) < 0,
                                eol_val,
                                new_eol.sel(**sel),
                            )
                    except Exception:
                        pass
                p[eol_name] = new_eol
            else:
                # No tech dimension — flat fallback
                p[eol_name] = xr.where(eol < 0, _eol_fallback, eol)
                logger.warning(
                    "%s: replaced %d bogus entries with fallback %d",
                    eol_name, n_bad, _eol_fallback,
                )

    # ── Clamp tiny availability values to zero ──────────────────────
    # Solar twilight, near-calm wind, etc. produce availability values
    # as small as ~3e-07.  These are physically meaningless (< 0.01%
    # of rated capacity) but create extreme matrix conditioning issues
    # (matrix range spans 11 orders of magnitude), causing the barrier
    # solver to cycle instead of converging.
    _AVAIL_FLOOR = 1e-4  # below 0.01% of rated capacity → treat as zero
    if "conversion_availability" in p:
        avail = p["conversion_availability"]
        tiny_mask = (avail > 0) & (avail < _AVAIL_FLOOR)
        n_clamped = int(tiny_mask.sum().values)
        if n_clamped > 0:
            p["conversion_availability"] = avail.where(~tiny_mask, 0.0)
            logger.info(
                "Clamped %d tiny conversion_availability values (< %.0e) to 0.0 "
                "(improves LP matrix conditioning)",
                n_clamped, _AVAIL_FLOOR,
            )

    # Allow electricity curtailment without artificially distorting price signal
    if "spillage_max_capacity" in p:
        p["spillage_max_capacity"].loc[dict(resource="electricity")] = 1e6
        # Reservoir water must also be spillable to handle real inflow peaks
        # (the inflow component is must-run; if storage is full, excess water
        #  must spill — otherwise the model becomes infeasible).
        if "reservoir_water" in p["spillage_max_capacity"].resource.values:
            p["spillage_max_capacity"].loc[dict(resource="reservoir_water")] = 1e6
        # H₂ spillage: allow curtailment at zero cost (over-production is
        # possible when electrolyser is large and electricity is cheap).
        if "hydrogen" in p["spillage_max_capacity"].resource.values:
            p["spillage_max_capacity"].loc[dict(resource="hydrogen")] = 1e6

    if "spillage_cost" in p:
        p["spillage_cost"].loc[dict(resource="electricity")] = 0.0
        if "reservoir_water" in p["spillage_cost"].resource.values:
            p["spillage_cost"].loc[dict(resource="reservoir_water")] = 0.0
        if "hydrogen" in p["spillage_cost"].resource.values:
            p["spillage_cost"].loc[dict(resource="hydrogen")] = 0.0

    # H₂ load shedding: allow unserved H₂ demand with an extreme penalty.
    # Without this, any hour where H₂ supply < demand is structurally
    # infeasible (presolve rejects in 0 iterations).
    # VOLL_H2 = 10,000 EUR/MWh ≈ 5× electricity VOLL.
    if "load_shedding_max_capacity" in p:
        if "hydrogen" in p["load_shedding_max_capacity"].resource.values:
            p["load_shedding_max_capacity"].loc[dict(resource="hydrogen")] = 1e6
        # reservoir_water: LS MUST stay blocked. SupplyForge correctly sets
        # `LoadShedding(resource="reservoir_water", max_capacity=0.0)` so the
        # LP cannot manufacture phantom water. The previous override here
        # (max=1e6, cost=0) allowed the LP to create unlimited free water,
        # which the Reservoir_Hydro_Plant then turned into ~69 TWh/yr of
        # ghost electricity in FR (Reservoir_Hydro_Plant ran at 86 TWh/yr
        # vs the real inflow of 17 TWh/yr). The right tool for shedding
        # *excess inflow* when the reservoir is full is SPILLAGE, not LS —
        # we keep spillage_max at 1e6 (set above) which is the legitimate
        # disposal channel. See FR HIGH v4 audit 2026-05-27 for the diagnosis.
        if "reservoir_water" in p["load_shedding_max_capacity"].resource.values:
            p["load_shedding_max_capacity"].loc[dict(resource="reservoir_water")] = 0.0
    if "load_shedding_cost" in p:
        # Hydrogen VoLL: default 10 000 €/MWh, overridable via `_h2vollNNN`
        # (e.g. _h2voll400 -> industrial-H2 effacement below the ~500 €/MWh
        # H2 marginal; _h2voll2000 -> H2 reliability backstop below 10 000).
        _h2_voll = _H2_VOLL_OVERRIDE if _H2_VOLL_OVERRIDE is not None else 10_000.
        if "hydrogen" in p["load_shedding_cost"].resource.values:
            p["load_shedding_cost"].loc[dict(resource="hydrogen")] = _h2_voll
            if _H2_VOLL_OVERRIDE is not None:
                logger.info(
                    "VoLL override: hydrogen load_shedding_cost = %.0f €/MWh (_h2vollNNN)",
                    _h2_voll,
                )
        # Electricity VoLL: default 30 000 €/MWh (DEFAULT_LOAD_SHEDDING_COST),
        # overridable via `_vollNNN` (e.g. _voll900 -> effacement remuneration
        # below the ~1300 €/MWh H2-CCGT peaker; _voll3000 -> scarcity cap above).
        if (
            _ELECTRICITY_VOLL_OVERRIDE is not None
            and "electricity" in p["load_shedding_cost"].resource.values
        ):
            p["load_shedding_cost"].loc[dict(resource="electricity")] = _ELECTRICITY_VOLL_OVERRIDE
            logger.info(
                "VoLL override: electricity load_shedding_cost = %.0f €/MWh (_vollNNN)",
                _ELECTRICITY_VOLL_OVERRIDE,
            )
        if "reservoir_water" in p["load_shedding_cost"].resource.values:
            p["load_shedding_cost"].loc[dict(resource="reservoir_water")] = 0.0

    # ── Intermediate-carrier load-shedding lockdown (2026-05-27) ──────
    # biomethane, raw_biomass, natural_gas, and oil are NOT final-demand
    # resources — they only exist to carry energy between an explicit
    # supplier (biomethane_plant CT, raw_biomass_supply CT, natural_gas/oil
    # NetImport) and consumers (BioCCGT, ATR, Gas_CCGT, Oil_OCGT). Without
    # explicit blocking, POMMES inherits the pommes_craft default LS cost of
    # 1000 €/MWh with UNLIMITED capacity, so the LP discovers a "free fuel"
    # path: e.g. BioCCGT can dispatch electricity by load-shedding the
    # biomethane it would have consumed (1000 €/MWh × 1.72 MWh-bio/MWh-e =
    # 1720 €/MWh, cheaper than electricity LS at 30 000). Setting max=0
    # makes the LS variable mask `np.logical_or(np.isnan(cap), cap > 0)`
    # evaluate to False → variable not created → real supply MUST balance
    # the bus. FR+DE smoke test (2026-05-27) confirmed the leak: BioCCGT
    # built 14 GW DE / 5 GW FR with biomethane_plant at zero capacity.
    if "load_shedding_max_capacity" in p:
        for res in ("biomethane", "raw_biomass", "natural_gas", "oil"):
            if res in p["load_shedding_max_capacity"].resource.values:
                p["load_shedding_max_capacity"].loc[dict(resource=res)] = 0.0
                logger.info(
                    "Intermediate-carrier LS lockdown: %s LS max set to 0 "
                    "(bus must balance via explicit supply)", res,
                )

    # ── Diagnostic dump of key parameters before build_model ──
    logger.info("=== PRE-BUILD PARAMETER DUMP ===")
    logger.info("Dataset variables: %s", list(p.data_vars))
    logger.info("Coordinates: %s", {str(k): list(v.values) for k, v in p.coords.items() if v.size < 50})

    if "spillage_max_capacity" in p:
        logger.info("spillage_max_capacity:\n%s", p["spillage_max_capacity"].to_dataframe())
    if "load_shedding_max_capacity" in p:
        logger.info("load_shedding_max_capacity:\n%s", p["load_shedding_max_capacity"].to_dataframe())

    conv_dim = "conversion_tech" if "conversion_tech" in p.dims else "conversion_technology"
    if conv_dim in p.dims:
        if "conversion_power_capacity_max" in p:
            logger.info("conversion_power_capacity_max:\n%s",
                        p["conversion_power_capacity_max"].to_dataframe().to_string())
        if "conversion_power_capacity_min" in p:
            logger.info("conversion_power_capacity_min:\n%s",
                        p["conversion_power_capacity_min"].to_dataframe().to_string())
        if "conversion_power_capacity_investment_max" in p:
            logger.info("conversion_power_capacity_investment_max:\n%s",
                        p["conversion_power_capacity_investment_max"].to_dataframe().to_string())
        if "conversion_power_capacity_investment_min" in p:
            logger.info("conversion_power_capacity_investment_min:\n%s",
                        p["conversion_power_capacity_investment_min"].to_dataframe().to_string())
        if "conversion_must_run" in p:
            logger.info("conversion_must_run:\n%s",
                        p["conversion_must_run"].to_dataframe().to_string())

    stor_dim = "storage_tech" if "storage_tech" in p.dims else "storage_technology"
    if stor_dim in p.dims:
        for svar in ["storage_power_capacity_investment_max", "storage_energy_capacity_investment_max",
                      "storage_power_capacity_investment_min", "storage_energy_capacity_investment_min"]:
            if svar in p:
                logger.info("%s:\n%s", svar, p[svar].to_dataframe().to_string())

    if "demand" in p:
        import numpy as _np
        for area in p["demand"].area.values:
            d = p["demand"].sel(area=area).values
            logger.info("demand @ %s: peak=%.1f, min=%.1f, mean=%.1f",
                        area, _np.nanmax(d), _np.nanmin(d), _np.nanmean(d))
    logger.info("=== END PRE-BUILD PARAMETER DUMP ===")

    # ── H₂ system coherence check ─────────────────────────────────────
    # If "hydrogen" is a declared resource, verify that the essential
    # supply-side components are present (electrolyser + H2PP + storage).
    # Missing components → the H₂ bus has demand but no supply, or vice
    # versa, which causes infeasibility or an idle H₂ module.
    if "resource" in p.dims:
        resources = [str(r) for r in p["resource"].values]
    elif "resource" in p.coords:
        resources = [str(r) for r in p.coords["resource"].values]
    else:
        resources = []
    if "hydrogen" in resources:
        # Conversion techs (electrolyser, H2PP)
        h2_conv_techs = set()
        if conv_dim in p.dims:
            h2_conv_techs = {str(t) for t in p[conv_dim].values}
        # Storage techs — live under storage_tech, NOT conversion_tech
        h2_stor_techs = set()
        for sdim in ("storage_tech", "storage_technology"):
            if sdim in p.dims:
                h2_stor_techs = {str(t) for t in p[sdim].values}
                break
        # Also check conversion_tech (pommes sometimes merges storage there)
        all_techs = h2_conv_techs | h2_stor_techs
        h2_supply = {"electrolysis", "Hydrogen_power_plant", "hydrogen_power_plant"}
        h2_storage = {"h2_storage", "H2_SaltCavern", "h2_saltcavern", "H2_Tank"}
        found_supply = h2_conv_techs & h2_supply
        found_storage = all_techs & h2_storage
        if not found_supply:
            logger.warning(
                "H₂ COHERENCE: 'hydrogen' resource declared but no electrolysis or "
                "H₂ power plant found in conversion techs. The H₂ bus may be idle. "
                "Ensure ADD_DEMANDFORGE_H2=True in the notebook configuration."
            )
        if not found_storage:
            logger.warning(
                "H₂ COHERENCE: 'hydrogen' resource declared but no H₂ storage found. "
                "Without inter-temporal buffering, the electrolyser must exactly match "
                "H₂ demand hour-by-hour, which is typically infeasible."
            )
        if found_supply:
            logger.info(
                "H₂ system check passed: supply=%s, storage=%s",
                sorted(found_supply), sorted(found_storage) if found_storage else "NONE",
            )

    # ── Final NaN sweep: fill any remaining NaN in capacity/cost vars ──
    # Some variables may have slipped through the per-subsystem sanitizers.
    # NaN in capacity bounds → linopy generates NaN coefficients → infeasible.
    #
    # IMPORTANT — POMMES semantics for NaN:
    #   In POMMES, NaN means "skip this constraint" (all constraint masks use
    #   np.isfinite()).  So we must NOT blindly replace NaN with 0.0 for
    #   upper-bound parameters like _capacity_max and _investment_max, because
    #   a finite 0.0 would ACTIVATE the constraint and lock the tech to zero.
    #
    #   Safe rules:
    #   • Lower bounds (_min): NaN → 0.0 (no minimum = relaxed)
    #   • Upper bounds (_max): NaN → leave as NaN (constraint stays inactive)
    #   • Costs: NaN → 0.0 (zero cost, no distortion)
    #   • Efficiencies/availability: NaN → 1.0 (perfect = relaxed)
    #   • must_run / ramp: NaN → safe neutral value
    _nan_fill_rules: dict[str, float | None] = {
        # Upper bounds: leave NaN = constraint stays off (POMMES isfinite mask)
        "_capacity_max": None,       # NaN = unconstrained capacity
        "_investment_max": None,     # NaN = unconstrained investment
        # Lower bounds: 0.0 = no minimum requirement
        "_capacity_min": 0.0,
        "_investment_min": 0.0,
        # Costs: 0.0 = free (safe neutral)
        "_cost": 0.0,
        # Efficiency/availability: 1.0 = perfect (safe neutral)
        "_efficiency": 1.0,
        "_availability": 1.0,
        # Must-run / ramp: safe neutral values
        "_must_run": 0.0,           # Not must-run by default
        "_hurdle_cost": 0.0,        # No hurdle cost
        "_energy_to_power_ratio": None,  # NaN = independent sizing (POMMES isfinite mask)
        "_ramp_up": 1.0,           # No ramp limit (100% per hour)
        "_ramp_down": 1.0,         # No ramp limit (100% per hour)
    }
    nan_fixes_applied = 0
    nan_skipped_upper = 0
    for vname in list(p.data_vars):
        for suffix, fill_val in _nan_fill_rules.items():
            if vname.endswith(suffix) or suffix.lstrip("_") in vname:
                nan_count = int(np.isnan(p[vname].values).sum())
                if nan_count > 0:
                    if fill_val is None:
                        # Upper bound: leave NaN so POMMES isfinite mask skips it
                        nan_skipped_upper += nan_count
                        logger.info(
                            "BLANKET NaN SKIP: %s has %d NaN — left as NaN "
                            "(upper bound, POMMES treats NaN as unconstrained)",
                            vname, nan_count,
                        )
                    else:
                        p[vname] = p[vname].fillna(fill_val)
                        nan_fixes_applied += nan_count
                        logger.warning(
                            "BLANKET NaN FIX: %s had %d NaN values, filled with %.1f",
                            vname, nan_count, fill_val,
                        )
                break  # only apply the first matching rule per variable
    if nan_fixes_applied > 0 or nan_skipped_upper > 0:
        logger.info(
            "Blanket NaN sweep: fixed %d NaN values, "
            "skipped %d NaN in upper-bound vars (left as unconstrained)",
            nan_fixes_applied, nan_skipped_upper,
        )
    else:
        logger.info("Blanket NaN sweep: dataset is clean (no NaN in capacity/cost vars)")

    # ── Build-only early exit (multi-year_op ensemble pre-build) ──────
    # When CLEVER_BUILD_ONLY=1, dump the final solve-ready parameter
    # dataset `p` (post check_inputs + R0 overrides + NaN sweep) and stop
    # BEFORE build_model/solve. Used to cheaply materialise one weather
    # year's input_dataset for later merging along year_op into an
    # ERAA-style ensemble (see scripts/merge_year_ops.py).
    if os.environ.get("CLEVER_BUILD_ONLY") == "1":
        if diagnostics_dir is not None:
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            dataset_path = diagnostics_dir / f"input_dataset_{year_op}.nc"
            p.to_netcdf(dataset_path)
            logger.info("CLEVER_BUILD_ONLY=1 — saved input dataset -> %s; "
                        "exiting before build_model/solve.", dataset_path)
        else:
            logger.warning("CLEVER_BUILD_ONLY=1 but diagnostics_dir is None — nothing dumped.")
        raise SystemExit(0)

    linopy_model = build_model(p)

    logger.info("Solving with %s solver", solver_name)
    logger.info("Solver options:")
    for k, v in solver_options.items():
        logger.info("  %s: %s", k, v)

    linopy_model.solve(
        solver_name=solver_name,
        **solver_options,
    )

    status = getattr(linopy_model, "status", None)
    termination = getattr(linopy_model, "termination_condition", None)
    solution = getattr(linopy_model, "solution", None)

    # Write diagnostics if requested
    if diagnostics_dir is not None:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)

        dataset_path = diagnostics_dir / f"input_dataset_{year_op}.nc"
        try:
            p.to_netcdf(dataset_path)
            logger.info("Saved input dataset -> %s", dataset_path)
        except Exception as err:
            logger.warning("Could not save input dataset: %s", err)

        if write_lp:
            lp_path = diagnostics_dir / f"linopy_problem_{year_op}.lp"
            try:
                if hasattr(linopy_model, "to_file"):
                    linopy_model.to_file(lp_path)
                    logger.info("Saved LP file -> %s", lp_path)
            except Exception as err:
                logger.warning("Could not save LP file: %s", err)

    # Check for optimal solution
    if solution is not None and str(termination).lower() == "optimal":
        # Use safe result extraction that skips zombie components
        # (techs that were dropped from the linopy dataset but still
        # exist in the POMMES model object — e.g. lowercase
        # 'hydrogen_power_plant' when 'Hydrogen_power_plant' won).
        try:
            model.set_all_results(linopy_model)
        except KeyError as e:
            logger.warning(
                "set_all_results() hit KeyError: %s — falling back to "
                "per-component extraction with zombie skipping.", e
            )
            # Manual per-component extraction (model.components is a flat list)
            for comp in getattr(model, "components", []):
                try:
                    comp.set_results(linopy_model)
                except KeyError as ce:
                    logger.warning(
                        "Skipped result extraction for component '%s': %s",
                        getattr(comp, "name", comp), ce
                    )

        if diagnostics_dir is not None:
            try:
                sol_path = diagnostics_dir / f"solution_{year_op}.nc"
                linopy_model.solution.to_netcdf(sol_path)
                logger.info("Saved solution -> %s", sol_path)

                duals = getattr(getattr(linopy_model, "constraints", None), "dual", None)
                if duals is not None:
                    dual_path = diagnostics_dir / f"dual_{year_op}.nc"
                    duals.to_netcdf(dual_path)
                    logger.info("Saved duals -> %s", dual_path)

            except Exception as err:
                logger.warning("Could not save solution/duals: %s", err)

        return linopy_model

    # Model is infeasible or did not converge
    if diagnostics_dir is not None:
        _export_gurobi_iis(linopy_model, year_op=year_op, diagnostics_dir=diagnostics_dir)

    raise RuntimeError(
        f"Model did not converge: status={status}, termination={termination}"
    )


# ═════════════════════════════════════════════════════════════════════
# RESULT EXPORT
# ═════════════════════════════════════════════════════════════════════


def _normalise_marginal_prices(model, year_op: int) -> pd.DataFrame:
    """
    Fetch and normalise ALL marginal prices from the model.

    Returns a DataFrame with columns [area, year_op, hour, resource, value]
    covering every resource in the model (electricity, hydrogen,
    reservoir_water, etc.).  Numerical noise below PRICE_NUMERICAL_TOL
    is zeroed.

    This is the shared backbone for export_prices() and
    export_h2_prices().
    """
    mp = model.get_results("operation", "marginal_prices")
    mp = mp.to_pandas() if hasattr(mp, "to_pandas") else pd.DataFrame(mp)
    mp.columns = [str(c).strip() for c in mp.columns]

    # Normalize column names
    rename_map = {}
    for cand in ["t", "time", "step", "hour_op"]:
        if "hour" not in mp.columns and cand in mp.columns:
            rename_map[cand] = "hour"
            break
    for cand in ["marginal_price", "price", "dual", "shadow_price"]:
        if "value" not in mp.columns and cand in mp.columns:
            rename_map[cand] = "value"
            break
    for cand in ["commodity", "carrier"]:
        if "resource" not in mp.columns and cand in mp.columns:
            rename_map[cand] = "resource"
            break
    for cand in ["zone", "country", "node"]:
        if "area" not in mp.columns and cand in mp.columns:
            rename_map[cand] = "area"
            break

    if rename_map:
        mp = mp.rename(columns=rename_map)

    required = {"hour", "value", "resource"}
    missing = required - set(mp.columns)
    if missing:
        raise ValueError(f"Unexpected structure in marginal_prices: missing {sorted(missing)}")

    if "area" not in mp.columns:
        mp["area"] = "UNKNOWN"

    mp["area"] = mp["area"].astype(str).str.upper().str.strip()
    mp["year_op"] = int(year_op)
    mp["hour"] = pd.to_numeric(mp["hour"], errors="coerce")
    mp["value"] = pd.to_numeric(mp["value"], errors="coerce")
    mp["resource"] = mp["resource"].astype(str).str.lower().str.strip()

    mp = mp.dropna(subset=["hour", "value"]).copy()
    mp["hour"] = mp["hour"].astype(int)

    # Clean numerical noise
    mp.loc[mp["value"].abs() < PRICE_NUMERICAL_TOL, "value"] = 0.0

    return mp[["area", "year_op", "hour", "resource", "value"]].sort_values(
        ["area", "year_op", "hour", "resource"]
    ).reset_index(drop=True)


def export_prices(model, year_op: int) -> pd.DataFrame:
    """
    Extract marginal electricity prices from model results.

    Parameters
    ----------
    model
        EnergyModel instance with results set.
    year_op : int
        Operating year.

    Returns
    -------
    pd.DataFrame
        Columns: [area, year_op, hour, resource, value]
        Sorted by (area, year_op, hour).
        Values < PRICE_NUMERICAL_TOL are set to 0.0.
    """
    mp = _normalise_marginal_prices(model, year_op)
    return mp[mp["resource"] == "electricity"].copy().reset_index(drop=True)


def export_h2_prices(model, year_op: int) -> pd.DataFrame:
    """
    Extract marginal hydrogen prices from model results.

    These are the duals of the per-area hydrogen energy balance
    constraint — the shadow price of one additional MWh of H₂.
    When the electrolyser is the marginal H₂ supplier, the
    relationship to electricity price is:

        price_H₂ ≈ price_elec × 1.85 + VOM_electrolysis

    When the H₂ CCGT is the marginal electricity supplier:

        price_elec ≈ price_H₂ × 2.85 + VOM_H₂CCGT

    Parameters
    ----------
    model
        EnergyModel instance with results set.
    year_op : int
        Operating year.

    Returns
    -------
    pd.DataFrame
        Columns: [area, year_op, hour, resource, value]
        Empty DataFrame if no hydrogen resource in model.
    """
    mp = _normalise_marginal_prices(model, year_op)
    h2 = mp[mp["resource"] == "hydrogen"].copy()
    if h2.empty:
        logger.info("export_h2_prices: no hydrogen resource in marginal prices.")
    return h2.reset_index(drop=True)


def export_all_prices(model, year_op: int) -> pd.DataFrame:
    """
    Extract marginal prices for ALL resources (electricity, hydrogen,
    reservoir_water, etc.).

    Useful for cross-checking dual consistency across coupled buses.

    Parameters
    ----------
    model
        EnergyModel instance with results set.
    year_op : int
        Operating year.

    Returns
    -------
    pd.DataFrame
        Columns: [area, year_op, hour, resource, value]
    """
    return _normalise_marginal_prices(model, year_op)


def export_conversion_capacity(model, year_op: int) -> pd.DataFrame:
    """
    Extract conversion (non-storage) capacity results.

    Tries multiple result_type/result_name candidates and excludes
    hydro storage technologies.

    Parameters
    ----------
    model
        EnergyModel instance with results set.
    year_op : int
        Operating year.

    Returns
    -------
    pd.DataFrame
        Columns: [area, year_op, name, value]
        Empty DataFrame if no results found.
    """
    candidates = [
        ("investment", "power_capacity"),
        ("investment", "capacity"),
        ("operation", "power_capacity"),
        ("operation", "installed_power_capacity"),
        ("operation", "capacity"),
    ]

    forbidden_names = {
        "Reservoir_Hydro_Store",
        "Reservoir_Hydro_Inflow",
        "Pumped_Hydro",
        "electric_line",
        "h2_pipeline",          # Transport tech, not conversion
    }

    forbidden_areas = {"NONE", "", "NAN", "UNKNOWN"}

    for result_type, result_name in candidates:
        try:
            out = model.get_results(result_type, result_name)
            df = out.to_pandas() if hasattr(out, "to_pandas") else pd.DataFrame(out)
            if df is None or df.empty:
                continue

            df = df.copy()
            df.columns = [str(c).strip() for c in df.columns]
            cols = set(df.columns)

            tech_col = None
            for cand in ["conversion_tech", "technology", "tech", "name"]:
                if cand in cols:
                    tech_col = cand
                    break

            value_col = None
            for cand in ["value", "power_capacity", "installed_power_capacity", "capacity", "installed_capacity"]:
                if cand in cols:
                    value_col = cand
                    break

            area_col = None
            for cand in ["area", "zone", "country", "node"]:
                if cand in cols:
                    area_col = cand
                    break

            if tech_col is None or value_col is None:
                continue

            df = df.rename(columns={tech_col: "name", value_col: "value"})
            if area_col is not None and area_col != "area":
                df = df.rename(columns={area_col: "area"})
            if "area" not in df.columns:
                df["area"] = "UNKNOWN"

            df["area"] = df["area"].astype(str).str.upper().str.strip()
            df["year_op"] = int(year_op)
            df["name"] = df["name"].astype(str)
            df["value"] = pd.to_numeric(df["value"], errors="coerce")

            df = df.dropna(subset=["value"]).copy()

            # Filter out transport techs, hydro-internal components, and
            # rows with missing/empty area (e.g. h2_pipeline links that
            # pommes puts in conversion results with no area dimension).
            df = df[
                ~df["name"].isin(forbidden_names)
                & ~df["area"].isin(forbidden_areas)
                & (df["area"].str.len() > 0)
            ].copy()

            return df[["area", "year_op", "name", "value"]].reset_index(drop=True)

        except Exception:
            continue

    return pd.DataFrame(columns=["area", "year_op", "name", "value"])


def export_storage_capacity(model, year_op: int) -> pd.DataFrame:
    """
    Extract storage energy capacity results.

    Parameters
    ----------
    model
        EnergyModel instance with results set.
    year_op : int
        Operating year.

    Returns
    -------
    pd.DataFrame
        Columns: [area, year_op, name, metric, value]
        metric = "energy_capacity_mwh"
        Empty DataFrame if no results found.
    """
    # ── Known storage technology names (used for fallback extraction
    #    from conversion results when pommes merges storage into
    #    the conversion result set) ──────────────────────────────────
    KNOWN_STORAGE_TECHS = {
        "Pumped_Hydro", "Battery_1h", "Battery_4h",
        "H2_SaltCavern", "Reservoir_Hydro_Store",
    }

    candidates = [
        ("investment", "energy_capacity"),
        ("investment", "storage_capacity"),
        ("investment", "capacity"),
        ("operation", "energy_capacity"),
    ]

    cap = None
    storage_col = None

    for result_type, result_name in candidates:
        try:
            out = model.get_results(result_type, result_name)
            df = out.to_pandas() if hasattr(out, "to_pandas") else pd.DataFrame(out)
            if df is not None and not df.empty:
                cols = [str(c).strip() for c in df.columns]
                # Accept both "storage_tech" and "storage_technology"
                for scol in ("storage_tech", "storage_technology"):
                    if scol in cols:
                        storage_col = scol
                        cap = df
                        break
                if cap is not None:
                    break
        except Exception:
            continue

    # ── Fallback: extract known storage techs from conversion results ──
    # pommes may merge all technologies into the conversion result set,
    # using "conversion_tech" instead of "storage_tech" as the dimension.
    if cap is None or cap.empty:
        fallback_candidates = [
            ("investment", "power_capacity"),
            ("investment", "capacity"),
            ("operation", "power_capacity"),
            ("operation", "installed_power_capacity"),
        ]
        for result_type, result_name in fallback_candidates:
            try:
                out = model.get_results(result_type, result_name)
                df = out.to_pandas() if hasattr(out, "to_pandas") else pd.DataFrame(out)
                if df is None or df.empty:
                    continue
                df.columns = [str(c).strip() for c in df.columns]
                # Find the technology column
                tech_col = None
                for cand in ("conversion_tech", "technology", "tech", "name"):
                    if cand in df.columns:
                        tech_col = cand
                        break
                if tech_col is None:
                    continue
                # Filter to known storage techs only
                mask = df[tech_col].astype(str).isin(KNOWN_STORAGE_TECHS)
                if mask.any():
                    cap = df[mask].copy()
                    storage_col = tech_col
                    logger.info(
                        "export_storage_capacity: using fallback — extracted %d storage rows "
                        "from conversion results (%s/%s).",
                        len(cap), result_type, result_name,
                    )
                    break
            except Exception:
                continue

    if cap is None or cap.empty:
        return pd.DataFrame(columns=["area", "year_op", "name", "metric", "value"])

    cap.columns = [str(c).strip() for c in cap.columns]

    if "value" not in cap.columns:
        for cand in ["energy_capacity", "storage_capacity", "capacity",
                      "installed_capacity", "power_capacity"]:
            if cand in cap.columns:
                cap = cap.rename(columns={cand: "value"})
                break

    if storage_col and storage_col in cap.columns and storage_col != "name":
        cap = cap.rename(columns={storage_col: "name"})

    if "area" not in cap.columns:
        for cand in ["zone", "country", "node"]:
            if cand in cap.columns:
                cap = cap.rename(columns={cand: "area"})
                break

    if "value" not in cap.columns or "name" not in cap.columns:
        return pd.DataFrame(columns=["area", "year_op", "name", "metric", "value"])

    if "area" not in cap.columns:
        cap["area"] = "UNKNOWN"

    cap["area"] = cap["area"].astype(str).str.upper().str.strip()
    cap["year_op"] = int(year_op)
    cap["name"] = cap["name"].astype(str)
    cap["metric"] = "energy_capacity_mwh"
    cap["value"] = pd.to_numeric(cap["value"], errors="coerce")

    cap = cap.dropna(subset=["value"]).copy()
    return cap[["area", "year_op", "name", "metric", "value"]].reset_index(drop=True)


def export_storage_power_capacity(model, year_op: int) -> pd.DataFrame:
    """
    Extract storage power capacity results.

    Excludes Reservoir_Hydro_Store (not a true storage with power output).

    Parameters
    ----------
    model
        EnergyModel instance with results set.
    year_op : int
        Operating year.

    Returns
    -------
    pd.DataFrame
        Columns: [area, year_op, name, metric, value]
        metric = "power_capacity_mw"
        Empty DataFrame if no results found.
    """
    # ── Known storage technology names (mirrors export_storage_capacity) ──
    KNOWN_STORAGE_TECHS = {
        "Pumped_Hydro", "Battery_1h", "Battery_4h",
        "H2_SaltCavern", "Reservoir_Hydro_Store",
    }

    candidates = [
        ("investment", "power_capacity"),
        ("operation", "power_capacity"),
    ]

    cap = None
    storage_col = None

    for result_type, result_name in candidates:
        try:
            out = model.get_results(result_type, result_name)
            df = out.to_pandas() if hasattr(out, "to_pandas") else pd.DataFrame(out)
            if df is not None and not df.empty:
                cols = [str(c).strip() for c in df.columns]
                for scol in ("storage_tech", "storage_technology"):
                    if scol in cols:
                        storage_col = scol
                        cap = df
                        break
                if cap is not None:
                    break
        except Exception:
            continue

    # ── Fallback: extract known storage techs from conversion results ──
    if cap is None or cap.empty:
        fallback_candidates = [
            ("investment", "power_capacity"),
            ("investment", "capacity"),
            ("operation", "power_capacity"),
            ("operation", "installed_power_capacity"),
        ]
        for result_type, result_name in fallback_candidates:
            try:
                out = model.get_results(result_type, result_name)
                df = out.to_pandas() if hasattr(out, "to_pandas") else pd.DataFrame(out)
                if df is None or df.empty:
                    continue
                df.columns = [str(c).strip() for c in df.columns]
                tech_col = None
                for cand in ("conversion_tech", "technology", "tech", "name"):
                    if cand in df.columns:
                        tech_col = cand
                        break
                if tech_col is None:
                    continue
                mask = df[tech_col].astype(str).isin(KNOWN_STORAGE_TECHS)
                if mask.any():
                    cap = df[mask].copy()
                    storage_col = tech_col
                    logger.info(
                        "export_storage_power_capacity: using fallback — extracted %d storage rows "
                        "from conversion results (%s/%s).",
                        len(cap), result_type, result_name,
                    )
                    break
            except Exception:
                continue

    if cap is None or cap.empty:
        return pd.DataFrame(columns=["area", "year_op", "name", "metric", "value"])

    cap.columns = [str(c).strip() for c in cap.columns]

    if "value" not in cap.columns:
        for cand in ["power_capacity", "capacity", "installed_capacity",
                      "installed_power_capacity"]:
            if cand in cap.columns:
                cap = cap.rename(columns={cand: "value"})
                break

    if storage_col and storage_col in cap.columns and storage_col != "name":
        cap = cap.rename(columns={storage_col: "name"})

    if "area" not in cap.columns:
        for cand in ["zone", "country", "node"]:
            if cand in cap.columns:
                cap = cap.rename(columns={cand: "area"})
                break

    if "value" not in cap.columns or "name" not in cap.columns:
        return pd.DataFrame(columns=["area", "year_op", "name", "metric", "value"])

    if "area" not in cap.columns:
        cap["area"] = "UNKNOWN"

    # Exclude Reservoir_Hydro_Store — it stores water, not electricity
    if "name" in cap.columns:
        cap = cap[cap["name"].astype(str) != "Reservoir_Hydro_Store"].copy()

    cap["area"] = cap["area"].astype(str).str.upper().str.strip()
    cap["year_op"] = int(year_op)
    cap["name"] = cap["name"].astype(str)
    cap["metric"] = "power_capacity_mw"
    cap["value"] = pd.to_numeric(cap["value"], errors="coerce")

    cap = cap.dropna(subset=["value"]).copy()
    return cap[["area", "year_op", "name", "metric", "value"]].reset_index(drop=True)
