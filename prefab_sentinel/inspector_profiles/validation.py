from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from prefab_sentinel.inspector_profiles.model import (
    SerializedSurface,
    SurfaceProperty,
    TargetIdentity,
    identity_match_priority,
)
from prefab_sentinel.inspector_profiles.schema import ProfileDiagnostic, validate_profile_document


@dataclass(frozen=True, slots=True)
class ArrayLengthMismatch:
    view_name: str
    lengths: dict[str, int] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {"view_name": self.view_name, "lengths": dict(self.lengths)}


@dataclass(frozen=True, slots=True)
class ProfileValidationResult:
    diagnostics: tuple[ProfileDiagnostic, ...]
    length_mismatches: tuple[ArrayLengthMismatch, ...]
    _writable: dict[str, dict[str, Any]] = field(repr=False)

    @property
    def valid(self) -> bool:
        return not self.diagnostics

    def writable_for(self, view_name: str) -> dict[str, Any]:
        return deepcopy(self._writable[view_name])


def _validate_fields_view(
    view: dict[str, Any],
    view_index: int,
    properties: dict[str, SurfaceProperty],
) -> tuple[list[ProfileDiagnostic], tuple[str, ...]]:
    diagnostics: list[ProfileDiagnostic] = []
    paths: list[str] = []
    for field_index, field_definition in enumerate(view["fields"]):
        path = field_definition["path"]
        paths.append(path)
        property_value = properties.get(path)
        diagnostic_path = f"$.views[{view_index}].fields[{field_index}]"
        if property_value is None:
            diagnostics.append(
                ProfileDiagnostic(
                    f"{diagnostic_path}.path",
                    f"serialized property does not exist: {path}",
                )
            )
            continue
        expected_type = field_definition.get("expected_type")
        if expected_type is not None and property_value.property_type != expected_type:
            diagnostics.append(
                ProfileDiagnostic(
                    f"{diagnostic_path}.expected_type",
                    f"expected {expected_type}, observed {property_value.property_type}",
                )
            )
            continue
        if property_value.property_type == "ObjectReference":
            diagnostics.extend(
                _validate_reference_payload(
                    property_value,
                    f"{diagnostic_path}.path",
                )
            )
    return diagnostics, tuple(paths)


def _element_type_matches(expected: str, observed: str | None) -> bool:
    """Match profile SerializedProperty types against Unity arrayElementType values."""
    if observed == expected:
        return True
    return (
        expected == "ObjectReference"
        and observed is not None
        and observed.startswith("PPtr<")
        and observed.endswith(">")
    )


def _validate_zipped_view(
    view: dict[str, Any],
    view_index: int,
    properties: dict[str, SurfaceProperty],
) -> tuple[list[ProfileDiagnostic], tuple[str, ...], ArrayLengthMismatch | None]:
    diagnostics: list[ProfileDiagnostic] = []
    paths: list[str] = []
    lengths: dict[str, int] = {}
    for array_index, array_definition in enumerate(view["arrays"]):
        path = array_definition["path"]
        paths.append(path)
        property_value = properties.get(path)
        diagnostic_path = f"$.views[{view_index}].arrays[{array_index}]"
        if property_value is None:
            diagnostics.append(
                ProfileDiagnostic(f"{diagnostic_path}.path", f"serialized property does not exist: {path}")
            )
            continue
        if property_value.array_size is None or property_value.array_size < 0:
            diagnostics.append(
                ProfileDiagnostic(f"{diagnostic_path}.path", f"serialized property is not an array: {path}")
            )
            continue
        expected_type = array_definition.get("element_type")
        expected_type_matches = expected_type is None or _element_type_matches(
            expected_type,
            property_value.element_type,
        )
        if not expected_type_matches:
            diagnostics.append(
                ProfileDiagnostic(
                    f"{diagnostic_path}.element_type",
                    f"expected {expected_type}, observed {property_value.element_type}",
                )
            )
        elif _element_type_matches("ObjectReference", property_value.element_type):
            for index in range(property_value.array_size):
                element_path = f"{path}.Array.data[{index}]"
                element = properties.get(element_path)
                if element is None:
                    diagnostics.append(
                        ProfileDiagnostic(
                            f"{diagnostic_path}.path",
                            f"serialized property does not exist: {element_path}",
                        )
                    )
                    continue
                diagnostics.extend(
                    _validate_reference_payload(
                        element,
                        f"{diagnostic_path}.path",
                    )
                )
        lengths[path] = property_value.array_size
    mismatch = None
    if not diagnostics and len(set(lengths.values())) > 1:
        mismatch = ArrayLengthMismatch(view["name"], lengths)
    return diagnostics, tuple(paths), mismatch


def _validate_reference_payload(
    prop: SurfaceProperty,
    diagnostic_path: str,
) -> list[ProfileDiagnostic]:
    if prop.property_type != "ObjectReference":
        return [
            ProfileDiagnostic(
                diagnostic_path,
                f"expected ObjectReference, observed {prop.property_type}",
            )
        ]
    if not isinstance(prop.effective_value, Mapping):
        return [ProfileDiagnostic(diagnostic_path, "ObjectReference payload is malformed")]
    if prop.effective_value.get("missing") is True:
        return [ProfileDiagnostic(diagnostic_path, f"ObjectReference is missing: {prop.path}")]
    return []


def _validate_reference_view(
    view: dict[str, Any],
    view_index: int,
    properties: dict[str, SurfaceProperty],
) -> tuple[list[ProfileDiagnostic], tuple[str, ...]]:
    diagnostics: list[ProfileDiagnostic] = []
    paths: list[str] = []
    for reference_index, reference in enumerate(view["references"]):
        path = reference["path"]
        paths.append(path)
        prop = properties.get(path)
        diagnostic_path = f"$.views[{view_index}].references[{reference_index}].path"
        if prop is None:
            diagnostics.append(
                ProfileDiagnostic(diagnostic_path, f"serialized property does not exist: {path}")
            )
            continue
        if prop.array_size is None:
            diagnostics.extend(_validate_reference_payload(prop, diagnostic_path))
            continue
        if prop.array_size < 0 or not _element_type_matches("ObjectReference", prop.element_type):
            diagnostics.append(
                ProfileDiagnostic(diagnostic_path, f"serialized property is not an ObjectReference array: {path}")
            )
            continue
        for index in range(prop.array_size):
            element_path = f"{path}.Array.data[{index}]"
            element = properties.get(element_path)
            if element is None:
                diagnostics.append(
                    ProfileDiagnostic(diagnostic_path, f"serialized property does not exist: {element_path}")
                )
                continue
            diagnostics.extend(_validate_reference_payload(element, diagnostic_path))
    return diagnostics, tuple(paths)


def _writable_state(
    view: dict[str, Any],
    view_index: int,
    paths: tuple[str, ...],
    view_valid: bool,
    addressable: Callable[[str, tuple[str, ...]], bool],
) -> tuple[dict[str, Any], list[ProfileDiagnostic]]:
    declaration = view.get("writable")
    if declaration is None or not declaration["enabled"]:
        return {"enabled": False}, []
    if not view_valid:
        return {"enabled": False}, []
    rejected_operations = tuple(
        operation
        for operation in declaration["operations"]
        if not addressable(operation, paths)
    )
    if rejected_operations:
        diagnostics = [
            ProfileDiagnostic(
                f"$.views[{view_index}].writable",
                f"writable path is not addressable: {path}",
            )
            for _operation in rejected_operations
            for path in paths
        ]
        return {"enabled": False}, diagnostics
    return deepcopy(declaration), []


def _view_mechanics(
    view: dict[str, Any],
    view_index: int,
    properties: dict[str, SurfaceProperty],
) -> tuple[list[ProfileDiagnostic], tuple[str, ...], ArrayLengthMismatch | None]:
    if view["kind"] == "fields":
        diagnostics, paths = _validate_fields_view(view, view_index, properties)
        return diagnostics, paths, None
    if view["kind"] == "zipped_arrays":
        return _validate_zipped_view(view, view_index, properties)
    diagnostics, paths = _validate_reference_view(view, view_index, properties)
    return diagnostics, paths, None


def _validate_all_views(
    views: list[dict[str, Any]],
    properties: dict[str, SurfaceProperty],
    addressable: Callable[[str, tuple[str, ...]], bool],
) -> tuple[list[ProfileDiagnostic], list[ArrayLengthMismatch], dict[str, dict[str, Any]]]:
    diagnostics: list[ProfileDiagnostic] = []
    mismatches: list[ArrayLengthMismatch] = []
    writable: dict[str, dict[str, Any]] = {}
    seen_names: set[str] = set()
    for view_index, view in enumerate(views):
        if view["name"] in seen_names:
            diagnostics.append(
                ProfileDiagnostic(f"$.views[{view_index}].name", f"duplicate view name: {view['name']}")
            )
        seen_names.add(view["name"])
        view_diagnostics, paths, mismatch = _view_mechanics(view, view_index, properties)
        diagnostics.extend(view_diagnostics)
        if mismatch is not None:
            mismatches.append(mismatch)
        writable_state, writable_diagnostics = _writable_state(
            view,
            view_index,
            paths,
            not view_diagnostics and mismatch is None,
            addressable,
        )
        writable[view["name"]] = writable_state
        diagnostics.extend(writable_diagnostics)
    return diagnostics, mismatches, writable


def validate_profile_against_surface(
    profile: dict[str, Any],
    identity: TargetIdentity,
    surface: SerializedSurface,
    addressable: Callable[[str, tuple[str, ...]], bool],
) -> ProfileValidationResult:
    schema_diagnostics = validate_profile_document(profile)
    if schema_diagnostics:
        views = profile.get("views")
        writable = (
            {
                view["name"]: {"enabled": False}
                for view in views
                if isinstance(view, dict) and isinstance(view.get("name"), str)
            }
            if isinstance(views, list)
            else {}
        )
        return ProfileValidationResult(schema_diagnostics, (), writable)
    if surface.target != identity:
        raise ValueError("serialized surface target does not match the requested target")
    diagnostics: list[ProfileDiagnostic] = []
    if identity_match_priority(profile["target"], identity) is None:
        diagnostics.append(ProfileDiagnostic("$.target", "profile target does not match the current target"))
    view_diagnostics, mismatches, writable = _validate_all_views(
        profile["views"],
        surface.property_map(),
        addressable,
    )
    diagnostics.extend(view_diagnostics)
    return ProfileValidationResult(tuple(diagnostics), tuple(mismatches), writable)
