"""Prefab Variant analysis service package.

Public API: :class:`PrefabVariantService` plus the dataclasses
:class:`OverrideEntry` and :class:`ChainValue` used in its return
payloads.
"""

from __future__ import annotations

from prefab_sentinel.services.prefab_variant.chain import ChainValue
from prefab_sentinel.services.prefab_variant.overrides import OverrideEntry
from prefab_sentinel.services.prefab_variant.service import PrefabVariantService

__all__ = ["PrefabVariantService", "OverrideEntry", "ChainValue"]
