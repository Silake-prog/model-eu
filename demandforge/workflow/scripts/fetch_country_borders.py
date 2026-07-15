"""
Script to fetch EU country borders using the demandforge library.
Called by Snakemake to download border data.
"""

from demandforge.fetch.country_borders import download_eu_borders

# Execute the download
download_eu_borders()
