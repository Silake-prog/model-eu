"""Routing/composition tests for the source-aware dispatchers (no file IO)."""
import datetime as dt

import numpy as np
import polars as pl
import pytest

import supplyforge.process.capacity_factors_dispatch as cfrun
import supplyforge.process.hydro_inflow_dispatch as hrun
from supplyforge.process.pecd.capacity_factor import _long_frame, ROR_PLANT_TYPE


def _ws_frame(plant_type: str, val: float, ws: str = "2009") -> pl.DataFrame:
    return _long_frame(np.full(8760, val), plant_type, ws)


def _fake_legacy_frame() -> pl.DataFrame:
    """Legacy ENTSO-E CF frame: plant_type + datetime hour + capacity_factor."""
    base = dt.datetime(2009, 1, 1)
    parts = []
    for plant_type, val in [
        ("Solar", 0.2), ("Wind Onshore", 0.3), ("Wind Offshore", 0.4), (ROR_PLANT_TYPE, 0.5),
    ]:
        hours = [base + dt.timedelta(hours=i) for i in range(10)]
        parts.append(pl.DataFrame({"plant_type": [plant_type] * 10, "hour": hours, "capacity_factor": [val] * 10}))
    return pl.concat(parts)


def _plant_types(path) -> set:
    return set(pl.read_parquet(path)["plant_type"].unique().to_list())


# --- capacity-factor dispatcher -------------------------------------------

def test_pecd_wind_solar_and_pecd_ror(monkeypatch, tmp_path):
    monkeypatch.setattr(cfrun, "pecd_wind_solar_frames",
                        lambda c, y, cfg: [_ws_frame("Solar", 0.2), _ws_frame("Wind Onshore", 0.3), _ws_frame("Wind Offshore", 0.4)])
    monkeypatch.setattr(cfrun, "pecd_ror_frame", lambda c, y, cfg: _ws_frame(ROR_PLANT_TYPE, 0.5))
    out = tmp_path / "cf.parquet"
    cfrun.run_capacity_factors("FR", 2009, {"res_source": "pecd", "hydro_source": "pecd"}, out)
    assert _plant_types(out) == {"Solar", "Wind Onshore", "Wind Offshore", ROR_PLANT_TYPE}


def test_entsoe_wind_solar_and_pecd_ror(monkeypatch, tmp_path):
    monkeypatch.setattr(cfrun, "compute_intermittent_capacity_factors", lambda c, y: _fake_legacy_frame())
    monkeypatch.setattr(cfrun, "pecd_ror_frame", lambda c, y, cfg: _ws_frame(ROR_PLANT_TYPE, 0.5))
    out = tmp_path / "cf.parquet"
    cfrun.run_capacity_factors("FR", 2009, {"res_source": "entsoe", "hydro_source": "pecd"}, out)
    assert _plant_types(out) == {"Solar", "Wind Onshore", "Wind Offshore", ROR_PLANT_TYPE}


def test_pecd_wind_solar_and_entsoe_ror(monkeypatch, tmp_path):
    monkeypatch.setattr(cfrun, "pecd_wind_solar_frames",
                        lambda c, y, cfg: [_ws_frame("Solar", 0.2), _ws_frame("Wind Onshore", 0.3), _ws_frame("Wind Offshore", 0.4)])
    monkeypatch.setattr(cfrun, "compute_intermittent_capacity_factors", lambda c, y: _fake_legacy_frame())
    out = tmp_path / "cf.parquet"
    cfrun.run_capacity_factors("FR", 2009, {"res_source": "pecd", "hydro_source": "entsoe"}, out)
    assert _plant_types(out) == {"Solar", "Wind Onshore", "Wind Offshore", ROR_PLANT_TYPE}


def test_entsoe_entsoe_delegates_to_legacy(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(cfrun, "calculate_intermittent_capacity_factors",
                        lambda c, y: called.setdefault("legacy", (c, y)))
    cfrun.run_capacity_factors("FR", 2009, {"res_source": "entsoe", "hydro_source": "entsoe"}, tmp_path / "cf.parquet")
    assert called.get("legacy") == ("FR", 2009)


def test_era5_wind_solar_and_entsoe_ror(monkeypatch, tmp_path):
    """res_source=era5 routes wind/solar to the ERA5 engine; RoR falls back to ENTSO-E."""
    import supplyforge.process.era5.capacity_factor as e5
    monkeypatch.setattr(e5, "era5_wind_solar_frames",
                        lambda c, y, cfg: [_ws_frame("Solar", 0.15), _ws_frame("Wind Onshore", 0.25), _ws_frame("Wind Offshore", 0.35)])
    monkeypatch.setattr(cfrun, "compute_intermittent_capacity_factors", lambda c, y: _fake_legacy_frame())
    out = tmp_path / "cf.parquet"
    cfrun.run_capacity_factors("FR", 2009, {"res_source": "era5", "hydro_source": "auto"}, out)
    assert _plant_types(out) == {"Solar", "Wind Onshore", "Wind Offshore", ROR_PLANT_TYPE}


def test_era5_fail_soft_when_engine_empty(monkeypatch, tmp_path):
    """If the ERA5 engine yields nothing (e.g. atlite absent), the file is still valid (RoR only)."""
    import supplyforge.process.era5.capacity_factor as e5
    monkeypatch.setattr(e5, "era5_wind_solar_frames", lambda c, y, cfg: [])
    monkeypatch.setattr(cfrun, "compute_intermittent_capacity_factors", lambda c, y: _fake_legacy_frame())
    out = tmp_path / "cf.parquet"
    cfrun.run_capacity_factors("FR", 2009, {"res_source": "era5", "hydro_source": "auto"}, out)
    assert _plant_types(out) == {ROR_PLANT_TYPE}     # wind/solar empty, RoR from ENTSO-E


# --- inflow dispatcher -----------------------------------------------------

def test_inflow_routes_to_pecd(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(hrun, "process_pecd_inflow", lambda c, y, out: called.setdefault("pecd", (c, y)))
    hrun.run_hydro_inflow("FR", 2009, {"res_source": "pecd", "hydro_source": "pecd"}, output_path=tmp_path / "i.parquet")
    assert called.get("pecd") == ("FR", 2009)


def test_inflow_routes_to_entsoe(monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr(hrun.pd, "read_parquet", lambda p: object())

    def fake_gen(**kwargs):
        called["entsoe"] = True
        return pl.DataFrame({"inflow_MW": [1.0, 2.0]})

    monkeypatch.setattr(hrun, "generate_hourly_inflow_dataset", fake_gen)
    out = tmp_path / "i.parquet"
    hrun.run_hydro_inflow("FR", 2009, {"res_source": "entsoe", "hydro_source": "entsoe"},
                          production_path="p", stock_path="s", output_path=out)
    assert called.get("entsoe") and out.exists()
