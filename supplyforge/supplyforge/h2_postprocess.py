"""
Post-processing and visualisation for H₂ network emergence analysis.

Provides plotting functions for the key outputs of the multi-horizon
DemandForge / POMMES-CRAFT coupling:

1. **Stacked area chart** — H₂ demand by sector over 2030–2050
2. **Country heatmap** — H₂ demand intensity per country at each horizon
3. **Network map** — pipeline flow arrows (requires geopandas, optional)
4. **Cost evolution** — annualised H₂ system cost vs total system cost
5. **Electrolyser capacity factor** — CF per country over time
6. **Sensitivity comparison** — bar chart comparing variants

All functions accept the results dict from
``h2_analysis.run_multi_horizon_analysis()`` or
``h2_analysis.run_sensitivity_variants()``.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

from supplyforge.h2_analysis import SCALE_THRESHOLD_TWH
from supplyforge.h2_profiles import DEMANDFORGE_SECTORS

try:
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    logger.warning("matplotlib not available — plotting disabled.")


# ═══════════════════════════════════════════════════════════════════
# Colour palette
# ═══════════════════════════════════════════════════════════════════
SECTOR_COLORS = {
    "ammonia":  "#2196F3",  # blue   — Haber-Bosch electrolysis
    "refinery": "#FF9800",  # orange — hydrotreaters / grey→green
    "steel":    "#F44336",  # red    — DRI-H₂
    "esaf":     "#4CAF50",  # green  — e-kerosene (Fischer-Tropsch)
    "maritime": "#00BCD4",  # cyan   — e-methanol / ammonia bunker
    "olefins":  "#9C27B0",  # purple — MTO / bio-naphtha
}

# Canonical sector order from h2_profiles (single source of truth)
SECTOR_ORDER = list(DEMANDFORGE_SECTORS)


def _check_mpl():
    if not HAS_MPL:
        raise ImportError(
            "matplotlib is required for plotting. "
            "Install it with: pip install matplotlib"
        )


# ═══════════════════════════════════════════════════════════════════
# 1. Stacked area: H₂ demand by sector over time
# ═══════════════════════════════════════════════════════════════════
def plot_h2_demand_by_sector(
    results: dict[int, dict],
    title: str = "H₂ demand by sector",
    save_path: str | Path | None = None,
    figsize: tuple = (10, 6),
) -> Any:
    """Stacked area chart of H₂ demand by sector over horizon years.

    Parameters
    ----------
    results : dict
        ``{year: results_dict}`` with ``h2_demand_by_sector``.
    title : str
        Plot title.
    save_path : str or Path, optional
        If provided, save figure to this path.

    Returns
    -------
    matplotlib.figure.Figure or None
    """
    _check_mpl()

    years = sorted(results.keys())
    sector_totals = {s: [] for s in SECTOR_ORDER}

    for year in years:
        res = results[year]
        year_sectors = {}
        for country_sectors in res.get("h2_demand_by_sector", {}).values():
            for sector, mwh in country_sectors.items():
                year_sectors[sector] = year_sectors.get(sector, 0.) + mwh

        for sector in SECTOR_ORDER:
            sector_totals[sector].append(year_sectors.get(sector, 0.) / 1e6)  # TWh

    fig, ax = plt.subplots(figsize=figsize)
    bottom = np.zeros(len(years))
    for sector in SECTOR_ORDER:
        values = np.array(sector_totals[sector])
        ax.fill_between(
            years, bottom, bottom + values,
            label=sector.capitalize(),
            color=SECTOR_COLORS[sector],
            alpha=0.8,
        )
        bottom += values

    ax.set_xlabel("Year")
    ax.set_ylabel("H₂ demand (TWh/yr)")
    ax.set_title(title)
    ax.legend(loc="upper left")
    ax.set_xlim(years[0], years[-1])
    ax.set_ylim(0, None)
    ax.grid(True, alpha=0.3)

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        logger.info(f"Saved: {save_path}")

    return fig


# ═══════════════════════════════════════════════════════════════════
# 2. Electrolyser capacity and CF over time
# ═══════════════════════════════════════════════════════════════════
def plot_electrolyser_evolution(
    results: dict[int, dict],
    save_path: str | Path | None = None,
    figsize: tuple = (12, 5),
) -> Any:
    """Dual-axis plot: electrolyser capacity (bars) and CF (line) over time."""
    _check_mpl()

    years = sorted(results.keys())
    total_cap = []
    avg_cf = []

    for year in years:
        res = results[year]
        caps = res.get("electrolyser_capacity", {})
        cfs = res.get("electrolyser_cf", {})
        total_cap.append(sum(caps.values()) / 1e3)  # GW
        if cfs:
            avg_cf.append(np.mean(list(cfs.values())) * 100)  # %
        else:
            avg_cf.append(0.)

    fig, ax1 = plt.subplots(figsize=figsize)
    ax1.bar(years, total_cap, width=3, color="#2196F3", alpha=0.7, label="Capacity (GW)")
    ax1.set_xlabel("Year")
    ax1.set_ylabel("Electrolyser capacity (GW)", color="#2196F3")
    ax1.tick_params(axis="y", labelcolor="#2196F3")

    ax2 = ax1.twinx()
    ax2.plot(years, avg_cf, "o-", color="#F44336", label="Avg. CF (%)")
    ax2.set_ylabel("Capacity factor (%)", color="#F44336")
    ax2.tick_params(axis="y", labelcolor="#F44336")
    ax2.set_ylim(0, 100)

    # Add 30-70% CF band
    ax2.axhspan(30, 70, alpha=0.1, color="green", label="Typical CF range")

    fig.suptitle("Electrolyser capacity and utilisation")
    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


# ═══════════════════════════════════════════════════════════════════
# 3. H₂ pipeline flows summary
# ═══════════════════════════════════════════════════════════════════
def plot_pipeline_flows(
    results: dict[int, dict],
    save_path: str | Path | None = None,
    figsize: tuple = (10, 6),
) -> Any:
    """Grouped bar chart of H₂ pipeline flows by corridor over time."""
    _check_mpl()

    years = sorted(results.keys())

    # Collect all corridors across years
    all_corridors = set()
    for year in years:
        res = results[year]
        for pair in res.get("h2_pipeline_flows", {}).keys():
            corridor = tuple(sorted(pair))
            all_corridors.add(corridor)

    if not all_corridors:
        logger.info("No H₂ pipeline flows to plot.")
        return None

    corridors = sorted(all_corridors)
    corridor_labels = [f"{c[0]}→{c[1]}" for c in corridors]

    fig, ax = plt.subplots(figsize=figsize)
    x = np.arange(len(corridors))
    width = 0.8 / len(years)

    for i, year in enumerate(years):
        res = results[year]
        flows = res.get("h2_pipeline_flows", {})
        values = []
        for corridor in corridors:
            fwd = flows.get(corridor, 0.)
            rev = flows.get((corridor[1], corridor[0]), 0.)
            values.append((fwd + rev) / 1e6)  # TWh

        ax.bar(
            x + i * width, values, width,
            label=str(year), alpha=0.8,
        )

    ax.set_xticks(x + width * len(years) / 2)
    ax.set_xticklabels(corridor_labels, rotation=45, ha="right")
    ax.set_ylabel("H₂ flow (TWh/yr)")
    ax.set_title("H₂ pipeline flows by corridor")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


# ═══════════════════════════════════════════════════════════════════
# 4. Sensitivity variant comparison
# ═══════════════════════════════════════════════════════════════════
def plot_sensitivity_comparison(
    variant_results: dict[str, dict[int, dict]],
    metric: str = "total_h2_demand_twh",
    save_path: str | Path | None = None,
    figsize: tuple = (10, 6),
) -> Any:
    """Line chart comparing a metric across sensitivity variants."""
    _check_mpl()

    fig, ax = plt.subplots(figsize=figsize)

    for variant_name, year_results in variant_results.items():
        years = sorted(year_results.keys())
        values = [year_results[y].get(metric, 0.) for y in years]
        ax.plot(years, values, "o-", label=variant_name, linewidth=2)

    ax.set_xlabel("Year")
    ax.set_ylabel(metric.replace("_", " ").title())
    ax.set_title(f"Sensitivity comparison: {metric}")
    ax.legend()
    ax.grid(True, alpha=0.3)

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


# ═══════════════════════════════════════════════════════════════════
# 5. Country heatmap
# ═══════════════════════════════════════════════════════════════════
def plot_country_heatmap(
    results: dict[int, dict],
    save_path: str | Path | None = None,
    figsize: tuple = (12, 8),
) -> Any:
    """Heatmap of H₂ demand intensity (TWh/yr) per country × year."""
    _check_mpl()

    years = sorted(results.keys())
    all_countries = set()
    for year in years:
        res = results[year]
        all_countries.update(res.get("h2_demand_by_sector", {}).keys())

    countries = sorted(all_countries)
    matrix = np.zeros((len(countries), len(years)))

    for j, year in enumerate(years):
        res = results[year]
        for i, country in enumerate(countries):
            sectors = res.get("h2_demand_by_sector", {}).get(country, {})
            matrix[i, j] = sum(sectors.values()) / 1e6  # TWh

    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(matrix, aspect="auto", cmap="YlOrRd")
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years)
    ax.set_yticks(range(len(countries)))
    ax.set_yticklabels(countries)
    ax.set_xlabel("Year")
    ax.set_title("H₂ demand intensity (TWh/yr)")

    # Annotate cells
    for i in range(len(countries)):
        for j in range(len(years)):
            val = matrix[i, j]
            if val > 0:
                ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                        fontsize=8, color="black" if val < matrix.max() * 0.6 else "white")

    fig.colorbar(im, ax=ax, label="TWh/yr")
    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


# ═══════════════════════════════════════════════════════════════════
# Summary report
# ═══════════════════════════════════════════════════════════════════
def generate_summary_report(
    multi_horizon_results: dict[int, dict],
    emergence_indicators: dict,
    output_path: str | Path | None = None,
) -> str:
    """Generate a text summary of H₂ network emergence analysis.

    Returns the report as a string and optionally writes to file.
    """
    lines = [
        "=" * 70,
        "H₂ NETWORK EMERGENCE ANALYSIS — SUMMARY REPORT",
        "=" * 70,
        "",
    ]

    # Emergence timeline
    fn = emergence_indicators.get("first_network_year")
    fs = emergence_indicators.get("first_scale_year")
    lines.append(f"First H₂ network formation year: {fn or 'Not reached'}")
    lines.append(f"First scale threshold (>{SCALE_THRESHOLD_TWH} TWh) year: {fs or 'Not reached'}")
    lines.append("")

    # Per-year summary
    for year in sorted(multi_horizon_results.keys()):
        res = multi_horizon_results[year]
        if "status" in res and res["status"] == "unsolved":
            lines.append(f"── {year}: Model not solved ──")
            continue

        total_twh = res.get("total_h2_demand_twh", 0.)
        n_countries = len(res.get("electrolyser_capacity", {}))
        total_elec_gw = sum(res.get("electrolyser_capacity", {}).values()) / 1e3
        n_pipelines = len([
            f for f in res.get("h2_pipeline_flows", {}).values() if f > 0
        ])
        network = emergence_indicators.get("network_formed", {}).get(year, False)

        lines.append(f"── {year} ──")
        lines.append(f"  Total H₂ demand:      {total_twh:.1f} TWh/yr")
        lines.append(f"  Electrolyser capacity: {total_elec_gw:.1f} GW ({n_countries} countries)")
        lines.append(f"  Active H₂ pipelines:   {n_pipelines}")
        lines.append(f"  Network formed:        {'Yes' if network else 'No'}")

        # Sector breakdown
        sectors_agg = {}
        for country_sectors in res.get("h2_demand_by_sector", {}).values():
            for sector, mwh in country_sectors.items():
                sectors_agg[sector] = sectors_agg.get(sector, 0.) + mwh
        if sectors_agg:
            lines.append("  Sector breakdown (TWh):")
            for sector in SECTOR_ORDER:
                if sector in sectors_agg:
                    lines.append(f"    {sector:12s}: {sectors_agg[sector]/1e6:6.1f}")

        lines.append("")

    report = "\n".join(lines)

    if output_path:
        Path(output_path).write_text(report)
        logger.info(f"Report written to {output_path}")

    return report
