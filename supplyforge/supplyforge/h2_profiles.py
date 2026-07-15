"""
Sector-specific hourly hydrogen demand profiles for POMMES-CRAFT coupling.

Each DemandForge sector has a distinct temporal characteristic reflecting
the physical operation of the underlying industrial process. The profiles
are normalised so that their integral over 8760 hours equals the annual
demand from DemandForge — no energy is created or destroyed.

Profiles
--------
- **ammonia** : Near-flat (baseload). Haber-Bosch runs continuously at ~95%
  capacity factor. Small sinusoidal seasonal modulation (±5%) for planned
  maintenance windows.
- **refinery** : Near-flat (baseload). Hydrotreaters run continuously. Slight
  summer peak for driving season (+3% amplitude).
- **steel** : Industrial weekly pattern. DRI-EAF follows industrial schedules:
  weekday-heavy, reduced weekends (80% weekend factor).
- **esaf** : Flat (baseload). Fischer-Tropsch / methanol-to-jet runs
  continuously.
- **maritime** : Seasonal sinusoidal with summer peak (June–September,
  ±20% amplitude) reflecting shipping activity.
- **olefins** : Flat (baseload). Steam crackers and MTO plants run
  continuously.
"""

import logging
from typing import Sequence

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Profile configuration — tuneable per sector
# ═══════════════════════════════════════════════════════════════════
PROFILE_CONFIG: dict[str, dict] = {
    "ammonia": {
        "type": "sinusoidal",
        "amplitude": 0.05,
        "peak_hour": 4380,       # mid-year (roughly July)
    },
    "refinery": {
        "type": "sinusoidal",
        "amplitude": 0.03,
        "peak_hour": 4380,       # slight summer peak
    },
    "steel": {
        "type": "weekly",
        "weekend_factor": 0.80,  # 80% of weekday output
    },
    "esaf": {
        "type": "flat",
    },
    "maritime": {
        "type": "sinusoidal",
        "amplitude": 0.20,
        "peak_hour": 4380,       # summer peak (shipping season)
    },
    "olefins": {
        "type": "flat",
    },
}


def _build_flat(n: int) -> np.ndarray:
    """Uniform profile."""
    return np.ones(n, dtype=np.float64)


def _build_sinusoidal(
    hours: Sequence[int],
    amplitude: float,
    peak_hour: int,
) -> np.ndarray:
    """Sinusoidal modulation around a baseline of 1.0.

    profile(h) = 1.0 + amplitude × sin(2π × (h − offset) / 8760)

    where offset = peak_hour − 8760/4, ensuring sin = +1 at h = peak_hour.
    """
    h = np.asarray(hours, dtype=np.float64)
    # sin peaks at π/2 → shift so that at h=peak_hour, argument = π/2
    # i.e.  2π*(peak_hour - offset)/8760 = π/2  →  offset = peak_hour - 8760/4
    offset = peak_hour - 8760 / 4
    profile = 1.0 + amplitude * np.sin(2 * np.pi * (h - offset) / 8760)
    return profile


def _build_weekly(
    hours: Sequence[int],
    weekend_factor: float,
) -> np.ndarray:
    """Industrial weekly pattern: full load weekdays, reduced weekends.

    Assumes hour 0 = Monday 00:00 (standard POMMES convention).
    """
    h = np.asarray(hours, dtype=np.float64)
    day_of_week = (h // 24).astype(int) % 7  # 0=Mon, 5=Sat, 6=Sun
    profile = np.where(day_of_week < 5, 1.0, weekend_factor)
    return profile


def build_h2_profile(
    sector: str,
    annual_demand_mwh: float,
    hours: list[int],
    year_op: int,
    config: dict | None = None,
) -> pl.DataFrame:
    """Build an hourly H₂ demand profile for a given sector.

    Parameters
    ----------
    sector : str
        DemandForge sector name (ammonia, refinery, steel, esaf, maritime,
        olefins).
    annual_demand_mwh : float
        Total annual H₂ demand in MWh (LHV) from DemandForge.
    hours : list[int]
        Hour indices (typically ``range(8760)``).
    year_op : int
        Operating year for the POMMES model.
    config : dict, optional
        Override for ``PROFILE_CONFIG[sector]``.  If ``None``, the module
        default is used.

    Returns
    -------
    pl.DataFrame
        Columns: ``demand`` (MWh per hour), ``hour``, ``year_op``.
        The ``demand`` column integrates to ``annual_demand_mwh`` over the
        year (mass-balance guaranteed by normalisation).
    """
    if config is None:
        config = PROFILE_CONFIG.get(sector, {"type": "flat"})

    n = len(hours)
    profile_type = config.get("type", "flat")

    if profile_type == "flat":
        raw = _build_flat(n)
    elif profile_type == "sinusoidal":
        raw = _build_sinusoidal(
            hours,
            amplitude=config["amplitude"],
            peak_hour=config.get("peak_hour", 4380),
        )
    elif profile_type == "weekly":
        raw = _build_weekly(
            hours,
            weekend_factor=config.get("weekend_factor", 0.80),
        )
    else:
        logger.warning(
            "Unknown profile type '%s' for sector '%s'. Falling back to flat.",
            profile_type,
            sector,
        )
        raw = _build_flat(n)

    # Normalise: sum(profile) * 1h = annual_demand_mwh
    total = raw.sum()
    if total > 0:
        profile = raw / total * annual_demand_mwh
    else:
        profile = np.zeros(n, dtype=np.float64)

    # Sanity check
    residual = abs(profile.sum() - annual_demand_mwh)
    if annual_demand_mwh > 0 and residual / annual_demand_mwh > 1e-10:
        logger.warning(
            "Profile normalisation residual for %s: %.2e MWh (%.2e %%)",
            sector,
            residual,
            residual / annual_demand_mwh * 100,
        )

    return pl.DataFrame(
        {
            "demand": profile.tolist(),
            "hour": hours,
            "year_op": [year_op] * n,
        }
    )


# ═══════════════════════════════════════════════════════════════════
# Convenience: build all sector profiles for one country
# ═══════════════════════════════════════════════════════════════════
DEMANDFORGE_SECTORS = ("ammonia", "refinery", "steel", "esaf", "maritime", "olefins")


def build_all_sector_profiles(
    sector_demands: dict[str, float],
    hours: list[int],
    year_op: int,
) -> dict[str, pl.DataFrame]:
    """Build hourly profiles for all sectors with non-zero demand.

    Parameters
    ----------
    sector_demands : dict
        ``{sector_name: annual_demand_mwh}``.  Sectors with zero or
        negative demand are silently skipped.
    hours : list[int]
        Hour indices.
    year_op : int
        Operating year.

    Returns
    -------
    dict[str, pl.DataFrame]
        ``{sector_name: profile_df}``.
    """
    profiles = {}
    for sector, demand_mwh in sector_demands.items():
        if demand_mwh <= 0:
            continue
        profiles[sector] = build_h2_profile(sector, demand_mwh, hours, year_op)
    return profiles
