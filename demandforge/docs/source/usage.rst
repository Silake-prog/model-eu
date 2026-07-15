Usage (Quickstart)
==================

This guide shows how to run common DemandForge workflows from Python or Snakemake.

Fetching data
-------------
- ENTSO-E load curves:

.. code-block:: python

    from demandforge.fetch.entsoe_load_curves import save_load_data_to_parquet
    output = save_load_data_to_parquet(country_code="DE", year=2023)

- ERA5 temperature for a country:

.. code-block:: python

    from demandforge.fetch.era5 import fetch_era5_temperature_by_country
    from demandforge import RESULTS_DIR

    nc_path = fetch_era5_temperature_by_country(
        country_id="DE", year=2023,
        gpkg_path=RESULTS_DIR / "country_borders.gpkg"
    )

- Population-weighted temperature:

.. code-block:: python

    from demandforge.process.population_weighted_temperature import calculate_population_weighted_temperature
    from demandforge import RESULTS_DIR

    df = calculate_population_weighted_temperature(
        country_code="DE",
        year=2023,
        population_years=[2000,2005,2010,2015,2020,2025],
        borders_path=RESULTS_DIR / "country_borders.gpkg",
        era5_path=RESULTS_DIR / "era5/era5_temp_DE_2023.nc",
        ghsl_base_path=RESULTS_DIR / "ghsl",
        output_path=RESULTS_DIR / "weighted_temp/weighted_temp_DE_2023.parquet",
    )

Thermosensitivity analysis
--------------------------
- Compute per-hour thermosensitivity and generate figures/report:

.. code-block:: python

    from demandforge.process.thermosensitivity import analyze_thermosensitivity
    from pathlib import Path

    analyze_thermosensitivity(
        country="DE",
        year=2023,
        load_path=Path("results/entsoe/entsoe_load_DE_2023.parquet"),
        temp_path=Path("results/weighted_temp/weighted_temp_DE_2023.parquet"),
        output_dir=Path("results/thermosensitivity"),
    )

- Compute thermosensitive share decomposition and create a PDF report:

.. code-block:: python

    from demandforge.process.thermosensitive_share import process_thermosensitive_share

    process_thermosensitive_share(
        thermo_csv="results/thermosensitivity/thermosensitivity_DE_2023.csv",
        load_parquet="results/entsoe/entsoe_load_DE_2023.parquet",
        temp_parquet="results/weighted_temp/weighted_temp_DE_2023.parquet",
        output_parquet="results/thermosensitive_share/thermosensitive_share_DE_2023.parquet",
        output_pdf="results/thermosensitive_share/thermosensitive_share_DE_2023.pdf",
        country="DE",
        year=2023,
    )

Load projection
---------------
The `project_load_curve` function in `demandforge/load_projection.py` allows you to project future electricity load curves based on a reference year and scaling factors. This function can be used independently of the main Snakemake workflow. If the necessary combined load data for the reference year is not available locally, the function will automatically attempt to download it from a remote Google Cloud Storage bucket.

Here's how to use the `project_load_curve` function:

.. code-block:: python

    from demandforge.load_projection import project_load_curve

    # Project load for Germany (DE) from a 2020 reference to 2030
    # with a 2% yearly growth rate for baseload and EV load.
    projected_df = project_load_curve(
        country="DE",
        reference_year=2020,
        target_year=2030,
        baseload_yearly_growth_rate=0.02,
        ev_yearly_growth_rate=0.02
    )

    print(projected_df.head())

In this example, we project the load curve for Germany from 2020 to 2030. We specify a 2% annual growth rate for both the baseload and electric vehicle (EV) load components. You can also specify target energy values directly instead of growth rates for each component (baseload, winter thermosensitive, summer thermosensitive, and EV load).

Running with Snakemake
----------------------
If you prefer pipelines, see the Snakefile and workflow/rules/ for available rules, then run e.g.::

    snakemake -c 4

Adjust config in config/config.yaml as needed.
