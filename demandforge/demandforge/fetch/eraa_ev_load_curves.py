import requests
import zipfile
import io
import pandas as pd
import logging
from demandforge import RESULTS_DIR
from pathlib import Path
from tqdm import tqdm
# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def download_ev_load_curves():
    """Downloads EV load curve data from ENTSOE, processes it, and saves the aggregated result."""
    url = "https://eepublicdownloads.blob.core.windows.net/public-cdn-container/clean-documents/sdc-documents/ERAA/ERAA_2024/Demand%20data.zip"
    
    try:
        logging.info(f"Downloading data from {url}")
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # Get total file size from headers
        total_size = int(response.headers.get('content-length', 0))

        # Use a buffer to store the downloaded content
        buffer = io.BytesIO()

        # Create a tqdm progress bar
        with tqdm(total=total_size, unit='iB', unit_scale=True, desc="Downloading EV data") as pbar:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    buffer.write(chunk)
                    pbar.update(len(chunk))

        buffer.seek(0) # Rewind the buffer to the beginning
        zip_file = zipfile.ZipFile(buffer)
        output_folder = RESULTS_DIR / "eraa_ev_load_curves"
        
        files_to_process = {}
        for file_info in zip_file.infolist():
            filename = file_info.filename
            if "Demand data/Demand segments/" in filename and "EVpart" in filename:
                p = Path(filename)
                file_country = p.stem[:2]
                file_year = p.stem[-4:]
                if file_country not in files_to_process:
                    files_to_process[file_country] = {}
                if file_year not in files_to_process[file_country]:
                    files_to_process[file_country][file_year] = []
                files_to_process[file_country][file_year].append(file_info)

        output_filenames = []
        for file_country, file_years in files_to_process.items():
            for file_year, file_infos in file_years.items():
                aggregated_df = None
                for file_info in file_infos:
                    with zip_file.open(file_info) as file:
                        df = pd.read_csv(file)
                        cols = [f"WS{i}" for i in range(1, 37)]

                        if aggregated_df is None:
                            aggregated_df = df[['Date'] + cols].copy()
                        else:
                            aggregated_df[cols] += df[cols]
                aggregated_df.set_index('Date', inplace=True)
                aggregated_df.index = pd.to_datetime(aggregated_df.index, utc=True)
                output_filename = output_folder / f"{file_country}_{file_year}_EV_load_curves.parquet"
                aggregated_df.to_parquet(output_filename)
                output_filenames.append(str(output_filename))
                logging.info(f"Saved aggregated data for {file_country} {file_year} to {output_filename}")

        # Save the list of output filenames to a text file
        if output_filenames:
            list_filepath = output_folder / "downloaded_files.txt"
            with open(list_filepath, 'w') as f:
                for filename in output_filenames:
                    f.write(f"{filename}\n")
            logging.info(f"List of downloaded files saved to {list_filepath}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading the file: {e}")
        raise
    except zipfile.BadZipFile:
        logging.error("Error: The downloaded file is not a valid zip file.")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        raise
