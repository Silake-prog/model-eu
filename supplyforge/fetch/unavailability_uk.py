"""
UK generation-unit unavailability fetch module — Elexon BMRS REMIT.

ENTSO-E publishes detailed unit-level outage events for its member countries.
The UK left ENTSO-E after Brexit; from 2020 onward, the equivalent source is the
Elexon BMRS ``/datasets/REMIT/stream`` endpoint, which carries REMIT-transparency
notices for GB generation assets.

The API is public and requires no authentication.  Each REMIT notice contains an
event start/end time, the unit's normal capacity, and the available capacity during
the event, matching the ENTSO-E outage-event schema expected by downstream
``availability.py``.

Strategy
--------
The ``/datasets/REMIT`` endpoint filters by *publish* date (not event date) and
enforces a maximum window of **1 day per request**.  Planned outages are often
published weeks or months in advance, so the query window starts three months before
the target year to capture pre-published maintenance notices.  Results are
de-duplicated by MRID (keeping the highest revision number) and then filtered to
events that overlap the target calendar year.

API reference
-------------
``GET https://data.elexon.co.uk/bmrs/api/v1/datasets/REMIT/stream``

Parameters:

- ``publishDateTimeFrom`` / ``publishDateTimeTo`` — publish-date window, format
  ``YYYY/MM/DD HH:MM``; maximum span of **1 day** per request.

Response: ``[{...}, ...]`` — array of REMIT message objects; no pagination.

Outputs
-------
``RESULTS_DIR / "unavailability" / "unavailability_UK_{year}.parquet"``

Schema:

- ``start``              : Datetime(us, UTC) — event start
- ``end``                : Datetime(us, UTC) — event end
- ``nominal_power``      : Float64           — unit normal capacity (MW)
- ``avail_qty``          : Float64           — available capacity during event (MW)
- ``docstatus``          : String            — Active / Inactive / Cancelled
- ``plant_type``         : String            — ENTSO-E fuel-type label (e.g. "Fossil Gas")
"""

import logging
from datetime import date, datetime, timedelta, timezone

import polars as pl
from elexon_bmrs import BMRSClient
from elexon_bmrs.exceptions import APIError

from supplyforge import RESULTS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Map Elexon REMIT eventStatus to ENTSO-E docstatus labels.
# "Dismissed" notices are erroneous publications; mapping to "Cancelled"
# causes availability.py to drop them (it filters out docstatus == "Cancelled").
_STATUS_MAP = {
    "Active":    "Active",
    "Inactive":  "Inactive",
    "Dismissed": "Cancelled",
}

_SCHEMA = {
    "start":         pl.Datetime(time_unit="us", time_zone="UTC"),
    "end":           pl.Datetime(time_unit="us", time_zone="UTC"),
    "nominal_power": pl.Float64,
    "avail_qty":     pl.Float64,
    "docstatus":     pl.String,
    "plant_type":    pl.String,
}

# Months of publish-date buffer before the target year start.
# Planned outages (e.g. nuclear refuelling) can be published months in advance.
_PRE_BUFFER_MONTHS = 3


def _publish_date_chunks(year: int):
    """
    Yield ``(date_from, date_to)`` string pairs for 1-day publish-date windows.

    The window spans ``_PRE_BUFFER_MONTHS`` months before the target year through
    the last day of the target year, inclusive.  Each chunk is exactly one day
    to comply with the Elexon API 1-day maximum range constraint.
    """
    start = date(year, 1, 1) - timedelta(days=_PRE_BUFFER_MONTHS * 30)
    end   = date(year, 12, 31)
    current = start
    while current <= end:
        yield current.strftime("%Y/%m/%d 00:00"), current.strftime("%Y/%m/%d 23:59")
        current += timedelta(days=1)


def fetch_and_save_unavailability_uk(country_code: str, year: int) -> None:
    """
    Fetch UK generation-unit unavailability from Elexon BMRS REMIT and save as Parquet.

    Queries ``/datasets/REMIT/stream`` in daily publish-date windows spanning three
    months before *year* through the end of *year*.  De-duplicates by keeping the
    highest-revision REMIT notice per MRID, retains only events that overlap the
    target calendar year, and normalises field names to the ENTSO-E schema consumed
    by downstream ``availability.py``.

    On HTTP errors for individual windows the chunk is skipped with a warning.  If
    no records are retrieved at all, an empty Parquet with the correct schema is
    written so that downstream rules do not fail.

    Args:
        country_code: Should be ``"UK"``.
        year: Calendar year to fetch.
    """
    output_dir = RESULTS_DIR / "unavailability"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"unavailability_{country_code}_{year}.parquet"

    # Use _make_request directly: the typed RemitMessage model marks assetType /
    # affectedArea as required but the real API sometimes omits them, so the
    # library's Pydantic parsing fails and returns raw dicts for those chunks.
    # Working with raw dicts throughout avoids the mixed-type list problem.
    client = BMRSClient()

    all_records: list[dict] = []
    chunks = list(_publish_date_chunks(year))
    for i, (date_from, date_to) in enumerate(chunks, 1):
        if i % 30 == 1:
            logger.info("Fetching REMIT chunk %d/%d  (%s)", i, len(chunks), date_from)
        try:
            raw = client._make_request(
                "GET",
                "/datasets/REMIT/stream",
                params={"publishDateTimeFrom": date_from, "publishDateTimeTo": date_to},
            )
            if isinstance(raw, list):
                all_records.extend(raw)
        except APIError as exc:
            logger.warning("REMIT request failed for %s: %s", date_from, exc)

    if not all_records:
        logger.warning("No REMIT data returned for UK %d; writing empty file.", year)
        pl.DataFrame(schema=_SCHEMA).write_parquet(output_file)
        return

    logger.info("Collected %d raw REMIT records; de-duplicating…", len(all_records))

    # Keep only the latest revision of each MRID
    latest: dict[str, dict] = {}
    for rec in all_records:
        mrid = rec.get("mrid", "")
        revision = rec.get("revisionNumber", 0)
        if mrid and (mrid not in latest or revision > latest[mrid].get("revisionNumber", 0)):
            latest[mrid] = rec

    year_start = datetime(year,     1, 1, tzinfo=timezone.utc)
    year_end   = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

    records = []
    for rec in latest.values():
        start_raw = rec.get("eventStartTime")
        end_raw   = rec.get("eventEndTime")
        if not start_raw or not end_raw:
            continue

        start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        end_dt   = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))

        # Keep only events that overlap the target year
        if start_dt >= year_end or end_dt <= year_start:
            continue

        records.append({
            "start":         start_dt,
            "end":           end_dt,
            "nominal_power": float(rec.get("normalCapacity") or 0.0),
            "avail_qty":     float(rec.get("availableCapacity") or 0.0),
            "docstatus":     _STATUS_MAP.get(rec.get("eventStatus", ""), rec.get("eventStatus", "")),
            "plant_type":    rec.get("fuelType", ""),
        })

    df = pl.DataFrame(records, schema=_SCHEMA) if records else pl.DataFrame(schema=_SCHEMA)

    logger.info("Writing %d REMIT events for UK %d to %s", len(df), year, output_file)
    df.write_parquet(output_file)


if __name__ == "__main__":
    try:
        fetch_and_save_unavailability_uk(
            country_code=snakemake.params.country,
            year=int(snakemake.params.year),
        )
    except NameError:
        fetch_and_save_unavailability_uk("UK", 2023)
        logger.warning("This module is called from unavailability.py, not directly by Snakemake.")
