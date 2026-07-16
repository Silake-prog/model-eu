"""Compatibility shim: pommes_eur…r0_overrides → pommes_eur.providers.clever.dataset_overrides.

The CLEVER 'R0' pipeline was renamed and rehomed under the CLEVER provider. This alias
keeps old `from clever.r0_overrides import …` imports (the generated launcher, notebooks)
resolving to the same module object. Prefer pommes_eur.providers.clever.dataset_overrides.
"""
import sys
from pommes_eur.providers.clever import dataset_overrides as _mod

sys.modules[__name__] = _mod
