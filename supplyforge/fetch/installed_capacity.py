"""
Installed generation capacity retrieval module.

Role in SupplyForge pipeline
----------------------------

This module retrieves installed electricity generation capacity by production
type from the ENTSO-E Transparency Platform for a given country and year and
stores the result as a Parquet file in the SupplyForge results directory.

It belongs to the category of:

    *structural supply data fetch*

The module provides the installed-capacity dataset used downstream to derive
maximum available generation capacities, compute availability profiles for
dispatchable technologies, and calculate capacity factors for intermittent
technologies.

Data source
-----------

- Provider: ENTSO-E Transparency Platform
- Access library: ``EntsoePandasClient``
- API method used: ``query_installed_generation_capacity``

The module queries one country and one year at a time and serializes the
installed-capacity table returned by ENTSO-E without technology-name
normalization at this stage.

Inputs
------
Main inputs:

- ``country_code``: two-letter country code
- ``year``: target calendar year

Implicit inputs:

- ENTSO-E API token from the environment variable ``ENTSOE_API_TOKEN``
- ``RESULTS_DIR`` as local output root directory

Outputs
-------
The module writes one Parquet file per country and year in:

- ``RESULTS_DIR / "installed_capacities"``

with filename:

- ``installed_capacities_<country_code>_<year>.parquet``

The saved dataset is the ENTSO-E installed-capacity table converted to a Polars
DataFrame after index reset, so that the original index becomes an explicit
column.

Algorithmic description
-----------------------

The module performs the following steps:

1. Load environment variables from ``.env``
2. Read the ENTSO-E API token from ``ENTSOE_API_TOKEN``
3. Initialize an ``EntsoePandasClient``
4. Define a UTC annual query window from:

   - ``<year>-01-01 00:00 UTC``
   - ``<year>-12-31 23:59 UTC``

5. Create the output directory ``RESULTS_DIR / "installed_capacities"``, if
   needed
6. Query ENTSO-E installed generation capacity with:

   - ``psr_type=None``

7. Convert the returned pandas object to a Polars DataFrame after resetting the
   index
8. Save the result as Parquet

If the returned pandas DataFrame is empty, the module creates an empty file at
the target output path and returns.

If ENTSO-E raises ``NoMatchingDataError``, the module also creates an empty file
at the target output path.

Temporal handling
-----------------

The query window is defined explicitly in UTC for the target calendar year.

Units
-----

The module does not redefine or convert physical units.

According to the workflow documentation, the output preserves the installed
capacity units returned by the ENTSO-E query.

Methodological assumptions
--------------------------

Code-visible assumptions include:

- the ENTSO-E installed-capacity query provides the relevant annual structural
  capacity data for the requested country and year
- the raw technology labels returned by ENTSO-E can be preserved unchanged at
  this stage
- direct Parquet serialization of the fetched table is sufficient for downstream
  processing
- querying with ``psr_type=None`` is appropriate to retrieve the full set of
  production-type capacities

Implicit assumptions include:

- the installed-capacity snapshot returned for the annual query is suitable for
  downstream aggregation by technology
- downstream modules can interpret the returned production-type categories
  without prior harmonization in this fetch step

Integration in pipeline
-----------------------

Downstream modules include:

- ``availability.py``
- ``capacity_factor.py``
- ``create_pommes_craft_model.py``

The installed-capacity data retrieved here are used downstream to:

- aggregate available capacity by plant type
- normalize unavailability into hourly availability shares
- normalize observed generation into hourly capacity factors
- parameterize maximum installed capacities in the SupplyForge-to-POMMES
  integration layer

The module therefore provides a core structural supply dataset for SupplyForge
before transformation into POMMES-compatible parameters.
"""

import logging
import os

import pandas as pd
import polars as pl
from dotenv import load_dotenv
from entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError

from supplyforge import RESULTS_DIR
from supplyforge.fetch.utils_uk import UK_ENTSOE_CUTOFF_YEAR, ENTSOE_COUNTRY_CODE_FOR_UK

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

COUNTRY_TO_ENTSOE_CODE = {
    "UK": ENTSOE_COUNTRY_CODE_FOR_UK,
}


def fetch_and_save_installed_capacity(country_code: str, year: int) -> None:
    """
    Fetches installed generation capacity from ENTSO-E for a given country and year,
    converts it to a Polars DataFrame, and saves it as a Parquet file.

    An ENTSO-E API token must be set in the `ENTSOE_API_TOKEN` environment variable.
    For ``country_code="UK"`` and ``year >= UK_ENTSOE_CUTOFF_YEAR``, delegates to the
    DUKES-based ``fetch_and_save_installed_capacity_uk`` instead.

    Args:
        country_code: The two-letter country code (e.g., 'DE', 'FR').
        year: The year for which to fetch the data.
    """
    if country_code == "UK" and int(year) >= UK_ENTSOE_CUTOFF_YEAR:
        from supplyforge.fetch.installed_capacity_uk import fetch_and_save_installed_capacity_uk
        fetch_and_save_installed_capacity_uk(country_code, year)
        return

    entsoe_code = COUNTRY_TO_ENTSOE_CODE.get(country_code, country_code)

    logger.info(f"Starting fetch for installed capacity for country: {country_code} (ENTSO-E code: {entsoe_code}), year: {year}")

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
    output_dir = RESULTS_DIR / "installed_capacities"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"installed_capacities_{country_code}_{year}.parquet"

    logger.info(f"Fetching installed capacity data for {country_code} for the year {year}...")
    try:
        # Fetch data from ENTSO-E (returns a pandas DataFrame)
        installed_capacity_df_pd = client.query_installed_generation_capacity(
            entsoe_code, start=start, end=end, psr_type=None
        )
        logger.info("Successfully fetched data from ENTSO-E.")

        if installed_capacity_df_pd.empty:
            logger.warning(f"No installed capacity data returned for {country_code} for {year}.")
            output_file.touch()
            return

        # Convert to Polars DataFrame and reset index to make timestamp a column
        installed_capacity_df_pl = pl.from_pandas(installed_capacity_df_pd.reset_index())

        # Save the DataFrame
        installed_capacity_df_pl.write_parquet(output_file)
        logger.info(f"Data saved to {output_file}")
    except NoMatchingDataError:
        output_file.touch()
    except Exception as e:
        logger.error(f"An error occurred while fetching data for {country_code} ({year}): {e}", exc_info=True)
        raise

if __name__ == "__main__":
    try:
        fetch_and_save_installed_capacity(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        logger.error("This script is intended to be run via Snakemake's 'script' directive.")