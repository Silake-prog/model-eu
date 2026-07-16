"""pommes_eur.providers.base — the ModelProvider contract.

The seam that lets POMMES-EUR be dataset-agnostic. Both the CLEVER builder
(``pommes_eur.model.create_multi_country_model_from_clever``) and the generic
ERAA-driven reference builder (``supplyforge.create_pommes_craft_model``) already
produce a ``pommes_craft.EnergyModel``; this module just *names* that contract so a
scenario run can be routed through any provider.

Kept dependency-light: no pandas / pommes_craft import here, so the protocol and the
input container import in the code-only environment. Frame-typed fields are ``Any``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from pommes_eur.scenario.registry import ScenarioSpec


@dataclass
class ProviderInputs:
    """The assembled inputs a provider hands to its model builder.

    Mirrors the arguments of
    ``pommes_eur.model.create_multi_country_model_from_clever`` exactly, so a provider's
    ``build_model`` can splat this straight through. No new data — this is a typed
    container around what the pipeline already produces.
    """

    country_codes: list[str]
    reference_year_weather: int
    model_year: int
    clever_capacity_df: Any
    clever_load_factor_df: Any
    clever_non_enr_df: Any
    electricity_demand_by_country: dict[str, Any]
    eoles_costs: dict[str, Any]
    interconnections: Optional[dict] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def builder_kwargs(self) -> dict[str, Any]:
        """Return the kwargs dict for the multi-country CLEVER builder."""
        kw: dict[str, Any] = {
            "country_codes": self.country_codes,
            "reference_year_weather": self.reference_year_weather,
            "model_year": self.model_year,
            "clever_capacity_df": self.clever_capacity_df,
            "clever_load_factor_df": self.clever_load_factor_df,
            "clever_non_enr_df": self.clever_non_enr_df,
            "electricity_demand_by_country": self.electricity_demand_by_country,
            "eoles_costs": self.eoles_costs,
        }
        if self.interconnections is not None:
            kw["interconnections"] = self.interconnections
        kw.update(self.extra)
        return kw


@runtime_checkable
class ModelProvider(Protocol):
    """A dataset/case-study that can build a POMMES-EUR model.

    Implementations: :class:`pommes_eur.providers.clever.CleverProvider` (working),
    ``pommes_eur.providers.eraa.EraaProvider`` (stub / reference).
    """

    def scenario_spec(self) -> ScenarioSpec:
        """Resolve the active scenario string to a typed spec."""
        ...

    def country_set(self) -> list[str]:
        """Return the region membership (area codes) this provider models."""
        ...

    def fetch_inputs(self, spec: ScenarioSpec) -> ProviderInputs:
        """Assemble the model inputs for ``spec`` (may hit data caches / network)."""
        ...

    def build_model(self, spec: ScenarioSpec, inputs: ProviderInputs) -> Any:
        """Build and return the ``pommes_craft.EnergyModel`` for ``spec`` + ``inputs``."""
        ...
