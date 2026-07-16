r"""
PECD 4.2 country -> zone mapping and zonal aggregation helpers.

Role in SupplyForge pipeline
----------------------------

PECD 4.2 delivers its aggregated energy variables on **bidding-zone / wind-zone**
grids, *not* on ISO-2 countries:

- hydro (``HRI``, ``HRR``, ``HPI``, ``HOL`` ...) and solar (``SPV``) are published
  at ``SZON`` (onshore bidding zones);
- onshore wind (``WON``) at ``PEON`` / ``P2ON`` (ERAA 2025 / 2026 wind zones);
- offshore wind (``WOF``) at ``PEOF`` / ``P2OF``.

A single country therefore maps to **one or several** zone columns in a PECD CSV
(e.g. ``DK -> {DKE1, DKW1}``, ``IT -> {ITN1, ITCN, ITCS, ITS1, ITSA, ITSI, ...}``,
``NO -> {NOS0, NOM1, NON1, ...}``, ``SE -> {SE01..SE04}``). This module owns the
country -> zone resolution and the two aggregation rules used everywhere
downstream:

    * **energy variables are SUMMED** across a country's zones (additive);
    * **capacity factors are CAPACITY-WEIGHTED-averaged** across zones
      (``CF_nat = sum_z CF_z * Cap_z / sum_z Cap_z``).

Methodology / design
--------------------

PECD zone codes are positional: every zone code begins with a **2-letter area
code** that, with two documented exceptions, equals the country's ISO-2 code:

- **Greece**: the live PECD SZON product uses ``GR`` (e.g. ``GR00``), while
  NUTS / the ``country_borders.gpkg`` use ``EL`` — we accept **both** prefixes so
  zone selection is robust across products (cf. ``demandforge``'s ``{"GR": "EL"}``);
- **United Kingdom** is ``UK`` here and may appear as ``UK`` or ``GB`` in PECD.

Rather than hard-code every (volatile) sub-zone code, zone selection is performed
by **prefix match against the actual columns of the downloaded CSV** (the CSV
header *is* the authoritative list of available zones for a given variable /
spatial level). The explicit :data:`KNOWN_PECD_SZON` table below is documentation
and test material only; the operational selector
:func:`select_zone_columns` always intersects the declared prefixes with the
columns really present and logs any mismatch. This keeps the mapping correct even
where a country's exact sub-zone codes differ between PECD spatial levels
(``SZON`` vs ``PEON`` vs ``P2ON``) or product versions.

Conventions logged at runtime
-----------------------------

- the ``GR -> EL`` (and ``UK -> {UK, GB}``) area-code remap;
- which CSV columns were selected for a country, and a warning when none match
  (the country is then absent from PECD and handled fail-soft upstream);
- when a capacity-factor aggregation falls back to equal weights because per-zone
  installed capacities were unavailable (never silent).

This module has no external dependencies beyond ``polars`` and is importable from
both the fetch and process layers.
"""
from __future__ import annotations

import logging
from typing import Iterable, Mapping

import polars as pl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Area-code remaps: ISO-2 (repo/ENTSO-E) -> PECD/NUTS area-code prefix(es)
# ---------------------------------------------------------------------------
# Most countries use their ISO-2 code as the PECD zone-code prefix. Only the
# documented clashes need an override. Greece: NUTS/PECD uses "EL". UK: accept
# both "UK" and "GB" because PECD products have used either across versions.
PECD_PREFIX_OVERRIDES: dict[str, list[str]] = {
    # The live PECD SZON product uses GR00 for Greece (verified against
    # sis-energy-pecd), while NUTS / country_borders.gpkg use EL — accept both.
    "GR": ["GR", "EL"],
    "UK": ["UK", "GB"],
}

# Backwards-readable single-value remap (mirrors demandforge's {"GR": "EL"}).
NUTS_COUNTRY_REMAP: dict[str, str] = {"GR": "EL"}

# ---------------------------------------------------------------------------
# Documentation / test reference of the multi-zone SZON splits (PROVISIONAL).
# The operational selector keys off the real CSV columns, not this table; it is
# kept so tests and reviewers can see the intended multi-zone behaviour. Exact
# sub-zone codes are confirmed at runtime against the downloaded CSV header.
# ---------------------------------------------------------------------------
KNOWN_PECD_SZON: dict[str, list[str]] = {
    "DK": ["DKE1", "DKW1"],
    "IT": ["ITN1", "ITCN", "ITCS", "ITS1", "ITSA", "ITSI", "ITCA"],
    "NO": ["NOS0", "NOM1", "NON1"],
    "SE": ["SE01", "SE02", "SE03", "SE04"],
    "LU": ["LUB1", "LUF1", "LUG1"],
}

# Countries that are (typically) a single SZON zone equal to "<CC>00".
# Listed only for documentation; selection is still prefix-based at runtime.
SINGLE_ZONE_HINT_SUFFIX = "00"


def pecd_country_prefixes(country: str) -> list[str]:
    """Return the accepted PECD area-code prefix(es) for a repo country code.

    Args:
        country: Two-letter country code as used in ``config.yaml`` (e.g. ``"FR"``,
            ``"GR"``, ``"UK"``).

    Returns:
        List of upper-case 2-letter prefixes to match against PECD zone codes.
        Defaults to ``[country.upper()]`` unless an override applies
        (``GR -> ["EL"]``, ``UK -> ["UK", "GB"]``).
    """
    cc = country.upper()
    return PECD_PREFIX_OVERRIDES.get(cc, [cc])


def select_zone_columns(columns: Iterable[str], country: str) -> list[str]:
    """Select the PECD zone columns belonging to a country.

    Zone selection is by 2-letter prefix match against the *actual* columns of a
    PECD CSV (the authoritative list of available zones). ISO-2 prefixes are
    unique per country, so collisions do not occur in practice.

    Args:
        columns: Column labels of a loaded PECD CSV (the zone codes), e.g.
            ``["AL00", "AT00", "DKE1", "DKW1", ...]``. Non-zone columns such as a
            ``Date`` index column are ignored.
        country: Two-letter repo country code.

    Returns:
        Sorted list of matching zone-code columns. Empty (with a warning) if the
        country has no zone in this product/level — the caller then treats the
        country as absent from PECD (fail-soft).
    """
    prefixes = set(pecd_country_prefixes(country))
    matched = sorted(
        str(c) for c in columns
        if str(c)[:2].upper() in prefixes and str(c).lower() != "date"
    )
    if not matched:
        logger.warning(
            "PECD: no zone columns matched country '%s' (prefixes=%s). "
            "Available columns: %s. Country will be absent from this PECD product.",
            country, sorted(prefixes), [str(c) for c in columns][:20],
        )
    else:
        logger.info("PECD: country '%s' -> zones %s", country, matched)
    return matched


def sum_zone_energy(df: pl.DataFrame, zone_cols: list[str]) -> pl.Series:
    """Sum an energy variable row-wise across a country's zones.

    Used for additive hydro energy variables (HRI/HRR/HPI/HOL, generation).

    Args:
        df: Wide PECD frame whose columns include ``zone_cols`` (one column per
            zone, rows = timestamps), values in energy units (e.g. MWh/week).
        zone_cols: The country's zone columns, from :func:`select_zone_columns`.

    Returns:
        A ``pl.Series`` named ``"value"`` of length ``df.height`` with the
        per-timestamp national total.

    Raises:
        ValueError: If ``zone_cols`` is empty.
    """
    if not zone_cols:
        raise ValueError("sum_zone_energy requires at least one zone column.")
    return (
        df.select(pl.sum_horizontal([pl.col(c) for c in zone_cols]).alias("value"))
        .get_column("value")
    )


def _active_zone_columns(df: pl.DataFrame, zone_cols: list[str]) -> list[str]:
    """Return the zones that carry real data (not entirely null/NaN/zero).

    PECD ships empty placeholder zones (notably offshore PEOF, but also some
    onshore/solar); counting them as 0 would dilute a national mean. A zone is
    "active" if it has at least one non-null, non-zero value over the year.
    """
    active: list[str] = []
    for z in zone_cols:
        total = df.select(
            pl.col(z).cast(pl.Float64, strict=False).fill_null(0.0).fill_nan(0.0).abs().sum()
        ).item()
        if total and total > 0:
            active.append(z)
    return active


def capacity_weighted_cf(
    df: pl.DataFrame,
    zone_cols: list[str],
    weights: Mapping[str, float] | None = None,
    *,
    country: str | None = None,
) -> pl.Series:
    """Capacity-weighted national capacity factor across a country's zones.

    Computes ``CF_nat = sum_z CF_z * w_z / sum_z w_z`` where ``w_z`` is the
    installed capacity of zone ``z``, **null/empty-aware**:

    - Empty/placeholder zones (entirely null/NaN/zero) are dropped first, so they
      do not dilute the mean — PECD ships many such zones, especially offshore.
    - Aggregation skips null cells *per hour* (an active zone missing a given hour
      drops out of that hour's average rather than counting as 0).
    - ``weights is None`` (or none usable) -> equal weights over the active zones
      (logged); some-missing -> the missing zones get the mean known weight (logged).

    A single active zone returns that zone's series; no active zone -> all zeros.

    Args:
        df: Wide PECD frame whose columns include ``zone_cols`` (CF in [0, 1]).
        zone_cols: The country's zone columns, from :func:`select_zone_columns`.
        weights: Optional mapping ``{zone_code: installed_capacity}``.
        country: Optional country code, for clearer log messages.

    Returns:
        A ``pl.Series`` named ``"capacity_factor"`` of length ``df.height``.

    Raises:
        ValueError: If ``zone_cols`` is empty.
    """
    if not zone_cols:
        raise ValueError("capacity_weighted_cf requires at least one zone column.")

    tag = f" for '{country}'" if country else ""

    active = _active_zone_columns(df, zone_cols)
    dropped = [z for z in zone_cols if z not in active]
    if dropped:
        logger.info("PECD: excluding %d empty/placeholder zone(s)%s: %s", len(dropped), tag, dropped)
    if not active:
        logger.warning("PECD: all %d zone(s) empty%s; capacity factor set to 0.", len(zone_cols), tag)
        return pl.Series("capacity_factor", [0.0] * df.height)

    if len(active) == 1:
        return df.get_column(active[0]).fill_nan(None).fill_null(0.0).alias("capacity_factor")

    # Resolve a positive weight per active zone.
    known = {
        z: float(weights[z]) for z in active
        if weights and weights.get(z) is not None and float(weights[z]) > 0
    }

    if not known:
        logger.warning(
            "PECD: no per-zone installed capacities available%s; aggregating %d zone(s) "
            "with EQUAL weights (less precise).", tag, len(active),
        )
        # Equal weight, ignoring null cells per row.
        return (
            df.select(pl.mean_horizontal([pl.col(z).fill_nan(None) for z in active]).alias("capacity_factor"))
            .get_column("capacity_factor")
            .fill_null(0.0)
        )

    if len(known) < len(active):
        fill = sum(known.values()) / len(known)
        missing = [z for z in active if z not in known]
        logger.warning(
            "PECD: missing per-zone capacity for zones %s%s; assigning each the mean "
            "known weight (%.1f) so they still contribute.", missing, tag, fill,
        )
        w = {z: known.get(z, fill) for z in active}
    else:
        w = known

    # Weighted mean over active zones, null-aware per hour.
    num = pl.sum_horizontal([
        pl.when(pl.col(z).fill_nan(None).is_not_null()).then(pl.col(z) * w[z]).otherwise(0.0)
        for z in active
    ])
    den = pl.sum_horizontal([
        pl.when(pl.col(z).fill_nan(None).is_not_null()).then(pl.lit(float(w[z]))).otherwise(0.0)
        for z in active
    ])
    return (
        df.select(pl.when(den > 0).then(num / den).otherwise(0.0).alias("capacity_factor"))
        .get_column("capacity_factor")
    )


__all__ = [
    "PECD_PREFIX_OVERRIDES",
    "NUTS_COUNTRY_REMAP",
    "KNOWN_PECD_SZON",
    "pecd_country_prefixes",
    "select_zone_columns",
    "sum_zone_energy",
    "capacity_weighted_cf",
]
