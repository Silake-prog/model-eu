"""
supplyforge
-----------

A Python package for generating country-level electricity supply datasets for power system analysis.

This package provides a reproducible Snakemake workflow to build a consistent and transparent
data foundation describing the generation infrastructure and resources of European electricity systems.
It integrates data primarily from ENTSO-E to create validated, ready-to-use datasets for
scenario analysis and planning models.

For more information, please visit the [GitLab repository](https://git.persee.minesparis.psl.eu/energy-alternatives/supplyforge).
"""

__version__ = "0.1.5"
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

import os
from pathlib import Path
from platformdirs import user_data_dir

# 1. Define the internal path (Code location)
PACKAGE_DIR = Path(__file__).parent.parent
INTERNAL_DATA_PATH = PACKAGE_DIR / "results"

# 2. Define the user-writable path (Standard fallback)
# On Linux/JupyterHub this is usually ~/.local/share/pommes_craft
# 2. Define the user-writable path (Standard fallback)
USER_DATA_PATH = Path(user_data_dir("supplyforge"))


def get_writable_data_path():
    """
    Determines the best location for data storage.
    Prioritizes the package directory, falls back to user home.
    """
    # Option A: Check if an Env Var overrides everything (Best Practice)
    if os.environ.get("SUPPLYFORGE_DATA"):
        path = Path(os.environ["SUPPLYFORGE_DATA"])
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

pd.set_option('future.no_silent_downcasting', True)