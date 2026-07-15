Workflow Overview
=================

DemandForge's workflow typically follows these steps to produce thermosensitivity insights for a given country and year:

1. Fetch input datasets

   - Country borders (GeoPackage) via demandforge.fetch.country_borders
   - ENTSO-E electricity load curves via demandforge.fetch.entsoe_load_curves
   - ERA5 air temperature via demandforge.fetch.era5
   - GHSL population rasters for Europe via demandforge.fetch.ghsl_population_europe

2. Derive population-weighted temperature

   - demandforge.process.population_weighted_temperature combines borders, ERA5 temperature fields, and GHSL population to output a time series of country-level, population-weighted temperature.

3. Analyze thermosensitivity (per-hour)

   - demandforge.process.thermosensitivity aligns load and temperature; scans winter/summer thresholds; runs regressions; and plots per-hour scatter/regression charts, plus a summary PDF.

4. Decompose load into baseload and thermo components

   - demandforge.process.thermosensitive_share uses the thermosensitivity results to estimate winter and summer thermosensitive load components and baseload, across hourly/daily/weekly/monthly resolutions. It also produces a comprehensive PDF report.

Artifacts
---------
- results/entsoe/entsoe_load_<CC>_<YYYY>.parquet: hourly load
- results/era5/era5_temp_<CC>_<YYYY>.nc: temperature grid
- results/ghsl/ghsl_pop_europe_<YYYY>.tif: population raster tiles merged
- results/weighted_temp/weighted_temp_<CC>_<YYYY>.parquet: population-weighted temp series
- results/thermosensitivity/: plots and a PDF summarizing per-hour regressions
- results/thermosensitive_share/: parquet and PDF showing baseload/winter/summer components and energy breakdown

Configuration
-------------
Pipeline parameters live in config/config.yaml (and related files). Adjust paths and country/year selection there when driving through Snakemake.
