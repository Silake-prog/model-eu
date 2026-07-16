"""Compatibility shim: pommes_eur.r0_overrides → pommes_eur.data.r0_overrides.

Kept so existing `from pommes_eur.r0_overrides import X` / `from clever.r0_overrides import X`
(the generated launcher, notebooks, external code) keep resolving to the SAME module
object after the reorg into pommes_eur/data/. Prefer importing pommes_eur.data.r0_overrides.
"""
import sys
from pommes_eur.data import r0_overrides as _mod

sys.modules[__name__] = _mod
