"""Tests for the scenario bundle management module (scenarios.py)."""
from __future__ import annotations

import pandas as pd
import pytest

from demandforge.load_projection.scenarios import (
    list_bundles,
    get_bundle_params,
    load_bundle,
    _load_registry,
    _SECTOR_ORDER,
    _STANDALONE_SECTORS,
)


class TestListBundles:
    """Tests for list_bundles()."""

    def test_list_bundles_returns_dict(self):
        """list_bundles() should return a dict of {name: description}."""
        result = list_bundles()
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_list_bundles_contains_central(self):
        """The 'central' bundle should always exist."""
        result = list_bundles()
        assert "central" in result

    def test_list_bundles_all_have_descriptions(self):
        """Every bundle should have a non-empty description string."""
        result = list_bundles()
        for name, desc in result.items():
            assert isinstance(desc, str), f"Bundle '{name}' description is not a string"
            assert len(desc) > 0, f"Bundle '{name}' has empty description"

    def test_list_bundles_expected_count(self):
        """We expect exactly 4 named bundles."""
        result = list_bundles()
        assert len(result) == 4, f"Expected 4 bundles, got {len(result)}: {list(result.keys())}"


class TestGetBundleParams:
    """Tests for get_bundle_params()."""

    def test_get_central_has_all_sectors(self):
        """The 'central' bundle should define params for all 6 sectors."""
        params = get_bundle_params("central")
        for sector in _SECTOR_ORDER:
            assert sector in params, f"Missing sector '{sector}' in central bundle"

    def test_get_bundle_strips_description(self):
        """get_bundle_params() should not include the 'description' key."""
        params = get_bundle_params("central")
        assert "description" not in params

    def test_get_unknown_bundle_raises_key_error(self):
        """Requesting a non-existent bundle should raise KeyError."""
        with pytest.raises(KeyError, match="Unknown scenario bundle"):
            get_bundle_params("nonexistent_bundle_xyz")

    def test_bundle_params_are_dicts(self):
        """Each sector's params should be a dict."""
        params = get_bundle_params("central")
        for sector, sector_params in params.items():
            assert isinstance(sector_params, dict), (
                f"Sector '{sector}' params should be dict, got {type(sector_params)}"
            )

    def test_ammonia_params_have_expected_keys(self):
        """Ammonia sector in central bundle should have key scenario params."""
        params = get_bundle_params("central")
        ammonia = params["ammonia"]
        expected = {"domestic_share_end", "h2_route_share_end", "decarb_start", "decarb_end"}
        assert expected.issubset(ammonia.keys()), (
            f"Missing ammonia keys: {expected - ammonia.keys()}"
        )

    def test_steel_h2_per_t_dri_is_positive(self):
        """h2_per_t_dri should be a positive float in every bundle."""
        for bundle_name in list_bundles():
            params = get_bundle_params(bundle_name)
            val = params["steel"]["h2_per_t_dri"]
            assert val > 0, f"h2_per_t_dri={val} in bundle '{bundle_name}'"

    def test_deepcopy_isolation(self):
        """Modifying returned params should not affect subsequent calls."""
        p1 = get_bundle_params("central")
        p1["ammonia"]["h2_route_share_end"] = 999.0
        p2 = get_bundle_params("central")
        assert p2["ammonia"]["h2_route_share_end"] != 999.0


class TestLoadBundleStandalone:
    """Tests for load_bundle() with standalone sectors (no infrastructure needed)."""

    def test_load_central_standalone_returns_dataframe(self):
        """load_bundle with standalone sectors should return a DataFrame."""
        df = load_bundle(
            "central",
            countries=["FR", "DE"],
            sectors=["ammonia", "maritime", "olefins"],
        )
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_load_central_has_expected_columns(self):
        """Output should have the standard 5-column schema."""
        df = load_bundle(
            "central",
            countries=["FR"],
            sectors=["ammonia"],
        )
        expected = {"country", "year", "sector", "h2_demand_t_per_yr", "h2_demand_mwh_per_yr"}
        assert expected.issubset(df.columns), f"Missing columns: {expected - set(df.columns)}"

    def test_load_central_ammonia_non_negative(self):
        """Ammonia H2 demand should be non-negative."""
        df = load_bundle("central", countries=["DE"], sectors=["ammonia"])
        assert (df["h2_demand_t_per_yr"] >= 0).all()

    def test_load_central_ammonia_de_magnitude(self):
        """DE ammonia H2 demand at 2050 should be in range [100k, 1M] t/yr."""
        df = load_bundle("central", countries=["DE"], sectors=["ammonia"])
        val_2050 = df[df["year"] == 2050]["h2_demand_t_per_yr"].sum()
        assert 100_000 < val_2050 < 1_000_000, (
            f"DE ammonia H2 at 2050 = {val_2050:,.0f} t/yr, expected 100k-1M range"
        )

    def test_load_high_h2_greater_than_low_h2(self):
        """High H2 bundle should produce more demand than low H2."""
        df_high = load_bundle(
            "high_h2",
            countries=["DE"],
            sectors=["ammonia", "maritime", "olefins"],
        )
        df_low = load_bundle(
            "low_h2",
            countries=["DE"],
            sectors=["ammonia", "maritime", "olefins"],
        )
        total_high = df_high[df_high["year"] == 2050]["h2_demand_t_per_yr"].sum()
        total_low = df_low[df_low["year"] == 2050]["h2_demand_t_per_yr"].sum()
        assert total_high > total_low, (
            f"High H2 ({total_high:,.0f}) should exceed Low H2 ({total_low:,.0f}) at 2050"
        )

    def test_load_bundle_auto_detects_sectors(self):
        """Without explicit sectors, standalone sectors should be auto-selected."""
        df = load_bundle("central", countries=["FR"])
        sectors_in_output = set(df["sector"].unique())
        assert _STANDALONE_SECTORS.issubset(sectors_in_output), (
            f"Expected standalone sectors {_STANDALONE_SECTORS}, "
            f"got {sectors_in_output}"
        )

    def test_load_unknown_bundle_raises(self):
        """Unknown bundle name should raise KeyError."""
        with pytest.raises(KeyError):
            load_bundle("does_not_exist", countries=["FR"], sectors=["ammonia"])

    def test_load_unknown_sector_raises(self):
        """Unknown sector name should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown sector"):
            load_bundle("central", countries=["FR"], sectors=["nuclear"])

    def test_sector_overrides_take_precedence(self):
        """Per-sector overrides should override YAML bundle values."""
        # Central bundle has h2_route_share_end=0.80 for ammonia.
        # Override to 0.0 → zero H2 demand.
        df_zero = load_bundle(
            "central",
            countries=["DE"],
            sectors=["ammonia"],
            ammonia={"h2_route_share_end": 0.0},
        )
        h2_2050 = df_zero[df_zero["year"] == 2050]["h2_demand_t_per_yr"].sum()
        assert h2_2050 == 0.0, f"Expected 0 with h2_route_share_end=0, got {h2_2050}"

    def test_mwh_equals_t_times_lhv(self):
        """MWh column should be exactly t × 33.33."""
        from demandforge.load_projection.constants import H2_LHV_MWH_PER_T
        df = load_bundle("central", countries=["FR"], sectors=["ammonia"])
        expected_mwh = df["h2_demand_t_per_yr"] * H2_LHV_MWH_PER_T
        diff = (df["h2_demand_mwh_per_yr"] - expected_mwh).abs().max()
        assert diff < 1e-6, f"MWh column inconsistency: max diff = {diff}"


class TestLoadRegistry:
    """Tests for _load_registry() internals."""

    def test_default_path_exists(self):
        """The default scenario_registry.yaml should exist."""
        from demandforge.load_projection.scenarios import _DEFAULT_REGISTRY_PATH
        assert _DEFAULT_REGISTRY_PATH.exists(), (
            f"Default registry not found at {_DEFAULT_REGISTRY_PATH}"
        )

    def test_registry_has_bundles_key(self):
        """Registry must have a top-level 'bundles' key."""
        registry = _load_registry()
        assert "bundles" in registry
