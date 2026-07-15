"""
Snakemake script to calculate thermosensitive share of load curves
"""
import sys
from pathlib import Path

# Add the demandforge package to the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from demandforge.process.thermosensitive_share import process_thermosensitive_share


def main():
    """Main function called by Snakemake"""
    # Get parameters from snakemake object
    thermo_csv = snakemake.input.thermo_csv
    load_parquet = snakemake.input.load_parquet
    temp_parquet = snakemake.input.temp_parquet

    output_parquet = snakemake.output.parquet
    output_pdf = snakemake.output.pdf

    country = snakemake.wildcards.country
    year = int(snakemake.wildcards.year)

    # Get optional parameters
    r2_threshold = snakemake.params.get('r2_threshold', 0.1)
    print(f"R2 threshold: {r2_threshold}")

    # Process thermosensitive share
    process_thermosensitive_share(
        thermo_csv=thermo_csv,
        load_parquet=load_parquet,
        temp_parquet=temp_parquet,
        output_parquet=output_parquet,
        output_pdf=output_pdf,
        country=country,
        year=year,
        r2_threshold=r2_threshold
    )



if __name__ == '__main__':
    main()