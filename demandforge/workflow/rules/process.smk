"""
Rules for processing and analyzing data
"""

rule calculate_population_weighted_temperature:
    """
    Calculate population-weighted temperature for a country and year.
    This combines country borders, ERA5 temperature data, and GHSL population data.
    Outputs as parquet file with datetime and temperature columns.
    """
    input:
        borders="results/country_borders.gpkg",
        era5="results/era5/era5_temp_{country}_{year}.nc",
        ghsl_dir="results/ghsl"
    output:
        "results/weighted_temp/weighted_temp_{country}_{year}.parquet"
    params:
        country="{country}",
        year="{year}",
        population_years=config["population_years"]
    log:
        "logs/weighted_temp/calculate_weighted_temp_{country}_{year}.log"
    script:
        "../scripts/calculate_population_weighted_temperature.py"


rule calculate_thermosensitivity:
    """
    Calculate thermosensitivity of load curves with respect to temperature.
    Tests multiple temperature thresholds for winter and summer and selects the best.
    Outputs CSV with all results and plot for best thresholds.
    """
    input:
        load="results/entsoe/entsoe_load_{country}_{year}.parquet",
        temperature="results/weighted_temp/weighted_temp_{country}_{year}.parquet"
    output:
        csv="results/thermosensitivity/thermosensitivity_results_{country}_{year}.csv",
        pdf="results/thermosensitivity/thermosensitivity_report_{country}_{year}.pdf"
    params:
        country="{country}",
        year="{year}",
        winter_thresholds=config.get("winter_thresholds", [5.0, 8.0, 10.0, 12.0, 15.0]),
        summer_thresholds=config.get("summer_thresholds", [15.0, 18.0, 20.0, 22.0, 25.0])
    log:
        "logs/thermosensitivity/calculate_thermosensitivity_{country}_{year}.log"
    script:
        "../scripts/calculate_thermosensitivity.py"


rule calculate_thermosensitive_share:
    """
    Calculate the thermosensitive share (baseload, winter heating, summer cooling)
    from thermosensitivity analysis results
    """
    input:
        thermo_csv = "results/thermosensitivity/thermosensitivity_results_{country}_{year}.csv",
        load_parquet = "results/entsoe/entsoe_load_{country}_{year}.parquet",
        temp_parquet = "results/weighted_temp/weighted_temp_{country}_{year}.parquet"
    output:
        parquet = "results/thermosensitive_share/thermosensitive_share_{country}_{year}.parquet",
        pdf = "results/thermosensitive_share/thermosensitive_share_report_{country}_{year}.pdf"
    params:
        r2_threshold = config.get("r2_threshold", 0.1)  # Minimum R² to consider thermosensitivity
    log:
        "logs/thermosensitive_share/thermosensitive_share_{country}_{year}.log"
    script:
        "../scripts/calculate_thermosensitive_share.py"


rule combine_thermosensitive_ev_load:
    input:
        load_parquet="results/thermosensitive_share/thermosensitive_share_{country}_{year}.parquet",
        ev_loads="results/eraa_ev_load_curves/downloaded_files.txt"
    output:
        parquet="results/combined_load/combined_load_{country}_{year}.parquet"
    log:
        "logs/combine_load_{country}_{year}.log"
    script:
        "../scripts/combine_thermosensitive_ev_load.py"


rule upload_results_to_gcs:
    input:
        combined_load="results/combined_load/combined_load_{country}_{year}.parquet",
    output:
        gcs="results/gcs_upload/combined_load_{country}_{year}.txt"
    log:
        "logs/combine_load_{country}_{year}.log"
    script:
        "../scripts/upload_gcs.py"
