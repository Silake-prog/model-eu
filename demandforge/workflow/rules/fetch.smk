rule fetch_entsoe_load_curves:
    output:
        "results/entsoe/entsoe_load_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}"
    log:
        "logs/entsoe/fetch_entsoe_load_curves_{country}_{year}.log"
    script:
        "../scripts/fetch_entsoe_load.py"


rule fetch_eu_country_borders:
    output:
        "results/country_borders.gpkg"
    script:
        "../scripts/fetch_country_borders.py"

rule fetch_eraa_ev_load_curves:
    output:
        "results/eraa_ev_load_curves/downloaded_files.txt"
    script:
        "../scripts/fetch_ev_load_curves.py"


rule fetch_era5_temperature:
    """
    Fetch ERA5 temperature data for a specific country and year.
    Requires country borders file to calculate bounding box.
    """
    input:
        borders="results/country_borders.gpkg"
    output:
        "results/era5/era5_temp_{country}_{year}.nc"
    params:
        country="{country}",
        year="{year}"
    log:
        "logs/era5/fetch_era5_temperature_{country}_{year}.log"
    script:
        "../scripts/fetch_era5_temperature.py"

rule download_ghsl_europe:
    output:
        "results/ghsl/ghsl_pop_europe_{population_year}.tif"
    params:
        year="{population_year}"
    log:
        "logs/ghsl/fetch_ghsl_pop_europe_{population_year}.log"
    script:
        "../scripts/fetch_ghsl_population.py"
