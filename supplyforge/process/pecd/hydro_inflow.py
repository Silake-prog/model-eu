r"""
PECD 4.2 reservoir-inflow processing module (HYDRO - handle with care).

Role in SupplyForge pipeline
----------------------------

Converts the PECD 4.2 **reservoir inflow** variable ``HRI`` (weekly energy, SZON)
into the canonical ``inflow_<country>_<climate_year>.parquet`` consumed,
*unchanged*, by ``create_pommes_craft_model.add_reservoir_hydro``. This **replaces**
the ENTSO-E water-balance reconstruction (``hydro_inflow.py``) when a PECD hydro
source is selected.

Why HRI rather than the ENTSO-E balance
---------------------------------------

``HRI`` is a *natural inflow* reconstruction, which is exactly the must-run
``reservoir_water`` inflow signal the POMMES reservoir triplet expects - arguably
more physical than the ENTSO-E identity ``I_w = G_w + dS_w`` (which conflates
inflow with dispatch decisions). The downstream model **shape-normalises** the
series (``availability = inflow_MW / inflow_MW.max()`` and
``power_capacity = inflow_MW.max()`` on the ``reservoir_water`` resource), so only
the *temporal profile* matters; the absolute MWh scale cancels. We still log the
absolute scale for transparency.

Method
------

1. Locate the staged ``HRI`` CSV for the climate year under
   ``RESULTS_DIR / "pecd_study"`` (produced by ``fetch/pecd/study.py``). PECD hydro CSVs
   are ``pd.read_csv(comment="#", index_col="Date")`` with rows = weekly
   timestamps and columns = SZON zone codes.
2. Select the country's SZON zone columns (prefix match, GR->EL) and **sum** the
   weekly energy across them (energy is additive).
3. Coerce to exactly 52 Monday week-starts using the SAME heuristic and helper as
   the ENTSO-E path (``create_week_start`` + ``interpolate_weekly_to_hourly``,
   imported from :mod:`supplyforge.process.hydro_inflow` - reused, not copied), so
   the weekly->hourly interpolation, ``bfill``, zero-clip and ``/168`` MWh/week ->
   MW conversion are byte-identical to ``inflow_*`` produced from ENTSO-E.
4. Build the full-year hourly UTC index exactly as the ENTSO-E path does
   (``pd.date_range(year-01-01 .. year+1-01-01, freq="1h", inclusive="left",
   tz="UTC")``; 8760 h, or 8784 in leap years - the consumer truncates to 8760).

Provenance / caveats (logged at runtime)
----------------------------------------

PECD inflows are a statistical (Random-Forest) reconstruction from weekly
temperature & precipitation; **reservoir inflow is zero-clipped at source** (we
clip again defensively); some zones carry temporary multiplicative corrections /
IC-rescaling (PUG Table 2.10; AL/CH/HU/PL/PT) that mix climate and
installed-capacity effects, and open-loop inflow for some zones derives from
PECDv3.1. The authors state inflows are **not fully validated**.

Output schema (canonical, matches the ENTSO-E sibling)
------------------------------------------------------

``inflow_MW`` (f64), ``timestamp`` (datetime, UTC). Fail-soft: missing/empty input
-> a valid **empty** Parquet with this schema (+ warning), so the consumer's
``inflows.is_empty()`` guard skips the country cleanly.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import polars as pl

from supplyforge import RESULTS_DIR, PACKAGE_DIR
from supplyforge.fetch.pecd.zones import select_zone_columns
from supplyforge.process.hydro_inflow import create_week_start, interpolate_weekly_to_hourly

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

PECD_STUDY_DIR = RESULTS_DIR / "pecd_study"

# Zones whose PECD inflow mixes climate + installed-capacity effects (PUG).
IC_RESCALED_ZONES = ("AL", "CH", "HU", "PL", "PT")

_EMPTY_SCHEMA = {
    "inflow_MW": pl.Float64,
    "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
}


def _empty_inflow() -> pl.DataFrame:
    return pl.DataFrame(schema=_EMPTY_SCHEMA)


def find_pecd_csv(code: str, year: int, study_dir: Path = PECD_STUDY_DIR) -> Path | None:
    """Return the staged PECD CSV for a data code + climate year, or None.

    Globs the fetcher's deterministic name ``pecd42_<code>_*_<year>*.csv``. If
    several match (different file_version/origin), the first sorted match is used
    and the choice logged.
    """
    if not study_dir.is_dir():
        return None
    matches = sorted(study_dir.glob(f"pecd42_{code}_*_{year}*.csv"))
    if not matches:
        return None
    if len(matches) > 1:
        logger.info("PECD: multiple %s files for %s: %s; using %s.",
                    code, year, [m.name for m in matches], matches[0].name)
    return matches[0]


def pecd_reservoir_inflow(hri_df: pd.DataFrame, country: str, year: int) -> pl.DataFrame:
    """Convert a wide weekly ``HRI`` frame into the canonical hourly inflow.

    Args:
        hri_df: Wide PECD HRI frame, rows = weekly timestamps (datetime index),
            columns = SZON zone codes, values = weekly inflow energy (MWh/week).
        country: Two-letter repo country code.
        year: Climate year (used for the canonical 52 Monday week-starts and the
            full-year hourly index).

    Returns:
        ``pl.DataFrame`` with columns ``inflow_MW`` (f64) and ``timestamp``
        (datetime, UTC). Empty (with schema) when the country has no SZON zone or
        the weekly series is unusable.
    """
    zone_cols = select_zone_columns(list(hri_df.columns), country)
    if not zone_cols:
        logger.warning("PECD HRI: no SZON zone for %s; writing empty inflow.", country)
        return _empty_inflow()

    weekly = hri_df[zone_cols].sum(axis=1).astype(float)
    weekly.index = pd.to_datetime(weekly.index)

    # Coerce to exactly 52 rows (same heuristic as the ENTSO-E stock table).
    if len(weekly) > 52:
        weekly = weekly.iloc[1:]
    if len(weekly) > 52:
        weekly = weekly.iloc[:-1]
    if len(weekly) == 51:
        weekly = pd.concat([weekly, weekly.tail(1)])
    if len(weekly) != 52:
        logger.warning(
            "PECD HRI: %s has %d usable weekly rows (expected ~52); writing empty inflow.",
            country, len(weekly),
        )
        return _empty_inflow()

    if (weekly < 0).any():
        logger.warning("PECD HRI: negative weekly inflow for %s before clipping (will clip >= 0).", country)

    # Canonical 52 Monday week-starts, then reuse the ENTSO-E interpolation helper
    # (linear -> bfill -> clip>=0 -> /168 MWh/week -> MW).
    weekly.index = create_week_start(year)
    target_index = pd.date_range(
        start=f"{year}-01-01", end=f"{year + 1}-01-01", freq="1h", inclusive="left", tz="UTC"
    )
    hourly_mw = interpolate_weekly_to_hourly(weekly.copy(), target_index)
    # Forward-fill the trailing partial week (hours after the last Monday week-start)
    # so the must-run inflow signal carries the last weekly value instead of NaN.
    hourly_mw = hourly_mw.ffill().fillna(0.0)

    if country in IC_RESCALED_ZONES:
        logger.warning(
            "PECD HRI: %s is IC-rescaled in PECD (PUG); projection-vs-historical "
            "differences mix climate and installed-capacity effects.", country,
        )
    logger.info(
        "PECD HRI: %s %s zones=%s -> mean=%.1f MW, max=%.1f MW over %d h "
        "(profile is what matters downstream; PECD inflow is an RF reconstruction).",
        country, year, zone_cols, float(hourly_mw.mean()), float(hourly_mw.max()), len(hourly_mw),
    )
    return pl.DataFrame({"inflow_MW": hourly_mw.to_numpy(), "timestamp": target_index})


def process_pecd_inflow(country: str, year: int, output_path: str | Path | None = None) -> Path:
    """Read the staged HRI CSV and write ``inflow_<country>_<year>.parquet``.

    Args:
        country: Two-letter repo country code.
        year: Climate year.
        output_path: Optional explicit output path (defaults to the canonical
            ``RESULTS_DIR/inflow/inflow_<country>_<year>.parquet``).

    Returns:
        The output ``Path`` (a valid empty-schema Parquet on missing/bad input).
    """
    out = Path(output_path) if output_path else RESULTS_DIR / "inflow" / f"inflow_{country}_{year}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)

    csv = find_pecd_csv("HRI", year)
    if csv is None:
        logger.warning("PECD HRI: no staged HRI CSV for %s; writing empty inflow to %s.", year, out)
        _empty_inflow().write_parquet(out)
        return out

    try:
        hri_df = pd.read_csv(csv, comment="#", index_col="Date")
        df = pecd_reservoir_inflow(hri_df, country, year)
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("PECD HRI: failed for %s %s (%s); writing empty inflow.", country, year, exc)
        df = _empty_inflow()

    df.write_parquet(out)
    logger.info("PECD HRI: wrote %d rows to %s.", df.height, out)
    return out


if __name__ == "__main__":
    try:
        country = snakemake.params.country  # type: ignore[name-defined]
        year = int(snakemake.params.year)  # type: ignore[name-defined]
        output_path = snakemake.output[0]  # type: ignore[name-defined]
    except NameError:
        logger.info("Not running under Snakemake; standalone debug run.")
        country, year, output_path = "FR", 2008, None
    process_pecd_inflow(country, year, output_path)
