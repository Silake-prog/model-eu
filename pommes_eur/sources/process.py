#!/usr/bin/env python3
"""
process.py — CLEVER spreadsheet processing module.

Role in pipeline
----------------
This module reads the raw CLEVER Excel workbook (one sheet per country)
and extracts structured CSV tables used by the modelling pipeline:

  1. **demand_by_sector_resource.csv** — sectoral electricity / gas / H₂ demand (TWh)
  2. **energy_conversion_tech_capacity.csv** — VRE installed capacities (GW → kept as raw value)
  3. **energy_conversion_tech_load_factor.csv** — VRE load factors (dimensionless)
  4. **non_enr.csv** — dispatchable generation (TWh)
  5. **power_to_liquid.csv** — power-to-X production (TWh)
  6. **clever_end_use_electricity.csv** — end-use splits for DemandForge (TWh)

These CSVs are the bridge between the CLEVER scenario assumptions and the
POMMES / EOLES / DemandForge modelling chain.

Design notes
------------
* The module is a refactored, more robust version of ``export_clever_csv.py``.
* All CLEVER indicator codes are declared once in indicator registries.
* Sheet detection and header discovery are tolerant of CLEVER layout quirks
  (varying header row, non-breaking spaces, mixed case).
* Each output table is fully described by a ``TableSpec`` dataclass.

Usage
-----
.. code-block:: python

    from pommes_eur.sources.process import process_clever_xlsx

    tables = process_clever_xlsx("Data_CLEVER.xlsx", output_dir="output")
    demand_df = tables["demand"]
    capacity_df = tables["capacity"]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

import pommes_eur as clever
from pommes_eur.constants import AREA_MAP

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# Constants
# ════════════════════════════════════════════════════════════════════
_YEAR_RE = re.compile(r"^\d{4}$")
_YEAR_FLOAT_RE = re.compile(r"^\d{4}\.0$")

# ════════════════════════════════════════════════════════════════════
# Indicator code registry
# ════════════════════════════════════════════════════════════════════
# Each entry: (label, indicator_code)
# "label" is a human-readable name used as the value in a categorical column.
# "indicator_code" is the CLEVER code found in the spreadsheet.

DEMAND_INDICATORS: list[tuple[str, str, str]] = [
    # (sector, resource, code)
    ("tertiary",  "Electricity",                     "elccfter"),
    ("tertiary",  "Gas grid / gas consumed locally", "gazcfter"),
    ("transport", "Electricity",                     "elccftra"),
    ("transport", "Gas grid / gas consumed locally", "gazcftra"),
    ("transport", "Hydrogen",                        "hydcftra"),
    ("industry",  "Electricity",                     "elccfind"),
    ("industry",  "Gas grid / gas consumed locally", "gazcfind"),
    ("industry",  "Hydrogen",                        "hydcfind"),
]

CAPACITY_INDICATORS: list[tuple[str, str]] = [
    # (tech, code)
    ("pv",         "caispv"),
    ("onshore",    "caieon"),
    ("offshore",   "caieof"),
    ("marine",     "caienm"),
    ("geothermal", "caight"),
    ("hydro",      "caihdr"),
]

LOAD_FACTOR_INDICATORS: list[tuple[str, str]] = [
    ("pv",         "fchspv"),
    ("onshore",    "fcheon"),
    ("offshore",   "fcheof"),
    ("marine",     "fchenm"),
    ("geothermal", "fchght"),
]

THERMAL_INDICATORS: list[tuple[str, str]] = [
    # (energy_source, code)
    ("nuke",    "proelcnuc"),
    ("coal",    "proelccms"),
    ("oil",     "proelcpet"),
    ("gas",     "proelcgaz"),
    ("biomass", "proelcboi"),
    ("waste",   "proelcwst"),
    ("ccg-h2",  "proelchyd"),
]

POWER_TO_LIQUID_INDICATORS: list[tuple[str, str]] = [
    ("electrolysis_h2", "prohyd"),
    ("methanation_ch4", "prohydmet"),
    ("e_liquid",        "prohydcl"),
]

END_USE_ELEC_INDICATORS: list[tuple[str, str]] = [
    ("res_space_heating_elec",    "elccfreschf"),
    ("passenger_mobility_elec",   "elccfmob"),
    ("res_cooling_total",         "toccfrescli"),
]

# Flat list of all known codes — used for auto-detecting the indicator column.
_ALL_KNOWN_CODES: list[str] = [
    code
    for group in (
        [c for _, _, c in DEMAND_INDICATORS],
        [c for _, c in CAPACITY_INDICATORS],
        [c for _, c in LOAD_FACTOR_INDICATORS],
        [c for _, c in THERMAL_INDICATORS],
        [c for _, c in POWER_TO_LIQUID_INDICATORS],
        [c for _, c in END_USE_ELEC_INDICATORS],
    )
    for code in group
]
_ALL_KNOWN_CODES_LOWER: list[str] = [c.lower() for c in _ALL_KNOWN_CODES]


# ════════════════════════════════════════════════════════════════════
# Table specification
# ════════════════════════════════════════════════════════════════════
@dataclass
class TableSpec:
    """Declarative description of one output CSV table.

    Attributes
    ----------
    name : str
        Key in the returned dict *and* CSV filename stem.
    indicators : list
        Tuples of (label1, [label2, ...], indicator_code).
    dim_columns : list[tuple[str, int]]
        Categorical columns to create.  Each entry is
        ``(column_name, tuple_position)`` — the value at ``tuple_position``
        in the indicator tuple becomes the value in the output column.
    value_column : str
        Name of the numeric column extracted from the year columns.
    output_columns : list[str]
        Final column order in the CSV.
    sort_keys : list[str]
        Columns to sort the output by.
    csv_filename : str
        Output filename.
    """
    name: str
    indicators: list
    dim_columns: list[tuple[str, int]]
    value_column: str
    output_columns: list[str]
    sort_keys: list[str]
    csv_filename: str


TABLE_SPECS: list[TableSpec] = [
    TableSpec(
        name="demand",
        indicators=DEMAND_INDICATORS,
        dim_columns=[("sector", 0), ("resource", 1)],
        value_column="value",
        output_columns=["area", "year_op", "sector", "resource", "value"],
        sort_keys=["area", "sector", "resource", "year_op"],
        csv_filename="demand_by_sector_resource.csv",
    ),
    TableSpec(
        name="capacity",
        indicators=CAPACITY_INDICATORS,
        dim_columns=[("conversion_tech", 0)],
        value_column="value",
        output_columns=["area", "year_op", "conversion_tech", "value"],
        sort_keys=["area", "conversion_tech", "year_op"],
        csv_filename="energy_conversion_tech_capacity.csv",
    ),
    TableSpec(
        name="load_factor",
        indicators=LOAD_FACTOR_INDICATORS,
        dim_columns=[("conversion_tech", 0)],
        value_column="load_factor",
        output_columns=["area", "year_op", "conversion_tech", "load_factor"],
        sort_keys=["area", "conversion_tech", "year_op"],
        csv_filename="energy_conversion_tech_load_factor.csv",
    ),
    TableSpec(
        name="thermal",
        indicators=THERMAL_INDICATORS,
        dim_columns=[("energy_source", 0)],
        value_column="energyproducedTWH",
        output_columns=["area", "year_op", "energy_source", "energyproducedTWH"],
        sort_keys=["area", "energy_source", "year_op"],
        csv_filename="non_enr.csv",
    ),
    TableSpec(
        name="ptl",
        indicators=POWER_TO_LIQUID_INDICATORS,
        dim_columns=[("ptX", 0)],
        value_column="energyproducedTWH",
        output_columns=["area", "year_op", "ptX", "energyproducedTWH"],
        sort_keys=["area", "ptX", "year_op"],
        csv_filename="power_to_liquid.csv",
    ),
    TableSpec(
        name="end_use",
        indicators=END_USE_ELEC_INDICATORS,
        dim_columns=[("end_use", 0)],
        value_column="value",
        output_columns=["area", "year_op", "end_use", "value"],
        sort_keys=["area", "end_use", "year_op"],
        csv_filename="clever_end_use_electricity.csv",
    ),
]


# ════════════════════════════════════════════════════════════════════
# Low-level helpers
# ════════════════════════════════════════════════════════════════════
def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase, strip, and replace spaces/hyphens with underscores.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame.

    Returns
    -------
    pd.DataFrame
        DataFrame with normalized column names.
    """
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace("\xa0", " ").replace(" ", "_").replace("-", "_")
        for c in df.columns
    ]
    return df


def _year_columns(df: pd.DataFrame) -> list:
    """Return column names that look like calendar years (e.g. 2015, 2050).

    Handles both integer years (2015) and float years (2015.0).

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame.

    Returns
    -------
    list
        Column names that match year patterns.
    """
    years = []
    for c in df.columns:
        s = str(c).strip()
        if _YEAR_RE.match(s) or _YEAR_FLOAT_RE.match(s):
            years.append(c)
    return years


def _to_number(x: Any) -> float:
    """Robust scalar → float conversion. Handles percentages and commas.

    Parameters
    ----------
    x : Any
        Input value.

    Returns
    -------
    float
        Converted numeric value, or NaN if conversion fails.

    Notes
    -----
    Handles:
    - Percentages: "50%" → 0.5
    - European comma notation: "1,5" → 1.5
    - Standard float strings: "1.5" → 1.5
    """
    if pd.isna(x):
        return np.nan
    s = str(x).strip()
    if s == "":
        return np.nan
    if s.endswith("%"):
        s2 = s[:-1].strip().replace(",", ".")
        try:
            return float(s2) / 100.0
        except ValueError:
            return np.nan
    s2 = s.replace(",", ".")
    try:
        return float(s2)
    except ValueError:
        return np.nan


def _find_indicator_code_column(
    df: pd.DataFrame,
    all_known_codes_lower: list[str],
) -> str:
    """Auto-detect the column containing CLEVER indicator codes.

    Strategy
    --------
    1. Look for a column whose header contains *indicator* and *code*.
    2. Failing that, look for the column with the most matches against
       known codes in its first 200 rows.

    Parameters
    ----------
    df : pd.DataFrame
        Sheet DataFrame.
    all_known_codes_lower : list[str]
        All valid CLEVER indicator codes (lowercase).

    Returns
    -------
    str
        Column name.

    Raises
    ------
    ValueError
        If no plausible indicator column is found.
    """
    cols = list(df.columns)

    # Strategy 1 — by header name
    name_candidates = [
        c for c in cols
        if "indicator" in str(c).lower().replace(" ", "_")
        and "code" in str(c).lower().replace(" ", "_")
    ]
    if name_candidates:
        for target in ("indicator_code", "indicatorcode"):
            for c in name_candidates:
                if str(c).strip().lower().replace(" ", "_") == target:
                    return c
        return name_candidates[0]

    # Strategy 2 — by content match
    sample = df.head(200)
    best_col, best_hits = None, 0
    for c in cols:
        hits = int(
            sample[c].astype(str).str.strip().str.lower().isin(all_known_codes_lower).sum()
        )
        if hits > best_hits:
            best_hits, best_col = hits, c

    if best_col is not None and best_hits >= 1:
        return best_col

    raise ValueError(
        f"Cannot find indicator-code column. First 20 column names: {cols[:20]}"
    )


def _detect_header_row(
    xlsx_path: Path,
    sheet_name: str,
    max_scan: int = 50,
) -> Optional[int]:
    """Scan the first *max_scan* rows looking for the CLEVER header row.

    The header row is identified by the co-presence of the words
    *indicator* and *code* in the same row.

    Parameters
    ----------
    xlsx_path : Path
        Path to the Excel file.
    sheet_name : str
        Name of the sheet to scan.
    max_scan : int
        Maximum number of rows to scan (default: 50).

    Returns
    -------
    Optional[int]
        Row index (0-based) of the header row, or None if not found.
        If None, Pandas' default header=0 is the safest fallback.
    """
    raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None, nrows=max_scan)
    for i in range(len(raw)):
        row_text = (
            raw.iloc[i]
            .fillna("")
            .astype(str)
            .str.lower()
            .str.replace("\xa0", " ", regex=False)
            .str.strip()
        )
        joined = " | ".join(row_text.tolist())
        if "indicator" in joined and "code" in joined:
            return i
    return None


def _read_sheet(xlsx_path: Path, sheet_name: str) -> pd.DataFrame:
    """Read one CLEVER sheet with automatic header detection.

    Parameters
    ----------
    xlsx_path : Path
        Path to the Excel file.
    sheet_name : str
        Name of the sheet to read.

    Returns
    -------
    pd.DataFrame
        Sheet data.
    """
    header_row = _detect_header_row(xlsx_path, sheet_name)
    if header_row is not None:
        return pd.read_excel(xlsx_path, sheet_name=sheet_name, header=header_row)
    return pd.read_excel(xlsx_path, sheet_name=sheet_name)


# ════════════════════════════════════════════════════════════════════
# Extraction logic
# ════════════════════════════════════════════════════════════════════
def _extract_one_code(
    df_norm: pd.DataFrame,
    code_col: str,
    years: list,
    indicator_code: str,
    value_name: str,
) -> pd.DataFrame:
    """Pull a single time-series row from the normalized sheet.

    Parameters
    ----------
    df_norm : pd.DataFrame
        Normalized sheet DataFrame.
    code_col : str
        Name of the indicator code column.
    years : list
        List of year column names.
    indicator_code : str
        The indicator code to extract.
    value_name : str
        Name for the numeric column in output.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns [year_op, value_name].
    """
    mask = df_norm[code_col].astype(str).str.strip().str.lower() == indicator_code.lower()
    sel = df_norm.loc[mask]
    if sel.empty:
        return pd.DataFrame(columns=["year_op", value_name])

    row = sel.iloc[0][years]
    out = pd.DataFrame({
        "year_op": years,
        value_name: [_to_number(v) for v in row.values],
    }).dropna(subset=[value_name], how="all")

    out["year_op"] = out["year_op"].apply(lambda x: int(float(str(x))))
    return out


def _extract_block(
    area: str,
    df_sheet: pd.DataFrame,
    specs: list[TableSpec],
) -> dict[str, pd.DataFrame]:
    """Extract multiple tables from a single country sheet.

    Parameters
    ----------
    area : str
        Country or region name (used as the 'area' column value).
    df_sheet : pd.DataFrame
        Sheet DataFrame.
    specs : list[TableSpec]
        List of TableSpec objects defining what to extract.

    Returns
    -------
    dict[str, pd.DataFrame]
        Mapping from spec.name to extracted DataFrame.
    """
    df_norm = _normalize_columns(df_sheet)
    year_cols = _year_columns(df_norm)
    if not year_cols:
        logger.warning("[%s] No year columns found.", area)
        return {spec.name: pd.DataFrame(columns=spec.output_columns) for spec in specs}

    try:
        code_col = _find_indicator_code_column(df_norm, _ALL_KNOWN_CODES_LOWER)
    except ValueError:
        logger.warning("[%s] Cannot find indicator column.", area)
        return {spec.name: pd.DataFrame(columns=spec.output_columns) for spec in specs}

    results = {}
    for spec in specs:
        rows: list[pd.DataFrame] = []
        for indicator_tuple in spec.indicators:
            *_dim_values, code = indicator_tuple  # last element is always the code
            series_df = _extract_one_code(
                df_norm, code_col, year_cols, code, spec.value_column
            )
            if series_df.empty:
                continue

            # Normalize area codes at source (e.g. UK → GB, EL → GR)
            normalized_area = AREA_MAP.get(area.upper(), area.upper())
            series_df.insert(0, "area", normalized_area)
            for col_name, idx in spec.dim_columns:
                series_df[col_name] = indicator_tuple[idx]
            rows.append(series_df)

        if not rows:
            results[spec.name] = pd.DataFrame(columns=spec.output_columns)
            continue

        combined = pd.concat(rows, ignore_index=True)
        # Ensure only declared columns are in output (guard against extras)
        for col in spec.output_columns:
            if col not in combined.columns:
                combined[col] = np.nan
        combined = combined[spec.output_columns]

        if combined.empty:
            results[spec.name] = combined
        else:
            results[spec.name] = combined.sort_values(spec.sort_keys).reset_index(drop=True)

    return results


# ════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════
def process_clever_xlsx(
    xlsx_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    write_csv: bool = True,
) -> dict[str, pd.DataFrame]:
    """Read and process the CLEVER Excel workbook into structured tables.

    Parameters
    ----------
    xlsx_path : str or Path
        Path to ``Data_CLEVER.xlsx``.
    output_dir : str, Path, or None
        Directory where CSVs are written (created if absent).
        If None, uses clever.CLEVER_CSV_DIR.
    write_csv : bool
        If True (default), each table is written to output_dir.

    Returns
    -------
    dict[str, pd.DataFrame]
        Mapping from table name to DataFrame.  Keys match
        TableSpec.name: demand, capacity, load_factor, thermal, ptl, end_use.

    Examples
    --------
    >>> tables = process_clever_xlsx("Data_CLEVER.xlsx", output_dir="output")
    >>> demand_df = tables["demand"]
    >>> demand_df.head()
    """
    xlsx_path = Path(xlsx_path)
    if output_dir is None:
        output_dir = clever.CLEVER_CSV_DIR
    else:
        output_dir = Path(output_dir)

    if not xlsx_path.exists():
        raise FileNotFoundError(f"CLEVER spreadsheet not found: {xlsx_path}")

    if write_csv:
        output_dir.mkdir(parents=True, exist_ok=True)

    xl = pd.ExcelFile(xlsx_path)
    sheets = xl.sheet_names
    logger.info(
        "Processing CLEVER workbook %s — %d sheet(s): %s",
        xlsx_path.name,
        len(sheets),
        ", ".join(sheets[:10]) + ("…" if len(sheets) > 10 else ""),
    )

    # Accumulate per-table across all sheets (= countries)
    accumulators: dict[str, list[pd.DataFrame]] = {spec.name: [] for spec in TABLE_SPECS}

    for sheet_name in sheets:
        df_sheet = _read_sheet(xlsx_path, sheet_name)
        block = _extract_block(sheet_name, df_sheet, TABLE_SPECS)
        for spec in TABLE_SPECS:
            table_df = block[spec.name]
            if not table_df.empty:
                accumulators[spec.name].append(table_df)

    # Concatenate and write
    results: dict[str, pd.DataFrame] = {}
    for spec in TABLE_SPECS:
        parts = accumulators[spec.name]
        if parts:
            df = pd.concat(parts, ignore_index=True)
            df = df.sort_values(spec.sort_keys).reset_index(drop=True)
        else:
            df = pd.DataFrame(columns=spec.output_columns)

        results[spec.name] = df

        if write_csv:
            csv_path = output_dir / spec.csv_filename
            df.to_csv(csv_path, index=False)
            logger.info("  → %s  (%d rows)", csv_path.name, len(df))

    logger.info("CLEVER processing complete — %d tables extracted.", len(results))
    return results


# ════════════════════════════════════════════════════════════════════
# CLI entry point
# ════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m clever.process /path/to/Data_CLEVER.xlsx [output_dir]")
        sys.exit(1)

    _xlsx = sys.argv[1]
    _out = sys.argv[2] if len(sys.argv) >= 3 else None
    process_clever_xlsx(_xlsx, _out)
