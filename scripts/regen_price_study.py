#!/usr/bin/env python
"""Regenerate the COUPLED-model results the notebook can't compute live cheaply.

The realistic single-zone model (`ps.run_realistic`, ~30 s) is run live in the
notebook for everything it can do correctly: the hourly profile, premium, market
layers, cross-country premium, and the BE-DOMESTIC levers (nuclear / demand /
interconnection). It cannot carry a SYSTEM-WIDE commodity shock (its imports are
fixed at real day-ahead prices, so a European gas crisis shows ~0 effect on the
focus zone). Those — plus the 9-country validation bar — require the multi-zone
coupled model (`ps.run_current`, ~7 min each), which is too slow to run live.

This script produces exactly those coupled artefacts, with the AUDIT-FIXED engine
(clipped CFs, real-priced/​skipped borders), into ``results/price_study/`` so the
notebook can load them behind a flag. It is committed + tested (see
``supplyforge/tests/test_price_study.py::test_regen_reproducible``) so the
headline numbers are reproducible — no stale, undocumented caches.

Run:  python -m scripts.regen_price_study      (≈ 25 min, needs the bucket data)
"""
from __future__ import annotations
import logging
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
logging.disable(logging.WARNING)

import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402

from supplyforge import price_study as ps  # noqa: E402
from supplyforge.grid_model import grid_flows  # noqa: E402
from supplyforge.utils import _get_input_data_file  # noqa: E402

OUT = REPO / "results" / "price_study"
OUT.mkdir(parents=True, exist_ok=True)

# Coupled scenarios for the factor lenses. The multi-zone coupled model handles
# every lever correctly (imports respond), so ALL factor sensitivity is consistent
# here: base (validation + price formation + congestion), the system-wide commodity
# shocks (gas, CO2), and the BE-domestic shocks (interconnection, nuclear).
COUPLED_SCENARIOS = ["base", "gas_crisis", "high_co2", "be_isolation", "be_nuclear_outage",
                     "fr_nuclear_outage", "be_renewables"]


def _focus_decomposition(base, cfg, demand):
    """The BE focus decomposition built from the coupled base solve."""
    fleet_long = []
    for cc in cfg.countries:
        caps = _get_input_data_file(cc, cfg.year, "installed_capacities").to_pandas().iloc[0]
        for cat, ptype in {"nuclear": "Nuclear", "gas": "Fossil Gas",
                           "coal": "Fossil Hard coal", "lignite": "Lignite",
                           "oil": "Fossil Oil", "biomass": "Biomass"}.items():
            gw = float(caps.get(ptype, 0) or 0) / 1e3
            if gw > 0:
                fleet_long.append({"country": cc, "category": cat, "gw": gw})
    data = ps.StudyData(fleet=pd.DataFrame(fleet_long), fuels=ps.fuels_for_year(cfg, {}),
                        demand=demand, cf={}, links=ps.ntc_links(cfg), cfg=cfg)
    return ps.focus_decomposition(base, data, cfg.focus)


def main() -> None:
    cfg = ps.load_config()
    demand = ps.powerstats_load(cfg.year)
    base = None
    for name in COUPLED_SCENARIOS:
        t = time.time()
        res = ps.run_current(cfg, name, demand)
        res.prices.to_parquet(OUT / f"{name}_prices.parquet", index=False)
        be = res.prices[res.prices.country == cfg.focus]["price_eur_per_mwh"].mean()
        print(f"coupled {name}: {time.time()-t:.0f}s | BE {be:.1f} EUR/MWh", flush=True)
        if name == "base":
            base = res
            # BE-border flows (congestion lens) + focus decomposition (price formation)
            fl = grid_flows(res.model)
            lc = "link" if "link" in fl.columns else fl.columns[-1]
            (fl.filter(pl.col(lc).str.contains(cfg.focus))
               .select([pl.col(lc).alias("link"), "hour", "value"])
               .write_parquet(OUT / "be_flows.parquet"))
            _focus_decomposition(base, cfg, demand).to_parquet(
                OUT / f"decomposition_{cfg.focus}.parquet", index=False)
    print("REGEN DONE", flush=True)


if __name__ == "__main__":
    main()
