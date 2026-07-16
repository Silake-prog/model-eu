"""Compatibility shim: pommes_eur.inputs → pommes_eur.data.inputs.

Kept so existing `from pommes_eur.inputs import X` / `from clever.inputs import X`
(the generated launcher, notebooks, external code) keep resolving to the SAME module
object after the reorg into pommes_eur/data/. Prefer importing pommes_eur.data.inputs.
"""
import sys
from pommes_eur.data import inputs as _mod

sys.modules[__name__] = _mod
