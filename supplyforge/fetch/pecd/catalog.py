r"""
PECD 4.2 data catalog — the authoritative, programmatic reference of everything
fetchable from the Copernicus CDS dataset ``sis-energy-pecd``.

This module is the single source of truth for the PECD options (variables, spatial
levels, technology codes, climate models, emission scenarios, energy scenarios,
year ranges, file-version rules). :mod:`supplyforge.fetch.pecd.study` imports it to
build and **validate** requests, and :func:`describe` exposes it for discovery
(e.g. in a notebook: "what can I fetch?").

Two product families
---------------------

1. **Energy products** (aggregated CSV — what supplyforge fetches & processes):
   solar/CSP/wind capacity factors and the hydro energy variables. These carry the
   confirmed CDS ``variable`` long-names and are listed in :data:`ENERGY_VARIABLES`.
2. **Raw climate fields** (gridded NetCDF — `area` sub-region, `0_25_degree`): wind
   speeds, 2 m temperature, surface solar radiation, precipitation. These feed an
   ERA5/atlite-style engine (Phase 2) and are listed, for completeness, in
   :data:`RAW_CLIMATE_FIELDS`. PECD also publishes aggregated **temperature**
   (``TAW`` population-weighted, ``TA``) — see :data:`TEMPERATURE_NOTE`; the exact
   aggregated API strings should be confirmed against the live CDS form before use.

Of the energy products, only ``SPV``/``WON``/``WOF`` (capacity factors) and ``HRI``
(reservoir inflow) — plus ``HRO``/``HPO`` for the PECD run-of-river CF — are wired
into the model today. The rest are *fetchable for analysis/export* via the fetcher's
``extra_variables`` option.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Energy products (aggregated CSV). code -> spec.
#   api       : CDS `variable` long-name (the API value, not the short code)
#   family    : drives spatial level, technology and energy_scenario handling
#   kind      : "cf" (capacity factor [0,1]) | "energy" (MWh)
#   resolution: native temporal resolution
#   levels    : valid CDS spatial_resolution values for this variable
#   modelled  : True if wired into the supplyforge model today
# ---------------------------------------------------------------------------
ENERGY_VARIABLES: dict[str, dict] = {
    "SPV": dict(api="solar_photovoltaic_generation_capacity_factor", family="solar",
                kind="cf", resolution="hourly", levels=["szon", "nuts_0", "nuts_2", "peon", "p2on"], modelled=True),
    "CSP": dict(api="concentrated_solar_generation_capacity_factor", family="csp",
                kind="cf", resolution="hourly", levels=["peon", "p2on"], modelled=False),
    "WON": dict(api="wind_power_onshore_capacity_factor", family="wind_on",
                kind="cf", resolution="hourly", levels=["peon", "p2on"], modelled=True),  # NUT0 deprecated
    "WOF": dict(api="wind_power_offshore_capacity_factor", family="wind_off",
                kind="cf", resolution="hourly", levels=["peof", "p2of"], modelled=True),
    "HRI": dict(api="hydropower_reservoir_inflow", family="hydro",
                kind="energy", resolution="weekly", levels=["szon"], modelled=True),
    "HRR": dict(api="hydropower_run_of_river_inflow", family="hydro",
                kind="energy", resolution="weekly", levels=["szon"], modelled=False),
    "HPI": dict(api="hydropower_run_of_river_with_pondage_inflow", family="hydro",
                kind="energy", resolution="weekly", levels=["szon"], modelled=False),
    "HOL": dict(api="hydropower_open_loop_pumped_storage_inflow", family="hydro",
                kind="energy", resolution="weekly", levels=["szon"], modelled=False),
    "HRG": dict(api="hydropower_reservoir_generation", family="hydro",
                kind="energy", resolution="weekly", levels=["szon"], modelled=False),
    "HRO": dict(api="hydropower_run_of_river_generation", family="hydro",
                kind="energy", resolution="weekly", levels=["szon"], modelled=True),   # -> RoR CF
    "HPO": dict(api="hydropower_run_of_river_with_pondage_generation", family="hydro",
                kind="energy", resolution="weekly", levels=["szon"], modelled=True),   # -> RoR CF
}

# Derived lookups (back-compat with study.py).
CODE_TO_API: dict[str, str] = {c: s["api"] for c, s in ENERGY_VARIABLES.items()}
API_TO_CODE: dict[str, str] = {v: k for k, v in CODE_TO_API.items()}
FAMILY: dict[str, str] = {c: s["family"] for c, s in ENERGY_VARIABLES.items()}
HYDRO_LONGNAME_TO_CODE: dict[str, str] = {
    s["api"]: c for c, s in ENERGY_VARIABLES.items() if s["family"] == "hydro"
}
CF_CODES = tuple(c for c, s in ENERGY_VARIABLES.items() if s["kind"] == "cf")
HYDRO_CODES = tuple(c for c, s in ENERGY_VARIABLES.items() if s["family"] == "hydro")

# Onshore wind level -> matching offshore level.
OFFSHORE_LEVEL = {"peon": "peof", "p2on": "p2of"}

# ---------------------------------------------------------------------------
# Spatial levels (CDS spatial_resolution enum -> meaning)
# ---------------------------------------------------------------------------
SPATIAL_LEVELS: dict[str, str] = {
    "nuts_0": "country (NUTS-0)",
    "nuts_2": "NUTS-2 region",
    "szon": "onshore bidding zone (ENTSO-E)",
    "szof": "offshore bidding zone",
    "peon": "ERAA-2025 onshore wind zone",
    "peof": "ERAA-2025 offshore wind zone",
    "p2on": "ERAA-2026 onshore wind zone",
    "p2of": "ERAA-2026 offshore wind zone",
    "0_25_degree": "gridded 0.25° (raw fields)",
    "city": "city points (raw fields)",
}

# ---------------------------------------------------------------------------
# Origins, scenarios, technologies, years
# ---------------------------------------------------------------------------
HISTORICAL_ORIGIN = "era5_reanalysis"
CLIMATE_MODELS: tuple[str, ...] = (
    "cmcc_cm2_sr5", "ec_earth3", "mpi_esm1_2_hr", "awi_cm_1_1_mr", "bcc_csm2_mr", "mri_esm2_0",
)
EMISSION_SCENARIOS: tuple[str, ...] = ("ssp1_2_6", "ssp2_4_5", "ssp3_7_0", "ssp5_8_5")
ENERGY_SCENARIOS: tuple[str, ...] = ("resource_grade_a", "resource_grade_b")
FILE_VERSIONS: tuple[str, ...] = ("fv1", "fv2")

# Technology codes per family (only the documented existing-fleet codes are named;
# the CDS form lists further variants).
TECHNOLOGY_CODES: dict[str, dict[str, str]] = {
    "wind_on": {"30": "existing onshore fleet"},
    "wind_off": {"20": "existing offshore fleet"},
    "solar": {"60": "existing PV fleet", "61": "PV variant", "62": "PV variant", "63": "PV variant"},
    "csp": {},  # CSP technology codes: see the CDS form (not pinned in the PUG excerpt)
}
DEFAULT_TECHNOLOGY: dict[str, str] = {"wind_on": "30", "wind_off": "20", "solar": "60"}

YEAR_RANGES: dict[str, tuple[int, int | None]] = {
    "historical": (1950, None),          # 1950 – near-present (updated monthly)
    "future_projections": (2015, 2100),
}
TEMPORAL_PERIODS: tuple[str, ...] = ("historical", "future_projections")

# Use fv2 (reprocessed) for this combination's wind; otherwise the configured fv.
FV2_REQUIRED = {"origin": "bcc_csm2_mr", "emission_scenario": "ssp2_4_5", "families": ("wind_on", "wind_off")}

# ---------------------------------------------------------------------------
# Raw climate fields (gridded NetCDF) — for an ERA5/atlite-style engine (Phase 2).
# Confirmed CDS strings (from the dataset form). Not wired into supplyforge today.
# ---------------------------------------------------------------------------
RAW_CLIMATE_FIELDS: dict[str, str] = {
    "100m_wind_speed": "100 m wind speed",
    "10m_wind_speed": "10 m wind speed",
    "2m_temperature": "2 m air temperature",
    "surface_solar_radiation_downwards": "surface solar radiation downwards (SSRD)",
    "total_precipitation": "total precipitation",
}
TEMPERATURE_NOTE = (
    "PECD 4.2 also publishes aggregated temperature: TAW (population-weighted, SZON) "
    "and TA. TAW is a candidate shared climate source for demandforge (PROMPT §7). The "
    "exact aggregated CDS `variable` strings are not pinned here — confirm them on the "
    "dataset form before fetching."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def resolve_code(name: str) -> str:
    """Map a data code (``WON``) or a CDS long-name to the canonical data code."""
    if name in ENERGY_VARIABLES:
        return name
    if name in API_TO_CODE:
        return API_TO_CODE[name]
    raise ValueError(f"Unknown PECD variable '{name}'. Known codes: {sorted(ENERGY_VARIABLES)}.")


def fv2_applies(code: str, origin, emission_scenario) -> bool:
    """Whether fv2 should be forced for this (variable, origin, SSP)."""
    return (
        FAMILY.get(code) in FV2_REQUIRED["families"]
        and origin == FV2_REQUIRED["origin"]
        and emission_scenario == FV2_REQUIRED["emission_scenario"]
    )


def validate_pecd_config(config: dict) -> None:
    """Validate the ``pecd`` config block against the catalog; raise on any problem.

    This is a *configuration* check (fail-fast on a malformed scenario), distinct
    from the per-request fail-soft in the fetcher. Collects all issues and raises a
    single ``ValueError``.
    """
    pecd = config.get("pecd") or {}
    issues: list[str] = []

    tp = pecd.get("temporal_period", "historical")
    if tp not in TEMPORAL_PERIODS:
        issues.append(f"temporal_period must be one of {TEMPORAL_PERIODS}, got '{tp}'.")

    origin = pecd.get("origin", HISTORICAL_ORIGIN)
    ssp = pecd.get("emission_scenario")
    if tp == "future_projections":
        if origin not in CLIMATE_MODELS:
            issues.append(f"projection origin (climate model) must be one of {CLIMATE_MODELS}, got '{origin}'.")
        if not ssp:
            issues.append(f"emission_scenario is required for future_projections (one of {EMISSION_SCENARIOS}).")
        elif ssp not in EMISSION_SCENARIOS:
            issues.append(f"emission_scenario must be one of {EMISSION_SCENARIOS}, got '{ssp}'.")
    elif tp == "historical":
        if origin != HISTORICAL_ORIGIN:
            issues.append(f"historical origin must be '{HISTORICAL_ORIGIN}', got '{origin}'.")

    fv = pecd.get("file_version", "fv1")
    if fv not in FILE_VERSIONS:
        issues.append(f"file_version must be one of {FILE_VERSIONS}, got '{fv}'.")

    wind_level = (pecd.get("spatial_resolution_wind") or "peon").lower()
    if wind_level not in ("peon", "p2on"):
        issues.append(f"spatial_resolution_wind must be peon|p2on (NUT0 wind is deprecated), got '{wind_level}'.")
    solar_level = (pecd.get("spatial_resolution_solar") or "szon").lower()
    if solar_level not in ENERGY_VARIABLES["SPV"]["levels"]:
        issues.append(f"spatial_resolution_solar must be one of {ENERGY_VARIABLES['SPV']['levels']}, got '{solar_level}'.")

    es = pecd.get("energy_scenario", "resource_grade_b")
    if es not in ENERGY_SCENARIOS:
        issues.append(f"energy_scenario must be one of {ENERGY_SCENARIOS}, got '{es}'.")

    lo, hi = YEAR_RANGES.get(tp, (1950, None))
    for y in pecd.get("climate_years", []) or []:
        if not isinstance(y, int):
            issues.append(f"climate_years must be integers, got {y!r}.")
        elif y < lo or (hi is not None and y > hi):
            issues.append(f"climate year {y} is outside the {tp} range [{lo}, {hi or 'near-present'}].")

    for ln in pecd.get("variables_hydro", []) or []:
        if ln not in HYDRO_LONGNAME_TO_CODE:
            issues.append(f"variables_hydro entry '{ln}' is not a known hydro variable {sorted(HYDRO_LONGNAME_TO_CODE)}.")

    for v in pecd.get("extra_variables", []) or []:
        if v not in ENERGY_VARIABLES and v not in API_TO_CODE:
            issues.append(f"extra_variables entry '{v}' is not a known PECD variable.")

    if issues:
        raise ValueError("Invalid PECD config:\n  - " + "\n  - ".join(issues))


def describe() -> dict:
    """Return the catalog as a plain dict (for discovery / display)."""
    return {
        "energy_variables": {
            c: {"long_name": s["api"], "kind": s["kind"], "resolution": s["resolution"],
                "levels": s["levels"], "modelled": s["modelled"]}
            for c, s in ENERGY_VARIABLES.items()
        },
        "spatial_levels": SPATIAL_LEVELS,
        "historical_origin": HISTORICAL_ORIGIN,
        "climate_models": list(CLIMATE_MODELS),
        "emission_scenarios": list(EMISSION_SCENARIOS),
        "energy_scenarios": list(ENERGY_SCENARIOS),
        "technology_codes": TECHNOLOGY_CODES,
        "file_versions": list(FILE_VERSIONS),
        "year_ranges": {k: list(v) for k, v in YEAR_RANGES.items()},
        "raw_climate_fields": RAW_CLIMATE_FIELDS,
        "temperature_note": TEMPERATURE_NOTE,
    }


def print_catalog() -> None:
    """Pretty-print what's fetchable from PECD 4.2 (notebook-friendly discovery)."""
    print("PECD 4.2 — fetchable energy products (aggregated CSV):")
    for c, s in ENERGY_VARIABLES.items():
        flag = " [modelled]" if s["modelled"] else ""
        print(f"  {c:<4} {s['kind']:<6} {s['resolution']:<7} levels={s['levels']}  {s['api']}{flag}")
    print(f"\nSpatial levels: {', '.join(SPATIAL_LEVELS)}")
    print(f"Historical origin: {HISTORICAL_ORIGIN}")
    print(f"Climate models (projections): {', '.join(CLIMATE_MODELS)}")
    print(f"Emission scenarios: {', '.join(EMISSION_SCENARIOS)}")
    print(f"Energy (resource-grade) scenarios: {', '.join(ENERGY_SCENARIOS)}")
    print(f"Year ranges: {YEAR_RANGES}")
    print(f"\nRaw climate fields (gridded, Phase-2 / atlite): {', '.join(RAW_CLIMATE_FIELDS)}")
    print(f"\nNote: {TEMPERATURE_NOTE}")


__all__ = [
    "ENERGY_VARIABLES", "CODE_TO_API", "API_TO_CODE", "FAMILY", "HYDRO_LONGNAME_TO_CODE",
    "CF_CODES", "HYDRO_CODES", "OFFSHORE_LEVEL", "SPATIAL_LEVELS", "HISTORICAL_ORIGIN",
    "CLIMATE_MODELS", "EMISSION_SCENARIOS", "ENERGY_SCENARIOS", "FILE_VERSIONS",
    "TECHNOLOGY_CODES", "DEFAULT_TECHNOLOGY", "YEAR_RANGES", "TEMPORAL_PERIODS",
    "RAW_CLIMATE_FIELDS", "TEMPERATURE_NOTE",
    "resolve_code", "fv2_applies", "validate_pecd_config", "describe", "print_catalog",
]
