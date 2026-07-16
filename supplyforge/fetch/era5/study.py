r"""
Snakemake fetch entry point for the raw-ERA5 engine — stage atlite cutouts.

Mirrors :mod:`supplyforge.fetch.pecd.study`. Loops ``era5.climate_years`` ×
``config['countries']``, builds/caches an ERA5 cutout for each (fail-soft per item), and
writes ``results/era5_res/download.txt`` (a provenance manifest, S2). atlite owns the CDS
download; this just orchestrates + records.
"""
from __future__ import annotations

import logging

from supplyforge.fetch.era5.cutout import ERA5_DIR, ensure_cutout

logger = logging.getLogger(__name__)


def fetch_era5_cutouts(config: dict):
    """Build/cache ERA5 cutouts for all configured countries × climate years; write the manifest."""
    ERA5_DIR.mkdir(parents=True, exist_ok=True)
    era5 = config.get("era5") or {}
    years = era5.get("climate_years") or []
    countries = config.get("countries") or []
    built, skipped = [], []
    for year in years:
        for country in countries:
            try:
                cutout = ensure_cutout(country, int(year), config)
            except Exception as exc:   # pragma: no cover - CDS/atlite path
                logger.warning("ERA5 fetch: %s %s failed (%s); skipping (fail-soft).", country, year, exc)
                cutout = None
            (built if cutout is not None else skipped).append(f"{country}_{year}")

    manifest = ERA5_DIR / "download.txt"
    manifest.write_text(
        "raw-ERA5 atlite cutouts (res_source=era5)\n"
        f"climate_years={years}\n"
        f"countries={countries}\n"
        f"hub_height_m={era5.get('hub_height_m')} turbine_onshore={era5.get('turbine_onshore')} "
        f"turbine_offshore={era5.get('turbine_offshore')} pv_panel={era5.get('pv_panel')} "
        f"pv_orientation={era5.get('pv_orientation')} land_use_masks={era5.get('land_use_masks')} "
        f"bias_correction={era5.get('bias_correction')}\n"
        f"built={built}\n"
        f"skipped={skipped}\n"
    )
    logger.info("ERA5 fetch: %d cutout(s) built, %d skipped -> %s", len(built), len(skipped), manifest)
    return manifest


if __name__ == "__main__":
    try:
        cfg = snakemake.config  # type: ignore[name-defined]  # noqa: F821
    except NameError:
        cfg = {"countries": ["FR"], "era5": {"climate_years": [2008]}}
        logger.error("ERA5 study: intended to run via Snakemake's 'script' directive; using debug defaults.")
    fetch_era5_cutouts(cfg)
