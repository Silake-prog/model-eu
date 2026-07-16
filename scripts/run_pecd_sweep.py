#!/usr/bin/env python
"""PECD climate-adequacy sweep driver (paper: Fully Renewable Systems under Adequacy Stress).

Orchestrates `scripts/run_adequacy.py` over the paper's grid

    (gcm, ssp, climate_year) x demand_bundle x supply_mix

and aggregates each run's adequacy dashboard (per-country ENS/LOLE/prices) into one
tidy CSV keyed by the sweep axes.

Because the model's scenario is an **import-time process global**, every combo runs in a
**fresh subprocess** with its own environment:

  CLEVER_SCENARIO      = composed scenario string (validated against the flag registry)
  SUPPLYFORGE_DATA     = <datasets-root>/<gcm>_<ssp>_<year>   (the PECD dataset dir;
                         PECD output filenames do NOT encode the SSP/GCM, so each climate
                         scenario must live in its own dataset dir)
  POMMES_EUR_VRE_RAW   = 1   (M2: bypass the CLEVER load-factor rescaling so the PECD
                         capacity-factor LEVEL — where the climate signal lives — survives)
  CLEVER_DATA          = <out>/runs/<combo_id>   (isolated model results/exports per combo)

Dataset dirs are produced beforehand with supplyforge (on a machine with CDS credentials):

    snakemake --cores all --config res_source=pecd hydro_source=pecd
    # with supplyforge/config/config.yaml: pecd.origin=<gcm>, emission_scenario=<ssp>,
    # temporal_period=future_projections, climate_years=[<year>]
    # and SUPPLYFORGE_DATA pointing at <datasets-root>/<gcm>_<ssp>_<year>

Usage (cluster):
    python scripts/run_pecd_sweep.py \
        --datasets-root $WORK/pecd_datasets --out $WORK/sweep_2050 \
        --gcms ec_earth3 --ssps ssp2_4_5,ssp5_8_5 --years 2050 \
        --demands elecX180_h2HIGH,elecX125_h2central --mixes fullyRE,nukeAllowed

    --dry-run        compose + validate the grid, check dataset dirs, write the manifest,
                     launch nothing (works in the code-only env).
    --collect-only   skip runs; (re-)aggregate existing per-combo exports.
    --plots          after aggregation, write the paper figures (LOLE heatmap,
                     system-ENS-vs-year per mix, fully-RE vs nuclear gap table).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── Supply-mix presets (paper definitions) ─────────────────────────────────────
# fullyRE     : no fossil methane, no nuclear  -> VRE + hydro + biomethane + H2 balancing
# nukeAllowed : no fossil methane, nuclear expandable
# A raw flag fragment (e.g. "noGas_nuke_nukeXXL") is also accepted as a mix value.
MIX_PRESETS: dict[str, str] = {
    "fullyRE": "noGas_noNuke",
    "nukeAllowed": "noGas_nuke",
}

# Default deep-decarbonisation flag stack shared by all paper runs (overridable).
DEFAULT_BASE = "R0_v1"
DEFAULT_EXTRA = "bioLow_atr_el700_corr2x_vreEXT_nofloor_noElecFloor_co2150_batt20"

# run_adequacy.py writes EXPORT_DIR/adequacy_dashboard_<tag>.csv with these columns.
_DASHBOARD_RENAME = {
    "Country": "country",
    "ENS (GWh)": "ens_gwh",
    "LOLE (h)": "lole_hours",
    "Peak LS (MW)": "peak_ls_mw",
    "Max dual (EUR/MWh)": "max_dual_eur_mwh",
    "Hours at VoLL": "hours_at_voll",
    "Mean price (EUR/MWh)": "mean_price_eur_mwh",
}


def compose_scenario(base: str, extra: str, demand: str, mix_flags: str, year: int) -> str:
    """Assemble the scenario string: base_extra_demand_mix_wyYYYY (single underscores)."""
    parts = [base]
    for frag in (extra, demand, mix_flags, f"wy{year}"):
        frag = frag.strip().strip("_")
        if frag:
            parts.append(frag)
    s = "_".join(parts)
    return re.sub(r"__+", "_", s)


def validate_scenario(s: str) -> None:
    from pommes_eur.scenario.registry import explain, validate
    if not validate(s):
        raise SystemExit(
            f"Scenario does not validate against the flag registry:\n  {s}\n"
            f"Diagnostic: {explain(s)}"
        )


def dataset_dir_for(root: Path, gcm: str, ssp: str, year: int) -> Path:
    return root / f"{gcm}_{ssp}_{year}"


def check_dataset(ds: Path, year: int, areas: list[str] | None) -> str | None:
    """Return None if the dataset dir looks usable, else a human-readable problem."""
    cf = ds / "capacity_factors"
    if not cf.is_dir():
        return f"missing dir: {cf}"
    have = list(cf.glob(f"capacity_factors_*_{year}.parquet"))
    if not have:
        return f"no capacity_factors_*_{year}.parquet in {cf}"
    if areas:
        missing = [a for a in areas if not (cf / f"capacity_factors_{a}_{year}.parquet").exists()]
        if missing:
            return f"missing countries for {year}: {missing}"
    return None


def combo_id(gcm: str, ssp: str, year: int, demand: str, mix_name: str) -> str:
    return f"{gcm}__{ssp}__{year}__{demand}__{mix_name}"


def build_grid(args) -> list[dict]:
    grid = []
    for gcm in args.gcms:
        for ssp in args.ssps:
            for year in args.years:
                for demand in args.demands:
                    for mix_name in args.mixes:
                        mix_flags = MIX_PRESETS.get(mix_name, mix_name)
                        scenario = compose_scenario(args.base, args.extra, demand, mix_flags, year)
                        grid.append({
                            "id": combo_id(gcm, ssp, year, demand, mix_name),
                            "gcm": gcm, "ssp": ssp, "year": year,
                            "demand": demand, "mix": mix_name,
                            "mix_flags": mix_flags, "scenario": scenario,
                        })
    return grid


def run_combo(combo: dict, args) -> dict:
    """Launch run_adequacy.py for one combo in a fresh subprocess. Returns status record."""
    ds = dataset_dir_for(args.datasets_root, combo["gcm"], combo["ssp"], combo["year"])
    problem = check_dataset(ds, combo["year"], args.areas)
    if problem is not None:
        return {**combo, "status": "missing_dataset", "detail": problem}

    run_dir = args.out / "runs" / combo["id"]
    run_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["CLEVER_SCENARIO"] = combo["scenario"]
    env["SUPPLYFORGE_DATA"] = str(ds)
    env["POMMES_EUR_VRE_RAW"] = "1"
    env["CLEVER_DATA"] = str(run_dir)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "demandforge")])
    if args.areas:
        env["CLEVER_AREAS"] = ",".join(args.areas)

    log_path = run_dir / "run.log"
    t0 = time.time()
    with open(log_path, "w") as log:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_adequacy.py")],
            env=env, cwd=str(REPO_ROOT), stdout=log, stderr=subprocess.STDOUT,
        )
    status = "ok" if proc.returncode == 0 else "failed"
    return {**combo, "status": status, "returncode": proc.returncode,
            "wall_s": round(time.time() - t0, 1), "run_dir": str(run_dir),
            "dataset": str(ds), "log": str(log_path)}


def collect(grid: list[dict], args) -> "object":
    """Aggregate each combo's adequacy dashboard into one tidy DataFrame."""
    import pandas as pd
    frames = []
    for combo in grid:
        run_dir = args.out / "runs" / combo["id"]
        export_dir = run_dir / "export" / combo["scenario"]
        hits = sorted(export_dir.glob("adequacy_dashboard_*.csv"))
        if not hits:
            continue
        df = pd.read_csv(hits[-1]).rename(columns=_DASHBOARD_RENAME)
        for k in ("gcm", "ssp", "year", "demand", "mix", "scenario"):
            df[k] = combo[k]
        df["combo_id"] = combo["id"]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    tidy = pd.concat(frames, ignore_index=True)
    key_cols = ["gcm", "ssp", "year", "demand", "mix", "scenario", "combo_id", "country"]
    other = [c for c in tidy.columns if c not in key_cols]
    return tidy[key_cols + other]


def make_plots(tidy, args) -> None:
    """Paper figures: LOLE heatmap, system-ENS-vs-year per mix, fully-RE-vs-nuclear gap."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figs = args.out / "figs"
    figs.mkdir(parents=True, exist_ok=True)
    tidy = tidy.copy()
    tidy["climate"] = tidy["gcm"] + "·" + tidy["ssp"] + "·" + tidy["year"].astype(str)

    # (a) LOLE heatmap: country x (climate · demand · mix)
    piv = tidy.pivot_table(index="country", values="lole_hours", aggfunc="max",
                           columns=["climate", "demand", "mix"])
    if not piv.empty:
        fig, ax = plt.subplots(figsize=(max(6, 0.5 * piv.shape[1] + 4), max(4, 0.3 * piv.shape[0] + 2)))
        im = ax.imshow(piv.values, aspect="auto", cmap="Reds")
        ax.set_yticks(range(piv.shape[0])); ax.set_yticklabels(piv.index)
        ax.set_xticks(range(piv.shape[1]))
        ax.set_xticklabels(["\n".join(map(str, c)) for c in piv.columns], fontsize=7, rotation=45, ha="right")
        fig.colorbar(im, ax=ax, label="LOLE (h)")
        ax.set_title("Loss-of-load expectation by country and scenario")
        fig.tight_layout(); fig.savefig(figs / "lole_heatmap.png", dpi=200); plt.close(fig)

    # (b) system ENS vs climate year, one line per (mix, demand)
    sys_ens = (tidy.groupby(["year", "mix", "demand", "gcm", "ssp"], as_index=False)["ens_gwh"].sum())
    if not sys_ens.empty:
        fig, ax = plt.subplots(figsize=(8, 5))
        for (mix, demand, gcm, ssp), g in sys_ens.groupby(["mix", "demand", "gcm", "ssp"]):
            g = g.sort_values("year")
            ax.plot(g["year"], g["ens_gwh"], marker="o", label=f"{mix} | {demand} | {gcm}·{ssp}")
        ax.set_xlabel("climate year"); ax.set_ylabel("system ENS (GWh)")
        ax.set_title("Adequacy stress vs climate year"); ax.legend(fontsize=7)
        fig.tight_layout(); fig.savefig(figs / "ens_vs_year.png", dpi=200); plt.close(fig)

    # (c) fully-RE vs nuclear-allowed adequacy gap (system totals per climate x demand)
    sys_tot = (tidy.groupby(["climate", "demand", "mix"], as_index=False)
               .agg(ens_gwh=("ens_gwh", "sum"), max_lole_h=("lole_hours", "max")))
    gap = sys_tot.pivot_table(index=["climate", "demand"], columns="mix",
                              values=["ens_gwh", "max_lole_h"])
    gap.to_csv(figs / "mix_adequacy_gap.csv")
    print(f"figures + gap table written to {figs}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets-root", type=Path, required=True,
                    help="dir containing <gcm>_<ssp>_<year>/ PECD dataset dirs")
    ap.add_argument("--out", type=Path, required=True, help="sweep output dir")
    ap.add_argument("--gcms", type=lambda s: s.split(","), default=["ec_earth3"])
    ap.add_argument("--ssps", type=lambda s: s.split(","), default=["ssp2_4_5", "ssp5_8_5"])
    ap.add_argument("--years", type=lambda s: [int(y) for y in s.split(",")], default=[2050])
    ap.add_argument("--demands", type=lambda s: s.split(","),
                    default=["elecX180_h2HIGH", "elecX125_h2central"])
    ap.add_argument("--mixes", type=lambda s: s.split(","), default=["fullyRE", "nukeAllowed"],
                    help=f"presets {sorted(MIX_PRESETS)} or raw flag fragments")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--extra", default=DEFAULT_EXTRA,
                    help="shared flag stack inserted after the base prefix")
    ap.add_argument("--areas", type=lambda s: s.split(","), default=None,
                    help="restrict to these countries (sets CLEVER_AREAS)")
    ap.add_argument("--max-runs", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--collect-only", action="store_true")
    ap.add_argument("--plots", action="store_true")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    grid = build_grid(args)
    if args.max_runs:
        grid = grid[: args.max_runs]

    # Validate every scenario string BEFORE any run — fail fast on vocabulary errors.
    for combo in grid:
        validate_scenario(combo["scenario"])
    print(f"grid: {len(grid)} combos, all scenario strings registry-valid")

    manifest_path = args.out / "sweep_manifest.json"
    records: list[dict] = []

    if args.collect_only:
        records = [{**c, "status": "collect_only"} for c in grid]
    else:
        for i, combo in enumerate(grid, 1):
            ds = dataset_dir_for(args.datasets_root, combo["gcm"], combo["ssp"], combo["year"])
            problem = check_dataset(ds, combo["year"], args.areas)
            if args.dry_run:
                rec = {**combo, "status": "dry_run", "dataset": str(ds),
                       "dataset_ok": problem is None, "dataset_problem": problem}
                print(f"[{i}/{len(grid)}] {combo['id']}\n    scenario = {combo['scenario']}\n"
                      f"    dataset  = {ds}  ({'OK' if problem is None else problem})")
            else:
                print(f"[{i}/{len(grid)}] running {combo['id']} …", flush=True)
                rec = run_combo(combo, args)
                print(f"    -> {rec['status']}"
                      + (f" ({rec.get('detail')})" if rec.get("detail") else "")
                      + (f" in {rec.get('wall_s', '?')}s" if "wall_s" in rec else ""))
            records.append(rec)
            manifest_path.write_text(json.dumps(records, indent=2))

    manifest_path.write_text(json.dumps(records, indent=2))
    print(f"manifest: {manifest_path}")

    if not args.dry_run:
        tidy = collect(grid, args)
        if len(tidy):
            out_csv = args.out / "adequacy_sweep.csv"
            tidy.to_csv(out_csv, index=False)
            print(f"tidy adequacy table: {out_csv}  ({len(tidy)} rows)")
            if args.plots:
                make_plots(tidy, args)
        else:
            print("no adequacy dashboards found to collect (yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
