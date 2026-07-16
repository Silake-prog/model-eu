r"""
Hourly hydro inflow reconstruction module.

Role in SupplyForge pipeline
----------------------------

This module reconstructs hourly hydro reservoir inflows from two upstream
historical datasets:

- hourly hydro reservoir generation
- weekly hydro storage levels

It belongs to the category of:

    *hydrological transformation and temporal disaggregation*

The module provides an inferred hourly inflow signal for reservoir hydro
systems. This signal is later used in the POMMES modeling framework to
parameterize reservoir hydro resource availability.

Data sources
------------

The module combines two upstream datasets:

- production dataset from ``generation.py``
- hydro stock dataset from ``hydro_storage.py``

Typical standalone file inputs:

- ``generation/generation_<country>_<year>.parquet``
- ``hydro_storage/hydro_storage_<country>_<year>.parquet``

Typical columns used:

Production data:

- timestamp column:
  user-provided via ``timestamp_col_prod``
- production column:
  user-provided via ``prod_col``

Stock data:

- timestamp column:
  user-provided via ``timestamp_col_stock``
- stock column:
  user-provided via ``stock_col``

In the default standalone test block:

- production column:
  ``('Hydro Water Reservoir', 'Actual Aggregated')``
- stock column:
  ``storage_mwh``

Outputs
-------

The module writes one Parquet file per country and year:

- typical directory:
  ``RESULTS_DIR / "inflow"``
- typical filename:
  ``inflow_<country>_<year>.parquet``

Output schema:

- ``inflow_MW`` (float)
- ``timestamp`` (datetime, UTC)

If processing fails:

- the script creates an empty file via ``touch()``


Main functions
--------------

The module is structured into four main processing functions:

- ``compute_weekly_inflows``
- ``interpolate_weekly_to_hourly``
- ``create_week_start``
- ``generate_hourly_inflow_dataset``

Purpose of the transformation
-----------------------------

The objective is to infer hydro inflows from an accounting balance between:

- hydro production
- change in stored energy

The weekly inflow is reconstructed first, then linearly interpolated to an
hourly series and converted into a power-equivalent hourly inflow rate.

Algorithmic description
-----------------------

The module performs the following steps:

1. load hourly production data
2. load weekly hydro storage data
3. standardize time indices:

   - production timestamps converted to UTC
   - stock timestamps localized to UTC

4. aggregate hourly production to weekly totals:

   - hourly resampling with mean
   - weekly resampling with ``W-MON`` and sum

5. compute weekly stock variation:

   - ``delta_S = S_w - S_{w-1}``

6. align weekly production and stock-change indices
7. reconstruct weekly inflow:

   - weekly inflow = weekly production + weekly stock change

8. construct a weekly calendar using ``create_week_start(year)``

9. clean stock table size heuristically:

   - if more than 52 rows, drop first row
   - if still more than 52 rows, drop last row
   - if 51 rows, duplicate last row

10. overwrite stock timestamps with generated Monday week starts
11. build full hourly target index over the year:

    - start: ``year-01-01 00:00 UTC``
    - end: ``year+1-01-01 00:00 UTC`` excluded

12. interpolate weekly inflow to hourly resolution:

    - linear interpolation
    - back-fill initial missing values
    - clip negatives to zero

13. convert weekly energy inflow to hourly power-equivalent inflow:

    - divide by 168 h/week

14. write final Parquet output

Formal definition
-----------------

The core balance implemented in the module is:

.. math::

    I_w = G_w + \Delta S_w

where:

- :math:`I_w` is weekly inflow
- :math:`G_w` is weekly hydro production
- :math:`\Delta S_w = S_w - S_{w-1}` is weekly stock variation

After interpolation to hourly resolution, the module converts weekly energy
to hourly power-equivalent inflow:

.. math::

    I_t^{MW} = \frac{\widetilde{I}_t^{MWh/week}}{168}

where:

- :math:`\widetilde{I}_t` is the interpolated hourly value of weekly inflow
- 168 is the number of hours in a week

Temporal conventions
--------------------

Production data:

- assumed to be hourly
- timestamps converted to UTC with:

  - ``tz_convert("UTC")``

Stock data:

- assumed to represent weekly values
- timestamps localized to UTC with:

  - ``tz_localize("UTC")``

Weekly alignment:

- weekly production totals are computed on ``W-MON``
- generated week starts are Mondays
- exactly 52 week starts are forced

Hourly target index:

- full-year UTC hourly index
- generated with:

  - ``pd.date_range(..., freq="1h", inclusive="left", tz="UTC")``

Methodological assumptions
--------------------------

Explicit assumptions:

- production and stock obey a simple accounting identity
- weekly inflow can be reconstructed from production plus stock change
- linear interpolation from weekly to hourly is acceptable
- inflows cannot be negative

Implicit assumptions:

- weekly stock observations are correctly aligned to Monday week starts
- 52 generated weeks are sufficient to represent the year
- production aggregation by weekly sum is compatible with stock reporting
- clipping negative inflows is physically preferable to preserving balance
- hourly inflow can be approximated by smooth interpolation of weekly values

Integration in pipeline
-----------------------

Direct upstream dependencies:

- ``generation.py``
- ``hydro_storage.py``

Downstream usage in SupplyForge:

- ``create_pommes_craft_model.py``

Role in POMMES
--------------

The output is used to parameterize reservoir hydro inflows.

In ``create_pommes_craft_model.py``:

- the inflow series is used to create reservoir inflow availability
- it contributes to the calibration of reservoir hydro behavior

Thus, this module contributes to:

    hydrological parametrization of reservoir hydro

rather than market, demand, or installed-capacity characterization.

"""




import logging
from pathlib import Path
import ast

import pandas as pd
import polars as pl

# Set up logging
logger = logging.getLogger(__name__)


def compute_weekly_inflows(
        prod_df: pd.DataFrame,
        stock_df: pd.DataFrame,
        prod_col: str = "prod_MW",
        stock_col: str = "stock_MWh",
        timestamp_col_prod: str = "timestamp",
        timestamp_col_stock: str = "week_start",
) -> pd.Series:
    """
    Compute weekly hydro inflows using weekly stock and weekly production totals.

    Args:
        prod_df (pd.DataFrame): Hourly production data with columns [timestamp_col_prod, prod_col].
        stock_df (pd.DataFrame): Weekly stock data with columns [timestamp_col_stock, stock_col].
        prod_col (str): Name of production column. Defaults to "prod_MW".
        stock_col (str): Name of stock column. Defaults to "stock_MWh".
        timestamp_col_prod (str): Timestamp column in production data.
        timestamp_col_stock (str): Timestamp column in stock data.

    Returns:
        pd.Series: Weekly inflow series indexed by week start.
    """
    # --- Ensure naive datetime indices ---
    prod_df = prod_df.copy()
    prod_df.index = pd.to_datetime(prod_df[timestamp_col_prod])
    prod_df.index = prod_df.index.tz_convert("UTC")

    stock_df = stock_df.copy()
    stock_df.index = pd.to_datetime(stock_df[timestamp_col_stock])
    stock_df.index = stock_df.index.tz_localize("UTC")

    # Aggregate production to weekly totals
    prod_hourly = prod_df[[prod_col]].resample("h").mean()
    prod_weekly = prod_hourly[prod_col].resample("W-MON").sum()

    # Compute weekly delta stock
    delta_S = stock_df[stock_col].diff().fillna(0)

    # Align prod_weekly and delta_S
    common_index = prod_weekly.index.intersection(delta_S.index)
    prod_weekly = prod_weekly[common_index]
    delta_S = delta_S[common_index]

    # Weekly inflows: production + change in stock
    inflow_weekly = prod_weekly + delta_S

    return inflow_weekly


def interpolate_weekly_index(
    weekly_series: pd.Series,
    target_index: pd.DatetimeIndex,
) -> pd.Series:
    """
    Linear weekly->hourly interpolation on a UTC index (no unit conversion).

    Shared core: align both indices to UTC, reindex onto the union of the target
    and weekly indices, linearly interpolate, back-fill leading NaNs, clip
    negatives to zero, then reindex to ``target_index``. This is used by
    :func:`interpolate_weekly_to_hourly` (which then converts MWh/week -> MW) and
    by the PECD run-of-river capacity-factor builder (which interpolates a weekly
    capacity factor directly, with no division). Factored out so neither caller
    copy-pastes the interpolation logic.

    Args:
        weekly_series (pd.Series): Weekly values indexed by week start.
        target_index (pd.DatetimeIndex): Hourly timestamps for interpolation.

    Returns:
        pd.Series: Interpolated hourly values aligned with ``target_index``
            (no unit conversion applied).
    """
    weekly_series = weekly_series.copy()
    weekly_series.index = pd.to_datetime(weekly_series.index, utc=True)
    target_index = pd.to_datetime(target_index, utc=True)

    return (
        weekly_series.reindex(target_index.union(weekly_series.index))
        .interpolate(method="linear")
        .bfill()  # Back-fill any remaining NaNs at the beginning
        .clip(lower=0.0)  # Values cannot be negative
        .reindex(target_index)  # Align to the final hourly index
    )


def interpolate_weekly_to_hourly(
    inflow_weekly: pd.Series,
    target_index: pd.DatetimeIndex,
) -> pd.Series:
    """
    Interpolate weekly inflows to hourly values.

    Args:
        inflow_weekly (pd.Series): Weekly inflows indexed by week start.
        target_index (pd.DatetimeIndex): Hourly timestamps for interpolation.

    Returns:
        pd.Series: Hourly inflows aligned with target_index.
    """
    logger.info("Interpolating weekly inflows to hourly resolution.")

    # Interpolate (shared core), then convert weekly MWh inflow to MW inflow rate.
    hourly_inflow = interpolate_weekly_index(inflow_weekly, target_index)
    hourly_inflow_mw = hourly_inflow / 168.0  # 168 hours in a week
    logger.info("Interpolation to hourly inflows complete.")

    return hourly_inflow_mw


def create_week_start(year: int) -> pd.DatetimeIndex:
    """
    Generate a DatetimeIndex of week start dates (Monday) for a given year.

    Args:
        year (int): Year for which to generate weekly start dates.

    Returns:
        pd.DatetimeIndex: Start of each week (Monday), length 52 (or 53 if ISO weeks overlap).
    """
    # First day of the year
    first_day = pd.Timestamp(f"{year}-01-01")
    # Align to the first Monday of the year
    first_monday = first_day + pd.offsets.Week(weekday=0)
    # Generate 52 weeks
    week_starts = pd.date_range(first_monday, periods=52, freq="W-MON")
    return week_starts


def generate_hourly_inflow_dataset(
    prod_df: pd.DataFrame,
    stock_df: pd.DataFrame,
    prod_col: str,
    stock_col: str,
    timestamp_col_prod: str,
    timestamp_col_stock: str,
    year: int,
) -> pd.DataFrame:
    """
    Generate an hourly hydro inflow dataset from hourly production and weekly stock.

    Args:
        prod_df (pd.DataFrame): Hourly production data.
        stock_df (pd.DataFrame): Weekly stock data.
        prod_col (str): Production column name.
        stock_col (str): Stock column name.
        timestamp_col_prod (str): Timestamp column in production data.
        timestamp_col_stock (str): Timestamp column in stock data.
        year (int): year of the simulation

    Returns:
        pd.DataFrame: Hourly dataset with columns [inflow_MW].
    """
    # --- 1. Compute weekly inflows ---
    if stock_df.shape[0] > 52:
        stock_df = stock_df.iloc[1:].copy()
    if stock_df.shape[0] > 52:
        stock_df = stock_df.iloc[:-1].copy()
    if stock_df.shape[0] == 51:
        stock_df = pd.concat([stock_df, stock_df.tail(1)], ignore_index=True)
    stock_df[timestamp_col_stock] = create_week_start(year)

    if prod_col not in prod_df.columns:
        return pl.DataFrame(
        {
            "inflow_MW": [],
            "timestamp": []
        }
        )

    inflow_weekly = compute_weekly_inflows(
        prod_df, stock_df, prod_col, stock_col, timestamp_col_prod, timestamp_col_stock
    )
    target_index = pd.date_range(start=f"{year}-01-01", end=f"{year+1}-01-01", freq="1h", inclusive="left", tz="UTC")
    # --- 2. Interpolate to hourly inflows ---
    hourly_inflow = interpolate_weekly_to_hourly(inflow_weekly, target_index)

    # --- 3. Construct final hourly dataset ---
    df_out = pl.from_pandas(pd.DataFrame(
        {
            "inflow_MW": hourly_inflow.values,
            "timestamp": target_index
        }
    ))

    return df_out


if __name__ == "__main__":
    # This block is executed when the script is run directly by Snakemake
    # or for debugging when run standalone.

    # --- Detect execution mode (Snakemake or Standalone) ---
    if "snakemake" in locals() or "snakemake" in globals():
        # --- Snakemake execution ---
        log_file = snakemake.log[0]
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            filename=log_file,
            filemode="w",
        )
        logger.info("Running in Snakemake mode.")

        year = int(snakemake.wildcards.year)
        production_path = Path(snakemake.input.production)
        stock_path = Path(snakemake.input.stock)
        output_path = Path(snakemake.output[0])
        prod_col = snakemake.params.prod_col
        stock_col = snakemake.params.stock_col
        timestamp_col_prod = snakemake.params.timestamp_col_prod
        timestamp_col_stock = snakemake.params.timestamp_col_stock
    else:
        # --- Standalone execution for debugging ---
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        logger.info("Running in standalone mode for debugging.")

        # Example values for Spain, 2022
        country, year = "ES", 2024
        base_path = Path.cwd().parents[1] / "results"

        production_path = base_path / f"generation/generation_{country}_{year}.parquet"
        stock_path = base_path / f"hydro_storage/hydro_storage_{country}_{year}.parquet"
        output_path = base_path / f"inflow/inflow_{country}_{year}_debug.parquet"

        prod_col = "('Hydro Water Reservoir', 'Actual Aggregated')"
        stock_col = "storage_mwh"
        timestamp_col_prod = "('index', '')"
        timestamp_col_stock = "timestamp"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # --- Data loading ---
        logger.info(f"Loading production data from {production_path}")
        prod_df = pd.read_parquet(production_path)
        logger.info(f"Loading stock data from {stock_path}")
        stock_df = pd.read_parquet(stock_path)

        # --- Main processing ---
        logger.info("Starting hourly hydro inflow generation.")
        hourly_inflow_df = generate_hourly_inflow_dataset(
            prod_df=prod_df,
            stock_df=stock_df,
            prod_col=prod_col,
            stock_col=stock_col,
            timestamp_col_prod=timestamp_col_prod,
            timestamp_col_stock=timestamp_col_stock,
            year=year
        )

        # --- Save output ---
        logger.info(f"Saving hourly inflow data to {output_path}")
        hourly_inflow_df.write_parquet(output_path)
        logger.info("Script finished successfully.")

    except Exception as e:
        logger.warning(f"An error occurred: {e}")
        output_path.touch()
