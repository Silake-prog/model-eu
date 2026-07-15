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

    from clever.runner import (
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
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import polars as pl
import xarray as xr

from pommes.io.build_input_dataset import build_input_parameters
from pommes.model.build_model import build_model
from pommes.model.data_validation.dataset_check import check_inputs

from clever.constants import PRICE_NUMERICAL_TOL

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
            "Crossover": 1,           # Crossover to basic solution
            "BarHomogeneous": 1,      # Homogeneous barrier formulation
            "NumericFocus": 3,        # High precision
            "Threads": 16,            # INARI has plenty of cores
            "NodefileStart": 8,       # Spill B&B nodes to disk after 8 GB
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
        p[inv_pmax_var] = xr.where(absent_power, 0.0, inv_pmax).fillna(0.0)

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
        p[inv_emax_var] = xr.where(absent_energy, 0.0, inv_emax).fillna(0.0)

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

    # Fill NaN in transport capacity bounds (cover both naming conventions)
    for v in [
        "transport_capacity_max",
        "transport_capacity_min",
        "transport_capacity_investment_max",
        "transport_capacity_investment_min",
        "transport_power_capacity_max",
        "transport_power_capacity_min",
        "transport_power_capacity_investment_max",
        "transport_power_capacity_investment_min",
    ]:
        if v in p:
            nan_count = int(np.isnan(p[v].values).sum())
            if nan_count > 0:
                logger.info(
                    "sanitize_transport: filling %d NaN values in %s with 0.0",
                    nan_count, v,
                )
            p[v] = p[v].fillna(0.0)

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
            import gurobipy as grb  # noqa: F811

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


def run_model_without_ramping(
    model,
    solver_name: str,
    solver_options: dict,
    year_op: int,
    write_lp: bool = False,
    diagnostics_dir: Optional[Path] = None,
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
    p = sanitize_absent_conversions(p)
    p = sanitize_storage_inputs(p)
    p = sanitize_transport_inputs(p)

    if "conversion_power_capacity_max" not in p:
        raise ValueError("conversion_power_capacity_max missing from POMMES dataset")

    # ── Fix bogus end_of_life values ────────────────────────────────
    # When a tech has no explicit lifetime, NaN is cast to int64 →
    # -9223372036854775808 (int64 min).  This makes the planning mask
    # (year_op < year_dec) * (year_dec <= end_of_life) always False,
    # so no investment window exists.  If investment_min > 0, the model
    # must build the tech but has no valid year → structural infeasibility.
    # Fix: replace with a safe far-future year (2130 = 80 years from 2050).
    _EOL_DEFAULT = 2130
    for eol_name in ["conversion_end_of_life", "storage_end_of_life"]:
        if eol_name in p:
            eol = p[eol_name]
            bad_mask = eol.values < 0  # int64 min = NaN-cast artifact
            n_bad = int(bad_mask.sum())
            if n_bad > 0:
                p[eol_name] = xr.where(eol < 0, _EOL_DEFAULT, eol)
                logger.warning(
                    "%s: replaced %d bogus entries (int64 min from NaN cast) "
                    "with %d",
                    eol_name, n_bad, _EOL_DEFAULT,
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
        # reservoir_water load shedding: must_run=1.0 inflow techs MUST be
        # able to shed excess water when reservoir is full.  Without this,
        # countries with high hydro inflow hit structural infeasibility.
        if "reservoir_water" in p["load_shedding_max_capacity"].resource.values:
            p["load_shedding_max_capacity"].loc[dict(resource="reservoir_water")] = 1e6
    if "load_shedding_cost" in p:
        if "hydrogen" in p["load_shedding_cost"].resource.values:
            p["load_shedding_cost"].loc[dict(resource="hydrogen")] = 10_000.
        if "reservoir_water" in p["load_shedding_cost"].resource.values:
            p["load_shedding_cost"].loc[dict(resource="reservoir_water")] = 0.0

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

    # ── Pre-build sanity check: detect zombie / duplicate conversions ──
    # A "zombie" is a conversion tech whose power capacity max is zero
    # (or NaN) across all areas, meaning it will be trivially eliminated
    # by presolve but adds junk columns to the matrix.  Common cause:
    # supplyforge creating a lowercase `hydrogen_power_plant` alongside
    # CLEVER's uppercase `Hydrogen_power_plant`.
    if conv_dim in p.dims and "conversion_power_capacity_max" in p:
        cap_max = p["conversion_power_capacity_max"]
        for tech in cap_max[conv_dim].values:
            tech_max = float(cap_max.sel({conv_dim: tech}).max())
            if np.isnan(tech_max) or tech_max <= 0.0:
                logger.warning(
                    "ZOMBIE detected: conversion tech '%s' has max capacity %.6f across "
                    "all areas — it will be presolve-eliminated but wastes matrix columns.",
                    tech, tech_max,
                )
        # Check for case-insensitive duplicates and REMOVE the zombie
        tech_names = [str(t) for t in cap_max[conv_dim].values]
        seen_lower: dict[str, str] = {}
        zombies_to_drop: list[str] = []
        for tn in tech_names:
            lower = tn.lower()
            if lower in seen_lower and seen_lower[lower] != tn:
                # Two techs with same name but different case — keep the one
                # with higher max capacity, drop the other.
                existing = seen_lower[lower]
                cap_existing = float(cap_max.sel({conv_dim: existing}).max())
                cap_new = float(cap_max.sel({conv_dim: tn}).max())
                if np.isnan(cap_existing):
                    cap_existing = 0.0
                if np.isnan(cap_new):
                    cap_new = 0.0
                drop = existing if cap_new >= cap_existing else tn
                keep = tn if cap_new >= cap_existing else existing
                logger.warning(
                    "DUPLICATE detected (case-insensitive): '%s' and '%s' coexist. "
                    "Dropping '%s' (cap_max=%.1f), keeping '%s' (cap_max=%.1f).",
                    existing, tn, drop,
                    cap_existing if drop == existing else cap_new,
                    keep,
                    cap_new if keep == tn else cap_existing,
                )
                zombies_to_drop.append(drop)
                seen_lower[lower] = keep
            else:
                seen_lower[lower] = tn

        # Actually drop zombie conversion techs from the dataset
        if zombies_to_drop:
            keep_mask = ~np.isin(p[conv_dim].values, zombies_to_drop)
            keep_techs = p[conv_dim].values[keep_mask]
            p = p.sel({conv_dim: keep_techs})
            logger.info(
                "Dropped %d zombie conversion tech(s): %s. "
                "Remaining techs: %s",
                len(zombies_to_drop), zombies_to_drop,
                list(keep_techs),
            )

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
        model.set_all_results(linopy_model)

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
