"""Import-graph guard for the package reorganization.

Catches the two failure modes a folder reorg can introduce — a broken import path or a
circular import — without needing the solver stack. Deliberately move-stable: it imports
the *stable* top-level module names (which stay resolvable via the sys.modules alias shims
after each move) and compiles every source file wherever it lives, so it does not need
editing as modules migrate into subpackages.

What it guards:
  * every module imports, OR fails ONLY because an external heavy dep is absent
    (pommes/pommes_craft/supplyforge/demandforge/xarray/polars/pyarrow). Any OTHER
    ImportError — a circular import, or a typo'd `pommes_eur.*` path — FAILS the test.
  * `clever.X is pommes_eur.X` object identity for the light modules (the shims must alias
    module objects, not re-export values — the launcher monkeypatches on these objects).
  * the constants facade chain still shares objects (`constants.EPS_MW is inputs.EPS_MW`).
  * every .py under pommes_eur/ and clever/ compiles (catches syntax + guards the 4
    un-importable-here modules model/runner/adequacy/demand).

Full validation of model/runner/adequacy/demand (dangling internal imports, monkeypatch
identity, numerics) needs a cluster no-solve build; this only guards structure.

Runnable under pytest or as a plain script.
"""
from __future__ import annotations

import importlib
import py_compile
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# External deps that may be absent in a code-only checkout. A module that fails to import
# ONLY because one of these is missing is skipped; anything else is a real failure.
ALLOWED_MISSING = {
    "pommes", "pommes_craft", "supplyforge", "demandforge",
    "xarray", "polars", "pyarrow", "numpy", "pandas",
}

# Stable top-level module names (always resolvable — real modules now, alias shims after
# they move into subpackages). Order irrelevant.
ALL_MODULES = [
    "constants", "inputs", "r0_input_tables", "r0_overrides",
    "carbon_price", "data_fetchers", "fetch", "process", "demand",
    "scenario.resolved", "data.expansion", "data.vre_limits", "costs.fuel_prices",
    "biomethane", "methane_h2_ccs", "mena_imports", "model", "runner", "adequacy",
    "scenario.parse", "scenario.registry", "scenario.env",
    "providers.base", "providers.clever", "providers.eraa",
]

# Modules with no heavy deps — these MUST import in any environment (canary).
MUST_IMPORT = [
    "constants", "inputs", "carbon_price", "fetch", "process",
    "scenario.resolved", "data.expansion", "data.vre_limits", "costs.fuel_prices",
    "biomethane", "methane_h2_ccs", "mena_imports", "r0_input_tables",
    "scenario.parse", "scenario.registry", "scenario.env",
    "providers.base", "providers.clever", "providers.eraa",
]


class _Skipped(Exception):
    pass


def _skip(reason: str) -> None:
    try:
        import pytest

        pytest.skip(reason)
    except ImportError:
        raise _Skipped(reason)


def _try_import(name: str):
    """Import pommes_eur.<name>; return module, or None if only an allowed dep is missing.

    Raises on any other ImportError (circular import, typo'd pommes_eur path, …).
    """
    try:
        return importlib.import_module(f"pommes_eur.{name}")
    except ModuleNotFoundError as e:
        root = (e.name or "").split(".")[0]
        if root in ALLOWED_MISSING:
            return None
        raise


def test_no_broken_or_circular_imports() -> None:
    for name in ALL_MODULES:
        _try_import(name)  # raises on a real breakage; returns None on absent heavy dep


def test_pure_modules_import() -> None:
    for name in MUST_IMPORT:
        assert _try_import(name) is not None, f"pommes_eur.{name} should import (no heavy deps)"


def test_clever_alias_identity() -> None:
    import clever  # noqa: F401 (triggers the shim)

    for name in MUST_IMPORT:
        p = _try_import(name)
        if p is None:
            continue
        c = importlib.import_module(f"clever.{name}")
        assert c is p, f"clever.{name} is not pommes_eur.{name} (shim must alias the object)"


def test_facade_chain_shares_objects() -> None:
    import pommes_eur.constants as c
    import pommes_eur.inputs as inp

    # constants is an aggregation facade over inputs/resolved/expansion/vre_limits/fuel_prices.
    assert c.EPS_MW is inp.EPS_MW
    assert c.AREA_MAP is inp.AREA_MAP


# Cross-module imports that MUST stay inside function bodies (lazy) — promoting any of
# these to module level reintroduces a hard circular import. (file, imported-module-substr)
_LAZY_ONLY_EDGES = [
    ("pommes_eur/costs/fuel_prices.py", "costs.carbon_price"),
    ("pommes_eur/costs/fuel_prices.py", "sources.data_fetchers"),
    ("pommes_eur/costs/carbon_price.py", "pommes_eur.constants"),
    ("pommes_eur/model/build.py", "model.techs.mena_imports"),
    ("pommes_eur/model/build.py", "model.techs.biomethane"),
    ("pommes_eur/model/build.py", "model.techs.ccs"),
    ("pommes_eur/model/techs/mena_imports.py", "pommes_eur.model import"),
    ("pommes_eur/model/techs/mena_imports.py", "model.techs.ccs"),
]


def _module_level_import_lines(path: Path) -> list[str]:
    """Return source lines of imports that are direct children of the module (not nested)."""
    import ast

    tree = ast.parse(path.read_text())
    src = path.read_text().splitlines()
    out = []
    for node in tree.body:  # only module-level statements
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(src[node.lineno - 1])
    return out


def test_cycle_edges_stay_lazy() -> None:
    for rel, needle in _LAZY_ONLY_EDGES:
        p = REPO_ROOT / rel
        if not p.exists():
            continue
        offenders = [ln for ln in _module_level_import_lines(p) if needle in ln]
        assert not offenders, (
            f"{rel}: import of {needle!r} was promoted to module level "
            f"(reintroduces a circular import). Keep it inside the function body.\n"
            + "\n".join(offenders)
        )


def test_all_sources_compile() -> None:
    failures = []
    for pkg in ("pommes_eur", "clever"):
        for f in sorted((REPO_ROOT / pkg).rglob("*.py")):
            try:
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as e:  # pragma: no cover
                failures.append(f"{f}: {e}")
    assert not failures, "source files failed to compile:\n  " + "\n  ".join(failures)


def _run_standalone() -> int:
    failed = 0
    for name, fn in [
        ("no_broken_or_circular_imports", test_no_broken_or_circular_imports),
        ("pure_modules_import", test_pure_modules_import),
        ("clever_alias_identity", test_clever_alias_identity),
        ("facade_chain_shares_objects", test_facade_chain_shares_objects),
        ("cycle_edges_stay_lazy", test_cycle_edges_stay_lazy),
        ("all_sources_compile", test_all_sources_compile),
    ]:
        try:
            fn()
            print(f"PASS  {name}")
        except _Skipped as e:
            print(f"SKIP  {name} — {e}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name} — {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
