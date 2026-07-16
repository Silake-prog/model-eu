"""Tests for the geo-boundary fetcher (offline: URL builders + cache logic)."""
import json

import pytest

from supplyforge.fetch.tools.visualisation import maps


def test_nuts_url_builder():
    assert maps.nuts_geojson_url(0) == (
        "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
        "NUTS_RG_20M_2021_4326_LEVL_0.geojson"
    )
    assert maps.nuts_geojson_url(2, scale="10M").endswith("NUTS_RG_10M_2021_4326_LEVL_2.geojson")


def test_countries_url_builder():
    assert maps.countries_geojson_url().endswith("CNTR_RG_20M_2020_4326.geojson")


@pytest.mark.parametrize("kw", [{"level": 9}, {"scale": "99M"}, {"crs": "9999"}])
def test_nuts_url_validates(kw):
    with pytest.raises(ValueError):
        maps.nuts_geojson_url(**kw)


def test_download_caches(tmp_path, monkeypatch):
    calls = {"n": 0}
    # >1024 bytes so it clears _download's cache threshold (tiny files are re-fetched)
    payload = b'{"type":"FeatureCollection","_pad":"' + b'A' * 1100 + b'","features":[]}'

    class FakeResp:
        def read(self):
            return payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(maps.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "x.geojson"

    p1 = maps._download("http://example/x.geojson", dest)
    assert p1.is_file() and calls["n"] == 1
    p2 = maps._download("http://example/x.geojson", dest)   # cached -> no 2nd hit
    assert p2 == p1 and calls["n"] == 1
    assert json.loads(dest.read_text())["type"] == "FeatureCollection"


def test_regions_and_resolve():
    assert len(maps.REGIONS["cwe"]) == 4
    assert maps._resolve_region("france") == maps.REGIONS["france"]
    assert maps._resolve_region("Western Europe") == maps.REGIONS["western_europe"]   # case/space normalised
    assert maps._resolve_region((1, 2, 3, 4)) == (1.0, 2.0, 3.0, 4.0)
    assert maps._resolve_region(None) is None
    with pytest.raises(ValueError):
        maps._resolve_region("atlantis")


def _patch_geometry(monkeypatch):
    """Replace fetch+load with a synthetic 3-polygon GeoDataFrame (no network)."""
    import geopandas as gpd
    from shapely.geometry import Polygon
    gdf = gpd.GeoDataFrame(
        {"NUTS_ID": ["AA", "BB", "CC"]},
        geometry=[Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
                  Polygon([(4, 0), (6, 0), (6, 2), (4, 2)]),
                  Polygon([(8, 0), (10, 0), (10, 2), (8, 2)])],
        crs="EPSG:4326")
    monkeypatch.setattr(maps, "fetch_nuts_geojson", lambda **k: "dummy")
    monkeypatch.setattr(maps, "load_geodataframe", lambda p: gdf)


def test_centroids(monkeypatch):
    _patch_geometry(monkeypatch)
    cen = maps.centroids(level=0)
    assert set(cen) == {"AA", "BB", "CC"}
    assert 0 < cen["AA"][0] < 2 and 0 < cen["AA"][1] < 2     # interior point of first square


def test_flow_and_bubble_maps_render(monkeypatch):
    import matplotlib
    matplotlib.use("Agg")
    _patch_geometry(monkeypatch)
    ax = maps.flow_map({("AA", "BB"): 3.0, ("CC", "AA"): -1.0}, level=0, region=(-1, -1, 11, 3))
    assert ax is not None
    ax2 = maps.bubble_map({"AA": 5, "BB": 2}, level=0, region=(-1, -1, 11, 3))
    assert ax2 is not None


def test_choropleth_vcenter_and_region(monkeypatch):
    import matplotlib
    matplotlib.use("Agg")
    _patch_geometry(monkeypatch)
    ax = maps.choropleth({"AA": -2, "BB": 0, "CC": 3}, level=0, vcenter=0.0, cmap="RdBu", region="europe")
    assert ax is not None
