"""
Snakemake rules for fetching data.
"""

rule fetch_generation:
    """
    Fetches actual generation per production type from ENTSO-E for a given country and year.
    """
    output:
        "results/generation/generation_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/fetch/generation.py"

rule fetch_installed_capacity:
    """
    Fetches installed generation capacity from ENTSO-E for a given country and year.
    """
    output:
        "results/installed_capacities/installed_capacities_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/fetch/installed_capacity.py"

rule fetch_unavailability:
    """
    Fetches unavailability of generation units from ENTSO-E for a given country and year.
    """
    output:
        "results/unavailability/unavailability_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/fetch/unavailability.py"

rule fetch_hydro_storage:
    """
    Fetches aggregate water reservoirs and hydro storage from ENTSO-E.
    """
    output:
        "results/hydro_storage/hydro_storage_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/fetch/hydro_storage.py"

rule fetch_day_ahead_prices:
    """
    Fetches day-ahead prices from ENTSO-E.
    """
    output:
        "results/day_ahead_prices/day_ahead_prices_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/fetch/day_ahead_prices.py"

rule fetch_crossborder_flows:
    """
    Fetches physical cross-border flows from ENTSO-E for all borders of a country.
    """
    output:
        "results/crossborder_flows/crossborder_flows_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/fetch/crossborder_flows.py"


rule fetch_eraa:
    """
    Fetch the ERAA 2024 data and extract it into RESULTS_DIR/eraa_study.
    """
    output:
        "results/eraa_study/download.txt"
    script:
        "../../supplyforge/fetch/eraa_study.py"


rule fetch_net_transfer_capacities:
    """
    Fetches physical net transfer capacities from ENTSO-E for all borders of a country.
    """
    output:
        "results/net_transfer_capacities/net_transfer_capacities_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    script:
        "../../supplyforge/fetch/net_transfer_capacities.py"
