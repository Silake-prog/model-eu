"""pommes_eur.providers.clever._min_bounds — electrolyser sovereignty min-bounds math.

factor x demand / (load-factor x 8760) vs an absolute floor -> per-country GW min bounds.
Split verbatim out of the old r0_input_tables.py.
"""
from __future__ import annotations

import logging

import pandas as pd  # noqa: F401

logger = logging.getLogger(__name__)

def _compute_electrolyser_min_bounds(
    sov_df: pd.DataFrame,
    annual_demand_mwh: dict[str, float],
    countries: list[str],
    load_factor_override: Optional[float] = None,
) -> dict[str, float]:
    """
    Compute per-country electrolyser min capacity in GW.

    For each country:
      * If ``sovereignty_factor`` is set:
          min_capacity_MW = factor × demand_MWh / (load_factor × 8760)
      * If ``absolute_floor_mw`` is set:
          min_capacity_MW = max(min_capacity_MW, absolute_floor)
      * If only ``absolute_floor_mw`` is set (factor blank):
          min_capacity_MW = absolute_floor

    Returns dict in GW.
    """
    bounds: dict[str, float] = {}

    # Index sovereignty CSV by area
    sov_by_area = {row["area"]: row for _, row in sov_df.iterrows()}

    for area in countries:
        if area not in sov_by_area:
            logger.warning(
                "r0_input_tables: no sovereignty row for %r; min bound = 0", area
            )
            continue

        row = sov_by_area[area]
        factor = row.get("sovereignty_factor")
        abs_floor_mw = row.get("absolute_floor_mw")
        lf = load_factor_override or row.get("load_factor_assumption") or 0.6

        demand_mwh = annual_demand_mwh.get(area, 0.0)

        capacity_mw_from_factor = 0.0
        if pd.notna(factor) and factor > 0 and demand_mwh > 0:
            capacity_mw_from_factor = (
                float(factor) * float(demand_mwh) / (float(lf) * 8760.0)
            )

        capacity_mw_from_floor = 0.0
        if pd.notna(abs_floor_mw):
            capacity_mw_from_floor = float(abs_floor_mw)

        # Take the larger of the two (so absolute_floor acts as a true floor)
        capacity_mw = max(capacity_mw_from_factor, capacity_mw_from_floor)

        if capacity_mw > 0:
            bounds[area] = capacity_mw / 1000.0  # GW
            logger.info(
                "r0_input_tables: %s electrolyser min = %.2f GW "
                "(factor=%s, demand=%.1f TWh, LF=%.2f, abs_floor=%s MW)",
                area, bounds[area],
                f"{factor:.2f}" if pd.notna(factor) else "—",
                demand_mwh / 1e6,
                lf,
                f"{abs_floor_mw:.0f}" if pd.notna(abs_floor_mw) else "—",
            )

    return bounds
