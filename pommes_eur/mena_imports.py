"""Compatibility shim: pommes_eur.mena_imports → pommes_eur.model.techs.mena_imports."""
import sys
from pommes_eur.model.techs import mena_imports as _mod

sys.modules[__name__] = _mod
