# ⚡️ supplyforge

**supplyforge** is a Python package designed to construct detailed, country-level datasets 
for the electricity supply side. It serves two primary purposes: executing a robust **Snakemake workflow** to build a 
comprehensive data pipeline, and providing high-level functions for end-users to generate pre-configured **`pommes_craft` 
optimization models**.

It automates the entire data pipeline, from fetching and processing to generating model-ready datasets, ensuring 
consistency and transparency. As the companion package to 
[**demandforge**](https://git.persee.minesparis.psl.eu/energy-alternatives/demandforge), it provides a complete data
foundation for holistic energy system modeling.

---

## 🌍 Overview

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

---

## 🧩 Features

-   **Automated Data Retrieval**: Fetches a wide array of data from the ENTSO-E Transparency Platform and the ENTSO-E ERAA 2024 Study, ensuring your models are built on the latest available information.
-   **Reproducible Workflow**: Utilizes a **Snakemake workflow** that guarantees full traceability and reproducibility of the entire data processing pipeline, from raw data to final model inputs.
-   **Harmonized Technology Classification**: Implements a consistent classification of power generation technologies across different countries, simplifying comparative analysis.
-   **Advanced Data Processing**: Computes key modeling parameters such as availability profiles for dispatchable plants, capacity factors for renewables, and reconstructed hydro inflows.
-   **Model-Ready Outputs**: Generates validated and standardized datasets in Parquet format, ready for seamless integration with power system optimization models.
-   **Extensible and Modular**: The workflow is designed to be easily extended, allowing for the addition of new data sources, countries, or custom processing steps to suit your specific modeling needs.

---

## 🔗 Relation to *demandforge*

| Package | Focus | Description |
|----------|--------|-------------|
| [**demandforge**](https://github.com/yourusername/demandforge) | **Demand** | Creates projected electricity load curves for future scenarios |
| **supplyforge** | **Supply** | Builds datasets describing generation capacity, availability, and inflows |

Together, these tools provide a **complete data pipeline** for future electricity system modeling at the national scale.

---

## ⚙️ Workflow

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

---

## 🧱 Outputs

The Snakemake workflow generates several key datasets in the `results/` directory, which are essential for power system modeling. The primary outputs include:

| Output File Pattern                                       | Description                                                                      |
| --------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `availability/availability_{country}_{year}.parquet`      | Hourly availability profiles for dispatchable power plants.                      |
| `capacity_factors/capacity_factors_{country}_{year}.parquet`| Hourly capacity factors for intermittent renewable energy sources (e.g., solar, wind). |
| `inflow/inflow_{country}_{year}.parquet`                  | Reconstructed hourly inflows for hydroelectric reservoirs.                       |
| `installed_capacities/installed_capacities_{country}_{year}.parquet` | Aggregated installed generation capacity by technology.                 |
| `net_transfer_capacities/net_transfer_capacities_{country}_{year}.parquet` | Hourly net transfer capacities for cross-border interconnections.      |

These datasets serve as direct inputs for creating detailed energy system models.

---

## 📦 Installation

There are several ways to install `supplyforge` and its dependencies.

### Using Conda (Recommended)

You can create a dedicated conda environment with all the necessary dependencies using the provided environment file. This is the recommended method as it ensures that all dependencies are at the correct versions.

```bash
conda env create -f ci/envs/environment-all.yaml
conda activate supplyforge-env
```

### Using Pip

You can install the package directly using pip:

```bash
pip install supplyforge
```

Or from source:

```bash
git clone https://git.persee.minesparis.psl.eu/energy-alternatives/supplyforge.git
cd supplyforge
pip install -e .
```

---

## 🚀 Usage

There are two main ways to use `supplyforge`:

### Developer Usage: Snakemake Workflow

This usage is intended for developers who want to regenerate the datasets from the raw data. This is necessary when new input data has been published or when the data processing logic has been updated.

To run the full Snakemake workflow, which downloads and processes all data, run the following command from the root of the repository:

```bash
snakemake --cores all
```

This will generate the final datasets under the `output/` directory and upload them to Google Cloud Storage.

### User Usage: Generating Optimization Models

For users who want to create pre-configured optimization models for power system analysis, `supplyforge` provides a convenient function. This function uses the pre-processed data generated by the Snakemake workflow.

You can use the `create_model` function from the `supplyforge.create_pommes_craft_model` module to generate an optimization model for a specific country and year.

Here is an example of how to use it:

```python
from supplyforge.create_pommes_craft_model import create_model

# Define the country and year for the model
country_code = "FR"
reference_year = 2022
model_year = 2025

# Create the pommes_craft energy model
energy_model = create_model(
    country_code=country_code,
    reference_year=reference_year,
    model_year=model_year
)

# You can now use the energy_model object with pommes_craft
print(energy_model)
```

This will create a `pommes_craft` EnergyModel object, pre-configured with the generation fleet, availability profiles, and hydro data for the specified country and year.

---

## 📚 Data Sources

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

---

## 🧑‍💻 Contributing

Contributions are welcome!  
You can:
- Add support for additional data sources or countries  
- Improve technology mappings or validation checks  
- Extend workflow outputs for specific modeling frameworks

---

## 📄 License

Licensed under the **MIT License**.

---

## ✉️ Contact

For questions, suggestions, or collaboration opportunities, please contact:  
**[Yassine Abdelouadoud]** – [yassine.abdelouadoud@minesparis.psl.eu]  
or open an issue on the [Gitlab repository](https://git.persee.minesparis.psl.eu/energy-alternatives/supplyforge).