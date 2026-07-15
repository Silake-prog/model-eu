"""
Snakemake rule for processing data.
"""

rule calculate_availability:
    """
    Calculates hourly availability per plant type from unavailability and installed capacity data.
    """
    input:
        unavailability="results/unavailability/unavailability_{country}_{year}.parquet",
        installed_capacity="results/installed_capacities/installed_capacities_{country}_{year}.parquet"
    output:
        "results/availability/availability_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/process/availability.py"

rule calculate_capacity_factors:
    """
    Calculates hourly capacity factors for intermittent renewables.
    """
    input:
        generation="results/generation/generation_{country}_{year}.parquet",
        installed_capacity="results/installed_capacities/installed_capacities_{country}_{year}.parquet"
    output:
        "results/capacity_factors/capacity_factors_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/process/capacity_factor.py"


rule calculate_hydro_inflow:
    """
    Calculates hourly hydro reservoir inflows from hourly generation and weekly stock levels.
    """
    input:
        production="results/generation/generation_{country}_{year}.parquet",
        stock="results/hydro_storage/hydro_storage_{country}_{year}.parquet"
    output:
        "results/inflow/inflow_{country}_{year}.parquet"
    params:
        # Column names are configurable here
        prod_col="('Hydro Water Reservoir', 'Actual Aggregated')",
        stock_col="storage_mwh",
        timestamp_col_prod="('index', '')",
        timestamp_col_stock="timestamp"
    log:
        "logs/calculate_hydro_inflow/{country}_{year}.log"
    script:
        "../../supplyforge/process/hydro_inflow.py"




rule upload_results_to_gcs:
    """
    Upload results to Google Cloud Storage.
    """
    input:
        day_ahead_prices="results/day_ahead_prices/day_ahead_prices_{country}_{year}.parquet",
        hydro_storage="results/hydro_storage/hydro_storage_{country}_{year}.parquet",
        installed_capacities="results/installed_capacities/installed_capacities_{country}_{year}.parquet",
        inflow="results/inflow/inflow_{country}_{year}.parquet",
        capacity_factors="results/capacity_factors/capacity_factors_{country}_{year}.parquet",
        availability="results/availability/availability_{country}_{year}.parquet",
        net_transfer_capacities="results/net_transfer_capacities/net_transfer_capacities_{country}_{year}.parquet",
        production="results/generation/generation_{country}_{year}.parquet",
    output:
        gcs="results/gcs_upload/combined_load_{country}_{year}.txt"
    log:
        "logs/gcs_upload_{country}_{year}.log"
    script:
        "../../supplyforge/process/upload_results.py"

