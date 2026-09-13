"""Runtime validation service package (issue #90 split).

Public surface is the single ``RuntimeValidationService`` class — the
helper modules (``classification``, ``protocol``, ``config``,
``editor_bridge_invoke``) are implementation detail and are not
re-exported.  All Unity-bound dispatch flows through the resident
Editor Bridge file watcher.
"""

from prefab_sentinel.services.runtime_validation.reporting import (
    RuntimeReportReservation,
    discard_runtime_report,
    publish_runtime_report,
    reserve_runtime_report,
    runtime_report_skeleton,
)
from prefab_sentinel.services.runtime_validation.service import (
    RuntimeValidationService,
)

__all__ = [
    "RuntimeReportReservation",
    "RuntimeValidationService",
    "discard_runtime_report",
    "publish_runtime_report",
    "reserve_runtime_report",
    "runtime_report_skeleton",
]
