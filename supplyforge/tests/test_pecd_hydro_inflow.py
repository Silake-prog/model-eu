"""Unit tests for the PECD reservoir-inflow processor (pure transform)."""
import pandas as pd
import polars as pl

from supplyforge.process.pecd.hydro_inflow import pecd_reservoir_inflow


def _make_hri(n_weeks: int, zones: list[str], value: float, year: int = 2009) -> pd.DataFrame:
    """Wide weekly HRI frame: rows = weekly stamps, cols = zone codes (MWh/week)."""
    idx = pd.date_range(f"{year}-01-06", periods=n_weeks, freq="7D")
    return pd.DataFrame({z: [value] * n_weeks for z in zones},
                        index=pd.Index(idx, name="Date"))


def test_multizone_sum_gives_constant_mw_profile():
    # DKE1 + DKW1 = 168 + 168 = 336 MWh/week -> /168 = 2.0 MW (constant)
    hri = _make_hri(52, ["DKE1", "DKW1"], 168.0, year=2009)
    out = pecd_reservoir_inflow(hri, "DK", 2009)
    assert out.columns == ["inflow_MW", "timestamp"]
    assert out.height == 8760  # 2009 is not a leap year
    head = out["inflow_MW"].head(8000)
    assert (head - 2.0).abs().max() < 1e-9
    assert head.min() >= 0.0


def test_53_weeks_are_trimmed_to_52():
    hri = _make_hri(53, ["DKE1", "DKW1"], 168.0, year=2009)
    assert pecd_reservoir_inflow(hri, "DK", 2009).height == 8760


def test_leap_year_length_is_8784():
    hri = _make_hri(52, ["FR00"], 168.0, year=2008)
    assert pecd_reservoir_inflow(hri, "FR", 2008).height == 8784


def test_no_matching_zone_returns_empty_with_schema():
    hri = _make_hri(52, ["FR00"], 168.0, year=2009)
    out = pecd_reservoir_inflow(hri, "ZZ", 2009)
    assert out.is_empty()
    assert out.columns == ["inflow_MW", "timestamp"]


def test_timestamp_is_monotonic_utc():
    hri = _make_hri(52, ["FR00"], 168.0, year=2009)
    ts = pecd_reservoir_inflow(hri, "FR", 2009)["timestamp"]
    assert ts.is_sorted()
    assert "UTC" in str(ts.dtype)


def test_greece_zone_selected_via_el():
    hri = _make_hri(52, ["EL00", "FR00"], 168.0, year=2009)
    out = pecd_reservoir_inflow(hri, "GR", 2009)
    # EL00 only -> 168/168 = 1.0 MW
    assert (out["inflow_MW"].head(8000) - 1.0).abs().max() < 1e-9
