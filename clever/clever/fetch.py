"""
clever.fetch — Download CLEVER scenario data and EOLES cost inputs.

This module handles downloading and caching the CLEVER Excel workbook from
the public data repository, along with validation of EOLES cost CSV files.

Features:
  - Atomic downloads with tempfile + shutil.move
  - SHA-256 integrity logging
  - Minimum file size validation (50 KB)
  - Stale cache fallback on download failures
  - EOLES CSV validation and directory setup
"""

import hashlib
import logging
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import clever

# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

CLEVER_DATA_URL = "https://data.clever-energy-scenario.eu/Data_CLEVER.xlsx"
DATA_CLEVER_FILENAME = "Data_CLEVER.xlsx"
_MIN_FILE_SIZE_BYTES = 50_000

# ─────────────────────────────────────────────────────────────────────────
# Module logger
# ─────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────


def fetch_clever_xlsx(
    data_dir: Optional[Path] = None,
    url: str = CLEVER_DATA_URL,
    force: bool = False,
) -> Path:
    """
    Download the CLEVER Excel workbook with atomic writes and integrity checks.

    Downloads the CLEVER scenario data workbook from the public repository.
    Downloads go to a temporary file first, then are atomically moved to the
    target location. If a download fails, the existing cached file (if any)
    is used as a fallback.

    Parameters
    ----------
    data_dir : Path, optional
        Directory where the Excel file is stored.
        Defaults to ``clever.RESULTS_DIR``.
    url : str
        URL to download from. Defaults to the official CLEVER repository.
    force : bool
        If True, re-download even if the file already exists.
        If False (default), use existing file if present.

    Returns
    -------
    Path
        Path to the downloaded (or cached) CLEVER Excel workbook.

    Raises
    ------
    FileNotFoundError
        If download fails and no cached file exists.
    ValueError
        If downloaded file is smaller than minimum size (50 KB).

    Examples
    --------
    >>> xlsx_path = fetch_clever_xlsx()
    >>> assert xlsx_path.exists()
    >>> assert xlsx_path.stat().st_size > 50_000

    >>> # Force re-download even if cached
    >>> xlsx_path = fetch_clever_xlsx(force=True)
    """
    if data_dir is None:
        data_dir = clever.RESULTS_DIR

    data_dir.mkdir(parents=True, exist_ok=True)
    target_path = data_dir / DATA_CLEVER_FILENAME

    # If file exists and not forcing re-download, return it
    if target_path.exists() and not force:
        logger.info(f"Using cached CLEVER workbook at {target_path}")
        return target_path

    logger.info(f"Downloading CLEVER workbook from {url}")

    try:
        # Download to temporary file first (atomic write)
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".xlsx",
            dir=data_dir,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            urllib.request.urlretrieve(url, tmp_path)
        except urllib.error.URLError as e:
            tmp_path.unlink(missing_ok=True)
            raise

        # Validate file size
        file_size = tmp_path.stat().st_size
        if file_size < _MIN_FILE_SIZE_BYTES:
            tmp_path.unlink()
            raise ValueError(
                f"Downloaded file too small: {file_size} bytes "
                f"(minimum: {_MIN_FILE_SIZE_BYTES} bytes)"
            )

        # Compute SHA-256 for integrity logging
        sha256_hash = _compute_sha256(tmp_path)
        logger.info(f"Downloaded file SHA-256: {sha256_hash}")

        # Atomic move to target location
        shutil.move(str(tmp_path), str(target_path))
        logger.info(f"Saved CLEVER workbook to {target_path}")

        return target_path

    except (urllib.error.URLError, ValueError) as e:
        logger.warning(f"Download failed: {e}")

        # Fallback to cached file if available
        if target_path.exists():
            logger.info(f"Using stale cached file as fallback: {target_path}")
            return target_path

        # No fallback available
        raise FileNotFoundError(
            f"Could not download CLEVER workbook from {url} and no cached "
            f"file exists at {target_path}"
        ) from e


def fetch_eoles_inputs(
    eoles_dir: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """
    Validate EOLES input CSV files and create directory structure.

    Ensures that the EOLES inputs directory exists and contains the required
    cost CSV files for 2026 scenarios. This function does not download files
    (they must be provided by the user); it validates that the expected files
    are present.

    Expected files:
      - fOM_2026.csv (fixed operating & maintenance costs)
      - vOM_2026.csv (variable operating & maintenance costs)
      - capex_2026.csv (capital expenditure)
      - discount_rate_uniform.csv (discount rate)
      - storage_capex_2026.csv (storage capital expenditure)

    Parameters
    ----------
    eoles_dir : Path, optional
        Directory containing EOLES input CSVs.
        Defaults to ``clever.EOLES_DIR``.
    force : bool
        Currently unused (reserved for future use).

    Returns
    -------
    Path
        Path to the EOLES inputs directory.

    Raises
    ------
    FileNotFoundError
        If the directory does not exist or required CSV files are missing,
        with instructions on how to obtain them.

    Examples
    --------
    >>> eoles_dir = fetch_eoles_inputs()
    >>> assert eoles_dir.exists()
    >>> assert (eoles_dir / "fOM_2026.csv").exists()
    """
    if eoles_dir is None:
        eoles_dir = clever.EOLES_DIR

    eoles_dir.mkdir(parents=True, exist_ok=True)

    # List of expected CSV files
    expected_files = {
        "fOM_2026.csv",
        "vOM_2026.csv",
        "capex_2026.csv",
        "discount_rate_uniform.csv",
        "storage_capex_2026.csv",
    }

    # Check which files are missing
    missing_files = {f for f in expected_files if not (eoles_dir / f).exists()}

    if missing_files:
        missing_list = "\n  ".join(sorted(missing_files))
        raise FileNotFoundError(
            f"EOLES input CSV files are missing from {eoles_dir}.\n\n"
            f"Missing files:\n  {missing_list}\n\n"
            f"Please download these files from the EOLES repository and place "
            f"them in:\n  {eoles_dir}\n\n"
            f"See the EOLES documentation for data availability and access "
            f"instructions."
        )

    logger.info(f"EOLES inputs validated at {eoles_dir}")
    return eoles_dir


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _compute_sha256(file_path: Path) -> str:
    """
    Compute SHA-256 hash of a file.

    Parameters
    ----------
    file_path : Path
        Path to file.

    Returns
    -------
    str
        SHA-256 hash in hexadecimal.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()
