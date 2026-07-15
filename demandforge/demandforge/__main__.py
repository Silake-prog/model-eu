"""DemandForge command-line interface.

Usage::

    python -m demandforge fetch --sector ammonia --countries FR,DE
    python -m demandforge project --sector steel --years 2019-2050
    python -m demandforge aggregate --bundle central --countries FR,DE,IT
    python -m demandforge validate --bundle central
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from demandforge.fetch.hydrogen.industry_data import EU27_COUNTRIES


def _parse_countries(s: str) -> list[str]:
    """Parse comma-separated country codes."""
    if s.lower() == "eu27":
        return EU27_COUNTRIES
    return [c.strip().upper() for c in s.split(",")]


def _parse_years(s: str) -> tuple[int, int]:
    """Parse year range 'YYYY-YYYY'."""
    parts = s.split("-")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Expected YYYY-YYYY, got '{s}'")
    return int(parts[0]), int(parts[1])


def cmd_project(args: argparse.Namespace) -> None:
    """Run a single sector projection."""
    from demandforge.load_projection.hydrogen import (
        project_ammonia_h2_demand,
        project_maritime_h2_demand,
        project_olefins_h2_demand,
        project_steel_h2_demand,
    )

    countries = _parse_countries(args.countries)
    year_start, year_end = _parse_years(args.years)

    dispatch = {
        "ammonia": project_ammonia_h2_demand,
        "maritime": project_maritime_h2_demand,
        "olefins": project_olefins_h2_demand,
        "steel": project_steel_h2_demand,
    }

    if args.sector not in dispatch:
        print(f"Error: sector '{args.sector}' not available for standalone projection.")
        print(f"Available: {list(dispatch.keys())}")
        print("Refinery and eSAF require CONCAWE config — use Python API directly.")
        sys.exit(1)

    func = dispatch[args.sector]
    kwargs = {
        "country": countries,
        "reference_year": year_start,
        "target_year": year_end,
    }

    # Steel needs data paths
    if args.sector == "steel":
        if not args.jrc_path or not args.tyndp_path:
            print("Error: steel requires --jrc-path and --tyndp-path")
            sys.exit(1)
        kwargs["jrc_idees_path"] = args.jrc_path
        kwargs["tyndp_path"] = args.tyndp_path

    df = func(**kwargs)

    if args.output:
        out = Path(args.output)
        if out.suffix == ".csv":
            df.to_csv(out, index=False)
        elif out.suffix == ".parquet":
            df.to_parquet(out, index=False)
        else:
            df.to_csv(out, index=False)
        print(f"Wrote {len(df)} rows to {out}")
    else:
        print(df.to_csv(index=False))


def cmd_aggregate(args: argparse.Namespace) -> None:
    """Run aggregate H2 demand across sectors, optionally from a named bundle."""
    countries = _parse_countries(args.countries)
    year_start, year_end = _parse_years(args.years)
    sectors = args.sectors.split(",") if args.sectors else None

    if args.bundle:
        # Use scenario bundle via load_bundle (handles dependencies)
        from demandforge.load_projection.scenarios import load_bundle

        kwargs: dict = {
            "countries": countries,
            "reference_year": year_start,
            "target_year": year_end,
        }
        if sectors:
            kwargs["sectors"] = sectors
        if args.jrc_path:
            kwargs["jrc_idees_path"] = args.jrc_path
        if args.tyndp_path:
            kwargs["tyndp_path"] = args.tyndp_path

        df = load_bundle(args.bundle, **kwargs)
    else:
        # Direct aggregation (no bundle, no dependency piping)
        from demandforge.load_projection.hydrogen import aggregate_h2_demand

        df = aggregate_h2_demand(
            country=countries,
            reference_year=year_start,
            target_year=year_end,
            sectors=sectors,
        )

    if args.output:
        out = Path(args.output)
        df.to_csv(out, index=False)
        print(f"Wrote {len(df)} rows to {out}")
    else:
        print(df.to_csv(index=False))


def cmd_bundles(args: argparse.Namespace) -> None:
    """List available scenario bundles."""
    from demandforge.load_projection.scenarios import list_bundles

    bundles = list_bundles()
    print("=== Available Scenario Bundles ===")
    for name, desc in bundles.items():
        print(f"  {name:20s} {desc}")
    print(f"\nUse: python -m demandforge aggregate --bundle <name>")


def cmd_validate(args: argparse.Namespace) -> None:
    """Run validation checks on projections."""
    from demandforge.load_projection.hydrogen import aggregate_h2_demand
    from demandforge.load_projection.constants import H2_LHV_MWH_PER_T

    countries = _parse_countries(args.countries)
    year_start, year_end = _parse_years(args.years)

    sectors = ["ammonia", "maritime", "olefins"]
    df = aggregate_h2_demand(
        country=countries,
        reference_year=year_start,
        target_year=year_end,
        sectors=sectors,
    )

    # Validation checks
    issues = []
    for sec in df["sector"].unique():
        sec_df = df[df["sector"] == sec]
        if (sec_df["h2_demand_t_per_yr"] < 0).any():
            issues.append(f"{sec}: negative H2 demand detected")
        max_val = sec_df["h2_demand_t_per_yr"].max()
        if max_val > 5e7:
            issues.append(f"{sec}: max H2 demand {max_val:.0f} t/yr exceeds 50 Mt sanity bound")

    eu_total = df.groupby("year")["h2_demand_t_per_yr"].sum()
    eu_twh = eu_total * H2_LHV_MWH_PER_T / 1e6

    print("=== DemandForge Validation Report ===")
    print(f"Countries: {len(countries)}")
    print(f"Years: {year_start}-{year_end}")
    print(f"Sectors: {', '.join(df['sector'].unique())}")
    print(f"EU H2 range: {eu_twh.min():.1f} - {eu_twh.max():.1f} TWh/yr")
    if issues:
        print(f"\nISSUES ({len(issues)}):")
        for iss in issues:
            print(f"  - {iss}")
    else:
        print("\nAll checks passed.")


def main() -> None:
    """Entry point for the DemandForge CLI."""
    parser = argparse.ArgumentParser(
        prog="demandforge",
        description="DemandForge — hydrogen demand projection for POMMES",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # project command
    p_proj = subparsers.add_parser("project", help="Run single sector projection")
    p_proj.add_argument("--sector", required=True, help="Sector name")
    p_proj.add_argument("--countries", default="EU27", help="Comma-separated codes or EU27")
    p_proj.add_argument("--years", default="2019-2050", help="Year range YYYY-YYYY")
    p_proj.add_argument("--output", "-o", help="Output file path (.csv or .parquet)")
    p_proj.add_argument("--jrc-path", help="JRC-IDEES data path (for steel)")
    p_proj.add_argument("--tyndp-path", help="TYNDP file path (for steel)")

    # aggregate command
    p_agg = subparsers.add_parser("aggregate", help="Aggregate H2 demand across sectors")
    p_agg.add_argument("--bundle", help="Named scenario bundle (e.g. central, high_h2)")
    p_agg.add_argument("--countries", default="EU27", help="Comma-separated codes or EU27")
    p_agg.add_argument("--years", default="2019-2050", help="Year range YYYY-YYYY")
    p_agg.add_argument("--sectors", help="Comma-separated sector names (default: all available)")
    p_agg.add_argument("--output", "-o", help="Output file path")
    p_agg.add_argument("--jrc-path", help="JRC-IDEES data path (for steel)")
    p_agg.add_argument("--tyndp-path", help="TYNDP file path (for steel)")

    # bundles command (list available scenario bundles)
    p_bun = subparsers.add_parser("bundles", help="List available scenario bundles")

    # validate command
    p_val = subparsers.add_parser("validate", help="Validate projection outputs")
    p_val.add_argument("--countries", default="EU27", help="Comma-separated codes or EU27")
    p_val.add_argument("--years", default="2019-2050", help="Year range YYYY-YYYY")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")

    if args.command == "project":
        cmd_project(args)
    elif args.command == "aggregate":
        cmd_aggregate(args)
    elif args.command == "bundles":
        cmd_bundles(args)
    elif args.command == "validate":
        cmd_validate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
