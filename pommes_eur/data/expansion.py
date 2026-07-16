"""pommes_eur.data.expansion — scenario-gated capacity-expansion + BESS tables.

Battery-power scaling (`_battNNN`/DECARB), the investable-technology set (with nuclear
injection for `_nuke*`), and the per-country capacity-expansion headroom (with the
nuclear-reentry and `_nukeXXL` mutations). Derived from static `data.inputs` base tables +
the scenario flags in `scenario.resolved`. Re-exported through pommes_eur.constants.
Carved out of the old constants.py monolith — values byte-identical, guarded by tests/golden.
The mutation order (headroom literal -> reentry loop -> _nukeXXL) is load-bearing; preserved.
"""
from __future__ import annotations

import re as _re

from pommes_eur.scenario.resolved import _SCENARIO, _NUKE_XXL
from pommes_eur.scenario.parse import _is_nuclear_expandable
from pommes_eur.data.inputs import (
    _BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY_BASE,
    _DEFAULT_BESS_POWER_INVESTMENT_MAX_MW_BASE,
    _NUCLEAR_REENTRY_COUNTRIES,
)

# ─── DECARB-aware scale (Phase 2.6) ─────────────────────────────────────────
# Tier order checked: explicit _battNNN > h2HIGH (DECARB HIGH) > DECARB > 1.0×.
# Pure regex / substring tests on _SCENARIO so the section is self-contained
# (does not depend on _IS_DECARB_* flags computed later in this file).
_BATTERY_SCALE: float = 1.0
if bool(_re.search(r"_noGas_corr2x", _SCENARIO)):
    # Any DECARB run
    _BATTERY_SCALE = 1.5
if "h2HIGH" in _SCENARIO:
    # DECARB HIGH overrides upward — heat pumps + electrolysers add huge flex load
    _BATTERY_SCALE = 2.0
_m_batt = _re.search(r"_batt(\d+)(?:_|$)", _SCENARIO)
if _m_batt:
    _BATTERY_SCALE = float(_m_batt.group(1)) / 10.0
del _m_batt

BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY: dict[str, dict[str, float]] = {
    country: {tech: cap * _BATTERY_SCALE for tech, cap in techs.items()}
    for country, techs in _BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY_BASE.items()
}
DEFAULT_BESS_POWER_INVESTMENT_MAX_MW: dict[str, float] = {
    tech: cap * _BATTERY_SCALE
    for tech, cap in _DEFAULT_BESS_POWER_INVESTMENT_MAX_MW_BASE.items()
}


# ═══════════════════════════════════════════════════════════════════
# 9. CAPACITY EXPANSION / ADEQUACY PARAMETERS
# ═══════════════════════════════════════════════════════════════════
EXPANDABLE_MODEL_TECHS: set[str] = {
    "Gas",
    "Hydrogen_power_plant",
}

# Nuclear becomes investable for the nuke variants (policy_nuke, R0_v1_nuke).
# Costs come from EOLES via techno_costs_from_eoles("nuclear", eoles_costs)
# — no hand-written cost dict. Per-country investment caps live in
# EXPANSION_HEADROOM_BY_COUNTRY / DEFAULT_EXPANSION_HEADROOM_MW below.
if _is_nuclear_expandable(_SCENARIO):
    EXPANDABLE_MODEL_TECHS.add("Nuclear")

# Default per-technology headroom (MW) used ONLY when CLEVER declares
# non-zero energy for that technology in a given country.
# If CLEVER says 0 TWh for a fuel in a country, the technology is NOT created
# (see add_dispatchable_from_non_enr).
# Sources: ENTSO-E TYNDP 2024 Distributed Energy / Global Ambition scenarios;
#   Germany H2-ready study (Frontier Economics 2024): ~53 GW total backup.
#   Conservative cap: allow up to 2× the CLEVER-implied capacity.
DEFAULT_EXPANSION_HEADROOM_MW: dict[str, float] = {
    "Gas": 10_000.0,
    "Hydrogen_power_plant": 15_000.0,
    "Nuclear":               2_000.0,  # conservative for non-nuclear states (policy_nuke/R0_v1_nuke)
}

# Country-specific headroom overrides (MW).
# Only relevant when CLEVER declares non-zero energy for the technology.
# Nuclear caps are sourced from announced national plans (EPR2, PEP2040,
# Sizewell C, Dukovany II, Paks II, Tidö, etc.) — see
# tables/<scenario>/nuclear_headroom_by_country.csv for per-country sources.
EXPANSION_HEADROOM_BY_COUNTRY: dict[str, dict[str, float]] = {
    "DE": {"Gas": 10_000.0, "Hydrogen_power_plant": 40_000.0, "Nuclear":      0.0},  # phase-out, hard zero
    "FR": {"Gas": 10_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear": 20_000.0},  # EPR2 programme target
    "ES": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear":  3_000.0},  # life extension
    "GB": {"Gas": 10_000.0, "Hydrogen_power_plant": 15_000.0, "Nuclear": 15_000.0},  # Hinkley + Sizewell C
    "IT": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear":      0.0},  # 1990 referendum, 2011 reaffirmed
    "NL": {"Gas":  5_000.0, "Hydrogen_power_plant": 10_000.0, "Nuclear":  5_000.0},  # Coalition Plan 2× units
    "BE": {"Gas":  3_000.0, "Hydrogen_power_plant":  5_000.0, "Nuclear":  2_000.0},  # Doel/Tihange life extension
    "CH": {"Gas":  2_000.0, "Hydrogen_power_plant":  3_000.0},
    "AT": {"Gas":  2_000.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # constitutional ban (1999)
    # ── Hard-zero nuclear (Phase 2.9): countries with current bans or
    # constitutional restrictions. Without explicit "Nuclear": 0.0 these
    # silently fell through to DEFAULT_EXPANSION_HEADROOM_MW["Nuclear"]=2 GW.
    "PT": {"Gas":  1_500.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # no nuclear policy
    "IE": {"Gas":  1_500.0, "Hydrogen_power_plant":  2_000.0, "Nuclear":      0.0},  # Electricity Regulation Act 1999 ban
    "DK": {"Gas":  1_500.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # 1985 parliamentary resolution
    "LU": {"Gas":    500.0, "Hydrogen_power_plant":  1_000.0, "Nuclear":      0.0},  # no nuclear policy
    "GR": {"Gas":  2_500.0, "Hydrogen_power_plant":  3_000.0, "Nuclear":      0.0},  # no nuclear policy
    "PL": {"Nuclear": 10_000.0},   # PEP2040 nuclear programme
    "CZ": {"Nuclear":  5_000.0},   # Dukovany II + Temelin uprate
    "FI": {"Nuclear":  3_000.0},   # Olkiluoto + new
    "HU": {"Nuclear":  4_000.0},   # Paks II
    "SK": {"Nuclear":  3_000.0},   # Mochovce + new
    "BG": {"Nuclear":  3_000.0},   # Kozloduy + Belene
    "RO": {"Nuclear":  3_000.0},   # Cernavoda + SMR
    "SE": {"Nuclear":  6_000.0},   # Tidö nuclear ambition
}

for _cc in _NUCLEAR_REENTRY_COUNTRIES:
    _m_nuke = _re.search(rf"_{_cc}Nuke(\d+)(?:_|$)", _SCENARIO)
    if _m_nuke:
        EXPANSION_HEADROOM_BY_COUNTRY.setdefault(_cc.upper(), {})["Nuclear"] = float(_m_nuke.group(1))
del _cc  # avoid leaking loop variable into module namespace
try:
    del _m_nuke
except NameError:
    pass

# `_nukeXXL` (2026-06-16): moderate ~2x lift of nuclear headroom. Double every
# NON-zero per-country cap and the default; policy hard-zeros (DE/IT/AT/PT/IE/
# DK/LU/GR) stay 0. Only bites when nuclear is expandable ('_nuke' present).
if _NUKE_XXL:
    DEFAULT_EXPANSION_HEADROOM_MW["Nuclear"] = DEFAULT_EXPANSION_HEADROOM_MW["Nuclear"] * 2.0
    for _cc_n in list(EXPANSION_HEADROOM_BY_COUNTRY):
        _nuc_cap = EXPANSION_HEADROOM_BY_COUNTRY[_cc_n].get("Nuclear")
        if _nuc_cap:  # non-zero only — preserves hard-zero policy bans
            EXPANSION_HEADROOM_BY_COUNTRY[_cc_n]["Nuclear"] = _nuc_cap * 2.0
    del _cc_n, _nuc_cap


