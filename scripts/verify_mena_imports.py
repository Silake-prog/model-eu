"""§8.1 unit verification for the Phase 2.5 MENA H₂ imports refactor.

Asserts:

  1. Parser correctness — all _menaH2NNN / _menaH2costNNN / _menaOptim* /
     _menaInfra* / _menaRisk* forms parse to their documented values.
  2. Default-OFF — with no MENA suffix, mena_imports_enabled() returns
     False and add_variant_a_imports_to_area is a no-op.
  3. Variant A — under _menaH2200_menaH2cost60, ES and IT each get a
     NetImport(name="mena_h2_import", resource="hydrogen", import_price=60,
     max_yearly_energy_import=200e6*0.5). FR / DE / etc. unchanged.
  4. Variant B — under _menaOptim, add_mena_to_model:
       * builds 4 Areas (MA, DZ, TN, LY) with Solar + Wind_Onshore +
         MENA_electrolysis + h2_storage + 2 Spillage components each
       * builds 5 TransportTechnologies (one per MENA_H2_ROUTES entry)
         wired via Link components with the right area_from / area_to
  5. Per-route CAPEX override — _menaInfraLY_IT_2500 makes
     get_route_capex("LY","IT") == 2.5 M€/MW.

No POMMES LP solve. Pure in-vitro construction check.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "supplyforge"))


# ─── 0. Parser-level assertions (env-independent) ────────────────────────────
from clever.constants import (  # noqa: E402
    _parse_mena_h2_cap,
    _parse_mena_h2_cost,
    _parse_mena_optim_active,
    _parse_mena_infra_global,
    _parse_mena_infra_overrides,
    _parse_mena_risk_overrides,
)

# Variant A
assert _parse_mena_h2_cap("R0_v1") == 0.0
assert _parse_mena_h2_cap("R0_v1_menaH2200") == 200.0
assert _parse_mena_h2_cost("R0_v1") == 75.0
assert _parse_mena_h2_cost("R0_v1_menaH2200_menaH2cost50") == 50.0
print("✓ Variant A parsers (cap default 0, price default 75)")

# Variant B subsets
assert _parse_mena_optim_active("R0_v1") == ()
assert _parse_mena_optim_active("R0_v1_menaOptim") == ("MA", "DZ", "TN", "LY")
assert _parse_mena_optim_active("R0_v1_menaOptimMA") == ("MA",)
assert _parse_mena_optim_active("R0_v1_menaOptimMA_DZ") == ("MA", "DZ")
assert _parse_mena_optim_active("R0_v1_menaOptimMA_DZ_TN") == ("MA", "DZ", "TN")
print("✓ Variant B subset parser (4 / 1 / 2 / 3 countries)")

# CAPEX overrides
assert _parse_mena_infra_global("R0_v1") is None
assert _parse_mena_infra_global("R0_v1_menaInfra1500") == 1_500_000.0
assert _parse_mena_infra_global("R0_v1_menaInfraLY_IT_2500") is None
assert _parse_mena_infra_overrides("R0_v1_menaInfraLY_IT_2500") == {("LY", "IT"): 2_500_000.0}
assert _parse_mena_infra_overrides(
    "R0_v1_menaInfraMA_ES_900_menaInfraTN_IT_1200"
) == {("MA", "ES"): 900_000.0, ("TN", "IT"): 1_200_000.0}
print("✓ CAPEX override parsers (global, per-route, multiple per-route)")

# Risk premia
assert _parse_mena_risk_overrides("R0_v1") == {}
assert _parse_mena_risk_overrides("R0_v1_menaRiskLY_50") == {"LY": 5.0}
assert _parse_mena_risk_overrides("R0_v1_menaRiskMA_5_menaRiskLY_50") == {"MA": 0.5, "LY": 5.0}
print("✓ Risk-premium parsers")


# ─── 1. Default-OFF (no MENA suffix) ─────────────────────────────────────────
import clever.constants as cc
import clever.mena_imports as mi

# Force the lazy globals to the "off" state so subsequent in-process tests
# see a clean baseline (the module-import values were derived from this
# process's CLEVER_SCENARIO env, which may already carry a MENA suffix).
cc._MENA_H2_IMPORT_CAP_TWH = 0.0
cc._MENA_H2_DELIVERED_COST_EUR_PER_MWH = 75.0
cc._MENA_OPTIM_ACTIVE_COUNTRIES = ()
cc._MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW = None
cc._MENA_INFRA_PER_ROUTE_OVERRIDES = {}
cc._MENA_RISK_PER_COUNTRY_OVERRIDES = {}

assert mi.mena_imports_enabled() is False
print("✓ Default-OFF: mena_imports_enabled() is False with no MENA suffix")

# Variant A early-return test
class _NoOpContext:
    def __enter__(self): return self
    def __exit__(self, *exc): return None
class _StubModel:
    def context(self): return _NoOpContext()
class _StubArea:
    def __init__(self, name: str):
        self.name = name
        self.model = _StubModel()
        self.added: list[Any] = []
    def add_component(self, comp): self.added.append(comp)

a_es = _StubArea("ES")
mi.add_variant_a_imports_to_area(a_es, "ES")
assert a_es.added == [], "Variant A must be silent when cap=0"
print("✓ Default-OFF: add_variant_a_imports_to_area is a no-op")


# ─── 2. Variant A under _menaH2200_menaH2cost60 ──────────────────────────────
cc._MENA_H2_IMPORT_CAP_TWH = 200.0
cc._MENA_H2_DELIVERED_COST_EUR_PER_MWH = 60.0

# Variant A (redesigned 2026-05-27): now creates a ConversionTechnology, not
# a NetImport. Capture CT kwargs by monkey-patching pommes_craft.
import pommes_craft
CAPTURED_VA: list[tuple[str, dict]] = []
class _CaptureCT_VA:
    def __init__(self, **kw):
        self._kw = kw
        CAPTURED_VA.append(("ConversionTechnology", kw))
pommes_craft.ConversionTechnology = _CaptureCT_VA  # type: ignore[attr-defined]

# ES + IT should both get a ConversionTechnology
for cc_code in ("ES", "IT"):
    area = _StubArea(cc_code)
    mi.add_variant_a_imports_to_area(area, cc_code)

# Non-MENA-entry country: silent
for cc_code in ("FR", "DE", "PT"):
    area = _StubArea(cc_code)
    mi.add_variant_a_imports_to_area(area, cc_code)

assert len(CAPTURED_VA) == 2, (
    f"Expected exactly 2 ConversionTechnologies (ES + IT), got {len(CAPTURED_VA)}"
)
for _, kw in CAPTURED_VA:
    assert kw["name"] == "mena_h2_import"
    factor = kw["factor"]
    assert factor.get("hydrogen") == 1.0, f"factor: {factor!r}"
    assert all(v >= 0 for v in factor.values()), (
        f"mena_h2_import must be production-only (all factors ≥ 0): {factor!r}"
    )
    assert kw["variable_cost"] == 60.0, f"variable_cost: {kw['variable_cost']}"
    # 200 TWh × 0.5 share = 100 TWh = 1e8 MWh
    assert kw["max_yearly_production"] == 200.0 * 1e6 * 0.5, (
        f"max_yearly_production: {kw['max_yearly_production']}"
    )
    assert kw["invest_cost"] == 0.0
    assert kw["fixed_cost"] == 0.0
    assert kw["power_capacity_investment_max"] > 0
print("✓ Variant A: ES + IT each get ConversionTechnology @ 60 €/MWh, cap=100 TWh, "
      "production-only (no NetImport trigger); FR/DE/PT silent")


# ─── 3. Variant B parser + active-country resolution ─────────────────────────
# Reset Variant A off so resolve_mena_variants reflects only Variant B
cc._MENA_H2_IMPORT_CAP_TWH = 0.0
cc._MENA_OPTIM_ACTIVE_COUNTRIES = ("MA", "DZ", "TN", "LY")
spec = mi.resolve_mena_variants()
assert spec["variant_b_active"] is True
assert spec["variant_b_countries"] == ("MA", "DZ", "TN", "LY")
print("✓ Variant B: full _menaOptim resolves to all 4 countries")


# ─── 4. Variant B per-route CAPEX with overrides ─────────────────────────────
cc._MENA_INFRA_PER_ROUTE_OVERRIDES = {("LY", "IT"): 2_500_000.0}
assert mi.get_route_capex("LY", "IT") == 2_500_000.0   # per-route override wins
assert mi.get_route_capex("MA", "ES") == 900_000.0      # untouched route uses base
print("✓ Per-route override: LY→IT = 2.5 M€/MW, MA→ES = 0.9 M€/MW base")

cc._MENA_INFRA_PER_ROUTE_OVERRIDES = {}
cc._MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW = 1_500_000.0
assert mi.get_route_capex("LY", "IT") == 1_500_000.0   # global override wins
assert mi.get_route_capex("MA", "ES") == 1_500_000.0
print("✓ Global override: all routes pinned at 1.5 M€/MW")

cc._MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW = None
assert mi.get_route_capex("MA", "ES") == 900_000.0
assert mi.get_route_capex("LY", "IT") == 1_700_000.0
print("✓ Base values restored: MA→ES = 0.9, LY→IT = 1.7 M€/MW")


# ─── 5. Variant B: add_mena_to_model end-to-end (mocked POMMES) ──────────────
# Reset captures and set up capture-mocks for ConversionTechnology / Storage /
# Spillage / TransportTechnology / Link / Area
CAPTURED_VB: list[tuple[str, dict]] = []
LINKS_VB: list[tuple[str, str]] = []   # (area_from.name, area_to.name)

class _CaptureCT:
    def __init__(self, **kw):
        self._kw = kw
        CAPTURED_VB.append(("ConversionTechnology", kw))

class _CaptureST:
    def __init__(self, **kw):
        self._kw = kw
        CAPTURED_VB.append(("StorageTechnology", kw))

class _CaptureSpillage:
    def __init__(self, **kw):
        self._kw = kw
        CAPTURED_VB.append(("Spillage", kw))

class _CaptureDemand:
    def __init__(self, **kw):
        self._kw = kw
        CAPTURED_VB.append(("Demand", kw))

class _CaptureLoadShedding:
    def __init__(self, **kw):
        self._kw = kw
        CAPTURED_VB.append(("LoadShedding", kw))

class _CaptureTT:
    def __init__(self, **kw):
        self._kw = kw
        CAPTURED_VB.append(("TransportTechnology", kw))
    def register_link(self, link):
        pass

class _CaptureLink:
    def __init__(self, name: str, area_from: Any, area_to: Any):
        self.name = name
        self.area_from = area_from
        self.area_to = area_to
        LINKS_VB.append((area_from.name, area_to.name))
        CAPTURED_VB.append(("Link", {"name": name,
                                     "area_from": area_from.name,
                                     "area_to":   area_to.name}))
    def add_transport_technology(self, tt):
        tt.register_link(self)

class _CaptureArea:
    def __init__(self, name: str):
        self.name = name
        self.model = _StubEM
        self.added: list[Any] = []
    def add_component(self, comp):
        self.added.append(comp)

class _StubEnergyModel:
    year_ops = [2050]
    hours = list(range(8760))
    def context(self): return _NoOpContext()
_StubEM = _StubEnergyModel()

pommes_craft.ConversionTechnology = _CaptureCT  # type: ignore[attr-defined]
pommes_craft.StorageTechnology    = _CaptureST  # type: ignore[attr-defined]
pommes_craft.Spillage             = _CaptureSpillage  # type: ignore[attr-defined]
pommes_craft.TransportTechnology  = _CaptureTT  # type: ignore[attr-defined]
pommes_craft.Link                 = _CaptureLink  # type: ignore[attr-defined]
pommes_craft.Area                 = _CaptureArea  # type: ignore[attr-defined]
pommes_craft.Demand               = _CaptureDemand  # type: ignore[attr-defined]
pommes_craft.LoadShedding         = _CaptureLoadShedding  # type: ignore[attr-defined]

# Pre-existing EU areas (ES + IT must exist for MA→ES and TN→IT to wire)
areas: dict[str, Any] = {"ES": _CaptureArea("ES"), "IT": _CaptureArea("IT"),
                         "FR": _CaptureArea("FR"), "DE": _CaptureArea("DE")}

# Variant B active for all 4 countries
cc._MENA_OPTIM_ACTIVE_COUNTRIES = ("MA", "DZ", "TN", "LY")

# Mock the PVGIS fetcher to avoid network during the test
import clever.data_fetchers as df_mod
import xarray as xr, numpy as np
def _fake_pvgis(lat, lon, tag, fallback_cf=0.20):
    return xr.DataArray(
        np.full(8760, 0.25, dtype="float32"),
        dims=["hour"], coords={"hour": np.arange(8760)},
    )
df_mod.fetch_pvgis_solar_hourly = _fake_pvgis  # type: ignore[assignment]

# Run the integration
mi.add_mena_to_model(_StubEM, areas)

# Areas: MA, DZ, TN, LY should now be in `areas`
assert set(areas.keys()) >= {"MA", "DZ", "TN", "LY", "ES", "IT", "FR", "DE"}
print(f"✓ Variant B: built MENA areas {sorted(set(areas.keys()) - {'ES','IT','FR','DE'})}")

# Each MENA area should have (post-2026-05-27 redesign): Solar, Wind_Onshore,
# MENA_electrolysis, h2_storage + 2 Spillage + 1 Demand (domestic_h2_demand)
# + 1 LoadShedding (hydrogen_load_shedding) = 8 components.
tech_names_per_area = {
    cc_code: sorted(set(c._kw.get("name", c.__class__.__name__) for c in areas[cc_code].added))
    for cc_code in ("MA", "DZ", "TN", "LY")
}
expected = {"Solar", "Wind_Onshore", "MENA_electrolysis", "h2_storage",
            "mena_electricity_spillage", "mena_hydrogen_spillage",
            "domestic_h2_demand", "hydrogen_load_shedding"}
for cc_code, names in tech_names_per_area.items():
    assert set(names) == expected, (
        f"{cc_code}: missing {expected - set(names)}, extra {set(names) - expected}"
    )
print(f"✓ Variant B: each MENA area carries 8 components — "
      f"{sorted(expected)}")

# Verify the tightened caps (literature-conservative defaults, no _menaCap suffix)
def _kw_for(cc_code, name):
    for c in areas[cc_code].added:
        if c._kw.get("name") == name:
            return c._kw
    raise AssertionError(f"No component named {name!r} on area {cc_code}")

assert _kw_for("MA", "MENA_electrolysis")["power_capacity_investment_max"] == 20_000.0, "MA electrolyser cap"
assert _kw_for("DZ", "MENA_electrolysis")["power_capacity_investment_max"] == 30_000.0, "DZ electrolyser cap"
assert _kw_for("TN", "MENA_electrolysis")["power_capacity_investment_max"] ==  8_000.0, "TN electrolyser cap"
assert _kw_for("LY", "MENA_electrolysis")["power_capacity_investment_max"] == 15_000.0, "LY electrolyser cap"
assert _kw_for("MA", "Solar")["power_capacity_investment_max"] == 60_000.0,  "MA solar cap"
assert _kw_for("DZ", "Solar")["power_capacity_investment_max"] == 80_000.0,  "DZ solar cap"
print("✓ Part A: tightened caps applied (MA: 60/20 GW solar/electrolyser; "
      "DZ: 80/30 GW; TN: 15/8 GW; LY: 40/15 GW)")

# Verify the domestic-H₂ reservation Demand sizing
# MA: 5 TWh/yr × 1e6 / 8760 ≈ 570.78 MW flat baseload
assert abs(_kw_for("MA", "domestic_h2_demand")["demand"] - 5.0e6 / 8760.0) < 0.01
assert abs(_kw_for("DZ", "domestic_h2_demand")["demand"] - 7.0e6 / 8760.0) < 0.01
assert abs(_kw_for("TN", "domestic_h2_demand")["demand"] - 2.0e6 / 8760.0) < 0.01
assert abs(_kw_for("LY", "domestic_h2_demand")["demand"] - 3.0e6 / 8760.0) < 0.01
# Resource must be hydrogen
for cc_code in ("MA", "DZ", "TN", "LY"):
    assert _kw_for(cc_code, "domestic_h2_demand")["resource"] == "hydrogen"
    ls = _kw_for(cc_code, "hydrogen_load_shedding")
    assert ls["resource"] == "hydrogen"
    assert ls["cost"] == 30_000.0, f"{cc_code} LS cost should be DEFAULT_LOAD_SHEDDING_COST"
    # LS max_capacity must equal Demand (so LP can shed at most the demand amount)
    assert ls["max_capacity"] == _kw_for(cc_code, "domestic_h2_demand")["demand"]
print("✓ Part B: domestic H₂ reservation Demand + LoadShedding wired "
      "(MA 570 MW, DZ 800 MW, TN 228 MW, LY 343 MW flat baseload)")

# Check 5 H₂ pipelines were registered (one per MENA_H2_ROUTES entry)
pipeline_links = [(a, b) for (a, b) in LINKS_VB
                  if any(c in a for c in ("MA", "DZ", "TN", "LY"))]
assert sorted(pipeline_links) == sorted(mi.MENA_H2_ROUTES), (
    f"Expected 5 MENA→EU links matching MENA_H2_ROUTES; got {pipeline_links}"
)
print(f"✓ Variant B: 5 pipelines wired — {pipeline_links}")

# Spot-check a TransportTechnology's invest_cost matches base
tts = [kw for cls, kw in CAPTURED_VB if cls == "TransportTechnology"]
ma_es = next(t for t in tts if t["name"] == "mena_h2_pipeline_MA_ES")
ly_it = next(t for t in tts if t["name"] == "mena_h2_pipeline_LY_IT")
assert ma_es["invest_cost"] == 900_000.0
assert ly_it["invest_cost"] == 1_700_000.0
assert ma_es["resource"] == "hydrogen"
assert ma_es["life_span"] == 40
print("✓ Variant B: pipeline invest_cost matches base (MA→ES 0.9, LY→IT 1.7 M€/MW)")


# ─── 6. Subset suffix — only MA active should build only MA route ───────────
CAPTURED_VB.clear()
LINKS_VB.clear()
areas2: dict[str, Any] = {"ES": _CaptureArea("ES"), "IT": _CaptureArea("IT")}
cc._MENA_OPTIM_ACTIVE_COUNTRIES = ("MA",)
mi.add_mena_to_model(_StubEM, areas2)
assert set(areas2.keys()) == {"MA", "ES", "IT"}
ma_only_links = [(a, b) for (a, b) in LINKS_VB if "MA" in a]
assert ma_only_links == [("MA", "ES")], f"got {ma_only_links}"
print("✓ Variant B subset (_menaOptimMA): only 1 country + 1 route built")


# ─── 7. Part C: _menaCap{NN} and _menaPref{NN} scenario suffixes ────────────
# _menaCap200 should DOUBLE all production caps; _menaPref50 should HALVE the
# domestic-H₂ reservation; _menaPref0 should disable the reservation entirely.
CAPTURED_VB.clear()
LINKS_VB.clear()
areas3: dict[str, Any] = {"ES": _CaptureArea("ES"), "IT": _CaptureArea("IT")}
cc._MENA_OPTIM_ACTIVE_COUNTRIES = ("MA",)
cc._MENA_CAP_SCALE = 2.0
cc._MENA_PREF_SCALE = 0.5
# clever.mena_imports caches the import — force a re-read by patching directly.
mi.add_mena_to_model(_StubEM, areas3)
ma_e = next(c._kw for c in areas3["MA"].added if c._kw.get("name") == "MENA_electrolysis")
ma_s = next(c._kw for c in areas3["MA"].added if c._kw.get("name") == "Solar")
ma_d = next(c._kw for c in areas3["MA"].added if c._kw.get("name") == "domestic_h2_demand")
assert ma_e["power_capacity_investment_max"] == 40_000.0, (
    f"_menaCap200 should give 40 GW MA electrolyser, got {ma_e['power_capacity_investment_max']}"
)
assert ma_s["power_capacity_investment_max"] == 120_000.0, (
    f"_menaCap200 should give 120 GW MA solar, got {ma_s['power_capacity_investment_max']}"
)
assert abs(ma_d["demand"] - 0.5 * 5.0e6 / 8760.0) < 0.01, (
    f"_menaPref50 should halve MA domestic demand to ~285 MW, got {ma_d['demand']:.1f}"
)
print("✓ Part C: _menaCap200 doubles caps (MA solar 60→120 GW, electrolyser 20→40 GW); "
      "_menaPref50 halves reservation (MA demand 570→285 MW)")

# _menaPref0 should not add Demand / LoadShedding at all
CAPTURED_VB.clear()
LINKS_VB.clear()
areas4: dict[str, Any] = {"ES": _CaptureArea("ES"), "IT": _CaptureArea("IT")}
cc._MENA_CAP_SCALE = 1.0
cc._MENA_PREF_SCALE = 0.0
mi.add_mena_to_model(_StubEM, areas4)
ma_names = sorted(c._kw.get("name") for c in areas4["MA"].added)
assert "domestic_h2_demand" not in ma_names, (
    f"_menaPref0 must skip the Demand component; got {ma_names}"
)
assert "hydrogen_load_shedding" not in ma_names
print("✓ Part C: _menaPref0 disables domestic reservation entirely (no Demand, no LS)")

# Reset suffix scalers so trailing assertions / future tests aren't polluted.
cc._MENA_CAP_SCALE = 1.0
cc._MENA_PREF_SCALE = 1.0


print()
print("All §8.1 MENA assertions passed ✔︎")
