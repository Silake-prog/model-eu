"""Compatibility shim: pommes_eur.fetch → pommes_eur.sources.fetch.

Kept so existing imports resolve to the same module object after the reorg. Prefer
importing pommes_eur.sources.fetch.
"""
import sys
from pommes_eur.sources import fetch as _mod

sys.modules[__name__] = _mod
