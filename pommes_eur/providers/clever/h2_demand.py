"""pommes_eur.providers.clever.h2_demand — CLEVER H₂ demand glue (DemandForge).

Relocated **verbatim** from the (generic) supplyforge tree
(``fetch_h2_demand_from_demandforge`` was in ``create_pommes_craft_model.py``;
``cross_check_clever_totals`` in ``h2_analysis.py``) so pommes_eur depends on supplyforge
only through the generic data loader. These are CLEVER/POMMES-EUR-specific: they pull
sector-decomposed H₂ demand from **DemandForge** and reconcile it against CLEVER's own
demand table. DemandForge is imported lazily inside the function (already a model dep);
this also decouples the H₂-demand path from ``create_pommes_craft_model``'s heavy
``entsoe`` import chain. No numerics changed.
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def fetch_h2_demand_from_demandforge(
    bundle_name: str,
    countries: list[str],
    model_year: int,
    sectors: list[str] | None = None,
    # Infrastructure params passed through to DemandForge
    units_config: dict | None = None,
    jet_unit_names: dict | None = None,
    jet_yields: dict | None = None,
    jrc_idees_path: str | None = None,
    tyndp_path: str | None = None,
    sectoral: bool = True,
) -> dict[str, dict[str, float]] | dict[str, float]:
    """Fetch hydrogen demand from DemandForge for use in POMMES.

    Calls DemandForge's load_bundle(), filters to a single model year,
    and returns sector-decomposed demand per country.

    Args:
        bundle_name: DemandForge scenario bundle (e.g. "central", "high_h2",
            "low_h2").
        countries: ISO-2 country codes.
        model_year: The year to extract (must be within the projection range).
        sectors: Optional sector filter. If None, uses all available sectors.
        units_config: CONCAWE unit definitions (for refinery sector).
        jet_unit_names: Jet reconstruction mapping (for eSAF sector).
        jet_yields: Hydrocracker jet yields (for eSAF sector).
        jrc_idees_path: JRC-IDEES path (for steel sector).
        tyndp_path: TYNDP path (for steel sector).
        sectoral: If True (default), return sector-decomposed dict.
            If False, return aggregated dict (legacy behaviour).

    Returns:
        If ``sectoral=True``:
            ``{country_code: {sector: demand_mwh_per_yr}}``
        If ``sectoral=False``:
            ``{country_code: demand_mwh_per_yr}`` (sum across sectors)

    Raises:
        ImportError: If demandforge is not installed.
        ValueError: If model_year is outside the projection range.
    """
    try:
        from demandforge.load_projection.scenarios import load_bundle
    except ImportError:
        raise ImportError(
            "DemandForge package not found. Install it with: "
            "pip install demandforge  (or add it to your environment)"
        )

    # Run DemandForge projection
    kwargs = {
        "countries": countries,
        "reference_year": 2019,
        "target_year": max(model_year, 2050),
    }
    if sectors is not None:
        kwargs["sectors"] = sectors
    if units_config is not None:
        kwargs["units_config"] = units_config
    if jet_unit_names is not None:
        kwargs["jet_unit_names"] = jet_unit_names
    if jet_yields is not None:
        kwargs["jet_yields"] = jet_yields
    if jrc_idees_path is not None:
        kwargs["jrc_idees_path"] = jrc_idees_path
    if tyndp_path is not None:
        kwargs["tyndp_path"] = tyndp_path

    df = load_bundle(bundle_name, **kwargs)

    # Filter to model year
    df_year = df[df["year"] == model_year]
    if df_year.empty:
        raise ValueError(
            f"No data for year {model_year} in bundle '{bundle_name}'. "
            f"Available years: {sorted(df['year'].unique())}"
        )

    if sectoral:
        # Return {country: {sector: MWh/yr}}
        result = {}
        for country in countries:
            df_c = df_year[df_year["country"] == country]
            if df_c.empty:
                result[country] = {}
            else:
                result[country] = (
                    df_c.set_index("sector")["h2_demand_mwh_per_yr"].to_dict()
                )
        return result
    else:
        # Legacy: return {country: total_MWh/yr}
        return (
            df_year
            .groupby("country")["h2_demand_mwh_per_yr"]
            .sum()
            .to_dict()
        )


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
