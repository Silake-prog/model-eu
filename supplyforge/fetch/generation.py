"""
Historical generation retrieval module.

Role in SupplyForge pipeline
----------------------------

This module retrieves historical actual electricity generation aggregated by
production type from the ENTSO-E Transparency Platform and stores the result as
a Parquet file in the SupplyForge results directory.

It belongs to the category of:

    *primary supply data fetch*

The module provides the historical generation time series used downstream to
derive technology-level supply indicators, in particular capacity factors for
intermittent technologies and hydrological reconstructions based on hydro
generation.

Data source
-----------

- Provider: ENTSO-E Transparency Platform
- Access library: ``EntsoePandasClient``
- API method used: ``query_generation``

The module queries one country and one year at a time and serializes the result
returned by ENTSO-E without technology-name normalization at this stage.

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

- ``RESULTS_DIR / "generation"``

with filename:

- ``generation_<country_code>_<year>.parquet``

The saved dataset is the ENTSO-E generation table converted to a Polars
DataFrame after index reset, so that the original time index becomes an
explicit column.

Algorithmic description
-----------------------

The module performs the following steps:

1. Load environment variables from ``.env``
2. Read the ENTSO-E API token from ``ENTSOE_API_TOKEN``
3. Initialize an ``EntsoePandasClient``
4. Define a UTC annual query window from:

   - ``<year>-01-01 00:00 UTC``
   - ``<year>-12-31 23:59 UTC``

5. Create the output directory ``RESULTS_DIR / "generation"``, if needed
6. Query ENTSO-E actual generation aggregated by production type
7. Convert the returned pandas object to a Polars DataFrame after resetting the
   index
8. Save the result as Parquet

If ENTSO-E returns ``NoMatchingDataError``, the module creates an empty file at
the target output path.

Temporal handling
-----------------

The query window is defined explicitly in UTC for the target calendar year.

Units
-----

The module does not redefine or convert physical units.

According to the code and the workflow description, the output preserves the
units returned by the ENTSO-E generation query.

Methodological assumptions
--------------------------

Code-visible assumptions include:

- the ENTSO-E generation query provides the relevant historical generation
  series for the requested country and year
- the raw technology labels returned by ENTSO-E can be preserved unchanged at
  this stage
- direct Parquet serialization of the fetched table is sufficient for downstream
  processing

Implicit assumptions include:

- the ENTSO-E annual extraction is complete enough to serve as a reference
  historical supply dataset
- downstream modules can interpret the returned technology categories without
  prior harmonization in this fetch step


Integration in pipeline
-----------------------

Downstream modules include:

- ``capacity_factor.py``
- ``hydro_inflow.py``

The historical generation time series retrieved here provide the basis for:

- computing historical capacity factors for intermittent technologies
- reconstructing hydrological inflows from hydro generation in downstream
  processing

The module therefore supplies a foundational historical generation dataset for
SupplyForge before transformation into POMMES-compatible parameters.
"""

import logging
import os

import pandas as pd
import polars as pl
from entsoe import EntsoePandasClient
from entsoe.exceptions import NoMatchingDataError

from supplyforge import RESULTS_DIR
from dotenv import load_dotenv
from supplyforge.fetch.utils_uk import UK_ENTSOE_CUTOFF_YEAR, ENTSOE_COUNTRY_CODE_FOR_UK

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Countries that are not in ENTSO-E by their ISO-3166 code and need remapping
COUNTRY_TO_ENTSOE_CODE = {
    "UK": ENTSOE_COUNTRY_CODE_FOR_UK,  # ENTSO-E uses "GB" for Great Britain
}



def fetch_and_save_generation(country_code: str, year: int) -> None:
    """
    Fetches actual generation per production type from ENTSO-E for a given country and year,
    converts it to a Polars DataFrame, and saves it as a Parquet file.

    An ENTSO-E API token must be set in the `ENTSOE_API_TOKEN` environment variable.
    For ``country_code="UK"`` and ``year >= UK_ENTSOE_CUTOFF_YEAR``, delegates to the
    Elexon-based ``fetch_and_save_generation_uk`` instead.

    Args:
        country_code: The two-letter country code (e.g., 'DE', 'FR').
        year: The year for which to fetch the data.
    """
    if country_code == "UK" and int(year) >= UK_ENTSOE_CUTOFF_YEAR:
        from supplyforge.fetch.generation_uk import fetch_and_save_generation_uk
        fetch_and_save_generation_uk(country_code, year)
        return

    # For UK pre-cutoff, use the ENTSO-E area code "GB"
    entsoe_code = COUNTRY_TO_ENTSOE_CODE.get(country_code, country_code)

    logger.info(f"Starting fetch for country: {country_code} (ENTSO-E code: {entsoe_code}), year: {year}")

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
    output_dir = RESULTS_DIR / "generation"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"generation_{country_code}_{year}.parquet"

    logger.info(f"Fetching generation data for {country_code} for the year {year}...")
    try:
        # Fetch data from ENTSO-E (returns a pandas DataFrame)
        generation_df_pd = client.query_generation(entsoe_code, start=start, end=end)
        logger.info("Successfully fetched data from ENTSO-E.")

        if generation_df_pd.empty:
            logger.warning(f"No generation data returned for {country_code} for {year}.")
            return

        # Convert to Polars DataFrame and reset index to make timestamp a column
        generation_df_pl = pl.from_pandas(generation_df_pd.reset_index())


        # Save the DataFrame
        generation_df_pl.write_parquet(output_file)
        logger.info(f"Data saved to {output_file}")

    except NoMatchingDataError:
        output_file.touch()
    except Exception as e:

        logger.error(f"An error occurred while fetching data for {country_code} ({year}): {e}", exc_info=True)
        raise

# This block is executed when the script is called directly by Snakemake's `script` directive.
# It accesses the 'snakemake' object that Snakemake automatically injects.
if __name__ == "__main__":
    try:
        # The 'snakemake' object is globally available in scripts run via the script directive
        fetch_and_save_generation(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        logger.warning("This script is intended to be run via Snakemake's 'script' directive.")
        fetch_and_save_generation(country_code="UK", year=2021)
