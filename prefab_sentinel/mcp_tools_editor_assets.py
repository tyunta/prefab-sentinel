from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server import MCPServer

from prefab_sentinel.editor_bridge import send_action

__all__ = [
    "DryRunContext",
    "PathPlan",
    "MovePathPlan",
    "RenderTextureParameters",
    "ValidatedAuditContext",
    "editor_create_generated_asset",
    "editor_move_asset",
    "finalize_asset_operation_response",
    "register_editor_asset_tools",
    "validate_confirm_report_inputs",
    "validate_generated_asset_path",
    "validate_move_asset_paths",
    "validate_render_texture_parameters",
]

from collections.abc import Callable
from typing import cast

_ASSET_TYPE = "render_texture"
_RENDER_TEXTURE_EXTENSION = ".renderTexture"
_CHANGE_REASON_MAX = 1024
_INT_MIN = 1
_TEXTURE_SIZE_MAX = 8192
_DEPTH_VALUES = [0, 16, 24, 32]
_FORMAT_VALUES = ["ARGB32", "ARGBHalf", "Default", "DefaultHDR"]
_READ_WRITE_VALUES = ["Default", "Linear", "sRGB"]
_FILTER_MODE_VALUES = ["Point", "Bilinear", "Trilinear"]
_WRAP_MODE_VALUES = ["Clamp", "Repeat", "Mirror", "MirrorOnce"]
_RENDER_TEXTURE_REQUIRED_KEYS = {"width", "height"}
_RENDER_TEXTURE_OPTIONAL_DEFAULTS = {
    "depth": 0,
    "format": "ARGB32",
    "read_write": "Default",
    "filter_mode": "Bilinear",
    "wrap_mode": "Clamp",
    "mip_map": False,
}
_RENDER_TEXTURE_ALLOWED_KEYS = (
    _RENDER_TEXTURE_REQUIRED_KEYS | set(_RENDER_TEXTURE_OPTIONAL_DEFAULTS)
)

_CREATE_SUCCESS_FIELDS: dict[str, type | tuple[type, ...]] = {
    "asset_type": str,
    "unity_type": str,
    "asset_path": str,
    "guid": str,
    "would_create": bool,
    "created": bool,
    "dry_run": bool,
    "saved": bool,
    "refreshed": bool,
    "dirty_before": bool,
    "dirty_after": bool,
    "name": str,
    "applied_parameters": dict,
}
_MOVE_SUCCESS_FIELDS: dict[str, type | tuple[type, ...]] = {
    "source_asset_path": str,
    "destination_asset_path": str,
    "unity_type": str,
    "before_guid": str,
    "after_guid": str,
    "guid_preserved": bool,
    "would_move": bool,
    "moved": bool,
    "dry_run": bool,
    "saved": bool,
    "refreshed": bool,
    "dirty_before": bool,
    "dirty_after": bool,
    "old_name": str,
    "new_name": str,
    "name_changed": bool,
}


@dataclass(frozen=True, slots=True)
class DryRunContext:
    confirm: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedAuditContext:
    project_root: Path
    out_report: Path
    change_reason: str
    confirm: bool = True


@dataclass(frozen=True, slots=True)
class PathPlan:
    asset_path: str
    name: str


@dataclass(frozen=True, slots=True)
class MovePathPlan:
    source_asset_path: str
    destination_asset_path: str
    old_name: str
    new_name: str


@dataclass(frozen=True, slots=True)
class _LexicalPath:
    path: str
    stem: str
    extension: str


@dataclass(frozen=True, slots=True)
class RenderTextureParameters:
    width: int
    height: int
    depth: int
    format: str
    read_write: str
    filter_mode: str
    wrap_mode: str
    mip_map: bool

    def to_bridge_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "depth": self.depth,
            "format": self.format,
            "read_write": self.read_write,
            "filter_mode": self.filter_mode,
            "wrap_mode": self.wrap_mode,
            "mip_map": self.mip_map,
        }


def _envelope(
    *,
    success: bool,
    severity: str,
    code: str,
    message: str,
    data: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "severity": severity,
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": diagnostics or [],
    }


def _error(
    code: str,
    message: str,
    *,
    field: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(data or {})
    if field is not None:
        payload["field"] = field
    return _envelope(
        success=False,
        severity="error",
        code=code,
        message=message,
        data=payload,
    )


def validate_confirm_report_inputs(
    confirm: object,
    project_root: object | None,
    out_report: object | None,
    change_reason: object | None,
) -> ValidatedAuditContext | DryRunContext | dict[str, Any]:
    if type(confirm) is not bool:
        return _error(
            "INVALID_CONFIRM_VALUE",
            "confirm is required and must be a JSON bool.",
            field="confirm",
            data={"received_type": type(confirm).__name__},
        )
    if not confirm:
        return DryRunContext()

    if not isinstance(project_root, str) or not project_root:
        return _project_root_invalid("project_root must be an absolute directory path.")
    root_path = Path(project_root)
    if not root_path.is_absolute():
        return _project_root_invalid("project_root must be absolute.")
    try:
        resolved_root = root_path.resolve(strict=True)
    except OSError as exc:
        return _project_root_invalid(f"project_root could not be resolved: {exc}")
    if not resolved_root.is_dir():
        return _project_root_invalid("project_root must be an existing directory.")

    if not isinstance(out_report, str) or not out_report.strip():
        return _error(
            "OUT_REPORT_REQUIRED",
            "out_report is required when confirm=True.",
            field="out_report",
        )
    report_path = Path(out_report)
    if not report_path.is_absolute():
        return _error(
            "OUT_REPORT_INVALID",
            "out_report must be an absolute path inside project_root.",
            field="out_report",
            data={"reason": "absolute_path_required"},
        )
    if os.path.lexists(report_path):
        return _error(
            "OUT_REPORT_EXISTS",
            "out_report already exists.",
            field="out_report",
            data={"out_report": str(report_path)},
        )
    try:
        report_parent = report_path.parent.resolve(strict=True)
    except OSError:
        return _error(
            "OUT_REPORT_PARENT_NOT_FOUND",
            "out_report parent directory does not exist.",
            field="out_report",
            data={"parent": str(report_path.parent)},
        )
    if not report_parent.is_dir():
        return _error(
            "OUT_REPORT_PARENT_NOT_FOUND",
            "out_report parent must be an existing directory.",
            field="out_report",
            data={"parent": str(report_parent)},
        )
    resolved_report = report_parent / report_path.name
    if not resolved_report.is_relative_to(resolved_root):
        return _error(
            "OUT_REPORT_INVALID",
            "out_report must resolve inside project_root.",
            field="out_report",
            data={"reason": "outside_project_root"},
        )

    if not isinstance(change_reason, str) or not change_reason.strip():
        return _error(
            "CHANGE_REASON_REQUIRED",
            "change_reason is required when confirm=True.",
            field="change_reason",
        )
    reason = change_reason.strip()
    if len(reason) > _CHANGE_REASON_MAX:
        return _error(
            "CHANGE_REASON_TOO_LONG",
            "change_reason must be at most 1024 characters.",
            field="change_reason",
            data={"max": _CHANGE_REASON_MAX, "length": len(reason)},
        )
    return ValidatedAuditContext(
        project_root=resolved_root,
        out_report=resolved_report,
        change_reason=reason,
    )


def _project_root_invalid(message: str) -> dict[str, Any]:
    return _error("PROJECT_ROOT_INVALID", message, field="project_root")


def validate_generated_asset_path(asset_path: object) -> PathPlan | dict[str, Any]:
    result = _validate_asset_path(
        asset_path,
        field="asset_path",
        invalid_code="GENERATED_ASSET_INVALID_PATH",
        meta_code="GENERATED_ASSET_PATH_IS_META_FILE",
        require_destination_stem=True,
    )
    if isinstance(result, dict):
        return result
    if result.extension != _RENDER_TEXTURE_EXTENSION:
        return _path_error(
            "GENERATED_ASSET_INVALID_PATH",
            "asset_path",
            asset_path,
            "extension_mismatch",
            expected_extension=_RENDER_TEXTURE_EXTENSION,
        )
    return PathPlan(asset_path=result.path, name=result.stem)


def validate_move_asset_paths(
    source_asset_path: object,
    destination_asset_path: object,
) -> MovePathPlan | dict[str, Any]:
    source = _validate_asset_path(
        source_asset_path,
        field="source_asset_path",
        invalid_code="ASSET_SOURCE_INVALID_PATH",
        meta_code="ASSET_SOURCE_IS_META_FILE",
        require_destination_stem=False,
    )
    if isinstance(source, dict):
        return source

    case_only = _case_only_move_error(source_asset_path, destination_asset_path)
    if case_only is not None:
        return case_only

    destination = _validate_asset_path(
        destination_asset_path,
        field="destination_asset_path",
        invalid_code="ASSET_DESTINATION_INVALID_PATH",
        meta_code="ASSET_DESTINATION_IS_META_FILE",
        require_destination_stem=True,
    )
    if isinstance(destination, dict):
        return destination

    if source.extension != destination.extension:
        return _error(
            "ASSET_EXTENSION_MISMATCH",
            "source and destination extensions must match exactly.",
            data={
                "source_asset_path": source.path,
                "destination_asset_path": destination.path,
                "source_extension": source.extension,
                "destination_extension": destination.extension,
                "reason": "extension_mismatch",
            },
        )
    if source.path == destination.path:
        return _error(
            "ASSET_MOVE_SAME_PATH",
            "source_asset_path and destination_asset_path must differ.",
            data={
                "source_asset_path": source.path,
                "destination_asset_path": destination.path,
                "reason": "same_path",
            },
        )
    return MovePathPlan(
        source_asset_path=source.path,
        destination_asset_path=destination.path,
        old_name=source.stem,
        new_name=destination.stem,
    )


def _case_only_move_error(source_asset_path: object, destination_asset_path: object) -> dict[str, Any] | None:
    if not isinstance(source_asset_path, str) or not isinstance(destination_asset_path, str):
        return None
    if source_asset_path != destination_asset_path and source_asset_path.lower() == destination_asset_path.lower():
        return _error(
            "ASSET_MOVE_CASE_ONLY_RENAME_UNSUPPORTED",
            "case-only asset move is not supported.",
            data={
                "source_asset_path": source_asset_path,
                "destination_asset_path": destination_asset_path,
                "reason": "case_only_path",
            },
        )
    return None


def _validate_asset_path(
    asset_path: object,
    *,
    field: str,
    invalid_code: str,
    meta_code: str,
    require_destination_stem: bool,
) -> _LexicalPath | dict[str, Any]:
    if not isinstance(asset_path, str) or asset_path == "":
        return _path_error(invalid_code, field, asset_path, f"{field}_required")
    if "\0" in asset_path:
        return _path_error(invalid_code, field, asset_path, "nul_byte")
    if asset_path.startswith("/") or re.match(r"^[A-Za-z]:", asset_path):
        return _path_error(invalid_code, field, asset_path, "absolute_path")
    if "\\" in asset_path:
        return _path_error(invalid_code, field, asset_path, "backslash")
    if asset_path == "Assets":
        return _path_error(invalid_code, field, asset_path, "assets_root_not_asset")
    if not asset_path.startswith("Assets/"):
        return _path_error(invalid_code, field, asset_path, "must_start_with_assets")
    segments = asset_path.split("/")
    if asset_path.endswith("/") or any(segment == "" for segment in segments):
        return _path_error(invalid_code, field, asset_path, "empty_path_segment")
    if any(segment in {".", ".."} for segment in segments):
        return _path_error(invalid_code, field, asset_path, "dot_segment")
    if asset_path.endswith(".meta"):
        return _path_error(meta_code, field, asset_path, "meta_file_path")

    leaf = PurePosixPath(asset_path).name
    stem, extension = _stem_and_extension(leaf)
    if require_destination_stem and stem == "":
        return _path_error(invalid_code, field, asset_path, "asset_name_stem_required")
    return _LexicalPath(path=asset_path, stem=stem, extension=extension)


def _stem_and_extension(leaf: str) -> tuple[str, str]:
    index = leaf.rfind(".")
    if index < 0:
        return leaf, ""
    return leaf[:index], leaf[index:]


def _path_error(
    code: str,
    field: str,
    value: object,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    data = {"field": field, "value": value, "reason": reason}
    data.update(extra)
    return _error(
        code,
        f"{field} is invalid: {reason}.",
        field=field,
        data=data,
    )


def validate_render_texture_parameters(parameters: object) -> RenderTextureParameters | dict[str, Any]:
    if not isinstance(parameters, dict):
        return _parameter_error(
            field="parameters",
            value=parameters,
            expected_type="object",
        )
    keys = set(parameters)
    missing = sorted(_RENDER_TEXTURE_REQUIRED_KEYS - keys)
    unknown = sorted(keys - _RENDER_TEXTURE_ALLOWED_KEYS)
    if missing or unknown:
        data: dict[str, Any] = {
            "field": "parameters",
            "expected_type": "object",
        }
        if missing:
            data["missing_keys"] = missing
        if unknown:
            data["unknown_keys"] = unknown
        return _error(
            "GENERATED_ASSET_INVALID_PARAMETER",
            "RenderTexture parameters contain missing or unknown keys.",
            field="parameters",
            data=data,
        )

    values: dict[str, object] = dict(_RENDER_TEXTURE_OPTIONAL_DEFAULTS)
    values.update(parameters)

    width_value = values["width"]
    if type(width_value) is not int:
        return _parameter_error(field="width", value=width_value, expected_type="integer")
    width = cast(int, width_value)
    if width < _INT_MIN or width > _TEXTURE_SIZE_MAX:
        return _parameter_error(
            field="width",
            value=width,
            expected_type="integer",
            min=_INT_MIN,
            max=_TEXTURE_SIZE_MAX,
        )

    height_value = values["height"]
    if type(height_value) is not int:
        return _parameter_error(field="height", value=height_value, expected_type="integer")
    height = cast(int, height_value)
    if height < _INT_MIN or height > _TEXTURE_SIZE_MAX:
        return _parameter_error(
            field="height",
            value=height,
            expected_type="integer",
            min=_INT_MIN,
            max=_TEXTURE_SIZE_MAX,
        )

    depth_value = values["depth"]
    if type(depth_value) is not int:
        return _parameter_error(field="depth", value=depth_value, expected_type="integer")
    depth = cast(int, depth_value)
    if depth not in _DEPTH_VALUES:
        return _parameter_error(
            field="depth",
            value=depth,
            expected_type="integer",
            allowed_values=_DEPTH_VALUES,
        )

    format_value = values["format"]
    if not isinstance(format_value, str):
        return _parameter_error(field="format", value=format_value, expected_type="string")
    if format_value not in _FORMAT_VALUES:
        return _parameter_error(
            field="format",
            value=format_value,
            expected_type="string",
            allowed_values=_FORMAT_VALUES,
        )

    read_write_value = values["read_write"]
    if not isinstance(read_write_value, str):
        return _parameter_error(
            field="read_write", value=read_write_value, expected_type="string"
        )
    if read_write_value not in _READ_WRITE_VALUES:
        return _parameter_error(
            field="read_write",
            value=read_write_value,
            expected_type="string",
            allowed_values=_READ_WRITE_VALUES,
        )

    filter_mode_value = values["filter_mode"]
    if not isinstance(filter_mode_value, str):
        return _parameter_error(
            field="filter_mode", value=filter_mode_value, expected_type="string"
        )
    if filter_mode_value not in _FILTER_MODE_VALUES:
        return _parameter_error(
            field="filter_mode",
            value=filter_mode_value,
            expected_type="string",
            allowed_values=_FILTER_MODE_VALUES,
        )

    wrap_mode_value = values["wrap_mode"]
    if not isinstance(wrap_mode_value, str):
        return _parameter_error(
            field="wrap_mode", value=wrap_mode_value, expected_type="string"
        )
    if wrap_mode_value not in _WRAP_MODE_VALUES:
        return _parameter_error(
            field="wrap_mode",
            value=wrap_mode_value,
            expected_type="string",
            allowed_values=_WRAP_MODE_VALUES,
        )

    mip_map_value = values["mip_map"]
    if type(mip_map_value) is not bool:
        return _parameter_error(
            field="mip_map",
            value=mip_map_value,
            expected_type="boolean",
        )
    mip_map = cast(bool, mip_map_value)

    return RenderTextureParameters(
        width=width,
        height=height,
        depth=depth,
        format=format_value,
        read_write=read_write_value,
        filter_mode=filter_mode_value,
        wrap_mode=wrap_mode_value,
        mip_map=mip_map,
    )


def _parameter_error(
    *,
    field: str,
    value: object,
    expected_type: str,
    **extra: Any,
) -> dict[str, Any]:
    data = {
        "field": field,
        "value": value,
        "expected_type": expected_type,
    }
    data.update(extra)
    return _error(
        "GENERATED_ASSET_INVALID_PARAMETER",
        f"RenderTexture parameter {field!r} is invalid.",
        field=field,
        data=data,
    )


def editor_create_generated_asset(
    asset_type: object,
    asset_path: object,
    parameters: object,
    confirm: object,
    project_root: object | None = None,
    out_report: object | None = None,
    change_reason: object | None = None,
) -> dict[str, Any]:
    audit = validate_confirm_report_inputs(
        confirm, project_root, out_report, change_reason,
    )
    if isinstance(audit, dict):
        return audit

    if not isinstance(asset_type, str) or asset_type != _ASSET_TYPE:
        return _finalize_local_error(
            _error(
                "UNSUPPORTED_GENERATED_ASSET_TYPE",
                "Only render_texture generated assets are supported.",
                field="asset_type",
                data={"field": "asset_type", "value": asset_type},
            ),
            audit,
        )

    path_plan = validate_generated_asset_path(asset_path)
    if isinstance(path_plan, dict):
        return _finalize_local_error(path_plan, audit)
    parameter_plan = validate_render_texture_parameters(parameters)
    if isinstance(parameter_plan, dict):
        return _finalize_local_error(parameter_plan, audit)

    bridge_response = send_action(
        action="create_generated_asset",
        asset_type=asset_type,
        asset_path=path_plan.asset_path,
        parameters=parameter_plan.to_bridge_dict(),
        confirm=bool(confirm),
    )
    return finalize_asset_operation_response("create", bridge_response, audit)


def editor_move_asset(
    source_asset_path: object,
    destination_asset_path: object,
    confirm: object,
    project_root: object | None = None,
    out_report: object | None = None,
    change_reason: object | None = None,
) -> dict[str, Any]:
    audit = validate_confirm_report_inputs(
        confirm, project_root, out_report, change_reason,
    )
    if isinstance(audit, dict):
        return audit

    path_plan = validate_move_asset_paths(source_asset_path, destination_asset_path)
    if isinstance(path_plan, dict):
        return _finalize_local_error(path_plan, audit)

    bridge_response = send_action(
        action="move_asset",
        source_asset_path=path_plan.source_asset_path,
        destination_asset_path=path_plan.destination_asset_path,
        confirm=bool(confirm),
    )
    return finalize_asset_operation_response("move", bridge_response, audit)


def _finalize_local_error(
    response: dict[str, Any],
    audit: ValidatedAuditContext | DryRunContext,
) -> dict[str, Any]:
    if isinstance(audit, DryRunContext):
        return response
    return _write_confirm_report(response, audit)


def finalize_asset_operation_response(
    operation: str,
    bridge_response: dict[str, Any],
    audit: ValidatedAuditContext | DryRunContext,
) -> dict[str, Any]:
    shape_error = _validate_bridge_envelope_shape(bridge_response)
    if shape_error is not None:
        return _finalize_bridge_shape_error(shape_error, audit)

    diagnostics = _normalize_diagnostics(bridge_response["diagnostics"])
    if isinstance(diagnostics, dict):
        return _finalize_bridge_shape_error(diagnostics, audit)

    if bridge_response["success"] is False:
        result = dict(bridge_response)
        result["diagnostics"] = diagnostics
        if isinstance(audit, ValidatedAuditContext):
            return _write_confirm_report(result, audit)
        return result

    required = _required_success_fields(operation)
    data = bridge_response["data"]
    field_error = _validate_success_fields(data, required)
    if field_error is not None:
        return _finalize_bridge_shape_error(field_error, audit)

    result = _envelope(
        success=True,
        severity=bridge_response["severity"],
        code=bridge_response["code"],
        message=bridge_response["message"],
        data={field: data[field] for field in required},
        diagnostics=diagnostics,
    )
    if isinstance(audit, ValidatedAuditContext):
        return _write_confirm_report(result, audit)
    return result


def _validate_bridge_envelope_shape(
    response: object,
) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return _bridge_invalid("Bridge response root must be an object.")
    required = {
        "success": bool,
        "severity": str,
        "code": str,
        "message": str,
        "data": dict,
        "diagnostics": list,
    }
    for field, expected_type in required.items():
        if field not in response:
            return _bridge_invalid(f"Bridge response missing {field!r}.")
        value = response[field]
        if expected_type is bool:
            if type(value) is not bool:
                return _bridge_invalid(f"Bridge response {field!r} must be bool.")
        elif not isinstance(value, expected_type):
            return _bridge_invalid(
                f"Bridge response {field!r} must be {expected_type.__name__}."
            )
    return None


def _normalize_diagnostics(
    diagnostics: list[Any],
) -> list[dict[str, Any]] | dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            return _bridge_invalid("Bridge diagnostics must contain objects.")
        if {"severity", "code", "message", "data"} <= diagnostic.keys():
            diagnostic_data = diagnostic["data"]
            if not isinstance(diagnostic_data, dict):
                return _bridge_invalid("Bridge diagnostic data must be an object.")
            normalized.append({
                "severity": str(diagnostic["severity"]),
                "code": str(diagnostic["code"]),
                "message": str(diagnostic["message"]),
                "data": diagnostic_data,
            })
            continue
        if "detail" in diagnostic:
            legacy_data: dict[str, Any] = {}
            if diagnostic.get("path"):
                legacy_data["path"] = diagnostic["path"]
            if diagnostic.get("location"):
                legacy_data["location"] = diagnostic["location"]
            if "code" in diagnostic:
                code = str(diagnostic["code"])
                message = str(diagnostic.get("detail") or diagnostic.get("evidence") or code)
            else:
                code = str(diagnostic["detail"])
                message = str(diagnostic.get("evidence") or code)
            normalized.append({
                "severity": str(diagnostic.get("severity") or "warning"),
                "code": code,
                "message": message,
                "data": legacy_data,
            })
            continue
        return _bridge_invalid("Bridge diagnostic shape is invalid.")
    return normalized


def _required_success_fields(operation: str) -> dict[str, type | tuple[type, ...]]:
    if operation == "create":
        return _CREATE_SUCCESS_FIELDS
    if operation == "move":
        return _MOVE_SUCCESS_FIELDS
    raise ValueError(f"unknown asset operation: {operation}")


def _validate_success_fields(
    data: dict[str, Any],
    required: dict[str, type | tuple[type, ...]],
) -> dict[str, Any] | None:
    for field, expected_type in required.items():
        if field not in data:
            return _bridge_invalid(f"Bridge success data missing {field!r}.")
        value = data[field]
        if expected_type is bool:
            if type(value) is not bool:
                return _bridge_invalid(f"Bridge success data {field!r} must be bool.")
        elif not isinstance(value, expected_type):
            type_name = (
                expected_type.__name__
                if isinstance(expected_type, type)
                else " or ".join(item.__name__ for item in expected_type)
            )
            return _bridge_invalid(
                f"Bridge success data {field!r} must be {type_name}."
            )
    return None


def _bridge_invalid(message: str) -> dict[str, Any]:
    return _error(
        "UNITY_BRIDGE_INVALID_RESPONSE",
        message,
        data={"state_unknown": True},
    )


def _finalize_bridge_shape_error(
    error: dict[str, Any],
    audit: ValidatedAuditContext | DryRunContext,
) -> dict[str, Any]:
    error["diagnostics"] = [_partial_side_effect_diagnostic()]
    if isinstance(audit, ValidatedAuditContext):
        return _write_confirm_report(error, audit)
    return error


def _partial_side_effect_diagnostic() -> dict[str, Any]:
    return {
        "severity": "warning",
        "code": "PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW",
        "message": "Unity asset operation state is unknown and requires review.",
        "data": {},
    }


def _write_confirm_report(
    response: dict[str, Any],
    audit: ValidatedAuditContext,
) -> dict[str, Any]:
    final_response = _with_audit(response, audit, report_written=True)
    try:
        with open(audit.out_report, "x", encoding="utf-8") as handle:
            json.dump(final_response, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        return _report_write_failed(response, exc)
    return final_response


def _with_audit(
    response: dict[str, Any],
    audit: ValidatedAuditContext,
    *,
    report_written: bool,
) -> dict[str, Any]:
    result = dict(response)
    result["data"] = dict(response.get("data", {}))
    result["data"]["change_reason"] = audit.change_reason
    result["data"]["report_written"] = report_written
    result["data"]["out_report"] = str(audit.out_report)
    return result


def _report_write_failed(
    operation_response: dict[str, Any],
    exc: OSError,
) -> dict[str, Any]:
    key = "operation_result" if operation_response.get("success") else "operation_error"
    return _error(
        "OUT_REPORT_WRITE_FAILED",
        "Operation completed but the report file could not be written.",
        data={
            key: operation_response,
            "error": str(exc),
        },
    )


def register_editor_asset_tools(server: MCPServer) -> None:
    @server.tool()
    def editor_create_generated_asset(
        asset_type: object,
        asset_path: object,
        parameters: object,
        confirm: object,
        project_root: object | None = None,
        out_report: object | None = None,
        change_reason: object | None = None,
    ) -> dict[str, Any]:
        """Dry-run or create a validated RenderTexture asset through the Unity Editor."""
        create_tool = cast(
            Callable[..., dict[str, Any]],
            globals()["editor_create_generated_asset"],
        )
        return create_tool(
            asset_type=asset_type,
            asset_path=asset_path,
            parameters=parameters,
            confirm=confirm,
            project_root=project_root,
            out_report=out_report,
            change_reason=change_reason,
        )

    @server.tool()
    def editor_move_asset(
        source_asset_path: object,
        destination_asset_path: object,
        confirm: object,
        project_root: object | None = None,
        out_report: object | None = None,
        change_reason: object | None = None,
    ) -> dict[str, Any]:
        """Dry-run or move a Unity asset to a validated destination path."""
        move_tool = cast(
            Callable[..., dict[str, Any]],
            globals()["editor_move_asset"],
        )
        return move_tool(
            source_asset_path=source_asset_path,
            destination_asset_path=destination_asset_path,
            confirm=confirm,
            project_root=project_root,
            out_report=out_report,
            change_reason=change_reason,
        )
