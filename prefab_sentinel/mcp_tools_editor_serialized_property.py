"""MCP wrappers for generic Unity SerializedProperty editor actions."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action

READ_OK = "EDITOR_CTRL_SERIALIZED_PROPERTY_READ_OK"
LIST_OK = "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_OK"
DRY_RUN_OK = "EDITOR_CTRL_SERIALIZED_PROPERTY_DRY_RUN_OK"
WRITE_OK = "EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK"
NO_CHANGE = "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_CHANGE"

NO_PATH = "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_PATH"
NO_COMPONENT_TYPE = "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_COMPONENT_TYPE"
NO_PROPERTY_PATH = "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_PROPERTY_PATH"
VALUE_REQUIRED = "EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_REQUIRED"
VALUE_CONFLICT = "EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_CONFLICT"
CHANGE_REASON_REQUIRED = "EDITOR_CTRL_SERIALIZED_PROPERTY_CHANGE_REASON_REQUIRED"
LIST_LIMIT_INVALID = "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_LIMIT_INVALID"
CURSOR_INVALID = "EDITOR_CTRL_SERIALIZED_PROPERTY_CURSOR_INVALID"
ARRAY_SIZE_INVALID = "EDITOR_CTRL_SERIALIZED_PROPERTY_ARRAY_SIZE_INVALID"

DEFAULT_DEPTH = 1
DEFAULT_CAP = 50
HARD_CAP = 200

__all__ = ["register_editor_serialized_property_tools"]


def _error(code: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "severity": "error",
        "code": code,
        "message": message,
        "data": data or {},
        "diagnostics": [],
    }


def _required_address_error(
    hierarchy_path: str,
    component_type: str,
    property_path: str | None,
) -> dict[str, Any] | None:
    if not hierarchy_path.strip():
        return _error(NO_PATH, "hierarchy_path is required.")
    if not component_type.strip():
        return _error(NO_COMPONENT_TYPE, "component_type is required.")
    if property_path is not None and not property_path.strip():
        return _error(NO_PROPERTY_PATH, "property_path is required.")
    return None


def _expand_serialized_property_json(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data")
    if not isinstance(data, dict):
        return response
    payload = data.get("serialized_property_json")
    if not isinstance(payload, str) or not payload:
        return response
    try:
        serialized_property = json.loads(payload)
    except json.JSONDecodeError:
        return response
    expanded = dict(response)
    expanded_data = dict(data)
    expanded_data["serialized_property"] = serialized_property
    expanded["data"] = expanded_data
    return expanded


def _traversal_error(depth: int, cap: int, cursor: str | None) -> dict[str, Any] | None:
    if depth < 0 or cap < 1 or cap > HARD_CAP:
        return _error(
            LIST_LIMIT_INVALID,
            f"depth must be >= 0 and cap must be between 1 and {HARD_CAP}.",
            {"depth": depth, "cap": cap, "hard_cap": HARD_CAP},
        )
    if cursor is not None and cursor != "" and not (cursor.isascii() and cursor.isdecimal()):
        return _error(CURSOR_INVALID, "cursor must be a non-negative integer.")
    return None


def _write_intents(
    *,
    bool_value: bool | None,
    int_value: int | None,
    long_value: int | None,
    float_value: float | None,
    string_value: str | None,
    enum_name: str | None,
    enum_index: int | None,
    object_reference_asset_path: str | None,
    object_reference_hierarchy_path: str | None,
    object_reference_null: bool,
    array_size: int | None,
) -> list[str]:
    intents: list[str] = []
    if bool_value is not None:
        intents.append("bool")
    if int_value is not None:
        intents.append("int")
    if long_value is not None:
        intents.append("long")
    if float_value is not None:
        intents.append("float")
    if string_value is not None:
        intents.append("string")
    if enum_name is not None:
        intents.append("enum_name")
    if enum_index is not None:
        intents.append("enum_index")
    if object_reference_asset_path is not None:
        intents.append("object_reference_asset_path")
    if object_reference_hierarchy_path is not None:
        intents.append("object_reference_hierarchy_path")
    if object_reference_null:
        intents.append("object_reference_null")
    if array_size is not None:
        intents.append("array_size")
    return intents


def _write_intent_error(intents: list[str], array_size: int | None) -> dict[str, Any] | None:
    if not intents:
        return _error(VALUE_REQUIRED, "Exactly one serialized-property value intent is required.")
    if len(intents) > 1:
        return _error(VALUE_CONFLICT, "Provide exactly one serialized-property value intent.")
    if array_size is not None and array_size < 0:
        return _error(ARRAY_SIZE_INVALID, "array_size must be non-negative.")
    return None


def register_editor_serialized_property_tools(server: FastMCP) -> None:
    """Register issue #112 SerializedProperty editor tools on *server*."""

    @server.tool()
    def editor_serialized_property_read(
        hierarchy_path: str,
        component_type: str,
        property_path: str,
        component_index: int | None = None,
    ) -> dict[str, Any]:
        err = _required_address_error(hierarchy_path, component_type, property_path)
        if err is not None:
            return err
        kwargs: dict[str, Any] = {
            "action": "editor_serialized_property_read",
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
            "property_path": property_path,
        }
        if component_index is not None:
            kwargs["component_index"] = component_index
        return _expand_serialized_property_json(send_action(**kwargs))

    @server.tool()
    def editor_serialized_property_list(
        hierarchy_path: str,
        component_type: str,
        component_index: int | None = None,
        root_property_path: str | None = None,
        depth: int = DEFAULT_DEPTH,
        cap: int = DEFAULT_CAP,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        err = _required_address_error(hierarchy_path, component_type, None)
        if err is not None:
            return err
        err = _traversal_error(depth, cap, cursor)
        if err is not None:
            return err

        kwargs: dict[str, Any] = {
            "action": "editor_serialized_property_list",
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
            "depth": depth,
            "cap": cap,
        }
        if component_index is not None:
            kwargs["component_index"] = component_index
        if root_property_path:
            kwargs["root_property_path"] = root_property_path
        if cursor not in (None, ""):
            kwargs["cursor"] = cursor
        return _expand_serialized_property_json(send_action(**kwargs))

    @server.tool()
    def editor_serialized_property_write(
        hierarchy_path: str,
        component_type: str,
        property_path: str,
        component_index: int | None = None,
        bool_value: bool | None = None,
        int_value: int | None = None,
        long_value: int | None = None,
        float_value: float | None = None,
        string_value: str | None = None,
        enum_name: str | None = None,
        enum_index: int | None = None,
        object_reference_asset_path: str | None = None,
        object_reference_hierarchy_path: str | None = None,
        object_reference_null: bool = False,
        array_size: int | None = None,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        err = _required_address_error(hierarchy_path, component_type, property_path)
        if err is not None:
            return err
        intents = _write_intents(
            bool_value=bool_value,
            int_value=int_value,
            long_value=long_value,
            float_value=float_value,
            string_value=string_value,
            enum_name=enum_name,
            enum_index=enum_index,
            object_reference_asset_path=object_reference_asset_path,
            object_reference_hierarchy_path=object_reference_hierarchy_path,
            object_reference_null=object_reference_null,
            array_size=array_size,
        )
        err = _write_intent_error(intents, array_size)
        if err is not None:
            return err
        stripped_reason = change_reason.strip()
        if confirm and not stripped_reason:
            return _error(CHANGE_REASON_REQUIRED, "Confirmed write requires change_reason.")

        kwargs: dict[str, Any] = {
            "action": "editor_serialized_property_write",
            "hierarchy_path": hierarchy_path,
            "component_type": component_type,
            "property_path": property_path,
            "confirm": confirm,
        }
        if component_index is not None:
            kwargs["component_index"] = component_index
        if bool_value is not None:
            kwargs["serialized_property_bool_value"] = bool_value
            kwargs["serialized_property_bool_value_present"] = True
        if int_value is not None:
            kwargs["serialized_property_int_value"] = int_value
            kwargs["serialized_property_int_value_present"] = True
        if long_value is not None:
            kwargs["serialized_property_long_value"] = long_value
            kwargs["serialized_property_long_value_present"] = True
        if float_value is not None:
            kwargs["serialized_property_float_value"] = float_value
            kwargs["serialized_property_float_value_present"] = True
        if string_value is not None:
            kwargs["serialized_property_string_value"] = string_value
            kwargs["serialized_property_string_value_present"] = True
        if enum_name is not None:
            kwargs["serialized_property_enum_name"] = enum_name
            kwargs["serialized_property_enum_name_present"] = True
        if enum_index is not None:
            kwargs["serialized_property_enum_index"] = enum_index
            kwargs["serialized_property_enum_index_present"] = True
        if object_reference_asset_path is not None:
            kwargs["serialized_property_object_reference_asset_path"] = object_reference_asset_path
            kwargs["serialized_property_object_reference_asset_path_present"] = True
        if object_reference_hierarchy_path is not None:
            kwargs["serialized_property_object_reference_hierarchy_path"] = (
                object_reference_hierarchy_path
            )
            kwargs["serialized_property_object_reference_hierarchy_path_present"] = True
        if object_reference_null:
            kwargs["serialized_property_object_reference_null"] = True
        if array_size is not None:
            kwargs["serialized_property_array_size"] = array_size
            kwargs["serialized_property_array_size_present"] = True
        if confirm:
            kwargs["change_reason"] = stripped_reason
        return _expand_serialized_property_json(send_action(**kwargs))
