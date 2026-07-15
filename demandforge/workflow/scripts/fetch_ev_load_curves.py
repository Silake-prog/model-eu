"""
Script to fetch EV load curves using the demandforge library.
Called by Snakemake to download EV load curves.
"""

from demandforge.fetch.eraa_ev_load_curves import download_ev_load_curves

# Execute the download
download_ev_load_curves()
