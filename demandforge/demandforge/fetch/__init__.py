"""Data acquisition utilities for DemandForge.

Submodules provide:
- entsoe_load_curves: ENTSO-E load data download and parquet export
- era5: ERA5 temperature retrieval by bounding box or country
- country_borders: EU country borders GeoPackage downloader
- ghsl_population_europe: GHSL population tiles download and merge

Concerning hydrogen, the submodules provide :

    industry_data:      JRC-IDEES, TYNDP, USGS, Eurostat raw data acquisition.
    bunkering_weights:  EU port bunkering shares for maritime allocation.

Usage example::

    from demandforge.fetch import (
        fetch_steel_production,
        fetch_ammonia_production,
        EU27_COUNTRIES,
    )
    df = fetch_ammonia_production(countries=["FR", "DE"])

    # Auto-download from real data sources:
    from demandforge.fetch import fetch_jrc_idees_steel_base
    df = fetch_jrc_idees_steel_base(jrc_idees_path="auto", countries=["FR", "DE"])
"""
from demandforge.fetch.industry_data import (
    EU27_COUNTRIES,
    _CRUDE_STEEL_2019_KT,
    _EAF_SHARE_2019,
    _EUROSTAT_REFINERY_OUTPUT_2019_KTOE,
    fetch_all_industry_data,
    fetch_ammonia_production,
    fetch_eurostat_refinery_output,
    fetch_jrc_idees_steel_base,
    fetch_methanol_production,
    fetch_olefins_production,
    fetch_refinery_output,
    fetch_steel_production,
    fetch_tyndp_demand_parameters,
    # Data source URL constants
    JRC_IDEES_BASE_URL,
    TYNDP_2024_URL,
    EUROSTAT_NRG_BAL_TSV_URL,
    EUROSTAT_NRG_BAL_JSON_URL,
    # JRC-IDEES utilities (for advanced users)
    _JRC_IDEES_SECTOR_CODES,
    _JRC_IDEES_FILE_VERSION,
    _download_jrc_idees_country,
    _read_jrc_idees_value,
    _read_jrc_idees_series,
    _download_tyndp_file,
    _download_eurostat_tsv,
)
from demandforge.fetch.bunkering_weights import fetch_bunkering_weights

__all__ = [
    # Constants
    "_CRUDE_STEEL_2019_KT",
    "_EAF_SHARE_2019",
    "_EUROSTAT_REFINERY_OUTPUT_2019_KTOE",
    "EU27_COUNTRIES",
    "JRC_IDEES_BASE_URL",
    "TYNDP_2024_URL",
    "EUROSTAT_NRG_BAL_TSV_URL",
    "EUROSTAT_NRG_BAL_JSON_URL",
    # JRC-IDEES internals
    "_JRC_IDEES_SECTOR_CODES",
    "_JRC_IDEES_FILE_VERSION",
    # Fetch functions
    "fetch_all_industry_data",
    "fetch_ammonia_production",
    "fetch_bunkering_weights",
    "fetch_eurostat_refinery_output",
    "fetch_jrc_idees_steel_base",
    "fetch_methanol_production",
    "fetch_olefins_production",
    "fetch_refinery_output",
    "fetch_steel_production",
    "fetch_tyndp_demand_parameters",
    # Download utilities
    "_download_jrc_idees_country",
    "_read_jrc_idees_value",
    "_read_jrc_idees_series",
    "_download_tyndp_file",
    "_download_eurostat_tsv",
]
