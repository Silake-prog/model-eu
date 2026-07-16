"""
Hydro storage level fetch module.

Role in SupplyForge pipeline
----------------------------

This module retrieves aggregate water reservoir and hydro storage levels from
the ENTSO-E Transparency Platform and stores them as a Parquet dataset in the
SupplyForge results directory.

It belongs to the category of:

    *hydrological state data fetch*

The module provides historical storage-level information used downstream to
reconstruct hydro inflows and parameterize reservoir hydro behavior in the
POMMES modeling framework.

Data source
-----------

The module queries ENTSO-E through:

- ``EntsoePandasClient``
- method: ``query_aggregate_water_reservoirs_and_hydro_storage``

An API token is required:

- environment variable: ``ENTSOE_API_TOKEN``

Inputs
------

Main function inputs:

- ``country_code``: two-letter country code (e.g. "FR", "DE")
- ``year``: target calendar year

Internal parameters:

- annual query window in UTC

No custom retry configuration is defined in the client initialization.

Outputs
-------

The module writes one Parquet file per country and year:

- directory:
  ``RESULTS_DIR / "hydro_storage"``
- filename:
  ``hydro_storage_<country>_<year>.parquet``

Output schema (code-visible):

- ``timestamp`` (datetime, UTC)
- ``storage_mwh`` (float)

If no data is returned, the module still writes a valid empty Parquet dataset
with that schema.

Algorithmic description
-----------------------

The module performs the following steps:

1. read country code and year
2. load ENTSO-E API token from environment
3. initialize ``EntsoePandasClient``
4. define annual UTC query window:

   - start: ``year-01-01 00:00 UTC``
   - end: ``year-12-31 23:59 UTC``

5. query aggregate water reservoirs and hydro storage from ENTSO-E
6. if data is returned:

   a. reset pandas index
   b. convert pandas → Polars DataFrame
   c. rename columns:

      - ``index`` → ``timestamp``
      - second data column → ``storage_mwh``

7. if no data is returned:

   - create empty Polars DataFrame with schema

8. create output directory if needed
9. write Parquet output

Temporal conventions
--------------------

- timestamps are handled in UTC
- full-year coverage:

  - start: ``pd.Timestamp(f"{year}-01-01", tz="UTC")``
  - end: ``pd.Timestamp(f"{year}-12-31 23:59", tz="UTC")``

- temporal resolution is determined by the ENTSO-E response
- in practice, this dataset is typically used as an aggregated hydro stock
  time series rather than a sub-hourly operational signal

Methodological assumptions
--------------------------

Explicit assumptions:

- ENTSO-E aggregate reservoir and hydro storage data represent national hydro
  storage state adequately
- yearly UTC query bounds are sufficient to capture the intended dataset
- the returned storage quantity can be interpreted as ``storage_mwh``

Implicit assumptions:

- the second non-index column of the returned pandas object always corresponds
  to the storage quantity of interest
- no additional disaggregation by hydro technology or reservoir class is needed
- no unit conversion beyond column renaming is required

Integration in pipeline
-----------------------

Direct upstream dependency:

- ENTSO-E Transparency Platform API

Downstream usage in SupplyForge:

- used by ``hydro_inflow.py``

Role in POMMES:

- indirect only
- storage levels are combined with hydro generation data in order to
  reconstruct inflows
- those reconstructed inflows are then used in
  ``create_pommes_craft_model.py`` to parameterize reservoir hydro

Thus, this module contributes to:

    hydrological state reconstruction for reservoir modeling

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

COUNTRY_TO_ENTSOE_CODE = {
    "UK": ENTSOE_COUNTRY_CODE_FOR_UK,
}


def fetch_and_save_hydro_storage(country_code: str, year: int) -> None:
    """
    Fetches aggregate water reservoirs and hydro storage from ENTSO-E for a given
    country and year, converts it to a Polars DataFrame, and saves it as a Parquet file.

    An ENTSO-E API token must be set in the `ENTSOE_API_TOKEN` environment variable.
    For ``country_code="UK"`` and ``year >= UK_ENTSOE_CUTOFF_YEAR``, delegates to the
    empty-file fallback ``fetch_and_save_hydro_storage_uk`` instead.

    Args:
        country_code: The two-letter country code (e.g., 'DE', 'FR').
        year: The year for which to fetch the data.
    """
    if country_code == "UK" and int(year) >= UK_ENTSOE_CUTOFF_YEAR:
        from supplyforge.fetch.hydro_storage_uk import fetch_and_save_hydro_storage_uk
        fetch_and_save_hydro_storage_uk(country_code, year)
        return

    entsoe_code = COUNTRY_TO_ENTSOE_CODE.get(country_code, country_code)

    logger.info(f"Starting fetch for hydro storage for country: {country_code} (ENTSO-E code: {entsoe_code}), year: {year}")

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

    logger.info(f"Fetching hydro storage data for {country_code} for the year {year}...")
    hydro_df_pl = None
    try:
        # Fetch data from ENTSO-E (returns a pandas DataFrame)
        hydro_df_pd = client.query_aggregate_water_reservoirs_and_hydro_storage(
            entsoe_code, start=start, end=end
        )

        if hydro_df_pd is not None and not hydro_df_pd.empty:
            logger.info("Successfully fetched data from ENTSO-E.")
            # Convert pandas Series to a DataFrame and reset the index
            hydro_df_pd = hydro_df_pd.reset_index()
            # Convert to Polars DataFrame and reset index to make timestamp a column
            hydro_df_pl = pl.from_pandas(hydro_df_pd).rename(
                {"index": "timestamp", str(hydro_df_pd.columns[1]): "storage_mwh"}
            )

    except exceptions.NoMatchingDataError:
        logger.warning(f"No hydro storage data found on ENTSO-E for {country_code} for {year}.")
    except Exception as e:
        logger.error(f"An error occurred while fetching data for {country_code} ({year}): {e}", exc_info=True)
        raise

    if hydro_df_pl is None:
        logger.info(f"Creating an empty parquet file for {country_code} for {year}.")
        hydro_df_pl = pl.DataFrame(
            schema={"timestamp": pl.Datetime(time_unit="us", time_zone="UTC"), "storage_mwh": pl.Float64}
        )

    # Define output path and ensure the directory exists
    output_dir = RESULTS_DIR / "hydro_storage"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"hydro_storage_{country_code}_{year}.parquet"

    # Save the DataFrame (either with data or empty)
    hydro_df_pl.write_parquet(output_file)
    logger.info(f"Data saved to {output_file}")

if __name__ == "__main__":
    try:
        fetch_and_save_hydro_storage(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        logger.error("This script is intended to be run via Snakemake's 'script' directive.")