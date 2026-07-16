"""
UK hydro storage fetch module (empty-file fallback).

Great Britain has negligible reservoir-type hydro storage compared to countries
like Norway, France, or Austria.  The main UK flexible hydro assets are pumped-
storage stations (Dinorwig, Cruachan, Ffestiniog, Foyers), which are not
equivalent to the ENTSO-E aggregate water-reservoir metric.

This module writes an empty Parquet file with the schema expected by the
downstream ``hydro_inflow.py`` module.  An empty storage input causes
``hydro_inflow.py`` to produce an empty inflow output, which is the correct
behaviour for a country with no significant open-cycle reservoir hydro.

If UK pumped-storage level data is required in the future, the Elexon EIS
``GET /datasets/FUELINST`` endpoint includes PS (pumped storage) net generation
which could be integrated to derive implied storage state.

Outputs
-------
``RESULTS_DIR / "hydro_storage" / "hydro_storage_UK_{year}.parquet"``

Schema (empty):

- ``timestamp``   : Datetime (UTC)
- ``storage_mwh`` : Float64
"""

import logging

import polars as pl

from supplyforge import RESULTS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_SCHEMA = {
    "timestamp":   pl.Datetime(time_unit="us", time_zone="UTC"),
    "storage_mwh": pl.Float64,
}


def fetch_and_save_hydro_storage_uk(country_code: str, year: int) -> None:
    """
    Write an empty hydro-storage Parquet for the UK.

    Args:
        country_code: Should be ``"UK"``.
        year: Calendar year (unused, included for interface consistency).
    """
    output_dir = RESULTS_DIR / "hydro_storage"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"hydro_storage_{country_code}_{year}.parquet"

    logger.info(
        f"UK has no significant reservoir hydro storage for {year}. "
        "Writing empty file — hydro inflow will be empty for UK."
    )

    pl.DataFrame(schema=_SCHEMA).write_parquet(output_file)


if __name__ == "__main__":
    try:
        fetch_and_save_hydro_storage_uk(
            country_code=snakemake.params.country,
            year=int(snakemake.params.year),
        )
    except NameError:
        logger.error("This module is called from hydro_storage.py, not directly by Snakemake.")
