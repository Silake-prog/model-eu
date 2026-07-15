.. supplyforge documentation master file, created by
   sphinx-quickstart on Mon Dec 11 15:12:35 2023.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

supplyforge documentation
=========================

**supplyforge** is a Python package designed to construct detailed, country-level datasets
for the electricity supply side. It serves two primary purposes: executing a robust **Snakemake workflow** to build a
comprehensive data pipeline, and providing high-level functions for end-users to generate pre-configured **`pommes_craft`
optimization models**.

It automates the entire data pipeline, from fetching and processing to generating model-ready datasets, ensuring
consistency and transparency. As the companion package to
[**demandforge**](https://git.persee.minesparis.psl.eu/energy-alternatives/demandforge), it provides a complete data
foundation for holistic energy system modeling.

Overview
********

The core mission of **supplyforge** is to build a transparent and consistent data foundation that describes the
generation infrastructure and resources of European electricity systems. The package leverages a sophisticated
**Snakemake workflow** to automatically retrieve data from the **ENTSO-E Transparency Platform** and other sources,
process it, and generate a suite of model-ready datasets.

These datasets include:

-   🏭 **Installed Generation Capacity**: A detailed breakdown of power plant capacities by technology (e.g., nuclear, gas, hydro, wind, solar).
-   ⚙️ **Technology Availability Profiles**: Hourly availability factors for dispatchable power plants, accounting for planned and unplanned outages.
-   💧 **Hydro Inflows and Reservoir Levels**: Reconstructed inflow data for hydroelectric power plants, crucial for modeling their dispatch.
-   ☀️ **Renewable Generation Profiles**: Time series data for intermittent renewable sources like solar and wind, based on historical generation and capacity factors.
-   📊 **Aggregated Country-Level Summaries**: Standardized, easy-to-use datasets ready for direct integration into power system modeling tools.

For end-users, the ultimate output is the ability to generate a fully configured **`pommes_craft` energy model**
with a single function call. This model comes pre-populated with the country's generation fleet, operational constraints,
and time series data, making it ready for immediate optimization and analysis.

Features
********

-   **Automated Data Retrieval**: Fetches a wide array of data from the ENTSO-E Transparency Platform and the ENTSO-E ERAA 2024 Study, ensuring your models are built on the latest available information.
-   **Reproducible Workflow**: Utilizes a **Snakemake workflow** that guarantees full traceability and reproducibility of the entire data processing pipeline, from raw data to final model inputs.
-   **Harmonized Technology Classification**: Implements a consistent classification of power generation technologies across different countries, simplifying comparative analysis.
-   **Advanced Data Processing**: Computes key modeling parameters such as availability profiles for dispatchable plants, capacity factors for renewables, and reconstructed hydro inflows.
-   **Model-Ready Outputs**: Generates validated and standardized datasets in Parquet format, ready for seamless integration with power system optimization models.
-   **Extensible and Modular**: The workflow is designed to be easily extended, allowing for the addition of new data sources, countries, or custom processing steps to suit your specific modeling needs.

Relation to *demandforge*
*************************

.. raw:: html

   <table>
     <tr>
       <td><b>Package</b></td>
       <td><b>Focus</b></td>
       <td><b>Description</b></td>
     </tr>
     <tr>
       <td><a href="https://github.com/yourusername/demandforge"><b>demandforge</b></a></td>
       <td><b>Demand</b></td>
       <td>Creates projected electricity load curves for future scenarios</td>
     </tr>
     <tr>
       <td><b>supplyforge</b></td>
       <td><b>Supply</b></td>
       <td>Builds datasets describing generation capacity, availability, and inflows</td>
     </tr>
   </table>

Together, these tools provide a **complete data pipeline** for future electricity system modeling at the national scale.

Data Sources
************

The `supplyforge` package relies on the following data sources:

- **ENTSO-E Transparency Platform**: The primary source for a wide range of data, including:
    - Actual generation per production type
    - Installed generation capacity
    - Day-ahead prices
    - Cross-border physical flows
    - Net transfer capacities
    - Unavailability of generation units
    - Aggregate water reservoirs and hydro storage levels

- **ENTSO-E ERAA 2024 Study**: Used for prospective data, including future capacity factors for renewable energy sources.

All datasets are open or publicly accessible. Note that fetching data from the ENTSO-E Transparency Platform requires an API token, which must be set as the `ENTSOE_API_TOKEN` environment variable.


Documentation contents
**********************

.. toctree::
   :maxdepth: 2

   installation
   usage
   workflow
   api



Contributing
************

Contributions are welcome!
You can:
- Add support for additional data sources or countries
- Improve technology mappings or validation checks
- Extend workflow outputs for specific modeling frameworks

License
*******

Licensed under the **MIT License**.

Contact
*******

For questions, suggestions, or collaboration opportunities, please contact:
**[Yassine Abdelouadoud]** – [yassine.abdelouadoud@minesparis.psl.eu]
or open an issue on the [Gitlab repository](https://git.persee.minesparis.psl.eu/energy-alternatives/supplyforge).

Indices and tables
******************

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
