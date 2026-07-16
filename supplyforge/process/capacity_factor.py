r"""
Hourly intermittent generation capacity factor calculation module.

Role in SupplyForge pipeline
----------------------------

This module computes hourly capacity factors for intermittent generation
technologies from historical generation data and installed capacity data.

It belongs to the category of:

    *supply-side transformation and harmonization*

The module transforms raw ENTSO-E-derived generation and installed capacity
datasets into normalized hourly availability profiles that can be used by
downstream modeling components.

Data sources
------------

This module does not query an external API directly.

It uses two upstream SupplyForge datasets stored in ``RESULTS_DIR``:

- generation dataset produced by ``generation.py``
- installed capacity dataset produced by ``installed_capacity.py``

Input files
-----------

Expected input files:

- ``RESULTS_DIR / "generation" / f"generation_<country>_<year>.parquet"``
- ``RESULTS_DIR / "installed_capacities" / f"installed_capacities_<country>_<year>.parquet"``

Main function inputs:

- ``country_code``: two-letter country code (e.g. "FR", "ES")
- ``year``: target calendar year

Purpose of the calculation
--------------------------

The module estimates, for each hour and for each selected intermittent
technology, the ratio between observed generation and installed capacity.

The resulting quantity is a historical capacity factor:

.. math::

    \mathrm{CF}_{p,t} =
    \frac{G_{p,t}}{C^{inst}_{p}}

where:

- ``p`` is the plant type
- ``t`` is the hour
- ``G_{p,t}`` is observed generation
- ``C^{inst}_{p}`` is total installed capacity for that plant type

This quantity is later used as an hourly availability profile in POMMES.

Technologies covered
--------------------

The module restricts the calculation to the following intermittent types:

- ``Solar``
- ``Wind Offshore``
- ``Wind Onshore``
- ``Hydro Run-of-river and poundage``

Other generation types present in the generation dataset are ignored.

Algorithmic description
-----------------------

The module performs the following steps:

1. construct input and output file paths
2. read generation and installed-capacity Parquet datasets
3. if one input file is missing:

   - log warning
   - create an empty marker file with ``touch()``
   - stop execution

4. clean generation column names:

   - detect tuple-like stringified column names
   - keep only columns containing ``'Actual Aggregated'``
   - extract plant type names
   - rename ``('index', ...)``-like column to ``index``

5. check whether generation or installed-capacity datasets are empty:

   - if yes, log warning
   - create empty marker output
   - stop execution

6. transform installed-capacity dataset:

   - unpivot wide table to long format
   - aggregate total installed capacity by ``plant_type``

7. transform generation dataset:

   - keep only selected intermittent technologies
   - unpivot to long format
   - obtain rows of the form:

     - ``hour``
     - ``plant_type``
     - ``generation_mw``

   - convert timestamps to UTC

8. join generation with installed capacity by ``plant_type``

9. compute hourly capacity factor:

   - if ``installed_capacity > 0``:

     .. math::

         \mathrm{capacity\_factor} =
         \frac{\mathrm{generation\_mw}}{\mathrm{installed\_capacity}}

   - otherwise:

     - set capacity factor to ``0.0``

10. discard rows without installed-capacity information

11. sort by ``plant_type`` and ``hour``

12. resample dynamically at 1-hour frequency by plant type using:

    - mean capacity factor
    - first installed capacity
    - mean generation

13. write final Parquet output

Output
------

The module writes one Parquet file per country and year:

- directory:
  ``RESULTS_DIR / "capacity_factors"``
- filename:
  ``capacity_factors_<country>_<year>.parquet``

Output schema (code-visible)
----------------------------

The output contains at least the following columns:

- ``plant_type``
- ``hour``
- ``capacity_factor``
- ``installed_capacity``
- ``generation_mw``

Notes:

- ``hour`` is timezone-aware and converted to UTC
- one row is expected per plant type and hour after grouping

Temporal conventions
--------------------

- the module works on hourly data
- timestamps are normalized to UTC through:

  - ``pl.col("hour").dt.convert_time_zone("UTC")``

- final grouping is explicitly hourly via:

  - ``group_by_dynamic(... every="1h", period="1h", group_by="plant_type")``

Formal definition
-----------------

Installed capacity is first aggregated by plant type:

.. math::

    C^{inst}_{p} = \sum_{i \in \mathcal{I}_{p}} C^{inst}_{i}

Hourly capacity factor is then computed as:

.. math::

    \mathrm{CF}_{p,t} =
    \begin{cases}
    \dfrac{G_{p,t}}{C^{inst}_{p}} & \text{if } C^{inst}_{p} > 0 \\\\
    0 & \text{otherwise}
    \end{cases}

where:

- :math:`G_{p,t}` is hourly generation for plant type ``p``
- :math:`C^{inst}_{p}` is the aggregated installed capacity for plant type ``p``

Methodological assumptions
--------------------------

Explicit assumptions:

- installed capacity can be aggregated nationally by plant type
- hourly generation and installed capacity are comparable at plant-type level
- intermittent technologies can be represented through historical capacity factors

Implicit assumptions:

- installed capacity is constant over the modeled year
- actual aggregated generation is the correct ENTSO-E field to use
- averaging within the 1-hour dynamic grouping does not materially alter hourly data
- negative or abnormal generation values are not explicitly corrected here

Therefore:

- capacity factors above 1 may remain in the output if generation exceeds
  installed capacity in the source data
- negative values are not explicitly filtered out if present upstream

Integration in pipeline
-----------------------

Direct upstream dependencies:

- ``generation.py``
- ``installed_capacity.py``

Direct downstream usage in SupplyForge:

- ``create_pommes_craft_model.py``

Role in POMMES
--------------

The output is used as an hourly availability proxy for intermittent
renewable technologies.

In downstream model construction:

- capacity factors are injected as technology availability profiles
- they constrain hourly renewable generation potential

Thus, this module contributes to:

    operational parametrization of intermittent supply


"""

import logging

import polars as pl
from supplyforge import RESULTS_DIR

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def compute_intermittent_capacity_factors(country_code: str, year: int):
    """
    Compute the intermittent capacity-factor frame for a country/year.

    Returns the long frame that :func:`calculate_intermittent_capacity_factors`
    writes (columns: ``plant_type``, ``hour`` [datetime, UTC], ``capacity_factor``,
    ``installed_capacity``, ``generation_mw``), or ``None`` when the generation /
    installed-capacity inputs are missing or empty. Factored out (additively, with
    identical numerics) so source-aware composition (PECD/ERA5 mixes) can reuse the
    ENTSO-E wind/solar/RoR series without re-reading the written Parquet.

    Args:
        country_code: The two-letter country code.
        year: The year for which to process the data.
    """
    logger.info(f"Calculating capacity factors for {country_code} for the year {year}...")

    # Define paths
    generation_file = RESULTS_DIR / "generation" / f"generation_{country_code}_{year}.parquet"
    installed_capacity_file = (
        RESULTS_DIR / "installed_capacities" / f"installed_capacities_{country_code}_{year}.parquet"
    )

    # Load data
    try:
        generation_df = pl.read_parquet(generation_file)
        installed_capacity_df = pl.read_parquet(installed_capacity_file)
    except:
        logger.warning(f"Input file not found. Skipping calculation.")
        return None

    # 1. Clean and filter generation column names.
    # We are only interested in 'Actual Aggregated' generation.
    # The original column names are tuples, e.g., ('Solar', 'Actual Aggregated').
    cleaned_columns = {}
    for col in generation_df.columns:
        col_str = str(col) 
        if "('index'" in col_str:
            cleaned_columns[col] = "index"
        elif "'Actual Aggregated'" in col_str:
            # Extract the plant type, e.g., 'Solar' from "('Solar', 'Actual Aggregated')"
            plant_type = col_str.split("'")[1]
            cleaned_columns[col] = plant_type

    generation_df = generation_df.rename(cleaned_columns)

    if generation_df.is_empty() or installed_capacity_df.is_empty():
        logger.warning(
            f"Generation or installed capacity data for {country_code} {year} is empty. "
            "Cannot calculate capacity factors."
        )
        return None


    # 2. Process Installed Capacity Data
    # Unpivot to long format and aggregate total capacity per plant type.
    value_vars = [col for col in installed_capacity_df.columns if col != "index"]
    installed_capacity_long = (
        installed_capacity_df.unpivot(index="index", on=value_vars, variable_name="plant_type", value_name="installed_capacity")
        .group_by("plant_type")
        .agg(pl.sum("installed_capacity"))
    )

    # 3. Process Generation Data
    # Unpivot to long format to get (timestamp, plant_type, generation_mw).
    intermittent_types = ["Solar", "Wind Offshore", "Wind Onshore", "Hydro Run-of-river and poundage"]
    # Filter for columns that are in our intermittent list
    value_cols = [col for col in generation_df.columns if col in intermittent_types]

    generation_long = generation_df.unpivot(
        index="index", on=value_cols, variable_name="plant_type", value_name="generation_mw"
    ).rename({"index": "hour"}).with_columns(pl.col("hour").dt.convert_time_zone("UTC"))

    # 4. Combine and Calculate Capacity Factor
    # Join generation data with aggregated installed capacity.
    capacity_factors_df = (
        generation_long.join(installed_capacity_long, on="plant_type", how="left")
        .with_columns(
            # Calculate capacity factor, handling cases with zero capacity to avoid division errors.
            pl.when(pl.col("installed_capacity") > 0)
            .then(pl.col("generation_mw") / pl.col("installed_capacity"))
            .otherwise(0.0)
            .alias("capacity_factor")
        )
        .filter(pl.col("installed_capacity").is_not_null()) # Only keep types with capacity data
    )
    capacity_factors_df = (
        capacity_factors_df
        .sort(["plant_type", "hour"])
        .group_by_dynamic(
            index_column="hour",
            every="1h",     # resample frequency
            period="1h",     # window size
            group_by="plant_type"
        )
        .agg([
            pl.col("capacity_factor").mean(),
            pl.col("installed_capacity").first(),
            pl.col("generation_mw").mean(),
        ])
    )
    return capacity_factors_df


def calculate_intermittent_capacity_factors(country_code: str, year: int) -> None:
    """
    Calculates the hourly capacity factor for intermittent renewable generation types.

    The capacity factor is the actual hourly generation as a share of the
    total installed capacity for that generation type.

    Args:
        country_code: The two-letter country code.
        year: The year for which to process the data.
    """
    output_dir = RESULTS_DIR / "capacity_factors"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"capacity_factors_{country_code}_{year}.parquet"

    capacity_factors_df = compute_intermittent_capacity_factors(country_code, year)
    if capacity_factors_df is None:
        output_file.touch()
        return None

    # Save result
    capacity_factors_df.write_parquet(output_file)
    logger.info(f"Capacity factor data saved to {output_file}")


if __name__ == "__main__":
    try:
        calculate_intermittent_capacity_factors(
            country_code=snakemake.params.country, year=int(snakemake.params.year)
        )
    except NameError:
        logger.error("This script is intended to be run via Snakemake's 'script' directive. Running with test values.")

        calculate_intermittent_capacity_factors(
            country_code="ES", year=2024
        )