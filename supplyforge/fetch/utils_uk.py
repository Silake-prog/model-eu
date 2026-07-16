"""
Shared constants and utilities for UK-specific data fetching.

The UK left ENTSO-E after Brexit. For years before UK_ENTSOE_CUTOFF_YEAR
ENTSO-E data is used (country code GB); for years from the cutoff onward,
UK-native sources (Elexon EIS, Frankfurter exchange rate API) are used.
"""

# Years >= this use UK-native sources; earlier years use ENTSO-E with code "GB"
UK_ENTSOE_CUTOFF_YEAR = 2020

# ENTSO-E area code for Great Britain (used for pre-cutoff queries)
ENTSOE_COUNTRY_CODE_FOR_UK = "GB"

# Elexon Insights Solution (EIS) API base URL — free, no authentication required
ELEXON_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"

def elexon_date_chunks(year: int, chunk_days: int = 7):
    """
    Yield ``(date_from, date_to)`` string pairs that cover a full calendar year
    in non-overlapping windows of at most ``chunk_days`` days.

    The Elexon EIS API enforces a maximum date range of 7 days per request for
    high-frequency datasets such as FUELINST and MID.
    """
    from datetime import date, timedelta

    current = date(year, 1, 1)
    year_end = date(year, 12, 31)
    while current <= year_end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), year_end)
        yield current.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        current = chunk_end + timedelta(days=1)


# Mapping from Elexon FUELINST fuel types to ENTSO-E technology names.
# Notes:
#   - CCGT and OCGT both map to "Fossil Gas" (summed in aggregation step).
#   - WIND covers onshore + offshore combined; Elexon does not separate them.
#     All UK wind capacity should therefore be placed under "Wind Onshore" in
#     the static installed-capacity table to keep capacity-factor math consistent.
#   - NPSHYD = Non-Pumped Storage Hydro ≈ run-of-river and pondage.
UK_FUEL_TO_ENTSOE: dict[str, str] = {
    "CCGT":    "Fossil Gas",
    "OCGT":    "Fossil Gas",
    "OIL":     "Fossil Oil",
    "COAL":    "Fossil Hard coal",
    "NUCLEAR": "Nuclear",
    "WIND":    "Wind Onshore",
    "NPSHYD":  "Hydro Run-of-river and poundage",
    "PS":      "Hydro Pumped Storage",
    "BIOMASS": "Biomass",
    "OTHER":   "Other",
    "SOLAR":   "Solar",
}

# Elexon fuel type codes that represent cross-border interconnector flows, not generation.
# These are excluded from the generation dataset.
ELEXON_INTERCONNECTOR_FUELS: frozenset[str] = frozenset({
    "INTEW", "INTIRL", "INTNED", "INTFR", "INTNEM", "INTNSL", "INTVKL",
})

# Installed generation capacity by year in MW.
# Source: DUKES Table 5.10 (BEIS / DESNZ), published each July for the previous year.
# "Wind Onshore" column aggregates all UK wind (onshore + offshore) because Elexon
# FUELINST reports combined wind generation.
# Update this table annually after each DUKES publication.
UK_INSTALLED_CAPACITY_MW: dict[int, dict[str, float]] = {
    2020: {
        "Fossil Gas":                      37_400.0,
        "Nuclear":                          6_375.0,
        "Wind Onshore":                    23_900.0,   # 13 700 onshore + 10 200 offshore
        "Solar":                           13_500.0,
        "Biomass":                          3_400.0,
        "Hydro Run-of-river and poundage":  1_450.0,
        "Hydro Pumped Storage":             2_800.0,
        "Fossil Hard coal":                 4_000.0,
        "Fossil Oil":                         900.0,
        "Other":                              600.0,
    },
    2021: {
        "Fossil Gas":                      37_500.0,
        "Nuclear":                          6_375.0,
        "Wind Onshore":                    25_100.0,   # 14 200 + 10 900
        "Solar":                           14_100.0,
        "Biomass":                          3_600.0,
        "Hydro Run-of-river and poundage":  1_450.0,
        "Hydro Pumped Storage":             2_800.0,
        "Fossil Hard coal":                 2_500.0,
        "Fossil Oil":                         900.0,
        "Other":                              600.0,
    },
    2022: {
        "Fossil Gas":                      37_000.0,
        "Nuclear":                          5_800.0,
        "Wind Onshore":                    28_100.0,   # 14 500 + 13 600
        "Solar":                           14_600.0,
        "Biomass":                          4_000.0,
        "Hydro Run-of-river and poundage":  1_450.0,
        "Hydro Pumped Storage":             2_800.0,
        "Fossil Hard coal":                 1_800.0,
        "Fossil Oil":                         900.0,
        "Other":                              600.0,
    },
    2023: {
        "Fossil Gas":                      36_500.0,
        "Nuclear":                          5_500.0,
        "Wind Onshore":                    29_700.0,   # 15 200 + 14 500
        "Solar":                           15_200.0,
        "Biomass":                          4_100.0,
        "Hydro Run-of-river and poundage":  1_450.0,
        "Hydro Pumped Storage":             3_000.0,
        "Fossil Hard coal":                 1_000.0,
        "Fossil Oil":                         900.0,
        "Other":                              700.0,
    },
    2024: {
        "Fossil Gas":                      36_000.0,
        "Nuclear":                          5_200.0,
        "Wind Onshore":                    32_000.0,   # 16 000 + 16 000
        "Solar":                           16_500.0,
        "Biomass":                          4_200.0,
        "Hydro Run-of-river and poundage":  1_450.0,
        "Hydro Pumped Storage":             3_000.0,
        "Fossil Hard coal":                     0.0,
        "Fossil Oil":                         900.0,
        "Other":                              700.0,
    },
}
