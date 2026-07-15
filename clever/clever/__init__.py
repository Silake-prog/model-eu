"""
clever
------

A Python package for building electricity adequacy and price models
calibrated on the CLEVER European sufficiency scenario.

This package provides a clean fetch → process → model → solve pipeline
that integrates CLEVER scenario data with the POMMES optimisation framework,
EOLES cost assumptions, DemandForge hourly demand shaping, and SupplyForge
VRE / hydro chronologies.

The package is designed to be added to the demandforge GitLab repository
so that any user with demandforge + supplyforge installed can reproduce
the full analysis from a self-sufficient notebook.

Submodules
~~~~~~~~~~
- **fetch**    : download the CLEVER Excel workbook
- **process**  : extract structured CSVs from the workbook
- **demand**   : build hourly electricity demand via DemandForge
- **constants**: all mappings, PEMMDB hydro data, EOLES parameters
- **model**    : build POMMES EnergyModel from CLEVER inputs
- **runner**   : solve the model, sanitize inputs, export results
- **adequacy** : ex-ante and ex-post adequacy diagnostics
"""

__version__ = "0.1.0"

__all__ = [
    "get_writable_data_path",
    "PACKAGE_DIR",
    "INTERNAL_DATA_PATH",
    "USER_DATA_PATH",
    "RESULTS_DIR",
    "CLEVER_CSV_DIR",
    "EOLES_DIR",
    "DEMAND_DIR",
]

import os
from pathlib import Path

from platformdirs import user_data_dir

# ── Package root (where the code lives) ───────────────────────────
PACKAGE_DIR = Path(__file__).parent.parent
INTERNAL_DATA_PATH = PACKAGE_DIR / "results"

# ── User-writable fallback (e.g. ~/.local/share/clever) ──────────
USER_DATA_PATH = Path(user_data_dir("clever"))


def get_writable_data_path() -> Path:
    """Determine the best location for persistent data storage.

    Priority order:
      1. ``CLEVER_DATA`` environment variable (if set).
      2. ``<package_dir>/results`` (if writable).
      3. ``~/.local/share/clever`` (always writable).
    """
    # 1. Environment variable override
    env = os.environ.get("CLEVER_DATA")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # 2. Try package-local results directory
    try:
        if not INTERNAL_DATA_PATH.exists():
            INTERNAL_DATA_PATH.mkdir(parents=True, exist_ok=True)
        if os.access(INTERNAL_DATA_PATH, os.W_OK):
            return INTERNAL_DATA_PATH
    except (PermissionError, OSError):
        pass

    # 3. Fallback to user home
    USER_DATA_PATH.mkdir(parents=True, exist_ok=True)
    return USER_DATA_PATH


# Initialise once at import time — all modules can use clever.RESULTS_DIR
RESULTS_DIR = get_writable_data_path()

# Sub-directories created lazily by individual modules
CLEVER_CSV_DIR = RESULTS_DIR / "clever_csv"
EOLES_DIR = RESULTS_DIR / "eoles_inputs"
DEMAND_DIR = RESULTS_DIR / "hourly_demand"
