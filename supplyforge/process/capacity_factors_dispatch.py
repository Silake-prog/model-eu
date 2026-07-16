r"""
Source-aware capacity-factor dispatcher (Snakemake ``script`` entry point).

Produces the canonical ``capacity_factors_<country>_<year>.parquet`` for whatever
``res_source`` (wind & solar) and ``ror_source`` (run-of-river) the config selects,
writing to the *same* path so ``rule all`` and the GCS upload are untouched.

Compose model
-------------

The capacity-factors file couples two selectors: wind/solar come from
``res_source`` while RoR follows ``hydro_source``/``ror_source``. This dispatcher
therefore *composes* them:

- **``entsoe`` + ``entsoe`` (the default)**: delegates to the original
  ``capacity_factor.calculate_intermittent_capacity_factors`` **unchanged**, so the
  legacy output is byte-for-byte identical (Operating Rule #1).
- **any other combination**: builds wind/solar frames from ``res_source`` and the
  RoR frame from ``ror_source``, harmonises ENTSO-E series to the canonical schema
  (``hour`` int, ``plant_type``/``WS`` categorical, ``capacity_factor`` f64), and
  concatenates. ENTSO-E ``compute_intermittent_capacity_factors`` is called at most
  once per run.
- **``res_source == era5``**: raises ``NotImplementedError`` (Phase 2 - the atlite
  engine is not yet implemented).
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from supplyforge import RESULTS_DIR, PACKAGE_DIR
from supplyforge.sources import resolve_res_source, resolve_ror_source
from supplyforge.process.capacity_factor import (
    calculate_intermittent_capacity_factors,
    compute_intermittent_capacity_factors,
)
from supplyforge.process.pecd.capacity_factor import (
    pecd_wind_solar_frames,
    pecd_ror_frame,
    _long_frame,
    CANONICAL_SCHEMA,
    ROR_PLANT_TYPE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

WIND_SOLAR_PLANT_TYPES = ["Solar", "Wind Onshore", "Wind Offshore"]


def _harmonize_entsoe(frame: "pl.DataFrame | None", plant_types: list[str], ws: str) -> list[pl.DataFrame]:
    """Harmonise selected plant types of a legacy ENTSO-E CF frame to canonical schema.

    The legacy frame has a datetime ``hour`` and extra columns; the consumer keeps
    only ``capacity_factor`` (re-indexing hours itself), so we sort by hour, take the
    capacity-factor series and re-emit it as a single-WS canonical frame.
    """
    out: list[pl.DataFrame] = []
    if frame is None:
        return out
    for plant_type in plant_types:
        sub = frame.filter(pl.col("plant_type") == plant_type).sort("hour")
        if sub.is_empty():
            continue
        values = sub["capacity_factor"].to_numpy().astype(float)
        out.append(_long_frame(values, plant_type, ws))
    return out


def run_capacity_factors(country: str, year: int, config: dict, output_path: str | Path | None = None) -> Path:
    """Build the canonical capacity-factors file for the configured sources."""
    out = Path(output_path) if output_path else RESULTS_DIR / "capacity_factors" / f"capacity_factors_{country}_{year}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)

    res = resolve_res_source(config)
    ror = resolve_ror_source(config)
    logger.info("capacity_factors: res_source=%s, ror_source=%s for %s %s.", res, ror, country, year)

    # Default path: pure legacy ENTSO-E -> byte-for-byte identical output.
    if res == "entsoe" and ror == "entsoe":
        calculate_intermittent_capacity_factors(country, year)
        return out

    ws = str(year)
    frames: list[pl.DataFrame] = []

    # Wind & solar from res_source.
    if res == "pecd":
        frames += pecd_wind_solar_frames(country, year, config)
    elif res == "era5":
        from supplyforge.process.era5.capacity_factor import era5_wind_solar_frames
        frames += era5_wind_solar_frames(country, year, config)   # fail-soft until atlite present
    else:  # entsoe
        frames += _harmonize_entsoe(
            compute_intermittent_capacity_factors(country, year), WIND_SOLAR_PLANT_TYPES, ws
        )

    # Run-of-river from ror_source.
    if ror == "pecd":
        ror_frame = pecd_ror_frame(country, year, config)
        if ror_frame is not None:
            frames.append(ror_frame)
    else:  # entsoe
        frames += _harmonize_entsoe(
            compute_intermittent_capacity_factors(country, year), [ROR_PLANT_TYPE], ws
        )

    if not frames:
        logger.warning("capacity_factors: nothing built for %s %s; writing empty file.", country, year)
        pl.DataFrame(schema=CANONICAL_SCHEMA).write_parquet(out)
        return out

    result = pl.concat(frames, how="vertical").sort(["plant_type", "WS", "hour"])
    result.write_parquet(out)
    logger.info("capacity_factors: composed %s for %s %s -> %s.",
                result["plant_type"].unique().to_list(), country, year, out)
    return out


if __name__ == "__main__":
    try:
        country = snakemake.params.country  # type: ignore[name-defined]
        year = int(snakemake.params.year)  # type: ignore[name-defined]
        config = snakemake.config  # type: ignore[name-defined]
        output_path = snakemake.output[0]  # type: ignore[name-defined]
    except NameError:
        import yaml
        logger.info("Not running under Snakemake; standalone debug run.")
        config = yaml.safe_load((PACKAGE_DIR / "config" / "config.yaml").read_text())
        country, year, output_path = "FR", 2008, None
    run_capacity_factors(country, year, config, output_path)
