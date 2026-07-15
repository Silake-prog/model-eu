"""
Script to fetch GHSL population rasters using the demandforge library.
Called by Snakemake to population data
"""

from demandforge.fetch.ghsl_population_europe import download_ghsl_europe
from pathlib import Path

# Get parameters from Snakemake
year = int(snakemake.params.year)
output_file = Path(snakemake.output[0])

# Fetch and save the data
download_ghsl_europe(
        year=year,
        output_path=output_file,
        keep_tiles=False
    )