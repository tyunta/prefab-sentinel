from __future__ import annotations

import hashlib
import hmac
import json
import os
import shlex
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from unitytool.contracts import Diagnostic, Severity, ToolResponse
from unitytool.unity_assets import decode_text_file

_SUPPORTED_OPS = {"set", "insert_array_element", "remove_array_element"}
_UNITY_BRIDGE_PROTOCOL_VERSION = 1
_UNITY_BRIDGE_SUPPORTED_SUFFIXES = {
    ".prefab",
    ".unity",
    ".asset",
    ".mat",
    ".anim",
    ".controller",
}
_UNITY_BRIDGE_ALLOWED_COMMANDS = {
    "python",
    "python3",
    "py",
    "python.exe",
    "py.exe",
    "uv",
    "uvx",
    "uv.exe",
    "uvx.exe",
    "unitytool-unity-bridge",
    "unitytool-unity-bridge.exe",
    "unitytool-unity-serialized-object-bridge",
    "unitytool-unity-serialized-object-bridge.exe",
}


class SerializedObjectMcp:
    """Serialized-object MCP scaffold with plan validation and dry-run preview."""

    TOOL_NAME = "unity-serialized-object-mcp"

    def __init__(
        self,
        bridge_command: tuple[str, ...] | None = None,
        bridge_timeout_sec: float = 120.0,
    ) -> None:
        self.bridge_command_error: str | None = None
        self.bridge_command = (
            bridge_command
            if bridge_command is not None
            else self._load_bridge_command_from_env()
        )
        try:
            timeout = float(bridge_timeout_sec)
        except (TypeError, ValueError):
            timeout = 120.0
        self.bridge_timeout_sec = max(1.0, timeout)

    def _load_bridge_command_from_env(self) -> tuple[str, ...] | None:
        raw = os.getenv("UNITYTOOL_PATCH_BRIDGE", "").strip()
        if not raw:
            return None
        try:
            parts = tuple(shlex.split(raw, posix=False))
        except ValueError as exc:
            self.bridge_command_error = (
                f"Failed to parse UNITYTOOL_PATCH_BRIDGE: {exc}"
            )
            return None
        normalized_parts: list[str] = []
        for part in parts:
            if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}:
                normalized_parts.append(part[1:-1])
            else:
                normalized_parts.append(part)
        parts = tuple(normalized_parts)
        if not parts:
            self.bridge_command_error = (
                "UNITYTOOL_PATCH_BRIDGE did not produce a command."
            )
            return None
        return parts

    def _is_unity_bridge_target(self, target_path: Path) -> bool:
        return target_path.suffix.lower() in _UNITY_BRIDGE_SUPPORTED_SUFFIXES

    def _is_bridge_command_allowed(self, command: tuple[str, ...]) -> bool:
        head = Path(command[0]).name.lower()
        return head in _UNITY_BRIDGE_ALLOWED_COMMANDS

    def _parse_bridge_response(
        self,
        payload: dict[str, Any],
        target_path: Path,
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        if not isinstance(payload, dict):
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_PROTOCOL",
                message="Unity bridge response must be a JSON object.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )

        protocol_raw = payload.get("protocol_version", _UNITY_BRIDGE_PROTOCOL_VERSION)
        try:
            protocol_version = int(protocol_raw)
        except (TypeError, ValueError):
            protocol_version = -1
        if protocol_version != _UNITY_BRIDGE_PROTOCOL_VERSION:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_PROTOCOL_VERSION",
                message="Unity bridge protocol version mismatch.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "expected_protocol_version": _UNITY_BRIDGE_PROTOCOL_VERSION,
                    "received_protocol_version": protocol_raw,
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )

        severity_raw = str(payload.get("severity", Severity.ERROR.value))
        try:
            severity = Severity(severity_raw)
        except ValueError:
            severity = Severity.ERROR

        diagnostics_payload = payload.get("diagnostics", [])
        diagnostics: list[Diagnostic] = []
        if isinstance(diagnostics_payload, list):
            for item in diagnostics_payload:
                if not isinstance(item, dict):
                    continue
                diagnostics.append(
                    Diagnostic(
                        path=str(item.get("path", "")),
                        location=str(item.get("location", "")),
                        detail=str(item.get("detail", "")),
                        evidence=str(item.get("evidence", "")),
                    )
                )

        data = payload.get("data", {})
        if not isinstance(data, dict):
            data = {}
        data.setdefault("target", str(target_path))
        data.setdefault("op_count", len(ops))
        data.setdefault("read_only", False)
        data.setdefault("executed", True)
        data.setdefault("protocol_version", protocol_version)

        return ToolResponse(
            success=bool(payload.get("success", False)),
            severity=severity,
            code=str(payload.get("code", "SER_BRIDGE_PROTOCOL")),
            message=str(payload.get("message", "Unity bridge response parsed.")),
            data=data,
            diagnostics=diagnostics,
        )

    def _apply_with_unity_bridge(
        self,
        target_path: Path,
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        if self.bridge_command_error:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_CONFIG",
                message="Unity bridge command configuration is invalid.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                    "error": self.bridge_command_error,
                },
                diagnostics=[],
            )
        if not self.bridge_command:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_UNSUPPORTED_TARGET",
                message=(
                    "Non-JSON target requires UNITYTOOL_PATCH_BRIDGE for Unity bridge "
                    "execution."
                ),
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )
        if not self._is_bridge_command_allowed(self.bridge_command):
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_DENIED",
                message="Unity bridge command is not in the allowlist.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "command": list(self.bridge_command),
                    "allowed_commands": sorted(_UNITY_BRIDGE_ALLOWED_COMMANDS),
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )

        request_payload = {
            "protocol_version": _UNITY_BRIDGE_PROTOCOL_VERSION,
            "target": str(target_path),
            "ops": ops,
        }
        try:
            completed = subprocess.run(
                list(self.bridge_command),
                input=json.dumps(request_payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.bridge_timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_TIMEOUT",
                message="Unity bridge process timed out.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "command": list(self.bridge_command),
                    "timeout_sec": self.bridge_timeout_sec,
                    "error": str(exc),
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )
        except OSError as exc:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_EXEC",
                message="Failed to start Unity bridge process.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "command": list(self.bridge_command),
                    "error": str(exc),
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )

        if completed.returncode != 0:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_FAILED",
                message="Unity bridge process returned non-zero exit code.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "command": list(self.bridge_command),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_BRIDGE_PROTOCOL",
                message="Unity bridge output must be valid JSON.",
                data={
                    "target": str(target_path),
                    "op_count": len(ops),
                    "command": list(self.bridge_command),
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "error": str(exc),
                    "read_only": False,
                    "executed": False,
                },
                diagnostics=[],
            )

        return self._parse_bridge_response(payload, target_path=target_path, ops=ops)

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
            if self._is_unity_bridge_target(target_path):
                return self._apply_with_unity_bridge(target_path=target_path, ops=ops)
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="SER_UNSUPPORTED_TARGET",
                message="Phase 1 apply backend supports .json or Unity bridge targets only.",
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Patch plan root must be an object.")
    return payload


def compute_patch_plan_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_patch_plan_hmac_sha256(path: Path, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), path.read_bytes(), hashlib.sha256)
    return digest.hexdigest()
