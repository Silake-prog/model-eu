"""Compatibility shim: pommes_eur.data_fetchers → pommes_eur.sources.data_fetchers.

Kept so existing imports resolve to the same module object after the reorg. Prefer
importing pommes_eur.sources.data_fetchers.
"""
import sys
from pommes_eur.sources import data_fetchers as _mod

sys.modules[__name__] = _mod
