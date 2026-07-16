"""Tier-B scaffold — model-builder contract check + deferred parameter-table hash.

The code-only snapshot environment has no ``pandas``/``polars``/``pommes_craft``, so
the full "build the LP and hash ``parameter_tables``" check cannot run here. This
module provides:

  * :func:`check_builder_contract` — a dependency-guarded check that the model builder
    imports and keeps its expected signature. Runs whenever the deps are present;
    skips cleanly otherwise. This is correct-by-construction and safe to ship.

  * :func:`build_reference_model` — the deferred hook that, in a deps-equipped
    environment, would assemble a tiny synthetic-input model and let the caller hash
    ``energy_model.parameter_tables``. Left as a documented ``NotImplementedError`` so
    nobody mistakes an untested fabrication for a validated fingerprint; fill it in
    against the real reader schemas (``clever.model.read_clever_*``) where the deps
    exist.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass

# The exact positional parameters create_multi_country_model_from_clever exposes today
# (clever/model.py). If a refactor changes these, Tier B fails loudly rather than
# silently building a different model.
EXPECTED_BUILDER_PARAMS = (
    "country_codes",
    "reference_year_weather",
    "model_year",
    "clever_capacity_df",
    "clever_load_factor_df",
    "clever_non_enr_df",
    "electricity_demand_by_country",
    "eoles_costs",
)

_REQUIRED_DEPS = ("pandas", "polars", "numpy", "pommes_craft")


@dataclass
class ContractResult:
    ok: bool
    reason: str
    skipped: bool = False


def _missing_deps() -> list[str]:
    return [d for d in _REQUIRED_DEPS if importlib.util.find_spec(d) is None]


def check_builder_contract() -> ContractResult:
    """Assert the CLEVER model builder imports with its expected signature."""
    missing = _missing_deps()
    if missing:
        return ContractResult(
            ok=True,
            skipped=True,
            reason=f"missing deps: {', '.join(missing)} (Tier B needs the solver stack)",
        )

    import inspect

    from clever import model  # noqa: WPS433

    fn = getattr(model, "create_multi_country_model_from_clever", None)
    if fn is None:
        return ContractResult(False, "create_multi_country_model_from_clever missing")

    params = tuple(inspect.signature(fn).parameters)
    missing_params = [p for p in EXPECTED_BUILDER_PARAMS if p not in params]
    if missing_params:
        return ContractResult(
            False, f"builder signature drifted; missing params: {missing_params}"
        )
    return ContractResult(True, "builder contract OK")


def build_reference_model(scenario_string: str):  # pragma: no cover - deferred
    """Deferred: assemble a synthetic single-country model for parameter-table hashing.

    Intentionally unimplemented. Complete this against the real reader schemas
    (``clever.model.read_clever_capacity_csv`` / ``read_clever_load_factor_csv`` /
    ``read_clever_non_enr_csv``) in an environment that has pandas/polars/pommes_craft,
    then hash ``energy_model.parameter_tables`` into the Tier-B baseline. Fabricating
    the schema blind here would ship an unvalidated fingerprint, which is worse than a
    clean skip.
    """
    raise NotImplementedError(
        "Tier-B synthetic model build is deferred to a deps-equipped environment; "
        "see synthetic_inputs.build_reference_model docstring."
    )
