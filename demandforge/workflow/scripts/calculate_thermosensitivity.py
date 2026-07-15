# workflow/scripts/calculate_thermosensitivity.py
"""
Snakemake wrapper script for thermosensitivity analysis.
"""

import logging
from pathlib import Path

from demandforge.process.thermosensitivity import analyze_thermosensitivity

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Main function for Snakemake script."""
    # Get parameters from Snakemake
    country = snakemake.params.country
    year = int(snakemake.params.year)

    load_path = Path(snakemake.input.load)
    temp_path = Path(snakemake.input.temperature)

    csv_output = Path(snakemake.output.csv)
    pdf_output = Path(snakemake.output.pdf)

    # Get thresholds from params (with defaults)
    winter_thresholds = snakemake.params.get(
        "winter_thresholds", [5.0, 8.0, 10.0, 12.0, 15.0]
    )
    summer_thresholds = snakemake.params.get(
        "summer_thresholds", [15.0, 18.0, 20.0, 22.0, 25.0]
    )

    logger.info(f"Processing thermosensitivity for {country} {year}")
    logger.info(f"Load path: {load_path}")
    logger.info(f"Temperature path: {temp_path}")
    logger.info(f"Winter thresholds: {winter_thresholds}")
    logger.info(f"Summer thresholds: {summer_thresholds}")

    # Run analysis
    csv_path, plot_dir, pdf_path = analyze_thermosensitivity(
        country=country,
        year=year,
        load_path=load_path,
        temp_path=temp_path,
        output_dir=csv_output.parent,
        winter_thresholds=winter_thresholds,
        summer_thresholds=summer_thresholds,
    )

    logger.info(f"Analysis complete")
    logger.info(f"CSV: {csv_path}")
    logger.info(f"Plot: {plot_dir}")
    logger.info(f"Plot: {pdf_path}")



if __name__ == "__main__":
    main()