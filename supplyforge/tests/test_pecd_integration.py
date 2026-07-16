"""End-to-end PECD integration tests.

(A) synthetic staged PECD CSVs -> the real processors -> canonical Parquet, then
    asserts the canonical files satisfy the consumer contract (schema, 8760 rows
    per plant type, single-WS selectability, value ranges);
(B) feeds synthetic PECD canonical inputs into the *unmodified* ``create_model``
    and asserts the reservoir triplet + intermittent techs are built (spec §6.4).
"""
import numpy as np
import pandas as pd
import polars as pl
import pytest

from supplyforge.process.pecd import capacity_factor as pcf
from supplyforge.process.pecd import hydro_inflow as phi

INTERMITTENT_LABELS = {"Solar", "Wind Onshore", "Wind Offshore", "Hydro Run-of-river and poundage"}


def _write_hourly_csv(path, year, zone_values, periods):
    idx = pd.date_range(f"{year}-01-01", periods=periods, freq="h")
    df = pd.DataFrame({z: [v] * periods for z, v in zone_values.items()}, index=pd.Index(idx, name="Date"))
    with open(path, "w") as f:
        f.write("# synthetic PECD test CSV\n")
        df.to_csv(f)


def _write_weekly_csv(path, year, zone_values, n_weeks=52):
    idx = pd.date_range(f"{year}-01-06", periods=n_weeks, freq="7D")
    df = pd.DataFrame({z: [v] * n_weeks for z, v in zone_values.items()}, index=pd.Index(idx, name="Date"))
    with open(path, "w") as f:
        f.write("# synthetic PECD test CSV (weekly energy MWh/week)\n")
        df.to_csv(f)


@pytest.fixture
def staged_pecd(tmp_path, monkeypatch):
    """Stage synthetic PECD CSVs and redirect find_pecd_csv to them."""
    year = 2009
    files = {
        "SPV": tmp_path / "spv.csv",
        "WON": tmp_path / "won.csv",
        "WOF": tmp_path / "wof.csv",
        "HRI": tmp_path / "hri.csv",
        "HRO": tmp_path / "hro.csv",
        "HPO": tmp_path / "hpo.csv",
    }
    _write_hourly_csv(files["SPV"], year, {"FR00": 0.2}, 8760)
    _write_hourly_csv(files["WON"], year, {"FR15": 0.3, "FR16": 0.5}, 8760)   # multi-zone -> weighted
    _write_hourly_csv(files["WOF"], year, {"FR90": 0.4}, 8760)
    _write_weekly_csv(files["HRI"], year, {"FR00": 168.0})                    # -> 1.0 MW
    _write_weekly_csv(files["HRO"], year, {"FR00": 84.0})                     # HRO+HPO=168 -> CF 0.5 @ 2 MW
    _write_weekly_csv(files["HPO"], year, {"FR00": 84.0})

    def fake_find(code, year_, study_dir=None):
        return files.get(code)

    monkeypatch.setattr(phi, "find_pecd_csv", fake_find)
    monkeypatch.setattr(pcf, "find_pecd_csv", fake_find)
    monkeypatch.setattr(pcf, "_ror_installed_capacity", lambda c, y: 2.0)
    return tmp_path, year


def test_processors_produce_contract_compliant_files(staged_pecd, tmp_path):
    out_dir, year = staged_pecd
    cfg = {"pecd": {"zone_capacities": {"wind_onshore": {"FR15": 1.0, "FR16": 3.0}}}}

    cf_path = out_dir / "cf.parquet"
    inflow_path = out_dir / "inflow.parquet"
    pcf.process_pecd_capacity_factors("FR", year, cfg, cf_path, ror_source="pecd")
    phi.process_pecd_inflow("FR", year, inflow_path)

    cf = pl.read_parquet(cf_path)
    # schema + labels
    assert set(cf.columns) == {"hour", "plant_type", "WS", "capacity_factor"}
    assert set(cf["plant_type"].unique().to_list()) == INTERMITTENT_LABELS
    assert cf["WS"].unique().to_list() == ["2009"]
    # 8760 rows per plant type; CF in [0, 1]; wind capacity-weighted = 0.45
    for pt in INTERMITTENT_LABELS:
        sub = cf.filter(pl.col("plant_type") == pt)
        assert sub.height == 8760
    assert cf["capacity_factor"].min() >= 0.0 and cf["capacity_factor"].max() <= 1.0
    won = cf.filter(pl.col("plant_type") == "Wind Onshore")["capacity_factor"]
    assert abs(won.mean() - 0.45) < 1e-9

    # consumer (add_intermittent_tech) filter logic: plant_type [+ optional WS] -> 8760
    for pt in INTERMITTENT_LABELS:
        assert cf.filter(pl.col("plant_type") == pt).filter(pl.col("WS") == "2009").height == 8760

    inflow = pl.read_parquet(inflow_path)
    assert set(inflow.columns) == {"inflow_MW", "timestamp"}
    assert inflow.height == 8760
    assert inflow["inflow_MW"].min() >= 0.0
    assert abs(inflow["inflow_MW"].mean() - 1.0) < 1e-9  # 168 MWh/week / 168 = 1 MW


def test_create_model_builds_with_pecd_inputs(staged_pecd, tmp_path, monkeypatch):
    """Spec §6.4: create_model builds the reservoir triplet + intermittent techs
    from PECD-shaped canonical inputs, without touching model code."""
    out_dir, year = staged_pecd
    cfg = {"pecd": {"zone_capacities": {"wind_onshore": {"FR15": 1.0, "FR16": 3.0}}}}
    cf_path = out_dir / "cf.parquet"
    inflow_path = out_dir / "inflow.parquet"
    pcf.process_pecd_capacity_factors("FR", year, cfg, cf_path, ror_source="pecd")
    phi.process_pecd_inflow("FR", year, inflow_path)

    import supplyforge.create_pommes_craft_model as cpm

    capacities = pl.DataFrame({
        "index": [0],
        "Solar": [1000.0], "Wind Onshore": [2000.0], "Wind Offshore": [1500.0],
        "Hydro Run-of-river and poundage": [500.0], "Hydro Water Reservoir": [3000.0],
        "Hydro Pumped Storage": [1000.0],
    })
    synth = {
        "installed_capacities": capacities,
        "capacity_factors": pl.read_parquet(cf_path),
        "inflow": pl.read_parquet(inflow_path),
        "availability": pl.DataFrame(schema={"plant_type": pl.Utf8, "availability_share": pl.Float64}),
    }
    monkeypatch.setattr(cpm, "_get_input_data_file", lambda country, yr, input_file: synth[input_file])
    monkeypatch.setattr(cpm, "get_storage_energy_capacities", lambda *a, **k: 5000.0)
    monkeypatch.setattr(cpm, "add_imports", lambda *a, **k: None)  # skip NTC/day-ahead machinery

    model = cpm.create_model("FR", year, 2025)
    area = model.areas["FR"]

    for tech_name in ("Solar", "Wind_Onshore", "Wind_Offshore", "RoR_Pondage"):
        assert tech_name in area.components, f"missing intermittent tech {tech_name}"
    for name in ("lake_hydro_store", "lake_hydro_inflow", "lake_hydro_plant"):
        assert name in area.components, f"missing reservoir component {name}"
