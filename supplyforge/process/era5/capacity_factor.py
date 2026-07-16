r"""
Raw-ERA5 -> wind/solar capacity factors via ``atlite``, in the canonical capacity_factors schema.

The engine builds an ERA5 ``atlite`` cutout per (country, climate year), applies the configured
turbine/PV power models, optionally GWA-corrects wind, aggregates grid cells to the national
shape, and emits frames **identical in schema** to the ENTSO-E/PECD paths (it reuses their
``_long_frame`` / ``_drop_feb29`` / ``_clip_flag`` helpers). Wind + solar only — hydro/RoR
follow ``hydro_source`` (ENTSO-E under ``res_source=era5``).

``atlite`` is an optional dependency (the ``era5`` extra). When it is unavailable, the engine
**fail-softs** (returns no frames) so the dispatcher still writes a valid file (S1).
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from supplyforge.process.pecd.capacity_factor import _clip_flag, _drop_feb29, _long_frame

logger = logging.getLogger(__name__)


def _series_to_frame(values, dates, plant_type: str, ws):
    """Hourly per-country CF series (values + datetime index) -> canonical long frame.

    Leap-trims to 8760 (drop Feb 29) and clips to [0, 1] via the shared PECD helpers, then
    labels with ``plant_type`` and ``WS=str(ws)`` (``_long_frame`` enforces the 8760 length).
    Pure transform — unit-testable offline.
    """
    values = _drop_feb29(np.asarray(values, dtype=float), pd.DatetimeIndex(dates))
    values = _clip_flag(values, f"{plant_type}/{ws}")
    return _long_frame(values, plant_type, str(ws))


def era5_wind_solar_frames(country: str, year: int, config: dict) -> list:
    """Canonical wind/solar CF frames for a country/year from the ERA5 + atlite engine.

    Fail-soft: returns ``[]`` (logged) when atlite is unavailable or the cutout/conversion
    fails, so the dispatcher still produces a valid (RoR-only / empty) capacity_factors file.
    """
    try:
        import atlite  # noqa: F401
    except ImportError:
        logger.warning(
            "ERA5 engine: atlite not installed (`pip install 'supplyforge[era5]'`); skipping "
            "wind/solar for %s %s (fail-soft).", country, year,
        )
        return []
    try:
        from supplyforge.process.era5._atlite_engine import compute_country_frames
        return compute_country_frames(country, year, config)
    except Exception as exc:   # pragma: no cover - atlite/CDS path
        logger.warning("ERA5 engine: CF build failed for %s %s (%s); fail-soft.", country, year, exc)
        return []


def process_era5_capacity_factors(country: str, year: int, config: dict, output_path=None) -> Path:
    """Standalone: write the ERA5 ``capacity_factors_<C>_<year>.parquet`` (wind/solar only)."""
    import polars as pl
    from supplyforge import RESULTS_DIR
    from supplyforge.process.pecd.capacity_factor import CANONICAL_SCHEMA

    out = Path(output_path) if output_path else (
        Path(RESULTS_DIR) / "capacity_factors" / f"capacity_factors_{country}_{year}.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = era5_wind_solar_frames(country, year, config)
    if not frames:
        logger.warning("ERA5: no wind/solar frames for %s %s; writing empty file (fail-soft).", country, year)
        pl.DataFrame(schema=CANONICAL_SCHEMA).write_parquet(out)
        return out
    pl.concat(frames, how="vertical").sort(["plant_type", "WS", "hour"]).write_parquet(out)
    logger.info("ERA5: wrote %s", out)
    return out


__all__ = ["era5_wind_solar_frames", "process_era5_capacity_factors", "_series_to_frame"]
