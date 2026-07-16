r"""
Fetch geographic boundaries for mapping supplyforge / PECD outputs.

Public, **no-auth** sources (Eurostat GISCO):

* **NUTS regions** (NUTS 0-3) — keyed by ``NUTS_ID``; these match the PECD ``nuts_0`` /
  ``nuts_2`` spatial levels directly, so a NUTS-level PECD fetch maps straight onto these
  geometries for a choropleth.
* **Countries** (``CNTR``) — keyed by ``CNTR_ID`` (ISO-2-ish; note ``EL`` = Greece,
  ``UK`` = United Kingdom, matching PECD area codes).

Downloads are cached under ``results/geo/`` and returned as :class:`~pathlib.Path`. The
fetchers use only the standard library; the optional plotting helpers
(:func:`load_geodataframe`, :func:`choropleth`) need ``geopandas`` (+ ``matplotlib``) and
fail with a clear message if those are absent.

PECD's own **bidding / wind zones** (``SZON``/``PEON``/``PEOF``) are *not* NUTS regions and
have no stable public GeoJSON; mapping at that resolution needs a PECD-specific zone
shapefile — see :data:`BIDDING_ZONE_NOTE`. Until then, aggregate PECD zones to NUTS or
country level (see :mod:`supplyforge.fetch.pecd.zone_registry`) and map with these geometries.

Plotting helpers (need ``geopandas`` + ``matplotlib``): :func:`choropleth` (filled regions, with
``region=`` crop presets + diverging ``vcenter``), :func:`flow_map` (directed inter-node flow
arrows — e.g. cross-border exchange), :func:`bubble_map` (sized/coloured points per node), and
:func:`centroids`. Geographic resolution: ``level`` 0 (country) → 3 (~department/NUTS-3) and
``scale`` 60M → 01M (see :data:`RESOLUTION_NOTE`).

Examples
--------
>>> from supplyforge.fetch.tools.visualisation import maps
>>> maps.choropleth({"FR": 0.24, "DE": 0.20}, level=0, region="cwe", vcenter=0.22)
>>> maps.flow_map({("FR", "DE"): 5.9, ("CH", "FR"): -5.0}, level=0, region="cwe")  # cross-border flows
>>> maps.bubble_map({"FR": 100, "DE": 150}, level=0, region="europe")              # per-node scalars
>>> maps.choropleth(cf_by_nuts2, level=2, scale="10M", region="france")            # NUTS-2 regions

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

GISCO_BASE = "https://gisco-services.ec.europa.eu/distribution/v2"
_SCALES = ("01M", "03M", "10M", "20M", "60M")     # GISCO generalisation levels
_CRS = ("4326", "3035", "3857")                    # WGS84 / ETRS-LAEA / Web-Mercator
_NUTS_LEVELS = (0, 1, 2, 3)
NUTS_DEFAULT_YEAR = 2021
CNTR_DEFAULT_YEAR = 2020

BIDDING_ZONE_NOTE = (
    "PECD SZON/PEON/PEOF zones are not NUTS regions and have no stable public GeoJSON. "
    "Aggregate PECD zones to NUTS or country level (zone_registry) and map with GISCO "
    "geometries, or supply a custom PECD zone shapefile to load_geodataframe()."
)

# Named lon/lat crop boxes (minx, miny, maxx, maxy in EPSG:4326) for the `region=` kwarg.
REGIONS = {
    "europe": (-12, 34, 34, 72),
    "western_europe": (-11, 35, 20, 60),
    "cwe": (-6, 41, 18, 55),               # central-western Europe (FR/DE/BE/NL/LU/CH/AT)
    "france": (-5.5, 41, 10, 51.5),
    "fr_be": (-5.5, 42, 7.8, 51.6),        # France + Belgium (tight)
    "iberia": (-10, 35, 5, 44),
    "italy": (6, 36, 19, 47.5),
    "nordics": (4, 54, 32, 71),
    "british_isles": (-11, 49, 2, 61),
    "benelux": (2, 48.5, 8, 54),
    "dach": (5, 45, 18, 55.5),             # Germany / Austria / Switzerland
}

RESOLUTION_NOTE = (
    "Two resolution knobs: `level` (0 country -> 3 ~department/NUTS-3) and `scale` (GISCO "
    "generalisation: 60M coarse .. 20M default .. 01M finest). Department-level maps: level=3; "
    "crisp close-ups: scale='03M' or '01M'."
)


def _resolve_region(region):
    """Return a (minx, miny, maxx, maxy) bbox from a preset name or 4-tuple, or None."""
    if region is None:
        return None
    if isinstance(region, (tuple, list)) and len(region) == 4:
        return tuple(float(x) for x in region)
    key = str(region).strip().lower().replace(" ", "_").replace("-", "_")
    if key in REGIONS:
        return REGIONS[key]
    raise ValueError(
        f"Unknown region {region!r}; pass an (minx, miny, maxx, maxy) bbox or one of {sorted(REGIONS)}."
    )


def _cache_dir(cache_dir: str | Path | None = None) -> Path:
    """The geo cache directory (``cache_dir`` or ``RESULTS_DIR/geo``), created if missing."""
    if cache_dir is not None:
        d = Path(cache_dir)
    else:
        from supplyforge import RESULTS_DIR
        d = Path(RESULTS_DIR) / "geo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def nuts_geojson_url(level: int = 0, year: int = NUTS_DEFAULT_YEAR,
                     scale: str = "20M", crs: str = "4326") -> str:
    """GISCO URL for NUTS regions at a level (validated)."""
    if level not in _NUTS_LEVELS:
        raise ValueError(f"NUTS level must be one of {_NUTS_LEVELS}, got {level!r}.")
    if scale not in _SCALES:
        raise ValueError(f"scale must be one of {_SCALES}, got {scale!r}.")
    if str(crs) not in _CRS:
        raise ValueError(f"crs must be one of {_CRS}, got {crs!r}.")
    return f"{GISCO_BASE}/nuts/geojson/NUTS_RG_{scale}_{year}_{crs}_LEVL_{level}.geojson"


def countries_geojson_url(year: int = CNTR_DEFAULT_YEAR,
                          scale: str = "20M", crs: str = "4326") -> str:
    """GISCO URL for country polygons (validated)."""
    if scale not in _SCALES:
        raise ValueError(f"scale must be one of {_SCALES}, got {scale!r}.")
    if str(crs) not in _CRS:
        raise ValueError(f"crs must be one of {_CRS}, got {crs!r}.")
    return f"{GISCO_BASE}/countries/geojson/CNTR_RG_{scale}_{year}_{crs}.geojson"


def _download(url: str, dest: Path, force: bool = False) -> Path:
    """Download ``url`` to ``dest`` (cached: skip if a non-trivial file already exists)."""
    if dest.is_file() and dest.stat().st_size > 1024 and not force:
        logger.info("geo: using cached %s", dest.name)
        return dest
    logger.info("geo: downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "supplyforge-maps/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    dest.write_bytes(data)
    logger.info("geo: wrote %s (%.1f kB)", dest.name, len(data) / 1024)
    return dest


def fetch_nuts_geojson(level: int = 0, year: int = NUTS_DEFAULT_YEAR, scale: str = "20M",
                       crs: str = "4326", cache_dir: str | Path | None = None,
                       force: bool = False) -> Path:
    """Fetch (and cache) the GISCO NUTS GeoJSON for a level. Returns the file path."""
    url = nuts_geojson_url(level, year, scale, crs)
    return _download(url, _cache_dir(cache_dir) / Path(url).name, force)


def fetch_countries_geojson(year: int = CNTR_DEFAULT_YEAR, scale: str = "20M",
                            crs: str = "4326", cache_dir: str | Path | None = None,
                            force: bool = False) -> Path:
    """Fetch (and cache) the GISCO country-polygons GeoJSON. Returns the file path."""
    url = countries_geojson_url(year, scale, crs)
    return _download(url, _cache_dir(cache_dir) / Path(url).name, force)


def load_geodataframe(path: str | Path):
    """Read a GeoJSON/shapefile into a GeoDataFrame (requires ``geopandas``)."""
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise RuntimeError(
            "load_geodataframe needs geopandas (optional): pip install geopandas"
        ) from exc
    return gpd.read_file(path)


def choropleth(values: dict, level: int = 0, *, year: int = NUTS_DEFAULT_YEAR,
               scale: str = "20M", id_col: str = "NUTS_ID", ax=None, region=None,
               title: str | None = None, cbar_label: str | None = None,
               vmin=None, vmax=None, vcenter=None, cmap=None, boundary: bool = True,
               missing_color: str = "lightgrey", legend: bool = True, figsize=(9, 9),
               **plot_kw):
    """Per-region value map (requires ``geopandas`` + ``matplotlib``).

    Args:
        values: ``{region_id: number}`` keyed by ``id_col`` (``NUTS_ID`` for NUTS, ``CNTR_ID``
            for countries; note ``EL``=Greece, ``UK``=United Kingdom).
        level: NUTS level (0 country .. 3 ~department) whose geometry to fetch.
        scale: GISCO generalisation ("60M".."01M"); finer = sharper borders for close-ups.
        region: crop to a named preset (see :data:`REGIONS`) or an (minx, miny, maxx, maxy) bbox.
        vcenter: centre a diverging colormap here (e.g. ``0`` for net import/export).
        boundary: draw thin region outlines.
    Returns the matplotlib Axes.
    """
    import matplotlib.pyplot as plt

    gdf = load_geodataframe(fetch_nuts_geojson(level=level, year=year, scale=scale))
    if id_col not in gdf.columns:
        raise KeyError(f"id_col {id_col!r} not in geometry columns {list(gdf.columns)}.")
    gdf = gdf.copy()
    gdf["_value"] = gdf[id_col].map(values)
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    norm = None
    if vcenter is not None:
        from matplotlib.colors import TwoSlopeNorm
        finite = [v for v in values.values() if v is not None]
        lo = vmin if vmin is not None else min(finite + [vcenter])
        hi = vmax if vmax is not None else max(finite + [vcenter])
        norm = TwoSlopeNorm(vcenter=vcenter, vmin=min(lo, vcenter - 1e-9), vmax=max(hi, vcenter + 1e-9))
        vmin = vmax = None
    edge = {"edgecolor": "white", "linewidth": 0.2} if boundary else {}
    legend_kwds = {"shrink": 0.6}
    if cbar_label:
        legend_kwds["label"] = cbar_label
    gdf.plot(column="_value", ax=ax, legend=legend, cmap=cmap, vmin=vmin, vmax=vmax, norm=norm,
             missing_kwds={"color": missing_color, "label": "no data"},
             legend_kwds=legend_kwds, **edge, **plot_kw)
    bbox = _resolve_region(region)
    if bbox:
        ax.set_xlim(bbox[0], bbox[2])
        ax.set_ylim(bbox[1], bbox[3])
    if title:
        ax.set_title(title)
    ax.set_axis_off()
    return ax


def _node_point(geom):
    """An interior label point on a geometry's **mainland** (its largest polygon).

    Using ``representative_point`` on the full geometry would place a country's marker on a remote
    territory (e.g. Norway's on Svalbard at ~80°N, Portugal's on the Azores) — pulling map markers and
    flow arrows far off the European frame. Picking the largest sub-polygon keeps the marker on the
    mainland.
    """
    from shapely.geometry import MultiPolygon

    if isinstance(geom, MultiPolygon) and not geom.is_empty:
        geom = max(geom.geoms, key=lambda g: g.area)
    return geom.representative_point()


def centroids(level: int = 0, *, year: int = NUTS_DEFAULT_YEAR, scale: str = "20M",
              crs: str = "4326", id_col: str = "NUTS_ID") -> dict:
    """``{region_id: (lon, lat)}`` interior mainland points per region (requires ``geopandas``)."""
    gdf = load_geodataframe(fetch_nuts_geojson(level=level, year=year, scale=scale, crs=crs))
    pts = gdf.geometry.apply(_node_point)
    return {rid: (float(p.x), float(p.y)) for rid, p in zip(gdf[id_col], pts)}


def flow_map(flows, level: int = 0, *, year: int = NUTS_DEFAULT_YEAR, scale: str = "20M",
             id_col: str = "NUTS_ID", ax=None, region="europe", base_color: str = "whitesmoke",
             cmap: str = "viridis", scale_width: float = 7.0, min_abs: float = 0.0,
             title: str | None = None, cbar_label: str = "|flow|", figsize=(10, 10)):
    """Directed flow arrows between node centroids over a light base map (geopandas + matplotlib).

    Args:
        flows: ``{(a, b): value}`` or iterable of ``(a, b, value)``. ``value > 0`` draws ``a->b``;
            ``value < 0`` reverses to ``b->a``. Arrow width & colour scale with ``|value|``.
        level/id_col: geometry level + id column the node codes refer to.
        region: crop preset or bbox (defaults to all of Europe).
    Returns the matplotlib Axes.
    """
    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize

    gdf = load_geodataframe(fetch_nuts_geojson(level=level, year=year, scale=scale))
    cen = {rid: (float(p.x), float(p.y))
           for rid, p in zip(gdf[id_col], gdf.geometry.apply(_node_point))}
    items = [(a, b, v) for (a, b), v in flows.items()] if isinstance(flows, dict) else list(flows)
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    gdf.plot(ax=ax, color=base_color, edgecolor="white", linewidth=0.3)
    mag = max((abs(v) for *_, v in items), default=1.0) or 1.0
    sm = ScalarMappable(norm=Normalize(0, mag), cmap=cmap)
    sm.set_array([])
    for a, b, v in items:
        if abs(v) < min_abs or a not in cen or b not in cen:
            continue
        (x0, y0), (x1, y1) = (cen[a], cen[b]) if v >= 0 else (cen[b], cen[a])
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>", color=sm.to_rgba(abs(v)),
                                    lw=0.6 + scale_width * abs(v) / mag, alpha=0.85,
                                    shrinkA=8, shrinkB=8))
    for rid in {a for a, _, _ in items} | {b for _, b, _ in items}:
        if rid in cen:
            ax.annotate(rid, cen[rid], fontsize=7, ha="center", va="center", color="white",
                        zorder=6, bbox=dict(boxstyle="circle,pad=0.25", fc="black", ec="none", alpha=0.75))
    bbox = _resolve_region(region)
    if bbox:
        ax.set_xlim(bbox[0], bbox[2])
        ax.set_ylim(bbox[1], bbox[3])
    if title:
        ax.set_title(title)
    cbar = ax.figure.colorbar(sm, ax=ax, shrink=0.5)
    cbar.set_label(cbar_label)
    ax.set_axis_off()
    return ax


def bubble_map(values: dict, level: int = 0, *, year: int = NUTS_DEFAULT_YEAR, scale: str = "20M",
               id_col: str = "NUTS_ID", ax=None, region="europe", base_color: str = "whitesmoke",
               cmap: str = "viridis", max_size: float = 600.0, title: str | None = None,
               cbar_label: str | None = None, figsize=(10, 10)):
    """Per-node scalars as sized/coloured bubbles at centroids over a base map (geopandas + matplotlib)."""
    import matplotlib.pyplot as plt

    gdf = load_geodataframe(fetch_nuts_geojson(level=level, year=year, scale=scale))
    cen = {rid: (float(p.x), float(p.y))
           for rid, p in zip(gdf[id_col], gdf.geometry.apply(_node_point))}
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    gdf.plot(ax=ax, color=base_color, edgecolor="white", linewidth=0.3)
    pts = [(cen[k][0], cen[k][1], v) for k, v in values.items() if k in cen and v is not None]
    if pts:
        xs, ys, vs = zip(*pts)
        amax = max(abs(v) for v in vs) or 1.0
        sc = ax.scatter(xs, ys, s=[max_size * abs(v) / amax for v in vs], c=vs, cmap=cmap,
                        alpha=0.85, edgecolor="k", linewidth=0.5, zorder=5)
        cbar = ax.figure.colorbar(sc, ax=ax, shrink=0.5)
        if cbar_label:
            cbar.set_label(cbar_label)
    bbox = _resolve_region(region)
    if bbox:
        ax.set_xlim(bbox[0], bbox[2])
        ax.set_ylim(bbox[1], bbox[3])
    if title:
        ax.set_title(title)
    ax.set_axis_off()
    return ax


__all__ = [
    "GISCO_BASE", "BIDDING_ZONE_NOTE", "REGIONS", "RESOLUTION_NOTE",
    "nuts_geojson_url", "countries_geojson_url",
    "fetch_nuts_geojson", "fetch_countries_geojson",
    "load_geodataframe", "choropleth", "centroids", "flow_map", "bubble_map",
]
