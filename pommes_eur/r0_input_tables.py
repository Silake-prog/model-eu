"""Compatibility shim: pommes_eur.r0_input_tables → pommes_eur.data.r0_input_tables.

Kept so existing `from pommes_eur.r0_input_tables import X` / `from clever.r0_input_tables import X`
(the generated launcher, notebooks, external code) keep resolving to the SAME module
object after the reorg into pommes_eur/data/. Prefer importing pommes_eur.data.r0_input_tables.
"""
import sys
from pommes_eur.data import r0_input_tables as _mod

sys.modules[__name__] = _mod
