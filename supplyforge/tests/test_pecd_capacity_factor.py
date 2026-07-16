"""Unit tests for the PECD capacity-factor processor (pure transforms)."""
import numpy as np
import pandas as pd
import polars as pl

from supplyforge.process.pecd.capacity_factor import (
    national_cf_from_frame,
    ror_cf_from_frames,
    _drop_feb29,
    _clip_flag,
    _long_frame,
)


def _hourly(year: int, zone_values: dict, periods: int) -> pd.DataFrame:
    idx = pd.date_range(f"{year}-01-01", periods=periods, freq="h")
    return pd.DataFrame({z: [v] * periods for z, v in zone_values.items()},
                        index=pd.Index(idx, name="Date"))


def _weekly(year: int, zone: str, value: float) -> pd.DataFrame:
    idx = pd.date_range(f"{year}-01-06", periods=52, freq="7D")
    return pd.DataFrame({zone: [value] * 52}, index=pd.Index(idx, name="Date"))


# --- leaf transforms -------------------------------------------------------

def test_drop_feb29_removes_24h_in_leap_year():
    idx = pd.date_range("2008-02-28", periods=72, freq="h")  # Feb 28, 29, Mar 1
    assert len(_drop_feb29(np.arange(72.0), idx)) == 48


def test_drop_feb29_noop_in_non_leap_year():
    idx = pd.date_range("2009-02-28", periods=72, freq="h")
    assert len(_drop_feb29(np.arange(72.0), idx)) == 72


def test_clip_flag_clips_to_unit_interval():
    assert _clip_flag(np.array([-0.1, 0.5, 1.2]), "x").tolist() == [0.0, 0.5, 1.0]


def test_long_frame_has_canonical_schema():
    f = _long_frame(np.full(8760, 0.3), "Wind Onshore", "2008")
    assert f.columns == ["hour", "plant_type", "WS", "capacity_factor"]
    assert f.schema["hour"] == pl.Int64
    assert f.schema["plant_type"] == pl.Categorical
    assert f.schema["WS"] == pl.Categorical
    assert f.schema["capacity_factor"] == pl.Float64
    assert f.height == 8760
    assert f["capacity_factor"][0] == 0.3


# --- wind/solar national aggregation --------------------------------------

def test_wind_capacity_weighted_aggregation_non_leap():
    df = _hourly(2009, {"FR15": 0.3, "FR16": 0.5}, 8760)
    cfg = {"pecd": {"zone_capacities": {"wind_onshore": {"FR15": 1.0, "FR16": 3.0}}}}
    out = national_cf_from_frame("WON", "FR", df, cfg)
    assert len(out) == 8760
    assert abs(out.mean() - 0.45) < 1e-9  # (0.3*1 + 0.5*3) / 4


def test_solar_leap_trim_single_zone():
    df = _hourly(2008, {"FR00": 0.6}, 8784)
    out = national_cf_from_frame("SPV", "FR", df, {})
    assert len(out) == 8760
    assert abs(out.mean() - 0.6) < 1e-9


def test_national_cf_no_matching_zone_returns_none():
    df = _hourly(2009, {"FR00": 0.6}, 8760)
    assert national_cf_from_frame("SPV", "ZZ", df, {}) is None


# --- run-of-river from weekly generation ----------------------------------

def test_ror_cf_from_weekly_generation():
    # HRO + HPO = 84 + 84 = 168 MWh/week; C_inst = 2 MW -> CF = 168 / (2*168) = 0.5
    out = ror_cf_from_frames("FR", 2009, _weekly(2009, "FR00", 84.0), _weekly(2009, "FR00", 84.0), 2.0)
    assert len(out) == 8760
    assert not np.isnan(out).any()  # trailing partial week is forward-filled
    assert abs(out.mean() - 0.5) < 1e-9


def test_ror_cf_without_capacity_returns_none():
    assert ror_cf_from_frames("FR", 2009, _weekly(2009, "FR00", 84.0), None, 0.0) is None
