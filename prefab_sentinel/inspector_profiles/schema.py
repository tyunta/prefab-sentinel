from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_SCHEMA_RESOURCE = "resources/inspector-profile.v1.schema.json"


@dataclass(frozen=True, slots=True)
class ProfileDiagnostic:
    path: str
    message: str


def _format_path(parts: Sequence[str | int]) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def load_profile_schema() -> Mapping[str, Any]:
    raw: object = json.loads(
        files("prefab_sentinel").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8")
    )
    if not isinstance(raw, Mapping):
        raise ValueError("inspector profile schema root must be an object")
    payload = cast(Mapping[str, Any], raw)
    Draft202012Validator.check_schema(payload)
    return payload


def _project_error(error: ValidationError) -> tuple[ProfileDiagnostic, ...]:
    path = tuple(error.absolute_path)
    if error.validator == "oneOf" and isinstance(error.instance, Mapping):
        kind = error.instance.get("kind")
        branch_index = {
            "fields": 0,
            "zipped_arrays": 1,
            "object_reference_table": 2,
        }.get(kind) if isinstance(kind, str) else None
        if branch_index is not None:
            projected = tuple(
                diagnostic
                for child in error.context
                if tuple(child.relative_schema_path)[:1] == (branch_index,)
                for diagnostic in _project_error(child)
            )
            if projected:
                return tuple(dict.fromkeys(projected))
    if (
        error.validator == "additionalProperties"
        and isinstance(error.instance, Mapping)
        and isinstance(error.schema, Mapping)
        and isinstance(error.schema.get("properties"), Mapping)
    ):
        declared = error.schema["properties"]
        unexpected = sorted(set(error.instance) - set(declared))
        return tuple(
            ProfileDiagnostic(_format_path((*path, name)), error.message)
            for name in unexpected
        )
    return (ProfileDiagnostic(_format_path(path), error.message),)

def validate_profile_document(document: Mapping[str, Any]) -> tuple[ProfileDiagnostic, ...]:
    validator = Draft202012Validator(load_profile_schema())
    errors = sorted(
        validator.iter_errors(document),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    projected = (
        diagnostic
        for error in errors
        for diagnostic in _project_error(error)
    )
    return tuple(dict.fromkeys(projected))
