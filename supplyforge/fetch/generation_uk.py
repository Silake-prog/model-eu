"""
UK actual generation fetch module (Elexon EIS source).

Fetches half-hourly / instantaneous generation by fuel type from the Elexon
Insights Solution (EIS) FUELINST dataset and writes a Parquet file that is
schema-compatible with the ENTSO-E generation output produced by
``generation.py``.

Data source
-----------
- Provider: Elexon Insights Solution (EIS) REST API
- Endpoint: ``GET /datasets/FUELINST``
- Authentication: none (open API)
- Resolution: 5-minute instantaneous values, resampled to 1-hour means
- Coverage: 2016 onward; used here for 2020+

Column naming convention
------------------------
Output columns follow the ENTSO-E MultiIndex-as-string convention used by
the rest of the pipeline:

- timestamp column : ``"('index', '')"``
- generation columns: ``"('FuelType', 'Actual Aggregated')"``

This ensures downstream modules (``capacity_factor.py``, ``hydro_inflow.py``)
can process UK data without modification.

Fuel-type mapping
-----------------
Elexon FUELINST fuel types are mapped to ENTSO-E technology names via
``UK_FUEL_TO_ENTSOE`` in ``utils_uk.py``.  CCGT and OCGT are summed into
"Fossil Gas".  WIND covers combined onshore + offshore (Elexon does not
separate them).  Interconnector flows are excluded.

Outputs
-------
``RESULTS_DIR / "generation" / "generation_UK_{year}.parquet"``
"""

import logging

import pandas as pd
import polars as pl
import requests
from tqdm import tqdm

from supplyforge import RESULTS_DIR
from supplyforge.fetch.utils_uk import (
    ELEXON_BASE_URL,
    ELEXON_INTERCONNECTOR_FUELS,
    UK_FUEL_TO_ENTSOE,
    elexon_date_chunks,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _fetch_fuelinst_chunk(date_from: str, date_to: str) -> list[dict]:
    """Fetch Elexon FUELINST records for a date window (max 7 days)."""
    resp = requests.get(
        f"{ELEXON_BASE_URL}/datasets/FUELINST",
        params={
            "settlementDateFrom": date_from,
            "settlementDateTo":   date_to,
            "format": "json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_and_save_generation_uk(country_code: str, year: int) -> None:
    """
    Fetch UK actual generation from Elexon EIS FUELINST for a full year,
    convert to ENTSO-E column naming convention, and save as Parquet.

    Args:
        country_code: Should be ``"UK"``.
        year: Calendar year to fetch.
    """
    output_dir = RESULTS_DIR / "generation"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"generation_{country_code}_{year}.parquet"

    logger.info(f"Fetching UK generation (Elexon FUELINST) for {year}")

    all_records: list[dict] = []
    for date_from, date_to in tqdm(elexon_date_chunks(year)):
        try:
            records = _fetch_fuelinst_chunk(date_from, date_to)
            all_records.extend(records)
        except Exception as exc:
            logger.warning(f"  FUELINST {date_from}→{date_to}: fetch failed — {exc}")

    logger.info(f"  Total records fetched: {len(all_records)}")

    if not all_records:
        logger.warning(f"No FUELINST data fetched for {year}. Writing empty file.")
        output_file.touch()
        return

    df = pd.DataFrame(all_records)

    # Drop interconnector flows; keep only mappable generation fuel types
    known_fuels = set(UK_FUEL_TO_ENTSOE.keys())
    df = df[
        ~df["fuelType"].isin(ELEXON_INTERCONNECTOR_FUELS)
        & df["fuelType"].isin(known_fuels)
    ].copy()

    if df.empty:
        logger.warning(f"All FUELINST records filtered out for {year}. Writing empty file.")
        output_file.touch()
        return

    # Map to ENTSO-E technology names
    df["entsoe_fuel"] = df["fuelType"].map(UK_FUEL_TO_ENTSOE)

    # Parse timestamps; truncate to the hour (FUELINST is 5-minute resolution)
    df["timestamp"] = pd.to_datetime(df["startTime"], utc=True).dt.floor("h")

    # Aggregate: sum across fuel types that share an ENTSO-E name (e.g. CCGT+OCGT)
    # then mean across the 5-min intervals within each hour
    hourly = (
        df.groupby(["timestamp", "entsoe_fuel"], as_index=False)["generation"]
        .mean()
    )

    # Pivot to wide format: one column per ENTSO-E fuel type
    wide = hourly.pivot(index="timestamp", columns="entsoe_fuel", values="generation")
    wide.columns.name = None
    wide = wide.reset_index()

    # Rename to ENTSO-E tuple-string column convention expected by downstream modules
    rename_map: dict[str, str] = {"timestamp": "('index', '')"}
    for col in wide.columns:
        if col != "timestamp":
            rename_map[col] = f"('{col}', 'Actual Aggregated')"
    wide = wide.rename(columns=rename_map)

    result = pl.from_pandas(wide)
    result.write_parquet(output_file)
    logger.info(
        f"UK generation saved to {output_file} "
        f"({result.shape[0]} rows × {result.shape[1]} columns)"
    )


if __name__ == "__main__":
    try:
        fetch_and_save_generation_uk(
            country_code=snakemake.params.country,
            year=int(snakemake.params.year),
        )
    except NameError:
        logger.error("This module is called from generation.py, not directly by Snakemake.")
