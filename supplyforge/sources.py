"""Resolution of the orthogonal weather-data source selectors.

Two config keys select where the *weather-dependent* model inputs come from:

- ``res_source`` : ``entsoe`` | ``era5`` | ``pecd``  -> wind & solar capacity factors
- ``hydro_source`` : ``auto`` | ``entsoe`` | ``pecd`` -> reservoir inflow + run-of-river

``hydro_source: auto`` follows ``res_source`` when it can supply hydro
(``pecd -> pecd``, ``entsoe -> entsoe``) and **falls back to ``entsoe`` when
``res_source == era5``** (a raw-ERA5 engine cannot practically produce hydro
inflows). Run-of-river follows the resolved hydro source, except that under a
PECD hydro source the ``pecd.ror_source`` key may pin it to ``entsoe`` or
``pecd`` (default ``pecd``).

Both keys are optional and BACKWARD-COMPATIBLE: when absent the functions below
resolve to ``entsoe`` everywhere, i.e. the original (pre-PECD) behaviour.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

RES_SOURCES = ("entsoe", "era5", "pecd")
HYDRO_SOURCES = ("auto", "entsoe", "pecd")
ROR_SOURCES = ("entsoe", "pecd")


def resolve_res_source(config: dict) -> str:
    """Return the validated ``res_source`` (default ``entsoe``)."""
    rs = (config.get("res_source") or "entsoe").lower()
    if rs not in RES_SOURCES:
        raise ValueError(f"res_source must be one of {RES_SOURCES}, got '{rs}'.")
    return rs


def resolve_hydro_source(config: dict) -> str:
    """Resolve ``hydro_source``, expanding ``auto`` against ``res_source``.

    Returns ``entsoe`` or ``pecd`` (never ``auto``). Logs loudly when ``auto``
    falls back to ``entsoe`` because ``res_source == era5``.
    """
    rs = resolve_res_source(config)
    hs = (config.get("hydro_source") or "auto").lower()
    if hs not in HYDRO_SOURCES:
        raise ValueError(f"hydro_source must be one of {HYDRO_SOURCES}, got '{hs}'.")
    if hs == "auto":
        if rs == "pecd":
            return "pecd"
        if rs == "era5":
            logger.warning(
                "hydro_source=auto with res_source=era5 -> falling back to ENTSO-E "
                "inflow reconstruction (a raw-ERA5 engine cannot produce hydro inflows)."
            )
        return "entsoe"
    return hs


def resolve_ror_source(config: dict) -> str:
    """Resolve the run-of-river capacity-factor source.

    RoR follows the resolved hydro source; under a PECD hydro source the
    ``pecd.ror_source`` key (default ``pecd``) may pin it to ``entsoe`` or
    ``pecd``.
    """
    hs = resolve_hydro_source(config)
    if hs != "pecd":
        return "entsoe"
    pecd_cfg = config.get("pecd") or {}
    ror = (pecd_cfg.get("ror_source") or "pecd").lower()
    if ror not in ROR_SOURCES:
        raise ValueError(f"pecd.ror_source must be one of {ROR_SOURCES}, got '{ror}'.")
    return ror


__all__ = [
    "RES_SOURCES",
    "HYDRO_SOURCES",
    "ROR_SOURCES",
    "resolve_res_source",
    "resolve_hydro_source",
    "resolve_ror_source",
]
