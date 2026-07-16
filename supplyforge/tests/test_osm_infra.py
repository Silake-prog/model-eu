"""Offline tests for the OSM grid-infrastructure + industry fetchers (parsing, geometry, aggregation)."""
import geopandas as gpd
from shapely.geometry import Polygon

from supplyforge.fetch.osm import electricity as elec
from supplyforge.fetch.osm import electricity_lines as el
from supplyforge.fetch.osm import industry as ind


def _mock_nuts(tmp_path, monkeypatch):
    """Two adjacent NUTS-2 boxes: RA = lon[2,4], RB = lon[4,6], both lat[46,48]."""
    nuts = gpd.GeoDataFrame(
        {"NUTS_ID": ["RA", "RB"]},
        geometry=[Polygon([(2, 46), (4, 46), (4, 48), (2, 48)]),
                  Polygon([(4, 46), (6, 46), (6, 48), (4, 48)])],
        crs="EPSG:4326")
    gj = tmp_path / "nuts.geojson"
    nuts.to_file(gj, driver="GeoJSON")
    monkeypatch.setattr("supplyforge.fetch.tools.visualisation.maps.fetch_nuts_geojson", lambda **k: str(gj))


# --- electricity_lines ---------------------------------------------------------------------------
def test_parse_voltage_and_class():
    assert el.parse_voltage("400000") == 400000
    assert el.parse_voltage("400 kV") == 400000
    assert el.parse_voltage("225000;400000") == 400000     # ;-list -> max
    assert el.parse_voltage("63000") == 63000
    assert el.parse_voltage(None) is None and el.parse_voltage("yes") is None
    assert el.voltage_class(400000) == "EHV" and el.voltage_class(150000) == "HV"
    assert el.voltage_class(20000) == "MV" and el.voltage_class(None) == "unknown"


def test_line_length_km():
    assert 110 < el.line_length_km([{"lon": 0, "lat": 0}, {"lon": 0, "lat": 1}]) < 112   # ~1deg lat
    assert el.line_length_km([{"lon": 0, "lat": 0}]) == 0.0


def test_fetch_power_lines(monkeypatch):
    fake = {"elements": [
        {"type": "way", "id": 1, "tags": {"power": "line", "voltage": "400000", "circuits": "2"},
         "geometry": [{"lon": 2.0, "lat": 48.0}, {"lon": 2.0, "lat": 48.5}]},
        {"type": "way", "id": 2, "tags": {"power": "cable", "frequency": "0", "voltage": "320000"},
         "geometry": [{"lon": 3.0, "lat": 47.0}, {"lon": 3.1, "lat": 47.0}]},
    ]}
    monkeypatch.setattr("supplyforge.fetch.osm.overpass.overpass", lambda ql, **k: fake)
    lines = el.fetch_power_lines((47, 2, 49, 4))
    a = next(L for L in lines if L["id"] == 1)
    assert a["voltage_class"] == "EHV" and a["circuits"] == 2 and a["length_km"] > 0 and a["hvdc"] is False
    b = next(L for L in lines if L["id"] == 2)
    assert b["hvdc"] is True and b["power"] == "cable"


def test_lines_to_region_stats(monkeypatch, tmp_path):
    _mock_nuts(tmp_path, monkeypatch)
    lines = [
        {"lon": 3.0, "lat": 47.0, "length_km": 50.0, "circuits": 2, "voltage_v": 400000, "voltage_class": "EHV"},
        {"lon": 3.5, "lat": 47.0, "length_km": 30.0, "circuits": 1, "voltage_v": 150000, "voltage_class": "HV"},
        {"lon": 5.0, "lat": 47.0, "length_km": 10.0, "circuits": None, "voltage_v": 63000, "voltage_class": "HV"},
    ]
    stats = el.lines_to_region_stats(lines, level=2)
    assert stats["RA"]["n_lines"] == 2 and stats["RA"]["circuit_km"] == 50 * 2 + 30 * 1
    assert stats["RA"]["circuit_km_by_class"]["EHV"] == 100.0
    assert stats["RB"]["circuit_km"] == 10.0           # circuits None -> 1
    # min_voltage filter keeps only EHV
    assert set(el.lines_to_region_stats(lines, level=2, min_voltage_v=220000)) == {"RA"}


# --- electricity (substations + converters) ------------------------------------------------------
def test_fetch_substations_and_converters(monkeypatch):
    subs_raw = {"elements": [{"type": "way", "id": 1, "center": {"lon": 2.3, "lat": 48.8},
                              "tags": {"power": "substation", "voltage": "225000", "name": "X"}}]}
    monkeypatch.setattr("supplyforge.fetch.osm.overpass.overpass", lambda ql, **k: subs_raw)
    subs = elec.fetch_substations((48, 2, 49, 3))
    assert subs[0]["voltage_v"] == 225000 and subs[0]["voltage_class"] == "EHV" and subs[0]["lon"] == 2.3

    conv_raw = {"elements": [{"type": "node", "id": 5, "lon": 1.0, "lat": 47.0,
                              "tags": {"power": "converter", "rating": "1000 MW", "converter": "vsc"}}]}
    monkeypatch.setattr("supplyforge.fetch.osm.overpass.overpass", lambda ql, **k: conv_raw)
    conv = elec.fetch_converters((46, 0, 48, 2))
    assert conv[0]["rating_mw"] == 1000.0 and conv[0]["converter"] == "vsc"


def test_substations_converters_to_region(monkeypatch, tmp_path):
    _mock_nuts(tmp_path, monkeypatch)
    subs = [{"lon": 3.0, "lat": 47.0, "voltage_v": 400000, "voltage_class": "EHV"},
            {"lon": 3.5, "lat": 47.0, "voltage_v": 225000, "voltage_class": "EHV"},
            {"lon": 5.0, "lat": 47.0, "voltage_v": 63000, "voltage_class": "HV"}]
    out = elec.substations_to_region(subs, level=2)
    assert out["RA"]["n"] == 2 and out["RA"]["by_class"]["EHV"] == 2 and out["RA"]["max_voltage_v"] == 400000
    assert out["RB"]["n"] == 1

    conv = [{"lon": 3.0, "lat": 47.0, "rating_mw": 1000.0}, {"lon": 3.2, "lat": 47.0, "rating_mw": None}]
    co = elec.converters_to_region(conv, level=2)
    assert co["RA"]["n"] == 2 and co["RA"]["rating_mw"] == 1000.0


# --- industry ------------------------------------------------------------------------------------
def test_fetch_industrial_sites(monkeypatch):
    fake = {"elements": [
        {"type": "way", "id": 1, "tags": {"landuse": "industrial", "name": "Zone"},
         "geometry": [{"lon": 2, "lat": 48}, {"lon": 2.1, "lat": 48}, {"lon": 2.1, "lat": 48.1},
                      {"lon": 2, "lat": 48.1}, {"lon": 2, "lat": 48}]},
        {"type": "node", "id": 2, "lon": 3, "lat": 47, "tags": {"man_made": "works", "name": "Plant"}},
    ]}
    monkeypatch.setattr("supplyforge.fetch.osm.overpass.overpass", lambda ql, **k: fake)
    sites = ind.fetch_industrial_sites((46, 1, 49, 4))
    poly = next(s for s in sites if s["id"] == 1)
    assert poly["kind"] == "industrial" and poly["lon"] is not None and poly["geometry"]
    node = next(s for s in sites if s["id"] == 2)
    assert node["lon"] == 3 and node["kind"] == "works"


def test_sites_to_region(monkeypatch, tmp_path):
    _mock_nuts(tmp_path, monkeypatch)
    ring = [{"lon": 2.95, "lat": 46.95}, {"lon": 3.05, "lat": 46.95},
            {"lon": 3.05, "lat": 47.05}, {"lon": 2.95, "lat": 47.05}, {"lon": 2.95, "lat": 46.95}]
    sites = [{"id": 1, "lon": 3.0, "lat": 47.0, "geometry": ring},      # RA, polygon -> area > 0
             {"id": 2, "lon": 3.5, "lat": 47.0, "geometry": None},      # RA, point -> 0 area
             {"id": 3, "lon": 5.0, "lat": 47.0, "geometry": None}]      # RB
    out = ind.sites_to_region(sites, level=2)
    assert out["RA"]["n_sites"] == 2 and out["RA"]["area_km2"] > 0
    assert out["RB"]["n_sites"] == 1 and out["RB"]["area_km2"] == 0.0
