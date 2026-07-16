r"""
OSM industrial land use -> per-NUTS-region industrial footprint (a demand-side proxy).

PECD/Ember describe *supply*; to know where the electricity is *consumed by industry* we map
``landuse=industrial`` polygons and ``man_made=works`` (factories/plants) for a bounding box via the
cached Overpass client, then aggregate **site count and industrial land area** per NUTS region.
Industrial land area is a coarse but useful proxy for regional industrial electricity demand when no
metered data exists — it lets the regional model place industrial load sub-nationally.

Caveats (logged): OSM industrial tagging is uneven (under-mapped in places); land area != energy
intensity (a steelworks and a warehouse park look alike); a site is assigned to its centroid's
region. Data: (c) OpenStreetMap contributors, ODbL.

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _centroid(geometry):
    pts = [(g["lon"], g["lat"]) for g in (geometry or []) if g.get("lon") is not None and g.get("lat") is not None]
    if not pts:
        return (None, None)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def fetch_industrial_sites(bbox, *, with_area=True, timeout=300, cache_key=None, force=False):
    """Fetch industrial sites in ``bbox=(s,w,n,e)`` -> list of ``{id,type,kind,name,geometry,lon,lat}``.

    ``with_area=True`` requests polygon geometry (``out geom``) so land area can be computed;
    ``False`` uses lighter ``out center`` (count only). Cached on disk.
    """
    from supplyforge.fetch.osm.overpass import overpass

    s, w, n, e = bbox
    # Count mode (out center) drops the expensive landuse *relations* and resolves man_made as
    # node/way only, so a country-scale query doesn't time out on the public mirrors; area mode keeps
    # full polygons (relations + geometry) for accurate land area, at the cost of a heavier query.
    if with_area:
        body = (f'way["landuse"="industrial"]({s},{w},{n},{e});'
                f'relation["landuse"="industrial"]({s},{w},{n},{e});'
                f'nwr["man_made"="works"]({s},{w},{n},{e});')
        out_mode = "out tags geom;"
    else:
        body = (f'way["landuse"="industrial"]({s},{w},{n},{e});'
                f'nw["man_made"="works"]({s},{w},{n},{e});')
        out_mode = "out tags center;"
    ql = f"[out:json][timeout:{timeout}];({body});{out_mode}"
    key = cache_key or ("industry_" + ("area_" if with_area else "")
                        + "_".join(str(x) for x in bbox).replace(".", "p").replace("-", "m"))
    data = overpass(ql, cache_key=key, force=force, timeout=timeout)

    sites = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        center = el.get("center") or {}
        geom = el.get("geometry")
        lon = el.get("lon", center.get("lon"))
        lat = el.get("lat", center.get("lat"))
        if lon is None and geom:
            lon, lat = _centroid(geom)
        sites.append({"id": el.get("id"), "type": el.get("type"),
                      "kind": t.get("landuse") or t.get("man_made"), "name": t.get("name"),
                      "geometry": geom if with_area else None, "lon": lon, "lat": lat})
    logger.info("OSM industrial sites: %d (%d with geometry).",
                len(sites), sum(1 for x in sites if x.get("geometry")))
    return sites


def sites_to_region(sites, *, level=2, year=2021, scale="20M", id_col="NUTS_ID"):
    """Aggregate industrial sites -> ``{region: {n_sites, area_km2}}`` by point-in-NUTS.

    Land area is computed from polygon geometry on an equal-area projection (EPSG:3035); point
    sites (``man_made=works`` nodes) contribute to the count but 0 area. Requires ``geopandas``.
    """
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    from supplyforge.fetch.tools.visualisation import maps

    rows = [x for x in sites if x.get("lon") is not None and x.get("lat") is not None]
    if not rows:
        logger.warning("OSM: no industrial sites with coords; empty.")
        return {}

    # Vectorised polygon area (km^2): build rings, reproject once to 3035, .area.
    polys = []
    for x in rows:
        pts = [(p["lon"], p["lat"]) for p in (x.get("geometry") or []) if p.get("lon") is not None]
        polys.append(Polygon(pts) if len(pts) >= 4 else None)
    area_km2 = [0.0] * len(rows)
    valid = [i for i, p in enumerate(polys) if p is not None and p.is_valid]
    if valid:
        ga = gpd.GeoSeries([polys[i] for i in valid], crs="EPSG:4326").to_crs(3035).area / 1e6
        for k, i in enumerate(valid):
            area_km2[i] = float(ga.iloc[k])

    gdf = gpd.GeoDataFrame(rows, geometry=[Point(x["lon"], x["lat"]) for x in rows], crs="EPSG:4326")
    gdf["area_km2"] = area_km2
    nuts = gpd.read_file(maps.fetch_nuts_geojson(level=level, year=year, scale=scale))[[id_col, "geometry"]]
    joined = gpd.sjoin(gdf, nuts, predicate="within", how="inner")

    out = {}
    for rid, sub in joined.groupby(id_col):
        out[rid] = {"n_sites": int(len(sub)), "area_km2": float(sub["area_km2"].sum())}
    logger.info("OSM: %d industrial sites -> %d NUTS-%d regions (%.0f km2 total).",
                len(joined), len(out), level, sum(o["area_km2"] for o in out.values()))
    return out


__all__ = ["fetch_industrial_sites", "sites_to_region"]
