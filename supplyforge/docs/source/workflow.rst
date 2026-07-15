Snakemake Workflow
==================

This section documents the data pipeline implemented using the Snakemake workflow.
Each rule corresponds to a processing step and is linked to its Python implementation.

Overview
--------

The `supplyforge` Snakemake workflow is organized into two main stages:

1.  **`fetch`**: This stage is responsible for downloading all the necessary raw data. It retrieves a variety of datasets from the ENTSO-E Transparency Platform, including:

    *   Generation data
    *   Installed capacity
    *   Generation unit unavailability
    *   Hydro storage levels
    *   Day-ahead prices
    *   Net transfer capacities

    This stage also downloads the ENTSO-E ERAA 2024 study data for prospective analysis.

2.  **`process`**: Once the raw data is fetched, this stage processes it to generate the final datasets. The key processing steps include:

    *   Calculating hourly availability profiles for dispatchable technologies.
    *   Computing capacity factors for intermittent renewable sources.
    *   Deriving hydro inflows for reservoir hydro plants.
    *   Aggregating all the processed data into a combined load file.
    *   Uploading the final datasets to Google Cloud Storage.

Each rule in the workflow is designed for transparency and reproducibility, ensuring that the entire data pipeline can be easily executed and audited.

Outputs
-------

The Snakemake workflow generates several key datasets in the `results/` directory, which are essential for power system modeling. The primary outputs include:

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Output File Pattern
     - Description
   * - ``availability/availability_{country}_{year}.parquet``
     - Hourly availability profiles for dispatchable power plants.
   * - ``capacity_factors/capacity_factors_{country}_{year}.parquet``
     - Hourly capacity factors for intermittent renewable energy sources (e.g., solar, wind).
   * - ``inflow/inflow_{country}_{year}.parquet``
     - Reconstructed hourly inflows for hydroelectric reservoirs.
   * - ``installed_capacities/installed_capacities_{country}_{year}.parquet``
     - Aggregated installed generation capacity by technology.
   * - ``net_transfer_capacities/net_transfer_capacities_{country}_{year}.parquet``
     - Hourly net transfer capacities for cross-border interconnections.

These datasets serve as direct inputs for creating detailed energy system models.

Rule Graph
----------


.. raw:: html

    <div style="width: 100%; overflow-x: auto;">
        <object type="image/svg+xml" data="_static/rulegraph.svg" style="width: 100%; height: auto;"></object>
    </div>

The rule graph shows dependencies between workflow rules.


Rules
-----

The following pages document each rule, including:

- description (from docstrings)
- inputs / outputs / parameters
- link to the Python implementation

.. toctree::
   :maxdepth: 3

   rules/index

How to Run
----------

To execute the workflow locally:

.. code-block:: bash

    snakemake --cores all


Design Principles
-----------------

- **Modularity**: Each rule performs a single well-defined task
- **Reproducibility**: All steps are explicitly defined with inputs and outputs
- **Traceability**: Each rule is linked to a documented Python module
- **Scalability**: The workflow can be parallelized across multiple cores or clusters

Notes
-----

- Rule documentation is automatically generated from the Snakefile
- Python modules are documented via Sphinx autodoc
- Links between rules and implementation ensure consistency between pipeline and code
