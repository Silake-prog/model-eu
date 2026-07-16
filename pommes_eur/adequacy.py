"""Compatibility shim: pommes_eur.adequacy → pommes_eur.solve.adequacy.

sys.modules alias (object identity) — REQUIRED for runner: the launcher monkeypatches
functions on the runner module object, so both names must resolve to the same object.
"""
import sys
from pommes_eur.solve import adequacy as _mod

sys.modules[__name__] = _mod
