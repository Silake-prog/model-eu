from pathlib import Path
from google.cloud import storage
import os
import logging
from dotenv import load_dotenv
# Load environment variables from .env file
load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)


def upload_combined_load_to_gcs(source_file_path: str | Path):
    """Uploads the combined_load_parquet file for a given year and country to Google Cloud Storage.

    Args:
        source_file_path (str | Path): Path to the file to upload.
    """
    bucket_name = "demandforge"
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

