"""Tests for the per-zone capacity template + registry validation."""
import logging

import pytest

from supplyforge.process.pecd.capacity_weights import write_capacity_template, get_zone_weights


def test_template_lists_registry_zones(tmp_path):
    p = write_capacity_template("FR", "p2on", tmp_path / "caps.csv")
    rows = p.read_text().strip().splitlines()
    assert rows[0] == "zone,technology,capacity_mw"
    assert len(rows) == 1 + 26                       # FR has 26 p2on zones
    assert rows[1].startswith("FR01,")
    assert all(r.endswith("wind_onshore,") for r in rows[1:])   # capacity left blank


def test_template_offshore_and_solar_tech(tmp_path):
    off = write_capacity_template("FR", "p2of", tmp_path / "off.csv").read_text()
    assert "wind_offshore," in off
    szon = write_capacity_template("DE", "szon", tmp_path / "szon.csv").read_text()
    assert "solar," in szon


def test_template_unknown_raises(tmp_path):
    with pytest.raises(ValueError):
        write_capacity_template("ZZ", "p2on", tmp_path / "x.csv")
    with pytest.raises(ValueError):
        write_capacity_template("FR", "not_a_level", tmp_path / "x.csv")


def test_filled_template_roundtrips_to_weights(tmp_path):
    p = tmp_path / "caps.csv"
    p.write_text("zone,technology,capacity_mw\n"
                 "FR01,wind_onshore,1200\nFR02,wind_onshore,800\nFR03,wind_onshore,\n")
    w = get_zone_weights("wind_onshore", {"pecd": {"zone_capacities_csv": str(p)}})
    assert w == {"FR01": 1200.0, "FR02": 800.0}      # blank capacity dropped


def test_unknown_zone_warns(caplog):
    with caplog.at_level(logging.WARNING):
        get_zone_weights("wind_onshore",
                         {"pecd": {"zone_capacities": {"wind_onshore": {"FR01": 100, "ZZ99": 50}}}})
    assert any("not in the PECD zone registry" in r.message for r in caplog.records)
