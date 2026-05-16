"""Serialized object service package (issue #91 Phase 1 carve-out).

Public surface is the single ``SerializedObjectService`` class.  The
prefab-create validation subtree lives in three sibling modules
(``prefab_create_dispatch``, ``prefab_create_structure``,
``prefab_create_values``) and is implementation detail — none of those
helpers are re-exported.
"""

from prefab_sentinel.services.serialized_object.service import (
    SerializedObjectService,
)

__all__ = ["SerializedObjectService"]
