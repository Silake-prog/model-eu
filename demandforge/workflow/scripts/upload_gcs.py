from pathlib import Path
from demandforge.process.upload_results import upload_combined_load_to_gcs

# Retrieve parameters from Snakemake
input_path = Path(snakemake.input.combined_load)

# Call the upload function
upload_combined_load_to_gcs(input_path)

output_path = Path(snakemake.output.gcs)
output_path.parent.mkdir(parents=True, exist_ok=True)
# Create a marker file to indicate completion
with open(output_path, 'w') as f:
    f.write(f'Uploaded {input_path.name} to GCS')
