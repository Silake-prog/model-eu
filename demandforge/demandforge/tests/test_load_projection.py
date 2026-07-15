import pandas as pd
import pytest
from demandforge.load_projection import _get_combined_load_data, project_load_curve

def test_get_combined_load_data():
    """Tests the _get_combined_load_data function for correct data retrieval."""
    country = "DE"
    year = 2020

    # Call the function to get the combined load data
    df = _get_combined_load_data(country, year)

    # Check that the result is a pandas DataFrame
    assert isinstance(df, pd.DataFrame)

    # Check that the DataFrame is not empty
    assert not df.empty

    # Check for expected columns
    expected_columns = [
        'baseload',
        'winter_thermosensitive_load',
        'summer_thermosensitive_load',
    ]
    for col in expected_columns:
        assert col in df.columns

def test_project_load_curve_with_targets():
    """Tests load projection using explicit total energy targets."""
    df = project_load_curve(
        reference_year=2020,
        country="DE",
        target_year=2030,
        reference_ev_year=2026,
        total_baseload_energy_target=1000,
        total_winter_thermosensitive_energy_target=500,
        total_summer_thermosensitive_energy_target=200,
        total_ev_energy_target=100,
    )
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert abs(df['baseload_projected'].sum() - 1000) < 1e-6
    assert abs(df['winter_thermosensitive_projected'].sum() - 500) < 1e-6
    assert abs(df['summer_thermosensitive_projected'].sum() - 200) < 1e-6
    assert abs(df['ev_projected'].sum() - 100) < 1e-6

def test_project_load_curve_with_growth_rates():
    """Tests load projection using yearly growth rates."""
    df = project_load_curve(
        reference_year=2020,
        country="DE",
        target_year=2030,
        reference_ev_year=2026,
        baseload_yearly_growth_rate=0.02,
        winter_thermosensitive_yearly_growth_rate=0.03,
        summer_thermosensitive_yearly_growth_rate=0.01,
        ev_yearly_growth_rate=0.1,
    )
    assert isinstance(df, pd.DataFrame)
    assert not df.empty

def test_project_load_curve_mixed_inputs():
    """Tests load projection with a mix of energy targets and growth rates."""
    df = project_load_curve(
        reference_year=2020,
        country="DE",
        target_year=2030,
        reference_ev_year=2026,
        total_baseload_energy_target=1000,
        winter_thermosensitive_yearly_growth_rate=0.03,
        summer_thermosensitive_yearly_growth_rate=0.01,
        total_ev_energy_target=100,
    )
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert abs(df['baseload_projected'].sum() - 1000) < 1e-6
    assert abs(df['ev_projected'].sum() - 100) < 1e-6

def test_project_load_curve_missing_input():
    """Tests that a ValueError is raised if an energy projection input is missing."""
    with pytest.raises(ValueError):
        project_load_curve(
            reference_year=2020,
            country="DE",
            target_year=2030,
            reference_ev_year=2026,
            total_baseload_energy_target=1000,
            total_winter_thermosensitive_energy_target=500,
            total_summer_thermosensitive_energy_target=200,
            # Missing EV input
        )

def test_project_load_curve_missing_ev_column():
    """Tests that a ValueError is raised for a missing reference EV load column."""
    with pytest.raises(ValueError):
        project_load_curve(
            reference_year=2020,
            country="DE",
            target_year=2030,
            reference_ev_year=2050,  # A year that won't be in the data
            total_baseload_energy_target=1000,
            total_winter_thermosensitive_energy_target=500,
            total_summer_thermosensitive_energy_target=200,
            total_ev_energy_target=100,
        )
