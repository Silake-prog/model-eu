"""
Script to fetch ENTSO-E load data using the demandforge library.
Called by Snakemake to download load curve data.
"""

from demandforge.fetch.entsoe_load_curves import save_load_data_to_parquet

# Get parameters from Snakemake
country_code = snakemake.params.country
year = int(snakemake.params.year)

# Fetch and save the data
output_file = save_load_data_to_parquet(country_code, year)