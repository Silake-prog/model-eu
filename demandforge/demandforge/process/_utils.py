"""Shared utility functions for hydrogen process modules.

This module contains reusable vectorized ramp functions used across
multiple process submodules (steel, refinery, etc.).
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def linear_ramp(
    years: np.ndarray,
    start_year: int,
    end_year: int,
    start_value: float = 0.0,
    end_value: float = 1.0,
) -> np.ndarray:
    """Vectorized linear ramp for numpy arrays.

    Computes a linear ramp from start_value to end_value over the period
    [start_year, end_year]. Values before start_year are set to start_value,
    and values after end_year are set to end_value.

    Args:
        years: Array of scenario years.
        start_year: Beginning of ramp (inclusive).
        end_year: End of ramp (inclusive).
        start_value: Value at start_year.
        end_value: Value at end_year.

    Returns:
        Array of ramp values, one per year.

    Raises:
        ValueError: If years array is empty.
    """
    if len(years) == 0:
        raise ValueError("years array cannot be empty")

    if start_year > end_year:
        logger.warning(f"start_year={start_year} > end_year={end_year}; swapping")
        start_year, end_year = end_year, start_year

    ramp = np.interp(years, [start_year, end_year], [start_value, end_value])
    ramp[years < start_year] = start_value
    ramp[years > end_year] = end_value

    return ramp
