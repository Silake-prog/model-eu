"""
Generation-unit unavailability retrieval module.

Role in SupplyForge pipeline
----------------------------

This module retrieves generation-unit unavailability events from the ENTSO-E
Transparency Platform for a given country and year and stores them as a Parquet
file in the SupplyForge results directory.

It belongs to the category of:

    *event-based supply data fetch*

The module does not yet convert unavailability events into hourly availability
profiles. Instead, it provides the raw event-level input that is used
downstream by ``availability.py`` to construct hourly availability shares by
plant type.

Data source
-----------

- Provider: ENTSO-E Transparency Platform
- Access library: ``EntsoePandasClient``
- API method used: ``query_unavailability_of_generation_units``

The module queries one country and one year at a time.

For some countries, the ENTSO-E query is performed using a specific bidding-zone
code rather than the original country code. In the current implementation:

- ``DE`` is mapped to ``DE_LU``

The output filename nevertheless preserves the original country code.

Inputs
------
Main inputs:

- ``country_code``: two-letter country code
- ``year``: target calendar year

Implicit inputs:

- ENTSO-E API token from the environment variable ``ENTSOE_API_TOKEN``
- ``RESULTS_DIR`` as local output root directory
- internal bidding-zone remapping rules in
  ``COUNTRY_BIDDING_ZONE_MAPPING``

Outputs
-------
The module writes one Parquet file per country and year in:

- ``RESULTS_DIR / "unavailability"``

with filename:

- ``unavailability_<country_code>_<year>.parquet``

The saved dataset is the event-level table returned by ENTSO-E, converted
directly to a Polars DataFrame without additional normalization in this module.

According to the workflow documentation and downstream use in
``availability.py``, expected columns include at least:

- ``start``
- ``end``
- ``nominal_power``
- ``avail_qty``
- ``docstatus``
- ``plant_type``

Algorithmic description
-----------------------

The module performs the following steps:

1. Preserve the original input country code for output naming
2. Replace the country code with a specific ENTSO-E bidding-zone code when
   required by ``COUNTRY_BIDDING_ZONE_MAPPING``
3. Load environment variables from ``.env``
4. Read the ENTSO-E API token from ``ENTSOE_API_TOKEN``
5. Initialize an ``EntsoePandasClient``
6. Define a UTC annual query window from:

   - ``<year>-01-01 00:00 UTC``
   - ``<year>-12-31 23:59 UTC``

7. Create the output directory ``RESULTS_DIR / "unavailability"``, if needed
8. Query ENTSO-E generation-unit unavailability events for the mapped code
9. Convert the returned pandas object to a Polars DataFrame
10. Save the result as Parquet under the original country code

If the returned pandas DataFrame is empty, the module logs a warning and
returns without writing a Parquet file.

If ENTSO-E raises ``requests.exceptions.HTTPError`` or
``NoMatchingDataError``, the module creates an empty placeholder file at the
target output path.

Temporal handling
-----------------

The query window is defined explicitly in UTC for the target calendar year.

The output is event-based rather than hourly: the module retrieves unavailability
records with time bounds and does not expand them into a regular time series.

Units
-----

The module does not redefine or convert physical units.

According to the expected downstream schema, the data include at least:

- nominal unit power
- available quantity during the event

Methodological assumptions
--------------------------

Code-visible assumptions include:

- ENTSO-E event-level unavailability data are the appropriate raw input for
  downstream hourly availability reconstruction
- some historical or country-specific queries require bidding-zone remapping
  prior to data retrieval
- direct serialization of the returned ENTSO-E event table is sufficient at
  this stage
- the original country code should be preserved in output filenames even when a
  different code is used for the query

Implicit assumptions include:

- the mapped bidding-zone code is a valid proxy for the requested country in the
  context of unavailability retrieval
- downstream modules can interpret the raw ENTSO-E event schema without prior
  harmonization in this fetch step

Integration in pipeline
-----------------------

Downstream module:

- ``availability.py``

The event-level unavailability data produced here are later combined with
installed capacities in ``availability.py`` to construct hourly availability
shares by plant type.

These hourly availability profiles are then used in
``create_pommes_craft_model.py`` to constrain dispatchable generation
technologies in POMMES.

The module therefore provides the raw outage-event layer of the SupplyForge
pipeline but is not itself a direct POMMES input.
"""

import logging
import os

import pandas as pd
import polars as pl
import requests
from dotenv import load_dotenv
load_dotenv()
from entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError

from supplyforge import RESULTS_DIR
from supplyforge.fetch.utils_uk import UK_ENTSOE_CUTOFF_YEAR, ENTSOE_COUNTRY_CODE_FOR_UK

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# For some countries, ENTSO-E requires a specific bidding zone code
# for unavailability data, especially for historical queries.
# e.g., Germany was part of DE_LU bidding zone before October 2018.
COUNTRY_BIDDING_ZONE_MAPPING = {
    "DE": "DE_LU",
    "UK": ENTSOE_COUNTRY_CODE_FOR_UK,  # ENTSO-E uses "GB" for pre-2020 UK queries
}


def fetch_and_save_unavailability(country_code: str, year: int) -> None:
    """
    Fetches unavailability of generation units from ENTSO-E for a given country and year,
    converts it to a Polars DataFrame, and saves it as a Parquet file.

    An ENTSO-E API token must be set in the `ENTSOE_API_TOKEN` environment variable.
    For ``country_code="UK"`` and ``year >= UK_ENTSOE_CUTOFF_YEAR``, delegates to the
    empty-file fallback ``fetch_and_save_unavailability_uk`` instead.

    Args:
        country_code: The two-letter country code (e.g., 'DE', 'FR').
        year: The year for which to fetch the data.
    """
    if country_code == "UK" and int(year) >= UK_ENTSOE_CUTOFF_YEAR:
        from supplyforge.fetch.unavailability_uk import fetch_and_save_unavailability_uk
        fetch_and_save_unavailability_uk(country_code, year)
        return

    original_country_code = country_code
    # Use a more specific bidding zone if required by ENTSO-E for the query
    entsoe_country_code = COUNTRY_BIDDING_ZONE_MAPPING.get(country_code, country_code)

    logger.info(f"Starting fetch for unavailability for country: {original_country_code} (using code: {entsoe_country_code}), year: {year}")

    # Load environment variables from .env file
    load_dotenv()
    api_token = os.getenv("ENTSOE_API_TOKEN")
    if not api_token:
        logger.error("ENTSOE_API_TOKEN environment variable not set.")
        raise ValueError("ENTSOE_API_TOKEN environment variable not set.")

    client = EntsoePandasClient(api_key=api_token)

    # Define the time range for the entire year in UTC
    start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    end = pd.Timestamp(f"{year}-12-31 23:59", tz="UTC")

    # Define output path and ensure the directory exists
    # We use the original country code for the output file to maintain consistency.
    output_dir = RESULTS_DIR / "unavailability"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"unavailability_{original_country_code}_{year}.parquet"

    logger.info(f"Fetching unavailability data for {entsoe_country_code} for the year {year}...")
    try:
        # Fetch data from ENTSO-E (returns a pandas DataFrame)
        unavailability_df_pd = client.query_unavailability_of_generation_units(
            entsoe_country_code, start=start, end=end
        )
        logger.info("Successfully fetched data from ENTSO-E.")

        if unavailability_df_pd.empty:
            logger.warning(f"No unavailability data returned for {country_code} for {year}.")
            return

        # Convert to Polars DataFrame
        unavailability_df_pl = pl.from_pandas(unavailability_df_pd)


        # Save the DataFrame
        unavailability_df_pl.write_parquet(output_file)
        logger.info(f"Data saved to {output_file}")
    except requests.exceptions.HTTPError as e:
        output_file.touch()
    except NoMatchingDataError:
        output_file.touch()
    except Exception as e:
        logger.error(f"An error occurred while fetching data for {original_country_code} ({year}): {e}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        fetch_and_save_unavailability(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        logger.error("This script is intended to be run via Snakemake's 'script' directive.")