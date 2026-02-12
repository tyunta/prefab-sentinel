from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from unitytool.contracts import Diagnostic, Severity, ToolResponse
from unitytool.unity_assets import decode_text_file

_SUPPORTED_OPS = {"set", "insert_array_element", "remove_array_element"}


class SerializedObjectMcp:
    """Serialized-object MCP scaffold with plan validation and dry-run preview."""

    TOOL_NAME = "unity-serialized-object-mcp"

    def _validate_op(
        self,
        target: str,
        index: int,
        op: dict[str, Any],
        diagnostics: list[Diagnostic],
    ) -> dict[str, Any] | None:
        op_name = str(op.get("op", "")).strip()
        component = str(op.get("component", "")).strip()
        property_path = str(op.get("path", "")).strip()

        if op_name not in _SUPPORTED_OPS:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported op '{op_name}'",
                )
            )
            return None
        if not component:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].component",
                    detail="schema_error",
                    evidence="component is required",
                )
            )
            return None
        if not property_path:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].path",
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
                        location=f"ops[{index}].value",
                        detail="schema_error",
                        evidence="value is required for set",
                    )
                )
                return None
            return {
                "op": op_name,
                "component": component,
                "path": property_path,
                "before": "(unknown)",
                "after": op.get("value"),
            }

        if "index" not in op:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].index",
                    detail="schema_error",
                    evidence=f"index is required for {op_name}",
                )
            )
            return None
        try:
            item_index = int(op.get("index"))
        except (TypeError, ValueError):
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].index",
                    detail="schema_error",
                    evidence="index must be an integer",
                )
            )
            return None
        if item_index < 0:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].index",
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
                        location=f"ops[{index}].value",
                        detail="schema_error",
                        evidence="value is required for insert_array_element",
                    )
                )
                return None
            return {
                "op": op_name,
                "component": component,
                "path": property_path,
                "before": "(unknown)",
                "after": {"insert_index": item_index, "value": op.get("value")},
            }

        return {
            "op": op_name,
            "component": component,
            "path": property_path,
            "before": "(unknown)",
            "after": {"remove_index": item_index},
        }

    def _resolve_target_path(self, target: str) -> Path:
        resolved = Path(target)
        if not resolved.is_absolute():
            resolved = Path.cwd() / resolved
        return resolved.resolve()

    def _split_path(self, property_path: str) -> list[str]:
        return [segment for segment in property_path.split(".") if segment]

    def _walk_dict_path(self, payload: object, property_path: str) -> object:
        value = payload
        for segment in self._split_path(property_path):
            if not isinstance(value, dict):
                raise TypeError(f"path segment '{segment}' expects an object")
            if segment not in value:
                raise KeyError(segment)
            value = value[segment]
        return value

    def _get_parent_and_leaf(self, payload: object, property_path: str) -> tuple[dict[str, Any], str]:
        segments = self._split_path(property_path)
        if not segments:
            raise ValueError("path is required")
        if len(segments) == 1:
            if not isinstance(payload, dict):
                raise TypeError("root payload must be an object for scalar set")
            return payload, segments[0]

        parent_path = ".".join(segments[:-1])
        parent = self._walk_dict_path(payload, parent_path)
        if not isinstance(parent, dict):
            raise TypeError("resolved parent is not an object")
        return parent, segments[-1]

    def _get_array_at_path(self, payload: object, property_path: str) -> list[Any]:
        if not property_path.endswith(".Array.data"):
            raise ValueError("array operations require a '.Array.data' path")
        base_path = property_path[: -len(".Array.data")]
        value = self._walk_dict_path(payload, base_path) if base_path else payload
        if not isinstance(value, list):
            raise TypeError("target path does not resolve to an array")
        return value

    def _apply_op(self, payload: object, op: dict[str, Any]) -> dict[str, Any]:
        op_name = str(op.get("op", ""))
        component = str(op.get("component", ""))
        property_path = str(op.get("path", ""))

        if op_name == "set":
            if property_path.endswith(".Array.size"):
                base_path = property_path[: -len(".Array.size")]
                value = self._walk_dict_path(payload, base_path) if base_path else payload
                if not isinstance(value, list):
                    raise TypeError("'.Array.size' target must resolve to an array")
                try:
                    new_size = int(op.get("value"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("array size must be an integer") from exc
                if new_size < 0:
                    raise ValueError("array size must be >= 0")
                before = len(value)
                if new_size < before:
                    del value[new_size:]
                elif new_size > before:
                    value.extend([None] * (new_size - before))
                return {
                    "op": op_name,
                    "component": component,
                    "path": property_path,
                    "before": before,
                    "after": len(value),
                }

            parent, leaf = self._get_parent_and_leaf(payload, property_path)
            if leaf not in parent:
                raise KeyError(leaf)
            before = parent[leaf]
            parent[leaf] = op.get("value")
            return {
                "op": op_name,
                "component": component,
                "path": property_path,
                "before": before,
                "after": parent[leaf],
            }

        if op_name == "insert_array_element":
            array_value = self._get_array_at_path(payload, property_path)
            index = int(op.get("index"))
            if index < 0 or index > len(array_value):
                raise IndexError("insert index is out of bounds")
            before_size = len(array_value)
            array_value.insert(index, op.get("value"))
            return {
                "op": op_name,
                "component": component,
                "path": property_path,
                "before": {"size": before_size},
                "after": {"size": len(array_value), "index": index},
            }

        if op_name == "remove_array_element":
            array_value = self._get_array_at_path(payload, property_path)
            index = int(op.get("index"))
            if index < 0 or index >= len(array_value):
                raise IndexError("remove index is out of bounds")
            before_size = len(array_value)
            removed = array_value.pop(index)
            return {
                "op": op_name,
                "component": component,
                "path": property_path,
                "before": {"size": before_size, "removed": removed},
                "after": {"size": len(array_value), "index": index},
            }

        raise ValueError(f"unsupported op '{op_name}'")

    def dry_run_patch(self, target: str, ops: list[dict[str, Any]]) -> ToolResponse:
        diagnostics: list[Diagnostic] = []
        if not str(target).strip():
            diagnostics.append(
                Diagnostic(
                    path="",
                    location="target",
                    detail="schema_error",
                    evidence="target is required",
                )
            )
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_PLAN_INVALID",
                message="Patch plan schema validation failed.",
                data={"target": target, "op_count": 0, "read_only": True},
                diagnostics=diagnostics,
            )
        if not ops:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="ops must contain at least one operation",
                )
            )
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_PLAN_INVALID",
                message="Patch plan schema validation failed.",
                data={"target": target, "op_count": 0, "read_only": True},
                diagnostics=diagnostics,
            )

        preview: list[dict[str, Any]] = []
        for index, op in enumerate(ops):
            if not isinstance(op, dict):
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}]",
                        detail="schema_error",
                        evidence="operation must be an object",
                    )
                )
                continue
            diff_entry = self._validate_op(target, index, op, diagnostics)
            if diff_entry is not None:
                preview.append(diff_entry)

        if diagnostics:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_PLAN_INVALID",
                message="Patch plan schema validation failed.",
                data={"target": target, "op_count": len(ops), "read_only": True},
                diagnostics=diagnostics,
            )

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="SER_DRY_RUN_OK",
            message="dry_run_patch generated a patch preview.",
            data={
                "target": target,
                "op_count": len(ops),
                "applied": 0,
                "diff": preview,
                "read_only": True,
            },
            diagnostics=[],
        )

    def apply_and_save(self, target: str, ops: list[dict[str, Any]]) -> ToolResponse:
        dry_run = self.dry_run_patch(target=target, ops=ops)
        if not dry_run.success:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_PLAN_INVALID",
                message="Patch plan schema validation failed.",
                data={
                    "target": target,
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=dry_run.diagnostics,
            )

        target_path = self._resolve_target_path(target)
        if target_path.suffix.lower() != ".json":
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_UNSUPPORTED_TARGET",
                message="Phase 1 apply backend only supports .json targets.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )
        if not target_path.exists():
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_TARGET_MISSING",
                message="Patch target file was not found.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )

        try:
            loaded = json.loads(decode_text_file(target_path))
        except OSError as exc:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_IO_ERROR",
                message="Failed to read patch target file.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                    "error": str(exc),
                },
                diagnostics=[],
            )
        except json.JSONDecodeError as exc:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_TARGET_FORMAT",
                message="Patch target file must be valid JSON for Phase 1 apply backend.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                    "error": str(exc),
                },
                diagnostics=[],
            )

        working = deepcopy(loaded)
        diagnostics: list[Diagnostic] = []
        applied_ops: list[dict[str, Any]] = []
        for index, op in enumerate(ops):
            try:
                applied_ops.append(self._apply_op(working, op))
            except (TypeError, ValueError, KeyError, IndexError) as exc:
                diagnostics.append(
                    Diagnostic(
                        path=str(target_path),
                        location=f"ops[{index}]",
                        detail="apply_error",
                        evidence=str(exc),
                    )
                )

        if diagnostics:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_APPLY_FAILED",
                message="Patch apply failed. Target was not modified.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": len(applied_ops),
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=diagnostics,
            )

        try:
            target_path.write_text(
                f"{json.dumps(working, ensure_ascii=False, indent=2)}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_IO_ERROR",
                message="Failed to write patch target file.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": len(applied_ops),
                    "read_only": False,
                    "executed": False,
                    "error": str(exc),
                },
                diagnostics=[],
            )

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="SER_APPLY_OK",
            message="Patch apply completed for JSON target.",
            data={
                "target": str(target_path),
                "op_count": len(ops),
                "applied": len(applied_ops),
                "diff": applied_ops,
                "read_only": False,
                "executed": True,
            },
            diagnostics=[],
        )


def load_patch_plan(path: Path) -> dict[str, Any]:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Patch plan root must be an object.")
    return payload
