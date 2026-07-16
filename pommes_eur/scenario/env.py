"""pommes_eur.scenario.env — environment resolution with CLEVER back-compat.

Centralises reading the scenario string and the writable-data dir from the
environment. The new ``POMMES_EUR_*`` variables take precedence; the historical
``CLEVER_*`` names remain honored so existing cluster launchers and notebooks keep
working unchanged.
"""
from __future__ import annotations

import os


def current_scenario() -> str:
    """Return the active scenario string.

    Precedence: ``POMMES_EUR_SCENARIO`` then the legacy ``CLEVER_SCENARIO`` (default "").
    """
    return os.environ.get("POMMES_EUR_SCENARIO") or os.environ.get("CLEVER_SCENARIO", "")
