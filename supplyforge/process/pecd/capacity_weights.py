r"""
Per-zone installed-capacity weights for PECD national capacity-factor aggregation.

PECD wind capacity factors are published on sub-national zones (PEON/P2ON onshore,
PEOF/P2OF offshore) and must be aggregated to a national capacity factor with
**installed-capacity weights** (``CF_nat = sum_z CF_z * Cap_z / sum_z Cap_z``,
see :func:`supplyforge.fetch.pecd.zones.capacity_weighted_cf`). Multi-zone solar
(SZON) is treated the same; single-zone countries need no weights.

This module resolves ``{zone_code: installed_capacity_MW}`` for a technology, in a
deterministic, precise-when-data-is-present, **never-silent** way:

1. **Config dict** ``pecd.zone_capacities[<tech>] = {<zone>: MW}`` - exact, fully
   maintainer-controlled (ideally populated from the PECD onshore/offshore *run
   workbooks*, the authoritative per-zone capacity source).
2. **Config CSV** ``pecd.zone_capacities_csv`` - a table with (zone, capacity[,
   technology]) columns; convenient when extracting the run-workbook capacities to
   a small file.
3. Otherwise ``None`` -> the caller falls back to **equal weights with a loud
   warning** (less precise; only affects the few multi-zone countries).

``<tech>`` is one of ``wind_onshore``, ``wind_offshore``, ``solar`` (the keys also
accept the data codes ``WON``/``WOF``/``SPV``).

To populate capacities, call :func:`write_capacity_template` to emit a per-zone CSV
skeleton from the zone registry, fill it with installed MW from your PEMMDB/ERAA
scenario, and point ``pecd.zone_capacities_csv`` at it. Note per-zone capacities are
**not** part of the PECD CDS deliverable (PECD ships capacity *factors*, not
capacities), so they come from your own scenario / PEMMDB-ERAA, not an auto-fetch.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Canonical technology keys (+ data-code aliases).
_TECH_ALIASES = {
    "won": "wind_onshore", "wind_onshore": "wind_onshore",
    "wof": "wind_offshore", "wind_offshore": "wind_offshore",
    "spv": "solar", "solar": "solar",
}
# Keywords used to match a free-text technology column in a capacity CSV.
_TECH_KEYWORDS = {
    "wind_onshore": ("onshore", "won"),
    "wind_offshore": ("offshore", "wof"),
    "solar": ("solar", "pv", "spv"),
}

_ZONE_COL_CANDIDATES = ("zone", "zone_code", "szon", "peon", "peof", "code", "node", "market_node")
_CAP_COL_CANDIDATES = ("capacity_mw", "capacity", "installed_capacity", "installed_capacity_mw", "mw", "value")
_TECH_COL_CANDIDATES = ("technology", "tech", "variable", "type")

_RUN_WORKBOOK_TODO = (
    "Run write_capacity_template(country, level, path) to emit a per-zone CSV "
    "skeleton (from the zone registry), fill it with installed MW from your "
    "PEMMDB/ERAA scenario, and point pecd.zone_capacities_csv at it."
)

# PECD spatial level -> the technology its zones carry (for the capacity template).
_LEVEL_TECH = {
    "peon": "wind_onshore", "p2on": "wind_onshore",
    "peof": "wind_offshore", "p2of": "wind_offshore",
    "szon": "solar",
}


def _canon_tech(tech: str) -> str:
    key = _TECH_ALIASES.get(tech.lower())
    if key is None:
        raise ValueError(f"Unknown technology '{tech}'. Expected one of {sorted(set(_TECH_ALIASES.values()))}.")
    return key


def _pick(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def _read_capacity_csv(path: str | Path, tech: str) -> dict[str, float] | None:
    """Read ``{zone: MW}`` for a technology from a flexible capacity CSV.

    Recognises common column names for zone / capacity / technology and filters
    to the requested technology when a technology column is present.
    """
    import pandas as pd

    p = Path(path)
    if not p.is_file():
        logger.warning("PECD weights: zone_capacities_csv '%s' not found.", p)
        return None
    df = pd.read_csv(p)
    zone_c = _pick(list(df.columns), _ZONE_COL_CANDIDATES)
    cap_c = _pick(list(df.columns), _CAP_COL_CANDIDATES)
    if zone_c is None or cap_c is None:
        logger.warning(
            "PECD weights: CSV '%s' lacks a recognisable zone/capacity column "
            "(columns=%s); ignoring.", p, list(df.columns),
        )
        return None

    tech_c = _pick(list(df.columns), _TECH_COL_CANDIDATES)
    if tech_c is not None:
        kw = _TECH_KEYWORDS[tech]
        mask = df[tech_c].astype(str).str.lower().apply(lambda s: any(k in s for k in kw))
        df = df[mask]

    weights: dict[str, float] = {}
    for _, row in df.iterrows():
        try:
            cap = float(row[cap_c])
        except (TypeError, ValueError):
            continue
        if cap > 0:
            weights[str(row[zone_c])] = cap
    return weights or None


def _all_known_zones() -> frozenset:
    """Every PECD zone code across the registry (for typo / wrong-level detection)."""
    from supplyforge.fetch.pecd import zone_registry as zr
    return frozenset(z for lvl in zr.PECD_ZONES.values() for zs in lvl.values() for z in zs)


def _warn_unknown_zones(weights: dict, tech: str) -> None:
    unknown = sorted(z for z in (weights or {}) if z not in _all_known_zones())
    if unknown:
        logger.warning(
            "PECD weights: %d zone(s) for %s are not in the PECD zone registry "
            "(possible typos / wrong level): %s", len(unknown), tech, unknown,
        )


def write_capacity_template(country: str, level: str, path: str | Path) -> Path:
    """Emit a CSV skeleton of a country's zones at ``level`` to fill with installed MW.

    Columns ``zone,technology,capacity_mw`` (capacity left blank). Fill it from your
    PEMMDB/ERAA scenario and point ``pecd.zone_capacities_csv`` at the result for
    precise capacity-weighted national aggregation.
    """
    from supplyforge.fetch.pecd import zone_registry as zr

    zones = zr.zones_for(country, level)
    if not zones:
        raise ValueError(
            f"No PECD zones for country {country!r} at level {level!r}. "
            f"Known levels: {zr.levels()}; example countries: {zr.countries(level)[:5] or '—'}."
        )
    tech = _LEVEL_TECH.get(level, "solar")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        f.write("zone,technology,capacity_mw\n")
        for z in zones:
            f.write(f"{z},{tech},\n")
    logger.info(
        "PECD weights: wrote capacity template for %s@%s (%d zones, tech=%s) -> %s",
        country, level, len(zones), tech, p,
    )
    return p


def get_zone_weights(tech: str, config: dict) -> dict[str, float] | None:
    """Resolve per-zone installed-capacity weights for a technology.

    Args:
        tech: ``wind_onshore`` | ``wind_offshore`` | ``solar`` (or the data codes
            ``WON``/``WOF``/``SPV``).
        config: Full SupplyForge config dict (reads the ``pecd`` block).

    Returns:
        ``{zone_code: capacity_MW}`` (positive capacities only), or ``None`` when
        no per-zone capacities are configured (the caller then uses equal weights
        and logs a warning).
    """
    tech = _canon_tech(tech)
    pecd_cfg = config.get("pecd") or {}

    table = (pecd_cfg.get("zone_capacities") or {}).get(tech)
    if table:
        weights = {str(z): float(c) for z, c in table.items() if c is not None and float(c) > 0}
        if weights:
            _warn_unknown_zones(weights, tech)
            logger.info("PECD weights: using config zone_capacities for %s (%d zones).", tech, len(weights))
            return weights

    csv = pecd_cfg.get("zone_capacities_csv")
    if csv:
        weights = _read_capacity_csv(csv, tech)
        if weights:
            _warn_unknown_zones(weights, tech)
            logger.info("PECD weights: using zone_capacities_csv for %s (%d zones).", tech, len(weights))
            return weights

    logger.warning(
        "PECD weights: no per-zone installed capacities configured for %s; "
        "multi-zone aggregation will use EQUAL weights (less precise). "
        "Set pecd.zone_capacities or pecd.zone_capacities_csv for precise weighting. %s",
        tech, _RUN_WORKBOOK_TODO,
    )
    return None


__all__ = ["get_zone_weights", "write_capacity_template"]
