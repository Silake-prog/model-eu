"""pommes_eur.providers.eraa — ERAA data provider (reference stub).

This is the extension point that proves POMMES-EUR is dataset-agnostic: a second,
non-CLEVER provider fed by ENTSO-E's **ERAA** (European Resource Adequacy Assessment)
data. It is intentionally a stub — the generic builder it would wrap already exists in
``supplyforge/supplyforge/create_pommes_craft_model.py``
(``fetch_eraa_data`` + ``create_multi_country_renewable_model``, which already returns a
``pommes_craft.EnergyModel`` and therefore already fits the ModelProvider contract), but
building/validating a real ERAA run needs the ERAA data caches, which are absent in this
code-only snapshot. Completing it is a future, data-bearing pass.

To implement:
  * ``country_set()``  → ERAA bidding-zone list (see
    ``supplyforge.fetch.day_ahead_prices.COUNTRY_BIDDING_ZONE_MAPPING``).
  * ``fetch_inputs()`` → ``supplyforge.fetch.eraa_study.fetch_eraa_data`` +
    the capacity/availability/CF/inflow tables the supplyforge builder assembles.
  * ``build_model()``  → delegate to
    ``supplyforge.create_pommes_craft_model.create_multi_country_renewable_model``.
"""
from __future__ import annotations

from typing import Any

from pommes_eur.providers.base import ProviderInputs
from pommes_eur.scenario.registry import ScenarioSpec, parse_scenario
from pommes_eur.scenario.env import current_scenario

_NOT_IMPLEMENTED = (
    "EraaProvider is a documented stub: the generic ERAA builder lives in "
    "supplyforge/supplyforge/create_pommes_craft_model.py (create_multi_country_renewable_model) "
    "and already fits the ModelProvider contract, but a real ERAA run needs the ERAA data caches "
    "absent from this code-only snapshot. Implement in a data-bearing environment."
)


class EraaProvider:
    """ENTSO-E ERAA dataset provider (reference stub)."""

    name = "eraa"

    def scenario_spec(self) -> ScenarioSpec:
        # Scenario grammar is provider-neutral; ERAA reuses the same registry.
        return parse_scenario(current_scenario())

    def country_set(self) -> list[str]:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def fetch_inputs(self, spec: ScenarioSpec) -> ProviderInputs:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def build_model(self, spec: ScenarioSpec, inputs: ProviderInputs) -> Any:
        raise NotImplementedError(_NOT_IMPLEMENTED)
