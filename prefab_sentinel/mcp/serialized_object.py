from __future__ import annotations

import json
import os
import shlex
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse
from prefab_sentinel.patch_plan import (
    PLAN_VERSION,
    build_bridge_request,
)
from prefab_sentinel.mcp.prefab_variant import PrefabVariantMcp
from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    decode_text_file,
    find_project_root,
    resolve_scope_path,
)

_SUPPORTED_OPS = {"set", "insert_array_element", "remove_array_element"}
_PREFAB_CREATE_OPS = {
    "create_prefab",
    "create_root",
    "create_game_object",
    "rename_object",
    "reparent",
    "add_component",
    "find_component",
    "remove_component",
    "set",
    "insert_array_element",
    "remove_array_element",
    "save",
}
_ROOT_HANDLE = "root"
_ASSET_HANDLE = "asset"
_SCENE_HANDLE = "scene"
_UNITY_BRIDGE_PROTOCOL_VERSION = PLAN_VERSION
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
    "prefab-sentinel-unity-bridge",
    "prefab-sentinel-unity-bridge.exe",
    "prefab-sentinel-unity-serialized-object-bridge",
    "prefab-sentinel-unity-serialized-object-bridge.exe",
}

_UNITY_BRIDGE_KIND_BY_SUFFIX = {
    ".prefab": "prefab",
    ".unity": "scene",
    ".asset": "asset",
    ".mat": "material",
    ".anim": "animation",
    ".controller": "controller",
}


@dataclass(frozen=True)
class _ResourcePlanContext:
    target: str
    kind: str
    mode: str
    target_path: Path
    ops: list[dict[str, Any]]


class _ResourceAdapter:
    supported_kind = ""
    supported_modes = frozenset({"open"})

    def supports(self, context: _ResourcePlanContext) -> bool:
        return (
            context.kind == self.supported_kind
            and context.mode in self.supported_modes
        )

    def dry_run(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        raise NotImplementedError

    def apply(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        raise NotImplementedError


class _JsonResourceAdapter(_ResourceAdapter):
    supported_kind = "json"

    def dry_run(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        return owner.dry_run_patch(target=context.target, ops=context.ops)

    def apply(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        return owner.apply_and_save(target=context.target, ops=context.ops)


class _PrefabResourceAdapter(_ResourceAdapter):
    supported_kind = "prefab"
    supported_modes = frozenset({"open", "create"})

    def dry_run(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        if context.mode == "open":
            return owner.dry_run_patch(target=context.target, ops=context.ops)
        diagnostics, preview = owner._validate_prefab_create_ops(
            target=context.target,
            ops=context.ops,
        )
        if diagnostics:
            return owner._resource_plan_invalid_response(
                context=context,
                diagnostics=diagnostics,
                read_only=True,
            )
        return owner._resource_plan_preview_response(context=context, preview=preview)

    def apply(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        dry_run = self.dry_run(owner, context)
        if not dry_run.success:
            return owner._resource_plan_apply_invalid_response(
                context=context,
                diagnostics=dry_run.diagnostics,
            )
        return owner._apply_with_unity_bridge(
            target_path=context.target_path,
            ops=context.ops,
            resource_kind=context.kind,
            resource_mode=context.mode,
        )


class _BridgeBackedAssetResourceAdapter(_ResourceAdapter):
    supported_modes = frozenset({"open", "create"})

    def dry_run(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        if context.mode == "open":
            return owner.dry_run_patch(target=context.target, ops=context.ops)
        diagnostics, preview = owner._validate_asset_create_ops(
            target=context.target,
            kind=context.kind,
            ops=context.ops,
        )
        if diagnostics:
            return owner._resource_plan_invalid_response(
                context=context,
                diagnostics=diagnostics,
                read_only=True,
            )
        return owner._resource_plan_preview_response(context=context, preview=preview)

    def apply(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        dry_run = self.dry_run(owner, context)
        if not dry_run.success:
            return owner._resource_plan_apply_invalid_response(
                context=context,
                diagnostics=dry_run.diagnostics,
            )
        return owner._apply_with_unity_bridge(
            target_path=context.target_path,
            ops=context.ops,
            resource_kind=context.kind,
            resource_mode=context.mode,
        )


class _AssetResourceAdapter(_BridgeBackedAssetResourceAdapter):
    supported_kind = "asset"


class _MaterialResourceAdapter(_BridgeBackedAssetResourceAdapter):
    supported_kind = "material"


class _SceneResourceAdapter(_ResourceAdapter):
    supported_kind = "scene"
    supported_modes = frozenset({"open", "create"})

    def dry_run(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        diagnostics, preview = owner._validate_scene_ops(
            target=context.target,
            mode=context.mode,
            ops=context.ops,
        )
        if diagnostics:
            return owner._resource_plan_invalid_response(
                context=context,
                diagnostics=diagnostics,
                read_only=True,
            )
        return owner._resource_plan_preview_response(context=context, preview=preview)

    def apply(
        self,
        owner: SerializedObjectMcp,
        context: _ResourcePlanContext,
    ) -> ToolResponse:
        dry_run = self.dry_run(owner, context)
        if not dry_run.success:
            return owner._resource_plan_apply_invalid_response(
                context=context,
                diagnostics=dry_run.diagnostics,
            )
        return owner._apply_with_unity_bridge(
            target_path=context.target_path,
            ops=context.ops,
            resource_kind=context.kind,
            resource_mode=context.mode,
        )


class SerializedObjectMcp:
    """Serialized-object MCP scaffold with plan validation and dry-run preview."""

    TOOL_NAME = "unity-serialized-object-mcp"

    def __init__(
        self,
        bridge_command: tuple[str, ...] | None = None,
        bridge_timeout_sec: float = 120.0,
        project_root: Path | None = None,
        prefab_variant: PrefabVariantMcp | None = None,
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
        self.project_root = find_project_root(project_root or Path.cwd())
        self._prefab_variant = prefab_variant
        self._before_cache: dict[str, str] | None = None
        self._resource_adapters: tuple[_ResourceAdapter, ...] = (
            _JsonResourceAdapter(),
            _PrefabResourceAdapter(),
            _AssetResourceAdapter(),
            _MaterialResourceAdapter(),
            _SceneResourceAdapter(),
        )

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

    def _infer_bridge_resource_kind(self, target_path: Path) -> str:
        return _UNITY_BRIDGE_KIND_BY_SUFFIX.get(target_path.suffix.lower(), "asset")

    def _infer_resource_kind(self, target_path: Path) -> str:
        if target_path.suffix.lower() == ".json":
            return "json"
        return self._infer_bridge_resource_kind(target_path)

    def _resolve_resource_context(
        self,
        resource: dict[str, Any],
        ops: list[dict[str, Any]],
    ) -> _ResourcePlanContext:
        target = str(resource.get("path", "")).strip()
        kind = str(resource.get("kind", "")).strip().lower()
        target_path = Path(target) if target else Path()
        if not kind and target:
            kind = self._infer_resource_kind(target_path)
        mode = str(resource.get("mode", "open")).strip().lower() or "open"
        return _ResourcePlanContext(
            target=target,
            kind=kind,
            mode=mode,
            target_path=target_path,
            ops=ops,
        )

    def _select_resource_adapter(
        self,
        context: _ResourcePlanContext,
    ) -> _ResourceAdapter | None:
        for adapter in self._resource_adapters:
            if adapter.supports(context):
                return adapter
        return None

    def _resource_plan_invalid_response(
        self,
        *,
        context: _ResourcePlanContext,
        diagnostics: list[Diagnostic],
        read_only: bool,
    ) -> ToolResponse:
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="SER_PLAN_INVALID",
            message="Patch plan schema validation failed.",
            data={
                "target": context.target,
                "kind": context.kind,
                "mode": context.mode,
                "op_count": len(context.ops),
                "read_only": read_only,
            },
            diagnostics=diagnostics,
        )

    def _resource_plan_apply_invalid_response(
        self,
        *,
        context: _ResourcePlanContext,
        diagnostics: list[Diagnostic],
    ) -> ToolResponse:
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="SER_PLAN_INVALID",
            message="Patch plan schema validation failed.",
            data={
                "target": context.target,
                "kind": context.kind,
                "mode": context.mode,
                "op_count": len(context.ops),
                "applied": 0,
                "read_only": False,
                "executed": False,
            },
            diagnostics=diagnostics,
        )

    def _resource_plan_preview_response(
        self,
        *,
        context: _ResourcePlanContext,
        preview: list[dict[str, Any]],
    ) -> ToolResponse:
        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="SER_DRY_RUN_OK",
            message="dry_run_patch generated a patch preview.",
            data={
                "target": context.target,
                "kind": context.kind,
                "mode": context.mode,
                "op_count": len(context.ops),
                "applied": 0,
                "diff": preview,
                "read_only": True,
            },
            diagnostics=[],
        )

    def _unsupported_resource_plan_response(
        self,
        *,
        context: _ResourcePlanContext,
        read_only: bool,
    ) -> ToolResponse:
        target_value = context.target
        if not read_only and context.target:
            target_value = str(self._resolve_target_path(context.target))
        data = {
            "target": target_value,
            "kind": context.kind,
            "mode": context.mode,
            "op_count": len(context.ops),
            "read_only": read_only,
        }
        if not read_only:
            data.update({"applied": 0, "executed": False})
        return ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="SER_UNSUPPORTED_TARGET",
            message="Resource mode/kind combination is not supported by the current backend.",
            data=data,
            diagnostics=[],
        )

    def _build_unity_bridge_request(
        self,
        target_path: Path,
        ops: list[dict[str, Any]],
        *,
        resource_kind: str | None = None,
        resource_mode: str = "open",
    ) -> dict[str, Any]:
        resource_id = "target"
        bridged_ops = [{**deepcopy(op), "resource": resource_id} for op in ops]
        return build_bridge_request(
            {
                "plan_version": PLAN_VERSION,
                "resources": [
                    {
                        "id": resource_id,
                        "kind": resource_kind or self._infer_bridge_resource_kind(target_path),
                        "path": str(target_path),
                        "mode": resource_mode,
                    }
                ],
                "ops": bridged_ops,
            }
        )

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
        *,
        resource_kind: str | None = None,
        resource_mode: str = "open",
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
            **self._build_unity_bridge_request(
                target_path=target_path,
                ops=ops,
                resource_kind=resource_kind,
                resource_mode=resource_mode,
            ),
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

    def _normalize_handle_name(self, raw: object) -> str:
        if not isinstance(raw, str):
            return ""
        normalized = raw.strip()
        if normalized.startswith("$"):
            normalized = normalized[1:]
        return normalized.strip()

    def _validate_result_handle(
        self,
        *,
        target: str,
        index: int,
        op: dict[str, Any],
        known_handles: dict[str, str],
        diagnostics: list[Diagnostic],
    ) -> str | None:
        if "result" not in op:
            return None
        handle_name = self._normalize_handle_name(op.get("result"))
        if not handle_name:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].result",
                    detail="schema_error",
                    evidence="result must be a non-empty string when provided",
                )
            )
            return None
        if handle_name in known_handles:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].result",
                    detail="schema_error",
                    evidence=f"handle '{handle_name}' is already defined",
                )
            )
            return None
        return handle_name

    def _require_handle_ref(
        self,
        *,
        target: str,
        index: int,
        field: str,
        op: dict[str, Any],
        known_handles: dict[str, str],
        diagnostics: list[Diagnostic],
        expected_kind: str | set[str] | tuple[str, ...] | None = None,
    ) -> str | None:
        handle_name = self._normalize_handle_name(op.get(field))
        if not handle_name:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].{field}",
                    detail="schema_error",
                    evidence=f"{field} must reference a handle",
                )
            )
            return None
        if handle_name not in known_handles:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].{field}",
                    detail="schema_error",
                    evidence=f"unknown handle '{handle_name}'",
                )
            )
            return None
        actual_kind = known_handles[handle_name]
        if expected_kind is not None:
            expected_kinds = (
                {expected_kind}
                if isinstance(expected_kind, str)
                else set(expected_kind)
            )
        else:
            expected_kinds = None
        if expected_kinds is not None and actual_kind not in expected_kinds:
            if len(expected_kinds) == 1:
                expected_text = next(iter(expected_kinds)).replace("_", " ")
            else:
                expected_text = " or ".join(
                    kind.replace("_", " ") for kind in sorted(expected_kinds)
                )
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].{field}",
                    detail="schema_error",
                    evidence=(
                        f"handle '{handle_name}' must reference a "
                        f"{expected_text}"
                    ),
                )
            )
            return None
        return handle_name

    def _validate_prefab_create_ops(
        self,
        target: str,
        ops: list[dict[str, Any]],
    ) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
        diagnostics: list[Diagnostic] = []
        preview: list[dict[str, Any]] = []
        if not target:
            diagnostics.append(
                Diagnostic(
                    path="",
                    location="resources[].path",
                    detail="schema_error",
                    evidence="target path is required for prefab create mode",
                )
            )
            return diagnostics, preview
        if Path(target).suffix.lower() != ".prefab":
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="resources[].path",
                    detail="schema_error",
                    evidence="prefab create mode requires a .prefab target path",
                )
            )
            return diagnostics, preview
        if not ops:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="ops must contain at least one operation",
                )
            )
            return diagnostics, preview

        created = False
        saved = False
        root_name = Path(target).stem or "PrefabRoot"
        known_handles: dict[str, str] = {}

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

            op_name = str(op.get("op", "")).strip()
            if op_name in {"create_prefab", "create_root"}:
                if created:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="prefab root may be created only once",
                        )
                    )
                    continue
                created = True
                known_handles[_ROOT_HANDLE] = "game_object"
                name_value = op.get("name")
                if op_name == "create_root":
                    if not isinstance(name_value, str) or not name_value.strip():
                        diagnostics.append(
                            Diagnostic(
                                path=target,
                                location=f"ops[{index}].name",
                                detail="schema_error",
                                evidence="name is required for create_root",
                            )
                        )
                        continue
                    root_name = name_value.strip()
                elif name_value is not None:
                    if not isinstance(name_value, str) or not name_value.strip():
                        diagnostics.append(
                            Diagnostic(
                                path=target,
                                location=f"ops[{index}].name",
                                detail="schema_error",
                                evidence="name must be a non-empty string when provided",
                            )
                        )
                        continue
                    root_name = name_value.strip()
                result_handle = self._validate_result_handle(
                    target=target,
                    index=index,
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                )
                if result_handle and result_handle != _ROOT_HANDLE:
                    known_handles[result_handle] = "game_object"
                preview.append(
                    {
                        "op": op_name,
                        "before": "(missing)",
                        "after": {
                            "path": target,
                            "root_name": root_name,
                            "handle": result_handle or _ROOT_HANDLE,
                            "kind": "game_object",
                        },
                    }
                )
                continue

            if op_name == "create_game_object":
                if not created:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="create_game_object requires a prefab root first",
                        )
                    )
                    continue
                name_value = op.get("name")
                if not isinstance(name_value, str) or not name_value.strip():
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].name",
                            detail="schema_error",
                            evidence="name is required for create_game_object",
                        )
                    )
                    continue
                parent_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="parent",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                result_handle = self._validate_result_handle(
                    target=target,
                    index=index,
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                )
                if parent_handle is None or ("result" in op and result_handle is None):
                    continue
                if result_handle:
                    known_handles[result_handle] = "game_object"
                preview.append(
                    {
                        "op": op_name,
                        "before": "(missing)",
                        "after": {
                            "name": name_value.strip(),
                            "parent": parent_handle,
                            "handle": result_handle or "(anonymous)",
                            "kind": "game_object",
                        },
                    }
                )
                continue

            if op_name == "rename_object":
                object_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                name_value = op.get("name")
                if object_handle is None:
                    continue
                if not isinstance(name_value, str) or not name_value.strip():
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].name",
                            detail="schema_error",
                            evidence="name is required for rename_object",
                        )
                    )
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": {"handle": object_handle},
                        "after": {"handle": object_handle, "name": name_value.strip()},
                    }
                )
                continue

            if op_name == "reparent":
                object_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                parent_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="parent",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                if object_handle is None or parent_handle is None:
                    continue
                if object_handle == _ROOT_HANDLE:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].target",
                            detail="schema_error",
                            evidence="root handle cannot be reparented",
                        )
                    )
                    continue
                if object_handle == parent_handle:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}]",
                            detail="schema_error",
                            evidence="target and parent handles must differ",
                        )
                    )
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": {"handle": object_handle},
                        "after": {"handle": object_handle, "parent": parent_handle},
                    }
                )
                continue

            if op_name in {"add_component", "find_component"}:
                if not created:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence=f"{op_name} requires a prefab root first",
                        )
                    )
                    continue
                object_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                type_name = op.get("type")
                if object_handle is None:
                    continue
                if not isinstance(type_name, str) or not type_name.strip():
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].type",
                            detail="schema_error",
                            evidence=f"type is required for {op_name}",
                        )
                    )
                    continue
                result_handle = self._validate_result_handle(
                    target=target,
                    index=index,
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                )
                if "result" in op and result_handle is None:
                    continue
                if result_handle:
                    known_handles[result_handle] = "component"
                preview.append(
                    {
                        "op": op_name,
                        "before": "(missing)" if op_name == "add_component" else {"target": object_handle},
                        "after": {
                            "target": object_handle,
                            "type": type_name.strip(),
                            "handle": result_handle or "(anonymous)",
                            "kind": "component",
                        },
                    }
                )
                continue

            if op_name == "remove_component":
                component_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="component",
                )
                if component_handle is None:
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": {"handle": component_handle, "kind": "component"},
                        "after": "(removed)",
                    }
                )
                continue

            if op_name in _SUPPORTED_OPS:
                component_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="component",
                )
                property_path = str(op.get("path", "")).strip()
                if component_handle is None:
                    continue
                if not property_path:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].path",
                            detail="schema_error",
                            evidence="path is required",
                        )
                    )
                    continue
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
                        continue
                    preview.append(
                        {
                            "op": op_name,
                            "before": {"handle": component_handle, "path": property_path},
                            "after": {
                                "handle": component_handle,
                                "path": property_path,
                                "value": deepcopy(op.get("value")),
                            },
                        }
                    )
                    continue

                op_index = op.get("index")
                if isinstance(op_index, bool) or not isinstance(op_index, int):
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].index",
                            detail="schema_error",
                            evidence="index must be an integer",
                        )
                    )
                    continue
                entry = {
                    "op": op_name,
                    "before": {
                        "handle": component_handle,
                        "path": property_path,
                        "index": op_index,
                    },
                    "after": {
                        "handle": component_handle,
                        "path": property_path,
                        "index": op_index,
                    },
                }
                if op_name == "insert_array_element" and "value" in op:
                    entry["after"]["value"] = deepcopy(op.get("value"))
                preview.append(entry)
                continue

            if op_name == "save":
                if saved:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save may appear only once",
                        )
                    )
                    continue
                if not created:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save requires a prefab root first",
                        )
                    )
                    continue
                if index != len(ops) - 1:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save must be the final operation in create mode",
                        )
                    )
                    continue
                saved = True
                preview.append(
                    {
                        "op": op_name,
                        "before": "(unsaved)",
                        "after": {"path": target},
                    }
                )
                continue

            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported prefab create op '{op_name}'",
                )
            )

        if not created:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="create mode requires a root creation operation",
                )
            )
        if not saved:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="create mode requires a save operation",
                )
            )
        return diagnostics, preview

    def _validate_asset_open_ops(
        self,
        *,
        target: str,
        kind: str,
        ops: list[dict[str, Any]],
    ) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
        diagnostics: list[Diagnostic] = []
        preview: list[dict[str, Any]] = []
        suffix = ".mat" if kind == "material" else ".asset"
        if not target:
            diagnostics.append(
                Diagnostic(
                    path="",
                    location="target",
                    detail="schema_error",
                    evidence=f"target path is required for {kind} open mode",
                )
            )
            return diagnostics, preview
        if Path(target).suffix.lower() != suffix:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="target",
                    detail="schema_error",
                    evidence=f"{kind} open mode requires a {suffix} target path",
                )
            )
            return diagnostics, preview
        if not ops:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="ops must contain at least one operation",
                )
            )
            return diagnostics, preview

        known_handles = {_ASSET_HANDLE: "asset"}
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

            op_name = str(op.get("op", "")).strip()
            if op_name not in _SUPPORTED_OPS:
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}].op",
                        detail="schema_error",
                        evidence=f"unsupported asset open op '{op_name}'",
                    )
                )
                continue

            asset_handle = self._require_handle_ref(
                target=target,
                index=index,
                field="target",
                op=op,
                known_handles=known_handles,
                diagnostics=diagnostics,
                expected_kind="asset",
            )
            property_path = str(op.get("path", "")).strip()
            if asset_handle is None:
                continue
            if not property_path:
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}].path",
                        detail="schema_error",
                        evidence="path is required",
                    )
                )
                continue

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
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": {"handle": asset_handle, "path": property_path},
                        "after": {
                            "handle": asset_handle,
                            "path": property_path,
                            "value": deepcopy(op.get("value")),
                        },
                    }
                )
                continue

            op_index = op.get("index")
            if isinstance(op_index, bool) or not isinstance(op_index, int):
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}].index",
                        detail="schema_error",
                        evidence="index must be an integer",
                    )
                )
                continue
            entry = {
                "op": op_name,
                "before": {
                    "handle": asset_handle,
                    "path": property_path,
                    "index": op_index,
                },
                "after": {
                    "handle": asset_handle,
                    "path": property_path,
                    "index": op_index,
                },
            }
            if op_name == "insert_array_element" and "value" in op:
                entry["after"]["value"] = deepcopy(op.get("value"))
            preview.append(entry)

        return diagnostics, preview

    def _validate_asset_create_ops(
        self,
        *,
        target: str,
        kind: str,
        ops: list[dict[str, Any]],
    ) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
        diagnostics: list[Diagnostic] = []
        preview: list[dict[str, Any]] = []
        suffix = ".mat" if kind == "material" else ".asset"
        if not target:
            diagnostics.append(
                Diagnostic(
                    path="",
                    location="resources[].path",
                    detail="schema_error",
                    evidence=f"target path is required for {kind} create mode",
                )
            )
            return diagnostics, preview
        if Path(target).suffix.lower() != suffix:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="resources[].path",
                    detail="schema_error",
                    evidence=f"{kind} create mode requires a {suffix} target path",
                )
            )
            return diagnostics, preview
        if not ops:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="ops must contain at least one operation",
                )
            )
            return diagnostics, preview

        created = False
        saved = False
        known_handles: dict[str, str] = {}
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

            op_name = str(op.get("op", "")).strip()
            if op_name == "create_asset":
                if created:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="asset root may be created only once",
                        )
                    )
                    continue
                created = True
                known_handles[_ASSET_HANDLE] = "asset"
                result_handle = self._validate_result_handle(
                    target=target,
                    index=index,
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                )
                if result_handle and result_handle != _ASSET_HANDLE:
                    known_handles[result_handle] = "asset"
                name_value = op.get("name")
                if name_value is not None and (
                    not isinstance(name_value, str) or not name_value.strip()
                ):
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].name",
                            detail="schema_error",
                            evidence="name must be a non-empty string when provided",
                        )
                    )
                    continue
                asset_name = (
                    name_value.strip()
                    if isinstance(name_value, str) and name_value.strip()
                    else Path(target).stem
                )

                if kind == "material":
                    shader_name = op.get("shader")
                    if not isinstance(shader_name, str) or not shader_name.strip():
                        diagnostics.append(
                            Diagnostic(
                                path=target,
                                location=f"ops[{index}].shader",
                                detail="schema_error",
                                evidence="shader is required for create_asset on material resources",
                            )
                        )
                        continue
                    type_name = op.get("type")
                    if type_name is not None and (
                        not isinstance(type_name, str) or not type_name.strip()
                    ):
                        diagnostics.append(
                            Diagnostic(
                                path=target,
                                location=f"ops[{index}].type",
                                detail="schema_error",
                                evidence="type must be a non-empty string when provided",
                            )
                        )
                        continue
                    preview.append(
                        {
                            "op": op_name,
                            "before": "(missing)",
                            "after": {
                                "path": target,
                                "type": type_name.strip()
                                if isinstance(type_name, str) and type_name.strip()
                                else "UnityEngine.Material",
                                "shader": shader_name.strip(),
                                "handle": result_handle or _ASSET_HANDLE,
                                "kind": "asset",
                                "name": asset_name,
                            },
                        }
                    )
                    continue

                type_name = op.get("type")
                if not isinstance(type_name, str) or not type_name.strip():
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].type",
                            detail="schema_error",
                            evidence="type is required for create_asset on asset resources",
                        )
                    )
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": "(missing)",
                        "after": {
                            "path": target,
                            "type": type_name.strip(),
                            "handle": result_handle or _ASSET_HANDLE,
                            "kind": "asset",
                            "name": asset_name,
                        },
                    }
                )
                continue

            if op_name in _SUPPORTED_OPS:
                if not created:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence=f"{op_name} requires a create_asset operation first",
                        )
                    )
                    continue
                asset_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="asset",
                )
                property_path = str(op.get("path", "")).strip()
                if asset_handle is None:
                    continue
                if not property_path:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].path",
                            detail="schema_error",
                            evidence="path is required",
                        )
                    )
                    continue
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
                        continue
                    preview.append(
                        {
                            "op": op_name,
                            "before": {"handle": asset_handle, "path": property_path},
                            "after": {
                                "handle": asset_handle,
                                "path": property_path,
                                "value": deepcopy(op.get("value")),
                            },
                        }
                    )
                    continue

                op_index = op.get("index")
                if isinstance(op_index, bool) or not isinstance(op_index, int):
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].index",
                            detail="schema_error",
                            evidence="index must be an integer",
                        )
                    )
                    continue
                entry = {
                    "op": op_name,
                    "before": {
                        "handle": asset_handle,
                        "path": property_path,
                        "index": op_index,
                    },
                    "after": {
                        "handle": asset_handle,
                        "path": property_path,
                        "index": op_index,
                    },
                }
                if op_name == "insert_array_element" and "value" in op:
                    entry["after"]["value"] = deepcopy(op.get("value"))
                preview.append(entry)
                continue

            if op_name == "save":
                if saved:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save may appear only once",
                        )
                    )
                    continue
                if not created:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save requires a create_asset operation first",
                        )
                    )
                    continue
                if index != len(ops) - 1:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save must be the final operation in create mode",
                        )
                    )
                    continue
                saved = True
                preview.append(
                    {
                        "op": op_name,
                        "before": "(unsaved)",
                        "after": {"path": target},
                    }
                )
                continue

            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported {kind} create op '{op_name}'",
                )
            )

        if not created:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="create mode requires a create_asset operation",
                )
            )
        if not saved:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="create mode requires a save operation",
                )
            )
        return diagnostics, preview

    def _validate_scene_ops(
        self,
        *,
        target: str,
        mode: str,
        ops: list[dict[str, Any]],
    ) -> tuple[list[Diagnostic], list[dict[str, Any]]]:
        diagnostics: list[Diagnostic] = []
        preview: list[dict[str, Any]] = []
        if not target:
            diagnostics.append(
                Diagnostic(
                    path="",
                    location="resources[].path" if mode == "create" else "target",
                    detail="schema_error",
                    evidence=f"target path is required for scene {mode} mode",
                )
            )
            return diagnostics, preview
        if Path(target).suffix.lower() != ".unity":
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="resources[].path" if mode == "create" else "target",
                    detail="schema_error",
                    evidence=f"scene {mode} mode requires a .unity target path",
                )
            )
            return diagnostics, preview
        if not ops:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="ops must contain at least one operation",
                )
            )
            return diagnostics, preview

        expected_first_op = "create_scene" if mode == "create" else "open_scene"
        known_handles: dict[str, str] = {_SCENE_HANDLE: "scene"}
        scene_initialized = False
        saved = False

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

            op_name = str(op.get("op", "")).strip()
            if index == 0:
                if op_name != expected_first_op:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location="ops[0].op",
                            detail="schema_error",
                            evidence=f"scene {mode} mode must start with {expected_first_op}",
                        )
                    )
                    continue
                scene_initialized = True
                preview.append(
                    {
                        "op": op_name,
                        "before": "(closed)" if mode == "open" else "(missing)",
                        "after": {"path": target, "handle": _SCENE_HANDLE, "kind": "scene"},
                    }
                )
                continue

            if op_name in {"create_scene", "open_scene"}:
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}].op",
                        detail="schema_error",
                        evidence=f"{op_name} may appear only as the first operation",
                    )
                )
                continue

            if op_name == "create_game_object":
                if not scene_initialized:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="create_game_object requires an opened scene first",
                        )
                    )
                    continue
                name_value = op.get("name")
                if not isinstance(name_value, str) or not name_value.strip():
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].name",
                            detail="schema_error",
                            evidence="name is required for create_game_object",
                        )
                    )
                    continue
                parent_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="parent",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind={"scene", "game_object"},
                )
                result_handle = self._validate_result_handle(
                    target=target,
                    index=index,
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                )
                if parent_handle is None or ("result" in op and result_handle is None):
                    continue
                if result_handle:
                    known_handles[result_handle] = "game_object"
                preview.append(
                    {
                        "op": op_name,
                        "before": "(missing)",
                        "after": {
                            "name": name_value.strip(),
                            "parent": parent_handle,
                            "handle": result_handle or "(anonymous)",
                            "kind": "game_object",
                        },
                    }
                )
                continue

            if op_name == "instantiate_prefab":
                if not scene_initialized:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="instantiate_prefab requires an opened scene first",
                        )
                    )
                    continue
                prefab_path = str(op.get("prefab", "")).strip()
                if not prefab_path:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].prefab",
                            detail="schema_error",
                            evidence="prefab is required for instantiate_prefab",
                        )
                    )
                    continue
                parent_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="parent",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind={"scene", "game_object"},
                )
                result_handle = self._validate_result_handle(
                    target=target,
                    index=index,
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                )
                if parent_handle is None or ("result" in op and result_handle is None):
                    continue
                if result_handle:
                    known_handles[result_handle] = "game_object"
                preview.append(
                    {
                        "op": op_name,
                        "before": "(missing)",
                        "after": {
                            "prefab": prefab_path,
                            "parent": parent_handle,
                            "handle": result_handle or "(anonymous)",
                            "kind": "game_object",
                        },
                    }
                )
                continue

            if op_name == "rename_object":
                object_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                name_value = op.get("name")
                if object_handle is None:
                    continue
                if not isinstance(name_value, str) or not name_value.strip():
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].name",
                            detail="schema_error",
                            evidence="name is required for rename_object",
                        )
                    )
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": {"handle": object_handle},
                        "after": {"handle": object_handle, "name": name_value.strip()},
                    }
                )
                continue

            if op_name == "reparent":
                object_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                parent_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="parent",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind={"scene", "game_object"},
                )
                if object_handle is None or parent_handle is None:
                    continue
                if object_handle == parent_handle:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}]",
                            detail="schema_error",
                            evidence="target and parent handles must differ",
                        )
                    )
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": {"handle": object_handle},
                        "after": {"handle": object_handle, "parent": parent_handle},
                    }
                )
                continue

            if op_name in {"add_component", "find_component"}:
                if not scene_initialized:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence=f"{op_name} requires an opened scene first",
                        )
                    )
                    continue
                object_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="game_object",
                )
                type_name = op.get("type")
                if object_handle is None:
                    continue
                if not isinstance(type_name, str) or not type_name.strip():
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].type",
                            detail="schema_error",
                            evidence=f"type is required for {op_name}",
                        )
                    )
                    continue
                result_handle = self._validate_result_handle(
                    target=target,
                    index=index,
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                )
                if "result" in op and result_handle is None:
                    continue
                if result_handle:
                    known_handles[result_handle] = "component"
                preview.append(
                    {
                        "op": op_name,
                        "before": "(missing)" if op_name == "add_component" else {"target": object_handle},
                        "after": {
                            "target": object_handle,
                            "type": type_name.strip(),
                            "handle": result_handle or "(anonymous)",
                            "kind": "component",
                        },
                    }
                )
                continue

            if op_name == "remove_component":
                component_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="component",
                )
                if component_handle is None:
                    continue
                preview.append(
                    {
                        "op": op_name,
                        "before": {"handle": component_handle, "kind": "component"},
                        "after": "(removed)",
                    }
                )
                continue

            if op_name in _SUPPORTED_OPS:
                component_handle = self._require_handle_ref(
                    target=target,
                    index=index,
                    field="target",
                    op=op,
                    known_handles=known_handles,
                    diagnostics=diagnostics,
                    expected_kind="component",
                )
                property_path = str(op.get("path", "")).strip()
                if component_handle is None:
                    continue
                if not property_path:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].path",
                            detail="schema_error",
                            evidence="path is required",
                        )
                    )
                    continue
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
                        continue
                    preview.append(
                        {
                            "op": op_name,
                            "before": {"handle": component_handle, "path": property_path},
                            "after": {
                                "handle": component_handle,
                                "path": property_path,
                                "value": deepcopy(op.get("value")),
                            },
                        }
                    )
                    continue

                op_index = op.get("index")
                if isinstance(op_index, bool) or not isinstance(op_index, int):
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].index",
                            detail="schema_error",
                            evidence="index must be an integer",
                        )
                    )
                    continue
                entry = {
                    "op": op_name,
                    "before": {
                        "handle": component_handle,
                        "path": property_path,
                        "index": op_index,
                    },
                    "after": {
                        "handle": component_handle,
                        "path": property_path,
                        "index": op_index,
                    },
                }
                if op_name == "insert_array_element" and "value" in op:
                    entry["after"]["value"] = deepcopy(op.get("value"))
                preview.append(entry)
                continue

            if op_name == "save_scene":
                if not scene_initialized:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save_scene requires an opened scene first",
                        )
                    )
                    continue
                if saved:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save_scene may appear only once",
                        )
                    )
                    continue
                if index != len(ops) - 1:
                    diagnostics.append(
                        Diagnostic(
                            path=target,
                            location=f"ops[{index}].op",
                            detail="schema_error",
                            evidence="save_scene must be the final operation in scene mode",
                        )
                    )
                    continue
                saved = True
                preview.append(
                    {
                        "op": op_name,
                        "before": "(unsaved)",
                        "after": {"path": target},
                    }
                )
                continue

            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].op",
                    detail="schema_error",
                    evidence=f"unsupported scene op '{op_name}'",
                )
            )

        if not scene_initialized:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence=f"scene {mode} mode requires {expected_first_op}",
                )
            )
        if not saved:
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location="ops",
                    detail="schema_error",
                    evidence="scene mode requires a save_scene operation",
                )
            )
        return diagnostics, preview

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
            if op_name in _PREFAB_CREATE_OPS:
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}].op",
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
        _bare = component.lstrip("-")
        if _bare.isdigit():
            diagnostics.append(
                Diagnostic(
                    path=target,
                    location=f"ops[{index}].component",
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
            value = op.get("value")
            entry = {
                "op": op_name,
                "component": component,
                "path": property_path,
                "before": self._resolve_before_value(target, component, property_path),
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

        if op_name in ("insert_array_element", "remove_array_element"):
            if not property_path.endswith(".Array.data"):
                diagnostics.append(
                    Diagnostic(
                        path=target,
                        location=f"ops[{index}].path",
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
                "before": self._resolve_before_value(target, component, property_path),
                "after": {"insert_index": item_index, "value": op.get("value")},
            }

        return {
            "op": op_name,
            "component": component,
            "path": property_path,
            "before": self._resolve_before_value(target, component, property_path),
            "after": {"remove_index": item_index},
        }

    def _resolve_before_value(
        self,
        target: str,
        component: str,
        property_path: str,
    ) -> str:
        """Best-effort resolution of the current value before a patch op.

        Traverses the full Prefab Variant chain (via
        ``resolve_chain_values()``) so that overrides from parent Variants and
        property values from the base prefab are included.  The closest
        (child) override wins.

        Returns a labelled placeholder when the value cannot be resolved.
        """
        if self._prefab_variant is None:
            return "(unresolved)"

        # Build/cache the effective-values lookup for this target.
        # An empty dict signals "file read but no overrides found" to avoid
        # re-reading the file for every op on the same target.
        if self._before_cache is None:
            try:
                target_path = resolve_scope_path(target, self.project_root)
                text = decode_text_file(target_path)
            except (OSError, UnicodeDecodeError):
                self._before_cache = {}
                return "(unresolved: file unreadable)"

            if SOURCE_PREFAB_PATTERN.search(text) is None:
                self._before_cache = {}
                return "(unresolved: not a variant)"

            self._before_cache = self._prefab_variant.resolve_chain_values(target)

        if not self._before_cache:
            # Empty cache = non-Variant or unreadable file (sentinel)
            return "(unresolved)"

        lookup_key = f"{component}:{property_path}"
        value = self._before_cache.get(lookup_key)
        if value is not None:
            return value
        return "(unresolved: not found in chain)"

    def _clear_before_cache(self) -> None:
        """Reset the per-target before-value cache."""
        self._before_cache = None

    def _resolve_target_path(self, target: str) -> Path:
        return resolve_scope_path(target, self.project_root)

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
        self._clear_before_cache()
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

        target_path = Path(str(target).strip())
        inferred_kind = (
            self._infer_bridge_resource_kind(target_path)
            if target_path.suffix.lower() in {".mat", ".asset", ".unity"}
            else ""
        )
        if inferred_kind == "scene":
            diagnostics, preview = self._validate_scene_ops(
                target=target,
                mode="open",
                ops=ops,
            )
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
        if inferred_kind in {"asset", "material"}:
            diagnostics, preview = self._validate_asset_open_ops(
                target=target,
                kind=inferred_kind,
                ops=ops,
            )
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

        soft_warnings: list[Diagnostic] = []
        for entry in preview:
            loc = f"{entry.get('component', '')}:{entry.get('path', '')}"
            before_val = entry.get("before", "")
            if isinstance(before_val, str) and before_val.startswith("(unresolved"):
                soft_warnings.append(
                    Diagnostic(
                        path=target,
                        location=loc,
                        detail="unresolved_before_value",
                        evidence=(
                            f"Before value unresolved: {before_val}. "
                            f"The target file may not exist, or the component path "
                            f"may be invalid. Verify with 'editor list-children' "
                            f"if bridge is available."
                        ),
                    )
                )
            warning_msg = entry.pop("_warning", None)
            if warning_msg:
                soft_warnings.append(
                    Diagnostic(
                        path=target,
                        location=loc,
                        detail="handle_in_value",
                        evidence=warning_msg,
                    )
                )

        if soft_warnings:
            return ToolResponse(
                success=True,
                severity=Severity.WARNING,
                code="SER_DRY_RUN_OK",
                message="dry_run_patch generated a patch preview with warnings.",
                data={
                    "target": target,
                    "op_count": len(ops),
                    "applied": 0,
                    "diff": preview,
                    "read_only": True,
                },
                diagnostics=soft_warnings,
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

    def dry_run_resource_plan(
        self,
        resource: dict[str, Any],
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        context = self._resolve_resource_context(resource, ops)
        adapter = self._select_resource_adapter(context)
        if adapter is None:
            return self._unsupported_resource_plan_response(
                context=context,
                read_only=True,
            )
        return adapter.dry_run(self, context)

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

    def apply_resource_plan(
        self,
        resource: dict[str, Any],
        ops: list[dict[str, Any]],
    ) -> ToolResponse:
        context = self._resolve_resource_context(resource, ops)
        if context.target:
            context = _ResourcePlanContext(
                target=context.target,
                kind=context.kind,
                mode=context.mode,
                target_path=self._resolve_target_path(context.target),
                ops=context.ops,
            )
        adapter = self._select_resource_adapter(context)
        if adapter is None:
            return self._unsupported_resource_plan_response(
                context=context,
                read_only=False,
            )
        return adapter.apply(self, context)
