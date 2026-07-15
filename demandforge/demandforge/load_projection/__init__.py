"""Load projection package — electricity and hydrogen demand curves.

Submodules:
    electricity: Country-level electricity load projection.
    hydrogen:    Country-level industrial H2 demand projection (6 sectors).
    scenarios:   Scenario bundle management (YAML → projection dispatch).
    constants:   Shared physical constants with unit suffixes.

Usage example::

    from demandforge.load_projection import (
        project_load_curve,
        project_ammonia_h2_demand,
        aggregate_h2_demand,
    )

    # Recommended entry point for scenario-based runs:
    from demandforge.load_projection.scenarios import load_bundle, list_bundles
    df = load_bundle("central", countries=["FR", "DE"])
"""
from demandforge.load_projection.electricity import project_load_curve
from demandforge.load_projection.hydrogen import (
    project_ammonia_h2_demand,
    project_refinery_h2_demand,
    project_esaf_h2_demand,
    project_maritime_h2_demand,
    project_olefins_h2_demand,
    project_steel_h2_demand,
    aggregate_h2_demand,
)
from demandforge.load_projection.scenarios import (
    load_bundle,
    list_bundles,
    get_bundle_params,
)

__all__ = [
    "project_load_curve",
    "project_ammonia_h2_demand",
    "project_refinery_h2_demand",
    "project_esaf_h2_demand",
    "project_maritime_h2_demand",
    "project_olefins_h2_demand",
    "project_steel_h2_demand",
    "aggregate_h2_demand",
    "load_bundle",
    "list_bundles",
    "get_bundle_params",
]
