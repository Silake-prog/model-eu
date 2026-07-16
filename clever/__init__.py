"""clever — backwards-compatible alias for the renamed :mod:`pommes_eur` package.

The model package was renamed ``clever`` → ``pommes_eur`` (POMMES-EUR). CLEVER is now
one *case study* of the general model, not the package identity. This shim keeps every
existing ``import clever`` / ``from clever.X import Y`` working by aliasing the
``pommes_eur`` module objects in :data:`sys.modules`, so ``clever.runner is
pommes_eur.runner`` — essential because the launcher monkeypatches functions on the
runner module and both names must resolve to the *same* object.

New code should import :mod:`pommes_eur` directly. The legacy ``CLEVER_SCENARIO`` /
``CLEVER_DATA`` environment variables remain honored (see
:mod:`pommes_eur.scenario.env`).
"""
import importlib
import sys

import pommes_eur

# `import clever` (and attribute access `clever.RESULTS_DIR`, …) resolves to the
# pommes_eur package object itself.
sys.modules[__name__] = pommes_eur

# Alias every importable pommes_eur submodule under the clever.* name so submodule
# imports return the SAME objects. Heavy modules (model/runner/…) need the solver stack;
# where it is absent they simply are not aliased (import-time errors are swallowed), but
# on the cluster every submodule aliases cleanly.
_SUBMODULES = (
    "constants", "inputs", "overrides", "process", "fetch", "demand", "adequacy",
    "carbon_price", "data_fetchers", "biomethane", "methane_h2_ccs", "mena_imports",
    "model", "runner", "r0_input_tables", "r0_overrides",
    "scenario", "scenario.parse", "scenario.registry", "scenario.env",
    "providers", "providers.base", "providers.clever", "providers.eraa",
    "data", "data.inputs", "data.overrides", "data.r0_input_tables", "data.r0_overrides",
    "costs", "costs.carbon_price",
    "sources", "sources.fetch", "sources.process", "sources.demand", "sources.data_fetchers",
    "model.build", "model.techs", "model.techs.biomethane", "model.techs.ccs", "model.techs.mena_imports",
    "solve", "solve.runner", "solve.adequacy",
)
for _name in _SUBMODULES:
    try:
        _mod = importlib.import_module(f"pommes_eur.{_name}")
    except Exception:  # noqa: BLE001 - optional heavy deps may be unavailable
        continue
    sys.modules[f"clever.{_name}"] = _mod
