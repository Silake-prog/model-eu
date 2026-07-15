"""Combines thermosensitive and electric vehicle (EV) load profiles.

This script takes pre-calculated thermosensitive load data and merges it with
EV load projections. It handles the temporal alignment of the EV data to match
the calendar of the target year.
"""

import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from demandforge import RESULTS_DIR

logger = logging.getLogger(__name__)


def align_ev_load(
        df_ev: pd.DataFrame,
        year: int,
        ev_year: int,
):
    """Aligns an EV load profile to a target year's calendar.

    The reference EV load data is from a fixed year (2018). This function shifts
    the daily load profiles to ensure that the days of the week match the target
    year. It also handles leap years by duplicating the load from Feb 28th for
    Feb 29th if the target year is a leap year.

    Args:
        df_ev (pd.DataFrame): DataFrame containing the reference EV load data.
            Expected to have a 'WS1' column.
        year (int): The target year to which the EV load should be aligned.
        ev_year (int): The reference year of the EV data, used for naming the
            output column.

    Returns:
        pd.DataFrame: A DataFrame with the EV load aligned to the target year's
            calendar. The index is a DatetimeIndex for the target year, and it
            contains a single column named 'ev_load_{ev_year}'.
    """
    # Ensure datetime index for EV data (assuming 2018)
    if not isinstance(df_ev.index, pd.DatetimeIndex):
        df_ev.index = pd.date_range(
            start="2018-01-01", periods=len(df_ev), freq="h"
        )

    # --- Timestamp Alignment ---
    start_day_ev = pd.Timestamp("2018-01-01").dayofweek
    start_day_thermo = pd.Timestamp(f"{year}-01-01").dayofweek
    day_shift = start_day_thermo - start_day_ev
    hour_shift = day_shift * 24

    ev_values = df_ev["WS1"].values.flatten()
    ev_values_rolled = np.roll(ev_values, hour_shift)

    target_index = pd.date_range(
        start=f"{year}-01-01", end=f"{year}-12-31 23:00:00", freq="h"
    )

    final_ev_values = ev_values_rolled
    if len(ev_values_rolled) < len(target_index):  # Leap year adjustment
        logger.debug("Adjusting for leap year.")
        feb_28_start_hour = 24 * 58
        feb_28_end_hour = 24 * 59
        feb_28_data = ev_values_rolled[feb_28_start_hour:feb_28_end_hour]
        insertion_point = 24 * 59
        final_ev_values = np.insert(ev_values_rolled, insertion_point, feb_28_data)

    # Create aligned EV DataFrame
    df_ev_aligned = pd.DataFrame(
        final_ev_values[: len(target_index)],
        index=target_index,
        columns=[f"ev_load_{ev_year}"],
    )

    return df_ev_aligned

def combine_load(
    path_thermosensitive: str | Path,
    output_path: str | Path,
    country: str,
    year: int,
) -> None:
    """Combines thermosensitive and multiple EV load projections.

    This function reads a thermosensitive load profile for a given country and
    year. It then finds all available EV load projections for that country, aligns
    each one to the target year's calendar using `align_ev_load`, and joins them
    as new columns to the thermosensitive load DataFrame. The final combined
    DataFrame is saved to a parquet file.

    Args:
        path_thermosensitive (str | Path): Path to the thermosensitive load parquet file.
        output_path (str | Path): Path to save the combined parquet file.
        country (str): The country code for which to combine the load data.
        year (int): The year of the thermosensitive load data.
    """
    logger.info(
        f"Combining thermosensitive data from {path_thermosensitive} "
        f"with EV data for country {country} and year {year}."
    )

    # Load data
    try:
        df_thermo = pd.read_parquet(path_thermosensitive)
    except:
        logger.warning(f"Could not load thermosensitive data from {path_thermosensitive}. Skipping analysis.")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).touch()
        return

    # Ensure datetime index for thermosensitive data
    if not isinstance(df_thermo.index, pd.DatetimeIndex):
        df_thermo.index = pd.date_range(
            start=f"{year}-01-01", periods=len(df_thermo), freq="h"
        )

    ev_folder = RESULTS_DIR / "eraa_ev_load_curves"

    for f in ev_folder.glob("*.parquet"):
        if f.stem.startswith(country):
            ev_year = int(f.stem.split("_")[1])
            ev_load = align_ev_load(pd.read_parquet(f), year, ev_year)
            df_thermo = df_thermo.join(ev_load)

    df_thermo.drop('timestamp', axis=1, inplace=True)
    df_thermo.to_parquet(output_path)
    logger.info(f"Saved combined load data to {output_path}")