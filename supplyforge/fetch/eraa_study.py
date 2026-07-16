"""
ERAA raw study data fetch module.

Role in SupplyForge pipeline
----------------------------

This module downloads the raw ERAA 2024 dashboard archive, extracts its
contents into the SupplyForge results directory, and prepares the local file
base used by downstream prospective-data modules.

It belongs to the category of:

    *prospective study data fetch*

The module does not create a modeling-ready dataset directly. Instead, it
stages raw ERAA files for later processing in modules such as
``eraa_capacity_factor.py`` and other scenario-building utilities.


Data source
-----------

The module downloads a ZIP archive from a fixed remote source:

- constant: ``ERAA_URL``
- current value:
  ``https://eepublicdownloads.blob.core.windows.net/public-cdn-container/clean-documents/sdc-documents/ERAA/ERAA_2024/Dashboard_raw_data.zip``

The archive is retrieved through:

- ``requests.get(..., stream=True)``

No API token is required.

Inputs
------

Main function inputs:

- none

Main function:

- ``fetch_eraa_data()``

Internal parameters and paths:

- target directory:
  ``RESULTS_DIR / "eraa_study"``
- temporary ZIP path:
  ``TARGET_DIR / "Dashboard_raw_data.zip"``
- extraction log:
  ``TARGET_DIR / "download.txt"``
- log file constant declared:
  ``TARGET_DIR / "eraa_fetch.log"``


Outputs
-------

The module produces files under:

- directory:
  ``RESULTS_DIR / "eraa_study"``

Main outputs:

- extracted ERAA raw files
- ``download.txt`` listing extracted filenames

Temporary artifact:

- ``Dashboard_raw_data.zip`` during execution
- deleted after successful extraction

No Parquet dataset is produced by this module.

Algorithmic description
-----------------------

The module performs the following steps:

1. create ``TARGET_DIR`` if it does not exist
2. define local temporary ZIP path:

   - ``TARGET_DIR / "Dashboard_raw_data.zip"``

3. send HTTP GET request to ``ERAA_URL`` with streaming enabled
4. raise an exception if the HTTP response is unsuccessful
5. write the streamed response to the local ZIP file
6. open the ZIP archive
7. iterate over all archive members
8. for each non-directory member:

   a. compute flattened destination path using ``Path(file).name``
   b. open the member from the ZIP archive
   c. copy its bytes to the destination file in ``TARGET_DIR``
   d. record the extracted filename

9. write the list of extracted filenames into ``download.txt``
10. remove the temporary ZIP file
11. log success messages throughout the process

Filesystem conventions
----------------------

The module uses the following local conventions:

- all extracted files are stored directly in:

  - ``RESULTS_DIR / "eraa_study"``

- the ZIP internal directory structure is not preserved
- extracted filenames are logged one per line in:

  - ``download.txt``

This module therefore acts as a local staging step for prospective ERAA inputs.

Integration in pipeline
-----------------------

Direct upstream dependency:

- remote ERAA public download archive

Downstream usage in SupplyForge:

- provides local raw inputs for:

  - ``eraa_capacity_factor.py``
  - scenario-oriented processing in ``create_pommes_craft_model.py``

Role in POMMES:

- indirect only
- does not parameterize the model directly
- prepares prospective source files used later to derive:

  - future capacity factors
  - storage-related assumptions
  - other ERAA-based scenario inputs

Thus, this module contributes to:

    prospective data staging (not direct model parametrization)


"""


# fetch/eraa.py
import logging
import requests
import zipfile
from pathlib import Path
import shutil

# Constants
from supplyforge import RESULTS_DIR
ERAA_URL = "https://eepublicdownloads.blob.core.windows.net/public-cdn-container/clean-documents/sdc-documents/ERAA/ERAA_2024/Dashboard_raw_data.zip"
TARGET_DIR = RESULTS_DIR / "eraa_study"
DOWNLOAD_LOG = TARGET_DIR / "download.txt"

# Set up logging
LOG_FILE = TARGET_DIR / "eraa_fetch.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def fetch_eraa_data():
    """
    Fetches the ERAA 2024 data, unzips it, and stores the contents in RESULTS_DIR/eraa_study,
    without keeping the intermediate folder from the ZIP file.
    A log of the downloaded files will be created in download.txt.
    """
    try:
        # Ensure target directory exists
        TARGET_DIR.mkdir(parents=True, exist_ok=True)

        # Path to the downloaded file
        zip_file_path = TARGET_DIR / "Dashboard_raw_data.zip"

        # Download file
        logger.info(f"Downloading ERAA data from {ERAA_URL}...")
        response = requests.get(ERAA_URL, stream=True)
        response.raise_for_status()  # Ensure any HTTP request errors are raised

        # Save to ZIP file
        with open(zip_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"Downloaded ERAA data to {zip_file_path}.")

        # Unzip the contents and flatten the structure
        logger.info("Unzipping ERAA data...")
        with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
            extracted_files = []
            for file in zip_ref.namelist():
                # Extract each file directly into TARGET_DIR
                target_path = TARGET_DIR / Path(file).name
                if not file.endswith("/"):  # Skip directories
                    with open(target_path, "wb") as output_file:
                        shutil.copyfileobj(zip_ref.open(file), output_file)
                    extracted_files.append(target_path.name)

        logger.info(f"Extracted ERAA data to {TARGET_DIR}.")
        logger.info(f"Extracted files: {', '.join(extracted_files)}")

        # Write the flattened file names to download.txt
        with open(DOWNLOAD_LOG, 'w') as download_file:
            download_file.write("\n".join(extracted_files))
        logger.info(f"Created download log at {DOWNLOAD_LOG}.")

        # Remove ZIP file after successful extraction
        zip_file_path.unlink(missing_ok=True)
        logger.info("Temporary ZIP file removed.")

    except Exception as e:
        logger.error(f"Failed to fetch and process ERAA data: {e}")
        raise


# Execute if run as a script
if __name__ == "__main__":
    fetch_eraa_data()
