r"""
PECD 4.2 per-zone regional registry — the real zone structure per spatial level.

PECD publishes per zone, not per country: ``SZON`` (onshore bidding zones; solar &
hydro), ``PEON``/``P2ON`` (ERAA-2025/2026 onshore wind), ``PEOF``/``P2OF`` (offshore
wind). This module is the authoritative ``country -> [zones]`` map per level, so the
zonal aggregation and the per-zone capacity weights can be validated and templated
against the real regional structure rather than only prefix-matched.

Provenance
----------
Extracted from staged PECD 4.2 aggregated CSVs: ERA5-reanalysis 2008 for
``szon``/``peon``/``peof``; the ``ec_earth3`` / ``ssp5_8_5`` 2050 projection for
``p2on``/``p2of``. The zone **structure** is stable across years/scenarios; per-zone
**feasibility** (populated vs empty placeholder — many offshore ``peof`` zones are
placeholders) is dataset-dependent and handled at runtime by the null-aware
aggregation in :func:`supplyforge.fetch.pecd.zones.capacity_weighted_cf`.

Use :func:`template_zone_capacities` to get a ``{zone: None}`` skeleton for a country
at a level, fill it with installed MW, and pass it as ``pecd.zone_capacities[<tech>]``
for precise capacity-weighted aggregation.
"""
from __future__ import annotations

PECD_ZONES = {
    "szon": {
        "AL": ["AL00"],
        "AT": ["AT00"],
        "BA": ["BA00"],
        "BE": ["BE00"],
        "BG": ["BG00"],
        "CH": ["CH00"],
        "CY": ["CY00"],
        "CZ": ["CZ00"],
        "DE": ["DE00"],
        "DK": ["DKE1", "DKW1"],
        "DZ": ["DZ00"],
        "EE": ["EE00"],
        "EG": ["EG00"],
        "ES": ["ES00"],
        "FI": ["FI00"],
        "FR": ["FR00", "FR15"],
        "GR": ["GR00", "GR03"],
        "HR": ["HR00"],
        "HU": ["HU00"],
        "IE": ["IE00"],
        "IL": ["IL00"],
        "IS": ["IS00"],
        "IT": ["ITCA", "ITCN", "ITCS", "ITN1", "ITS1", "ITSA", "ITSI"],
        "JO": ["JO00"],
        "LB": ["LB00"],
        "LT": ["LT00"],
        "LU": ["LU00"],
        "LV": ["LV00"],
        "LY": ["LY00"],
        "MA": ["MA00"],
        "MD": ["MD00"],
        "ME": ["ME00"],
        "MK": ["MK00"],
        "MT": ["MT00"],
        "NL": ["NL00"],
        "NO": ["NOM1", "NON1", "NOS0", "NOS1", "NOS2", "NOS3"],
        "PL": ["PL00"],
        "PS": ["PS00"],
        "PT": ["PT00"],
        "RO": ["RO00"],
        "RS": ["RS00"],
        "SE": ["SE01", "SE02", "SE03", "SE04"],
        "SI": ["SI00"],
        "SK": ["SK00"],
        "SY": ["SY00"],
        "TN": ["TN00"],
        "TR": ["TR00"],
        "UA": ["UA01", "UA02"],
        "UK": ["UK00", "UKNI"],
    },
    "peon": {
        "AL": ["AL00"],
        "AT": ["AT01", "AT02", "AT03"],
        "BA": ["BA00"],
        "BE": ["BE01", "BE02", "BE03"],
        "BG": ["BG01", "BG02"],
        "CH": ["CH00"],
        "CY": ["CY00"],
        "CZ": ["CZ01", "CZ02"],
        "DE": ["DE01", "DE02", "DE03", "DE04", "DE05", "DE06", "DE07"],
        "DK": ["DKE1", "DKW1"],
        "DZ": ["DZ01", "DZ02", "DZ03"],
        "EE": ["EE00"],
        "EG": ["EG00"],
        "ES": ["ES01", "ES02", "ES03", "ES04", "ES05", "ES06", "ES07", "ES08", "ES09", "ES10", "ES11", "ES12"],
        "FI": ["FI01", "FI02"],
        "FR": ["FR01", "FR02", "FR03", "FR04", "FR05", "FR06", "FR07", "FR08", "FR09", "FR10", "FR11", "FR12", "FR13", "FR14", "FR15"],
        "GR": ["GR01", "GR02", "GR03"],
        "HR": ["HR01", "HR02"],
        "HU": ["HU01", "HU02", "HU03"],
        "IE": ["IE00"],
        "IL": ["IL00"],
        "IS": ["IS00"],
        "IT": ["ITCA", "ITCN", "ITCS", "ITN1", "ITS1", "ITSA", "ITSI"],
        "JO": ["JO00"],
        "LB": ["LB00"],
        "LT": ["LT00"],
        "LU": ["LU00"],
        "LV": ["LV00"],
        "LY": ["LY01", "LY02", "LY03"],
        "MA": ["MA01", "MA02", "MA03", "MA04", "MA05", "MA06"],
        "MD": ["MD00"],
        "ME": ["ME00"],
        "MK": ["MK00"],
        "MT": ["MT00"],
        "NL": ["NL01", "NL02", "NL03", "NL04"],
        "NO": ["NOM1", "NON1", "NOS1", "NOS2", "NOS3"],
        "PL": ["PL01", "PL02", "PL03", "PL04", "PL05"],
        "PS": ["PS00"],
        "PT": ["PT01", "PT02"],
        "RO": ["RO01", "RO02", "RO03"],
        "RS": ["RS01"],
        "SE": ["SE01", "SE02", "SE03", "SE04"],
        "SI": ["SI00"],
        "SK": ["SK00"],
        "SY": ["SY00"],
        "TN": ["TN01", "TN02", "TN03", "TN04", "TN05", "TN06", "TN07", "TN08"],
        "TR": ["TR01", "TR02", "TR03", "TR04", "TR05", "TR06", "TR07", "TR08", "TR09", "TR10", "TR11", "TR12", "TR13", "TR14", "TR15"],
        "UA": ["UA01", "UA02"],
        "UK": ["UK01", "UK02", "UK03", "UK04", "UK05", "UKNI"],
        "XK": ["XK00"],
    },
    "peof": {
        "AL": ["AL00_OFF"],
        "BE": ["BE011_OFF", "BE012_OFF", "BE013_OFF"],
        "BG": ["BG01_OFF"],
        "CY": ["CY00_OFF"],
        "DE": ["DE011_OFF", "DE012_OFF", "DE013_OFF", "DE014_OFF", "DE015_OFF", "DE02_OFF"],
        "DK": ["DKBI_OFF", "DKE1_OFF", "DKKF_OFF", "DKW013_OFF", "DKW11_OFF", "DKW12_OFF", "DKW14_OFF", "DKW15_OFF"],
        "DZ": ["DZ00_OFF"],
        "EE": ["EEN1_OFF", "EES1_OFF", "EEW1_OFF"],
        "EG": ["EG00_OFF"],
        "ES": ["ES01_OFF", "ES02_OFF", "ES04_OFF", "ES06_OFF", "ES09_OFF", "ES10_OFF", "ES11_OFF"],
        "FI": ["FI011_OFF", "FI021_OFF", "FI022_OFF", "FI023_OFF"],
        "FR": ["FR01_OFF", "FR02_OFF", "FR031_OFF", "FR032_OFF", "FR03_OFF", "FR041_OFF", "FR042_OFF", "FR04_OFF", "FR081_OFF", "FR082_OFF", "FR09_OFF", "FR13_OFF"],
        "GG": ["GG00_OFF"],
        "GR": ["GR01_OFF", "GR02_OFF", "GR03_OFF"],
        "HR": ["HR01_OFF"],
        "IE": ["IE00_OFF"],
        "IL": ["IL00_OFF"],
        "IS": ["IS00_OFF"],
        "IT": ["ITCA_OFF", "ITCN_OFF", "ITCS_OFF", "ITN1_OFF", "ITS1_OFF", "ITSA_OFF", "ITSI_OFF"],
        "LB": ["LB00_OFF"],
        "LT": ["LT00_OFF"],
        "LV": ["LV00_OFF"],
        "LY": ["LY01_OFF", "LY02_OFF"],
        "MA": ["MA01_OFF", "MA02_OFF", "MA04_OFF", "MA05_OFF", "MA06_OFF"],
        "ME": ["ME00_OFF"],
        "MT": ["MT00_OFF"],
        "NL": ["NL011_OFF", "NL012_OFF", "NL013_OFF", "NL031_OFF", "NL032_OFF", "NL033_OFF"],
        "NO": ["NOM1_OFF", "NON1_OFF", "NOS21_OFF", "NOS22_OFF", "NOS3_OFF"],
        "PL": ["PL04_OFF", "PL05_OFF"],
        "PS": ["PS00_OFF"],
        "PT": ["PT01_OFF", "PT02_OFF"],
        "RO": ["RO03_OFF"],
        "SE": ["SE01_OFF", "SE02_OFF", "SE03_OFF", "SE04_OFF"],
        "SI": ["SI00_OFF"],
        "SY": ["SY00_OFF"],
        "TN": ["TN01_OFF", "TN02_OFF", "TN04_OFF", "TN06_OFF", "TN07_OFF"],
        "TR": ["TR01_OFF", "TR02_OFF", "TR03_OFF", "TR04_OFF", "TR06_OFF", "TR07_OFF", "TR10_OFF", "TR11_OFF"],
        "UA": ["UA02_OFF"],
        "UK": ["UK011_OFF", "UK012_OFF", "UK02_OFF", "UK031_OFF", "UK032_OFF", "UK041_OFF", "UK042_OFF", "UK043_OFF", "UK051_OFF", "UK052_OFF", "UK053_OFF", "UKNI_OFF"],
    },
    "p2on": {
        "AT": ["AT01", "AT02", "AT03", "AT04"],
        "BA": ["BA00"],
        "BE": ["BE01", "BE02", "BE03"],
        "BG": ["BG01", "BG02"],
        "CH": ["CH00"],
        "CY": ["CY00"],
        "CZ": ["CZ01", "CZ02"],
        "DE": ["DE01", "DE02", "DE03", "DE04", "DE05", "DE06", "DE07"],
        "DK": ["DKE1", "DKW1"],
        "DZ": ["DZ09"],
        "EE": ["EE00"],
        "EG": ["EG03", "EG06", "EG07"],
        "ES": ["ES01", "ES02", "ES03", "ES04", "ES05", "ES06", "ES08", "ES09", "ES10", "ES11", "ES12"],
        "FI": ["FI01", "FI02"],
        "FR": ["FR01", "FR02", "FR03", "FR04", "FR05", "FR07", "FR08", "FR09", "FR10", "FR11", "FR12", "FR13", "FR14", "FR15", "FR16", "FR17", "FR18", "FR19", "FR20", "FR21", "FR22", "FR23", "FR24", "FR25", "FR26", "FR27"],
        "GR": ["GR01", "GR02", "GR03"],
        "HR": ["HR01"],
        "HU": ["HU02", "HU03"],
        "IE": ["IE00"],
        "IL": ["IL01"],
        "IS": ["IS00"],
        "IT": ["ITCA", "ITCN", "ITCS", "ITN1", "ITS1", "ITSA", "ITSI"],
        "JO": ["JO01", "JO02", "JO04"],
        "LT": ["LT00"],
        "LU": ["LU00"],
        "LV": ["LV00"],
        "MA": ["MA01", "MA06", "MA07", "MA11"],
        "ME": ["ME00"],
        "MK": ["MK00"],
        "NL": ["NL01", "NL02", "NL03", "NL04"],
        "NO": ["NOM1", "NOM2", "NON1", "NON2", "NON3", "NOS13", "NOS21", "NOS22", "NOS31", "NOS33"],
        "PL": ["PL01", "PL02", "PL03", "PL04", "PL05"],
        "PS": ["PS01"],
        "PT": ["PT01", "PT02"],
        "RO": ["RO01", "RO02", "RO03"],
        "RS": ["RS01"],
        "SE": ["SE01", "SE02", "SE03", "SE04"],
        "SI": ["SI00"],
        "SK": ["SK00"],
        "SY": ["SY01", "SY03"],
        "TN": ["TN01"],
        "TR": ["TR01", "TR02", "TR03", "TR04", "TR05", "TR06", "TR07", "TR08", "TR09", "TR10", "TR11", "TR12", "TR13"],
        "UA": ["UA01", "UA02"],
        "UK": ["UK01", "UK02", "UK03", "UK04", "UK05", "UKNI"],
        "XK": ["XK00"],
    },
    "p2of": {
        "BE": ["BE012_OFF"],
        "DE": ["DE011_OFF", "DE012_OFF", "DE02_OFF"],
        "DK": ["DKE1_OFF", "DKW11_OFF", "DKW12_OFF"],
        "ES": ["ES04_OFF"],
        "FI": ["FI021_OFF"],
        "FR": ["FR111_OFF", "FR112_OFF"],
        "IE": ["IE02_OFF"],
        "NL": ["NL011_OFF", "NL031_OFF", "NL033_OFF"],
        "NO": ["NOS21_OFF"],
        "PT": ["PT02_OFF"],
        "SE": ["SE03_OFF", "SE04_OFF"],
        "UK": ["UK011_OFF", "UK031_OFF", "UK032_OFF", "UK041_OFF", "UK042_OFF", "UK043_OFF", "UK051_OFF"],
    },
}


FEASIBILITY_NOTE = 'Feasibility snapshot (populated / total zones) from the staged products: szon 68/68, peon 130/153, peof 25/124, p2on 152/152, p2of 26/26. Offshore PEOF is largely placeholder zones (dropped by the null-aware aggregation); ERAA-2026 P2OF/P2ON are curated to feasible zones.'


def levels() -> list[str]:
    """PECD spatial levels covered by this registry."""
    return sorted(PECD_ZONES)


def countries(level: str) -> list[str]:
    """Repo country codes with at least one zone at ``level``."""
    return sorted(PECD_ZONES.get(level, {}))


def zones_for(country: str, level: str) -> list[str]:
    """PECD zone codes for a country at a spatial level (empty list if none)."""
    return list(PECD_ZONES.get(level, {}).get(country.upper(), []))


def template_zone_capacities(country: str, level: str) -> dict:
    """A ``{zone: None}`` skeleton for a country's zones at ``level``.

    Fill with installed capacity (MW) and pass as ``pecd.zone_capacities[<tech>]``
    for precise capacity-weighted national aggregation (else equal weights are used).
    """
    return {z: None for z in zones_for(country, level)}


__all__ = ["PECD_ZONES", "FEASIBILITY_NOTE", "levels", "countries", "zones_for", "template_zone_capacities"]
