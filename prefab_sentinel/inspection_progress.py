"""Reusable inspection progress metadata."""

from __future__ import annotations

from typing import Any

__all__ = ["InspectionProgress"]


class InspectionProgress:
    """Collect ordered progress records for inspection responses."""

    def __init__(self) -> None:
        self._stages: list[dict[str, Any]] = []
        self._partial_counts: dict[str, int] = {}

    def record_stage(
        self,
        name: str,
        *,
        completed: bool = False,
        count: int | None = None,
    ) -> None:
        stage: dict[str, Any] = {
            "name": name,
            "completed": completed,
        }
        if isinstance(count, int) and count >= 0:
            stage["count"] = count
            self._partial_counts[name] = count
        self._stages.append(stage)

    def to_data(
        self,
        current_or_slowest_step: str = "",
        suggested_next_action: str = "",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "progress_summary": [dict(stage) for stage in self._stages],
            "partial_counts": dict(self._partial_counts),
        }
        if current_or_slowest_step:
            data["current_or_slowest_step"] = current_or_slowest_step
        if suggested_next_action:
            data["suggested_next_action"] = suggested_next_action
        return data
