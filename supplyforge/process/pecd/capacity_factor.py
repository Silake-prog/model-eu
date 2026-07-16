r"""
PECD 4.2 capacity-factor processing module.

Role in SupplyForge pipeline
----------------------------

Analogue of ``eraa_capacity_factor.py``: converts the staged PECD 4.2 CSVs into
the canonical ``capacity_factors_<country>_<climate_year>.parquet`` consumed,
*unchanged*, by ``create_pommes_craft_model.add_intermittent_tech``. Emits rows
for the four intermittent plant types of ``INTERMITTENT_TECH_DICT``:

- ``Solar`` (from ``SPV``), ``Wind Onshore`` (``WON``), ``Wind Offshore`` (``WOF``)
  -> already hourly capacity factors in PECD;
- ``Hydro Run-of-river and poundage`` (RoR) when ``pecd.ror_source == pecd``,
  derived from weekly generation ``CF_week = (HRO + HPO) / (C_inst_RoR * 168)`` and
  interpolated weekly->hourly (D3, resolved: pecd).

Schema (canonical; matches the ERAA sibling so the unmodified consumer accepts it)
---------------------------------------------------------------------------------

``hour`` (int 0..8759), ``plant_type`` (categorical, an ``INTERMITTENT_TECH_DICT``
value), ``WS`` (categorical = the climate year as a string), ``capacity_factor``
(f64). Rows are sorted; ``add_intermittent_tech`` discards the file's hour values
and re-indexes with ``range(8760)``, so what matters is correct labels, a single
chronological series per ``(plant_type, WS)``, and the ``WS`` selector.

Key design decisions (surfaced in the PR)
-----------------------------------------

- **S1 - one climate year per file, single-value ``WS``**: each file holds exactly
  one climate year (``WS = str(climate_year)``). This is consumable by *both* the
  single-country ``create_model`` (``ws=None`` -> the WS column is simply not
  filtered, and a single-WS series yields the right 8760 rows) and the
  multi-country ``create_multi_country_renewable_model(ws=...)``. Interleaving
  several climate years in one file would break the single-country path.
- **S3 - leap-year trim by dropping Feb 29**: PECD hourly CSVs have 8784 rows in
  leap years; we drop the 24 Feb-29 hours deterministically (preserving the
  year-boundary alignment) to land on 8760.

National aggregation
--------------------

Wind is aggregated from its PEON/P2ON (onshore) or PEOF/P2OF (offshore) zones with
**installed-capacity weights** (:mod:`supplyforge.process.pecd.capacity_weights`);
multi-zone solar likewise. Single-zone countries (and solar requested at NUT0) are
exact with no weighting. Capacity factors are clipped to [0, 1] (out-of-range
values are counted and logged, not silently dropped).

Fail-soft: any missing/empty product is skipped per plant type; if nothing can be
built, a valid **empty** Parquet with the canonical schema is written.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from supplyforge import RESULTS_DIR
from supplyforge.fetch.pecd.zones import select_zone_columns, capacity_weighted_cf
from supplyforge.process.hydro_inflow import create_week_start, interpolate_weekly_index
from supplyforge.process.pecd.capacity_weights import get_zone_weights
from supplyforge.process.pecd.hydro_inflow import find_pecd_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PLANT_TYPE_BY_CODE = {"SPV": "Solar", "WON": "Wind Onshore", "WOF": "Wind Offshore"}
TECH_BY_CODE = {"SPV": "solar", "WON": "wind_onshore", "WOF": "wind_offshore"}
ROR_PLANT_TYPE = "Hydro Run-of-river and poundage"
ROR_INSTALLED_COL = "Hydro Run-of-river and poundage"

CANONICAL_SCHEMA = {
    "hour": pl.Int64,
    "plant_type": pl.Categorical,
    "WS": pl.Categorical,
    "capacity_factor": pl.Float64,
}


def _empty_cf() -> pl.DataFrame:
    return pl.DataFrame(schema=CANONICAL_SCHEMA)


def _drop_feb29(values: np.ndarray, dates: pd.DatetimeIndex) -> np.ndarray:
    """Drop the 24 Feb-29 hours (leap-year trim); no-op in non-leap years."""
    dates = pd.DatetimeIndex(dates)
    mask = ~((dates.month == 2) & (dates.day == 29))
    return values[np.asarray(mask)]


def _clip_flag(values: np.ndarray, label: str) -> np.ndarray:
    """Clip capacity factors to [0, 1], logging any out-of-range values."""
    n_low = int((values < 0).sum())
    n_high = int((values > 1).sum())
    if n_low or n_high:
        logger.warning(
            "PECD CF: %s has %d value(s) < 0 and %d value(s) > 1; clipping to [0, 1].",
            label, n_low, n_high,
        )
    return np.clip(values, 0.0, 1.0)


def _normalize_8760(values: np.ndarray, label: str) -> np.ndarray:
    """Coerce to exactly 8760 values (truncate extra / pad tail), logging mismatches."""
    n = len(values)
    if n == 8760:
        return values
    if n > 8760:
        logger.info("PECD CF: %s has %d hours; truncating to 8760.", label, n)
        return values[:8760]
    logger.warning("PECD CF: %s has only %d hours; padding tail to 8760.", label, n)
    pad = np.full(8760 - n, values[-1] if n else 0.0)
    return np.concatenate([values, pad])


def _long_frame(values: np.ndarray, plant_type: str, ws: str) -> pl.DataFrame:
    values = _normalize_8760(np.asarray(values, dtype=float), f"{plant_type}/{ws}")
    return pl.DataFrame(
        {
            "hour": list(range(8760)),
            "plant_type": [plant_type] * 8760,
            "WS": [ws] * 8760,
            "capacity_factor": values,
        }
    ).with_columns(
        pl.col("plant_type").cast(pl.Categorical),
        pl.col("WS").cast(pl.Categorical),
    )


def national_cf_from_frame(code: str, country: str, df: pd.DataFrame, config: dict) -> np.ndarray | None:
    """National hourly CF for SPV/WON/WOF from a wide hourly PECD frame.

    Args:
        code: ``"SPV"`` | ``"WON"`` | ``"WOF"``.
        country: Repo country code.
        df: Wide hourly frame, datetime index, columns = zone codes, values = CF.
        config: Full config (for per-zone weights).

    Returns:
        Hourly national CF (numpy, leap-trimmed, clipped to [0, 1]), or ``None`` if
        the country has no zone in this product.
    """
    zone_cols = select_zone_columns(list(df.columns), country)
    if not zone_cols:
        return None
    weights = get_zone_weights(TECH_BY_CODE[code], config) if len(zone_cols) > 1 else None
    series = capacity_weighted_cf(pl.from_pandas(df[zone_cols]), zone_cols, weights, country=country)
    values = _drop_feb29(series.to_numpy().astype(float), pd.DatetimeIndex(df.index))
    return _clip_flag(values, f"{code}/{country}")


def ror_cf_from_frames(
    country: str,
    year: int,
    hro_df: pd.DataFrame | None,
    hpo_df: pd.DataFrame | None,
    ror_capacity_mw: float | None,
) -> np.ndarray | None:
    """RoR hourly CF from weekly generation: ``(HRO+HPO)/(C_inst*168)`` -> hourly.

    Weekly resolution loses sub-weekly RoR variability vs. the ENTSO-E hourly
    series - logged loudly. Returns ``None`` if no generation or no RoR capacity.
    """
    weekly = None
    for df in (hro_df, hpo_df):
        if df is None:
            continue
        zones = select_zone_columns(list(df.columns), country)
        if not zones:
            continue
        wk = df[zones].sum(axis=1).astype(float)
        wk.index = pd.to_datetime(wk.index)
        weekly = wk if weekly is None else weekly.add(wk, fill_value=0.0)

    if weekly is None or len(weekly) == 0:
        return None
    if not ror_capacity_mw or ror_capacity_mw <= 0:
        logger.warning("PECD RoR: no RoR installed capacity for %s %s; cannot form a CF.", country, year)
        return None

    logger.warning(
        "PECD RoR: building run-of-river CF for %s from WEEKLY generation "
        "(loses diurnal/sub-weekly variability vs. the ENTSO-E hourly series).",
        country,
    )
    weekly_cf = weekly / (float(ror_capacity_mw) * 168.0)

    if len(weekly_cf) > 52:
        weekly_cf = weekly_cf.iloc[1:]
    if len(weekly_cf) > 52:
        weekly_cf = weekly_cf.iloc[:-1]
    if len(weekly_cf) == 51:
        weekly_cf = pd.concat([weekly_cf, weekly_cf.tail(1)])
    if len(weekly_cf) != 52:
        logger.warning("PECD RoR: %s has %d usable weekly rows (expected ~52); skipping RoR.", country, len(weekly_cf))
        return None

    weekly_cf.index = create_week_start(year)
    target_index = pd.date_range(
        start=f"{year}-01-01", end=f"{year + 1}-01-01", freq="1h", inclusive="left", tz="UTC"
    )
    hourly = interpolate_weekly_index(weekly_cf.copy(), target_index)  # no /168 (already a CF)
    hourly = hourly.ffill().fillna(0.0)  # carry last weekly value over the trailing partial week
    values = _drop_feb29(hourly.to_numpy().astype(float), target_index)
    return _clip_flag(values, f"RoR/{country}")


def _ror_installed_capacity(country: str, year: int) -> float | None:
    """National RoR installed capacity (MW) from installed_capacities_<C>_<year>."""
    from supplyforge.utils import _get_input_data_file
    try:
        cap = _get_input_data_file(country, year, "installed_capacities")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PECD RoR: could not load installed_capacities for %s %s (%s).", country, year, exc)
        return None
    if ROR_INSTALLED_COL in cap.columns and cap.height:
        return float(cap[ROR_INSTALLED_COL][0])
    logger.warning("PECD RoR: no '%s' column in installed_capacities for %s %s.", ROR_INSTALLED_COL, country, year)
    return None


def _read_pecd_csv(code: str, year: int) -> pd.DataFrame | None:
    csv = find_pecd_csv(code, year)
    if csv is None:
        return None
    df = pd.read_csv(csv, comment="#", index_col="Date")
    df.index = pd.to_datetime(df.index)
    return df


def pecd_wind_solar_frames(country: str, year: int, config: dict) -> list[pl.DataFrame]:
    """Canonical CF frames for Solar / Wind Onshore / Wind Offshore from PECD.

    Returns one ``_long_frame`` per available plant type (empty list if none are
    staged). Used directly by the pure-PECD path and by the composing dispatcher.
    """
    frames: list[pl.DataFrame] = []
    ws = str(year)
    for code in ("SPV", "WON", "WOF"):
        df = _read_pecd_csv(code, year)
        if df is None:
            logger.warning("PECD CF: no %s CSV staged for %s; skipping %s.", code, year, PLANT_TYPE_BY_CODE[code])
            continue
        values = national_cf_from_frame(code, country, df, config)
        if values is not None:
            frames.append(_long_frame(values, PLANT_TYPE_BY_CODE[code], ws))
    return frames


def pecd_ror_frame(country: str, year: int, config: dict) -> pl.DataFrame | None:
    """Canonical CF frame for run-of-river from PECD weekly generation, or ``None``."""
    values = ror_cf_from_frames(
        country, year, _read_pecd_csv("HRO", year), _read_pecd_csv("HPO", year),
        _ror_installed_capacity(country, year),
    )
    if values is None:
        return None
    return _long_frame(values, ROR_PLANT_TYPE, str(year))


def process_pecd_capacity_factors(
    country: str,
    year: int,
    config: dict,
    output_path: str | Path | None = None,
    ror_source: str = "pecd",
) -> Path:
    """Build ``capacity_factors_<country>_<year>.parquet`` from staged PECD CSVs.

    Args:
        country: Repo country code.
        year: Climate year (also the ``WS`` label and the file key).
        config: Full SupplyForge config dict.
        output_path: Optional explicit output path.
        ror_source: ``pecd`` (default) -> add a PECD-derived RoR CF; ``entsoe`` ->
            omit RoR here (the dispatcher composes ENTSO-E RoR instead).

    Returns:
        The output ``Path`` (valid empty-schema Parquet when nothing can be built).
    """
    out = Path(output_path) if output_path else RESULTS_DIR / "capacity_factors" / f"capacity_factors_{country}_{year}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)

    frames = pecd_wind_solar_frames(country, year, config)
    if ror_source == "pecd":
        rf = pecd_ror_frame(country, year, config)
        if rf is not None:
            frames.append(rf)

    if not frames:
        logger.warning("PECD CF: nothing built for %s %s; writing empty capacity_factors.", country, year)
        _empty_cf().write_parquet(out)
        return out

    result = pl.concat(frames, how="vertical").sort(["plant_type", "WS", "hour"])
    result.write_parquet(out)
    logger.info("PECD CF: wrote %d rows (%s) to %s.",
                result.height, result["plant_type"].unique().to_list(), out)
    return out


if __name__ == "__main__":
    try:
        country = snakemake.params.country  # type: ignore[name-defined]
        year = int(snakemake.params.year)  # type: ignore[name-defined]
        config = snakemake.config  # type: ignore[name-defined]
        output_path = snakemake.output[0]  # type: ignore[name-defined]
        ror_source = snakemake.params.get("ror_source", "pecd")  # type: ignore[name-defined]
    except NameError:
        import yaml
        from supplyforge import PACKAGE_DIR
        logger.info("Not running under Snakemake; standalone debug run.")
        config = yaml.safe_load((PACKAGE_DIR / "config" / "config.yaml").read_text())
        country, year, output_path, ror_source = "FR", 2008, None, "pecd"
    process_pecd_capacity_factors(country, year, config, output_path, ror_source)
