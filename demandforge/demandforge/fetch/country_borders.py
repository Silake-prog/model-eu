import requests
import logging
from pathlib import Path

# Import the RESULTS_DIR from the project structure
from demandforge import RESULTS_DIR

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def download_eu_borders():
    """Downloads EU country borders from GISCO services and saves them to the results folder."""

    # URL for the EU country borders GPKG file
    url = "https://gisco-services.ec.europa.eu/distribution/v2/countries/gpkg/CNTR_RG_60M_2024_4326.gpkg"

    # Ensure results directory exists
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Output file path
    output_file = RESULTS_DIR / "country_borders.gpkg"

    logger.info(f"Downloading EU country borders from: {url}")
    logger.info(f"Saving to: {output_file}")

    try:
        # Download the file
        response = requests.get(url, stream=True)
        response.raise_for_status()

        # Save the file
        with open(output_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"Successfully downloaded and saved to {output_file}")
        logger.info(f"File size: {output_file.stat().st_size / (1024*1024):.2f} MB")

    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading file: {e}")
        raise
    except IOError as e:
        logger.error(f"Error saving file: {e}")
        raise


if __name__ == "__main__":
    download_eu_borders()