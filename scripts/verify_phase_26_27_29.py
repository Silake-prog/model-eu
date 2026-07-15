"""§8.1 unit verification for Phases 2.6 + 2.7 + 2.9.

  Phase 2.6 — DECARB-aware battery cap scaling
    * non-DECARB / policy_*  → 1.0× (FR Battery_4h = 20 GW)
    * DECARB CENTRAL / LOW   → 1.5× (FR Battery_4h = 30 GW)
    * DECARB HIGH (h2HIGH)   → 2.0× (FR Battery_4h = 40 GW)
    * _battNNN override      → NNN/10 ×

  Phase 2.7 — Investable corridors
    * 13 corridors total; 5 new (IT-AT, IT-CH, DE-PL, DE-CH, IT-GR)
    * each new pair has a baseline NTC entry in MANUAL_INTERCONNECTIONS

  Phase 2.9 — Nuclear policy gates
    * IT/AT/PT/IE/DK/LU/GR have explicit Nuclear:0.0 (no silent 2 GW fallback)
    * _itNuke10000 → IT["Nuclear"] = 10000
    * _atNuke5000  → AT["Nuclear"] = 5000

Each block runs the import in a fresh subprocess so module-level globals
(_SCENARIO, _BATTERY_SCALE, EXPANSION_HEADROOM_BY_COUNTRY) are recomputed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_check(scenario: str, body: str, label: str) -> None:
    """Run a Python check in a subprocess with CLEVER_SCENARIO set.
    Body must end with prints; exit 0 = pass, non-zero = fail."""
    preamble = (
        f"import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'supplyforge')!r})\n"
    )
    script = preamble + body
    env = {**os.environ, "CLEVER_SCENARIO": scenario, "MPLBACKEND": "Agg"}
    try:
        out = subprocess.run(
            [sys.executable, "-c", script],
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


# ─── Phase 2.6 — battery scaling tiers ──────────────────────────────────────
print("=" * 64)
print("Phase 2.6 — DECARB-aware battery cap scaling")
print("=" * 64)

run_check(
    "R0_v1_nuke",
    """
from clever.constants import _BATTERY_SCALE, BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY
assert _BATTERY_SCALE == 1.0, _BATTERY_SCALE
assert BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY['FR']['Battery_4h'] == 20_000.0
print(f'_BATTERY_SCALE={_BATTERY_SCALE} (FR Battery_4h={BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY[\"FR\"][\"Battery_4h\"]:.0f} MW)')
""",
    "Non-DECARB baseline → 1.0×",
)

run_check(
    "R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX125_h2central",
    """
from clever.constants import _BATTERY_SCALE, BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY
assert _BATTERY_SCALE == 1.5, _BATTERY_SCALE
assert BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY['FR']['Battery_4h'] == 30_000.0
assert BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY['DE']['Battery_4h'] == 60_000.0
print(f'_BATTERY_SCALE={_BATTERY_SCALE} (FR={BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY[\"FR\"][\"Battery_4h\"]:.0f}, DE={BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY[\"DE\"][\"Battery_4h\"]:.0f} MW Battery_4h)')
""",
    "DECARB CENTRAL → 1.5×",
)

run_check(
    "R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT",
    """
from clever.constants import _BATTERY_SCALE, BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY
assert _BATTERY_SCALE == 2.0, _BATTERY_SCALE
assert BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY['FR']['Battery_4h'] == 40_000.0
assert BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY['DE']['Battery_4h'] == 80_000.0
total = sum(
    cap for d in BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY.values() for cap in d.values()
) / 1000.0
assert total > 400, f'EU total {total:.0f} GW < 400 GW for HIGH scale'
print(f'_BATTERY_SCALE={_BATTERY_SCALE} (EU sum across listed countries = {total:.0f} GW)')
""",
    "DECARB HIGH → 2.0×",
)

run_check(
    "R0_v1_nuke_bioHigh_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT_batt25",
    """
from clever.constants import _BATTERY_SCALE, BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY
assert _BATTERY_SCALE == 2.5, _BATTERY_SCALE
assert BESS_POWER_INVESTMENT_MAX_MW_BY_COUNTRY['FR']['Battery_4h'] == 50_000.0
print(f'_BATTERY_SCALE={_BATTERY_SCALE} (override via _batt25)')
""",
    "_batt25 override → 2.5×",
)


# ─── Phase 2.7 — investable corridors ───────────────────────────────────────
print()
print("=" * 64)
print("Phase 2.7 — Investable corridor expansion")
print("=" * 64)

run_check(
    "R0_v1_nuke",
    """
from clever.constants import INVESTABLE_CORRIDOR_PAIRS, MANUAL_INTERCONNECTIONS
assert len(INVESTABLE_CORRIDOR_PAIRS) == 13, len(INVESTABLE_CORRIDOR_PAIRS)
new_pairs = [
    frozenset({"IT","AT"}), frozenset({"IT","CH"}),
    frozenset({"DE","PL"}), frozenset({"DE","CH"}),
    frozenset({"IT","GR"}),
]
for p in new_pairs:
    assert p in INVESTABLE_CORRIDOR_PAIRS, f'missing pair {p}'
    # Confirm baseline NTC exists in MANUAL_INTERCONNECTIONS for at least one direction
    found = any((a,b) in MANUAL_INTERCONNECTIONS for a in p for b in p if a != b)
    assert found, f'no baseline NTC for {p} in MANUAL_INTERCONNECTIONS'
print(f'13 corridors total; 5 new pairs all have baseline NTC')
""",
    "13 investable corridors with baseline NTCs",
)


# ─── Phase 2.9 — nuclear policy gates ───────────────────────────────────────
print()
print("=" * 64)
print("Phase 2.9 — Country-specific nuclear policy gates")
print("=" * 64)

run_check(
    "R0_v1_nuke",
    """
from clever.constants import EXPANSION_HEADROOM_BY_COUNTRY
for cc in ("IT", "AT", "PT", "IE", "DK", "LU", "GR"):
    headroom = EXPANSION_HEADROOM_BY_COUNTRY.get(cc, {})
    assert "Nuclear" in headroom, f'{cc}: Nuclear key MISSING (silent fallback to 2 GW!)'
    assert headroom["Nuclear"] == 0.0, f'{cc}: Nuclear = {headroom["Nuclear"]} (should be 0.0)'
print('IT/AT/PT/IE/DK/LU/GR all have explicit Nuclear=0.0 — no silent 2 GW fallback')
""",
    "All 7 ban-countries have explicit Nuclear=0.0",
)

run_check(
    "R0_v1_nuke_itNuke10000",
    """
from clever.constants import EXPANSION_HEADROOM_BY_COUNTRY
assert EXPANSION_HEADROOM_BY_COUNTRY["IT"]["Nuclear"] == 10_000.0
# Sibling countries stay at 0.0
assert EXPANSION_HEADROOM_BY_COUNTRY["AT"]["Nuclear"] == 0.0
assert EXPANSION_HEADROOM_BY_COUNTRY["PT"]["Nuclear"] == 0.0
print(f'IT[Nuclear]={EXPANSION_HEADROOM_BY_COUNTRY["IT"]["Nuclear"]:.0f} MW (override applied)')
""",
    "_itNuke10000 sets IT Nuclear=10 GW; others unchanged",
)

run_check(
    "R0_v1_nuke_itNuke5000_atNuke3000",
    """
from clever.constants import EXPANSION_HEADROOM_BY_COUNTRY
assert EXPANSION_HEADROOM_BY_COUNTRY["IT"]["Nuclear"] == 5_000.0
assert EXPANSION_HEADROOM_BY_COUNTRY["AT"]["Nuclear"] == 3_000.0
assert EXPANSION_HEADROOM_BY_COUNTRY["PT"]["Nuclear"] == 0.0
print('Multiple per-country overrides stack correctly (IT=5 GW, AT=3 GW, PT untouched)')
""",
    "Multiple _ccNuke overrides stack",
)


print()
print("=" * 64)
print("All Phase 2.6 / 2.7 / 2.9 §8.1 assertions passed ✔︎")
print("=" * 64)
