r"""
PECD 4.2 raw study-data fetch module (Copernicus CDS).

Role in SupplyForge pipeline
----------------------------

This module is the PECD analogue of :mod:`supplyforge.fetch.eraa_study`: it is a
**staging fetcher**. Driven by config, it issues the minimal set of Copernicus
Climate Data Store (CDS) requests for the selected PECD 4.2 variables / spatial
levels / climate years and stages the raw **aggregated CSV** files under
``RESULTS_DIR / "pecd_study"`` together with a ``download.txt`` manifest. It does
**not** produce model-ready Parquet — that harmonisation lives in
``process/pecd/capacity_factor.py`` and ``process/pecd/hydro_inflow.py``.

Data source
-----------

- CDS dataset id: ``sis-energy-pecd`` (Python client ``cdsapi``,
  endpoint ``https://cds.climate.copernicus.eu/api``).
- Requires a CDS account + API key (``~/.cdsapirc`` or ``CDSAPI_URL`` /
  ``CDSAPI_KEY`` in ``.env``) **and one-time per-dataset Terms & Conditions
  acceptance** on the dataset page. Never hard-code a key.
- Version is an explicit request field: ``pecd_version: "pecd4_2"`` (never the
  deprecated ``pecd4_1``).

Origin / temporal period (the "ERA5 vs projection" axis)
--------------------------------------------------------

- ``temporal_period: ["historical"]`` + ``origin: ["era5_reanalysis"]`` ->
  the **ERA5 reanalysis** stream (hourly, 1950-near-present).
- ``temporal_period: ["future_projections"]`` (plural) + ``origin: [<model>]``
  (one of ``cmcc_cm2_sr5|ec_earth3|mpi_esm1_2_hr|awi_cm_1_1_mr|bcc_csm2_mr|
  mri_esm2_0``) + ``emission_scenario: [<ssp>]`` -> CMIP6 projections (2015-2100).
  The climate model *is* the ``origin`` value; there is no separate
  ``climate_model`` field.

Variables fetched (data code -> CDS API long name)
--------------------------------------------------

Capacity factors (CF in [0, 1], hourly):
  ``SPV`` solar PV, ``WON`` wind onshore, ``WOF`` wind offshore.
Hydro energy (MWh, **weekly**, SZON only):
  ``HRI`` reservoir inflow, ``HRR`` run-of-river inflow,
  ``HPI`` RoR-with-pondage inflow, ``HOL`` open-loop PHS inflow,
  ``HRO`` RoR generation, ``HPO`` RoR-with-pondage generation
  (HRO/HPO are fetched only when ``pecd.ror_source == pecd`` -> used to build the
  RoR capacity factor in the process step).

Only the variables actually required by the active config are requested
(see :func:`_required_codes`): res-side CF iff ``res_source == pecd``; hydro iff
the resolved ``hydro_source == pecd``.

Spatial levels & aggregation
-----------------------------

We use the **aggregated** levels (CSV, no ``area``):
``PEON``/``P2ON`` (onshore wind), ``PEOF``/``P2OF`` (offshore wind),
``SZON`` (hydro, always; solar unless ``nuts_0`` requested). Country-level WON/WOF
(``NUT0``) was removed from PECD and is **never requested** (guarded below). One
CSV = one climate year (leap years have 8784 hourly rows; trimming happens in the
process step). CSV layout consumed downstream:
``pd.read_csv(fn, comment="#", index_col="Date")`` with rows = timestamps and
columns = zone codes.

Known-issue handling
--------------------

- ``file_version: fv2`` is forced for ``bcc_csm2_mr`` + ``ssp2_4_5`` **wind**
  (and is recommended for MENA aggregations); otherwise the configured
  ``file_version`` (default ``fv1``) is used.
- WON/WOF are never requested at ``nuts_0`` (deprecated) — raises if mis-configured.

Provenance (recorded in ``download.txt``)
----------------------------------------

The manifest header records ``pecd_version``, ``temporal_period``, ``origin``,
``emission_scenario``, ``file_version`` and the spatial levels used, so every
staged dataset is traceable. PECD hydro inflows are a statistical (Random-Forest)
reconstruction, reservoir inflow is zero-clipped at source, and some zones carry
temporary correction factors / IC-rescaling (PUG Table 2.10; AL/CH/HU/PL/PT) —
these caveats are surfaced again in the process modules' logs.

Robustness
----------

Each ``client.retrieve(...)`` is wrapped in try/except and fail-soft per
(variable, year): a failure is logged and skipped so one missing product does not
kill the DAG. Missing CDS credentials raise a single, actionable error (this is a
setup problem, not per-product missing data).
"""
from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path

from supplyforge import RESULTS_DIR, PACKAGE_DIR
from supplyforge.sources import (
    resolve_res_source,
    resolve_hydro_source,
    resolve_ror_source,
)
from supplyforge.fetch.pecd import catalog
from supplyforge.fetch.pecd.catalog import validate_pecd_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATASET_ID = "sis-energy-pecd"
TARGET_DIR = RESULTS_DIR / "pecd_study"
DOWNLOAD_LOG = TARGET_DIR / "download.txt"

# Variable / level / technology lookups all come from the catalog (single source
# of truth: supplyforge/fetch/pecd/catalog.py).
CODE_TO_API = catalog.CODE_TO_API
CODE_FAMILY = catalog.FAMILY
HYDRO_LONGNAME_TO_CODE = catalog.HYDRO_LONGNAME_TO_CODE
DEFAULT_SOLAR_TECHNOLOGY = catalog.DEFAULT_TECHNOLOGY["solar"]
_OFFSHORE_LEVEL = catalog.OFFSHORE_LEVEL


def _required_codes(config: dict) -> list[str]:
    """Return the PECD data codes required by the active config (order-stable)."""
    pecd_cfg = config.get("pecd") or {}
    codes: list[str] = []

    if resolve_res_source(config) == "pecd":
        codes += ["SPV", "WON", "WOF"]

    if resolve_hydro_source(config) == "pecd":
        codes.append("HRI")
        if resolve_ror_source(config) == "pecd":
            codes += ["HRO", "HPO"]
        # Any extra inflow variables the maintainer listed (e.g. HOL for D4,
        # fetched-but-unused; HRR/HPI for analysis).
        for longname in pecd_cfg.get("variables_hydro", []) or []:
            code = HYDRO_LONGNAME_TO_CODE.get(longname)
            if code is None:
                logger.warning("PECD: unknown variables_hydro entry '%s' (skipped).", longname)
            elif code not in codes:
                codes.append(code)

    # Extra variables (codes or long-names) requested for analysis/export — staged
    # in addition to what the model needs (e.g. CSP, HRG, HRR/HPI/HOL).
    for v in pecd_cfg.get("extra_variables", []) or []:
        try:
            code = catalog.resolve_code(v)
        except ValueError:
            logger.warning("PECD: unknown extra_variables entry '%s' (skipped).", v)
            continue
        if code not in codes:
            codes.append(code)

    return list(dict.fromkeys(codes))


def _level_for(code: str, pecd_cfg: dict) -> str:
    """Resolve the CDS ``spatial_resolution`` enum for a data code."""
    fam = CODE_FAMILY[code]
    if fam == "wind_on":
        return (pecd_cfg.get("spatial_resolution_wind") or "peon").lower()
    if fam == "wind_off":
        on = (pecd_cfg.get("spatial_resolution_wind") or "peon").lower()
        return _OFFSHORE_LEVEL.get(on, "peof")
    if fam == "csp":  # concentrated solar is published on the onshore wind zones
        return (pecd_cfg.get("spatial_resolution_wind") or "peon").lower()
    if fam == "solar":
        return (pecd_cfg.get("spatial_resolution_solar") or "szon").lower()
    return "szon"  # hydro is SZON-only


def _resolve_file_version(code: str, pecd_cfg: dict) -> str:
    """fv2 for bcc_csm2_mr+ssp2_4_5 wind; otherwise the configured file_version."""
    fv = (pecd_cfg.get("file_version") or "fv1").lower()
    if (
        CODE_FAMILY[code] in ("wind_on", "wind_off")
        and (pecd_cfg.get("origin") == "bcc_csm2_mr")
        and (pecd_cfg.get("emission_scenario") == "ssp2_4_5")
    ):
        return "fv2"
    return fv


def _build_request(code: str, year: int, pecd_cfg: dict) -> dict:
    """Build one CDS request dict for a (data code, climate year)."""
    fam = CODE_FAMILY[code]
    level = _level_for(code, pecd_cfg)

    if fam in ("wind_on", "wind_off") and level in ("nut0", "nuts_0"):
        raise ValueError(
            f"PECD: WON/WOF must not be requested at NUT0 (deprecated). "
            f"Use peon/p2on (onshore) or peof/p2of (offshore). Got level '{level}'."
        )

    request: dict = {
        "pecd_version": pecd_cfg.get("pecd_version", "pecd4_2"),
        "temporal_period": [pecd_cfg.get("temporal_period", "historical")],
        "origin": [pecd_cfg.get("origin", "era5_reanalysis")],
        "variable": [CODE_TO_API[code]],
        "spatial_resolution": [level],
        "year": [str(year)],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "file_version": [_resolve_file_version(code, pecd_cfg)],
    }

    if pecd_cfg.get("temporal_period") == "future_projections":
        ssp = pecd_cfg.get("emission_scenario")
        if not ssp:
            raise ValueError(
                "PECD: emission_scenario is required when temporal_period == "
                "future_projections (e.g. ssp2_4_5)."
            )
        request["emission_scenario"] = [ssp]

    if fam in ("wind_on", "wind_off", "solar", "csp"):
        if fam == "wind_on":
            request["technology"] = [str(pecd_cfg.get("wind_technology_onshore", catalog.DEFAULT_TECHNOLOGY["wind_on"]))]
        elif fam == "wind_off":
            request["technology"] = [str(pecd_cfg.get("wind_technology_offshore", catalog.DEFAULT_TECHNOLOGY["wind_off"]))]
        elif fam == "solar":
            request["technology"] = [str(pecd_cfg.get("solar_technology", DEFAULT_SOLAR_TECHNOLOGY))]
        else:  # csp — technology codes aren't pinned in the PUG; require an explicit one
            csp_tech = pecd_cfg.get("csp_technology")
            if not csp_tech:
                raise ValueError("PECD: csp_technology is required to fetch CSP (see the CDS form for codes).")
            request["technology"] = [str(csp_tech)]

    if fam in ("wind_on", "wind_off"):
        request["energy_scenario"] = [pecd_cfg.get("energy_scenario", "resource_grade_b")]

    return request


def _target_basename(code: str, year: int, pecd_cfg: dict) -> str:
    """Deterministic, glob-friendly base name the process step can rely on.

    Pattern: ``pecd42_<CODE>_<LEVEL>_<YEAR>[_<origin>_<ssp>]_<fv>`` (the data code
    and zone codes inside the CSV are what the process modules key on; this name
    just makes staging predictable and idempotent for caching).
    """
    level = _level_for(code, pecd_cfg)
    parts = ["pecd42", code, level, str(year)]
    if pecd_cfg.get("temporal_period") == "future_projections":
        parts += [str(pecd_cfg.get("origin", "")), str(pecd_cfg.get("emission_scenario", ""))]
    parts.append(_resolve_file_version(code, pecd_cfg))
    return "_".join(p for p in parts if p)


def _build_client():
    """Create a cdsapi client from ~/.cdsapirc or CDSAPI_URL/CDSAPI_KEY (.env).

    Raises a single actionable error if no credentials are available — this is a
    one-time setup problem, distinct from per-product fail-soft.
    """
    import cdsapi  # lazy: keep the module importable without the optional dep

    if (Path.home() / ".cdsapirc").exists():
        return cdsapi.Client()
    url, key = os.getenv("CDSAPI_URL"), os.getenv("CDSAPI_KEY")
    if url and key:
        return cdsapi.Client(url=url, key=key)
    raise RuntimeError(
        "No CDS credentials found. To fetch PECD 4.2:\n"
        "  1. Register at https://cds.climate.copernicus.eu and create an API key;\n"
        "  2. Accept the Terms & Conditions on the 'sis-energy-pecd' dataset page;\n"
        "  3. Put the key in ~/.cdsapirc, or set CDSAPI_URL and CDSAPI_KEY in .env."
    )


def _normalize_download(tmp_path: Path, base: str) -> list[str]:
    """Turn a raw CDS download (zip or csv) into deterministically named CSV(s).

    Returns the list of staged file names. Removes the temporary download.
    """
    staged: list[str] = []
    if zipfile.is_zipfile(tmp_path):
        with zipfile.ZipFile(tmp_path) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            csvs = [m for m in members if m.lower().endswith(".csv")] or members
            for i, member in enumerate(csvs):
                name = f"{base}.csv" if len(csvs) == 1 else f"{base}__{Path(member).name}"
                dest = TARGET_DIR / name
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
                staged.append(name)
    else:
        dest = TARGET_DIR / f"{base}.csv"
        tmp_path.replace(dest)
        staged.append(dest.name)
    tmp_path.unlink(missing_ok=True)
    return staged


def fetch_pecd_data(config: dict) -> None:
    """Stage the PECD 4.2 raw CSVs required by ``config`` under ``pecd_study/``.

    Args:
        config: The full SupplyForge config dict (needs ``res_source``,
            ``hydro_source`` and the ``pecd`` block).
    """
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    validate_pecd_config(config)   # fail-fast on a malformed scenario (catalog-checked)
    pecd_cfg = config.get("pecd") or {}
    codes = _required_codes(config)
    years = pecd_cfg.get("climate_years", []) or []

    if not codes:
        logger.info("PECD: neither res_source nor hydro_source selects PECD; nothing to fetch.")
        _write_manifest(config, [])
        return
    if not years:
        logger.warning("PECD: pecd.climate_years is empty; nothing to fetch.")
        _write_manifest(config, [])
        return

    logger.info("PECD: fetching codes %s for climate years %s", codes, years)
    client = _build_client()

    staged: list[str] = []
    for code in codes:
        for year in years:
            base = _target_basename(code, year, pecd_cfg)
            final_csv = TARGET_DIR / f"{base}.csv"
            if final_csv.exists():
                logger.info("PECD: %s already staged, skipping.", final_csv.name)
                staged.append(final_csv.name)
                continue
            try:
                request = _build_request(code, year, pecd_cfg)
            except ValueError as exc:
                logger.error("PECD: bad request for %s %s: %s", code, year, exc)
                continue
            tmp = TARGET_DIR / f"{base}.download"
            try:
                logger.info("PECD: retrieving %s (%s) for %s ...", code, CODE_TO_API[code], year)
                client.retrieve(DATASET_ID, request).download(str(tmp))
                staged += _normalize_download(tmp, base)
            except Exception as exc:  # noqa: BLE001 - fail-soft per (code, year)
                logger.warning("PECD: retrieve failed for %s %s: %s (skipping).", code, year, exc)
                tmp.unlink(missing_ok=True)
                continue

    _write_manifest(config, staged)
    logger.info("PECD: staged %d file(s) under %s", len(staged), TARGET_DIR)


def _write_manifest(config: dict, staged: list[str]) -> None:
    """Write download.txt with a provenance header + one staged filename per line."""
    pecd_cfg = config.get("pecd") or {}
    header = [
        "# PECD 4.2 staged data (supplyforge fetch/pecd/study.py)",
        f"# pecd_version={pecd_cfg.get('pecd_version', 'pecd4_2')}",
        f"# temporal_period={pecd_cfg.get('temporal_period')}",
        f"# origin={pecd_cfg.get('origin')}",
        f"# emission_scenario={pecd_cfg.get('emission_scenario')}",
        f"# file_version={pecd_cfg.get('file_version')}",
        f"# spatial_resolution_wind={pecd_cfg.get('spatial_resolution_wind')}",
        f"# spatial_resolution_solar={pecd_cfg.get('spatial_resolution_solar')}  hydro=szon",
        f"# climate_years={pecd_cfg.get('climate_years')}",
        "# Provenance note: hydro inflows are a statistical (RF) reconstruction; "
        "reservoir inflow is zero-clipped at source; some zones carry temporary "
        "corrections / IC-rescaling (PUG Table 2.10).",
    ]
    DOWNLOAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_LOG.write_text("\n".join(header + sorted(set(staged))) + "\n")


if __name__ == "__main__":
    try:
        cfg = snakemake.config  # type: ignore[name-defined]  # injected by Snakemake
    except NameError:
        import yaml
        logger.info("Not running under Snakemake; loading config/config.yaml for a standalone run.")
        cfg = yaml.safe_load((PACKAGE_DIR / "config" / "config.yaml").read_text())
    fetch_pecd_data(cfg)
