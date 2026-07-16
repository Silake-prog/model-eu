"""Tests for the ported ERA5 geometry helpers (offline; synthetic GISCO layer)."""
import geopandas as gpd
from shapely.geometry import Polygon

from supplyforge.fetch.era5 import geometry as geo


def _fake_layer(monkeypatch):
    gdf = gpd.GeoDataFrame(
        {"CNTR_ID": ["FR", "EL", "DE"]},
        geometry=[Polygon([(2, 46), (6, 46), (6, 49), (2, 49)]),     # France-ish box
                  Polygon([(22, 38), (24, 38), (24, 40), (22, 40)]),  # Greece (EL) box
                  Polygon([(8, 48), (12, 48), (12, 52), (8, 52)])],   # Germany box
        crs="EPSG:4326")
    monkeypatch.setattr(geo, "_countries_gdf", lambda scale="20M": (gdf, "CNTR_ID"))


def test_bbox_basic(monkeypatch):
    _fake_layer(monkeypatch)
    assert geo.get_country_bbox("FR", pad=0.0) == {
        "min_lon": 2.0, "min_lat": 46.0, "max_lon": 6.0, "max_lat": 49.0}


def test_bbox_pad(monkeypatch):
    _fake_layer(monkeypatch)
    b = geo.get_country_bbox("FR", pad=0.5)
    assert b["min_lon"] == 1.5 and b["max_lat"] == 49.5


def test_bbox_greece_remap(monkeypatch):
    _fake_layer(monkeypatch)        # repo code "GR" -> GISCO "EL"
    b = geo.get_country_bbox("GR", pad=0.0)
    assert b is not None and round(b["min_lon"]) == 22


def test_bbox_unknown_country(monkeypatch):
    _fake_layer(monkeypatch)
    assert geo.get_country_bbox("ZZ") is None


def test_country_geometry(monkeypatch):
    _fake_layer(monkeypatch)
    g = geo.country_geometry("DE")
    assert g is not None and g.area > 0
