"""Processing and analysis utilities for DemandForge.

Electricity submodules include:
- population_weighted_temperature: compute country-level population-weighted T
- thermosensitivity: per-hour thermosensitivity regression and reporting
- thermosensitive_share: baseload vs winter/summer thermo decomposition and report

Hydrogen sector process modules — intermediate transformations.

This package provides functions for transforming hydrogen sector data
before it is consumed by the projection layer (``load_projection/hydrogen.py``).
Each module encapsulates one sector's physical model:

- **refinery**: CONCAWE unit-feed allocation, H₂ specific consumption, and
  the petrochem naphtha boundary correction to avoid double-counting with
  the olefins module.
- **esaf**: Fossil jet fuel reconstruction from CONCAWE refinery data.
- **steel**: DRI route splitting, EAF/primary decomposition, BF fuel mix,
  and H₂ demand from DRI-H₂ production.
- **olefins**: Four-pathway decarbonization mix (MTO, bio-naphtha, chemical
  recycling, fossil) with per-pathway H₂ intensities and independent ramp
  timings.

Public API -- Refinery:
    apply_capacity_delay()              -- Shift CONCAWE trajectories forward
    build_refinery_unit_allocation()    -- CONCAWE unit-feed allocation
    apply_petrochem_naphtha_correction() -- Remove cracker-bound naphtha HT
                                           from refinery H2 accounting
    get_naphtha_for_crackers()          -- Naphtha supply for olefins coupling

Public API — eSAF:
    compute_jet_fossil()               — Fossil jet reconstruction for eSAF

Public API — Steel:
    build_dri_mix()                    — DRI CH4/H2 fuel mix
    build_bf_fuel_mix()                — Blast-furnace fuel mix + CCS
    compute_eaf_scrap_series()         — EAF scrap trajectory
    split_primary_from_eaf()           — EAF/primary decomposition
    compute_dri_bf_split()             — DRI/BF-BOF split with 2019 floor
    compute_steel_h2_demand()          — H2 demand from DRI-H2 production
    build_steel_base_table()           — JRC-IDEES + TYNDP → route table
    validate_steel_base_table()        — Schema and mass-balance checks

Public API — Olefins:
    build_olefins_pathway_mix()        — Annual pathway share trajectories
    apply_naphtha_supply_constraint()  — Cap fossil share vs refinery naphtha
    compute_olefins_h2_demand()        — Per-pathway H₂ demand from production

Utilities:
    linear_ramp()                      — Vectorized linear ramp (shared)
"""
from demandforge.process._utils import linear_ramp
from demandforge.process.refinery import (
    build_refinery_unit_allocation,
    CONCAWE_MORE_MOLECULE,
    CONCAWE_MAX_ELECTRON,
)
from demandforge.process.esaf import (
    compute_jet_fossil,
    DEFAULT_JET_UNIT_NAMES,
    DEFAULT_JET_YIELDS,
)
from demandforge.process.steel import (
    build_dri_mix,
    build_bf_fuel_mix,
    compute_eaf_scrap_series,
    split_primary_from_eaf,
    compute_dri_bf_split,
    compute_steel_h2_demand,
    build_steel_base_table,
    validate_steel_base_table,
)
from demandforge.process.olefins import (
    apply_naphtha_supply_constraint,
    build_olefins_pathway_mix,
    compute_olefins_h2_demand,
    OLEFIN_PATHWAYS,
)
from demandforge.process.refinery import (
    apply_capacity_delay,
    apply_petrochem_naphtha_correction,
    get_naphtha_for_crackers,
)
from demandforge.process.co2_feedstock import (
    CO2_STOICHIOMETRY_T_PER_T_H2,
    DEFAULT_ESAF_CO2_ROUTE_SHARES,
    attach_co2_to_bundle,
    compute_co2_demand_for_sector,
    compute_esaf_co2_demand,
    compute_maritime_co2_demand,
    compute_olefins_co2_demand,
)

# Backward-compatible alias for internal use
_linear_ramp = linear_ramp

__all__ = [
    "linear_ramp",
    "_linear_ramp",
    "build_refinery_unit_allocation",
    "apply_capacity_delay",
    "apply_petrochem_naphtha_correction",
    "CONCAWE_MORE_MOLECULE",
    "CONCAWE_MAX_ELECTRON",
    "compute_jet_fossil",
    "DEFAULT_JET_UNIT_NAMES",
    "DEFAULT_JET_YIELDS",
    "build_dri_mix",
    "build_bf_fuel_mix",
    "compute_eaf_scrap_series",
    "split_primary_from_eaf",
    "compute_dri_bf_split",
    "compute_steel_h2_demand",
    "build_steel_base_table",
    "validate_steel_base_table",
    "apply_naphtha_supply_constraint",
    "build_olefins_pathway_mix",
    "compute_olefins_h2_demand",
    "get_naphtha_for_crackers",
    "OLEFIN_PATHWAYS",
    # CO2 feedstock accounting
    "CO2_STOICHIOMETRY_T_PER_T_H2",
    "DEFAULT_ESAF_CO2_ROUTE_SHARES",
    "attach_co2_to_bundle",
    "compute_co2_demand_for_sector",
    "compute_esaf_co2_demand",
    "compute_maritime_co2_demand",
    "compute_olefins_co2_demand",
]
