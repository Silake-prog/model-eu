"""Tests for ERA5 GWA bias-correction config resolution + scaling math (offline)."""
import logging

import numpy as np
import pytest

from supplyforge.process.era5.bias_correction import _scaling_ratio, resolve_bias_correction


def test_resolve_defaults_to_gwa2():
    assert resolve_bias_correction({}) == "gwa2"
    assert resolve_bias_correction({"era5": {}}) == "gwa2"
    assert resolve_bias_correction({"era5": {"bias_correction": "GWA2"}}) == "gwa2"   # case-insensitive


def test_resolve_none_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_bias_correction({"era5": {"bias_correction": "none"}}) == "none"
    assert any("uncorrected" in r.message.lower() for r in caplog.records)


def test_resolve_invalid_raises():
    with pytest.raises(ValueError):
        resolve_bias_correction({"era5": {"bias_correction": "gwa9"}})


def test_scaling_ratio_basic():
    np.testing.assert_allclose(
        _scaling_ratio(np.array([5.0, 8.0, 10.0]), np.array([6.0, 8.0, 12.0])),
        [1.2, 1.0, 1.2],
    )


def test_scaling_ratio_guards():
    # era5<=0 -> 1.0 ; nan gwa -> 1.0 ; valid -> ratio
    out = _scaling_ratio(np.array([0.0, -1.0, 5.0, 5.0]), np.array([6.0, 6.0, np.nan, 10.0]))
    np.testing.assert_allclose(out, [1.0, 1.0, 1.0, 2.0])
