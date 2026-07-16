"""pommes_eur.providers.clever.h2_network — CLEVER H₂ transmission glue.

Relocated **verbatim** from the (generic) supplyforge ``create_pommes_craft_model.py`` so
pommes_eur depends on supplyforge only through the generic data loader
(``supplyforge.utils._get_input_data_file`` + ``RESULTS_DIR``). These are
CLEVER/POMMES-EUR-specific H₂ transmission assumptions — the European Hydrogen Backbone
adjacency and the pipeline/storage cost points — plus two POMMES-version-agnostic
component helpers used by the driver's H₂ wiring. No numerics changed.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


H2_PIPELINE_COSTS = {
    # New dedicated H₂ pipeline (representative for ~500 km avg distance)
    "new_pipeline_invest": 500_000.,       # €/MW
    "repurposed_pipeline_invest": 150_000., # €/MW (from natural gas)
    "hurdle_cost": 5.,                      # €/MWh (compression energy + losses)
    "fixed_cost": 0.,                       # €/MW/yr
    "lifetime": 40,                         # years (must be multiple of lifetime_step)
}

# Salt cavern storage (large-scale) vs pressurised tanks
H2_STORAGE_COSTS = {
    "tank": {
        "invest_energy": 5_400.,   # €/MWh
        "invest_power": 12_600.,   # €/MW
    },
    "salt_cavern": {
        "invest_energy": 125.,     # €/MWh (50–200 range, midpoint)
        "invest_power": 12_600.,   # €/MW (same injection/withdrawal)
    },
}

# European H₂ pipeline adjacency (country pairs that can be connected)
H2_ADJACENCY: set[tuple[str, str]] = {
    ("FR", "DE"), ("FR", "BE"), ("FR", "ES"), ("FR", "IT"), ("FR", "CH"),
    ("DE", "NL"), ("DE", "BE"), ("DE", "AT"), ("DE", "PL"), ("DE", "CZ"),
    ("DE", "DK"), ("DE", "CH"),
    ("NL", "BE"),
    ("IT", "AT"), ("IT", "CH"), ("IT", "SI"), ("IT", "MT"),  # MT: Malta H2 import (island; else ~100% H2 ENS)
    ("AT", "CZ"), ("AT", "HU"), ("AT", "SI"),
    ("PL", "CZ"), ("PL", "SK"),
    ("CZ", "SK"),
    ("HU", "SK"), ("HU", "RO"),
    ("ES", "PT"),
    ("DK", "SE"), ("DK", "NO"),
    ("SE", "NO"), ("SE", "FI"),
    ("NO", "FI"),
    ("BG", "RO"), ("BG", "GR"),
    ("RO", "HU"),
    ("BE", "LU"),
    # ── 2050 horizon additions: UK & Norway subsea pipelines ─────────────
    # European Hydrogen Backbone priority corridors 6 (UK-NW Europe) + 7 (Norway).
    ("GB", "FR"), ("GB", "NL"),
    ("NO", "DE"), ("NO", "NL"),
}


def _find_component(area, name: str):
    """Look up a component by name on an ``Area`` in a POMMES-version-agnostic way.

    POMMES' ``Area.components`` has been both a list and a dict across versions.
    We normalise that here so the rest of the code doesn't care.
    """
    components = getattr(area, "components", getattr(area, "_components", None))
    if components is None:
        return None
    if isinstance(components, dict):
        return components.get(name)
    # Assume iterable of components with a ``.name`` attribute
    for c in components:
        if getattr(c, "name", None) == name:
            return c
    return None


def _component_factor_dict(component) -> dict:
    """Return ``component.factor`` as a plain ``{resource: coef}`` dict.

    Handles the three representations seen in the wild:
      * plain ``dict`` (older pommes_craft);
      * polars ``DataFrame`` with two columns (resource, value) — newer pommes_craft;
      * pandas ``Series``/``DataFrame``;
      * ``None`` / missing attribute → ``{}``.

    Avoids the ``or {}`` truthiness shortcut, which raises
    ``TypeError: the truth value of a DataFrame is ambiguous`` on polars.
    """
    raw = getattr(component, "factor", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, "iter_rows"):       # polars DataFrame
        try:
            return {row[0]: row[1] for row in raw.iter_rows()}
        except Exception:
            return {}
    if hasattr(raw, "to_dict"):         # pandas
        try:
            d = raw.to_dict()
            if d and all(isinstance(v, dict) for v in d.values()):
                return {k: next(iter(v.values())) for k, v in d.items()}
            return dict(d)
        except Exception:
            return {}
    try:
        return dict(raw)
    except Exception:
        return {}


def add_h2_interconnections(
    energy_model,
    countries: list[str],
    pipeline_type: str = "new",
    pipeline_costs: dict | None = None,
):
    """Add bidirectional H₂ pipeline links between adjacent countries.

    Only connects country pairs that appear in ``H2_ADJACENCY``.
    Pipeline capacity is investable — the optimiser decides the optimal
    sizing.

    Args:
        energy_model: The POMMES EnergyModel.
        countries: List of country codes present in the model.
        pipeline_type: ``"new"`` for dedicated H₂ pipeline or
            ``"repurposed"`` for converted natural gas pipeline.
        pipeline_costs: Override for ``H2_PIPELINE_COSTS``.
    """
    from itertools import combinations

    from pommes_craft import TransportTechnology, Link

    costs = pipeline_costs or H2_PIPELINE_COSTS
    if pipeline_type == "repurposed":
        invest = costs["repurposed_pipeline_invest"]
    else:
        invest = costs["new_pipeline_invest"]

    hurdle = costs["hurdle_cost"]
    fixed = costs["fixed_cost"]
    lifetime = float(costs["lifetime"])

    # Resolve available areas (only connect countries present in the model)
    model_areas = energy_model.areas
    if isinstance(model_areas, list):
        model_areas = {getattr(a, "name", str(a)): a for a in model_areas}
    available = set(model_areas.keys())

    links_added = 0
    for c1, c2 in combinations(countries, 2):
        if c1 not in available or c2 not in available:
            continue
        if (c1, c2) not in H2_ADJACENCY and (c2, c1) not in H2_ADJACENCY:
            continue

        with energy_model.context():
            # Forward direction: c1 → c2
            transport_fwd = TransportTechnology(
                name="h2_pipeline",
                resource="hydrogen",
                life_span=lifetime,
                invest_cost=invest,
                fixed_cost=fixed,
                hurdle_costs=hurdle,
                finance_rate=0.,
            )
            link_fwd = Link(
                name=f"h2_link_{c1}_{c2}",
                area_from=model_areas[c1],
                area_to=model_areas[c2],
            )
            link_fwd.add_transport_technology(transport_fwd)

            # Reverse direction: c2 → c1
            transport_rev = TransportTechnology(
                name="h2_pipeline",
                resource="hydrogen",
                life_span=lifetime,
                invest_cost=invest,
                fixed_cost=fixed,
                hurdle_costs=hurdle,
                finance_rate=0.,
            )
            link_rev = Link(
                name=f"h2_link_{c2}_{c1}",
                area_from=model_areas[c2],
                area_to=model_areas[c1],
            )
            link_rev.add_transport_technology(transport_rev)

        links_added += 1
        logger.info(f"Added H₂ pipeline link: {c1} ↔ {c2}")

    logger.info(f"Total H₂ interconnections added: {links_added}")
