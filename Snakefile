"""
Main Snakemake file for the supplyforge workflow.
"""

configfile: "config/config.yaml"

include: "workflow/rules/fetch.smk"
include: "workflow/rules/process.smk"

rule all:
    input:
        # expand("data/processed/{dataset}.csv", dataset=config["datasets"]),
        # Example of how to request the new generation files:
        expand("results/generation/generation_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request installed capacity files
        expand("results/installed_capacities/installed_capacities_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request unavailability of generation units files
        expand("results/unavailability/unavailability_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request hydro storage files
        expand("results/hydro_storage/hydro_storage_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request day-ahead price files
        expand("results/day_ahead_prices/day_ahead_prices_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request availability files
        expand("results/availability/availability_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request capacity factor files
        expand("results/capacity_factors/capacity_factors_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request hydro inflow files
        expand("results/inflow/inflow_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Request file upload
        expand("results/gcs_upload/combined_load_{country}_{year}.txt",
            country=config["countries"], year=config["years"]),
        # Request net transfer capacities files
        expand("results/net_transfer_capacities/net_transfer_capacities_{country}_{year}.parquet",
            country=config["countries"], year=config["years"]),
        # Download ERAA study data
        "results/eraa_study/download.txt"
