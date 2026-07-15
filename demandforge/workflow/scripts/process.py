import sys
from pathlib import Path

# Allow importing from src package
sys.path.append(str(Path(__file__).parent.parent / "src"))
import polars as pl
import demandforge as df

# Snakemake context
in_load = snakemake.input["load"]
in_temp = snakemake.input["temp"]
output_file = snakemake.output[0]

# Read inputs
load_ts = pl.read_parquet(in_load)
temp_ts = pl.read_parquet(in_temp)

# Merge using library function
merged = df.merge_data(load_ts=load_ts, temp_ts=temp_ts)

# Ensure output directory exists
Path(output_file).parent.mkdir(parents=True, exist_ok=True)

merged.write_parquet(output_file)
print(f"Merged data saved to {output_file}")
