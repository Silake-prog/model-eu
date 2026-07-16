"""
UK day-ahead electricity price fetch module (Elexon EIS + ECB exchange rate).

Fetches N2EX day-ahead prices from the Elexon EIS Market Index Data (MID)
endpoint, converts them from GBP/MWh to EUR/MWh using daily exchange rates
from the Frankfurter API (ECB data), and writes a Parquet file that is
schema-compatible with the ENTSO-E day-ahead price output produced by
``day_ahead_prices.py``.

Data sources
------------
- **Prices (GBP/MWh)**: Elexon EIS ``GET /datasets/MID``, filter ``dataProvider
  == "APXMIDP"`` (APX / EPEX Spot UK day-ahead market for Great Britain).
  N2EX (Nord Pool UK, ``N2EXMIDP``) ceased operations after Brexit and returns
  zeros — ``APXMIDP`` is the active market.
- **Exchange rate (GBP → EUR)**: Frankfurter API ``GET
  /YYYY-MM-DD..YYYY-MM-DD?from=GBP&to=EUR`` (ECB reference rates, free, no
  authentication required).

Resolution
----------
MID data is published per settlement period (30-minute).  This module resamples
to hourly means to match the rest of the pipeline.

Outputs
-------
``RESULTS_DIR / "day_ahead_prices" / "day_ahead_prices_UK_{year}.parquet"``

Schema:

- ``timestamp``         : Datetime (UTC)
- ``price_eur_per_mwh`` : Float64
"""

import logging

import pandas as pd
import polars as pl
import requests

from supplyforge import RESULTS_DIR
from supplyforge.fetch.utils_uk import ELEXON_BASE_URL, elexon_date_chunks

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# APX (EPEX Spot UK) market index — the active GB day-ahead market post-Brexit.
# N2EX (Nord Pool UK, "N2EXMIDP") ceased operations after Brexit and returns zeros.
_APX_INDEX = "APXMIDP"


def _fetch_mid_chunk(date_from: str, date_to: str) -> list[dict]:
    """Fetch Elexon MID records for a date window (max 7 days).

    Note: the MID endpoint uses ``from`` / ``to`` query parameters,
    not ``settlementDateFrom`` / ``settlementDateTo``.
    """
    resp = requests.get(
        f"{ELEXON_BASE_URL}/datasets/MID",
        params={
            "from":   date_from,
            "to":     date_to,
            "format": "json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _get_gbp_eur_daily(year: int) -> pd.Series:
    """
    Return a daily Series of EUR-per-GBP exchange rates for the given year.

    Uses the Frankfurter API (backed by ECB reference rates).
    Missing days (weekends / holidays) are forward-filled then back-filled.

    Returns:
        pd.Series with a DatetimeIndex (UTC midnight) and float values
        representing how many EUR equal 1 GBP.
    """
    url = f"https://api.frankfurter.app/{year}-01-01..{year}-12-31"
    try:
        resp = requests.get(url, params={"from": "GBP", "to": "EUR"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        rates = {
            pd.Timestamp(date, tz="UTC"): values["EUR"]
            for date, values in data.get("rates", {}).items()
        }
        if not rates:
            raise ValueError("Empty rates response")
        series = pd.Series(rates).sort_index()
        full_index = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D", tz="UTC")
        return series.reindex(full_index).ffill().bfill()
    except Exception as exc:
        logger.warning(
            f"Could not fetch GBP/EUR exchange rate for {year}: {exc}. "
            "Falling back to annual average rate."
        )
        # Conservative fallback: approximate annual averages (EUR per GBP)
        fallback_rates = {2020: 1.125, 2021: 1.162, 2022: 1.176, 2023: 1.150, 2024: 1.180}
        rate = fallback_rates.get(year, 1.15)
        full_index = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D", tz="UTC")
        return pd.Series(rate, index=full_index)


def fetch_and_save_day_ahead_prices_uk(country_code: str, year: int) -> None:
    """
    Fetch UK day-ahead prices from Elexon EIS, convert GBP→EUR, and save as Parquet.

    Args:
        country_code: Should be ``"UK"``.
        year: Calendar year to fetch.
    """
    output_dir = RESULTS_DIR / "day_ahead_prices"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"day_ahead_prices_{country_code}_{year}.parquet"

    logger.info(f"Fetching UK day-ahead prices (Elexon MID APXMIDP) for {year}")

    all_records: list[dict] = []
    for date_from, date_to in elexon_date_chunks(year):
        try:
            records = _fetch_mid_chunk(date_from, date_to)
            all_records.extend(records)
        except Exception as exc:
            logger.warning(f"  MID {date_from}→{date_to}: fetch failed — {exc}")

    logger.info(f"  Total MID records fetched: {len(all_records)}")

    # Produce empty output on failure
    empty_schema = {
        "timestamp":         pl.Datetime(time_unit="us", time_zone="UTC"),
        "price_eur_per_mwh": pl.Float64,
    }
    if not all_records:
        logger.warning(f"No MID data fetched for {year}. Writing empty file.")
        pl.DataFrame(schema=empty_schema).write_parquet(output_file)
        return

    df = pd.DataFrame(all_records)

    # Keep only APX (EPEX Spot UK) day-ahead prices
    df = df[df["dataProvider"] == _APX_INDEX].copy()

    if df.empty:
        logger.warning(f"No {_APX_INDEX} records found for {year}. Writing empty file.")
        pl.DataFrame(schema=empty_schema).write_parquet(output_file)
        return

    # Parse settlement period start times and resample to hourly
    df["timestamp"] = pd.to_datetime(df["startTime"], utc=True).dt.floor("h")
    hourly = df.groupby("timestamp", as_index=False)["price"].mean()

    # Fetch daily GBP→EUR exchange rates
    gbp_eur = _get_gbp_eur_daily(year)

    # Align exchange rate to hourly timestamps via date key
    hourly["date"] = hourly["timestamp"].dt.normalize()
    rate_map = gbp_eur.rename("eur_per_gbp").reset_index().rename(columns={"index": "date"})
    hourly = hourly.merge(rate_map, on="date", how="left")
    hourly["eur_per_gbp"] = hourly["eur_per_gbp"].ffill().bfill()

    hourly["price_eur_per_mwh"] = hourly["price"] * hourly["eur_per_gbp"]

    result = pl.from_pandas(hourly[["timestamp", "price_eur_per_mwh"]])
    result.write_parquet(output_file)
    logger.info(
        f"UK day-ahead prices saved to {output_file} ({result.shape[0]} hourly rows)"
    )


if __name__ == "__main__":
    try:
        fetch_and_save_day_ahead_prices_uk(
            country_code=snakemake.params.country,
            year=int(snakemake.params.year),
        )
    except NameError:
        logger.error("This module is called from day_ahead_prices.py, not directly by Snakemake.")
