"""Unit tests for the PECD country -> zone mapping and zonal aggregation."""
import polars as pl
import pytest

from supplyforge.fetch.pecd.zones import (
    pecd_country_prefixes,
    select_zone_columns,
    sum_zone_energy,
    capacity_weighted_cf,
)


# --- prefix resolution -----------------------------------------------------

def test_prefixes_default_and_case_insensitive():
    assert pecd_country_prefixes("FR") == ["FR"]
    assert pecd_country_prefixes("fr") == ["FR"]


def test_prefixes_greece_and_uk_overrides():
    assert pecd_country_prefixes("GR") == ["GR", "EL"]
    assert pecd_country_prefixes("UK") == ["UK", "GB"]


# --- zone-column selection -------------------------------------------------

def test_select_single_zone_ignores_date_and_others():
    cols = ["Date", "AL00", "AT00", "FR00", "DE00"]
    assert select_zone_columns(cols, "FR") == ["FR00"]
    assert select_zone_columns(cols, "AT") == ["AT00"]


def test_select_multi_zone_dk_and_se():
    cols = ["Date", "DKE1", "DKW1", "DE00", "SE01", "SE02", "SE03", "SE04"]
    assert select_zone_columns(cols, "DK") == ["DKE1", "DKW1"]
    assert select_zone_columns(cols, "SE") == ["SE01", "SE02", "SE03", "SE04"]


def test_select_greece_accepts_gr_or_el():
    # live PECD SZON uses GR00; NUTS-style data may use EL — both must match
    assert select_zone_columns(["Date", "GR00", "FR00", "DE00"], "GR") == ["GR00"]
    assert select_zone_columns(["Date", "EL00", "FR00", "DE00"], "GR") == ["EL00"]


def test_select_uk_accepts_gb_or_uk():
    assert select_zone_columns(["Date", "GB00", "FR00"], "UK") == ["GB00"]
    assert select_zone_columns(["Date", "UK00", "FR00"], "UK") == ["UK00"]


def test_select_no_match_returns_empty(caplog):
    assert select_zone_columns(["Date", "FR00", "DE00"], "ZZ") == []


# --- energy aggregation (sum) ----------------------------------------------

def test_sum_zone_energy_is_additive():
    df = pl.DataFrame({"DKE1": [1.0, 2.0], "DKW1": [3.0, 4.0], "DE00": [9.0, 9.0]})
    assert sum_zone_energy(df, ["DKE1", "DKW1"]).to_list() == [4.0, 6.0]


def test_sum_zone_energy_empty_raises():
    with pytest.raises(ValueError):
        sum_zone_energy(pl.DataFrame({"A": [1.0]}), [])


# --- capacity-factor aggregation (capacity-weighted) -----------------------

def test_cap_weighted_single_zone_passthrough():
    df = pl.DataFrame({"FR00": [0.1, 0.2, 0.3]})
    assert capacity_weighted_cf(df, ["FR00"]).to_list() == [0.1, 0.2, 0.3]


def test_cap_weighted_mean_with_weights():
    df = pl.DataFrame({"A": [0.0, 1.0], "B": [1.0, 1.0]})
    # weights A=1, B=3 -> (0*1+1*3)/4 = 0.75 ; (1*1+1*3)/4 = 1.0
    s = capacity_weighted_cf(df, ["A", "B"], weights={"A": 1.0, "B": 3.0})
    assert s.to_list() == [0.75, 1.0]


def test_cap_weighted_equal_fallback_when_no_weights():
    df = pl.DataFrame({"A": [0.0, 1.0], "B": [1.0, 1.0]})
    s = capacity_weighted_cf(df, ["A", "B"], weights=None)
    assert s.to_list() == [0.5, 1.0]


def test_cap_weighted_partial_missing_filled_with_mean_known():
    df = pl.DataFrame({"A": [0.2], "B": [1.0], "C": [1.0]})
    # known A=1, B=3 -> mean known = 2 assigned to C
    # value = (0.2*1 + 1*3 + 1*2) / (1+3+2) = 5.2/6
    s = capacity_weighted_cf(df, ["A", "B", "C"], weights={"A": 1.0, "B": 3.0})
    assert abs(s.to_list()[0] - 5.2 / 6.0) < 1e-12


def test_cap_weighted_empty_raises():
    with pytest.raises(ValueError):
        capacity_weighted_cf(pl.DataFrame({"A": [1.0]}), [])


def test_cap_weighted_excludes_empty_and_zero_zones():
    # PECD ships empty placeholder zones (esp. offshore); they must not dilute.
    df = pl.DataFrame({"EMPTY": [None, None], "ZERO": [0.0, 0.0], "REAL": [0.4, 0.5]})
    s = capacity_weighted_cf(df, ["EMPTY", "ZERO", "REAL"], weights=None, country="XX")
    assert s.to_list() == [0.4, 0.5]


def test_cap_weighted_skips_nulls_per_row():
    df = pl.DataFrame({"A": [0.4, None], "B": [0.6, 0.8]})
    s = capacity_weighted_cf(df, ["A", "B"], weights=None)
    assert s.to_list() == [0.5, 0.8]  # row 2 averages only B (A is null)


def test_cap_weighted_all_empty_returns_zeros():
    df = pl.DataFrame({"A": [None, None], "B": [None, None]})
    assert capacity_weighted_cf(df, ["A", "B"], weights=None, country="XX").to_list() == [0.0, 0.0]
