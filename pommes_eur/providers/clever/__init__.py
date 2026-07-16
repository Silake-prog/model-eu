"""pommes_eur.providers.clever — the CLEVER case-study provider + its pipeline.

- ``provider``          : CleverProvider (the ModelProvider adapter).
- ``dataset_overrides`` : apply CLEVER per-country overrides to the assembled POMMES
                          dataset (electrolyser CAPEX/floors, H2 storage) — formerly
                          the cryptically-named ``r0_overrides``.
- ``override_inputs``   : build the per-country override kwargs from the CLEVER reference
                          tables + DemandForge H2 demand — formerly ``r0_input_tables``.
"""
from pommes_eur.providers.clever.provider import CleverProvider

__all__ = ["CleverProvider"]
