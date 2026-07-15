"""
Snakemake script to combine thermosensitive share of load curves and EV load curves
"""

from demandforge.process.combine_thermosensitive_ev_load import combine_load


def main():
    """Main function called by Snakemake"""
    # Get parameters from snakemake object
    load_parquet = snakemake.input.load_parquet
    output_parquet = snakemake.output.parquet
    country = snakemake.wildcards.country
    year = int(snakemake.wildcards.year)

    # Process thermosensitive share
    combine_load(
        path_thermosensitive=load_parquet,
        output_path=output_parquet,
        country=country,
        year=year
    )


if __name__ == '__main__':
    main()