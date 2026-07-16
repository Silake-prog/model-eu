"""Guard the clever -> pommes_eur rename compatibility shim.

The rename keeps `import clever` working via a sys.modules alias. The critical property
is *object identity*: `clever.runner is pommes_eur.runner`, because the launcher
monkeypatches functions on the runner module and both names must resolve to the same
object. Also guards the POMMES_EUR_SCENARIO / CLEVER_SCENARIO precedence.

Runnable under pytest or as a plain script.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Light submodules that import without the solver stack — identity must hold for these.
_LIGHT = ("constants", "inputs", "overrides", "scenario.parse", "scenario.registry", "scenario.env")


def test_clever_is_pommes_eur() -> None:
    import clever
    import pommes_eur

    assert clever is pommes_eur


def test_submodule_identity() -> None:
    import importlib

    import clever  # noqa: F401 (triggers the shim)

    for name in _LIGHT:
        c = importlib.import_module(f"clever.{name}")
        p = importlib.import_module(f"pommes_eur.{name}")
        assert c is p, f"clever.{name} is not pommes_eur.{name}"


def test_env_var_precedence() -> None:
    prog = (
        "from pommes_eur.scenario.env import current_scenario as cs\n"
        "print(cs())\n"
    )
    # POMMES_EUR_SCENARIO wins over CLEVER_SCENARIO
    env = dict(os.environ, POMMES_EUR_SCENARIO="new_wins", CLEVER_SCENARIO="legacy")
    env["PYTHONPATH"] = str(REPO_ROOT)
    out = subprocess.check_output([sys.executable, "-c", prog], env=env, cwd=str(REPO_ROOT)).decode().strip()
    assert out == "new_wins", out
    # legacy CLEVER_SCENARIO still honored when the new var is unset
    env2 = {k: v for k, v in os.environ.items() if k != "POMMES_EUR_SCENARIO"}
    env2["CLEVER_SCENARIO"] = "legacy_ok"
    env2["PYTHONPATH"] = str(REPO_ROOT)
    out2 = subprocess.check_output([sys.executable, "-c", prog], env=env2, cwd=str(REPO_ROOT)).decode().strip()
    assert out2 == "legacy_ok", out2


def _run_standalone() -> int:
    failed = 0
    for name, fn in [
        ("clever_is_pommes_eur", test_clever_is_pommes_eur),
        ("submodule_identity", test_submodule_identity),
        ("env_var_precedence", test_env_var_precedence),
    ]:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name} — {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
