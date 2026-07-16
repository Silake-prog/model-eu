r"""
Global Wind Atlas (GWA) bias-correction for the raw-ERA5 wind engine.

ERA5 100 m wind is systematically biased (low in complex terrain — Alps, Norway, Balkans),
so wind **speed** is scaled per grid cell by the long-run GWA-to-ERA5 mean-speed ratio
*before* the turbine power curve (the curve is non-linear, so correcting the speed — not the
CF — is the right place):

    CF = powercurve( wind_era5 * GWA_mean / ERA5_mean )

Solar is untouched. Default ``gwa2`` (mandatory per spec §4.9); ``none`` is allowed but warned.
If the GWA layer can't be fetched the engine proceeds uncorrected (fail-soft, S1) — logged.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

BIAS_METHODS = ("none", "gwa2")


def resolve_bias_correction(config: dict) -> str:
    """Validated ``era5.bias_correction`` (default ``gwa2``); warns loudly on ``none``."""
    method = ((config.get("era5") or {}).get("bias_correction") or "gwa2").lower()
    if method not in BIAS_METHODS:
        raise ValueError(f"era5.bias_correction must be one of {BIAS_METHODS}, got '{method}'.")
    if method == "none":
        logger.warning(
            "era5.bias_correction=none: ERA5 wind is uncorrected and systematically biased in "
            "complex terrain (Alps/Norway/Balkans); not recommended, especially for mountainous "
            "countries. Default is 'gwa2'."
        )
    return method


def _scaling_ratio(era5_mean, gwa_mean) -> np.ndarray:
    """Per-cell multiplicative wind-speed scaling = ``GWA_mean / ERA5_mean``.

    Guards: where ERA5 mean <= 0 or either mean is non-finite, the ratio is ``1.0`` (no change).
    Pure-numpy core — unit-testable offline (no atlite/raster).
    """
    era5 = np.asarray(era5_mean, dtype=float)
    gwa = np.asarray(gwa_mean, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = gwa / era5
    ok = np.isfinite(ratio) & np.isfinite(gwa) & np.isfinite(era5) & (era5 > 0)
    return np.where(ok, ratio, 1.0)


def gwa_scaling_field(cutout, country: str, config: dict):
    """Per-cell wind-speed scaling field for a cutout (``GWA_mean / ERA5_mean``), or ``None``.

    Phase-2 GWA step: fetch the Global Wind Atlas mean-speed raster for ``country``, regrid it
    onto the cutout grid (``rioxarray.reproject_match``), and return :func:`_scaling_ratio` of
    (regridded GWA mean, cutout ERA5 mean). Returns ``None`` (logged) when the GWA layer is
    unavailable, so the caller proceeds uncorrected (fail-soft).
    """
    logger.warning(
        "ERA5 GWA: scaling-field fetch not yet wired for %s; proceeding with UNCORRECTED ERA5 "
        "wind. (Phase-2 GWA step.)", country,
    )
    return None


__all__ = ["BIAS_METHODS", "resolve_bias_correction", "_scaling_ratio", "gwa_scaling_field"]
