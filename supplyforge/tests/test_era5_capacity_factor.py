"""Tests for the ERA5 engine's canonical frame builder + fail-soft (offline; atlite-free)."""
import numpy as np
import pandas as pd

from supplyforge.process.era5.capacity_factor import _series_to_frame, era5_wind_solar_frames


def test_series_to_frame_schema_and_length():
    dates = pd.date_range("2009-01-01", periods=8760, freq="h", tz="UTC")
    f = _series_to_frame(np.full(8760, 0.3), dates, "Wind Onshore", 2009)
    assert set(f.columns) == {"hour", "plant_type", "WS", "capacity_factor"}
    assert f.height == 8760
    assert f["WS"].unique().to_list() == ["2009"]
    assert f["plant_type"].unique().to_list() == ["Wind Onshore"]


def test_series_to_frame_leap_trim_and_clip():
    dates = pd.date_range("2008-01-01", periods=8784, freq="h", tz="UTC")   # leap year
    vals = np.full(8784, 0.5)
    vals[:24] = 1.5                                                          # out-of-range -> clipped
    f = _series_to_frame(vals, dates, "Solar", 2008)
    assert f.height == 8760                                                 # Feb 29 dropped
    assert f["capacity_factor"].max() <= 1.0 and f["capacity_factor"].min() >= 0.0


def test_engine_fail_soft_without_atlite():
    # atlite absent (or cutout unavailable) -> [] rather than a crash (S1 fail-soft).
    assert era5_wind_solar_frames("FR", 2008, {"era5": {"climate_years": [2008]}}) == []
