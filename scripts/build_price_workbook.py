"""Build the SPF-Finance deliverable: per-hour electricity-price SCENARIOS for 2023.

Importable: ``build_price_workbook(realistic_prices=_res.prices)`` is called at the end of
``notebooks/belgium_market_design.ipynb`` so a full run drops the files into ``deliverables/``.
Run standalone with ``python -m scripts.build_price_workbook`` to (re)generate from the cache.

Outputs (in supplyforge_PECD/deliverables/):
  - be_price_scenarios_2023.xlsx       — openable workbook (BE hourly all scenarios, EU base
                                         hourly, real 2023 day-ahead, summary, monthly).
  - price_scenarios_2023_long.parquet  — COMPLETE tidy dataset (scenario × country × 8760 h).
  - real_2023_dayahead_long.parquet    — real ENTSO-E day-ahead, all staged countries.

Scenarios = the coupled-model results (scripts/regen_price_study.py) + the realistic BE model
+ the real day-ahead + the real balancing price.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import polars as pl

REPO = Path(__file__).resolve().parents[1]
PS = REPO / "results" / "price_study"
DA = REPO / "results" / "day_ahead_prices"
OUT = REPO / "deliverables"
YEAR = 2023
COUNTRIES = ["BE", "FR", "DE", "NL", "LU", "AT", "CH", "ES", "PL"]   # the 9 coupled zones

COUPLED = {
    "base":              ("base", "Reference: real 2023 system — real fleet, observed weather/outages, "
                                  "2023 fuel (gas ~41 €/MWh_th) & CO₂ (~84 €/t)."),
    "gas_crisis":        ("gas_crisis", "Gas-price shock: TTF at 90 €/GJ (≈2022 crisis level), CO₂ unchanged."),
    "high_co2":          ("high_co2", "Carbon-price shock: EU-ETS at 150 €/t, gas unchanged."),
    "be_isolation":      ("be_isolation", "Belgium's electricity interconnectors cut (ntc_scale=0 on BE borders)."),
    "be_nuclear_outage": ("be_nuclear_outage", "Belgian nuclear fleet unavailable (Doel+Tihange off)."),
    "fr_nuclear_outage": ("fr_nuclear_outage", "French nuclear crunch: FR nuclear capacity halved (a "
                                               "2022-style crunch on Belgium's main supplier)."),
    "be_renewables":     ("be_renewables", "Optimised high-renewables Belgium: +80% wind & solar "
                                           "(a Princess-Elisabeth-scale RES build-out)."),
}
NOTE = {
    "real_day_ahead":  "ACTUAL ENTSO-E 2023 day-ahead price (the real market outcome).",
    "balancing":       "ACTUAL BE 2023 imbalance/balancing price (real-time settlement), hourly mean of "
                       "the quarter-hourly single imbalance price.",
}

_DT = pd.date_range(f"{YEAR}-01-01 00:00", periods=8760, freq="h")
_HOUR = np.arange(8760)


def _wide(stem):
    df = pl.read_parquet(PS / f"{stem}_prices.parquet").to_pandas()
    return df.pivot_table(index="hour", columns="country", values="price_eur_per_mwh").reindex(range(8760))


def _real_da(cc):
    p = DA / f"day_ahead_prices_{cc}_{YEAR}.parquet"
    if not p.exists():
        return None
    s = pl.read_parquet(p)["price_eur_per_mwh"].to_numpy()[:8760]
    return np.concatenate([s, [np.nan] * (8760 - len(s))]) if len(s) < 8760 else s


def build_price_workbook(realistic_prices: "pd.DataFrame | None" = None, out: Path = OUT) -> dict:
    """Build the workbook + parquets. ``realistic_prices`` (the live ``run_realistic`` result, a
    ``[country, hour, price_eur_per_mwh]`` frame) is used for the ``model_realistic`` series when
    given, else the cached ``realistic_be_prices.parquet`` is read. Returns the written paths."""
    out = Path(out); out.mkdir(exist_ok=True)
    SCN = {k: _wide(vv[0]) for k, vv in COUPLED.items()}
    real = {cc: _real_da(cc) for cc in COUNTRIES}

    # realistic BE: prefer the live solve passed in, else the cache
    if realistic_prices is not None:
        rbe = realistic_prices[realistic_prices.country == "BE"].sort_values("hour")
        realistic_be = pd.Series(rbe["price_eur_per_mwh"].to_numpy()[:8760]).reindex(range(8760))
    elif (PS / "realistic_be_prices.parquet").exists():
        realistic_be = _wide("realistic_be")["BE"].reset_index(drop=True)
    else:
        realistic_be = pd.Series(np.nan, index=range(8760))

    # balancing (quarter-hourly -> hourly)
    imb_p = PS / "imbalance_BE_2023.parquet"
    if imb_p.exists():
        im = pl.read_parquet(imb_p).to_pandas()
        ts = pd.to_datetime(im["timestamp"], utc=True)
        im["h"] = ((ts - ts.min()).dt.total_seconds() // 3600).astype(int)
        balancing = im.groupby("h")["Long"].mean().reindex(range(8760)).to_numpy()
    else:
        balancing = np.full(8760, np.nan)

    # ---- reshape coupled scenarios onto the realistic hourly profile ----
    # The coupled 9-zone model has the right LEVEL/effect but a BLOCKY hourly shape (price = the
    # marginal unit's SRMC, ~10 distinct values, corr 0.65). The realistic single-zone model has the
    # right hourly SHAPE (corr 0.94) but can't carry system-wide shocks. Combine them per hour:
    #     scenario[h] = realistic_base[h] + ( coupled_scenario[h] − coupled_base[h] )
    # -> each scenario keeps its hour-specific DELTA (e.g. a gas crisis bites harder in the gas-
    # marginal peak hours) while regaining realistic intraday fluctuation. The 'base' column is thus
    # the realistic model (mean ~92.4 vs the coupled 103.6); only the deltas matter for scenarios and
    # they are preserved EXACTLY. Reshaping is BE-only (the realistic model is Belgium-focused); the
    # other countries keep the coupled model in EU_base_hourly / the long parquet.
    rbe = pd.Series(realistic_be.values, dtype=float)               # fine shape (corr 0.94)
    cbe = SCN["base"]["BE"].reset_index(drop=True).astype(float)    # coupled BE base (blocky)
    BE = {"base": rbe.values}
    for k in [c for c in COUPLED if c != "base"]:
        BE[k] = (rbe + (SCN[k]["BE"].reset_index(drop=True).astype(float) - cbe)).values

    # ---- sheets ----
    be = pd.DataFrame({"datetime_CET": _DT, "hour": _HOUR, "real_day_ahead": real["BE"],
                       "balancing_realtime": balancing})
    for k in COUPLED:                                  # base (=realistic) + reshaped scenarios
        be[k] = BE[k]
    be = be.round(2)

    eu = pd.DataFrame({"datetime_CET": _DT, "hour": _HOUR})
    for cc in COUNTRIES:                               # BE = realistic-shape base; others = coupled
        eu[cc] = BE["base"] if cc == "BE" else SCN["base"][cc].values
    eu = eu.round(2)

    rd = pd.DataFrame({"datetime_CET": _DT, "hour": _HOUR})
    for cc in COUNTRIES:
        rd[cc] = real[cc]
    rd = rd.round(2)

    rows = []
    for cc in COUNTRIES:
        is_be = cc == "BE"
        series_k = (lambda k: BE[k]) if is_be else (lambda k: SCN[k][cc].values)
        base_m = series_k("base").mean()
        r = {"country": cc, "real_day_ahead": np.nanmean(real[cc]) if real[cc] is not None else np.nan}
        for k in COUPLED:
            r[k] = series_k(k).mean()
        for k in [c for c in COUPLED if c != "base"]:
            r[f"Δ {k}"] = series_k(k).mean() - base_m
        rows.append(r)
    summary = pd.DataFrame(rows).round(1)

    dist_rows = []
    for label, series in ([("real_day_ahead", real["BE"]), ("balancing_realtime", balancing)]
                          + [(k, BE[k]) for k in COUPLED]):
        s = pd.Series(series).dropna()
        dist_rows.append({"series": label, "mean": s.mean(), "median": s.median(), "std": s.std(),
                          "P1": s.quantile(.01), "P99": s.quantile(.99),
                          ">200 €/MWh %": (s > 200).mean() * 100, "<0 €/MWh %": (s < 0).mean() * 100,
                          "max": s.max(), "min": s.min()})
    be_dist = pd.DataFrame(dist_rows).round(1)

    mdf = be[["datetime_CET"] + list(COUPLED)].copy()
    mdf["month"] = mdf["datetime_CET"].dt.month
    monthly = mdf.groupby("month").mean(numeric_only=True).round(1)
    monthly.index = [pd.Timestamp(YEAR, m, 1).strftime("%b") for m in monthly.index]

    readme = [["Belgium electricity-price SCENARIOS — 2023 (per-hour)", ""],
              ["Prepared for", "SPF Finance"],
              ["Generated from", "supplyforge price_study (coupled 9-zone) + realistic BE model"],
              ["Units", "all prices in €/MWh; one row per hour, 8760 h of 2023 (CET clock)"],
              ["", ""], ["SHEETS", ""],
              ["BE_hourly", "Belgium price every hour under each scenario + the real day-ahead & balancing"],
              ["EU_base_hourly", "Reference (base) scenario price every hour for all 9 modelled countries"],
              ["real_dayahead_hourly", "ACTUAL ENTSO-E 2023 day-ahead price, all 9 countries (real dataset)"],
              ["summary_means", "Per-country mean price by scenario, and the Δ vs the base scenario"],
              ["BE_distribution", "Distribution stats (mean/median/P1/P99/tails) per BE series"],
              ["monthly_BE", "Monthly average BE price by scenario"],
              ["", ""], ["SCENARIOS (columns)", ""]]
    for k, (_, desc) in COUPLED.items():
        readme.append([k, desc])
    for k, desc in NOTE.items():
        readme.append([k, desc])
    readme += [["", ""],
               ["HOURLY METHOD", "BE scenario[h] = realistic_base[h] + (coupled_scenario[h] − "
                                 "coupled_base[h]). The coupled 9-zone model gives the right scenario "
                                 "EFFECT but a blocky hourly shape (price = marginal SRMC, ~10 distinct "
                                 "values); the realistic single-zone model gives the right hourly SHAPE "
                                 "(corr 0.94). Adding the coupled hourly DELTA onto the realistic profile "
                                 "keeps both. So the 'base' column IS the realistic model (mean ~92.4); "
                                 "each scenario's Δ vs base is exactly the coupled-model effect. Other "
                                 "countries (EU_base, long parquet) are the coupled model only (BE has the "
                                 "detailed realistic model)."],
               ["METHOD", "Modelled price = dual of the hourly energy-balance (adequacy) constraint of a "
                          "merit-order dispatch LP. The coupled model re-dispatches all 9 zones (carries "
                          "system-wide gas/CO₂ shocks); the realistic model is single-zone (best hourly "
                          "shape). Reproducible via scripts/regen_price_study.py."],
               ["VALIDATION", "Modelled base vs real 2023 day-ahead: BE 103.6 vs 97.3; the realistic BE "
                              "model tracks the real hourly price at correlation 0.94."],
               ["CAVEATS", "Fundamental merit-order pricing (no unit-commitment/strategic bidding → real "
                           "day-ahead sits a few €/MWh above the model). Gas fuel is uncapped, so "
                           "'be_isolation' is electricity-adequacy conditional on gas supply, not energy "
                           "autarky. Zonal (NTC), not nodal."],
               ["FULL DATA", "price_scenarios_2023_long.parquet holds every scenario × country × hour; use "
                             "it if a sheet is too large or for scripting."]]
    readme_df = pd.DataFrame(readme, columns=["field", "description"])

    # ---- write Excel ----
    xlsx = out / "be_price_scenarios_2023.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        readme_df.to_excel(xw, sheet_name="README", index=False)
        be.to_excel(xw, sheet_name="BE_hourly", index=False)
        eu.to_excel(xw, sheet_name="EU_base_hourly", index=False)
        rd.to_excel(xw, sheet_name="real_dayahead_hourly", index=False)
        summary.to_excel(xw, sheet_name="summary_means", index=False)
        be_dist.to_excel(xw, sheet_name="BE_distribution", index=False)
        monthly.to_excel(xw, sheet_name="monthly_BE")

    from openpyxl import load_workbook
    from openpyxl.styles import Font, Alignment
    wb = load_workbook(xlsx)
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.font = Font(bold=True); c.alignment = Alignment(horizontal="center")
        for col in ws.columns:
            w = max((len(str(c.value)) for c in col if c.value is not None), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max(w + 1, 9), 46)
        if ws.title == "README":
            ws.column_dimensions["A"].width = 22; ws.column_dimensions["B"].width = 110
            for row in ws.iter_rows(min_col=2, max_col=2):
                row[0].alignment = Alignment(wrap_text=True, vertical="top")
        else:
            for row in ws.iter_rows(min_row=2):
                for c in row:
                    if isinstance(c.value, (int, float)) and ws.cell(1, c.column).value != "hour":
                        c.number_format = "0.0"
    wb.save(xlsx)

    # ---- write parquets ----
    dt_map = pl.DataFrame({"hour": list(range(8760)), "datetime_CET": _DT.tz_localize(None)})
    parts = []
    for k in COUPLED:
        d = (pl.read_parquet(PS / f"{k}_prices.parquet").with_columns(pl.lit(k).alias("scenario"))
             .select(["scenario", "country", "hour", "price_eur_per_mwh"]))
        # BE = the reshaped (realistic-shape) series; other countries = the coupled model
        d = d.filter(pl.col("country") != "BE")
        be_re = pl.DataFrame({"scenario": [k] * 8760, "country": ["BE"] * 8760,
                              "hour": list(range(8760)), "price_eur_per_mwh": BE[k]})
        parts.append(pl.concat([d, be_re]))
    for cc in COUNTRIES:
        if real[cc] is None:
            continue
        parts.append(pl.DataFrame({"scenario": ["real_day_ahead"] * 8760, "country": [cc] * 8760,
                                   "hour": list(range(8760)), "price_eur_per_mwh": real[cc]}))
    parts.append(pl.DataFrame({"scenario": ["balancing"] * 8760, "country": ["BE"] * 8760,
                               "hour": list(range(8760)), "price_eur_per_mwh": balancing}))
    long_all = (pl.concat(parts, how="vertical_relaxed").join(dt_map, on="hour", how="left")
                .select(["scenario", "country", "hour", "datetime_CET", "price_eur_per_mwh"]))
    long_parquet = out / "price_scenarios_2023_long.parquet"
    long_all.write_parquet(long_parquet)

    real_all = []
    for f in sorted(DA.glob(f"day_ahead_prices_*_{YEAR}.parquet")):
        cc = f.stem.split("_")[-2]
        s = pl.read_parquet(f)["price_eur_per_mwh"].to_numpy()[:8760]
        if len(s) < 8760:
            continue
        real_all.append(pl.DataFrame({"country": [cc] * 8760, "hour": list(range(8760)),
                                      "price_eur_per_mwh": s}))
    real_parquet = out / "real_2023_dayahead_long.parquet"
    pl.concat(real_all).join(dt_map, on="hour", how="left").write_parquet(real_parquet)

    return {"xlsx": xlsx, "long_parquet": long_parquet, "real_parquet": real_parquet,
            "rows": long_all.height}


def main() -> None:
    import os
    paths = build_price_workbook()
    print("=== WROTE ===")
    for key in ("xlsx", "long_parquet", "real_parquet"):
        f = paths[key]
        print(f"  {f.name:38s} {os.path.getsize(f) / 1e6:.2f} MB")
    print(f"long dataset rows: {paths['rows']:,}")


if __name__ == "__main__":
    main()
