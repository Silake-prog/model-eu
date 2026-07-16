r"""
JRC ENSPRESO biomass workbook fetch (one-shot staging download).

Role in SupplyForge pipeline
----------------------------
Stages the raw **ENSPRESO_BIOMASS.xlsx** open-data workbook under ``RESULTS_DIR/enspreso/`` for
:mod:`supplyforge.biomethane`, which derives per-country biomethane potentials + costs from it. Like
:mod:`supplyforge.fetch.eraa_study`, this is a *prospective study data fetch*: it stages a raw source
file, it does not produce a model-ready dataset.

Data source
-----------
``https://cidportal.jrc.ec.europa.eu/ftp/jrc-opendata/ENSPRESO/ENSPRESO_BIOMASS.xlsx`` (~15.7 MB).
ENSPRESO — the JRC ENergy Systems Potentials for Renewable Energy biomass dataset (Ruiz et al., 2019).
License **CC BY 4.0** — attribute the JRC in any derived product.

Author: Simon Brigode <simon.brigode@ehess.fr>.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from supplyforge import RESULTS_DIR

logger = logging.getLogger(__name__)

ENSPRESO_BIOMASS_URL = (
    "https://cidportal.jrc.ec.europa.eu/ftp/jrc-opendata/ENSPRESO/ENSPRESO_BIOMASS.xlsx"
)
TARGET_DIR = RESULTS_DIR / "enspreso"
ENSPRESO_BIOMASS_FILE = TARGET_DIR / "ENSPRESO_BIOMASS.xlsx"
_MIN_FILE_SIZE_BYTES = 5_000_000  # 5 MB sanity floor (the real workbook is ~15.7 MB)


def fetch_enspreso_biomass(force: bool = False, url: str = ENSPRESO_BIOMASS_URL) -> Path:
    """Download (and cache) the JRC ``ENSPRESO_BIOMASS.xlsx`` workbook.

    Uses an **atomic write** (download to a temp file, then rename) so a partial/failed download never
    poisons the cache, and a **size floor** to reject truncated files. Skips the download when a valid
    cached copy already exists; on a network failure, falls back to a stale cache if one is present.

    Args:
        force: re-download even if a valid cache exists.
        url: override the JRC source URL.

    Returns:
        Path to the (downloaded or cached) workbook.

    Raises:
        FileNotFoundError: if the download fails and no usable cache exists.
    """
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    target = ENSPRESO_BIOMASS_FILE

    if target.exists() and not force and target.stat().st_size >= _MIN_FILE_SIZE_BYTES:
        logger.info("ENSPRESO biomass already cached at %s (%d bytes)", target, target.stat().st_size)
        return target

    logger.info("Downloading ENSPRESO biomass from %s", url)
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=TARGET_DIR, prefix=".enspreso_dl_",
                                         suffix=".tmp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            req = urllib.request.Request(url, headers={"User-Agent": "supplyforge-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                shutil.copyfileobj(resp, tmp)
        size = tmp_path.stat().st_size
        if size < _MIN_FILE_SIZE_BYTES:
            tmp_path.unlink(missing_ok=True)
            raise ValueError(f"ENSPRESO download too small ({size} bytes < {_MIN_FILE_SIZE_BYTES})")
        shutil.move(str(tmp_path), str(target))
        logger.info("ENSPRESO biomass downloaded: %d bytes -> %s", size, target)
        return target
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        if target.exists() and target.stat().st_size >= _MIN_FILE_SIZE_BYTES:
            logger.warning("ENSPRESO download failed (%s); using stale cache %s", exc, target)
            return target
        raise FileNotFoundError(
            f"ENSPRESO biomass download failed and no cache available: {exc}. "
            f"Manually download {url} to {target}."
        ) from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    print(fetch_enspreso_biomass())
