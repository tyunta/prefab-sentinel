from __future__ import annotations

import json
from typing import TypeVar
from unittest.mock import MagicMock

from prefab_sentinel.contracts import ToolResponse

T = TypeVar("T")


def _describe_type(value: object) -> str:
    return type(value).__name__


def require_mapping(value: object, label: str = "value") -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{label} expected dict, got {_describe_type(value)}")
    return value


def require_list(value: object, label: str = "value") -> list[object]:
    if not isinstance(value, list):
        raise AssertionError(f"{label} expected list, got {_describe_type(value)}")
    return value


def require_str(value: object, label: str = "value") -> str:
    if not isinstance(value, str):
        raise AssertionError(f"{label} expected str, got {_describe_type(value)}")
    return value


def require_not_none(value: T | None, label: str = "value") -> T:
    if value is None:
        raise AssertionError(f"{label} expected non-None value")
    return value


def load_json_object(raw: str | bytes | bytearray, label: str = "json") -> dict[str, object]:
    return require_mapping(json.loads(raw), label)


def load_json_list(raw: str | bytes | bytearray, label: str = "json") -> list[object]:
    return require_list(json.loads(raw), label)


def require_tool_response(value: object, label: str = "response") -> ToolResponse:
    if not isinstance(value, ToolResponse):
        raise AssertionError(f"{label} expected ToolResponse, got {_describe_type(value)}")
    return value


def require_magic_mock(value: object, label: str = "mock") -> MagicMock:
    if not isinstance(value, MagicMock):
        raise AssertionError(f"{label} expected MagicMock, got {_describe_type(value)}")
    return value
