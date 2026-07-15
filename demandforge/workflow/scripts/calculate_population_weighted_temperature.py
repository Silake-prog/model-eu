"""
Script to calculate population weighted temperature using the demandforge library.
"""

from demandforge.process.population_weighted_temperature import calculate_population_weighted_temperature
from pathlib import Path

# Get parameters from Snakemake
country_code = snakemake.params.country
year = int(snakemake.params.year)
population_years = [int(y) for y in snakemake.params.population_years]

borders_path = Path(snakemake.input.borders)
era5_path = Path(snakemake.input.era5)
ghsl_base_path = Path(snakemake.input.ghsl_dir)
output_path = Path(snakemake.output[0])

calculate_population_weighted_temperature(
    country_code=country_code,
    year=year,
    population_years=population_years,
    borders_path=borders_path,
    era5_path=era5_path,
    ghsl_base_path=ghsl_base_path,
    output_path=output_path
)