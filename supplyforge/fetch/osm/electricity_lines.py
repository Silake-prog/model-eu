r"""
OSM power transmission lines/cables -> per-NUTS-region circuit length by voltage class.

Pulls ``power=line`` (overhead transmission; ``power=minor_line`` distribution is *excluded*) and
``power=cable`` (underground / submarine) for a bounding box via the cached Overpass client,
parses voltage / circuits / frequency, computes each line's geodesic length, and aggregates
**circuit-kilometres by voltage class** per NUTS region (a line is assigned to the region containing
its centroid). Together with ``generation.py`` (sources) and ``electricity.py`` (substation /
converter nodes) this gives an OSM-derived picture of the electricity supply network feeding
industry and households.

Caveats (logged): a line crossing several regions is attributed wholly to its centroid's region
(no per-segment split); ``voltage`` is often a ``;``-list (the max is taken); ``circuits`` defaults
to 1 when untagged; an HVDC ``power=cable`` has ``frequency=0``. Data: (c) OpenStreetMap, ODbL.

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger(__name__)

# voltage class -> minimum volts (ordered high to low); EHV >= 220 kV, HV >= 110 kV, MV >= 1 kV.
VOLTAGE_CLASSES = (("EHV", 220_000), ("HV", 110_000), ("MV", 1_000), ("LV", 0))


def parse_voltage(value):
    """Max voltage in **volts** from an OSM ``voltage`` tag, or ``None``.

    Handles a bare number (OSM convention = volts, e.g. ``"400000"`` -> 400 kV), a ``;``/``,``-list
    (``"400000;225000"`` -> 400000), and an explicit ``"400 kV"`` form. The max is returned.
    """
    if value is None:
        return None
    vals = []
    for tok in re.split(r"[;,]", str(value)):
        m = re.search(r"([0-9]*\.?[0-9]+)\s*(k?v)?", tok.strip().lower())
        if m:
            vals.append(float(m.group(1)) * (1000.0 if m.group(2) == "kv" else 1.0))
    return max(vals) if vals else None


def voltage_class(volts):
    """Map volts to a class name (``EHV``/``HV``/``MV``/``LV``), or ``"unknown"`` if ``None``."""
    if volts is None:
        return "unknown"
    for name, minv in VOLTAGE_CLASSES:
        if volts >= minv:
            return name
    return "LV"


def _to_int(value):
    try:
        return int(float(str(value).split(";")[0]))
    except (TypeError, ValueError):
        return None


def _haversine_km(a, b):
    R = 6371.0088
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _points(geometry):
    return [(g["lon"], g["lat"]) for g in (geometry or []) if g.get("lon") is not None and g.get("lat") is not None]


def line_length_km(geometry):
    """Geodesic length (km) of an Overpass ``out geom`` way geometry ``[{lat,lon}, ...]``."""
    pts = _points(geometry)
    return sum(_haversine_km(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) if len(pts) > 1 else 0.0


def _centroid(geometry):
    pts = _points(geometry)
    if not pts:
        return (None, None)
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def fetch_power_lines(bbox, *, kinds=("line", "cable"), geometry=True, cache_key=None, force=False):
    """Fetch transmission lines/cables in ``bbox=(south, west, north, east)`` -> list of line dicts.

    Each dict: ``{id, power, voltage_v, voltage_class, circuits, cables, frequency, hvdc, location,
    length_km, name, operator, lon, lat}`` (``lon/lat`` = geometry centroid). Cached on disk.

    ``geometry=True`` requests full way geometry (``out geom``) so ``length_km`` is computed -- correct
    but heavy (busy public Overpass mirrors 504 on large areas; use smaller boxes or a Geofabrik
    extract). ``geometry=False`` uses the much lighter ``out center`` (count + voltage only, no length).
    """
    from supplyforge.fetch.osm.overpass import overpass

    s, w, n, e = bbox
    sel = "|".join(kinds)
    out_mode = "out tags geom;" if geometry else "out tags center;"
    ql = f'[out:json][timeout:300];(way["power"~"^({sel})$"]({s},{w},{n},{e}););{out_mode}'
    key = cache_key or ("lines_" + ("geom_" if geometry else "ctr_")
                        + "_".join(str(x) for x in bbox).replace(".", "p").replace("-", "m"))
    data = overpass(ql, cache_key=key, force=force)

    lines = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        t = el.get("tags", {})
        geom = el.get("geometry") or []
        center = el.get("center") or {}
        v = parse_voltage(t.get("voltage"))
        clon, clat = _centroid(geom) if geom else (center.get("lon"), center.get("lat"))
        lines.append({
            "id": el.get("id"), "power": t.get("power"),
            "voltage_v": v, "voltage_class": voltage_class(v),
            "circuits": _to_int(t.get("circuits")), "cables": _to_int(t.get("cables")),
            "frequency": t.get("frequency"), "hvdc": str(t.get("frequency")).strip() == "0",
            "location": t.get("location") or ("overhead" if t.get("power") == "line" else None),
            "length_km": line_length_km(geom), "name": t.get("name"), "operator": t.get("operator"),
            "lon": clon, "lat": clat,
        })
    total_km = sum(L["length_km"] for L in lines)
    logger.info("OSM lines: %d ways, %.0f km total (%d HVDC).",
                len(lines), total_km, sum(1 for L in lines if L["hvdc"]))
    return lines


def lines_to_region_stats(lines, *, level=2, year=2021, scale="20M", id_col="NUTS_ID", min_voltage_v=0):
    """Aggregate lines to ``{region: {n_lines, total_km, circuit_km, circuit_km_by_class}}`` by centroid.

    ``circuit_km`` weights length by the number of circuits (default 1 when untagged). Filter to a
    minimum voltage with ``min_voltage_v`` (e.g. ``220_000`` for EHV only). Requires ``geopandas``.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    from supplyforge.fetch.tools.visualisation import maps

    rows = [L for L in lines
            if L["lon"] is not None and (L["voltage_v"] or 0) >= min_voltage_v]
    if not rows:
        logger.warning("OSM: no lines with a centroid (>= %d V); empty stats.", min_voltage_v)
        return {}
    gdf = gpd.GeoDataFrame(rows, geometry=[Point(L["lon"], L["lat"]) for L in rows], crs="EPSG:4326")
    gdf["circuit_km"] = [L["length_km"] * max(L["circuits"] or 1, 1) for L in rows]
    nuts = gpd.read_file(maps.fetch_nuts_geojson(level=level, year=year, scale=scale))[[id_col, "geometry"]]
    joined = gpd.sjoin(gdf, nuts, predicate="within", how="inner")

    stats = {}
    for rid, sub in joined.groupby(id_col):
        by_class = sub.groupby("voltage_class")["circuit_km"].sum()
        n_by_class = sub.groupby("voltage_class").size()
        stats[rid] = {
            "n_lines": int(len(sub)),
            "total_km": float(sub["length_km"].sum()),
            "circuit_km": float(sub["circuit_km"].sum()),
            "circuit_km_by_class": {k: float(v) for k, v in by_class.items()},
            "n_by_class": {k: int(v) for k, v in n_by_class.items()},
        }
    logger.info("OSM: aggregated %d lines into %d NUTS-%d regions.", len(joined), len(stats), level)
    return stats


__all__ = ["parse_voltage", "voltage_class", "line_length_km", "fetch_power_lines",
           "lines_to_region_stats", "VOLTAGE_CLASSES"]
