r"""
Source-aware hydro reservoir-inflow dispatcher (Snakemake ``script`` entry point).

Produces the canonical ``inflow_<country>_<year>.parquet`` from whatever the
resolved ``hydro_source`` selects, writing to the *same* path so ``rule all`` and
the GCS upload are untouched:

- **``entsoe``** (default / ``auto`` when ``res_source`` is not ``pecd``): the
  original water-balance reconstruction (``hydro_inflow.generate_hourly_inflow_dataset``),
  unchanged.
- **``pecd``**: ``pecd.hydro_inflow.process_pecd_inflow`` (PECD ``HRI``).

Only the reservoir inflow is produced here; PECD run-of-river is emitted into the
capacity-factors file by the capacity-factor dispatcher.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from supplyforge import RESULTS_DIR, PACKAGE_DIR
from supplyforge.sources import resolve_hydro_source
from supplyforge.process.hydro_inflow import generate_hourly_inflow_dataset
from supplyforge.process.pecd.hydro_inflow import process_pecd_inflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ENTSO-E reconstruction column names (mirror the calculate_hydro_inflow rule params).
DEFAULT_PROD_COL = "('Hydro Water Reservoir', 'Actual Aggregated')"
DEFAULT_STOCK_COL = "storage_mwh"
DEFAULT_TS_COL_PROD = "('index', '')"
DEFAULT_TS_COL_STOCK = "timestamp"


def run_hydro_inflow(
    country: str,
    year: int,
    config: dict,
    production_path: str | Path | None = None,
    stock_path: str | Path | None = None,
    output_path: str | Path | None = None,
    prod_col: str = DEFAULT_PROD_COL,
    stock_col: str = DEFAULT_STOCK_COL,
    timestamp_col_prod: str = DEFAULT_TS_COL_PROD,
    timestamp_col_stock: str = DEFAULT_TS_COL_STOCK,
) -> Path:
    """Build the canonical inflow file for the resolved hydro source."""
    out = Path(output_path) if output_path else RESULTS_DIR / "inflow" / f"inflow_{country}_{year}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)

    hydro_source = resolve_hydro_source(config)
    logger.info("hydro_inflow: hydro_source=%s for %s %s.", hydro_source, country, year)

    if hydro_source == "pecd":
        process_pecd_inflow(country, year, out)
        return out

    # ENTSO-E water-balance reconstruction (legacy).
    try:
        prod_df = pd.read_parquet(production_path)
        stock_df = pd.read_parquet(stock_path)
        df = generate_hourly_inflow_dataset(
            prod_df=prod_df,
            stock_df=stock_df,
            prod_col=prod_col,
            stock_col=stock_col,
            timestamp_col_prod=timestamp_col_prod,
            timestamp_col_stock=timestamp_col_stock,
            year=year,
        )
        df.write_parquet(out)
        logger.info("hydro_inflow: ENTSO-E reconstruction written to %s.", out)
    except Exception as exc:  # noqa: BLE001 - fail-soft (matches the legacy path)
        logger.warning("hydro_inflow: ENTSO-E reconstruction failed for %s %s (%s); touching empty.", country, year, exc)
        out.touch()
    return out


if __name__ == "__main__":
    try:
        config = snakemake.config  # type: ignore[name-defined]
        country = snakemake.params.country  # type: ignore[name-defined]
        year = int(snakemake.params.year)  # type: ignore[name-defined]
        output_path = snakemake.output[0]  # type: ignore[name-defined]
        production_path = getattr(snakemake.input, "production", None)  # type: ignore[name-defined]
        stock_path = getattr(snakemake.input, "stock", None)  # type: ignore[name-defined]
        prod_col = getattr(snakemake.params, "prod_col", DEFAULT_PROD_COL)  # type: ignore[name-defined]
        stock_col = getattr(snakemake.params, "stock_col", DEFAULT_STOCK_COL)  # type: ignore[name-defined]
        timestamp_col_prod = getattr(snakemake.params, "timestamp_col_prod", DEFAULT_TS_COL_PROD)  # type: ignore[name-defined]
        timestamp_col_stock = getattr(snakemake.params, "timestamp_col_stock", DEFAULT_TS_COL_STOCK)  # type: ignore[name-defined]
    except NameError:
        import yaml
        logger.info("Not running under Snakemake; standalone debug run.")
        config = yaml.safe_load((PACKAGE_DIR / "config" / "config.yaml").read_text())
        country, year, output_path, production_path, stock_path = "FR", 2008, None, None, None
        prod_col, stock_col = DEFAULT_PROD_COL, DEFAULT_STOCK_COL
        timestamp_col_prod, timestamp_col_stock = DEFAULT_TS_COL_PROD, DEFAULT_TS_COL_STOCK
    run_hydro_inflow(
        country, year, config, production_path, stock_path, output_path,
        prod_col, stock_col, timestamp_col_prod, timestamp_col_stock,
    )
