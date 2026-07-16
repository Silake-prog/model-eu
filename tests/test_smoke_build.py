"""No-solve smoke test.

Two layers, mirroring the environment split:
  * ``test_scenario_spec_builds`` — always runs (pure Python): a reference scenario
    resolves to a fully-typed ScenarioSpec with the expected value shapes. This is the
    fast "does the scenario pipeline assemble" check the mission asks for, needing no
    solver, data caches, or pommes_craft.
  * ``test_lp_builder_contract`` — builds toward the LP when the solver stack is present,
    else skips: asserts clever.model's EnergyModel builder is importable with a stable
    signature (full LP assembly from synthetic inputs is the deferred Tier-B hook).

Runs under pytest or as a plain script (`python tests/test_smoke_build.py`).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from clever.scenario.registry import ScenarioSpec, parse_scenario  # noqa: E402
from tests.golden import synthetic_inputs  # noqa: E402

_REFERENCE = "R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT"


class _Skipped(Exception):
    pass


def _skip(reason: str) -> None:
    try:
        import pytest

        pytest.skip(reason)
    except ImportError:
        raise _Skipped(reason)


def test_scenario_spec_builds() -> None:
    """A reference scenario assembles into a typed spec with sane value shapes."""
    spec = parse_scenario(_REFERENCE)
    assert isinstance(spec, ScenarioSpec)
    assert spec.scenario == _REFERENCE
    assert spec.no_gas is True                       # _noGas
    assert spec.biomethane_scope == "bioMed"         # _bioMed
    assert spec.vre_extended is True                 # _vreEXT
    assert spec.co2_price_eur_per_tonne == 150.0     # default trajectory anchor
    assert spec.elec_demand_multiplier == 1.8        # _elecX180
    assert isinstance(spec.mena_optim_active_countries, tuple)


def test_lp_builder_contract() -> None:
    """Model builder importable with a stable signature (skips without the solver stack)."""
    result = synthetic_inputs.check_builder_contract()
    if result.skipped:
        _skip(result.reason)
        return
    assert result.ok, result.reason


def _run_standalone() -> int:
    tests = [
        ("test_scenario_spec_builds", test_scenario_spec_builds),
        ("test_lp_builder_contract", test_lp_builder_contract),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except _Skipped as exc:
            print(f"SKIP  {name} — {exc}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name} — {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
