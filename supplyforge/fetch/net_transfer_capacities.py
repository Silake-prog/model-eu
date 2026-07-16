"""
Net transfer capacities fetch module.

Wraps :mod:`supplyforge.fetch.ember_ntc` for Snakemake usage.
Produces one Parquet file per country with static reference NTC values
sourced from the Ember Europe Electricity Interconnection dataset.

Output
------
``results/net_transfer_capacities/net_transfer_capacities_{country}_{year}.parquet``

Wide-format, one row, one column per connected country (MW).
"""

import logging

from supplyforge import RESULTS_DIR
from supplyforge.fetch.ember_ntc import get_ntc_for_country

logger = logging.getLogger(__name__)


def fetch_and_save_net_transfer_capacities(country_code: str, year: int) -> None:
    """
    Extracts Ember reference NTC values for *country_code* and saves them as
    a wide-format Parquet file.

    Args:
        country_code: 2-letter country code (e.g. 'FR', 'DE').
        year: Reference year (used for file naming; Ember data is static).
    """
    output_dir = RESULTS_DIR / "net_transfer_capacities"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"net_transfer_capacities_{country_code}_{year}.parquet"

    try:
        ntc_df = get_ntc_for_country(country_code)

        if ntc_df.is_empty():
            logger.warning(f"No NTC data found for {country_code}. Writing empty file.")
            output_file.touch()
            return

        ntc_df.write_parquet(output_file)
        logger.info(f"NTC data for {country_code} saved to {output_file}")

    except Exception as e:
        logger.warning(
            f"Error fetching Ember NTC for {country_code} ({year}): {e}",
            exc_info=True,
        )
        output_file.touch()


if __name__ == "__main__":
    try:
        fetch_and_save_net_transfer_capacities(
            country_code=snakemake.params.country,
            year=int(snakemake.params.year),
        )
    except NameError:
        fetch_and_save_net_transfer_capacities(country_code="FR", year=2023)
        logger.error("This script is intended to be run via Snakemake's 'script' directive.")
