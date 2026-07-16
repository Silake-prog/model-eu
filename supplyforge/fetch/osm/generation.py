r"""
OSM ``power=plant`` -> per-NUTS-region installed capacity by technology.

Pulls power stations (with ``plant:source`` + ``plant:output:electricity``) for a bounding box
via the cached Overpass client, parses the rated capacity to MW, maps the OSM source to a
supplyforge technology, and aggregates by point-in-NUTS-region. The result feeds
``pecd.zone_capacities`` (precise PECD weighting) and the regional grid model's per-region fleet
— replacing the area-split assumption with real installed capacity.

Caveats (logged): ``plant:output:electricity`` is often ``yes``/absent → those plants are counted
but contribute no MW (so the totals are a *lower bound*, best for the large, well-mapped plants);
onshore/offshore wind isn't distinguished from ``plant:source=wind`` alone (all -> wind_onshore
here). Data: © OpenStreetMap contributors (ODbL).

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# unit -> MW (real power; *Wp peak and *VA apparent treated as the same magnitude)
_UNIT_MW = {"w": 1e-6, "kw": 1e-3, "mw": 1.0, "gw": 1e3, "tw": 1e6,
            "wp": 1e-6, "kwp": 1e-3, "mwp": 1.0, "gwp": 1e3,
            "va": 1e-6, "kva": 1e-3, "mva": 1.0, "gva": 1e3}

# OSM plant:source -> supplyforge technology key
OSM_SOURCE_TO_TECH = {
    "solar": "solar", "photovoltaic": "solar",
    "wind": "wind_onshore",            # onshore/offshore not separable from source alone
    "hydro": "hydro", "water": "hydro",
    "nuclear": "nuclear",
    "gas": "gas", "diesel": "oil", "oil": "oil", "coal": "coal",
    "biomass": "biomass", "biogas": "biomass", "biofuel": "biomass", "waste": "waste",
    "geothermal": "geothermal",
}


def parse_output_mw(value):
    """Parse an OSM ``*:output:electricity`` value to MW, or ``None``.

    Handles ``"5200 MW"``, ``"5.2 GW"``, ``"1500 kW"``, ``"*Wp"`` peak units, a **bare number
    (watts — OSM's SI base unit, so e.g. ``"300000"`` -> 0.3 MW)**, the first of a ``;``-separated
    list, and ``yes``/``no``/empty/unknown-unit -> ``None``.
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if ";" in v:
        v = v.split(";")[0].strip()
    if v in ("", "yes", "no", "unknown"):
        return None
    m = re.match(r"([0-9]*\.?[0-9]+)\s*([a-zµ]*)", v)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "w").replace("µ", "u")   # bare number -> watts (OSM SI base unit)
    factor = _UNIT_MW.get(unit)
    if factor is None:
        logger.debug("OSM: unrecognised output unit %r in %r; skipping.", unit, value)
        return None
    return num * factor


def fetch_power_plants(bbox, *, cache_key=None, force=False):
    """Fetch ``power=plant`` in ``bbox=(south, west, north, east)`` -> list of plant dicts.

    Each dict: ``{name, source, tech, output_mw (float|None), lon, lat}`` (centroid via Overpass
    ``out center``). Cached on disk (see :func:`supplyforge.fetch.osm.overpass.overpass`).
    """
    from supplyforge.fetch.osm.overpass import overpass

    s, w, n, e = bbox
    ql = f'[out:json][timeout:240];(nwr["power"="plant"]({s},{w},{n},{e}););out center tags;'
    key = cache_key or "plants_" + "_".join(str(x) for x in bbox).replace(".", "p").replace("-", "m")
    data = overpass(ql, cache_key=key, force=force)

    plants = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        src = (t.get("plant:source") or "").split(";")[0].strip().lower()
        center = el.get("center") or {}
        plants.append({
            "name": t.get("name"),
            "source": src,
            "tech": OSM_SOURCE_TO_TECH.get(src, src or "other"),
            "output_mw": parse_output_mw(t.get("plant:output:electricity")),
            "lon": el.get("lon", center.get("lon")),
            "lat": el.get("lat", center.get("lat")),
        })
    n_mw = sum(1 for p in plants if p["output_mw"] is not None)
    total_mw = sum(p["output_mw"] for p in plants if p["output_mw"])
    logger.info("OSM plants: %d found, %d with MW (%d%%), %.0f MW total.",
                len(plants), n_mw, 100 * n_mw // max(len(plants), 1), total_mw)
    return plants


def plants_to_region_capacity(plants, *, level=2, year=2021, scale="20M", id_col="NUTS_ID"):
    """Aggregate plants (with coords + MW) to ``{region_id: {tech: MW}}`` by point-in-NUTS.

    Plants without coords or without a parsed MW are skipped (the MW totals are a lower bound).
    Requires ``geopandas`` (the ``viz`` extra). Uses GISCO NUTS geometry via the maps fetcher.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    from supplyforge.fetch.tools.visualisation import maps

    rows = [p for p in plants if p.get("lon") is not None and p.get("lat") is not None and p.get("output_mw")]
    if not rows:
        logger.warning("OSM: no plants with both coordinates and a parsed MW; empty capacity.")
        return {}
    pgdf = gpd.GeoDataFrame(rows, geometry=[Point(p["lon"], p["lat"]) for p in rows], crs="EPSG:4326")
    nuts = gpd.read_file(maps.fetch_nuts_geojson(level=level, year=year, scale=scale))[[id_col, "geometry"]]
    joined = gpd.sjoin(pgdf, nuts, predicate="within", how="inner")

    cap = {}
    for rid, sub in joined.groupby(id_col):
        cap[rid] = {tech: float(mw) for tech, mw in sub.groupby("tech")["output_mw"].sum().items()}
    logger.info("OSM: aggregated %d plants into %d NUTS-%d regions.", len(joined), len(cap), level)
    return cap


__all__ = ["parse_output_mw", "OSM_SOURCE_TO_TECH", "fetch_power_plants", "plants_to_region_capacity"]
