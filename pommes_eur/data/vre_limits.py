"""pommes_eur.data.vre_limits — scenario-gated VRE expansion bands.

DECARB-run detection flags and the VRE upper-expansion / downside-decommission bands
(`_vreEXT`/`_vreXXL`/`_vreFree`/DECARB-aware), keyed off the scenario flags in
`scenario.resolved`. Re-exported through pommes_eur.constants. Carved out
of the old constants.py monolith — values byte-identical, guarded by tests/golden. No numerics changed.
"""
from __future__ import annotations

import re as _re

from pommes_eur.scenario.resolved import _SCENARIO, _VRE_FREE, _VRE_XXL, _VRE_EXTENDED
from pommes_eur.scenario.parse import _is_high_demand

# Scenario-gated default VRE upper band:
#   R0_v1 / R0_v1_nuke      → 0.15 (CLEVER sufficiency-aligned)
#   policy_re / policy_nuke → 0.30 (non-sufficiency counter-factual; raised from
#                                    +20% on 2026-05-19 — Simon's call, motivated by
#                                    article-readability: +20% gave ~190 GW marginal
#                                    headroom above the demand-implied ~685 GW
#                                    additional VRE need under the central H₂ bundle,
#                                    risking 5+ countries binding on the cap and
#                                    suppressing the "VRE needed without sufficiency"
#                                    headline. +30% removes this risk.)
# Detect DECARB-style scenarios (final-paper runs) by the `_noGas_corr2x` signature.
# These get more flexible VRE bands so the LP can right-size the fleet to demand:
#   downside  = −40 % for LOW (bioLow + no h2HIGH); −25 % for CENTRAL / HIGH
#   upside    = per-tech (25/20/60) for HIGH (_vreEXT); uniform +25 % for LOW & CENTRAL
_IS_DECARB_RUN: bool = bool(_re.search(r"_noGas_corr2x", _SCENARIO))
_IS_DECARB_LOW: bool = _IS_DECARB_RUN and "bioLow" in _SCENARIO and "h2HIGH" not in _SCENARIO

if _VRE_FREE:
    # `_vreFree` (2026-06-26): cap = CLEVER level exactly (no expansion margin).
    # Combined with the downside band = 1.0 below, the LP sites VRE in [0, CLEVER].
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.0,
        "Wind_Onshore":  0.0,
        "Wind_Offshore": 0.0,
    }
elif _VRE_XXL:
    # `_vreXXL` (2026-06-16): ~2x the `_vreEXT` band — moderate headroom lift.
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.50,
        "Wind_Onshore":  0.40,
        "Wind_Offshore": 1.20,
    }
elif _VRE_EXTENDED:
    # `_vreEXT` scenarios: per-tech default reflects JRC-ENSPRESO / national-pledge floors.
    # The big-country overrides below are blanked so they don't bind below these.
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.25,
        "Wind_Onshore":  0.20,
        "Wind_Offshore": 0.60,
    }
elif _IS_DECARB_RUN:
    # DECARB CENTRAL / LOW: uniform +25 % upside (lifted from default +15 %)
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         0.25,
        "Wind_Onshore":  0.25,
        "Wind_Offshore": 0.25,
    }
else:
    # Backward compat: a single scalar value, replicated per VRE tech so
    # consumers can always use a .get(tech, fallback) pattern.
    _vre_scalar = 0.30 if _is_high_demand(_SCENARIO) else 0.15
    DEFAULT_VRE_EXPANSION_HEADROOM_FRACTION: dict[str, float] = {
        "Solar":         _vre_scalar,
        "Wind_Onshore":  _vre_scalar,
        "Wind_Offshore": _vre_scalar,
    }

# Country-specific overrides (same semantics: fraction of CLEVER capacity).
# Blanked for DECARB runs (LOW/CENTRAL/HIGH) so the conservative
# DE/FR/ES/IT/GB caps don't bind below the +25 % uniform default.
VRE_EXPANSION_HEADROOM_BY_COUNTRY: dict[str, dict[str, float]] = {} if (_VRE_EXTENDED or _IS_DECARB_RUN or _VRE_FREE) else {
    "DE": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "FR": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "ES": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "IT": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
    "GB": {"Solar": 0.15, "Wind_Onshore": 0.10, "Wind_Offshore": 0.10},
}

# ── VRE downside band (symmetric ±X% around CLEVER, added 2026-05-12) ─
# Fraction by which the LP may DECOMMISSION CLEVER's announced capacity.
# Reframes CLEVER's role from "ambition floor" to "central estimate with
# uncertainty band" — consistent with Girard 2025's "delivered vs announced"
# deployment-gap finding. Set to 0.0 to revert to the legacy floor-only
# behaviour (CLEVER as hard minimum).
#
# Free-decommissioning: no cost penalty for the LP picking below CLEVER —
# the CAPEX-into-variable-cost adder (clever.model._add_vre_tech_to_area) is
# scaled by `capacity_floor / capacity_mw` (Option β), so the variable-cost
# burden only covers the guaranteed floor portion. Anything above the floor
# is paid via conversion_invest_cost × annuity in the normal LP investment
# objective. Anything below the original CLEVER value is implicitly written
# off (CLEVER's value reinterpreted as upper-bound ambition rather than
# committed deployment).
# DECARB-aware downside band:
#   LOW (bioLow + no h2HIGH) gets −50 % so the LP can right-size to the lower
#   sufficiency demand without paying for CLEVER's "over-supplied" VRE floor.
#   CENTRAL / HIGH get −30 % (was −25 %; loosened 2026-05-25 because the LP was
#   binding exactly at the −25 % floor in every big country, indicating the
#   true cost-optimal sits below).
#   Pre-DECARB scenarios keep the legacy −15 %.
if _VRE_FREE:
    # `_vreFree` (2026-06-26): floor -> 0 (no CLEVER minimum; LP may build nothing).
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 1.0
elif _IS_DECARB_LOW:
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 0.50
elif _IS_DECARB_RUN:
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 0.30
else:
    DEFAULT_VRE_DOWNSIDE_BAND_FRACTION: float = 0.15


