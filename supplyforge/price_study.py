r"""Electricity price-factor reanalysis engine (hybrid: structural + empirical).

This is the computational backend for ``notebooks/electricity_price_factors.ipynb``.
It answers one question — *how do different factors move the wholesale electricity
price?* — two complementary ways:

1. **Structural / counterfactual.** Build a multi-country merit-order dispatch
   model (one :class:`pommes_craft.Area` per country) from the **real** generation
   fleet (ENTSO-E ERAA 2024), **real** fuel + CO2 prices (ERAA), **real** hourly
   demand (ENTSO-E Power-Statistics) and PECD weather. Solve it. The hourly
   **clearing price** is the dual of the energy-balance ("adequacy") constraint —
   the short-run marginal cost of serving one more MWh. Then perturb a single
   factor (gas price, CO2 price, demand, nuclear availability, RES build-out,
   interconnection) via the YAML and watch the modelled price respond. The
   difference vs the base run is that factor's price contribution.

2. **Empirical.** Compare the modelled price to the **real** ENTSO-E day-ahead
   price for the same year, where that series is available.

Everything the default config needs is **keyless** — the public ERAA archive, the
public ENTSO-E Power-Statistics load CSV, and cached PECD weather — so the
notebook is shareable and runs without an ENTSO-E API token. A token only enriches
the empirical (real-price) half.

Data provenance
---------------
* Fleet              : ERAA 2024 ``GenerationCapacities.xlsx`` (per market node x technology, MW)
* Fuel + CO2 prices  : ERAA 2024 ``Commodity Prices.xlsx`` (2023 EUR)
* Demand (hourly)    : ENTSO-E Power-Statistics ``monthly_hourly_load_values_{year}.csv`` (keyless)
* Wind/solar CF      : PECD 4.2 staged CSVs (``supplyforge.process.pecd``)
* Interconnection    : Ember NTC (``supplyforge.fetch.ember_ntc``) or cached supplyforge NTC
* Real day-ahead     : ENTSO-E ``day_ahead_prices_{cc}_{year}.parquet`` (cached / token), fail-soft

Companion to **demandforge** (the demand-side package): the structural model can
optionally be driven by a demandforge load curve instead of Power-Statistics; see
:func:`country_demand`.

Author: built for the supplyforge price-factor study.
"""
from __future__ import annotations

import io
import logging
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from supplyforge import RESULTS_DIR

logger = logging.getLogger(__name__)

HOURS_PER_YEAR = 8760
_GJ_PER_MWH = 3.6  # 1 MWh = 3.6 GJ — converts ERAA's EUR/GJ fuel prices to EUR/MWh_th

# ---------------------------------------------------------------------------
# Technology vocabulary
# ---------------------------------------------------------------------------
# ERAA TECHNOLOGY label -> the fleet category we model. Categories split into
# FIRM dispatchable (priced by short-run marginal cost -> set the merit order)
# and intermittent RENEWABLE (priced at 0, availability = weather capacity factor).
# Storage / DSR / electrolysers are out of scope for this first-pass price model
# (they need inter-temporal modelling); they are dropped and logged.
_ERAA_TO_CATEGORY: dict[str, str] = {
    "Nuclear": "nuclear",
    "Gas": "gas",
    "Hard coal": "coal",
    "Lignite": "lignite",
    "Oil": "oil",
    "Biofuel": "biomass",
    "Small biomass": "biomass",
    "Solar (PV)": "solar",
    "Solar roof-top PV": "solar",
    "Solar (thermal)": "solar",
    "Wind onshore": "wind_onshore",
    "Wind offshore": "wind_offshore",
    "Run of river": "ror",
    "Reservoir": "hydro",
    "Pondage": "hydro",
    "Geothermal": "other_res",
    "Marine": "other_res",
    "Others (RES)": "other_res",
    "Not defined or splitting not known RES": "other_res",
}

# Firm (dispatchable) categories and their thermodynamics, used to build the
# short-run marginal cost SRMC = fuel/eff + CO2_price * emission_factor/eff + VOM.
#   eff        : electrical efficiency (MWh_e per MWh_th)
#   co2_t_mwhth: combustion CO2 (tonnes per MWh of *fuel*)
#   vom        : non-fuel variable O&M (EUR/MWh_e)
#   fuel       : key into the fuel-price table (None = no fuel, SRMC is just `water_value`/VOM)
FIRM: dict[str, dict] = {
    "nuclear": dict(fuel="nuclear", eff=0.33, co2_t_mwhth=0.0, vom=2.0),
    "gas":     dict(fuel="gas",     eff=0.50, co2_t_mwhth=0.202, vom=2.0),
    "coal":    dict(fuel="coal",    eff=0.40, co2_t_mwhth=0.341, vom=3.0),
    "lignite": dict(fuel="lignite", eff=0.38, co2_t_mwhth=0.364, vom=3.0),
    "oil":     dict(fuel="oil",     eff=0.38, co2_t_mwhth=0.279, vom=3.0),
    "biomass": dict(fuel="biomass", eff=0.35, co2_t_mwhth=0.0,  vom=4.0),
    # Reservoir/pondage hydro: no fuel, but priced at a stylised WATER VALUE so it
    # sits mid-merit instead of flooding the system as free baseload (we do not
    # model inflow energy limits in this first pass). Configurable via YAML.
    "hydro":   dict(fuel=None, eff=1.0, co2_t_mwhth=0.0, vom=None),
    "other_res": dict(fuel=None, eff=1.0, co2_t_mwhth=0.0, vom=5.0),
}
# Renewable (zero-SRMC, availability = capacity factor) categories -> PECD CF label.
RES_CF_LABEL = {"solar": "Solar", "wind_onshore": "Wind Onshore",
                "wind_offshore": "Wind Offshore"}

# ERAA Commodity-Prices FUEL label per fuel key (ERAA gives several variants;
# we take one representative series, all in EUR/GJ except CO2 in EUR/tonne).
_ERAA_FUEL_LABEL = {
    "gas": "Natural Gas",
    "coal": "Hard coal",
    "lignite": "Lignite G2 (SK - DE - RS - PL - ME - UKNI - BA - IE)",
    "oil": "Light oil",
    "nuclear": "Nuclear (updated)",
}

# Flat run-of-river capacity factor (keyless approximation; ROR is near-must-run).
_ROR_CF = 0.45


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class StudyConfig:
    """Parsed ``config/price_study.yaml`` (see that file for field docs)."""
    countries: list[str]
    year: int
    hour_stride: int
    eraa_target_year: int
    eraa_data_version: str
    climate_year: int
    commodities: dict
    scenarios: dict
    focus: str = "BE"                      # focal country for the single-country deep dive
    water_value_eur_per_mwh: float = 30.0  # stylised hydro-reservoir SRMC (just above nuclear)

    @property
    def hours(self) -> list[int]:
        """The representative hours actually solved (every ``hour_stride``-th)."""
        return list(range(0, HOURS_PER_YEAR, self.hour_stride))


def load_config(path: str | Path = None) -> StudyConfig:
    """Load the YAML study config (defaults to ``config/price_study.yaml``)."""
    import yaml

    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "price_study.yaml"
    with open(path) as fh:
        raw = yaml.safe_load(fh)
    run = raw["run"]
    return StudyConfig(
        countries=list(run["countries"]),
        year=int(run["year"]),
        hour_stride=int(run.get("hour_stride", 24)),
        eraa_target_year=int(run.get("eraa_target_year", 2026)),
        eraa_data_version=str(run.get("eraa_data_version", "Final")),
        climate_year=int(run.get("climate_year", 2008)),
        commodities=dict(raw.get("commodities") or {}),
        scenarios=dict(raw.get("scenarios") or {"base": {}}),
        focus=str(run.get("focus", "BE")),
        water_value_eur_per_mwh=float(run.get("water_value_eur_per_mwh", 30.0)),
    )


# ---------------------------------------------------------------------------
# Keyless data layer — fleet, fuel prices, demand, weather
# ---------------------------------------------------------------------------
def _eraa_path(name: str) -> Path:
    return RESULTS_DIR / "eraa_study" / name


def _country_nodes(market_nodes: list[str], cc: str) -> list[str]:
    """ERAA market nodes belonging to country ``cc`` (handles zonal splits & offshore).

    UK -> UK00/UKNI, IT -> all IT* zones, BE -> BE00/BEOF (offshore), etc.
    """
    return [n for n in market_nodes if str(n).upper().startswith(cc.upper())]


def eraa_fleet(cfg: StudyConfig) -> pd.DataFrame:
    """Installed capacity (GW) per country x fleet category from ERAA 2024.

    Returns a tidy frame ``[country, category, gw]`` for the configured countries,
    ``eraa_target_year`` and ``eraa_data_version``. Categories follow
    :data:`_ERAA_TO_CATEGORY`; unmapped/out-of-scope technologies (storage, DSR,
    electrolysers) are dropped.
    """
    g = pd.read_excel(_eraa_path("GenerationCapacities.xlsx"), sheet_name="data")
    g = g[(g["TARGET_YEAR"] == cfg.eraa_target_year)
          & (g["DATA_VERSION"] == cfg.eraa_data_version)
          & (g["STATUS"] == "Market")].copy()
    g["category"] = g["TECHNOLOGY"].map(_ERAA_TO_CATEGORY)
    nodes = g["MARKET_NODE"].unique().tolist()
    rows = []
    for cc in cfg.countries:
        sub = g[g["MARKET_NODE"].isin(_country_nodes(nodes, cc)) & g["category"].notna()]
        agg = sub.groupby("category")["CAPACITY_MW"].sum() / 1000.0  # MW -> GW
        for cat, gw in agg.items():
            if gw > 0:
                rows.append({"country": cc, "category": cat, "gw": float(gw)})
    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError(
            f"No ERAA fleet for {cfg.countries} @ {cfg.eraa_target_year}/"
            f"{cfg.eraa_data_version}; check the ERAA archive is staged.")
    return out


def _eur_per_gj(df: pd.DataFrame, label: str) -> float | None:
    """First EUR/GJ price for an ERAA fuel label (averaging any duplicates)."""
    sub = df[(df["FUEL"] == label) & df["UNIT"].astype(str).str.contains("GJ")]
    return float(sub["PRICE"].mean()) if not sub.empty else None


def eraa_commodities(cfg: StudyConfig) -> dict:
    """Fuel prices (EUR/MWh_th) + CO2 price (EUR/t) for ``eraa_target_year``.

    ERAA values are used where the YAML ``commodities`` field leaves them null;
    an explicit YAML number overrides. Returns ``{gas, coal, lignite, oil,
    nuclear, biomass, co2}`` — fuels in EUR/MWh_th, ``co2`` in EUR/tonne.

    Note: ERAA publishes the per-GJ fuel prices under the ``Pre-CfE`` data version
    (the ``Final`` version carries the per-tonne variant), so the fuel lookup is
    deliberately data-version-agnostic and keys on the EUR/GJ unit instead.
    """
    c = pd.read_excel(_eraa_path("Commodity Prices.xlsx"), sheet_name="Commodity prices")
    c = c[c["YEAR"] == cfg.eraa_target_year].copy()       # YEAR only — see note above
    ov = cfg.commodities
    out: dict[str, float] = {}
    for key, label in _ERAA_FUEL_LABEL.items():
        override = ov.get(f"{key}_eur_per_gj")
        gj = float(override) if override is not None else _eraa_gj_any(c, label)
        out[key] = (gj or 0.0) * _GJ_PER_MWH
    # biomass has no clean ERAA fuel price -> YAML value (EUR/GJ) only
    out["biomass"] = float(ov.get("biomass_eur_per_gj") or 8.0) * _GJ_PER_MWH
    co2_ov = ov.get("co2_eur_per_t")
    if co2_ov is not None:
        out["co2"] = float(co2_ov)
    else:
        co2 = c[c["FUEL"] == "CO2 price"]
        # prefer the Final figure, else any
        fin = co2[co2["DATA_VERSION"] == cfg.eraa_data_version]["PRICE"]
        out["co2"] = float(fin.mean()) if not fin.empty else (
            float(co2["PRICE"].mean()) if not co2.empty else 0.0)
    return out


def _eraa_gj_any(c: pd.DataFrame, label: str) -> float | None:
    """Mean EUR/GJ for a fuel label (any data version carrying the EUR/GJ unit)."""
    sub = c[(c["FUEL"] == label) & c["UNIT"].astype(str).str.contains("GJ")]
    return float(sub["PRICE"].mean()) if not sub.empty else None


def srmc(category: str, fuels: dict, cfg: StudyConfig) -> float:
    """Short-run marginal cost (EUR/MWh_e) of a firm category given fuel + CO2 prices.

    SRMC = fuel_price/efficiency + CO2_price * emission_factor/efficiency + VOM.
    Hydro reservoirs carry no fuel and are priced at the configured water value.
    """
    p = FIRM[category]
    if p["fuel"] is None:                       # hydro reservoir / other RES
        return cfg.water_value_eur_per_mwh if category == "hydro" else (p["vom"] or 0.0)
    fuel_th = float(fuels.get(p["fuel"], 0.0))
    return fuel_th / p["eff"] + fuels["co2"] * p["co2_t_mwhth"] / p["eff"] + p["vom"]


def powerstats_load(year: int) -> dict[str, np.ndarray]:
    """Real hourly load (MW) per country from the keyless ENTSO-E Power-Statistics CSV.

    Downloads ``monthly_hourly_load_values_{year}.csv`` once (cached under
    ``RESULTS_DIR/powerstats``), returns ``{ISO2: hourly MW array}`` padded/truncated
    to 8760 h. Country codes follow the project convention (GB -> UK).
    """
    cache = RESULTS_DIR / "powerstats" / f"load_{year}.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
    else:
        url = ("https://www.entsoe.eu/publications/data/power-stats/"
               f"{year}/monthly_hourly_load_values_{year}.csv")
        logger.info("Fetching keyless Power-Statistics load: %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "supplyforge/0.1"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
        df = pd.read_csv(io.BytesIO(raw), sep="\t",
                         usecols=["DateUTC", "CountryCode", "Value"],
                         dtype={"CountryCode": str})
        df["CountryCode"] = df["CountryCode"].str.strip().replace({"GB": "UK"})
        df["DateUTC"] = pd.to_datetime(df["DateUTC"], dayfirst=True, errors="coerce")
        df = df.dropna(subset=["DateUTC", "Value"])
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache)
    out: dict[str, np.ndarray] = {}
    for cc, grp in df.groupby("CountryCode"):
        arr = grp.sort_values("DateUTC")["Value"].to_numpy(dtype=float)
        out[str(cc)] = _to_8760(arr)
    return out


def _to_8760(arr: np.ndarray) -> np.ndarray:
    """Pad (ffill) or truncate a series to exactly 8760 hours."""
    arr = np.asarray(arr, dtype=float)
    if len(arr) >= HOURS_PER_YEAR:
        return arr[:HOURS_PER_YEAR]
    pad = np.full(HOURS_PER_YEAR - len(arr), arr[-1] if len(arr) else 0.0)
    return np.concatenate([arr, pad])


def country_demand(cfg: StudyConfig, source: str = "powerstats") -> dict[str, np.ndarray]:
    """Hourly demand (MW) per configured country, full 8760 arrays.

    ``source="powerstats"`` (default, keyless) uses real ENTSO-E Power-Statistics
    load. ``source="demandforge"`` uses demandforge's ENTSO-E load client (needs a
    token) — the companion demand-side package — falling back to Power-Statistics.
    """
    if source == "demandforge":
        try:
            from demandforge.fetch.entsoe_load_curves import fetch_entsoe_load
            out = {}
            for cc in cfg.countries:
                df = fetch_entsoe_load(cc, cfg.year).to_pandas()
                out[cc] = _to_8760(df["load_mw"].to_numpy(dtype=float))
            return out
        except Exception as exc:  # noqa: BLE001 - fail-soft to keyless
            logger.warning("demandforge demand unavailable (%s); using Power-Statistics.", exc)
    allc = powerstats_load(cfg.year)
    missing = [c for c in cfg.countries if c not in allc]
    if missing:
        logger.warning("Power-Statistics has no load for %s; flat fallback from ERAA demand.", missing)
    dem = eraa_demand_twh(cfg)
    out = {}
    for cc in cfg.countries:
        if cc in allc and np.nansum(allc[cc]) > 0:
            out[cc] = allc[cc]
        else:                                   # keyless flat fallback
            twh = dem.get(cc, 50.0)
            out[cc] = np.full(HOURS_PER_YEAR, twh * 1e6 / HOURS_PER_YEAR)
    return out


def eraa_demand_twh(cfg: StudyConfig) -> dict[str, float]:
    """Annual demand (TWh) per country from ERAA ``Aggregated_Demand`` (Avg weather)."""
    d = pd.read_excel(_eraa_path("Aggregated_Demand.xlsx"), sheet_name=0)
    d = d[(d["TARGET_YEAR"] == cfg.eraa_target_year)
          & (d["DATA_VERSION"] == cfg.eraa_data_version)
          & (d["TYPE_WS"] == "Avg")]
    nodes = d["MARKET_NODE"].unique().tolist()
    return {cc: float(d[d["MARKET_NODE"].isin(_country_nodes(nodes, cc))]["DEMAND_TWH"].sum())
            for cc in cfg.countries}


def pecd_capacity_factors(cfg: StudyConfig) -> dict[str, dict[str, np.ndarray]]:
    """Wind/solar hourly capacity factors per country from cached PECD weather.

    Returns ``{country: {"Solar": arr, "Wind Onshore": arr, "Wind Offshore": arr}}``
    (full 8760), reading the staged PECD szon/peon CSVs for ``climate_year``.
    """
    from supplyforge.process.pecd.capacity_factor import national_cf_from_frame

    pecd_cfg = {"pecd": {"spatial_resolution_solar": "szon",
                         "spatial_resolution_wind": "peon",
                         "origin": "era5_reanalysis",
                         "climate_years": [cfg.climate_year]}}
    frames: dict[str, pd.DataFrame | None] = {}

    def _frame(code: str, res: str):
        key = f"{code}_{res}"
        if key not in frames:
            p = RESULTS_DIR / "pecd_study" / f"pecd42_{code}_{res}_{cfg.climate_year}_fv1.csv"
            frames[key] = pd.read_csv(p, comment="#", index_col="Date") if p.exists() else None
        return frames[key]

    out: dict[str, dict[str, np.ndarray]] = {}
    for cc in cfg.countries:
        cf: dict[str, np.ndarray] = {}
        for code, res, label in [("SPV", "szon", "Solar"), ("WON", "peon", "Wind Onshore"),
                                 ("WOF", "peof", "Wind Offshore")]:
            df = _frame(code, res)
            if df is None:
                continue
            v = national_cf_from_frame(code, cc, df, pecd_cfg)
            if v is not None:
                cf[label] = _to_8760(np.asarray(v, dtype=float))
        out[cc] = cf
    return out


def ntc_links(cfg: StudyConfig) -> list[tuple[str, str, float]]:
    """Inter-country NTC links (MW) among the configured countries (Ember; fail-soft)."""
    try:
        from supplyforge.grid_model import ember_ntc_links
        return ember_ntc_links(cfg.countries)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ember NTC unavailable (%s); building with no interconnection.", exc)
        return []


def real_day_ahead_prices(cfg: StudyConfig) -> dict[str, pd.DataFrame]:
    """Real ENTSO-E day-ahead prices per country (the empirical half), fail-soft.

    Reads ``RESULTS_DIR/day_ahead_prices/day_ahead_prices_{cc}_{year}.parquet`` if
    present (cached, or synced from a shared bucket). When a parquet is missing and
    an ``ENTSOE_API_TOKEN`` is set, fetches + caches it. Countries with neither are
    simply absent from the returned dict (the modelled half still runs).
    """
    import polars as pl

    base = RESULTS_DIR / "day_ahead_prices"
    out: dict[str, pd.DataFrame] = {}
    for cc in cfg.countries:
        fp = base / f"day_ahead_prices_{cc}_{cfg.year}.parquet"
        if not fp.exists():
            try:
                from supplyforge.fetch.day_ahead_prices import fetch_and_save_day_ahead_prices
                fetch_and_save_day_ahead_prices(cc, cfg.year)
            except Exception as exc:  # noqa: BLE001 - no token / no data
                logger.info("No real prices for %s %d (%s).", cc, cfg.year, exc)
        if fp.exists():
            df = pl.read_parquet(fp).to_pandas()
            if not df.empty and "price_eur_per_mwh" in df:
                out[cc] = df
    return out


# ---------------------------------------------------------------------------
# Bundled study data (load once, reuse across scenarios)
# ---------------------------------------------------------------------------
@dataclass
class StudyData:
    """All keyless inputs for a study run, loaded once and reused per scenario."""
    fleet: pd.DataFrame                       # [country, category, gw]
    fuels: dict                               # base fuel + CO2 prices
    demand: dict                              # {country: 8760 MW}
    cf: dict                                  # {country: {label: 8760 CF}}
    links: list                              # [(a, b, mw)]
    cfg: StudyConfig = field(repr=False)


def load_study_data(cfg: StudyConfig, demand_source: str = "powerstats") -> StudyData:
    """Load every keyless input the scenarios share (fleet, fuels, demand, CF, NTC)."""
    logger.info("Loading study data for %s @ %d ...", cfg.countries, cfg.year)
    return StudyData(
        fleet=eraa_fleet(cfg),
        fuels=eraa_commodities(cfg),
        demand=country_demand(cfg, demand_source),
        cf=pecd_capacity_factors(cfg),
        links=ntc_links(cfg),
        cfg=cfg,
    )


# ---------------------------------------------------------------------------
# Scenario assembly + solve
# ---------------------------------------------------------------------------
def _apply_factors(fuels: dict, scenario: dict) -> dict:
    """Return a fuel/CO2 dict with the scenario's absolute price overrides applied."""
    f = dict(fuels)
    if scenario.get("gas_eur_per_gj") is not None:
        f["gas"] = float(scenario["gas_eur_per_gj"]) * _GJ_PER_MWH
    if scenario.get("co2_eur_per_t") is not None:
        f["co2"] = float(scenario["co2_eur_per_t"])
    return f


def build_assumptions(data: StudyData, scenario: dict) -> dict:
    """Per-country ``build_grid_model`` assumptions for one scenario.

    Applies the scenario's multiplicative factor knobs: ``demand_scale``,
    ``nuclear_avail``, ``coal_avail`` (de-rate firm capacity), ``res_scale``
    (scale wind+solar). Fuel/CO2 overrides feed the firm SRMCs.

    ``apply_to`` (optional list of country codes) restricts the **capacity/demand**
    knobs (demand_scale, nuclear_avail, coal_avail, res_scale) to those countries
    — so a scenario can de-rate *Belgian* nuclear only, leaving neighbours intact.
    Fuel and CO2 prices are EU-wide commodities and always apply system-wide.
    """
    cfg = data.cfg
    fuels = _apply_factors(data.fuels, scenario)
    targets = set(scenario.get("apply_to") or cfg.countries)
    g_demand = float(scenario.get("demand_scale", 1.0))
    g_nuclear = float(scenario.get("nuclear_avail", 1.0))
    g_coal = float(scenario.get("coal_avail", 1.0))
    g_res = float(scenario.get("res_scale", 1.0))

    fleet_by_country = {cc: grp.set_index("category")["gw"].to_dict()
                        for cc, grp in data.fleet.groupby("country")}
    assumptions: dict[str, dict] = {}
    for cc in cfg.countries:
        fl = fleet_by_country.get(cc, {})
        on = cc in targets
        demand_scale = g_demand if on else 1.0
        nuclear_avail = g_nuclear if on else 1.0
        coal_avail = g_coal if on else 1.0
        res_scale = g_res if on else 1.0

        # Firm dispatchable fleet -> {label: {GW, cost}} priced by SRMC (merit order).
        firm: dict[str, dict] = {}
        for cat in FIRM:
            gw = float(fl.get(cat, 0.0))
            if cat == "nuclear":
                gw *= nuclear_avail
            elif cat in ("coal", "lignite"):
                gw *= coal_avail
            if gw > 0:
                firm[cat] = {"GW": gw, "cost": srmc(cat, fuels, cfg)}
        # Run-of-river: must-run-ish flat RES, folded in as a cheap firm block.
        if fl.get("ror", 0) > 0:
            firm["ror"] = {"GW": float(fl["ror"]) * _ROR_CF, "cost": 0.5}

        A = {
            "solar_GW": float(fl.get("solar", 0.0)) * res_scale,
            "wind_onshore_GW": float(fl.get("wind_onshore", 0.0)) * res_scale,
            "wind_offshore_GW": float(fl.get("wind_offshore", 0.0)) * res_scale,
            "hydro_turbine_GW": 0, "hydro_energy_GWh": 0,
            "demand_profile": data.demand[cc] * demand_scale,
            "firm": firm,
            "voll": 3000.0,   # load-shedding price caps scarcity (EUR/MWh)
        }
        assumptions[cc] = A
    return assumptions, scenario


def marginal_price(lm, hours) -> pd.DataFrame:
    """Hourly modelled clearing price (EUR/MWh) per area from the solved LP.

    The price is the dual of ``operation_adequacy_constraint`` for the electricity
    resource, de-annualised by the representative-hours weight ``8760/H`` (the model
    weights each solved hour up to the full year, scaling the dual by the same factor).
    Returns ``[country, hour, price_eur_per_mwh]``.
    """
    H = len(hours)
    con = lm.constraints["operation_adequacy_constraint"]
    dual = con.dual.sel(resource="electricity")
    df = dual.to_dataframe(name="dual").reset_index()
    df = df.rename(columns={"area": "country"})
    df["price_eur_per_mwh"] = df["dual"] / (HOURS_PER_YEAR / H)
    return df[["country", "hour", "price_eur_per_mwh"]].reset_index(drop=True)


@dataclass
class ScenarioResult:
    name: str
    prices: pd.DataFrame                       # [country, hour, price_eur_per_mwh]
    model: object = field(repr=False)
    status: str = ""

    def mean_price(self) -> pd.Series:
        """Time-mean modelled price (EUR/MWh) per country."""
        return self.prices.groupby("country")["price_eur_per_mwh"].mean()


def run_scenario(data: StudyData, name: str) -> ScenarioResult:
    """Build, solve and price one named scenario from the YAML."""
    from supplyforge.grid_model import build_grid_model

    cfg = data.cfg
    scenario = dict(data.cfg.scenarios.get(name, {}))
    ntc_scale = float(scenario.get("ntc_scale", 1.0))
    targets = set(scenario.get("apply_to") or cfg.countries)
    assumptions, _ = build_assumptions(data, scenario)
    # ntc_scale applies only to links touching a targeted country (so a
    # Belgium-only interconnection scenario scales just BE's borders).
    links = [(a, b, mw * (ntc_scale if (a in targets or b in targets) else 1.0))
             for a, b, mw in data.links]
    hours = cfg.hours
    model = build_grid_model(cfg.countries, cfg.year, assumptions,
                             data.cf, {c: None for c in cfg.countries},
                             links, hours=hours, name=f"price_{name}")
    lm = model.run(return_linopy_model=True)
    prices = marginal_price(lm, hours)
    logger.info("Scenario '%s' solved (%s): mean price EUR/MWh =\n%s",
                name, lm.status, prices.groupby("country")["price_eur_per_mwh"].mean().round(1))
    return ScenarioResult(name=name, prices=prices, model=model, status=str(lm.status))


def run_all(data: StudyData, names: list[str] = None) -> dict[str, ScenarioResult]:
    """Run several scenarios (default: every scenario in the YAML)."""
    names = names or list(data.cfg.scenarios)
    return {n: run_scenario(data, n) for n in names}


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------
def attribution_table(results: dict[str, ScenarioResult], base: str = "base") -> pd.DataFrame:
    """Mean price by country per scenario + delta vs base — the factor-attribution view.

    Returns a frame indexed by country with one column per scenario (mean EUR/MWh)
    plus ``<scenario>_delta`` columns (scenario - base). Sorting the deltas gives a
    tornado of which factors move the price most.
    """
    means = {n: r.mean_price() for n, r in results.items()}
    tbl = pd.DataFrame(means)
    if base in tbl.columns:
        for n in tbl.columns:
            if n != base:
                tbl[f"{n}_delta"] = tbl[n] - tbl[base]
    return tbl.round(2)


def compare_to_real(result: ScenarioResult, real: dict[str, pd.DataFrame],
                    cfg: StudyConfig) -> pd.DataFrame:
    """Modelled vs real day-ahead price stats per country (the empirical comparison).

    Aligns the modelled representative-hour prices to the real series at the same
    hours and reports mean modelled, mean real, bias and mean-absolute error.
    Countries without a real series are skipped.
    """
    hours = cfg.hours
    rows = []
    for cc in cfg.countries:
        if cc not in real:
            continue
        rp = real[cc].sort_values("timestamp")["price_eur_per_mwh"].to_numpy(dtype=float)
        rp = _to_8760(rp)[hours]
        mp = (result.prices[result.prices["country"] == cc]
              .sort_values("hour")["price_eur_per_mwh"].to_numpy(dtype=float))
        n = min(len(rp), len(mp))
        rp, mp = rp[:n], mp[:n]
        valid = np.isfinite(rp) & np.isfinite(mp)
        if valid.sum() == 0:
            continue
        rows.append({
            "country": cc,
            "modelled_mean": float(np.mean(mp[valid])),
            "real_mean": float(np.mean(rp[valid])),
            "bias": float(np.mean(mp[valid] - rp[valid])),
            "mae": float(np.mean(np.abs(mp[valid] - rp[valid]))),
            "corr": float(np.corrcoef(mp[valid], rp[valid])[0, 1]) if valid.sum() > 2 else np.nan,
        })
    return pd.DataFrame(rows).round(1)


def price_timestamps(cfg: StudyConfig) -> pd.DatetimeIndex:
    """The wall-clock timestamps of the solved representative hours (for plotting)."""
    full = pd.date_range(f"{cfg.year}-01-01", periods=HOURS_PER_YEAR, freq="h")
    return full[cfg.hours]


# ===========================================================================
# COHERENT REAL-YEAR MODEL  (current_<year>) — the market-design engine
# ===========================================================================
# A second, higher-fidelity path that drives pommes_craft with the REAL observed
# system for a historical year, all keyless from the supplyforge GCS bucket:
#   * real installed capacity per country/tech           (installed_capacities)
#   * real OBSERVED capacity factors = real weather       (capacity_factors)
#   * real hourly AVAILABILITY = real outages, incl. the   (availability)
#     2023 French nuclear crunch
#   * reconstructed hydro inflow + ERAA storage energy     (inflow + ERAA Storage)
# plus real hourly demand (Power-Statistics) and Ember NTC interconnection.
# It reuses supplyforge.create_pommes_craft_model's tech-adders (which apply the
# real availability/CF, ramp rates, reservoir + pumped hydro) and adds the one
# thing those builders omit — a real Demand — then solves the full 8760 h and
# reads the clearing price from the adequacy dual. This is the model to use for
# *market-design* questions (price formation, CO2/fuel pass-through, congestion,
# scarcity / flexibility); the ERAA path above stays for fast exploration.

# Real-year fuel prices (EUR per MWh thermal) + CO2 (EUR/t). 2023 annual averages:
# TTF gas, ARA coal, lignite (cheap domestic), Brent-linked oil, EUA carbon.
FUELS_BY_YEAR: dict[int, dict] = {
    2023: {"gas": 41.0, "coal": 18.0, "lignite": 8.0, "oil": 70.0, "nuclear": 7.0,
           "biomass": 15.0, "co2": 84.0},
}
# Marginal conversion efficiencies + combustion CO2 (t/MWh_th) per dispatchable
# tech, used to turn fuel + CO2 prices into a short-run marginal cost. Gas uses a
# modern-CCGT efficiency (the marginal gas unit), which sets the clearing price.
_DISPATCH_PARAM = {
    "Nuclear": dict(fuel="nuclear", eff=0.33, co2=0.0,  vom=2.0),
    "Gas":     dict(fuel="gas",     eff=0.55, co2=0.202, vom=2.0),
    "Coal":    dict(fuel="coal",    eff=0.40, co2=0.341, vom=3.0),
    "Lignite": dict(fuel="lignite", eff=0.38, co2=0.364, vom=3.0),
    "Oil":     dict(fuel="oil",     eff=0.38, co2=0.279, vom=3.0),
    "Biomass": dict(fuel="biomass", eff=0.35, co2=0.0,  vom=4.0),
    "Waste":   dict(fuel=None, eff=1.0, co2=0.0, vom=20.0),
    "Other":   dict(fuel=None, eff=1.0, co2=0.0, vom=80.0),
}


# ---------------------------------------------------------------------------
# CO2 price trajectories — the single most important fossil price signal, made
# documented + swappable (ported from CLEVER's carbon_price.py). Each is a
# {year: EUR/tCO2} mapping with a cited source; carbon_price() interpolates.
# "historical" anchors the REAL EU-ETS (EUA) clearing price so a reanalysis year
# resolves to the true figure (2023 ~ EUR 84); the forward scenarios (FF55 /
# TYNDP / IEA-WEO / BNEF) are for cited CO2 sensitivity studies.
# ---------------------------------------------------------------------------
CARBON_TRAJECTORIES: dict[str, dict[int, float]] = {
    # Real EU-ETS December-contract annual averages (EEX/ICE).
    "historical": {2018: 16.0, 2019: 25.0, 2020: 25.0, 2021: 54.0,
                   2022: 81.0, 2023: 84.0, 2024: 65.0},
    # EC "Fit for 55" Impact Assessment 2021 (ETS reform baseline).
    "ff55":    {2025: 80.0, 2030: 100.0, 2035: 120.0, 2040: 130.0, 2045: 140.0, 2050: 150.0},
    # ENTSOG/ENTSO-E TYNDP 2024 — National Trends / Distributed Energy / Global Ambition.
    "tyndp_nt": {2025: 80.0, 2030: 95.0, 2035: 120.0, 2040: 150.0, 2045: 175.0, 2050: 200.0},
    "tyndp_de": {2025: 85.0, 2030: 110.0, 2035: 150.0, 2040: 200.0, 2045: 250.0, 2050: 290.0},
    "tyndp_ga": {2025: 85.0, 2030: 105.0, 2035: 140.0, 2040: 180.0, 2045: 220.0, 2050: 260.0},
    # IEA World Energy Outlook 2024 — STEPS / APS / NZE (EU).
    "weo_steps": {2025: 85.0, 2030: 100.0, 2035: 115.0, 2040: 130.0, 2045: 140.0, 2050: 150.0},
    "weo_aps":  {2025: 85.0, 2030: 120.0, 2035: 160.0, 2040: 195.0, 2045: 215.0, 2050: 230.0},
    "weo_nze":  {2025: 90.0, 2030: 150.0, 2035: 190.0, 2040: 220.0, 2045: 235.0, 2050: 250.0},
    # BloombergNEF New Energy Outlook 2024 — Economic Transition.
    "bnef":     {2025: 85.0, 2030: 127.0, 2035: 155.0, 2040: 180.0, 2045: 210.0, 2050: 240.0},
}


def carbon_price(year: int, trajectory: str = "historical") -> float:
    """CO2 price (EUR/tCO2) for ``year`` on a named trajectory (linear interp, clamped).

    ``historical`` returns the real EU-ETS clearing price (so a reanalysis year
    is the true figure); the forward scenarios are cited published pathways for
    sensitivity analysis. See :data:`CARBON_TRAJECTORIES`.
    """
    traj = CARBON_TRAJECTORIES.get(trajectory.lower())
    if traj is None:
        raise ValueError(f"Unknown CO2 trajectory '{trajectory}'. "
                         f"Valid: {sorted(CARBON_TRAJECTORIES)}")
    yrs = sorted(traj)
    if year <= yrs[0]:
        return float(traj[yrs[0]])
    if year >= yrs[-1]:
        return float(traj[yrs[-1]])
    for y0, y1 in zip(yrs, yrs[1:]):
        if y0 <= year <= y1:
            f = (year - y0) / (y1 - y0)
            return float(traj[y0] + f * (traj[y1] - traj[y0]))
    return float(traj[yrs[-1]])


def fuels_for_year(cfg: StudyConfig, scenario: dict) -> dict:
    """Real-year fuel + CO2 prices, with the CO2 price taken from a documented
    trajectory and scenario/commodity overrides applied.

    CO2 precedence (high→low): a scenario/commodity ``co2_eur_per_t`` flat
    override → a named ``co2_trajectory`` (scenario or YAML) → the ``historical``
    EU-ETS price for ``cfg.year``. Gas can be overridden in EUR/GJ as before.
    """
    base = dict(FUELS_BY_YEAR.get(cfg.year, FUELS_BY_YEAR[2023]))
    ov = cfg.commodities
    # CO2 from the documented trajectory (default: real historical EU-ETS)
    traj = scenario.get("co2_trajectory") or ov.get("co2_trajectory") or "historical"
    base["co2"] = carbon_price(cfg.year, traj)
    if ov.get("gas_eur_per_gj") is not None:
        base["gas"] = float(ov["gas_eur_per_gj"]) * _GJ_PER_MWH
    if ov.get("co2_eur_per_t") is not None:
        base["co2"] = float(ov["co2_eur_per_t"])
    if scenario.get("gas_eur_per_gj") is not None:
        base["gas"] = float(scenario["gas_eur_per_gj"]) * _GJ_PER_MWH
    if scenario.get("co2_eur_per_t") is not None:
        base["co2"] = float(scenario["co2_eur_per_t"])
    return base


def dispatch_costs(fuels: dict) -> dict:
    """Short-run marginal cost (EUR/MWh_e) per dispatchable tech from fuel + CO2 prices."""
    out = {}
    for tech, p in _DISPATCH_PARAM.items():
        if p["fuel"] is None:
            out[tech] = p["vom"]
        else:
            out[tech] = (fuels[p["fuel"]] / p["eff"]
                         + fuels["co2"] * p["co2"] / p["eff"] + p["vom"])
    return out


def build_current_model(cfg: StudyConfig, scenario: dict, demand: dict):
    """Build the coherent real-year pommes_craft model (full 8760 h) for a scenario.

    Reuses ``create_pommes_craft_model``'s real-data tech-adders (real capacity,
    observed CF, real availability, ramp, reservoir + pumped hydro) and adds the
    real ``Demand`` they omit. Market-design levers from ``scenario`` are applied:
    ``gas_eur_per_gj``/``co2_eur_per_t`` (fuel → SRMC), ``demand_scale``,
    ``nuclear_avail`` / ``coal_avail`` (extra de-rate of real capacity),
    ``ntc_scale`` (interconnection), each optionally restricted by ``apply_to``.
    Returns ``(energy_model, hours)``.
    """
    from supplyforge.create_pommes_craft_model import (
        add_dispatchable_tech, add_intermittent_tech, add_reservoir_hydro,
        add_pumped_hydro, DISPATCHABLE_TECH_DICT, INTERMITTENT_TECH_DICT,
        DISPATCHABLE_RAMP_RATES, FIXED_COSTS, INVEST_COSTS)
    from supplyforge.utils import _get_input_data_file
    from supplyforge.grid_model import _add_bidirectional_link
    from pommes_craft import (EnergyModel, Area, EconomicHypothesis, TimeStepManager,
                              Demand, Spillage, LoadShedding)
    import polars as pl

    year = cfg.year
    costs = dispatch_costs(fuels_for_year(cfg, scenario))
    targets = set(scenario.get("apply_to") or cfg.countries)
    demand_scale = float(scenario.get("demand_scale", 1.0))
    nuclear_avail = float(scenario.get("nuclear_avail", 1.0))
    coal_avail = float(scenario.get("coal_avail", 1.0))
    res_scale = float(scenario.get("res_scale", 1.0))
    ntc_scale = float(scenario.get("ntc_scale", 1.0))
    LIFE = 25  # single decommission horizon; CAPEX does not affect the price dual

    H = HOURS_PER_YEAR
    em = EnergyModel(name=f"current_{year}_{scenario.get('_name','base')}",
                     hours=list(range(H)), year_ops=[year], year_invs=[year],
                     year_decs=[year + LIFE], modes=["base"],
                     resources=["electricity", "reservoir_water"])
    areas = {}
    with em.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=year, planning_step=25)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)
        for cc in cfg.countries:
            areas[cc] = Area(cc)

    for cc in cfg.countries:
        on = cc in targets
        caps = _get_input_data_file(cc, year, "installed_capacities")
        inflows = _get_input_data_file(cc, year, "inflow")
        # extra capacity de-rate / RES build-out for domestic scenarios
        if on and (nuclear_avail != 1.0 or coal_avail != 1.0 or res_scale != 1.0):
            caps = caps.clone()
            for col, f in (("Nuclear", nuclear_avail), ("Fossil Hard coal", coal_avail),
                           ("Lignite", coal_avail)):
                if col in caps.columns:
                    caps = caps.with_columns((pl.col(col) * f).alias(col))
            if res_scale != 1.0:                      # scale wind + solar (the RES lever)
                for col in ("Solar", "Wind Onshore", "Wind Offshore"):
                    if col in caps.columns:
                        caps = caps.with_columns((pl.col(col) * res_scale).alias(col))
        for tech, ptype in DISPATCHABLE_TECH_DICT.items():
            if ptype in caps.columns and float(caps[ptype][0] or 0) > 0:
                add_dispatchable_tech(areas[cc], tech, ptype, caps, costs[tech], year,
                    ramp_rate=DISPATCHABLE_RAMP_RATES[tech], fixed_cost=FIXED_COSTS[tech],
                    investment_cost=INVEST_COSTS[tech], lifetime=LIFE)
        for tech, ptype in INTERMITTENT_TECH_DICT.items():
            if ptype in caps.columns and float(caps[ptype][0] or 0) > 0:
                add_intermittent_tech(areas[cc], tech, ptype, float(caps[ptype][0]), year,
                    fixed_cost=FIXED_COSTS[tech], investment_cost=INVEST_COSTS[tech], lifetime=LIFE)
        try:
            add_reservoir_hydro(areas[cc], caps, inflows, FIXED_COSTS['Reservoir_Hydro'],
                                INVEST_COSTS['Reservoir_Hydro'], cc, lifetime=LIFE)
        except Exception as exc:  # noqa: BLE001
            logger.info("%s reservoir hydro skipped (%s)", cc, exc)
        try:
            add_pumped_hydro(areas[cc], caps, cc, FIXED_COSTS['Pumped_Hydro'],
                             INVEST_COSTS['Pumped_Hydro'], lifetime=LIFE)
        except Exception as exc:  # noqa: BLE001
            logger.info("%s pumped hydro skipped (%s)", cc, exc)
        with em.context():
            dem = np.nan_to_num(_to_8760(demand.get(cc, np.zeros(H)))) * (demand_scale if on else 1.0)
            areas[cc].add_component(Demand(name=f"{cc}_demand", resource="electricity",
                demand=pl.DataFrame({"demand": dem.tolist(), "hour": list(range(H)),
                                     "year_op": [year] * H})))
            areas[cc].add_component(LoadShedding(name=f"{cc}_ls", resource="electricity",
                                                 cost=float(scenario.get("voll", 15000.0))))
            areas[cc].add_component(Spillage(name=f"{cc}_spill", resource="electricity", max_capacity=1e6))
            areas[cc].add_component(Spillage(name=f"{cc}_wspill", resource="reservoir_water", max_capacity=5e4))
            areas[cc].add_component(LoadShedding(name=f"{cc}_wls", resource="reservoir_water", max_capacity=0.0))

    with em.context():
        for a, b, mw in ntc_links(cfg):
            scale = ntc_scale if (a in targets or b in targets) else 1.0
            _add_bidirectional_link(areas[a], areas[b], float(mw) * scale, 0.01)
    return em, list(range(H))


def run_current(cfg: StudyConfig, name: str, demand: dict) -> ScenarioResult:
    """Build + solve the coherent real-year model for one scenario, return prices."""
    scenario = dict(cfg.scenarios.get(name, {}))
    scenario["_name"] = name
    em, hours = build_current_model(cfg, scenario, demand)
    lm = em.run(return_linopy_model=True)
    prices = marginal_price(lm, hours)
    logger.info("current-model scenario '%s' solved (%s): mean price =\n%s", name,
                lm.status, prices.groupby("country")["price_eur_per_mwh"].mean().round(1))
    return ScenarioResult(name=name, prices=prices, model=em, status=str(lm.status))


# ---------------------------------------------------------------------------
# REALISTIC single-zone model — a *smooth, real-shaped* hourly price profile
# ---------------------------------------------------------------------------
# The coupled `current_<year>` model reproduces the price LEVEL well but gives a
# blocky hourly profile: a pure merit-order LP clears at the marginal unit's SRMC,
# which takes only a handful of fixed values. Three levers fix that for the focus
# country:
#   (1) real hourly border prices — model the focus country in detail and price
#       its imports/exports at each neighbour's REAL day-ahead series
#       (create_pommes_craft_model.add_imports), injecting the continuous European
#       price into the zone;
#   (2) time-varying fuel — gas enters as a PRICED RESOURCE fed by a NetImport at
#       the monthly TTF price, so the gas plants' SRMC moves month to month inside
#       one annual solve (pommes' variable_cost is annual-only);
#   (3) heat-rate banding — each thermal fleet is split into efficiency bands, so
#       its supply curve is a smooth staircase rather than one flat block.
# Result for Belgium 2023: hourly correlation with the real day-ahead price ~0.94
# and ~1500 distinct price values, vs ~0 / ~6 for the blocky coupled model.

# Heat-rate bands per thermal tech: (electrical efficiency, share of capacity).
HEAT_RATE_BANDS: dict[str, list] = {
    "Gas":     [(0.58, 0.30), (0.50, 0.40), (0.38, 0.30)],   # new CCGT / old CCGT / OCGT
    "Coal":    [(0.44, 0.50), (0.36, 0.50)],
    "Lignite": [(0.40, 0.50), (0.34, 0.50)],
}
# Monthly 2023 TTF gas price (EUR/MWh thermal) — the seasonal fuel signal.
GAS_MONTHLY_EUR_MWH_TH: dict[int, list] = {
    2023: [65, 54, 44, 41, 32, 34, 30, 35, 36, 43, 46, 37],
}


def _monthly_gas_hourly(year: int) -> np.ndarray:
    """Hourly (8760) gas price (EUR/MWh_th) from the monthly series for ``year``."""
    monthly = GAS_MONTHLY_EUR_MWH_TH.get(year, GAS_MONTHLY_EUR_MWH_TH[2023])
    months = pd.date_range(f"{year}-01-01", periods=HOURS_PER_YEAR, freq="h").month
    return np.array([monthly[m - 1] for m in months], dtype=float)


def _availability_frame(avail, plant_type: str, year: int):
    """Hourly availability frame for a plant type (clipped, padded/trimmed to 8760)."""
    import polars as pl
    a = (avail.filter(pl.col("plant_type") == plant_type)
         .select("availability_share").rename({"availability_share": "availability"})
         .with_columns(pl.col("availability").clip(0.0, 1.0)))
    if a.is_empty():
        a = pl.DataFrame({"availability": [1.0] * HOURS_PER_YEAR})
    if a.height < HOURS_PER_YEAR:
        a = pl.concat([a, a[-(HOURS_PER_YEAR - a.height):]])
    a = a[:HOURS_PER_YEAR]
    return a.with_columns(hour=pl.Series(range(HOURS_PER_YEAR)),
                          year_op=pl.lit(year).cast(pl.Int64))


def build_realistic_model(cfg: StudyConfig, scenario: dict, demand: dict):
    """Build the realistic single-zone model for ``cfg.focus`` (levers 1+2+3).

    Models the focus country in detail — heat-rate-banded thermal, gas as a
    monthly-priced resource, observed-CF renewables, reservoir + pumped hydro,
    real demand — and prices its borders by the neighbours' **real hourly
    day-ahead** series via ``add_imports``. Scenario commodity/demand overrides
    apply. Returns ``(energy_model, hours)``.
    """
    from supplyforge.create_pommes_craft_model import (
        add_dispatchable_tech, add_intermittent_tech, add_reservoir_hydro,
        add_pumped_hydro, add_imports, DISPATCHABLE_TECH_DICT, INTERMITTENT_TECH_DICT,
        DISPATCHABLE_RAMP_RATES, FIXED_COSTS, INVEST_COSTS)
    from supplyforge.utils import _get_input_data_file
    from pommes_craft import (EnergyModel, Area, EconomicHypothesis, TimeStepManager,
                              Demand, Spillage, LoadShedding, NetImport, ConversionTechnology)
    import polars as pl

    cc = cfg.focus
    year = cfg.year
    LIFE = 25
    H = HOURS_PER_YEAR
    fuels = fuels_for_year(cfg, scenario)
    costs = dispatch_costs(fuels)
    P = _DISPATCH_PARAM
    demand_scale = float(scenario.get("demand_scale", 1.0))
    gas_hourly = _monthly_gas_hourly(year)

    em = EnergyModel(name=f"realistic_{cc}_{year}_{scenario.get('_name','base')}",
                     hours=list(range(H)), year_ops=[year], year_invs=[year],
                     year_decs=[year + LIFE], modes=["base"],
                     resources=["electricity", "reservoir_water", "gas"])
    with em.context():
        EconomicHypothesis("eco", discount_rate=0.0, year_ref=year, planning_step=25)
        TimeStepManager("ts", time_step_duration=1.0, operation_year_duration=8760)
        area = Area(cc)

    caps = _get_input_data_file(cc, year, "installed_capacities")
    inflows = _get_input_data_file(cc, year, "inflow")
    avail = _get_input_data_file(cc, year, "availability")

    # market-design levers: extra de-rate of the focus country's firm capacity
    nuclear_avail = float(scenario.get("nuclear_avail", 1.0))
    coal_avail = float(scenario.get("coal_avail", 1.0))
    if nuclear_avail != 1.0 or coal_avail != 1.0:
        for col, fac in (("Nuclear", nuclear_avail), ("Fossil Hard coal", coal_avail),
                         ("Lignite", coal_avail)):
            if col in caps.columns:
                caps = caps.with_columns((pl.col(col) * fac).alias(col))

    # lever 2: gas as a monthly-priced resource (import-only; excess spilled)
    with em.context():
        area.add_component(NetImport(name="gas_market", resource="gas",
            import_price=pl.DataFrame({"import_price": gas_hourly.tolist(),
                                       "hour": list(range(H)), "year_op": [year] * H}),
            export_price=pl.DataFrame({"export_price": [0.0] * H,
                                       "hour": list(range(H)), "year_op": [year] * H})))

    for tech, ptype in DISPATCHABLE_TECH_DICT.items():
        if ptype not in caps.columns or float(caps[ptype][0] or 0) <= 0:
            continue
        total = float(caps[ptype][0])
        p = P[tech]
        if tech == "Gas":   # lever 2+3: banded gas burning the priced gas resource
            with em.context():
                for i, (eff, frac) in enumerate(HEAT_RATE_BANDS["Gas"]):
                    area.add_component(ConversionTechnology(name=f"Gas_b{i}",
                        factor={"electricity": 1.0, "gas": -1.0 / eff},
                        availability=_availability_frame(avail, ptype, year), must_run=0.0,
                        variable_cost=fuels["co2"] * p["co2"] / eff + p["vom"],
                        invest_cost=INVEST_COSTS[tech], fixed_cost=FIXED_COSTS[tech],
                        finance_rate=0.04, life_span=LIFE,
                        power_capacity_investment_max=total * frac,
                        power_capacity_investment_min=total * frac, early_decommissioning=True))
        elif tech in HEAT_RATE_BANDS:   # lever 3: heat-rate bands, annual fuel cost
            for i, (eff, frac) in enumerate(HEAT_RATE_BANDS[tech]):
                srmc = fuels[p["fuel"]] / eff + fuels["co2"] * p["co2"] / eff + p["vom"]
                caps_b = caps.with_columns(pl.lit(total * frac).alias(ptype))
                add_dispatchable_tech(area, f"{tech}_b{i}", ptype, caps_b, srmc, year,
                    ramp_rate=DISPATCHABLE_RAMP_RATES[tech], fixed_cost=FIXED_COSTS[tech],
                    investment_cost=INVEST_COSTS[tech], lifetime=LIFE)
        else:
            add_dispatchable_tech(area, tech, ptype, caps, costs[tech], year,
                ramp_rate=DISPATCHABLE_RAMP_RATES[tech], fixed_cost=FIXED_COSTS[tech],
                investment_cost=INVEST_COSTS[tech], lifetime=LIFE)
    for tech, ptype in INTERMITTENT_TECH_DICT.items():
        if ptype in caps.columns and float(caps[ptype][0] or 0) > 0:
            add_intermittent_tech(area, tech, ptype, float(caps[ptype][0]), year,
                fixed_cost=FIXED_COSTS[tech], investment_cost=INVEST_COSTS[tech], lifetime=LIFE)
    try:
        add_reservoir_hydro(area, caps, inflows, FIXED_COSTS['Reservoir_Hydro'],
                            INVEST_COSTS['Reservoir_Hydro'], cc, lifetime=LIFE)
    except Exception as exc:  # noqa: BLE001
        logger.info("%s reservoir hydro skipped (%s)", cc, exc)
    try:
        add_pumped_hydro(area, caps, cc, FIXED_COSTS['Pumped_Hydro'],
                         INVEST_COSTS['Pumped_Hydro'], lifetime=LIFE)
    except Exception as exc:  # noqa: BLE001
        logger.info("%s pumped hydro skipped (%s)", cc, exc)

    with em.context():
        dem = np.nan_to_num(_to_8760(demand.get(cc, np.zeros(H)))) * demand_scale
        area.add_component(Demand(name=f"{cc}_demand", resource="electricity",
            demand=pl.DataFrame({"demand": dem.tolist(), "hour": list(range(H)),
                                 "year_op": [year] * H})))
        area.add_component(LoadShedding(name=f"{cc}_ls", resource="electricity",
                                        cost=float(scenario.get("voll", 15000.0))))
        area.add_component(Spillage(name=f"{cc}_spill", resource="electricity", max_capacity=1e6))
        area.add_component(Spillage(name=f"{cc}_wspill", resource="reservoir_water", max_capacity=5e4))
        area.add_component(LoadShedding(name=f"{cc}_wls", resource="reservoir_water", max_capacity=0.0))
        area.add_component(Spillage(name=f"{cc}_gasspill", resource="gas", max_capacity=1e7))

    # lever 1: real-priced imports/exports to every neighbour (ntc_scale = grid lever)
    add_imports(area, cc, year, ntc_scale=float(scenario.get("ntc_scale", 1.0)))
    return em, list(range(H))


def run_realistic(cfg: StudyConfig, name: str, demand: dict) -> ScenarioResult:
    """Build + solve the realistic single-zone model for ``cfg.focus`` (levers 1+2+3)."""
    scenario = dict(cfg.scenarios.get(name, {}))
    scenario["_name"] = name
    em, hours = build_realistic_model(cfg, scenario, demand)
    lm = em.run(return_linopy_model=True)
    prices = marginal_price(lm, hours)
    return ScenarioResult(name=name, prices=prices, model=em, status=str(lm.status))


# ---------------------------------------------------------------------------
# Single-country focus analysis (e.g. Belgium): imports + what sets the price
# ---------------------------------------------------------------------------
def net_imports(result: ScenarioResult, country: str) -> pd.Series:
    """Hourly **net imports** (MW, +ve = importing) for ``country`` from the solved flows.

    net = Σ flow on links ``*->country`` − Σ flow on links ``country->*``.
    Indexed by the model hour (0..H-1).
    """
    import polars as pl
    from supplyforge.grid_model import grid_flows

    fl = grid_flows(result.model)
    link_col = "link" if "link" in fl.columns else fl.columns[-1]
    imp = (fl.filter(pl.col(link_col).str.ends_with(f"->{country}"))
           .group_by("hour").agg(pl.col("value").sum().alias("imp")))
    exp = (fl.filter(pl.col(link_col).str.starts_with(f"{country}->"))
           .group_by("hour").agg(pl.col("value").sum().alias("exp")))
    m = imp.join(exp, on="hour", how="full", coalesce=True).fill_null(0.0).sort("hour")
    s = (m["imp"] - m["exp"]).to_pandas()
    s.index = m["hour"].to_pandas()
    return s.rename("net_import_mw")


def focus_decomposition(result: ScenarioResult, data: StudyData,
                        country: str, tol: float = 0.75) -> pd.DataFrame:
    """Per-hour story of *what sets ``country``'s price* — the focus-country answer.

    For each solved hour returns the price, net imports, the **regime** that sets
    the price, and the responsible driver:

    * ``import`` — the country is a net importer and its price equals (within
      ``tol`` EUR/MWh) a cheaper neighbour's price → the price is *imported*; the
      ``partner`` is the coupled neighbour (closest price).
    * ``domestic`` — the price equals the SRMC of one of the country's own firm
      technologies → that ``marginal_tech`` is on the margin locally.

    Columns: ``hour, timestamp, price, net_import_mw, regime, partner, marginal_tech``.
    """
    cfg = data.cfg
    price = (result.prices[result.prices["country"] == country]
             .set_index("hour")["price_eur_per_mwh"])
    others = {c: result.prices[result.prices["country"] == c].set_index("hour")["price_eur_per_mwh"]
              for c in cfg.countries if c != country}
    imp = net_imports(result, country).reindex(price.index).fillna(0.0)

    # domestic SRMC ladder for this country (label -> cost)
    fuels = _apply_factors(data.fuels, dict(cfg.scenarios.get(result.name, {})))
    fl = data.fleet[data.fleet["country"] == country].set_index("category")["gw"].to_dict()
    ladder = {cat: srmc(cat, fuels, cfg) for cat in FIRM if fl.get(cat, 0) > 0}

    # map each solved hour-index (0..H-1, full-year for the current model or strided
    # for the ERAA path) to its wall-clock timestamp
    full_ts = pd.date_range(f"{cfg.year}-01-01", periods=HOURS_PER_YEAR, freq="h")
    rows = []
    for h, p in price.items():
        # closest neighbour by price
        partner, pdiff = None, np.inf
        for c, s in others.items():
            if h in s.index and abs(s[h] - p) < pdiff:
                partner, pdiff = c, abs(s[h] - p)
        importing = imp.get(h, 0.0) > 1.0
        if importing and pdiff <= tol:
            regime, tech = "import", None
        else:
            # nearest domestic firm tech by SRMC
            tech = min(ladder, key=lambda k: abs(ladder[k] - p)) if ladder else None
            regime, partner = "domestic", (partner if pdiff <= tol else None)
        ts_h = full_ts[int(h)] if 0 <= int(h) < HOURS_PER_YEAR else pd.NaT
        rows.append({"hour": h, "timestamp": ts_h,
                     "price": float(p), "net_import_mw": float(imp.get(h, 0.0)),
                     "regime": regime, "partner": partner, "marginal_tech": tech})
    return pd.DataFrame(rows)


__all__ = [
    "StudyConfig", "load_config", "StudyData", "load_study_data",
    "eraa_fleet", "eraa_commodities", "srmc", "powerstats_load", "country_demand",
    "eraa_demand_twh", "pecd_capacity_factors", "ntc_links", "real_day_ahead_prices",
    "build_assumptions", "marginal_price", "ScenarioResult", "run_scenario", "run_all",
    "attribution_table", "compare_to_real", "price_timestamps", "FIRM", "RES_CF_LABEL",
    "net_imports", "focus_decomposition",
    "FUELS_BY_YEAR", "fuels_for_year", "dispatch_costs",
    "CARBON_TRAJECTORIES", "carbon_price",
    "build_current_model", "run_current",
    "HEAT_RATE_BANDS", "GAS_MONTHLY_EUR_MWH_TH", "build_realistic_model", "run_realistic",
]
