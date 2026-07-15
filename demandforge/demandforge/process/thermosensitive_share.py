"""Calculates the thermosensitive share of load curves.

This script uses the results from the thermosensitivity analysis to decompose
a total load curve into its baseload, winter thermosensitive (heating), and
summer thermosensitive (cooling) components.
"""
import logging
from pathlib import Path
from typing import Dict, Tuple
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages

# Set up logging
logger = logging.getLogger(__name__)

# Set style for plots
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (14, 8)
plt.rcParams['font.size'] = 10

# Minimum R² threshold for considering thermosensitivity
R2_THRESHOLD = 0.1


def load_thermosensitivity_results(csv_path: str) -> pd.DataFrame:
    """Loads thermosensitivity results from a CSV file.

    Args:
        csv_path (str): Path to the CSV file containing thermosensitivity results.

    Returns:
        pd.DataFrame: DataFrame with thermosensitivity parameters per hour.
    """
    logger.info(f"Loading thermosensitivity results from {csv_path}")
    df = pd.read_csv(csv_path)
    logger.info(f"Loaded {len(df)} rows with columns: {df.columns.tolist()}")
    return df


def load_load_and_temp_data(load_path: str, temp_path: str) -> pd.DataFrame:
    """Loads and merges load and temperature data.

    Args:
        load_path (str): Path to the load data parquet file.
        temp_path (str): Path to the temperature data parquet file.

    Returns:
        pd.DataFrame: Merged DataFrame with timestamp, load, and temperature.
    """
    logger.info(f"Loading load data from {load_path}")
    load_df = pd.read_parquet(load_path)

    logger.info(f"Loading temperature data from {temp_path}")
    temp_df = pd.read_parquet(temp_path)

    # Ensure timestamp columns are datetime
    if 'timestamp' in load_df.columns:
        load_df['timestamp'] = pd.to_datetime(load_df['timestamp'])
    if 'timestamp' in temp_df.columns:
        temp_df['timestamp'] = pd.to_datetime(temp_df['timestamp'])

    # Merge on timestamp
    # Merge on timestamp
    merged = pd.merge(
        load_df,
        temp_df,
        on="timestamp",
        how="outer",
    )
    merged['load_mw'] = merged['load_mw'].interpolate("linear")
    logger.info(f"Merged data: {len(merged)} rows")

    return merged


def define_seasons(
    data: pd.DataFrame,
    weekly_temp_threshold: float = 12.0,
    rolling_window: int = 4
) -> pd.DataFrame:
    """Defines winter and summer seasons based on a rolling average of weekly temperatures.

    This method helps prevent rapid switching between seasons by using a smoothed
    temperature profile.

    Args:
        data (pd.DataFrame): DataFrame with 'timestamp' and 'temperature' columns.
        weekly_temp_threshold (float): The temperature threshold (in Celsius) to
            distinguish winter from summer. Defaults to 12.0.
        rolling_window (int): The number of weeks for the rolling average of
            temperature. Defaults to 4.

    Returns:
        pd.DataFrame: The input DataFrame with an added 'season' column
            containing 'winter' or 'summer'.
    """
    logger.info(f"Defining seasons with a weekly temperature threshold of {weekly_temp_threshold}°C "
                f"and a {rolling_window}-week rolling average.")

    # Ensure timestamp is the index
    data = data.set_index('timestamp')

    # Calculate weekly average temperature
    weekly_temp = data['temperature'].resample('W-MON').mean()

    # Calculate a rolling average of the weekly temperature to smooth it
    smoothed_weekly_temp = weekly_temp.rolling(window=rolling_window, min_periods=1).mean()

    # Create a 'season' series based on the smoothed weekly temperature
    weekly_season = pd.Series('summer', index=smoothed_weekly_temp.index)
    weekly_season[smoothed_weekly_temp < weekly_temp_threshold] = 'winter'

    # Upsample the weekly season back to the original frequency of the data
    # Use forward fill to propagate the season definition through the week
    data['season'] = weekly_season.resample(data.index.freq or 'H').ffill()

    # Handle any remaining NaNs at the beginning by backfilling
    data['season'] = data['season'].bfill()

    logger.info(f"Seasons defined: "
                f"{(data['season'] == 'winter').sum() / len(data) * 100:.1f}% winter, "
                f"{(data['season'] == 'summer').sum() / len(data) * 100:.1f}% summer.")

    return data.reset_index()


def calculate_thermosensitive_share(
        data: pd.DataFrame,
        thermo_params: pd.DataFrame,
        r2_threshold: float = R2_THRESHOLD
) -> pd.DataFrame:
    """Calculates the thermosensitive share of the load for each timestamp.

    This function decomposes the total load into three components:
    1.  Winter thermosensitive load (heating)
    2.  Summer thermosensitive load (cooling)
    3.  Baseload (residual load)

    The baseload is calculated as:
    `baseload = total_load - winter_load - summer_load`

    A proportional adjustment is made if the calculated baseload is negative.

    Args:
        data (pd.DataFrame): DataFrame containing 'timestamp', 'load_mw', and
            'temperature' columns.
        thermo_params (pd.DataFrame): DataFrame with thermosensitivity parameters
            for each hour, as generated by `thermosensitivity.py`.
        r2_threshold (float): Minimum R-squared value from the regression to
            consider a given hour's load as thermosensitive.

    Returns:
        pd.DataFrame: A DataFrame with the decomposed load components:
            'timestamp', 'total_load', 'baseload',
            'winter_thermosensitive_load', and 'summer_thermosensitive_load'.
    """
    logger.info("Calculating thermosensitive share...")

    # Define seasons based on weekly average temperature to avoid back and forth
    data = define_seasons(data)

    # Add hour column to data
    data = data.copy()
    data['hour'] = data['timestamp'].dt.hour

    # Identify the column names
    load_col = 'load_mw'
    temp_col = 'temperature'

    # Initialize output columns
    data['winter_thermosensitive_load'] = 0.0
    data['summer_thermosensitive_load'] = 0.0

    # Process each hour
    for hour in range(24):
        # Get parameters for this hour
        hour_params = thermo_params[(thermo_params['hour'] == hour) & (thermo_params['best_regression'])]

        if len(hour_params) == 0:
            logger.warning(f"No parameters found for hour {hour}")
            continue

        winter_hour_params = hour_params[hour_params['season'] == "winter"]
        summer_hour_params = hour_params[hour_params['season'] == "summer"]

        # Get data for this hour
        hour_mask = data['hour'] == hour

        # Extract parameters
        winter_slope = winter_hour_params["thermosensitivity_mw_per_c"].values[0]
        summer_slope = summer_hour_params["thermosensitivity_mw_per_c"].values[0]
        winter_threshold = winter_hour_params["threshold"].values[0]
        summer_threshold = summer_hour_params["threshold"].values[0]
        winter_r2 = winter_hour_params["r_squared"].values[0]
        summer_r2 = summer_hour_params["r_squared"].values[0]

        # Calculate winter thermosensitive load (heating)
        # Only apply if R² is above threshold and it's the winter season
        if winter_r2 >= r2_threshold and winter_slope != 0:
            winter_season_mask = data['season'] == 'winter'
            winter_mask = hour_mask & (data[temp_col] < winter_threshold) & winter_season_mask
            temp_diff =  data.loc[winter_mask, temp_col] - winter_threshold
            data.loc[winter_mask, 'winter_thermosensitive_load'] = winter_slope * temp_diff

        # Calculate summer thermosensitive load (cooling)
        # Only apply if R² is above threshold and it's the summer season
        if summer_r2 >= r2_threshold and summer_slope != 0:
            summer_season_mask = data['season'] == 'summer'
            summer_mask = hour_mask & (data[temp_col] > summer_threshold) & summer_season_mask
            temp_diff = data.loc[summer_mask, temp_col] - summer_threshold
            data.loc[summer_mask, 'summer_thermosensitive_load'] = summer_slope * temp_diff

    # Ensure no negative values for thermosensitive loads
    data['winter_thermosensitive_load'] = data['winter_thermosensitive_load'].clip(lower=0)
    data['summer_thermosensitive_load'] = data['summer_thermosensitive_load'].clip(lower=0)

    # Calculate baseload as residual
    # baseload = total_load - winter_thermosensitive - summer_thermosensitive
    data['baseload'] = (
            data[load_col] -
            data['winter_thermosensitive_load'] -
            data['summer_thermosensitive_load']
    )

    # Ensure baseload is not negative (can happen if model overestimates thermosensitivity)
    # In such cases, proportionally reduce thermosensitive loads
    negative_baseload_mask = data['baseload'] < 0
    if negative_baseload_mask.any():
        logger.warning(
            f"Found {negative_baseload_mask.sum()} timestamps with negative baseload. "
            "Adjusting thermosensitive loads proportionally."
        )

        # For negative baseload cases, scale down thermosensitive loads
        for idx in data[negative_baseload_mask].index:
            total_load = data.loc[idx, load_col]
            winter_load = data.loc[idx, 'winter_thermosensitive_load']
            summer_load = data.loc[idx, 'summer_thermosensitive_load']
            thermo_total = winter_load + summer_load

            if thermo_total > 0:
                # Scale down proportionally to match total load
                scale_factor = total_load / thermo_total
                data.loc[idx, 'winter_thermosensitive_load'] = winter_load * scale_factor
                data.loc[idx, 'summer_thermosensitive_load'] = summer_load * scale_factor
                data.loc[idx, 'baseload'] = 0.0
            else:
                # If no thermosensitive load, all is baseload
                data.loc[idx, 'baseload'] = total_load

    # Select and rename columns
    result = data[[
        'timestamp',
        load_col,
        'winter_thermosensitive_load',
        'summer_thermosensitive_load',
        'baseload'
    ]].copy()
    result.rename(columns={load_col: 'total_load'}, inplace=True)

    # Reorder columns for clarity
    result = result[[
        'timestamp',
        'total_load',
        'baseload',
        'winter_thermosensitive_load',
        'summer_thermosensitive_load'
    ]]

    logger.info("Thermosensitive share calculation complete")
    logger.info(f"Average baseload: {result['baseload'].mean():.2f} MW")
    logger.info(f"Average winter thermosensitive load: {result['winter_thermosensitive_load'].mean():.2f} MW")
    logger.info(f"Average summer thermosensitive load: {result['summer_thermosensitive_load'].mean():.2f} MW")

    return result


def aggregate_to_resolution(df: pd.DataFrame, resolution: str) -> pd.DataFrame:
    """Aggregates data to a specified temporal resolution.

    Args:
        df (pd.DataFrame): DataFrame with a 'timestamp' column and load columns.
        resolution (str): The target resolution, e.g., 'H' (hourly), 'D' (daily),
            'W' (weekly), 'ME' (month-end).

    Returns:
        pd.DataFrame: The aggregated DataFrame.
    """
    df = df.copy()
    df.set_index('timestamp', inplace=True)

    # Resample and take mean
    resampled = df.resample(resolution).mean()
    resampled.reset_index(inplace=True)

    return resampled


def plot_stacked_area(df: pd.DataFrame, resolution_name: str, ax=None) -> plt.Figure:
    """Creates a stacked area plot of the decomposed load components.

    Args:
        df (pd.DataFrame): DataFrame containing 'timestamp', 'baseload',
            'winter_thermosensitive_load', and 'summer_thermosensitive_load'.
        resolution_name (str): Name of the temporal resolution for the plot title
            (e.g., 'Hourly', 'Daily').
        ax (plt.Axes, optional): A matplotlib axes object to plot on. If None,
            a new figure and axes are created.

    Returns:
        plt.Figure: The matplotlib figure object.
    """
    if ax is None:
        fig, ax = plt.subplots(figsize=(14, 6))
    else:
        fig = ax.get_figure()

    # Prepare data
    timestamps = df['timestamp']
    baseload = df['baseload']
    winter_load = df['winter_thermosensitive_load']
    summer_load = df['summer_thermosensitive_load']

    # Create stacked area plot
    ax.fill_between(timestamps, 0, baseload,
                    label='Baseload', color='#2E86AB', alpha=0.7)
    ax.fill_between(timestamps, baseload, baseload + winter_load,
                    label='Winter Thermosensitive (Heating)', color='#A23B72', alpha=0.7)
    ax.fill_between(timestamps, baseload + winter_load,
                    baseload + winter_load + summer_load,
                    label='Summer Thermosensitive (Cooling)', color='#F18F01', alpha=0.7)

    # Add total load line for comparison if available
    if 'total_load' in df.columns:
        ax.plot(timestamps, df['total_load'],
                label='Actual Total Load', color='black', linewidth=1.5, alpha=0.8, linestyle='--')

    ax.set_xlabel('Time')
    ax.set_ylabel('Load (MW)')
    ax.set_title(f'Load Decomposition - {resolution_name}')
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)

    # Format x-axis
    if resolution_name == 'Hourly' or 'First' in resolution_name:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    elif resolution_name == 'Daily':
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    elif resolution_name == 'Weekly':
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    else:  # Monthly
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()

    return fig


def calculate_summary_statistics(df: pd.DataFrame, thermo_params: pd.DataFrame, r2_threshold: float) -> Dict:
    """Calculates summary statistics for the thermosensitive load analysis.

    Args:
        df (pd.DataFrame): DataFrame with the decomposed thermosensitive load
            components.
        thermo_params (pd.DataFrame): DataFrame with the hourly thermosensitivity
            parameters.
        r2_threshold (float): The R-squared threshold used in the analysis.

    Returns:
        Dict: A dictionary containing a comprehensive set of summary statistics,
            including annual energy, peak loads, average loads, and shares.
    """
    logger.info("Calculating summary statistics...")

    # Average thermosensitivity (MW/°C)
    winter_mask = (thermo_params['r_squared'] >= r2_threshold) & (thermo_params['season'] == 'winter')
    summer_mask = (thermo_params['r_squared'] >= r2_threshold) & (thermo_params['season'] == 'summer')
    winter_slopes = thermo_params[winter_mask]['thermosensitivity_mw_per_c']
    summer_slopes = thermo_params[summer_mask]['thermosensitivity_mw_per_c']

    avg_winter_sensitivity = winter_slopes.mean() if len(winter_slopes) > 0 else 0
    avg_summer_sensitivity = summer_slopes.mean() if len(summer_slopes) > 0 else 0

    # Annual energy (GWh)
    hours_in_year = len(df)
    baseload_energy = df['baseload'].sum() / 1000  # Convert MWh to GWh
    winter_energy = df['winter_thermosensitive_load'].sum() / 1000
    summer_energy = df['summer_thermosensitive_load'].sum() / 1000
    total_energy = df['total_load'].sum() / 1000 if 'total_load' in df.columns else 0

    # Thermosensitive share (%)
    thermosensitive_energy = winter_energy + summer_energy
    thermosensitive_share = (thermosensitive_energy / total_energy * 100) if total_energy > 0 else 0
    baseload_share = (baseload_energy / total_energy * 100) if total_energy > 0 else 0
    winter_share = (winter_energy / total_energy * 100) if total_energy > 0 else 0
    summer_share = (summer_energy / total_energy * 100) if total_energy > 0 else 0

    # Peak loads (MW)
    baseload_peak = df['baseload'].max()
    winter_peak = df['winter_thermosensitive_load'].max()
    summer_peak = df['summer_thermosensitive_load'].max()
    total_peak = df['total_load'].max() if 'total_load' in df.columns else 0

    # Average loads (MW)
    baseload_avg = df['baseload'].mean()
    winter_avg = df['winter_thermosensitive_load'].mean()
    summer_avg = df['summer_thermosensitive_load'].mean()
    total_avg = df['total_load'].mean() if 'total_load' in df.columns else 0

    # Count of hours with thermosensitivity
    winter_hours = (df['winter_thermosensitive_load'] > 0).sum()
    summer_hours = (df['summer_thermosensitive_load'] > 0).sum()

    summary = {
        'avg_winter_sensitivity_mw_per_c': float(avg_winter_sensitivity),
        'avg_summer_sensitivity_mw_per_c': float(avg_summer_sensitivity),
        'baseload_annual_energy_gwh': float(baseload_energy),
        'winter_thermosensitive_annual_energy_gwh': float(winter_energy),
        'summer_thermosensitive_annual_energy_gwh': float(summer_energy),
        'thermosensitive_annual_energy_gwh': float(thermosensitive_energy),
        'total_annual_energy_gwh': float(total_energy),
        'baseload_share_percent': float(baseload_share),
        'winter_share_percent': float(winter_share),
        'summer_share_percent': float(summer_share),
        'thermosensitive_share_percent': float(thermosensitive_share),
        'baseload_peak_mw': float(baseload_peak),
        'winter_thermosensitive_peak_mw': float(winter_peak),
        'summer_thermosensitive_peak_mw': float(summer_peak),
        'total_peak_mw': float(total_peak),
        'baseload_avg_mw': float(baseload_avg),
        'winter_thermosensitive_avg_mw': float(winter_avg),
        'summer_thermosensitive_avg_mw': float(summer_avg),
        'total_avg_mw': float(total_avg),
        'winter_thermosensitive_hours': int(winter_hours),
        'summer_thermosensitive_hours': int(summer_hours),
        'total_hours': int(hours_in_year),
        'r2_threshold': r2_threshold
    }

    logger.info("Summary statistics calculated")

    return summary


def create_pdf_report(
        df_hourly: pd.DataFrame,
        df_daily: pd.DataFrame,
        df_weekly: pd.DataFrame,
        df_monthly: pd.DataFrame,
        summary: Dict,
        output_path: str,
        country: str,
        year: int
):
    """Creates a multi-page PDF report with plots and summary statistics.

    The report includes:
    - A summary page with key statistics.
    - Stacked area plots for typical winter and summer weeks.
    - Stacked area plots for daily, weekly, and monthly resolutions.
    - Pie and bar charts for energy, peak load, and average load breakdowns.

    Args:
        df_hourly (pd.DataFrame): Hourly resolution data.
        df_daily (pd.DataFrame): Daily resolution data.
        df_weekly (pd.DataFrame): Weekly resolution data.
        df_monthly (pd.DataFrame): Monthly resolution data.
        summary (Dict): Dictionary with summary statistics.
        output_path (str): Path to save the PDF report.
        country (str): Country code for the report title.
        year (int): Year of analysis for the report title.
    """
    logger.info(f"Creating PDF report: {output_path}")

    with PdfPages(output_path) as pdf:
        # Page 1: Title and summary statistics
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle(f'Thermosensitive Load Share Analysis\n{country} - {year}',
                     fontsize=20, fontweight='bold')

        # Add summary statistics as text
        text_str = f"""
Summary Statistics
{'='*80}

Thermosensitivity Parameters:
    • Average Winter Sensitivity: {summary['avg_winter_sensitivity_mw_per_c']:.2f} MW/°C
    • Average Summer Sensitivity: {summary['avg_summer_sensitivity_mw_per_c']:.2f} MW/°C

Annual Energy Breakdown:
    • Total Annual Energy: {summary['total_annual_energy_gwh']:.2f} GWh (100%)
    • Baseload Energy: {summary['baseload_annual_energy_gwh']:.2f} GWh ({summary['baseload_share_percent']:.1f}%)
    • Winter Thermosensitive Energy: {summary['winter_thermosensitive_annual_energy_gwh']:.2f} GWh ({summary['winter_share_percent']:.1f}%)
    • Summer Thermosensitive Energy: {summary['summer_thermosensitive_annual_energy_gwh']:.2f} GWh ({summary['summer_share_percent']:.1f}%)
    • Total Thermosensitive Energy: {summary['thermosensitive_annual_energy_gwh']:.2f} GWh ({summary['thermosensitive_share_percent']:.1f}%)

Peak Loads:
    • Total Peak: {summary['total_peak_mw']:.2f} MW
    • Baseload Peak: {summary['baseload_peak_mw']:.2f} MW
    • Winter Thermosensitive Peak: {summary['winter_thermosensitive_peak_mw']:.2f} MW
    • Summer Thermosensitive Peak: {summary['summer_thermosensitive_peak_mw']:.2f} MW

Average Loads:
    • Total Average: {summary['total_avg_mw']:.2f} MW
    • Baseload Average: {summary['baseload_avg_mw']:.2f} MW
    • Winter Thermosensitive Average: {summary['winter_thermosensitive_avg_mw']:.2f} MW
    • Summer Thermosensitive Average: {summary['summer_thermosensitive_avg_mw']:.2f} MW

Duration:
    • Total Hours: {summary['total_hours']} hours
    • Winter Thermosensitive Hours: {summary['winter_thermosensitive_hours']} hours ({summary['winter_thermosensitive_hours']/summary['total_hours']*100:.1f}%)
    • Summer Thermosensitive Hours: {summary['summer_thermosensitive_hours']} hours ({summary['summer_thermosensitive_hours']/summary['total_hours']*100:.1f}%)

Analysis Parameters:
    • Minimum R² Threshold: {summary['r2_threshold']}
    • Only hours with R² > {summary['r2_threshold']} are considered thermosensitive
    • Baseload = Total Load - Winter Thermosensitive - Summer Thermosensitive
        """

        fig.text(0.1, 0.5, text_str, fontsize=10, family='monospace',
                 verticalalignment='center')

        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        df_hourly['week'] = df_hourly['timestamp'].dt.isocalendar().week
        winter_week_data = df_hourly[df_hourly['week'] == 3].copy()

        fig, ax = plt.subplots(figsize=(14, 8))
        plot_stacked_area(winter_week_data, 'Winter Week (Hourly)', ax=ax)
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        summer_week_data = df_hourly[df_hourly['week'] == 25].copy()

        fig, ax = plt.subplots(figsize=(14, 8))
        plot_stacked_area(summer_week_data, 'Summer Week (Hourly)', ax=ax)
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # Page 4: Daily stacked area plot
        fig, ax = plt.subplots(figsize=(14, 8))
        plot_stacked_area(df_daily, 'Daily', ax=ax)
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # Page 5: Weekly stacked area plot
        fig, ax = plt.subplots(figsize=(14, 8))
        plot_stacked_area(df_weekly, 'Weekly', ax=ax)
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # Page 6: Monthly stacked area plot
        fig, ax = plt.subplots(figsize=(14, 8))
        plot_stacked_area(df_monthly, 'Monthly', ax=ax)
        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # Page 7: Energy breakdown pie chart and bar charts
        fig = plt.figure(figsize=(14, 10))
        gs = fig.add_gridspec(2, 2, hspace=0.3, wspace=0.3)

        # Energy distribution pie chart
        ax1 = fig.add_subplot(gs[0, 0])
        energy_data = [
            summary['baseload_annual_energy_gwh'],
            summary['winter_thermosensitive_annual_energy_gwh'],
            summary['summer_thermosensitive_annual_energy_gwh']
        ]
        energy_labels = ['Baseload', 'Winter\nThermosensitive', 'Summer\nThermosensitive']
        colors = ['#2E86AB', '#A23B72', '#F18F01']

        ax1.pie(energy_data, labels=energy_labels, autopct='%1.1f%%',
                colors=colors, startangle=90)
        ax1.set_title('Annual Energy Distribution')

        # Peak load comparison
        ax2 = fig.add_subplot(gs[0, 1])
        peak_data = [
            summary['baseload_peak_mw'],
            summary['winter_thermosensitive_peak_mw'],
            summary['summer_thermosensitive_peak_mw']
        ]
        peak_labels = ['Baseload', 'Winter\nThermosens.', 'Summer\nThermosens.']
        x_pos = np.arange(len(peak_labels))
        bars = ax2.bar(x_pos, peak_data, color=colors, alpha=0.7)
        ax2.set_xticks(x_pos)
        ax2.set_xticklabels(peak_labels)
        ax2.set_ylabel('Peak Load (MW)')
        ax2.set_title('Peak Load Comparison')
        ax2.grid(True, alpha=0.3, axis='y')

        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height,
                     f'{height:.0f}',
                     ha='center', va='bottom', fontsize=9)

        # Average load comparison
        ax3 = fig.add_subplot(gs[1, 0])
        avg_data = [
            summary['baseload_avg_mw'],
            summary['winter_thermosensitive_avg_mw'],
            summary['summer_thermosensitive_avg_mw']
        ]
        bars = ax3.bar(x_pos, avg_data, color=colors, alpha=0.7)
        ax3.set_xticks(x_pos)
        ax3.set_xticklabels(peak_labels)
        ax3.set_ylabel('Average Load (MW)')
        ax3.set_title('Average Load Comparison')
        ax3.grid(True, alpha=0.3, axis='y')

        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            ax3.text(bar.get_x() + bar.get_width()/2., height,
                     f'{height:.0f}',
                     ha='center', va='bottom', fontsize=9)

        # Duration comparison
        ax4 = fig.add_subplot(gs[1, 1])
        duration_data = [
            summary['total_hours'],
            summary['winter_thermosensitive_hours'],
            summary['summer_thermosensitive_hours']
        ]
        duration_labels = ['Total\nHours', 'Winter\nThermosens.\nHours', 'Summer\nThermosens.\nHours']
        duration_colors = ['#555555', '#A23B72', '#F18F01']
        x_pos_dur = np.arange(len(duration_labels))
        bars = ax4.bar(x_pos_dur, duration_data, color=duration_colors, alpha=0.7)
        ax4.set_xticks(x_pos_dur)
        ax4.set_xticklabels(duration_labels)
        ax4.set_ylabel('Hours')
        ax4.set_title('Duration Comparison')
        ax4.grid(True, alpha=0.3, axis='y')

        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            ax4.text(bar.get_x() + bar.get_width()/2., height,
                     f'{int(height)}',
                     ha='center', va='bottom', fontsize=9)

        pdf.savefig(fig, bbox_inches='tight')
        plt.close(fig)

        # Set PDF metadata
        d = pdf.infodict()
        d['Title'] = f'Thermosensitive Load Share Analysis - {country} {year}'
        d['Author'] = 'DemandForge'
        d['Subject'] = 'Electricity Load Thermosensitivity Analysis'
        d['Keywords'] = 'Thermosensitivity, Load, Electricity, Heating, Cooling'
        d['CreationDate'] = pd.Timestamp.now()

    logger.info(f"PDF report saved to {output_path}")


def process_thermosensitive_share(
        thermo_csv: str,
        load_parquet: str,
        temp_parquet: str,
        output_parquet: str,
        output_pdf: str,
        country: str,
        year: int,
        r2_threshold: float = R2_THRESHOLD
) -> Tuple[pd.DataFrame, Dict]:
    """Main processing function to calculate thermosensitive share and create a report.

    This function orchestrates the entire workflow:
    1. Loads all necessary input data.
    2. Calculates the thermosensitive load decomposition.
    3. Saves the results to a parquet file.
    4. Generates summary statistics.
    5. Creates a comprehensive PDF report.

    Args:
        thermo_csv (str): Path to thermosensitivity results CSV.
        load_parquet (str): Path to load data parquet.
        temp_parquet (str): Path to temperature data parquet.
        output_parquet (str): Path to save the output parquet file.
        output_pdf (str): Path to save the output PDF report.
        country (str): Country code for the analysis.
        year (int): Year of the analysis.
        r2_threshold (float): Minimum R-squared threshold for considering
            thermosensitivity.

    Returns:
        Tuple[pd.DataFrame, Dict]: A tuple containing the result DataFrame with
            the decomposed load and the summary statistics dictionary.
    """
    # Load data
    try:
        thermo_params = load_thermosensitivity_results(thermo_csv)
    except:
        logger.warning(f"Could not load thermosensitivity results from {thermo_csv}. Skipping analysis.")
        Path(output_parquet).parent.mkdir(parents=True, exist_ok=True)
        Path(output_parquet).touch()
        Path(output_pdf).parent.mkdir(parents=True, exist_ok=True)
        Path(output_pdf).touch()
        return pd.DataFrame(), {}

    merged_data = load_load_and_temp_data(load_parquet, temp_parquet)

    # Calculate thermosensitive share
    result_df = calculate_thermosensitive_share(merged_data, thermo_params, r2_threshold)

    # Save to parquet
    logger.info(f"Saving results to {output_parquet}")
    Path(output_parquet).parent.mkdir(parents=True, exist_ok=True)
    result_df.to_parquet(output_parquet, index=False)

    # Aggregate to different resolutions
    df_hourly = result_df.copy()
    df_daily = aggregate_to_resolution(result_df, 'D')
    df_weekly = aggregate_to_resolution(result_df, 'W')
    df_monthly = aggregate_to_resolution(result_df, 'ME')

    # Calculate summary statistics
    summary = calculate_summary_statistics(result_df, thermo_params, r2_threshold)

    # Create PDF report
    Path(output_pdf).parent.mkdir(parents=True, exist_ok=True)
    create_pdf_report(
        df_hourly=df_hourly,
        df_daily=df_daily,
        df_weekly=df_weekly,
        df_monthly=df_monthly,
        summary=summary,
        output_path=output_pdf,
        country=country,
        year=year
    )

    logger.info("Analysis complete!")
    logger.info(f"Results saved to: {output_parquet}")
    logger.info(f"Report saved to: {output_pdf}")

    # Print summary
    print("\n" + "="*80)
    print(f"THERMOSENSITIVE LOAD SHARE ANALYSIS - {country} {year}")
    print("="*80)
    print(f"\nAverage Winter Sensitivity: {summary['avg_winter_sensitivity_mw_per_c']:.2f} MW/°C")
    print(f"Average Summer Sensitivity: {summary['avg_summer_sensitivity_mw_per_c']:.2f} MW/°C")
    print(f"\nTotal Annual Energy: {summary['total_annual_energy_gwh']:.2f} GWh (100%)")
    print(f"  • Baseload: {summary['baseload_annual_energy_gwh']:.2f} GWh ({summary['baseload_share_percent']:.1f}%)")
    print(f"  • Winter Thermosensitive: {summary['winter_thermosensitive_annual_energy_gwh']:.2f} GWh ({summary['winter_share_percent']:.1f}%)")
    print(f"  • Summer Thermosensitive: {summary['summer_thermosensitive_annual_energy_gwh']:.2f} GWh ({summary['summer_share_percent']:.1f}%)")
    print(f"\nThermosensitive Share: {summary['thermosensitive_share_percent']:.2f}%")
    print("="*80 + "\n")

    return result_df, summary
