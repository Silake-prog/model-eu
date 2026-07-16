"""clever.scenario.registry — the declarative scenario-flag registry.

This is the single place where the scenario-string vocabulary is *declared*. Each
:class:`Flag` names one lever, gives the regex for the token(s) it consumes, points at
the pure parser in :mod:`clever.scenario.parse` that resolves it, and documents what it
changes. From that one declaration the module provides:

  * :func:`validate` — structural validation that replaces the hand-maintained
    ``_VALID_SCENARIOS`` whitelist + regex-strip assert in ``scripts/run_adequacy.py``.
    A string is valid iff it starts with a known base and every remaining ``_token`` is
    claimed by a registered flag. Declaring a flag here is therefore enough to make any
    well-formed scenario that uses it valid — no whitelist edit needed.
  * :func:`parse_scenario` — resolve a scenario string to a typed :class:`ScenarioSpec`
    (a pure function of the string; no dependency on the import-time env global). Values
    come straight from the ``clever.scenario.parse`` functions, so a spec is identical to
    the ``clever.constants`` globals for the same string (guarded by tests).
  * :func:`iter_flags` — enumerate the vocabulary for docs (see docs/flags.md).

Backwards compatibility: this does not change how any value is computed — it only
declares, in one table, the flags that ``parse.py`` already implements, plus the base
prefixes and the ``corr``/``batt``/``storPx`` levers resolved in ``scenario/resolved.py`` and the
drivers. No numerics changed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from pommes_eur.scenario import parse as _p


# ── Base run prefixes (the demand/sufficiency stem a scenario starts from) ──────────
# Order longest-first so the matcher is greedy and unambiguous.
BASE_PREFIXES: tuple[str, ...] = ("R0_v1", "policy_nuke", "policy_re")


@dataclass(frozen=True)
class Flag:
    """One declared scenario lever.

    Attributes
    ----------
    name:      canonical flag id (e.g. ``bio``, ``co2``, ``menaRisk``).
    token:     regex for the token *after* the leading ``_`` (no anchors — the registry
               adds the ``_`` prefix and a ``(?=_|$)`` boundary).
    kind:      value shape the parser yields (``flag``/``enum``/``int``/``float``/``map``).
    changes:   one-line description of what the lever changes (used in docs/flags.md).
    parser:    the ``clever.scenario.parse`` function that resolves it (None for the
               purely structural modifiers ``nuke``/``noMin`` and the base-inherited gates).
    """

    name: str
    token: str
    kind: str
    changes: str
    parser: Optional[Callable[..., object]] = None

    @property
    def pattern(self) -> "re.Pattern[str]":
        return re.compile("_" + self.token + r"(?=_|$)")


# ── The flag vocabulary ─────────────────────────────────────────────────────────────
# Ordered most-specific-first so the left-to-right matcher never mis-splits a token
# (e.g. `menaH2cost..` must be tried before `menaH2..`). Patterns mirror the regexes
# inside clever/scenario/parse.py (and the corr/batt/storPx levers in scenario/resolved.py /
# r0_input_tables.py) exactly.
FLAG_REGISTRY: tuple[Flag, ...] = (
    # structural modifiers (no dedicated parser; gate via prefix/substring)
    Flag("nuke", r"nuke", "flag", "make Nuclear expandable (also as base policy_nuke)"),
    Flag("noMin", r"noMin", "flag", "drop electrolyser sovereignty min-bounds"),
    # biomethane / ATR / H2 bundle
    Flag("bioOff", r"bioOff", "flag", "disable biomethane scope", _p._parse_biomethane_scope),
    Flag("bio", r"bio(?:Low|Med|High)", "enum", "biomethane potential tier", _p._parse_biomethane_scope),
    Flag("atr", r"atr", "flag", "enable ATR-biomethane H2 route", _p._parse_atr_enabled),
    Flag("h2HIGH", r"h2HIGH", "enum", "industrial H2 demand bundle = high_h2", _p._parse_demandforge_bundle_override),
    Flag("h2central", r"h2central", "enum", "industrial H2 demand bundle = central", _p._parse_demandforge_bundle_override),
    Flag("h2Local", r"h2Local\d+", "float", "H2 local-production share (deferred)", _p._parse_h2_local_share),
    Flag("h2voll", r"h2voll\d+", "float", "H2 demand-response VoLL override", _p._parse_voll_override),
    # electrolyser / CO2 / fuel prices
    Flag("el", r"el\d+", "int", "electrolyser CAPEX €/kW override", _p._parse_electrolyser_capex_override),
    Flag("co2off", r"co2off", "flag", "disable CO2 adder", _p._parse_co2_price_override),
    Flag("co2traj", r"co2traj[a-zA-Z0-9]+", "enum", "named CO2 price trajectory", None),
    Flag("co2", r"co2\d+", "int", "flat CO2 price €/tCO2 override", _p._parse_co2_price_override),
    Flag("ngPrice", r"ngPrice\d+", "int", "explicit natural-gas import price €/MWh_th", _p._parse_ng_price_override),
    Flag("oilPrice", r"oilPrice\d+", "int", "explicit oil import price €/MWh_th", _p._parse_oil_price_override),
    Flag("fuelRamp", r"fuelRamp\d+", "int", "linear ramp on WB fuel prices (pct/yr ×10)", _p._parse_fuel_ramp_override),
    Flag("ngLeak", r"ngLeak\d+", "int", "NG upstream methane leak rate (per-mille)", _p._parse_ng_leak_rate),
    Flag("bioLeak", r"bioLeak\d+", "int", "biomethane CH4 leak rate (per-mille)", _p._parse_bio_leak_rate),
    Flag("ccsCap", r"ccsCap\d+", "int", "CCS cap GW/country", _p._parse_ccs_cap_gw),
    # floors / bans / grid
    Flag("nofloor", r"nofloor", "flag", "lift Gas existing-capacity floor", _p._parse_gas_floor_lifted),
    Flag("noElecFloor", r"noElecFloor", "flag", "drop electrolyser deployment floor", _p._parse_no_elec_floor),
    Flag("noGridExp", r"noGridExp", "flag", "disable grid expansion", _p._parse_no_grid_exp),
    Flag("noGas", r"noGas", "flag", "methane ban", _p._parse_no_gas),
    Flag("voll", r"voll\d+", "float", "electricity demand-response VoLL override", _p._parse_voll_override),
    # VRE / nuclear ceilings
    Flag("vreEXT", r"vreEXT", "flag", "VRE ceiling normal (extended)", _p._parse_vre_extended),
    Flag("vreXXL", r"vreXXL", "flag", "VRE ceiling doubled", _p._parse_vre_xxl),
    Flag("vreFree", r"vreFree", "flag", "VRE fully cost-optimised (drop floor)", _p._parse_vre_free),
    Flag("nukeXXL", r"nukeXXL", "flag", "nuclear ceiling doubled", _p._parse_nuke_xxl),
    Flag("ccNuke", r"(?:it|at|pt|ie|dk|lu|gr)Nuke\d+", "map", "per-country nuclear re-entry cap GW", None),
    # demand / weather / corridor / battery / storage
    Flag("elecX", r"elecX\d+", "int", "electricity demand multiplier (percent)", _p._parse_elec_demand_multiplier),
    Flag("wy", r"wy\d{4}", "int", "weather reference year override", _p._parse_weather_year),
    Flag("corr", r"corr\dx", "int", "HVDC corridor expansion multiplier", None),
    Flag("batt", r"batt\d+", "int", "battery power-investment scale (percent)", None),
    Flag("storPx", r"storPx\d+", "int", "storage-power cap multiplier (percent)", None),
    Flag("pipekm", r"pipekm\d+", "int", "pipeline €/MW/km", _p._parse_pipeline_km_cost),
    # MENA imports (specific-first)
    Flag("menaH2cost_entry", r"menaH2cost[A-Z]{2}_\d+", "map", "MENA H2 delivered cost by entry point", _p._parse_mena_h2_cost_by_entry),
    Flag("menaH2cost", r"menaH2cost\d+", "int", "MENA H2 delivered cost €/MWh (global)", _p._parse_mena_h2_cost),
    Flag("menaH2", r"menaH2\d+", "int", "MENA H2 import cap TWh", _p._parse_mena_h2_cap),
    Flag("menaOptim", r"menaOptim(?:[A-Z]{2}_?)*", "map", "MENA optimised-import active countries", _p._parse_mena_optim_active),
    Flag("menaInfra_route", r"menaInfra[A-Z]{2}_[A-Z]{2}_\d+", "map", "MENA infra CAPEX per route", _p._parse_mena_infra_overrides),
    Flag("menaInfra", r"menaInfra\d+", "int", "MENA infra CAPEX global €/MW", _p._parse_mena_infra_global),
    Flag("menaRisk", r"menaRisk[A-Z]{2}_\d+", "map", "MENA WACC risk per country (pp ×10)", _p._parse_mena_risk_overrides),
    Flag("menaNGcost", r"menaNGcost[A-Z]{2}_\d+", "map", "MENA NG wholesale price per country", _p._parse_mena_ng_wholesale_overrides),
    Flag("menaCap", r"menaCap\d+", "int", "MENA import cap scale (percent)", _p._parse_mena_cap_scale),
    Flag("menaPref", r"menaPref\d+", "int", "MENA preference scale (percent)", _p._parse_mena_pref_scale),
)


def iter_flags() -> tuple[Flag, ...]:
    """Return the declared flag vocabulary (for docs generation)."""
    return FLAG_REGISTRY


# ── Structural validation ───────────────────────────────────────────────────────────
def _match_base(s: str) -> Optional[int]:
    for base in BASE_PREFIXES:
        if s == base or s.startswith(base + "_"):
            return len(base)
    return None


def explain(s: str) -> dict:
    """Return a diagnostic dict: matched base, consumed flags, and any unmatched tail."""
    base_end = _match_base(s)
    if base_end is None:
        return {"valid": False, "base": None, "flags": [], "unmatched": s}
    base = s[:base_end]
    i = base_end
    matched: list[str] = []
    while i < len(s):
        if s[i] != "_":
            return {"valid": False, "base": base, "flags": matched, "unmatched": s[i:]}
        hit = None
        for flag in FLAG_REGISTRY:
            m = flag.pattern.match(s, i)
            if m:
                hit = (flag.name, m.end())
                break
        if hit is None:
            return {"valid": False, "base": base, "flags": matched, "unmatched": s[i:]}
        matched.append(hit[0])
        i = hit[1]
    return {"valid": True, "base": base, "flags": matched, "unmatched": ""}


def validate(s: str) -> bool:
    """True iff ``s`` is a structurally valid scenario string under the registry."""
    return explain(s)["valid"]


# ── Typed scenario spec (pure function of the string) ───────────────────────────────
@dataclass(frozen=True)
class ScenarioSpec:
    """All scenario-resolved values, mirroring the clever.constants globals 1:1."""

    scenario: str
    high_demand: bool
    nuclear_expandable: bool
    biomethane_scope: Optional[str]
    atr_enabled: bool
    electrolyser_capex_eur_per_kw: float
    demandforge_bundle_override: Optional[str]
    co2_price_eur_per_tonne: float
    gas_floor_lifted: bool
    ng_upstream_leak_rate: float
    bio_ch4_leak_rate: float
    ccs_cap_gw_per_country: Optional[float]
    vre_xxl: bool
    vre_free: bool
    nuke_xxl: bool
    vre_extended: bool
    electricity_voll_override: Optional[float]
    h2_voll_override: Optional[float]
    pipeline_eur_per_mw_per_km: Optional[float]
    elec_demand_multiplier: float
    weather_year_override: Optional[int]
    no_elec_floor: bool
    no_grid_expansion: bool
    h2_local_share: Optional[float]
    no_gas: bool
    mena_h2_import_cap_twh: float
    mena_h2_delivered_cost_eur_per_mwh: Optional[float]
    mena_h2_delivered_cost_by_entry: dict
    mena_optim_active_countries: tuple
    mena_infra_global_override_eur_per_mw: Optional[float]
    mena_infra_per_route_overrides: dict
    mena_risk_per_country_overrides: dict
    mena_cap_scale: float
    mena_pref_scale: float
    mena_ng_wholesale_overrides: dict
    ng_price_override_eur_per_mwh_th: Optional[float]
    oil_price_override_eur_per_mwh_th: Optional[float]
    fuel_ramp_pct_override: Optional[float]


def parse_scenario(s: str) -> ScenarioSpec:
    """Resolve a scenario string to a typed spec — a pure function (no env global)."""
    return ScenarioSpec(
        scenario=s,
        high_demand=_p._is_high_demand(s),
        nuclear_expandable=_p._is_nuclear_expandable(s),
        biomethane_scope=_p._parse_biomethane_scope(s),
        atr_enabled=_p._parse_atr_enabled(s),
        electrolyser_capex_eur_per_kw=_p._parse_electrolyser_capex_override(s),
        demandforge_bundle_override=_p._parse_demandforge_bundle_override(s),
        co2_price_eur_per_tonne=_p._parse_co2_price_override(s),
        gas_floor_lifted=_p._parse_gas_floor_lifted(s),
        ng_upstream_leak_rate=_p._parse_ng_leak_rate(s),
        bio_ch4_leak_rate=_p._parse_bio_leak_rate(s),
        ccs_cap_gw_per_country=_p._parse_ccs_cap_gw(s),
        vre_xxl=_p._parse_vre_xxl(s),
        vre_free=_p._parse_vre_free(s),
        nuke_xxl=_p._parse_nuke_xxl(s),
        vre_extended=_p._parse_vre_extended(s) or _p._parse_vre_xxl(s),
        electricity_voll_override=_p._parse_voll_override(s, "voll"),
        h2_voll_override=_p._parse_voll_override(s, "h2voll"),
        pipeline_eur_per_mw_per_km=_p._parse_pipeline_km_cost(s),
        elec_demand_multiplier=_p._parse_elec_demand_multiplier(s),
        weather_year_override=_p._parse_weather_year(s),
        no_elec_floor=_p._parse_no_elec_floor(s),
        no_grid_expansion=_p._parse_no_grid_exp(s),
        h2_local_share=_p._parse_h2_local_share(s),
        no_gas=_p._parse_no_gas(s),
        mena_h2_import_cap_twh=_p._parse_mena_h2_cap(s),
        mena_h2_delivered_cost_eur_per_mwh=_p._parse_mena_h2_cost(s),
        mena_h2_delivered_cost_by_entry=_p._parse_mena_h2_cost_by_entry(s),
        mena_optim_active_countries=_p._parse_mena_optim_active(s),
        mena_infra_global_override_eur_per_mw=_p._parse_mena_infra_global(s),
        mena_infra_per_route_overrides=_p._parse_mena_infra_overrides(s),
        mena_risk_per_country_overrides=_p._parse_mena_risk_overrides(s),
        mena_cap_scale=_p._parse_mena_cap_scale(s),
        mena_pref_scale=_p._parse_mena_pref_scale(s),
        mena_ng_wholesale_overrides=_p._parse_mena_ng_wholesale_overrides(s),
        ng_price_override_eur_per_mwh_th=_p._parse_ng_price_override(s),
        oil_price_override_eur_per_mwh_th=_p._parse_oil_price_override(s),
        fuel_ramp_pct_override=_p._parse_fuel_ramp_override(s),
    )


__all__ = [
    "Flag",
    "FLAG_REGISTRY",
    "BASE_PREFIXES",
    "ScenarioSpec",
    "iter_flags",
    "validate",
    "explain",
    "parse_scenario",
]
