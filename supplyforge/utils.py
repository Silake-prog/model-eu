"""Shared helpers for reading staged inputs and deriving border prices / emission factors.

Small utilities used across the country-level model builder: a cache-first reader for the staged
Parquet inputs (downloading from the supplyforge GCS bucket on a miss), the hourly day-ahead import
price series for a bidding zone, and an ENTSO-E-generation-weighted hourly grid emission factor.
"""
import polars as pl
import logging
import urllib.request
import urllib.error
from supplyforge import RESULTS_DIR


def _get_input_data_file(country: str, year: int, input_file: str) -> pl.DataFrame:
    """Retrieves the input for a given country and year.

    Downloads the data from a remote URL if it's not available locally.

    Args:
        country (str): The country code (e.g., 'DE').
        year (int): The reference year.
        input_file (str) : The name of the input file.

    Returns:
        pl.DataFrame: A polars DataFrame with the combined load data.
    """
    file_name = f"{input_file}_{country}_{year}.parquet"
    data_dir = RESULTS_DIR / input_file
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / file_name

    if not file_path.is_file():
        logging.info(f"'{file_path}' not found. Downloading from remote URL...")
        url = f"https://storage.googleapis.com/supplyforge/{file_name}"
        try:
            urllib.request.urlretrieve(url, file_path)
            logging.info(f"Successfully downloaded '{file_path}'.")
        except urllib.error.URLError as e:
            logging.error(f"Failed to download {url}. Error: {e}")
            raise

    return pl.read_parquet(file_path)


def get_electricity_import_prices(country, reference_year, year_op, hours, bzn):
    """Hourly day-ahead import prices (EUR/MWh) for a bidding zone, as a pommes price frame.

    Reads the staged ``day_ahead_prices`` series for ``country``/``reference_year``, keeps the rows
    for bidding zone ``bzn``, clips negative prices to 0, and re-indexes onto the model's ``hours`` /
    ``year_op``. Used by :func:`create_pommes_craft_model.add_imports` to price a border.

    Args:
        country: country code whose price file to read.
        reference_year: year of the price series.
        year_op: model operation year to stamp on each row.
        hours: the model's hour index (truncates the series to ``len(hours)``).
        bzn: bidding-zone code to filter on.

    Returns:
        A polars frame with columns ``import_price``, ``hour``, ``year_op``.
    """
    df = (
        _get_input_data_file(country, reference_year, "day_ahead_prices")
        .rename({"price_eur_per_mwh": "import_price"})
    )
    # Filter to the bidding zone only when that column exists — several staged
    # day-ahead files (e.g. LU, UK) have a single zone and no `bidding_zone`
    # column, and filtering on a missing column dropped them to the flat fallback.
    if "bidding_zone" in df.columns:
        df = df.filter(pl.col("bidding_zone") == bzn)
    return (
        df.drop(["timestamp", "bidding_zone"], strict=False)[:len(hours)]
        .with_columns(hour=pl.Series(hours),
                      year_op=pl.lit(year_op).cast(pl.Int64),
                      import_price=pl.col("import_price").clip(0.))
    )


def get_electricity_emission_factors(country, reference_year, year_op, hours, emission_factors=None):
    """Hourly average grid CO₂ intensity (tCO₂/MWh) from the ENTSO-E generation mix.

    Weights per-technology emission factors by the actual hourly generation of each technology
    (ENTSO-E ``generation`` series for ``country``/``reference_year``) to get a system average each
    hour, then converts to tonnes (the default factors are given in kgCO₂/MWh). The series is leap-
    trimmed/padded to 8760 h and re-indexed onto the model's ``hours`` / ``year_op``.

    Args:
        country: country code whose generation mix to read.
        reference_year: year of the generation series.
        year_op: model operation year to stamp on each row.
        hours: the model's hour index.
        emission_factors: optional ``{plant_type: kgCO2/MWh}`` override; a built-in default set is
            used when ``None``.

    Returns:
        A polars frame with columns ``emission_factor`` (tCO₂/MWh), ``hour``, ``year_op``.
    """
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

