import numpy as np
import pytest
from supplyforge.create_pommes_craft_model import _get_input_data_file, DISPATCHABLE_TECH_DICT, INTERMITTENT_TECH_DICT


# Test case for create_model
def test_create_model():
    from supplyforge.create_pommes_craft_model import create_model
    country_code = "FR"
    reference_year = 2022
    capacities = _get_input_data_file(country_code, reference_year, "installed_capacities")
    availabilities = _get_input_data_file(country_code, reference_year, "availability")
    capacity_factors = _get_input_data_file(country_code, reference_year, "capacity_factors")
    hydro_inflows = _get_input_data_file(country_code, reference_year, "inflow")
    hydro_storage = _get_input_data_file(country_code, reference_year, "hydro_storage")

    model = create_model(country_code=country_code,
                         reference_year=reference_year,
                         model_year=2025)
    area = model.areas[country_code]
    assert area.name == country_code


    capacities = capacities.drop('index')
    tech_dict = DISPATCHABLE_TECH_DICT | INTERMITTENT_TECH_DICT
    for tech_name, plant_type in tech_dict.items():
        if plant_type in capacities.columns:
            assert tech_name in area.components.keys()
            file_capa = capacities[plant_type][0]
            model_capa_min = area.components[tech_name].power_capacity_min["power_capacity_min"][0]
            model_capa_max = area.components[tech_name].power_capacity_max["power_capacity_max"][0]
            np.testing.assert_equal(model_capa_min, file_capa)
            np.testing.assert_equal(model_capa_max, file_capa)

    assert model.year_invs == [2025]
    assert model.year_ops == [2025]
    assert model.year_decs == list(range(2025, 2025 + 80 + 5, 5))

    pass
