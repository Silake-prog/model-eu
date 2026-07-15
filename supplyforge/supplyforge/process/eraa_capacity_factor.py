r"""
Prospective ERAA capacity factor processing module.

Role in SupplyForge pipeline
----------------------------

This module processes prospective capacity factor datasets derived from
the ERAA (European Resource Adequacy Assessment) study.

It belongs to the category of:

    *prospective data transformation and harmonization*

The module converts raw ERAA CSV files into structured hourly capacity
factor datasets by:

- technology
- weather scenario (WS)
- country
- target year

The resulting datasets are directly usable as availability profiles for
intermittent generation technologies in POMMES.

Data sources
------------

Primary data source:

- ERAA study CSV files downloaded via ``eraa_study.py``

Location:

- ``RESULTS_DIR / "eraa_study" / "capacity_factors"``

Configuration:

- list of countries loaded from:
  ``PACKAGE_DIR / "config" / "config.yaml"``

Input files
-----------

File naming convention (implicit from glob pattern):

- ``{country_code}*{technology}*{year}.csv``

Technologies considered:

- ``Wind_Onshore``
- ``Wind_Offshore``
- ``Solar``

Years processed:

- ``2026``
- ``2028``
- ``2030``
- ``2035``

Each CSV file:

- contains multiple weather scenarios (WS1 to WS35)
- includes metadata rows (first 10 rows skipped)
- contains hourly values per column

Main function inputs:

- ``country_code``: two-letter country code (e.g. "FR", "DE")

Purpose of the transformation
-----------------------------

The module restructures ERAA capacity factor data into a unified long-format
dataset indexed by:

- hour
- technology
- weather scenario

This enables direct use of ERAA scenarios as stochastic or deterministic
availability inputs in energy system modeling.

Algorithmic description
-----------------------

For each country and each target year:

1. identify all CSV files matching:

   - country
   - technology
   - year

2. for each technology:

   a. load all matching CSV files lazily using ``pl.scan_csv``

   b. skip metadata rows:

      - ``skip_rows=10``

   c. generate an hourly index per file:

      - ``with_row_index(name="hour")``

   d. concatenate all files vertically

   e. reshape from wide to long format:

      - unpivot columns ``WS1`` to ``WS35``
      - resulting columns:

        - ``hour``
        - ``WS`` (weather scenario)
        - ``capacity_factor``

   f. add technology label:

      - ``plant_type = tech``

   g. enforce categorical typing:

      - ``plant_type`` → categorical
      - ``WS`` → categorical

   h. if multiple files exist for the same technology:

      - aggregate by mean:

        - group by ``hour``, ``plant_type``, ``WS``

3. harmonize schema:

   enforce column order:

   - ``hour``
   - ``plant_type``
   - ``WS``
   - ``capacity_factor``

4. concatenate all technologies for the year

5. sort by:

   - ``hour``
   - ``plant_type``
   - ``WS``

6. write output as Parquet using streaming:

   - ``sink_parquet()``

Output
------

One Parquet file per country and year:

- directory:
  ``RESULTS_DIR / "capacity_factors"``
- filename:
  ``capacity_factors_<country>_<year>.parquet``

Output schema (code-visible)
----------------------------

The output contains:

- ``hour``: integer index (0–8759 expected)
- ``plant_type``: categorical (technology)
- ``WS``: categorical (weather scenario)
- ``capacity_factor``: float

Notes:

- no explicit datetime conversion is performed
- hour is a positional index, not a timestamp

Temporal conventions
--------------------

- hourly resolution is implicit
- no timezone handling
- no calendar alignment (e.g. leap years)

Formal definition
-----------------

For a given:

- hour :math:`h`
- technology :math:`p`
- weather scenario :math:`s`

The capacity factor is:

.. math::

    \mathrm{CF}_{h,p,s}

If multiple files exist for the same tuple (p, y), the module computes:

.. math::

    \mathrm{CF}_{h,p,s} =
    \frac{1}{N_{p,y}} \sum_{k=1}^{N_{p,y}} \mathrm{CF}^{(k)}_{h,p,s}

where:

- :math:`N_{p,y}` is the number of files for technology p and year y

Methodological assumptions
--------------------------

Explicit assumptions:

- ERAA CSV structure is stable (skip_rows=10 is valid)
- WS1–WS35 correspond to distinct weather scenarios
- multiple files per technology are combinable via averaging

Implicit assumptions:

- all files share identical temporal indexing
- averaging across files is meaningful (same scenario semantics)
- no missing hours or misalignment across files
- capacity factors are already normalized in [0, 1]

Integration in pipeline
-----------------------

Upstream dependency:

- ``eraa_study.py``

Downstream usage:

- ``create_pommes_craft_model.py``

Role in POMMES
--------------

The output provides:

    prospective availability profiles for intermittent technologies

In model construction:

- used as ``availability`` for renewable technologies
- optionally selected by weather scenario (WS)

This enables:

- stochastic simulations
- climate scenario analysis
- adequacy studies

"""

from supplyforge import RESULTS_DIR, PACKAGE_DIR
import polars as pl
import yaml
import logging
logging.basicConfig(level=logging.INFO)

def load_and_merge_eraa_data(country_code: str):

    """
    Loads ERAA capacity factor CSV files for a given country, processes them,
    and saves the merged data into a yearly Parquet file.

    This function processes data for multiple technologies and weather scenarios,
    unpivots the data, and aggregates it, using Polars' lazy API for memory efficiency.

    Args:
        country_code: The two-letter country code (e.g., "DE").
    """
    capa_folder = RESULTS_DIR / "eraa_study" / "capacity_factors"
    technologies = ["Wind_Onshore", "Wind_Offshore", "Solar"]
    years = ["2026", "2028", "2030", "2035"]
    cols = [f"WS{i}" for i in range(1, 36)]

    for year in years:
        lazy_frames_for_year = []
        for tech in technologies:
            logging.info(f"Loading capacity factors for {tech} in {country_code} for year {year}...")
            year_files = list(capa_folder.glob(f"{country_code}*{tech}*{year}.csv"))

            if not year_files:
                logging.warning(f"No files found for {tech} in {country_code} for year {year}. Skipping.")
                continue

            # Create a lazy frame for each file to correctly generate the 'hour' index per file.
            # This is crucial for the subsequent group_by operation.
            lazy_frames_per_tech = [
                pl.scan_csv(f, skip_rows=10).with_row_index(name="hour")
                for f in year_files
            ]

            # Concatenate the lazy frames for the current technology.
            lf_tech = pl.concat(lazy_frames_per_tech, how="vertical")
            lf_tech = lf_tech.unpivot(index="hour", on=cols, variable_name="WS", value_name="capacity_factor")
            lf_tech = lf_tech.with_columns(plant_type=pl.lit(tech).cast(pl.Categorical), WS=pl.col("WS").cast(pl.Categorical))

            if len(year_files) > 1:
                logging.info(f"Merging capacity factors for {tech} in {country_code} for year {year}...")
                lf_tech = lf_tech.group_by(['hour', 'plant_type', 'WS']).agg(pl.col("capacity_factor").mean())

            # Ensure a consistent column order to prevent schema errors during concatenation.
            final_cols = ['hour', 'plant_type', 'WS', 'capacity_factor']
            if lf_tech.columns != final_cols:
                lf_tech = lf_tech.select(final_cols)

            lazy_frames_for_year.append(lf_tech.sort(["hour", "plant_type", "WS"]))

        if lazy_frames_for_year:
            output_path = RESULTS_DIR / f"capacity_factors/capacity_factors_{country_code}_{year}.parquet"
            logging.info(f"Processing and saving data for year {year} to {output_path}...")
            pl.concat(lazy_frames_for_year, how='vertical').sink_parquet(output_path)
            logging.info(f"Successfully saved data for year {year}.")

if __name__ == "__main__":
    conf = yaml.safe_load((PACKAGE_DIR / "config" / "config.yaml").read_text())

    # load_and_merge_eraa_data("SE")
    for country in conf["countries"]:
        load_and_merge_eraa_data(country)