"""§8.1 unit verification for Phase 3 — methane (+ oil) endogenisation.

Asserts:
  1. Fetcher returns plausible values in the 10-60 €/MWh_th range.
  2. _ngPriceNNN / _oilPriceNNN scenario overrides win over the fetcher.
  3. natural_gas_import_price() / oil_import_price() layer CO₂ on top
     correctly (NG: +0.202 × CO2_price; oil: +0.267 × CO2_price).
  4. FUEL_ADDER_2050 no longer carries ch4_ccgt or ch4_ocgt entries.
  5. _create_empty_energy_model adds "natural_gas" + "oil" to resources
     when fossil methane is allowed; both are absent under _noGas.
  6. add_dispatchable_from_non_enr (via mock) sets the Gas factor to
     {"electricity":1, "natural_gas":-1/0.58} and Oil to
     {"electricity":1, "natural_gas":-1/0.38}.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_check(scenario: str, body: str, label: str) -> None:
    preamble = (
        f"import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'supplyforge')!r})\n"
    )
    env = {**os.environ, "CLEVER_SCENARIO": scenario, "MPLBACKEND": "Agg"}
    try:
        out = subprocess.run(
            [sys.executable, "-c", preamble + body],
            check=True, capture_output=True, text=True, env=env,
        )
        for ln in out.stdout.strip().splitlines():
            print(f"  {ln}")
        print(f"✓ {label}  [scenario={scenario!r}]")
    except subprocess.CalledProcessError as exc:
        print(f"✗ {label}  [scenario={scenario!r}]")
        print("--- stdout ---"); print(exc.stdout)
        print("--- stderr ---"); print(exc.stderr)
        raise SystemExit(1)


print("=" * 64)
print("Phase 3 — Fossil methane endogenisation")
print("=" * 64)

# ─── 1. Fetcher returns plausible values ──────────────────────────────────
run_check(
    "",
    """
from clever.data_fetchers import (
    fetch_natural_gas_price_eur_per_mwh_th,
    fetch_brent_crude_price_eur_per_mwh_th,
)
ng = fetch_natural_gas_price_eur_per_mwh_th()
oil = fetch_brent_crude_price_eur_per_mwh_th()
assert 10 <= ng  <= 60, f'NG price {ng:.1f} out of plausible 10-60 range'
assert 15 <= oil <= 90, f'Oil price {oil:.1f} out of plausible 15-90 range'
print(f'NG = {ng:.1f} EUR/MWh_th (live), Brent+refining = {oil:.1f} EUR/MWh_th')
""",
    "Fetcher: NG + Brent in plausible ranges",
)

# ─── 2. Scenario overrides ────────────────────────────────────────────────
run_check(
    "R0_v1_nuke_ngPrice25",
    """
from clever.constants import _NG_PRICE_OVERRIDE_EUR_PER_MWH_TH, natural_gas_import_price, _CO2_PRICE_EUR_PER_TONNE
assert _NG_PRICE_OVERRIDE_EUR_PER_MWH_TH == 25.0
# With default _CO2_PRICE=150 and intensity 0.202: 25 + 150*0.202 = 55.3 EUR/MWh_th
expected = 25.0 + 150.0 * 0.202
assert abs(natural_gas_import_price() - expected) < 0.01, natural_gas_import_price()
print(f'override 25 + CO2 = {natural_gas_import_price():.2f} EUR/MWh_th')
""",
    "_ngPrice25 override applied + CO2 layer",
)

run_check(
    "R0_v1_nuke_oilPrice70",
    """
from clever.constants import _OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH, oil_import_price
assert _OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH == 70.0
# 70 + 150 * 0.267 = 110.05
expected = 70.0 + 150.0 * 0.267
assert abs(oil_import_price() - expected) < 0.01
print(f'override 70 + CO2 = {oil_import_price():.2f} EUR/MWh_th')
""",
    "_oilPrice70 override + CO2 layer (intensity 0.267)",
)

# ─── 3. _co2300 raises both bus prices ────────────────────────────────────
run_check(
    "R0_v1_nuke_ngPrice25_co2300",
    """
from clever.constants import natural_gas_import_price, _CO2_PRICE_EUR_PER_TONNE
assert _CO2_PRICE_EUR_PER_TONNE == 300.0
# 25 + 300*0.202 = 85.6
expected = 25.0 + 300.0 * 0.202
assert abs(natural_gas_import_price() - expected) < 0.01
print(f'_co2300 raised NG bus price to {natural_gas_import_price():.2f} EUR/MWh_th')
""",
    "_co2300 propagates through NG bus",
)

# ─── 4. FUEL_ADDER stripped ───────────────────────────────────────────────
run_check(
    "R0_v1_nuke",
    """
from clever.constants import FUEL_ADDER_2050
assert 'ch4_ccgt' not in FUEL_ADDER_2050, 'ch4_ccgt should be stripped'
assert 'ch4_ocgt' not in FUEL_ADDER_2050, 'ch4_ocgt should be stripped'
# Retained entries
for k in ('nuclear', 'coal', 'waste', 'biomass_coge', 'h2_ccgt'):
    assert k in FUEL_ADDER_2050, f'{k} should still be in FUEL_ADDER_2050'
print(f'FUEL_ADDER keys: {sorted(FUEL_ADDER_2050.keys())}')
""",
    "FUEL_ADDER stripped of ch4_ccgt/ch4_ocgt; nuclear/coal/waste/biomass_coge/h2_ccgt retained",
)

# ─── 5. EnergyModel resources include natural_gas + oil ───────────────────
run_check(
    "R0_v1_nuke",
    """
from clever.model import _create_empty_energy_model
em = _create_empty_energy_model(name='test', model_year=2050, include_hydrogen=True)
assert 'natural_gas' in em.resources
assert 'oil' in em.resources
print(f'Resources: {em.resources}')
""",
    "Non-_noGas: natural_gas + oil both in EnergyModel resources",
)

run_check(
    "R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central",
    """
from clever.model import _create_empty_energy_model
em = _create_empty_energy_model(name='test', model_year=2050)
assert 'natural_gas' not in em.resources, 'natural_gas must be absent under _noGas'
assert 'oil' not in em.resources,         'oil must be absent under _noGas'
print(f'Resources under _noGas: {em.resources}')
""",
    "_noGas: natural_gas + oil both ABSENT",
)

# ─── 6. Gas / Oil factor in add_dispatchable_from_non_enr ─────────────────
# Monkey-patch ConversionTechnology to capture the factor for "Gas" and "Oil"
# techs; check both consume natural_gas at the right inverse-efficiency.
run_check(
    "R0_v1_nuke_ngPrice25",
    """
import pommes_craft
captured = []
class _Cap:
    def __init__(self, **kw): captured.append((kw.get('name'), kw))
pommes_craft.ConversionTechnology = _Cap

# Build a minimal fake area + clever_non_enr_df with Gas + Oil rows
class _NoOp:
    def __enter__(self): return self
    def __exit__(self, *exc): return None
class _M:
    def context(self): return _NoOp()
class _A:
    def __init__(self, name): self.name = name; self.model = _M()
    def add_component(self, c): pass

# Build the row data: model_tech="Gas" and "Oil", small existing capacities.
import pandas as pd
df = pd.DataFrame([
    {'area': 'FR', 'year_op': 2050, 'model_tech': 'Gas', 'max_yearly_production_mwh': 1e7},
    {'area': 'FR', 'year_op': 2050, 'model_tech': 'Oil', 'max_yearly_production_mwh': 1e6},
])

# Stub eoles_costs with all required entries
eoles_costs = {
    'vom':           {'ch4_ccgt': 6.0,  'ch4_ocgt': 7.0},
    'fom':           {'ch4_ccgt': 47.0, 'ch4_ocgt': 23.0},
    'capex':         {'ch4_ccgt':1015.0,'ch4_ocgt':814.0},
    'discount':      {'ch4_ccgt': 0.04, 'ch4_ocgt': 0.04},
    'storage_capex': {},
}

from clever.model import add_dispatchable_from_non_enr
add_dispatchable_from_non_enr(_A('FR'), df, country_code='FR', model_year=2050, eoles_costs=eoles_costs)

# Two captures expected (Gas + Oil)
names = [n for n, _ in captured]
assert 'Gas' in names and 'Oil' in names, names
gas_kw = next(kw for n, kw in captured if n == 'Gas')
oil_kw = next(kw for n, kw in captured if n == 'Oil')

# Gas: factor electricity 1.0, natural_gas -1/0.58
assert gas_kw['factor']['electricity'] == 1.0
assert abs(gas_kw['factor']['natural_gas'] - (-1.0/0.58)) < 1e-9
assert gas_kw['variable_cost'] == 6.0, gas_kw['variable_cost']

# Oil: factor electricity 1.0, natural_gas -1/0.38
assert oil_kw['factor']['electricity'] == 1.0
assert abs(oil_kw['factor']['natural_gas'] - (-1.0/0.38)) < 1e-9
assert oil_kw['variable_cost'] == 7.0, oil_kw['variable_cost']

print(f'Gas factor: {gas_kw[\"factor\"]}, VOM-only variable_cost={gas_kw[\"variable_cost\"]}')
print(f'Oil factor: {oil_kw[\"factor\"]}, VOM-only variable_cost={oil_kw[\"variable_cost\"]}')
""",
    "Gas + Oil factor consume natural_gas at correct inverse efficiency",
)


print()
print("=" * 64)
print("All Phase 3 §8.1 assertions passed ✔︎")
print("=" * 64)
