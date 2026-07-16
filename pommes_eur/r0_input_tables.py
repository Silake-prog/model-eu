"""Compatibility shim: pommes_eur…r0_input_tables → pommes_eur.providers.clever.override_inputs.

The CLEVER 'R0' pipeline was renamed and rehomed under the CLEVER provider. This alias
keeps old `from clever.r0_input_tables import …` imports (the generated launcher, notebooks)
resolving to the same module object. Prefer pommes_eur.providers.clever.override_inputs.
"""
import sys
from pommes_eur.providers.clever import override_inputs as _mod

sys.modules[__name__] = _mod
