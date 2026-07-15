"""
DemandForge package initialization.

This module exposes commonly used project paths:
- ROOT_DIR: the project root directory
- RESULTS_DIR: the default results directory used by fetch/process modules

DemandForge — electricity and hydrogen demand projection for POMMES.

This package provides tools for projecting country-level electricity and
hydrogen demand curves, suitable for input to the POMMES / pommes_craft
energy optimisation pipeline.

Subpackages:
    fetch/           Raw data acquisition (ENTSO-E, JRC-IDEES, TYNDP, etc.)
    process/         Data cleaning, transformation, intermediate computations
    load_projection/ Scenario construction (electricity and hydrogen)
    tests/           Unit and integration tests
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    from platformdirs import user_data_dir

    _default = Path(user_data_dir("demandforge", "persee"))
except ImportError:
    _default = Path(__file__).resolve().parent / "results"

RESULTS_DIR: Path = Path(os.environ.get("DEMANDFORGE_DATA", str(_default)))
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)
__version__ = "0.2.2"

import os
from pathlib import Path
from platformdirs import user_data_dir

# 1. Define the internal path (Code location)
PACKAGE_DIR = Path(__file__).parent.parent
INTERNAL_DATA_PATH = PACKAGE_DIR / "results"

# 2. Define the user-writable path (Standard fallback)
USER_DATA_PATH = Path(user_data_dir("demandforge"))


def get_writable_data_path():
    """
    Determines the best location for data storage.
    Prioritizes the package directory, falls back to user home.
    """
    # Option A: Check if an Env Var overrides everything (Best Practice)
    if os.environ.get("DEMANDFORGE_DATA"):
        path = Path(os.environ["DEMANDFORGE_DATA"])
        path.mkdir(parents=True, exist_ok=True)
        return path

    # Option B: Try to use the package directory (Your preferred behavior)
    try:
        # Check if we can write to the parent dir or the dir itself
        if not INTERNAL_DATA_PATH.exists():
            INTERNAL_DATA_PATH.mkdir(parents=True, exist_ok=True)

        # Test write permissions specifically
        if not os.access(INTERNAL_DATA_PATH, os.W_OK):
            raise PermissionError

        return INTERNAL_DATA_PATH

    except (PermissionError, OSError):
        # Option C: Fallback to User Home Directory
        # This works on JupyterHub because users always own their home
        USER_DATA_PATH.mkdir(parents=True, exist_ok=True)
        return USER_DATA_PATH


# Initialize the paths based on the logic above
RESULTS_DIR = get_writable_data_path()
