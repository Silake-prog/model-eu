
"""Module for calculating the thermosensitivity of electricity load curves.

This module provides functions to analyze the relationship between temperature
and electricity load, identifying separate thermosensitivity effects for winter
(heating) and summer (cooling).
"""

import logging
from pathlib import Path
from typing import List, Tuple, Dict, Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import polars as pl
import seaborn as sns
from scipy import stats
from matplotlib.backends.backend_pdf import PdfPages

# Configure logging
logger = logging.getLogger(__name__)

# Default minimum number of samples required for regression
DEFAULT_MIN_SAMPLES = 30


def load_data(
        load_path: Path, temp_path: Path
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Loads load curve and temperature data from parquet files.

    Args:
        load_path (Path): Path to the load curve parquet file.
        temp_path (Path): Path to the temperature parquet file.

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame]: A tuple containing the load and
            temperature data as pandas DataFrames.
    """
    logger.info(f"Loading load data from {load_path}")
    load_df = pl.read_parquet(load_path).to_pandas()

    logger.info(f"Loading temperature data from {temp_path}")
    temp_df = pl.read_parquet(temp_path).to_pandas()

    return load_df, temp_df


def align_temporal_resolution(
        load_df: pd.DataFrame, temp_df: pd.DataFrame
) -> pd.DataFrame:
    """Aligns load and temperature data to a common hourly temporal resolution.

    This function ensures both datasets are on an hourly frequency, resampling the
    load data if necessary, and then merges them based on the timestamp.

    Args:
        load_df (pd.DataFrame): DataFrame with 'timestamp' and 'load_mw' columns.
        temp_df (pd.DataFrame): DataFrame with 'timestamp' and 'temperature' columns.

    Returns:
        pd.DataFrame: A merged DataFrame with 'timestamp', 'load_mw', and
            'temperature' columns.
    """
    logger.info("Aligning temporal resolution")

    # Ensure timestamp columns are datetime
    load_df["timestamp"] = pd.to_datetime(load_df["timestamp"])
    temp_df["timestamp"] = pd.to_datetime(temp_df["timestamp"])

    # Set timestamp as index for resampling
    load_df = load_df.set_index("timestamp")

    # Check if load data is hourly
    time_diff = load_df.index.to_series().diff()
    median_diff = time_diff.median()

    logger.info(f"Median time difference in load data: {median_diff}")

    # Resample to hourly if needed
    if median_diff != pd.Timedelta(hours=1):
        logger.info(f"Resampling load data from {median_diff} to hourly")
        load_df = load_df.resample("1h").mean()
    else:
        logger.info("Load data is already hourly")

    # Reset index to merge
    load_df = load_df.reset_index()

    # Merge on timestamp
    merged_df = pd.merge(
        load_df,
        temp_df,
        on="timestamp",
        how="outer",
    )
    merged_df['load_mw'] = merged_df['load_mw'].interpolate("linear")

    logger.info(
        f"Merged data shape: {merged_df.shape}, "
        f"date range: {merged_df['timestamp'].min()} to "
        f"{merged_df['timestamp'].max()}"
    )

    return merged_df


def calculate_regression(
        temperatures: np.ndarray, loads: np.ndarray, min_samples: int = DEFAULT_MIN_SAMPLES
) -> Dict[str, float]:
    """Calculates a linear regression between temperature and load.

    Args:
        temperatures (np.ndarray): Array of temperature values.
        loads (np.ndarray): Array of load values.
        min_samples (int): Minimum number of samples required to perform the
            regression.

    Returns:
        Dict[str, float]: A dictionary containing the regression results:
            'slope', 'intercept', 'r_squared', 'p_value', 'std_err', and
            'n_samples'. Returns NaNs if samples are insufficient.
    """
    n_samples = len(temperatures)

    if loads.sum() == 0.:
        logger.debug(f"No valid data for regression: sum(loads) == 0")
        return {
            "slope": np.nan,
            "intercept": np.nan,
            "r_squared": np.nan,
            "p_value": np.nan,
            "std_err": np.nan,
            "n_samples": n_samples,
        }

    if n_samples < min_samples:
        logger.debug(f"Insufficient samples for regression: {n_samples} < {min_samples}")
        return {
            "slope": np.nan,
            "intercept": np.nan,
            "r_squared": np.nan,
            "p_value": np.nan,
            "std_err": np.nan,
            "n_samples": n_samples,
        }

    slope, intercept, r_value, p_value, std_err = stats.linregress(
        temperatures, loads
    )

    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_value**2,
        "p_value": p_value,
        "std_err": std_err,
        "n_samples": n_samples,
    }


def calculate_thermosensitivity_for_threshold(
        df: pd.DataFrame,
        winter_threshold: float,
        summer_threshold: float,
        hour: Optional[int] = None,
        min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Dict[str, Dict[str, float]]:
    """Calculates thermosensitivity for given winter and summer temperature thresholds.

    Args:
        df (pd.DataFrame): DataFrame with 'timestamp', 'load_mw', and 'temperature'.
        winter_threshold (float): Temperature below which is considered winter.
        summer_threshold (float): Temperature above which is considered summer.
        hour (Optional[int]): If provided, filters the data for a specific hour (0-23).
        min_samples (int): Minimum number of samples for a valid regression.

    Returns:
        Dict[str, Dict[str, float]]: A dictionary with 'winter' and 'summer' keys,
            each containing a dictionary of regression results.
    """
    # Filter by hour if specified
    if hour is not None:
        df = df[df['hour'] == hour].copy()

    # Split data by season
    winter_mask = df["temperature"] < winter_threshold
    summer_mask = df["temperature"] > summer_threshold

    winter_df = df[winter_mask]
    summer_df = df[summer_mask]

    logger.debug(
        f"Hour {hour}: Winter samples: {len(winter_df)}, Summer samples: {len(summer_df)}"
    )

    # Calculate regressions
    winter_results = calculate_regression(
        winter_df["temperature"].values, winter_df["load_mw"].values, min_samples
    )
    summer_results = calculate_regression(
        summer_df["temperature"].values, summer_df["load_mw"].values, min_samples
    )

    return {"winter": winter_results, "summer": summer_results}


def find_best_thresholds_per_hour(
        df: pd.DataFrame,
        winter_thresholds: List[float],
        summer_thresholds: List[float],
        min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Tuple[pd.DataFrame, Dict[int, Tuple[float, float]]]:
    """Finds the best winter and summer temperature thresholds for each hour.

    The "best" combination is defined as the one that maximizes the sum of the
    R-squared values for the winter and summer regressions, considering only
    combinations where both seasons have sufficient data.

    Args:
        df (pd.DataFrame): DataFrame with 'timestamp', 'load_mw', 'temperature',
            and 'hour' columns.
        winter_thresholds (List[float]): A list of winter threshold temperatures to test.
        summer_thresholds (List[float]): A list of summer threshold temperatures to test.
        min_samples (int): Minimum number of samples for a valid regression.

    Returns:
        Tuple[pd.DataFrame, Dict[int, Tuple[float, float]]]:
            - A DataFrame containing the regression results for all tested thresholds.
            - A dictionary mapping each hour to its best (winter, summer) threshold pair.
    """
    logger.info(
        f"Testing {len(winter_thresholds)} winter and "
        f"{len(summer_thresholds)} summer thresholds for each hour "
        f"(min_samples={min_samples})"
    )

    if df['load_mw'].sum() == 0.:
        logger.warning("No data to analyze. Skipping.")
        return pd.DataFrame(), {}

    results = []
    best_thresholds = {}

    # Process each hour
    for hour in range(24):
        logger.info(f"Processing hour {hour}")
        hour_df = df[df['hour'] == hour].copy()

        if len(hour_df) == 0:
            logger.warning(f"No data for hour {hour}")
            continue

        hour_results = []
        valid_combinations = 0

        for winter_thresh in winter_thresholds:
            for summer_thresh in summer_thresholds:
                if winter_thresh >= summer_thresh:
                    continue  # Skip invalid combinations

                season_results = calculate_thermosensitivity_for_threshold(
                    hour_df, winter_thresh, summer_thresh, hour=None, min_samples=min_samples
                )

                # Store results
                for season, reg_results in season_results.items():
                    result = {
                        "hour": hour,
                        "season": season,
                        "winter_threshold": winter_thresh,
                        "summer_threshold": summer_thresh,
                        "threshold": (
                            winter_thresh
                            if season == "winter"
                            else summer_thresh
                        ),
                        "thermosensitivity_mw_per_c": reg_results["slope"],
                        "r_squared": reg_results["r_squared"],
                        "intercept": reg_results["intercept"],
                        "p_value": reg_results.get("p_value", np.nan),
                        "std_err": reg_results.get("std_err", np.nan),
                        "n_samples": reg_results["n_samples"],
                        "best_regression": False,  # Will be updated later
                    }
                    hour_results.append(result)
                    results.append(result)

                    # Count valid regressions (not NaN)
                    if not np.isnan(reg_results["r_squared"]):
                        valid_combinations += 1

        # Find best combination for this hour (maximum sum of R² values)
        # Only consider combinations where both winter and summer have valid regressions
        if hour_results:
            hour_df_results = pd.DataFrame(hour_results)

            # Filter to only valid regressions (non-NaN R²)
            valid_results = hour_df_results[~hour_df_results["r_squared"].isna()].copy()

            if len(valid_results) > 0:
                combined_r2 = (
                    valid_results.groupby(["winter_threshold", "summer_threshold"])["r_squared"]
                    .sum()
                    .reset_index()
                )

                # Only consider combinations where both seasons have valid data
                # (sum should come from 2 seasons)
                season_count = (
                    valid_results.groupby(["winter_threshold", "summer_threshold"])
                    .size()
                    .reset_index(name='count')
                )
                combined_r2 = combined_r2.merge(season_count, on=["winter_threshold", "summer_threshold"])
                combined_r2 = combined_r2[combined_r2['count'] == 2]  # Both seasons must have data

                if len(combined_r2) > 0:
                    best_idx = combined_r2["r_squared"].idxmax()
                    best_winter = combined_r2.loc[best_idx, "winter_threshold"]
                    best_summer = combined_r2.loc[best_idx, "summer_threshold"]
                    best_combined_r2 = combined_r2.loc[best_idx, "r_squared"]

                    best_thresholds[hour] = (best_winter, best_summer)

                    logger.info(
                        f"Hour {hour}: Best thresholds: winter={best_winter}°C, "
                        f"summer={best_summer}°C, combined R²={best_combined_r2:.4f} "
                        f"({valid_combinations} valid regressions out of {len(hour_results)} total)"
                    )
                else:
                    logger.warning(f"Hour {hour}: No valid threshold combinations with both seasons having sufficient data")
            else:
                logger.warning(f"Hour {hour}: No valid regressions (insufficient samples)")

    # Create results DataFrame and mark best regressions
    results_df = pd.DataFrame(results)

    # Mark best regressions for each hour
    for hour, (best_winter, best_summer) in best_thresholds.items():
        mask = (
                (results_df['hour'] == hour) &
                (results_df['winter_threshold'] == best_winter) &
                (results_df['summer_threshold'] == best_summer)
        )
        results_df.loc[mask, 'best_regression'] = True

    return results_df, best_thresholds


def plot_thermosensitivity_per_hour(
        df: pd.DataFrame,
        hour: int,
        winter_threshold: float,
        summer_threshold: float,
        country: str,
        year: int,
        output_path: Path,
        min_samples: int = DEFAULT_MIN_SAMPLES,
):
    """Creates scatter plots of load vs. temperature with regression lines for a specific hour.

    Args:
        df (pd.DataFrame): DataFrame with 'timestamp', 'load_mw', 'temperature', 'hour'.
        hour (int): The hour of the day to plot (0-23).
        winter_threshold (float): The temperature threshold for the winter season.
        summer_threshold (float): The temperature threshold for the summer season.
        country (str): The country code for the plot title.
        year (int): The year for the plot title.
        output_path (Path): The path to save the output plot image.
        min_samples (int): The minimum number of samples required for regression.
    """
    logger.info(f"Creating thermosensitivity plot for {country} {year} hour {hour}")

    # Filter data for this hour
    hour_df = df[df['hour'] == hour].copy()

    if len(hour_df) == 0:
        logger.warning(f"No data for hour {hour}")
        return

    # Split data by season
    winter_mask = hour_df["temperature"] < winter_threshold
    summer_mask = hour_df["temperature"] > summer_threshold

    winter_df = hour_df[winter_mask]
    summer_df = hour_df[summer_mask]

    # Calculate regressions
    winter_reg = calculate_regression(
        winter_df["temperature"].values, winter_df["load_mw"].values, min_samples
    )
    summer_reg = calculate_regression(
        summer_df["temperature"].values, summer_df["load_mw"].values, min_samples
    )

    # Create figure with two subplots
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Set style
    sns.set_style("whitegrid")

    # Winter plot
    if len(winter_df) > 0:
        axes[0].scatter(
            winter_df["temperature"],
            winter_df["load_mw"],
            alpha=0.5,
            s=20,
            color='blue',
        )

        # Plot regression line only if we have sufficient samples
        if not np.isnan(winter_reg["slope"]):
            temp_range = np.array(
                [winter_df["temperature"].min(), winter_df["temperature"].max()]
            )
            load_pred = (
                    winter_reg["slope"] * temp_range + winter_reg["intercept"]
            )
            axes[0].plot(
                temp_range, load_pred, "r-", linewidth=2, label="Regression line"
            )

        # Add statistics text
        if not np.isnan(winter_reg["slope"]):
            stats_text = (
                f"Thermosensitivity: {winter_reg['slope']:.2f} MW/°C\n"
                f"R²: {winter_reg['r_squared']:.4f}\n"
                f"n: {winter_reg['n_samples']:,}\n"
                f"Threshold: T < {winter_threshold}°C"
            )
        else:
            stats_text = (
                f"Insufficient data\n"
                f"n: {winter_reg['n_samples']:,} < {min_samples}\n"
                f"Threshold: T < {winter_threshold}°C"
            )

        axes[0].text(
            0.05,
            0.95,
            stats_text,
            transform=axes[0].transAxes,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
            fontsize=10,
        )

    axes[0].set_xlabel("Temperature (°C)", fontsize=12)
    axes[0].set_ylabel("Load (MW)", fontsize=12)
    axes[0].set_title(
        f"Winter Thermosensitivity - {country} {year} - Hour {hour:02d}:00", fontsize=14
    )
    if len(winter_df) > 0 and not np.isnan(winter_reg["slope"]):
        axes[0].legend()

    # Summer plot
    if len(summer_df) > 0:
        axes[1].scatter(
            summer_df["temperature"],
            summer_df["load_mw"],
            alpha=0.5,
            s=20,
            color='orange',
        )

        # Plot regression line only if we have sufficient samples
        if not np.isnan(summer_reg["slope"]):
            temp_range = np.array(
                [summer_df["temperature"].min(), summer_df["temperature"].max()]
            )
            load_pred = (
                    summer_reg["slope"] * temp_range + summer_reg["intercept"]
            )
            axes[1].plot(
                temp_range, load_pred, "b-", linewidth=2, label="Regression line"
            )

        # Add statistics text
        if not np.isnan(summer_reg["slope"]):
            stats_text = (
                f"Thermosensitivity: {summer_reg['slope']:.2f} MW/°C\n"
                f"R²: {summer_reg['r_squared']:.4f}\n"
                f"n: {summer_reg['n_samples']:,}\n"
                f"Threshold: T > {summer_threshold}°C"
            )
        else:
            stats_text = (
                f"Insufficient data\n"
                f"n: {summer_reg['n_samples']:,} < {min_samples}\n"
                f"Threshold: T > {summer_threshold}°C"
            )

        axes[1].text(
            0.05,
            0.95,
            stats_text,
            transform=axes[1].transAxes,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="lightblue", alpha=0.8),
            fontsize=10,
        )

    axes[1].set_xlabel("Temperature (°C)", fontsize=12)
    axes[1].set_ylabel("Load (MW)", fontsize=12)
    axes[1].set_title(
        f"Summer Thermosensitivity - {country} {year} - Hour {hour:02d}:00", fontsize=14
    )
    if len(summer_df) > 0 and not np.isnan(summer_reg["slope"]):
        axes[1].legend()

    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close()

    logger.info(f"Plot saved to {output_path}")


def plot_summary_graphs(
        results_df: pd.DataFrame,
        country: str,
        year: int,
) -> Tuple[plt.Figure, plt.Figure]:
    """Creates summary graphs of thermosensitivity and R-squared values by hour.

    Args:
        results_df (pd.DataFrame): DataFrame containing the analysis results.
        country (str): The country code for titles.
        year (int): The year for titles.

    Returns:
        Tuple[plt.Figure, plt.Figure]: A tuple containing the matplotlib figure
            objects for the thermosensitivity and R-squared plots.
    """
    # Get best regressions only
    best_results = results_df[results_df['best_regression'] == True].copy()

    # Separate winter and summer
    winter_best = best_results[best_results['season'] == 'winter'].sort_values('hour')
    summer_best = best_results[best_results['season'] == 'summer'].sort_values('hour')

    # Create thermosensitivity plot
    fig1, ax1 = plt.subplots(figsize=(14, 6))

    # Plot winter thermosensitivity
    if len(winter_best) > 0:
        ax1.plot(
            winter_best['hour'],
            winter_best['thermosensitivity_mw_per_c'],
            marker='o',
            linewidth=2,
            markersize=8,
            label='Winter',
            color='blue'
        )
        # Add markers for missing data
        all_hours = set(range(24))
        winter_hours = set(winter_best['hour'].values)
        missing_winter = all_hours - winter_hours
        if missing_winter:
            ax1.scatter(
                list(missing_winter),
                [np.nan] * len(missing_winter),
                marker='x',
                s=100,
                color='blue',
                alpha=0.5,
                label='Winter (insufficient data)'
            )

    # Plot summer thermosensitivity
    if len(summer_best) > 0:
        ax1.plot(
            summer_best['hour'],
            summer_best['thermosensitivity_mw_per_c'],
            marker='s',
            linewidth=2,
            markersize=8,
            label='Summer',
            color='red'
        )
        # Add markers for missing data
        summer_hours = set(summer_best['hour'].values)
        missing_summer = all_hours - summer_hours
        if missing_summer:
            ax1.scatter(
                list(missing_summer),
                [np.nan] * len(missing_summer),
                marker='x',
                s=100,
                color='red',
                alpha=0.5,
                label='Summer (insufficient data)'
            )

    ax1.set_xlabel('Hour of Day', fontsize=12)
    ax1.set_ylabel('Thermosensitivity (MW/°C)', fontsize=12)
    ax1.set_title(f'Thermosensitivity by Hour - {country} {year}', fontsize=14, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=11)
    ax1.set_xticks(range(24))
    ax1.set_xticklabels([f'{h:02d}:00' for h in range(24)], rotation=45, ha='right')

    # Add zero reference line
    ax1.axhline(y=0, color='black', linestyle='--', linewidth=1, alpha=0.5)

    plt.tight_layout()

    # Create R² plot
    fig2, ax2 = plt.subplots(figsize=(14, 6))

    # Plot winter R²
    if len(winter_best) > 0:
        ax2.plot(
            winter_best['hour'],
            winter_best['r_squared'],
            marker='o',
            linewidth=2,
            markersize=8,
            label='Winter',
            color='blue'
        )

    # Plot summer R²
    if len(summer_best) > 0:
        ax2.plot(
            summer_best['hour'],
            summer_best['r_squared'],
            marker='s',
            linewidth=2,
            markersize=8,
            label='Summer',
            color='red'
        )

    ax2.set_xlabel('Hour of Day', fontsize=12)
    ax2.set_ylabel('R² (Coefficient of Determination)', fontsize=12)
    ax2.set_title(f'R² by Hour - {country} {year}', fontsize=14, fontweight='bold')
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=11)
    ax2.set_xticks(range(24))
    ax2.set_xticklabels([f'{h:02d}:00' for h in range(24)], rotation=45, ha='right')
    ax2.set_ylim([0, 1])

    # Add reference lines for R² interpretation
    ax2.axhline(y=0.7, color='green', linestyle='--', linewidth=1, alpha=0.3, label='Good fit (0.7)')
    ax2.axhline(y=0.5, color='orange', linestyle='--', linewidth=1, alpha=0.3, label='Moderate fit (0.5)')
    ax2.legend(fontsize=10)

    plt.tight_layout()

    return fig1, fig2


def create_pdf_report(
        plot_dir: Path,
        country: str,
        year: int,
        output_path: Path,
        results_df: pd.DataFrame,
        min_samples: int = DEFAULT_MIN_SAMPLES,
):
    """Creates a PDF report summarizing the thermosensitivity analysis.

    The report includes a title page, summary graphs of thermosensitivity and
    R-squared values by hour, a statistical summary, and all the individual
    hourly scatter plots.

    Args:
        plot_dir (Path): Directory containing the hourly plot images.
        country (str): The country code.
        year (int): The year of the analysis.
        output_path (Path): The path to save the final PDF report.
        results_df (pd.DataFrame): DataFrame with all analysis results.
        min_samples (int): The minimum number of samples used for regressions.
    """
    logger.info(f"Creating PDF report for {country} {year}")

    with PdfPages(output_path) as pdf:
        # Add title page
        fig = plt.figure(figsize=(11, 8.5))
        fig.text(0.5, 0.6, f'Thermosensitivity Analysis Report',
                 ha='center', va='center', fontsize=24, fontweight='bold')
        fig.text(0.5, 0.5, f'{country} - {year}',
                 ha='center', va='center', fontsize=18)
        fig.text(0.5, 0.4, f'Hourly Analysis (00:00 - 23:00)',
                 ha='center', va='center', fontsize=14)
        fig.text(0.5, 0.3, f'Minimum samples for regression: {min_samples}',
                 ha='center', va='center', fontsize=12, style='italic')
        pdf.savefig(fig, bbox_inches='tight')
        plt.close()

        # Add summary graphs
        logger.info("Adding summary graphs to PDF")
        thermo_fig, r2_fig = plot_summary_graphs(results_df, country, year)

        # Add thermosensitivity summary graph
        pdf.savefig(thermo_fig, bbox_inches='tight')
        plt.close(thermo_fig)

        # Add R² summary graph
        pdf.savefig(r2_fig, bbox_inches='tight')
        plt.close(r2_fig)

        # Add summary page with statistics
        fig, ax = plt.subplots(figsize=(11, 8.5))
        ax.axis('off')

        # Get best regressions summary
        best_results = results_df[results_df['best_regression'] == True].copy()

        if len(best_results) > 0:
            summary_text = f"Summary Statistics\n{'='*50}\n\n"
            summary_text += f"Total hours analyzed: {len(best_results['hour'].unique())}\n"
            summary_text += f"Total threshold combinations tested: {len(results_df) // 2 // 24}\n\n"

            # Winter summary
            winter_best = best_results[best_results['season'] == 'winter']
            if len(winter_best) > 0:
                valid_winter = winter_best[~winter_best['r_squared'].isna()]
                summary_text += f"Winter Season:\n"
                summary_text += f"  Hours with valid regressions: {len(valid_winter)}\n"
                if len(valid_winter) > 0:
                    summary_text += f"  Avg thermosensitivity: {valid_winter['thermosensitivity_mw_per_c'].mean():.2f} MW/°C\n"
                    summary_text += f"  Min thermosensitivity: {valid_winter['thermosensitivity_mw_per_c'].min():.2f} MW/°C\n"
                    summary_text += f"  Max thermosensitivity: {valid_winter['thermosensitivity_mw_per_c'].max():.2f} MW/°C\n"
                    summary_text += f"  Avg R²: {valid_winter['r_squared'].mean():.4f}\n"
                    summary_text += f"  Min R²: {valid_winter['r_squared'].min():.4f}\n"
                    summary_text += f"  Max R²: {valid_winter['r_squared'].max():.4f}\n"
                    summary_text += f"  Avg samples: {valid_winter['n_samples'].mean():.0f}\n\n"

            # Summer summary
            summer_best = best_results[best_results['season'] == 'summer']
            if len(summer_best) > 0:
                valid_summer = summer_best[~summer_best['r_squared'].isna()]
                summary_text += f"Summer Season:\n"
                summary_text += f"  Hours with valid regressions: {len(valid_summer)}\n"
                if len(valid_summer) > 0:
                    summary_text += f"  Avg thermosensitivity: {valid_summer['thermosensitivity_mw_per_c'].mean():.2f} MW/°C\n"
                    summary_text += f"  Min thermosensitivity: {valid_summer['thermosensitivity_mw_per_c'].min():.2f} MW/°C\n"
                    summary_text += f"  Max thermosensitivity: {valid_summer['thermosensitivity_mw_per_c'].max():.2f} MW/°C\n"
                    summary_text += f"  Avg R²: {valid_summer['r_squared'].mean():.4f}\n"
                    summary_text += f"  Min R²: {valid_summer['r_squared'].min():.4f}\n"
                    summary_text += f"  Max R²: {valid_summer['r_squared'].max():.4f}\n"
                    summary_text += f"  Avg samples: {valid_summer['n_samples'].mean():.0f}\n"

            ax.text(0.1, 0.9, summary_text, transform=ax.transAxes,
                    verticalalignment='top', fontsize=11, family='monospace')

        pdf.savefig(fig, bbox_inches='tight')
        plt.close()

        # Add plots for each hour
        for hour in range(24):
            plot_path = plot_dir / f"thermosensitivity_plot_{country}_{year}_hour_{hour:02d}.png"
            if plot_path.exists():
                fig = plt.figure(figsize=(16, 6))
                img = plt.imread(plot_path)
                plt.imshow(img)
                plt.axis('off')
                pdf.savefig(fig, bbox_inches='tight')
                plt.close()
            else:
                logger.warning(f"Plot not found for hour {hour}: {plot_path}")

        # Set PDF metadata
        d = pdf.infodict()
        d['Title'] = f'Thermosensitivity Analysis - {country} {year}'
        d['Author'] = 'Thermosensitivity Analysis Tool'
        d['Subject'] = 'Hourly thermosensitivity analysis of electricity load'
        d['Keywords'] = f'thermosensitivity, {country}, {year}, hourly'
        d['CreationDate'] = pd.Timestamp.now()

    logger.info(f"PDF report saved to {output_path}")


def analyze_thermosensitivity(
        country: str,
        year: int,
        load_path: Path,
        temp_path: Path,
        output_dir: Path,
        winter_thresholds: Optional[List[float]] = None,
        summer_thresholds: Optional[List[float]] = None,
        min_samples: int = DEFAULT_MIN_SAMPLES,
) -> Tuple[Path, Path, Path]:
    """Performs a complete thermosensitivity analysis for a country and year.

    This function orchestrates the entire analysis workflow:
    1.  Loads and aligns load and temperature data.
    2.  Iteratively finds the best temperature thresholds for each hour.
    3.  Saves the detailed regression results to a CSV file.
    4.  Generates scatter plots for each hour showing the best-fit regressions.
    5.  Creates a comprehensive PDF report summarizing the analysis.

    Args:
        country (str): The two-letter country code (e.g., 'DE', 'FR').
        year (int): The year to analyze.
        load_path (Path): Path to the load curve parquet file.
        temp_path (Path): Path to the temperature parquet file.
        output_dir (Path): Directory to save all output files.
        winter_thresholds (Optional[List[float]]): A list of winter temperature
            thresholds to test. Defaults to [5.0, 8.0, 10.0, 12.0, 15.0].
        summer_thresholds (Optional[List[float]]): A list of summer temperature
            thresholds to test. Defaults to [15.0, 18.0, 20.0, 22.0, 25.0].
        min_samples (int): The minimum number of data points required to perform
            a regression. Defaults to 30.

    Returns:
        Tuple[Path, Path, Path]: A tuple containing the paths to the output CSV
            file, the plot directory, and the final PDF report.
    """
    logger.info(f"Starting thermosensitivity analysis for {country} {year}")

    # Set default thresholds if not provided
    if winter_thresholds is None:
        winter_thresholds = [5.0, 8.0, 10.0, 12.0, 15.0]
    if summer_thresholds is None:
        summer_thresholds = [15.0, 18.0, 20.0, 22.0, 25.0]

    # Load and align data
    load_df, temp_df = load_data(load_path, temp_path)
    merged_df = align_temporal_resolution(load_df, temp_df)

    # Add hour column
    merged_df['hour'] = merged_df['timestamp'].dt.hour

    # Find best thresholds for each hour
    results_df, best_thresholds = find_best_thresholds_per_hour(
        merged_df, winter_thresholds, summer_thresholds, min_samples
    )

    # Save all results to CSV
    csv_path = (
            output_dir / f"thermosensitivity_results_{country}_{year}.csv"
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    pdf_path = output_dir / f"thermosensitivity_report_{country}_{year}.pdf"

    if len(best_thresholds) == 0:
        logger.warning(f"No results found for {country} {year}. Skipping CSV save.")
        csv_path.touch()
        pdf_path.touch()
        return csv_path, output_dir, pdf_path

    results_df.to_csv(csv_path, index=False)
    logger.info(f"Results saved to {csv_path}")

    # Create plot directory
    plot_dir = output_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)

    # Create plots for each hour with best thresholds
    for hour in range(24):
        if hour in best_thresholds:
            best_winter, best_summer = best_thresholds[hour]
            plot_path = plot_dir / f"thermosensitivity_plot_{country}_{year}_hour_{hour:02d}.png"
            plot_thermosensitivity_per_hour(
                merged_df, hour, best_winter, best_summer, country, year, plot_path, min_samples
            )

    # Create PDF report
    create_pdf_report(plot_dir, country, year, pdf_path, results_df, min_samples)

    logger.info(f"Analysis complete for {country} {year}")

    return csv_path, plot_dir, pdf_path


if __name__ == "__main__":
    # Example usage
    from demandforge import RESULTS_DIR

    country = "DE"
    year = 2020

    load_path = RESULTS_DIR / f"entsoe/entsoe_load_{country}_{year}.parquet"
    temp_path = (
            RESULTS_DIR / f"weighted_temp/weighted_temp_{country}_{year}.parquet"
    )
    output_dir = RESULTS_DIR / "thermosensitivity"

    csv_path, plot_dir, pdf_path = analyze_thermosensitivity(
        country=country,
        year=year,
        load_path=load_path,
        temp_path=temp_path,
        output_dir=output_dir,
        min_samples=30,  # You can adjust this value
    )

    print(f"Results saved to: {csv_path}")
    print(f"Plots saved to: {plot_dir}")
    print(f"PDF report saved to: {pdf_path}")