# DemandForge

**Country-level electricity load analysis and projection.**

DemandForge is a Python-based workflow for analyzing and projecting country-level electricity load curves. It leverages weather data, electricity consumption data, and population statistics to model the relationship between temperature and electricity demand.

## Features

*   **Thermosensitivity Analysis:** Calculates the sensitivity of electricity load to temperature changes for both winter and summer periods.
*   **Data Fetching:** Automatically fetches data from various sources, including:
    *   [ERA5](https://www.ecmwf.int/en/forecasts/datasets/reanalysis-datasets/era5) for weather data.
    *   [ENTSO-E](https://www.entsoe.eu/) for electricity load data.
    *   [GHSL](https://ghsl.jrc.ec.europa.eu/) for population data.
*   **Workflow Management:** Uses [Snakemake](https://snakemake.readthedocs.io/en/stable/) to manage the data processing workflow, ensuring reproducibility and scalability.
*   **Reporting:** Generates detailed reports, including CSV files with results, plots of thermosensitivity by hour, and a comprehensive PDF report.

## Workflow

The project is orchestrated by a `Snakefile` that defines the entire data processing pipeline. The workflow consists of two main parts:

1.  **Fetching:** The `workflow/rules/fetch.smk` rule fetches all the necessary data from the respective sources.
2.  **Processing:** The `workflow/rules/process.smk` rule processes the raw data, performs the thermosensitivity analysis, and generates the final reports.

The main steps in the workflow are:
1.  Fetch country borders.
2.  Fetch ENTSO-E load data for specified countries and years.
3.  Fetch ERA5 temperature data.
4.  Fetch GHSL population data.
5.  Calculate population-weighted temperature.
6.  Perform thermosensitivity analysis.
7.  Generate and upload reports.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd demandforge
    ```

2.  **Install dependencies:**
    The project uses `pip` for dependency management. The required packages are listed in the `pyproject.toml` file.

    To install the core dependencies:
    ```bash
    pip install .
    ```

    To install dependencies for the workflow and notebooks:
    ```bash
    pip install .[workflow,notebooks]
    ```

## Usage

1.  **Configure the analysis:**
    The analysis is configured through the `config/config.yaml` file. In this file, you can specify the countries, years, and other parameters for the analysis.

2.  **Run the workflow:**
    To run the entire workflow, use the following command:
    ```bash
    snakemake --cores <number-of-cores>
    ```

    This will execute the entire pipeline, from fetching the data to generating the final reports. The results will be saved in the directory specified in the `config.yaml` file.

## Load projection usage

The `project_load_curve` function in `demandforge/load_projection.py` allows you to project future electricity load curves based on a reference year and scaling factors. This function can be used independently of the main Snakemake workflow. If the necessary combined load data for the reference year is not available locally, the function will automatically attempt to download it from a remote Google Cloud Storage bucket.

### Example Usage

Here's how to use the `project_load_curve` function:

```python
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
```

In this example, we project the load curve for Germany from 2020 to 2030. We specify a 2% annual growth rate for both the baseload and electric vehicle (EV) load components. You can also specify target energy values directly instead of growth rates for each component (baseload, winter thermosensitive, summer thermosensitive, and EV load).

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue.

## License

This project is licensed under the MIT License. See the `LICENCE.md` file for details.
