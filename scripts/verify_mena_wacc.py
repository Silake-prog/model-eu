"""§8.1 audit: verify country-specific MENA WACC survives runner post-processing.

The runner's enforce_uniform_wacc_on_h2_techs() override (clever/runner.py:916)
rescales h2-system techs that were instantiated with finance_rate=0 (supplyforge
default) up to 4%. MENA components MUST escape this rescaling because we
explicitly applied a country-specific finance rate (4% + sovereign-risk premium).

The override has two guardrails:
  1. Tech-name filter — only "electrolysis" / "hydrogen_power_plant" rescaled
     on the conversion side. MENA's tech is "MENA_electrolysis" → bypass.
  2. is_zero mask — only slots with finance_rate == 0 are touched. MENA passes
     0.045 (MA) up to 0.07 (LY) so the mask is False for MENA slots.

Probe:
  a. mena_finance_rate("MA") ≈ 0.045, mena_finance_rate("LY") ≈ 0.07.
  b. Construct a minimal xarray with one MENA-style storage_tech="h2_storage"
     entry at finance_rate=0.045 and one EU-style at finance_rate=0.0. Apply
     enforce_uniform_wacc_on_h2_techs; assert MENA stays at 0.045 and EU gets
     bumped to 0.04.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "supplyforge"))


# ─── 1. Verify mena_finance_rate() respects per-country risk premium ──────
from clever.mena_imports import mena_finance_rate, MENA_COUNTRY_CONFIG

for cc, expected_premium_pct in (("MA", 0.5), ("DZ", 1.0), ("TN", 0.5), ("LY", 3.0)):
    got = mena_finance_rate(cc)
    expected = 0.04 + expected_premium_pct / 100.0
    assert abs(got - expected) < 1e-9, (
        f"mena_finance_rate({cc!r}) — got {got}, expected {expected}"
    )
print("✓ mena_finance_rate per-country: MA=4.5%, DZ=5.0%, TN=4.5%, LY=7.0%")


# ─── 2. Verify per-country override via _menaRisk{CC}_NNN ─────────────────
import clever.constants as cc_mod
saved = dict(cc_mod._MENA_RISK_PER_COUNTRY_OVERRIDES)
try:
    cc_mod._MENA_RISK_PER_COUNTRY_OVERRIDES = {"LY": 5.0}  # 5 pp uplift override
    assert mena_finance_rate("LY") == 0.04 + 0.05
    print("✓ _menaRiskLY_50 override: LY WACC = 9.0%")
finally:
    cc_mod._MENA_RISK_PER_COUNTRY_OVERRIDES = saved


# ─── 3. Inspect mena_imports.py call-site for finance_rate plumbing ───────
import inspect, clever.mena_imports as mi
src = inspect.getsource(mi.add_mena_country_area)
# Every CAPEX-bearing component in MENA must carry finance_rate=finance_rate.
for tech_block in ("name=\"Solar\"", "name=\"Wind_Onshore\"",
                   "name=\"MENA_electrolysis\"", "name=\"h2_storage\""):
    # Find the tech block, then check the next ~25 lines for finance_rate=
    idx = src.find(tech_block)
    assert idx >= 0, f"missing tech block: {tech_block}"
    block = src[idx : idx + 1500]
    assert "finance_rate=finance_rate" in block, (
        f"{tech_block}: finance_rate NOT wired to mena_finance_rate result"
    )
print("✓ Source audit: Solar / Wind_Onshore / MENA_electrolysis / h2_storage "
      "all pass finance_rate=mena_finance_rate(country)")


# ─── 4. Verify runner.py override skips MENA-named electrolysis ───────────
from clever.runner import enforce_uniform_wacc_on_h2_techs
override_src = inspect.getsource(enforce_uniform_wacc_on_h2_techs)
# The tuple at line ~958 lists only EU names — MENA_electrolysis isn't there
assert "MENA_electrolysis" not in override_src, (
    "runner.py override mentions MENA_electrolysis — would incorrectly rescale it"
)
assert "\"electrolysis\"" in override_src
assert "\"hydrogen_power_plant\"" in override_src
print("✓ runner.py enforce_uniform_wacc: tech-name filter excludes MENA_electrolysis")


# ─── 5. Empirical xarray test: per-slot is_zero protects MENA h2_storage ──
# Construct a minimal Dataset shaped like POMMES would build for two areas:
# "ES" (EU, finance_rate=0 from supplyforge) and "MA" (MENA, finance_rate=0.045).
import numpy as np
import xarray as xr

areas = ["ES", "MA"]
year_inv = [2050]
year_dec = [2090]  # 40-year life

ds = xr.Dataset(
    {
        "storage_finance_rate": (
            ("storage_tech", "area", "year_inv"),
            # ES = 0 (supplyforge default), MA = 0.045 (MENA per-country uplift)
            np.array([[[0.00], [0.045]]]),
        ),
        "storage_annuity_cost_energy": (
            ("storage_tech", "area", "year_inv", "year_dec"),
            # Pre-override annuity cost — for ES this is the "wrong" value (CRF=1/40
            # because finance_rate=0). For MA it's the "right" value (CRF includes 4.5%).
            np.array([[[[100.0]], [[100.0]]]]),  # arbitrary; only the multiplication matters
        ),
        "storage_annuity_cost_power": (
            ("storage_tech", "area", "year_inv", "year_dec"),
            np.array([[[[50.0]], [[50.0]]]]),
        ),
    },
    coords={
        "storage_tech": ["h2_storage"],
        "area":         areas,
        "year_inv":     year_inv,
        "year_dec":     year_dec,
    },
)

ds_after = enforce_uniform_wacc_on_h2_techs(ds.copy(deep=True), wacc=0.04)

# Assertions
fr_after = ds_after["storage_finance_rate"].sel(storage_tech="h2_storage")
fr_es = float(fr_after.sel(area="ES").item())
fr_ma = float(fr_after.sel(area="MA").item())
assert fr_es == 0.04, f"ES (EU) finance_rate after override: {fr_es} (expected 0.04)"
assert fr_ma == 0.045, f"MA (MENA) finance_rate after override: {fr_ma} (expected 0.045 — should be UNCHANGED)"
print(f"✓ Empirical test: enforce_uniform_wacc bumps ES 0 → 4%, leaves MA at 4.5% untouched")

# Annuity cost: ES gets rescaled by CRF(4%, 40)*40 = 0.0505 * 40 = 2.02;
# MA must NOT be rescaled (its annuity stays at 100).
ann_es_after = float(ds_after["storage_annuity_cost_energy"]
                     .sel(storage_tech="h2_storage", area="ES").item())
ann_ma_after = float(ds_after["storage_annuity_cost_energy"]
                     .sel(storage_tech="h2_storage", area="MA").item())
crf40 = 0.04 * (1 + 0.04) ** 40 / ((1 + 0.04) ** 40 - 1)
expected_es_scaled = 100.0 * crf40 * 40
assert abs(ann_es_after - expected_es_scaled) < 1e-3, (
    f"ES annuity rescaling wrong: {ann_es_after} vs expected {expected_es_scaled}"
)
assert ann_ma_after == 100.0, f"MA annuity should be unchanged: got {ann_ma_after}"
print(f"✓ Empirical test: ES annuity rescaled 100 → {ann_es_after:.2f}; MA annuity unchanged at 100")


print()
print("MENA WACC audit: CLEAN ✔︎")
print("  - Per-country risk premium correctly applied via finance_rate at build time.")
print("  - runner.py override has two guardrails (tech-name filter + per-slot is_zero")
print("    mask) that BOTH protect MENA from being silently bumped back to 4%.")
print("  - Empirical xarray test confirms the protection works end-to-end.")
