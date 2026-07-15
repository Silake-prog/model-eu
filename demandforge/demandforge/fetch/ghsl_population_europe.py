
"""Script to download GHSL population data tiles for Europe.

Downloads individual tiles, extracts them, and combines into a single raster.
"""

from pathlib import Path
import zipfile
import requests
import geopandas as gpd
import rasterio
from rasterio.merge import merge
from typing import List, Tuple
import tempfile
import shutil
import logging


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def download_file(url: str, output_path: Path) -> None:
    """Downloads a file from a URL to the specified output path.

    Args:
        url (str): URL to download from.
        output_path (Path): Path where the file should be saved.
    """
    logger.info(f"Downloading {url}...")
    response = requests.get(url, stream=True)
    response.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info(f"Downloaded to {output_path}")


def extract_zip(zip_path: Path, extract_to: Path) -> None:
    """Extracts a zip file to the specified directory.

    Args:
        zip_path (Path): Path to the zip file.
        extract_to (Path): Directory to extract to.
    """
    logger.info(f"Extracting {zip_path}...")
    extract_to.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

    logger.info(f"Extracted to {extract_to}")


def get_europe_tiles(shapefile_url: str, temp_dir: Path) -> List[Tuple[int, int]]:
    """Downloads the GHSL tile shapefile and identifies tiles covering Europe.

    Args:
        shapefile_url (str): URL to the GHSL tile shapefile.
        temp_dir (Path): Temporary directory for intermediate files.

    Returns:
        List[Tuple[int, int]]: List of (row, column) tuples for European tiles.
    """
    # Download shapefile
    shapefile_zip = temp_dir / "tiles_shapefile.zip"
    download_file(shapefile_url, shapefile_zip)

    # Extract shapefile
    shapefile_dir = temp_dir / "tiles_shapefile"
    extract_zip(shapefile_zip, shapefile_dir)

    # Find the .shp file
    shp_files = list(shapefile_dir.rglob("*.shp"))
    if not shp_files:
        raise FileNotFoundError("No shapefile found in the downloaded archive")

    # Read shapefile
    tiles_gdf = gpd.read_file(shp_files[0])

    # Define Europe bounding box (approximate)
    # Longitude: -25 to 45, Latitude: 35 to 72
    europe_bbox = (-25, 34, 45, 72)

    # Filter tiles that intersect with Europe
    europe_tiles = tiles_gdf.cx[europe_bbox[0]:europe_bbox[2],
    europe_bbox[1]:europe_bbox[3]]

    # Extract row and column identifiers from the shapefile
    # Adjust this based on the actual column names in the shapefile
    tile_ids = []
    for _, tile in europe_tiles.iterrows():
        # Try different possible column name patterns
        if 'tile_id' in tile.index:
            tile_id = tile['tile_id']
        elif 'TILE_ID' in tile.index:
            tile_id = tile['TILE_ID']
        elif 'ID' in tile.index:
            tile_id = tile['ID']
        else:
            # Try to extract from geometry or other fields
            # This may need adjustment based on actual shapefile structure
            continue

        # Parse tile_id to extract row and column
        # Format is typically like "R5_C18"
        if isinstance(tile_id, str) and 'R' in tile_id and 'C' in tile_id:
            parts = tile_id.split('_')
            row = int(parts[0].replace('R', ''))
            col = int(parts[1].replace('C', ''))
            tile_ids.append((row, col))

    logger.info(f"Found {len(tile_ids)} tiles covering Europe")
    return tile_ids


def download_ghsl_tile(row: int, col: int, year: int, output_dir: Path) -> Path:
    """Downloads a single GHSL population tile.

    Args:
        row (int): Tile row number.
        col (int): Tile column number.
        year (int): Year of data (e.g., 2020).
        output_dir (Path): Directory to save the downloaded tile.

    Returns:
        Path: Path to the extracted GeoTIFF file.
    """
    # Construct URL for the tile
    base_url = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A"
    tile_name = f"GHS_POP_E{year}_GLOBE_R2023A_4326_30ss_V1_0_R{row}_C{col}"
    url = f"{base_url}/GHS_POP_E{year}_GLOBE_R2023A_4326_30ss/V1-0/tiles/{tile_name}.zip"

    # Download
    zip_path = output_dir / f"{tile_name}.zip"
    download_file(url, zip_path)

    # Extract
    extract_dir = output_dir / f"R{row}_C{col}"
    extract_zip(zip_path, extract_dir)

    # Find the .tif file
    tif_files = list(extract_dir.rglob("*.tif"))
    if not tif_files:
        raise FileNotFoundError(f"No GeoTIFF found in {extract_dir}")

    # Remove zip file to save space
    zip_path.unlink()

    return tif_files[0]


def merge_tiles(tile_paths: List[Path], output_path: Path) -> None:
    """Merges multiple GeoTIFF tiles into a single file.

    Args:
        tile_paths (List[Path]): List of paths to GeoTIFF files.
        output_path (Path): Path for the merged output file.
    """
    logger.info(f"Merging {len(tile_paths)} tiles...")

    # Open all tiles
    src_files_to_mosaic = []
    for tile_path in tile_paths:
        src = rasterio.open(tile_path)
        src_files_to_mosaic.append(src)

    # Merge tiles
    mosaic, out_trans = merge(src_files_to_mosaic)

    # Copy metadata from the first tile
    out_meta = src_files_to_mosaic[0].meta.copy()

    # Update metadata
    out_meta.update({
        "driver": "GTiff",
        "height": mosaic.shape[1],
        "width": mosaic.shape[2],
        "transform": out_trans,
        "compress": "lzw"
    })

    # Write merged file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, "w", **out_meta) as dest:
        dest.write(mosaic)

    # Close all source files
    for src in src_files_to_mosaic:
        src.close()

    logger.info(f"Merged tiles saved to {output_path}")


def download_ghsl_europe(
        year: int,
        output_path: Path,
        shapefile_url: str = "https://ghsl.jrc.ec.europa.eu/download/GHSL_data_4326_shapefile.zip",
        keep_tiles: bool = False
) -> None:
    """Main function to download and merge GHSL population data for Europe.

    Args:
        year (int): Year of population data (e.g., 2020).
        output_path (Path): Path for the final merged GeoTIFF.
        shapefile_url (str): URL to the tile index shapefile.
        keep_tiles (bool): Whether to keep individual tile files after merging.
    """
    # Create temporary directory
    temp_dir = Path(tempfile.mkdtemp())
    tiles_dir = temp_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Get list of tiles covering Europe
        europe_tiles = get_europe_tiles(shapefile_url, temp_dir)

        # Download all tiles
        tile_paths = []
        for row, col in europe_tiles:
            try:
                tile_path = download_ghsl_tile(row, col, year, tiles_dir)
                tile_paths.append(tile_path)
            except Exception as e:
                logger.warning(f"Failed to download tile R{row}_C{col}: {e}")
                continue

        if not tile_paths:
            raise RuntimeError("No tiles were successfully downloaded")

        # Merge tiles
        merge_tiles(tile_paths, output_path)

        logger.info(f"Successfully created merged GHSL population data at {output_path}")

    finally:
        # Clean up temporary directory
        if not keep_tiles:
            shutil.rmtree(temp_dir)
            logger.info("Cleaned up temporary files")


# Snakemake integration
if __name__ == "__main__":
    from demandforge import RESULTS_DIR
    year = 2025
    output_file = RESULTS_DIR / f"ghsl_population_europe_{year}.tif"

    download_ghsl_europe(
        year=year,
        output_path=output_file,
        keep_tiles=False
    )