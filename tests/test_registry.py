"""Phase-4 scenario-registry tests.

Guards three properties:
  1. Structural validation is a *superset* of the historical whitelist — every string in
     ``scripts/run_adequacy.py``'s ``_VALID_SCENARIOS`` (plus free-suffix compositions)
     validates, and obviously-malformed strings are rejected.
  2. ``parse_scenario(s)`` reproduces the ``clever.constants`` scenario globals exactly
     (per-scenario subprocess, since the globals are an import-time singleton). This is
     what proves the registry changed no numerics.
  3. Every flag declares a non-empty pattern and description (docs/flags.md contract).

Runs under pytest or as a plain script (`python tests/test_registry.py`).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from clever.scenario import registry as R  # noqa: E402

# spec field -> clever.constants global name (only fields with a stored global)
_FIELD_TO_GLOBAL = {
    "biomethane_scope": "_BIOMETHANE_SCOPE",
    "atr_enabled": "_ATR_ENABLED",
    "electrolyser_capex_eur_per_kw": "_ELECTROLYSER_CAPEX_EUR_PER_KW",
    "demandforge_bundle_override": "_DEMANDFORGE_BUNDLE_OVERRIDE",
    "co2_price_eur_per_tonne": "_CO2_PRICE_EUR_PER_TONNE",
    "gas_floor_lifted": "_GAS_FLOOR_LIFTED",
    "ng_upstream_leak_rate": "_NG_UPSTREAM_LEAK_RATE",
    "bio_ch4_leak_rate": "_BIO_CH4_LEAK_RATE",
    "ccs_cap_gw_per_country": "_CCS_CAP_GW_PER_COUNTRY",
    "vre_xxl": "_VRE_XXL",
    "vre_free": "_VRE_FREE",
    "nuke_xxl": "_NUKE_XXL",
    "vre_extended": "_VRE_EXTENDED",
    "electricity_voll_override": "_ELECTRICITY_VOLL_OVERRIDE",
    "h2_voll_override": "_H2_VOLL_OVERRIDE",
    "pipeline_eur_per_mw_per_km": "_PIPELINE_EUR_PER_MW_PER_KM",
    "elec_demand_multiplier": "_ELEC_DEMAND_MULTIPLIER",
    "weather_year_override": "_WEATHER_YEAR_OVERRIDE",
    "no_elec_floor": "_NO_ELEC_FLOOR",
    "no_grid_expansion": "_NO_GRID_EXPANSION",
    "h2_local_share": "_H2_LOCAL_SHARE",
    "no_gas": "_NO_GAS",
    "mena_h2_import_cap_twh": "_MENA_H2_IMPORT_CAP_TWH",
    "mena_h2_delivered_cost_eur_per_mwh": "_MENA_H2_DELIVERED_COST_EUR_PER_MWH",
    "mena_h2_delivered_cost_by_entry": "_MENA_H2_DELIVERED_COST_BY_ENTRY",
    "mena_optim_active_countries": "_MENA_OPTIM_ACTIVE_COUNTRIES",
    "mena_infra_global_override_eur_per_mw": "_MENA_INFRA_GLOBAL_OVERRIDE_EUR_PER_MW",
    "mena_infra_per_route_overrides": "_MENA_INFRA_PER_ROUTE_OVERRIDES",
    "mena_risk_per_country_overrides": "_MENA_RISK_PER_COUNTRY_OVERRIDES",
    "mena_cap_scale": "_MENA_CAP_SCALE",
    "mena_pref_scale": "_MENA_PREF_SCALE",
    "mena_ng_wholesale_overrides": "_MENA_NG_WHOLESALE_OVERRIDES",
    "ng_price_override_eur_per_mwh_th": "_NG_PRICE_OVERRIDE_EUR_PER_MWH_TH",
    "oil_price_override_eur_per_mwh_th": "_OIL_PRICE_OVERRIDE_EUR_PER_MWH_TH",
    "fuel_ramp_pct_override": "_FUEL_RAMP_PCT_OVERRIDE",
}

REFERENCE_SCENARIOS = [
    "R0_v1",
    "policy_re",
    "R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT",
    "R0_v1_nuke_bioLow_atr_el700_corr2x_noGas_elecX180_h2HIGH_vreEXT"
    "_nofloor_noElecFloor_co2150_batt20_menaOptim_menaCap200",
]


def _whitelist_strings() -> set[str]:
    src = (REPO_ROOT / "scripts" / "run_adequacy.py").read_text()
    block = src[src.index("_VALID_SCENARIOS"): src.index("NO_MIN_BOUNDS")]
    return set(re.findall(r"['\"]((?:R0_v1|policy_)[A-Za-z0-9_]*)['\"]", block))


def _canon(o):
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in sorted(o.items(), key=lambda kv: str(kv[0]))}
    if isinstance(o, (list, tuple)):
        return [_canon(x) for x in o]
    return o


_DUMP = r"""
import json, os
from clever import constants as ov
from clever.scenario.registry import parse_scenario, _canon_hook  # noqa
"""


def _compare_in_subprocess(scenario: str) -> list[str]:
    """Return list of mismatch messages (empty == spec matches overrides globals)."""
    prog = (
        "import json, os\n"
        "from clever import constants as ov\n"
        "from clever.scenario.registry import parse_scenario\n"
        "s = os.environ['CLEVER_SCENARIO']\n"
        "spec = parse_scenario(s)\n"
        f"m = {json.dumps(_FIELD_TO_GLOBAL)}\n"
        "out = {}\n"
        "for f, g in m.items():\n"
        "    sv = getattr(spec, f); gv = getattr(ov, g)\n"
        "    out[f] = [repr(sv), repr(gv)]\n"
        "print(json.dumps(out))\n"
    )
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ)
        env["CLEVER_SCENARIO"] = scenario
        env["CLEVER_DATA"] = d
        env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), env.get("PYTHONPATH", "")])
        out = subprocess.check_output([sys.executable, "-c", prog], env=env, cwd=str(REPO_ROOT))
    data = json.loads(out)
    return [f"{f}: spec={sv} != global={gv}" for f, (sv, gv) in data.items() if sv != gv]


def test_registry_accepts_whitelist() -> None:
    bad = sorted(s for s in _whitelist_strings() if not R.validate(s))
    assert bad == [], f"registry rejects historically-valid scenarios: {bad}"


def test_registry_rejects_malformed() -> None:
    for s in ["R0_v1_bogusFlag", "policy_re_co2", "notabase_nuke", "R0_v1_bio"]:
        assert not R.validate(s), f"expected {s!r} to be rejected"


def test_parse_scenario_matches_overrides_globals() -> None:
    for scn in REFERENCE_SCENARIOS:
        mism = _compare_in_subprocess(scn)
        assert mism == [], f"spec drift for {scn}:\n  " + "\n  ".join(mism)


def test_flag_declarations_complete() -> None:
    for fl in R.iter_flags():
        assert fl.name and fl.token and fl.changes, f"incomplete flag: {fl}"
        assert fl.pattern.pattern.startswith("_"), fl.name


def _run_standalone() -> int:
    tests = [
        ("accepts_whitelist", test_registry_accepts_whitelist),
        ("rejects_malformed", test_registry_rejects_malformed),
        ("parse_matches_globals", test_parse_scenario_matches_overrides_globals),
        ("flag_declarations_complete", test_flag_declarations_complete),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name} — {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
