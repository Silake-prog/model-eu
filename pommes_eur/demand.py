"""Compatibility shim: pommes_eur.demand → pommes_eur.sources.demand.

Kept so existing imports resolve to the same module object after the reorg. Prefer
importing pommes_eur.sources.demand.
"""
import sys
from pommes_eur.sources import demand as _mod

sys.modules[__name__] = _mod
