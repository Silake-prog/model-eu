import json
import sys
from pathlib import Path

# Add the src directory to the Python path to allow importing the library
sys.path.append(str(Path(__file__).parent.parent / "src"))
import demandforge as df

# Access Snakemake parameters
country = snakemake.params.country
year = snakemake.params.get("year", None)
start = snakemake.params.start
end = snakemake.params.end
output_file = snakemake.output[0]
entsoe_api_key = "DUMMY_KEY"

# If year is provided, override date range to the calendar year
if year is not None:
    start = f"{int(year)}-01-01"
    end = f"{int(year)}-12-31"

# Main Script Logic
print(f"--- Running analysis for {country} ({year if year else 'full period'}) ---")
temp_ts = df.forge_temperature_driver(region=country, start=start, end=end)
load_ts = df.fetch_entsoe_load(region=country, start=start, end=end, api_key=entsoe_api_key)
merged_data = df.merge_data(load_ts=load_ts, temp_ts=temp_ts)
model_results = df.analyze_thermosensitivity(merged_data=merged_data)

with open(output_file, 'w') as f:
    json.dump(model_results, f, indent=4)

print(f"--- Successfully saved results for {country} to {output_file} ---")
