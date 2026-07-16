"""Compatibility shim: pommes_eur.biomethane → pommes_eur.model.techs.biomethane."""
import sys
from pommes_eur.model.techs import biomethane as _mod

sys.modules[__name__] = _mod
