"""Maritime bunkering weight vector for EU-27 countries.

The bunkering weights distribute EU-level marine fuel demand to individual
countries based on their port activity.  Values are approximations derived
from IEA (2025) port data and Eurostat ``mar_sg_am_cyv``.

Landlocked countries receive weight 0.

Note on calibration:
    These weights are a first-order approximation.  They should be calibrated
    against Eurostat ``mar_sg_am_cyv`` (goods handled in main ports by type
    of cargo) once the full time-series is available.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Bunkering weights — share of EU total marine fuel bunkering per country.
# Sources: IEA (2025) port data estimates + Eurostat mar_sg_am_cyv.
#
# FIX (2026-03-25): rebalanced so that non-zero weights sum to exactly 1.0.
# Previously IE was added (0.005) without adjusting NL, causing total = 1.020.
# NL reduced from 0.280 to 0.260 to compensate.
#
# FIX (2026-05-12): GB added (Southampton, Liverpool, Hull, Tilbury, Falmouth).
# UK 2019 bunker fuel sales ~3 Mt, roughly 5% of European total. NL reduced
# 0.260 → 0.220 and ES reduced 0.150 → 0.140 to free 0.050 for GB. Sum still 1.0.
_EU27_BUNKERING_WEIGHTS: dict[str, float] = {
    # Major bunkering nations
    "NL": 0.220,   # Rotterdam, Amsterdam — reduced from 0.260 (2026-05-12 rebalance for GB)
    "ES": 0.140,   # Algeciras, Barcelona, Cartagena — reduced from 0.150 (2026-05-12 rebalance for GB)
    "BE": 0.120,   # Antwerp-Bruges
    "IT": 0.100,   # Augusta, Genova, Trieste
    "GR": 0.080,   # Piraeus, Thessaloniki
    "FR": 0.060,   # Marseille-Fos, Le Havre
    "GB": 0.050,   # Southampton, Liverpool, Hull, Tilbury — added 2026-05-12
    "MT": 0.050,   # Marsaxlokk
    "DE": 0.040,   # Hamburg, Bremerhaven
    "CY": 0.030,   # Limassol
    "PT": 0.020,   # Sines, Leixoes
    "FI": 0.020,   # Helsinki, Turku
    "SE": 0.020,   # Gothenburg
    "HR": 0.010,   # Rijeka
    "IE": 0.005,   # Dublin, Cork
    "DK": 0.005,   # Copenhagen, Aarhus
    "SI": 0.005,   # Koper
    "EE": 0.005,   # Tallinn
    "LV": 0.005,   # Riga
    "LT": 0.005,   # Klaipeda
    "BG": 0.005,   # Varna, Burgas
    "RO": 0.005,   # Constanta
    # Landlocked or negligible bunkering — zero weight
    "AT": 0.0,
    "CZ": 0.0,
    "HU": 0.0,
    "LU": 0.0,
    "PL": 0.0,     # PL has Baltic ports (Gdansk, Gdynia) but negligible bunkering
    "SK": 0.0,
}

# Compile-time assertion: weights must sum to 1.0
_WEIGHT_SUM = sum(_EU27_BUNKERING_WEIGHTS.values())
assert abs(_WEIGHT_SUM - 1.0) < 1e-9, (
    f"_EU27_BUNKERING_WEIGHTS sum to {_WEIGHT_SUM}, expected 1.0"
)


def fetch_bunkering_weights(
    countries: list[str] | None = None,
    validate: bool = True,
    tolerance: float = 1e-6,
) -> dict[str, float]:
    """Return EU-27 bunkering weight vector.

    Args:
        countries: If provided, filter to these country codes and
            renormalize so that weights sum to 1.0 over the subset.
        validate: If True, assert that weights are non-negative and sum
            to 1.0 (within *tolerance*).
        tolerance: Absolute tolerance for sum-to-one check.

    Returns:
        Dictionary mapping ISO-2 country code to bunkering share.

    Raises:
        ValueError: If any weight is negative or sum deviates from 1.0.
    """
    weights = dict(_EU27_BUNKERING_WEIGHTS)

    if countries is not None:
        weights = {cc: weights.get(cc, 0.0) for cc in countries}
        total = sum(weights.values())
        if total > 0:
            weights = {cc: w / total for cc, w in weights.items()}
        else:
            logger.warning(
                "All requested countries have zero bunkering weight. "
                "Cannot renormalize."
            )

    if validate:
        negatives = {k: v for k, v in weights.items() if v < 0}
        if negatives:
            raise ValueError(f"Negative bunkering weights: {negatives}")

        total = sum(weights.values())
        if abs(total - 1.0) > tolerance and total > 0:
            raise ValueError(
                f"Bunkering weights sum to {total:.8f}, expected 1.0 "
                f"(tolerance={tolerance})"
            )

    return weights
