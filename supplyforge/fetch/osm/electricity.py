r"""
OSM electrical grid **nodes**: ``power=substation`` + ``power=converter`` -> per-NUTS-region counts.

Substations are the switching/transformation nodes of the grid (tagged with ``voltage``); converters
are HVDC stations (tagged with a ``rating`` in MW/MVA, and a ``converter`` type lcc/vsc/back-to-back).
This complements ``generation.py`` (sources) and ``electricity_lines.py`` (lines) to describe the
electricity supply network. Fetched via the cached Overpass client and aggregated by point-in-NUTS.

Data: (c) OpenStreetMap contributors, ODbL.

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fetch_substations(bbox, *, transmission_only=False, cache_key=None, force=False):
    """Fetch ``power=substation`` in ``bbox=(s,w,n,e)`` -> list of ``{id,name,voltage_v,voltage_class,
    substation,lon,lat}`` dicts (centroid via Overpass ``out center``). Cached on disk.

    ``transmission_only=True`` restricts to ``substation=transmission`` (the grid backbone -- a few
    thousand across a country), excluding the many small MV/LV distribution substations.
    """
    from supplyforge.fetch.osm.electricity_lines import parse_voltage, voltage_class
    from supplyforge.fetch.osm.overpass import overpass

    s, w, n, e = bbox
    extra = '["substation"="transmission"]' if transmission_only else ""
    ql = f'[out:json][timeout:240];(nwr["power"="substation"]{extra}({s},{w},{n},{e}););out center tags;'
    key = cache_key or ("substations_" + ("tx_" if transmission_only else "")
                        + "_".join(str(x) for x in bbox).replace(".", "p").replace("-", "m"))
    data = overpass(ql, cache_key=key, force=force)

    subs = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        center = el.get("center") or {}
        v = parse_voltage(t.get("voltage"))
        subs.append({"id": el.get("id"), "name": t.get("name"), "voltage_v": v,
                     "voltage_class": voltage_class(v), "substation": t.get("substation"),
                     "lon": el.get("lon", center.get("lon")), "lat": el.get("lat", center.get("lat"))})
    logger.info("OSM substations: %d (%d with voltage).", len(subs), sum(1 for x in subs if x["voltage_v"]))
    return subs


def fetch_converters(bbox, *, cache_key=None, force=False):
    """Fetch ``power=converter`` (HVDC stations) in ``bbox`` -> ``{id,name,rating_mw,converter,lon,lat}``."""
    from supplyforge.fetch.osm.generation import parse_output_mw
    from supplyforge.fetch.osm.overpass import overpass

    s, w, n, e = bbox
    ql = f'[out:json][timeout:240];(nwr["power"="converter"]({s},{w},{n},{e}););out center tags;'
    key = cache_key or "converters_" + "_".join(str(x) for x in bbox).replace(".", "p").replace("-", "m")
    data = overpass(ql, cache_key=key, force=force)

    conv = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        center = el.get("center") or {}
        rating = parse_output_mw(t.get("rating") or t.get("converter:rating") or t.get("power_rating"))
        conv.append({"id": el.get("id"), "name": t.get("name"), "rating_mw": rating,
                     "converter": t.get("converter"),
                     "lon": el.get("lon", center.get("lon")), "lat": el.get("lat", center.get("lat"))})
    logger.info("OSM converters: %d (%d with rating).", len(conv), sum(1 for x in conv if x["rating_mw"]))
    return conv


def _sjoin_nuts(items, level, year, scale, id_col):
    """Point-in-NUTS join of ``items`` (each with lon/lat) -> a GeoDataFrame grouped-ready by region."""
    import geopandas as gpd
    from shapely.geometry import Point

    from supplyforge.fetch.tools.visualisation import maps

    rows = [x for x in items if x.get("lon") is not None and x.get("lat") is not None]
    if not rows:
        return None
    gdf = gpd.GeoDataFrame(rows, geometry=[Point(x["lon"], x["lat"]) for x in rows], crs="EPSG:4326")
    nuts = gpd.read_file(maps.fetch_nuts_geojson(level=level, year=year, scale=scale))[[id_col, "geometry"]]
    return gpd.sjoin(gdf, nuts, predicate="within", how="inner")


def substations_to_region(subs, *, level=2, year=2021, scale="20M", id_col="NUTS_ID"):
    """Aggregate substations -> ``{region: {n, by_class, max_voltage_v}}`` by point-in-NUTS."""
    joined = _sjoin_nuts(subs, level, year, scale, id_col)
    if joined is None or joined.empty:
        logger.warning("OSM: no substations with coords; empty.")
        return {}
    out = {}
    for rid, sub in joined.groupby(id_col):
        out[rid] = {"n": int(len(sub)),
                    "by_class": {k: int(v) for k, v in sub.groupby("voltage_class").size().items()},
                    "max_voltage_v": float(sub["voltage_v"].max()) if sub["voltage_v"].notna().any() else None}
    logger.info("OSM: %d substations -> %d NUTS-%d regions.", len(joined), len(out), level)
    return out


def converters_to_region(conv, *, level=2, year=2021, scale="20M", id_col="NUTS_ID"):
    """Aggregate HVDC converters -> ``{region: {n, rating_mw}}`` by point-in-NUTS."""
    joined = _sjoin_nuts(conv, level, year, scale, id_col)
    if joined is None or joined.empty:
        logger.warning("OSM: no converters with coords; empty.")
        return {}
    out = {}
    for rid, sub in joined.groupby(id_col):
        out[rid] = {"n": int(len(sub)), "rating_mw": float(sub["rating_mw"].fillna(0).sum())}
    logger.info("OSM: %d converters -> %d NUTS-%d regions.", len(joined), len(out), level)
    return out


__all__ = ["fetch_substations", "fetch_converters", "substations_to_region", "converters_to_region"]
