"""Compatibility shim: pommes_eur.methane_h2_ccs → pommes_eur.model.techs.ccs."""
import sys
from pommes_eur.model.techs import ccs as _mod

sys.modules[__name__] = _mod
