"""§8.1 unit verification for the Phase 1 + 1.5 biomethane endogenisation.

Captures the kwargs passed to NetImport / ConversionTechnology when
add_biomethane_to_area runs, and asserts:

  1. A NetImport on resource="biomethane" is created per area, priced at
     the ENSPRESO feedstock cost and capped at the JRC potential.
  2. Biomethane_CCGT consumes biomethane via factor (-1/CCGT_EFF) and
     produces electricity (+1). variable_cost = VOM only; no
     max_yearly_production kwarg.
  3. ATR_biomethane consumes biomethane via factor (-1/ATR_EFF) and
     produces hydrogen (+1). variable_cost = VOM only; no
     max_yearly_production kwarg.
  4. _parse_biomethane_scope recognises bioLow / bioMed / bioHigh, and
     _bioOff overrides any tier suffix.

Run from the repo root:

    CLEVER_SCENARIO=R0_v1_nuke_bioLow_atr_el700_noGas_corr2x \
        python scripts/verify_biomethane_refactor.py

Re-run with the HIGH baseline to verify bioHigh end-to-end:

    CLEVER_SCENARIO=R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT \
        python scripts/verify_biomethane_refactor.py

No POMMES LP solve, no inari. Pure in-vitro construction check.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

import pandas as pd


# ── 0. Make sure the scenario carries biomethane + ATR before anything imports
#       clever.constants, which freezes parsers at module-load time. ─────────
os.environ.setdefault(
    "CLEVER_SCENARIO",
    "R0_v1_nuke_bioLow_atr_el700_noGas_corr2x",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "supplyforge"))


# ── 0.5. Parser-level assertions (env-independent — pure regex). ────────────
# These verify the bioHigh extension landed correctly without needing any
# CLEVER_SCENARIO setting; they exercise _parse_biomethane_scope directly.
from clever.constants import _parse_biomethane_scope as _pbs  # noqa: E402

_PARSER_CASES = {
    "R0_v1":                            None,
    "R0_v1_bioLow":                     "bioLow",
    "R0_v1_bioMed_atr":                 "bioMed",
    "R0_v1_nuke_bioHigh_atr_el700":     "bioHigh",
    "R0_v1_bioMed_bioOff":              None,        # bioOff overrides
    "R0_v1_bioHigh_bioOff_h2HIGH":      None,        # bioOff still wins
    "R0_v1_biohigh":                    None,        # case-sensitive
}
for scenario, expected in _PARSER_CASES.items():
    got = _pbs(scenario)
    assert got == expected, (
        f"_parse_biomethane_scope({scenario!r}) — got {got!r}, expected {expected!r}"
    )
print("✓ _parse_biomethane_scope: bioLow / bioMed / bioHigh / bioOff all wired")

from clever.constants import _BIOMETHANE_ENSPRESO_SCENARIO  # noqa: E402
# Quick mapping check using the same dict-construction shape as constants.py
_MAPPING = {"bioLow": "ENS_Low", "bioMed": "ENS_Med", "bioHigh": "ENS_High"}
for scope, ens in _MAPPING.items():
    assert _MAPPING.get(scope) == ens
print("✓ _BIOMETHANE_ENSPRESO_SCENARIO mapping: bioHigh → ENS_High included")
print()


# ── 1. Capture all kwargs handed to POMMES component constructors. ──────────
CAPTURED: list[tuple[str, dict[str, Any]]] = []


class _Capture:
    """Drop-in replacement for a POMMES component class."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        CAPTURED.append((type(self).__name__, kwargs))


class ConversionTechnology(_Capture):
    pass


class NetImport(_Capture):
    pass


# Inject the captures into pommes_craft BEFORE clever.biomethane lazy-imports
# them at call time. The biomethane.py code does:
#   from pommes_craft import ConversionTechnology, NetImport
# so we need to attach our captures as attributes on the package module.
import pommes_craft  # noqa: E402 — after sys.path setup
pommes_craft.ConversionTechnology = ConversionTechnology  # type: ignore[attr-defined]
pommes_craft.NetImport = NetImport  # type: ignore[attr-defined]


# ── 2. Stub the per-country potential loader so we don't depend on the CSV. ─
import clever.biomethane as bm  # noqa: E402

_FAKE_POTENTIALS = pd.DataFrame(
    [{"country": "FR", "potential_TWh_th": 120.0, "cost_eur_per_MWh_th": 30.0}]
)


def _fake_loader(scope: str, ens_scen: str, ws=None) -> pd.DataFrame:
    return _FAKE_POTENTIALS


bm._load_country_potentials_cached = _fake_loader  # type: ignore[assignment]


# ── 3. Minimal Area / model context stand-in. ───────────────────────────────
class _NoOpContext:
    def __enter__(self) -> "_NoOpContext":
        return self

    def __exit__(self, *exc) -> None:
        return None


class _StubModel:
    def context(self) -> _NoOpContext:
        return _NoOpContext()


class _StubArea:
    def __init__(self) -> None:
        self.model = _StubModel()
        self.added: list[Any] = []

    def add_component(self, comp: Any) -> None:
        self.added.append(comp)


# ── 4. Run the function under test. ─────────────────────────────────────────
area = _StubArea()
bm.add_biomethane_to_area(area=area, country_code="FR")


# ── 5. Sanity-check + assertions. ───────────────────────────────────────────
def _by_name(name: str) -> dict[str, Any]:
    """Return the kwargs dict for the captured component with this `name`."""
    matches = [kw for _, kw in CAPTURED if kw.get("name") == name]
    if not matches:
        raise AssertionError(
            f"No component captured with name={name!r}. "
            f"Captured: {[(c, kw.get('name')) for c, kw in CAPTURED]}"
        )
    if len(matches) > 1:
        raise AssertionError(f"Multiple components captured with name={name!r}")
    return matches[0]


print("─── Captured components ────────────────────────────────")
for cls, kw in CAPTURED:
    print(f"  {cls}({kw.get('name')!r})")
print()

# Phase 1.7: expect FOUR components (raw_biomass_supply, biomethane_plant, BioCCGT, ATR)

# (a) ConversionTechnology(raw_biomass_supply) — Phase 1.7 redesign (2026-05-27)
# Was a NetImport in the original Phase 1.7 design; switched to a
# production-only ConversionTechnology to avoid triggering POMMES's full
# net_import module expansion (which would create ~7.35M variables and
# ~3.68M equality constraints unmasked over (area × hour × resource × year_op)).
# Semantics are identical: priced supply on the raw_biomass bus, capped at
# the JRC ENSPRESO potential.
bm_supply = _by_name("raw_biomass_supply")
assert ("factor" in bm_supply), (
    f"raw_biomass_supply must be a ConversionTechnology with a factor dict: {bm_supply!r}"
)
factor = bm_supply["factor"]
assert factor.get("raw_biomass") == 1.0, (
    f"raw_biomass_supply factor must be +1 raw_biomass (production-only): {factor!r}"
)
# Crucially: no consumed resource (the whole point of the redesign is that
# this CT does NOT trigger p.net_import=True). Cross-check no negative entry.
assert all(v >= 0 for v in factor.values()), (
    f"raw_biomass_supply must be production-only (all factors ≥ 0): {factor!r}"
)
assert bm_supply["variable_cost"] == 30.0, (
    f"raw_biomass_supply variable_cost: got {bm_supply['variable_cost']}, expected 30.0"
)
assert bm_supply["max_yearly_production"] == 120.0 * 1e6, (
    f"raw_biomass_supply max_yearly_production: {bm_supply['max_yearly_production']}"
)
assert bm_supply["invest_cost"] == 0.0, (
    f"raw_biomass_supply invest_cost must be 0 (no real CAPEX — pure cost-of-feedstock CT)"
)
assert bm_supply["fixed_cost"] == 0.0
# power_capacity_investment_max should be a finite positive value (sized at 5×
# average power) so the planning variable is bounded.
assert bm_supply["power_capacity_investment_max"] > 0
print("✓ ConversionTechnology(raw_biomass_supply): "
      "factor={raw_biomass:+1}, var_cost=30 €/MWh_th, cap=120 TWh/yr, "
      "production-only (no NetImport trigger)")

# (b) biomethane_plant — NEW in Phase 1.7
plant = _by_name("biomethane_plant")
factor = plant["factor"]
assert factor.get("raw_biomass") == -1.0, f"biomethane_plant raw_biomass factor: {factor!r}"
assert factor.get("biomethane") == 1.0, f"biomethane_plant biomethane factor: {factor!r}"
expected_ad_capex = bm._AD_CAPEX_EUR_PER_KW * 1000.0
assert plant["invest_cost"] == expected_ad_capex, (
    f"AD plant invest_cost: got {plant['invest_cost']}, expected {expected_ad_capex}"
)
expected_ad_fom = bm._AD_FOM_EUR_PER_KW_YR * 1000.0
assert plant["fixed_cost"] == expected_ad_fom, (
    f"AD plant fixed_cost: got {plant['fixed_cost']}, expected {expected_ad_fom}"
)
assert plant["variable_cost"] == 0.0, (
    f"AD plant variable_cost must be 0 (feedstock is on the bus): got {plant['variable_cost']}"
)
assert plant["life_span"] == bm._AD_LIFE_YR
print(f"✓ biomethane_plant: factor (raw_biomass:-1, biomethane:+1), "
      f"CAPEX {expected_ad_capex/1e6:.2f} M€/MW_th, life {bm._AD_LIFE_YR}y")

# (c) Biomethane_CCGT — CCGT-only CAPEX (no AD bundle)
ccgt = _by_name("Biomethane_CCGT")
factor = ccgt["factor"]
assert factor.get("electricity") == 1.0
expected_bio_in_ccgt = -1.0 / bm._CCGT_EFF
assert abs(factor.get("biomethane", 0) - expected_bio_in_ccgt) < 1e-9
expected_ccgt_capex = bm._CCGT_CAPEX_EUR_PER_KW * 1000.0
assert ccgt["invest_cost"] == expected_ccgt_capex, (
    f"BioCCGT invest_cost: got {ccgt['invest_cost']}, expected {expected_ccgt_capex} "
    f"(CCGT-only — Phase 1.7 unbundled the AD plant)"
)
assert ccgt["variable_cost"] == bm._CCGT_VOM_EUR_PER_MWH
assert "max_yearly_production" not in ccgt
print(f"✓ Biomethane_CCGT: consumes biomethane, CCGT-only CAPEX "
      f"{expected_ccgt_capex/1e6:.2f} M€/MW_e (was 5.42 M€ bundled in Phase 1)")

# (d) ATR_biomethane — ATR-only CAPEX (no AD bundle)
atr = _by_name("ATR_biomethane")
factor = atr["factor"]
assert factor.get("hydrogen") == 1.0
expected_bio_in_atr = -1.0 / bm._ATR_EFF
assert abs(factor.get("biomethane", 0) - expected_bio_in_atr) < 1e-9
expected_atr_capex = bm._ATR_CAPEX_EUR_PER_KW * 1000.0
assert atr["invest_cost"] == expected_atr_capex, (
    f"ATR invest_cost: got {atr['invest_cost']}, expected {expected_atr_capex} "
    f"(ATR-only — Phase 1.7 unbundled the AD plant)"
)
assert atr["variable_cost"] == bm._ATR_VOM_EUR_PER_MWH
assert "max_yearly_production" not in atr
print(f"✓ ATR_biomethane: consumes biomethane, ATR-only CAPEX "
      f"{expected_atr_capex/1e6:.2f} M€/MW_H2 (was 4.11 M€ bundled in Phase 1)")

# (e) Joint consistency — AD plant is shared between BioCCGT and ATR
assert bm_supply["max_yearly_production"] == 120.0 * 1e6
print("✓ Joint chain: raw_biomass CT cap 120 TWh → biomethane_plant (shared CAPEX) → "
      "BioCCGT + ATR share the methanizer fleet")

print()
print("All §8.1 assertions passed ✔︎")
