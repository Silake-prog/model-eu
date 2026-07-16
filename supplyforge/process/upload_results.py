from pathlib import Path
from google.cloud import storage
import os
import logging
from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def upload_file_to_gcs(source_file_path: str | Path):
    """Uploads the file for a given year and country to Google Cloud Storage.

    Args:
        source_file_path (str | Path): Path to the file to upload.
    """
    bucket_name = "supplyforge"
    destination_blob_name = Path(source_file_path).name

    # Ensure the source file exists before attempting to upload
    if not os.path.exists(source_file_path):
        logger.error(f"Source file not found at {source_file_path}")
        raise FileNotFoundError(f"Source file not found: {source_file_path}")

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    blob.upload_from_filename(source_file_path)

    logger.info(f"File {source_file_path} uploaded to gs://{bucket_name}/{destination_blob_name}")

if __name__ == "__main__":
    # Retrieve parameters from Snakemake
    for input_name, input_file in snakemake.input.items():
        logger.info(f"Uploading {input_file} to GCS")
        input_path = Path(input_file)

        # Call the upload function
        upload_file_to_gcs(input_path)

        output_path = Path(snakemake.output.gcs)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Create a marker file to indicate completion
        with open(output_path, 'w') as f:
            f.write(f'Uploaded {input_path.name} to GCS')

