"""Shared physical constants for hydrogen demand projection.

All constants are dimensioned and documented.  They are used across multiple
sector projection functions in ``hydrogen.py``.

Convention: variable names carry unit suffixes to prevent unit errors.
"""

# ---------------------------------------------------------------------------
# Hydrogen
# ---------------------------------------------------------------------------

# Hydrogen lower heating value
H2_LHV_MWH_PER_T: float = 33.33  # MWh/t  (33,330 kWh/t)

# ---------------------------------------------------------------------------
# Ammonia (Haber-Bosch)
# ---------------------------------------------------------------------------

# Stoichiometric H2 requirement per tonne of NH3
# N2 + 3 H2 -> 2 NH3 => 3×2.016 / (2×17.031) ≈ 0.178
# Industrial practice including losses: ~0.18 t H2 / t NH3
H2_T_PER_T_NH3: float = 0.18

# Haber-Bosch process electricity demand per tonne of NH3 produced
HB_ELECTRICITY_MWH_PER_T_NH3: float = 0.98522

# Steam-methane reforming (SMR) stoichiometry
# CH4 + 2 H2O -> CO2 + 4 H2 => stoich ratio ~2.0 t CH4 / t H2
SMR_STOICH_CH4_T_PER_T_H2: float = 2.0

# SMR overall efficiency (thermal + process)
SMR_EFFICIENCY: float = 0.76

# Derived: actual CH4 demand per tonne of H2 via SMR
CH4_T_PER_T_H2_SMR: float = SMR_STOICH_CH4_T_PER_T_H2 / SMR_EFFICIENCY

# ---------------------------------------------------------------------------
# E-methanol (Power-to-Liquid)
# ---------------------------------------------------------------------------

# CO2 + 3 H2 -> CH3OH + H2O
# Stoichiometric: 3×2.016 / 32.04 ≈ 0.189 -> rounded to 0.19
H2_T_PER_T_E_METHANOL: float = 0.19

# ---------------------------------------------------------------------------
# Ammonia as fuel (Haber-Bosch for fuel-grade NH3)
# ---------------------------------------------------------------------------

# Same reaction as above but for fuel-grade ammonia.
# Industrial convention: 0.178 t H2 / t NH3 (closer to stoichiometric).
H2_T_PER_T_NH3_FUEL: float = 0.178

# ---------------------------------------------------------------------------
# eSAF (electro-Sustainable Aviation Fuel)
# ---------------------------------------------------------------------------

# H2 intensity of the Power-to-Liquid eSAF route
# -----------------------------------------------------------------
# Two distinct technological pathways are modelled.  The stoichiometric
# FLOOR is identical for both (same net C/H/O balance through jet-cut
# paraffin C12H26):
#
#     12 CO2 + 37 H2  →  C12H26 + 24 H2O
#
#     floor = (37 × M_H2) / M_C12H26
#           = (37 × 2.016) / 170.33
#           = 0.4380 t H2 / t SAF   (hard lower bound, 100 % selectivity)
#
# Process losses determine the practical intensity:
#
#   • Fischer-Tropsch (FT) — direct RWGS + FT chain growth.
#     Single-reactor process with modest H2 overstoichiometry
#     to push FT selectivity.  Typical H2 slip + oligomer
#     hydrogenation makeup: ~5 %.
#         0.4380 × (1 + 0.05) ≈ 0.46 t H2 / t SAF
#
#   • Methanol-to-Jet (MtJ) — CO2 + H2 → MeOH → olefins → jet via
#     oligomerisation + hydrogenation.  Three cascaded conversion
#     steps, each with finite selectivity:
#         MeOH synthesis   ~95 % C-selectivity
#         MTO (SAPO-34)    ~85 % C-selectivity to C2=/C3=
#         oligo + HT       ~95 % effective
#         combined C-yield ~0.77, so H2 input scales up by 1 / 0.77
#         0.4380 / 0.77 ≈ 0.57 t H2 / t SAF
#     Literature ranges (0.53–0.60 t H2/t SAF) bracket this value.
#
# The historical single blended value (0.50) corresponds to a roughly
# 60 %/40 % FT/MtJ mix (0.6 × 0.46 + 0.4 × 0.57 = 0.504) and is
# retained for backward compatibility with legacy callers.
H2_INTENSITY_T_PER_T_ESAF: float = 0.50

# Pathway-specific H2 intensities — used when a scenario supplies a
# pathway_shares mix and the bundle projects per-pathway H2 separately.
# Both values are strictly above the 0.438 t H2/t SAF stoichiometric
# floor; see derivation block above.
H2_INTENSITY_T_PER_T_ESAF_BY_PATHWAY: dict[str, float] = {
    "fischer_tropsch": 0.46,  # floor 0.438 × 1.05 (process losses)
    "methanol_to_jet": 0.57,  # floor 0.438 / 0.77 (cascade C-yield)
}

# ---------------------------------------------------------------------------
# Olefins
# ---------------------------------------------------------------------------

# Route 1: MTO (e-methanol → olefins via SAPO-34/ZSM-5 catalyst)
# Chain: CO₂ + 3 H₂ → CH₃OH + H₂O, then CH₃OH → olefins (MTO)
# H₂ per tonne methanol: 0.19 t (stoich + losses, see H2_T_PER_T_E_METHANOL)
# MTO mass yield: ~0.45 t olefin / t methanol (UOP/Honeywell, Lurgi MTP)
# H₂ intensity: 0.19 / 0.45 ≈ 0.422 t H₂ / t olefin
H2_T_PER_T_OLEFIN_MTO: float = 0.42

# Route 2: Bio-naphtha (lipid HVO hydroprocessing → steam cracker)
# H₂ consumption in HVO: ~3.5 wt% on lipid feed (Neste, TotalEnergies data)
# Total liquid yield: ~85%; mass-proportional H₂ allocation to naphtha:
#   0.035 / 0.85 = 0.0412 t H₂ / t bio-naphtha
# Steam cracker yield: ~45% light olefins (C2= + C3=)
# H₂ intensity: 0.0412 / 0.45 ≈ 0.092 t H₂ / t olefin
H2_T_PER_T_OLEFIN_BIO_NAPHTHA: float = 0.09

# Route 3: Chemical recycling (plastic waste pyrolysis oil → hydrotreat → crack)
# H₂ consumption: ~6 wt% on pyrolysis oil (higher than HVO due to
#   aromatics, heteroatoms, oxygen; source: Plastic Energy, BASF ChemCycling)
# Naphtha-range yield after hydrotreating: ~60%
# H₂ per tonne cracker-grade naphtha: 0.06 / 0.60 = 0.100
# Steam cracker yield: ~45%
# H₂ intensity: 0.100 / 0.45 ≈ 0.222 t H₂ / t olefin
H2_T_PER_T_OLEFIN_CHEM_RECYCL: float = 0.22

# Steam cracker light-olefin yield (ethylene + propylene) from naphtha feed
# Source: IEA, SRI Consulting, typical EU cracker performance
# Used to convert available naphtha feedstock (Mt/yr) to max olefin output.
STEAM_CRACKER_OLEFIN_YIELD: float = 0.45  # t olefin / t naphtha

# Route 4: Fossil naphtha cracking — upstream hydrotreating only
# Steam cracking itself consumes no H₂ (H₂ is a byproduct, ~1-2 wt%).
# But upstream naphtha hydrotreating uses H₂ at 0.12 wt% (CONCAWE data).
# With 14% inefficiency and 45% crack yield:
#   0.0012 × 1.14 / 0.45 ≈ 0.003 t H₂ / t olefin
H2_T_PER_T_OLEFIN_FOSSIL_HT: float = 0.003

# ---------------------------------------------------------------------------
# Refinery
# ---------------------------------------------------------------------------

# H2 overconsumption factor on top of CONCAWE specific consumption
REFINERY_INEFFICIENCY_SHARE: float = 0.14

# Fraction of CONCAWE "Naphtha Hydrotreater" throughput that serves
# petrochemical steam crackers (olefins production) rather than catalytic
# reformers (gasoline).  In the EU, ~60% of hydrotreated naphtha goes to
# crackers (source: Eurostat, CEFIC Facts & Figures).
# This fraction is subtracted from the CONCAWE naphtha HT capacity to
# avoid double-counting with the olefins module, which carries its own
# H₂ accounting for fossil naphtha hydrotreating (H2_T_PER_T_OLEFIN_FOSSIL_HT).
PETROCHEM_NAPHTHA_FRACTION: float = 0.60
