"""Fossil jet reconstruction from CONCAWE refinery data for eSAF blend.

This module ports the fossil jet reconstruction logic from the eSAF notebook.
It handles reconstruction of annual fossil jet fuel availability from CONCAWE
refinery unit-feed data using either the Kero Hydrotreater unit or a fallback
weighted sum of VGO and Residue Hydrocracker units.

Methodological note -- fossil-jet baseline scope
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The default method (``prefer_kero_hydrotreater``) uses the Kero Hydrotreater
unit feed as a proxy for **domestic EU-27 refinery production** of fossil jet
fuel.  In the 2024 CONCAWE reference this unit represents ~3.1 % of total
EU-27 refinery throughput, yielding a baseline of ~17-18 Mt/yr.

This figure is broadly consistent with -- and slightly above -- EU-27 refinery
jet/kerosene *production* (~10-12 Mt/yr, Eurostat ``nrg_bal_c``, ~9.9 Mtoe
in 2023).  The Kero HT throughput exceeds net sellable product because it
includes hydrotreating of intermediate streams, not only final jet output.

The commonly cited 50-60 Mt/yr figure for European jet fuel refers to
**consumption** (fuel uplifted at EU airports), not domestic production.
The EU is a large net importer of jet fuel (~75-80 % of demand sourced
externally, primarily from the Middle East).

The model's eSAF gap is therefore computed against the *refinery-production*
ceiling, not total consumption.  This is the physically correct framing for a
supply-side hydrogen model: as EU refinery capacity declines, imported fossil
jet could still fill part of the demand, reducing the eSAF (and H2) burden.
The resulting ~500 TWh (central, 2050) should be read as the H2 needed to
replace **domestic refinery jet output** with synthetic fuel, under the
assumption that imports decline in parallel with domestic capacity.

Users should be aware that:

* if imports persist, actual eSAF H2 demand could be lower;
* if policy mandates e-SAF on *all* consumption (including imports),
  H2 demand would scale to the full 50+ Mt baseline (~1 000+ TWh).

Authors:
    Simon Brigode -- PERSEE Lab, Mines Paris PSL
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# --- Default jet reconstruction constants ------------------------------------
# These map CONCAWE refinery unit roles to their unit names and jet yield
# fractions.  Used by ``compute_jet_fossil()`` when the caller does not
# supply explicit values.

DEFAULT_JET_UNIT_NAMES: dict[str, str] = {
    "kero_hydrotreater": "Kero Hydrotreater",
    "vgo_hydrocracker": "VGO Hydrocracker",
    "residue_hydrocracker": "Residue Hydrocracker",
}

DEFAULT_JET_YIELDS: dict[str, float] = {
    "vgo_hydrocracker": 0.20,
    "residue_hydrocracker": 0.10,
}


def compute_jet_fossil(
    df_refinery: pd.DataFrame,
    jet_unit_names: dict[str, str],
    jet_yields: dict[str, float],
    method: str = "prefer_kero_hydrotreater",
) -> pd.DataFrame:
    """Reconstruct annual fossil jet fuel availability from CONCAWE refinery data.

    Implements two methods for fossil jet reconstruction:
        1. **prefer_kero_hydrotreater** (default): Uses Kero Hydrotreater unit feed
           directly as fossil jet availability.
        2. **fallback_hydrocracker**: Weighted sum of VGO Hydrocracker and
           Residue Hydrocracker feeds, each multiplied by its jet yield coefficient.

    Args:
        df_refinery: Normalized refinery DataFrame with columns:
            country, year, unit, unit_feed_t_per_yr.
        jet_unit_names: Dict mapping role to CONCAWE unit name, e.g.
            {
                "kero_hydrotreater": "Kero Hydrotreater",
                "vgo_hydrocracker": "VGO Hydrocracker",
                "residue_hydrocracker": "Residue Hydrocracker",
            }
        jet_yields: Dict mapping hydrocracker key to jet yield fraction, e.g.
            {
                "vgo_hydrocracker": 0.20,
                "residue_hydrocracker": 0.10,
            }
        method: "prefer_kero_hydrotreater" or "fallback_hydrocracker".

    Returns:
        DataFrame[country, year, fossil_jet_consumed_t_per_yr]

    Raises:
        TypeError: If df_refinery is not a pandas DataFrame.
        ValueError: If required columns are missing from df_refinery; if method
            is not one of the allowed values; if jet_unit_names is empty; if
            result is empty; if result contains NaN values; or if negative
            jet demand is detected.
    """
    if not isinstance(df_refinery, pd.DataFrame):
        raise TypeError("df_refinery must be a pandas DataFrame")

    required_cols = {"country", "year", "unit", "unit_feed_t_per_yr"}
    if not required_cols.issubset(df_refinery.columns):
        raise ValueError(
            f"df_refinery missing columns: {required_cols - set(df_refinery.columns)}"
        )

    if method not in ("prefer_kero_hydrotreater", "fallback_hydrocracker"):
        raise ValueError(
            f"method must be 'prefer_kero_hydrotreater' or 'fallback_hydrocracker', "
            f"got '{method}'"
        )

    if not jet_unit_names:
        raise ValueError("jet_unit_names dict cannot be empty")

    # Ensure data is properly typed
    df = df_refinery.copy()
    df["year"] = df["year"].astype(int)
    df["unit_feed_t_per_yr"] = df["unit_feed_t_per_yr"].astype(float)

    rows = []

    if method == "prefer_kero_hydrotreater":
        # Use Kero Hydrotreater unit feed directly
        kero_unit = jet_unit_names.get("kero_hydrotreater")
        if not kero_unit:
            raise ValueError(
                "jet_unit_names must contain 'kero_hydrotreater' for "
                "prefer_kero_hydrotreater method"
            )

        for (country, year), grp in df.groupby(["country", "year"]):
            # Filter for kero hydrotreater unit
            kero_feed = grp[grp["unit"] == kero_unit]["unit_feed_t_per_yr"]

            if len(kero_feed) == 0:
                logger.warning(
                    f"No {kero_unit} found for {country}/{year}, using 0"
                )
                fossil_jet = 0.0
            elif len(kero_feed) > 1:
                logger.warning(
                    f"Multiple {kero_unit} entries for {country}/{year}, summing them"
                )
                fossil_jet = kero_feed.sum()
            else:
                fossil_jet = kero_feed.iloc[0]

            rows.append({
                "country": country,
                "year": year,
                "fossil_jet_consumed_t_per_yr": max(fossil_jet, 0.0),
            })

    elif method == "fallback_hydrocracker":
        # Use weighted sum of VGO and Residue Hydrocrackers
        vgo_unit = jet_unit_names.get("vgo_hydrocracker")
        residue_unit = jet_unit_names.get("residue_hydrocracker")

        if not vgo_unit or not residue_unit:
            raise ValueError(
                "jet_unit_names must contain 'vgo_hydrocracker' and "
                "'residue_hydrocracker' for fallback_hydrocracker method"
            )

        vgo_yield = jet_yields.get("vgo_hydrocracker", 0.0)
        residue_yield = jet_yields.get("residue_hydrocracker", 0.0)

        for (country, year), grp in df.groupby(["country", "year"]):
            vgo_feed = grp[grp["unit"] == vgo_unit]["unit_feed_t_per_yr"]
            residue_feed = grp[grp["unit"] == residue_unit]["unit_feed_t_per_yr"]

            vgo_val = vgo_feed.sum() if len(vgo_feed) > 0 else 0.0
            residue_val = residue_feed.sum() if len(residue_feed) > 0 else 0.0

            fossil_jet = vgo_val * vgo_yield + residue_val * residue_yield

            rows.append({
                "country": country,
                "year": year,
                "fossil_jet_consumed_t_per_yr": max(fossil_jet, 0.0),
            })

    result = pd.DataFrame(rows)

    # Data quality checks
    if len(result) == 0:
        raise ValueError("Result DataFrame is empty")
    if result.isna().any().any():
        raise ValueError("Result contains NaN values")
    if not (result["fossil_jet_consumed_t_per_yr"] >= 0).all():
        raise ValueError("Negative jet demand detected")

    logger.info(
        f"Computed fossil jet for {result['country'].nunique()} countries, "
        f"{result['year'].nunique()} years using method={method}"
    )

    return result.sort_values(["country", "year"]).reset_index(drop=True)
