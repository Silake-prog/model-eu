"""
generate_scenario_observatory.py — Cross-scenario HTML observatory for the
POMMES CLEVER 2050 biomethane / H₂ / nuclear sensitivity matrix.

Produces a single self-contained HTML file with plotly-interactive figures
comparing all solved scenarios. Built to mirror the depth of the single-scenario
``generate_observatory.py`` (storage, transport, hourly dispatch, prices)
but applied across the full sensitivity matrix.

Design choices
--------------
- No body text. Figures only, LaTeX-style titles (Computer Modern font).
- Auto-discovery: any folder under results/diagnostics/<scen>/solution_2050.nc
  is included as a scenario.
- Each scenario gets a consistent color across all bar charts.
- Plotly figures embedded inline (full hourly arrays are kept raw — hourly
  VRE variability is the point of this analysis).
- "Small-multiples" sections (hourly dispatch, SoC, H₂ balance, weekly zooms)
  are restricted to FOCAL_SCENARIOS for readability.
- Comparison bar/heatmap sections always cover all loaded scenarios.

Usage
-----
    python notebooks/generate_scenario_observatory.py \\
        --diagnostics-dir results/diagnostics \\
        --output results/observatoire_scenarios.html
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import xarray as xr
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# Runtime flags (set by main(), used by figure builders)
# ════════════════════════════════════════════════════════════════════
# Stride for box-plot data when --lite is active. 1 = raw (default).
# 10 = every 10th hour-country point; ~26 k samples/scenario, distribution
# shape preserved exactly, file ≈ 10× smaller.
_LITE_BOX_STRIDE: int = 1
_SHAPEFILE_PATH: Optional[Path] = None  # set in main()
_EUROPE_GEOJSON_CACHE: Optional[dict] = None

# ════════════════════════════════════════════════════════════════════
# CONFIG — typography, palette, scenario taxonomy
# ════════════════════════════════════════════════════════════════════

LATEX_FONT_FAMILY = (
    "'Computer Modern Serif', 'Latin Modern Roman', "
    "'CMU Serif', Garamond, 'Times New Roman', serif"
)
LATEX_FONT_FAMILY_SANS = (
    "'Computer Modern Sans Serif', 'Latin Modern Sans', "
    "'CMU Sans Serif', sans-serif"
)

# Worldview families — base prefixes that determine bar colour
WORLDVIEW_FAMILIES = {
    "R0_v1":           {"name": "Sufficiency",       "color": "#1f77b4"},  # blue
    "policy_re_noMin": {"name": "Policy (no floor)", "color": "#9467bd"},  # purple
    "policy_re":       {"name": "Policy",            "color": "#d62728"},  # red
    "policy_nuke":     {"name": "Policy + Nuclear",  "color": "#2ca02c"},  # green
}

# Tech colour palette (electricity)
TECH_COLORS = {
    "Solar":                 "#ffcc00",
    "Wind_Onshore":          "#4f9aff",
    "Wind_Offshore":         "#003c8f",
    "Nuclear":               "#7f2f99",
    "Gas":                   "#888888",
    "Hydrogen_power_plant":  "#00aaa0",
    "electrolysis":          "#0080a0",
    "Biomethane_CCGT":       "#41a04f",
    "ATR_biomethane":        "#80c050",
    "Waste":                 "#bbbb88",
    "Reservoir_Hydro_Plant": "#3399ff",
    "RoR_Hydro":             "#3366cc",
}
STORAGE_COLORS = {
    "h2_storage":            "#00aaa0",
    "Battery_1h":            "#ff7f0e",
    "Battery_4h":            "#ff9933",
    "Pumped_Hydro":          "#3366cc",
    "Reservoir_Hydro_Store": "#3399ff",
}
TECH_DISPLAY = {
    "Solar":                 "Solar",
    "Wind_Onshore":          "Wind Onshore",
    "Wind_Offshore":         "Wind Offshore",
    "Nuclear":               "Nuclear",
    "Gas":                   "Gas (fossil)",
    "Hydrogen_power_plant":  "H₂-CCGT",
    "electrolysis":          "Electrolyser",
    "Biomethane_CCGT":       "Biomethane CCGT",
    "ATR_biomethane":        "ATR (CH₄→H₂)",
    "Waste":                 "Waste",
    "Reservoir_Hydro_Plant": "Hydro reservoir",
    "RoR_Hydro":             "Hydro RoR",
    "h2_storage":            "H₂ underground storage",
    "Battery_1h":            "Battery 1h",
    "Battery_4h":            "Battery 4h",
    "Pumped_Hydro":          "Pumped hydro",
    "Reservoir_Hydro_Store": "Reservoir hydro",
}

# Order in stacked bars / areas
TECH_ORDER_ELECTRIC = [
    "Solar", "Wind_Onshore", "Wind_Offshore", "RoR_Hydro",
    "Reservoir_Hydro_Plant", "Nuclear",
    "Biomethane_CCGT", "Hydrogen_power_plant", "Gas", "Waste",
]
TECH_ORDER_H2_SUPPLY = ["electrolysis", "ATR_biomethane"]

# Focal scenarios used for small-multiples (panels). Kept short for readability.
# Order matters — left→right, top→bottom panel layout.
FOCAL_SCENARIOS_PREF = [
    "R0_v1_nuke_bioLow",
    "policy_re",
    "policy_re_noMin_bioMed",
    "policy_nuke",
    "policy_nuke_bioMed_atr",
    "policy_nuke_bioMed_h2HIGH_atr",
    # CO2-sensitivity scenarios (appended if available)
    "R0_v1_nuke_bioLow_co2250_nofloor",
    "policy_nuke_bioMed_atr_co2250_nofloor",
    "policy_nuke_bioMed_h2HIGH_atr_co2250_nofloor",
]

# Weekly slices (start_hour, end_hour). Jan 15 = day 15 = hour 14*24 = 336.
WEEK_WINTER_START_HOUR = 14 * 24    # Jan 15
WEEK_WINTER_END_HOUR   = 21 * 24    # Jan 22
WEEK_SUMMER_START_HOUR = 196 * 24   # Jul 16
WEEK_SUMMER_END_HOUR   = 203 * 24   # Jul 23

# Calendar month boundaries (cumulative hours)
MONTH_TICKS = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30]) * 24
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CO2_INTENSITY_GAS_T_PER_MWHE = 0.37  # fossil CCGT


# ════════════════════════════════════════════════════════════════════
# DATA LOADING
# ════════════════════════════════════════════════════════════════════

@dataclass
class ScenarioData:
    name: str
    solution: xr.Dataset
    input_dataset: xr.Dataset
    dual: Optional[xr.Dataset] = None

    # Aggregates (populated post-load)
    elec_demand_twh: float = 0.0
    h2_demand_twh: float = 0.0
    elec_demand_hourly_eu: np.ndarray = field(default_factory=lambda: np.zeros(8760))
    h2_demand_hourly_eu: np.ndarray = field(default_factory=lambda: np.zeros(8760))
    totex_geur: float = 0.0
    capacities_gw: Dict[str, float] = field(default_factory=dict)
    storage_power_gw: Dict[str, float] = field(default_factory=dict)
    storage_energy_twh: Dict[str, float] = field(default_factory=dict)
    pipeline_capacity_gw: float = 0.0
    pipeline_flow_twh: float = 0.0
    electric_line_capacity_gw: float = 0.0
    electric_line_flow_twh: float = 0.0
    cost_breakdown_geur: Dict[str, float] = field(default_factory=dict)
    h2_shadow_prices: np.ndarray = field(default_factory=lambda: np.array([]))
    elec_shadow_prices: np.ndarray = field(default_factory=lambda: np.array([]))
    gas_dispatch_twh: float = 0.0
    bio_ccgt_dispatch_twh: float = 0.0
    atr_dispatch_twh: float = 0.0
    electrolysis_dispatch_twh: float = 0.0
    load_shedding_elec_twh: float = 0.0
    load_shedding_h2_twh: float = 0.0
    spillage_twh: float = 0.0
    per_country_capacities: Dict[str, Dict[str, float]] = field(default_factory=dict)
    per_country_load_shedding_h: Dict[str, float] = field(default_factory=dict)

    @property
    def total_demand_twh(self) -> float:
        return self.elec_demand_twh + self.h2_demand_twh

    @property
    def eur_per_mwh(self) -> float:
        return self.totex_geur * 1000 / self.total_demand_twh if self.total_demand_twh > 0 else 0.0

    @property
    def vre_share_pct(self) -> float:
        op = self.solution["operation_conversion_power"]
        techs = list(map(str, op.coords["conversion_tech"].values))
        vre_techs = [t for t in ("Solar", "Wind_Onshore", "Wind_Offshore") if t in techs]
        if not vre_techs:
            return 0.0
        vre = float(op.sel(conversion_tech=vre_techs).sum()) / 1e6
        # Total electricity generation: all techs producing into "electricity"
        net = self.solution["operation_conversion_net_generation"]
        elec_gen = float(net.sel(resource="electricity").where(lambda x: x > 0, 0).sum()) / 1e6
        return vre / elec_gen * 100 if elec_gen > 0 else 0.0

    @property
    def worldview_family(self) -> str:
        for prefix in sorted(WORLDVIEW_FAMILIES, key=len, reverse=True):
            if self.name.startswith(prefix):
                return prefix
        return "R0_v1"

    # ---- Hourly EU-aggregate getters (lazily computed, not stored) -----
    def eu_dispatch_hourly_gw(self, techs: Sequence[str]) -> Dict[str, np.ndarray]:
        """Return {tech: array(8760)} in GW, EU-aggregate."""
        op = self.solution["operation_conversion_power"]
        present = [t for t in techs if t in op.coords["conversion_tech"].values]
        out = {}
        for t in present:
            arr = op.sel(conversion_tech=t).sum(dim="area").squeeze(drop=True).values / 1000.0
            if float(np.nanmax(np.abs(arr))) > 0.01:
                out[t] = arr
        return out

    def eu_storage_level_hourly_twh(self, storage_tech: str) -> Optional[np.ndarray]:
        if "operation_storage_level" not in self.solution.data_vars:
            return None
        soc = self.solution["operation_storage_level"]
        if storage_tech not in soc.coords["storage_tech"].values:
            return None
        return soc.sel(storage_tech=storage_tech).sum(dim="area").squeeze(drop=True).values / 1e6

    def country_dispatch_hourly_gw(self, area: str, techs: Sequence[str]) -> Dict[str, np.ndarray]:
        op = self.solution["operation_conversion_power"]
        if area not in op.coords["area"].values:
            return {}
        sub = op.sel(area=area)
        present = [t for t in techs if t in sub.coords["conversion_tech"].values]
        out = {}
        for t in present:
            arr = sub.sel(conversion_tech=t).squeeze(drop=True).values / 1000.0
            if float(np.nanmax(np.abs(arr))) > 0.01:
                out[t] = arr
        return out

    def country_demand_hourly_gw(self, area: str, resource: str = "electricity") -> Optional[np.ndarray]:
        d = self.input_dataset["demand"]
        if area not in d.coords["area"].values:
            return None
        return d.sel(area=area, resource=resource).squeeze(drop=True).values / 1000.0

    def h2_pipeline_link_flows_twh(self) -> Dict[str, float]:
        """Return {link_name: |flow|·hour summed, TWh} for h2_pipeline."""
        if "operation_transport_power" not in self.solution.data_vars:
            return {}
        tp = self.solution["operation_transport_power"]
        if "h2_pipeline" not in tp.coords["transport_tech"].values:
            return {}
        sub = tp.sel(transport_tech="h2_pipeline")
        out = {}
        for link in sub.coords["link"].values:
            out[str(link)] = float(np.abs(sub.sel(link=link)).sum()) / 1e6
        return out


def load_scenarios(diagnostics_dir: Path) -> Dict[str, ScenarioData]:
    scenarios: Dict[str, ScenarioData] = {}
    for sub in sorted(diagnostics_dir.iterdir()):
        if not sub.is_dir():
            continue
        sol_p = sub / "solution_2050.nc"
        inp_p = sub / "input_dataset_2050.nc"
        if not sol_p.exists() or not inp_p.exists():
            continue
        try:
            sol = xr.open_dataset(sol_p)
            inp = xr.open_dataset(inp_p)
            dual_p = sub / "dual_2050.nc"
            dual = xr.open_dataset(dual_p) if dual_p.exists() else None
            scenarios[sub.name] = ScenarioData(
                name=sub.name, solution=sol, input_dataset=inp, dual=dual,
            )
        except Exception as e:
            logger.warning("failed loading %s: %s", sub.name, e)
    return scenarios


def compute_aggregates(scenarios: Dict[str, ScenarioData]) -> None:
    for name, s in scenarios.items():
        sol, inp = s.solution, s.input_dataset

        # Demand
        d = inp["demand"]
        s.elec_demand_twh = float(d.sel(resource="electricity").sum()) / 1e6
        s.h2_demand_twh = float(d.sel(resource="hydrogen").sum()) / 1e6
        s.elec_demand_hourly_eu = d.sel(resource="electricity").sum(dim="area").squeeze(drop=True).values / 1000.0
        s.h2_demand_hourly_eu = d.sel(resource="hydrogen").sum(dim="area").squeeze(drop=True).values / 1000.0

        # Conversion capacities
        cap = sol["operation_conversion_power_capacity"]
        techs = list(map(str, cap.coords["conversion_tech"].values))
        for t in techs:
            s.capacities_gw[t] = float(cap.sel(conversion_tech=t).sum()) / 1000.0
            for area in cap.coords["area"].values:
                v = float(cap.sel(conversion_tech=t, area=area).sum()) / 1000.0
                if v > 0.005:
                    s.per_country_capacities.setdefault(str(area), {})[t] = v

        # Storage capacities
        if "operation_storage_power_capacity" in sol.data_vars:
            spc = sol["operation_storage_power_capacity"]
            sec = sol["operation_storage_energy_capacity"]
            for st in map(str, spc.coords["storage_tech"].values):
                s.storage_power_gw[st]  = float(spc.sel(storage_tech=st).sum()) / 1000.0
                s.storage_energy_twh[st] = float(sec.sel(storage_tech=st).sum()) / 1e6

        # Transport
        if "operation_transport_power_capacity" in sol.data_vars:
            tpc = sol["operation_transport_power_capacity"]
            tpw = sol["operation_transport_power"]
            tx_techs = list(map(str, tpc.coords["transport_tech"].values))
            if "h2_pipeline" in tx_techs:
                s.pipeline_capacity_gw = float(tpc.sel(transport_tech="h2_pipeline").sum()) / 1000.0
                s.pipeline_flow_twh = float(np.abs(tpw.sel(transport_tech="h2_pipeline")).sum()) / 1e6
            if "electric_line" in tx_techs:
                s.electric_line_capacity_gw = float(tpc.sel(transport_tech="electric_line").sum()) / 1000.0
                s.electric_line_flow_twh = float(np.abs(tpw.sel(transport_tech="electric_line")).sum()) / 1e6

        # Totex + cost breakdown
        for var in ("annualised_totex",
                    "operation_conversion_costs", "operation_storage_costs",
                    "operation_transport_costs",
                    "operation_load_shedding_costs", "operation_spillage_costs",
                    "planning_conversion_costs", "planning_storage_costs",
                    "planning_transport_costs"):
            if var in sol.data_vars:
                s.cost_breakdown_geur[var] = float(sol[var].sum()) / 1e9
        s.totex_geur = s.cost_breakdown_geur.get("annualised_totex", 0.0)

        # Shadow prices (resource adequacy duals = €/MWh of resource)
        if s.dual is not None and "operation_adequacy_constraint" in s.dual.data_vars:
            ac = s.dual["operation_adequacy_constraint"]
            if "resource" in ac.coords:
                if "hydrogen" in ac.coords["resource"].values:
                    arr = np.abs(ac.sel(resource="hydrogen").values).flatten()
                    s.h2_shadow_prices = arr[~np.isnan(arr)]
                if "electricity" in ac.coords["resource"].values:
                    arr = np.abs(ac.sel(resource="electricity").values).flatten()
                    s.elec_shadow_prices = arr[~np.isnan(arr)]

        # Tech dispatch (TWh, electricity + H2 outputs)
        op = sol["operation_conversion_power"]
        for t, attr in [("Gas", "gas_dispatch_twh"),
                        ("Biomethane_CCGT", "bio_ccgt_dispatch_twh"),
                        ("ATR_biomethane", "atr_dispatch_twh"),
                        ("electrolysis", "electrolysis_dispatch_twh")]:
            if t in op.coords["conversion_tech"].values:
                setattr(s, attr, float(op.sel(conversion_tech=t).sum()) / 1e6)

        # Load shedding / spillage by resource
        if "operation_load_shedding_power" in sol.data_vars:
            ls = sol["operation_load_shedding_power"]
            s.load_shedding_elec_twh = float(ls.sel(resource="electricity").sum()) / 1e6
            s.load_shedding_h2_twh   = float(ls.sel(resource="hydrogen").sum()) / 1e6
            # Hours of non-zero shedding per country (elec only)
            ls_elec = ls.sel(resource="electricity").squeeze(drop=True)
            for area in ls_elec.coords["area"].values:
                hrs = int((ls_elec.sel(area=area) > 1e-3).sum())
                if hrs > 0:
                    s.per_country_load_shedding_h[str(area)] = hrs
        if "operation_spillage_power" in sol.data_vars:
            sp = sol["operation_spillage_power"]
            s.spillage_twh = float(sp.sel(resource="electricity").sum()) / 1e6


# ════════════════════════════════════════════════════════════════════
# COMMON STYLING
# ════════════════════════════════════════════════════════════════════

def _scenario_order(scenarios: Dict[str, ScenarioData]) -> List[str]:
    families = list(WORLDVIEW_FAMILIES.keys())
    return sorted(scenarios.keys(),
                  key=lambda n: (next((i for i, f in enumerate(families)
                                       if n.startswith(f)), 99), n))


def _scenario_color(name: str) -> str:
    for prefix, info in sorted(WORLDVIEW_FAMILIES.items(), key=lambda kv: -len(kv[0])):
        if name.startswith(prefix):
            return info["color"]
    return "#888888"


def _focal_scenarios(scenarios: Dict[str, ScenarioData]) -> List[str]:
    """Pick the focal scenarios that exist in the loaded set."""
    return [s for s in FOCAL_SCENARIOS_PREF if s in scenarios]


def _apply_latex_layout(fig: go.Figure, title: str,
                        height: int = 460,
                        y_title: str = "", x_title: str = "") -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(family=LATEX_FONT_FAMILY, size=20)),
        xaxis=dict(title=dict(text=x_title, font=dict(family=LATEX_FONT_FAMILY, size=14)),
                   tickfont=dict(family=LATEX_FONT_FAMILY, size=11)),
        yaxis=dict(title=dict(text=y_title, font=dict(family=LATEX_FONT_FAMILY, size=14)),
                   tickfont=dict(family=LATEX_FONT_FAMILY, size=11)),
        font=dict(family=LATEX_FONT_FAMILY, size=12),
        height=height,
        margin=dict(l=70, r=30, t=70, b=70),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, linecolor="#888", linewidth=1, ticks="outside")
    fig.update_yaxes(showgrid=True, gridcolor="#ddd", linecolor="#888", linewidth=1, ticks="outside")
    return fig


def _month_axis(fig: go.Figure, row: int = None, col: int = None) -> None:
    """Apply month-of-year tick labels on the x-axis."""
    kwargs = dict(tickvals=MONTH_TICKS[:-1], ticktext=MONTH_LABELS, range=[0, 8760])
    if row is not None and col is not None:
        fig.update_xaxes(**kwargs, row=row, col=col)
    else:
        fig.update_xaxes(**kwargs)


# ════════════════════════════════════════════════════════════════════
# §0 — SCENARIO INDEX (assumptions + key results tables)
# ════════════════════════════════════════════════════════════════════

def _parse_scenario_axes(name: str) -> Dict[str, str]:
    """Decode a scenario name into the policy / sensitivity axes it varies."""
    # ── Worldview & default H₂ demand bundle ──────────────────────────────
    if name.startswith("R0_v1"):
        worldview = "CLEVER (sufficiency)"
        bundle_default = "low_h2"
    elif name.startswith("policy_re_noMin"):
        worldview = "Policy / no gas-cap floor"
        bundle_default = "central"
    elif name.startswith("policy_nuke"):
        worldview = "Policy + 82 GW nuclear"
        bundle_default = "central"
    elif name.startswith("policy_re"):
        worldview = "Policy / EU re-floor"
        bundle_default = "central"
    else:
        worldview = "?"; bundle_default = "?"
    # ── Nuclear ─────────────────────────────────────────────────────────────
    nuclear = ("82 GW (allowed)"
               if name.startswith("policy_nuke") or "_nuke" in name
               else "no new build")
    # ── Biomethane scope ────────────────────────────────────────────────────
    if "_bioMed" in name:
        bio = "bioMed (5 + MINBIOCRP31)"
    elif "_bioLow" in name:
        bio = "bioLow (5 codes)"
    else:
        bio = "—"
    # ── ATR (auto-thermal reforming of biomethane → H₂) ─────────────────────
    atr = "ATR enabled" if "_atr" in name else "—"
    # ── Corridor expansion (NTC + H₂ pipeline scaling) ─────────────────────
    if "_corr3x" in name: corridor = "×3"
    elif "_corr2x" in name: corridor = "×2"
    else: corridor = "×1 (baseline)"
    # ── Electrolyser CAPEX ─────────────────────────────────────────────────
    if "_el700" in name:   el = "700 €/kW"
    elif "_el900" in name: el = "900 €/kW"
    else:                  el = "500 €/kW (default)"
    # ── H₂ demand bundle (DemandForge) ─────────────────────────────────────
    bundle = "high_h2" if "_h2HIGH" in name else bundle_default
    # ── CO₂ price ──────────────────────────────────────────────────────────
    co2 = "250 €/t" if "_co2250" in name else "150 €/t (default)"
    # ── Politically-retained gas-capacity floor ────────────────────────────
    floor = "lifted" if "_nofloor" in name else "kept (politically retained)"
    return {
        "worldview":  worldview,
        "h2_bundle":  bundle,
        "bio_scope":  bio,
        "atr":        atr,
        "nuclear":    nuclear,
        "corridor":   corridor,
        "el_capex":   el,
        "co2_price":  co2,
        "gas_floor":  floor,
    }


def build_assumptions_table_html(scenarios: Dict[str, ScenarioData]) -> str:
    """Render the per-scenario assumptions as an HTML table."""
    order = _scenario_order(scenarios)
    rows = []
    for n in order:
        ax = _parse_scenario_axes(n)
        color = _scenario_color(n)
        rows.append(
            f'<tr>'
            f'  <td class="scen-name"><span class="swatch" style="background:{color}"></span>{n}</td>'
            f'  <td>{ax["worldview"]}</td>'
            f'  <td>{ax["h2_bundle"]}</td>'
            f'  <td>{ax["bio_scope"]}</td>'
            f'  <td>{ax["atr"]}</td>'
            f'  <td>{ax["nuclear"]}</td>'
            f'  <td>{ax["corridor"]}</td>'
            f'  <td>{ax["el_capex"]}</td>'
            f'  <td>{ax["co2_price"]}</td>'
            f'  <td>{ax["gas_floor"]}</td>'
            f'</tr>'
        )
    return (
        '<div class="figure">'
        '<h3 style="margin:0 0 8px 4px;font-weight:normal">Table A — Scenario assumptions</h3>'
        '<div class="table-wrap"><table class="scenario-table"><thead><tr>'
        '<th>Scenario</th>'
        '<th>Worldview</th>'
        '<th>H₂ demand bundle</th>'
        '<th>Biomethane scope</th>'
        '<th>ATR</th>'
        '<th>Nuclear</th>'
        '<th>Corridor</th>'
        '<th>Electrolyser CAPEX</th>'
        '<th>CO₂ price</th>'
        '<th>Gas-cap floor</th>'
        '</tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table></div>'
        '<p class="table-note">'
        'CLEVER bundle = JRC-2024 sufficiency narrative (lowest electricity demand, low H₂). '
        'Policy bundles assume a higher residual electricity demand from incomplete sufficiency. '
        'Corridor &times;N scales both cross-border NTC and the H₂ pipeline planning caps. '
        'Politically-retained gas floor = lower-bound capacity per country derived from current '
        'thermal fleet keep-rate assumptions; <em>lifted</em> drops that constraint entirely.'
        '</p>'
        '</div>'
    )


def build_results_table_html(scenarios: Dict[str, ScenarioData]) -> str:
    """Per-scenario key result metrics relevant to H₂ infrastructure deployment."""
    order = _scenario_order(scenarios)
    rows = []
    for n in order:
        s = scenarios[n]
        color = _scenario_color(n)
        vre_gw = sum(s.capacities_gw.get(t, 0)
                     for t in ("Solar", "Wind_Onshore", "Wind_Offshore"))
        bat_twh = (s.storage_energy_twh.get("Battery_1h", 0)
                   + s.storage_energy_twh.get("Battery_4h", 0))
        # Electrolyser load factor
        el_gw = s.capacities_gw.get("electrolysis", 0)
        el_lf = (s.electrolysis_dispatch_twh * 1000 / (el_gw * 8760) * 100) if el_gw > 0.1 else 0.0
        atr_gw = s.capacities_gw.get("ATR_biomethane", 0)
        atr_lf = (s.atr_dispatch_twh * 1000 / (atr_gw * 8760) * 100) if atr_gw > 0.1 else 0.0
        # H₂ shadow price summary
        if len(s.h2_shadow_prices) > 0:
            h2_clean = s.h2_shadow_prices[s.h2_shadow_prices < 500]
            h2_mean = float(np.mean(h2_clean)) if len(h2_clean) else 0.0
            h2_p95 = float(np.percentile(h2_clean, 95)) if len(h2_clean) else 0.0
        else:
            h2_mean = h2_p95 = 0.0
        load_shed_gwh = (s.load_shedding_elec_twh + s.load_shedding_h2_twh) * 1000

        rows.append(
            f'<tr>'
            f'  <td class="scen-name"><span class="swatch" style="background:{color}"></span>{n}</td>'
            f'  <td class="num">{s.totex_geur:.1f}</td>'
            f'  <td class="num">{s.eur_per_mwh:.1f}</td>'
            f'  <td class="num">{vre_gw:.0f}</td>'
            f'  <td class="num">{s.capacities_gw.get("Nuclear", 0):.0f}</td>'
            f'  <td class="num">{el_gw:.0f}</td>'
            f'  <td class="num">{el_lf:.0f}</td>'
            f'  <td class="num">{atr_gw:.0f}</td>'
            f'  <td class="num">{atr_lf:.0f}</td>'
            f'  <td class="num">{s.capacities_gw.get("Biomethane_CCGT", 0):.0f}</td>'
            f'  <td class="num">{s.capacities_gw.get("Hydrogen_power_plant", 0):.0f}</td>'
            f'  <td class="num">{s.storage_energy_twh.get("h2_storage", 0):.1f}</td>'
            f'  <td class="num">{bat_twh:.2f}</td>'
            f'  <td class="num">{s.pipeline_capacity_gw:.0f}</td>'
            f'  <td class="num">{s.pipeline_flow_twh:.0f}</td>'
            f'  <td class="num">{h2_mean:.0f}</td>'
            f'  <td class="num">{h2_p95:.0f}</td>'
            f'  <td class="num">{s.gas_dispatch_twh:.1f}</td>'
            f'  <td class="num">{load_shed_gwh:.1f}</td>'
            f'</tr>'
        )
    return (
        '<div class="figure">'
        '<h3 style="margin:0 0 8px 4px;font-weight:normal">Table B — Key results, with hydrogen-infrastructure focus</h3>'
        '<div class="table-wrap"><table class="scenario-table results"><thead>'
        '<tr>'
        '<th rowspan="2">Scenario</th>'
        '<th rowspan="2">Totex<br/>(G€/yr)</th>'
        '<th rowspan="2">€/MWh<br/>final</th>'
        '<th rowspan="2">VRE<br/>(GW)</th>'
        '<th rowspan="2">Nuclear<br/>(GW)</th>'
        '<th colspan="4">H₂ production</th>'
        '<th colspan="2">Dispatchable backup</th>'
        '<th colspan="2">Storage</th>'
        '<th colspan="2">H₂ pipeline</th>'
        '<th colspan="2">H₂ price (€/MWh)</th>'
        '<th rowspan="2">Fossil gas<br/>(TWh)</th>'
        '<th rowspan="2">Load shed<br/>(GWh)</th>'
        '</tr>'
        '<tr>'
        '<th>Electrolyser<br/>GW</th><th>LF&nbsp;%</th>'
        '<th>ATR<br/>GW</th><th>LF&nbsp;%</th>'
        '<th>Bio CCGT<br/>GW</th><th>H₂-CCGT<br/>GW</th>'
        '<th>H₂<br/>TWh</th><th>Battery<br/>TWh</th>'
        '<th>Capacity<br/>(GW)</th><th>Transit<br/>(TWh)</th>'
        '<th>Mean</th><th>P95</th>'
        '</tr>'
        '</thead><tbody>'
        + "".join(rows)
        + '</tbody></table></div>'
        '<p class="table-note">'
        'VRE = Solar + Wind onshore + Wind offshore. Load factor (LF) = annual dispatch ÷ (capacity × 8760 h). '
        'H₂ storage = underground (caverns + depleted reservoirs), capped per country by JRC ENSPRESO geology. '
        'H₂ price stats are computed over hourly × country adequacy duals, clipped at 500 €/MWh '
        '(load-shed spikes go to 30 000 €/MWh and would dominate the mean otherwise). '
        'Fossil gas dispatch in <strong>R0_v1_*</strong> scenarios is small because CLEVER demand is much lower; '
        'in <strong>policy_*</strong> scenarios the gas floor + higher residual load drive non-zero dispatch.'
        '</p>'
        '</div>'
    )


# ════════════════════════════════════════════════════════════════════
# §1 — HEADLINE COMPARISON
# ════════════════════════════════════════════════════════════════════

def fig_totex_per_scenario(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].totex_geur for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>Totex: %{y:.1f} G€/yr<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Total annualised system cost (Totex) per scenario",
        y_title="G€/yr", x_title="")


def fig_eur_per_mwh(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].eur_per_mwh for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.1f} €/MWh<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Levelised system cost per MWh of total final demand",
        y_title="€/MWh (elec + H₂)", x_title="")


def fig_demand_stack(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Electricity", x=order,
                        y=[scenarios[n].elec_demand_twh for n in order],
                        marker_color="#4f9aff",
                        hovertemplate="%{x}<br>Elec: %{y:.0f} TWh<extra></extra>"))
    fig.add_trace(go.Bar(name="Hydrogen", x=order,
                        y=[scenarios[n].h2_demand_twh for n in order],
                        marker_color="#00aaa0",
                        hovertemplate="%{x}<br>H₂: %{y:.0f} TWh<extra></extra>"))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="Final-energy demand (DemandForge input)",
        y_title="TWh/yr", x_title="")


def fig_vre_share(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].vre_share_pct for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>VRE share: %{y:.1f}%<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Variable renewable share of electricity generation",
        y_title="% (Solar + Wind / total elec gen)", x_title="")


# ════════════════════════════════════════════════════════════════════
# §2 — CAPACITIES
# ════════════════════════════════════════════════════════════════════

def fig_capacity_heatmap(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    techs = [t for t in TECH_ORDER_ELECTRIC + ["ATR_biomethane", "electrolysis"]
             if any(scenarios[n].capacities_gw.get(t, 0) > 0.05 for n in order)]
    z = [[scenarios[n].capacities_gw.get(t, 0.0) for n in order] for t in techs]
    fig = go.Figure(data=go.Heatmap(
        x=order, y=[TECH_DISPLAY.get(t, t) for t in techs], z=z,
        colorscale="Viridis", colorbar=dict(title="GW"),
        hovertemplate="<b>%{x}</b><br>%{y}: %{z:.1f} GW<extra></extra>",
        text=[[f"{v:.0f}" for v in row] for row in z],
        texttemplate="%{text}", textfont={"size": 9, "family": LATEX_FONT_FAMILY},
    ))
    return _apply_latex_layout(fig,
        title="Per-technology installed capacity across scenarios",
        y_title="", x_title="", height=560)


def fig_dispatchable_stacked(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for tech in ("Nuclear", "Biomethane_CCGT", "Hydrogen_power_plant",
                 "ATR_biomethane", "Gas", "Waste"):
        y = [scenarios[n].capacities_gw.get(tech, 0.0) for n in order]
        if max(y) < 0.05:
            continue
        fig.add_trace(go.Bar(name=TECH_DISPLAY.get(tech, tech), x=order, y=y,
                            marker_color=TECH_COLORS.get(tech, "#888")))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="Dispatchable / backup conversion capacity",
        y_title="GW", x_title="")


def fig_vre_stacked(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for tech in ("Solar", "Wind_Onshore", "Wind_Offshore"):
        fig.add_trace(go.Bar(
            name=TECH_DISPLAY[tech], x=order,
            y=[scenarios[n].capacities_gw.get(tech, 0.0) for n in order],
            marker_color=TECH_COLORS[tech]))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="Variable renewable (VRE) capacity stack",
        y_title="GW", x_title="")


# ════════════════════════════════════════════════════════════════════
# §3 — HOURLY ELECTRIC DISPATCH (small-multiples, focal scenarios)
# ════════════════════════════════════════════════════════════════════

def _stacked_dispatch_panels(scenarios: Dict[str, ScenarioData],
                             focal: List[str],
                             tech_order: List[str],
                             hour_slice: Optional[slice] = None,
                             smooth_hours: int = 168,
                             title: str = "",
                             height_per_row: int = 240) -> go.Figure:
    """Build a grid of stacked-area panels, one per focal scenario.

    Each panel plots techs in tech_order, EU-aggregate, in GW.
    Demand line is overlaid in black.
    """
    n = len(focal)
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    fig = make_subplots(rows=rows, cols=cols,
                        subplot_titles=focal,
                        shared_xaxes=False, shared_yaxes=True,
                        horizontal_spacing=0.06, vertical_spacing=0.12)

    legend_shown: set = set()
    for idx, name in enumerate(focal):
        r = idx // cols + 1
        c = idx % cols + 1
        s = scenarios[name]
        series = s.eu_dispatch_hourly_gw(tech_order)
        # Smooth and slice
        for t, arr in list(series.items()):
            a = pd.Series(np.maximum(arr, 0)).rolling(smooth_hours, min_periods=1).mean().values
            if hour_slice is not None:
                a = a[hour_slice]
            series[t] = a
        # Hours axis
        if hour_slice is not None:
            x = np.arange(hour_slice.start or 0, hour_slice.stop or 8760)
        else:
            x = np.arange(8760)

        for t in tech_order:
            if t not in series:
                continue
            show = (t not in legend_shown)
            legend_shown.add(t)
            fig.add_trace(go.Scatter(
                x=x, y=series[t], name=TECH_DISPLAY.get(t, t),
                mode="lines", stackgroup="one", line=dict(width=0),
                fillcolor=TECH_COLORS.get(t, "#888"),
                showlegend=show,
                legendgroup=t,
                hovertemplate=f"{TECH_DISPLAY.get(t, t)}: %{{y:.0f}} GW<extra></extra>",
            ), row=r, col=c)
        # Demand overlay (electric + electrolyser load if present)
        dem = pd.Series(s.elec_demand_hourly_eu).rolling(smooth_hours, min_periods=1).mean().values
        if hour_slice is not None:
            dem = dem[hour_slice]
        fig.add_trace(go.Scatter(
            x=x, y=dem, name="Elec demand",
            mode="lines", line=dict(color="#111", width=1.2),
            showlegend=("Elec demand" not in legend_shown),
            legendgroup="demand",
            hovertemplate="Demand: %{y:.0f} GW<extra></extra>",
        ), row=r, col=c)
        legend_shown.add("Elec demand")

        # Month ticks if full year; otherwise leave default
        if hour_slice is None:
            fig.update_xaxes(tickvals=MONTH_TICKS[:-1], ticktext=MONTH_LABELS,
                            row=r, col=c)

    fig.update_layout(
        title=dict(text=title, font=dict(family=LATEX_FONT_FAMILY, size=20)),
        font=dict(family=LATEX_FONT_FAMILY, size=10),
        height=height_per_row * rows + 80,
        margin=dict(l=60, r=30, t=80, b=50),
        plot_bgcolor="white", paper_bgcolor="white",
        showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=-0.05,
                    font=dict(family=LATEX_FONT_FAMILY, size=10)),
    )
    fig.update_xaxes(showgrid=False, linecolor="#888")
    fig.update_yaxes(showgrid=True, gridcolor="#eee", linecolor="#888", title_text="GW",
                    title_font=dict(family=LATEX_FONT_FAMILY, size=10))
    return fig


def fig_annual_dispatch_panels(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    focal = _focal_scenarios(scenarios)
    return _stacked_dispatch_panels(
        scenarios, focal, TECH_ORDER_ELECTRIC,
        smooth_hours=168,
        title="Annual EU-aggregate electricity dispatch (7-day rolling mean) — focal scenarios",
        height_per_row=260)


def fig_winter_week_panels(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    focal = _focal_scenarios(scenarios)
    return _stacked_dispatch_panels(
        scenarios, focal, TECH_ORDER_ELECTRIC,
        hour_slice=slice(WEEK_WINTER_START_HOUR, WEEK_WINTER_END_HOUR),
        smooth_hours=1,
        title="Winter week (Jan 15–21) — EU-aggregate dispatch",
        height_per_row=240)


def fig_summer_week_panels(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    focal = _focal_scenarios(scenarios)
    return _stacked_dispatch_panels(
        scenarios, focal, TECH_ORDER_ELECTRIC,
        hour_slice=slice(WEEK_SUMMER_START_HOUR, WEEK_SUMMER_END_HOUR),
        smooth_hours=1,
        title="Summer week (Jul 16–22) — EU-aggregate dispatch",
        height_per_row=240)


def fig_residual_load_duration(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Residual = demand − Solar − Wind − RoR_Hydro − Reservoir_Hydro, sorted descending."""
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for n in order:
        s = scenarios[n]
        op = s.solution["operation_conversion_power"]
        techs_av = list(map(str, op.coords["conversion_tech"].values))
        vre = np.zeros(8760)
        for t in ("Solar", "Wind_Onshore", "Wind_Offshore", "RoR_Hydro", "Reservoir_Hydro_Plant"):
            if t in techs_av:
                vre += op.sel(conversion_tech=t).sum(dim="area").squeeze(drop=True).values / 1000.0
        residual = s.elec_demand_hourly_eu - vre  # GW, EU
        sorted_desc = np.sort(residual)[::-1]
        x, y = _subsample_dc(sorted_desc)
        fig.add_trace(go.Scatter(
            x=x, y=y, name=n,
            mode="lines", line=dict(color=_scenario_color(n), width=0.8),
            hovertemplate=f"<b>{n}</b><br>%{{y:.0f}} GW<extra></extra>",
        ))
    fig.add_hline(y=0, line_color="#666", line_width=0.8, line_dash="dot")
    return _apply_latex_layout(fig,
        title="EU-aggregate residual load duration curve (demand − VRE − hydro)",
        y_title="GW of residual load to cover with firm + storage",
        x_title="Hours sorted (descending)",
        height=520)


# ════════════════════════════════════════════════════════════════════
# §4 — STORAGE
# ════════════════════════════════════════════════════════════════════

def fig_storage_capacity_paired(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Two subplots: power (GW) and energy (TWh), stacked by storage tech."""
    order = _scenario_order(scenarios)
    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=("Power capacity (GW)", "Energy capacity (TWh)"),
                        horizontal_spacing=0.08)
    storage_order = ["h2_storage", "Pumped_Hydro", "Reservoir_Hydro_Store",
                     "Battery_4h", "Battery_1h"]
    for st in storage_order:
        y_p = [scenarios[n].storage_power_gw.get(st, 0.0) for n in order]
        y_e = [scenarios[n].storage_energy_twh.get(st, 0.0) for n in order]
        if max(y_p) < 0.001 and max(y_e) < 1e-4:
            continue
        fig.add_trace(go.Bar(name=TECH_DISPLAY.get(st, st), x=order, y=y_p,
                            marker_color=STORAGE_COLORS.get(st, "#888"),
                            legendgroup=st, showlegend=True), row=1, col=1)
        fig.add_trace(go.Bar(name=TECH_DISPLAY.get(st, st), x=order, y=y_e,
                            marker_color=STORAGE_COLORS.get(st, "#888"),
                            legendgroup=st, showlegend=False), row=1, col=2)
    fig.update_layout(
        barmode="stack",
        title=dict(text="Storage installed capacity — power vs energy",
                   font=dict(family=LATEX_FONT_FAMILY, size=20)),
        font=dict(family=LATEX_FONT_FAMILY, size=11),
        height=520,
        margin=dict(l=70, r=30, t=80, b=130),
        plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="top", y=-0.12),
    )
    fig.update_xaxes(tickangle=45)
    return fig


def fig_storage_soc_panels(scenarios: Dict[str, ScenarioData],
                            storage_tech: str, title: str) -> go.Figure:
    """SoC trajectory small-multiples for a given storage tech across focal scenarios."""
    focal = _focal_scenarios(scenarios)
    n = len(focal)
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    fig = make_subplots(rows=rows, cols=cols,
                        subplot_titles=focal,
                        shared_xaxes=False, shared_yaxes=True,
                        horizontal_spacing=0.06, vertical_spacing=0.12)
    color = STORAGE_COLORS.get(storage_tech, "#00aaa0")
    # rgba with alpha=0.2 for fill (plotly doesn't accept 8-char hex)
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    fillcolor = f"rgba({r},{g},{b},0.20)"
    for idx, name in enumerate(focal):
        r = idx // cols + 1; c = idx % cols + 1
        s = scenarios[name]
        soc = s.eu_storage_level_hourly_twh(storage_tech)
        if soc is None or np.nanmax(soc) < 1e-3:
            fig.add_annotation(text="n/a", xref=f"x{idx+1}", yref=f"y{idx+1}",
                              x=4380, y=0, showarrow=False, row=r, col=c)
            continue
        fig.add_trace(go.Scatter(
            x=np.arange(8760), y=soc, name=name,
            mode="lines", line=dict(color=color, width=0.7),
            fill="tozeroy", fillcolor=fillcolor,
            showlegend=False,
            hovertemplate="%{y:.2f} TWh<extra></extra>",
        ), row=r, col=c)
        fig.update_xaxes(tickvals=MONTH_TICKS[:-1], ticktext=MONTH_LABELS,
                        row=r, col=c)
    fig.update_layout(
        title=dict(text=title, font=dict(family=LATEX_FONT_FAMILY, size=20)),
        font=dict(family=LATEX_FONT_FAMILY, size=10),
        height=240 * rows + 80,
        margin=dict(l=60, r=30, t=80, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
    )
    fig.update_xaxes(showgrid=False, linecolor="#888")
    fig.update_yaxes(showgrid=True, gridcolor="#eee", linecolor="#888",
                    title_text="TWh", title_font=dict(family=LATEX_FONT_FAMILY, size=10))
    return fig


def fig_h2_storage_cycles(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Annual H₂ throughput / energy capacity = cycles/yr."""
    order = _scenario_order(scenarios)
    cycles = []
    for n in order:
        s = scenarios[n]
        cap_twh = s.storage_energy_twh.get("h2_storage", 0.0)
        if cap_twh < 0.001:
            cycles.append(0.0); continue
        if "operation_storage_power_in" not in s.solution.data_vars:
            cycles.append(0.0); continue
        pin = s.solution["operation_storage_power_in"]
        if "h2_storage" not in pin.coords["storage_tech"].values:
            cycles.append(0.0); continue
        throughput_twh = float(pin.sel(storage_tech="h2_storage").sum()) / 1e6
        cycles.append(throughput_twh / cap_twh)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=cycles,
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.2f} cycles/yr<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Hydrogen storage utilisation — cycles per year (throughput / energy capacity)",
        y_title="cycles / yr", x_title="")


# ════════════════════════════════════════════════════════════════════
# §5 — TRANSPORT
# ════════════════════════════════════════════════════════════════════

def fig_h2_pipeline_capacity(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].pipeline_capacity_gw for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.0f} GW<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Hydrogen pipeline installed capacity (Σ over 86 cross-border links)",
        y_title="GW", x_title="")


def fig_h2_pipeline_flow(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].pipeline_flow_twh for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.0f} TWh/yr<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Annual hydrogen transit through cross-border pipelines",
        y_title="TWh/yr (Σ |hourly flow|)", x_title="")


def fig_pipeline_utilization(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    util = []
    for n in order:
        s = scenarios[n]
        if s.pipeline_capacity_gw < 1:
            util.append(0.0); continue
        # Net capacity-hours = capacity_GW × 8760
        util.append(s.pipeline_flow_twh * 1000 / (s.pipeline_capacity_gw * 8760) * 100)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=util,
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.1f}% util<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="H₂ pipeline capacity utilisation rate",
        y_title="% (annual TWh / [GW × 8760])", x_title="")


def fig_h2_corridor_heatmap(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Heatmap: H₂ flow per corridor × scenario (top 25)."""
    order = _scenario_order(scenarios)
    if not order:
        return go.Figure()
    sample = scenarios[order[0]].h2_pipeline_link_flows_twh()
    if not sample:
        fig = go.Figure()
        return _apply_latex_layout(fig, title="(H₂ corridor flow unavailable)")
    links = sorted(sample.keys(), key=lambda l: -sample[l])[:25]
    z = [[scenarios[n].h2_pipeline_link_flows_twh().get(l, 0.0) for n in order]
         for l in links]
    fig = go.Figure(data=go.Heatmap(
        x=order, y=[l.replace("h2_link_", "") for l in links], z=z,
        colorscale="Viridis", colorbar=dict(title="TWh/yr"),
        hovertemplate="<b>%{x}</b><br>%{y}: %{z:.1f} TWh<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Annual H₂ pipeline flow per corridor (top 25 by max scenario)",
        y_title="Corridor", x_title="", height=640)


def fig_electric_line_capacity(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].electric_line_capacity_gw for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.0f} GW<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Cross-border electric line installed capacity",
        y_title="GW (Σ over 86 NTC links)", x_title="")


def fig_electric_line_flow(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].electric_line_flow_twh for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.0f} TWh<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Annual cross-border electricity transit",
        y_title="TWh/yr (Σ |hourly flow|)", x_title="")


# ════════════════════════════════════════════════════════════════════
# §6 — HYDROGEN (deep focus)
# ════════════════════════════════════════════════════════════════════

def fig_h2_supply_capacity_stack(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for tech in ("electrolysis", "ATR_biomethane"):
        fig.add_trace(go.Bar(
            name=TECH_DISPLAY[tech], x=order,
            y=[scenarios[n].capacities_gw.get(tech, 0.0) for n in order],
            marker_color=TECH_COLORS[tech]))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="Hydrogen supply CAPACITY: Electrolyser vs ATR",
        y_title="GW (H₂ output equivalent)", x_title="")


def fig_h2_supply_dispatch_stack(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for tech, label in (("electrolysis", "Electrolyser"),
                       ("ATR_biomethane", "ATR (CH₄→H₂)")):
        y = [getattr(scenarios[n], f"{tech.replace('electrolysis','electrolysis').replace('ATR_biomethane','atr')}_dispatch_twh")
             if hasattr(scenarios[n], f"{tech.replace('electrolysis','electrolysis').replace('ATR_biomethane','atr')}_dispatch_twh") else 0
             for n in order]
        # Direct lookup:
        if tech == "electrolysis":
            y = [scenarios[n].electrolysis_dispatch_twh for n in order]
        else:
            y = [scenarios[n].atr_dispatch_twh for n in order]
        fig.add_trace(go.Bar(name=label, x=order, y=y,
                            marker_color=TECH_COLORS[tech]))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="Hydrogen supply DISPATCH (annual TWh)",
        y_title="TWh H₂/yr", x_title="")


def fig_h2_balance_panels(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Hourly H₂ balance, EU-aggregate, per focal scenario.

    Supply side (positive): electrolysis, ATR_biomethane, H₂ storage discharge.
    Sink side (negative): H₂ demand, H₂ CCGT consumption, storage charge.
    """
    focal = _focal_scenarios(scenarios)
    n = len(focal)
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=focal,
                        shared_yaxes=True, horizontal_spacing=0.06, vertical_spacing=0.12)

    SUPPLY = [("electrolysis", "#0080a0"), ("ATR_biomethane", "#80c050")]
    legend_shown: set = set()
    for idx, name in enumerate(focal):
        r = idx // cols + 1; c = idx % cols + 1
        s = scenarios[name]
        # 7-day rolling
        for t, color in SUPPLY:
            arr = s.eu_dispatch_hourly_gw([t]).get(t)
            if arr is None: continue
            sm = pd.Series(arr).rolling(168, min_periods=1).mean().values
            show = t not in legend_shown
            legend_shown.add(t)
            fig.add_trace(go.Scatter(
                x=np.arange(8760), y=sm, name=TECH_DISPLAY.get(t, t),
                mode="lines", stackgroup="supply", line=dict(width=0),
                fillcolor=color, legendgroup=t, showlegend=show,
            ), row=r, col=c)
        # Demand line
        dem = pd.Series(s.h2_demand_hourly_eu).rolling(168, min_periods=1).mean().values
        fig.add_trace(go.Scatter(
            x=np.arange(8760), y=dem, name="H₂ demand",
            mode="lines", line=dict(color="#111", width=1.2),
            legendgroup="dem", showlegend=("H₂ demand" not in legend_shown),
        ), row=r, col=c)
        legend_shown.add("H₂ demand")
        fig.update_xaxes(tickvals=MONTH_TICKS[:-1], ticktext=MONTH_LABELS, row=r, col=c)

    fig.update_layout(
        title=dict(text="Hydrogen hourly supply vs demand (7-day rolling, EU-aggregate)",
                   font=dict(family=LATEX_FONT_FAMILY, size=20)),
        font=dict(family=LATEX_FONT_FAMILY, size=10),
        height=240 * rows + 80,
        margin=dict(l=60, r=30, t=80, b=50),
        plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="top", y=-0.06),
    )
    fig.update_xaxes(showgrid=False, linecolor="#888")
    fig.update_yaxes(showgrid=True, gridcolor="#eee", linecolor="#888",
                    title_text="GW", title_font=dict(family=LATEX_FONT_FAMILY, size=10))
    return fig


def _subsample_dc(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Subsample a duration curve while preserving the top 100 ranks at full
    resolution. Returns (x_ranks, y_values).
    """
    if _LITE_BOX_STRIDE <= 1:
        return np.arange(len(arr)), arr
    head_n = min(100, len(arr))
    head_x = np.arange(head_n)
    tail_x = np.arange(head_n, len(arr), _LITE_BOX_STRIDE)
    x = np.concatenate([head_x, tail_x])
    y = np.concatenate([arr[:head_n], arr[head_n::_LITE_BOX_STRIDE]])
    return x, y


def fig_h2_shadow_price_duration_curve(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Raw hourly H₂ shadow prices, sorted descending — one curve per scenario."""
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for n in order:
        prices = scenarios[n].h2_shadow_prices
        if len(prices) == 0: continue
        sd = np.sort(prices)[::-1]
        sd_clip = np.minimum(sd, 500)
        x, y = _subsample_dc(sd_clip)
        fig.add_trace(go.Scatter(
            x=x, y=y, name=n, mode="lines",
            line=dict(color=_scenario_color(n), width=0.7),
            hovertemplate=f"<b>{n}</b><br>%{{y:.0f}} €/MWh at rank %{{x}}<extra></extra>",
        ))
    note = f" (lite: top-100 raw + ×{_LITE_BOX_STRIDE} stride)" if _LITE_BOX_STRIDE > 1 else ""
    return _apply_latex_layout(fig,
        title=f"Hydrogen shadow price duration curve (raw hourly × country, sorted descending){note}",
        y_title="€/MWh of H₂ (clipped at 500)",
        x_title="Hour-country rank (descending)",
        height=520)


def fig_h2_shadow_price_box_raw(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """RAW hourly H₂ shadow-price box plot per scenario.

    Simon's explicit request: keep the full hourly variability — reflects
    VRE / electrolyser stress hours. File size cost is acceptable.
    """
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for n in order:
        prices = scenarios[n].h2_shadow_prices
        if len(prices) == 0: continue
        capped = prices[prices < 500]  # visualisation cap (load-shed spikes ~ 30k €/MWh)
        if _LITE_BOX_STRIDE > 1:
            capped = capped[::_LITE_BOX_STRIDE]
        fig.add_trace(go.Box(
            y=capped, name=n,
            marker_color=_scenario_color(n),
            boxpoints=False, hoverinfo="y+name",
        ))
    fig.update_layout(showlegend=False)
    note = f" (×{_LITE_BOX_STRIDE} subsample)" if _LITE_BOX_STRIDE > 1 else ""
    return _apply_latex_layout(fig,
        title=f"Hourly H₂ shadow price distribution — raw hourly × country (clipped at 500 €/MWh){note}",
        y_title="€/MWh of H₂", x_title="", height=560)


def fig_elec_shadow_price_duration_curve(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for n in order:
        prices = scenarios[n].elec_shadow_prices
        if len(prices) == 0: continue
        sd = np.sort(prices)[::-1]
        sd_clip = np.minimum(sd, 500)
        x, y = _subsample_dc(sd_clip)
        fig.add_trace(go.Scatter(
            x=x, y=y, name=n, mode="lines",
            line=dict(color=_scenario_color(n), width=0.7),
        ))
    note = f" (lite: top-100 raw + ×{_LITE_BOX_STRIDE} stride)" if _LITE_BOX_STRIDE > 1 else ""
    return _apply_latex_layout(fig,
        title=f"Electricity shadow price duration curve (raw hourly × country){note}",
        y_title="€/MWh_e (clipped at 500)",
        x_title="Hour-country rank (descending)",
        height=520)


def fig_elec_shadow_price_box_raw(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    for n in order:
        prices = scenarios[n].elec_shadow_prices
        if len(prices) == 0: continue
        capped = prices[prices < 500]
        if _LITE_BOX_STRIDE > 1:
            capped = capped[::_LITE_BOX_STRIDE]
        fig.add_trace(go.Box(
            y=capped, name=n, marker_color=_scenario_color(n),
            boxpoints=False, hoverinfo="y+name",
        ))
    fig.update_layout(showlegend=False)
    note = f" (×{_LITE_BOX_STRIDE} subsample)" if _LITE_BOX_STRIDE > 1 else ""
    return _apply_latex_layout(fig,
        title=f"Hourly electricity shadow price distribution — raw hourly × country{note}",
        y_title="€/MWh_e (clipped at 500)", x_title="", height=560)


def fig_electrolyser_load_factor(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    lf = []
    for n in order:
        s = scenarios[n]
        cap = s.capacities_gw.get("electrolysis", 0.0)
        if cap < 0.1:
            lf.append(0.0); continue
        # H2 output TWh / (cap GW × 8760 h) × 100
        lf.append(s.electrolysis_dispatch_twh * 1000 / (cap * 8760) * 100)
    fig.add_trace(go.Bar(x=order, y=lf,
                        marker_color=[_scenario_color(n) for n in order],
                        hovertemplate="<b>%{x}</b><br>%{y:.1f}%<extra></extra>"))
    return _apply_latex_layout(fig,
        title="Electrolyser annual load factor (utilization)",
        y_title="% capacity factor", x_title="")


def fig_atr_load_factor(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    lf = []
    for n in order:
        s = scenarios[n]
        cap = s.capacities_gw.get("ATR_biomethane", 0.0)
        if cap < 0.1:
            lf.append(0.0); continue
        lf.append(s.atr_dispatch_twh * 1000 / (cap * 8760) * 100)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=order, y=lf,
                        marker_color=[_scenario_color(n) for n in order],
                        hovertemplate="<b>%{x}</b><br>%{y:.1f}%<extra></extra>"))
    return _apply_latex_layout(fig,
        title="ATR (CH₄→H₂) annual load factor",
        y_title="% capacity factor", x_title="")


def fig_electrolyser_dispatch_panels(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Electrolyser hourly utilization heatmap (week-of-year × hour-of-day) for focal scenarios."""
    focal = _focal_scenarios(scenarios)
    n = len(focal)
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=focal,
                        horizontal_spacing=0.07, vertical_spacing=0.13)
    for idx, name in enumerate(focal):
        r = idx // cols + 1; c = idx % cols + 1
        s = scenarios[name]
        cap = s.capacities_gw.get("electrolysis", 0.0)
        if cap < 0.1:
            continue
        arr = s.eu_dispatch_hourly_gw(["electrolysis"]).get("electrolysis")
        if arr is None:
            continue
        # Reshape to (52 weeks, 168 hours-of-week). Pad/truncate to 52×168=8736.
        full = np.zeros(52 * 168)
        full[:min(len(arr), len(full))] = arr[:min(len(arr), len(full))]
        mat = full.reshape(52, 168) / cap * 100  # % utilization
        fig.add_trace(go.Heatmap(
            z=mat, colorscale="Viridis", zmin=0, zmax=100,
            colorbar=dict(title="%") if idx == 0 else None,
            showscale=(idx == 0),
            hovertemplate=f"<b>{name}</b><br>Week %{{y}}, Hour-of-week %{{x}}<br>%{{z:.0f}}%<extra></extra>",
        ), row=r, col=c)
        fig.update_xaxes(title_text="hour of week", row=r, col=c,
                        tickfont=dict(family=LATEX_FONT_FAMILY, size=8))
        fig.update_yaxes(title_text="week", row=r, col=c,
                        tickfont=dict(family=LATEX_FONT_FAMILY, size=8))
    fig.update_layout(
        title=dict(text="Electrolyser utilisation — week × hour-of-week heatmap (EU-aggregate)",
                   font=dict(family=LATEX_FONT_FAMILY, size=20)),
        font=dict(family=LATEX_FONT_FAMILY, size=10),
        height=300 * rows + 80,
        margin=dict(l=60, r=30, t=80, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
    )
    return fig


# ════════════════════════════════════════════════════════════════════
# §7 — COSTS & EMISSIONS
# ════════════════════════════════════════════════════════════════════

def fig_cost_breakdown(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    components = [
        ("operation_conversion_costs",   "Op. Conversion",  "#1f77b4"),
        ("operation_storage_costs",      "Op. Storage",     "#ff7f0e"),
        ("operation_transport_costs",    "Op. Transport",   "#2ca02c"),
        ("planning_conversion_costs",    "Pl. Conversion",  "#9467bd"),
        ("planning_storage_costs",       "Pl. Storage",     "#8c564b"),
        ("planning_transport_costs",     "Pl. Transport",   "#e377c2"),
        ("operation_load_shedding_costs","Load shed",       "#d62728"),
        ("operation_spillage_costs",     "Spillage",        "#7f7f7f"),
    ]
    fig = go.Figure()
    for key, label, color in components:
        y = [scenarios[n].cost_breakdown_geur.get(key, 0.0) for n in order]
        if max(y) < 0.01: continue
        fig.add_trace(go.Bar(name=label, x=order, y=y, marker_color=color))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="System-cost component breakdown",
        y_title="G€/yr", x_title="")


def fig_cost_per_mwh_breakdown(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    """Same cost breakdown but normalized per total final MWh."""
    order = _scenario_order(scenarios)
    components = [
        ("operation_conversion_costs",   "Op. Conversion",  "#1f77b4"),
        ("operation_storage_costs",      "Op. Storage",     "#ff7f0e"),
        ("operation_transport_costs",    "Op. Transport",   "#2ca02c"),
        ("planning_conversion_costs",    "Pl. Conversion",  "#9467bd"),
        ("planning_storage_costs",       "Pl. Storage",     "#8c564b"),
        ("planning_transport_costs",     "Pl. Transport",   "#e377c2"),
        ("operation_load_shedding_costs","Load shed",       "#d62728"),
        ("operation_spillage_costs",     "Spillage",        "#7f7f7f"),
    ]
    fig = go.Figure()
    for key, label, color in components:
        y = []
        for n in order:
            s = scenarios[n]
            tot = s.total_demand_twh
            y.append(s.cost_breakdown_geur.get(key, 0.0) * 1000 / tot if tot > 0 else 0)
        if max(y) < 0.01: continue
        fig.add_trace(go.Bar(name=label, x=order, y=y, marker_color=color))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="System-cost breakdown per MWh final demand",
        y_title="€/MWh (elec + H₂)", x_title="")


def fig_gas_dispatch_emissions(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    gas_twh = [scenarios[n].gas_dispatch_twh for n in order]
    co2_mt = [v * CO2_INTENSITY_GAS_T_PER_MWHE for v in gas_twh]
    fig.add_trace(go.Bar(name="Fossil gas dispatch", x=order, y=gas_twh,
                        marker_color="#888"), secondary_y=False)
    fig.add_trace(go.Scatter(name="CO₂ emissions", x=order, y=co2_mt,
                            mode="markers+lines", marker=dict(size=11, color="#d62728")),
                  secondary_y=True)
    fig.update_yaxes(title_text="Gas dispatched (TWh/yr)", secondary_y=False,
                    title_font_family=LATEX_FONT_FAMILY)
    fig.update_yaxes(title_text="CO₂ (MtCO₂/yr)", secondary_y=True,
                    title_font_family=LATEX_FONT_FAMILY)
    return _apply_latex_layout(fig,
        title="Fossil gas dispatch & resulting CO₂ emissions",
        y_title="", x_title="")


def fig_load_shedding(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Electricity", x=order,
                        y=[scenarios[n].load_shedding_elec_twh * 1000 for n in order],
                        marker_color="#d62728"))
    fig.add_trace(go.Bar(name="Hydrogen", x=order,
                        y=[scenarios[n].load_shedding_h2_twh * 1000 for n in order],
                        marker_color="#ff7f0e"))
    fig.update_layout(barmode="stack")
    return _apply_latex_layout(fig,
        title="Annual load shedding by resource",
        y_title="GWh shed / yr", x_title="")


def fig_spillage(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order, y=[scenarios[n].spillage_twh for n in order],
        marker_color=[_scenario_color(n) for n in order],
        hovertemplate="<b>%{x}</b><br>%{y:.1f} TWh<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Electricity spillage (curtailment)",
        y_title="TWh/yr", x_title="")


# ════════════════════════════════════════════════════════════════════
# §8 — PER-COUNTRY
# ════════════════════════════════════════════════════════════════════

def fig_country_capacity_heatmap(scenarios: Dict[str, ScenarioData],
                                  tech: str) -> go.Figure:
    order = _scenario_order(scenarios)
    all_countries = sorted(set().union(*(s.per_country_capacities.keys()
                                          for s in scenarios.values())))
    z = [[scenarios[n].per_country_capacities.get(c, {}).get(tech, 0.0)
          for n in order]
         for c in all_countries]
    label = TECH_DISPLAY.get(tech, tech)
    fig = go.Figure(data=go.Heatmap(
        x=order, y=all_countries, z=z, colorscale="Viridis",
        colorbar=dict(title="GW"),
        hovertemplate=f"<b>%{{x}}</b><br>%{{y}}: %{{z:.2f}} GW<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title=f"Per-country installed capacity — {label}",
        y_title="Country", x_title="", height=640)


def fig_country_load_shedding(scenarios: Dict[str, ScenarioData]) -> go.Figure:
    order = _scenario_order(scenarios)
    all_countries = sorted(set().union(*(s.per_country_load_shedding_h.keys()
                                         for s in scenarios.values())))
    if not all_countries:
        fig = go.Figure()
        return _apply_latex_layout(fig, title="No electricity load shedding across scenarios")
    z = [[scenarios[n].per_country_load_shedding_h.get(c, 0)
          for n in order] for c in all_countries]
    fig = go.Figure(data=go.Heatmap(
        x=order, y=all_countries, z=z, colorscale="Reds",
        colorbar=dict(title="hours"),
        hovertemplate="<b>%{x}</b><br>%{y}: %{z:.0f} h<extra></extra>",
    ))
    return _apply_latex_layout(fig,
        title="Hours of electricity load shedding per country",
        y_title="Country", x_title="", height=620)


# ════════════════════════════════════════════════════════════════════
# §9 — MAPS (Natural Earth 110 m choropleth + network)
# ════════════════════════════════════════════════════════════════════

# Approximate country centroids for placing pipeline/line endpoints when
# we can't compute them from the shapefile (used for network maps).
COUNTRY_LATLON = {
    "AT": (47.5, 14.5),  "BE": (50.6,  4.7),  "BG": (42.7, 25.5),
    "CH": (46.8,  8.2),  "CY": (35.1, 33.4),  "CZ": (49.8, 15.5),
    "DE": (51.2, 10.4),  "DK": (56.0,  9.5),  "EE": (58.7, 25.6),
    "ES": (40.2, -3.7),  "FI": (64.2, 26.0),  "FR": (46.6,  2.2),
    "GB": (53.0, -2.0),  "GR": (39.0, 22.0),  "HR": (45.1, 15.5),
    "HU": (47.2, 19.5),  "IE": (53.1, -7.7),  "IT": (42.5, 12.6),
    "LT": (55.3, 23.9),  "LU": (49.8,  6.1),  "LV": (56.9, 24.6),
    "MT": (35.9, 14.4),  "NL": (52.1,  5.3),  "NO": (61.0,  9.0),
    "PL": (52.0, 19.0),  "PT": (39.5, -8.0),  "RO": (45.9, 24.9),
    "SE": (62.0, 15.0),  "SI": (46.1, 14.8),  "SK": (48.7, 19.7),
}


def _load_europe_geojson() -> dict:
    """Load the Natural Earth shapefile, filter to modelled countries, return GeoJSON."""
    global _EUROPE_GEOJSON_CACHE
    if _EUROPE_GEOJSON_CACHE is not None:
        return _EUROPE_GEOJSON_CACHE
    if _SHAPEFILE_PATH is None or not _SHAPEFILE_PATH.exists():
        logger.warning("Shapefile not available (path=%s) — maps will be skipped",
                      _SHAPEFILE_PATH)
        return None
    try:
        import geopandas as gpd
    except ImportError:
        logger.warning("geopandas not installed — maps will be skipped")
        return None
    gdf = gpd.read_file(_SHAPEFILE_PATH)
    iso_col = "ISO_A2_EH" if "ISO_A2_EH" in gdf.columns else "ISO_A2"
    keep = list(COUNTRY_LATLON.keys())
    gdf_sub = gdf[gdf[iso_col].isin(keep)].copy()
    gdf_sub["iso"] = gdf_sub[iso_col]
    _EUROPE_GEOJSON_CACHE = json.loads(gdf_sub[["iso", "geometry"]].to_json())
    return _EUROPE_GEOJSON_CACHE


def _country_values_for_tech(scenario: "ScenarioData", tech: str) -> Dict[str, float]:
    return {area: caps.get(tech, 0.0)
            for area, caps in scenario.per_country_capacities.items()}


def _country_net_h2_imports_twh(scenario: "ScenarioData") -> Dict[str, float]:
    """Net H₂ imports per country (TWh/yr). +imports, −exports."""
    out: Dict[str, float] = {}
    sol = scenario.solution
    if "operation_transport_net_generation" not in sol.data_vars:
        return out
    ng = sol["operation_transport_net_generation"]
    if "h2_pipeline" not in ng.coords["transport_tech"].values:
        return out
    if "hydrogen" not in ng.coords["resource"].values:
        return out
    sub = ng.sel(transport_tech="h2_pipeline", resource="hydrogen")
    for area in sub.coords["area"].values:
        out[str(area)] = float(sub.sel(area=area).sum()) / 1e6
    return out


def _country_net_elec_imports_twh(scenario: "ScenarioData") -> Dict[str, float]:
    out: Dict[str, float] = {}
    sol = scenario.solution
    if "operation_transport_net_generation" not in sol.data_vars:
        return out
    ng = sol["operation_transport_net_generation"]
    if "electric_line" not in ng.coords["transport_tech"].values:
        return out
    sub = ng.sel(transport_tech="electric_line", resource="electricity")
    for area in sub.coords["area"].values:
        out[str(area)] = float(sub.sel(area=area).sum()) / 1e6
    return out


def _make_choropleth_panels(scenarios: Dict[str, ScenarioData],
                            focal: List[str],
                            value_fn,
                            colorscale: str,
                            title: str,
                            colorbar_title: str,
                            sym_cmap: bool = False,
                            cmin: Optional[float] = None,
                            cmax: Optional[float] = None) -> Optional[go.Figure]:
    """Generic helper: small-multiples choropleth across focal scenarios."""
    gj = _load_europe_geojson()
    if gj is None: return None
    n = len(focal)
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols

    # First pass: gather all values to set a common colour scale
    all_vals = []
    per_scen_vals: Dict[str, Dict[str, float]] = {}
    for name in focal:
        d = value_fn(scenarios[name])
        per_scen_vals[name] = d
        all_vals.extend(d.values())
    if not all_vals:
        return None
    if cmin is None:
        if sym_cmap:
            m = max(abs(min(all_vals)), abs(max(all_vals)))
            cmin, cmax = -m, m
        else:
            cmin, cmax = 0.0, max(all_vals)

    fig = make_subplots(
        rows=rows, cols=cols,
        subplot_titles=focal,
        specs=[[{"type": "geo"}] * cols for _ in range(rows)],
        horizontal_spacing=0.04, vertical_spacing=0.10,
    )
    for idx, name in enumerate(focal):
        r = idx // cols + 1; c = idx % cols + 1
        d = per_scen_vals[name]
        locs = list(d.keys())
        zs   = [d[k] for k in locs]
        fig.add_trace(go.Choropleth(
            geojson=gj,
            locations=locs,
            z=zs,
            featureidkey="properties.iso",
            colorscale=colorscale,
            zmin=cmin, zmax=cmax,
            marker_line_color="#444", marker_line_width=0.3,
            showscale=(idx == 0),
            colorbar=dict(title=colorbar_title, len=0.85, x=1.02) if idx == 0 else None,
            hovertemplate=f"<b>{name}</b><br>%{{location}}: %{{z:.2f}}<extra></extra>",
        ), row=r, col=c)
        # Per-subplot geo
        fig.update_geos(
            scope="europe", projection_type="mercator",
            lonaxis_range=[-11, 31], lataxis_range=[34, 66],
            showcoastlines=True, coastlinecolor="#888", coastlinewidth=0.4,
            showland=True, landcolor="#f8f8f8",
            showocean=True, oceancolor="#e7eef5",
            row=r, col=c,
        )

    fig.update_layout(
        title=dict(text=title, font=dict(family=LATEX_FONT_FAMILY, size=20)),
        font=dict(family=LATEX_FONT_FAMILY, size=10),
        height=380 * rows + 80,
        margin=dict(l=20, r=80, t=80, b=20),
        paper_bgcolor="white",
    )
    return fig


# ── Pie-chart-on-map helpers ───────────────────────────────────────────

_MAP_REF_LAT = 50.0   # map-center latitude — pies use a single stretch so they all look the same shape


def _country_pie_wedges(lat: float, lon: float, radius_deg: float,
                        shares: List[float], n_pts: int = 22) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Build pie wedges centered at (lat, lon).

    shares need not sum to 1; they're normalised internally. Returns one closed
    polygon per share (lons, lats arrays). Uses a fixed map-center longitude
    stretch so every pie looks the same shape regardless of latitude.
    """
    pos = [s for s in shares if s > 0]
    if not pos:
        return [(np.array([]), np.array([]))] * len(shares)
    total = sum(pos)
    lon_stretch = 1.0 / np.cos(np.radians(_MAP_REF_LAT))
    wedges = []
    cur = 0.0
    for s in shares:
        if s <= 0:
            wedges.append((np.array([]), np.array([])))
            continue
        end = cur + (s / total) * 2 * np.pi
        thetas = np.linspace(cur, end, max(4, int(n_pts * s / total) + 2))
        arc_lons = lon + radius_deg * lon_stretch * np.sin(thetas)
        arc_lats = lat + radius_deg * np.cos(thetas)
        lons = np.concatenate([[lon], arc_lons, [lon]])
        lats = np.concatenate([[lat], arc_lats, [lat]])
        wedges.append((lons, lats))
        cur = end
    return wedges


def _arrow_polygon(lat_a: float, lon_a: float, lat_b: float, lon_b: float,
                   head_size_deg: float = 0.8,
                   along_position: float = 0.85
                  ) -> Tuple[List[float], List[float]]:
    """Closed arrowhead triangle at a fractional position along A→B, pointing toward B."""
    lon_stretch = 1.0 / np.cos(np.radians(_MAP_REF_LAT))
    # Projected-coord direction vector (account for Mercator)
    dlon = (lon_b - lon_a) / lon_stretch
    dlat = lat_b - lat_a
    norm = np.sqrt(dlon * dlon + dlat * dlat)
    if norm < 1e-6:
        return [], []
    ux, uy = dlon / norm, dlat / norm
    px, py = -uy, ux                       # perpendicular
    head_lon = lon_a + along_position * (lon_b - lon_a)
    head_lat = lat_a + along_position * (lat_b - lat_a)
    # Triangle: tip + two back vertices half head_size_deg behind, half-width sideways
    tip = (head_lon, head_lat)
    back_l = (head_lon - head_size_deg * (ux + 0.55 * px) * lon_stretch,
              head_lat - head_size_deg * (uy + 0.55 * py))
    back_r = (head_lon - head_size_deg * (ux - 0.55 * px) * lon_stretch,
              head_lat - head_size_deg * (uy - 0.55 * py))
    return [tip[0], back_l[0], back_r[0], tip[0]], [tip[1], back_l[1], back_r[1], tip[1]]


def _parse_link_endpoints(link_name: str) -> Optional[Tuple[str, str]]:
    body = link_name.replace("h2_link_", "").replace("electric_link_", "")
    for sep in ("_", "-"):
        if sep in body:
            parts = body.split(sep)
            if len(parts) == 2 and parts[0] in COUNTRY_LATLON and parts[1] in COUNTRY_LATLON:
                return parts[0], parts[1]
    return None


def _net_link_flow_twh(s: ScenarioData, link_name: str, tech: str) -> float:
    """Signed annual net flow (TWh). Positive = nominal A→B direction in link name."""
    if "operation_transport_power" not in s.solution.data_vars:
        return 0.0
    tpw = s.solution["operation_transport_power"]
    if tech not in tpw.coords["transport_tech"].values:
        return 0.0
    if link_name not in tpw.coords["link"].values:
        return 0.0
    return float(tpw.sel(transport_tech=tech, link=link_name).sum()) / 1e6


def _draw_h2_system_on_subplot(fig: go.Figure, s: ScenarioData, gj: dict,
                                max_cap_gw: float, max_flow_twh: float,
                                legend_shown: set,
                                row: Optional[int] = None,
                                col: Optional[int] = None) -> None:
    """Draw the H₂ system map for one scenario into one geo subplot.

    - Country backgrounds via choropleth (constant z, neutral grey)
    - Pipeline arrows: line + arrowhead per link, width ∝ |net flow|
    - Per-country pies: electrolyser/ATR shares, radius ∝ √(GW)
    - Country code labels above each pie
    """
    sp_kwargs = {"row": row, "col": col} if row is not None else {}

    # Country background — neutral fill
    fig.add_trace(go.Choropleth(
        geojson=gj, locations=list(COUNTRY_LATLON.keys()),
        z=[0] * len(COUNTRY_LATLON), featureidkey="properties.iso",
        colorscale=[[0, "#fafafa"], [1, "#fafafa"]], showscale=False,
        marker_line_color="#999", marker_line_width=0.4,
        hoverinfo="skip", showlegend=False,
    ), **sp_kwargs)

    # Arrows for H₂ pipeline net flows
    if "operation_transport_power" in s.solution.data_vars:
        tpw = s.solution["operation_transport_power"]
        if "h2_pipeline" in tpw.coords["transport_tech"].values:
            for link in tpw.coords["link"].values:
                ln = str(link)
                if not ln.startswith("h2_link_"):
                    continue
                ends = _parse_link_endpoints(ln)
                if ends is None:
                    continue
                a, b = ends
                net = _net_link_flow_twh(s, ln, "h2_pipeline")
                if abs(net) < 0.1:
                    continue
                if net < 0:
                    a, b = b, a; net = -net
                lat_a, lon_a = COUNTRY_LATLON[a]
                lat_b, lon_b = COUNTRY_LATLON[b]
                frac = net / max_flow_twh
                width = 0.5 + 3.5 * frac
                # Line
                fig.add_trace(go.Scattergeo(
                    lon=[lon_a, lon_b], lat=[lat_a, lat_b],
                    mode="lines",
                    line=dict(color="#0e7c7b", width=width),
                    opacity=0.75,
                    hovertemplate=f"<b>{a}→{b}</b><br>{net:.1f} TWh/yr H₂<extra></extra>",
                    showlegend=False,
                ), **sp_kwargs)
                # Arrowhead positioned 1/3 of the way from B back to A so it doesn't get hidden under the destination pie
                hsize = 0.30 + 0.35 * frac
                arr_lons, arr_lats = _arrow_polygon(lat_a, lon_a, lat_b, lon_b,
                                                    head_size_deg=hsize,
                                                    along_position=0.68)
                if arr_lons:
                    fig.add_trace(go.Scattergeo(
                        lon=arr_lons, lat=arr_lats,
                        mode="lines", fill="toself",
                        line=dict(color="#0e7c7b", width=0.3),
                        fillcolor="#0e7c7b", opacity=0.85,
                        hoverinfo="skip", showlegend=False,
                    ), **sp_kwargs)

    # Pies per country: electrolyser + ATR mix
    PIE_COLORS = [("Electrolyser", "#0080a0"), ("ATR (CH₄→H₂)", "#80c050")]
    for area, (lat0, lon0) in COUNTRY_LATLON.items():
        caps = s.per_country_capacities.get(area, {})
        el = caps.get("electrolysis", 0)
        atr = caps.get("ATR_biomethane", 0)
        tot = el + atr
        if tot < 0.05:
            continue
        # Pie radius scaled by √GW so the AREA (visual mass) tracks capacity.
        # max ~ 1.0° lat at the biggest country — keeps neighbours readable.
        radius = 0.35 + 0.95 * np.sqrt(tot / max_cap_gw)
        wedges = _country_pie_wedges(lat0, lon0, radius, [el, atr])
        for (lon_arr, lat_arr), (label, color), gw in zip(wedges, PIE_COLORS, [el, atr]):
            if len(lon_arr) == 0 or gw <= 0:
                continue
            show = label not in legend_shown
            legend_shown.add(label)
            share_pct = gw / tot * 100
            fig.add_trace(go.Scattergeo(
                lon=lon_arr, lat=lat_arr,
                mode="lines", fill="toself",
                line=dict(color="#222", width=0.4),
                fillcolor=color, opacity=0.88,
                name=label, legendgroup=label, showlegend=show,
                hovertemplate=f"<b>{area}</b><br>{label}: %{{customdata:.1f}} GW ({share_pct:.0f}%)<extra></extra>",
                customdata=[gw] * len(lon_arr),
            ), **sp_kwargs)
        # Country code label centered on the pie
        fig.add_trace(go.Scattergeo(
            lon=[lon0], lat=[lat0],
            mode="text", text=[area],
            textfont=dict(size=8, color="#111", family=LATEX_FONT_FAMILY),
            hoverinfo="skip", showlegend=False,
        ), **sp_kwargs)

    fig.update_geos(
        scope="europe", projection_type="mercator",
        lonaxis_range=[-11, 31], lataxis_range=[34, 66],
        showcoastlines=True, coastlinecolor="#888", coastlinewidth=0.4,
        showland=True, landcolor="#f8f8f8",
        showocean=True, oceancolor="#e7eef5",
        **sp_kwargs,
    )


def fig_map_h2_system_panels(scenarios: Dict[str, ScenarioData]) -> Optional[go.Figure]:
    """Small-multiples maps for focal scenarios.

    For each scenario:
      • pies on each country = electrolyser vs ATR share (radius ∝ √(GW))
      • arrows on each pipeline link = net H₂ flow direction (width ∝ TWh)
    """
    gj = _load_europe_geojson()
    if gj is None:
        return None
    focal = _focal_scenarios(scenarios)
    if not focal:
        return None
    n = len(focal)
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols

    # Compute scale references across focal set
    max_cap, max_flow = 0.0, 0.0
    for name in focal:
        s = scenarios[name]
        for area in COUNTRY_LATLON:
            caps = s.per_country_capacities.get(area, {})
            max_cap = max(max_cap, caps.get("electrolysis", 0) + caps.get("ATR_biomethane", 0))
        if "operation_transport_power" in s.solution.data_vars:
            tpw = s.solution["operation_transport_power"]
            if "h2_pipeline" in tpw.coords["transport_tech"].values:
                for link in tpw.coords["link"].values:
                    ln = str(link)
                    if not ln.startswith("h2_link_"):
                        continue
                    max_flow = max(max_flow, abs(_net_link_flow_twh(s, ln, "h2_pipeline")))
    if max_cap  <= 0: max_cap = 1.0
    if max_flow <= 0: max_flow = 1.0

    fig = make_subplots(
        rows=rows, cols=cols, subplot_titles=focal,
        specs=[[{"type": "geo"}] * cols for _ in range(rows)],
        horizontal_spacing=0.02, vertical_spacing=0.07,
    )
    legend_shown: set = set()
    for idx, name in enumerate(focal):
        r = idx // cols + 1; c = idx % cols + 1
        _draw_h2_system_on_subplot(fig, scenarios[name], gj, max_cap, max_flow,
                                   legend_shown, row=r, col=c)

    fig.update_layout(
        title=dict(text=("Hydrogen system per scenario — camemberts: electrolyser vs ATR share "
                        "(radius ∝ √GW) · flèches: net H₂ pipeline flow (width ∝ TWh/yr)"),
                   font=dict(family=LATEX_FONT_FAMILY, size=17)),
        font=dict(family=LATEX_FONT_FAMILY, size=10),
        width=460 * cols, height=420 * rows + 80,
        margin=dict(l=20, r=20, t=90, b=50),
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="top", y=-0.02,
                    font=dict(family=LATEX_FONT_FAMILY, size=11)),
    )
    return fig


def fig_map_capacity(scenarios: Dict[str, ScenarioData], tech: str) -> Optional[go.Figure]:
    focal = _focal_scenarios(scenarios)
    label = TECH_DISPLAY.get(tech, tech)
    return _make_choropleth_panels(
        scenarios, focal,
        value_fn=lambda s: _country_values_for_tech(s, tech),
        colorscale="Viridis",
        title=f"Per-country installed capacity — {label}",
        colorbar_title="GW",
    )


def fig_map_h2_net_imports(scenarios: Dict[str, ScenarioData]) -> Optional[go.Figure]:
    focal = _focal_scenarios(scenarios)
    return _make_choropleth_panels(
        scenarios, focal,
        value_fn=_country_net_h2_imports_twh,
        colorscale="RdBu_r",
        title="Net hydrogen imports per country (positive = importer)",
        colorbar_title="TWh/yr",
        sym_cmap=True,
    )


def fig_map_elec_net_imports(scenarios: Dict[str, ScenarioData]) -> Optional[go.Figure]:
    focal = _focal_scenarios(scenarios)
    return _make_choropleth_panels(
        scenarios, focal,
        value_fn=_country_net_elec_imports_twh,
        colorscale="RdBu_r",
        title="Net electricity imports per country (positive = importer)",
        colorbar_title="TWh/yr",
        sym_cmap=True,
    )


def fig_map_load_shedding(scenarios: Dict[str, ScenarioData]) -> Optional[go.Figure]:
    focal = _focal_scenarios(scenarios)
    return _make_choropleth_panels(
        scenarios, focal,
        value_fn=lambda s: {k: float(v) for k, v in s.per_country_load_shedding_h.items()},
        colorscale="Reds",
        title="Electricity load-shedding hours per country",
        colorbar_title="hours/yr",
    )


def fig_map_h2_system_single(scenarios: Dict[str, ScenarioData],
                              scenario_name: str) -> Optional[go.Figure]:
    """Single-scenario detailed H₂ system map (pies + arrows).

    Same content as the small-multiples panels but at full size so labels and
    arrow widths read cleanly. One per focal scenario.
    """
    gj = _load_europe_geojson()
    if gj is None or scenario_name not in scenarios:
        return None
    s = scenarios[scenario_name]
    # Self-relative scales (max within this scenario)
    max_cap = 0.0
    for area in COUNTRY_LATLON:
        caps = s.per_country_capacities.get(area, {})
        max_cap = max(max_cap, caps.get("electrolysis", 0) + caps.get("ATR_biomethane", 0))
    max_flow = 0.0
    if "operation_transport_power" in s.solution.data_vars:
        tpw = s.solution["operation_transport_power"]
        if "h2_pipeline" in tpw.coords["transport_tech"].values:
            for link in tpw.coords["link"].values:
                ln = str(link)
                if not ln.startswith("h2_link_"):
                    continue
                max_flow = max(max_flow, abs(_net_link_flow_twh(s, ln, "h2_pipeline")))
    if max_cap  <= 0: max_cap = 1.0
    if max_flow <= 0: max_flow = 1.0

    fig = go.Figure()
    _draw_h2_system_on_subplot(fig, s, gj, max_cap, max_flow,
                               legend_shown=set())
    fig.update_layout(
        title=dict(text=f"H₂ system — {scenario_name} "
                        f"(camemberts: electrolyser vs ATR · flèches: net pipeline flow)",
                   font=dict(family=LATEX_FONT_FAMILY, size=18)),
        font=dict(family=LATEX_FONT_FAMILY, size=11),
        width=1100, height=820,
        margin=dict(l=20, r=20, t=70, b=20),
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="top", y=0.02,
                    font=dict(family=LATEX_FONT_FAMILY, size=12)),
    )
    return fig


# ════════════════════════════════════════════════════════════════════
# §10 — SENSITIVITY DELTAS
# ════════════════════════════════════════════════════════════════════

def fig_sensitivity_delta(scenarios: Dict[str, ScenarioData],
                          axis_label: str, pairs: List[tuple]) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    labels, deltas, bio_gws = [], [], []
    for base, variant, label in pairs:
        if base not in scenarios or variant not in scenarios: continue
        d = scenarios[variant].totex_geur - scenarios[base].totex_geur
        bio = scenarios[variant].capacities_gw.get("Biomethane_CCGT", 0.0)
        labels.append(label); deltas.append(d); bio_gws.append(bio)
    fig.add_trace(go.Bar(name="ΔTotex (G€/yr)", x=labels, y=deltas,
                        marker_color=["#d62728" if v > 0 else "#2ca02c" for v in deltas]),
                  secondary_y=False)
    fig.add_trace(go.Scatter(name="Variant Bio-CCGT (GW)", x=labels, y=bio_gws,
                            mode="markers", marker=dict(size=11, color="#41a04f")),
                  secondary_y=True)
    fig.update_yaxes(title_text="ΔTotex (G€/yr)", secondary_y=False)
    fig.update_yaxes(title_text="Bio CCGT (GW)", secondary_y=True)
    return _apply_latex_layout(fig,
        title=f"Sensitivity Δ: {axis_label}",
        y_title="", x_title="")


# ════════════════════════════════════════════════════════════════════
# HTML OUTPUT
# ════════════════════════════════════════════════════════════════════

HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>POMMES CLEVER 2050 — Scenario Observatory</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/aaaakshat/cm-web-fonts@latest/fonts.css">
<style>
  body {
    font-family: 'Computer Modern Serif', 'Latin Modern Roman', 'CMU Serif', Garamond, serif;
    color: #222; max-width: 1500px; margin: 0 auto; padding: 24px;
    background: #fcfcfc;
  }
  h1 { font-weight: normal; font-size: 28pt; text-align: center; margin: 0 0 4px 0; }
  .subtitle { text-align: center; color: #666; font-style: italic; margin-bottom: 36px; font-size: 12pt; }
  h2 { font-weight: normal; font-size: 22pt; border-bottom: 1px solid #999;
       padding-bottom: 4px; margin-top: 64px; }
  h3 { font-weight: normal; font-size: 16pt; color: #444; margin-top: 28px; margin-bottom: 0; }
  .toc { background: #f5f5f5; border: 1px solid #ccc; padding: 16px 24px;
         margin: 24px 0; font-size: 11pt; column-count: 2; }
  .toc ol { margin: 6px 0; }
  .figure { margin: 18px 0; padding: 8px;
            border: 1px solid #e0e0e0; border-radius: 6px;
            background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
  .footer { color: #888; font-size: 9pt; text-align: center; margin-top: 64px;
            border-top: 1px solid #ddd; padding-top: 12px; }
  a { color: #2a5fbf; text-decoration: none; }
  a:hover { text-decoration: underline; }
  /* Scenario tables */
  .table-wrap { overflow-x: auto; }
  .scenario-table {
    width: 100%; border-collapse: collapse; font-size: 9.5pt;
    font-family: 'Computer Modern Serif', 'Latin Modern Roman', 'CMU Serif', Garamond, serif;
  }
  .scenario-table th, .scenario-table td {
    border: 1px solid #d0d0d0; padding: 3px 7px; vertical-align: middle;
  }
  .scenario-table thead { background: #ececec; }
  .scenario-table th { font-weight: normal; text-align: center; }
  .scenario-table tbody tr:nth-child(even) { background: #fafafa; }
  .scenario-table tbody tr:hover { background: #fff5d6; }
  .scenario-table .num { text-align: right; font-variant-numeric: tabular-nums; }
  .scenario-table .scen-name {
    font-family: 'JetBrains Mono', 'Menlo', 'Courier New', monospace;
    font-size: 9pt; white-space: nowrap;
  }
  .scenario-table .swatch {
    display: inline-block; width: 9px; height: 9px; margin-right: 6px;
    border-radius: 2px; vertical-align: middle;
  }
  .table-note {
    font-size: 9.5pt; color: #555; font-style: italic;
    margin: 8px 4px 0 4px; line-height: 1.35;
  }
</style>
</head>
<body>
"""

HTML_FOOTER = """
<div class="footer">
  Generated by <code>generate_scenario_observatory.py</code>
  on {timestamp} from {n_scenarios} scenarios in {diag_dir}
</div>
</body>
</html>"""


def fig_to_div(fig: go.Figure, div_id: str, first: bool = False) -> str:
    include_js = "cdn" if first else False
    return f'<div class="figure">\n{fig.to_html(full_html=False, include_plotlyjs=include_js, div_id=div_id)}\n</div>'


def build_observatory_html(scenarios: Dict[str, ScenarioData],
                            diagnostics_dir: Path,
                            output_path: Path) -> None:

    # Sensitivity pairs
    BIO_PAIRS = [
        ("policy_re",       "policy_re_bioMed",       "Policy: +Bio"),
        ("policy_re_noMin", "policy_re_noMin_bioMed", "Policy/noMin: +Bio"),
        ("policy_nuke",     "policy_nuke_bioMed",     "Policy/nuke: +Bio"),
        ("R0_v1_nuke",      "R0_v1_nuke_bioLow",      "CLEVER/nuke: +Bio"),
    ]
    NUKE_PAIRS = [
        ("policy_re",     "policy_nuke",          "Policy: +Nuclear"),
        ("R0_v1_bioLow",  "R0_v1_nuke_bioLow",    "CLEVER/bio: +Nuclear"),
    ]
    ATR_PAIRS = [
        ("policy_nuke_bioMed",         "policy_nuke_bioMed_atr",         "PolicyNukeBio: +ATR"),
        ("policy_nuke_bioMed_h2HIGH",  "policy_nuke_bioMed_h2HIGH_atr",  "PolicyNukeBio/h2HIGH: +ATR"),
    ]
    CORR_PAIRS = [
        ("policy_re",   "policy_re_corr3x",   "Policy: corr×3"),
        ("policy_nuke", "policy_nuke_corr3x", "PolicyNuke: corr×3"),
        ("R0_v1_nuke",  "R0_v1_nuke_corr3x",  "CLEVERNuke: corr×3"),
    ]
    EL_PAIRS = [
        ("policy_nuke_bioMed", "policy_nuke_bioMed_el700", "El→700€/kW"),
        ("policy_nuke_bioMed", "policy_nuke_bioMed_el900", "El→900€/kW"),
    ]
    H2HIGH_PAIRS = [
        ("policy_nuke_bioMed",     "policy_nuke_bioMed_h2HIGH",     "Demand→high_h2"),
        ("policy_nuke_bioMed_atr", "policy_nuke_bioMed_h2HIGH_atr", "Same + ATR"),
    ]
    CO2_PAIRS = [
        ("policy_nuke_bioMed_atr",       "policy_nuke_bioMed_atr_co2250_nofloor",       "+CO₂250 + nofloor (Policy)"),
        ("R0_v1_nuke_bioLow",            "R0_v1_nuke_bioLow_co2250_nofloor",            "+CO₂250 + nofloor (CLEVER)"),
        ("policy_nuke_bioMed_h2HIGH_atr","policy_nuke_bioMed_h2HIGH_atr_co2250_nofloor","+CO₂250 + nofloor (HighH₂)"),
    ]

    # ── Build the figure-list as (section_id, section_label, key, fig) ──
    # Each entry is appended in render order; key is a stable slug used for
    # export filenames.
    items: List[Tuple[str, str, str, go.Figure]] = []
    def push(section_id: str, section_label: str, key: str, fig: Optional[go.Figure]):
        if fig is None:
            return
        items.append((section_id, section_label, key, fig))

    # §1
    push("headline", "1. Headline comparison", "01_totex",        fig_totex_per_scenario(scenarios))
    push("headline", "1. Headline comparison", "02_eur_per_mwh",  fig_eur_per_mwh(scenarios))
    push("headline", "1. Headline comparison", "03_demand_stack", fig_demand_stack(scenarios))
    push("headline", "1. Headline comparison", "04_vre_share",    fig_vre_share(scenarios))

    # §2
    push("capacity", "2. Conversion capacities", "05_capacity_heatmap",     fig_capacity_heatmap(scenarios))
    push("capacity", "2. Conversion capacities", "06_dispatchable_stack",   fig_dispatchable_stacked(scenarios))
    push("capacity", "2. Conversion capacities", "07_vre_stack",            fig_vre_stacked(scenarios))

    # §3
    push("hourly-elec", "3. Hourly electricity dispatch", "08_annual_dispatch_panels", fig_annual_dispatch_panels(scenarios))
    push("hourly-elec", "3. Hourly electricity dispatch", "09_winter_week_panels",     fig_winter_week_panels(scenarios))
    push("hourly-elec", "3. Hourly electricity dispatch", "10_summer_week_panels",     fig_summer_week_panels(scenarios))
    push("hourly-elec", "3. Hourly electricity dispatch", "11_residual_load_duration", fig_residual_load_duration(scenarios))

    # §4
    push("storage", "4. Storage", "12_storage_cap_paired",    fig_storage_capacity_paired(scenarios))
    push("storage", "4. Storage", "13_soc_h2",                fig_storage_soc_panels(scenarios, "h2_storage",
        "Hydrogen storage state-of-charge (EU-aggregate, TWh)"))
    push("storage", "4. Storage", "14_soc_battery4h",         fig_storage_soc_panels(scenarios, "Battery_4h",
        "4-hour battery state-of-charge (EU-aggregate, TWh)"))
    push("storage", "4. Storage", "15_soc_pumped_hydro",      fig_storage_soc_panels(scenarios, "Pumped_Hydro",
        "Pumped-hydro state-of-charge (EU-aggregate, TWh)"))
    push("storage", "4. Storage", "16_h2_storage_cycles",     fig_h2_storage_cycles(scenarios))

    # §5
    push("transport", "5. Transport — H₂ & electricity", "17_h2_pipeline_cap",        fig_h2_pipeline_capacity(scenarios))
    push("transport", "5. Transport — H₂ & electricity", "18_h2_pipeline_flow",       fig_h2_pipeline_flow(scenarios))
    push("transport", "5. Transport — H₂ & electricity", "19_pipeline_util",          fig_pipeline_utilization(scenarios))
    push("transport", "5. Transport — H₂ & electricity", "20_h2_corridor_heatmap",    fig_h2_corridor_heatmap(scenarios))
    push("transport", "5. Transport — H₂ & electricity", "21_electric_line_cap",      fig_electric_line_capacity(scenarios))
    push("transport", "5. Transport — H₂ & electricity", "22_electric_line_flow",     fig_electric_line_flow(scenarios))

    # §6
    push("hydrogen", "6. Hydrogen — deep focus", "23_h2_supply_cap",      fig_h2_supply_capacity_stack(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "24_h2_supply_dispatch", fig_h2_supply_dispatch_stack(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "25_h2_balance_panels",  fig_h2_balance_panels(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "26_h2_price_duration",  fig_h2_shadow_price_duration_curve(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "27_h2_price_box",       fig_h2_shadow_price_box_raw(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "28_elec_price_duration",fig_elec_shadow_price_duration_curve(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "29_elec_price_box",     fig_elec_shadow_price_box_raw(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "30_electrolyser_lf",    fig_electrolyser_load_factor(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "31_atr_lf",             fig_atr_load_factor(scenarios))
    push("hydrogen", "6. Hydrogen — deep focus", "32_electrolyser_panels",fig_electrolyser_dispatch_panels(scenarios))

    # §7
    push("costs", "7. Costs & emissions", "33_cost_breakdown",         fig_cost_breakdown(scenarios))
    push("costs", "7. Costs & emissions", "34_cost_per_mwh",           fig_cost_per_mwh_breakdown(scenarios))
    push("costs", "7. Costs & emissions", "35_gas_emissions",          fig_gas_dispatch_emissions(scenarios))
    push("costs", "7. Costs & emissions", "36_load_shedding",          fig_load_shedding(scenarios))
    push("costs", "7. Costs & emissions", "37_spillage",               fig_spillage(scenarios))

    # §8
    country_tech_idx = 38
    for tech in ("Biomethane_CCGT", "Nuclear", "ATR_biomethane", "electrolysis",
                 "Hydrogen_power_plant", "Solar", "Wind_Onshore", "Wind_Offshore", "Gas"):
        if any(scenarios[n].capacities_gw.get(tech, 0) > 0.05 for n in scenarios):
            push("country", "8. Per-country deployment",
                 f"{country_tech_idx:02d}_country_cap_{tech.lower()}",
                 fig_country_capacity_heatmap(scenarios, tech))
            country_tech_idx += 1
    push("country", "8. Per-country deployment",
         f"{country_tech_idx:02d}_country_load_shedding",
         fig_country_load_shedding(scenarios))

    # §9 — Maps
    map_idx = country_tech_idx + 1
    if _SHAPEFILE_PATH is not None and _SHAPEFILE_PATH.exists():
        # Headline: H₂ system map with pies (electrolyser/ATR) + arrows (flows)
        push("maps", "9. Maps", f"{map_idx:02d}_map_h2_system_panels",
             fig_map_h2_system_panels(scenarios))
        map_idx += 1
        # Per-focal-scenario detailed H₂ maps
        for sn in _focal_scenarios(scenarios):
            push("maps", "9. Maps", f"{map_idx:02d}_map_h2_system_{sn}",
                 fig_map_h2_system_single(scenarios, sn))
            map_idx += 1
        # Per-country capacity choropleths for techs NOT already on the H₂ pies
        for tech in ("Biomethane_CCGT", "Nuclear", "Hydrogen_power_plant",
                     "Solar", "Wind_Onshore", "Wind_Offshore"):
            if any(scenarios[n].capacities_gw.get(tech, 0) > 0.05 for n in scenarios):
                push("maps", "9. Maps", f"{map_idx:02d}_map_cap_{tech.lower()}",
                     fig_map_capacity(scenarios, tech))
                map_idx += 1
        push("maps", "9. Maps", f"{map_idx:02d}_map_h2_net_imports",
             fig_map_h2_net_imports(scenarios))
        map_idx += 1
        push("maps", "9. Maps", f"{map_idx:02d}_map_elec_net_imports",
             fig_map_elec_net_imports(scenarios))
        map_idx += 1
        push("maps", "9. Maps", f"{map_idx:02d}_map_load_shedding",
             fig_map_load_shedding(scenarios))
        map_idx += 1

    # §10 — Sensitivity deltas
    sens_idx = map_idx
    for label, pairs in [
        ("Add Biomethane",              BIO_PAIRS),
        ("Add Nuclear",                 NUKE_PAIRS),
        ("Add ATR (biomethane→H₂)",     ATR_PAIRS),
        ("Corridor expansion ×3",       CORR_PAIRS),
        ("Electrolyser CAPEX",          EL_PAIRS),
        ("H₂ demand → high_h2 bundle",  H2HIGH_PAIRS),
        ("CO₂ price 250 €/t + Gas floor lifted", CO2_PAIRS),
    ]:
        if not any(p[0] in scenarios and p[1] in scenarios for p in pairs):
            continue
        push("sensitivity", "10. Sensitivity deltas",
             f"{sens_idx:02d}_sens_{label[:24].replace(' ', '_').lower()}",
             fig_sensitivity_delta(scenarios, label, pairs))
        sens_idx += 1

    # ── HTML composition ──
    chunks: List[str] = [HTML_HEAD]
    chunks.append('<h1>POMMES CLEVER 2050 — Scenario Observatory</h1>')
    subtitle_extra = " (lite — box plots subsampled)" if _LITE_BOX_STRIDE > 1 else ""
    chunks.append(f'<div class="subtitle">Cross-scenario comparison — biomethane, nuclear, '
                  f'corridor, electrolyser CAPEX, H₂ demand &amp; CO₂ price sensitivities '
                  f'(N = {len(scenarios)} solved scenarios){subtitle_extra}</div>')

    # Build TOC dynamically from item sections
    seen_sections = []
    for sid, slabel, _, _ in items:
        if sid not in seen_sections:
            seen_sections.append(sid)
    section_labels = {sid: lbl for sid, lbl, _, _ in items}
    chunks.append('<div class="toc"><strong>Sections</strong><ol>')
    chunks.append('<li><a href="#index">0. Scenario index (assumptions &amp; results)</a></li>')
    for sid in seen_sections:
        chunks.append(f'<li><a href="#{sid}">{section_labels[sid]}</a></li>')
    chunks.append('</ol></div>')

    # ── §0. Scenario index — two tables ──
    chunks.append('<h2 id="index">0. Scenario index</h2>')
    chunks.append(build_assumptions_table_html(scenarios))
    chunks.append(build_results_table_html(scenarios))

    fig_counter = 0
    current_section = None
    first_fig = True
    for sid, slabel, key, fig in items:
        if sid != current_section:
            chunks.append(f'<h2 id="{sid}">{slabel}</h2>')
            current_section = sid
        fig_counter += 1
        chunks.append(fig_to_div(fig, f"fig{fig_counter:03d}", first=first_fig))
        first_fig = False

    chunks.append(HTML_FOOTER.format(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        n_scenarios=len(scenarios),
        diag_dir=diagnostics_dir,
    ))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(chunks), encoding="utf-8")
    logger.info("Wrote %s (%d figures, %d scenarios)", output_path, len(items), len(scenarios))
    print(f"→ Wrote {output_path}")
    print(f"  {len(items)} figures, {len(scenarios)} scenarios")
    print(f"  Size: {output_path.stat().st_size / (1024*1024):.1f} MB")

    return items   # so main() can drive figure export


def export_individual_figures(items: List[Tuple[str, str, str, go.Figure]],
                              out_dir: Path,
                              formats: Sequence[str] = ("html", "png", "svg")) -> None:
    """Export each figure as standalone HTML / PNG / SVG.

    PNG / SVG use kaleido. They are silently skipped if kaleido isn't installed
    or if export takes too long for a given figure (e.g. the very heavy box plot).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    has_kaleido = True
    try:
        import kaleido  # noqa: F401
    except ImportError:
        has_kaleido = False
        logger.warning("kaleido not installed — PNG/SVG export skipped")

    sizes: Dict[str, int] = {"html": 0, "png": 0, "svg": 0}
    for idx, (sid, slabel, key, fig) in enumerate(items, 1):
        base = out_dir / key
        # HTML
        if "html" in formats:
            html_path = base.with_suffix(".html")
            full = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{key}</title>"
                "<link rel='stylesheet' href='https://cdn.jsdelivr.net/gh/aaaakshat/cm-web-fonts@latest/fonts.css'>"
                "<style>body{margin:0;padding:14px;background:#fff;"
                f"font-family:{LATEX_FONT_FAMILY}}}</style></head><body>"
                + fig.to_html(full_html=False, include_plotlyjs="cdn", div_id=key)
                + "</body></html>"
            )
            html_path.write_text(full, encoding="utf-8")
            sizes["html"] += html_path.stat().st_size

        if has_kaleido:
            # Pick reasonable export size. Maps + small-multiples need taller.
            h = fig.layout.height or 460
            w = 1600
            try:
                if "png" in formats:
                    png_path = base.with_suffix(".png")
                    fig.write_image(png_path, width=w, height=h, scale=2)
                    sizes["png"] += png_path.stat().st_size
                if "svg" in formats:
                    svg_path = base.with_suffix(".svg")
                    fig.write_image(svg_path, width=w, height=h)
                    sizes["svg"] += svg_path.stat().st_size
            except Exception as e:
                logger.warning("export failed for %s: %s", key, e)

        print(f"  [{idx:>2}/{len(items)}] exported {key}")

    print()
    for fmt, total in sizes.items():
        if total > 0:
            print(f"  Σ {fmt}: {total/(1024*1024):.1f} MB")


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cross-scenario observatory HTML.")
    p.add_argument("--diagnostics-dir", type=Path,
                   default=Path("/diskdata/cired/brigode/clever-work/results/diagnostics"))
    p.add_argument("--output", type=Path,
                   default=Path("/diskdata/cired/brigode/clever-work/results/observatoire_scenarios.html"))
    p.add_argument("--exclude", action="append", default=[],
                   help="Scenario name to exclude (can be repeated).")
    p.add_argument("--lite", action="store_true",
                   help="Produce a smaller HTML by subsampling box-plot data 10× "
                        "(duration curves remain raw). Suitable for GitHub Pages.")
    p.add_argument("--lite-stride", type=int, default=10,
                   help="Subsample stride for box plots when --lite is set (default 10).")
    p.add_argument("--export-figures", type=Path, default=None,
                   help="If set, write each figure as standalone .html/.png/.svg in this dir.")
    p.add_argument("--shapefile", type=Path,
                   default=Path("/diskdata/cired/brigode/clever-work/"
                                "ne_110m_admin_0_countries/ne_110m_admin_0_countries.shp"),
                   help="Natural Earth Admin-0 shapefile (used for the maps section).")
    p.add_argument("-v", "--verbose", action="count", default=0)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    global _LITE_BOX_STRIDE, _SHAPEFILE_PATH
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2 else logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.lite:
        _LITE_BOX_STRIDE = max(1, int(args.lite_stride))
        print(f"LITE mode: box-plot stride = {_LITE_BOX_STRIDE}")
    _SHAPEFILE_PATH = args.shapefile if args.shapefile.exists() else None
    if _SHAPEFILE_PATH is None:
        print(f"WARNING: shapefile not found at {args.shapefile} — maps will be skipped")

    print(f"Loading scenarios from {args.diagnostics_dir}")
    scenarios = load_scenarios(args.diagnostics_dir)
    for n in args.exclude:
        scenarios.pop(n, None)
    print(f"Loaded {len(scenarios)} scenarios: {sorted(scenarios.keys())}")
    if not scenarios:
        print("ERROR: no scenarios found."); return 2
    print("Computing aggregates...")
    compute_aggregates(scenarios)
    print("Building HTML...")
    items = build_observatory_html(scenarios, args.diagnostics_dir, args.output)
    if args.export_figures is not None:
        print(f"Exporting individual figures to {args.export_figures}")
        export_individual_figures(items, args.export_figures)
    return 0


if __name__ == "__main__":
    sys.exit(main())
