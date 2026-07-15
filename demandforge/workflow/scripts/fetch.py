import sys
from pathlib import Path

# Allow importing from src package
sys.path.append(str(Path(__file__).parent.parent / "src"))
import polars as pl
import demandforge as df

# Snakemake context
country = snakemake.params.country
year = snakemake.params.get("year", None)
start = snakemake.params.start
end = snakemake.params.end
kind = snakemake.params.kind  # "load" or "temp"
output_file = snakemake.output[0]

# If year is provided, override the date range to that calendar year
if year is not None:
    start = f"{int(year)}-01-01"
    end = f"{int(year)}-12-31"

if kind == "load":
    # In a real scenario, an API key might come from config or env. Here we keep a dummy.
    entsoe_api_key = "DUMMY_KEY"
    data = df.fetch_entsoe_load(region=country, start=start, end=end, api_key=entsoe_api_key)
elif kind == "temp":
    data = df.forge_temperature_driver(region=country, start=start, end=end)
else:
    raise ValueError(f"Unknown fetch kind: {kind}")

# Ensure output directory exists
Path(output_file).parent.mkdir(parents=True, exist_ok=True)

# Write as parquet
data.write_parquet(output_file)
print(f"Saved {kind} data for {country} to {output_file}")
