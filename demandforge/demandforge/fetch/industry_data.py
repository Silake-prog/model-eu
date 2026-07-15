"""
Fetch industry-specific demand data: steel, chemicals (ammonia, methanol, olefins, refineries).

Data sources:
- JRC-IDEES 2023 (EU steel, chemicals demand)
- TYNDP 2024 scenarios (steel production roadmaps)
- USGS Minerals Commodities (ammonia production data, circa 2019)
- Eurostat (refinery output, energy balances)
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import zipfile
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Any, Dict, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Data source URLs as module-level constants
JRC_IDEES_BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/JRC-IDEES/JRC-IDEES-2023_v1/"
TYNDP_2024_URL = "https://2024-data.entsos-tyndp-scenarios.eu/files/scenarios-inputs/Demand_Scenarios_TYNDP_2024_After_Public_Consultation.xlsb.zip"

# Eurostat REST API for energy balance data (nrg_bal_c dataset)
# IMPORTANT: We use nrg_bal_c (Complete energy balances, 139 categories, 72 SIEC products),
# NOT nrg_bal_s (Simplified energy balances, 92 categories, 12 SIEC products).
# nrg_bal_c contains the detailed refinery flow TO_RPI_RO (Transformation output —
# refineries and petrochemical industry) needed for refinery H2 demand estimation.
# NOTE: The correct Eurostat API endpoint uses the statistics API v1.0 or SDMX 2.1.
# The TSV bulk download format is:
#   https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/nrg_bal_c?format=TSV
# For filtered queries (e.g. refinery output only):
#   https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_bal_c?nrg_bal=TI_RPI_RO&siec=TOTAL&unit=KTOE&format=JSON
EUROSTAT_NRG_BAL_TSV_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/nrg_bal_c?format=TSV"
EUROSTAT_NRG_BAL_JSON_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_bal_c"

# Despite the historical name, this list now includes "GB" (United Kingdom)
# for POMMES area-coord compatibility (2026-05-12). All sector base-data dicts
# below iterate over this list, so adding GB once propagates downstream.
EU27_COUNTRIES = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "GB",  # United Kingdom — added 2026-05-12 for POMMES area-coord compatibility
]

# Critical EU countries requiring monitoring for zero defaults
_CRITICAL_COUNTRIES = {"DE", "FR", "IT", "ES", "PL", "NL", "BE", "AT", "SE", "CZ", "GB"}

# Data vintage: year when static data was acquired or last validated
_DATA_VINTAGE = 2019

# USGS Ammonia production data (2019, in megatonnes Mt/yr)
# Source: USGS Mineral Commodity Summaries 2020, Nitrogen (Ammonia) section.
# Values are in Mt (megatonnes = million tonnes per year).
# Countries not listed (CY, DK, IE, LU, LV, MT, PT, SE, SI) have negligible or zero
# ammonia production and intentionally default to 0.
_USGS_AMMONIA_2019_MT = {
    "AT": 0.55,
    "BE": 1.75,
    "BG": 0.96,
    "HR": 0.0,
    "CZ": 0.71,
    "EE": 0.0,
    "FI": 0.35,
    "FR": 1.89,
    "DE": 2.71,
    "GR": 0.0,
    "HU": 0.38,
    "IT": 0.08,
    "NL": 1.05,
    "PL": 1.14,
    "RO": 0.0,
    "SK": 0.45,
    "ES": 0.0,
    "GB": 1.0,  # CF Industries Billingham (closed 2020); 2019 production ~1.0 Mt. USGS MCS 2020.
}

# Methanol production data (2019, in tonnes)
# Countries not listed have negligible or zero methanol production.
# Source: European chemical industry surveys, circa 2019. Coverage: 11/27 EU countries.
_METHANOL_PRODUCTION_T = {
    "AT": 33000,
    "BE": 110000,
    "DE": 120000,
    "ES": 45000,
    "FR": 50000,
    "IT": 28000,
    "NL": 90000,
    "PL": 35000,
    "SE": 25000,
    "SK": 42000,
    "GB": 50000,  # Brexit but included for historical completeness (renamed UK→GB 2026-05-12)
}

# Eurostat nrg_bal_c — Refinery transformation output (TO_RPI_RO), 2019, KTOE.
# Source: Eurostat Complete Energy Balances (nrg_bal_c), flow TO_RPI_RO
#         (Transformation output — Refineries), product TOTAL, unit KTOE.
# Data vintage: 2019.  Greece uses ISO-2 code "GR" (Eurostat uses "EL").
#
# Countries with no operational refinery in 2019 (CY, EE, LV, LU, MT, SI)
# default to 0.0.  Values are gross refinery output in kilotonnes of oil
# equivalent per year.
#
# NOTE: These are fallback values for standalone testing.  For production
#       runs, provide a CSV via ``source_path`` or use
#       ``fetch_eurostat_refinery_output()`` with a fresh Eurostat bulk
#       download to ensure the latest revisions.
_EUROSTAT_REFINERY_OUTPUT_2019_KTOE: Dict[str, float] = {
    "AT":   8_987.0,    # OMV Schwechat
    "BE":  30_891.0,    # Antwerp cluster (TotalEnergies, ExxonMobil, etc.)
    "BG":   5_468.0,    # Lukoil Neftochim Burgas
    "HR":   3_618.0,    # INA Rijeka/Sisak
    "CY":       0.0,    # No refinery
    "CZ":   7_629.0,    # Unipetrol Litvínov, Česká rafinérská Kralupy
    "DK":   7_141.0,    # Equinor Kalundborg
    "EE":       0.0,    # No refinery
    "FI":  11_754.0,    # Neste Porvoo, Naantali
    "FR":  56_338.0,    # TotalEnergies (Normandie, Donges, Feyzin, etc.)
    "DE": 105_262.0,    # Largest EU refiner (Rhineland, Gelsenkirchen, etc.)
    "GR":  22_716.0,    # Hellenic Petroleum (Aspropyrgos, Elefsina, Thessaloniki)
    "HU":   7_256.0,    # MOL Százhalombatta
    "IE":   3_158.0,    # Irving Oil Whitegate
    "IT":  76_892.0,    # ENI/Saras/API (multiple sites)
    "LV":       0.0,    # No refinery
    "LT":   9_473.0,    # ORLEN Lietuvos Energija (Mažeikiai)
    "LU":       0.0,    # No refinery
    "MT":       0.0,    # No refinery
    "NL":  62_156.0,    # Shell Pernis, ExxonMobil Rotterdam, BP Europoort
    "PL":  27_584.0,    # ORLEN Płock, Lotos Gdańsk
    "PT":  12_896.0,    # Galp Sines, Matosinhos
    "RO":  10_368.0,    # Petromidia, Petrobrazi, Arpechim
    "SK":   5_726.0,    # Slovnaft Bratislava
    "SI":       0.0,    # No refinery
    "ES":  66_542.0,    # Repsol (multiple), Cepsa, BP
    "SE":  19_802.0,    # Preem Lysekil/Göteborg, St1 Gothenburg
    "GB":  57_800.0,    # Stanlow, Fawley, Lindsey, Humber, Pembroke, Grangemouth — UK DUKES 2020 Table 3.1
}

# EAF (Electric Arc Furnace) share of crude steel production by country, 2019.
# Source: Worldsteel "World Steel in Figures 2020" and Eurofer "European Steel
# in Figures 2020", cross-referenced with Global Efficiency Intelligence
# (Hasanbeigi 2022).  EAF = secondary steelmaking from scrap.
#
# Countries with zero production (CY, DK, EE, IE, MT) default to 0.0 (no steel
# industry).  Small producers with only EAF mini-mills (HR, LU, LV, PT, SI)
# default to 1.0.  These are base-year shares; scenario projections use
# ``compute_eaf_scrap_series()`` to ramp toward targets.
_EAF_SHARE_2019: Dict[str, float] = {
    "AT": 0.11,   # voestalpine BF dominates; small EAF at Breitenfeld
    "BE": 0.29,   # ArcelorMittal BF at Ghent; EAF at Charleroi
    "BG": 0.80,   # STOMANA, Promet Steel — predominantly EAF
    "HR": 1.00,   # ABS Sisak (small EAF only)
    "CY": 0.00,   # No crude steel production
    "CZ": 0.10,   # Třinecké/Arcelor BF; small EAF at Vítkovice
    "DK": 0.00,   # No crude steel production
    "EE": 0.00,   # No crude steel production
    "FI": 0.65,   # Outokumpu EAF (stainless); SSAB BF at Raahe
    "FR": 0.37,   # ArcelorMittal BF at Dunkirk/Fos; EAF at multiple sites
    "DE": 0.30,   # ThyssenKrupp/Salzgitter/ArcelorMittal BF; ~13 Mt EAF
    "GR": 0.68,   # Sidenor, Halyvourgiki — mostly EAF
    "HU": 0.28,   # ISD Dunaferr BF; small EAF
    "IE": 0.00,   # No crude steel production
    "IT": 0.82,   # Largest EU EAF share; Acciaierie d'Italia BF at Taranto
    "LV": 1.00,   # Liepajas Metalurgs — small EAF
    "LT": 0.00,   # Negligible steel production
    "LU": 1.00,   # ArcelorMittal EAF only (no BF)
    "MT": 0.00,   # No crude steel production
    "NL": 0.00,   # Tata Steel IJmuiden — 100% BF-BOF
    "PL": 0.52,   # ArcelorMittal BF at Dąbrowa; large EAF sector
    "PT": 1.00,   # Megasa, SN Seixal — EAF only
    "RO": 0.35,   # ArcelorMittal BF at Galati; some EAF
    "SK": 0.28,   # U.S. Steel Košice BF; small EAF
    "SI": 1.00,   # SIJ Group — EAF only
    "ES": 0.65,   # ArcelorMittal BF at Asturias; large EAF sector
    "SE": 0.37,   # SSAB BF at Luleå/Oxelösund; Ovako/Sandvik EAF
    "GB": 0.22,   # Tata Steel Port Talbot/Scunthorpe BF; Liberty Steel + Celsa EAF
}

# World Steel Association — crude steel production by country, 2019, in kt.
# Source: World Steel in Figures 2020 (worldsteel.org).
# Values are in kilotonnes (1 kt = 1,000 t).
# Countries with no reported crude steel production default to 0.
#
# NOTE: These are fallback values for standalone testing without JRC-IDEES
#       files.  For production runs, prefer ``fetch_jrc_idees_steel_base()``
#       with actual JRC-IDEES data.
_CRUDE_STEEL_2019_KT: Dict[str, float] = {
    "AT":  7_422.0,  # voestalpine (Linz, Donawitz)
    "BE":  7_825.0,  # ArcelorMittal (Ghent, Liège)
    "BG":    616.0,  # STOMANA, Promet Steel
    "HR":     60.0,  # ABS Sisak
    "CY":      0.0,  # No crude steel production
    "CZ":  4_597.0,  # Třinecké, ArcelorMittal Ostrava
    "DK":      0.0,  # No crude steel production
    "EE":      0.0,  # No crude steel production
    "FI":  3_564.0,  # SSAB Raahe, Outokumpu Tornio
    "FR": 14_450.0,  # ArcelorMittal (Dunkirk, Fos), Ascometal
    "DE": 39_671.0,  # ThyssenKrupp, Salzgitter, ArcelorMittal
    "GR":  1_315.0,  # Sidenor, Halyvourgiki
    "HU":  1_664.0,  # ISD Dunaferr (Dunaújváros)
    "IE":      0.0,  # No crude steel production
    "IT": 23_200.0,  # Acciaierie d'Italia (Taranto), Arvedi, Beltrame
    "LV":    145.0,  # Liepajas Metalurgs
    "LT":      0.0,  # No crude steel production
    "LU":  2_185.0,  # ArcelorMittal (EAF only)
    "MT":      0.0,  # No crude steel production
    "NL":  6_720.0,  # Tata Steel (IJmuiden)
    "PL":  9_050.0,  # ArcelorMittal (Dąbrowa), Celsa Huta Ostrowiec
    "PT":  2_264.0,  # Megasa, SN Seixal
    "RO":  3_389.0,  # ArcelorMittal (Galati), Donasid
    "SK":  5_042.0,  # U.S. Steel (Košice)
    "SI":    627.0,  # SIJ Group (Jesenice, Ravne)
    "ES": 13_631.0,  # ArcelorMittal (Asturias), Celsa, Sidenor
    "SE":  4_695.0,  # SSAB (Luleå, Oxelösund), Ovako, Sandvik
    "GB":  7_229.0,  # Tata Steel Port Talbot, British Steel Scunthorpe — World Steel in Figures 2020
}

# Cache dictionary for fetch results
_CACHE: Dict[str, pd.DataFrame] = {}

# Optional disk cache directory (from RESULTS_DIR environment variable if available)
_disk_cache_dir: Path | None = None
try:
    from pathlib import Path
    results_dir = Path(__file__).parent.parent.parent / "RESULTS_DIR"
    if results_dir.exists():
        _disk_cache_dir = results_dir / ".cache"
        _disk_cache_dir.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _validate_path(path: str | Path | None, expected_ext: str | None = None) -> Path | None:
    """Validate and normalize a file path.

    Args:
        path: File path to validate (str, Path, or None).
        expected_ext: Optional file extension to check (e.g., '.csv', '.xlsx').
            If provided and path exists, raises ValueError if extension doesn't match.

    Returns:
        Path object if valid, None if input is None.

    Raises:
        ValueError: If path exists but has unexpected extension.
        FileNotFoundError: If expected_ext is provided but path doesn't exist.
    """
    if path is None:
        return None

    p = Path(path)

    if expected_ext is not None and p.exists():
        if not p.suffix.lower() == expected_ext.lower():
            raise ValueError(
                f"Expected file extension '{expected_ext}', got '{p.suffix}' for {p}"
            )

    return p


def _get_cache(key: str) -> pd.DataFrame | None:
    """Retrieve cached DataFrame if present.

    Returns:
        Cached DataFrame or None if not found.
    """
    return _CACHE.get(key)


def _set_cache(key: str, df: pd.DataFrame) -> None:
    """Store DataFrame in cache and optionally write to disk.

    Args:
        key: Cache key identifier.
        df: DataFrame to cache.
    """
    _CACHE[key] = df.copy()

    # Optionally persist to disk cache
    if _disk_cache_dir is not None:
        try:
            cache_file = _disk_cache_dir / f"{key}.parquet"
            df.to_parquet(cache_file, index=False)
            logger.debug(f"Persisted cache '{key}' to disk: {cache_file}")
        except Exception as e:
            logger.warning(f"Failed to write disk cache for '{key}': {e}")


def _clear_cache() -> None:
    """Clear all cached data from memory and disk."""
    _CACHE.clear()
    if _disk_cache_dir is not None:
        try:
            import shutil
            if _disk_cache_dir.exists():
                shutil.rmtree(_disk_cache_dir)
                _disk_cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Failed to clear disk cache: {e}")


def _check_data_vintage(reference_year: int) -> None:
    """Log warning if static data is > 2 years old relative to reference year.

    Args:
        reference_year: Reference year for the analysis.
    """
    age = reference_year - _DATA_VINTAGE
    if age > 2:
        logger.warning(
            f"Static data vintage is {_DATA_VINTAGE} (age: {age} years relative to "
            f"reference_year={reference_year}). Consider updating source data."
        )


# ---------------------------------------------------------------------------
# JRC-IDEES file structure conventions
# ---------------------------------------------------------------------------
# The JRC-IDEES database is published as per-country ZIP archives on an FTP
# server.  Each ZIP contains Excel workbooks for different sectors.
#
# URL template (v2023):
#   {JRC_IDEES_BASE_URL}/{country_el}/JRC-IDEES-2021_Industry_{country_el}.xlsx
#
# Where {country_el} uses Eurostat convention (GR → EL for Greece).
#
# The workbooks contain sheets per industrial subsector.  For steel:
#   Sheet: "ISI" (Iron and Steel Industry)
#   Row 5: Total production (physical output in kt)
#   Subsectors: "Integrated steelworks" (BF-BOF), "Electric arc" (EAF)
#
# Sector mapping codes:
_JRC_IDEES_SECTOR_CODES = {
    "ISI": "Iron and steel",
    "NFM": "Non-ferrous metals",
    "CHI": "Chemical industry",
    "NMM": "Non-metallic mineral products",
    "PPA": "Pulp, paper and printing",
    "FBT": "Food, beverages and tobacco",
    "TRE": "Transport equipment",
    "MAE": "Machinery equipment",
    "TEL": "Textiles and leather",
    "WWP": "Wood and wood products",
    "OIS": "Other industrial sectors",
}

# Steel subsectors (rows within "ISI" sheet)
_JRC_IDEES_STEEL_SUBSECTORS = {
    "ISI": ["Integrated steelworks", "Electric arc"],
}

# JRC-IDEES version used for URL construction.
# The 2023 release still uses "JRC-IDEES-2021" in filenames.
_JRC_IDEES_FILE_VERSION = "JRC-IDEES-2021"

# ISO-2 → Eurostat country code mapping
_ISO2_TO_EUROSTAT = {"GR": "EL"}
_EUROSTAT_TO_ISO2 = {"EL": "GR"}


def _iso2_to_eurostat(country: str) -> str:
    """Convert ISO 3166-1 alpha-2 code to Eurostat convention."""
    return _ISO2_TO_EUROSTAT.get(country, country)


def _eurostat_to_iso2(country: str) -> str:
    """Convert Eurostat country code back to ISO-2."""
    return _EUROSTAT_TO_ISO2.get(country, country)


# ---------------------------------------------------------------------------
# JRC-IDEES download / extraction utilities
# ---------------------------------------------------------------------------

def _get_jrc_idees_cache_dir(
    jrc_idees_path: str | Path | None = None,
) -> Path:
    """Resolve the local directory for cached JRC-IDEES files.

    Priority:
    1. Explicit ``jrc_idees_path`` if provided and exists.
    2. Environment variable ``DEMANDFORGE_DATA_DIR`` / ``JRC-IDEES``.
    3. Platform cache dir ``~/.cache/demandforge/JRC-IDEES``.

    Returns:
        Path to the JRC-IDEES data directory (created if needed).
    """
    if jrc_idees_path is not None:
        p = Path(jrc_idees_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    env_dir = os.environ.get("DEMANDFORGE_DATA_DIR")
    if env_dir:
        p = Path(env_dir) / "JRC-IDEES"
        p.mkdir(parents=True, exist_ok=True)
        return p

    p = Path.home() / ".cache" / "demandforge" / "JRC-IDEES"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _download_jrc_idees_country(
    country: str,
    cache_dir: Path,
    version: str = _JRC_IDEES_FILE_VERSION,
    base_url: str = JRC_IDEES_BASE_URL,
    force: bool = False,
) -> Path:
    """Download and extract JRC-IDEES data for a single country.

    Downloads the per-country ZIP archive from the JRC FTP server, extracts
    the Industry Excel workbook, and returns the path to the extracted file.

    The archive URL follows the pattern::

        {base_url}/{country_el}/{version}_Industry_{country_el}.xlsx

    Some editions serve the file directly (no ZIP).  Others serve a ZIP
    containing the Excel file.  This function handles both.

    Args:
        country: ISO-2 country code (e.g., "FR", "DE", "GR").
        cache_dir: Local directory for caching downloaded files.
        version: JRC-IDEES version string for filename construction.
        base_url: Base URL for the JRC-IDEES FTP server.
        force: If True, re-download even if file exists locally.

    Returns:
        Path to the local Excel file for the country's Industry sector.

    Raises:
        RuntimeError: If download or extraction fails.
    """
    import urllib.request
    import urllib.error

    country_el = _iso2_to_eurostat(country)
    country_dir = cache_dir / country_el
    country_dir.mkdir(parents=True, exist_ok=True)

    xlsx_filename = f"{version}_Industry_{country_el}.xlsx"
    xlsx_path = country_dir / xlsx_filename

    # Return cached file if it exists
    if xlsx_path.exists() and not force:
        logger.debug(f"JRC-IDEES cache hit: {xlsx_path}")
        return xlsx_path

    # Try direct Excel download first, then ZIP
    # The JRC-IDEES FTP may serve either format depending on the version.
    excel_url = f"{base_url.rstrip('/')}/{country_el}/{xlsx_filename}"
    zip_url = f"{base_url.rstrip('/')}/{country_el}/{version}_{country_el}.zip"

    # Attempt 1: direct .xlsx download
    try:
        logger.info(f"Downloading JRC-IDEES for {country} from {excel_url}")
        urllib.request.urlretrieve(excel_url, xlsx_path)
        logger.info(f"Downloaded {xlsx_path.name} ({xlsx_path.stat().st_size / 1024:.0f} KB)")
        return xlsx_path
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.debug(f"Direct .xlsx not found (404), trying ZIP archive...")
        else:
            logger.warning(f"HTTP error downloading .xlsx: {e}")

    # Attempt 2: ZIP archive containing the workbook
    try:
        logger.info(f"Downloading JRC-IDEES ZIP for {country} from {zip_url}")
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name
            urllib.request.urlretrieve(zip_url, tmp_path)

        with zipfile.ZipFile(tmp_path, "r") as zf:
            # Find the Industry Excel file inside the archive
            members = zf.namelist()
            industry_file = None
            for member in members:
                if "Industry" in member and member.endswith(".xlsx"):
                    industry_file = member
                    break

            if industry_file is None:
                # Fallback: look for any .xlsx file
                xlsx_members = [m for m in members if m.endswith(".xlsx")]
                if xlsx_members:
                    industry_file = xlsx_members[0]
                    logger.warning(
                        f"No 'Industry' file found in ZIP for {country}. "
                        f"Using first .xlsx: {industry_file}"
                    )
                else:
                    raise RuntimeError(
                        f"No .xlsx files found in JRC-IDEES ZIP for {country}. "
                        f"Archive contents: {members}"
                    )

            # Extract the target file
            with zf.open(industry_file) as src:
                xlsx_path.write_bytes(src.read())

        os.unlink(tmp_path)
        logger.info(f"Extracted {xlsx_path.name} ({xlsx_path.stat().st_size / 1024:.0f} KB)")
        return xlsx_path

    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Failed to download JRC-IDEES data for {country}: {e}\n"
            f"Tried URLs:\n  {excel_url}\n  {zip_url}"
        ) from e
    except zipfile.BadZipFile as e:
        raise RuntimeError(
            f"Downloaded file for {country} is not a valid ZIP: {e}"
        ) from e
    finally:
        # Clean up temp file if it still exists
        if "tmp_path" in locals() and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_jrc_idees_value(
    xlsx_path: Path,
    sheet: str,
    row: int,
    year: int = 2015,
) -> float:
    """Read a single value from a JRC-IDEES Excel workbook.

    This mirrors the ``get_sheet_IDEES()`` function from the POMMES
    prototype notebook.  The JRC-IDEES workbooks have years as column
    headers and industrial subsectors / indicators as rows.

    Args:
        xlsx_path: Path to the Excel workbook.
        sheet: Sheet name (e.g., "ISI" for Iron and Steel).
        row: 0-indexed row number to read.
        year: Column year to extract (integer).

    Returns:
        Numeric value at the specified sheet/row/year intersection.

    Raises:
        ValueError: If the sheet or year column is not found.
        IndexError: If the row index is out of range.
    """
    try:
        df = pd.read_excel(xlsx_path, sheet_name=sheet, engine="openpyxl")
    except ValueError as e:
        available = pd.ExcelFile(xlsx_path, engine="openpyxl").sheet_names
        raise ValueError(
            f"Sheet '{sheet}' not found in {xlsx_path.name}. "
            f"Available sheets: {available}"
        ) from e

    if row >= len(df):
        raise IndexError(
            f"Row {row} out of range for sheet '{sheet}' in {xlsx_path.name} "
            f"(sheet has {len(df)} data rows)"
        )

    # Year columns may be int or string
    if year in df.columns:
        return float(df.iloc[row][year])
    elif str(year) in df.columns:
        return float(df.iloc[row][str(year)])
    else:
        # Try to find the closest year column
        year_cols = [c for c in df.columns if isinstance(c, (int, float)) or str(c).isdigit()]
        raise ValueError(
            f"Year {year} not found in sheet '{sheet}' columns. "
            f"Available year columns: {year_cols}"
        )


def _read_jrc_idees_series(
    xlsx_path: Path,
    sheet: str,
    row: int,
    years: list[int] | None = None,
) -> pd.Series:
    """Read a time series from a JRC-IDEES Excel workbook.

    Like ``_read_jrc_idees_value`` but returns all year columns (or a
    specified subset) as a pandas Series indexed by year.

    Args:
        xlsx_path: Path to the Excel workbook.
        sheet: Sheet name.
        row: 0-indexed row number.
        years: Optional list of years to extract. If None, returns all.

    Returns:
        pd.Series indexed by year (int), values are floats.
    """
    df = pd.read_excel(xlsx_path, sheet_name=sheet, engine="openpyxl")
    if row >= len(df):
        raise IndexError(
            f"Row {row} out of range for sheet '{sheet}' in {xlsx_path.name}"
        )

    row_data = df.iloc[row]

    # Extract numeric year columns
    year_values = {}
    for col in df.columns:
        try:
            yr = int(col)
            year_values[yr] = float(row_data[col])
        except (ValueError, TypeError):
            continue

    series = pd.Series(year_values, dtype=float)
    series.index.name = "year"

    if years is not None:
        series = series.reindex(years)

    return series


def fetch_jrc_idees_steel_base(
    jrc_idees_path: str | Path | None = None,
    countries: list[str] | None = None,
    reference_year: int = 2019,
    force_reload: bool = False,
) -> pd.DataFrame:
    """Fetch raw steel production from JRC-IDEES — no share application.

    This function reads steel physical output from the JRC-IDEES database
    (sheet ``ISI``, row 5 of the ``Industry`` workbook for each country).
    It handles three data access patterns:

    1. **Local Excel files**: If ``jrc_idees_path`` points to a directory
       containing per-country Excel files (the standard JRC-IDEES layout),
       reads directly from those files.
    2. **Pre-made CSV** (legacy): If ``jrc_idees_path`` points to a directory
       containing ``2023_FEC_Steel_Demand.csv``, reads the CSV.
    3. **Auto-download**: If ``jrc_idees_path`` is ``"auto"`` or the directory
       is empty, downloads data from the JRC FTP server.

    The JRC-IDEES file structure is::

        {jrc_idees_path}/
          {country_el}/
            JRC-IDEES-2021_Industry_{country_el}.xlsx

    Where ``country_el`` uses Eurostat convention (GR → EL).

    Args:
        jrc_idees_path: Path to JRC-IDEES data directory, or ``"auto"``
            to download automatically. If None, uses static fallback.
        countries: Optional ISO-2 country filter.
        reference_year: Reference year to extract from the Excel files.
        force_reload: Bypass in-memory cache and re-read files.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - steel_production_kt (float): Crude steel production in kilotonnes

    Raises:
        FileNotFoundError: If JRC-IDEES files are not found and auto-download fails.
        ValueError: If required data cannot be extracted.
    """
    cache_key = f"jrc_steel_base_{reference_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    target_countries = countries if countries is not None else EU27_COUNTRIES

    # --- If jrc_idees_path is None, return static fallback ---
    if jrc_idees_path is None:
        logger.info("jrc_idees_path is None — using static _CRUDE_STEEL_2019_KT fallback.")
        rows = []
        for cc in target_countries:
            rows.append({
                "country": cc,
                "steel_production_kt": _CRUDE_STEEL_2019_KT.get(cc, 0.0),
            })
        result = pd.DataFrame(rows)
        _set_cache(cache_key, result)
        return result

    jrc_path = Path(jrc_idees_path)

    # Check if we need to download or if files are already present.
    auto_download = str(jrc_idees_path).lower() == "auto"
    cache_dir = _get_jrc_idees_cache_dir(
        None if auto_download else jrc_idees_path
    )

    rows = []
    for cc in target_countries:
        country_el = _iso2_to_eurostat(cc)
        xlsx_filename = f"{_JRC_IDEES_FILE_VERSION}_Industry_{country_el}.xlsx"

        # Check multiple possible locations
        xlsx_candidates = [
            cache_dir / country_el / xlsx_filename,
            cache_dir / xlsx_filename,
            cache_dir / f"{country_el}" / f"{_JRC_IDEES_FILE_VERSION}_{country_el}" / xlsx_filename,
        ]

        xlsx_path = None
        for candidate in xlsx_candidates:
            if candidate.exists():
                xlsx_path = candidate
                break

        if xlsx_path is None:
            if auto_download or str(jrc_idees_path).lower() == "auto":
                # Download the file
                try:
                    xlsx_path = _download_jrc_idees_country(cc, cache_dir, force=force_reload)
                except RuntimeError as e:
                    logger.warning(
                        f"Failed to download JRC-IDEES for {cc}: {e}. "
                        f"Using static fallback."
                    )
                    rows.append({
                        "country": cc,
                        "steel_production_kt": _CRUDE_STEEL_2019_KT.get(cc, 0.0),
                    })
                    continue
            else:
                logger.warning(
                    f"JRC-IDEES Industry workbook not found for {cc} in {cache_dir}. "
                    f"Expected: {xlsx_filename}. Using static fallback."
                )
                rows.append({
                    "country": cc,
                    "steel_production_kt": _CRUDE_STEEL_2019_KT.get(cc, 0.0),
                })
                continue

        # Read steel production from sheet ISI, row 5
        # The value is physical output in kt.
        try:
            value_kt = _read_jrc_idees_value(
                xlsx_path, sheet="ISI", row=5, year=reference_year,
            )
            if np.isnan(value_kt) or value_kt < 0:
                logger.warning(
                    f"Invalid steel production for {cc}: {value_kt}. "
                    f"Using static fallback."
                )
                value_kt = _CRUDE_STEEL_2019_KT.get(cc, 0.0)
        except (ValueError, IndexError, KeyError) as e:
            logger.warning(
                f"Could not read steel production for {cc} from {xlsx_path}: {e}. "
                f"Using static fallback."
            )
            value_kt = _CRUDE_STEEL_2019_KT.get(cc, 0.0)

        rows.append({
            "country": cc,
            "steel_production_kt": float(value_kt),
        })

    result = pd.DataFrame(rows)

    # Schema validation
    if (result["steel_production_kt"] < 0).any():
        raise ValueError("Negative steel production values in JRC-IDEES data")

    logger.info(
        f"Fetched {len(result)} rows of JRC-IDEES steel production "
        f"(total: {result['steel_production_kt'].sum():,.0f} kt)"
    )
    _set_cache(cache_key, result)
    return result


def fetch_steel_production(
    jrc_idees_path: str | Path | None = None,
    tyndp_path: str | Path | None = None,
    countries: list[str] | None = None,
    reference_year: int = 2019,
    force_reload: bool = False
) -> pd.DataFrame:
    """
    Fetch steel production demand and DRI/BF+BOF split by country.

    Uses :func:`fetch_jrc_idees_steel_base` for raw production and
    :func:`fetch_tyndp_demand_parameters` for technology shares, then
    delegates to :func:`~demandforge.process.steel.build_steel_base_table`
    for scientific route reconstruction.

    .. deprecated::
        This convenience wrapper mixes fetch and process concerns.
        Prefer calling ``fetch_jrc_idees_steel_base()`` and
        ``build_steel_base_table()`` separately for cleaner architecture.

    Args:
        jrc_idees_path: Path to JRC-IDEES data directory. If None, raises ValueError.
        tyndp_path: Path to TYNDP scenarios file. If None, raises ValueError.
        countries: Optional list of ISO-2 country codes to filter results.
        reference_year: Reference year to include in output. Default 2019.
        force_reload: If True, skip cache and recompute from source.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - year (int): Reference year
        - scenario (str): Always "baseline" for this data source
        - steel_production_kt (float): Total steel production in kilotonnes
        - crude_steel_production_t_per_yr (float): Steel production in tonnes/year
        - bf_bof_production_t_per_yr (float): BF+BOF production in tonnes/year
        - dri_ch4_production_t_per_yr (float): DRI with natural gas in tonnes/year
        - dri_h2_production_t_per_yr (float): DRI with hydrogen in tonnes/year
        - dri_hydrogen_share (float): Share of DRI using hydrogen (0-1)
        - dri_natural_gas_share (float): Share of DRI using natural gas (0-1)
        - blastfurnace_bof_share (float): Share using BF+BOF (0-1)

    Raises:
        ValueError: If jrc_idees_path or tyndp_path is None.
        FileNotFoundError: If JRC-IDEES steel demand file is not found.
    """
    if jrc_idees_path is None or tyndp_path is None:
        # ---- Static fallback: WorldSteel 2019 + EAF shares ----
        logger.info(
            "jrc_idees_path or tyndp_path not provided — using static "
            "WorldSteel 2019 + EAF share fallback."
        )
        rows = []
        for cc in (countries or EU27_COUNTRIES):
            crude_kt = _CRUDE_STEEL_2019_KT.get(cc, 0.0)
            crude_t = crude_kt * 1000.0
            eaf_frac = _EAF_SHARE_2019.get(cc, 0.0)
            eaf_t = crude_t * eaf_frac
            primary_t = crude_t - eaf_t
            # Base year: all primary is BF-BOF (no DRI yet)
            rows.append({
                "country": cc,
                "year": reference_year,
                "scenario": "baseline",
                "steel_production_kt": crude_kt,
                "crude_steel_production_t_per_yr": crude_t,
                "primary_production_t_per_yr": primary_t,
                "bf_bof_production_t_per_yr": primary_t,
                "dri_ch4_production_t_per_yr": 0.0,
                "dri_h2_production_t_per_yr": 0.0,
                "eaf_production_t_per_yr": eaf_t,
                "blastfurnace_bof_share": 1.0 if primary_t > 0 else 0.0,
                "dri_natural_gas_share": 0.0,
                "dri_hydrogen_share": 0.0,
                "eaf_share": eaf_frac,
            })
        result = pd.DataFrame(rows)
        if countries is not None:
            result = result[result["country"].isin(countries)].reset_index(drop=True)
        _set_cache(f"steel_production_{reference_year}", result)
        return result

    cache_key = f"steel_production_{reference_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    # ---- Step 1: Pure fetch (no transforms) ----
    jrc_df = fetch_jrc_idees_steel_base(
        jrc_idees_path, countries=countries, reference_year=reference_year,
        force_reload=force_reload,
    )
    tyndp_df = fetch_tyndp_demand_parameters(tyndp_path, force_reload=force_reload)

    # ---- Step 2: Delegate to process layer ----
    from demandforge.process.steel import build_steel_base_table
    base_table = build_steel_base_table(jrc_df, tyndp_df, reference_year=reference_year)

    # ---- Step 3: Reshape to legacy output format ----
    result = base_table.copy()
    result["scenario"] = "baseline"
    result["steel_production_kt"] = result["crude_steel_production_t_per_yr"] / 1000.0

    logger.debug(f"Fetched {len(result)} rows for steel production (via build_steel_base_table)")

    if countries is not None:
        result = result[result["country"].isin(countries)].reset_index(drop=True)

    _set_cache(cache_key, result)
    return result


def fetch_ammonia_production(
    source_path: str | Path | None = None,
    countries: list[str] | None = None,
    reference_year: int = 2019,
    force_reload: bool = False
) -> pd.DataFrame:
    """
    Fetch ammonia production by EU country.

    Uses USGS 2019 data with fallback to zero for countries without reported production.

    Args:
        source_path: Optional path to CSV override. If None, uses embedded data.
        countries: Optional list of ISO-2 country codes to filter results.
            If provided, only rows matching these countries are returned.
        reference_year: Reference year to include in output. Default 2019.
        force_reload: If True, skip cache and recompute from source.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - year (int): Reference year
        - scenario (str): Always "baseline" for this data source
        - ammonia_production_mt (float): Ammonia production in megatonnes (Mt/yr)
        - ammonia_production_t_per_yr (float): Ammonia production in tonnes/year
    """
    cache_key = f"ammonia_production_{reference_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    _check_data_vintage(reference_year)

    if source_path:
        df = pd.read_csv(source_path, dtype={"country": str})
        # Standardize column name to ammonia_production_mt
        if "ammonia_production" in df.columns and "ammonia_production_mt" not in df.columns:
            df = df.rename(columns={"ammonia_production": "ammonia_production_mt"})
    else:
        # Use embedded USGS data (values are in megatonnes Mt/yr)
        data = [
            {"country": country, "ammonia_production_mt": mt}
            for country, mt in _USGS_AMMONIA_2019_MT.items()
        ]
        # Add zero entries for missing countries and log warnings for critical countries
        for country in EU27_COUNTRIES:
            if country not in _USGS_AMMONIA_2019_MT:
                data.append({"country": country, "ammonia_production_mt": 0.0})
                if country in _CRITICAL_COUNTRIES:
                    logger.warning(
                        f"Ammonia production for critical country {country} "
                        f"defaulting to 0 (not in USGS 2019 data)."
                    )
        df = pd.DataFrame(data)

    # Derive tonnes column: 1 Mt = 1,000,000 t
    df["ammonia_production_t_per_yr"] = df["ammonia_production_mt"] * 1_000_000.0

    # Add year and scenario columns
    df["year"] = reference_year
    df["scenario"] = "baseline"

    logger.debug(f"Fetched {len(df)} rows for ammonia production")

    # Filter by countries if provided
    if countries is not None:
        df = df[df["country"].isin(countries)].reset_index(drop=True)

    _set_cache(cache_key, df)
    return df


def fetch_refinery_output(
    source_path: str | Path | None = None,
    countries: list[str] | None = None,
    reference_year: int = 2019,
    force_reload: bool = False
) -> pd.DataFrame:
    """
    Fetch refinery crude oil output by country (ktoe/year).

    .. note:: JRC-IDEES EnergyBalance row index

       When regenerating the refinery CSV from the raw JRC-IDEES XLSB file,
       use **row 64** (``Transformation output — Refineries``) of the
       ``EnergyBalance`` sheet — NOT row 60.  The original notebook used
       ``get_demand_data_IDEES('index', 60, ...)``, which was off by
       4 rows due to header offsets in the 2023 edition.

    Args:
        source_path: Optional path to data source. If None, raises NotImplementedError.
        countries: Optional list of ISO-2 country codes to filter results.
            If provided, only rows matching these countries are returned.
        reference_year: Reference year to include in output. Default 2019.
        force_reload: If True, skip cache and recompute from source.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - year (int): Reference year
        - scenario (str): Always "baseline" for this data source
        - refinery_output_ktoe (float): Refinery output in kilotonnes of oil equivalent

    Raises:
        ValueError: If CSV source has wrong columns.
    """
    cache_key = f"refinery_output_{reference_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    if source_path is not None:
        df = pd.read_csv(source_path, dtype={"country": str})

        # Standardize column name to refinery_output_ktoe
        if "refinery_output_ktoe" in df.columns:
            pass  # Already correct
        elif "refinery_output" in df.columns:
            df = df.rename(columns={"refinery_output": "refinery_output_ktoe"})
        else:
            raise ValueError(
                f"CSV file must contain either 'refinery_output' or 'refinery_output_ktoe' column. "
                f"Got columns: {list(df.columns)}"
            )
    else:
        # Use embedded Eurostat nrg_bal_c 2019 data as static fallback
        _check_data_vintage(reference_year)
        data = [
            {"country": cc, "refinery_output_ktoe": val}
            for cc, val in _EUROSTAT_REFINERY_OUTPUT_2019_KTOE.items()
        ]
        # Add zero entries for any EU-27 countries not in the dict
        for cc in EU27_COUNTRIES:
            if cc not in _EUROSTAT_REFINERY_OUTPUT_2019_KTOE:
                data.append({"country": cc, "refinery_output_ktoe": 0.0})
                logger.warning(
                    f"Country {cc} not in _EUROSTAT_REFINERY_OUTPUT_2019_KTOE, "
                    f"defaulting to 0.0 ktoe."
                )
        df = pd.DataFrame(data)
        logger.info(
            f"Using embedded Eurostat nrg_bal_c 2019 refinery output data "
            f"({len(df)} countries). For production runs, provide source_path "
            f"or use fetch_eurostat_refinery_output()."
        )

    # Add year and scenario columns
    df["year"] = reference_year
    df["scenario"] = "baseline"

    logger.debug(f"Fetched {len(df)} rows for refinery output")

    # Filter by countries if provided
    if countries is not None:
        df = df[df["country"].isin(countries)].reset_index(drop=True)

    _set_cache(cache_key, df)
    return df


def _download_eurostat_tsv(
    dataset: str = "nrg_bal_c",
    cache_dir: Path | None = None,
    url: str | None = None,
    force: bool = False,
) -> Path:
    """Download Eurostat TSV bulk data.

    Args:
        dataset: Eurostat dataset code (e.g. ``"nrg_bal_c"``).
        cache_dir: Local cache directory. Defaults to
            ``~/.cache/demandforge/eurostat/``.
        url: Override URL. If None, uses the standard SDMX endpoint.
        force: If True, re-download even if cached.

    Returns:
        Path to the local TSV file.
    """
    import urllib.request
    import urllib.error

    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "demandforge" / "eurostat"
    cache_dir.mkdir(parents=True, exist_ok=True)

    tsv_filename = f"estat_{dataset}.tsv"
    tsv_path_local = cache_dir / tsv_filename

    if tsv_path_local.exists() and not force:
        logger.debug(f"Eurostat cache hit: {tsv_path_local}")
        return tsv_path_local

    if url is None:
        url = f"https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/{dataset}?format=TSV"

    logger.info(f"Downloading Eurostat {dataset} from {url}")
    try:
        urllib.request.urlretrieve(url, tsv_path_local)
        size_mb = tsv_path_local.stat().st_size / (1024 * 1024)
        logger.info(f"Eurostat {dataset} saved ({size_mb:.1f} MB)")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Failed to download Eurostat {dataset}: {e}"
        ) from e

    return tsv_path_local


def fetch_eurostat_refinery_output(
    tsv_path: str | Path | None = None,
    nrg_bal_code: str = "TO_RPI_RO",
    siec_code: str = "TOTAL",
    unit_code: str = "KTOE",
    reference_year: int = 2019,
    countries: list[str] | None = None,
    force_reload: bool = False,
) -> pd.DataFrame:
    """Parse Eurostat nrg_bal_c TSV bulk download to extract refinery output.

    The Eurostat TSV format has a composite first column
    ``freq,nrg_bal,siec,unit,geo`` followed by tab-separated year columns.
    Values may carry trailing whitespace or ``:`` for missing data.

    The function filters for rows matching ``nrg_bal_code``, ``siec_code``,
    and ``unit_code``, extracts EU-27 country values for ``reference_year``,
    and returns a DataFrame compatible with ``fetch_refinery_output``.

    Args:
        tsv_path: Path to the Eurostat TSV file (nrg_bal_c bulk download).
        nrg_bal_code: Energy balance flow code. Default "TO_RPI_RO"
            (Transformation output — refineries, refinery output).
            Use "TI_RPI_RO" for transformation input instead.
        siec_code: Standard International Energy Classification code.
            Default "TOTAL" for all energy products.
        unit_code: Unit code. Default "KTOE" (kilotonnes of oil equivalent).
        reference_year: Year column to extract. Default 2019.
        countries: Optional list of ISO-2 country codes to filter results.
            If provided, only rows matching these countries are returned.
            If None, returns all EU-27 countries.
        force_reload: If True, skip cache and reparse from file.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - refinery_output_ktoe (float): Refinery output in KTOE for
          the reference year.

    Raises:
        FileNotFoundError: If tsv_path does not exist.
        ValueError: If the reference year column is not found in the TSV.
    """
    cache_key = f"eurostat_refinery_{nrg_bal_code}_{reference_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    # --- Auto-download or resolve path ---
    if tsv_path is None:
        raise ValueError(
            "tsv_path is required.  Pass a file path to a downloaded "
            "Eurostat nrg_bal_c TSV file, or 'auto' to download."
        )
    if str(tsv_path).lower() == "auto":
        tsv_path = _download_eurostat_tsv(
            dataset="nrg_bal_c", force=force_reload
        )
    else:
        tsv_path = Path(tsv_path)

    if not tsv_path.exists():
        raise FileNotFoundError(f"Eurostat TSV file not found: {tsv_path}")

    # Read the TSV — first column is composite key, rest are year values
    raw = pd.read_csv(tsv_path, sep="\t", dtype=str)

    # The first column header looks like "freq,nrg_bal,siec,unit,geo\t2015 \t2016 ..."
    # After pd.read_csv with sep=\t, the first column is the composite key
    first_col = raw.columns[0]  # "freq,nrg_bal,siec,unit,geo"

    # Split the composite key into separate columns
    key_parts = raw[first_col].str.split(",", expand=True)
    key_parts.columns = ["freq", "nrg_bal", "siec", "unit", "geo"]
    for col in key_parts.columns:
        key_parts[col] = key_parts[col].str.strip().str.strip('"')

    # Determine which countries to filter for.
    # Eurostat uses "EL" for Greece, so convert ISO-2 → Eurostat codes.
    target_iso2 = countries if countries is not None else EU27_COUNTRIES
    target_eurostat = [_iso2_to_eurostat(c) for c in target_iso2]

    # Filter for the desired energy balance flow
    mask = (
        (key_parts["nrg_bal"] == nrg_bal_code)
        & (key_parts["siec"] == siec_code)
        & (key_parts["unit"] == unit_code)
        & (key_parts["geo"].isin(target_eurostat))
    )
    filtered = raw[mask].copy()
    geo_values = key_parts.loc[mask, "geo"].values

    # Find the reference year column (may have trailing space)
    year_col = None
    for col in raw.columns[1:]:
        if col.strip().startswith(str(reference_year)):
            year_col = col
            break
    if year_col is None:
        raise ValueError(
            f"Reference year {reference_year} not found in TSV columns: "
            f"{[c.strip() for c in raw.columns[1:]]}"
        )

    # Parse values — clean whitespace, handle ":" as NaN
    values = filtered[year_col].str.strip().str.replace(":", "", regex=False)
    values = pd.to_numeric(values, errors="coerce")

    result = pd.DataFrame({
        "country": geo_values,
        "refinery_output_ktoe": values.values,
    })
    # Convert Eurostat country codes back to ISO-2 (EL → GR)
    result["country"] = result["country"].map(_eurostat_to_iso2)
    result = result.dropna(subset=["refinery_output_ktoe"])
    result = result.sort_values("country").reset_index(drop=True)

    logger.info(
        f"Eurostat {nrg_bal_code}: parsed {len(result)} countries "
        f"for year {reference_year}, "
        f"total = {result['refinery_output_ktoe'].sum():,.1f} KTOE"
    )

    _set_cache(cache_key, result)
    return result


def fetch_olefins_production(
    naphtha_source_path: str | Path | None = None,
    countries: list[str] | None = None,
    reference_year: int = 2019,
    force_reload: bool = False
) -> pd.DataFrame:
    """
    Fetch olefins (ethylene, propylene) production by country.

    Olefins are produced via naphtha cracking. The calculation converts naphtha
    feedstock (ktoe) to olefins product mass using a stoichiometric /14 cracking
    yield factor.

    Args:
        naphtha_source_path: Optional path to naphtha data (ktoe/year). If None, estimates from refinery output.
        countries: Optional list of ISO-2 country codes to filter results.
            If provided, only rows matching these countries are returned.
        reference_year: Reference year to include in output. Default 2019.
        force_reload: If True, skip cache and recompute from source.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - year (int): Reference year
        - scenario (str): Always "baseline" for this data source
        - olefins_production_t_per_yr (float): Olefins production mass in tonnes/year
    """
    cache_key = f"olefins_production_{reference_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    if naphtha_source_path:
        df_naphtha = pd.read_csv(naphtha_source_path, dtype={"country": str})
        # Convert naphtha (ktoe) → olefins (tonnes) using cracking yield
        df_olefins = df_naphtha.copy()
        df_olefins["olefins_production_t_per_yr"] = (
            df_naphtha["naphtha_ktoe"] * 1000 / 14.0
        )
        df_olefins = df_olefins[["country", "olefins_production_t_per_yr"]]
    else:
        # Use embedded Eurostat-derived estimates (2019, in t/yr).
        # Source: Eurostat Prodcom, CEFIC Facts & Figures 2019.
        # Covers EU27 naphtha-cracker based olefins (ethylene + propylene).
        _OLEFINS_PRODUCTION_2019_T: dict[str, float] = {
            "DE": 5_800_000, "NL": 4_200_000, "FR": 2_900_000,
            "BE": 2_600_000, "ES": 1_800_000, "IT": 1_400_000,
            "PL": 800_000, "SE": 600_000, "FI": 500_000,
            "AT": 400_000, "CZ": 350_000, "HU": 300_000,
            "SK": 250_000, "PT": 200_000, "RO": 150_000,
            "BG": 100_000, "HR": 50_000, "GR": 50_000,
            # 2026-05-12: GB added for POMMES area-coord compatibility.
            # INEOS Grangemouth (~720 kt ethylene) + SABIC Wilton (~865 kt) +
            # propylene; CEFIC Facts & Figures + company filings, 2019.
            "GB": 1_700_000,
        }
        data = []
        for cc in EU27_COUNTRIES:
            data.append({
                "country": cc,
                "olefins_production_t_per_yr": _OLEFINS_PRODUCTION_2019_T.get(cc, 0.0),
            })
        df_olefins = pd.DataFrame(data)

    # Add year and scenario columns
    df_olefins["year"] = reference_year
    df_olefins["scenario"] = "baseline"

    logger.debug(f"Fetched {len(df_olefins)} rows for olefins production")

    # Filter by countries if provided
    if countries is not None:
        df_olefins = df_olefins[df_olefins["country"].isin(countries)].reset_index(drop=True)

    _set_cache(cache_key, df_olefins)
    return df_olefins


def fetch_methanol_production(
    source_path: str | Path | None = None,
    countries: list[str] | None = None,
    reference_year: int = 2019,
    force_reload: bool = False
) -> pd.DataFrame:
    """
    Fetch methanol production by EU country.

    Uses industry survey data (circa 2019) with fallback to zero for missing countries.

    Args:
        source_path: Optional path to CSV override. If None, uses embedded data.
        countries: Optional list of ISO-2 country codes to filter results.
            If provided, only rows matching these countries are returned.
        reference_year: Reference year to include in output. Default 2019.
        force_reload: If True, skip cache and recompute from source.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - year (int): Reference year
        - scenario (str): Always "baseline" for this data source
        - methanol_production_t (float): Methanol production in tonnes
    """
    cache_key = f"methanol_production_{reference_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    _check_data_vintage(reference_year)

    if source_path:
        df = pd.read_csv(source_path, dtype={"country": str})
    else:
        # Use embedded data
        data = [
            {"country": country, "methanol_production_t": t}
            for country, t in _METHANOL_PRODUCTION_T.items()
        ]
        # Add zero entries for missing countries (EU27 only) and log warnings
        for country in EU27_COUNTRIES:
            if country not in _METHANOL_PRODUCTION_T:
                data.append({"country": country, "methanol_production_t": 0.0})
                if country in _CRITICAL_COUNTRIES:
                    logger.warning(
                        f"Methanol production for critical country {country} "
                        f"defaulting to 0 (not in 2019 survey data)."
                    )
        df = pd.DataFrame(data)
        # Remove non-EU27 entries (e.g., UK)
        df = df[df["country"].isin(EU27_COUNTRIES)]

    # Add year and scenario columns
    df["year"] = reference_year
    df["scenario"] = "baseline"

    logger.debug(f"Fetched {len(df)} rows for methanol production")

    # Filter by countries if provided
    if countries is not None:
        df = df[df["country"].isin(countries)].reset_index(drop=True)

    _set_cache(cache_key, df)
    return df


def _download_tyndp_file(
    cache_dir: Path | None = None,
    url: str = TYNDP_2024_URL,
    force: bool = False,
) -> Path:
    """Download TYNDP demand scenarios file from the ENTSO-E server.

    The file is distributed as a ZIP-compressed XLSB. This function
    downloads, extracts, and caches the result.

    Args:
        cache_dir: Directory to store the downloaded file. If None,
            uses ``~/.cache/demandforge/TYNDP/``.
        url: URL to the TYNDP file (ZIP or XLSB).
        force: If True, re-download even if cached.

    Returns:
        Path to the local XLSB file.
    """
    import urllib.request
    import urllib.error

    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "demandforge" / "TYNDP"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Derive filename from URL
    url_filename = url.rsplit("/", 1)[-1]
    # e.g. "Demand_Scenarios_TYNDP_2024_After_Public_Consultation.xlsb.zip"

    # Final target: the unzipped .xlsb
    if url_filename.endswith(".xlsb.zip"):
        xlsb_filename = url_filename[:-4]  # strip ".zip"
    elif url_filename.endswith(".zip"):
        xlsb_filename = url_filename[:-4] + ".xlsb"
    else:
        xlsb_filename = url_filename

    xlsb_path = cache_dir / xlsb_filename
    if xlsb_path.exists() and not force:
        logger.debug(f"TYNDP cache hit: {xlsb_path}")
        return xlsb_path

    logger.info(f"Downloading TYNDP from {url}")
    with tempfile.NamedTemporaryFile(suffix=".download", delete=False) as tmp:
        tmp_path = tmp.name
        urllib.request.urlretrieve(url, tmp_path)

    try:
        # Try treating as ZIP
        if zipfile.is_zipfile(tmp_path):
            with zipfile.ZipFile(tmp_path, "r") as zf:
                members = zf.namelist()
                xlsb_member = None
                for m in members:
                    if m.endswith(".xlsb"):
                        xlsb_member = m
                        break
                if xlsb_member is None:
                    # Take the largest file
                    xlsb_member = max(members, key=lambda m: zf.getinfo(m).file_size)
                    logger.warning(f"No .xlsb in ZIP; using largest: {xlsb_member}")

                with zf.open(xlsb_member) as src:
                    xlsb_path.write_bytes(src.read())
        else:
            # Not a ZIP — assume it's the raw XLSB
            import shutil
            shutil.move(tmp_path, xlsb_path)
            tmp_path = None  # prevent cleanup

        logger.info(f"TYNDP saved to {xlsb_path} ({xlsb_path.stat().st_size / 1024:.0f} KB)")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return xlsb_path


def _read_tyndp_xlsb_raw(
    tyndp_file: Path,
    sheet_name: str = "2_DEMAND_PARAMETERS",
) -> pd.DataFrame:
    """Read raw TYNDP demand parameters from XLSB using pd.read_excel.

    The TYNDP 2024 XLSB has a sheet ``"2_DEMAND_PARAMETERS"`` with
    the following layout:

    - First column is an unnamed index (to be dropped).
    - Next 7 unnamed columns: country, parameter, sector, subsector,
      application, type, unit.
    - Remaining columns are scenario–year values: REF (=2019 baseline),
      DE (=DE 2040), DE.1 (=DE 2050), GA (=GA 2040), GA.1 (=GA 2050).
    - First data row (row 0 after header) is often empty/header — drop it.

    Returns a melted DataFrame with columns:
        country, parameter, sector, subsector, application, type, unit,
        scenario, year, value
    """
    # pd.read_excel can handle .xlsb with engine="calamine" (or pyxlsb).
    # Try calamine first (faster, pip install python-calamine), then pyxlsb.
    engines_to_try = ["calamine", "pyxlsb"]
    df = None
    for engine in engines_to_try:
        try:
            df = pd.read_excel(
                tyndp_file, sheet_name=sheet_name, engine=engine
            )
            logger.info(f"Read TYNDP with engine '{engine}': {df.shape}")
            break
        except ImportError:
            continue
        except Exception as e:
            logger.debug(f"Engine '{engine}' failed: {e}")
            continue

    if df is None:
        # Last resort: try pyxlsb directly
        try:
            import pyxlsb
            with pyxlsb.open_workbook(str(tyndp_file)) as wb:
                with wb.get_sheet(sheet_name) as sheet:
                    rows_data = []
                    for row in sheet.rows():
                        rows_data.append([cell.v for cell in row])
                    if rows_data:
                        df = pd.DataFrame(rows_data[1:], columns=rows_data[0])
                    else:
                        raise ValueError(f"Empty sheet '{sheet_name}'")
        except ImportError:
            raise ImportError(
                "Cannot read XLSB: install python-calamine or pyxlsb. "
                "pip install python-calamine pyxlsb"
            )

    # --- Clean up the TYNDP structure ---
    # Drop the first unnamed column (index column)
    if "Unnamed: 0" in df.columns or df.columns[0] is None:
        df = df.drop(columns=[df.columns[0]])

    # Drop the first row if it's empty / a sub-header
    if df.iloc[0].isna().sum() > len(df.columns) // 2:
        df = df.iloc[1:].reset_index(drop=True)

    # Rename the first 7 unnamed columns
    _META_COLS = ["country", "parameter", "sector", "subsector",
                  "application", "type", "unit"]
    new_names = {}
    unnamed_idx = 0
    for col in df.columns:
        col_str = str(col)
        if col_str.startswith("Unnamed:") or col is None:
            if unnamed_idx < len(_META_COLS):
                new_names[col] = _META_COLS[unnamed_idx]
                unnamed_idx += 1

    if new_names:
        df = df.rename(columns=new_names)

    # Rename scenario–year columns to a standard form
    # REF → DE2019 (reference year is the DE baseline)
    # DE → DE2040, DE.1 → DE2050, GA → GA2040, GA.1 → GA2050
    _YEAR_RENAME = {
        "REF": "DE2019",
        "DE": "DE2040",
        "DE.1": "DE2050",
        "GA": "GA2040",
        "GA.1": "GA2050",
    }
    df = df.rename(columns=_YEAR_RENAME)

    # GA baseline is same as DE baseline
    if "GA2019" not in df.columns and "DE2019" in df.columns:
        df["GA2019"] = df["DE2019"]

    # Melt from wide to long
    id_vars = [c for c in _META_COLS if c in df.columns]
    value_vars = [c for c in df.columns if c not in id_vars]

    df_melted = df.melt(
        id_vars=id_vars,
        var_name="year_scenario",
        value_name="value",
    )

    # Extract scenario (2-letter prefix) and year (digits)
    extracted = df_melted["year_scenario"].str.extract(r"([A-Z]{2})(\d+)")
    df_melted["scenario"] = extracted[0]
    df_melted["year"] = pd.to_numeric(extracted[1], errors="coerce")

    # Drop rows that couldn't be parsed (non-year columns)
    df_melted = df_melted.dropna(subset=["scenario", "year"])
    df_melted["year"] = df_melted["year"].astype(int)

    # Clean value column — handle "0x2a" and other non-numeric values
    df_melted["value"] = (
        df_melted["value"]
        .astype(str)
        .str.replace("0x2a", "0", regex=False)
        .str.strip()
    )
    df_melted["value"] = pd.to_numeric(df_melted["value"], errors="coerce")

    return df_melted


def fetch_tyndp_demand_parameters(
    tyndp_path: str | Path | None = None,
    scenario: str = "DE",
    target_year: int | None = None,
    countries: list[str] | None = None,
    force_reload: bool = False,
) -> pd.DataFrame:
    """Fetch TYNDP 2024 demand scenario parameters.

    Reads the TYNDP 2024 demand parameters file and returns a wide-format
    DataFrame with one row per country and columns for each parameter.

    The function handles three input formats:

    1. **Real XLSB** (recommended): The official TYNDP file with sheet
       ``"2_DEMAND_PARAMETERS"``.  Downloaded from ENTSO-E if
       ``tyndp_path="auto"``.
    2. **Pre-processed CSV**: A CSV with columns ``country``, ``parameter``,
       ``value`` (already pivoted for a single scenario/year).
    3. **Pre-processed XLSX**: An Excel file with one sheet per scenario.

    The TYNDP provides data for years 2019, 2040, 2050 under scenarios
    DE (Distributed Energy) and GA (Global Ambition).  The ``scenario``
    and ``target_year`` parameters select which slice to return.  When
    ``target_year`` is not one of the milestone years, values are linearly
    interpolated.

    Args:
        tyndp_path: Path to TYNDP file, or ``"auto"`` to download.
            If None, raises ValueError.
        scenario: Scenario code: ``"DE"`` or ``"GA"``. Default ``"DE"``.
        target_year: Year to extract (e.g., 2040, 2050). If None,
            returns the reference year (2019) values.
        countries: Optional list of ISO-2 country codes to filter.
        force_reload: If True, skip cache and recompute from source.

    Returns:
        DataFrame with columns:
        - country (str): ISO-2 country code
        - industry_steel_production (float): Steel production index (fraction
          of base year, e.g. 1.0 = same as 2019)
        - industry_steel_dri_network_gas_share (float): DRI-NG share (0–1)
        - industry_steel_dri_hydrogen_share (float): DRI-H2 share (0–1)
        - industry_steel_blastfurnace_bof_share (float): BF-BOF share (0–1)
        - (and other parameters present in the TYNDP file)

    Raises:
        ValueError: If tyndp_path is None.
        KeyError: If expected columns are missing.
        ImportError: If required packages are not installed.
    """
    if tyndp_path is None:
        raise ValueError(
            "tyndp_path must be provided. Pass a file path to the TYNDP "
            "scenarios file, or 'auto' to download from ENTSO-E."
        )

    scenario = scenario.upper()
    if scenario not in ("DE", "GA"):
        logger.warning(
            f"Non-standard scenario '{scenario}'. Expected 'DE' or 'GA'. "
            f"Will attempt to match in the data."
        )

    cache_key = f"tyndp_demand_{scenario}_{target_year}"
    if not force_reload:
        cached = _get_cache(cache_key)
        if cached is not None:
            if countries is not None:
                return cached[cached["country"].isin(countries)].reset_index(drop=True)
            return cached

    tyndp_file = Path(tyndp_path)

    # --- Auto-download if requested ---
    if str(tyndp_path).lower() == "auto":
        tyndp_file = _download_tyndp_file(force=force_reload)

    # --- Handle ZIP wrapping ---
    if tyndp_file.suffix.lower() == ".zip":
        # Extract XLSB from ZIP
        with zipfile.ZipFile(tyndp_file, "r") as zf:
            members = zf.namelist()
            xlsb_member = None
            for m in members:
                if m.endswith(".xlsb"):
                    xlsb_member = m
                    break
            if xlsb_member is None:
                raise ValueError(
                    f"No .xlsb file found in TYNDP ZIP: {members}"
                )
            extract_dir = tyndp_file.parent / "tyndp_extracted"
            extract_dir.mkdir(exist_ok=True)
            extracted_path = extract_dir / Path(xlsb_member).name
            if not extracted_path.exists() or force_reload:
                with zf.open(xlsb_member) as src:
                    extracted_path.write_bytes(src.read())
            tyndp_file = extracted_path

    # --- Read based on file format ---
    if tyndp_file.suffix.lower() == ".xlsb":
        # Real TYNDP XLSB — use the melt/pivot pipeline
        df_melted = _read_tyndp_xlsb_raw(tyndp_file)

        # Filter to requested scenario and year
        df_scenario = df_melted[df_melted["scenario"] == scenario].copy()

        if df_scenario.empty:
            available = df_melted["scenario"].unique().tolist()
            raise ValueError(
                f"Scenario '{scenario}' not found in TYNDP data. "
                f"Available: {available}"
            )

        if target_year is not None:
            available_years = sorted(df_scenario["year"].unique())
            if target_year in available_years:
                df_year = df_scenario[df_scenario["year"] == target_year]
            else:
                # Interpolate between available years
                logger.info(
                    f"TYNDP: interpolating {scenario} to year {target_year} "
                    f"(available: {available_years})"
                )
                df_pivoted = df_scenario.pivot_table(
                    index=["country", "parameter"],
                    columns="year",
                    values="value",
                    aggfunc="first",
                )
                # Interpolate along year axis
                all_years = sorted(set(available_years + [target_year]))
                df_pivoted = df_pivoted.reindex(columns=all_years)
                df_pivoted = df_pivoted.interpolate(axis=1, method="linear")
                df_year = (
                    df_pivoted[[target_year]]
                    .rename(columns={target_year: "value"})
                    .reset_index()
                )
        else:
            # Return reference year (2019)
            df_year = df_scenario[df_scenario["year"] == 2019]
            if df_year.empty:
                # Fall back to the earliest year
                min_year = df_scenario["year"].min()
                df_year = df_scenario[df_scenario["year"] == min_year]

        # Pivot to wide format: one row per country, columns are parameters
        if "parameter" in df_year.columns:
            df = df_year.pivot_table(
                index="country",
                columns="parameter",
                values="value",
                aggfunc="first",
            ).reset_index()
        else:
            df = df_year

    elif tyndp_file.suffix.lower() == ".xlsx":
        # Pre-processed Excel with scenario as sheet name
        df = pd.read_excel(tyndp_file, sheet_name=scenario)

    elif tyndp_file.suffix.lower() == ".csv":
        df = pd.read_csv(tyndp_file)

    else:
        raise ValueError(
            f"Unsupported TYNDP file format: {tyndp_file.suffix}. "
            f"Expected .xlsb, .xlsx, .csv, or .zip"
        )

    # --- Post-processing: normalize column names ---
    # Convert Eurostat country codes (EL → GR)
    if "country" in df.columns:
        df["country"] = df["country"].replace(_EUROSTAT_TO_ISO2)

    # Ensure expected columns exist (soft check — log warnings instead of raising)
    expected_columns = [
        "industry_steel_production",
        "industry_steel_dri_network_gas_share",
        "industry_steel_dri_hydrogen_share",
        "industry_steel_blastfurnace_bof_share",
        "industry_useful_demand_for_chemical_fertilizers",
        "industry_useful_demand_for_chemical_refineries",
        "industry_useful_demand_for_chemical_other",
    ]

    present_columns = [c for c in expected_columns if c in df.columns]
    missing_columns = [c for c in expected_columns if c not in df.columns]
    if missing_columns:
        logger.warning(
            f"TYNDP data missing some expected columns: {missing_columns}. "
            f"Present: {list(df.columns)[:20]}... "
            f"Downstream functions may need to handle missing data."
        )

    # Ensure numeric types for present expected columns
    for col in present_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # ---- TYNDP percentage-to-fraction conversion ----
    share_columns = [c for c in df.columns if "_share" in c.lower()]
    for col in share_columns:
        vals = df[col].dropna()
        if len(vals) == 0:
            continue
        if vals.max() > 1.5:
            logger.info(
                f"TYNDP column '{col}': max={vals.max():.1f} > 1.0, "
                f"converting from percentage to fraction (/100)"
            )
            df[col] = df[col] / 100.0

    # Validate share ranges
    for col in share_columns:
        vals = df[col].dropna()
        if len(vals) > 0:
            if (vals < -1e-9).any() or (vals > 1.0 + 1e-9).any():
                logger.warning(
                    f"TYNDP share column '{col}' out of [0,1]: "
                    f"min={vals.min():.4f}, max={vals.max():.4f}"
                )

    # Production index columns (100 = base year)
    index_columns = [c for c in df.columns if "_production" in c.lower() and "_share" not in c.lower()]
    for col in index_columns:
        vals = df[col].dropna()
        if len(vals) > 0 and vals.max() > 10:
            logger.info(
                f"TYNDP column '{col}': max={vals.max():.1f}, "
                f"converting from percentage index to fraction (/100)"
            )
            df[col] = df[col] / 100.0

    logger.info(
        f"TYNDP: {len(df)} countries, scenario={scenario}, "
        f"target_year={target_year}, {len(df.columns)} columns"
    )

    if countries is not None:
        df = df[df["country"].isin(countries)].reset_index(drop=True)

    _set_cache(cache_key, df)
    return df


def fetch_all_industry_data(
    jrc_idees_path: str | Path | None = None,
    tyndp_path: str | Path | None = None,
    eurostat_tsv_path: str | Path | None = None,
    countries: list[str] | None = None,
    force_reload: bool = False,
) -> Dict[str, pd.DataFrame]:
    """Fetch all industry datasets in a single call.

    When paths are None, uses static fallback data.  When set to
    ``"auto"``, downloads from the original data sources.

    Args:
        jrc_idees_path: Path to JRC-IDEES data directory, ``"auto"``,
            or None (static fallback).
        tyndp_path: Path to TYNDP file, ``"auto"``, or None (skip TYNDP).
        eurostat_tsv_path: Path to Eurostat TSV, ``"auto"``, or None (skip).
        countries: Optional list of ISO-2 country codes.
        force_reload: If True, skip cache and recompute from source.

    Returns:
        Dictionary with keys: steel, ammonia, methanol, olefins, refinery.
        Optionally includes "tyndp" if tyndp_path is provided, and
        "eurostat_refinery" if eurostat_tsv_path is provided.
    """
    result = {
        "steel": fetch_steel_production(
            jrc_idees_path, tyndp_path,
            countries=countries, force_reload=force_reload,
        ),
        "ammonia": fetch_ammonia_production(
            countries=countries, force_reload=force_reload,
        ),
        "methanol": fetch_methanol_production(
            countries=countries, force_reload=force_reload,
        ),
        "olefins": fetch_olefins_production(
            countries=countries, force_reload=force_reload,
        ),
        "refinery": fetch_refinery_output(
            countries=countries, force_reload=force_reload,
        ),
    }

    if tyndp_path is not None:
        result["tyndp"] = fetch_tyndp_demand_parameters(
            tyndp_path, countries=countries, force_reload=force_reload,
        )

    if eurostat_tsv_path is not None:
        result["eurostat_refinery"] = fetch_eurostat_refinery_output(
            eurostat_tsv_path, countries=countries, force_reload=force_reload,
        )

    return result
