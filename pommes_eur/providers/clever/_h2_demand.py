"""pommes_eur.providers.clever._h2_demand — annual H2 demand fetch for the R0 pipeline.

Per-country annual hydrogen demand from supplyforge (falling back to demandforge). Split
verbatim out of the old r0_input_tables.py.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

def _fetch_annual_h2_demand(
    bundle_name: str,
    countries: list[str],
    model_year: int,
) -> dict[str, float]:
    """
    Pull per-country annual H₂ demand from DemandForge for the given bundle.

    Returns
    -------
    dict[str, float]
        area_code → annual H₂ demand in MWh (energy of H₂).

    Notes
    -----
    Uses the supplyforge wrapper for compatibility with what
    ``adequacy_clean.ipynb`` already calls. The wrapper preserves the
    bundle's ``pathway_shares`` for eSAF — see ``r0_override_contract.md``
    and the dictionary entry "Coupled industry-electricity capacity-expansion".
    """
    try:
        from supplyforge.h2_profiles import fetch_h2_demand_from_demandforge
    except ImportError:
        # Fall back to direct DemandForge call (canonical path)
        from demandforge.load_projection.scenarios import load_bundle
        frame = load_bundle(
            bundle_name=bundle_name,
            countries=sorted(countries),
            target_year=model_year,
        )
        # Sum all sectors per country for the target year
        frame_y = frame[frame["year"] == model_year]
        out = (
            frame_y.groupby("country")["h2_demand_mwh_per_yr"].sum().to_dict()
        )
        return {str(k): float(v) for k, v in out.items() if str(k) in countries}

    sectoral = fetch_h2_demand_from_demandforge(
        bundle_name=bundle_name,
        countries=countries,
        model_year=model_year,
        sectoral=True,
    )
    return {
        cc: float(sum(v for v in (sectoral.get(cc, {}) or {}).values() if v > 0))
        for cc in countries
    }
