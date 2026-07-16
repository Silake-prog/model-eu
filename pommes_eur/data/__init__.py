"""pommes_eur.data — static input tables + scenario-resolved values.

- ``inputs``           : static input tables (country/tech maps, EOLES costs, PEMMDB
                         hydro, VRE/BESS specs, NTC corridors, numerical scalars).
- ``overrides``        : scenario-resolved globals + scenario-gated derived tables.
- ``r0_input_tables``  : CLEVER R0 per-country CSV glue → runner override kwargs.
- ``r0_overrides``     : CLEVER R0 override pipeline applied to the POMMES dataset.
"""
