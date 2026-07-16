"""Unit tests for the PECD catalog + config validation + fetcher coverage."""
import pytest

from supplyforge.fetch.pecd import catalog, study
from supplyforge.fetch.pecd.catalog import validate_pecd_config, resolve_code, fv2_applies


def _proj(**over):
    p = {"temporal_period": "future_projections", "origin": "ec_earth3", "emission_scenario": "ssp5_8_5",
         "spatial_resolution_wind": "p2on", "spatial_resolution_solar": "szon",
         "energy_scenario": "resource_grade_b", "climate_years": [2050],
         "variables_hydro": ["hydropower_reservoir_inflow"], "ror_source": "entsoe"}
    p.update(over)
    return {"res_source": "pecd", "hydro_source": "pecd", "pecd": p}


def _hist(**over):
    p = {"temporal_period": "historical", "origin": "era5_reanalysis", "spatial_resolution_wind": "peon",
         "spatial_resolution_solar": "szon", "energy_scenario": "resource_grade_b", "climate_years": [2008]}
    p.update(over)
    return {"res_source": "pecd", "hydro_source": "pecd", "pecd": p}


def test_valid_configs_pass():
    validate_pecd_config(_proj())
    validate_pecd_config(_hist())


@pytest.mark.parametrize("cfg, why", [
    (_proj(emission_scenario=None), "missing ssp"),
    (_proj(origin="not_a_model"), "bad origin"),
    (_proj(emission_scenario="ssp9"), "bad ssp"),
    (_proj(climate_years=[1850]), "year out of range"),
    (_hist(spatial_resolution_wind="nuts_0"), "nut0 wind deprecated"),
    (_hist(origin="ec_earth3"), "historical must be era5"),
    (_hist(spatial_resolution_solar="zzz"), "bad solar level"),
    (_hist(energy_scenario="grade_x"), "bad energy_scenario"),
    (_hist(variables_hydro=["not_a_var"]), "unknown hydro variable"),
    (_proj(extra_variables=["NOPE"]), "unknown extra variable"),
])
def test_invalid_configs_raise(cfg, why):
    with pytest.raises(ValueError):
        validate_pecd_config(cfg)


def test_resolve_code():
    assert resolve_code("WON") == "WON"
    assert resolve_code("wind_power_onshore_capacity_factor") == "WON"
    with pytest.raises(ValueError):
        resolve_code("not_a_variable")


def test_fv2_rule():
    assert fv2_applies("WON", "bcc_csm2_mr", "ssp2_4_5") is True
    assert fv2_applies("WOF", "bcc_csm2_mr", "ssp2_4_5") is True
    assert fv2_applies("SPV", "bcc_csm2_mr", "ssp2_4_5") is False   # solar, not wind
    assert fv2_applies("WON", "ec_earth3", "ssp2_4_5") is False


def test_catalog_modelled_set_and_describe():
    modelled = {c for c, s in catalog.ENERGY_VARIABLES.items() if s["modelled"]}
    assert modelled == {"SPV", "WON", "WOF", "HRI", "HRO", "HPO"}
    d = catalog.describe()
    assert set(d["energy_variables"]) == set(catalog.ENERGY_VARIABLES)
    assert set(d["climate_models"]) == set(catalog.CLIMATE_MODELS)


def test_extra_variables_are_fetched():
    codes = study._required_codes(_proj(extra_variables=["CSP", "hydropower_reservoir_generation"]))
    assert "CSP" in codes and "HRG" in codes


def test_csp_requires_technology():
    cfg = _proj()["pecd"]
    with pytest.raises(ValueError):
        study._build_request("CSP", 2050, cfg)
    req = study._build_request("CSP", 2050, {**cfg, "csp_technology": "70"})
    assert req["spatial_resolution"] == ["p2on"]
