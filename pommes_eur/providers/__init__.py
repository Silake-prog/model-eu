"""pommes_eur.providers — data/scenario providers behind a common contract.

A *provider* supplies the inputs for one dataset/case-study and builds a
``pommes_craft.EnergyModel`` from them. CLEVER is one provider (:class:`CleverProvider`);
other datasets (ERAA, TYNDP, custom CSVs) plug in the same way. See :mod:`.base`.
"""
from __future__ import annotations

from pommes_eur.providers.base import ModelProvider, ProviderInputs

__all__ = ["ModelProvider", "ProviderInputs", "CleverProvider", "EraaProvider"]


def __getattr__(name: str):  # lazy: CleverProvider pulls in the heavy model builder
    if name == "CleverProvider":
        from pommes_eur.providers.clever import CleverProvider

        return CleverProvider
    if name == "EraaProvider":
        from pommes_eur.providers.eraa import EraaProvider

        return EraaProvider
    raise AttributeError(name)
