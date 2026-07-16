r"""
Country geometry & bounding boxes for the raw-ERA5 engine.

Adapted (**ported, not imported**) from demandforge's ``fetch/era5.py`` +
``country_borders.py`` — per the integration spec, ``supplyforge`` must not take a hard
dependency on ``demandforge``. Country polygons are sourced from Eurostat GISCO via the
existing maps fetcher (:func:`supplyforge.fetch.tools.visualisation.maps.fetch_countries_geojson`),
so we reuse one cached download instead of a second border file. Greece is remapped
``GR -> EL`` (GISCO ``CNTR_ID``), matching demandforge and the PECD path.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Europe bbox to drop overseas territories (French Guiana, Réunion, Canaries, ...).
EUROPE_BBOX = {"min_lon": -25.0, "max_lon": 45.0, "min_lat": 34.0, "max_lat": 72.0}
_CNTR_REMAP = {"GR": "EL"}   # repo country code -> GISCO CNTR_ID


def _countries_gdf(scale: str = "20M"):
    """GISCO country polygons as a (GeoDataFrame, id_column) tuple (cached via maps)."""
    import geopandas as gpd
    from supplyforge.fetch.tools.visualisation import maps

    gdf = gpd.read_file(maps.fetch_countries_geojson(scale=scale))
    col = next((c for c in ("CNTR_ID", "CNTR_CODE", "id") if c in gdf.columns), None)
    if col is None:
        raise ValueError(f"No country-id column in GISCO countries layer; columns={list(gdf.columns)}.")
    return gdf, col


def filter_geometries_to_europe(gdf, europe_bbox: dict = EUROPE_BBOX):
    """Keep only polygons whose centroid lies in the Europe bbox (drops overseas parts).

    Explodes MultiPolygons, filters by an interior representative point (avoids the
    geographic-CRS centroid warning). Adapted from
    ``demandforge.fetch.era5.filter_geometries_to_europe``.
    """
    exploded = gdf.explode(index_parts=False).reset_index(drop=True)
    cen = exploded.geometry.representative_point()
    keep = (
        (cen.x >= europe_bbox["min_lon"]) & (cen.x <= europe_bbox["max_lon"])
        & (cen.y >= europe_bbox["min_lat"]) & (cen.y <= europe_bbox["max_lat"])
    )
    return exploded[keep].copy()


def _country_rows(country: str, scale: str = "20M"):
    cid = _CNTR_REMAP.get(country.upper(), country.upper())
    gdf, col = _countries_gdf(scale)
    return gdf[gdf[col] == cid].copy()


def get_country_bbox(country: str, *, europe_only: bool = True, pad: float = 0.5,
                     scale: str = "20M"):
    """``{min_lon, min_lat, max_lon, max_lat}`` for a country (GR->EL), padded for atlite cutouts.

    ``pad`` (degrees) widens the box so ERA5 0.25° edge cells aren't clipped. Returns ``None``
    (logged) if the country isn't found — fail-soft (S1).
    """
    rows = _country_rows(country, scale)
    if rows.empty:
        logger.warning("ERA5 geometry: country %r not found in GISCO countries layer.", country)
        return None
    if europe_only:
        rows = filter_geometries_to_europe(rows)
        if rows.empty:
            logger.warning("ERA5 geometry: no European geometry for %r.", country)
            return None
    minx, miny, maxx, maxy = rows.total_bounds
    return {"min_lon": float(minx) - pad, "min_lat": float(miny) - pad,
            "max_lon": float(maxx) + pad, "max_lat": float(maxy) + pad}


def country_geometry(country: str, *, europe_only: bool = True, scale: str = "20M"):
    """Dissolved shapely geometry for a country (for atlite ``shapes=``), or ``None`` (fail-soft)."""
    rows = _country_rows(country, scale)
    if rows.empty:
        return None
    if europe_only:
        rows = filter_geometries_to_europe(rows)
        if rows.empty:
            return None
    geom = rows.geometry
    return geom.union_all() if hasattr(geom, "union_all") else geom.unary_union


__all__ = ["EUROPE_BBOX", "filter_geometries_to_europe", "get_country_bbox", "country_geometry"]
