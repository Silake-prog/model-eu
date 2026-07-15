"""
Physical cross-border flow fetch module.

Role in SupplyForge pipeline
----------------------------

This module retrieves historical physical cross-border electricity flows for a
country from the ENTSO-E Transparency Platform and stores them as a Parquet
dataset in the SupplyForge results tree.

It belongs to the category of:

    *cross-border exchange data fetch*

The module is intended primarily for historical analysis, validation, and
supporting studies of international electricity exchanges. In the currently
documented SupplyForge architecture, it is not the main source used to build
interconnection constraints in POMMES, which rely instead on net transfer
capacity datasets.

Data source
-----------

The module queries ENTSO-E through:

- ``EntsoePandasClient``
- method: ``query_physical_crossborder_allborders``

The API request is made for one country and all its borders at once, for a full
calendar year, with hourly aggregation requested through the API call.

A local environment variable is required:

- ``ENTSOE_API_TOKEN``

Inputs
------

Main function inputs:

- ``country_code``: two-letter country code
- ``year``: target calendar year

Internal code-visible parameters:

- annual time window in UTC
- ``export=False``
- ``per_hour=True``

Country-code adjustment
-----------------------

For some countries, the code replaces the requested country code with a more
specific ENTSO-E bidding-zone code before querying the API.

Currently visible mapping:

- ``DE -> DE_LU``

The original country code is nevertheless preserved in the output file name.

Outputs
-------

The module writes one Parquet file per country and year:

- directory:
  ``RESULTS_DIR / "crossborder_flows"``
- filename:
  ``crossborder_flows_<country>_<year>.parquet``

The exact output schema is inherited from the ENTSO-E client response and is not
normalized in this module.

Code-visible output properties:

- the response is converted from pandas to Polars
- the result is written directly to Parquet
- no column renaming, reshaping, or unit conversion is applied

If ENTSO-E returns no matching data, the code attempts to create an empty marker
file.

Algorithmic description
-----------------------

The module performs the following steps:

1. read the country code and year
2. replace the country code with a specific bidding-zone code when required
3. load the ENTSO-E API token from environment variables
4. initialize an ``EntsoePandasClient``
5. define an annual UTC query window:

   - start: ``year-01-01 00:00 UTC``
   - end: ``year-12-31 23:59 UTC``

6. query ENTSO-E for hourly physical cross-border flows on all borders of the
   selected country
7. convert the returned pandas object to a Polars DataFrame
8. save the dataset to Parquet in ``RESULTS_DIR``

Temporal conventions
--------------------

The query window is defined in UTC and spans the full calendar year.

Code-visible timestamps:

- start: ``pd.Timestamp(f"{year}-01-01", tz="UTC")``
- end: ``pd.Timestamp(f"{year}-12-31 23:59", tz="UTC")``

The API request explicitly asks for:

- hourly output: ``per_hour=True``

Methodological assumptions
--------------------------

Documented and code-visible assumptions include:

- ENTSO-E physical flow data are an acceptable representation of observed
  cross-border electricity exchanges
- a single country-level query across all borders is sufficient for the intended
  historical dataset
- hourly resolution is adequate for downstream analysis
- bidding-zone substitution is necessary for some historical country queries

Implicit assumptions include:

- the ENTSO-E client returns a schema stable enough to be stored without local
  normalization
- the meaning and direction convention of the returned flow values are handled
  later, or are already understood by the analyst
- no additional harmonization across borders is required in this module

Integration in pipeline
-----------------------

Direct upstream dependency:

- ENTSO-E Transparency Platform API

Documented downstream role:

- historical exchange analysis
- validation
- auxiliary studies

Not the main interconnection input to POMMES:

- ``create_pommes_craft_model.py`` relies on
  ``net_transfer_capacities.py`` rather than on this module for modeled
  interconnection constraints

Therefore, this module is best interpreted as:

    historical cross-border exchange observation dataset

rather than:

    direct POMMES parameter construction module
"""

import logging
import os
from typing import List

import pandas as pd
import polars as pl
from dotenv import load_dotenv
from entsoe import EntsoePandasClient, exceptions
from entsoe.mappings import NEIGHBOURS
from supplyforge import RESULTS_DIR

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# For some countries, ENTSO-E requires a specific bidding zone code
# for cross-border flow data, especially for historical queries.
# e.g., Germany was part of DE_LU bidding zone before October 2018.
COUNTRY_BIDDING_ZONE_MAPPING = {
    "DE": "DE_LU",
}


def fetch_and_save_crossborder_flows(country_code: str, year: int) -> None:
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

    logger.info(f"Starting fetch for cross-border flows for country: {original_country_code} (using code: {entsoe_country_code}), year: {year}")

    load_dotenv()
    api_token = os.getenv("ENTSOE_API_TOKEN")
    if not api_token:
        logger.error("ENTSOE_API_TOKEN environment variable not set.")
        raise ValueError("ENTSOE_API_TOKEN environment variable not set.")

    client = EntsoePandasClient(api_key=api_token)

    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year}-12-31 23:59", tz="UTC")


    # Define output path and ensure the directory exists
    # We use the original country code for the output file to maintain consistency.
    output_dir = RESULTS_DIR / "crossborder_flows"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"crossborder_flows_{original_country_code}_{year}.parquet"
    logger.info(f"Fetching cross border flow data for {entsoe_country_code} for the year {year}...")
    try:
        # Fetch data from ENTSO-E (returns a pandas DataFrame)
        flow_df_pd = client.query_physical_crossborder_allborders(entsoe_country_code,
                                                                  start=start,
                                                                  end=end,
                                                                  export=False,
                                                                  per_hour=True)
        logger.info("Successfully fetched data from ENTSO-E.")

        if flow_df_pd.empty:
            logger.warning(f"No cross border flow data returned for {country_code} for {year}.")
            return

        # Convert to Polars DataFrame
        flow_df_pl = pl.from_pandas(flow_df_pd)


        # Save the DataFrame
        flow_df_pl.write_parquet(output_file)
        logger.info(f"Data saved to {output_file}")

    except NoMatchingDataError:
        output_file.touch()
    except Exception as e:
        logger.error(f"An error occurred while fetching data for {original_country_code} ({year}): {e}", exc_info=True)
        raise


if __name__ == "__main__":
    try:
        fetch_and_save_crossborder_flows(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        logger.error("This script is intended to be run via Snakemake's 'script' directive.")