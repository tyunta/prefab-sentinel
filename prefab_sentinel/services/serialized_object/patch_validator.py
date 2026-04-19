"""Per-op schema validator for JSON-target ``dry_run_patch`` plans.

``validate_op`` checks a single open-mode operation and returns the
matching diff-preview row, or ``None`` when the op is rejected.  It
resolves the ``before`` value through ``resolve_before_value`` so the
preview reflects the current state of the Prefab Variant chain.
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
    if not component:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}] ({op_label}).component",
                detail="schema_error",
                evidence="component is required",
            )
        )
        return None
    _bare = component.lstrip("-")
    if _bare.isdigit():
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
        entry = {
            "op": op_name,
            "component": component,
            "path": property_path,
            "before": resolve_before_value(service, target, component, property_path),
            "after": value,
        }
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
        return {
            "op": op_name,
            "component": component,
            "path": property_path,
            "before": resolve_before_value(service, target, component, property_path),
            "after": {"insert_index": item_index, "value": op.get("value")},
        }

    return {
        "op": op_name,
        "component": component,
        "path": property_path,
        "before": resolve_before_value(service, target, component, property_path),
        "after": {"remove_index": item_index},
    }


__all__ = ["validate_op"]
