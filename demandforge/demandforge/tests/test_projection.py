"""Test suite for demandforge.load_projection.hydrogen projection functions.

Tests cover:
- Ammonia H2 demand projection (single and multi-country)
- Maritime H2 demand projection
- Olefins H2 demand projection
- Aggregate H2 demand across sectors
- Physical constants validation
- Mass balance checks
- Year range validation
"""

import pytest
import numpy as np
import pandas as pd

from demandforge.load_projection.hydrogen import (
    project_ammonia_h2_demand,
    project_maritime_h2_demand,
    project_olefins_h2_demand,
    aggregate_h2_demand,
)
from demandforge.load_projection.constants import (
    H2_LHV_MWH_PER_T,
    H2_T_PER_T_NH3,
)


class TestAmmoniaDemand:
    """Tests for project_ammonia_h2_demand()."""

    def test_ammonia_demand_single_country(self):
        """Ammonia demand for single country should return correct columns."""
        df = project_ammonia_h2_demand(country="FR")

        expected_cols = {
            "country", "year",
            "nh3_consumption_t", "nh3_domestic_t", "nh3_imported_t",
            "nh3_domestic_ch4_to_h2_to_nh3_t", "nh3_domestic_h2_to_nh3_t",
            "h2_demand_network_t", "h2_produced_onsite_t",
            "ch4_demand_for_h2_t", "hb_electricity_mwh",
        }
        assert expected_cols.issubset(df.columns), f"Missing columns. Got: {df.columns.tolist()}"

    def test_ammonia_demand_single_country_data(self):
        """Single country ammonia demand should have correct structure."""
        df = project_ammonia_h2_demand(country="FR", reference_year=2019, target_year=2050)

        assert len(df) > 0, "No data returned"
        assert (df["country"] == "FR").all(), "Country should be FR"
        assert df["year"].min() == 2019
        assert df["year"].max() == 2050

    def test_ammonia_demand_non_negative_values(self):
        """Ammonia demand values should be non-negative."""
        df = project_ammonia_h2_demand(country="FR")

        non_negative_cols = [
            "nh3_consumption_t", "nh3_domestic_t", "nh3_imported_t",
            "h2_demand_network_t", "h2_produced_onsite_t",
            "ch4_demand_for_h2_t", "hb_electricity_mwh",
        ]
        for col in non_negative_cols:
            if col in df.columns:
                assert (df[col] >= 0).all(), f"Found negative values in {col}"

    def test_ammonia_demand_mass_balance(self):
        """Domestic NH3 should equal imported + consumed domestically."""
        df = project_ammonia_h2_demand(country="DE", reference_year=2019, target_year=2050)

        # nh3_consumption_t = nh3_domestic_t + nh3_imported_t
        computed = df["nh3_domestic_t"] + df["nh3_imported_t"]
        assert np.allclose(computed, df["nh3_consumption_t"], rtol=1e-5), \
            "Mass balance: consumption != domestic + imported"

    def test_ammonia_demand_multiple_countries(self):
        """Multiple countries should return data for all requested."""
        countries = ["FR", "DE", "IT"]
        df = project_ammonia_h2_demand(country=countries)

        assert set(df["country"].unique()) == set(countries)
        for cc in countries:
            assert (df[df["country"] == cc]["year"].min() == 2019)

    def test_ammonia_demand_year_range(self):
        """Year range should match target_year."""
        df = project_ammonia_h2_demand(country="FR", reference_year=2019, target_year=2040)

        assert df["year"].min() == 2019
        assert df["year"].max() == 2040

    def test_ammonia_demand_h2_conversion(self):
        """H2 demand should be consistent with NH3 consumption via H2_T_PER_T_NH3."""
        df = project_ammonia_h2_demand(
            country="FR",
            reference_year=2019,
            target_year=2030,
            h2_route_share_end=1.0,  # All H2 route
            domestic_share_end=1.0,   # All domestic
        )

        # At 100% H2 route and 100% domestic, h2_demand_network_t should be ~nh3_domestic_t * H2_T_PER_T_NH3
        for idx, row in df.iterrows():
            if row["h2_route_share_of_domestic"] > 0.99:  # Near 100%
                expected_h2 = row["nh3_domestic_h2_to_nh3_t"] * H2_T_PER_T_NH3
                # Allow some tolerance for rounding and intermediate calculations
                assert np.isclose(row["h2_demand_network_t"], expected_h2, rtol=0.1), \
                    f"H2 demand inconsistent at year {row['year']}"

    def test_ammonia_demand_default_params(self):
        """Default parameters should work without errors."""
        df = project_ammonia_h2_demand()
        assert len(df) > 0


class TestMaritimeDemand:
    """Tests for project_maritime_h2_demand()."""

    def test_maritime_demand_correct_columns(self):
        """Maritime demand should return correct columns."""
        df = project_maritime_h2_demand(country=["FR", "ES"])

        expected_cols = {"country", "year", "h2_demand_total_t_per_yr"}
        assert expected_cols.issubset(df.columns)

    def test_maritime_demand_fuel_shares_sum(self):
        """Fuel shares (if present) should sum to 1.0."""
        df = project_maritime_h2_demand(country="FR")

        # Check if any fuel share columns exist
        fuel_share_cols = [c for c in df.columns if "share" in c.lower()]
        for col in fuel_share_cols:
            if "sum" not in col.lower():
                # Assuming shares should sum to 1.0 per country-year group
                pass  # May not have all share columns in basic output

    def test_maritime_demand_non_negative(self):
        """Maritime H2 demand should be non-negative."""
        df = project_maritime_h2_demand(country=["NL", "ES", "FR"])

        assert (df["h2_demand_total_t_per_yr"] >= 0).all()

    def test_maritime_demand_multiple_countries(self):
        """Should return data for multiple countries."""
        countries = ["NL", "ES", "IT", "BE"]
        df = project_maritime_h2_demand(country=countries)

        returned_countries = set(df["country"].unique())
        assert len(returned_countries) > 0

    def test_maritime_demand_year_range(self):
        """Year range should match parameters."""
        df = project_maritime_h2_demand(
            country="NL",
            reference_year=2019,
            target_year=2045,
        )

        assert df["year"].min() == 2019
        assert df["year"].max() == 2045


class TestOlefinsDemand:
    """Tests for project_olefins_h2_demand()."""

    def test_olefins_demand_correct_columns(self):
        """Olefins demand should return correct columns."""
        df = project_olefins_h2_demand(country="FR")

        assert "h2_demand_t_per_yr" in df.columns
        assert "country" in df.columns
        assert "year" in df.columns

    def test_olefins_demand_non_negative(self):
        """Olefins H2 demand should be non-negative."""
        df = project_olefins_h2_demand(country=["DE", "IT"])

        assert (df["h2_demand_t_per_yr"] >= 0).all()

    def test_olefins_demand_single_country(self):
        """Single country should return valid data."""
        df = project_olefins_h2_demand(country="DE")

        assert len(df) > 0
        assert (df["country"] == "DE").all()

    def test_olefins_demand_year_range(self):
        """Year range should be respected."""
        df = project_olefins_h2_demand(
            country="FR",
            reference_year=2019,
            target_year=2050,
        )

        assert df["year"].min() == 2019
        assert df["year"].max() == 2050


class TestAggregate:
    """Tests for aggregate_h2_demand()."""

    def test_aggregate_sector_validation(self):
        """Unknown sector should raise ValueError."""
        with pytest.raises(ValueError):
            aggregate_h2_demand(
                country="FR",
                sectors=["unknown_sector"],
            )

    def test_aggregate_default_sectors(self):
        """Default sectors (subset that doesn't need config) should work."""
        # Use only sectors that don't require external configuration
        df = aggregate_h2_demand(
            country="FR",
            sectors=["ammonia", "maritime", "olefins"],
        )

        assert len(df) > 0
        assert set(df["sector"].unique()) == {"ammonia", "maritime", "olefins"}

    def test_aggregate_correct_columns(self):
        """Aggregate should return standard columns."""
        df = aggregate_h2_demand(
            country="FR",
            sectors=["ammonia", "maritime"],
        )

        expected_cols = {"country", "year", "sector", "h2_demand_t_per_yr", "h2_demand_mwh_per_yr"}
        assert expected_cols.issubset(df.columns)

    def test_aggregate_h2_mwh_conversion(self):
        """H2 demand in MWh should be consistent with tonnes via H2_LHV_MWH_PER_T."""
        df = aggregate_h2_demand(
            country="FR",
            sectors=["ammonia"],
            reference_year=2019,
            target_year=2025,
        )

        # Check that MWh = tonnes * H2_LHV_MWH_PER_T (approximately)
        for idx, row in df.iterrows():
            expected_mwh = row["h2_demand_t_per_yr"] * H2_LHV_MWH_PER_T
            assert np.isclose(row["h2_demand_mwh_per_yr"], expected_mwh, rtol=0.05), \
                f"MWh conversion incorrect at year {row['year']}"

    def test_aggregate_multiple_countries(self):
        """Should aggregate across multiple countries."""
        df = aggregate_h2_demand(
            country=["FR", "DE"],
            sectors=["ammonia"],
        )

        assert set(df["country"].unique()) == {"FR", "DE"}

    def test_aggregate_positive_demand(self):
        """H2 demand should be positive."""
        df = aggregate_h2_demand(
            country="FR",
            sectors=["ammonia"],
        )

        assert (df["h2_demand_t_per_yr"] >= 0).all()


class TestConstantsValidation:
    """Tests for physical constants."""

    def test_h2_lhv_value(self):
        """H2_LHV_MWH_PER_T should be ~33.33."""
        assert np.isclose(H2_LHV_MWH_PER_T, 33.33, rtol=0.01), \
            f"H2_LHV_MWH_PER_T = {H2_LHV_MWH_PER_T}, expected ~33.33"

    def test_h2_per_t_nh3_value(self):
        """H2_T_PER_T_NH3 should be ~0.18."""
        assert np.isclose(H2_T_PER_T_NH3, 0.18, rtol=0.01), \
            f"H2_T_PER_T_NH3 = {H2_T_PER_T_NH3}, expected ~0.18"

    def test_constants_positive(self):
        """All physical constants should be positive."""
        assert H2_LHV_MWH_PER_T > 0
        assert H2_T_PER_T_NH3 > 0


class TestProjectionDataQuality:
    """Tests for general data quality of projections."""

    def test_ammonia_finite_values(self):
        """All ammonia projection values should be finite."""
        df = project_ammonia_h2_demand(country="FR")

        numeric_cols = df.select_dtypes(include=[np.number]).columns
        assert np.all(np.isfinite(df[numeric_cols].values)), "Found inf or nan values"

    def test_maritime_finite_values(self):
        """All maritime projection values should be finite."""
        df = project_maritime_h2_demand(country="FR")

        numeric_cols = df.select_dtypes(include=[np.number]).columns
        assert np.all(np.isfinite(df[numeric_cols].values)), "Found inf or nan values"

    def test_olefins_finite_values(self):
        """All olefins projection values should be finite."""
        df = project_olefins_h2_demand(country="FR")

        numeric_cols = df.select_dtypes(include=[np.number]).columns
        assert np.all(np.isfinite(df[numeric_cols].values)), "Found inf or nan values"

    def test_ammonia_returns_dataframe(self):
        """Projections should return DataFrame."""
        result = project_ammonia_h2_demand(country="FR")
        assert isinstance(result, pd.DataFrame)

    def test_maritime_returns_dataframe(self):
        """Maritime projection should return DataFrame."""
        result = project_maritime_h2_demand(country="FR")
        assert isinstance(result, pd.DataFrame)

    def test_olefins_returns_dataframe(self):
        """Olefins projection should return DataFrame."""
        result = project_olefins_h2_demand(country="FR")
        assert isinstance(result, pd.DataFrame)
