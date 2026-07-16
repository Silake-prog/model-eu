"""pommes_eur.data — static input tables + scenario-gated derived tables.

- ``inputs``     : static input tables (country/tech maps, EOLES costs, PEMMDB hydro,
                   VRE/BESS specs, NTC corridors, numerical scalars). ``inputs.py`` is a
                   facade over ``_inputs_generic`` (dataset-agnostic) + ``_inputs_clever``.
- ``expansion``  : scenario-gated capacity-expansion + BESS tables (derived).
- ``vre_limits`` : scenario-gated VRE expansion / downside bands (derived).

The scenario-resolved scalar globals live in ``pommes_eur.scenario.resolved`` and the
fuel-price functions in ``pommes_eur.costs.fuel_prices``; ``pommes_eur.constants`` is the
back-compat facade that aggregates all of these. The CLEVER "R0" calibration pipeline lives
in ``pommes_eur.providers.clever`` (``dataset_calibration`` / ``calibration_inputs``).
"""
