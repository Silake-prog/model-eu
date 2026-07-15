import sys
from pathlib import Path
import json

# Allow importing from src package
sys.path.append(str(Path(__file__).parent.parent / "src"))
import polars as pl
import demandforge as df

# Snakemake context
input_file = snakemake.input[0]
output_file = snakemake.output[0]

# Read merged data
merged = pl.read_parquet(input_file)

# Analyze
model_results = df.analyze_thermosensitivity(merged_data=merged)

# Ensure output directory exists
Path(output_file).parent.mkdir(parents=True, exist_ok=True)

with open(output_file, 'w') as f:
    json.dump(model_results, f, indent=4)

print(f"Saved analysis results to {output_file}")
