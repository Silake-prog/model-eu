"""Tests for the OSM generation fetcher (offline: parsing, mapping, aggregation, cache)."""
from supplyforge.fetch.osm import generation as gen


def test_parse_output_mw():
    assert gen.parse_output_mw("5200 MW") == 5200.0
    assert gen.parse_output_mw("5.2 GW") == 5200.0
    assert gen.parse_output_mw("1500 kW") == 1.5
    assert gen.parse_output_mw("800") == 800.0 * 1e-6     # bare number -> watts (OSM SI base)
    assert gen.parse_output_mw("300 kWp") == 0.3          # peak units handled
    assert gen.parse_output_mw("2 MW;3 MW") == 2.0        # first of a list
    for v in ("yes", "no", "unknown", "", None, "12 xyz"):   # unknown unit -> None
        assert gen.parse_output_mw(v) is None


def test_source_to_tech():
    assert gen.OSM_SOURCE_TO_TECH["wind"] == "wind_onshore"
    assert gen.OSM_SOURCE_TO_TECH["solar"] == "solar"
    assert gen.OSM_SOURCE_TO_TECH["nuclear"] == "nuclear"
    assert gen.OSM_SOURCE_TO_TECH["water"] == "hydro"


def test_fetch_power_plants_parses(monkeypatch):
    fake = {"elements": [
        {"type": "way", "center": {"lon": 2.5, "lat": 46.5},
         "tags": {"power": "plant", "plant:source": "nuclear", "plant:output:electricity": "3600 MW", "name": "X"}},
        {"type": "node", "lon": 5.1, "lat": 46.2,
         "tags": {"power": "plant", "plant:source": "solar", "plant:output:electricity": "yes", "name": "Y"}},
    ]}
    monkeypatch.setattr("supplyforge.fetch.osm.overpass.overpass", lambda ql, **k: fake)
    plants = gen.fetch_power_plants((46, 2, 47, 6))
    assert len(plants) == 2
    nuc = next(p for p in plants if p["source"] == "nuclear")
    assert nuc["tech"] == "nuclear" and nuc["output_mw"] == 3600.0 and nuc["lon"] == 2.5
    sol = next(p for p in plants if p["source"] == "solar")
    assert sol["output_mw"] is None       # "yes" -> no MW


def test_plants_to_region_capacity(monkeypatch, tmp_path):
    import geopandas as gpd
    from shapely.geometry import Polygon
    nuts = gpd.GeoDataFrame(
        {"NUTS_ID": ["RA", "RB"]},
        geometry=[Polygon([(2, 46), (4, 46), (4, 48), (2, 48)]),
                  Polygon([(4, 46), (6, 46), (6, 48), (4, 48)])],
        crs="EPSG:4326")
    gj = tmp_path / "nuts.geojson"
    nuts.to_file(gj, driver="GeoJSON")
    monkeypatch.setattr("supplyforge.fetch.tools.visualisation.maps.fetch_nuts_geojson", lambda **k: str(gj))

    plants = [
        {"tech": "nuclear", "output_mw": 3600.0, "lon": 3.0, "lat": 47.0},      # RA
        {"tech": "solar", "output_mw": 100.0, "lon": 3.5, "lat": 47.0},         # RA
        {"tech": "solar", "output_mw": 50.0, "lon": 5.0, "lat": 47.0},          # RB
        {"tech": "wind_onshore", "output_mw": None, "lon": 5.0, "lat": 47.0},   # skipped (no MW)
    ]
    cap = gen.plants_to_region_capacity(plants, level=2)
    assert cap["RA"]["nuclear"] == 3600.0 and cap["RA"]["solar"] == 100.0
    assert cap["RB"]["solar"] == 50.0
    assert "wind_onshore" not in cap.get("RB", {})


def test_overpass_disk_cache(tmp_path, monkeypatch):
    from supplyforge.fetch.osm import overpass as ov
    calls = {"n": 0}
    # >50 bytes so it clears the cache's tiny-file/error-page guard (real Overpass JSON is larger)
    payload = b'{"version":0.6,"elements":[{"type":"node","id":1,"lat":46.0,"lon":2.0}]}'

    class _Resp:
        def read(self):
            return payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(ov, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(ov.urllib.request, "urlopen", fake_urlopen)
    r1 = ov.overpass("[out:json];node;out;", cache_key="t")
    assert calls["n"] == 1 and r1["elements"]
    r2 = ov.overpass("[out:json];node;out;", cache_key="t")   # served from disk cache
    assert calls["n"] == 1 and r2 == r1
