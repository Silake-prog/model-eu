"""
Script to fetch ERA5 temperature data using the demandforge library.
Called by Snakemake to download ERA5 temperature data for a specific country.
"""

from demandforge.fetch.era5 import fetch_era5_temperature_by_country
from pathlib import Path

# Get parameters from Snakemake
country_id = snakemake.params.country
year = snakemake.params.year

# Get input file (country borders GPKG)
gpkg_path = snakemake.input.borders

# Get output file
output_file = snakemake.output[0]

# Fetch and save the data
result_file = fetch_era5_temperature_by_country(
    country_id=country_id,
    year=year,
    gpkg_path=gpkg_path,
    output_file=output_file
)
