"""Test suite for demandforge.process.hydrogen modules.

Tests cover:
- DRI fuel mix (CH4 and H2) shares summing to 1.0
- Blast furnace fuel mix (coal, biomass, H2) and CCS capture rates
- EAF scrap production trajectory (non-negative)
- Primary/EAF split with mass balance preservation
- DRI/BF split with mass balance preservation
- Steel H2 demand calculation (non-negative)
- Jet fuel reconstruction from refinery data
- Refinery unit allocation (H2 demand from CONCAWE)
- Linear ramp utility function
"""

import pytest
import numpy as np
import pandas as pd

from demandforge.process.steel import (
    build_dri_mix,
    build_bf_fuel_mix,
    compute_eaf_scrap_series,
    split_primary_from_eaf,
    compute_dri_bf_split,
    compute_steel_h2_demand,
    build_steel_base_table,
    validate_steel_base_table,
)
from demandforge.process.esaf import compute_jet_fossil
from demandforge.process.refinery import build_refinery_unit_allocation
from demandforge.process._utils import linear_ramp


class TestDRIMix:
    """Tests for build_dri_mix()."""

    def test_dri_mix_ch4_h2_sum_to_one(self):
        """DRI CH4 and H2 shares should sum to 1.0 for all years."""
        years = np.array([2020, 2025, 2030, 2040, 2050])
        df = build_dri_mix(years, ramp_start=2030, ch4_only_years=5, h2_ramp_years=15)

        share_sum = df["ch4_share"] + df["h2_share"]
        assert np.allclose(share_sum, 1.0), f"Shares do not sum to 1.0: {share_sum.values}"

    def test_dri_mix_ch4_only_phase(self):
        """Before ramp_start + ch4_only_years, CH4 share should be 1.0."""
        years = np.array([2020, 2025, 2030])
        df = build_dri_mix(years, ramp_start=2025, ch4_only_years=3, h2_ramp_years=10)

        # Before 2025 + 3 = 2028, CH4 should be ~1.0
        early = df[df["year"] < 2028]
        assert np.allclose(early["ch4_share"], 1.0), "CH4 not 1.0 in CH4-only phase"

    def test_dri_mix_h2_ramp(self):
        """H2 share should ramp up during the ramp phase."""
        years = np.arange(2030, 2051)
        df = build_dri_mix(years, ramp_start=2030, ch4_only_years=0, h2_ramp_years=20)

        # H2 should be increasing
        h2_shares = df["h2_share"].values
        assert h2_shares[0] < h2_shares[-1], "H2 should increase during ramp"
        assert h2_shares[-1] >= 0.9, "H2 should reach ~1.0 by end of ramp"

    def test_dri_mix_returns_dataframe(self):
        """build_dri_mix should return a DataFrame."""
        years = np.array([2020, 2030, 2050])
        result = build_dri_mix(years, ramp_start=2030, ch4_only_years=5, h2_ramp_years=15)
        assert isinstance(result, pd.DataFrame)
        assert "year" in result.columns
        assert "ch4_share" in result.columns
        assert "h2_share" in result.columns

    def test_dri_mix_correct_number_of_rows(self):
        """build_dri_mix should return one row per year."""
        years = np.array([2020, 2025, 2030, 2035, 2040, 2045, 2050])
        df = build_dri_mix(years, ramp_start=2030, ch4_only_years=5, h2_ramp_years=15)
        assert len(df) == len(years)


class TestBFMix:
    """Tests for build_bf_fuel_mix()."""

    def test_bf_mix_fuel_shares_sum_to_one(self):
        """Blast furnace fuel shares should sum to 1.0."""
        years = np.array([2020, 2025, 2030, 2040, 2050])
        df = build_bf_fuel_mix(
            years=years,
            ramp_start=2025,
            ramp_end=2040,
            biomass_max=0.3,
            h2_max=0.2,
            ccs_capture_rate_start=0.0,
            ccs_capture_rate_end=0.8,
            ccs_ramp_start=2030,
            ccs_ramp_end=2050,
        )

        fuel_sum = df["coal_share"] + df["biomass_share"] + df["h2_share"]
        assert np.allclose(fuel_sum, 1.0), f"Fuel shares do not sum to 1.0"

    def test_bf_mix_ccs_in_bounds(self):
        """CCS capture rate should be in [0, 1]."""
        years = np.array([2020, 2030, 2040, 2050])
        df = build_bf_fuel_mix(
            years=years,
            ramp_start=2025,
            ramp_end=2040,
            biomass_max=0.3,
            h2_max=0.2,
            ccs_capture_rate_start=0.0,
            ccs_capture_rate_end=0.8,
            ccs_ramp_start=2030,
            ccs_ramp_end=2050,
        )

        assert (df["ccs_capture_rate"] >= 0).all()
        assert (df["ccs_capture_rate"] <= 1.0).all()

    def test_bf_mix_biomass_ramp(self):
        """Biomass share should ramp up from 0 to biomass_max."""
        years = np.arange(2020, 2051)
        df = build_bf_fuel_mix(
            years=years,
            ramp_start=2025,
            ramp_end=2040,
            biomass_max=0.35,
            h2_max=0.15,
            ccs_capture_rate_start=0.0,
            ccs_capture_rate_end=0.7,
            ccs_ramp_start=2035,
            ccs_ramp_end=2050,
        )

        # Before ramp, biomass ~0
        before_ramp = df[df["year"] < 2025]
        assert np.allclose(before_ramp["biomass_share"], 0.0, atol=1e-2)

        # At ramp_end, biomass should be close to max
        at_ramp_end = df[df["year"] == 2040]
        assert np.allclose(at_ramp_end["biomass_share"], 0.35, atol=1e-2)

    def test_bf_mix_returns_dataframe(self):
        """build_bf_fuel_mix should return a DataFrame."""
        years = np.array([2020, 2030, 2050])
        result = build_bf_fuel_mix(
            years=years,
            ramp_start=2025,
            ramp_end=2040,
            biomass_max=0.3,
            h2_max=0.2,
            ccs_capture_rate_start=0.0,
            ccs_capture_rate_end=0.8,
            ccs_ramp_start=2030,
            ccs_ramp_end=2050,
        )
        assert isinstance(result, pd.DataFrame)
        assert "coal_share" in result.columns
        assert "biomass_share" in result.columns
        assert "h2_share" in result.columns
        assert "ccs_capture_rate" in result.columns


class TestEAFScrap:
    """Tests for compute_eaf_scrap_series()."""

    def test_eaf_scrap_non_negative(self):
        """EAF scrap production should be non-negative."""
        years = np.array([2020, 2025, 2030, 2040, 2050])
        steel_total = np.array([100, 100, 100, 95, 90]) * 1e6  # Mt/yr

        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=30e6,
            eaf_share_base=0.30,
            recycling_enabled=True,
            rec_target_2050=0.50,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        assert np.all(eaf >= 0), "Found negative EAF production"

    def test_eaf_scrap_recycling_disabled(self):
        """With recycling_enabled=False, EAF should stay at base level."""
        years = np.array([2020, 2025, 2030, 2040, 2050])
        steel_total = np.array([100, 100, 100, 95, 90]) * 1e6
        eaf_base = 30e6

        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=eaf_base,
            eaf_share_base=0.30,
            recycling_enabled=False,
            rec_target_2050=0.50,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        assert np.allclose(eaf, eaf_base), "EAF should be constant when recycling_enabled=False"

    def test_eaf_scrap_recycling_enabled_grows(self):
        """With recycling_enabled=True, EAF share should increase toward target."""
        years = np.array([2020, 2030, 2050])
        steel_total = np.array([100, 100, 100]) * 1e6

        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=30e6,
            eaf_share_base=0.30,
            recycling_enabled=True,
            rec_target_2050=0.55,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        # EAF at 2050 should be higher than at 2020
        assert eaf[2] >= eaf[0], "EAF should increase with recycling_enabled=True"

    def test_eaf_scrap_returns_ndarray(self):
        """compute_eaf_scrap_series should return ndarray."""
        years = np.array([2020, 2030, 2050])
        steel_total = np.array([100, 100, 100]) * 1e6

        result = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=30e6,
            eaf_share_base=0.30,
            recycling_enabled=True,
            rec_target_2050=0.50,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        assert isinstance(result, np.ndarray)
        assert len(result) == len(years)


class TestSplitPrimaryFromEAF:
    """Tests for split_primary_from_eaf()."""

    def test_split_primary_mass_balance(self):
        """EAF + Primary should equal Total."""
        total = np.array([100, 100, 100]) * 1e6
        eaf = np.array([30, 35, 40]) * 1e6

        eaf_out, primary = split_primary_from_eaf(total, eaf)

        assert np.allclose(eaf_out + primary, total), "Mass balance violated"

    def test_split_primary_non_negative(self):
        """Both EAF and primary should be non-negative."""
        total = np.array([100, 100, 100]) * 1e6
        eaf = np.array([30, 35, 40]) * 1e6

        eaf_out, primary = split_primary_from_eaf(total, eaf)

        assert np.all(eaf_out >= 0)
        assert np.all(primary >= 0)

    def test_split_primary_returns_tuple(self):
        """split_primary_from_eaf should return tuple of two arrays."""
        total = np.array([100, 100, 100]) * 1e6
        eaf = np.array([30, 35, 40]) * 1e6

        result = split_primary_from_eaf(total, eaf)

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], np.ndarray)
        assert isinstance(result[1], np.ndarray)

    def test_split_primary_length_mismatch_raises(self):
        """Mismatched array lengths should raise ValueError."""
        total = np.array([100, 100, 100]) * 1e6
        eaf = np.array([30, 35]) * 1e6

        with pytest.raises(ValueError):
            split_primary_from_eaf(total, eaf)

    def test_split_primary_eaf_exceeds_total_raises(self):
        """EAF exceeding total should raise ValueError."""
        total = np.array([100, 100, 100]) * 1e6
        eaf = np.array([110, 100, 100]) * 1e6  # First year exceeds total

        with pytest.raises(ValueError):
            split_primary_from_eaf(total, eaf)


class TestDRIBFSplit:
    """Tests for compute_dri_bf_split()."""

    def test_dri_bf_mass_balance(self):
        """DRI + BF should equal primary."""
        primary = np.array([70, 70, 70]) * 1e6
        dri_2019 = 10e6
        dri_shares = np.array([0.14, 0.16, 0.18])

        dri, bf = compute_dri_bf_split(primary, dri_2019, dri_shares)

        assert np.allclose(dri + bf, primary), "Mass balance violated"

    def test_dri_bf_non_negative(self):
        """Both DRI and BF should be non-negative."""
        primary = np.array([70, 70, 70]) * 1e6
        dri_2019 = 10e6
        dri_shares = np.array([0.14, 0.16, 0.18])

        dri, bf = compute_dri_bf_split(primary, dri_2019, dri_shares)

        assert np.all(dri >= 0)
        assert np.all(bf >= 0)

    def test_dri_bf_invalid_dri_2019_raises(self):
        """Negative dri_2019 should raise ValueError."""
        primary = np.array([70, 70, 70]) * 1e6

        with pytest.raises(ValueError):
            compute_dri_bf_split(primary, -1.0, np.array([0.14, 0.16, 0.18]))

    def test_dri_bf_invalid_shares_raises(self):
        """DRI shares outside [0, 1] should raise ValueError."""
        primary = np.array([70, 70, 70]) * 1e6
        dri_2019 = 10e6

        with pytest.raises(ValueError):
            compute_dri_bf_split(primary, dri_2019, np.array([0.14, 1.5, 0.18]))

    def test_dri_bf_returns_tuple(self):
        """compute_dri_bf_split should return tuple of two arrays."""
        primary = np.array([70, 70, 70]) * 1e6
        dri_2019 = 10e6
        dri_shares = np.array([0.14, 0.16, 0.18])

        result = compute_dri_bf_split(primary, dri_2019, dri_shares)

        assert isinstance(result, tuple)
        assert len(result) == 2


class TestSteelH2Demand:
    """Tests for compute_steel_h2_demand()."""

    def test_steel_h2_demand_non_negative(self):
        """H2 demand should be non-negative."""
        dri_h2 = np.array([10, 15, 20]) * 1e6  # t/yr
        h2_per_t_dri = 1.6

        h2_demand = compute_steel_h2_demand(dri_h2, h2_per_t_dri)

        assert np.all(h2_demand >= 0), "Found negative H2 demand"

    def test_steel_h2_demand_proportional(self):
        """H2 demand should be proportional to DRI production."""
        dri_h2 = np.array([10, 20]) * 1e6
        h2_per_t_dri = 1.6

        h2_demand = compute_steel_h2_demand(dri_h2, h2_per_t_dri)

        # Double DRI should double H2
        ratio = h2_demand[1] / h2_demand[0]
        assert np.isclose(ratio, 2.0), f"Expected ratio ~2.0, got {ratio}"

    def test_steel_h2_demand_negative_h2_per_t_raises(self):
        """Negative h2_per_t_dri should raise ValueError."""
        dri_h2 = np.array([10, 15, 20]) * 1e6

        with pytest.raises(ValueError):
            compute_steel_h2_demand(dri_h2, -1.0)

    def test_steel_h2_demand_returns_ndarray(self):
        """compute_steel_h2_demand should return ndarray."""
        dri_h2 = np.array([10, 15, 20]) * 1e6
        h2_per_t_dri = 1.6

        result = compute_steel_h2_demand(dri_h2, h2_per_t_dri)

        assert isinstance(result, np.ndarray)
        assert len(result) == len(dri_h2)


class TestJetFossil:
    """Tests for compute_jet_fossil()."""

    def test_jet_fossil_type_check(self):
        """compute_jet_fossil should raise TypeError for non-DataFrame input."""
        with pytest.raises(TypeError):
            compute_jet_fossil(
                df_refinery=[],  # Not a DataFrame
                jet_unit_names={"kero_hydrotreater": "Kero Hydrotreater"},
                jet_yields={},
            )

    def test_jet_fossil_missing_columns(self):
        """Missing required columns should raise ValueError."""
        df = pd.DataFrame({
            "country": ["FR"],
            "year": [2019],
            # Missing "unit" and "unit_feed_t_per_yr"
        })

        with pytest.raises(ValueError):
            compute_jet_fossil(
                df_refinery=df,
                jet_unit_names={"kero_hydrotreater": "Kero Hydrotreater"},
                jet_yields={},
            )

    def test_jet_fossil_prefer_kero_hydrotreater(self):
        """prefer_kero_hydrotreater method should use Kero Hydrotreater feed."""
        df = pd.DataFrame({
            "country": ["FR", "FR"],
            "year": [2019, 2019],
            "unit": ["Kero Hydrotreater", "VGO Hydrocracker"],
            "unit_feed_t_per_yr": [5000, 3000],
        })

        result = compute_jet_fossil(
            df_refinery=df,
            jet_unit_names={
                "kero_hydrotreater": "Kero Hydrotreater",
                "vgo_hydrocracker": "VGO Hydrocracker",
            },
            jet_yields={"vgo_hydrocracker": 0.2},
            method="prefer_kero_hydrotreater",
        )

        assert len(result) > 0
        assert "fossil_jet_consumed_t_per_yr" in result.columns
        # Kero Hydrotreater feed should be 5000
        assert result["fossil_jet_consumed_t_per_yr"].iloc[0] == 5000.0

    def test_jet_fossil_fallback_hydrocracker(self):
        """fallback_hydrocracker method should use weighted sum."""
        df = pd.DataFrame({
            "country": ["FR", "FR"],
            "year": [2019, 2019],
            "unit": ["VGO Hydrocracker", "Residue Hydrocracker"],
            "unit_feed_t_per_yr": [1000, 2000],
        })

        result = compute_jet_fossil(
            df_refinery=df,
            jet_unit_names={
                "vgo_hydrocracker": "VGO Hydrocracker",
                "residue_hydrocracker": "Residue Hydrocracker",
            },
            jet_yields={
                "vgo_hydrocracker": 0.20,
                "residue_hydrocracker": 0.10,
            },
            method="fallback_hydrocracker",
        )

        assert len(result) > 0
        # Expected: 1000 * 0.20 + 2000 * 0.10 = 200 + 200 = 400
        assert result["fossil_jet_consumed_t_per_yr"].iloc[0] == 400.0

    def test_jet_fossil_invalid_method_raises(self):
        """Invalid method should raise ValueError."""
        df = pd.DataFrame({
            "country": ["FR"],
            "year": [2019],
            "unit": ["Kero Hydrotreater"],
            "unit_feed_t_per_yr": [5000],
        })

        with pytest.raises(ValueError):
            compute_jet_fossil(
                df_refinery=df,
                jet_unit_names={"kero_hydrotreater": "Kero Hydrotreater"},
                jet_yields={},
                method="invalid_method",
            )


class TestRefineryUnitAllocation:
    """Tests for build_refinery_unit_allocation()."""

    def test_refinery_unit_correct_columns(self):
        """Result should have all required columns."""
        units_config = {
            "Hydrocracker": {
                "spec_cons_wt": 2.5,
                "utilized_capacity_mton": {2020: 10, 2050: 8},
            }
        }
        years = np.array([2020, 2030, 2050])

        result = build_refinery_unit_allocation(
            base_output_t_per_yr=1e6,
            units_config=units_config,
            years=years,
        )

        required_cols = {"year", "unit", "unit_capacity_share", "unit_feed_t_per_yr",
                         "spec_cons_wt", "h2_demand_t_per_yr", "refinery_output_total_t_per_yr",
                         "level_factor"}
        assert required_cols.issubset(result.columns)

    def test_refinery_unit_empty_config_raises(self):
        """Empty units_config should raise ValueError."""
        years = np.array([2020, 2030, 2050])

        with pytest.raises(ValueError):
            build_refinery_unit_allocation(
                base_output_t_per_yr=1e6,
                units_config={},
                years=years,
            )

    def test_refinery_unit_negative_base_output_raises(self):
        """Negative or zero base_output should raise ValueError."""
        units_config = {
            "Hydrocracker": {
                "spec_cons_wt": 2.5,
                "utilized_capacity_mton": {2020: 10},
            }
        }
        years = np.array([2020])

        with pytest.raises(ValueError):
            build_refinery_unit_allocation(
                base_output_t_per_yr=-1000,
                units_config=units_config,
                years=years,
            )

    def test_refinery_unit_empty_years_raises(self):
        """Empty years array should raise ValueError."""
        units_config = {
            "Hydrocracker": {
                "spec_cons_wt": 2.5,
                "utilized_capacity_mton": {2020: 10},
            }
        }

        with pytest.raises(ValueError):
            build_refinery_unit_allocation(
                base_output_t_per_yr=1e6,
                units_config=units_config,
                years=np.array([]),
            )

    def test_refinery_unit_h2_demand_non_negative(self):
        """H2 demand should be non-negative."""
        units_config = {
            "Hydrocracker": {
                "spec_cons_wt": 2.5,
                "utilized_capacity_mton": {2020: 10, 2050: 8},
            }
        }
        years = np.array([2020, 2030, 2050])

        result = build_refinery_unit_allocation(
            base_output_t_per_yr=1e6,
            units_config=units_config,
            years=years,
        )

        assert (result["h2_demand_t_per_yr"] >= 0).all()

    def test_refinery_unit_multiple_units(self):
        """Should handle multiple refinery units."""
        units_config = {
            "Hydrocracker": {
                "spec_cons_wt": 2.5,
                "utilized_capacity_mton": {2020: 10, 2050: 8},
            },
            "Reformer": {
                "spec_cons_wt": 1.5,
                "utilized_capacity_mton": {2020: 5, 2050: 4},
            },
        }
        years = np.array([2020, 2030, 2050])

        result = build_refinery_unit_allocation(
            base_output_t_per_yr=1e6,
            units_config=units_config,
            years=years,
        )

        # Should have rows for both units
        assert "Hydrocracker" in result["unit"].values
        assert "Reformer" in result["unit"].values


class TestLinearRamp:
    """Tests for linear_ramp() utility function."""

    def test_linear_ramp_boundary_values(self):
        """Ramp should return start_value before start_year, end_value after end_year."""
        years = np.array([2010, 2020, 2030, 2040, 2050])
        result = linear_ramp(years, start_year=2020, end_year=2040, start_value=0.0, end_value=1.0)

        assert result[0] == 0.0, "Before start_year should be start_value"
        assert result[-1] == 1.0, "After end_year should be end_value"

    def test_linear_ramp_interpolation(self):
        """Ramp should interpolate linearly between years."""
        years = np.array([2020, 2030, 2040])
        result = linear_ramp(years, start_year=2020, end_year=2040, start_value=0.0, end_value=1.0)

        # At midpoint, should be 0.5
        assert np.isclose(result[1], 0.5), f"Midpoint should be 0.5, got {result[1]}"

    def test_linear_ramp_empty_years_raises(self):
        """Empty years array should raise ValueError."""
        with pytest.raises(ValueError):
            linear_ramp(np.array([]), 2020, 2040, 0.0, 1.0)

    def test_linear_ramp_returns_ndarray(self):
        """linear_ramp should return ndarray."""
        years = np.array([2020, 2030, 2040])
        result = linear_ramp(years, start_year=2020, end_year=2040, start_value=0.0, end_value=1.0)

        assert isinstance(result, np.ndarray)
        assert len(result) == len(years)

    def test_linear_ramp_custom_values(self):
        """Ramp should respect custom start and end values."""
        years = np.array([2020, 2040])
        result = linear_ramp(years, start_year=2020, end_year=2040, start_value=10.0, end_value=20.0)

        assert result[0] == 10.0
        assert result[1] == 20.0


class TestEAFFloorConstraint:
    """Tests for the EAF floor constraint in compute_eaf_scrap_series.

    The floor constraint ensures that countries already above the global
    recycling target never have their EAF share forced downward:

        effective_target = max(rec_target_2050, eaf_share_base)
    """

    def test_eaf_floor_base_above_target(self):
        """Country with base share 60% should NOT drop to 50% target."""
        years = np.array([2020, 2030, 2040, 2050])
        steel_total = np.array([100, 100, 100, 100]) * 1e6

        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=60e6,
            eaf_share_base=0.60,
            recycling_enabled=True,
            rec_target_2050=0.50,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        # EAF share at 2050 should be 60%, not 50%
        eaf_share_2050 = eaf[-1] / steel_total[-1]
        assert np.isclose(eaf_share_2050, 0.60, atol=1e-6), (
            f"EAF share at 2050 should be 0.60 (floor), got {eaf_share_2050:.4f}"
        )

    def test_eaf_floor_base_below_target(self):
        """Country with base share 30% should ramp UP to 50% target."""
        years = np.array([2020, 2030, 2040, 2050])
        steel_total = np.array([100, 100, 100, 100]) * 1e6

        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=30e6,
            eaf_share_base=0.30,
            recycling_enabled=True,
            rec_target_2050=0.50,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        eaf_share_2050 = eaf[-1] / steel_total[-1]
        assert np.isclose(eaf_share_2050, 0.50, atol=1e-6), (
            f"EAF share at 2050 should be 0.50 (target), got {eaf_share_2050:.4f}"
        )

    def test_eaf_floor_never_decreases(self):
        """EAF share should never decrease over time when floor is active."""
        years = np.arange(2020, 2051)
        steel_total = np.full(len(years), 100e6)

        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=60e6,
            eaf_share_base=0.60,
            recycling_enabled=True,
            rec_target_2050=0.50,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        eaf_shares = eaf / steel_total
        # All shares should be constant at 0.60
        assert np.allclose(eaf_shares, 0.60, atol=1e-9), (
            f"EAF shares should remain constant at 0.60, got range "
            f"[{eaf_shares.min():.4f}, {eaf_shares.max():.4f}]"
        )

    def test_eaf_floor_equal_base_and_target(self):
        """When base share equals target, share should stay constant."""
        years = np.array([2020, 2035, 2050])
        steel_total = np.array([100, 100, 100]) * 1e6

        eaf = compute_eaf_scrap_series(
            years=years,
            steel_total=steel_total,
            eaf_base=50e6,
            eaf_share_base=0.50,
            recycling_enabled=True,
            rec_target_2050=0.50,
            rec_ramp_start=2020,
            rec_ramp_end=2050,
        )

        eaf_shares = eaf / steel_total
        assert np.allclose(eaf_shares, 0.50, atol=1e-9)


class TestBuildSteelBaseTable:
    """Tests for build_steel_base_table().

    Validates the "initial CSV generation" stage that combines JRC-IDEES
    raw production with explicit EAF (secondary steel) shares and TYNDP
    technology route shares for the primary fraction.

    Key semantic: EAF is explicit secondary steel.  TYNDP BF/DRI shares
    partition *primary* steel only (sum ≈ 1.0).
    """

    @pytest.fixture
    def sample_jrc_df(self):
        """Minimal JRC-IDEES steel production DataFrame."""
        return pd.DataFrame({
            "country": ["DE", "FR", "IT", "SE"],
            "steel_production_kt": [40000.0, 15000.0, 25000.0, 5000.0],
        })

    @pytest.fixture
    def sample_tyndp_df(self):
        """TYNDP shares DataFrame — shares of PRIMARY steel (sum ≈ 1.0)."""
        return pd.DataFrame({
            "country": ["DE", "FR", "IT", "SE"],
            "industry_steel_blastfurnace_bof_share": [0.90, 0.95, 0.80, 0.80],
            "industry_steel_dri_network_gas_share": [0.10, 0.05, 0.20, 0.20],
            "industry_steel_dri_hydrogen_share": [0.00, 0.00, 0.00, 0.00],
        })

    @pytest.fixture
    def sample_eaf_shares(self):
        """Explicit EAF shares of total crude steel by country."""
        return {"DE": 0.30, "FR": 0.37, "IT": 0.82, "SE": 0.37}

    def test_base_table_output_columns(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """Output should contain all expected columns including primary."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)
        expected_cols = {
            "country", "year",
            "crude_steel_production_t_per_yr",
            "primary_production_t_per_yr",
            "bf_bof_production_t_per_yr",
            "dri_ch4_production_t_per_yr",
            "dri_h2_production_t_per_yr",
            "eaf_production_t_per_yr",
            "blastfurnace_bof_share",
            "dri_natural_gas_share",
            "dri_hydrogen_share",
            "eaf_share",
        }
        assert expected_cols.issubset(result.columns)

    def test_base_table_row_count(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """One row per country."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)
        assert len(result) == 4

    def test_base_table_total_mass_balance(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """S = EAF + BF + DRI_CH4 + DRI_H2 for every country."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)

        route_sum = (
            result["eaf_production_t_per_yr"]
            + result["bf_bof_production_t_per_yr"]
            + result["dri_ch4_production_t_per_yr"]
            + result["dri_h2_production_t_per_yr"]
        )
        np.testing.assert_allclose(
            route_sum.values,
            result["crude_steel_production_t_per_yr"].values,
            atol=1.0,
        )

    def test_base_table_primary_mass_balance(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """primary = BF + DRI_CH4 + DRI_H2."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)

        primary_sum = (
            result["bf_bof_production_t_per_yr"]
            + result["dri_ch4_production_t_per_yr"]
            + result["dri_h2_production_t_per_yr"]
        )
        np.testing.assert_allclose(
            primary_sum.values,
            result["primary_production_t_per_yr"].values,
            atol=1.0,
        )

    def test_base_table_eaf_is_explicit(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """EAF share should match the explicit input, not be a residual."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)

        de_row = result[result["country"] == "DE"].iloc[0]
        assert np.isclose(de_row["eaf_share"], 0.30, atol=1e-6)

        it_row = result[result["country"] == "IT"].iloc[0]
        assert np.isclose(it_row["eaf_share"], 0.82, atol=1e-6)

    def test_base_table_bf_applies_to_primary(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """BF production = primary × bf_share, NOT total × bf_share."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)

        de_row = result[result["country"] == "DE"].iloc[0]
        total = 40000.0 * 1000
        primary = total * (1.0 - 0.30)  # 70% primary
        expected_bf = primary * 0.90     # 90% of primary is BF
        assert np.isclose(de_row["bf_bof_production_t_per_yr"], expected_bf, rtol=1e-6)

    def test_base_table_kt_to_t_conversion(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """crude_steel should be steel_production_kt * 1000."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)

        de_row = result[result["country"] == "DE"].iloc[0]
        assert np.isclose(de_row["crude_steel_production_t_per_yr"], 40000.0 * 1000)

    def test_base_table_non_negative(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """All production volumes should be non-negative."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares)

        for col in ["crude_steel_production_t_per_yr", "primary_production_t_per_yr",
                     "bf_bof_production_t_per_yr", "dri_ch4_production_t_per_yr",
                     "dri_h2_production_t_per_yr", "eaf_production_t_per_yr"]:
            assert (result[col] >= -1e-6).all(), f"Negative values in {col}"

    def test_base_table_missing_jrc_column_raises(self, sample_tyndp_df, sample_eaf_shares):
        """Missing 'steel_production_kt' in JRC should raise ValueError."""
        bad_jrc = pd.DataFrame({"country": ["DE"], "wrong_col": [1000]})

        with pytest.raises(ValueError, match="missing required columns"):
            build_steel_base_table(bad_jrc, sample_tyndp_df, eaf_shares=sample_eaf_shares)

    def test_base_table_missing_tyndp_column_raises(self, sample_jrc_df, sample_eaf_shares):
        """Missing share column in TYNDP should raise ValueError."""
        bad_tyndp = pd.DataFrame({
            "country": ["DE"],
            "industry_steel_blastfurnace_bof_share": [0.70],
        })

        with pytest.raises(ValueError, match="missing required columns"):
            build_steel_base_table(sample_jrc_df, bad_tyndp, eaf_shares=sample_eaf_shares)

    def test_base_table_reference_year(self, sample_jrc_df, sample_tyndp_df, sample_eaf_shares):
        """Reference year should appear in output."""
        result = build_steel_base_table(
            sample_jrc_df, sample_tyndp_df, eaf_shares=sample_eaf_shares, reference_year=2025
        )
        assert (result["year"] == 2025).all()

    def test_base_table_percentage_shares_raise(self):
        """Shares in percentage form (0-100) should raise ValueError."""
        jrc_df = pd.DataFrame({
            "country": ["DE"],
            "steel_production_kt": [40000.0],
        })
        tyndp_df = pd.DataFrame({
            "country": ["DE"],
            "industry_steel_blastfurnace_bof_share": [70.0],
            "industry_steel_dri_network_gas_share": [5.0],
            "industry_steel_dri_hydrogen_share": [0.0],
        })

        with pytest.raises(ValueError, match="out of.*0.*1"):
            build_steel_base_table(jrc_df, tyndp_df, eaf_shares={"DE": 0.30})

    def test_base_table_default_eaf_shares(self, sample_jrc_df, sample_tyndp_df):
        """When eaf_shares=None, should fall back to _EAF_SHARE_2019."""
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=None)
        # Should not raise; _EAF_SHARE_2019 provides defaults for DE, FR, IT, SE
        assert len(result) == 4
        # DE should have EAF share from _EAF_SHARE_2019
        de_row = result[result["country"] == "DE"].iloc[0]
        assert de_row["eaf_share"] > 0  # DE has some EAF

    def test_base_table_eaf_shares_as_dataframe(self, sample_jrc_df, sample_tyndp_df):
        """eaf_shares can be a DataFrame with country and eaf_share columns."""
        eaf_df = pd.DataFrame({
            "country": ["DE", "FR", "IT", "SE"],
            "eaf_share": [0.30, 0.37, 0.82, 0.37],
        })
        result = build_steel_base_table(sample_jrc_df, sample_tyndp_df, eaf_shares=eaf_df)
        de_row = result[result["country"] == "DE"].iloc[0]
        assert np.isclose(de_row["eaf_share"], 0.30, atol=1e-6)

    def test_base_table_renormalization(self):
        """Primary shares not summing to 1.0 should be renormalized."""
        jrc_df = pd.DataFrame({"country": ["DE"], "steel_production_kt": [40000.0]})
        tyndp_df = pd.DataFrame({
            "country": ["DE"],
            "industry_steel_blastfurnace_bof_share": [0.80],
            "industry_steel_dri_network_gas_share": [0.10],
            "industry_steel_dri_hydrogen_share": [0.00],
            # Sum = 0.90, will be renormalized to 1.0
        })
        result = build_steel_base_table(jrc_df, tyndp_df, eaf_shares={"DE": 0.30})
        de = result.iloc[0]
        # After renorm: bf=0.80/0.90≈0.889, dri_ch4=0.10/0.90≈0.111
        assert np.isclose(de["blastfurnace_bof_share"], 0.80 / 0.90, atol=1e-3)
        assert np.isclose(de["dri_natural_gas_share"], 0.10 / 0.90, atol=1e-3)


class TestValidateSteelBaseTable:
    """Tests for validate_steel_base_table()."""

    def test_valid_table_passes(self):
        """A well-formed table should pass validation."""
        df = pd.DataFrame({
            "country": ["DE", "FR"],
            "crude_steel_production_t_per_yr": [40e6, 15e6],
            "primary_production_t_per_yr": [28e6, 9.45e6],
            "bf_bof_production_t_per_yr": [25.2e6, 8.97e6],
            "dri_ch4_production_t_per_yr": [2.8e6, 0.48e6],
            "dri_h2_production_t_per_yr": [0.0, 0.0],
            "eaf_production_t_per_yr": [12e6, 5.55e6],
        })
        # Should not raise
        validate_steel_base_table(df)

    def test_valid_table_without_primary_passes(self):
        """Table without primary_production_t_per_yr should still pass."""
        df = pd.DataFrame({
            "country": ["DE"],
            "crude_steel_production_t_per_yr": [40e6],
            "bf_bof_production_t_per_yr": [25e6],
            "dri_ch4_production_t_per_yr": [3e6],
            "dri_h2_production_t_per_yr": [0.0],
            "eaf_production_t_per_yr": [12e6],
        })
        validate_steel_base_table(df)

    def test_missing_column_raises(self):
        """Missing required column should raise ValueError."""
        df = pd.DataFrame({
            "country": ["DE"],
            "crude_steel_production_t_per_yr": [40e6],
        })

        with pytest.raises(ValueError, match="Missing columns"):
            validate_steel_base_table(df)

    def test_duplicate_country_raises(self):
        """Duplicate country should raise ValueError."""
        df = pd.DataFrame({
            "country": ["DE", "DE"],
            "crude_steel_production_t_per_yr": [40e6, 40e6],
            "bf_bof_production_t_per_yr": [28e6, 28e6],
            "dri_ch4_production_t_per_yr": [2e6, 2e6],
            "dri_h2_production_t_per_yr": [0.0, 0.0],
            "eaf_production_t_per_yr": [10e6, 10e6],
        })

        with pytest.raises(ValueError, match="Duplicate"):
            validate_steel_base_table(df)

    def test_negative_production_raises(self):
        """Negative production values should raise ValueError."""
        df = pd.DataFrame({
            "country": ["DE"],
            "crude_steel_production_t_per_yr": [40e6],
            "bf_bof_production_t_per_yr": [-1e6],
            "dri_ch4_production_t_per_yr": [2e6],
            "dri_h2_production_t_per_yr": [0.0],
            "eaf_production_t_per_yr": [39e6],
        })

        with pytest.raises(ValueError, match="Negative"):
            validate_steel_base_table(df)

    def test_primary_mass_balance_violation_raises(self):
        """Primary col inconsistent with primary routes should raise."""
        # Total mass balance OK: 12+25+3+0 = 40M
        # But primary col says 30M while BF+DRI sum to 28M
        df = pd.DataFrame({
            "country": ["DE"],
            "crude_steel_production_t_per_yr": [40e6],
            "primary_production_t_per_yr": [30e6],      # Wrong: should be 28M
            "bf_bof_production_t_per_yr": [25e6],
            "dri_ch4_production_t_per_yr": [3e6],
            "dri_h2_production_t_per_yr": [0.0],
            "eaf_production_t_per_yr": [12e6],
        })

        with pytest.raises(ValueError, match="Primary mass balance"):
            validate_steel_base_table(df)

    def test_mass_balance_violation_raises(self):
        """Routes not summing to total should raise ValueError."""
        df = pd.DataFrame({
            "country": ["DE"],
            "crude_steel_production_t_per_yr": [40e6],
            "bf_bof_production_t_per_yr": [28e6],
            "dri_ch4_production_t_per_yr": [2e6],
            "dri_h2_production_t_per_yr": [0.0],
            "eaf_production_t_per_yr": [5e6],  # Sum = 35M != 40M
        })

        with pytest.raises(ValueError, match="Mass balance"):
            validate_steel_base_table(df)
