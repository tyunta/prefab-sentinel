"""Primitive validators shared by runtime response schema modules."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ProtocolSchemaError(ValueError):
    """Raised when a Unity runtime payload violates its exact schema."""


def schema_error(path: str, expected: str) -> ProtocolSchemaError:
    return ProtocolSchemaError(
        f"Unity runtime response field '{path}' must be {expected}."
    )


def exact_object(
    value: object,
    *,
    path: str,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise schema_error(path, "an object")
    expected = set(fields)
    actual = set(value)
    if actual != expected:
        raise schema_error(path, f"an object with exactly fields {sorted(expected)!r}")
    return value


def boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise schema_error(path, "a boolean")
    return value


def integer(value: object, path: str) -> int:
    if type(value) is not int:
        raise schema_error(path, "an integer")
    return value


def string(value: object, path: str) -> str:
    if type(value) is not str:
        raise schema_error(path, "a string")
    return value


def string_array(value: object, path: str) -> list[str]:
    if not isinstance(value, list) or any(type(entry) is not str for entry in value):
        raise schema_error(path, "an array of strings")
    return value


def runtime_diagnostic(value: object, path: str) -> dict[str, Any]:
    diagnostic = exact_object(
        value,
        path=path,
        fields=("path", "location", "detail", "evidence"),
    )
    for field in ("path", "location", "detail", "evidence"):
        string(diagnostic[field], f"{path}.{field}")
    return diagnostic


def runtime_diagnostics(value: object, path: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise schema_error(path, "an array")
    return [
        runtime_diagnostic(entry, f"{path}[{index}]")
        for index, entry in enumerate(value)
    ]


def asset_identity(value: object, path: str) -> None:
    identity = exact_object(
        value,
        path=path,
        fields=(
            "guid",
            "local_file_id",
            "path",
            "type",
            "dirty",
            "attribution_unknown",
        ),
    )
    string(identity["guid"], f"{path}.guid")
    integer(identity["local_file_id"], f"{path}.local_file_id")
    string(identity["path"], f"{path}.path")
    string(identity["type"], f"{path}.type")
    boolean(identity["dirty"], f"{path}.dirty")
    string_array(identity["attribution_unknown"], f"{path}.attribution_unknown")


def scene_identity(value: object, path: str) -> None:
    identity = exact_object(
        value,
        path=path,
        fields=("path", "handle", "dirty", "attribution_unknown"),
    )
    string(identity["path"], f"{path}.path")
    integer(identity["handle"], f"{path}.handle")
    boolean(identity["dirty"], f"{path}.dirty")
    string_array(identity["attribution_unknown"], f"{path}.attribution_unknown")


def identity_array(
    value: object,
    path: str,
    validator: Callable[[object, str], None],
) -> None:
    if not isinstance(value, list):
        raise schema_error(path, "an array")
    for index, entry in enumerate(value):
        validator(entry, f"{path}[{index}]")
