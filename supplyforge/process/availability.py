r"""
Hourly availability computation from generation-unit unavailability events.

Role in SupplyForge pipeline
----------------------------

This module converts event-based unavailability data into hourly availability
profiles per plant type, expressed as a share of installed capacity.

It belongs to the category of:

    *supply data transformation and temporal aggregation*

It is a critical transformation layer that bridges:

- raw ENTSO-E outage events (``unavailability.py``)
- installed capacities (``installed_capacity.py``)

into:

- hourly availability constraints used directly in POMMES.

Data sources
------------

Inputs are local Parquet datasets produced upstream:

1. Unavailability events:

   - Source: ``unavailability.py``
   - Path:
     ``RESULTS_DIR / "unavailability" / f"unavailability_<country>_<year>.parquet"``

   Expected columns include:

   - ``start`` (datetime)
   - ``end`` (datetime)
   - ``nominal_power``
   - ``avail_qty``
   - ``docstatus``
   - ``plant_type``

2. Installed capacities:

   - Source: ``installed_capacity.py``
   - Path:
     ``RESULTS_DIR / "installed_capacities" / f"installed_capacities_<country>_<year>.parquet"``

   Wide format with one column per plant type.

Inputs
------

- ``country_code``: two-letter country code
- ``year``: target calendar year

Outputs
-------

One Parquet file per country and year:

- Path:
  ``RESULTS_DIR / "availability" / f"availability_<country>_<year>.parquet"``

Schema:

- ``hour``: datetime (UTC)
- ``plant_type``: string
- ``total_unavailable_capacity``: float
- ``installed_capacity``: float
- ``availability_share``: float

If inputs are missing or empty, an empty dataset with this schema is written.

Algorithmic description
-----------------------

The computation proceeds in four main stages:

1. Installed capacity aggregation

   - Convert wide-format capacity table to long format
   - Aggregate total installed capacity per ``plant_type``

   Result:
   ``C_inst_p`` for each plant type p

2. Unavailability preprocessing

   - Convert ``start`` and ``end`` timestamps to UTC
   - Compute unavailable capacity per event:

     ``unavailable_capacity = nominal_power - avail_qty``

   - Filter:
     - strictly positive unavailable capacity
     - exclude events with ``docstatus == "Cancelled"``

3. Temporal expansion and aggregation

   - For each event, generate the list of hourly timestamps it covers
   - Expand events into hourly rows (event → hours)
   - Aggregate by:

     - ``hour``
     - ``plant_type``

   Result:

   ``U_p,t = sum of unavailable capacity for plant type p at hour t``

4. Availability computation

   - Build full grid:

     all hours × all plant types

   - Left join:

     - hourly unavailable capacity
     - installed capacity

   - Fill missing unavailable capacity with 0

   - Compute availability:

     ``availability_share = 1 - (U_p,t / C_inst_p)``

   - Filter plant types with zero installed capacity

Mathematical formulation
------------------------

Unavailable capacity:

.. math::

    U_{p,t} = \sum_{e \in \mathcal{E}_{p,t}} \left(P^{nom}_{e} - P^{avail}_{e}\right)

Installed capacity:

.. math::

    C^{inst}_{p} = \sum_{i \in \mathcal{I}_{p}} C^{inst}_{i}

Availability share:

.. math::

    a_{p,t} = 1 - \frac{U_{p,t}}{C^{inst}_{p}}

for:

.. math::
    C^{inst}_{p} > 0

Temporal handling
-----------------

- The time axis is constructed as a complete hourly UTC range:

  ``year-01-01 00:00`` → ``year-12-31 23:59:59``

- Unavailability events are expanded into hourly intervals using
  ``pl.datetime_range``

- Output is a dense hourly panel (no missing timestamps)

Units
-----

- Capacities are assumed consistent with ENTSO-E:

  - typically MW for ``nominal_power`` and ``avail_qty``

- Availability is dimensionless:

  - range theoretically [0, 1]

Methodological assumptions
--------------------------

Explicit assumptions:

- unavailability is additive across units within a plant type
- installed capacity is constant over the year
- availability can be derived as a simple ratio of unavailable capacity
- event expansion to hourly resolution is sufficient (no sub-hour modeling)

Implicit assumptions:

- plant_type classification is consistent between:

  - installed capacity dataset
  - unavailability dataset

- ENTSO-E event timestamps correctly represent physical outages
- no overlap inconsistencies exist between events

Edge cases handling
-------------------

If:

- input files are missing
- or datasets are empty

Then:

- an empty DataFrame with the correct schema is written
- computation is skipped

This ensures downstream compatibility.

Integration in pipeline
-----------------------

Upstream modules:

- ``unavailability.py``
- ``installed_capacity.py``

Downstream module:

- ``create_pommes_craft_model.py``

In the model:

- ``availability_share`` is:

  - renamed to ``availability``
  - clipped to [0,1]
  - used as a constraint on dispatchable technologies

Thus:

    availability → operational constraint on generation capacity

"""

import logging

import polars as pl
from supplyforge import RESULTS_DIR

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def calculate_availability(country_code: str, year: int) -> None:
    """
    Calculates hourly availability per plant type as a share of installed capacity.

    Args:
        country_code: The two-letter country code.
        year: The year for which to process the data.
    """
    logger.info(f"Calculating availability for {country_code} for the year {year}...")

    # Define paths
    unavailability_file = RESULTS_DIR / "unavailability" / f"unavailability_{country_code}_{year}.parquet"
    installed_capacity_file = (
        RESULTS_DIR / "installed_capacities" / f"installed_capacities_{country_code}_{year}.parquet"
    )
    output_dir = RESULTS_DIR / "availability"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"availability_{country_code}_{year}.parquet"

    # Load data
    missing_input_data = False
    try:
        unavailability_df = pl.read_parquet(unavailability_file)
        installed_capacity_df = pl.read_parquet(installed_capacity_file)
    except:
        missing_input_data = True


    if missing_input_data or installed_capacity_df.is_empty() or unavailability_df.is_empty():
        logger.warning(f"Unavailability data for {country_code} {year} is empty. Skipping calculation.")
        # Create an empty dataframe with the correct schema and save it
        (
            pl.DataFrame(schema={
                "hour": pl.Datetime(time_unit='us', time_zone='UTC'),
                "plant_type": pl.String,
                "total_unavailable_capacity": pl.Float64,
                "installed_capacity": pl.Float64,
                "availability_share": pl.Float64
            })
            .write_parquet(output_file)
        )
        logger.info(f"Empty availability data frame saved to {output_file}")
        return

    # 1. Process Installed Capacity Data
    # Melt to long format. The column names from installed capacity become 'plant_type'.
    value_vars = [col for col in installed_capacity_df.columns if col != "index"]
    installed_capacity_long = (
        installed_capacity_df.unpivot(index="index", on=value_vars, variable_name="plant_type", value_name="installed_capacity")
        .group_by("plant_type")
        .agg(pl.sum("installed_capacity"))
    )

    # 2. Process Unavailability Data
    # Ensure datetime columns are in UTC
    unavailability_df = unavailability_df.with_columns(
        pl.col("start").dt.convert_time_zone("UTC"),
        pl.col("end").dt.convert_time_zone("UTC"),
    )

    # Calculate unavailable capacity for each event
    unavailability_df = unavailability_df.with_columns(
        (pl.col("nominal_power") - pl.col("avail_qty").cast(pl.Float64)).alias("unavailable_capacity")
    ).filter((pl.col("unavailable_capacity") > 0)
             & ((pl.col("docstatus").is_null()) | (pl.col("docstatus") != "Cancelled"))
             )

    # Create a complete hourly timeseries for the year in UTC
    hourly_range = pl.datetime_range(
        start=pl.datetime(year, 1, 1),
        end=pl.datetime(year, 12, 31, 23, 59, 59),
        interval="1h",
        time_zone="UTC",
        eager=True,
    ).alias("hour")

    # 3. Calculate total unavailable capacity per hour and plant type
    # This is more memory-efficient than creating a large grid first.
    # It expands each unavailability event into the hours it covers.
    total_unavailable_by_hour = (
        unavailability_df.lazy()
        .with_columns(
            # Group start/end to apply the function row-wise, creating a list of hours for each event.
            pl.struct(["start", "end"])
            .map_elements(lambda r: pl.datetime_range(r["start"], r["end"], "1h", time_zone="UTC", eager=True).to_list(), return_dtype=pl.List(pl.Datetime(time_zone="UTC")))
            .alias("hour")
        )
        .explode("hour") # Create a row for each hour in the list
        .group_by(["hour", "plant_type"])
        .agg(pl.sum("unavailable_capacity").alias("total_unavailable_capacity"))
        .collect()
    )

    # 4. Combine and Calculate Availability Share
    # Create a full grid of all hours and plant types, then join the results.
    grid = hourly_range.to_frame().join(installed_capacity_long.select("plant_type"), how="cross")
    availability_df = (
        grid.join(total_unavailable_by_hour, on=["hour", "plant_type"], how="left")
        .join(installed_capacity_long, on="plant_type", how="left")
        .with_columns(pl.col("total_unavailable_capacity").fill_null(0.0))
        .with_columns(
            (1 - (pl.col("total_unavailable_capacity") / pl.col("installed_capacity"))).alias("availability_share")
        )
        .filter(pl.col("installed_capacity") > 0)  # Avoid division by zero for types with no capacity
    )

    # Save result
    availability_df.write_parquet(output_file)
    logger.info(f"Availability data saved to {output_file}")


if __name__ == "__main__":
    try:
        calculate_availability(country_code=snakemake.params.country, year=int(snakemake.params.year))
    except NameError:
        calculate_availability(country_code="UK", year=2023)
        logger.error("This script is intended to be run via Snakemake's 'script' directive.")