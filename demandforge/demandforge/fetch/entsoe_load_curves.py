
import logging
import polars as pl
from entsoe import EntsoePandasClient
from dotenv import load_dotenv
from entsoe.exceptions import NoMatchingDataError

load_dotenv()
import os
from pathlib import Path
import pandas as pd
from demandforge import RESULTS_DIR


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def fetch_entsoe_load(country_code: str, year: int) -> pl.DataFrame:
    """Fetches ENTSO-E load data for a specific country and year.

    Args:
        country_code (str): Two-letter country code (e.g., 'DE' for Germany, 'FR' for France).
        year (int): Year for which to fetch data.

    Returns:
        pl.DataFrame: Polars DataFrame with timestamp and load data.
    """
    logger.info(f"Starting fetch for country: {country_code}, year: {year}")

    # Load environment variables from .env file
    load_dotenv()
    api_key = os.getenv('ENTSOE_API_KEY')

    if not api_key:
        logger.error("ENTSOE_API_KEY not found in environment variables")
        raise ValueError("ENTSOE_API_KEY not found in environment variables")

    logger.debug("API key loaded successfully")

    # Initialize the client
    try:
        client = EntsoePandasClient(api_key=api_key)
        logger.debug("ENTSO-E client initialized")
    except Exception as e:
        logger.error(f"Failed to initialize ENTSO-E client: {e}")
        raise

    # Define the date range for the year with UTC timezone
    start = pd.Timestamp(f'{year}-01-01', tz='UTC')
    end = pd.Timestamp(f'{year + 1}-01-01', tz='UTC')


    logger.info(f"Fetching ENTSO-E load data for {country_code} from {start} to {end}")

    # Fetch the load data
    try:
        pandas_df = client.query_load(country_code, start=start, end=end)
        logger.info(f"Successfully fetched data from ENTSO-E API for {country_code}")
        logger.info(f"Converting DatetimeIndex to UTC timezone")
        pandas_df.index = pandas_df.index.tz_convert('UTC')
        logger.debug(f"Raw data shape: {pandas_df.shape}")
    except NoMatchingDataError:
        logger.warning(f"No data found for {country_code} in {year}, returning 0 MW load for all hours")
        idx = pd.date_range(start, end, freq='h', inclusive="left")
        pandas_df = pd.DataFrame(index=idx, data=[0] * len(idx), columns=['Actual Load'])
    except Exception as e:
        logger.error(f"Failed to fetch data from ENTSO-E API for {country_code}: {e}")
        raise

    pandas_df = pandas_df.reindex(pd.date_range(pandas_df.index.min(),
                                                pandas_df.index.max(), freq="h"))
    pandas_df = pandas_df.interpolate(method="time").ffill().bfill()
    # Convert to Polars DataFrame
    try:
        df = pl.from_pandas(pandas_df.reset_index())
        logger.debug(f"Converted to Polars DataFrame with {len(df)} rows")
    except Exception as e:
        logger.error(f"Failed to convert pandas DataFrame to Polars: {e}")
        raise

    # Rename columns for clarity
    original_columns = df.columns
    df = df.rename({df.columns[0]: "timestamp", df.columns[1]: "load_mw"})
    logger.debug(f"Renamed columns: {original_columns[0]} -> timestamp, {original_columns[1]} -> load_mw")

    logger.info(f"Successfully processed {len(df)} records for {country_code} in {year}")
    return df


def save_load_data_to_parquet(country_code: str, year: int):
    """Fetches ENTSO-E load data and saves it as a Parquet file.

    Args:
        country_code (str): Two-letter country code (e.g., 'DE' for Germany).
        year (int): Year for which to fetch data.
    """
    logger.info(f"Starting save operation for {country_code} {year}")

    # Create output directory if it doesn't exist
    output_folder = Path(RESULTS_DIR / "entsoe")

    try:
        output_folder.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Output directory ensured: {output_folder}")
    except Exception as e:
        logger.error(f"Failed to create output directory {output_folder}: {e}")
        raise

    # Fetch the data
    try:
        df = fetch_entsoe_load(country_code, year)
    except Exception as e:
        logger.error(f"Failed to fetch load data: {e}")
        raise

    # Define output filename
    output_file = output_folder / f"entsoe_load_{country_code}_{year}.parquet"
    logger.debug(f"Output file path: {output_file}")

    # Save as Parquet
    try:
        df.write_parquet(output_file)
        logger.info(f"Data saved successfully to {output_file}")
    except Exception as e:
        logger.error(f"Failed to write Parquet file {output_file}: {e}")
        raise

    record_count = len(df)
    logger.info(f"Total records saved: {record_count}")

    return output_file


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Example usage
    country_code = "PL"  # Germany
    year = 2023

    logger.info("=== Starting ENTSO-E data download ===")
    try:
        output_path = save_load_data_to_parquet(country_code, year)
        logger.info(f"=== Completed successfully. Output: {output_path} ===")
    except Exception as e:
        logger.error(f"=== Failed with error: {e} ===")
        raise