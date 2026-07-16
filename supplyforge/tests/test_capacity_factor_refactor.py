"""Regression guard for the additive capacity_factor.py refactor (compute_/calculate_).

Runs compute_intermittent_capacity_factors on synthetic ENTSO-E-shaped inputs and
checks CF = generation / installed_capacity, ensuring the extracted compute step
still produces the legacy result.
"""
import datetime as dt

import polars as pl

import supplyforge.process.capacity_factor as cf


def test_compute_intermittent_capacity_factors(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "RESULTS_DIR", tmp_path)

    hours = [dt.datetime(2009, 1, 1, h, tzinfo=dt.timezone.utc) for h in range(3)]
    generation = pl.DataFrame({
        "('index', '')": hours,
        "('Solar', 'Actual Aggregated')": [50.0, 100.0, 0.0],
        "('Wind Onshore', 'Actual Aggregated')": [200.0, 400.0, 600.0],
    })
    installed = pl.DataFrame({
        "index": [0],
        "Solar": [100.0], "Wind Onshore": [1000.0],
        "Wind Offshore": [500.0], "Hydro Run-of-river and poundage": [200.0],
    })
    (tmp_path / "generation").mkdir()
    (tmp_path / "installed_capacities").mkdir()
    generation.write_parquet(tmp_path / "generation" / "generation_FR_2009.parquet")
    installed.write_parquet(tmp_path / "installed_capacities" / "installed_capacities_FR_2009.parquet")

    out = cf.compute_intermittent_capacity_factors("FR", 2009)
    assert out is not None
    assert {"plant_type", "hour", "capacity_factor", "installed_capacity", "generation_mw"}.issubset(out.columns)

    solar = out.filter(pl.col("plant_type") == "Solar").sort("hour")["capacity_factor"].to_list()
    wind = out.filter(pl.col("plant_type") == "Wind Onshore").sort("hour")["capacity_factor"].to_list()
    assert solar == [0.5, 1.0, 0.0]          # [50,100,0] / 100
    assert wind == [0.2, 0.4, 0.6]           # [200,400,600] / 1000


def test_compute_returns_none_on_missing_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "RESULTS_DIR", tmp_path)
    assert cf.compute_intermittent_capacity_factors("ZZ", 1999) is None
