r"""
Build / cache an ``atlite`` ERA5 cutout per (country, climate year).

``atlite`` owns the actual CDS download (``module="era5"``); credentials come from
``~/.cdsapirc`` (same convention as the PECD fetcher, ``fetch/pecd/study.py``). The cutout is
cached as a NetCDF under ``RESULTS_DIR/era5_res/`` and reopened cheaply on re-runs. ``atlite``
is an optional dependency (the ``era5`` extra) — every entry point here **fail-softs** to
``None`` (logged) when it's absent.

FLAG: the ``atlite.Cutout(...)`` / ``.prepare()`` API is verified at implementation against the
installed atlite (it was not installed when this was written).
"""
from __future__ import annotations

import logging
from pathlib import Path

from supplyforge import RESULTS_DIR
from supplyforge.fetch.era5.geometry import get_country_bbox

logger = logging.getLogger(__name__)

ERA5_DIR = Path(RESULTS_DIR) / "era5_res"


def cutout_path(country: str, year: int) -> Path:
    return ERA5_DIR / f"cutout_{country}_{year}.nc"


def ensure_cutout(country: str, year: int, config: dict):
    """Return a built/cached atlite ERA5 Cutout for ``(country, year)``, or ``None`` (fail-soft).

    Skips the (slow) CDS download when a non-trivial cached cutout already exists.
    """
    try:
        import atlite
    except ImportError:
        logger.warning("ERA5 cutout: atlite not installed; cannot build cutout for %s %s.", country, year)
        return None

    path = cutout_path(country, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        logger.info("ERA5 cutout: using cached %s", path.name)
        return atlite.Cutout(path=str(path))

    bbox = get_country_bbox(country)
    if bbox is None:
        logger.warning("ERA5 cutout: no bbox for %s; skipping (fail-soft).", country)
        return None

    logger.info("ERA5 cutout: building %s via CDS (may queue/download for minutes) ...", path.name)
    cutout = atlite.Cutout(                       # FLAG: verify atlite Cutout signature
        path=str(path), module="era5",
        x=slice(bbox["min_lon"], bbox["max_lon"]),
        y=slice(bbox["min_lat"], bbox["max_lat"]),
        time=str(year),
    )
    cutout.prepare()                              # FLAG: triggers the ERA5 CDS download
    return cutout


__all__ = ["ERA5_DIR", "cutout_path", "ensure_cutout"]
