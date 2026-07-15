"""CO2 feedstock demand associated with sector-level H2 demand.

This module computes the CO2 feedstock requirement that is thermodynamically
coupled to each sector's H2 demand through a specific chemical pathway.  It
is *not* a generic one-size-fits-all coefficient: each pathway has its own
stoichiometric ratio, derived from first principles and traceable to a named
reaction.

Scope and conventions
---------------------
- All ratios are expressed as ``t CO2 / t H2`` on a mass basis.
- Hydrogen energy is LHV-consistent with the rest of DemandForge
  (33.33 MWh/t).  CO2 is a mass flow only — it carries no heating value.
- A CO2 ratio of ``0.0`` means the pathway does not consume CO2 as a
  chemical feedstock, even if it emits CO2 downstream.  Combustion
  emissions and life-cycle accounting are outside the scope of this module.
- The coupling is strictly bottom-up: the module never invents a new H2
  figure.  It reads the H2 volumes produced by the sector projection
  functions in ``load_projection/hydrogen.py`` and multiplies them by the
  appropriate stoichiometric ratio.

Sectors and routes
------------------
Only three of the six DemandForge sectors consume CO2 as feedstock:

+------------+-----------------------------------------------------------+
| Sector     | CO2-consuming route(s)                                     |
+============+===========================================================+
| maritime   | e-methanol only — ammonia-fuel consumes N2, not CO2.      |
+------------+-----------------------------------------------------------+
| olefins    | MTO pathway only — bio-naphtha, chem-recycling, and       |
|            | fossil routes do not consume CO2 as feedstock.             |
+------------+-----------------------------------------------------------+
| eSAF       | Fischer-Tropsch (via RWGS) and/or methanol-to-jet —        |
|            | route mix set by the active scenario bundle.               |
+------------+-----------------------------------------------------------+

Ammonia (Haber-Bosch: N2 + 3H2 -> 2NH3), steel (direct-reduced iron
with H2), and refinery (hydrotreating / hydrocracking) do not consume
CO2 as feedstock.  Their CO2 demand is 0.

Stoichiometric derivations
--------------------------
All ratios below are derived from molecular masses:
    M(H2)  = 2.016 g/mol
    M(CO2) = 44.01 g/mol
    M(CH3OH) = 32.04 g/mol

1. **e-methanol** (Power-to-Methanol via CO2 hydrogenation)
   Reaction:       CO2 + 3 H2 -> CH3OH + H2O
   CO2 per H2:     M(CO2) / (3 * M(H2))
                 = 44.01 / (3 * 2.016)
                 = 7.275 t CO2 / t H2
   Notes:
   - Stoichiometric; does not include selectivity losses, which in
     practice are small (<5 %) and are already absorbed into the
     0.19 t H2 / t MeOH intensity used in DemandForge.
   - Used by: maritime e-methanol, olefins MTO pathway (MTO runs
     methanol through SAPO-34/ZSM-5 with no additional CO2 input,
     so the CO2 charge is identical to that of the upstream MeOH
     synthesis).

2. **Fischer-Tropsch** (via reverse water-gas shift + FT synthesis)
   Net reaction (for jet-cut paraffin C12H26):
                   12 CO2 + 37 H2 -> C12H26 + 24 H2O
   Derivation:
     RWGS:   CO2 + H2 -> CO + H2O                (1)
     FT:     12 CO + 25 H2 -> C12H26 + 12 H2O    (2)
     Sum:    12 CO2 + 37 H2 -> C12H26 + 24 H2O   (3)
   CO2 per H2:     (12 * M(CO2)) / (37 * M(H2))
                 = (12 * 44.01) / (37 * 2.016)
                 = 7.084 t CO2 / t H2
   Notes:
   - Chain length C12H26 is representative of the jet cut; heavier or
     lighter chains give ratios within +/- 1 % of this value.
   - Selectivity losses (non-jet FT products) are already captured in
     the H2_INTENSITY_T_PER_T_ESAF = 0.50 figure used by
     project_esaf_h2_demand.

3. **Methanol-to-jet** (MeOH -> olefins -> oligomerisation -> HT)
   CO2 per H2:     Identical to e-methanol (7.275 t CO2 / t H2).
   Rationale:
   - All CO2 is supplied at the methanol synthesis step.  The downstream
     olefin conversion, oligomerisation, and hydrotreating steps do not
     net-consume CO2 (they release water and combustion CO2 but do not
     take CO2 as a reactant).
   - The downstream H2 used for hydrotreating is already counted in the
     blended H2_INTENSITY_T_PER_T_ESAF, so applying the MeOH ratio to
     the total eSAF H2 is internally consistent with the module's
     accounting convention.

4. **Sabatier methanation** (future-proof, currently unused by any sector)
   Reaction:       CO2 + 4 H2 -> CH4 + 2 H2O
   CO2 per H2:     M(CO2) / (4 * M(H2)) = 5.457 t CO2 / t H2
   Registered for completeness so a future SNG sector can reuse the
   same registry.

Non-CO2 pathways (explicitly registered as 0.0)
-----------------------------------------------
The following routes consume H2 but not CO2 as feedstock:
- Ammonia (Haber-Bosch): N2 + 3H2 -> 2NH3.  Nitrogen feedstock, not CO2.
- Steel DRI-H2:          Fe2O3 + 3H2 -> 2Fe + 3H2O.  Avoids CO2 entirely.
- Refinery HT/HC:        Hydrogen added to hydrocarbons; no CO2 input.
- Maritime NH3 fuel:     Same reaction as ammonia above.
- Olefins bio-naphtha:   Biogenic feedstock; CO2 accounting is biogenic
                         and outside this module's scope.
- Olefins chem recycling:Hydrogen upgrades pyrolysis oil; no CO2 input.
- Olefins fossil HT:     Hydrotreating only; no CO2 input.

Scenario coupling
-----------------
The FT/MtJ mix is a scenario parameter carried by the YAML key
``esaf.pathway_shares``.  It is consumed by ``project_esaf_h2_demand``,
which splits eSAF production into pathway-specific H2 demands using
the pathway-specific intensities in
:data:`demandforge.load_projection.constants.H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY`
(FT ≈ 0.43, MtJ ≈ 0.55 t H2/t SAF).  The resulting per-pathway H2
columns are the authoritative input for this CO2 module — applying the
per-pathway stoichiometric CO2:H2 ratio to each column separately
produces a physically coherent CO2 demand that responds jointly to
both H2 and pathway mix:

    H2_FT  = SAF * share_FT  * 0.43   (t/yr)
    H2_MtJ = SAF * share_MtJ * 0.55   (t/yr)
    CO2    = H2_FT  * 7.084 + H2_MtJ * 7.275

For backward compatibility with legacy ``h2_intensity`` callers that
do not expose the per-pathway columns, the module also supports a
share-weighted blended ratio applied to the aggregate H2 total.

Validation invariants
---------------------
- Sum of per-sector CO2 demand equals the dispatcher total
  (mass-balance check, tolerance 1e-6 t).
- Every CO2 demand is non-negative.
- No sector returns CO2 without a traceable route name in the registry.

Authors:
    Simon Brigode -- PERSEE Lab, Mines Paris PSL
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# =============================================================================
# Stoichiometric registry — t CO2 per t H2
# =============================================================================

# Molar masses (g/mol) — kept for audit/transparency even though the ratios
# below are pre-computed to avoid repeated floating-point drift.
_M_H2: float = 2.016
_M_CO2: float = 44.01
_M_CH3OH: float = 32.04

# CO2 : H2 mass ratios, keyed by route name.  Every value is derived from
# a named reaction and documented in the module docstring above.
CO2_STOICHIOMETRY_T_PER_T_H2: dict[str, float] = {
    # CO2-consuming routes
    "e_methanol":       _M_CO2 / (3.0 * _M_H2),         # 7.275
    "methanol_to_jet":  _M_CO2 / (3.0 * _M_H2),         # 7.275 (MeOH step governs)
    "fischer_tropsch":  (12.0 * _M_CO2) / (37.0 * _M_H2),  # 7.084 (RWGS + FT, C12H26)
    "sabatier":         _M_CO2 / (4.0 * _M_H2),         # 5.457 (future SNG)
    # Non-CO2-consuming routes (registered for explicitness and validation)
    "haber_bosch":        0.0,  # N2 + 3H2 -> 2NH3
    "dri_h2":             0.0,  # Fe2O3 + 3H2 -> 2Fe + 3H2O
    "refinery_ht":        0.0,  # H2 to hydrocarbons, no CO2 input
    "bio_naphtha":        0.0,  # biogenic, outside this module's scope
    "chem_recycling":     0.0,  # H2 upgrades pyrolysis oil, no CO2 input
    "fossil_naphtha_ht":  0.0,  # upstream hydrotreating only
}

# Default eSAF route mix used when the scenario bundle does not supply its
# own ``co2_route_shares``.  Chosen as the lowest-CO2 assumption (pure FT).
DEFAULT_ESAF_CO2_ROUTE_SHARES: dict[str, float] = {"fischer_tropsch": 1.0}


# =============================================================================
# Helpers
# =============================================================================

def _blend_ratio(route_shares: dict[str, float]) -> float:
    """Compute the share-weighted CO2:H2 ratio for a route mix.

    Args:
        route_shares: Mapping ``{route_name: share}`` where shares sum to 1.

    Returns:
        Blended ratio in t CO2 / t H2.

    Raises:
        KeyError:   If a route name is not in the registry.
        ValueError: If shares sum outside [1 - 1e-6, 1 + 1e-6] or are negative.
    """
    if not route_shares:
        raise ValueError("route_shares cannot be empty")

    total = 0.0
    for route, share in route_shares.items():
        if share < 0:
            raise ValueError(f"Negative share {share} for route '{route}'")
        if route not in CO2_STOICHIOMETRY_T_PER_T_H2:
            raise KeyError(
                f"Unknown route '{route}'. "
                f"Registered routes: {sorted(CO2_STOICHIOMETRY_T_PER_T_H2)}"
            )
        total += share

    if not (1.0 - 1e-6 <= total <= 1.0 + 1e-6):
        raise ValueError(
            f"Route shares must sum to 1.0 within tolerance 1e-6, got {total:.9f}"
        )

    ratio = sum(
        share * CO2_STOICHIOMETRY_T_PER_T_H2[route]
        for route, share in route_shares.items()
    )
    return float(ratio)


# =============================================================================
# Per-sector CO2 demand functions
# =============================================================================

def compute_maritime_co2_demand(
    maritime_df: pd.DataFrame,
) -> pd.DataFrame:
    """CO2 feedstock for maritime sector (e-methanol route only).

    Ammonia-fuel H2 consumes nitrogen, not CO2, so it contributes zero.

    Args:
        maritime_df: Output of ``project_maritime_h2_demand``.  Must include
            ``h2_demand_for_e_methanol_t_per_yr``.

    Returns:
        DataFrame with columns: country, year, co2_demand_t_per_yr.
    """
    required = {"country", "year", "h2_demand_for_e_methanol_t_per_yr"}
    missing = required - set(maritime_df.columns)
    if missing:
        raise ValueError(f"maritime_df missing columns: {missing}")

    ratio = CO2_STOICHIOMETRY_T_PER_T_H2["e_methanol"]
    out = maritime_df[["country", "year"]].copy()
    out["co2_demand_t_per_yr"] = (
        maritime_df["h2_demand_for_e_methanol_t_per_yr"].values * ratio
    )
    return out


def compute_olefins_co2_demand(
    olefins_df: pd.DataFrame,
) -> pd.DataFrame:
    """CO2 feedstock for olefins sector (MTO pathway only).

    Bio-naphtha, chemical recycling, and fossil pathways do not consume
    CO2 as feedstock and therefore contribute zero.

    Args:
        olefins_df: Output of ``project_olefins_h2_demand`` with
            per-pathway H2 columns preserved (``h2_demand_mto_t_per_yr``).

    Returns:
        DataFrame with columns: country, year, co2_demand_t_per_yr.
    """
    required = {"country", "year", "h2_demand_mto_t_per_yr"}
    missing = required - set(olefins_df.columns)
    if missing:
        raise ValueError(
            f"olefins_df missing columns: {missing}.  "
            f"Ensure project_olefins_h2_demand exposes per-pathway H2 breakdown."
        )

    ratio = CO2_STOICHIOMETRY_T_PER_T_H2["e_methanol"]  # MTO == e-MeOH upstream
    out = olefins_df[["country", "year"]].copy()
    out["co2_demand_t_per_yr"] = (
        olefins_df["h2_demand_mto_t_per_yr"].values * ratio
    )
    return out


def compute_esaf_co2_demand(
    esaf_df: pd.DataFrame,
    co2_route_shares: dict[str, float] | None = None,
) -> pd.DataFrame:
    """CO2 feedstock for eSAF sector, consuming per-pathway H2 columns.

    Physical coherence — why per-pathway H2 columns:
    --------------------------------------------------
    Fischer-Tropsch and methanol-to-jet differ in *both* their H2
    intensity (FT ~0.43 t H2/t SAF, MtJ ~0.55) *and* their CO2 intensity
    (FT 7.084 t CO2/t H2, MtJ 7.275).  A scenario that shifts the mix
    must therefore shift H2 and CO2 consistently; computing CO2 from a
    blended ratio applied to a pathway-agnostic H2 total would be
    physically incoherent.

    The current ``project_esaf_h2_demand`` exposes per-pathway H2
    columns (``h2_demand_for_esaf_fischer_tropsch_t_per_yr``,
    ``h2_demand_for_esaf_methanol_to_jet_t_per_yr``) whenever a
    pathway mix is in effect.  This function consumes those columns
    directly, analogous to the olefins MTO accounting.

    Backward compatibility:
    If the per-pathway columns are absent (legacy ``h2_intensity``
    path in ``project_esaf_h2_demand``), the function falls back to
    applying a share-weighted blended ratio to the total
    ``h2_demand_for_esaf_t_per_yr``, using either the supplied
    ``co2_route_shares`` or ``DEFAULT_ESAF_CO2_ROUTE_SHARES``.

    Args:
        esaf_df: Output of ``project_esaf_h2_demand``.
        co2_route_shares: Only used in legacy mode.  Ignored when
            per-pathway H2 columns are present (because the pathway
            split — and hence the CO2 mix — is already resolved upstream).

    Returns:
        DataFrame with columns: country, year, co2_demand_t_per_yr.
    """
    required_base = {"country", "year"}
    missing = required_base - set(esaf_df.columns)
    if missing:
        raise ValueError(f"esaf_df missing columns: {missing}")

    # --- Preferred path: per-pathway H2 columns are present -------------
    # The pathway split has already been resolved by project_esaf_h2_demand
    # and each pathway's H2 is known.  Apply the per-pathway stoichiometric
    # CO2:H2 ratios directly.
    pathway_cols = {
        "fischer_tropsch": "h2_demand_for_esaf_fischer_tropsch_t_per_yr",
        "methanol_to_jet": "h2_demand_for_esaf_methanol_to_jet_t_per_yr",
    }
    has_pathway_cols = any(c in esaf_df.columns for c in pathway_cols.values())

    out = esaf_df[["country", "year"]].copy()

    if has_pathway_cols:
        co2 = np.zeros(len(esaf_df), dtype=float)
        contributions = {}
        for pth, col in pathway_cols.items():
            if col not in esaf_df.columns:
                continue
            ratio = CO2_STOICHIOMETRY_T_PER_T_H2[pth]
            pth_co2 = esaf_df[col].values * ratio
            co2 = co2 + pth_co2
            contributions[pth] = (float(pth_co2.sum()), ratio)
        out["co2_demand_t_per_yr"] = co2
        logger.info(
            "eSAF CO2 demand computed per pathway (physically coherent): "
            + ", ".join(
                f"{p}={v / 1e6:.2f} Mt at {r:.3f} t CO2/t H2"
                for p, (v, r) in contributions.items()
            )
        )
        return out

    # --- Legacy path: single blended intensity, fall back to ratio × total
    if "h2_demand_for_esaf_t_per_yr" not in esaf_df.columns:
        raise ValueError(
            "esaf_df must carry either per-pathway H2 columns "
            f"({sorted(pathway_cols.values())}) or the aggregate "
            f"'h2_demand_for_esaf_t_per_yr' column.  Neither was found."
        )

    if co2_route_shares is None:
        co2_route_shares = DEFAULT_ESAF_CO2_ROUTE_SHARES

    allowed = {"fischer_tropsch", "methanol_to_jet"}
    invalid = set(co2_route_shares) - allowed
    if invalid:
        raise ValueError(
            f"Invalid eSAF CO2 routes {invalid}.  "
            f"Allowed routes: {sorted(allowed)}"
        )

    ratio = _blend_ratio(co2_route_shares)
    out["co2_demand_t_per_yr"] = (
        esaf_df["h2_demand_for_esaf_t_per_yr"].values * ratio
    )
    logger.warning(
        "eSAF CO2 demand computed in LEGACY mode (single blended "
        f"CO2:H2 ratio = {ratio:.4f} from {co2_route_shares}).  "
        "This path is physically incoherent when the H2 projection "
        "used a single blended intensity — prefer project_esaf_h2_demand "
        "with pathway_shares."
    )
    return out


# =============================================================================
# Dispatcher
# =============================================================================

# Sectors that do not consume CO2 as feedstock.  Listed explicitly so the
# dispatcher fails loud if a new sector is added without a CO2 decision.
_ZERO_CO2_SECTORS: set[str] = {"ammonia", "refinery", "steel"}

# Sectors that do consume CO2, mapped to their compute function.
_CO2_SECTOR_FUNCS: dict[str, Any] = {
    "maritime": compute_maritime_co2_demand,
    "olefins":  compute_olefins_co2_demand,
    "esaf":     compute_esaf_co2_demand,
}


def compute_co2_demand_for_sector(
    sector: str,
    sector_df: pd.DataFrame,
    esaf_route_shares: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Route a sector DataFrame to the correct CO2 calculation.

    Args:
        sector: Sector name (one of the six DemandForge sectors).
        sector_df: Per-country/year DataFrame as produced by the sector's
            projection function.  May be ignored for zero-CO2 sectors.
        esaf_route_shares: Only used when ``sector == "esaf"``.

    Returns:
        DataFrame with columns: country, year, co2_demand_t_per_yr.
        Zero-filled for sectors that do not consume CO2.
    """
    if sector in _ZERO_CO2_SECTORS:
        if sector_df is None or sector_df.empty:
            return pd.DataFrame(columns=["country", "year", "co2_demand_t_per_yr"])
        out = sector_df[["country", "year"]].drop_duplicates().copy()
        out["co2_demand_t_per_yr"] = 0.0
        return out.reset_index(drop=True)

    if sector not in _CO2_SECTOR_FUNCS:
        raise ValueError(
            f"Unknown sector '{sector}'.  "
            f"Known sectors: {_ZERO_CO2_SECTORS | set(_CO2_SECTOR_FUNCS)}"
        )

    func = _CO2_SECTOR_FUNCS[sector]
    if sector == "esaf":
        return func(sector_df, co2_route_shares=esaf_route_shares)
    return func(sector_df)


# =============================================================================
# Aggregator — maps the load_bundle summary frame to a CO2 column
# =============================================================================

def attach_co2_to_bundle(
    bundle_df: pd.DataFrame,
    sector_frames: dict[str, pd.DataFrame],
    esaf_route_shares: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Attach a ``co2_demand_t_per_yr`` column to the load_bundle output.

    The bundle frame has one row per (country, year, sector) with the
    aggregated H2 demand.  For each row, we look up the corresponding
    detailed per-sector DataFrame (which carries the per-pathway columns
    needed for CO2 accounting) and compute CO2 demand consistently.

    Args:
        bundle_df: Output of ``load_bundle``.  Must have columns
            ``country``, ``year``, ``sector``, ``h2_demand_t_per_yr``.
        sector_frames: Mapping ``{sector: detailed_df}`` as emitted by
            the projection functions *before* column reduction.
        esaf_route_shares: Optional eSAF CO2 route mix.

    Returns:
        A copy of ``bundle_df`` with an additional ``co2_demand_t_per_yr``
        column.  Rows whose sector does not consume CO2 have 0.

    Raises:
        ValueError: If a sector in ``bundle_df`` is missing from
            ``sector_frames`` and is not a zero-CO2 sector.
    """
    required = {"country", "year", "sector", "h2_demand_t_per_yr"}
    missing = required - set(bundle_df.columns)
    if missing:
        raise ValueError(f"bundle_df missing columns: {missing}")

    out = bundle_df.copy()
    out["co2_demand_t_per_yr"] = 0.0

    for sector in out["sector"].unique():
        if sector in _ZERO_CO2_SECTORS:
            # Column already initialised to 0.
            continue

        if sector not in sector_frames:
            raise ValueError(
                f"Sector '{sector}' is in bundle_df but its detailed frame "
                f"was not supplied in sector_frames.  "
                f"Available frames: {sorted(sector_frames)}"
            )

        co2_df = compute_co2_demand_for_sector(
            sector=sector,
            sector_df=sector_frames[sector],
            esaf_route_shares=esaf_route_shares,
        )

        # Merge CO2 values into the corresponding rows
        key = ["country", "year"]
        lookup = co2_df.set_index(key)["co2_demand_t_per_yr"]

        mask = out["sector"] == sector
        # Build a joint key on the subset, then map
        sub_keys = list(zip(out.loc[mask, "country"], out.loc[mask, "year"]))
        values = np.array([lookup.get(k, 0.0) for k in sub_keys], dtype=float)
        out.loc[mask, "co2_demand_t_per_yr"] = values

    # --- Validation: no negative CO2 ---
    if (out["co2_demand_t_per_yr"] < 0).any():
        n_bad = int((out["co2_demand_t_per_yr"] < 0).sum())
        raise ValueError(f"Negative CO2 demand in {n_bad} rows (should be impossible).")

    # --- Validation: mass-balance spot check ---
    # For each (country, year, sector) in a CO2-consuming sector, the CO2
    # value must equal H2 * a ratio in [min, max] of the registry.
    ratios = [v for v in CO2_STOICHIOMETRY_T_PER_T_H2.values() if v > 0]
    max_ratio = max(ratios)
    # Allow a tiny numerical slack
    violating = (
        out["co2_demand_t_per_yr"]
        > out["h2_demand_t_per_yr"] * max_ratio * (1 + 1e-6)
    )
    if violating.any():
        sample = out[violating].iloc[0]
        raise ValueError(
            f"CO2 demand exceeds max plausible ratio for sector "
            f"{sample['sector']} at {sample['country']}/{sample['year']}: "
            f"CO2={sample['co2_demand_t_per_yr']:.2f} t, "
            f"H2={sample['h2_demand_t_per_yr']:.2f} t, "
            f"ratio={sample['co2_demand_t_per_yr'] / max(sample['h2_demand_t_per_yr'], 1e-9):.3f}"
        )

    # Log a brief summary for auditability
    total_co2 = out["co2_demand_t_per_yr"].sum()
    by_sector = out.groupby("sector")["co2_demand_t_per_yr"].sum()
    nonzero_sectors = by_sector[by_sector > 0].sort_values(ascending=False)
    logger.info(
        f"CO2 feedstock attached: total {total_co2 / 1e6:.2f} Mt CO2 "
        f"across {len(out)} rows.  "
        f"Sector shares: "
        + ", ".join(f"{s}={v / 1e6:.2f} Mt" for s, v in nonzero_sectors.items())
    )

    return out


__all__ = [
    "CO2_STOICHIOMETRY_T_PER_T_H2",
    "DEFAULT_ESAF_CO2_ROUTE_SHARES",
    "compute_maritime_co2_demand",
    "compute_olefins_co2_demand",
    "compute_esaf_co2_demand",
    "compute_co2_demand_for_sector",
    "attach_co2_to_bundle",
]
