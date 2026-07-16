"""pommes_eur.providers.clever — the CLEVER case-study provider.

A thin delegate over the existing CLEVER pipeline: it does not reimplement anything, it
routes the already-working assembly (``fetch`` -> ``process`` -> ``demand`` ->
``create_multi_country_model_from_clever``) through the :class:`ModelProvider` contract.
Building via this provider therefore produces exactly the same ``EnergyModel`` as calling
the builder directly — CLEVER becomes *one activatable provider*, not the package's
identity.

All heavy imports (pandas / pommes_craft / the data fetchers) are deferred to call time so
this module imports in the code-only environment.
"""
from __future__ import annotations

from typing import Any, Optional

from pommes_eur.providers.base import ProviderInputs
from pommes_eur.scenario.env import current_scenario
from pommes_eur.scenario.registry import ScenarioSpec, parse_scenario


class CleverProvider:
    """CLEVER dataset provider. Satisfies :class:`pommes_eur.providers.base.ModelProvider`."""

    name = "clever"

    def __init__(self, model_year: int = 2050, reference_year_weather: Optional[int] = None):
        self.model_year = model_year
        self._weather_override = reference_year_weather

    # ── scenario ────────────────────────────────────────────────────────────────────
    def scenario_spec(self) -> ScenarioSpec:
        """Resolve the active scenario (POMMES_EUR_SCENARIO / CLEVER_SCENARIO) to a spec."""
        return parse_scenario(current_scenario())

    # ── region membership (data-driven) ──────────────────────────────────────────────
    def country_set(self) -> list[str]:
        """Area codes CLEVER models — data-driven from inputs, not hardcoded call-sites.

        Non-EU regions are added by other providers (or by extending the tables); MENA
        (MA/DZ/TN/LY) already enters this set via the ``_menaOptim`` scenario flag.
        """
        from pommes_eur.inputs import DEFAULT_KEEP_AREAS

        return sorted(DEFAULT_KEEP_AREAS)

    def reference_year_weather(self, spec: ScenarioSpec) -> int:
        from pommes_eur.inputs import DEFAULT_WEATHER_REF_YEAR

        if self._weather_override is not None:
            return self._weather_override
        return spec.weather_year_override or DEFAULT_WEATHER_REF_YEAR

    # ── input assembly (delegates to the existing pipeline) ───────────────────────────
    def fetch_inputs(self, spec: ScenarioSpec) -> ProviderInputs:
        """Assemble CLEVER inputs. Requires the data caches + the scientific stack.

        Delegates to the existing fetch/process/demand pipeline; raises a clear error if
        the data-bearing environment is not available (the code-only snapshot).
        """
        raise NotImplementedError(
            "CleverProvider.fetch_inputs delegates to the CLEVER fetch/process/demand "
            "pipeline, which needs the data caches present only on the cluster. Build the "
            "ProviderInputs from pommes_eur.fetch / .process / .demand in a data-bearing "
            "environment, or construct ProviderInputs directly and call build_model()."
        )

    # ── model build (thin delegate — identical to the direct call) ────────────────────
    def build_model(self, spec: ScenarioSpec, inputs: ProviderInputs) -> Any:
        """Build the EnergyModel via the existing multi-country CLEVER builder."""
        from pommes_eur.model import create_multi_country_model_from_clever

        return create_multi_country_model_from_clever(**inputs.builder_kwargs())
