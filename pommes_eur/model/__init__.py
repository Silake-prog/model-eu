"""pommes_eur.model — POMMES EnergyModel construction.

`build.py` holds the model-assembly + CLEVER-wiring code (formerly model.py); `techs/`
holds the scenario-gated technology builders (biomethane, CCS, MENA imports).

The public surface of `build` is re-exported **lazily** (PEP 562 ``__getattr__``): the
heavy ``build`` module (which needs pommes_craft/supplyforge) is only imported when a
symbol is first accessed. This keeps `from pommes_eur.model import X` working exactly as
when model.py was one module — including underscore helpers like `_candidate_area_codes`
that clever.adequacy imports — while NOT forcing pommes_craft on code that only touches
`pommes_eur.model.techs.*` (which import fine without the solver stack).
"""
from __future__ import annotations

import importlib
from typing import Any


def __getattr__(name: str) -> Any:  # PEP 562 — lazy re-export from build
    build = importlib.import_module("pommes_eur.model.build")
    try:
        value = getattr(build, name)
    except AttributeError as exc:  # pragma: no cover
        raise AttributeError(f"module 'pommes_eur.model' has no attribute {name!r}") from exc
    globals()[name] = value  # cache so __getattr__ fires once per name
    return value


def __dir__() -> list[str]:
    build = importlib.import_module("pommes_eur.model.build")
    return sorted(set(globals()) | set(vars(build)))
