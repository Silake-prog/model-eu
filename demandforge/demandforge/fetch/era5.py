import cdsapi
import xarray as xr
import geopandas as gpd
import logging
from pathlib import Path
from typing import Dict, Union, Optional


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Define Europe bounding box (to exclude overseas territories)
# Roughly: Iceland in the north, Canary Islands in the south,
# Azores in the west, and Ural Mountains in the east
EUROPE_BBOX = {
    'min_lon': -25.0,   # West (includes Azores region but we'll filter by lat)
    'max_lon': 45.0,    # East (Ural Mountains)
    'min_lat': 34.0,    # South (includes Cyprus, Crete)
    'max_lat': 72.0     # North (includes northern Scandinavia)
}


def filter_geometries_to_europe(gdf: gpd.GeoDataFrame,
                                europe_bbox: Dict[str, float] = EUROPE_BBOX) -> gpd.GeoDataFrame:
    """Filters geometries to only include those within Europe.

    This excludes overseas territories like French Guiana, Réunion, etc.
    Handles both Polygon and MultiPolygon geometries by exploding them first.

    Args:
        gdf (GeoDataFrame): GeoDataFrame with country geometries (can be MultiPolygon).
        europe_bbox (dict): Bounding box defining Europe (min_lon, max_lon, min_lat, max_lat).

    Returns:
        GeoDataFrame: Filtered GeoDataFrame with only European geometries, dissolved back into a single geometry.
    """
    logger.info(f"Original data has {len(gdf)} row(s)")

    # Explode MultiPolygons into individual Polygons
    # This creates a new row for each polygon in a MultiPolygon
    exploded = gdf.explode(index_parts=False).reset_index(drop=True)

    logger.info(f"After exploding: {len(exploded)} individual polygon(s)")

    # Get centroids of all individual polygons
    centroids = exploded.geometry.centroid

    # Filter geometries where centroid is within Europe
    in_europe = (
            (centroids.x >= europe_bbox['min_lon']) &
            (centroids.x <= europe_bbox['max_lon']) &
            (centroids.y >= europe_bbox['min_lat']) &
            (centroids.y <= europe_bbox['max_lat'])
    )

    filtered_exploded = exploded[in_europe].copy()

    logger.info(f"After filtering to Europe: {len(filtered_exploded)} polygon(s)")

    if filtered_exploded.empty:
        return filtered_exploded

    # Dissolve back into a single MultiPolygon (or Polygon)
    # Keep all non-geometry columns from the first row
    non_geom_cols = [col for col in filtered_exploded.columns if col != 'geometry']

    if non_geom_cols:
        # Preserve the first row's attributes
        dissolved = filtered_exploded.dissolve(by=non_geom_cols[0]).reset_index()
    else:
        # If no other columns, just dissolve all geometries
        dissolved = gpd.GeoDataFrame(
            geometry=[filtered_exploded.geometry.unary_union],
            crs=filtered_exploded.crs
        )
        # Copy over original columns
        for col in gdf.columns:
            if col != 'geometry':
                dissolved[col] = gdf[col].iloc[0]

    logger.info(f"Dissolved back into {len(dissolved)} geometry/geometries")

    return dissolved


def get_country_bbox(country_id: str,
                     gpkg_path: Union[str, Path],
                     europe_only: bool = True) -> Dict[str, float]:
    """Gets bounding box for a country from the EU country borders GeoPackage.

    By default, filters to only include European territories.

    Args:
        country_id (str): Country identifier (e.g., 'DE', 'FR', 'ES').
        gpkg_path (str | Path): Path to the eu_country_borders.gpkg file.
        europe_only (bool, optional): If True, only include geometries within Europe (excludes overseas territories).
            Defaults to True.

    Returns:
        dict: Dictionary with keys 'min_lat', 'max_lat', 'min_lon', 'max_lon'.
    """
    country_id_replace_dict = {
        "GR": "EL"
    }
    gpkg_path = Path(gpkg_path)

    if not gpkg_path.exists():
        raise FileNotFoundError(f"GeoPackage file not found: {gpkg_path}")

    logger.info(f"Reading country borders from {gpkg_path}")

    # Read the GeoPackage
    gdf = gpd.read_file(gpkg_path)

    # Check if CNTR_ID column exists
    if 'CNTR_ID' not in gdf.columns:
        raise ValueError(f"Column 'CNTR_ID' not found in {gpkg_path}. Available columns: {list(gdf.columns)}")

    for c_id, c_id_replace in country_id_replace_dict.items():
        if country_id == c_id:
            country_id = c_id_replace
            break

    # Filter for the specified country
    country_gdf = gdf[gdf['CNTR_ID'] == country_id].copy()

    if country_gdf.empty:
        available_ids = sorted(gdf['CNTR_ID'].unique())
        logger.warning(f"Country ID '{country_id}' not found in the dataset. Available IDs: {available_ids}")
        return None

    logger.info(f"Found country: {country_id}")
    logger.info(f"Geometry type: {country_gdf.geometry.iloc[0].geom_type}")

    # Filter to Europe only if requested
    if europe_only:
        country_gdf = filter_geometries_to_europe(country_gdf)

        if country_gdf.empty:
            raise ValueError(
                f"No European territories found for country '{country_id}'. "
                f"Try setting europe_only=False to include overseas territories."
            )

    # Get the bounding box
    bounds = country_gdf.total_bounds  # Returns [minx, miny, maxx, maxy]

    bbox = {
        'min_lon': float(bounds[0]),
        'min_lat': float(bounds[1]),
        'max_lon': float(bounds[2]),
        'max_lat': float(bounds[3])
    }

    logger.info(f"Bounding box for {country_id} (Europe only: {europe_only}): {bbox}")

    return bbox


def fetch_era5_temperature(
        bbox: Dict[str, float],
        year: int,
        output_file: Union[str, Path],
        country_id: Optional[str] = None
) -> Path:
    """Fetches ERA5 temperature data for a given bounding box and year.

    Downloads data month by month and concatenates into a single file.

    Args:
        bbox (dict): Dictionary with keys 'min_lat', 'max_lat', 'min_lon', 'max_lon'.
            Example: {'min_lat': 40, 'max_lat': 50, 'min_lon': -10, 'max_lon': 10}.
        year (int): Year for which to download data.
        output_file (str | Path): Path to the output concatenated file (e.g., 'data/era5_temp_2022.nc').
        country_id (str, optional): Country ID for logging purposes.

    Returns:
        Path: Path object pointing to the concatenated output file.
    """
    # Convert to Path object
    output_path = Path(output_file)

    # Create output directory if it doesn't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create temporary directory for monthly files
    temp_dir = output_path.parent / f"temp_{output_path.stem}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Initialize CDS API client
    c = cdsapi.Client()

    # Validate bbox
    if not all(k in bbox for k in ['min_lat', 'max_lat', 'min_lon', 'max_lon']):
        raise ValueError("bbox must contain 'min_lat', 'max_lat', 'min_lon', 'max_lon'")

    # Area format for CDS API: [North, West, South, East]
    area = [bbox['max_lat'], bbox['min_lon'], bbox['min_lat'], bbox['max_lon']]

    monthly_files = []

    logger.info(f"Downloading ERA5 temperature data for year {year}")
    if country_id:
        logger.info(f"Country: {country_id}")
    logger.info(f"Bounding box: {bbox}")
    logger.info(f"Output file: {output_path}")

    # Download data month by month
    for month in range(1, 13):
        month_str = f"{month:02d}"
        monthly_file = temp_dir / f"era5_temp_{year}_{month_str}.nc"
        monthly_files.append(monthly_file)

        # Skip if file already exists
        if monthly_file.exists():
            logger.info(f"Month {month_str} already downloaded, skipping...")
            continue

        logger.info(f"Downloading data for {year}-{month_str}...")

        try:
            c.retrieve(
                'reanalysis-era5-single-levels',
                {
                    'product_type': 'reanalysis',
                    'variable': '2m_temperature',
                    'year': str(year),
                    'month': month_str,
                    'day': [f"{d:02d}" for d in range(1, 32)],
                    'time': [
                        '00:00', '01:00', '02:00', '03:00', '04:00', '05:00',
                        '06:00', '07:00', '08:00', '09:00', '10:00', '11:00',
                        '12:00', '13:00', '14:00', '15:00', '16:00', '17:00',
                        '18:00', '19:00', '20:00', '21:00', '22:00', '23:00',
                    ],
                    'area': area,
                    'format': 'netcdf',
                },
                str(monthly_file)
            )
            logger.info(f"Successfully downloaded {monthly_file.name}")
        except Exception as e:
            logger.error(f"Error downloading data for {year}-{month_str}: {str(e)}")
            # Remove partial file if it exists
            if monthly_file.exists():
                monthly_file.unlink()
            raise

    # Concatenate all monthly files
    logger.info("Concatenating monthly files...")

    try:
        # Open all monthly files
        datasets = []
        for file in monthly_files:
            if file.exists():
                datasets.append(xr.open_dataset(file))
            else:
                logger.warning(f"Monthly file not found: {file.name}")

        if not datasets:
            raise ValueError("No monthly files found to concatenate")

        # Concatenate along time dimension
        combined = xr.concat(datasets, dim='valid_time')

        # Save concatenated dataset
        combined.to_netcdf(output_path)
        logger.info(f"Successfully created concatenated file: {output_path}")

        # Close all datasets
        for ds in datasets:
            ds.close()

        # Remove individual monthly files
        logger.info("Removing individual monthly files...")
        for file in monthly_files:
            if file.exists():
                file.unlink()
                logger.debug(f"Removed {file.name}")

        # Remove temporary directory
        if temp_dir.exists():
            temp_dir.rmdir()
            logger.info("Removed temporary directory")

        return output_path

    except Exception as e:
        logger.error(f"Error during concatenation: {str(e)}")
        raise


def fetch_era5_temperature_by_country(
        country_id: str,
        year: int,
        gpkg_path: Union[str, Path],
        output_file: Optional[Union[str, Path]] = None,
        europe_only: bool = True
) -> Path:
    """Fetches ERA5 temperature data for a specific country by calculating its bounding box.

    Args:
        country_id (str): Country identifier from the CNTR_ID column (e.g., 'DE', 'FR', 'ES').
        year (int): Year for which to download data.
        gpkg_path (str | Path): Path to the eu_country_borders.gpkg file.
        output_file (str | Path, optional): Path to the output file. If not provided,
            defaults to 'era5_data/era5_temp_{country_id}_{year}.nc'.
        europe_only (bool, optional): If True, only include European territories
            (excludes overseas territories). Defaults to True.

    Returns:
        Path: Path object pointing to the output file.
    """
    # Get bounding box for the country
    bbox = get_country_bbox(country_id, gpkg_path, europe_only=europe_only)

    # Set default output file if not provided
    if output_file is None:
        output_file = Path('era5_data') / f'era5_temp_{country_id}_{year}.nc'

    if bbox is None:
        # create empty file if no data available
        output_path = Path(output_file)
        output_path.touch()
        return output_path

    # Fetch the data
    return fetch_era5_temperature(bbox, year, output_file, country_id=country_id)


def main():
    """Example usage of the fetch_era5_temperature_by_country function."""
    # Example 1: Fetch data for France (European territories only)
    country_id = 'FR'  # France
    year = 2022
    gpkg_path = Path('results') / 'country_borders.gpkg'

    logger.info(f"=== Fetching ERA5 data for country {country_id}, year {year} ===")

    result_file = fetch_era5_temperature_by_country(
        country_id=country_id,
        year=year,
        gpkg_path=gpkg_path,
        europe_only=True  # This will exclude French Guiana, Réunion, etc.
    )

    logger.info(f"Data download complete!")
    logger.info(f"Output file: {result_file}")

    # Load and display basic info about the downloaded data
    ds = xr.open_dataset(result_file)
    logger.info("Dataset information:")
    logger.info(f"\n{ds}")
    ds.close()


if __name__ == "__main__":
    main()