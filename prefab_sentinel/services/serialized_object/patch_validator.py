"""Schema validators for JSON-target plans and Unity bridge responses.

``validate_op`` checks a single open-mode operation and returns the
matching diff-preview row, or ``None`` when the op is rejected.  It
resolves the ``before`` value through ``resolve_before_value`` so the
preview reflects the current state of the Prefab Variant chain.
Created-result and diagnostic helpers validate the bridge response shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.before_cache import resolve_before_value
from prefab_sentinel.services.serialized_object.handles import (
    ARRAY_DATA_SUFFIX,
    PREFAB_CREATE_OPS,
    VALUE_OPS,
)

if TYPE_CHECKING:
    from prefab_sentinel.services.serialized_object.service import (
        SerializedObjectService,
    )


_CREATED_RESULT_STRING_FIELDS = (
    "handle",
    "symbol_path",
    "game_object_file_id",
    "transform_file_id",
    "source_asset_path",
    "source_asset_guid",
)
_CREATED_RESULT_FIELDS = frozenset((*_CREATED_RESULT_STRING_FIELDS, "overrides"))
_PROPERTY_OVERRIDE_FIELDS = frozenset({"component", "property_path"})
_BRIDGE_DIAGNOSTIC_FIELDS = frozenset({"path", "location", "detail", "evidence"})


def _property_override_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _PROPERTY_OVERRIDE_FIELDS:
        return False
    return all(type(value[field]) is str for field in _PROPERTY_OVERRIDE_FIELDS)


def _created_result_is_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _CREATED_RESULT_FIELDS:
        return False
    if any(
        type(value[field]) is not str or not value[field].strip()
        for field in _CREATED_RESULT_STRING_FIELDS
    ):
        return False
    overrides = value["overrides"]
    return isinstance(overrides, list) and all(
        _property_override_is_valid(item) for item in overrides
    )


def created_results_are_valid(
    value: object,
    *,
    expected_handles: set[str] | None = None,
) -> bool:
    if not isinstance(value, list) or not all(
        _created_result_is_valid(item) for item in value
    ):
        return False
    handles = [item["handle"] for item in value]
    return len(handles) == len(set(handles)) and (
        expected_handles is None or set(handles) == expected_handles
    )


def bridge_diagnostics_are_valid(diagnostics: object) -> bool:
    return isinstance(diagnostics, list) and all(
        isinstance(item, dict)
        and set(item) == _BRIDGE_DIAGNOSTIC_FIELDS
        and all(type(item[field]) is str for field in _BRIDGE_DIAGNOSTIC_FIELDS)
        for item in diagnostics
    )


def parse_bridge_diagnostics(payload: list[dict[str, str]]) -> list[Diagnostic]:
    return [
        Diagnostic(item["path"], item["location"], item["detail"], item["evidence"])
        for item in payload
    ]


def validate_op(
    service: SerializedObjectService,
    target: str,
    index: int,
    op: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> dict[str, Any] | None:
    """Validate one open-mode op and return a preview row or ``None``.

    Diagnostics are appended to ``diagnostics``; the caller decides how
    to bubble them up.
    """
    op_name = str(op.get("op", "")).strip()
    op_label = op_name or "?"
    component = str(op.get("component", "")).strip()
    file_id = str(op.get("file_id", "")).strip()
    property_path = str(op.get("path", "")).strip()

    if op_name not in VALUE_OPS:
        if op_name in PREFAB_CREATE_OPS:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}] ({op_label}).op",
                    detail="schema_error",
                    evidence=(
                        f"'{op_name}' is a create-mode operation and cannot be "
                        f"used in open-mode patch plans. "
                        f"To add components to existing prefabs, edit the YAML "
                        f"directly or use Unity's Add Component menu."
                    ),
                )
            )
        else:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}] ({op_label}).op",
                    detail="schema_error",
                    evidence=f"unsupported op '{op_name}'",
                )
            )
        return None
    # Writable profile probes must retain their exact local fileID for every
    # value operation. Component selectors remain supported for existing callers;
    # when both identifiers are present, file_id wins.
    if file_id:
        target_id = file_id
    elif component:
        target_id = component
    else:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).component",
                detail="schema_error",
                evidence="component or file_id is required",
            )
        )
        return None
    if not file_id and component and component.lstrip("-").isdigit():
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).component",
                detail="likely_fileid",
                evidence=(
                    f"component '{component}' looks like a numeric fileID. "
                    f"The Unity bridge resolves components by type name "
                    f"(e.g. 'SkinnedMeshRenderer' or "
                    f"'TypeName@/hierarchy/path'). Numeric fileIDs will "
                    f"fail at apply time."
                ),
            )
        )
    if not property_path:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).path",
                detail="schema_error",
                evidence="path is required",
            )
        )
        return None

    if op_name == "set":
        if "value" not in op:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}] ({op_label}).value",
                    detail="schema_error",
                    evidence="value is required for set",
                )
            )
            return None
        value = op.get("value")
        # Issue #37: a preview row names the target identifier the op
        # actually carried; an unused selector/fileID key is omitted
        # rather than emitted as an empty string.
        entry: dict[str, Any] = {"op": op_name}
        if component:
            entry["component"] = component
        if file_id:
            entry["file_id"] = file_id
        entry["path"] = property_path
        entry["before"] = resolve_before_value(
            service, target, target_id, property_path
        )
        entry["after"] = value
        if isinstance(value, str) and (
            value.startswith("$")
            or value.startswith("c_")
            or value.startswith("go_")
        ):
            entry["_warning"] = (
                f"Value '{value}' looks like a create-mode handle. "
                f"Handle strings are only resolved in 'target'/'parent' fields. "
                f"For ObjectReference, use {{\"guid\": \"...\", \"fileID\": ...}} "
                f"or null."
            )
        return entry

    if op_name in ("insert_array_element", "remove_array_element") and not property_path.endswith(ARRAY_DATA_SUFFIX):
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).path",
                detail="schema_error",
                evidence=(
                    f"Array operations require path ending with '.Array.data', "
                    f"got '{property_path}'. "
                    f"Example: 'globalSwitches.Array.data' instead of "
                    f"'globalSwitches'."
                ),
            )
        )
        return None

    if "index" not in op:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).index",
                detail="schema_error",
                evidence=f"index is required for {op_name}",
            )
        )
        return None
    try:
        item_index = int(op["index"])
    except (TypeError, ValueError):
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).index",
                detail="schema_error",
                evidence="index must be an integer",
            )
        )
        return None
    if item_index < 0:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).index",
                detail="schema_error",
                evidence="index must be >= 0",
            )
        )
        return None

    if op_name == "insert_array_element":
        if "value" not in op:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}] ({op_label}).value",
                    detail="schema_error",
                    evidence="value is required for insert_array_element",
                )
            )
            return None
        entry = {
            "op": op_name,
            "path": property_path,
            "before": resolve_before_value(service, target, target_id, property_path),
            "after": {"insert_index": item_index, "value": op.get("value")},
        }
    else:
        entry = {
            "op": op_name,
            "path": property_path,
            "before": resolve_before_value(service, target, target_id, property_path),
            "after": {"remove_index": item_index},
        }
    if component:
        entry["component"] = component
    if file_id:
        entry["file_id"] = file_id
    return entry


__all__ = [
    "bridge_diagnostics_are_valid", "created_results_are_valid",
    "parse_bridge_diagnostics", "validate_op",
]
