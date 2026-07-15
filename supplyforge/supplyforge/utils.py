import os
import polars as pl
import logging
import urllib
import urllib.request
import urllib.error
from supplyforge import RESULTS_DIR

# Inputs known to always be a full-year hourly series (8760/8784 rows). A
# truncated download of these silently corrupts the VRE profile, so we reject
# anything shorter. Kept narrow on purpose: some other inputs (e.g. emission
# factors) are legitimately short and padded by their caller, so they get
# atomicity + a parseability check only, not a strict row count.
_HOURLY_INPUTS = {"capacity_factors"}


def _get_input_data_file(country: str, year: int, input_file: str) -> pl.DataFrame:
    """Retrieves the input for a given country and year.

    Downloads the data from a remote URL if it's not available locally.

    Downloads are ATOMIC (per-process temp file + os.replace) and integrity-checked
    (parseable; full-year length for known hourly inputs). This prevents the
    concurrent-download race that many SLURM jobs sharing this cache dir would
    otherwise hit — a truncated parquet that silently distorts the profile (the
    same failure class fixed for demandforge's combined_load).

    Args:
        country (str): The country code (e.g., 'DE').
        year (int): The reference year.
        input_file (str) : The name of the input file.

    Returns:
        pl.DataFrame: A polars DataFrame with the requested data.
    """
    file_name = f"{input_file}_{country}_{year}.parquet"
    data_dir = RESULTS_DIR / input_file
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / file_name
    url = f"https://storage.googleapis.com/supplyforge/{file_name}"
    min_rows = 8000 if input_file in _HOURLY_INPUTS else 1

    def _download_atomic() -> None:
        tmp = file_path.with_name(f"{file_name}.{os.getpid()}.tmp")
        try:
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, file_path)  # atomic on the same filesystem
        except Exception:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise

    for attempt in (1, 2):
        if not file_path.is_file():
            logging.info(f"'{file_path}' not found. Downloading (atomic) from remote URL...")
            try:
                _download_atomic()
                logging.info(f"Successfully downloaded '{file_path}'.")
            except urllib.error.URLError as e:
                logging.error(f"Failed to download {url}. Error: {e}")
                raise
        try:
            df = pl.read_parquet(file_path)
            if len(df) < min_rows:
                raise ValueError(f"'{file_name}' truncated: {len(df)} rows (< {min_rows}).")
            return df
        except Exception as e:
            logging.warning(f"'{file_name}' invalid on attempt {attempt} ({e}); "
                            f"discarding cache and re-downloading.")
            try:
                file_path.unlink()
            except FileNotFoundError:
                pass
    raise ValueError(f"'{file_name}' still invalid after re-download; aborting to avoid a corrupt profile.")


def get_electricity_import_prices(country, reference_year, year_op, hours, bzn):

    return (
        _get_input_data_file(
            country,
            reference_year,
            "day_ahead_prices"
        )
        .rename({"price_eur_per_mwh": "import_price"})
        .filter(pl.col("bidding_zone") == bzn)
        .drop(["timestamp", "bidding_zone"], strict=False)[:len(hours)]
        .with_columns(hour=pl.Series(hours),
                      year_op=pl.lit(year_op).cast(pl.Int64),
                      import_price=pl.col("import_price").clip(0.))
    )


def get_electricity_emission_factors(country, reference_year, year_op, hours, emission_factors=None):

    if emission_factors is None:
        emission_factors = {
            'Biomass': 230.0,
            'Fossil Gas': 500.0,
            'Fossil Hard coal': 1000.0,
            'Fossil Brown coal/Lignite': 1050.,
            'Fossil Coal-derived gas': 1000.0,
            'Geothermal': 40.0,
            'Fossil Oil': 700.0,
            'Hydro Pumped Storage': 0.0,
            'Hydro Run-of-river and poundage': 24.0,
            'Hydro Water Reservoir': 24.0,
            'Nuclear': 5.0,
            'Solar': 48.0,
            'Waste': 230.0,
            'Wind Onshore': 12.0,
            'Wind Offshore': 15.0,
            'Other': 700.0,
            'Other renewable': 40.0,
        } # in TCO2/MWh

    generation = _get_input_data_file(country=country, year=reference_year, input_file="generation")
    cols_to_keep = [c for c in generation.columns if 'Actual Aggregated' in c]
    generation = generation.select(cols_to_keep)
    generation = generation.rename({c: c.split("'")[1] for c in generation.columns})

    emission_totals = (
        generation.with_columns(**{c: pl.col(c) * emission_factors[c] for c in generation.columns})
    ).sum_horizontal()

    emission_factors = emission_totals / generation.sum_horizontal()

    if len(emission_factors) < 8760:
        emission_factors = pl.concat([emission_factors, emission_factors[-(8760 - len(emission_factors)):]],)
    if len(emission_factors) > 8760:
        emission_factors = emission_factors[:8760]

    return pl.DataFrame(
        {
            "emission_factor": emission_factors / 1000.,
            "hour": pl.Series(hours),
            "year_op": [year_op] * 8760,
        }
    )

