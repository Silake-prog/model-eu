"""
Ember Europe Electricity Interconnection NTC data module.

Downloads and caches the Ember reference net transfer capacities dataset,
then extracts a per-country static NTC table from Interconnectors/REF_NTC.csv.

Data source
-----------
URL: https://files.ember-energy.org/public-downloads/europe_interconnection_data_tool/europe_interconnection_data.zip
File inside zip: Interconnectors/REF_NTC.csv

Schema of REF_NTC.csv:
    Border  From  To  Year  NTC_F  NTC_B

NTC_F is the forward capacity (From→To) and NTC_B is the backward capacity (To→From).

Output (per-country)
--------------------
Wide-format DataFrame with one row and one column per connected country.
Column names are 2-letter country codes; values are the interconnection capacity in MW
(max of the two directional NTC values for the shared border).
"""

import logging
import urllib.request
import zipfile

import polars as pl

from supplyforge import RESULTS_DIR

logger = logging.getLogger(__name__)

EMBER_NTC_URL = (
    "https://files.ember-energy.org/public-downloads/"
    "europe_interconnection_data_tool/europe_interconnection_data.zip"
)
_EMBER_NTC_CSV_IN_ZIP = "Interconnectors/REF_NTC.csv"
_CACHE_DIR = RESULTS_DIR / "ember_ntc"
_CACHE_CSV = _CACHE_DIR / "REF_NTC.csv"


def _get_ember_ntc_raw() -> pl.DataFrame:
    """Downloads (once) and returns the full Ember REF_NTC table."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not _CACHE_CSV.is_file():
        zip_path = _CACHE_DIR / "ember_ntc.zip"
        logger.info(f"Downloading Ember NTC dataset from {EMBER_NTC_URL} ...")
        urllib.request.urlretrieve(EMBER_NTC_URL, zip_path)
        with zipfile.ZipFile(zip_path, "r") as z:
            with z.open(_EMBER_NTC_CSV_IN_ZIP) as src:
                _CACHE_CSV.write_bytes(src.read())
        zip_path.unlink(missing_ok=True)
        logger.info(f"Ember NTC CSV cached at {_CACHE_CSV}")

    return pl.read_csv(_CACHE_CSV, infer_schema_length=1000)


def get_ntc_for_country(country_code: str) -> pl.DataFrame:
    """
    Returns a wide-format DataFrame of NTC values for a given country.

    Each column is a connected country code; the single row holds the
    interconnection capacity in MW (max of both directional NTC values).

    Args:
        country_code: 2-letter country code (e.g. 'FR', 'DE').

    Returns:
        pl.DataFrame with one row and one column per connected country,
        or an empty DataFrame if no borders are found.
    """
    raw = _get_ember_ntc_raw()

    # Borders where country is the origin: NTC_F = country→neighbor
    from_rows = (
        raw.filter(pl.col("From") == country_code)
        .select(pl.col("To").alias("neighbor"), pl.col("NTC_F").alias("capacity").cast(pl.Float64))
    )
    # Borders where country is the destination: NTC_B = country→neighbor
    to_rows = (
        raw.filter(pl.col("To") == country_code)
        .select(pl.col("From").alias("neighbor"), pl.col("NTC_B").alias("capacity").cast(pl.Float64))
    )

    all_borders = pl.concat([from_rows, to_rows])
    if all_borders.is_empty():
        return pl.DataFrame()

    # Keep max capacity per neighbor (handles asymmetric entries)
    ntc_by_neighbor = (
        all_borders
        .group_by("neighbor")
        .agg(pl.col("capacity").max())
        .sort("neighbor")
    )

    return pl.DataFrame(
        {row["neighbor"]: [float(row["capacity"])] for row in ntc_by_neighbor.iter_rows(named=True)}
    )
