"""Unit tests for the PECD per-zone capacity-weight provider."""
import pytest

from supplyforge.process.pecd.capacity_weights import get_zone_weights


def test_config_dict_weights_and_alias():
    cfg = {"pecd": {"zone_capacities": {"wind_onshore": {"FR15": 100, "FR16": 300}}}}
    assert get_zone_weights("wind_onshore", cfg) == {"FR15": 100.0, "FR16": 300.0}
    # data-code alias WON resolves to the same tech
    assert get_zone_weights("WON", cfg) == {"FR15": 100.0, "FR16": 300.0}


def test_zero_or_missing_capacities_dropped():
    cfg = {"pecd": {"zone_capacities": {"solar": {"A": 0, "B": 50, "C": None}}}}
    assert get_zone_weights("SPV", cfg) == {"B": 50.0}


def test_no_weights_returns_none():
    assert get_zone_weights("solar", {"pecd": {}}) is None
    assert get_zone_weights("wind_offshore", {}) is None


def test_csv_weights_filtered_by_technology(tmp_path):
    p = tmp_path / "caps.csv"
    p.write_text(
        "zone,technology,capacity_mw\n"
        "FR15,Wind Onshore,100\n"
        "FR16,Wind Onshore,300\n"
        "FR99,Solar PV,50\n"
    )
    cfg = {"pecd": {"zone_capacities_csv": str(p)}}
    assert get_zone_weights("wind_onshore", cfg) == {"FR15": 100.0, "FR16": 300.0}
    assert get_zone_weights("solar", cfg) == {"FR99": 50.0}


def test_config_dict_takes_precedence_over_csv(tmp_path):
    p = tmp_path / "caps.csv"
    p.write_text("zone,capacity_mw\nZ,999\n")
    cfg = {"pecd": {"zone_capacities": {"solar": {"Z": 1.0}}, "zone_capacities_csv": str(p)}}
    assert get_zone_weights("solar", cfg) == {"Z": 1.0}


def test_unknown_tech_raises():
    with pytest.raises(ValueError):
        get_zone_weights("coal", {})
