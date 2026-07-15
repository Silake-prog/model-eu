"""
Day-ahead net transfer capacities (NTC) fetch module.

Role in SupplyForge pipeline
----------------------------

This module retrieves hourly day-ahead net transfer capacities (NTC)
from the ENTSO-E Transparency Platform for all cross-border interconnections
of a given country.

It belongs to the category of:

    *infrastructure capacity data fetch*

The module provides physical interconnection limits used to parameterize
cross-border electricity exchanges in the POMMES modeling framework.

Data source
-----------

The module queries ENTSO-E through:

- ``EntsoePandasClient``
- method: ``query_net_transfer_capacity_dayahead``

Neighboring countries are obtained via:

- ``entsoe.mappings.NEIGHBOURS``
- ``lookup_area``

An API token is required:

- environment variable: ``ENTSOE_API_TOKEN``

Inputs
------

Main function inputs:

- ``country_code``: two-letter country code (e.g. "FR", "DE")
- ``year``: target calendar year

Internal parameters:

- annual query window in UTC:

  - start: ``year-01-01 00:00 UTC``
  - end: ``year-12-31 23:59 UTC``

- timezone conversion:

  - from UTC → local ENTSO-E area timezone

Bidding zone handling
---------------------

ENTSO-E NTC data may require specific bidding zone codes
depending on historical configurations.

The module uses a mapping:

.. code-block:: python

    COUNTRY_BIDDING_ZONE_MAPPING = {
        "DE": "DE_LU",
        "IE": "IE_SEM",
        "LU": "DE_LU"
    }

Outputs
-------

The module writes one Parquet file per country and year:

- directory:
  ``RESULTS_DIR / "net_transfer_capacities"``
- filename:
  ``net_transfer_capacities_<country>_<year>.parquet``

Output schema (code-visible):

- index: ``timestamp`` (datetime, localized timezone)
- columns:

  - one column per neighbouring country
  - values: NTC (MW)

Structure:

- wide format:

  - each column represents a directional NTC:

    ``country → neighbour``

Temporal conventions
--------------------

- ENTSO-E data is queried in UTC
- converted to local timezone of the bidding zone
- resampled to hourly resolution:

  - ``df.resample('h').first()``

- full-year coverage expected

Algorithmic description
-----------------------

The module performs the following steps:

1. read country code and year
2. resolve ENTSO-E bidding zone:

   - use mapping if available
   - fallback to country code

3. load ENTSO-E API token from environment
4. initialize ``EntsoePandasClient``
5. retrieve ENTSO-E area metadata via ``lookup_area``
6. identify neighbouring countries via ``NEIGHBOURS``

7. for each neighbour:

   a. query day-ahead NTC:

      - ``country_code_from`` = country
      - ``country_code_to`` = neighbour

   b. if data exists:

      - store as pandas Series
      - name column after neighbour

   c. if no data:

      - skip silently (NoMatchingDataError)

8. concatenate all neighbour series into a DataFrame
9. convert timezone:

   - UTC → local ENTSO-E timezone

10. truncate to yearly window
11. resample to hourly resolution
12. convert pandas → Polars DataFrame
13. write Parquet output

14. if no data available:

    - create empty file via ``touch()``

Methodological assumptions
--------------------------

Explicit assumptions:

- NTC values represent valid operational transmission limits
- day-ahead NTC is representative of available cross-border capacity
- hourly resolution is sufficient for modeling

Implicit assumptions:

- NTC values can be used directly without smoothing or filtering
- directional NTC (country → neighbour) is sufficient
- no symmetry enforcement between directions

Integration in pipeline
-----------------------

Direct upstream dependency:

- ENTSO-E Transparency Platform API

Downstream usage in SupplyForge:

- used in ``create_pommes_craft_model.py``

Role in POMMES:

- defines cross-border transmission constraints
- used to construct:

  - interconnection capacities
  - import/export limits

In particular:

- maximum NTC values are often used to derive:

  - static interconnection capacities

Thus, this module contributes to:

    physical system constraints (network capacity)


"""

import logging
import os
from typing import List

import pandas as pd
import polars as pl
from dotenv import load_dotenv
load_dotenv()
from entsoe import EntsoePandasClient, exceptions
from entsoe.exceptions import NoMatchingDataError
from entsoe.mappings import NEIGHBOURS, lookup_area
from supplyforge import RESULTS_DIR

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# For some countries, ENTSO-E requires a specific bidding zone code
# for cross-border flow data, especially for historical queries.
# e.g., Germany was part of DE_LU bidding zone before October 2018.
COUNTRY_BIDDING_ZONE_MAPPING = {
    "DE": "DE_LU",
    "IE": "IE_SEM",
    "LU": "DE_LU"
}


def fetch_and_save_net_transfer_capacities(country_code: str, year: int) -> None:
    """
    Fetches physical cross-border flows from ENTSO-E for a given country and all its
    neighbors for a specific year, combines them, converts to a Polars DataFrame,
    and saves as a Parquet file.

    An ENTSO-E API token must be set in the `ENTSOE_API_TOKEN` environment variable.

    Args:
        country_code: The two-letter country code (e.g., 'DE', 'FR').
        year: The year for which to fetch the data.
    """
    original_country_code = country_code
    # Use a more specific bidding zone if required by ENTSO-E for the query
    entsoe_country_code = COUNTRY_BIDDING_ZONE_MAPPING.get(original_country_code, original_country_code)

    logger.info(f"Starting fetch for net transfer capacities for country: {original_country_code} (using code: {entsoe_country_code}), year: {year}")

    load_dotenv()
    api_token = os.getenv("ENTSOE_API_TOKEN")
    if not api_token:
        logger.error("ENTSOE_API_TOKEN environment variable not set.")
        raise ValueError("ENTSOE_API_TOKEN environment variable not set.")

    client = EntsoePandasClient(api_key=api_token)

    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year}-12-31 23:59", tz="UTC")


    logger.info(f"Fetching net transfer capacities data for {entsoe_country_code} for the year {year}...")
    # Define output path and ensure the directory exists
    # We use the original country code for the output file to maintain consistency.
    output_dir = RESULTS_DIR / "net_transfer_capacities"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"net_transfer_capacities_{original_country_code}_{year}.parquet"
    try:
        # Fetch data from ENTSO-E (returns a pandas DataFrame)
        area = lookup_area(entsoe_country_code)
        netcs = []
        for neighbour in NEIGHBOURS[area.name]:
            try:
                netc = client.query_net_transfer_capacity_dayahead(
                    country_code_from=country_code,
                    country_code_to=neighbour,
                    end=end,
                    start=start
                )

            except NoMatchingDataError:
                continue
            netc.name = neighbour
            netcs.append(netc)


        if len(netcs) == 0:
            logger.warning(f"No net transfer capacities data returned for {country_code} for {year}.")
            output_file.touch()
            return

        df = pd.concat(netcs, axis=1, sort=True)
        df = df.tz_convert(area.tz)
        df = df.truncate(before=start, after=end)
        df = df.resample('h').first()

        logger.info("Successfully fetched data from ENTSO-E.")

        # Convert to Polars DataFrame
        flow_df_pl = pl.from_pandas(df)

        # Save the DataFrame
        flow_df_pl.write_parquet(output_file)
        logger.info(f"Data saved to {output_file}")

    except Exception as e:
        logger.warning(f"An error occurred while fetching data for {original_country_code} ({year}): {e}", exc_info=True)
        output_file.touch()



if __name__ == "__main__":
    try:
        fetch_and_save_net_transfer_capacities(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        fetch_and_save_net_transfer_capacities(
            country_code="FR", year=2022
        )
        logger.error("This script is intended to be run via Snakemake's 'script' directive.")