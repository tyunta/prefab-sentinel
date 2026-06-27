"""Declarative material validation rule loading."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import Diagnostic

__all__ = [
    "FolderPolicy",
    "MaterialValidationRules",
    "MaterialValidationRulesLoadResult",
    "ShaderNamePolicy",
    "SharedMaterialGroup",
    "load_material_validation_rules",
]

_CONFIG_RELATIVE_PATH = Path("config") / "material_validation_rules.json"
_INVALID_CODE = "MATERIAL_RULES_INVALID"


@dataclass(frozen=True, slots=True)
class ShaderNamePolicy:
    id: str
    scope: str
    hierarchy_prefix: str
    expected_shader: str


@dataclass(frozen=True, slots=True)
class SharedMaterialGroup:
    id: str
    scope: str
    hierarchy_prefix: str
    expected_material: str | None = None


@dataclass(frozen=True, slots=True)
class FolderPolicy:
    id: str
    folder: str
    disallowed_extensions: tuple[str, ...]
    disallowed_asset_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterialValidationRules:
    config_status: str
    config_path: Path | None
    shader_name_policies: tuple[ShaderNamePolicy, ...]
    shared_material_groups: tuple[SharedMaterialGroup, ...]
    folder_policies: tuple[FolderPolicy, ...]

    @classmethod
    def empty(cls, config_path: Path | None) -> MaterialValidationRules:
        return cls(
            config_status="absent",
            config_path=config_path,
            shader_name_policies=(),
            shared_material_groups=(),
            folder_policies=(),
        )


@dataclass(frozen=True, slots=True)
class MaterialValidationRulesLoadResult:
    status: str
    rules: MaterialValidationRules | None
    diagnostics: tuple[Diagnostic, ...]
    config_path: Path


class _RulesSchemaError(ValueError):
    pass


def load_material_validation_rules(
    project_root: Path,
) -> MaterialValidationRulesLoadResult:
    config_path = project_root / _CONFIG_RELATIVE_PATH
    try:
        if config_path.is_symlink():
            return _invalid_result(config_path, "config path must not be a symlink")
        if not config_path.exists():
            return MaterialValidationRulesLoadResult(
                status="absent",
                rules=MaterialValidationRules.empty(config_path),
                diagnostics=(),
                config_path=config_path,
            )
        resolved_config_path = config_path.resolve()
        resolved_config_path.relative_to(project_root.resolve())
    except (OSError, RuntimeError) as exc:
        return _invalid_result(config_path, str(exc))
    except ValueError:
        return _invalid_result(config_path, "config path resolves outside project root")

    try:
        raw = json.loads(resolved_config_path.read_text(encoding="utf-8"))
        rules = _parse_rules(raw, config_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _RulesSchemaError) as exc:
        return _invalid_result(config_path, str(exc))

    return MaterialValidationRulesLoadResult(
        status="loaded",
        rules=rules,
        diagnostics=(),
        config_path=config_path,
    )


def _invalid_result(
    config_path: Path,
    detail: str,
) -> MaterialValidationRulesLoadResult:
    return MaterialValidationRulesLoadResult(
        status="invalid",
        rules=None,
        diagnostics=(
            Diagnostic(
                path=str(config_path),
                location="",
                detail=_INVALID_CODE,
                evidence=f"Invalid material validation rules config: {detail}",
                severity="error",
            ),
        ),
        config_path=config_path,
    )


def _parse_rules(raw: Any, config_path: Path) -> MaterialValidationRules:
    root = _require_mapping(raw, "root")
    version = root.get("version")
    if isinstance(version, bool) or version != 1:
        raise _RulesSchemaError("version must be 1")

    return MaterialValidationRules(
        config_status="loaded",
        config_path=config_path,
        shader_name_policies=_parse_shader_name_policies(root),
        shared_material_groups=_parse_shared_material_groups(root),
        folder_policies=_parse_folder_policies(root),
    )


def _parse_shader_name_policies(
    root: Mapping[str, Any],
) -> tuple[ShaderNamePolicy, ...]:
    return tuple(
        ShaderNamePolicy(
            id=_required_string(entry, "id", "shader_name_policies"),
            scope=_required_string(entry, "scope", "shader_name_policies"),
            hierarchy_prefix=_required_string(
                entry, "hierarchy_prefix", "shader_name_policies",
            ),
            expected_shader=_required_string(
                entry, "expected_shader", "shader_name_policies",
            ),
        )
        for entry in _optional_mapping_sequence(root, "shader_name_policies")
    )


def _parse_shared_material_groups(
    root: Mapping[str, Any],
) -> tuple[SharedMaterialGroup, ...]:
    return tuple(
        SharedMaterialGroup(
            id=_required_string(entry, "id", "shared_material_groups"),
            scope=_required_string(entry, "scope", "shared_material_groups"),
            hierarchy_prefix=_required_string(
                entry, "hierarchy_prefix", "shared_material_groups",
            ),
            expected_material=_optional_string(entry, "expected_material"),
        )
        for entry in _optional_mapping_sequence(root, "shared_material_groups")
    )


def _parse_folder_policies(root: Mapping[str, Any]) -> tuple[FolderPolicy, ...]:
    policies = []
    for entry in _optional_mapping_sequence(root, "folder_policies"):
        policies.append(
            FolderPolicy(
                id=_required_string(entry, "id", "folder_policies"),
                folder=_required_string(entry, "folder", "folder_policies"),
                disallowed_extensions=_optional_string_sequence(entry, "disallowed_extensions"),
                disallowed_asset_kinds=_optional_string_sequence(entry, "disallowed_asset_kinds"),
            )
        )
    return tuple(policies)


def _optional_mapping_sequence(
    root: Mapping[str, Any],
    field: str,
) -> tuple[Mapping[str, Any], ...]:
    value = root.get(field, ())
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise _RulesSchemaError(f"{field} must be a list")
    return tuple(_require_mapping(item, field) for item in value)


def _require_mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _RulesSchemaError(f"{context} must be an object")
    return value


def _required_string(
    entry: Mapping[str, Any],
    field: str,
    context: str,
) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or value == "":
        raise _RulesSchemaError(f"{context}.{field} must be a non-empty string")
    return value


def _optional_string(entry: Mapping[str, Any], field: str) -> str | None:
    if field not in entry:
        return None
    value = entry[field]
    if not isinstance(value, str) or value == "":
        raise _RulesSchemaError(f"{field} must be a non-empty string")
    return value


def _optional_string_sequence(
    entry: Mapping[str, Any],
    field: str,
) -> tuple[str, ...]:
    value = entry.get(field, ())
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise _RulesSchemaError(f"{field} must be a list")
    if any(not isinstance(item, str) or item == "" for item in value):
        raise _RulesSchemaError(f"{field} must contain only non-empty strings")
    return tuple(value)
