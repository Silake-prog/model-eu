r"""
Cache-first Overpass API client (rate-limit friendly).

The public Overpass servers throttle bursts hard (429/406 after a handful of rapid queries),
so this client: (1) **caches every response on disk** under ``RESULTS_DIR/osm/`` keyed by a
caller-supplied ``cache_key`` (re-runs never hit the network); (2) sends a descriptive
**User-Agent**; (3) tries **multiple mirrors** with backoff. Use ONE well-scoped query per area
and let the cache serve repeats — do not loop many small live queries.

Data is OpenStreetMap, © OpenStreetMap contributors, ODbL — attribute it in any derived product.

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

from supplyforge import RESULTS_DIR

logger = logging.getLogger(__name__)

MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
)
USER_AGENT = ("supplyforge/0.1 (OSM power data; "
              "+https://git.persee.minesparis.psl.eu/energy-alternatives/supplyforge)")


def _cache_dir() -> Path:
    d = Path(RESULTS_DIR) / "osm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def overpass(ql: str, *, cache_key: str, force: bool = False, mirrors=MIRRORS,
             timeout: int = 300, backoff: float = 2.0) -> dict:
    """Run an Overpass QL query, caching the JSON result on disk.

    Args:
        ql: the Overpass QL string (should start with ``[out:json]...``).
        cache_key: filename stem for the on-disk cache (``RESULTS_DIR/osm/<key>.json``).
        force: re-fetch even if cached.
        mirrors: Overpass endpoints to try in order (backoff between failures).
    Returns the parsed JSON dict. Raises RuntimeError if all mirrors fail.
    """
    dest = _cache_dir() / f"{cache_key}.json"
    if dest.is_file() and dest.stat().st_size > 50 and not force:
        logger.info("OSM Overpass: using cached %s", dest.name)
        return json.loads(dest.read_text())

    payload = urllib.parse.urlencode({"data": ql}).encode()
    last_err = None
    for i, mirror in enumerate(mirrors):
        try:
            logger.info("OSM Overpass: querying %s (cache_key=%s) ...", mirror, cache_key)
            req = urllib.request.Request(mirror, data=payload, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            obj = json.loads(raw)            # raises if the mirror returned an HTML error page
            dest.write_bytes(raw)
            logger.info("OSM Overpass: cached %s (%d elements)", dest.name, len(obj.get("elements", [])))
            return obj
        except Exception as exc:             # network error, throttle, or non-JSON error page
            last_err = exc
            logger.warning("OSM Overpass: mirror %s failed (%s); backing off + next mirror.", mirror, exc)
            time.sleep(backoff * (i + 1))
    raise RuntimeError(f"All Overpass mirrors failed for cache_key={cache_key!r}: {last_err}")


__all__ = ["overpass", "MIRRORS", "USER_AGENT"]
