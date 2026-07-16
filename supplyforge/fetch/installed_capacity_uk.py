"""
UK installed generation capacity fetch module (Elexon BMRS IGCA).

Produces a Parquet file schema-compatible with the ENTSO-E installed-capacity
output from ``installed_capacity.py``: one row indexed by year, one column per
technology (MW values).

Data source
-----------
- Provider: Elexon BMRS ``/datasets/IGCA`` (Installed Generation Capacity Aggregated)
- Public API, no authentication required.
- Published annually, typically in December of year Y-1 for year Y.
- Falls back to the static ``UK_INSTALLED_CAPACITY_MW`` table in ``utils_uk.py``
  if the API returns no data for the requested year.

Wind note
---------
Elexon FUELINST reports combined wind (onshore + offshore) as a single "WIND"
series.  To keep the capacity-factor calculation consistent, the "Wind Onshore"
column in the installed-capacity output aggregates both onshore and offshore
installed capacity from IGCA.  "Wind Offshore" is therefore absent from the
UK output.

API reference
-------------
``GET https://data.elexon.co.uk/bmrs/api/v1/datasets/IGCA``

Parameters:

- ``publishDateTimeFrom`` / ``publishDateTimeTo`` — publish-date window
  (``YYYY-MM-DD`` format); a 2-year window is used to ensure the annual
  publication (typically released in December of the prior year) is captured.

Response: ``{"data": [{dataset, documentId, documentRevisionNumber,
publishTime, businessType, psrType, year, quantity}]}``

Outputs
-------
``RESULTS_DIR / "installed_capacities" / "installed_capacities_UK_{year}.parquet"``

Schema:

- ``index``  : int (the year)
- one column per technology (float, MW)
"""

import logging

import polars as pl
from elexon_bmrs import BMRSClient
from elexon_bmrs.exceptions import APIError

from supplyforge import RESULTS_DIR
from supplyforge.fetch.utils_uk import UK_INSTALLED_CAPACITY_MW

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _fetch_igca_from_api(year: int) -> dict[str, float] | None:
    """
    Query the Elexon BMRS IGCA endpoint for installed capacity for a given year.

    Searches a 2-year publish window (year-1 to year+1) to capture the annual
    IGCA publication (typically released in December of year-1).  Returns the
    latest-revision record per psrType, with Wind Offshore merged into
    Wind Onshore and "Other renewable" merged into "Other".

    Args:
        year: Calendar year to fetch.

    Returns:
        Dict mapping ENTSO-E technology name → installed capacity (MW), or
        ``None`` if no records for *year* are found.
    """
    client = BMRSClient()
    params = {
        "publishDateTimeFrom": f"{year - 1}-01-01",
        "publishDateTimeTo":   f"{year + 1}-01-01",
    }
    try:
        raw = client._make_request("GET", "/datasets/IGCA", params=params)
    except APIError as exc:
        raise exc

    if isinstance(raw, dict):
        records = raw.get("data", [])
    elif isinstance(raw, list):
        records = raw
    else:
        records = []

    year_records = [r for r in records if r.get("year") == (year + 1)]
    if not year_records:
        return None

    # Keep the latest revision per psrType (compare publishTime then revisionNumber)
    latest: dict[str, dict] = {}
    for rec in year_records:
        psr = rec.get("psrType", "")
        if not psr:
            continue
        existing = latest.get(psr)
        if existing is None or (
            rec.get("publishTime", ""),
            rec.get("documentRevisionNumber", 0),
        ) > (
            existing.get("publishTime", ""),
            existing.get("documentRevisionNumber", 0),
        ):
            latest[psr] = rec

    capacity: dict[str, float] = {
        psr: float(rec.get("quantity") or 0.0)
        for psr, rec in latest.items()
    }

    # Merge Wind Offshore into Wind Onshore — FUELINST reports combined wind
    wind_onshore  = capacity.pop("Wind Onshore",  0.0)
    wind_offshore = capacity.pop("Wind Offshore", 0.0)
    capacity["Wind Onshore"] = wind_onshore + wind_offshore

    # Merge "Other renewable" into "Other" — no distinct downstream column
    other_renewable = capacity.pop("Other renewable", 0.0)
    capacity["Other"] = capacity.get("Other", 0.0) + other_renewable

    return capacity


def fetch_and_save_installed_capacity_uk(country_code: str, year: int) -> None:
    """
    Fetch UK installed generation capacity from the Elexon BMRS IGCA API and
    save as Parquet.  Falls back to the static DUKES lookup if the API returns
    no data for the requested year.

    Args:
        country_code: Should be ``"UK"``.
        year: Calendar year to fetch.
    """
    output_dir = RESULTS_DIR / "installed_capacities"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"installed_capacities_{country_code}_{year}.parquet"

    capacity: dict[str, float] | None = None

    try:
        capacity = _fetch_igca_from_api(year)
        if capacity is not None:
            logger.info("Fetched UK %d installed capacity from Elexon IGCA API.", year)
    except APIError as exc:
        logger.warning(
            "IGCA API request failed for UK %d: %s. Falling back to static DUKES data.",
            year, exc,
        )

    if capacity is None:
        capacity = UK_INSTALLED_CAPACITY_MW.get(year)
        if capacity is not None:
            logger.info("Using static DUKES installed capacity for UK %d.", year)

    if capacity is None:
        logger.warning(
            "No installed capacity data for UK %d (API returned no data and no static "
            "entry found). Update UK_INSTALLED_CAPACITY_MW in utils_uk.py after the "
            "next DUKES release.",
            year,
        )
        output_file.touch()
        return

    row: dict = {"index": year, **capacity}
    df = pl.DataFrame([row])
    df.write_parquet(output_file)
    logger.info("UK installed capacity saved to %s (year=%d)", output_file, year)


if __name__ == "__main__":
    try:
        fetch_and_save_installed_capacity_uk(
            country_code=snakemake.params.country,
            year=int(snakemake.params.year),
        )
    except NameError:
        fetch_and_save_installed_capacity_uk(
            country_code="UK",
            year=2023,
        )
        logger.warning("This module is called from installed_capacity.py, not directly by Snakemake.")
