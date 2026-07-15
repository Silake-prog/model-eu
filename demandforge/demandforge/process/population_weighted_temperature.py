

"""Script to calculate population-weighted temperature by country.

Combines country borders, ERA5 temperature data, and GHSL population data.
Outputs as parquet file with datetime and temperature columns.
"""

import xarray as xr
import rasterio
import geopandas as gpd
import numpy as np
import pandas as pd
from pathlib import Path
from typing import List, Union, Optional
import logging
from rasterio.mask import mask
import tempfile
from rasterio.warp import reproject, Resampling

from tqdm import tqdm

logger = logging.getLogger(__name__)


def select_closest_population_year(target_year: int, available_years: List[int]) -> int:
    """Select the closest population year from available years.

    Args:
        target_year (int): Target year for temperature data.
        available_years (list of int): List of available population data years.

    Returns:
        int: Closest available population year.
    """
    if not available_years:
        raise ValueError("No population years available")

    # Find the year with minimum absolute difference
    closest_year = min(available_years, key=lambda y: abs(y - target_year))

    logger.info(f"Selected population year {closest_year} for temperature year {target_year}")
    logger.info(f"  Time difference: {abs(closest_year - target_year)} years")

    return closest_year


def clip_raster_to_country(
        raster_path: Path,
        country_gdf: gpd.GeoDataFrame,
        output_path: Optional[Path] = None
) -> Path:
    """Clip a raster to country boundaries.

    Args:
        raster_path (Path): Path to the raster file.
        country_gdf (GeoDataFrame): GeoDataFrame with country geometry.
        output_path (Path, optional): Path to save clipped raster. If None, uses temporary file.

    Returns:
        Path: Path to the clipped raster.
    """
    if output_path is None:
        temp_file = tempfile.NamedTemporaryFile(suffix='.tif', delete=False)
        output_path = Path(temp_file.name)
        temp_file.close()

    with rasterio.open(raster_path) as src:
        # Reproject country geometry to match raster CRS
        country_gdf_reproj = country_gdf.to_crs(src.crs)

        # Clip the raster
        out_image, out_transform = mask(
            src,
            country_gdf_reproj.geometry,
            crop=True,
            filled=True,
            nodata=0
        )

        # Update metadata
        out_meta = src.meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform,
            "nodata": 0
        })

        # Write clipped raster
        with rasterio.open(output_path, "w", **out_meta) as dest:
            dest.write(out_image)

    logger.info(f"Clipped raster saved to {output_path}")
    return output_path


def aggregate_population_to_temperature_grid(
        pop_data: np.ndarray,
        pop_transform: rasterio.Affine,
        temp_lons: np.ndarray,
        temp_lats: np.ndarray
) -> np.ndarray:
    """Aggregate population data to temperature grid using reprojection.

    Args:
        pop_data (np.ndarray): Population raster data.
        pop_transform (rasterio.Affine): Transform of population raster.
        temp_lons (np.ndarray): Longitude coordinates of temperature grid.
        temp_lats (np.ndarray): Latitude coordinates of temperature grid.

    Returns:
        np.ndarray: Population aggregated to temperature grid.
    """
    # Create target transform for temperature grid
    # Temperature grid is usually regular
    lon_res = np.abs(temp_lons[1] - temp_lons[0])
    lat_res = np.abs(temp_lats[1] - temp_lats[0])

    # Create transform for temperature grid (top-left corner)
    temp_transform = rasterio.transform.from_bounds(
        west=temp_lons.min() - lon_res / 2,
        south=temp_lats.min() - lat_res / 2,
        east=temp_lons.max() + lon_res / 2,
        north=temp_lats.max() + lat_res / 2,
        width=len(temp_lons),
        height=len(temp_lats)
    )

    # Initialize output array
    aggregated_pop = np.zeros((len(temp_lats), len(temp_lons)), dtype=np.float64)

    # Reproject population to temperature grid
    reproject(
        source=pop_data,
        destination=aggregated_pop,
        src_transform=pop_transform,
        src_crs='EPSG:4326',  # Assuming WGS84
        dst_transform=temp_transform,
        dst_crs='EPSG:4326',
        resampling=Resampling.sum  # Sum population within each temp grid cell
    )

    return aggregated_pop


def calculate_population_weighted_temperature(
        country_code: str,
        year: int,
        population_years: List[int],
        borders_path: Union[str, Path],
        era5_path: Union[str, Path],
        ghsl_base_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        temp_var_name: str = 't2m',
        chunk_size: int = 500
) -> pd.DataFrame:
    """Calculate population-weighted temperature for a country.

    Args:
        country_code (str): Country code (e.g., 'DE', 'FR', 'ES').
        year (int): Year for temperature data.
        population_years (list of int): List of available population years.
        borders_path (str | Path): Path to country borders GeoPackage.
        era5_path (str | Path): Path to ERA5 temperature NetCDF file.
        ghsl_base_path (str | Path): Base path for GHSL population files (without year suffix).
            Expected format: {ghsl_base_path}/ghsl_pop_europe_{year}.tif
        output_path (str | Path, optional): Path to save output parquet file.
        temp_var_name (str, optional): Name of temperature variable in ERA5 dataset. Defaults to 't2m'.
        chunk_size (int, optional): Number of time steps to process at once (for memory efficiency).
            Defaults to 500.

    Returns:
        pd.DataFrame: DataFrame with columns: datetime, temperature.
    """
    borders_path = Path(borders_path)
    era5_path = Path(era5_path)
    ghsl_base_path = Path(ghsl_base_path)

    # Select closest population year
    pop_year = select_closest_population_year(year, population_years)

    # Construct population file path
    if ghsl_base_path.is_dir():
        pop_path = ghsl_base_path / f"ghsl_pop_europe_{pop_year}.tif"
    else:
        # Assume ghsl_base_path is a pattern that needs year substitution
        pop_path = Path(str(ghsl_base_path).replace("{population_year}", str(pop_year)))

    if not pop_path.exists():
        raise FileNotFoundError(f"Population raster not found: {pop_path}")

    logger.info(f"Processing country: {country_code}")
    logger.info(f"Temperature year: {year}")
    logger.info(f"Population year: {pop_year}")
    logger.info(f"Borders file: {borders_path}")
    logger.info(f"ERA5 file: {era5_path}")
    logger.info(f"Population file: {pop_path}")
    country_id_replace_dict = {
        "GR": "EL"
    }

    for c_id, c_id_replace in country_id_replace_dict.items():
        if country_code == c_id:
            logger.info(f"replacing country code {country_code} with {c_id_replace}")
            country_code = c_id_replace
            break
    # Load country borders
    logger.info("Loading country borders...")
    gdf = gpd.read_file(borders_path)
    country_gdf = gdf[gdf['CNTR_ID'] == country_code].copy()

    if country_gdf.empty:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.warning(f"No data for country {country_code}. Saving empty DataFrame to {output_path}")

    # Load ERA5 temperature data
    logger.info("Loading ERA5 temperature data...")
    temp_ds = xr.open_dataset(era5_path)

    time_coord = 'valid_time'

    # Check if temperature variable exists
    if temp_var_name not in temp_ds:
        available_vars = list(temp_ds.data_vars)
        raise ValueError(
            f"Temperature variable '{temp_var_name}' not found. "
            f"Available variables: {available_vars}"
        )

    # Clip population raster to country
    logger.info("Clipping population raster to country boundaries...")
    pop_clipped_path = clip_raster_to_country(pop_path, country_gdf)

    # Load clipped population data
    with rasterio.open(pop_clipped_path) as pop_src:
        pop_data = pop_src.read(1)
        pop_transform = pop_src.transform
        pop_crs = pop_src.crs

    # Set nodata and negative values to 0
    pop_data = np.where(pop_data < 0, 0, pop_data)
    pop_data = np.where(np.isnan(pop_data), 0, pop_data)

    total_population_original = pop_data.sum()
    logger.info(f"Total population (high-res): {total_population_original:,.0f}")

    if total_population_original == 0:
        raise ValueError(f"No population data found for country {country_code}")

    # Get temperature grid coordinates
    logger.info("Extracting temperature grid coordinates...")
    temp_lons = temp_ds['longitude'].values
    temp_lats = temp_ds['latitude'].values

    logger.info(f"Temperature grid shape: {len(temp_lats)} x {len(temp_lons)}")
    logger.info(f"Population grid shape: {pop_data.shape}")

    # Aggregate population to temperature grid
    logger.info("Aggregating population to temperature grid...")
    aggregated_pop = aggregate_population_to_temperature_grid(
        pop_data=pop_data,
        pop_transform=pop_transform,
        temp_lons=temp_lons,
        temp_lats=temp_lats
    )

    # Verify total population is preserved
    total_population = aggregated_pop.sum()
    logger.info(f"Total population (aggregated): {total_population:,.0f}")
    logger.info(f"Population preservation: {total_population / total_population_original * 100:.2f}%")

    # Flatten for easier processing
    aggregated_pop_flat = aggregated_pop.flatten()
    pop_mask = aggregated_pop_flat > 0
    pop_weights = aggregated_pop_flat[pop_mask]

    logger.info(f"Temperature cells with population: {pop_mask.sum()} / {len(pop_mask)}")

    # Get total number of time steps
    time_steps = len(temp_ds[time_coord])
    logger.info(f"Total time steps: {time_steps}")

    # Initialize array to store weighted temperature
    weighted_temp = np.zeros(time_steps)

    # Process in chunks to avoid memory issues
    n_chunks = int(np.ceil(time_steps / chunk_size))
    logger.info(f"Processing in {n_chunks} chunks of {chunk_size} time steps")

    for chunk_idx in tqdm(range(n_chunks), desc="Processing temperature chunks"):
        start_idx = chunk_idx * chunk_size
        end_idx = min((chunk_idx + 1) * chunk_size, time_steps)

        # Select chunk of time steps
        temp_chunk = temp_ds[temp_var_name].isel({time_coord: slice(start_idx, end_idx)})

        # Drop any extra dimensions (like 'expver' in ERA5)
        for dim in temp_chunk.dims:
            if dim not in [time_coord, 'longitude', 'latitude']:
                temp_chunk = temp_chunk.isel({dim: 0})

        # Get temperature values and flatten spatial dimensions
        temp_values = temp_chunk.values  # Shape: (time, lat, lon)
        temp_flat = temp_values.reshape(temp_values.shape[0], -1)  # Shape: (time, lat*lon)

        # Select only cells with population
        temp_flat_masked = temp_flat[:, pop_mask]

        # Handle NaN values in temperature
        temp_flat_masked = np.where(np.isnan(temp_flat_masked), 0, temp_flat_masked)

        # Calculate population-weighted temperature for this chunk (vectorized)
        weighted_temp[start_idx:end_idx] = np.dot(temp_flat_masked, pop_weights) / total_population

    # Convert from Kelvin to Celsius if needed
    if weighted_temp.mean() > 200:  # Likely in Kelvin
        logger.info("Converting temperature from Kelvin to Celsius")
        weighted_temp = weighted_temp - 273.15

    # Create DataFrame with datetime and temperature columns
    df = pd.DataFrame({
        'timestamp': pd.to_datetime(temp_ds[time_coord].values, utc=True),
        'temperature': weighted_temp
    })

    # Save to parquet file if output path provided
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(output_path, index=False)
        logger.info(f"Saved population-weighted temperature to {output_path}")

    # Clean up temporary files
    if pop_clipped_path.parent.name.startswith('tmp'):
        pop_clipped_path.unlink()

    logger.info("Calculation complete!")
    logger.info(f"Temperature range: {weighted_temp.min():.2f}°C to {weighted_temp.max():.2f}°C")
    logger.info(f"Mean temperature: {weighted_temp.mean():.2f}°C")
    logger.info(f"DataFrame shape: {df.shape}")

    # Close the dataset
    temp_ds.close()

    return df


def main():
    """Example usage when called as a standalone script."""
    # Example parameters
    country_code = 'DE'
    year = 2020
    population_years = [2000, 2005, 2010, 2015, 2020, 2025]
    from demandforge import RESULTS_DIR

    borders_path = RESULTS_DIR / "country_borders.gpkg"
    era5_path = RESULTS_DIR / f"era5/era5_temp_{country_code}_{year}.nc"
    ghsl_base_path = RESULTS_DIR / "ghsl"
    output_path = RESULTS_DIR / f"weighted_temp/weighted_temp_{country_code}_{year}.parquet"

    result = calculate_population_weighted_temperature(
        country_code=country_code,
        year=year,
        population_years=population_years,
        borders_path=borders_path,
        era5_path=era5_path,
        ghsl_base_path=ghsl_base_path,
        output_path=output_path
    )

    print(f"\nResult shape: {result.shape}")
    print(f"\nFirst few rows:")
    print(result.head(10))
    print(f"\nLast few rows:")
    print(result.tail(10))
    print(f"\nSummary statistics:")
    print(result['temperature'].describe())


if __name__ == "__main__":
    main()