"""Compatibility shim: pommes_eur.process → pommes_eur.sources.process.

Kept so existing imports resolve to the same module object after the reorg. Prefer
importing pommes_eur.sources.process.
"""
import sys
from pommes_eur.sources import process as _mod

sys.modules[__name__] = _mod
