"""pommes_eur.providers.clever — the CLEVER case-study provider + its pipeline.

- ``provider``          : CleverProvider (the ModelProvider adapter).
- ``dataset_calibration`` : calibrate the assembled POMMES dataset to CLEVER per-country
                          values (electrolyser CAPEX/floors, H2 storage) — formerly ``r0_overrides``.
- ``calibration_inputs``  : build the per-country calibration kwargs from the CLEVER
                          reference tables + DemandForge H2 demand — formerly ``r0_input_tables``.
"""
from pommes_eur.providers.clever.provider import CleverProvider

__all__ = ["CleverProvider"]
