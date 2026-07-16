"""Phase-7 provider-seam tests.

Guards that CleverProvider satisfies the ModelProvider contract and that its light
methods (scenario resolution, data-driven country set, builder-kwargs shaping) work in
the code-only environment. The heavy build_model path needs pommes_craft and is exercised
on the cluster; here we assert the delegation shape without executing it.

Runnable under pytest or as a plain script.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pommes_eur.providers import CleverProvider  # noqa: E402
from pommes_eur.providers.base import ModelProvider, ProviderInputs  # noqa: E402
from pommes_eur.scenario.registry import ScenarioSpec  # noqa: E402


def test_clever_satisfies_protocol() -> None:
    assert isinstance(CleverProvider(), ModelProvider)


def test_scenario_spec_resolves() -> None:
    os.environ["POMMES_EUR_SCENARIO"] = "R0_v1_nuke_bioMed_atr_el700_noGas_corr2x_elecX180_h2HIGH_vreEXT"
    try:
        spec = CleverProvider().scenario_spec()
    finally:
        os.environ.pop("POMMES_EUR_SCENARIO", None)
    assert isinstance(spec, ScenarioSpec)
    assert spec.no_gas is True and spec.biomethane_scope == "bioMed"


def test_country_set_is_data_driven() -> None:
    from pommes_eur.inputs import DEFAULT_KEEP_AREAS

    cs = CleverProvider().country_set()
    assert cs == sorted(DEFAULT_KEEP_AREAS)
    assert len(cs) > 0 and cs == sorted(cs)


def test_builder_kwargs_shape() -> None:
    pin = ProviderInputs(
        country_codes=["FR"],
        reference_year_weather=2021,
        model_year=2050,
        clever_capacity_df="<df>",
        clever_load_factor_df="<df>",
        clever_non_enr_df="<df>",
        electricity_demand_by_country={"FR": "<pl>"},
        eoles_costs={},
        interconnections={("FR", "DE"): {}},
        extra={"include_hydrogen": True},
    )
    kw = pin.builder_kwargs()
    for key in (
        "country_codes", "reference_year_weather", "model_year", "clever_capacity_df",
        "clever_load_factor_df", "clever_non_enr_df", "electricity_demand_by_country",
        "eoles_costs", "interconnections", "include_hydrogen",
    ):
        assert key in kw, f"missing builder kwarg: {key}"
    assert kw["include_hydrogen"] is True


def test_eraa_provider_is_a_conformant_stub() -> None:
    """The ERAA provider proves a second dataset plugs into the same seam."""
    from pommes_eur.providers import EraaProvider
    from pommes_eur.providers.base import ProviderInputs

    prov = EraaProvider()
    assert isinstance(prov, ModelProvider)  # structural conformance
    # unimplemented data paths raise clearly rather than silently mis-building
    for call in (
        prov.country_set,
        lambda: prov.fetch_inputs(None),
        lambda: prov.build_model(None, ProviderInputs([], 2021, 2050, None, None, None, {}, {})),
    ):
        try:
            call()
        except NotImplementedError:
            continue
        raise AssertionError("expected NotImplementedError from ERAA stub")


def _run_standalone() -> int:
    failed = 0
    for name, fn in [
        ("clever_satisfies_protocol", test_clever_satisfies_protocol),
        ("scenario_spec_resolves", test_scenario_spec_resolves),
        ("country_set_is_data_driven", test_country_set_is_data_driven),
        ("builder_kwargs_shape", test_builder_kwargs_shape),
        ("eraa_provider_is_a_conformant_stub", test_eraa_provider_is_a_conformant_stub),
    ]:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name} — {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
