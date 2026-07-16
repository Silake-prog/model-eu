"""
clever.carbon_price — CO₂ price trajectories for the methane (natural_gas)
and oil resource buses.

Why a dedicated module
----------------------
The CO₂ tax is the single most important price signal for the fossil-methane
bus. Before this module, `constants.py::_CO2_PRICE_EUR_PER_TONNE` held a
single flat 2050 number (default 150 €/tCO₂, overridable via `_co2NNN`).
That worked but gave no documented trajectory and no clean way to switch
between peer-reviewed scenario families (EC FF55, TYNDP, IEA WEO, BNEF).

This module:

  1. Stores named CO₂ price *trajectories* (€/tCO₂ vs year) anchored to
     specific published sources with citations.
  2. Exposes `carbon_price(year, trajectory)` with linear interpolation
     between published anchor points.
  3. Resolves the active trajectory + year via `resolved_carbon_price()`
     honoring the scenario-suffix layer:
        `_co2off`            → 0 €/tCO₂
        `_co2NNN`            → flat NNN €/tCO₂ (bypasses trajectory)
        `_co2trajNAME`       → use named trajectory at MODEL_YEAR
        (none of the above) → DEFAULT_TRAJECTORY at MODEL_YEAR
  4. Is consumed by `clever.constants.natural_gas_import_price()` and
     `clever.constants.oil_import_price()` so the CO₂ adder applied to the
     methane bus matches the LP's intended pathway and is trivially
     swappable for sensitivity studies.

Trajectory sources (anchor points are dated and cited inline). All values
are in €/tCO₂ at the indicated year. Linear interpolation between anchors.

Note on alignment with CLEVER 2050 modelling year
-------------------------------------------------
CLEVER's LP is currently single-year (`year_op = 2050`). The trajectory
returns *just* the 2050 value at run time. The full trajectory exists for
documentation, sensitivity analysis, and so the same module supports a
future multi-year-LP variant without re-architecting.
"""

from __future__ import annotations
import logging
import re
import os
from typing import Mapping

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Trajectories — each one is a {year: €/tCO₂} mapping with public source cited.
# ────────────────────────────────────────────────────────────────────────────

#: European Commission "Fit for 55" Impact Assessment 2021 — ETS-driven
#: trajectory, current EU ETS reform baseline. Most conservative of the
#: peer-reviewed long-run estimates.
EC_FF55_TRAJECTORY: dict[int, float] = {
    2025:  80.0,
    2030: 100.0,
    2035: 120.0,
    2040: 130.0,
    2045: 140.0,
    2050: 150.0,
}

#: TYNDP 2024 National Trends scenario (ENTSOG-ENTSO-E joint, June 2024).
#: "Policy-as-implemented" — assumes current EU legislation runs to schedule
#: but no additional ratchet. Median 2050 ETS price assumption.
TYNDP_2024_NT_TRAJECTORY: dict[int, float] = {
    2025:  80.0,
    2030:  95.0,
    2035: 120.0,
    2040: 150.0,
    2045: 175.0,
    2050: 200.0,
}

#: TYNDP 2024 Distributed Energy scenario — decarbonisation pathway via
#: demand-side / decentralised supply. Higher CO₂ price reflects faster
#: tightening of ETS cap consistent with sufficiency narratives.
TYNDP_2024_DE_TRAJECTORY: dict[int, float] = {
    2025:  85.0,
    2030: 110.0,
    2035: 150.0,
    2040: 200.0,
    2045: 250.0,
    2050: 290.0,
}

#: TYNDP 2024 Global Ambition scenario — supply-side / CCS-heavy decarb.
#: Slightly lower long-run CO₂ price than DE because CCS provides a soft
#: ceiling on the marginal abatement cost.
TYNDP_2024_GA_TRAJECTORY: dict[int, float] = {
    2025:  85.0,
    2030: 105.0,
    2035: 140.0,
    2040: 180.0,
    2045: 220.0,
    2050: 260.0,
}

#: IEA World Energy Outlook 2024 — STEPS (Stated Policies) scenario, EU
#: trajectory. The most market-anchored / least normative long-run path.
IEA_WEO_2024_STEPS_TRAJECTORY: dict[int, float] = {
    2025:  85.0,
    2030: 100.0,
    2035: 115.0,
    2040: 130.0,
    2045: 140.0,
    2050: 150.0,
}

#: IEA WEO 2024 APS (Announced Pledges Scenario) — assumes all NDCs +
#: net-zero pledges are achieved on time. Middle ground.
IEA_WEO_2024_APS_TRAJECTORY: dict[int, float] = {
    2025:  85.0,
    2030: 120.0,
    2035: 160.0,
    2040: 195.0,
    2045: 215.0,
    2050: 230.0,
}

#: IEA WEO 2024 NZE (Net Zero by 2050) scenario — 1.5 °C-aligned pathway,
#: most aggressive CO₂ price required to displace fossil incumbency.
IEA_WEO_2024_NZE_TRAJECTORY: dict[int, float] = {
    2025:  90.0,
    2030: 150.0,
    2035: 190.0,
    2040: 220.0,
    2045: 235.0,
    2050: 250.0,
}

#: BloombergNEF New Energy Outlook 2024 — Economic Transition Scenario,
#: market-anchored projection of EU ETS clearing prices.
BNEF_2024_ETS_TRAJECTORY: dict[int, float] = {
    2025:  85.0,
    2030: 127.0,
    2035: 155.0,
    2040: 180.0,
    2045: 210.0,
    2050: 240.0,
}

#: Registry of all named trajectories. Suffix `_co2trajNAME` resolves NAME
#: against this dict (case-insensitive). Keys are camelCase / no underscores
#: so the scenario-suffix regex can use `[a-zA-Z0-9]+` and not get truncated
#: at the first underscore in names like "weo_nze" / "tyndp_de".
TRAJECTORIES: dict[str, dict[int, float]] = {
    "ff55":      EC_FF55_TRAJECTORY,
    "tyndpNT":   TYNDP_2024_NT_TRAJECTORY,
    "tyndpDE":   TYNDP_2024_DE_TRAJECTORY,
    "tyndpGA":   TYNDP_2024_GA_TRAJECTORY,
    "weoSTEPS":  IEA_WEO_2024_STEPS_TRAJECTORY,
    "weoAPS":    IEA_WEO_2024_APS_TRAJECTORY,
    "weoNZE":    IEA_WEO_2024_NZE_TRAJECTORY,
    "bnef":      BNEF_2024_ETS_TRAJECTORY,
}

#: Default trajectory used when neither `_co2NNN` nor `_co2trajNAME` is set.
#: FF55 chosen to preserve historical CLEVER behaviour (150 €/tCO₂ in 2050).
DEFAULT_TRAJECTORY: str = "ff55"

#: CLEVER's central modelling year — must match `_TARGET_MODEL_YEAR` in
#: constants.py. Repeated here so this module is self-contained.
TARGET_MODEL_YEAR: int = 2050


# ────────────────────────────────────────────────────────────────────────────
# Interpolation
# ────────────────────────────────────────────────────────────────────────────

def carbon_price(
    year: int = TARGET_MODEL_YEAR,
    trajectory: str = DEFAULT_TRAJECTORY,
) -> float:
    """Return the CO₂ price (€/tCO₂) for `year` on the chosen `trajectory`.

    Uses linear interpolation between anchor points. Extrapolation is
    clamped: years before the earliest anchor return the earliest value;
    years after the last anchor return the last value. (Both ends of the
    published trajectories are nominally 2025 and 2050.)

    Parameters
    ----------
    year : int
        Target year (default 2050).
    trajectory : str
        Trajectory name from `TRAJECTORIES`. Matched case-insensitively.
        Default = `DEFAULT_TRAJECTORY` (= "ff55", 150 €/tCO₂ at 2050).

    Returns
    -------
    float
        CO₂ price in €/tCO₂.

    Raises
    ------
    ValueError
        If `trajectory` is not in TRAJECTORIES.
    """
    # Case-insensitive lookup against camelCase keys
    lc_lookup = {k.lower(): k for k in TRAJECTORIES}
    key = lc_lookup.get(trajectory.lower())
    if key is None:
        raise ValueError(
            f"Unknown CO₂ trajectory {trajectory!r}. "
            f"Valid: {sorted(TRAJECTORIES.keys())}"
        )
    traj = TRAJECTORIES[key]
    anchor_years = sorted(traj.keys())

    if year <= anchor_years[0]:
        return float(traj[anchor_years[0]])
    if year >= anchor_years[-1]:
        return float(traj[anchor_years[-1]])

    # Linear interpolation between bracketing anchor years
    for i in range(len(anchor_years) - 1):
        y0, y1 = anchor_years[i], anchor_years[i + 1]
        if y0 <= year <= y1:
            p0, p1 = traj[y0], traj[y1]
            frac = (year - y0) / (y1 - y0)
            return float(p0 + frac * (p1 - p0))
    raise RuntimeError(
        f"Carbon price interpolation failed for year={year}, "
        f"trajectory={trajectory}. Anchors: {anchor_years}"
    )


# ────────────────────────────────────────────────────────────────────────────
# Scenario suffix resolution
# ────────────────────────────────────────────────────────────────────────────

def _parse_co2_trajectory_suffix(s: str) -> str | None:
    """Return the trajectory name from `_co2trajNAME` if present, else None.

    Trajectory NAMEs are camelCase / digits only (no underscores) so the
    regex can use a simple `[A-Za-z0-9]+` token and stop cleanly at the next
    `_` (= start of another scenario suffix) or end-of-string.
    """
    m = re.search(r"_co2traj([A-Za-z][A-Za-z0-9]*)(?:_|$)", s)
    return m.group(1).lower() if m else None


def resolved_carbon_price(year: int = TARGET_MODEL_YEAR) -> float:
    """Resolve the active CO₂ price for `year` against the current
    `CLEVER_SCENARIO` env var (re-read at every call so scenario changes
    propagate without module reload).

    Precedence (highest to lowest):
      1. `_co2off`              → 0.0 (set via _CO2_PRICE_EUR_PER_TONNE=0)
      2. `_co2NNN`              → flat NNN €/tCO₂ (bypasses trajectory)
      3. `_co2trajNAME`         → carbon_price(year, NAME)
      4. (no suffix)            → carbon_price(year, DEFAULT_TRAJECTORY)
    """
    # Read existing _CO2_PRICE_EUR_PER_TONNE — that's where `_co2off` and
    # `_co2NNN` are already resolved by constants.py.
    from pommes_eur.constants import _CO2_PRICE_EUR_PER_TONNE

    from pommes_eur.scenario.env import current_scenario
    scen = current_scenario()

    # 1. _co2off → 0 (signaled by _CO2_PRICE_EUR_PER_TONNE = 0)
    if _CO2_PRICE_EUR_PER_TONNE == 0.0:
        logger.debug("resolved_carbon_price: _co2off active → 0 €/tCO₂")
        return 0.0

    # 2. _co2NNN flat override (any value other than the default 150)
    #    is treated as user intent to bypass the trajectory. We detect this
    #    by checking if the suffix _co2NNN appears in the scenario string.
    if re.search(r"_co2\d+(?:_|$)", scen):
        logger.debug(
            "resolved_carbon_price: _co2NNN flat override = %.1f €/tCO₂",
            _CO2_PRICE_EUR_PER_TONNE,
        )
        return _CO2_PRICE_EUR_PER_TONNE

    # 3. _co2trajNAME → named trajectory at year
    traj_name = _parse_co2_trajectory_suffix(scen)
    if traj_name is not None:
        try:
            price = carbon_price(year, trajectory=traj_name)
            logger.info(
                "resolved_carbon_price: _co2traj%s @ year=%d → %.1f €/tCO₂",
                traj_name, year, price,
            )
            return price
        except ValueError as e:
            logger.error(
                "resolved_carbon_price: %s — falling back to default trajectory %s",
                e, DEFAULT_TRAJECTORY,
            )

    # 4. Default trajectory at year
    price = carbon_price(year, trajectory=DEFAULT_TRAJECTORY)
    logger.debug(
        "resolved_carbon_price: default trajectory %s @ year=%d → %.1f €/tCO₂",
        DEFAULT_TRAJECTORY, year, price,
    )
    return price


# ────────────────────────────────────────────────────────────────────────────
# CLI: python -m clever.carbon_price  →  print all trajectories side by side
# ────────────────────────────────────────────────────────────────────────────

def _print_trajectory_table() -> None:
    """Print all named trajectories as a side-by-side comparison table."""
    years = sorted({y for t in TRAJECTORIES.values() for y in t.keys()})
    name_w = max(len(n) for n in TRAJECTORIES) + 2
    print(f"{'trajectory':<{name_w}}" + " ".join(f"{y:>6}" for y in years))
    print("─" * (name_w + 7 * len(years)))
    for name in TRAJECTORIES:
        row = f"{name:<{name_w}}"
        for y in years:
            v = carbon_price(y, name)
            row += f"{v:>6.0f}"
        print(row)


if __name__ == "__main__":
    _print_trajectory_table()
