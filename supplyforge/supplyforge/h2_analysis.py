"""
Multi-horizon H₂ network emergence analysis for POMMES-CRAFT.

This module provides:
1. ``run_multi_horizon_analysis()`` — runs the coupled DemandForge /
   POMMES-CRAFT model at multiple horizon years (2030–2050) and collects
   results.
2. ``run_sensitivity_variants()`` — runs the S1–S4 sensitivity matrix.
3. ``extract_h2_results()`` — extracts H₂-specific outputs from a solved
   POMMES model (electrolyser capacity, storage, pipeline flows, sector
   attribution).
4. ``compute_emergence_indicators()`` — computes network emergence
   thresholds and summary statistics.
5. ``cross_check_clever_totals()`` — calibration comparison between
   DemandForge and CLEVER's own H₂ indicators.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════
ANALYSIS_YEARS = [2030, 2035, 2040, 2045, 2050]

# Emergence thresholds
SECTOR_ACTIVATION_TWH = 0.1       # sector H₂ demand > 0.1 TWh/yr
NETWORK_FORMATION_MWH = 0.0       # any H₂ pipeline flow > 0
SCALE_THRESHOLD_TWH = 50.0        # total EU H₂ demand > 50 TWh/yr

# Sensitivity scenario matrix
SENSITIVITY_VARIANTS = {
    "S1-base": {
        "bundle": "low_h2",
        "h2_transport": True,
        "description": "Sufficiency reference with H₂ network",
    },
    "S2-no-network": {
        "bundle": "low_h2",
        "h2_transport": False,
        "description": "Autarkic H₂ (no inter-area transport)",
    },
    "S3-central": {
        "bundle": "central",
        "h2_transport": True,
        "description": "Moderate transition with H₂ network",
    },
    "S4-high": {
        "bundle": "high_h2",
        "h2_transport": True,
        "description": "Upper bound on H₂ network",
    },
}


# ═══════════════════════════════════════════════════════════════════
# Result extraction
# ═══════════════════════════════════════════════════════════════════
def extract_h2_results(energy_model, year: int) -> dict[str, Any]:
    """Extract H₂-specific results from a solved POMMES model.

    Parameters
    ----------
    energy_model : pommes_craft.EnergyModel
        A solved energy model.
    year : int
        The model year.

    Returns
    -------
    dict
        Keys:
        - ``year``: model year
        - ``electrolyser_capacity``: {country: MW}
        - ``electrolyser_cf``: {country: capacity factor}
        - ``h2_storage_capacity``: {country: MWh}
        - ``h2_demand_by_sector``: {country: {sector: MWh}}
        - ``h2_pipeline_flows``: {(c1,c2): MWh/yr}
        - ``total_h2_demand_twh``: float
        - ``system_cost``: float (total annualised)
    """
    results = {
        "year": year,
        "electrolyser_capacity": {},
        "electrolyser_cf": {},
        "h2_storage_capacity": {},
        "h2_demand_by_sector": {},
        "h2_pipeline_flows": {},
        "total_h2_demand_twh": 0.,
        "system_cost": 0.,
    }

    areas = energy_model.areas
    if isinstance(areas, list):
        areas = {getattr(a, "name", str(a)): a for a in areas}

    for area_name, area in areas.items():
        # Access components robustly (dict or list depending on POMMES version)
        components = getattr(area, "components", getattr(area, "_components", {}))
        if isinstance(components, dict):
            component_list = list(components.values())
        elif isinstance(components, list):
            component_list = components
        else:
            component_list = []

        for comp in component_list:
            if hasattr(comp, 'name') and comp.name == "electrolysis":
                try:
                    cap = comp.power_capacity.value
                    results["electrolyser_capacity"][area_name] = cap
                    # CF = total production / (capacity * 8760)
                    if cap > 0:
                        production = sum(
                            comp.production[h].value
                            for h in range(8760)
                        )
                        results["electrolyser_cf"][area_name] = production / (cap * 8760)
                except (AttributeError, TypeError):
                    pass

            # H₂ storage
            if hasattr(comp, 'name') and comp.name == "h2_storage":
                try:
                    results["h2_storage_capacity"][area_name] = (
                        comp.energy_capacity.value
                    )
                except (AttributeError, TypeError):
                    pass

            # Sector demands
            if hasattr(comp, 'name') and comp.name.startswith("h2_demand_"):
                sector = comp.name.replace("h2_demand_", "")
                try:
                    demand_mwh = sum(
                        comp.demand_served[h].value
                        for h in range(8760)
                    )
                    if area_name not in results["h2_demand_by_sector"]:
                        results["h2_demand_by_sector"][area_name] = {}
                    results["h2_demand_by_sector"][area_name][sector] = demand_mwh
                except (AttributeError, TypeError):
                    pass

    # Pipeline flows — access links robustly
    links = getattr(energy_model, "links", getattr(energy_model, "_links", {}))
    if isinstance(links, list):
        links = {getattr(lk, "name", str(lk)): lk for lk in links}
    for link_name, link in links.items():
        if "h2_link" in str(link_name):
            try:
                flow = sum(
                    link.flow[h].value for h in range(8760)
                )
                parts = link_name.replace("h2_link_", "").split("_")
                if len(parts) == 2:
                    results["h2_pipeline_flows"][(parts[0], parts[1])] = flow
            except (AttributeError, TypeError):
                pass

    # Aggregate total H₂ demand
    total_mwh = sum(
        sum(sectors.values())
        for sectors in results["h2_demand_by_sector"].values()
    )
    results["total_h2_demand_twh"] = total_mwh / 1e6

    return results


# ═══════════════════════════════════════════════════════════════════
# Multi-horizon analysis
# ═══════════════════════════════════════════════════════════════════
def run_multi_horizon_analysis(
    model_builder_fn,
    builder_kwargs: dict,
    bundle_name: str = "low_h2",
    years: list[int] | None = None,
    solver_fn=None,
) -> dict[int, dict]:
    """Run the coupled model at multiple horizon years.

    Parameters
    ----------
    model_builder_fn : callable
        Function that builds the POMMES model
        (e.g. ``create_multi_country_renewable_model``).
    builder_kwargs : dict
        Base keyword arguments for the model builder.  ``model_year``
        and ``demandforge_bundle`` will be overridden per iteration.
    bundle_name : str
        DemandForge bundle to use.
    years : list[int], optional
        Horizon years. Defaults to ``[2030, 2035, 2040, 2045, 2050]``.
    solver_fn : callable, optional
        Function that solves the model and returns results.
        Signature: ``solver_fn(energy_model) -> solved_model``.

    Returns
    -------
    dict[int, dict]
        ``{year: results_dict}`` from ``extract_h2_results()``.
    """
    if years is None:
        years = ANALYSIS_YEARS

    all_results = {}
    for year in years:
        logger.info(f"═══ Running horizon year {year} ═══")

        # Override model_year and bundle
        kwargs = {**builder_kwargs}
        kwargs["model_year"] = year
        kwargs["demandforge_bundle"] = bundle_name

        # Build model
        energy_model = model_builder_fn(**kwargs)

        # Solve
        if solver_fn is not None:
            energy_model = solver_fn(energy_model)
            all_results[year] = extract_h2_results(energy_model, year)
        else:
            logger.warning(
                f"No solver provided — model for {year} built but not solved."
            )
            all_results[year] = {"year": year, "status": "unsolved"}

    return all_results


# ═══════════════════════════════════════════════════════════════════
# Sensitivity analysis
# ═══════════════════════════════════════════════════════════════════
def run_sensitivity_variants(
    model_builder_fn,
    builder_kwargs: dict,
    solver_fn=None,
    variants: dict | None = None,
    years: list[int] | None = None,
) -> dict[str, dict[int, dict]]:
    """Run multi-horizon analysis for each sensitivity variant.

    Parameters
    ----------
    model_builder_fn : callable
        Model builder function.
    builder_kwargs : dict
        Base builder kwargs.
    solver_fn : callable, optional
        Solver function.
    variants : dict, optional
        Sensitivity variant definitions. Defaults to ``SENSITIVITY_VARIANTS``.
    years : list[int], optional
        Horizon years.

    Returns
    -------
    dict[str, dict[int, dict]]
        ``{variant_name: {year: results}}``.
    """
    if variants is None:
        variants = SENSITIVITY_VARIANTS

    all_variant_results = {}
    for variant_name, variant_config in variants.items():
        logger.info(f"╔══ Sensitivity variant: {variant_name} ══╗")
        logger.info(f"  {variant_config['description']}")

        kwargs = {**builder_kwargs}
        kwargs["h2_transport"] = variant_config["h2_transport"]

        all_variant_results[variant_name] = run_multi_horizon_analysis(
            model_builder_fn=model_builder_fn,
            builder_kwargs=kwargs,
            bundle_name=variant_config["bundle"],
            years=years,
            solver_fn=solver_fn,
        )

    return all_variant_results


# ═══════════════════════════════════════════════════════════════════
# Emergence indicators
# ═══════════════════════════════════════════════════════════════════
def compute_emergence_indicators(
    multi_horizon_results: dict[int, dict],
) -> dict:
    """Compute H₂ network emergence indicators from multi-horizon results.

    Parameters
    ----------
    multi_horizon_results : dict
        ``{year: results_dict}`` from ``run_multi_horizon_analysis()``.

    Returns
    -------
    dict
        - ``sector_activation``: {year: {country: [activated sectors]}}
        - ``network_formed``: {year: bool}
        - ``scale_reached``: {year: bool}
        - ``first_network_year``: int or None
        - ``first_scale_year``: int or None
    """
    indicators = {
        "sector_activation": {},
        "network_formed": {},
        "scale_reached": {},
        "first_network_year": None,
        "first_scale_year": None,
    }

    for year in sorted(multi_horizon_results.keys()):
        res = multi_horizon_results[year]
        if "status" in res and res["status"] == "unsolved":
            continue

        # Sector activation
        year_activation = {}
        for country, sectors in res.get("h2_demand_by_sector", {}).items():
            activated = [
                s for s, d in sectors.items()
                if d / 1e6 > SECTOR_ACTIVATION_TWH  # MWh → TWh
            ]
            if activated:
                year_activation[country] = activated
        indicators["sector_activation"][year] = year_activation

        # Network formation
        has_flow = any(
            flow > NETWORK_FORMATION_MWH
            for flow in res.get("h2_pipeline_flows", {}).values()
        )
        indicators["network_formed"][year] = has_flow
        if has_flow and indicators["first_network_year"] is None:
            indicators["first_network_year"] = year

        # Scale threshold
        total_twh = res.get("total_h2_demand_twh", 0.)
        above_scale = total_twh > SCALE_THRESHOLD_TWH
        indicators["scale_reached"][year] = above_scale
        if above_scale and indicators["first_scale_year"] is None:
            indicators["first_scale_year"] = year

    return indicators


# ═══════════════════════════════════════════════════════════════════
# CLEVER cross-check
# ═══════════════════════════════════════════════════════════════════
def cross_check_clever_totals(
    demandforge_results: dict[str, dict[str, float]],
    clever_demand_csv: str | Path,
    model_year: int,
) -> pl.DataFrame:
    """Compare DemandForge H₂ totals against CLEVER's hydcfind + hydcftra.

    Parameters
    ----------
    demandforge_results : dict
        ``{country: {sector: MWh/yr}}`` from ``fetch_h2_demand_from_demandforge()``.
    clever_demand_csv : str or Path
        Path to CLEVER's ``demand_by_sector_resource.csv``.
    model_year : int
        Year to compare.

    Returns
    -------
    pl.DataFrame
        Columns: ``country``, ``demandforge_mwh``, ``clever_mwh``,
        ``ratio``, ``difference_mwh``.
    """
    # Load CLEVER demand
    clever = pl.read_csv(clever_demand_csv)
    clever_h2 = (
        clever
        .filter(pl.col("resource").str.to_lowercase() == "hydrogen")
        .filter(pl.col("year_op") == model_year)
        .group_by("area")
        .agg(pl.col("value").sum().alias("clever_twh"))
    )

    rows = []
    for country, sectors in demandforge_results.items():
        df_total_mwh = sum(sectors.values())
        clever_row = clever_h2.filter(pl.col("area") == country)
        clever_mwh = (
            clever_row["clever_twh"][0] * 1e6
            if not clever_row.is_empty()
            else 0.
        )
        ratio = df_total_mwh / clever_mwh if clever_mwh > 0 else float("inf")
        rows.append({
            "country": country,
            "demandforge_mwh": df_total_mwh,
            "clever_mwh": clever_mwh,
            "ratio": ratio,
            "difference_mwh": df_total_mwh - clever_mwh,
        })

    result = pl.DataFrame(rows)

    # Log warnings for large deviations
    for row in result.iter_rows(named=True):
        if row["ratio"] > 2.0 or row["ratio"] < 0.5:
            logger.warning(
                f"Large DemandForge/CLEVER deviation for {row['country']}: "
                f"ratio={row['ratio']:.2f} "
                f"(DF={row['demandforge_mwh']:.0f} MWh, "
                f"CLEVER={row['clever_mwh']:.0f} MWh)"
            )

    return result
