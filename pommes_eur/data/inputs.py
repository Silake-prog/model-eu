"""pommes_eur.data.inputs — static input tables (facade).

The tables are split by ownership into two modules and re-exported here so every existing
`from pommes_eur.data.inputs import X` (and the legacy `pommes_eur.inputs` shim) keeps
resolving unchanged:

  - :mod:`pommes_eur.data._inputs_generic` — dataset-agnostic scalars/defaults.
  - :mod:`pommes_eur.data._inputs_clever`  — CLEVER case-study data tables.

Values are byte-for-byte identical to the pre-split module (guarded by tests/golden).
"""
from __future__ import annotations

from pommes_eur.data import _inputs_generic as _g
from pommes_eur.data import _inputs_clever as _c

globals().update({k: v for k, v in vars(_g).items() if not k.startswith("__")})
globals().update({k: v for k, v in vars(_c).items() if not k.startswith("__")})
del _g, _c
