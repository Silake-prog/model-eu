"""Compatibility shim: pommes_eur.carbon_price → pommes_eur.costs.carbon_price.

Kept so existing imports keep resolving to the same module object after the reorg.
Prefer importing pommes_eur.costs.carbon_price.
"""
import sys
from pommes_eur.costs import carbon_price as _mod

sys.modules[__name__] = _mod
