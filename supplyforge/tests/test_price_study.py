"""Tests for ``supplyforge.price_study`` — the electricity price-factor engine.

Two tiers:
  * lightweight (config / commodity / fleet / SRMC parsing) — pure data, fast;
  * solve tests (marginal-price extraction, factor response) — need a HiGHS
    solver via pommes_craft and the keyless ERAA/PECD/Power-Stats inputs.

Solve tests are skipped automatically when pommes_craft or the staged data are
absent, so the lightweight tier still runs anywhere. The file is also runnable
as a plain script (``python test_price_study.py``) for environments without
pytest.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

# Prefer the local repo over any stale site-packages copy of supplyforge
# (mirrors the notebooks' ``sys.path.insert(0, "..")``).
_REPO = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    import pytest
except ImportError:  # plain-script fallback
    pytest = None

from supplyforge import price_study as ps

# Small, fast run config shared by the solve tests.
_SMALL = dict(countries=["FR", "BE", "DE"], hour_stride=2920)  # ~3 hours


def _cfg(**over):
    cfg = ps.load_config()
    cfg.countries = over.get("countries", _SMALL["countries"])
    cfg.hour_stride = over.get("hour_stride", _SMALL["hour_stride"])
    return cfg


def _data_available() -> bool:
    try:
        cfg = _cfg()
        ps.eraa_fleet(cfg)
        return True
    except Exception:
        return False


def _solver_available() -> bool:
    try:
        import pommes_craft  # noqa: F401
        return True
    except Exception:
        return False


_skip_solve = (
    pytest.mark.skipif(not (_data_available() and _solver_available()),
                       reason="needs pommes_craft solver + staged ERAA/PECD data")
    if pytest else (lambda f: f)
)


# --------------------------------------------------------------------------
# Lightweight tier
# --------------------------------------------------------------------------
def test_load_config_fields():
    cfg = ps.load_config()
    assert cfg.countries and isinstance(cfg.countries, list)
    assert isinstance(cfg.year, int)
    assert "base" in cfg.scenarios
    assert cfg.hours == list(range(0, ps.HOURS_PER_YEAR, cfg.hour_stride))


def test_srmc_merit_order():
    """SRMC must order the merit stack: nuclear < gas < oil; CO2 lifts coal."""
    fuels = {"nuclear": 7.0, "gas": 26.0, "coal": 10.0, "lignite": 8.0,
             "oil": 55.0, "biomass": 29.0, "co2": 80.0}
    cfg = ps.load_config()
    s = {c: ps.srmc(c, fuels, cfg) for c in
         ["nuclear", "gas", "coal", "oil"]}
    assert s["nuclear"] < s["gas"] < s["oil"]
    # higher CO2 raises a coal SRMC
    hi = dict(fuels, co2=160.0)
    assert ps.srmc("coal", hi, cfg) > ps.srmc("coal", fuels, cfg)


def test_to_8760():
    assert len(ps._to_8760(np.arange(100))) == ps.HOURS_PER_YEAR
    assert len(ps._to_8760(np.arange(10000))) == ps.HOURS_PER_YEAR


# --------------------------------------------------------------------------
# Solve tier
# --------------------------------------------------------------------------
@_skip_solve
def test_eraa_commodities_positive():
    fuels = ps.eraa_commodities(_cfg())
    for k in ("gas", "coal", "oil", "co2"):
        assert fuels[k] > 0, f"{k} price should be positive"


@_skip_solve
def test_marginal_price_finite_and_positive():
    """A solved scenario yields finite, positive hourly prices for every country."""
    cfg = _cfg()
    data = ps.load_study_data(cfg)
    res = ps.run_scenario(data, "base")
    p = res.prices["price_eur_per_mwh"].to_numpy()
    assert np.isfinite(p).all()
    assert (p >= 0).all()
    assert set(res.prices["country"]) == set(cfg.countries)


@_skip_solve
def test_gas_crisis_raises_gas_marginal_price():
    """A gas-price spike must raise the price in a gas-marginal country (DE)."""
    cfg = _cfg()
    data = ps.load_study_data(cfg)
    base = ps.run_scenario(data, "base").mean_price()
    # ad-hoc scenario: 4x gas
    data.cfg.scenarios["_gastest"] = {"gas_eur_per_gj": 80.0}
    crisis = ps.run_scenario(data, "_gastest").mean_price()
    assert crisis["DE"] > base["DE"] + 5.0


@_skip_solve
def test_realistic_reproduces_be_2023():
    """Reproducibility guard for the headline: the realistic BE model must track
    the real 2023 day-ahead price (mean in band, high hourly correlation).

    Re-derives the result from raw data + the live solve — catches engine drift
    or a regression in the audit-fixed CF/border handling.
    """
    import polars as pl
    from supplyforge import RESULTS_DIR
    cfg = ps.load_config()
    cfg.countries = [cfg.focus]                  # single zone is enough + fast
    demand = ps.powerstats_load(cfg.year)
    res = ps.run_realistic(cfg, "base", demand)
    mod = (res.prices[res.prices.country == cfg.focus]
           .sort_values("hour")["price_eur_per_mwh"].to_numpy())
    fp = RESULTS_DIR / "day_ahead_prices" / f"day_ahead_prices_{cfg.focus}_{cfg.year}.parquet"
    if not fp.exists():
        return  # no real-price file staged -> skip the empirical part
    real = pl.read_parquet(fp)["price_eur_per_mwh"].to_numpy()
    n = min(len(mod), len(real)); m, r = mod[:n], real[:n]
    v = np.isfinite(m) & np.isfinite(r)
    assert 80.0 <= m[v].mean() <= 110.0, f"BE modelled mean {m[v].mean():.1f} off band"
    assert np.corrcoef(m[v], r[v])[0, 1] >= 0.85, "BE hourly correlation regressed"


def _run_as_script():
    """Run every test_* function in-process (for envs without pytest)."""
    have = _data_available() and _solver_available()
    solve_tests = {"test_eraa_commodities_positive",
                   "test_marginal_price_finite_and_positive",
                   "test_gas_crisis_raises_gas_marginal_price",
                   "test_realistic_reproduces_be_2023"}
    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        if name in solve_tests and not have:
            print(f"SKIP {name} (no solver/data)")
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
    return fails


if __name__ == "__main__":
    import sys
    sys.exit(_run_as_script())
