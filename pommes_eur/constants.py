"""clever.constants — backwards-compatible facade.

Historically this was the single ~100 KB source of truth for the whole model. It has
been carved, with no change to any value, into three focused modules:

  * ``clever/scenario/parse.py`` — pure scenario-string parsers (string -> value).
  * ``clever/inputs.py``         — static input tables (country/tech maps, EOLES costs,
                                    PEMMDB hydro, VRE specs, BESS specs, NTCs, scalars).
  * ``clever/overrides.py``      — scenario-resolved globals and scenario-gated derived
                                    tables (the override layer), plus the fuel-price
                                    functions.

This module re-exports the union of those namespaces so that every existing
``from clever.constants import X`` (in model.py, runner.py, demand.py, mena_imports.py,
the drivers, …) keeps resolving exactly as before. New code should import from the
specific module instead. Values are byte-for-byte identical — guarded by tests/golden.
"""
from __future__ import annotations

# overrides imports parse + inputs internally, so its module namespace already holds
# the parsers, the static tables, and the scenario-resolved/derived values. Copy the
# whole thing across (including the underscore-prefixed scenario globals that a plain
# `import *` would skip) to preserve the historical `clever.constants` surface.
from pommes_eur import overrides as _overrides

globals().update(
    {k: v for k, v in vars(_overrides).items() if not k.startswith("__")}
)
del _overrides
