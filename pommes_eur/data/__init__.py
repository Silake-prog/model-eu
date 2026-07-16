"""pommes_eur.data — static input tables + scenario-gated derived tables.

- ``inputs``     : static input tables (country/tech maps, EOLES costs, PEMMDB hydro,
                   VRE/BESS specs, NTC corridors, numerical scalars).
- ``expansion``  : scenario-gated capacity-expansion + BESS tables (derived).
- ``vre_limits`` : scenario-gated VRE expansion / downside bands (derived).
- ``overrides``  : thin back-compat facade re-exporting the scenario-resolved values
                   (from ``scenario.resolved``), the derived tables (``expansion`` +
                   ``vre_limits``) and the fuel-price functions (``costs.fuel_prices``),
                   so ``from pommes_eur.constants import X`` keeps resolving.

The CLEVER "R0" override pipeline moved to ``pommes_eur.providers.clever``
(``dataset_overrides`` / ``override_inputs``); ``data/r0_*.py`` remain as compat shims.
"""
