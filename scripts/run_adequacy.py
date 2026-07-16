# ══════════════════════════════════════════════════════════════════════
# 1.0  IMPORTS  (single cell — every import the notebook needs)
# ══════════════════════════════════════════════════════════════════════
from __future__ import annotations

# ── Force Python to load `supplyforge` and `clever` from THIS workspace ──
# (avoids shadowing by an older copy installed elsewhere, e.g. tp_pommes_kraft)
import sys
from pathlib import Path as _Path
import os as _os
# Workspace root: set CLEVER_WORK_ROOT env var on the server; otherwise
# default to the local Desktop copy.  This is the ONLY change needed to
# run this notebook on the INARI cluster.
_WS_ROOT = _Path(_os.environ.get("CLEVER_WORK_ROOT", _Path.home() / "Desktop" / "clever-work"))
# Order matters: more specific paths first so their packages win over any
# older copy that may be lingering on the default sys.path (e.g. pip-installed
# versions in the active env, or siblings like tp_pommes_kraft/).
# NOTE: do NOT insert _WS_ROOT/"supplyforge" — the project uses the
# namespace-pkg style import `supplyforge.create_pommes_craft_model`
# which requires the OUTER repo dir to be importable as `supplyforge` (PEP 420
# namespace pkg). Inserting the inner path would shadow the outer namespace pkg
# with the inner regular package and break `supplyforge.X`.
for _p in (_WS_ROOT / "demandforge",    # vendored DemandForge (pathway-resolved eSAF + CO₂)
           _WS_ROOT):                    # clever/ pkg + outer namespace pkg `supplyforge`
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)
# Evict every previously-imported copy of these packages so the paths above win
for _m in [k for k in list(sys.modules)
          if k == "supplyforge" or k.startswith("supplyforge.")
          or k == "clever"      or k.startswith("clever.")
          or k == "demandforge" or k.startswith("demandforge.")]:
    del sys.modules[_m]

# Sanity: confirm we loaded the vendored DemandForge (not a stray pip install)
import demandforge as _df
_df_path = _Path(_df.__file__).resolve().parent
assert _df_path.is_relative_to((_WS_ROOT / "demandforge").resolve()), (
    f"Wrong demandforge picked up: {_df_path}. Expected {_WS_ROOT/'demandforge'}."
)
# Confirm the pathway-resolved eSAF constant is present (smoke test for the
# corrected version from the DemandForge session recap)
from demandforge.load_projection.constants import H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY
assert set(H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY) == {"fischer_tropsch", "methanol_to_jet"}, (
    "demandforge constants missing pathway-resolved eSAF H₂ intensity"
)
print(f"demandforge from: {_df_path}")

import logging
import warnings
from pathlib import Path
from typing import Any, Optional

import matplotlib
if not _os.environ.get("DISPLAY"):
    matplotlib.use("Agg")   # headless server / .py script — no windowed backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import polars as pl
import xarray as xr

# CLEVER modules
from clever.fetch import fetch_clever_xlsx
from clever.process import process_clever_xlsx
from clever.demand import (
    read_total_electricity_twh,
    read_end_use_twh,
    compute_targets,
    build_hourly_profiles,
)
from clever.model import (
    create_multi_country_model_from_clever,
    read_clever_capacity_csv,
    read_clever_load_factor_csv,
    read_clever_non_enr_csv,
    extract_baseload_profile,
    shape_h2_demand_from_baseload,
    shape_h2_demand_flat,
)
from clever.runner import (
    run_model_without_ramping,
    build_solver_options,
    load_hourly_total,
    build_demand_dict,
    export_prices,
    export_conversion_capacity,
    export_storage_capacity,
    export_storage_power_capacity,
)
from clever.adequacy import (
    compute_country_adequacy_metrics,
    compute_expost_adequacy,
)
from clever.constants import (
    MANUAL_INTERCONNECTIONS,
    DEFAULT_LOAD_SHEDDING_COST,
    AREA_MAP,
    _EOLES_CAPEX_2026,
    _EOLES_FOM_2026,
    _EOLES_VOM_2026,
    _EOLES_DISCOUNT_RATE_UNIFORM,
    _EOLES_STORAGE_CAPEX_2026,
    DEFAULT_KEEP_AREAS,
    EXPANSION_HEADROOM_BY_COUNTRY,
    DEFAULT_EXPANSION_HEADROOM_MW,
)
from clever import CLEVER_CSV_DIR, DEMAND_DIR, RESULTS_DIR

# POMMES high-level API (for post-hoc flex)
from pommes_craft import Demand, FlexibleDemand

# POMMES internals (for ramping intervention)
from pommes.io.build_input_dataset import build_input_parameters
from pommes.model.build_model import build_model
from pommes.model.data_validation.dataset_check import check_inputs
from clever.runner import (
    sanitize_absent_conversions,
    sanitize_storage_inputs,
    sanitize_transport_inputs,
)

# DemandForge H₂ coupling modules
#   Only the symbols genuinely used downstream are imported — the
#   analysis/post-processing helpers live under supplyforge.h2_analysis
#   and supplyforge.h2_postprocess and should be imported on demand in a
#   dedicated analysis notebook, not here.
from supplyforge.create_pommes_craft_model import (
    fetch_h2_demand_from_demandforge,
    add_hydrogen,
    add_h2_interconnections,
    _find_component,           # used in §3.5 & §3.6.5 to detect CLEVER h2pp
    H2_PIPELINE_COSTS,
    H2_STORAGE_COSTS,
    H2_ADJACENCY,
    FIXED_COSTS as SF_FIXED_COSTS,
    INVEST_COSTS as SF_INVEST_COSTS,
    LIFETIMES as SF_LIFETIMES,
)

from supplyforge.create_pommes_craft_model import (
    DISPATCHABLE_DEFAULT_COSTS as SF_VARIABLE_COSTS,
)

# MENA areas (Phase 2.5 Variant B) carry their own dedicated H₂ supply stack
# built inside clever.mena_imports.add_mena_country_area. The H₂-loops below
# iterate model.areas — they must skip MENA codes to avoid duplicate
# h2_storage / hydrogen-stack injection (which would crash with
# "Component name 'h2_storage' already exists in area 'MA/DZ/TN/LY'").
from clever.mena_imports import MENA_COUNTRY_CONFIG as _MENA_COUNTRY_CONFIG
_MENA_AREA_CODES: frozenset[str] = frozenset(_MENA_COUNTRY_CONFIG.keys())
from supplyforge.h2_analysis import cross_check_clever_totals   # §3.6 only

# ── Logging / warnings ────────────────────────────────────────────
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*shadow price.*")
warnings.filterwarnings("ignore", message=".*Overwriting.*")

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
for _lib in ("linopy", "gurobipy", "pommes", "pommes_craft", "xarray", "urllib3", "supplyforge"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

# ── Matplotlib defaults ───────────────────────────────────────────
plt.rcParams.update({
    "figure.figsize": (14, 6),
    "figure.dpi": 120,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "savefig.bbox": "tight",
    "savefig.dpi": 150,
})

print("All imports OK.")


# ── Fetch CLEVER XLSX ──────────────────────────────────────────────
xlsx_path = fetch_clever_xlsx(force=False)
print(f"CLEVER spreadsheet: {xlsx_path}  ({xlsx_path.stat().st_size / 1e6:.1f} MB)")

# ── Parse into structured tables ──────────────────────────────────
tables = process_clever_xlsx(xlsx_path)
print(f"Parsed tables: {list(tables.keys())}")

# ══════════════════════════════════════════════════════════════════════
# 1.1  SCENARIO PARAMETERS
# ══════════════════════════════════════════════════════════════════════

# ── General ────────────────────────────────────────────────────────
MODEL_YEAR       = 2050
# Scenario-gated weather-year override: `_wyYYYY` suffix wins, else 2024 default
from clever.constants import _WEATHER_YEAR_OVERRIDE as _PARSED_WY
WEATHER_REF_YEAR = _PARSED_WY if _PARSED_WY is not None else 2024  # SupplyForge / DemandForge reference year
REF_EV_YEAR      = 2024          # EV profile reference year
SOLVER           = "gurobi"      # "gurobi" or "highs"
ADD_HYDRO        = True          # include hydro (RoR, reservoir, PHS)
ADD_INTERCO      = True          # include NTC interconnections

# Default = all 30 CLEVER countries. For fast smoke tests, set CLEVER_AREAS as
# a comma-separated subset (e.g. CLEVER_AREAS=FR,DE) — this restricts COUNTRIES
# to that subset, intersected with DEFAULT_KEEP_AREAS so typos can't sneak in.
_CLEVER_AREAS_ENV = _os.environ.get("CLEVER_AREAS", "").strip()
if _CLEVER_AREAS_ENV:
    _requested = [c.strip().upper() for c in _CLEVER_AREAS_ENV.split(",") if c.strip()]
    _valid = set(DEFAULT_KEEP_AREAS)
    COUNTRIES = sorted(c for c in _requested if c in _valid)
    if not COUNTRIES:
        raise ValueError(
            f"CLEVER_AREAS={_CLEVER_AREAS_ENV!r} matched no valid areas. "
            f"Valid set: {sorted(_valid)}"
        )
    _logger = logging.getLogger(__name__)
    _logger.warning(
        "CLEVER_AREAS override active: restricting to %s (smoke-test mode)",
        COUNTRIES,
    )
else:
    COUNTRIES = sorted(DEFAULT_KEEP_AREAS)  # ALL CLEVER countries

# ── Scenario knob ──────────────────────────────────────────────────
# Drives per-scenario diagnostics / figures / exports / tables dir.
# Default reads from env var so concurrent runs can each set their own:
#   CLEVER_SCENARIO=policy_re python -u scripts/run_adequacy.py ...
# Accepted values:
#   "R0_v1"        — central sufficiency baseline (low_h2 bundle)
#   "policy_re"        — non-sufficiency RE counterfactual (central, +20% elec, VRE 0.30)
#   "policy_nuke"      — policy_re + Nuclear expandable
#   "R0_v1_nuke"       — R0_v1 demand base + Nuclear expandable
#   "policy_re_noMin"  — policy_re without electrolyser sovereignty floors
#   "policy_re_corrNx" — policy_re + N× HVDC corridor expansion (N ∈ {2,3})
#   "policy_nuke_corrNx" — policy_nuke + N× HVDC corridor expansion
SCENARIO = _os.environ.get("CLEVER_SCENARIO", "policy_re")
_VALID_SCENARIOS = {
    "R0_v1", "policy_re", "policy_nuke", "R0_v1_nuke", "policy_re_noMin",
    "R0_v1_corr2x", "R0_v1_corr3x",
    "policy_re_corr2x", "policy_re_corr3x",
    "policy_nuke_corr2x", "policy_nuke_corr3x",
    "R0_v1_nuke_corr2x", "R0_v1_nuke_corr3x",
    "policy_re_noMin_corr2x", "policy_re_noMin_corr3x",
    "R0_v1_bioLow", "R0_v1_nuke_bioLow", "policy_re_bioMed", "policy_re_noMin_bioMed", "policy_nuke_bioMed", "policy_nuke_bioMed_el700", "policy_nuke_bioMed_el900", "policy_nuke_bioMed_atr", "policy_nuke_bioMed_el900_atr", "policy_nuke_bioMed_h2HIGH", "policy_nuke_bioMed_h2HIGH_atr", "R0_v1_bioMed", "policy_re_bioLow",
    "R0_v1_nuke_bioLow_co2250_nofloor", "policy_nuke_bioMed_atr_co2250_nofloor", "policy_nuke_bioMed_h2HIGH_atr_co2250_nofloor",
    'R0_v1_nuke_bioLow_atr_el700_noGas_corr2x', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH',
    'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX150_h2central',
    'R0_v1_nuke_bioLow_atr_el500_noGas_corr2x', 'R0_v1_nuke_bioLow_atr_el900_noGas_corr2x', 'R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_wy2010', 'R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_wy2018', 'R0_v1_nuke_bioMed_atr_el500_noGas_corr2x_elecX180_h2HIGH_vreEXT', 'R0_v1_nuke_bioMed_atr_el900_noGas_corr2x_elecX180_h2HIGH_vreEXT', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2010', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2018', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT',
    'R0_v1_nuke_bioMed_atr_el500_noGas_corr2x_elecX125_h2central', 'R0_v1_nuke_bioMed_atr_el900_noGas_corr2x_elecX125_h2central', 'R0_v1_nuke_bioOff_el700_noGas_corr2x_elecX125_h2central', 'R0_v1_bioMed_atr_el700_noGas_corr2x_elecX125_h2central', 'R0_v1_nuke_bioMed_atr_el700_corr2x_elecX125_h2central_co2300', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr3x_elecX125_h2central', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_vreEXT', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_wy2010', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_wy2018', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX150_h2central',
    'R0_v1_nuke_bioLow_atr_el500_noGas_corr2x', 'R0_v1_nuke_bioLow_atr_el900_noGas_corr2x', 'R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_wy2010', 'R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_wy2018', 'R0_v1_nuke_bioMed_atr_el500_noGas_corr2x_elecX180_h2HIGH_vreEXT', 'R0_v1_nuke_bioMed_atr_el900_noGas_corr2x_elecX180_h2HIGH_vreEXT', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2010', 'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2018',
    # ── bioHigh tier (Phase 1.5 refactor) — retargeted HIGH baseline + sensitivities ──
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT',
    # Gas-active counterfactual of the HIGH baseline (post-Phase-3 methane bus
    # + carbon_price.py module). Same demand stack, default CO₂ trajectory
    # (ff55 → 150 €/tCO₂ in 2050) so it lines up with the _noGas HIGH for
    # head-to-head comparison: does fossil gas displace bio? does it depress
    # the mean electricity price? Added 2026-05-28.
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT',
    # Same gas-active HIGH variant but with _nofloor — lifts CLEVER's
    # politically-retained Gas existing-capacity floor (~22 GW EU-wide).
    # Lets the LP fully decommission residual Gas if economically irrational.
    # Used in conjunction with the SMR/ATR-CCS techs (clever/methane_h2_ccs.py,
    # add_h2_ccs_techs_to_area): methane → H₂ with 90 % carbon capture, competes
    # head-to-head with ATR_biomethane on the H₂ bus.
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor',
    # Same as above PLUS `_noElecFloor` (removes the tech-specific
    # electrolyser sovereignty floor). With BECCS available the LP would
    # rather build SMR_CCS/ATR_CCS bio_mode than electrolysis; this scenario
    # lets it. Combined with the v11 flex Gas cap lift (~50 GW) it should
    # eliminate the artificial v10c VoLL.
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor',
    # v12 — same as v11 minus _elecX180 (baseline CLEVER sufficiency electricity
    # demand). Tests prices/dispatch when bio chain isn't stretched by an
    # electrified-everything industrial load. h2HIGH demand retained.
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor',
    # v13 — v11 stack + MENA Variant A (200 TWh/yr cap, upper-band per-entry
    # delivered cost defaults: ES=€100, IT=€115/MWh_H2). Smoke run is FR+ES+IT
    # to see whether the LP imports MENA H2 at upper-band prices when bio
    # chain (BECCS) is competitive locally.
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_menaH2200',
    # v14 — Path B: same as v11 stack + MENA Variant B (per-country optimised),
    # all 4 MENA countries with renewables.ninja CFs (V112-3000 @ 100m hub,
    # MERRA-2 2023). LP picks build mix + dispatch endogenously; Variant A
    # disabled (no _menaH2NNN). Smoke is FR+ES+IT+MA+DZ+TN+LY (7 areas).
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_menaOptim',
    # v15g — diagnostic: same v15 stack but _menaPref0 zeros out MENA local
    # demand (export-only mode). If cost drops back to ~v14's 220 B€/yr,
    # the blow-up is in demand wiring; if it stays huge, the blow-up is in
    # the blue-H₂ / Gas CCGT / NG NetImport wiring.
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_menaOptim_menaPref0',
    # v15h — diagnostic: same as v15 but _menaPref10 scales MENA demand to
    # 10% of nominal (so MA 15/3, DZ 26/5, TN 5.5/1, LY 5/1.5 TWh elec/H2).
    # Plus reverted MENA_ tech name renames + scalar demand. Compare cost
    # against v14 (220) and v15g (219) to detect non-linear scaling bugs.
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_menaOptim_menaPref10',
    'R0_v1_nuke_bioHigh_atr_el500_noGas_corr2x_elecX180_h2HIGH_vreEXT',
    'R0_v1_nuke_bioHigh_atr_el900_noGas_corr2x_elecX180_h2HIGH_vreEXT',
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2010',
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_wy2018',
    # ── MENA H₂ imports (Phase 2.5) ── Variant A: exogenous cap + price
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaH2100',
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaH2200',
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaH2400',
    'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_menaH2200',
    # ── MENA H₂ imports — Variant B: per-country optimised
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaOptim',
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaOptimMA',
    'R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_menaOptimMA_DZ',
    'R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central_menaOptim',
    'R0_v1_nuke_bioLow_atr_el700_noGas_corr2x_menaOptim',
    # ── EU30-only sensitivity sweep (2026-05-31) — NO MENA. One-axis-at-a-time
    #    perturbations of the v16 HIGH baseline (line ~269). Footprint = EU30
    #    (CLEVER_AREAS unset). Baseline point (CO₂ 150 / bioHigh / h2HIGH /
    #    elecX180) IS v16 itself, already run.
    # CO₂ price tornado (flat overrides; default ff55 = 150 €/tCO₂):
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2off',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2100',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2300',
    # Biomass-availability axis (biomass is the binding constraint — dual-confirmed in v16):
    'R0_v1_nuke_bioMed_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor',
    # H₂-demand axis (HIGH → central bundle):
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2central_vreEXT_nofloor_noElecFloor',
    # Electricity-demand axis (×1.80 → ×1.25):
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX125_h2HIGH_vreEXT_nofloor_noElecFloor',
    # ── 12-cell sensitivity matrix (scripts/run_sensitivity_sweep.sh) ──
    # elec{LOW(x1.0), HIGH(elecX180)} × H2{LOW(low_h2), HIGH(h2HIGH)} × bio{Low,Med,High};
    # fixed background co2150 + batt20. Added 2026-06-03 (were missing → assert failed).
    'R0_v1_nuke_bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    # ── CORRECTED matrix (2026-06-11): electrolysis 1.43, CH4-leak pricing
    # (ng 2.5% / bio 1.0%, GWP100, default-on), CCS cap 5 GW_H2/country ──
    'R0_v1_nuke_bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    'R0_v1_nuke_bioHigh_atr_el700_corr2x_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20',
    # noGas variant of the corrected matrix (bioMed; `_noGas` NON-adjacent to
    # `_corr2x` to avoid the DECARB coupling; no ccsCap — CCS is off under noGas):
    'R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_elecX180_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioMed_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    # ── Gas-free headroom-sensitivity variants (2026-06-16): bioLow, h2HIGH,
    # noGas; 3 batches x {elecLOW, elecHIGH}. Batch1 no nuke; Batch2 +vreXXL
    # (~2x VRE headroom); Batch3 +vreXXL+nukeXXL (~2x VRE & nuclear). ──
    'R0_v1_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreXXL_nukeXXL_nofloor_noElecFloor_co2150_batt20',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nukeXXL_nofloor_noElecFloor_co2150_batt20',
    # ── MENA Variant B (endogenous hub) overlay on the gas-free eHI headroom
    # baselines (2026-06-17): one-variable add of _menaOptim (all 4 MA/DZ/TN/LY,
    # renewables.ninja CFs) to B1/B2/B3 elecHIGH. Tests whether cheap imported
    # H2 (~€60-115/MWh vs €424-546 domestic) closes the gas-free high-demand
    # adequacy gap. CLEVER_AREAS unset -> EU30 + 4 MENA areas. NB Variant B
    # builds MENA blue-H2 on local fossil gas, so this is NOT globally gas-free.
    'R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_menaOptim',
    # Maximally-relieved nuclear-exit stack (2026-07-10): no-nuclear gas-free eHI +
    # ALL reliefs (demand-response voll900/h2voll400 + imports menaOptim; +storPx1000
    # added at run time). The no-nuke sibling of fiStP. Weather-ensemble headline:
    # can storage×10 + imports + demand-response make the system with NEITHER gas NOR
    # nuclear adequate across 2017/2018/2024?
    'R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_menaOptim',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_menaOptim',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nukeXXL_nofloor_noElecFloor_co2150_batt20_menaOptim',
    # ── Demand-side load-shedding (effacement / VoLL) sensitivity (2026-06-17,
    # Lucie/Quentin): one-variable overlay on the gas-free eHI baselines. The
    # gas-free H2-CCGT peaker is marginal at ~1300 €/MWh; H2 marginal ~500.
    #   _vollNNN  = electricity VoLL  (default 30 000). _voll900 = effacement
    #     remuneration BELOW the peaker; _voll3000 = scarcity cap ABOVE it.
    #   _h2vollNNN = hydrogen VoLL (default 10 000). _h2voll400 = industrial-H2
    #     effacement below the H2 marginal; _h2voll2000 = H2 backstop. ──
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_voll3000',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_voll900',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_h2voll2000',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20_h2voll400',
    'R0_v1_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900',
    # ── Scenario-matrix completion (2026-06-17): clean one-lever-at-a-time on the
    # STANDARD gas-free build (nuclear + std VRE), bioLow / h2HIGH baseline. R2 =
    # gas-free base (the foundational cell, never run); R5 = +flex (elec 900 + H2
    # 400 effacement); R6 = +MENA imports; R7 = +flex +imports. Lever rows at
    # x1.8 only (gas-free base is adequate at x1.0). See analysis/scenario_matrix.tex.
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',                              # R2 x1.0
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20',                     # R2 x1.8
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400',   # R5 +flex
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_menaOptim',           # R6 +imports
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_menaOptim', # R7 +flex+imports

    'R0_v1_nuke_bioLow_atr_el700_corr2x_h2HIGH_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20_vreFree',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_vreFree',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_ccsCap5_co2150_batt20_vreFree',
    'R0_v1_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_vreFree',
    'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreXXL_nukeXXL_nofloor_noElecFloor_co2150_batt20_vreFree',
}
_VALID_SCENARIOS = _VALID_SCENARIOS | {'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_menaOptim_menaCap200'}  # central-set Imp_RE2x (MENA 2xRE)
_VALID_SCENARIOS = _VALID_SCENARIOS | {'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_h2HIGH_vreEXT_nofloor_noElecFloor_co2150_batt20_voll900_h2voll400_menaOptim_vreFree'}  # eL FlexImp (no-floor) = fi_storP low-elec twin
import re as _re_pk  # free sensitivity axes stripped before validation: distance-CAPEX, storage-power, weather-year, MENA-risk
_VALID_SCENARIOS = _VALID_SCENARIOS | {'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT_nukeXXL_nofloor_noElecFloor_co2150_batt20'}  # Exp NUKEonly (nuclear headroom only, VRE at EXT)
_VALID_SCENARIOS = _VALID_SCENARIOS | {'R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreXXL_nofloor_noElecFloor_co2150_batt20'}  # Exp VREonly (VRE headroom only, nuclear normal)
# Scenario validation is now structural: the declarative flag registry
# (clever/scenario/registry.py) accepts any string whose tokens are all declared
# flags — declaring a flag once is enough, no whitelist edit needed. The legacy
# _VALID_SCENARIOS set above is retained for one release as a shadow-check that the
# registry is a strict superset (any string the old whitelist accepted, the registry
# must accept too); once trusted, the set and this shadow-check can be deleted.
from clever.scenario.registry import validate as _scn_validate  # noqa: E402
_scenario_valid = _scn_validate(SCENARIO)
_legacy_valid = _re_pk.sub(r'_pipekm\d+|_storPx\d+|_wy\d+|_menaRisk[A-Z]{2}_\d+|_noGridExp', '', SCENARIO) in _VALID_SCENARIOS
assert _scenario_valid, (
    f"Unknown SCENARIO: {SCENARIO!r}. Every '_'-token must be a declared flag — "
    f"see clever/scenario/registry.py (FLAG_REGISTRY) and docs/flags.md. "
    f"Diagnostic: {__import__('clever.scenario.registry', fromlist=['explain']).explain(SCENARIO)}"
)
assert not (_legacy_valid and not _scenario_valid), (
    f"Registry regression: legacy whitelist accepts {SCENARIO!r} but the registry does not."
)
# Sub-task 5 flag: policy_re_noMin is policy_re with electrolyser_min_bounds_gw=None
NO_MIN_BOUNDS = SCENARIO.endswith("_noMin")

# ── Paths (per-scenario) ───────────────────────────────────────────
DIAG_DIR   = RESULTS_DIR / "diagnostics" / SCENARIO
FIG_DIR    = RESULTS_DIR / "figures"     / SCENARIO
EXPORT_DIR = RESULTS_DIR / "export"      / SCENARIO
TABLES_DIR = _WS_ROOT    / "tables"      / SCENARIO

for _d in [DIAG_DIR, FIG_DIR, EXPORT_DIR, DEMAND_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

print(f"SCENARIO   = {SCENARIO!r}")
print(f"  DIAG_DIR   = {DIAG_DIR}")
print(f"  TABLES_DIR = {TABLES_DIR}")

# ══════════════════════════════════════════════════════════════════════
# 1.2  RAMPING CONFIGURATION
# ══════════════════════════════════════════════════════════════════════
# Toggle: False = baseline (all techs ramp freely), True = per-tech ramp limits
RAMPING_ENABLED = False  # disabled to reduce model size on server

# Values: (ramp_up_fraction_per_hour, ramp_down_fraction_per_hour)
# IMPORTANT: base names are matched as case-insensitive substrings of the
# actual POMMES tech coordinate (see run_model_with_ramping()).  The H₂ CCGT
# component is always named ``Hydrogen_power_plant`` (CLEVER-injected) or
# ``hydrogen_power_plant`` (fresh fallback from add_hydrogen) — one key
# ``"hydrogen_power_plant"`` matches both.  A legacy ``"h2_ccgt"`` entry
# matched nothing and has been removed.
RAMP_RATES_BASE = {
    "nuclear":              (0.05, 0.05),   # 5 %/h — conservative French fleet
    "ch4_ccgt":             (0.50, 0.50),   # 50 %/h — modern CCGT
    "ch4_ocgt":             (1.00, 1.00),   # 100 %/h — fast peaker
    "gas":                  (0.50, 0.50),   # generic gas label
    "hydrogen_power_plant": (0.50, 0.50),   # H₂ CCGT — matches both capital- and lower-case variants
    "biomass":              (0.30, 0.30),
    "waste":                (0.30, 0.30),
    "coal":                 (0.20, 0.20),
    # VRE & hydro — keep at 1.0 (resource-limited, not ramp-limited)
    "solar":                (1.00, 1.00),
    "wind_onshore":         (1.00, 1.00),
    "wind_offshore":        (1.00, 1.00),
    "reservoir_hydro":      (1.00, 1.00),
    "run_of_river":         (1.00, 1.00),
    "ror_hydro":            (1.00, 1.00),
}

# ══════════════════════════════════════════════════════════════════════
# 1.3  DEMAND-SIDE FLEXIBILITY CONFIGURATION
# ══════════════════════════════════════════════════════════════════════
FLEX_CATEGORIES = {
    "ev":         {"fraction_of_total_demand": 0.08, "conservation_hours": 12,
                   "max_multiplier": 1.30, "min_multiplier": 0.50, "variable_cost": 5.0},
    "heat_pump":  {"fraction_of_total_demand": 0.10, "conservation_hours":  4,
                   "max_multiplier": 1.20, "min_multiplier": 0.80, "variable_cost": 15.0},
    "industrial": {"fraction_of_total_demand": 0.05, "conservation_hours":  8,
                   "max_multiplier": 1.10, "min_multiplier": 0.70, "variable_cost": 25.0},
}

# ── Aggregate into a single POMMES-compatible FlexibleDemand ──────
FLEX_TOTAL_FRACTION   = 0.0   # DISABLED — save RAM for 30-country run
FLEX_CONSERVATION_HRS = 8     # default (flex disabled)
FLEX_MAX_MULTIPLIER   = 1.21
FLEX_MIN_MULTIPLIER   = 0.67
FLEX_VARIABLE_COST    = 13.7
FLEX_RAMP_UP          = np.nan
FLEX_RAMP_DOWN        = np.nan

# ══════════════════════════════════════════════════════════════════════
# 1.4  INTERCONNECTION OVERRIDES
# ══════════════════════════════════════════════════════════════════════
# Expanded FR-DE NTC based on ENTSO-E TYNDP 2024 "Global Ambition" 2050.
INTERCONNECTION_OVERRIDES = {
    ("FR", "DE"): {"capacity": 9_500.0, "investment_cost": 0.0},
}

# ══════════════════════════════════════════════════════════════════════
# 1.5  DemandForge H₂ COUPLING
# ══════════════════════════════════════════════════════════════════════
# Master toggle for the physically-coupled electricity↔hydrogen layer
# (sections 3.3, 3.4 become no-ops when True, and 3.5 builds the full
#  H₂ bus with electrolyser, storage, H₂ CCGT, and optional pipelines).
ADD_DEMANDFORGE_H2  = True

# DemandForge scenario bundle — driven by SCENARIO:
#   R0_v1 / R0_v1_nuke      → "low_h2"  (CLEVER sufficiency-aligned)
#   policy_re / policy_nuke → "central" (≈ 2× low_h2 industrial H₂)
# DemandForge bundle selection (3 options): low_h2 (CLEVER sufficiency-aligned,
# ~456 TWh EU30 industrial H2), central (REPowerEU-aligned, ~898 TWh),
# high_h2 (full electrolytic transition with recalibrated per-sector params,
# ~1400 TWh, used by _h2HIGH sensitivity scenarios). See clever/constants.py
# for the suffix parser and DemandForge scenario_registry.yaml for parameters.
# Layered: explicit _h2HIGH/_h2central suffix wins, else prefix default
if "_h2HIGH" in SCENARIO:
    DEMANDFORGE_BUNDLE = "high_h2"
elif "_h2central" in SCENARIO:
    DEMANDFORGE_BUNDLE = "central"
elif SCENARIO.startswith("R0_v1"):
    DEMANDFORGE_BUNDLE = "low_h2"
else:
    DEMANDFORGE_BUNDLE = "central"
print(f"  DEMANDFORGE_BUNDLE = {DEMANDFORGE_BUNDLE!r}")

# Make electrolyser/storage capacities investable (optimiser decides sizing)
H2_INVESTABLE       = True

# Storage technology: "tank" (5,400 €/MWh) or "salt_cavern" (125 €/MWh)
H2_STORAGE_TYPE     = "salt_cavern"

# H₂ inter-area transport
H2_TRANSPORT_ENABLED = True
H2_PIPELINE_TYPE     = "new"  # "new" or "repurposed"

# Capacity-expansion headrooms for H₂ subsystems that CLEVER itself does
# NOT cap (electrolyser + H₂ storage).  H₂ CCGT respects CLEVER's own
# per-country EXPANSION_HEADROOM_BY_COUNTRY["Hydrogen_power_plant"] cap
# or, for countries with no CLEVER h2pp, DEFAULT_EXPANSION_HEADROOM_MW.
DEFAULT_ELECTROLYSER_INVEST_MAX_MW    = 50_000.
DEFAULT_STORAGE_POWER_INVEST_MAX_MW   = 20_000.
DEFAULT_STORAGE_ENERGY_INVEST_MAX_MWH = 10_000_000.   # 10 TWh headroom

H2_ANALYSIS_YEARS = [2030, 2035, 2040, 2045, 2050]    # multi-horizon (§11)

# ══════════════════════════════════════════════════════════════════════
# 1.5b  LEGACY H₂ CONFIG (only read when ADD_DEMANDFORGE_H2 = False)
# ══════════════════════════════════════════════════════════════════════
# These knobs feed the legacy electricity-only H₂ CCGT (§3.3) and the
# electricity-only salt cavern abstraction (§3.4).  They are kept for
# reproducibility of earlier runs but are IGNORED when the DemandForge
# coupling is enabled — the coupled layer derives its own caps from
# EXPANSION_HEADROOM_BY_COUNTRY and the DEFAULT_*_INVEST_MAX_MW constants
# above.
CCGT_H2_INV_MAX = 40_000      # MW (legacy §3.3 H₂ CCGT invest cap)

ADD_H2_SALT_CAVERN = True     # legacy §3.4 toggle (auto-disabled below)
H2_CAVERN_SPECS = {
    "name": "H2_SaltCavern",
    "eoles_tech": "h2_saltcavern",
    "roundtrip_efficiency": 0.40,   # electrolyser 70% × CCGT 58%
    "duration_hours": 168.0,        # 1-week long-duration storage
    "factor_keep": 0.0,
}
H2_CAVERN_POWER_MAX_MW = {
    "FR": 5_000., "DE": 10_000., "ES": 3_000., "IT": 3_000.,
    "GB": 5_000., "NL":  5_000., "BE": 1_000., "CH":   500., "AT": 500.,
}
DEFAULT_H2_CAVERN_POWER_MAX_MW = 2_000.

# ══════════════════════════════════════════════════════════════════════
# 1.6  SENSITIVITY SCENARIOS (for Section 9)
# ══════════════════════════════════════════════════════════════════════
SENSITIVITY_SCENARIOS = {
    "baseline_no_ramp_no_flex": {"ramping": False, "flex_fraction": 0.00},
    "no_ramp_ev_only":          {"ramping": False, "flex_fraction": 0.08, "flex_conservation": 6,
                                 "flex_max": 1.15, "flex_min": 0.85, "flex_cost": 10.0},
    "no_ramp_full_flex":        {"ramping": False, "flex_fraction": FLEX_TOTAL_FRACTION,
                                 "flex_conservation": FLEX_CONSERVATION_HRS,
                                 "flex_max": FLEX_MAX_MULTIPLIER, "flex_min": FLEX_MIN_MULTIPLIER,
                                 "flex_cost": FLEX_VARIABLE_COST},
    "ramp_full_flex":           {"ramping": True,  "flex_fraction": FLEX_TOTAL_FRACTION,
                                 "flex_conservation": FLEX_CONSERVATION_HRS,
                                 "flex_max": FLEX_MAX_MULTIPLIER, "flex_min": FLEX_MIN_MULTIPLIER,
                                 "flex_cost": FLEX_VARIABLE_COST},
}

# ── Configuration summary ────────────────────────────────────────
print(f"Scenario: CLEVER {MODEL_YEAR} | {len(COUNTRIES)} countries | "
      f"weather ref {WEATHER_REF_YEAR} | solver {SOLVER}")
print(f"Ramping: {'ENABLED' if RAMPING_ENABLED else 'DISABLED'}")
print(f"Flexibility: {FLEX_TOTAL_FRACTION:.1%} of demand | "
      f"conservation {FLEX_CONSERVATION_HRS}h | "
      f"bounds [{FLEX_MIN_MULTIPLIER:.2f}, {FLEX_MAX_MULTIPLIER:.2f}] | "
      f"cost {FLEX_VARIABLE_COST:.1f} EUR/MWh")
print(f"DemandForge H₂: {'ENABLED' if ADD_DEMANDFORGE_H2 else 'DISABLED'} | "
      f"bundle={DEMANDFORGE_BUNDLE} | investable={H2_INVESTABLE} | "
      f"storage={H2_STORAGE_TYPE} | transport={H2_TRANSPORT_ENABLED}")


# EOLES costs are embedded in clever.constants as dicts (no CSV dependency)
eoles_costs = {
    "capex":         _EOLES_CAPEX_2026,
    "fom":           _EOLES_FOM_2026,
    "vom":           _EOLES_VOM_2026,
    "discount":      _EOLES_DISCOUNT_RATE_UNIFORM,
    "storage_capex": _EOLES_STORAGE_CAPEX_2026,
}
print(f"EOLES cost categories: {list(eoles_costs.keys())}")
for cat, d in eoles_costs.items():
    print(f"  {cat:15s}: {len(d)} technologies")

# ── Annual targets from CLEVER ─────────────────────────────────────
base_totals = read_total_electricity_twh(CLEVER_CSV_DIR)
end_use_csv = CLEVER_CSV_DIR / "clever_end_use_electricity.csv"
end_use_wide = read_end_use_twh(end_use_csv) if end_use_csv.exists() else None

targets = compute_targets(base_totals, end_use_wide, allow_fallback=True)
print(f"Demand targets: {len(targets)} country-year pairs")
print(targets.head(10)) if hasattr(targets, 'head') else print(targets)

# ── Build 8760-hour profiles ───────────────────────────────────────
hourly = build_hourly_profiles(
    targets_df=targets,
    out_dir=DEMAND_DIR,
    per_area_files=False,
    tol_rel=1e-2,
    reference_year=WEATHER_REF_YEAR,
    reference_ev_year=REF_EV_YEAR,
)
print(f"Hourly profiles built: {DEMAND_DIR / 'hourly_electricity_demand.csv'}")

# ── Load hourly demand ─────────────────────────────────────────────
demand_wide = load_hourly_total(DEMAND_DIR / "hourly_electricity_demand.csv")

# CRITICAL: filter to modelled countries AND target year before building dict.
# build_demand_dict expects data for a single year_op; passing multiple years
# causes pommes_craft Demand validation to fail (e.g., year_op=2015 != 2050).
demand_wide = demand_wide[demand_wide["area"].isin(COUNTRIES)].copy()
demand_wide = demand_wide[demand_wide["year_op"] == MODEL_YEAR].copy()

if demand_wide.empty:
    raise RuntimeError(
        f"No demand data for year_op={MODEL_YEAR} and countries={COUNTRIES}. "
        f"Available years: {sorted(load_hourly_total(DEMAND_DIR / 'hourly_electricity_demand.csv')['year_op'].unique())}"
    )

demand_dict = build_demand_dict(demand_wide)

# SAFETY NET: force year_op to MODEL_YEAR in every entry.
# This catches edge cases where build_demand_dict picks up a stale year
# from the first row of a group.
import polars as pl
for area in list(demand_dict.keys()):
    ddf = demand_dict[area]
    unique_years = ddf["year_op"].unique().to_list()
    if unique_years != [MODEL_YEAR]:
        print(f"  FIX: {area} had year_op={unique_years}, forcing to {MODEL_YEAR}")
        demand_dict[area] = ddf.with_columns(pl.lit(MODEL_YEAR).alias("year_op"))

# ── Scenario-gated electricity demand multiplier ──────────────────
# policy_re / policy_nuke: +20% uniform per country, all hours.
# R0_v1 / R0_v1_nuke: unchanged (×1.00).
# Layered: explicit _elecXNNN suffix wins, else policy_ default, else 1.0
from clever.constants import _ELEC_DEMAND_MULTIPLIER as _PARSED_ELEC_MULT
if _PARSED_ELEC_MULT != 1.0:
    ELEC_DEMAND_MULTIPLIER = _PARSED_ELEC_MULT
elif SCENARIO.startswith("policy_"):
    ELEC_DEMAND_MULTIPLIER = 1.20
else:
    ELEC_DEMAND_MULTIPLIER = 1.00
if ELEC_DEMAND_MULTIPLIER != 1.0:
    for area in list(demand_dict.keys()):
        demand_dict[area] = demand_dict[area].with_columns(
            (pl.col("demand") * ELEC_DEMAND_MULTIPLIER).alias("demand")
        )
    print(f"Applied electricity demand multiplier ×{ELEC_DEMAND_MULTIPLIER:.2f} to {len(demand_dict)} areas (SCENARIO={SCENARIO!r})")
else:
    print(f"Electricity demand multiplier ×1.00 (SCENARIO={SCENARIO!r}) — no uplift applied")

# Check which countries have demand entries
missing_countries = [c for c in COUNTRIES if c not in demand_dict]
COUNTRIES_MODELLED = [c for c in COUNTRIES if c in demand_dict]

if missing_countries:
    print(f"WARNING: Missing demand for {missing_countries} — excluded from model.")
print(f"Countries modelled: {COUNTRIES_MODELLED}")

for area, ddf in sorted(demand_dict.items()):
    peak = float(ddf["demand"].max())
    energy_twh = float(ddf["demand"].sum()) / 1e6
    yr = ddf["year_op"].unique().to_list()
    print(f"  {area}: peak={peak:,.0f} MW | annual={energy_twh:.1f} TWh | year_op={yr}")

# ── Load CLEVER capacity, load-factor, and non-ENR tables ─────────
capacity_df   = read_clever_capacity_csv(CLEVER_CSV_DIR / "energy_conversion_tech_capacity.csv")
load_factor_df = read_clever_load_factor_csv(CLEVER_CSV_DIR / "energy_conversion_tech_load_factor.csv")
non_enr_df    = read_clever_non_enr_csv(CLEVER_CSV_DIR / "non_enr.csv")

print(f"Capacity:    {len(capacity_df)} rows")
print(f"Load factor: {len(load_factor_df)} rows")
print(f"Non-ENR:     {len(non_enr_df)} rows")


# ── Installed VRE capacity by country (GW) ────────────────────────
cap_2050 = capacity_df[
    (capacity_df["year_op"] == MODEL_YEAR) &
    (capacity_df["area"].isin(COUNTRIES_MODELLED))
].copy()
cap_pivot = cap_2050.pivot_table(
    index="area", columns="model_tech", values="capacity_mw", aggfunc="sum"
).fillna(0) / 1000  # MW → GW
print("Installed VRE capacity (GW):")
print(cap_pivot.round(1)) if hasattr(cap_pivot, 'style') else print(cap_pivot.round(1))

# ── Peak demand by country (GW) ──────────────────────────────────
peak_demand = {}
for cc, ddf in demand_dict.items():
    peak_demand[cc] = ddf["demand"].max() / 1000  # MW → GW
peak_df = pd.Series(peak_demand, name="peak_demand_gw").sort_values(ascending=False)
print("\nPeak demand (GW):")
print(peak_df.round(1))

# Suppress verbose model-building output
for _lib in ("pommes_craft", "supplyforge"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

# ── Guard: verify demand_dict has correct year_op ─────────────────
# This prevents the year_op mismatch error if cells are run out of order.
for _area, _ddf in demand_dict.items():
    _yrs = _ddf["year_op"].unique().to_list()
    if _yrs != [MODEL_YEAR]:
        raise RuntimeError(
            f"demand_dict['{_area}'] has year_op={_yrs} but MODEL_YEAR={MODEL_YEAR}. "
            f"Please re-run the 'Load model inputs' cell (Section 2.4) first."
        )
print(f"demand_dict year_op check: all {len(demand_dict)} entries have year_op={MODEL_YEAR} ✓")

model = create_multi_country_model_from_clever(
    country_codes=COUNTRIES_MODELLED,
    reference_year_weather=WEATHER_REF_YEAR,
    model_year=MODEL_YEAR,
    clever_capacity_df=capacity_df,
    clever_load_factor_df=load_factor_df,
    clever_non_enr_df=non_enr_df,
    electricity_demand_by_country=demand_dict,
    eoles_costs=eoles_costs,
    interconnections={**MANUAL_INTERCONNECTIONS, **INTERCONNECTION_OVERRIDES} if ADD_INTERCO else None,
    add_interconnections=ADD_INTERCO,
    add_hydro=ADD_HYDRO,
    # Flexibility parameters (aggregated from categories in Section 1)
    flex_demand_fraction=FLEX_TOTAL_FRACTION,
    flex_conservation_hrs=FLEX_CONSERVATION_HRS,
    flex_max_multiplier=FLEX_MAX_MULTIPLIER,
    flex_min_multiplier=FLEX_MIN_MULTIPLIER,
    flex_ramp_up=FLEX_RAMP_UP,
    flex_ramp_down=FLEX_RAMP_DOWN,
    flex_variable_cost=FLEX_VARIABLE_COST,
    # Hydrogen demand add
    include_hydrogen=ADD_DEMANDFORGE_H2,  # register "hydrogen" resource if DemandForge active
)

# Restore logging
for _lib in ("pommes_craft", "supplyforge"):
    logging.getLogger(_lib).setLevel(logging.INFO)

print(f"Model built: {len(COUNTRIES_MODELLED)} areas")

# ── Inspect areas and their components ─────────────────────────────
areas = model.areas if hasattr(model, 'areas') else getattr(model, '_areas', {})
if isinstance(areas, list):
    areas = {getattr(a, 'name', str(a)): a for a in areas}

summary_rows = []
for area_name, area_obj in sorted(areas.items()):
    components = getattr(area_obj, 'components', getattr(area_obj, '_components', {}))
    if isinstance(components, list):
        components = {getattr(c, 'name', str(c)): c for c in components}
    elif not isinstance(components, dict):
        components = {}

    conv_techs = [n for n, c in components.items()
                  if "Conversion" in type(c).__name__]
    stor_techs = [n for n, c in components.items()
                  if "Storage" in type(c).__name__]
    flex_comps = [n for n, c in components.items()
                  if "Flexible" in type(c).__name__]

    summary_rows.append({
        "Area": area_name,
        "Conversion techs": len(conv_techs),
        "Storage techs": len(stor_techs),
        "Flex demand": len(flex_comps),
        "Components (detail)": ", ".join(sorted(conv_techs + stor_techs + flex_comps)),
    })

build_summary = pd.DataFrame(summary_rows)
print("Model component inventory:")
print(build_summary) if hasattr(build_summary, 'style') else print(build_summary.to_string())

# ══════════════════════════════════════════════════════════════════════
# 3.3  Force-add H2 CCGT to countries that don't already have it
# ══════════════════════════════════════════════════════════════════════
from pommes_craft import ConversionTechnology
from clever.model import techno_costs_from_eoles
from clever.constants import EXPANSION_HEADROOM_BY_COUNTRY, DEFAULT_EXPANSION_HEADROOM_MW

ADD_H2_CCGT = True   # ← set False to respect CLEVER scenario boundary

# Skip legacy H₂ CCGT when DemandForge coupling is active
if ADD_DEMANDFORGE_H2:
    print("DemandForge H₂ coupling ENABLED → skipping legacy electricity-only H₂ CCGT.")
    print("  → Section 3.5 adds a proper H₂-bus CCGT (factor electricity:+1, hydrogen:-2.85)")
    print("  → The electrolyser consumes from the electricity bus, so prices couple automatically.")
    ADD_H2_CCGT = False

if ADD_H2_CCGT:
    # Compute H2 CCGT costs from EOLES database
    h2_costs = techno_costs_from_eoles("h2_ccgt", eoles_costs)
    print(f"H2 CCGT costs: variable={h2_costs['variable_cost']:.1f} EUR/MWh, "
          f"invest={h2_costs['invest_cost']:.0f} EUR/kW, "
          f"lifetime={h2_costs['life_span']} yr")

    areas = model.areas if hasattr(model, 'areas') else getattr(model, '_areas', {})
    if isinstance(areas, list):
        areas = {getattr(a, 'name', str(a)): a for a in areas}

    added = 0
    for area_code, area_obj in sorted(areas.items()):
        if area_code in _MENA_AREA_CODES:
            continue  # MENA areas have their own H₂ supply stack — see clever.mena_imports
        # Check if H2 already exists
        components = getattr(area_obj, 'components', getattr(area_obj, '_components', {}))
        if isinstance(components, list):
            components = {getattr(c, 'name', str(c)): c for c in components}

        has_h2 = any("hydrogen" in str(n).lower() or "h2" in str(n).lower()
                      for n in components.keys())
        
        if has_h2:
            print(f"  {area_code}: H2 CCGT already present — skipping")
            continue

        # Get country-specific headroom or default
        headroom = EXPANSION_HEADROOM_BY_COUNTRY.get(area_code, {})
        invest_max = headroom.get("Hydrogen_power_plant",
                                   DEFAULT_EXPANSION_HEADROOM_MW.get("Hydrogen_power_plant", 15_000.0))
        
        # Override from config if set
        if CCGT_H2_INV_MAX is not None:
            invest_max = CCGT_H2_INV_MAX

        with model.context():
            area_obj.add_component(ConversionTechnology(
                name="Hydrogen_power_plant",
                factor={"electricity": 1.0},
                availability=1.0,
                must_run=0.0,
                variable_cost=h2_costs["variable_cost"],
                fixed_cost=h2_costs["fixed_cost"],
                invest_cost=h2_costs["invest_cost"],
                finance_rate=h2_costs["finance_rate"],
                life_span=h2_costs["life_span"],
                power_capacity_min=0.0,
                power_capacity_max=invest_max,
                power_capacity_investment_min=0.0,
                power_capacity_investment_max=invest_max,
                early_decommissioning=True,
            ))
        
        print(f"  {area_code}: Added H2 CCGT | invest_max={invest_max:,.0f} MW | "
              f"var_cost={h2_costs['variable_cost']:.1f} EUR/MWh")
        added += 1

    print(f"\nH2 CCGT added to {added} areas (investment-only, no existing capacity)")
else:
    print("ADD_H2_CCGT = False → respecting CLEVER scenario boundary (no H2)")

# ══════════════════════════════════════════════════════════════════════
# 3.4  Force-add salt cavern H2 storage (Option D)
# ══════════════════════════════════════════════════════════════════════
from pommes_craft import StorageTechnology
from clever.model import storage_costs_from_eoles

# Skip legacy salt cavern when DemandForge coupling handles H₂ storage
if ADD_DEMANDFORGE_H2:
    print("DemandForge H₂ coupling ENABLED → skipping legacy electricity-only salt cavern.")
    print("  → Section 3.5 adds a proper H₂-bus storage with configurable type (tank / salt_cavern)")
    ADD_H2_SALT_CAVERN = False

if ADD_H2_SALT_CAVERN:
    cavern_costs = storage_costs_from_eoles(H2_CAVERN_SPECS["eoles_tech"], eoles_costs)
    eta_rt = H2_CAVERN_SPECS["roundtrip_efficiency"]
    duration_h = H2_CAVERN_SPECS["duration_hours"]

    print(f"H2 Salt Cavern costs:")
    print(f"  invest_cost_power:  {cavern_costs['invest_cost_power']:>10.1f} EUR/MW")
    print(f"  invest_cost_energy: {cavern_costs['invest_cost_energy']:>10.4f} EUR/MWh")
    print(f"  fixed_cost_power:   {cavern_costs['fixed_cost_power']:>10.1f} EUR/MW/yr")
    print(f"  life_span:          {cavern_costs['life_span']:>10d} yr")
    print(f"  roundtrip_eff:      {eta_rt:>10.0%}")
    print(f"  duration:           {duration_h:>10.0f} h")

    areas = model.areas if hasattr(model, 'areas') else getattr(model, '_areas', {})
    if isinstance(areas, list):
        areas = {getattr(a, 'name', str(a)): a for a in areas}

    added_cavern = 0
    for area_code, area_obj in sorted(areas.items()):
        if area_code in _MENA_AREA_CODES:
            continue  # MENA areas have their own salt-cavern storage — see clever.mena_imports
        # Check if H2 salt cavern already exists
        components = getattr(area_obj, 'components', getattr(area_obj, '_components', {}))
        if isinstance(components, list):
            components = {getattr(c, 'name', str(c)): c for c in components}

        has_cavern = any("saltcavern" in str(n).lower() or "salt_cavern" in str(n).lower()
                         for n in components.keys())
        if has_cavern:
            print(f"  {area_code}: H2 salt cavern already present — skipping")
            continue

        # Power cap (MW)
        power_max_mw = H2_CAVERN_POWER_MAX_MW.get(area_code, DEFAULT_H2_CAVERN_POWER_MAX_MW)
        # Energy cap (MWh) = power × duration
        energy_max_mwh = power_max_mw * duration_h

        with model.context():
            area_obj.add_component(
                StorageTechnology(
                    name=H2_CAVERN_SPECS["name"],
                    factor_in={"electricity": -1.0},
                    factor_out={"electricity": eta_rt},
                    factor_keep={"electricity": H2_CAVERN_SPECS["factor_keep"]},
                    fixed_cost_power=cavern_costs["fixed_cost_power"],
                    invest_cost_power=cavern_costs["invest_cost_power"],
                    invest_cost_energy=cavern_costs["invest_cost_energy"],
                    finance_rate=cavern_costs["finance_rate"],
                    life_span=cavern_costs["life_span"],
                    energy_capacity_investment_min=0.0,
                    energy_capacity_investment_max=energy_max_mwh,
                    power_capacity_investment_min=0.0,
                    power_capacity_investment_max=power_max_mw,
                    early_decommissioning=True,
                )
            )

        print(f"  {area_code}: Added H2 SaltCavern | power_max={power_max_mw:,.0f} MW | "
              f"energy_max={energy_max_mwh:,.0f} MWh ({duration_h:.0f}h) | "
              f"η_rt={eta_rt:.0%}")
        added_cavern += 1

    print(f"\nH2 salt cavern added to {added_cavern} areas")
else:
    print("ADD_H2_SALT_CAVERN = False — no long-duration H2 storage")

# ══════════════════════════════════════════════════════════════════════
# 3.5  DemandForge H₂ sector coupling
# ══════════════════════════════════════════════════════════════════════
# Coupled electricity ↔ hydrogen optimisation, respecting CLEVER's
# capacity discipline:
#   • Every modelled country receives the H₂ supply stack.
#   • H₂ demand profile: shaped from DemandForge baseload ELECTRICITY
#     component (minus thermosensitive + EV), rescaled to annual H₂ MWh.
#   • H₂ CCGT: if CLEVER already injected a ``Hydrogen_power_plant``
#     (via non_enr.csv / ``proelchyd``), we PATCH its factor to draw
#     from the H₂ bus (``electricity:+1, hydrogen:-2.85``) and keep
#     CLEVER's own MW + EXPANSION_HEADROOM_BY_COUNTRY cap.  Otherwise
#     we add a fresh H₂ CCGT bounded by the same per-country headroom.
#   • Electrolyser & storage capacities are ENDOGENOUS, bounded by
#     explicit DEFAULT_*_INVEST_MAX_MW caps from §1.5.
#   • Electrolyser load enters the electricity bus ⇒ electricity-price
#     ↔ H₂ price coupling is automatic through the LP dual.
# ══════════════════════════════════════════════════════════════════════
if ADD_DEMANDFORGE_H2:
    # ── 1. Fetch sector-decomposed industrial H₂ demand (annual) ─
    h2_sectoral = fetch_h2_demand_from_demandforge(
        bundle_name=DEMANDFORGE_BUNDLE,
        countries=COUNTRIES_MODELLED,
        model_year=MODEL_YEAR,
        sectoral=True,
    )

    print(f"DemandForge H₂ demand for {MODEL_YEAR} (bundle: {DEMANDFORGE_BUNDLE}):")
    total_eu = 0.
    for cc in sorted(h2_sectoral.keys()):
        sectors = h2_sectoral[cc]
        country_total = sum(sectors.values())
        total_eu += country_total
        if country_total > 0:
            sector_str = ", ".join(f"{s}={v/1e6:.2f} TWh"
                                    for s, v in sectors.items() if v > 0)
            print(f"  {cc}: {country_total/1e6:.2f} TWh — {sector_str}")
    print(f"  EU total: {total_eu/1e6:.1f} TWh")

    # ── 1b. Pre-build H₂ demand profiles from ELECTRICITY baseload ──
    # The DemandForge hourly_electricity_demand.csv contains the baseload
    # component = industrial + services + residential baseline.  We use
    # this temporal SHAPE (not the magnitude) as a proxy for when H₂-
    # consuming industries are active, then rescale to the annual H₂ MWh
    # from DemandForge's industrial H₂ demand.
    #
    # IMPORTANT: the CSV is ELECTRICITY demand; the shaped output is
    # H₂ demand in MWh_H₂.  The conversion is purely a normalisation
    # (shape transfer), not a unit conversion.
    _elec_demand_csv = str(DEMAND_DIR / "hourly_electricity_demand.csv")
    _baseload_cache: dict[str, pl.DataFrame | None] = {}
    _shaped_h2_profiles: dict[str, pl.DataFrame] = {}

    for cc in COUNTRIES_MODELLED:
        # Extract baseload ELECTRICITY profile for this country
        bl = extract_baseload_profile(
            electricity_demand_csv_path=_elec_demand_csv,
            country_code=cc,
            year_op=MODEL_YEAR,
        )
        _baseload_cache[cc] = bl

        # Compute annual H₂ demand for this country (all sectors)
        sector_demands = h2_sectoral.get(cc, {}) or {}
        annual_h2 = sum(v for v in sector_demands.values() if v > 0)

        if annual_h2 > 0:
            if bl is not None:
                shaped = shape_h2_demand_from_baseload(
                    baseload_profile_pl=bl,
                    annual_h2_demand_mwh=annual_h2,
                    year_op=MODEL_YEAR,
                )
                peak_mw = float(shaped["demand"].max())
                avg_mw = annual_h2 / 8760.
                print(f"  {cc}: H₂ profile from elec-baseload shape | "
                      f"peak={peak_mw:.0f} MW_H2 | avg={avg_mw:.0f} MW_H2 | "
                      f"peak/avg={peak_mw/avg_mw:.2f}")
            else:
                shaped = shape_h2_demand_flat(
                    annual_h2_mwh=annual_h2,
                    year_op=MODEL_YEAR,
                )
                print(f"  {cc}: H₂ profile FLAT fallback (no baseload data) | "
                      f"avg={annual_h2/8760.:.0f} MW_H2")
            _shaped_h2_profiles[cc] = shaped

    # ── 2. Add H₂ supply stack + shaped demand to every country ──
    # Strategy: call add_hydrogen() with hydrogen_demand={} (empty dict)
    # so it adds the supply stack (electrolyser, storage, H₂PP) in
    # supply-only mode, then we inject our baseload-shaped Demand node.
    areas = model.areas if hasattr(model, "areas") else getattr(model, "_areas", {})
    if isinstance(areas, list):
        areas = {getattr(a, "name", str(a)): a for a in areas}

    countries_with_demand = 0
    countries_supply_only = 0
    patched_h2pp = 0
    fresh_h2pp = 0

    for area_code, area_obj in sorted(areas.items()):
        if area_code in _MENA_AREA_CODES:
            continue  # MENA areas already have electrolyser/h2_storage/H2-export pipe via clever.mena_imports
        sector_demands = h2_sectoral.get(area_code, {}) or {}
        total_mwh = sum(v for v in sector_demands.values() if v > 0)

        # Per-country H₂ CCGT headroom
        country_hr = EXPANSION_HEADROOM_BY_COUNTRY.get(area_code, {})
        h2pp_cap = country_hr.get(
            "Hydrogen_power_plant",
            DEFAULT_EXPANSION_HEADROOM_MW.get("Hydrogen_power_plant", 15_000.),
        )

        had_clever_h2pp = _find_component(area_obj, "Hydrogen_power_plant") is not None
        if had_clever_h2pp:
            patched_h2pp += 1
        else:
            fresh_h2pp += 1

        # Pass empty dict → supply-only mode (no demand node created)
        add_hydrogen(
            area=area_obj,
            hydrogen_demand={},                   # supply stack only
            hydrogen_capacity=0.,
            fixed_costs=SF_FIXED_COSTS,
            investment_costs=SF_INVEST_COSTS,
            variable_costs=SF_VARIABLE_COSTS,
            lifetimes=SF_LIFETIMES,
            investable=H2_INVESTABLE,
            storage_type=H2_STORAGE_TYPE,
            allow_h2_trade=H2_TRANSPORT_ENABLED,
            h2pp_invest_max_mw=h2pp_cap,
            electrolyser_invest_max_mw=DEFAULT_ELECTROLYSER_INVEST_MAX_MW,
            storage_power_invest_max_mw=DEFAULT_STORAGE_POWER_INVEST_MAX_MW,
            storage_energy_invest_max_mwh=DEFAULT_STORAGE_ENERGY_INVEST_MAX_MWH,
            patch_existing_h2pp=True,
        )

        # NOTE: H₂ load shedding + spillage feasibility margin is applied
        # at the xarray dataset level in runner.py (after build_input_parameters),
        # not here at the component level — component-level patches don't
        # propagate to the frozen dataset.

        # Inject baseload-shaped H₂ demand (if this country has H₂ demand)
        if area_code in _shaped_h2_profiles:
            with model.context():
                h2_demand_node = Demand(
                    name="h2_demand_total",
                    resource="hydrogen",
                    demand=_shaped_h2_profiles[area_code],
                )
                area_obj.add_component(h2_demand_node)

            n_sectors = sum(1 for v in sector_demands.values() if v > 0)
            src_label = "patched" if had_clever_h2pp else "fresh"
            profile_src = "baseload-shaped" if _baseload_cache.get(area_code) is not None else "flat-fallback"
            print(f"  {area_code}: industrial + supply | {total_mwh/1e6:.2f} TWh | "
                  f"{n_sectors} sectors | profile={profile_src} | "
                  f"H₂CCGT={src_label} | storage={H2_STORAGE_TYPE}")
            countries_with_demand += 1
        else:
            src_label = "patched" if had_clever_h2pp else f"fresh (cap {h2pp_cap:.0f} MW)"
            print(f"  {area_code}: supply only (no industrial demand) | H₂CCGT={src_label}")
            countries_supply_only += 1

    print(
        f"\nH₂ system added to {countries_with_demand + countries_supply_only} areas "
        f"({countries_with_demand} with industrial demand, "
        f"{countries_supply_only} supply-only). "
        f"H₂ CCGT: {patched_h2pp} patched from CLEVER, {fresh_h2pp} added fresh."
    )

    # ── 3. Add H₂ inter-area pipelines ───────────────────────────
    if H2_TRANSPORT_ENABLED:
        add_h2_interconnections(
            energy_model=model,
            countries=COUNTRIES_MODELLED,
            pipeline_type=H2_PIPELINE_TYPE,
        )
        print(f"H₂ pipelines: {H2_PIPELINE_TYPE} type")

else:
    print("DemandForge H₂ coupling DISABLED — using legacy H₂ CCGT approach")
    h2_sectoral = {}  # empty for downstream checks


# ══════════════════════════════════════════════════════════════════════
# 3.6  CLEVER ↔ DemandForge H₂ cross-check
# ══════════════════════════════════════════════════════════════════════
if ADD_DEMANDFORGE_H2 and h2_sectoral:
    clever_demand_csv = CLEVER_CSV_DIR / "demand_by_sector_resource.csv"
    if clever_demand_csv.exists():
        xcheck = cross_check_clever_totals(
            demandforge_results=h2_sectoral,
            clever_demand_csv=str(clever_demand_csv),
            model_year=MODEL_YEAR,
        )
        print("DemandForge / CLEVER H₂ cross-check:")
        print(xcheck.to_pandas().to_string(index=False))
        print()
        avg_ratio = xcheck["ratio"].mean()
        print(f"Average ratio (DemandForge/CLEVER): {avg_ratio:.2f}")
        if avg_ratio > 2.0 or avg_ratio < 0.5:
            print("⚠ Large deviation — review narrative consistency")
        else:
            print("✓ Within 2× envelope — reasonable coherence")
    else:
        print(f"CLEVER demand CSV not found at {clever_demand_csv} — skipping cross-check")
else:
    print("DemandForge H₂ disabled — no cross-check needed")


# ══════════════════════════════════════════════════════════════════════
# 3.6.2  DemandForge canonical vs supplyforge wrapper — coherence
# ══════════════════════════════════════════════════════════════════════
# Why this check exists:
#   The supplyforge wrapper fetch_h2_demand_from_demandforge() must preserve
#   the bundle's pathway_shares (FT vs MtJ).  If it bypasses load_bundle and
#   calls project_esaf_h2_demand directly without forwarding the split, the
#   active bundle silently falls back to pure FT (0.46 t H₂/t SAF) — which
#   matters for `central` (0.70/0.30), `high_h2` (0.40/0.60),
#   `industry_stress` (0.30/0.70), but is a no-op for `low_h2` (1.00/0.00).
#   The comparison below is the end-to-end invariant that guards against it.
if ADD_DEMANDFORGE_H2 and h2_sectoral:
    import yaml as _yaml
    from demandforge.load_projection.scenarios import load_bundle as _df_load_bundle

    # 1. Surface the bundle's configured pathway_shares (ground truth)
    _registry_path = Path(_df.__file__).resolve().parent / "scenario_registry.yaml"
    with open(_registry_path) as _fh:
        _registry = _yaml.safe_load(_fh)
    _bundle_yaml = _registry["bundles"][DEMANDFORGE_BUNDLE]
    _pathway_shares = _bundle_yaml.get("esaf", {}).get("pathway_shares")
    print(f"Active bundle: {DEMANDFORGE_BUNDLE!r}")
    print(f"  refinery.capacity_delay_years = "
          f"{_bundle_yaml.get('refinery', {}).get('capacity_delay_years')}")
    print(f"  refinery.molecule_scenario    = "
          f"{_bundle_yaml.get('refinery', {}).get('molecule_scenario')!r}")
    print(f"  esaf.pathway_shares           = {_pathway_shares}")

    # 2. Canonical DemandForge output for the exact same (bundle, countries,
    #    target_year) tuple used by the supplyforge wrapper.
    try:
        _df_frame = _df_load_bundle(
            bundle_name=DEMANDFORGE_BUNDLE,
            countries=sorted(COUNTRIES_MODELLED),
            target_year=MODEL_YEAR,
        )
    except Exception as _e:
        print(f"\n⚠ Could not call load_bundle directly ({type(_e).__name__}: {_e})")
        print("  Coherence check skipped — investigate if supplyforge results "
              "look suspicious.")
    else:
        # Aggregate canonical H₂ by country (target_year only, all sectors)
        _df_ty = _df_frame[_df_frame["year"] == MODEL_YEAR]
        _canon = (_df_ty.groupby("country")["h2_demand_mwh_per_yr"]
                        .sum()
                        .to_dict())

        # Supplyforge wrapper totals for the same year
        _wrap = {cc: sum(v for v in h2_sectoral.get(cc, {}).values() if v > 0)
                 for cc in sorted(COUNTRIES_MODELLED)}

        print(f"\n{MODEL_YEAR} H₂ demand — canonical (load_bundle) vs "
              f"wrapper (fetch_h2_demand_from_demandforge):")
        print(f"{'Area':<6} {'canonical_TWh':>14} {'wrapper_TWh':>13} "
              f"{'abs_err_TWh':>12} {'rel_err':>9}")
        print("─" * 60)

        _tot_canon = _tot_wrap = 0.
        _max_rel = 0.
        _offenders = []
        for _cc in sorted(COUNTRIES_MODELLED):
            _c = _canon.get(_cc, 0.) / 1e6
            _w = _wrap.get(_cc, 0.) / 1e6
            _abs = abs(_c - _w)
            _rel = _abs / max(_c, 1e-9) if _c > 0 else 0.
            _tot_canon += _c
            _tot_wrap  += _w
            _max_rel = max(_max_rel, _rel)
            if _rel > 0.02 and _abs > 0.1:  # >2% and >0.1 TWh → real divergence
                _offenders.append((_cc, _c, _w, _rel))
            print(f"{_cc:<6} {_c:>14.2f} {_w:>13.2f} {_abs:>12.2f} {_rel:>9.1%}")

        print("─" * 60)
        _tot_err = abs(_tot_canon - _tot_wrap)
        _tot_rel = _tot_err / max(_tot_canon, 1e-9)
        print(f"{'EU':<6} {_tot_canon:>14.2f} {_tot_wrap:>13.2f} "
              f"{_tot_err:>12.2f} {_tot_rel:>9.1%}")

        if _offenders or _tot_rel > 0.02:
            print("\n⚠ Coherence FAILED:")
            for _cc, _c, _w, _rel in _offenders[:10]:
                print(f"  {_cc}: canonical={_c:.2f} TWh, wrapper={_w:.2f} TWh "
                      f"(rel err {_rel:.1%})")
            raise RuntimeError(
                f"supplyforge wrapper diverges from DemandForge load_bundle "
                f"(max rel err {_max_rel:.1%}, EU-27 rel err {_tot_rel:.1%}). "
                f"Likely cause: pathway_shares not forwarded by the wrapper."
            )
        else:
            print(f"\n✓ Coherence verified (max per-country rel err "
                  f"{_max_rel:.2%}, EU-27 rel err {_tot_rel:.2%})")
            print("  → supplyforge wrapper honours the bundle's pathway_shares.")
else:
    print("DemandForge H₂ disabled — coherence check skipped.")


from supplyforge.create_pommes_craft_model import _component_factor_dict
# ══════════════════════════════════════════════════════════════════════
# 3.6.5  H₂ ↔ electricity coupling verification
# ══════════════════════════════════════════════════════════════════════
# Scans every area to confirm the components are present, correctly wired,
# and (critically) that there is no H₂ CCGT duplication between CLEVER's
# injection and the DemandForge coupling layer.  Fails loudly.

if ADD_DEMANDFORGE_H2:
    areas = model.areas if hasattr(model, "areas") else getattr(model, "_areas", {})
    if isinstance(areas, list):
        areas = {getattr(a, "name", str(a)): a for a in areas}

    print(f"{'Area':<6} {'Electrolyser':>12} {'H2_CCGT':>11} {'H2_store':>9} "
          f"{'H2_dem':>7} {'Ind_TWh':>9}")
    print("─" * 63)

    issues = []
    total_ind_twh = 0.
    n_mena_skipped = 0

    for area_code, area_obj in sorted(areas.items()):
        # MENA areas (Phase 2.5 Variant B) carry their own H₂ supply chain
        # under different component names (MENA_electrolysis, no
        # Hydrogen_power_plant by design — MENA exports H₂ via the
        # mena_h2_pipeline_*, doesn't generate electricity from H₂ domestically).
        # Skip them in this EU-CLEVER-targeted coupling validation.
        if area_code in _MENA_AREA_CODES:
            n_mena_skipped += 1
            continue
        components = getattr(area_obj, "components",
                              getattr(area_obj, "_components", {}))
        if isinstance(components, list):
            components = {getattr(c, "name", str(c)): c for c in components}

        has_elec  = "electrolysis"          in components
        has_store = "h2_storage"            in components

        # H₂ CCGT detection — CLEVER uses ``Hydrogen_power_plant`` (capital H),
        # our fresh additions use ``hydrogen_power_plant`` (lowercase).  We
        # must have exactly ONE, otherwise we are double-counting.
        clever_h2pp = components.get("Hydrogen_power_plant")
        fresh_h2pp  = components.get("hydrogen_power_plant")

        if clever_h2pp is not None and fresh_h2pp is not None:
            issues.append(f"{area_code}: DUPLICATE H₂ CCGT (CLEVER + fresh) — "
                          f"electricity-only CCGT will win, H₂ coupling broken")
            h2pp_label = "DUPLICATE!"
        elif clever_h2pp is not None:
            h2pp_label = "patched"
            # Verify CLEVER's one was rewired to consume H₂
            fac = _component_factor_dict(clever_h2pp)
            if fac.get("hydrogen", 0.) >= 0:
                issues.append(f"{area_code}: CLEVER H₂ CCGT not rewired — "
                              f"factor[hydrogen]={fac.get('hydrogen', 0.):.2f} (should be <0)")
        elif fresh_h2pp is not None:
            h2pp_label = "fresh"
        else:
            h2pp_label = "MISSING"
            issues.append(f"{area_code}: no H₂ CCGT at all")

        # Count sectoral demand nodes + sum their annual value from h2_sectoral
        sector_demands = h2_sectoral.get(area_code, {}) or {}
        n_dem = sum(1 for v in sector_demands.values() if v > 0)
        ind_twh = sum(sector_demands.values()) / 1e6
        total_ind_twh += ind_twh

        # Verify factor sign on electrolyser
        if has_elec:
            elec = components["electrolysis"]
            elec_draw = _component_factor_dict(elec).get("electricity", 0.)
            if elec_draw >= 0:
                issues.append(f"{area_code}: electrolyser factor[electricity]={elec_draw} "
                              f"(should be NEGATIVE — it consumes electricity)")

        print(f"{area_code:<6} {'✓' if has_elec else '✗':>12} "
              f"{h2pp_label:>11} {'✓' if has_store else '✗':>9} "
              f"{n_dem:>7d} {ind_twh:>9.2f}")

        if not (has_elec and has_store):
            issues.append(f"{area_code}: missing H₂ components "
                           f"(elec={has_elec}, store={has_store})")

    print("─" * 63)
    print(f"EU total industrial H₂ demand: {total_ind_twh:.1f} TWh")
    if n_mena_skipped > 0:
        print(f"(Skipped {n_mena_skipped} MENA areas — own H₂ supply via clever.mena_imports)")

    if issues:
        print("\n⚠ COUPLING ISSUES DETECTED:")
        for m in issues:
            print(f"  - {m}")
        raise RuntimeError(
            f"{len(issues)} H₂↔electricity coupling issue(s) — fix before solving."
        )
    else:
        print("\n✓ Coupling verified:")
        print("  • Exactly one H₂ CCGT per area (CLEVER patched or fresh fallback)")
        print("  • H₂ CCGT draws from hydrogen bus (factor[hydrogen] < 0)")
        print("  • Electrolyser draws from electricity bus ⇒ price coupling automatic")
        print("  • H₂ storage present ⇒ industrial & power-sector demand decoupled in time")
else:
    print("DemandForge H₂ coupling disabled — skipping coupling verification.")

# ── Ex-ante adequacy (peak margin check) ──────────────────────────
exante_rows = []
for country in COUNTRIES_MODELLED:
    demand_pl = demand_dict[country]
    m = compute_country_adequacy_metrics(
        country_code=country,
        model_year=MODEL_YEAR,
        clever_capacity_df=capacity_df,
        clever_non_enr_df=non_enr_df,
        electricity_demand_pl=demand_pl,
    )
    m["country"] = country
    exante_rows.append(m)

exante_df = pd.DataFrame(exante_rows).set_index("country")
print("Ex-ante adequacy screening (pre-optimisation):")
print(exante_df.round(0)) if hasattr(exante_df, 'style') else print(exante_df.round(0).to_string())

# Flag countries with negative margin
at_risk = exante_df[exante_df.get("adequacy_margin_mw", exante_df.iloc[:, -1]) < 0]
if not at_risk.empty:
    print(f"\n⚠ Countries at risk (negative margin): {at_risk.index.tolist()}")
else:
    print("\n✓ All countries have positive ex-ante margin")

# ── DIAGNOSTIC v2: wrap generate_component_table on the actual classes ──
# Old version called the method with wrong arg shape and trivially "raised"
# on everything.  This version instruments the bound class methods so we
# only see the true None returns from pommes_craft\'s real loop.
import pommes_craft.core.model as _pcm
_orig_gtfp = _pcm.EnergyModel._generate_tables_from_params

def _patched_gtfp(self, parameters, file_attr_groups):
    areas = self.areas if isinstance(self.areas, list) else list(self.areas.values())
    seen_classes = set()
    for a in areas:
        comps = getattr(a, "components", getattr(a, "_components", {}))
        comps = list(comps.values()) if isinstance(comps, dict) else list(comps)
        for c in comps:
            cls = type(c)
            for base in cls.__mro__:
                if "generate_component_table" not in base.__dict__:
                    continue
                if id(base) in seen_classes:
                    continue
                seen_classes.add(id(base))
                orig_m = base.generate_component_table
                if getattr(orig_m, "_diag_wrapped", False):
                    continue
                def make(o, cn):
                    def w(*args, **kwargs):
                        r = o(*args, **kwargs)
                        if r is None:
                            comp = args[0] if args else kwargs.get("self") or kwargs.get("component")
                            print(f"[diag] NONE  comp={getattr(comp,'name','?'):<30}  type={type(comp).__name__:<25}  base={cn}")
                        return r
                    w._diag_wrapped = True
                    return w
                base.generate_component_table = make(orig_m, base.__name__)
    print(f"[diag] wrapped generate_component_table on {len(seen_classes)} class(es)")
    return _orig_gtfp(self, parameters, file_attr_groups)

_pcm.EnergyModel._generate_tables_from_params = _patched_gtfp
print("✓ diagnostic monkey-patch v2 installed")


# ── DIAGNOSTIC v3: pinpoint non-unique MultiIndex in build_input_parameters ──
import pandas as _pd
import pommes.io.build_input_dataset as _pib
_orig_bip = _pib.build_input_parameters

def _patched_bip(config_, dict_df=None):
    parameters = config_["input"]["parameters"]
    if dict_df is None:
        from pommes.io.build_input_dataset import load_inputs_as_dict_of_df
        dict_df = load_inputs_as_dict_of_df(config_)
    hits = 0
    for key, param in parameters.items():
        try:
            df = dict_df[param["file"]]
        except KeyError:
            continue
        if param["column"] not in df.columns:
            continue
        series = df[param["column"]]
        if series.index.names[0] is None:
            continue
        idx = series.index
        if hasattr(idx, "is_unique") and not idx.is_unique:
            hits += 1
            print(f"[diag-v3] NON-UNIQUE key={key}  file={param['file']}  col={param['column']}")
            print(f"[diag-v3]   index names: {list(idx.names)}")
            dup_mask = idx.duplicated(keep=False)
            dup_df = df.loc[dup_mask].reset_index()
            print(f"[diag-v3]   {int(dup_mask.sum())} duplicate rows (showing up to 30):")
            print(dup_df.head(30).to_string())
            print("[diag-v3] ── distinct duplicate index values ──")
            print(dup_df[list(idx.names)].drop_duplicates().head(30).to_string())
    print(f"[diag-v3] total non-unique parameters: {hits}")
    return _orig_bip(config_, dict_df)

_pib.build_input_parameters = _patched_bip
try:
    import clever.runner as _cr
    if hasattr(_cr, "build_input_parameters"):
        _cr.build_input_parameters = _patched_bip
except Exception as _e:
    print(f"[diag-v3] runner patch skipped: {_e}")
print("✓ diagnostic monkey-patch v3 installed")


# ── DIAGNOSTIC v4: pinpoint NaN in pommes input params + linopy objective ──
# Wraps pommes.model.build_model.build_model so we can inspect the input xarray
# Dataset for NaN BEFORE the LP is built, and the resulting linopy model's
# objective expression AFTER, to locate where NaN coefficients originate.
import numpy as np
import pommes.model.build_model as _pbm

_orig_bm = _pbm.build_model

def _patched_bm(parameters, *args, **kwargs):
    print("[diag-v4] ── scanning input parameters for NaN ──")
    nan_vars = []
    for name in sorted(parameters.data_vars):
        da = parameters[name]
        try:
            if not np.issubdtype(da.dtype, np.number):
                continue
        except Exception:
            continue
        arr = da.values
        n_nan = int(np.isnan(arr).sum()) if arr.size else 0
        if n_nan > 0:
            nan_vars.append((name, n_nan, arr.size))
            print(f"[diag-v4]   NaN in {name}: {n_nan}/{arr.size} "
                  f"dims={da.dims} shape={da.shape}")
            # Show a few NaN coordinates
            try:
                stacked = da.stack(__z=da.dims)
                nan_mask = np.isnan(stacked.values)
                nan_coords = stacked.__z[nan_mask].values[:5]
                for coord in nan_coords:
                    print(f"[diag-v4]     e.g. {coord}")
            except Exception as _e:
                print(f"[diag-v4]     (could not extract coords: {_e})")
    if not nan_vars:
        print("[diag-v4]   no NaN in any input parameter")
    print(f"[diag-v4] total input vars with NaN: {len(nan_vars)}")

    linopy_model = _orig_bm(parameters, *args, **kwargs)

    # Inspect objective for NaN coefficients
    print("[diag-v4] ── scanning linopy objective for NaN coefficients ──")
    try:
        obj = linopy_model.objective
        expr = getattr(obj, "expression", obj)
        coeffs = getattr(expr, "coeffs", None)
        if coeffs is not None:
            cvals = np.asarray(coeffs.values) if hasattr(coeffs, "values") else np.asarray(coeffs)
            n_nan = int(np.isnan(cvals).sum())
            print(f"[diag-v4]   objective coeffs: {cvals.size} total, {n_nan} NaN")
            if n_nan > 0:
                # Report which variable labels have NaN coeffs
                vars_da = getattr(expr, "vars", None)
                try:
                    nan_mask = np.isnan(cvals)
                    nan_var_labels = np.asarray(vars_da.values)[nan_mask] if vars_da is not None else None
                    if nan_var_labels is not None:
                        uniq = np.unique(nan_var_labels)
                        print(f"[diag-v4]   distinct NaN-coeff variable labels: {uniq.size}")
                        for v in uniq[:20]:
                            # Map label back to variable name via model.variables
                            try:
                                vname = linopy_model.variables.get_name_by_label(int(v))
                                print(f"[diag-v4]     var label {int(v)} -> {vname}")
                            except Exception:
                                print(f"[diag-v4]     var label {int(v)}")
                except Exception as _e:
                    print(f"[diag-v4]   could not resolve variable names: {_e}")
    except Exception as _e:
        print(f"[diag-v4]   objective scan failed: {_e}")

    return linopy_model

_pbm.build_model = _patched_bm
# Also patch at the import site in clever.runner / build_input_dataset if used
try:
    import clever.runner as _cr
    if hasattr(_cr, "build_model"):
        _cr.build_model = _patched_bm
except Exception:
    pass
print("✓ diagnostic monkey-patch v4 installed")


def run_model_with_ramping(
    model,
    solver_name: str,
    solver_options: dict,
    year_op: int,
    ramp_rates: dict[str, tuple[float, float]],
    write_lp: bool = False,
    diagnostics_dir: Optional[Path] = None,
):
    """
    Same pipeline as run_model_without_ramping(), but applies per-tech ramp
    rates AFTER sanitisation and BEFORE build_model().

    Parameters
    ----------
    ramp_rates : dict
        Maps base technology name (lowercase) to (ramp_up_frac, ramp_down_frac).
        Values are fractions of capacity per hour (0.0–1.0).
        Techs not matched keep the default 1.0 (unrestricted).
    """
    import xarray as xr
    logger = logging.getLogger("clever.notebook")

    # Step 1: Convert pommes_craft model → POMMES parameter tables
    model.to_pommes_model()
    p = build_input_parameters(model.config, model.parameter_tables)

    # Step 2: Standard sanitisation (sets all ramps to 1.0)
    p = check_inputs(p)
    p = sanitize_absent_conversions(p)
    p = sanitize_storage_inputs(p)
    p = sanitize_transport_inputs(p)

    # Allow spillage (same as run_model_without_ramping)
    if "spillage_max_capacity" in p:
        p["spillage_max_capacity"].loc[dict(resource="electricity")] = 1e6
        if "reservoir_water" in p["spillage_max_capacity"].resource.values:
            p["spillage_max_capacity"].loc[dict(resource="reservoir_water")] = 1e6
    if "spillage_cost" in p:
        p["spillage_cost"].loc[dict(resource="electricity")] = 0.0
        if "reservoir_water" in p["spillage_cost"].resource.values:
            p["spillage_cost"].loc[dict(resource="reservoir_water")] = 0.0

    # Step 3: OVERRIDE ramp values per technology (THE KEY STEP)
    # Discover the tech dimension name
    tech_dim = "conversion_tech" if "conversion_tech" in p.dims else "conversion_technology"
    if tech_dim not in p.dims:
        logger.warning("Cannot find tech dimension in POMMES dataset — skipping ramp override")
    else:
        tech_names = list(p["conversion_ramp_up"].coords[tech_dim].values)
        logger.info("POMMES tech names: %s", tech_names)

        matched, unmatched = 0, 0
        for base_name, (ramp_up, ramp_down) in ramp_rates.items():
            # Match: exact name OR tech name contains base_name
            targets = [t for t in tech_names if base_name in t.lower()]
            if targets:
                for t in targets:
                    p["conversion_ramp_up"].loc[{tech_dim: t}] = ramp_up
                    p["conversion_ramp_down"].loc[{tech_dim: t}] = ramp_down
                    logger.info("  Ramp: %s → up=%.2f, down=%.2f", t, ramp_up, ramp_down)
                matched += 1
            else:
                unmatched += 1
        logger.info("Ramp override: %d matched, %d unmatched base names", matched, unmatched)

    # Step 3b: FORCE conversion_ramp_relative_to_capacity = True
    # Without this, POMMES may not generate ramp constraints even if ramp < 1.0.
    # check_inputs() defaults it to False; sanitize_absent_conversions() sets True
    # but as a DataArray. We ensure it's unambiguously True here.
    if "conversion_ramp_relative_to_capacity" in p:
        p["conversion_ramp_relative_to_capacity"] = xr.full_like(
            p["conversion_ramp_up"], True, dtype=bool
        )
        logger.info("Set conversion_ramp_relative_to_capacity = True (all techs)")
    else:
        p["conversion_ramp_relative_to_capacity"] = xr.full_like(
            p["conversion_ramp_up"], True, dtype=bool
        )
        logger.info("Created conversion_ramp_relative_to_capacity = True (all techs)")

    # Step 4: Build and solve
    linopy_model = build_model(p)

    logger.info("Solving with %s (ramping ENABLED)", solver_name)
    linopy_model.solve(solver_name=solver_name, **solver_options)

    status = getattr(linopy_model, "status", None)
    termination = getattr(linopy_model, "termination_condition", None)
    solution = getattr(linopy_model, "solution", None)

    # Step 5: Diagnostics
    if diagnostics_dir is not None:
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        try:
            inp_nc = diagnostics_dir / f"input_dataset_{year_op}.nc"
            if inp_nc.exists():
                inp_nc.unlink()
            p.to_netcdf(inp_nc)
        except Exception as err:
            logger.warning("Could not save input dataset: %s", err)

    # Step 6: Check for optimal solution
    if solution is not None and str(termination).lower() == "optimal":
        model.set_all_results(linopy_model)

        if diagnostics_dir is not None:
            try:
                sol_nc = diagnostics_dir / f"solution_{year_op}.nc"
                if sol_nc.exists():
                    sol_nc.unlink()
                linopy_model.solution.to_netcdf(sol_nc)
                duals = getattr(getattr(linopy_model, "constraints", None), "dual", None)
                if duals is not None:
                    dual_nc = diagnostics_dir / f"dual_{year_op}.nc"
                    if dual_nc.exists():
                        dual_nc.unlink()
                    duals.to_netcdf(dual_nc)
            except Exception as err:
                logger.warning("Could not save solution/duals: %s", err)

        return linopy_model

    raise RuntimeError(f"Model did not converge: status={status}, termination={termination}")

print("run_model_with_ramping() defined.")

# ══════════════════════════════════════════════════════════════════════
# 4.0  R0 per-country override kwargs (sub-tasks 1, 2, 3)
# ══════════════════════════════════════════════════════════════════════
# Builds the r0_overrides_kwargs payload from the CSVs in TABLES_DIR
# (per-scenario tables/<SCENARIO>/) and DemandForge's live H₂ demand for
# the active bundle.  See `notes/r0_override_contract.md` for the
# Layer-1 / Layer-2 ownership map.
from pommes_eur.providers.clever.calibration_inputs import build_r0_overrides_kwargs

# Pull electrolyser CAPEX from the scenario-name parser (_elNNN suffix).
# Default 500 €/kW when no suffix; _el700 → 700, _el900 → 900, etc.
from clever.constants import _ELECTROLYSER_CAPEX_EUR_PER_KW as _SCEN_EL_CAPEX
r0_kwargs = build_r0_overrides_kwargs(
    bundle_name=DEMANDFORGE_BUNDLE,
    countries=COUNTRIES_MODELLED,
    model_year=MODEL_YEAR,
    tables_dir=TABLES_DIR,
    electrolyser_invest_cost_eur_per_kw=_SCEN_EL_CAPEX,
)

print(f"R0 overrides ready: "
      f"{len(r0_kwargs['electrolyser_min_bounds_gw'])} electrolyser min-bound countries, "
      f"{len(r0_kwargs['storage_capex_by_area'])} storage CAPEX rows, "
      f"{len(r0_kwargs['storage_caps_by_area'])} storage cap rows, "
      f"electrolyser CAPEX={r0_kwargs['electrolyser_invest_cost_eur_per_kw']:.0f} €/kW")

# Sub-task 5: disable electrolyser sovereignty min bounds for the
# "policy_re_noMin" SCENARIO. Same demand / VRE / storage / CAPEX as
# policy_re, only the per-country floor is removed. Must come AFTER the
# print above, which would crash on len(None).
if NO_MIN_BOUNDS:
    r0_kwargs["electrolyser_min_bounds_gw"] = None
    print("Sub-task 5: electrolyser sovereignty min bounds DISABLED (policy_re_noMin)")

# 2026-05-28: also disable the electrolyser floor for any scenario carrying
# the `_noElecFloor` suffix (decoupled from policy_re_noMin so it composes
# with any base scenario). Used to evaluate the LP's H₂-supply choice when
# BECCS via ATR_CCS/SMR_CCS bio_mode is available.
from clever.constants import _NO_ELEC_FLOOR
if _NO_ELEC_FLOOR and r0_kwargs.get("electrolyser_min_bounds_gw") is not None:
    r0_kwargs["electrolyser_min_bounds_gw"] = None
    print("_noElecFloor: electrolyser sovereignty min bounds DISABLED — "
          "LP is free to allocate H₂ production across any local tech.")

# ══════════════════════════════════════════════════════════════════════
# 4.1  Build + solve
# ══════════════════════════════════════════════════════════════════════
solver_options = build_solver_options(solver_name=SOLVER)

# Keep pommes at INFO so build_model() progress is visible.
# Only suppress linopy internals and gurobipy parameter dumps.
logging.getLogger("linopy").setLevel(logging.WARNING)
logging.getLogger("gurobipy").setLevel(logging.WARNING)
# pommes stays at INFO → you'll see constraint-building progress

print(f"Solver: {SOLVER} | Ramping: {'ENABLED' if RAMPING_ENABLED else 'DISABLED'}")
print(f"Diagnostics: {DIAG_DIR}")
print(f"Building model... (this may take 10-20 min for {len(COUNTRIES_MODELLED)} countries)")

if RAMPING_ENABLED:
    # NOTE: R0 overrides currently only plumbed through run_model_without_ramping.
    # If you re-enable ramping, mirror the r0_overrides_kwargs argument into
    # run_model_with_ramping (one-line edit to its signature in clever/runner.py)
    # and forward it here.
    linopy_model = run_model_with_ramping(
        model=model,
        solver_name=SOLVER,
        solver_options=solver_options,
        year_op=MODEL_YEAR,
        ramp_rates=RAMP_RATES_BASE,
        write_lp=False,
        diagnostics_dir=DIAG_DIR,
    )
else:
    linopy_model = run_model_without_ramping(
        model=model,
        solver_name=SOLVER,
        solver_options=solver_options,
        year_op=MODEL_YEAR,
        write_lp=False,
        diagnostics_dir=DIAG_DIR,
        r0_overrides_kwargs=r0_kwargs,
    )

# Restore logging after solve
logging.getLogger("linopy").setLevel(logging.WARNING)
logging.getLogger("gurobipy").setLevel(logging.WARNING)

obj_val = getattr(linopy_model, "objective_value", None)
print(f"\n✓ Solve complete | Objective value: {obj_val:,.0f} EUR" if obj_val else "\n✓ Solve complete")


# ── Load xarray datasets for deep analysis ────────────────────────
# These are written by run_model_with_ramping() diagnostics.
# Load FIRST so downstream cells always have sol_ds / dual_ds / inp_ds.
sol_path  = DIAG_DIR / f"solution_{MODEL_YEAR}.nc"
dual_path = DIAG_DIR / f"dual_{MODEL_YEAR}.nc"
inp_path  = DIAG_DIR / f"input_dataset_{MODEL_YEAR}.nc"

sol_ds  = xr.open_dataset(sol_path)  if sol_path.exists()  else None
dual_ds = xr.open_dataset(dual_path) if dual_path.exists() else None
inp_ds  = xr.open_dataset(inp_path)  if inp_path.exists()  else None

print("── Diagnostics files ──")
for label, path, ds in [("Solution", sol_path, sol_ds),
                         ("Duals", dual_path, dual_ds),
                         ("Input", inp_path, inp_ds)]:
    if path.exists():
        size_mb = path.stat().st_size / 1e6
        n_vars = len(ds.data_vars) if ds else "?"
        print(f"  {label:10s}: {path.name} ({size_mb:.1f} MB, {n_vars} variables) ✓")
    else:
        print(f"  {label:10s}: NOT FOUND ✗")

if sol_ds is not None:
    print(f"\nSolution variables: {list(sol_ds.data_vars)[:10]}...")
if dual_ds is not None:
    print(f"Dual variables:     {list(dual_ds.data_vars)[:10]}...")

# ── Extract prices and capacities from model ──────────────────────
# Wrapped in try/except so a single export failure doesn't block everything.
prices_df   = pd.DataFrame()
conv_cap_df = pd.DataFrame()
stor_cap_df = pd.DataFrame()
stor_pow_df = pd.DataFrame()

try:
    prices_df = export_prices(model, MODEL_YEAR)
except Exception as e:
    print(f"⚠ export_prices failed: {e}")

try:
    conv_cap_df = export_conversion_capacity(model, MODEL_YEAR)
except Exception as e:
    print(f"⚠ export_conversion_capacity failed: {e}")

try:
    stor_cap_df = export_storage_capacity(model, MODEL_YEAR)
except Exception as e:
    print(f"⚠ export_storage_capacity failed: {e}")

try:
    stor_pow_df = export_storage_power_capacity(model, MODEL_YEAR)
except Exception as e:
    print(f"⚠ export_storage_power_capacity failed: {e}")

print(f"\nPrices:             {len(prices_df):>6} rows")
print(f"Conversion cap:     {len(conv_cap_df):>6} rows")
print(f"Storage energy cap: {len(stor_cap_df):>6} rows")
print(f"Storage power cap:  {len(stor_pow_df):>6} rows")

# ══════════════════════════════════════════════════════════════════════
# 5.1  HELPER: extract load shedding and compute adequacy per country
# ══════════════════════════════════════════════════════════════════════

LS_VAR   = "operation_load_shedding_power"
DUAL_VAR = "operation_adequacy_constraint"
SHEDDING_THRESHOLD = DEFAULT_LOAD_SHEDDING_COST * 0.9  # 27,000 EUR/MWh

def compute_adequacy_from_solution(sol_ds, dual_ds, prices_df, countries, year):
    """Compute LOLE, ENS, and related metrics for each country."""
    results = {}
    for area in countries:
        r = {"area": area, "ens_gwh": 0, "lole_hours": 0, "peak_shedding_mw": 0,
             "dual_max": 0, "dual_at_voll": 0, "mean_price": 0}

        # ENS from solution
        if sol_ds is not None and LS_VAR in sol_ds:
            ls_da = sol_ds[LS_VAR]
            try:
                ls_area = ls_da.sel(area=area)
                if "resource" in ls_area.dims:
                    res_vals = [str(v) for v in ls_area.coords["resource"].values]
                    elec_res = [v for v in res_vals if "elec" in v.lower()]
                    if elec_res:
                        ls_area = ls_area.sel(resource=elec_res[0])
                if "year_op" in ls_area.dims:
                    ls_area = ls_area.sel(year_op=year)
                ls_vals = ls_area.values.flatten()
                ls_vals = np.where(np.isnan(ls_vals), 0, ls_vals)
                r["ens_gwh"] = ls_vals.sum() / 1000  # MWh → GWh
                r["lole_hours"] = int((ls_vals > 0.1).sum())
                r["peak_shedding_mw"] = float(ls_vals.max())
            except Exception:
                pass

        # Dual variable (shadow price at shedding constraint)
        if dual_ds is not None and DUAL_VAR in dual_ds:
            try:
                d = dual_ds[DUAL_VAR].sel(area=area)
                if "year_op" in d.dims:
                    d = d.sel(year_op=year)
                dv = d.values.flatten()
                dv = np.where(np.isnan(dv), 0, dv)
                r["dual_max"] = float(np.abs(dv).max())
                r["dual_at_voll"] = int((np.abs(dv) >= SHEDDING_THRESHOLD).sum())
            except Exception:
                pass

        # Mean price
        area_prices = prices_df[prices_df["area"] == area]["value"]
        r["mean_price"] = float(area_prices.mean()) if len(area_prices) > 0 else 0

        results[area] = r
    return results

adequacy_results = compute_adequacy_from_solution(
    sol_ds, dual_ds, prices_df, COUNTRIES_MODELLED, MODEL_YEAR
)

# Display
adeq_df = pd.DataFrame(adequacy_results.values()).set_index("area")
print("Adequacy results:")
print(adeq_df.round(2)) if hasattr(adeq_df, 'style') else print(adeq_df.round(2).to_string())

total_ens = adeq_df["ens_gwh"].sum()
total_lole_max = adeq_df["lole_hours"].max()
print(f"\nSystem total ENS: {total_ens:.2f} GWh | Max country LOLE: {total_lole_max} hours")

# ── Load-shedding heatmap (hour-of-day vs day-of-year) ────────────
if sol_ds is not None and LS_VAR in sol_ds:
    ls_da = sol_ds[LS_VAR]

    # Aggregate across all countries
    all_ls = np.zeros(8760)
    for area in COUNTRIES_MODELLED:
        try:
            ls_a = ls_da.sel(area=area)
            if "resource" in ls_a.dims:
                res_vals = [str(v) for v in ls_a.coords["resource"].values]
                elec = [v for v in res_vals if "elec" in v.lower()]
                if elec:
                    ls_a = ls_a.sel(resource=elec[0])
            if "year_op" in ls_a.dims:
                ls_a = ls_a.sel(year_op=MODEL_YEAR)
            vals = ls_a.values.flatten()[:8760]
            all_ls[:len(vals)] += np.nan_to_num(vals)
        except Exception:
            continue

    # Reshape to (365, 24) for heatmap
    ls_matrix = all_ls[:8760].reshape(365, 24)

    fig, ax = plt.subplots(figsize=(16, 6))
    im = ax.imshow(ls_matrix.T, aspect="auto", cmap="Reds", origin="lower",
                   interpolation="nearest")
    ax.set_xlabel("Day of year")
    ax.set_ylabel("Hour of day")
    ax.set_title("System-wide load shedding (MW) — hour of day vs day of year")
    plt.colorbar(im, ax=ax, label="Load shedding (MW)")
    fig.savefig(FIG_DIR / "load_shedding_heatmap.png")
    plt.show()
else:
    print("No load-shedding data available in solution dataset.")

# ── Final adequacy dashboard ───────────────────────────────────────
dash_rows = []
for area in COUNTRIES_MODELLED:
    r = adequacy_results.get(area, {})
    dash_rows.append({
        "Country": area,
        "ENS (GWh)": round(r.get("ens_gwh", 0), 2),
        "LOLE (h)": r.get("lole_hours", 0),
        "Peak LS (MW)": round(r.get("peak_shedding_mw", 0), 0),
        "Max dual (EUR/MWh)": round(r.get("dual_max", 0), 0),
        "Hours at VoLL": r.get("dual_at_voll", 0),
        "Mean price (EUR/MWh)": round(r.get("mean_price", 0), 1),
    })
dashboard = pd.DataFrame(dash_rows)
print("ADEQUACY DASHBOARD")
print("=" * 80)
print(dashboard) if hasattr(dashboard, 'style') else print(dashboard.to_string(index=False))

# ══════════════════════════════════════════════════════════════════════
# 6.0  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

TECH_COLORS = {
    "Solar": "#FFD700", "Wind_Onshore": "#4CAF50", "Wind_Offshore": "#1565C0",
    "RoR_Hydro": "#00BCD4", "Reservoir_Hydro_Plant": "#0097A7",
    "Gas": "#FF5722", "Hydrogen_power_plant": "#9C27B0",
    "Biomass": "#795548", "Nuclear": "#E91E63", "Coal": "#424242",
    "Waste": "#607D8B", "Other": "#9E9E9E", "Load_Shedding": "#F44336",
}
TECH_ORDER = list(TECH_COLORS.keys())


def _guess_dims(da, kind):
    """Heuristic to identify dimension names in an xarray DataArray."""
    mapping = {}
    for d in da.dims:
        dl = d.lower()
        if any(k in dl for k in ["area", "zone", "country", "node"]):
            mapping["area"] = d
        elif any(k in dl for k in ["hour", "time", "step"]):
            mapping["hour"] = d
        elif any(k in dl for k in ["resource", "commodity", "carrier"]):
            mapping["resource"] = d
        elif any(k in dl for k in ["year_op", "year"]):
            mapping["year"] = d
        elif kind == "conversion" and any(k in dl for k in ["conversion_tech", "tech"]):
            mapping["tech"] = d
        elif kind == "storage" and any(k in dl for k in ["storage_tech", "tech"]):
            mapping["tech"] = d
        elif kind == "transport" and any(k in dl for k in ["transport", "link", "line"]):
            mapping["link"] = d
    return mapping


def extract_hourly_dispatch(model, sol_ds, year_op: int) -> pd.DataFrame:
    """Extract hourly dispatch (MW) from POMMES results, with sol.nc fallback."""
    df = pd.DataFrame()
    # Attempt 1: model API
    try:
        gen = model.get_results("operation", "conversion_power")
        df = gen.to_pandas() if hasattr(gen, "to_pandas") else pd.DataFrame(gen)
    except Exception:
        pass
    if df.empty:
        try:
            gen = model.get_results("operation", "production")
            df = gen.to_pandas() if hasattr(gen, "to_pandas") else pd.DataFrame(gen)
        except Exception:
            pass

    # Attempt 2: sol.nc fallback
    if df.empty and sol_ds is not None:
        CONV_VAR = "operation_conversion_power"
        if CONV_VAR in sol_ds:
            conv_da = sol_ds[CONV_VAR]
            dims = _guess_dims(conv_da, "conversion")
            tech_dim = dims.get("tech")
            rows = []
            if tech_dim:
                for tech in conv_da.coords[tech_dim].values:
                    for area in COUNTRIES_MODELLED:
                        try:
                            sel = {tech_dim: tech}
                            if "area" in dims:
                                sel[dims["area"]] = area
                            if "year" in dims:
                                sel[dims["year"]] = year_op
                            d = conv_da.sel(sel)
                            if "resource" in dims:
                                for r in d.coords[dims["resource"]].values:
                                    if "elec" in str(r).lower():
                                        d = d.sel({dims["resource"]: r})
                                        break
                            vals = d.values.flatten()
                            for h, v in enumerate(vals):
                                if not np.isnan(v) and abs(v) > 0.01:
                                    rows.append({"hour": h, "area": area,
                                                 "tech": str(tech), "value": float(v)})
                        except Exception:
                            continue
            if rows:
                df = pd.DataFrame(rows)

    # Normalise columns
    if not df.empty:
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if "hour" in cl or "time" in cl or "step" in cl:
                col_map[c] = "hour"
            elif "area" in cl or "zone" in cl or "country" in cl:
                col_map[c] = "area"
            elif "tech" in cl or "conversion" in cl:
                col_map[c] = "tech"
            elif "value" in cl or "power" in cl:
                col_map[c] = "value"
        df = df.rename(columns=col_map)
        for col in ["hour", "area", "tech", "value"]:
            if col not in df.columns:
                df[col] = np.nan if col == "value" else ""

    return df


def extract_storage_soc(model, sol_ds, year_op: int) -> pd.DataFrame:
    """Extract storage state-of-charge (MWh), with sol.nc fallback."""
    df = pd.DataFrame()
    for rt, rn in [("operation", "storage_level"), ("operation", "energy_level"),
                   ("operation", "state_of_charge")]:
        try:
            soc = model.get_results(rt, rn)
            df = soc.to_pandas() if hasattr(soc, "to_pandas") else pd.DataFrame(soc)
            if not df.empty:
                break
        except Exception:
            continue

    if df.empty and sol_ds is not None:
        STOR_VAR = "operation_storage_level"
        if STOR_VAR in sol_ds:
            stor_da = sol_ds[STOR_VAR]
            dims = _guess_dims(stor_da, "storage")
            tech_dim = dims.get("tech")
            rows = []
            if tech_dim:
                for tech in stor_da.coords[tech_dim].values:
                    for area in COUNTRIES_MODELLED:
                        try:
                            sel = {tech_dim: tech}
                            if "area" in dims:
                                sel[dims["area"]] = area
                            if "year" in dims:
                                sel[dims["year"]] = year_op
                            d = stor_da.sel(sel)
                            vals = d.values.flatten()
                            for h, v in enumerate(vals):
                                if not np.isnan(v):
                                    rows.append({"hour": h, "area": area,
                                                 "tech": str(tech), "value": float(v)})
                        except Exception:
                            continue
            if rows:
                df = pd.DataFrame(rows)
    return df


def extract_flows(model, sol_ds, year_op: int) -> pd.DataFrame:
    """Extract hourly cross-border electricity flows, with sol.nc fallback."""
    df = pd.DataFrame()
    for rt, rn in [("operation", "transport_power"), ("operation", "flow"),
                   ("operation", "link_flow")]:
        try:
            fl = model.get_results(rt, rn)
            df = fl.to_pandas() if hasattr(fl, "to_pandas") else pd.DataFrame(fl)
            if not df.empty:
                break
        except Exception:
            continue

    if df.empty and sol_ds is not None:
        FLOW_VAR = "operation_transport_power"
        if FLOW_VAR in sol_ds:
            flow_da = sol_ds[FLOW_VAR]
            dims = _guess_dims(flow_da, "transport")
            link_dim = dims.get("link")
            rows = []
            if link_dim:
                for link in flow_da.coords[link_dim].values:
                    try:
                        f = flow_da.sel({link_dim: link})
                        if "resource" in dims:
                            for r in f.coords[dims["resource"]].values:
                                if "elec" in str(r).lower():
                                    f = f.sel({dims["resource"]: r})
                                    break
                        if "year" in dims:
                            f = f.sel({dims["year"]: year_op})
                        vals = f.values.flatten()
                        for h, v in enumerate(vals):
                            if not np.isnan(v) and abs(v) > 0.01:
                                rows.append({"hour": h, "link": str(link),
                                             "value": float(v)})
                    except Exception:
                        continue
            if rows:
                df = pd.DataFrame(rows)
    return df

print("Helper functions defined: extract_hourly_dispatch, extract_storage_soc, extract_flows")

if conv_cap_df.empty:
    print('⚠ No conversion capacity data — skipping capacity plots')
else:
    # ── Conversion capacity (MW → GW) ─────────────────────────────────
    cap_pivot = conv_cap_df.pivot_table(
        index="area", columns="name", values="value", aggfunc="sum"
    ).fillna(0) / 1000
    print("Optimal conversion capacity (GW):")
    print(cap_pivot.round(2)) if hasattr(cap_pivot, 'style') else print(cap_pivot.round(2).to_string())
    
    # ── Storage power capacity (MW → GW) ─────────────────────────────
    stor_piv = stor_pow_df.pivot_table(
        index="area", columns="name", values="value", aggfunc="sum"
    ).fillna(0) / 1000
    print("\nStorage power capacity (GW):")
    print(stor_piv.round(2)) if hasattr(stor_piv, 'style') else print(stor_piv.round(2).to_string())
    
    # ── Storage energy capacity (MWh → GWh) ──────────────────────────
    stor_en_piv = stor_cap_df.pivot_table(
        index="area", columns="name", values="value", aggfunc="sum"
    ).fillna(0) / 1000
    print("\nStorage energy capacity (GWh):")
    print(stor_en_piv.round(2)) if hasattr(stor_en_piv, 'style') else print(stor_en_piv.round(2).to_string())

# ── Extract dispatch ───────────────────────────────────────────────
dispatch_df = extract_hourly_dispatch(model, sol_ds, MODEL_YEAR) if sol_ds is not None else pd.DataFrame()
print(f"Dispatch: {len(dispatch_df)} rows | techs: {dispatch_df['tech'].unique().tolist() if not dispatch_df.empty else []}")

def plot_system_dispatch(dispatch_df, demand_dict, countries, year, focus_week=None, title_suffix=""):
    """Plot stacked dispatch for the system (sum over all countries)."""
    if dispatch_df.empty:
        print("No dispatch data to plot.")
        return

    df = dispatch_df[dispatch_df["area"].isin(countries)].copy()
    piv = df.pivot_table(index="hour", columns="tech", values="value", aggfunc="sum").fillna(0)
    piv = piv.reindex(columns=[t for t in TECH_ORDER if t in piv.columns], fill_value=0)

    # Build system demand
    sys_demand = np.zeros(8760)
    for cc in countries:
        if cc in demand_dict:
            d = demand_dict[cc]["demand"].to_numpy()
            sys_demand[:len(d)] += d

    hours = np.arange(8760)
    if focus_week is not None:
        h0, h1 = (focus_week - 1) * 168, focus_week * 168
        piv = piv.loc[(piv.index >= h0) & (piv.index < h1)]
        sys_demand = sys_demand[h0:h1]
        hours = np.arange(h0, h1)

    fig, ax = plt.subplots(figsize=(16, 6))
    bottom = np.zeros(len(piv))
    for tech in piv.columns:
        vals = piv[tech].values
        color = TECH_COLORS.get(tech, "#999999")
        ax.fill_between(piv.index, bottom, bottom + vals, label=tech,
                        color=color, alpha=0.85, linewidth=0)
        bottom += vals
    ax.plot(hours[:len(sys_demand)], sys_demand / 1000, "k-", lw=1.2, label="Demand")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Power (GW)")
    title = f"System dispatch — CLEVER {year}{title_suffix}"
    if focus_week:
        title += f" (week {focus_week})"
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=7, ncol=3)
    fig.savefig(FIG_DIR / f"dispatch{'_w' + str(focus_week) if focus_week else ''}{title_suffix.replace(' ','_')}.png")
    plt.show()

# Full year
plot_system_dispatch(dispatch_df, demand_dict, COUNTRIES_MODELLED, MODEL_YEAR)
# Winter peak week
plot_system_dispatch(dispatch_df, demand_dict, COUNTRIES_MODELLED, MODEL_YEAR, focus_week=4)
# Summer trough
plot_system_dispatch(dispatch_df, demand_dict, COUNTRIES_MODELLED, MODEL_YEAR, focus_week=30)

if prices_df.empty:
    print('⚠ No price data — skipping price analysis')
    price_stats = pd.DataFrame()
else:
    # ── Price statistics ───────────────────────────────────────────────
    price_stats = prices_df.groupby("area")["value"].agg(["mean", "median", "std", "min", "max"])
    print("Price statistics (EUR/MWh):")
    print(price_stats.round(1)) if hasattr(price_stats, 'style') else print(price_stats.round(1).to_string())
    
    # ── Price duration curve ──────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(12, 5))
    for area in COUNTRIES_MODELLED:
        ap = prices_df[prices_df["area"] == area]["value"].sort_values(ascending=False).reset_index(drop=True)
        pct = np.arange(1, len(ap) + 1) / len(ap) * 100
        ax.plot(pct, ap.values, label=area, alpha=0.8, lw=1)
    ax.set_xlabel("% of hours")
    ax.set_ylabel("EUR/MWh")
    ax.set_title("Price duration curves")
    ax.set_ylim(bottom=-10, top=min(500, price_stats["max"].max() * 1.1))
    ax.legend(fontsize=8, ncol=3)
    fig.savefig(FIG_DIR / "price_duration_curves.png")
    plt.show()
    
    # ── Scarcity hours ────────────────────────────────────────────────
    scarcity_hours = prices_df[prices_df["value"] >= SHEDDING_THRESHOLD].groupby("area").size()
    zero_hours = prices_df[prices_df["value"] <= 1.0].groupby("area").size()
    print(f"\nHours at scarcity price (>={SHEDDING_THRESHOLD:.0f} EUR/MWh):")
    print(scarcity_hours if len(scarcity_hours) > 0 else "  None")
    print(f"\nHours near zero price (<=1 EUR/MWh):")
    print(zero_hours if len(zero_hours) > 0 else "  None")

if prices_df.empty:
    print('⚠ No price data — skipping price heatmaps')
else:
    # ── Price heatmaps for top-3 countries ─────────────────────────────
    top3 = price_stats.nlargest(3, "mean").index.tolist()
    
    fig, axes = plt.subplots(1, len(top3), figsize=(6 * len(top3), 5), sharey=True)
    if len(top3) == 1:
        axes = [axes]
    
    for ax, area in zip(axes, top3):
        ap = prices_df[prices_df["area"] == area]["value"].values
        if len(ap) >= 8760:
            mat = ap[:8760].reshape(365, 24)
            im = ax.imshow(mat.T, aspect="auto", cmap="YlOrRd", origin="lower",
                           interpolation="nearest", vmin=0, vmax=min(200, mat.max()))
            ax.set_xlabel("Day of year")
            ax.set_title(f"{area}")
            if ax == axes[0]:
                ax.set_ylabel("Hour of day")
    
    plt.suptitle("Electricity prices (EUR/MWh) — hour of day vs day of year", y=1.02)
    plt.colorbar(im, ax=axes, label="EUR/MWh", shrink=0.8)
    fig.savefig(FIG_DIR / "price_heatmaps.png")
    plt.show()

# ── Check flexibility activation ───────────────────────────────────
flex_active = False
if inp_ds is not None:
    # flexibility_demand may be a multi-dimensional DataArray, not a scalar.
    # Check if ANY value is > 0 across all dims.
    fd = inp_ds.get("flexibility_demand")
    if fd is not None:
        fd_vals = fd.values.flatten()
        fd_max = float(fd_vals[~np.isnan(fd_vals)].max()) if fd_vals[~np.isnan(fd_vals)].size > 0 else 0
        if fd_max > 0:
            flex_active = True
            print(f"Flexibility is ACTIVE (max demand fraction across areas = {fd_max:.4f})")
            print(f"  Variable shape: {fd.dims} → {fd.shape}")
        else:
            print("Flexibility is INACTIVE (all flexibility_demand values are 0)")
    else:
        # Fallback: check if any flex variable exists in inp_ds
        flex_vars = [v for v in inp_ds.data_vars if "flex" in v.lower()]
        if flex_vars:
            print(f"flexibility_demand not found, but flex-related vars exist: {flex_vars}")
            # Check if flexibility_conservation_hrs > 0 as proxy
            fc = inp_ds.get("flexibility_conservation_hrs")
            if fc is not None and float(np.nanmax(fc.values)) > 0:
                flex_active = True
                print(f"  → Flexibility appears active (conservation_hrs > 0)")
        else:
            print("Flexibility is INACTIVE (no flex variables in input dataset)")
else:
    print("No input dataset loaded — cannot check flexibility status")

# ── Flexibility usage from solution ───────────────────────────────
FLEX_VAR = "operation_flexible_demand_power"
if flex_active and sol_ds is not None:
    # Try multiple possible variable names
    flex_var_name = None
    for candidate in [FLEX_VAR, "operation_flexibility_power", "flexibility_power"]:
        if candidate in sol_ds:
            flex_var_name = candidate
            break
    
    if flex_var_name is None:
        # Search for any flex-related operation variable
        flex_candidates = [v for v in sol_ds.data_vars if "flex" in v.lower()]
        print(f"Standard flex variable not found. Candidates: {flex_candidates}")
        if flex_candidates:
            flex_var_name = flex_candidates[0]
    
    if flex_var_name:
        flex_da = sol_ds[flex_var_name]
        print(f"\nFlexible demand variable: {flex_var_name}")
        print(f"  Dims: {flex_da.dims}, Shape: {flex_da.shape}")

        # Per-country flex activation summary
        flex_summary = []
        area_dim = None
        for d in flex_da.dims:
            if "area" in d.lower():
                area_dim = d
                break
        
        if area_dim:
            for area in COUNTRIES_MODELLED:
                try:
                    fa = flex_da.sel({area_dim: area})
                    if "year_op" in fa.dims:
                        fa = fa.sel(year_op=MODEL_YEAR)
                    if "resource" in fa.dims:
                        res = [str(v) for v in fa.coords["resource"].values]
                        elec = [v for v in res if "elec" in v.lower()]
                        if elec:
                            fa = fa.sel(resource=elec[0])
                    vals = fa.values.flatten()
                    vals = np.where(np.isnan(vals), 0, vals)
                    flex_summary.append({
                        "Area": area,
                        "Mean flex (MW)": np.mean(vals),
                        "Max flex up (MW)": np.max(vals),
                        "Max flex down (MW)": np.min(vals),
                        "Active hours": int((np.abs(vals) > 0.1).sum()),
                    })
                except Exception as e:
                    print(f"  Warning: could not extract flex for {area}: {e}")
                    continue
        
        if flex_summary:
            flex_df = pd.DataFrame(flex_summary)
            print("\nFlexibility activation summary:")
            print(flex_df.round(1)) if hasattr(flex_df, 'style') else print(flex_df.round(1).to_string())
        else:
            print("\nNo per-country flex data could be extracted.")
    else:
        print("\nNo flexibility operation variable found in solution dataset.")
elif flex_active:
    print("\nFlexible demand active in inputs but solution dataset not available.")

if sol_ds is None or inp_ds is None:
    print('⚠ No solution/input data — skipping ramping analysis')
else:
    # ── Ramping analysis: detect hours where ramp constraints bind ────
    CONV_VAR = "operation_conversion_power"
    
    if sol_ds is not None and CONV_VAR in sol_ds and RAMPING_ENABLED:
        conv_da = sol_ds[CONV_VAR]
        dims = _guess_dims(conv_da, "conversion")
        tech_dim = dims.get("tech")
    
        if tech_dim:
            print("Ramping analysis: detecting binding ramp constraints")
            print("=" * 70)
    
            ramp_binding = []
            for tech in conv_da.coords[tech_dim].values:
                tech_lower = str(tech).lower()
                # Find the applicable ramp rate
                ramp_rate = 1.0
                for base_name, (ru, rd) in RAMP_RATES_BASE.items():
                    if base_name in tech_lower:
                        ramp_rate = ru
                        break
    
                if ramp_rate >= 0.99:  # Skip techs with unrestricted ramp
                    continue
    
                for area in COUNTRIES_MODELLED:
                    try:
                        sel = {tech_dim: tech}
                        if "area" in dims:
                            sel[dims["area"]] = area
                        if "year" in dims:
                            sel[dims["year"]] = MODEL_YEAR
                        d = conv_da.sel(sel)
                        if "resource" in dims:
                            for r in d.coords[dims["resource"]].values:
                                if "elec" in str(r).lower():
                                    d = d.sel({dims["resource"]: r})
                                    break
                        vals = d.values.flatten()
    
                        # Get capacity for this tech/area from inp_ds
                        cap = 0
                        if inp_ds is not None and "conversion_power_capacity_max" in inp_ds:
                            try:
                                cap_da = inp_ds["conversion_power_capacity_max"]
                                cap_sel = {k: v for k, v in sel.items()
                                           if k in cap_da.dims}
                                cap = float(cap_da.sel(cap_sel).values)
                            except Exception:
                                pass
    
                        if cap <= 0:
                            continue
    
                        # Compute hour-to-hour ramps
                        ramps = np.diff(vals)
                        max_ramp_allowed = ramp_rate * cap
                        binding_up = (ramps >= max_ramp_allowed * 0.95).sum()
                        binding_down = (ramps <= -max_ramp_allowed * 0.95).sum()
    
                        if binding_up + binding_down > 0:
                            ramp_binding.append({
                                "Tech": str(tech), "Area": area,
                                "Ramp rate": ramp_rate,
                                "Capacity (MW)": cap,
                                "Binding up (h)": int(binding_up),
                                "Binding down (h)": int(binding_down),
                            })
                    except Exception:
                        continue
    
            if ramp_binding:
                ramp_df = pd.DataFrame(ramp_binding)
                print("\nBinding ramp constraints (>95% of limit):")
                print(ramp_df) if hasattr(ramp_df, 'style') else print(ramp_df.to_string())
            else:
                print("\nNo binding ramp constraints detected.")
        else:
            print("Tech dimension not found in conversion power variable.")
    elif not RAMPING_ENABLED:
        print("Ramping is DISABLED in this run — no ramp constraint analysis.")
    else:
        print("Conversion power variable not found in solution dataset.")

# ── Residual load = demand - VRE generation ───────────────────────
if not dispatch_df.empty:
    vre_techs = ["Solar", "Wind_Onshore", "Wind_Offshore", "RoR_Hydro"]
    vre_dispatch = dispatch_df[dispatch_df["tech"].isin(vre_techs)]
    vre_by_hour = vre_dispatch.groupby("hour")["value"].sum()

    # System demand
    sys_demand = np.zeros(8760)
    for cc in COUNTRIES_MODELLED:
        if cc in demand_dict:
            d = demand_dict[cc]["demand"].to_numpy()
            sys_demand[:len(d)] += d

    # Residual load
    residual = pd.Series(sys_demand, name="residual_mw")
    for h in vre_by_hour.index:
        if h < 8760:
            residual.iloc[int(h)] -= vre_by_hour[h]

    # Duration curve
    residual_sorted = residual.sort_values(ascending=False).reset_index(drop=True)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Left: duration curve
    axes[0].plot(residual_sorted.values / 1000, lw=1, color="darkred")
    axes[0].axhline(0, color="gray", ls="--", lw=0.8)
    axes[0].set_xlabel("Hours (sorted)")
    axes[0].set_ylabel("Residual load (GW)")
    axes[0].set_title("Residual load duration curve")

    # Right: hourly ramps in residual load
    ramps = np.diff(residual.values)
    axes[1].hist(ramps / 1000, bins=100, color="steelblue", alpha=0.7, edgecolor="none")
    axes[1].set_xlabel("Hour-to-hour ramp (GW/h)")
    axes[1].set_ylabel("Frequency")
    axes[1].set_title("Residual load ramp distribution")

    plt.tight_layout()
    fig.savefig(FIG_DIR / "residual_load_analysis.png")
    plt.show()

    print(f"\nResidual load stats:")
    print(f"  Peak: {residual.max()/1000:.1f} GW | Min: {residual.min()/1000:.1f} GW")
    print(f"  Hours negative (VRE > demand): {(residual < 0).sum()}")
    print(f"  Max ramp up: {ramps.max()/1000:.1f} GW/h | Max ramp down: {ramps.min()/1000:.1f} GW/h")
else:
    print("No dispatch data available for residual load analysis.")

# ── Extract storage SOC ────────────────────────────────────────────
soc_df = extract_storage_soc(model, sol_ds, MODEL_YEAR) if sol_ds is not None else pd.DataFrame()
if not soc_df.empty:
    # Aggregate by tech across all areas
    if "tech" in soc_df.columns and "hour" in soc_df.columns:
        soc_by_tech = soc_df.groupby(["hour", "tech"])["value"].sum().reset_index()
        techs = soc_by_tech["tech"].unique()

        fig, axes = plt.subplots(len(techs), 1, figsize=(14, 4 * len(techs)), sharex=True)
        if len(techs) == 1:
            axes = [axes]

        for ax, tech in zip(axes, techs):
            t_data = soc_by_tech[soc_by_tech["tech"] == tech]
            ax.plot(t_data["hour"], t_data["value"] / 1000, lw=0.8)
            ax.set_ylabel("SOC (GWh)")
            ax.set_title(f"Storage SOC — {tech}")

        axes[-1].set_xlabel("Hour")
        plt.tight_layout()
        fig.savefig(FIG_DIR / "storage_soc_profiles.png")
        plt.show()
    else:
        print("SOC data available but columns don't match expected format.")
        print(f"  Columns: {soc_df.columns.tolist()}")
else:
    print("No storage SOC data available.")

# ── Extract cross-border flows ─────────────────────────────────────
flow_df = extract_flows(model, sol_ds, MODEL_YEAR) if sol_ds is not None else pd.DataFrame()
if not flow_df.empty and "link" in flow_df.columns:
    # Annual net flow by link (TWh)
    annual_flow = flow_df.groupby("link")["value"].agg(["sum", "mean", "max", "min"])
    annual_flow["net_twh"] = annual_flow["sum"] / 1e6  # MWh → TWh
    print("Cross-border flow summary:")
    print(annual_flow[["net_twh", "mean", "max", "min"]].round(2)) \
        if hasattr(annual_flow, 'style') else print(annual_flow.round(2).to_string())

    # Plot top links
    top_links = annual_flow.nlargest(6, "max").index.tolist()
    if top_links:
        fig, axes = plt.subplots(2, 3, figsize=(16, 8), sharex=True)
        axes = axes.flatten()
        for ax, link in zip(axes, top_links):
            ldata = flow_df[flow_df["link"] == link].sort_values("hour")
            ax.plot(ldata["hour"], ldata["value"] / 1000, lw=0.3, alpha=0.7)
            ax.axhline(0, color="gray", ls="--", lw=0.5)
            ax.set_title(link, fontsize=9)
            ax.set_ylabel("GW")
        for ax in axes[len(top_links):]:
            ax.set_visible(False)
        plt.suptitle("Cross-border flows (positive = A→B)")
        plt.tight_layout()
        fig.savefig(FIG_DIR / "cross_border_flows.png")
        plt.show()
else:
    print("No cross-border flow data available.")

if sol_ds is None:
    print('⚠ No solution data — skipping spillage analysis')
else:
    # ── Spillage analysis ──────────────────────────────────────────────
    SPILL_VAR = "operation_spillage_power"
    
    if sol_ds is not None and SPILL_VAR in sol_ds:
        spill_da = sol_ds[SPILL_VAR]
        print(f"Spillage variable: {SPILL_VAR} | Dims: {spill_da.dims}")
    
        spill_by_country = {}
        for area in COUNTRIES_MODELLED:
            try:
                sp = spill_da.sel(area=area)
                if "resource" in sp.dims:
                    res = [str(v) for v in sp.coords["resource"].values]
                    elec = [v for v in res if "elec" in v.lower()]
                    if elec:
                        sp = sp.sel(resource=elec[0])
                if "year_op" in sp.dims:
                    sp = sp.sel(year_op=MODEL_YEAR)
                vals = sp.values.flatten()
                spill_twh = np.nansum(vals) / 1e6
                spill_by_country[area] = spill_twh
            except Exception:
                continue
    
        spill_series = pd.Series(spill_by_country, name="spillage_twh")
        print("\nAnnual VRE spillage by country (TWh):")
        print(spill_series.round(2))
        print(f"\nTotal system spillage: {spill_series.sum():.2f} TWh")
    else:
        print("No spillage data available in solution dataset.")

# ══════════════════════════════════════════════════════════════════════
# 9.1  Store current run results for comparison
# Guard: skip if previous cells didn't produce results
# ══════════════════════════════════════════════════════════════════════

current_scenario = {
    "name": f"{'ramp' if RAMPING_ENABLED else 'no_ramp'}_flex{FLEX_TOTAL_FRACTION:.0%}",
    "ramping": RAMPING_ENABLED,
    "flex_fraction": FLEX_TOTAL_FRACTION,
    "flex_conservation_hrs": FLEX_CONSERVATION_HRS,
    "total_ens_gwh": adeq_df["ens_gwh"].sum(),
    "max_lole_hours": int(adeq_df["lole_hours"].max()),
    "mean_price_eur": float(adeq_df["mean_price"].mean()),
    "objective_value": getattr(linopy_model, "objective_value", None),
}
print(f"Current scenario stored: {current_scenario['name']}")
print(f"  ENS: {current_scenario['total_ens_gwh']:.2f} GWh | "
      f"LOLE: {current_scenario['max_lole_hours']} h | "
      f"Mean price: {current_scenario['mean_price_eur']:.1f} EUR/MWh")

# ══════════════════════════════════════════════════════════════════════
# 9.2  Manual scenario comparison table
# ══════════════════════════════════════════════════════════════════════
# Paste results from multiple runs here.
# Example structure:

scenario_results = [current_scenario]
# Uncomment and fill after running other scenarios:
# scenario_results.append({
#     "name": "no_ramp_no_flex",
#     "ramping": False,
#     "flex_fraction": 0.0,
#     "total_ens_gwh": ???,
#     "max_lole_hours": ???,
#     "mean_price_eur": ???,
#     "objective_value": ???,
# })

comparison_df = pd.DataFrame(scenario_results)
print("Scenario comparison:")
print(comparison_df) if hasattr(comparison_df, 'style') else print(comparison_df.to_string(index=False))

# ── Tornado chart placeholder ─────────────────────────────────────
if len(scenario_results) >= 2:
    fig, ax = plt.subplots(figsize=(10, 5))
    names = [s["name"] for s in scenario_results]
    ens_vals = [s["total_ens_gwh"] for s in scenario_results]
    y_pos = range(len(names))
    ax.barh(y_pos, ens_vals, color="coral", alpha=0.8)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(names)
    ax.set_xlabel("Total ENS (GWh)")
    ax.set_title("Scenario comparison: Energy Not Served")
    fig.savefig(FIG_DIR / "scenario_comparison_ens.png")
    plt.show()
else:
    print("\nRun additional scenarios and add them to scenario_results[] for comparison.")

# ══════════════════════════════════════════════════════════════════════
# 10.1  Summary
# ══════════════════════════════════════════════════════════════════════

print("=" * 80)
print("ADEQUACY ASSESSMENT SUMMARY")
print(f"Scenario: CLEVER {MODEL_YEAR} | Full-ENR + Sufficiency")
print(f"Ramping: {'ENABLED' if RAMPING_ENABLED else 'DISABLED'}")
print(f"Flexibility: {FLEX_TOTAL_FRACTION:.0%} of demand "
      f"({', '.join(FLEX_CATEGORIES.keys())})")
print("=" * 80)

# System-level
print(f"\nSystem total ENS:      {adeq_df['ens_gwh'].sum():.2f} GWh")
print(f"Max country LOLE:      {adeq_df['lole_hours'].max()} hours")
print(f"Mean electricity price: {adeq_df['mean_price'].mean():.1f} EUR/MWh")

# Per-country verdict
print("\nPer-country adequacy verdict:")
for area in COUNTRIES_MODELLED:
    r = adequacy_results.get(area, {})
    ens = r.get("ens_gwh", 0)
    lole = r.get("lole_hours", 0)
    if ens < 0.01 and lole == 0:
        verdict = "ADEQUATE"
    elif lole <= 3:
        verdict = "MARGINAL"
    else:
        verdict = "INADEQUATE"
    print(f"  {area}: {verdict} (ENS={ens:.2f} GWh, LOLE={lole}h)")

if prices_df.empty and conv_cap_df.empty:
    print('⚠ No data to export')
else:
    # ══════════════════════════════════════════════════════════════════════
    # 10.2  Export results to CSV
    # ══════════════════════════════════════════════════════════════════════
    
    tag = f"{'ramp' if RAMPING_ENABLED else 'noramp'}_flex{int(FLEX_TOTAL_FRACTION*100)}pct"
    
    # Adequacy dashboard
    dashboard.to_csv(EXPORT_DIR / f"adequacy_dashboard_{tag}.csv", index=False)
    print(f"Saved: {EXPORT_DIR / f'adequacy_dashboard_{tag}.csv'}")
    
    # Prices
    prices_df.to_csv(EXPORT_DIR / f"prices_{tag}.csv", index=False)
    print(f"Saved: {EXPORT_DIR / f'prices_{tag}.csv'}")
    
    # Conversion capacities
    conv_cap_df.to_csv(EXPORT_DIR / f"conversion_capacity_{tag}.csv", index=False)
    print(f"Saved: {EXPORT_DIR / f'conversion_capacity_{tag}.csv'}")
    
    # Storage capacities
    stor_cap_df.to_csv(EXPORT_DIR / f"storage_energy_capacity_{tag}.csv", index=False)
    stor_pow_df.to_csv(EXPORT_DIR / f"storage_power_capacity_{tag}.csv", index=False)
    print(f"Saved: storage capacity CSVs")
    
    # Ex-ante adequacy
    exante_df.to_csv(EXPORT_DIR / f"exante_adequacy_{tag}.csv")
    print(f"Saved: {EXPORT_DIR / f'exante_adequacy_{tag}.csv'}")
    
    print(f"\nAll exports in: {EXPORT_DIR}")
    print(f"All figures in: {FIG_DIR}")
