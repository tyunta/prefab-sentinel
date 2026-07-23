from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.editor_status_blockers import classify_tool_error_blocker
from prefab_sentinel.inspector_profiles.model import (
    SelectedProfile,
    SerializedSurface,
    SurfaceProperty,
    TargetIdentity,
)
from prefab_sentinel.inspector_profiles.offline_identity import (
    OfflineTargetMetadata,
    inspect_offline_target_metadata,
)
from prefab_sentinel.inspector_profiles.rendering import render_requested_view
from prefab_sentinel.inspector_profiles.repository import (
    ProfileRepository,
    ProfileRepositoryError,
)
from prefab_sentinel.inspector_profiles.schema import validate_profile_document
from prefab_sentinel.inspector_profiles.validation import (
    ProfileValidationResult,
    validate_profile_against_surface,
)
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.session import ProjectSession
from prefab_sentinel.unity_assets_path import resolve_asset_path
from prefab_sentinel.wsl_compat import to_windows_path


def _response(
    success: bool,
    severity: str,
    code: str,
    message: str,
    data: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "success": success,
        "severity": severity,
        "code": code,
        "message": message,
        "data": data,
        "diagnostics": diagnostics,
    }


def _address_error(
    asset_path: str,
    symbol_path: str | None,
    project_root: Path | None,
) -> dict[str, Any] | None:
    if not asset_path:
        return _response(
            False,
            "error",
            "INSPECTOR_SURFACE_ADDRESS_INVALID",
            "asset_path is required.",
            {"field": "asset_path"},
            [],
        )
    path = PurePosixPath(asset_path)
    if path.is_absolute() or "\\" in asset_path or ".." in path.parts or not path.parts or path.parts[0] != "Assets":
        return _response(
            False,
            "error",
            "INSPECTOR_SURFACE_ADDRESS_INVALID",
            "asset_path must be a project-relative path below Assets.",
            {"field": "asset_path", "asset_path": asset_path},
            [],
        )
    if project_root is not None:
        candidate = project_root
        try:
            for segment in path.parts:
                candidate /= segment
                if candidate.is_symlink():
                    raise ValueError("symlink assets are not addressable")
            resolved = resolve_asset_path(asset_path, project_root)
        except (OSError, ValueError):
            return _response(
                False,
                "error",
                "INSPECTOR_SURFACE_ADDRESS_INVALID",
                "asset_path must resolve below the activated project's Assets directory.",
                {"field": "asset_path", "asset_path": asset_path},
                [],
            )
        if not resolved.is_relative_to((project_root / "Assets").resolve()):
            return _response(
                False,
                "error",
                "INSPECTOR_SURFACE_ADDRESS_INVALID",
                "asset_path must resolve below the activated project's Assets directory.",
                {"field": "asset_path", "asset_path": asset_path},
                [],
            )
    suffix = path.suffix.lower()
    if suffix in {".prefab", ".unity"} and not symbol_path:
        return _response(
            False,
            "error",
            "INSPECTOR_SURFACE_ADDRESS_INVALID",
            "symbol_path is required for component assets.",
            {"field": "symbol_path", "asset_path": asset_path},
            [],
        )
    if suffix == ".asset" and symbol_path:
        return _response(
            False,
            "error",
            "INSPECTOR_SURFACE_ADDRESS_INVALID",
            "symbol_path is not allowed for ScriptableObject assets.",
            {"field": "symbol_path", "asset_path": asset_path},
            [],
        )
    if suffix not in {".prefab", ".unity", ".asset"}:
        return _response(
            False,
            "error",
            "INSPECTOR_SURFACE_ADDRESS_INVALID",
            "asset_path must identify a Prefab, Scene, or ScriptableObject asset.",
            {"field": "asset_path", "asset_path": asset_path},
            [],
        )
    return None


def _bridge_diagnostic(response: dict[str, Any]) -> dict[str, Any]:
    raw_severity = response.get("severity")
    severity = raw_severity if raw_severity in {"info", "warning", "error", "critical"} else "error"
    raw_code = response.get("code")
    code = raw_code if isinstance(raw_code, str) and raw_code else "EDITOR_BRIDGE_FAILURE"
    blocker = classify_tool_error_blocker({"code": code, "message": "Editor Bridge request failed.", "data": {}})
    data = (
        {
            "blocker_class": blocker["blocker_class"],
            "suggested_next_action": blocker["suggested_next_action"],
        }
        if blocker is not None
        else {}
    )
    return {
        "severity": severity,
        "code": code,
        "message": "Editor Bridge request failed.",
        "data": data,
    }


def _target_identity(surface: dict[str, Any]) -> TargetIdentity:
    target = surface.get("target")
    if not isinstance(target, dict):
        raise ValueError("serialized surface target identity is missing")
    managed_type = target.get("managed_type")
    if not isinstance(managed_type, str) or not managed_type:
        raise ValueError("serialized surface managed_type is invalid")
    optional_strings: dict[str, str | None] = {}
    for field in ("assembly", "script_guid"):
        value = target.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"serialized surface {field} is invalid")
        optional_strings[field] = value
    script_file_id = target.get("script_file_id")
    if script_file_id is not None and (not isinstance(script_file_id, int) or isinstance(script_file_id, bool)):
        raise ValueError("serialized surface script_file_id is invalid")
    script_path = target.get("script_path")
    if isinstance(script_path, str):
        if not script_path:
            raise ValueError("serialized surface script_path is invalid")
    elif script_path is None:
        reasons = target.get("script_path_degradation_reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) and reason for reason in reasons)
        ):
            raise ValueError("serialized surface null script_path requires degradation reasons")
    else:
        raise ValueError("serialized surface script_path is invalid")
    return TargetIdentity(
        managed_type=managed_type,
        assembly=optional_strings["assembly"],
        script_guid=optional_strings["script_guid"],
        script_file_id=script_file_id,
    )


def _recommended_profile_path(project_root: Path, identity: TargetIdentity) -> Path:
    identity_payload = {
        "managed_type": identity.managed_type,
        "assembly": identity.assembly,
        "script_guid": identity.script_guid,
        "script_file_id": identity.script_file_id,
    }
    digest = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", identity.managed_type).strip("-").lower()
    return (project_root / ".prefab-sentinel" / "profiles" / f"{stem}-{digest}.json").relative_to(
        project_root
    )


def _selected_profile_path(project_root: Path, selected: SelectedProfile) -> str:
    if selected.source == "project":
        return selected.path.relative_to(project_root).as_posix()
    if selected.source == "bundled":
        return (Path("profiles") / selected.path.name).as_posix()
    raise ValueError(f"unsupported inspector profile source: {selected.source}")


def _surface_summary(surface: dict[str, Any]) -> dict[str, Any]:
    properties = surface.get("properties")
    if not isinstance(properties, list):
        raise ValueError("serialized surface properties are missing")
    roots = [item for item in properties if isinstance(item, dict) and ".Array.data[" not in str(item.get("path"))]
    return {
        "available": True,
        "field_count": len(roots),
        "array_candidates": [item["path"] for item in roots if item.get("array_size") is not None],
        "object_reference_fields": [item["path"] for item in roots if item.get("property_type") == "ObjectReference"],
    }


def _validate_source_candidate(candidate: dict[str, Any]) -> None:
    kind = candidate.get("kind")
    if kind == "runtime_component":
        managed_type = candidate.get("managed_type")
        if not isinstance(managed_type, str) or not managed_type:
            raise ValueError("runtime component candidate type is invalid")
        return
    if kind == "runtime_script":
        path = candidate.get("path")
        if isinstance(path, str) and path:
            return
        raise ValueError("runtime script candidate path is invalid")
    raise ValueError("source candidate kind is invalid")


def _validate_editor_candidate(candidate: dict[str, Any]) -> None:
    editor_type = candidate.get("type")
    if not isinstance(editor_type, str) or not editor_type:
        raise ValueError("custom editor candidate type is invalid")
    if not isinstance(candidate.get("active"), bool):
        raise ValueError("custom editor candidate active state is invalid")


def _candidate_diagnostics(surface: dict[str, Any]) -> dict[str, Any]:
    status = surface["source_candidates_status"]
    source_candidates = surface["source_candidates"]
    editor_candidates = surface["custom_editor_candidates"]
    if status not in {"complete", "degraded"}:
        raise ValueError("source candidate status must be complete or degraded")
    if not isinstance(source_candidates, list) or not all(
        isinstance(candidate, dict) for candidate in source_candidates
    ):
        raise ValueError("source candidates must be objects")
    if not isinstance(editor_candidates, list) or not all(
        isinstance(candidate, dict) for candidate in editor_candidates
    ):
        raise ValueError("custom editor candidates must be objects")

    for candidate in source_candidates:
        _validate_source_candidate(candidate)
    for candidate in editor_candidates:
        _validate_editor_candidate(candidate)

    bounded_sources: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, object]] = set()
    for candidate in source_candidates:
        kind = str(candidate["kind"])
        identity: object = (
            candidate["managed_type"]
            if kind == "runtime_component"
            else candidate.get("path")
        )
        key = (kind, identity)
        if key in seen_sources:
            continue
        seen_sources.add(key)
        bounded_sources.append(candidate)
        if len(bounded_sources) == 2:
            break

    active_editors = [candidate for candidate in editor_candidates if candidate["active"] is True]
    payload: dict[str, Any] = {
        "source_candidates_status": status,
        "source_candidates": bounded_sources,
        "custom_editor_candidates": active_editors[:1],
    }
    if status == "degraded":
        reasons = surface["source_candidates_reasons"]
        if (
            not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) and reason for reason in reasons)
        ):
            raise ValueError("degraded source candidate discovery requires reasons")
        payload["source_candidates_reasons"] = list(reasons)
    return payload


def _authoring_payload(
    project_root: Path,
    identity: TargetIdentity,
    surface: dict[str, Any],
    asset_path: str,
    symbol_path: str | None,
    include_override_origin: bool,
) -> dict[str, Any]:
    target_payload = dict(surface["target"])
    target_payload["address"] = {"asset_path": asset_path, "symbol_path": symbol_path}
    payload = {
        "required_skill": "prefab-sentinel:inspector-profile-authoring",
        "related_skills": ["prefab-sentinel:guide"],
        "profile_schema": "inspector_profile.v1",
        "validation_tool": "validate_inspector_profile",
        "recommended_profile_path": str(_recommended_profile_path(project_root, identity)),
        "target": target_payload,
        "surface_summary": _surface_summary(surface),
        "surface_ref": {
            "tool": "inspect_serialized_surface",
            "args": {
                "asset_path": asset_path,
                "symbol_path": symbol_path,
                "include_override_origin": include_override_origin,
            },
        },
        "property_drawer_candidates": [],
        "property_drawer_candidates_status": {
            "status": "degraded",
            "reasons": ["PropertyDrawer discovery is not available from the public Unity API boundary."],
        },
        "next_action": (
            "Use the required skill to author or repair a project-local inspector profile, "
            "then validate it with validate_inspector_profile."
        ),
    }
    payload.update(_candidate_diagnostics(surface))
    return payload


def _unavailable_authoring_payload(
    project_root: Path,
    metadata: OfflineTargetMetadata,
    asset_path: str,
    symbol_path: str | None,
    include_override_origin: bool,
) -> dict[str, Any]:
    address: dict[str, str] = {"asset_path": asset_path}
    arguments: dict[str, Any] = {
        "asset_path": asset_path,
        "include_override_origin": include_override_origin,
    }
    if symbol_path is not None:
        address["symbol_path"] = symbol_path
        arguments["symbol_path"] = symbol_path
    target = dict(metadata.target)
    target["address"] = address
    return {
        "required_skill": "prefab-sentinel:inspector-profile-authoring",
        "related_skills": ["prefab-sentinel:guide"],
        "profile_schema": "inspector_profile.v1",
        "validation_tool": "validate_inspector_profile",
        "recommended_profile_path": str(_recommended_profile_path(project_root, metadata.identity)),
        "target": target,
        "surface_summary": {"available": False},
        "surface_ref": {
            "tool": "inspect_serialized_surface",
            "args": arguments,
        },
        "source_candidates_status": "degraded",
        "source_candidates_reasons": ["Editor Bridge surface and candidate discovery are unavailable."],
        "source_candidates": [],
        "custom_editor_candidates": [],
        "property_drawer_candidates": [],
        "property_drawer_candidates_status": {
            "status": "degraded",
            "reasons": ["PropertyDrawer discovery is not available from the public Unity API boundary."],
        },
        "next_action": (
            "Use the required skill to author or repair a project-local inspector profile, "
            "then validate it with validate_inspector_profile."
        ),
    }


def _invalid_profile_response(
    payload: dict[str, Any],
    error: ProfileRepositoryError,
) -> dict[str, Any]:
    diagnostics = [{"path": diagnostic.path, "message": diagnostic.message} for diagnostic in error.diagnostics]
    if not diagnostics:
        diagnostics.append({"path": "$", "message": str(error)})
    return _response(
        False,
        "warning",
        "INSPECTOR_PROFILE_INVALID",
        "The matched inspector profile is invalid and cannot be used for the requested target.",
        payload,
        diagnostics,
    )


def _validate_object_reference_value(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("serialized ObjectReference value must be an object")
    if value.get("object_reference") is not True:
        raise ValueError("serialized ObjectReference marker is invalid")
    for field in ("guid", "asset_path", "object_type", "hierarchy_path"):
        if not isinstance(value.get(field), str):
            raise ValueError(f"serialized ObjectReference {field} is invalid")
    local_file_id = value.get("local_file_id")
    if not isinstance(local_file_id, int) or isinstance(local_file_id, bool):
        raise ValueError("serialized ObjectReference local_file_id is invalid")
    for field in ("null", "missing"):
        if not isinstance(value.get(field), bool):
            raise ValueError(f"serialized ObjectReference {field} status is invalid")
    if value["null"] and value["missing"]:
        raise ValueError("serialized ObjectReference status is contradictory")


def _serialized_surface(
    surface: dict[str, Any],
    identity: TargetIdentity,
) -> SerializedSurface:
    raw_target = surface.get("target")
    if not isinstance(raw_target, dict):
        raise ValueError("serialized surface target is missing")
    raw_local_file_id = raw_target.get("local_file_id")
    if raw_local_file_id is not None and (
        not isinstance(raw_local_file_id, int) or isinstance(raw_local_file_id, bool)
    ):
        raise ValueError("serialized surface target local_file_id is invalid")

    raw_properties = surface.get("properties")
    if not isinstance(raw_properties, list):
        raise ValueError("serialized surface properties are missing")
    properties: list[SurfaceProperty] = []
    for item in raw_properties:
        if not isinstance(item, dict):
            raise ValueError("serialized surface property must be an object")
        path = item.get("path")
        property_type = item.get("property_type")
        if not isinstance(path, str) or not path:
            raise ValueError("serialized surface property path is invalid")
        if not isinstance(property_type, str) or not property_type:
            raise ValueError("serialized surface property_type is invalid")
        if "source_value" not in item or "effective_value" not in item:
            raise ValueError("serialized surface property values are missing")
        source_value = item["source_value"]
        effective_value = item["effective_value"]
        if property_type == "ObjectReference":
            _validate_object_reference_value(source_value)
            _validate_object_reference_value(effective_value)
        origin = item.get("origin")
        if origin is not None and not isinstance(origin, dict):
            raise ValueError("serialized surface property origin must be an object or null")
        array_size = item.get("array_size")
        if array_size is not None and (
            not isinstance(array_size, int) or isinstance(array_size, bool) or array_size < 0
        ):
            raise ValueError("serialized surface property array_size is invalid")
        element_type = item.get("element_type")
        if element_type is not None and (not isinstance(element_type, str) or not element_type):
            raise ValueError("serialized surface property element_type is invalid")
        properties.append(
            SurfaceProperty(
                path=path,
                property_type=property_type,
                source_value=source_value,
                effective_value=effective_value,
                origin=origin,
                array_size=array_size,
                element_type=element_type,
            )
        )
    local_file_id = str(raw_local_file_id) if raw_local_file_id is not None else None
    return SerializedSurface(identity, tuple(properties), local_file_id)


def _mismatch_warnings(validation: ProfileValidationResult) -> list[dict[str, Any]]:
    return [
        {
            "code": "INSPECTOR_ZIPPED_ARRAY_LENGTH_MISMATCH",
            **mismatch.to_dict(),
        }
        for mismatch in validation.length_mismatches
    ]


def _writer_target_address(
    asset_path: str,
    symbol_path: str | None,
    local_file_id: str | None,
) -> dict[str, str] | None:
    if local_file_id is None or not local_file_id.strip():
        return None
    suffix = PurePosixPath(asset_path).suffix.lower()
    if suffix == ".prefab" and symbol_path is not None:
        return {"file_id": local_file_id}
    if suffix == ".asset" and symbol_path is None:
        return {"target": "$asset"}
    return None


def _writable_probe_ops(
    operation: str,
    paths: tuple[str, ...],
    properties: dict[str, SurfaceProperty],
    target_address: dict[str, str],
) -> list[dict[str, object]] | None:
    ops: list[dict[str, object]] = []
    for path in paths:
        property_value = properties.get(path)
        if property_value is None:
            return None
        if operation == "set":
            ops.append(
                {
                    "resource": "target",
                    "op": "set",
                    **target_address,
                    "path": path,
                    "value": property_value.effective_value,
                }
            )
            continue

        array_size = property_value.array_size
        if array_size is None or array_size < 1:
            return None
        array_path = f"{path}.Array.data"
        row_index = 0 if operation == "set_element" else array_size - 1

        if operation == "remove_row":
            ops.append(
                {
                    "resource": "target",
                    "op": "remove_array_element",
                    **target_address,
                    "path": array_path,
                    "index": row_index,
                }
            )
            continue

        row_path = f"{array_path}[{row_index}]"
        row_value = properties.get(row_path)
        if row_value is None:
            return None
        if operation == "set_element":
            ops.append(
                {
                    "resource": "target",
                    "op": "set",
                    **target_address,
                    "path": row_path,
                    "value": row_value.effective_value,
                }
            )
            continue
        if operation == "append_row":
            ops.append(
                {
                    "resource": "target",
                    "op": "insert_array_element",
                    **target_address,
                    "path": array_path,
                    "index": array_size,
                    "value": row_value.effective_value,
                }
            )
            continue
        return None
    return ops


class ProfileFileError(ValueError):
    pass


def _schema_invalid_profile_response(
    profile_path: Path,
    profile: dict[str, Any],
) -> dict[str, Any] | None:
    diagnostics = validate_profile_document(profile)
    if not diagnostics:
        return None
    return _response(
        False,
        "warning",
        "INSPECTOR_PROFILE_INVALID",
        "The inspector profile failed mechanical validation.",
        {
            "profile_path": profile_path.as_posix(),
            "valid": False,
            "confidence": profile.get("confidence"),
            "evidence": profile.get("evidence"),
            "length_mismatches": [],
            "writable": {},
        },
        [{"path": item.path, "message": item.message} for item in diagnostics],
    )


def _load_explicit_profile(
    project_root: Path,
    profile_path: str,
) -> tuple[Path, dict[str, Any]]:
    requested = Path(profile_path)
    candidate = requested if requested.is_absolute() else project_root / requested
    try:
        project_boundary = project_root.resolve(strict=True)
        relative_candidate = candidate.relative_to(project_root)
        if ".." in relative_candidate.parts:
            raise ValueError("profile path traversal is not allowed")
        current = project_root
        for segment in relative_candidate.parts:
            current /= segment
            if current.is_symlink():
                raise ValueError("profile path contains a symlink")
        resolved = candidate.resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise ProfileFileError("profile path could not be resolved") from exc
    if not resolved.is_relative_to(project_boundary):
        raise ProfileFileError("profile path is outside the activated project")
    try:
        is_file = candidate.is_file()
    except OSError as exc:
        raise ProfileFileError("profile path status could not be read") from exc
    if candidate.suffix.lower() != ".json" or not is_file:
        raise ProfileFileError("profile must be a regular non-symlink JSON file")
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileFileError("profile JSON could not be read") from exc
    if not isinstance(document, dict):
        raise ProfileFileError("profile JSON root must be an object")
    return resolved.relative_to(project_boundary), document


def _unsafe_profile_response(message: str) -> dict[str, Any]:
    return _response(
        False,
        "warning",
        "INSPECTOR_PROFILE_INVALID",
        "The inspector profile path is unsafe.",
        {"field": "profile_path"},
        [
            {
                "severity": "error",
                "code": "INSPECTOR_PROFILE_PATH_UNSAFE",
                "message": message,
                "data": {"field": "profile_path"},
            }
        ],
    )


class InspectorProfileApplication:
    def __init__(self, session: ProjectSession) -> None:
        self._session = session

    def _addressability_checker(
        self,
        asset_path: str,
        symbol_path: str | None,
        surface: SerializedSurface,
    ) -> Callable[[str, tuple[str, ...]], bool]:
        properties = surface.property_map()
        target_address = _writer_target_address(
            asset_path,
            symbol_path,
            surface.local_file_id,
        )
        results: dict[tuple[str, tuple[str, ...]], bool] = {}

        def addressable(operation: str, paths: tuple[str, ...]) -> bool:
            key = (operation, paths)
            if key not in results:
                ops = (
                    _writable_probe_ops(
                        operation,
                        paths,
                        properties,
                        target_address,
                    )
                    if target_address is not None
                    else None
                )
                if not ops:
                    results[key] = False
                else:
                    plan: dict[str, object] = {
                        "plan_version": PLAN_VERSION,
                        "resources": [
                            {
                                "id": "target",
                                "path": asset_path,
                                "mode": "open",
                            }
                        ],
                        "ops": ops,
                    }
                    response = (
                        self._session.get_orchestrator()
                        .serialized_value_patch_apply(
                            plan=plan,
                            dry_run=True,
                            confirm=False,
                            change_reason=None,
                        )
                    )
                    results[key] = response.success
            return results[key]

        return addressable

    def inspect_serialized_surface(
        self,
        asset_path: str,
        symbol_path: str | None,
        include_override_origin: bool,
    ) -> dict[str, Any]:
        project_root = self._session.project_root
        if project_root is None:
            return _response(
                False,
                "error",
                "PROJECT_NOT_ACTIVATED",
                "Activate a Unity project before inspecting a serialized surface.",
                {},
                [],
            )
        address_error = _address_error(asset_path, symbol_path, project_root)
        if address_error is not None:
            return address_error
        request: dict[str, Any] = {
            "action": "editor_inspect_serialized_surface",
            "asset_path": asset_path,
            "include_override_origin": include_override_origin,
            "expected_project_root": to_windows_path(str(project_root)),
        }
        if symbol_path is not None:
            request["symbol_path"] = symbol_path
        bridge_response = send_action(**request)
        if (
            bridge_response.get("success") is False
            and bridge_response.get("code") == "EDITOR_CTRL_INSPECTOR_TARGET_NOT_FOUND"
        ):
            address: dict[str, str] = {"asset_path": asset_path}
            if symbol_path is not None:
                address["symbol_path"] = symbol_path
            return _response(
                False,
                "error",
                "INSPECTOR_SURFACE_TARGET_NOT_FOUND",
                "The requested serialized target was not found.",
                {"address": address},
                [],
            )
        if bridge_response.get("success") is not True:
            return _response(
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                "The last-saved serialized surface is unavailable because the Editor Bridge could not be used.",
                {},
                [_bridge_diagnostic(bridge_response)],
            )
        return self._expand_surface_response(bridge_response)

    def inspect_with_profile(
        self,
        asset_path: str,
        view_name: str,
        symbol_path: str | None,
        include_override_origin: bool,
    ) -> dict[str, Any]:
        project_root = self._session.project_root
        if project_root is None:
            return _response(
                False,
                "error",
                "PROJECT_NOT_ACTIVATED",
                "Activate a Unity project before inspecting with a profile.",
                {},
                [],
            )
        address_error = _address_error(asset_path, symbol_path, project_root)
        if address_error is not None:
            return address_error

        raw_response = self.inspect_serialized_surface(
            asset_path,
            symbol_path,
            include_override_origin,
        )
        bundled_root = Path(str(files("prefab_sentinel").joinpath("resources", "profiles")))
        repository = ProfileRepository(project_root, bundled_root)
        if not raw_response["success"]:
            offline = inspect_offline_target_metadata(
                self._session,
                asset_path,
                symbol_path,
            )
            if offline is None:
                return raw_response
            try:
                offline_selected = repository.select_plausible_for_offline(offline.identity)
            except ProfileRepositoryError:
                return raw_response
            if offline_selected is not None:
                return raw_response
            payload = _unavailable_authoring_payload(
                project_root,
                offline,
                asset_path,
                symbol_path,
                include_override_origin,
            )
            return _response(
                False,
                "info",
                "INSPECTOR_PROFILE_REQUIRED",
                "No inspector profile exists for the requested target.",
                payload,
                list(raw_response["diagnostics"]),
            )

        surface = raw_response["data"]["surface"]
        identity = _target_identity(surface)
        try:
            selected = repository.select(identity)
        except ProfileRepositoryError as error:
            payload = _authoring_payload(
                project_root,
                identity,
                surface,
                asset_path,
                symbol_path,
                include_override_origin,
            )
            return _invalid_profile_response(payload, error)
        if selected is not None:
            return self._inspect_selected_profile(
                project_root,
                selected,
                identity,
                surface,
                asset_path,
                view_name,
                symbol_path,
                include_override_origin,
            )
        payload = _authoring_payload(
            project_root,
            identity,
            surface,
            asset_path,
            symbol_path,
            include_override_origin,
        )
        return _response(
            False,
            "info",
            "INSPECTOR_PROFILE_REQUIRED",
            "No inspector profile exists for the requested target.",
            payload,
            [],
        )

    def _inspect_selected_profile(
        self,
        project_root: Path,
        selected: SelectedProfile,
        identity: TargetIdentity,
        raw_surface: dict[str, Any],
        asset_path: str,
        view_name: str,
        symbol_path: str | None,
        include_override_origin: bool,
    ) -> dict[str, Any]:
        profile = selected.document()
        surface = _serialized_surface(raw_surface, identity)
        validation = validate_profile_against_surface(
            profile,
            identity,
            surface,
            self._addressability_checker(asset_path, symbol_path, surface),
        )
        authoring = _authoring_payload(
            project_root,
            identity,
            raw_surface,
            asset_path,
            symbol_path,
            include_override_origin,
        )
        if not validation.valid:
            diagnostics = [{"path": item.path, "message": item.message} for item in validation.diagnostics]
            return _response(
                False,
                "warning",
                "INSPECTOR_PROFILE_INVALID",
                "The matched inspector profile is invalid and cannot be used for the requested target.",
                authoring,
                diagnostics,
            )
        warnings = _mismatch_warnings(validation)
        available_views = [view["name"] for view in profile["views"]]
        if view_name not in available_views:
            return _response(
                False,
                "info",
                "INSPECTOR_PROFILE_INCOMPLETE",
                "The matched inspector profile does not define the requested view.",
                {**authoring, "available_views": available_views, "warnings": warnings},
                [],
            )
        rendered = render_requested_view(
            profile,
            surface,
            view_name,
            include_override_origin,
            validation.writable_for(view_name),
        )
        requested_mismatch = any(mismatch.view_name == view_name for mismatch in validation.length_mismatches)
        return _response(
            True,
            "warning" if requested_mismatch else "info",
            "INSPECTOR_PROFILE_VIEW_OK",
            "The requested inspector profile view was rendered.",
            {
                "profile_source": selected.source,
                "profile_path": _selected_profile_path(project_root, selected),
                "profile_warning": selected.warning,
                **rendered,
                "warnings": warnings,
            },
            [],
        )

    def validate_inspector_profile(
        self,
        profile_path: str,
        asset_path: str,
        symbol_path: str | None,
    ) -> dict[str, Any]:
        if self._session.project_root is None:
            return _response(
                False,
                "error",
                "PROJECT_NOT_ACTIVATED",
                "Activate a Unity project before validating an inspector profile.",
                {},
                [],
            )
        try:
            profile_identifier, profile = _load_explicit_profile(
                self._session.project_root,
                profile_path,
            )
        except ProfileFileError as exc:
            return _unsafe_profile_response(str(exc))
        address_error = _address_error(
            asset_path,
            symbol_path,
            self._session.project_root,
        )
        if address_error is not None:
            return address_error
        schema_error = _schema_invalid_profile_response(profile_identifier, profile)
        if schema_error is not None:
            return schema_error
        raw_response = self.inspect_serialized_surface(asset_path, symbol_path, False)
        if not raw_response["success"]:
            return raw_response
        raw_surface = raw_response["data"]["surface"]
        identity = _target_identity(raw_surface)
        surface = _serialized_surface(raw_surface, identity)
        validation = validate_profile_against_surface(
            profile,
            identity,
            surface,
            self._addressability_checker(asset_path, symbol_path, surface),
        )
        diagnostics = [{"path": item.path, "message": item.message} for item in validation.diagnostics]
        mismatches = [item.to_dict() for item in validation.length_mismatches]
        writable = {
            view["name"]: validation.writable_for(view["name"])
            for view in profile.get("views", [])
            if isinstance(view, dict) and isinstance(view.get("name"), str)
        }
        if not validation.valid:
            return _response(
                False,
                "warning",
                "INSPECTOR_PROFILE_INVALID",
                "The inspector profile failed mechanical validation.",
                {
                    "profile_path": profile_identifier.as_posix(),
                    "valid": False,
                    "confidence": profile.get("confidence"),
                    "evidence": profile.get("evidence"),
                    "length_mismatches": mismatches,
                    "writable": writable,
                },
                diagnostics,
            )
        return _response(
            True,
            "warning" if mismatches else "info",
            "INSPECTOR_PROFILE_VALIDATION_RESULT",
            "The inspector profile is mechanically valid.",
            {
                "profile_path": profile_identifier.as_posix(),
                "valid": True,
                "confidence": profile["confidence"],
                "evidence": profile["evidence"],
                "length_mismatches": mismatches,
                "writable": writable,
            },
            [],
        )

    @staticmethod
    def _expand_surface_response(bridge_response: dict[str, Any]) -> dict[str, Any]:
        try:
            data = bridge_response.get("data")
            if not isinstance(data, dict):
                raise ValueError("serialized surface response data is not an object")
            raw = data.get("serialized_surface_json")
            surface = json.loads(raw) if isinstance(raw, str) else None
            if not isinstance(surface, dict):
                raise ValueError("serialized surface payload is not an object")
            identity = _target_identity(surface)
            _serialized_surface(surface, identity).property_map()
            _candidate_diagnostics(surface)
        except (KeyError, TypeError, ValueError):
            return _response(
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                "The last-saved serialized surface is unavailable because the Editor Bridge could not be used.",
                {},
                [
                    {
                        "severity": "error",
                        "code": "EDITOR_BRIDGE_RESPONSE_SCHEMA",
                        "message": "Editor Bridge returned an invalid serialized surface payload.",
                        "data": {},
                    }
                ],
            )
        return _response(
            True,
            "info",
            "INSPECTOR_SERIALIZED_SURFACE_OK",
            "The last-saved serialized surface was inspected.",
            {"surface": surface},
            [],
        )
