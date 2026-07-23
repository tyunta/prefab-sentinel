"""Narrow actionability classification for wiring diagnostics."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from prefab_sentinel.contracts import Diagnostic

ACTIONABILITY_CLASSES = (
    "actionable",
    "expected",
    "optional",
)

_DUPLICATE_TARGET_RE = re.compile(r"^fileID:(?P<file_id>[^.]+)$")


def empty_actionability_counts() -> dict[str, int]:
    return dict.fromkeys(ACTIONABILITY_CLASSES, 0)


def classify_null_field(
    script_name: str,
    field_name: str,
    cause_kind: str,
) -> str:
    if (
        script_name == "ScrollRect"
        and field_name == "m_HorizontalScrollbar"
        and cause_kind == "unwired"
    ):
        return "optional"
    return "actionable"


def classify_duplicate_reference(
    diagnostic: Diagnostic,
    components: Sequence[Mapping[str, object]],
) -> str:
    target_file_id = _duplicate_target_file_id(diagnostic)
    if not target_file_id:
        return "actionable"

    has_button_target_graphic = False
    has_script_background = False
    for component in components:
        script_name = str(component.get("script_name", ""))
        fields = component.get("fields", [])
        if not isinstance(fields, list):
            continue
        for raw_field in fields:
            if not isinstance(raw_field, Mapping):
                continue
            if str(raw_field.get("file_id", "")) != target_file_id:
                continue
            field_name = str(raw_field.get("name", ""))
            if script_name == "Button" and field_name == "m_TargetGraphic":
                has_button_target_graphic = True
            elif field_name == "background":
                has_script_background = True

    if has_button_target_graphic and has_script_background:
        return "expected"
    return "actionable"


def _duplicate_target_file_id(diagnostic: Diagnostic) -> str:
    match = _DUPLICATE_TARGET_RE.match(diagnostic.location)
    if match is None:
        return ""
    return match.group("file_id")
