"""
Day-ahead electricity price fetch module.

Role in SupplyForge pipeline
----------------------------

This module retrieves hourly day-ahead electricity prices from the ENTSO-E
Transparency Platform and stores them as a Parquet dataset in the
SupplyForge results directory.

It belongs to the category of:

    *market signal data fetch*

The module provides price signals used for economic valuation of imports and
exports in the POMMES modeling framework.

Data source
-----------

The module queries ENTSO-E through:

- ``EntsoePandasClient``
- method: ``query_day_ahead_prices``

An API token is required:

- environment variable: ``ENTSOE_API_TOKEN``

Inputs
------

Main function inputs:

- ``country_code``: two-letter country code (e.g. "FR", "DE")
- ``year``: target calendar year

Internal parameters:

- annual query window in UTC
- retry configuration:

  - ``retry_delay=60``
  - ``retry_count=10``
  - ``timeout=360``

Bidding zone handling
---------------------

ENTSO-E prices are defined at the bidding-zone level, not strictly at the
country level.

The module uses a mapping:

.. code-block:: python

    COUNTRY_BIDDING_ZONE_MAPPING = {
        "DE": ["DE_LU"],
        "IT": ["IT_NORD", "IT_CNOR", "IT_CSUD", "IT_SUD",
               "IT_SICI", "IT_SARD", "IT_CALA"]
    }

Behavior:

- if a country has multiple bidding zones:

  - each zone is queried independently
  - results are concatenated

- if no mapping exists:

  - the country code itself is used as a single bidding zone

Outputs
-------

The module writes one Parquet file per country and year:

- directory:
  ``RESULTS_DIR / "day_ahead_prices"``
- filename:
  ``day_ahead_prices_<country>_<year>.parquet``

Output schema (code-visible):

- ``timestamp`` (datetime, UTC)
- ``price_eur_per_mwh`` (float)
- additional column (before transformation):

  - ``bidding_zone`` (lost after renaming/aggregation step)


Algorithmic description
-----------------------

The module performs the following steps:

1. read country code and year
2. resolve bidding zones:

   - use mapping if available
   - fallback to country code

3. load ENTSO-E API token from environment
4. initialize ``EntsoePandasClient`` with retry configuration
5. define annual UTC query window:

   - start: ``year-01-01 00:00 UTC``
   - end: ``year-12-31 23:59 UTC``

6. for each bidding zone:

   a. query day-ahead prices (pandas Series)
   b. convert to DataFrame
   c. add ``bidding_zone`` column
   d. append to list if non-empty

7. concatenate all zone DataFrames (if multiple)
8. reset index → convert timestamp column
9. convert pandas → Polars DataFrame
10. rename columns:

    - ``index`` → ``timestamp``
    - ``0`` → ``price_eur_per_mwh``

11. if no data found:

    - create empty Polars DataFrame with schema

12. write Parquet output

Temporal conventions
--------------------

- timestamps are handled in UTC
- full-year coverage:

  - start: ``pd.Timestamp(f"{year}-01-01", tz="UTC")``
  - end: ``pd.Timestamp(f"{year}-12-31 23:59", tz="UTC")``

- hourly resolution (ENTSO-E standard for day-ahead prices)

Methodological assumptions
--------------------------

Explicit assumptions:

- ENTSO-E day-ahead prices represent valid marginal price signals
- bidding zones are appropriate units for price retrieval
- hourly resolution is sufficient for downstream modeling

Implicit assumptions:

- concatenation of multiple bidding zones is acceptable
- no weighting or aggregation is required across zones
- price signals can be used directly without currency/unit transformation

Integration in pipeline
-----------------------

Direct upstream dependency:

- ENTSO-E Transparency Platform API

Downstream usage in SupplyForge:

- used in ``create_pommes_craft_model.py``
- specifically via:

  - ``get_electricity_import_prices``

Role in POMMES:

- defines economic signals for:

  - electricity imports
  - electricity exports

- affects:

  - dispatch decisions
  - cross-border trade valuation

Thus, this module contributes to:

    economic parametrization (not physical constraints)

"""

import logging
import os

import pandas as pd
import polars as pl
from dotenv import load_dotenv
from entsoe import EntsoePandasClient, exceptions
from supplyforge import RESULTS_DIR
from supplyforge.fetch.utils_uk import UK_ENTSOE_CUTOFF_YEAR, ENTSOE_COUNTRY_CODE_FOR_UK

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# For some countries, ENTSO-E requires a specific bidding zone code
# for day-ahead prices, especially for historical queries.
# e.g., Germany was part of DE_LU bidding zone before October 2018.
# For countries with multiple bidding zones, provide a list of codes.
# The script will fetch data for all zones and average the prices.
# Example for Italy (not exhaustive): IT: ["IT_NORD", "IT_CNOR", "IT_CSUD", "IT_SUD"]
COUNTRY_BIDDING_ZONE_MAPPING = {
    "DE": ["DE_LU"],
    "IT": [
        "IT_NORD", "IT_CNOR", "IT_CSUD", "IT_SUD",
        "IT_SICI", "IT_SARD", "IT_CALA"
    ],
    # Pre-2020 UK data lives under ENTSO-E bidding zone "GB"
    "UK": [ENTSOE_COUNTRY_CODE_FOR_UK],
}


def fetch_and_save_day_ahead_prices(country_code: str, year: int) -> None:
    """
    Fetches day-ahead prices from ENTSO-E for a given country and year,
    converts it to a Polars DataFrame, and saves it as a Parquet file.

    An ENTSO-E API token must be set in the `ENTSOE_API_TOKEN` environment variable.
    For ``country_code="UK"`` and ``year >= UK_ENTSOE_CUTOFF_YEAR``, delegates to the
    Elexon-based ``fetch_and_save_day_ahead_prices_uk`` instead.

    Args:
        country_code: The two-letter country code (e.g., 'DE', 'FR').
        year: The year for which to fetch the data.
    """
    if country_code == "UK" and int(year) >= UK_ENTSOE_CUTOFF_YEAR:
        from supplyforge.fetch.day_ahead_prices_uk import fetch_and_save_day_ahead_prices_uk
        fetch_and_save_day_ahead_prices_uk(country_code, year)
        return

    original_country_code = country_code
    # Use a more specific bidding zone if required by ENTSO-E for the query
    # If no mapping exists, use the country code itself in a list.
    entsoe_bidding_zones = COUNTRY_BIDDING_ZONE_MAPPING.get(country_code, [country_code])

    logger.info(
        f"Starting fetch for day-ahead prices for country: {original_country_code} (using zones: {entsoe_bidding_zones}), year: {year}"
    )

    # Load environment variables from .env file
    load_dotenv()
    api_token = os.getenv("ENTSOE_API_TOKEN")
    if not api_token:
        logger.error("ENTSOE_API_TOKEN environment variable not set.")
        raise ValueError("ENTSOE_API_TOKEN environment variable not set.")

    client = EntsoePandasClient(api_key=api_token, retry_delay=60, retry_count=10, timeout=360)

    # Define the time range for the entire year in UTC
    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year}-12-31 23:59", tz="UTC")

    logger.info(f"Fetching day-ahead prices for {original_country_code} for the year {year}...")
    prices_df_pl = None
    try:
        all_prices_series = []
        for zone in entsoe_bidding_zones:
            try:
                logger.info(f"Querying bidding zone: {zone}")
                # Fetch data from ENTSO-E (returns a pandas Series)
                prices_pd = client.query_day_ahead_prices(zone, start=start, end=end)
                prices_pd = prices_pd.to_frame()
                prices_pd["bidding_zone"] = zone
                if prices_pd is not None and not prices_pd.empty:
                    all_prices_series.append(prices_pd)
            except exceptions.NoMatchingDataError:
                logger.warning(f"No day-ahead price data found on ENTSO-E for bidding zone {zone} for {year}.")

        if all_prices_series:
            logger.info("Successfully fetched data from ENTSO-E.")
            if len(all_prices_series) > 1:
                logger.info(f"Averaging prices across {len(all_prices_series)} bidding zones.")
                # Concatenate into a DataFrame and calculate the mean price across zones for each timestamp
                prices_pd = pd.concat(all_prices_series, axis=0)
            else:
                prices_pd = all_prices_series[0]

            # Convert pandas Series to a DataFrame and reset the index
            prices_df_pd = prices_pd.reset_index()
            # Convert to Polars DataFrame and rename columns
            prices_df_pl = pl.from_pandas(prices_df_pd).rename({"index": "timestamp", "0": "price_eur_per_mwh"})

    except exceptions.NoMatchingDataError:
        logger.warning(f"No day-ahead price data found on ENTSO-E for any specified zone in {original_country_code} for {year}.")
    except Exception as e:
        logger.error(f"An error occurred while fetching data for {original_country_code} ({year}): {e}", exc_info=True)
        raise

    if prices_df_pl is None:
        logger.info(f"Creating an empty parquet file for {original_country_code} for {year}.")
        prices_df_pl = pl.DataFrame(
            schema={"timestamp": pl.Datetime(time_unit="us", time_zone="UTC"), "price_eur_per_mwh": pl.Float64}
        )

    # Define output path and ensure the directory exists
    # We use the original country code for the output file to maintain consistency.
    output_dir = RESULTS_DIR / "day_ahead_prices"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"day_ahead_prices_{original_country_code}_{year}.parquet"

    # Save the DataFrame (either with data or empty)
    prices_df_pl.write_parquet(output_file)
    logger.info(f"Data saved to {output_file}")

if __name__ == "__main__":
    try:
        fetch_and_save_day_ahead_prices(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        logger.info("This script is intended to be run via Snakemake's 'script' directive. Here it is a standalone"
                    "for testing purposes.")

        fetch_and_save_day_ahead_prices(
            country_code="ES", year=2021
        )