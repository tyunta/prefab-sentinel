"""Runtime validation service package (issue #90 split).

Public surface is the single ``RuntimeValidationService`` class — the
helper modules (``classification``, ``protocol``, ``config``,
``batchmode``, ``editor_bridge_invoke``) are implementation detail and
are not re-exported.
"""

from prefab_sentinel.services.runtime_validation.service import (
    RuntimeValidationService,
)

__all__ = ["RuntimeValidationService"]
