"""Compatibility shim: pommes_eur.runner → pommes_eur.solve.runner.

sys.modules alias (object identity) — REQUIRED for runner: the launcher monkeypatches
functions on the runner module object, so both names must resolve to the same object.
"""
import sys
from pommes_eur.solve import runner as _mod

sys.modules[__name__] = _mod
