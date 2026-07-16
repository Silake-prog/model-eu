"""Compatibility shim: pommes_eur.overrides → pommes_eur.data.overrides.

Kept so existing `from pommes_eur.overrides import X` / `from clever.overrides import X`
(the generated launcher, notebooks, external code) keep resolving to the SAME module
object after the reorg into pommes_eur/data/. Prefer importing pommes_eur.data.overrides.
"""
import sys
from pommes_eur.data import overrides as _mod

sys.modules[__name__] = _mod
