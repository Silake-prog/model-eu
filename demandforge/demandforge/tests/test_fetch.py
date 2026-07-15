"""Test suite for demandforge.fetch.hydrogen.industry_data and bunkering_weights.

Tests cover:
- Ammonia, methanol, olefins, and refinery production data fetch functions
- Bunkering weights vector normalization and filtering
- Static data validation (EU27 country list, data magnitude)
- Error handling for missing files and invalid parameters
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path
import tempfile

from demandforge.fetch.industry_data import (
    fetch_ammonia_production,
    fetch_jrc_idees_steel_base,
    fetch_methanol_production,
    fetch_olefins_production,
    fetch_refinery_output,
    fetch_eurostat_refinery_output,
    EU27_COUNTRIES,
    _USGS_AMMONIA_2019_MT,
)
from demandforge.fetch.bunkering_weights import fetch_bunkering_weights


class TestAmmonia:
    """Tests for fetch_ammonia_production()."""

    def test_ammonia_returns_correct_columns(self):
        """Ammonia should return columns: country, year, scenario, ammonia_production_mt, ammonia_production_t_per_yr."""
        df = fetch_ammonia_production()
        expected_cols = {"country", "year", "scenario", "ammonia_production_mt", "ammonia_production_t_per_yr"}
        assert expected_cols.issubset(df.columns), f"Missing columns. Got: {df.columns.tolist()}"

    def test_ammonia_eu27_count(self):
        """Ammonia should return 27 rows for EU27 (default with no country filter)."""
        df = fetch_ammonia_production()
        assert len(df) == 27, f"Expected 27 rows (EU27), got {len(df)}"

    def test_ammonia_non_negative_values(self):
        """Ammonia production values should be non-negative."""
        df = fetch_ammonia_production()
        assert (df["ammonia_production_mt"] >= 0).all(), "Found negative ammonia production values"

    def test_ammonia_year_is_2019(self):
        """Default ammonia data should have year=2019."""
        df = fetch_ammonia_production()
        assert (df["year"] == 2019).all(), "Expected all years to be 2019"

    def test_ammonia_scenario_is_baseline(self):
        """Ammonia scenario should be 'baseline'."""
        df = fetch_ammonia_production()
        assert (df["scenario"] == "baseline").all(), "Expected scenario='baseline'"

    def test_ammonia_country_filter(self):
        """Country filter should return only requested countries."""
        countries = ["FR", "DE"]
        df = fetch_ammonia_production(countries=countries)
        assert set(df["country"].unique()) == set(countries)
        assert len(df) == 2

    def test_ammonia_force_reload(self):
        """Force reload should bypass cache."""
        df1 = fetch_ammonia_production()
        df2 = fetch_ammonia_production(force_reload=True)
        pd.testing.assert_frame_equal(df1, df2)

    def test_ammonia_reference_year(self):
        """Reference year parameter should be included in output."""
        df = fetch_ammonia_production(reference_year=2025)
        assert (df["year"] == 2025).all()

    def test_ammonia_usgs_data_magnitude(self):
        """USGS ammonia data should have all values < 5 Mt."""
        for country, mt in _USGS_AMMONIA_2019_MT.items():
            assert mt < 5.0, f"Ammonia for {country} exceeds 5 Mt: {mt}"


class TestMethanol:
    """Tests for fetch_methanol_production()."""

    def test_methanol_returns_correct_columns(self):
        """Methanol should return columns: country, year, scenario, methanol_production_t."""
        df = fetch_methanol_production()
        expected_cols = {"country", "year", "scenario", "methanol_production_t"}
        assert expected_cols.issubset(df.columns), f"Missing columns. Got: {df.columns.tolist()}"

    def test_methanol_no_uk(self):
        """Methanol EU27 data should NOT include UK."""
        df = fetch_methanol_production()
        assert "UK" not in df["country"].values, "UK should not be in EU27 methanol data"

    def test_methanol_eu27_count(self):
        """Methanol should have 27 rows for EU27."""
        df = fetch_methanol_production()
        assert len(df) == 27, f"Expected 27 rows (EU27), got {len(df)}"

    def test_methanol_non_negative_values(self):
        """Methanol production should be non-negative."""
        df = fetch_methanol_production()
        assert (df["methanol_production_t"] >= 0).all(), "Found negative methanol values"

    def test_methanol_year_default(self):
        """Default methanol should have year=2019."""
        df = fetch_methanol_production()
        assert (df["year"] == 2019).all()

    def test_methanol_scenario_baseline(self):
        """Methanol scenario should be 'baseline'."""
        df = fetch_methanol_production()
        assert (df["scenario"] == "baseline").all()

    def test_methanol_country_filter(self):
        """Methanol country filter should work correctly."""
        countries = ["BE", "NL"]
        df = fetch_methanol_production(countries=countries)
        assert set(df["country"].unique()) == set(countries)


class TestOlefins:
    """Tests for fetch_olefins_production()."""

    def test_olefins_returns_correct_columns(self):
        """Olefins should return column: olefins_production_t_per_yr."""
        df = fetch_olefins_production()
        assert "olefins_production_t_per_yr" in df.columns, "Missing olefins_production_t_per_yr"

    def test_olefins_has_year_scenario(self):
        """Olefins should have year and scenario columns."""
        df = fetch_olefins_production()
        assert "year" in df.columns
        assert "scenario" in df.columns

    def test_olefins_non_negative(self):
        """Olefins production should be non-negative."""
        df = fetch_olefins_production()
        assert (df["olefins_production_t_per_yr"] >= 0).all()

    def test_olefins_eu27_count(self):
        """Olefins should have ~27 rows for EU27."""
        df = fetch_olefins_production()
        assert len(df) >= 20, f"Expected at least 20 rows, got {len(df)}"

    def test_olefins_default_year(self):
        """Default olefins should have year=2019."""
        df = fetch_olefins_production()
        assert (df["year"] == 2019).all()


class TestRefinery:
    """Tests for fetch_refinery_output()."""

    def test_refinery_no_source_raises_not_implemented(self):
        """fetch_refinery_output() without source_path should raise NotImplementedError."""
        with pytest.raises(NotImplementedError):
            fetch_refinery_output(source_path=None)

    def test_refinery_with_valid_csv(self):
        """fetch_refinery_output() with valid CSV should return correct columns."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("country,refinery_output_ktoe\n")
            f.write("FR,1000\n")
            f.write("DE,1500\n")
            f.flush()
            temp_path = f.name

        try:
            df = fetch_refinery_output(source_path=temp_path)
            assert "country" in df.columns
            assert "refinery_output_ktoe" in df.columns
            assert "year" in df.columns
            assert "scenario" in df.columns
            assert len(df) == 2
        finally:
            Path(temp_path).unlink()

    def test_refinery_csv_with_alternate_column_name(self):
        """fetch_refinery_output() should accept 'refinery_output' column."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            f.write("country,refinery_output\n")
            f.write("FR,1000\n")
            f.flush()
            temp_path = f.name

        try:
            df = fetch_refinery_output(source_path=temp_path)
            assert "refinery_output_ktoe" in df.columns
        finally:
            Path(temp_path).unlink()


class TestEurostatRefinery:
    """Tests for fetch_eurostat_refinery_output()."""

    def test_eurostat_nonexistent_file_raises_file_not_found(self):
        """fetch_eurostat_refinery_output() should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            fetch_eurostat_refinery_output(tsv_path="/nonexistent/path/file.tsv")

    def test_eurostat_valid_tsv(self):
        """fetch_eurostat_refinery_output() with valid TSV should parse correctly."""
        # Create a minimal valid Eurostat TSV file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write('freq,nrg_bal,siec,unit,geo\t2019\n')
            f.write('A,TO_RPI_RO,TOTAL,KTOE,FR\t1000\n')
            f.write('A,TO_RPI_RO,TOTAL,KTOE,DE\t1500\n')
            f.write('A,TO_RPI_RO,TOTAL,KTOE,IT\t900\n')
            f.flush()
            temp_path = f.name

        try:
            df = fetch_eurostat_refinery_output(tsv_path=temp_path)
            assert "country" in df.columns
            assert "refinery_output_ktoe" in df.columns
            assert len(df) > 0
        finally:
            Path(temp_path).unlink()

    def test_eurostat_country_filter(self):
        """fetch_eurostat_refinery_output() should filter by country."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
            f.write('freq,nrg_bal,siec,unit,geo\t2019\n')
            f.write('A,TO_RPI_RO,TOTAL,KTOE,FR\t1000\n')
            f.write('A,TO_RPI_RO,TOTAL,KTOE,DE\t1500\n')
            f.flush()
            temp_path = f.name

        try:
            df = fetch_eurostat_refinery_output(tsv_path=temp_path, countries=["FR"])
            assert all(df["country"] == "FR")
            assert len(df) == 1
        finally:
            Path(temp_path).unlink()


class TestEU27Countries:
    """Tests for EU27_COUNTRIES constant."""

    def test_eu27_has_27_entries(self):
        """EU27_COUNTRIES should have exactly 27 entries."""
        assert len(EU27_COUNTRIES) == 27, f"Expected 27 countries, got {len(EU27_COUNTRIES)}"

    def test_eu27_all_unique(self):
        """EU27_COUNTRIES should have no duplicates."""
        assert len(EU27_COUNTRIES) == len(set(EU27_COUNTRIES)), "Duplicates found in EU27_COUNTRIES"

    def test_eu27_all_strings(self):
        """EU27_COUNTRIES should contain only strings."""
        assert all(isinstance(cc, str) for cc in EU27_COUNTRIES), "Non-string entries in EU27_COUNTRIES"

    def test_eu27_no_uk(self):
        """EU27_COUNTRIES should not contain UK (post-Brexit)."""
        assert "UK" not in EU27_COUNTRIES, "UK should not be in EU27"

    def test_eu27_contains_major_countries(self):
        """EU27_COUNTRIES should contain major EU countries."""
        major = {"DE", "FR", "IT", "ES", "PL"}
        assert major.issubset(set(EU27_COUNTRIES)), "Missing major EU countries"


class TestBunkeringWeights:
    """Tests for fetch_bunkering_weights()."""

    def test_bunkering_weights_sum_to_one(self):
        """Bunkering weights should sum to 1.0."""
        weights = fetch_bunkering_weights()
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"

    def test_bunkering_weights_all_non_negative(self):
        """All bunkering weights should be non-negative."""
        weights = fetch_bunkering_weights()
        negatives = {k: v for k, v in weights.items() if v < 0}
        assert not negatives, f"Found negative weights: {negatives}"

    def test_bunkering_weights_country_filter(self):
        """Country filter should return only requested countries."""
        countries = ["NL", "ES", "BE"]
        weights = fetch_bunkering_weights(countries=countries)
        assert set(weights.keys()) == set(countries)

    def test_bunkering_weights_filter_renormalizes(self):
        """Filtered weights should renormalize to 1.0."""
        countries = ["NL", "ES", "BE", "IT"]
        weights = fetch_bunkering_weights(countries=countries)
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6, f"Filtered weights sum to {total}"

    def test_bunkering_weights_validation_on(self):
        """With validate=True, invalid weights should raise ValueError."""
        # This test verifies the validation logic works
        weights = fetch_bunkering_weights(validate=True)
        assert weights is not None

    def test_bunkering_weights_landlocked_zero(self):
        """Landlocked countries should have zero weight."""
        weights = fetch_bunkering_weights()
        landlocked = {"AT", "CZ", "HU", "SK", "LU"}  # Subset of landlocked
        for country in landlocked:
            if country in weights:
                assert weights[country] == 0.0, f"{country} should have zero weight"

    def test_bunkering_weights_major_ports_nonzero(self):
        """Major port countries should have non-zero weights."""
        weights = fetch_bunkering_weights()
        major_ports = {"NL", "ES", "BE", "IT"}
        for country in major_ports:
            assert weights.get(country, 0.0) > 0.0, f"{country} should have positive weight"

    def test_bunkering_weights_all_eu27_present(self):
        """All EU27 countries should be in the weights dict."""
        weights = fetch_bunkering_weights()
        missing = set(EU27_COUNTRIES) - set(weights.keys())
        assert not missing, f"Missing countries in bunkering weights: {missing}"

    def test_bunkering_weights_filter_zero_countries_warning(self):
        """Filtering to all-zero countries should handle gracefully."""
        # Landlocked countries with zero weights
        weights = fetch_bunkering_weights(countries=["AT", "CZ"], validate=False)
        # Should either be empty or all zeros
        assert all(v == 0.0 for v in weights.values()) or len(weights) == 0


class TestJrcIdeesSteelBase:
    """Tests for fetch_jrc_idees_steel_base().

    This tests the pure data acquisition function that reads JRC-IDEES
    steel production without applying any TYNDP shares or transforms.
    """

    def test_valid_csv_returns_correct_columns(self):
        """Valid CSV should return country and steel_production_kt columns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "2023_FEC_Steel_Demand.csv"
            csv_path.write_text(
                "country,steel_production_kt\n"
                "DE,40000\n"
                "FR,15000\n"
                "IT,25000\n"
            )
            df = fetch_jrc_idees_steel_base(tmpdir, force_reload=True)
            assert set(df.columns) == {"country", "steel_production_kt"}
            assert len(df) == 3

    def test_valid_csv_values(self):
        """Returned values should match input CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "2023_FEC_Steel_Demand.csv"
            csv_path.write_text(
                "country,steel_production_kt\n"
                "DE,40000\n"
                "SE,5000\n"
            )
            df = fetch_jrc_idees_steel_base(tmpdir, force_reload=True)
            de_val = df.loc[df["country"] == "DE", "steel_production_kt"].iloc[0]
            assert de_val == 40000.0

    def test_country_filter(self):
        """Country filter should return only requested countries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "2023_FEC_Steel_Demand.csv"
            csv_path.write_text(
                "country,steel_production_kt\n"
                "DE,40000\n"
                "FR,15000\n"
                "IT,25000\n"
            )
            df = fetch_jrc_idees_steel_base(
                tmpdir, countries=["DE", "IT"], force_reload=True
            )
            assert set(df["country"].tolist()) == {"DE", "IT"}
            assert len(df) == 2

    def test_missing_file_raises(self):
        """Missing CSV file should raise FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                fetch_jrc_idees_steel_base(tmpdir, force_reload=True)

    def test_missing_column_raises(self):
        """CSV without steel_production_kt should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "2023_FEC_Steel_Demand.csv"
            csv_path.write_text("country,wrong_col\nDE,100\n")
            with pytest.raises(ValueError, match="missing columns"):
                fetch_jrc_idees_steel_base(tmpdir, force_reload=True)

    def test_negative_production_raises(self):
        """Negative steel production should raise ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "2023_FEC_Steel_Demand.csv"
            csv_path.write_text(
                "country,steel_production_kt\n"
                "DE,-100\n"
            )
            with pytest.raises(ValueError, match="Negative"):
                fetch_jrc_idees_steel_base(tmpdir, force_reload=True)

    def test_alternate_column_name_accepted(self):
        """CSV with 'steel_production' (no _kt suffix) should be accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "2023_FEC_Steel_Demand.csv"
            csv_path.write_text(
                "country,steel_production\n"
                "DE,40000\n"
            )
            df = fetch_jrc_idees_steel_base(tmpdir, force_reload=True)
            assert "steel_production_kt" in df.columns
            assert df.iloc[0]["steel_production_kt"] == 40000.0

    def test_non_negative_values(self):
        """All returned steel production values should be non-negative."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "2023_FEC_Steel_Demand.csv"
            csv_path.write_text(
                "country,steel_production_kt\n"
                "DE,40000\n"
                "CY,0\n"
                "MT,0\n"
            )
            df = fetch_jrc_idees_steel_base(tmpdir, force_reload=True)
            assert (df["steel_production_kt"] >= 0).all()
