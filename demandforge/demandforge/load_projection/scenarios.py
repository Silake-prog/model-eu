"""Scenario bundle management — bridge between YAML definitions and projection functions.

This module provides the main user-facing entry point for running hydrogen
demand projections.  It reads named scenario bundles from
``scenario_registry.yaml`` and dispatches their parameters to the six
sector projection functions in :mod:`hydrogen`.

The YAML file stores **scientific choices** (shares, ramp timings, growth
rates).  **Infrastructure parameters** (data file paths, CONCAWE unit
configs) are passed by the caller — they are not scenario-dependent.

Dispatch pipeline:
    1. Load the YAML bundle and resolve CONCAWE ``units_config``.
    2. Apply the petrochem naphtha boundary correction
       (:func:`~demandforge.process.refinery.apply_petrochem_naphtha_correction`)
       to the ``units_config`` when the olefins sector is active.  This
       prevents double-counting of naphtha hydrotreater H₂ between the
       refinery and olefins modules.
    3. Run sectors in dependency order (refinery before eSAF; piping
       refinery output → eSAF automatically).
    4. For olefins, inject the nested ``pathways`` dict from YAML directly
       as a keyword argument — ``project_olefins_h2_demand()`` unpacks it.
    5. Concatenate, sanity-check, and return the standardised DataFrame.

Typical usage::

    from demandforge.load_projection.scenarios import load_bundle, list_bundles

    # Inspect available scenario bundles
    for name, desc in list_bundles().items():
        print(f"{name}: {desc}")

    # Run the central scenario for France and Germany
    df = load_bundle("central", countries=["FR", "DE"])

    # Override one parameter while keeping the rest of the bundle
    df = load_bundle("central", countries=["FR", "DE"],
                     steel={"dri_share_2050": 0.80})

    # Get raw parameters without running (for inspection or modification)
    params = get_bundle_params("central")
    params["ammonia"]["h2_route_share_end"] = 0.90
    df = load_bundle("central", countries=["FR", "DE"], ammonia=params["ammonia"])

Authors:
    Simon Brigode — PERSEE Lab, Mines Paris PSL
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from demandforge.load_projection.constants import (
    H2_LHV_MWH_PER_T,
    PETROCHEM_NAPHTHA_FRACTION,
    STEAM_CRACKER_OLEFIN_YIELD,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML location
# ---------------------------------------------------------------------------
_DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "scenario_registry.yaml"
)

# All sectors in execution order.
# Refinery MUST run before eSAF because eSAF requires the refinery DataFrame.
_SECTOR_ORDER: list[str] = [
    "ammonia",
    "refinery",
    "esaf",
    "maritime",
    "olefins",
    "steel",
]

# Sectors that can run without any infrastructure parameters.
_STANDALONE_SECTORS: set[str] = {"ammonia", "maritime", "olefins"}


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def _load_registry(registry_path: Path | None = None) -> dict[str, Any]:
    """Read and validate the scenario registry YAML file.

    Args:
        registry_path: Path to the YAML file.  If None, uses the default
            location next to the package root.

    Returns:
        Parsed YAML dictionary with a ``"bundles"`` key.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If the YAML file is malformed or missing ``"bundles"``.
    """
    path = registry_path or _DEFAULT_REGISTRY_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Scenario registry not found at {path}.  "
            f"Expected a YAML file with a 'bundles:' key."
        )
    with open(path) as f:
        registry = yaml.safe_load(f)
    if not isinstance(registry, dict) or "bundles" not in registry:
        raise ValueError(
            f"Scenario registry at {path} is malformed — "
            f"expected a top-level 'bundles:' key."
        )
    return registry


# ---------------------------------------------------------------------------
# Public API: list / get / load
# ---------------------------------------------------------------------------

def list_bundles(registry_path: Path | None = None) -> dict[str, str]:
    """Return available scenario bundle names with their descriptions.

    Args:
        registry_path: Optional override for the YAML file location.

    Returns:
        ``{bundle_name: description}`` ordered as defined in the YAML.
    """
    registry = _load_registry(registry_path)
    result: dict[str, str] = {}
    for name, bundle in registry["bundles"].items():
        desc = bundle.get("description", "(no description)")
        result[name] = desc
    return result


def get_bundle_params(
    bundle_name: str,
    registry_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the raw sector parameters for a named bundle.

    Useful for inspecting or programmatically modifying parameters before
    passing them to :func:`load_bundle`.

    Args:
        bundle_name: Name of the scenario bundle (e.g. ``"central"``).
        registry_path: Optional override for the YAML file location.

    Returns:
        ``{sector_name: {param: value, ...}}`` for each sector.
        The ``"description"`` key is stripped out.

    Raises:
        KeyError: If *bundle_name* is not defined in the registry.
    """
    registry = _load_registry(registry_path)
    if bundle_name not in registry["bundles"]:
        available = list(registry["bundles"].keys())
        raise KeyError(
            f"Unknown scenario bundle '{bundle_name}'.  "
            f"Available bundles: {available}"
        )
    bundle = copy.deepcopy(registry["bundles"][bundle_name])
    bundle.pop("description", None)
    return bundle


def load_bundle(
    bundle_name: str,
    *,
    countries: str | list[str] | None = None,
    reference_year: int = 2019,
    target_year: int = 2050,
    base_scenario: str = "DE",
    sectors: list[str] | None = None,
    # Infrastructure parameters (not in YAML — caller provides these)
    units_config: dict[str, dict] | None = None,
    jet_unit_names: dict[str, str] | None = None,
    jet_yields: dict[str, float] | None = None,
    jrc_idees_path: str | Path | None = None,
    tyndp_path: str | Path | None = None,
    # Registry location
    registry_path: Path | None = None,
    # Per-sector overrides (take precedence over YAML bundle)
    **sector_overrides: dict[str, Any],
) -> pd.DataFrame:
    """Load a named scenario bundle and run all sector projections.

    This is the main entry point for running a complete hydrogen demand
    scenario.  It:

    1. Reads the named bundle from ``scenario_registry.yaml``.
    2. Resolves CONCAWE ``units_config`` (package defaults if not provided).
    3. Applies the petrochem naphtha boundary correction when the olefins
       sector is active, reducing the Naphtha Hydrotreater capacity by
       ``PETROCHEM_NAPHTHA_FRACTION`` to avoid double-counting with the
       olefins module's own fossil naphtha H₂ accounting.
    4. Runs each sector projection function in dependency order
       (refinery before eSAF), automatically piping refinery output
       to eSAF.
    5. For olefins, passes the nested ``pathways`` dict from YAML as a
       keyword argument — the projection function unpacks it into
       per-pathway share targets and ramp timings.
    6. Concatenates results and applies sanity checks.
    7. Returns a standardised DataFrame.

    Scenario parameters come from the YAML bundle.  Infrastructure
    parameters (data file paths, CONCAWE unit configs) are passed
    explicitly by the caller.  Per-sector overrides in ``**sector_overrides``
    take precedence over the YAML values.

    Args:
        bundle_name: Name of the scenario bundle (e.g. ``"central"``).
        countries: ISO-2 country code(s).  If None, all EU-27.
        reference_year: Base year for projections.
        target_year: End year for projections.
        base_scenario: TYNDP base scenario label (e.g. ``"DE"``).
        sectors: Which sectors to include.  If None, includes all sectors
            for which the required infrastructure is available.
        units_config: CONCAWE unit definitions for refinery.  If None,
            package defaults are used (``CONCAWE_MORE_MOLECULE`` or
            ``CONCAWE_MAX_ELECTRON`` depending on the bundle's
            ``molecule_scenario``).  The petrochem naphtha correction is
            applied automatically when olefins is in the sector list.
        jet_unit_names: CONCAWE unit name mapping for eSAF jet
            reconstruction.  Required if ``"esaf"`` is in *sectors*.
        jet_yields: Hydrocracker jet yield fractions for eSAF.
        jrc_idees_path: Path to JRC-IDEES steel data.  Required if
            ``"steel"`` is in *sectors*.
        tyndp_path: Path to TYNDP demand parameters file.  Required if
            ``"steel"`` is in *sectors*.
        registry_path: Override path for the YAML registry file.
        **sector_overrides: Per-sector parameter dicts that override the
            YAML values.  Example: ``steel={"dri_share_2050": 0.80}``.

    Returns:
        pd.DataFrame with columns:
            ``country``, ``year``, ``sector``,
            ``h2_demand_t_per_yr``, ``h2_demand_mwh_per_yr``

    Raises:
        KeyError: If *bundle_name* is not defined in the registry.
        ValueError: If required infrastructure parameters are missing
            for the requested sectors, or if sanity checks fail.
    """
    # --- Lazy imports to avoid circular dependencies ---
    from demandforge.load_projection.hydrogen import (
        project_ammonia_h2_demand,
        project_esaf_h2_demand,
        project_maritime_h2_demand,
        project_olefins_h2_demand,
        project_refinery_h2_demand,
        project_steel_h2_demand,
    )

    # --- Load YAML bundle ---
    bundle_params = get_bundle_params(bundle_name, registry_path=registry_path)

    # --- Resolve package defaults for refinery / eSAF / steel --------
    # When callers don't pass explicit configs, use the CONCAWE defaults
    # embedded in the process modules.  This allows all 6 sectors to run
    # without external files.
    if units_config is None:
        from demandforge.process.refinery import (
            CONCAWE_MORE_MOLECULE,
            CONCAWE_MAX_ELECTRON,
        )
        # Pick scenario from YAML bundle (defaults to more-molecule)
        mol_scenario = bundle_params.get("refinery", {}).get(
            "molecule_scenario", "more-molecule"
        )
        if mol_scenario == "max-electron":
            units_config = CONCAWE_MAX_ELECTRON
        else:
            units_config = CONCAWE_MORE_MOLECULE
        logger.info(
            f"Using package-default CONCAWE units_config "
            f"(molecule_scenario='{mol_scenario}')"
        )

    # --- Apply capacity delay (slower near-term decline) ---
    # The CONCAWE Low Carbon Pathways (2020) assume a near-term refinery
    # decline pace (~5-8 %/yr before 2030) that exceeds historical closure
    # rates.  A delay parameter shifts the trajectory forward, producing a
    # more realistic transition while preserving the long-run structural
    # narrative.  This MUST be applied before naphtha extraction and
    # petrochem correction so that all downstream modules (refinery H2,
    # eSAF, olefins) see the delayed capacity.
    _delay_years = bundle_params.get("refinery", {}).get(
        "capacity_delay_years", 0
    )
    if _delay_years > 0:
        from demandforge.process.refinery import apply_capacity_delay
        units_config = apply_capacity_delay(
            units_config,
            delay_years=_delay_years,
            reference_year=reference_year,
        )

    # --- Compute naphtha supply for olefins constraint ---
    # Must be extracted BEFORE the petrochem correction, which removes
    # the cracker-bound naphtha from the refinery config.
    _naphtha_supply_mt: np.ndarray | None = None
    years = np.arange(reference_year, target_year + 1)
    if "olefins" in (sectors or _SECTOR_ORDER):
        from demandforge.process.refinery import (
            apply_petrochem_naphtha_correction,
            get_naphtha_for_crackers,
        )
        _naphtha_supply_mt = get_naphtha_for_crackers(
            units_config, years, petrochem_fraction=PETROCHEM_NAPHTHA_FRACTION,
        )
        # --- Apply petrochem naphtha correction ---
        # The CONCAWE Naphtha Hydrotreater includes throughput for both
        # catalytic reformer feed (fuel-side) and steam cracker feed (petrochem).
        # The olefins module carries its own H₂ accounting for fossil naphtha
        # hydrotreating, so we subtract the petrochem fraction here to avoid
        # double-counting.
        units_config = apply_petrochem_naphtha_correction(
            units_config,
            petrochem_fraction=PETROCHEM_NAPHTHA_FRACTION,
        )

    if jet_unit_names is None:
        from demandforge.process.esaf import DEFAULT_JET_UNIT_NAMES
        jet_unit_names = DEFAULT_JET_UNIT_NAMES
        logger.info("Using package-default jet_unit_names")

    if jet_yields is None:
        from demandforge.process.esaf import DEFAULT_JET_YIELDS
        jet_yields = DEFAULT_JET_YIELDS
        logger.info("Using package-default jet_yields")

    # Steel: fetch_steel_production() now has a static fallback when
    # jrc_idees_path / tyndp_path are None, so steel is always available.

    # --- Resolve sector list ---
    if sectors is None:
        # Auto-detect: include sectors whose infrastructure is available.
        # With package defaults resolved above, all 6 sectors are now
        # available even without external files.
        sectors = list(_STANDALONE_SECTORS)
        if units_config is not None:
            sectors.append("refinery")
            if jet_unit_names is not None:
                sectors.append("esaf")
        # Steel is always available (static fallback in fetch layer)
        sectors.append("steel")

        # Preserve canonical order
        sectors = [s for s in _SECTOR_ORDER if s in sectors]

        if not sectors:
            # At minimum the standalone sectors are always available
            sectors = sorted(_STANDALONE_SECTORS)

        logger.info(
            f"Auto-selected sectors based on available infrastructure: {sectors}"
        )
    else:
        # Validate user-specified sectors
        for sec in sectors:
            if sec not in _SECTOR_ORDER:
                raise ValueError(
                    f"Unknown sector '{sec}'.  "
                    f"Valid sectors: {_SECTOR_ORDER}"
                )

    # --- Enforce eSAF dependency on refinery ---
    if "esaf" in sectors and "refinery" not in sectors:
        logger.info(
            "eSAF requires refinery output — adding 'refinery' to sector list."
        )
        # Insert refinery before esaf in the run order
        idx = sectors.index("esaf")
        sectors.insert(idx, "refinery")

    # --- Dispatch table ---
    dispatch: dict[str, tuple[Any, str]] = {
        "ammonia":  (project_ammonia_h2_demand,  "h2_demand_network_t_per_yr"),
        "maritime": (project_maritime_h2_demand,  "h2_demand_total_t_per_yr"),
        "olefins":  (project_olefins_h2_demand,   "h2_demand_t_per_yr"),
        "steel":    (project_steel_h2_demand,     "h2_demand_for_steel_t_per_yr"),
        "refinery": (project_refinery_h2_demand,  "h2_demand_t_per_yr"),
        "esaf":     (project_esaf_h2_demand,      "h2_demand_for_esaf_t_per_yr"),
    }

    # --- Run sectors in order ---
    all_parts: list[pd.DataFrame] = []
    refinery_df: pd.DataFrame | None = None  # captured for eSAF piping
    # Detailed per-sector frames (with per-pathway columns).  These are the
    # inputs to the CO2 feedstock module, which needs access to columns such
    # as ``h2_demand_for_e_methanol_t_per_yr`` (maritime) and
    # ``h2_demand_mto_t_per_yr`` (olefins).  The aggregated ``all_parts``
    # frame does not carry this granularity.
    sector_detail_frames: dict[str, pd.DataFrame] = {}

    for sec_name in _SECTOR_ORDER:
        if sec_name not in sectors:
            continue

        func, h2_col = dispatch[sec_name]

        # Build kwargs: common params + YAML bundle + infrastructure + overrides
        kwargs: dict[str, Any] = {
            "country": countries,
            "reference_year": reference_year,
            "target_year": target_year,
        }

        # base_scenario for sectors that use TYNDP
        if sec_name not in ("maritime",):
            kwargs["base_scenario"] = base_scenario

        # Inject YAML bundle params for this sector, filtering out keys
        # that are consumed by load_bundle() itself (not by the projection fn).
        # `pathway_shares` is accepted by project_esaf_h2_demand directly,
        # so it is NOT filtered here — it drives both the per-pathway H2
        # decomposition and (downstream) the CO2 accounting via the
        # per-pathway H2 columns exposed in the eSAF output.
        _META_KEYS = {
            "molecule_scenario",
            "capacity_delay_years",
        }
        yaml_params = bundle_params.get(sec_name, {})
        if isinstance(yaml_params, dict):
            kwargs.update({k: v for k, v in yaml_params.items()
                          if k not in _META_KEYS})

        # Inject infrastructure parameters
        if sec_name == "refinery":
            if units_config is None:
                raise ValueError(
                    f"Sector 'refinery' requires units_config (CONCAWE unit "
                    f"definitions).  Pass units_config=... to load_bundle()."
                )
            kwargs["units_config"] = units_config

        elif sec_name == "esaf":
            if refinery_df is not None:
                # Automatic piping: refinery output → eSAF input
                kwargs["refinery_df"] = refinery_df
                logger.info(
                    "Piping refinery output to eSAF "
                    f"({len(refinery_df)} rows, "
                    f"{refinery_df['country'].nunique()} countries)."
                )
            if jet_unit_names is not None:
                kwargs["jet_unit_names"] = jet_unit_names
            if jet_yields is not None:
                kwargs["jet_yields"] = jet_yields

        elif sec_name == "olefins":
            # Inject naphtha supply constraint for refinery–olefins coupling.
            # This ensures fossil olefins production cannot exceed available
            # naphtha from the CONCAWE refinery scenario.
            if _naphtha_supply_mt is not None:
                kwargs["naphtha_supply_mt"] = _naphtha_supply_mt
                kwargs["cracker_yield"] = STEAM_CRACKER_OLEFIN_YIELD

        elif sec_name == "steel":
            # Steel now has a static fallback in fetch_steel_production()
            # when jrc_idees_path / tyndp_path are None, so we always pass
            # whatever the caller gave (including None).
            kwargs["jrc_idees_path"] = jrc_idees_path
            kwargs["tyndp_path"] = tyndp_path

        # Apply per-sector overrides (take precedence over YAML)
        overrides = sector_overrides.get(sec_name, {})
        if isinstance(overrides, dict):
            kwargs.update(overrides)

        # Run the projection
        logger.info(f"Running {sec_name} projection (bundle={bundle_name})...")
        try:
            df_sec = func(**kwargs)
        except Exception as exc:
            logger.error(f"Sector {sec_name} failed: {exc}")
            raise

        if df_sec.empty:
            logger.warning(f"No data produced for sector {sec_name}, skipping.")
            continue

        # Capture refinery output for eSAF dependency
        if sec_name == "refinery":
            refinery_df = df_sec.copy()
            # Aggregate over units for the H2 summary
            df_sec = (
                df_sec.groupby(["country", "year"], as_index=False)[h2_col]
                .sum()
            )

        # Preserve the detailed per-sector frame BEFORE column reduction.
        # CO2 feedstock accounting needs per-pathway columns (e.g. MTO H2,
        # e-methanol H2) that are lost in the aggregated output below.
        sector_detail_frames[sec_name] = df_sec.copy()

        # Standardise output columns
        part = df_sec[["country", "year"]].copy()
        part["sector"] = sec_name
        part["h2_demand_t_per_yr"] = df_sec[h2_col].values
        part["h2_demand_mwh_per_yr"] = (
            part["h2_demand_t_per_yr"] * H2_LHV_MWH_PER_T
        )
        all_parts.append(part)

    if not all_parts:
        raise ValueError(
            f"No sectors produced data for bundle '{bundle_name}'.  "
            f"Requested: {sectors}"
        )

    df = pd.concat(all_parts, ignore_index=True)

    # --- Sanity checks ---
    if (df["h2_demand_t_per_yr"] < 0).any():
        neg_rows = df[df["h2_demand_t_per_yr"] < 0]
        raise ValueError(
            f"Negative H2 demand detected in {len(neg_rows)} rows.  "
            f"First offender: {neg_rows.iloc[0].to_dict()}"
        )

    by_country_year = df.groupby(["country", "year"])["h2_demand_t_per_yr"].sum()
    max_val = by_country_year.max()
    if max_val > 50e6:  # 50 Mt/yr per country
        idx = by_country_year.idxmax()
        logger.warning(
            f"Very high H2 demand for {idx}: {max_val:,.0f} t/yr.  "
            f"Check unit consistency."
        )

    eu_total_mwh = df.groupby("year")["h2_demand_mwh_per_yr"].sum()
    eu_total_twh = eu_total_mwh / 1e6
    if eu_total_twh.max() > 2000:
        logger.warning(
            f"EU-wide H2 demand exceeds 2,000 TWh/yr at peak — "
            f"max = {eu_total_twh.max():.1f} TWh.  Check assumptions."
        )

    # --- Attach CO2 feedstock demand ---
    # Only three sectors consume CO2 as feedstock: maritime (e-methanol),
    # olefins (MTO), and eSAF (Fischer-Tropsch / methanol-to-jet).  The
    # stoichiometry is handled by demandforge.process.co2_feedstock.  For
    # eSAF the scenario-dependent FT/MtJ pathway mix is resolved earlier
    # (inside project_esaf_h2_demand), which emits per-pathway H2 columns
    # that the CO2 module consumes directly.  No blended-ratio fallback
    # is needed in the default path; esaf_route_shares is therefore only
    # kept as a legacy escape hatch and is left unset here.
    from demandforge.process.co2_feedstock import attach_co2_to_bundle

    df = attach_co2_to_bundle(
        bundle_df=df,
        sector_frames=sector_detail_frames,
        esaf_route_shares=None,
    )

    logger.info(
        f"Bundle '{bundle_name}' complete: "
        f"{len(sectors)} sectors, {df['country'].nunique()} countries, "
        f"years {reference_year}-{target_year}, "
        f"{len(df)} rows."
    )

    return df
