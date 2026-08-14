from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, call, patch

from mcp.server.mcpserver.exceptions import ToolError

from prefab_sentinel.inspector_profiles import application
from prefab_sentinel.mcp_server import create_server
from tests._mcp_test_support import call_tool_result, run, structured_payload


def _assert_component_surface_request(
    mock_send: Mock,
    *,
    expected_project_root: str,
    asset_path: str,
    symbol_path: str,
    include_override_origin: bool,
) -> None:
    mock_send.assert_called_once_with(
        action="editor_inspect_serialized_surface",
        asset_path=asset_path,
        symbol_path=symbol_path,
        include_override_origin=include_override_origin,
        expected_project_root=expected_project_root,
    )


def _valid_profile() -> dict[str, Any]:
    return {
        "schema_version": "inspector_profile.v1",
        "target": {
            "managed_type": "Example.Component",
            "assembly": "Example.Assembly",
            "script_guid": "a" * 32,
            "script_file_id": 11500000,
        },
        "generated_by": {
            "kind": "ai-skill",
            "skill": "prefab-sentinel:inspector-profile-authoring",
            "created_at": "2026-07-10T00:00:00Z",
            "source": "INSPECTOR_PROFILE_REQUIRED",
            "mcp_tool": "inspect_with_profile",
        },
        "confidence": "high",
        "evidence": [{"kind": "source", "detail": "Source binds exampleField."}],
        "limitations": [],
        "views": [
            {
                "name": "overview",
                "kind": "fields",
                "evidence": [],
                "limitations": [],
                "fields": [
                    {
                        "name": "example",
                        "label": "Example",
                        "path": "exampleField",
                        "expected_type": "String",
                    }
                ],
            }
        ],
    }


def _write_component_prefab(project_root: Path) -> None:
    assets = project_root / "Assets"
    assets.mkdir(exist_ok=True)
    (assets / "Test.prefab").write_text(
        "--- !u!1 &1\n"
        "GameObject:\n"
        "  m_Component:\n"
        "  - component: {fileID: 2}\n"
        "  - component: {fileID: 3}\n"
        "  m_Name: Root\n"
        "--- !u!4 &2\n"
        "Transform:\n"
        "  m_GameObject: {fileID: 1}\n"
        "  m_Father: {fileID: 0}\n"
        "  m_Children: []\n"
        "--- !u!114 &3\n"
        "MonoBehaviour:\n"
        "  m_GameObject: {fileID: 1}\n"
        f"  m_Script: {{fileID: 11500000, guid: {'a' * 32}, type: 3}}\n",
        encoding="utf-8",
    )


class TestInspectorProfileToolRegistration(unittest.TestCase):
    def test_exact_three_generic_inspector_profile_tools_are_registered(self) -> None:
        server = create_server()

        names = {
            tool.name
            for tool in run(server.list_tools())
            if tool.name
            in {
                "inspect_serialized_surface",
                "inspect_with_profile",
                "validate_inspector_profile",
            }
        }

        self.assertEqual(
            {
                "inspect_serialized_surface",
                "inspect_with_profile",
                "validate_inspector_profile",
            },
            names,
            msg=f"Inspector-profile MCP inventory is incomplete or renamed: {sorted(names)!r}",
        )

    def test_tool_schemas_require_the_authority_named_inputs(self) -> None:
        server = create_server()

        tools = {tool.name: tool for tool in run(server.list_tools())}

        self.assertEqual(
            {
                "inspect_serialized_surface": ["asset_path"],
                "inspect_with_profile": ["asset_path", "view_name"],
                "validate_inspector_profile": ["asset_path", "profile_path"],
            },
            {
                name: sorted(tools[name].input_schema["required"])
                for name in (
                    "inspect_serialized_surface",
                    "inspect_with_profile",
                    "validate_inspector_profile",
                )
            },
            msg="MCPServer required-field schemas drifted from MC-003 through MC-005",
        )

    def test_empty_view_name_precedes_malformed_target_resolution(self) -> None:
        server = create_server()

        try:
            result = structured_payload(call_tool_result(server,
                    "inspect_with_profile",
                    {"asset_path": "", "view_name": ""},
                )
            )
        except ToolError as exc:
            self.fail(f"expected INSPECTOR_VIEW_NAME_REQUIRED, observed tool error: {exc}")

        self.assertEqual(
            (
                False,
                "error",
                "INSPECTOR_VIEW_NAME_REQUIRED",
                "view_name is required.",
                {},
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                result["data"],
            ),
            msg=f"blank view_name must win before target/profile/Bridge work; got {result!r}",
        )


class TestSerializedSurfaceTool(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_inactive_project_returns_before_bridge_dispatch(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                    },
                )
            )

        self.assertEqual(
            (False, "error", "PROJECT_NOT_ACTIVATED"),
            (result["success"], result["severity"], result["code"]),
        )
        mock_send.assert_not_called()

    def test_bridge_unavailable_returns_blocker_without_surface_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = create_server(project_root=Path(temporary))
            try:
                result = structured_payload(call_tool_result(server,
                        "inspect_serialized_surface",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )
            except ToolError as exc:
                self.fail(f"expected INSPECTOR_SURFACE_UNAVAILABLE, observed tool error: {exc}")

        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                "The last-saved serialized surface is unavailable because the Editor Bridge could not be used.",
                False,
                "EDITOR_BRIDGE_WATCH_DIR_MISSING",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                "surface" in result["data"],
                result["diagnostics"][0]["code"],
            ),
            msg=(f"Bridge loss must preserve blocker provenance without a substitute surface; got {result!r}"),
        )

    def test_non_boolean_bridge_success_is_unavailable(self) -> None:
        surface = {
            "target": {
                "managed_type": "Example.Component",
                "assembly": "Example.Assembly",
                "script_guid": "a" * 32,
                "script_file_id": 11500000,
                "script_path": "Assets/Example.cs",
            },
            "properties": [],
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
        }
        server = create_server(project_root="/project")

        for code in (
            "EDITOR_CTRL_PROTOCOL_ERROR",
            "EDITOR_CTRL_INSPECTOR_TARGET_NOT_FOUND",
        ):
            bridge_response: dict[str, Any] = {
                "success": "false",
                "severity": "error",
                "code": code,
                "message": "Malformed response.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }
            with (
                self.subTest(code=code),
                patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ),
            ):
                result = structured_payload(call_tool_result(server,
                        "inspect_serialized_surface",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

                self.assertEqual(
                    (False, "warning", "INSPECTOR_SURFACE_UNAVAILABLE", False),
                    (
                        result["success"],
                        result["severity"],
                        result["code"],
                        "surface" in result["data"],
                    ),
                    msg=f"non-boolean Bridge success was accepted: {result!r}",
                )

    def test_recorded_bridge_surface_is_expanded_without_losing_effective_or_origin_data(self) -> None:
        server = create_server(project_root="/project")
        surface = {
            "target": {
                "managed_type": "Example.Component",
                "assembly": "Example.Assembly",
                "script_guid": "a" * 32,
                "script_file_id": 11500000,
                "script_path": "Assets/Example.cs",
            },
            "properties": [
                {
                    "path": "targetRef",
                    "property_type": "ObjectReference",
                    "source_value": {
                        "object_reference": True,
                        "guid": "b" * 32,
                        "local_file_id": 2100000,
                        "asset_path": "Assets/Base.mat",
                        "object_type": "UnityEngine.Material",
                        "hierarchy_path": "",
                        "null": False,
                        "missing": False,
                    },
                    "effective_value": {
                        "object_reference": True,
                        "guid": "c" * 32,
                        "local_file_id": 2100000,
                        "asset_path": "Assets/Override.mat",
                        "object_type": "UnityEngine.Material",
                        "hierarchy_path": "",
                        "null": False,
                        "missing": False,
                    },
                    "origin": {"layer": "nested_override", "source": "Assets/Base.prefab"},
                    "array_size": None,
                    "element_type": None,
                }
            ],
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
        }
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
            "message": "Serialized surface inspected.",
            "data": {"serialized_surface_json": json.dumps(surface)},
            "diagnostics": [],
        }

        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                        "include_override_origin": True,
                    },
                )
            )

        self.assertEqual(
            (True, "info", "INSPECTOR_SERIALIZED_SURFACE_OK", surface),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["surface"],
            ),
            msg=f"recorded last-saved Bridge payload was altered or rejected: {result!r}",
        )
        mock_send.assert_called_once_with(
            action="editor_inspect_serialized_surface",
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=True,
            expected_project_root=application.to_windows_path("/project"),
        )

    def test_control_character_string_survives_serialized_surface_json(self) -> None:
        server = create_server(project_root="/project")
        control_value = "before\x01after"
        surface = {
            "target": {
                "managed_type": "Example.Component",
                "assembly": "Example.Assembly",
                "script_guid": "a" * 32,
                "script_file_id": 11500000,
                "script_path": "Assets/Example.cs",
            },
            "properties": [
                {
                    "path": "message",
                    "property_type": "String",
                    "source_value": control_value,
                    "effective_value": control_value,
                    "origin": None,
                    "array_size": None,
                    "element_type": None,
                }
            ],
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
        }
        serialized_surface_json = json.dumps(surface)
        self.assertIn(r"\u0001", serialized_surface_json)
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
            "message": "Serialized surface inspected.",
            "data": {"serialized_surface_json": serialized_surface_json},
            "diagnostics": [],
        }

        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                        "include_override_origin": False,
                    },
                )
            )

        self.assertEqual(
            (True, "INSPECTOR_SERIALIZED_SURFACE_OK", control_value),
            (
                result["success"],
                result["code"],
                result["data"]["surface"]["properties"][0]["source_value"],
            ),
        )
        mock_send.assert_called_once_with(
            action="editor_inspect_serialized_surface",
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
            expected_project_root=application.to_windows_path("/project"),
        )

    def test_bridge_unresolved_component_preserves_requested_target_address(self) -> None:
        server = create_server(project_root="/project")
        bridge_response = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_INSPECTOR_TARGET_NOT_FOUND",
            "message": "The requested serialized target was not found.",
            "data": {},
            "diagnostics": [],
        }

        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Missing)",
                    },
                )
            )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path("/project"),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Missing)",
            include_override_origin=False,
        )

        self.assertEqual(
            {
                "success": False,
                "severity": "error",
                "code": "INSPECTOR_SURFACE_TARGET_NOT_FOUND",
                "message": "The requested serialized target was not found.",
                "data": {
                    "address": {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Missing)",
                    }
                },
                "diagnostics": [],
            },
            result,
            msg=f"unresolved Bridge targets must retain the canonical requested address: {result!r}",
        )

    def test_malformed_bridge_surface_returns_schema_envelope(self) -> None:
        server = create_server(project_root="/project")
        valid_target = {
            "managed_type": "Example.Component",
            "assembly": "Example.Assembly",
            "script_guid": "a" * 32,
            "script_file_id": 11500000,
            "script_path": "Assets/Example.cs",
        }
        malformed_data: tuple[object, ...] = (
            [],
            {
                "serialized_surface_json": json.dumps(
                    {
                        "target": valid_target,
                        "properties": [],
                    }
                )
            },
            {"serialized_surface_json": json.dumps({"target": {}, "properties": "bad"})},
            {
                "serialized_surface_json": json.dumps(
                    {
                        "target": valid_target,
                        "properties": [],
                        "source_candidates_status": "complete",
                        "source_candidates_reasons": [],
                        "source_candidates": [{}],
                        "custom_editor_candidates": [],
                    }
                )
            },
            {
                "serialized_surface_json": json.dumps(
                    {
                        "target": valid_target,
                        "properties": [],
                        "source_candidates_status": "complete",
                        "source_candidates_reasons": [],
                        "source_candidates": [
                            {
                                "kind": "runtime_component",
                                "managed_type": "Example.Component",
                            }
                        ],
                        "custom_editor_candidates": [{}],
                    }
                )
            },
            {
                "serialized_surface_json": json.dumps(
                    {
                        "target": valid_target,
                        "properties": [],
                        "source_candidates_status": "degraded",
                        "source_candidates_reasons": [
                            "The target has no public MonoScript source."
                        ],
                        "source_candidates": [
                            {
                                "kind": "runtime_script",
                                "path": None,
                            }
                        ],
                        "custom_editor_candidates": [],
                    }
                )
            },
            {
                "serialized_surface_json": json.dumps(
                    {
                        "target": valid_target,
                        "properties": [],
                        "source_candidates_status": "degraded",
                        "source_candidates_reasons": [
                            "The target has no public MonoScript source."
                        ],
                        "source_candidates": [
                            {
                                "kind": "runtime_script",
                                "path": "",
                            }
                        ],
                        "custom_editor_candidates": [],
                    }
                )
            },
        )

        for data in malformed_data:
            with self.subTest(data=data):
                bridge_response = {
                    "success": True,
                    "severity": "info",
                    "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                    "message": "Serialized surface inspected.",
                    "data": data,
                    "diagnostics": [],
                }
                with patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send:
                    result = structured_payload(call_tool_result(server,
                            "inspect_serialized_surface",
                            {
                                "asset_path": "Assets/Test.prefab",
                                "symbol_path": "Root/MonoBehaviour(Example)",
                            },
                        )
                    )

                _assert_component_surface_request(
                    mock_send,
                    expected_project_root=application.to_windows_path("/project"),
                    asset_path="Assets/Test.prefab",
                    symbol_path="Root/MonoBehaviour(Example)",
                    include_override_origin=False,
                )

                self.assertEqual(
                    (
                        False,
                        "warning",
                        "INSPECTOR_SURFACE_UNAVAILABLE",
                        "EDITOR_BRIDGE_RESPONSE_SCHEMA",
                    ),
                    (
                        result["success"],
                        result["severity"],
                        result["code"],
                        result["diagnostics"][0]["code"],
                    ),
                    msg=(f"malformed Bridge surface must remain a structured schema failure: {result!r}"),
                )

    def test_malformed_bridge_json_returns_schema_envelope(self) -> None:
        server = create_server(project_root="/project")
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
            "message": "Serialized surface inspected.",
            "data": {"serialized_surface_json": "{not-json"},
            "diagnostics": [],
        }
        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                    },
                )
            )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path("/project"),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                "EDITOR_BRIDGE_RESPONSE_SCHEMA",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["diagnostics"][0]["code"],
            ),
            msg=f"malformed Bridge JSON escaped the schema envelope: {result!r}",
        )

    def test_invalid_utf8_bridge_response_returns_envelope_and_cleans_ipc_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary) / "project"
            (project_root / "Assets").mkdir(parents=True)
            watch_dir = Path(temporary) / "watch"
            watch_dir.mkdir()
            server = create_server(project_root=project_root)
            original_rename = Path.rename

            def publish_invalid_response(source: Path, destination: Path) -> Path:
                renamed = original_rename(source, destination)
                if destination.name.endswith(".request.json"):
                    request_id = destination.name.removesuffix(".request.json")
                    (watch_dir / f"{request_id}.response.json").write_bytes(b"\xff")
                return renamed

            with (
                patch.dict(
                    os.environ,
                    {"UNITYTOOL_BRIDGE_WATCH_DIR": str(watch_dir)},
                    clear=False,
                ),
                patch.object(Path, "rename", publish_invalid_response),
            ):
                result = structured_payload(call_tool_result(server,
                        "inspect_serialized_surface",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )
            remaining_files = sorted(path.name for path in watch_dir.iterdir())

        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                "EDITOR_BRIDGE_RESPONSE_READ",
                [],
                False,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["diagnostics"][0]["code"],
                remaining_files,
                temporary in json.dumps(result),
            ),
            msg=f"invalid UTF-8 Bridge response escaped or leaked transport state: {result!r}",
        )

    def test_incomplete_target_and_object_reference_nodes_return_schema_envelope(self) -> None:
        server = create_server(project_root="/project")
        candidate_metadata = {
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
        }
        valid_target = {
            "managed_type": "Example.Component",
            "assembly": "Example.Assembly",
            "script_guid": "a" * 32,
            "script_file_id": 11500000,
            "script_path": "Assets/Example.cs",
        }
        valid_reference = {
            "object_reference": True,
            "guid": "b" * 32,
            "local_file_id": 2100000,
            "asset_path": "Assets/Test.mat",
            "object_type": "UnityEngine.Material",
            "hierarchy_path": "",
            "null": False,
            "missing": False,
        }
        property_fields = {
            "path": "targetRef",
            "property_type": "ObjectReference",
            "origin": None,
            "array_size": None,
            "element_type": None,
        }
        malformed_surfaces = (
            {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                },
                "properties": [],
                **candidate_metadata,
            },
            {
                "target": {
                    **valid_target,
                    "script_path": None,
                },
                "properties": [],
                **candidate_metadata,
            },
            {
                "target": valid_target,
                "properties": [
                    {
                        **property_fields,
                        "source_value": "not-an-object-reference",
                        "effective_value": valid_reference,
                    }
                ],
                **candidate_metadata,
            },
            {
                "target": valid_target,
                "properties": [
                    {
                        **property_fields,
                        "source_value": valid_reference,
                        "effective_value": {"missing": False},
                    }
                ],
                **candidate_metadata,
            },
        )

        for surface in malformed_surfaces:
            with self.subTest(surface=surface):
                bridge_response = {
                    "success": True,
                    "severity": "info",
                    "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                    "message": "Serialized surface inspected.",
                    "data": {"serialized_surface_json": json.dumps(surface)},
                    "diagnostics": [],
                }
                with patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send:
                    result = structured_payload(call_tool_result(server,
                            "inspect_serialized_surface",
                            {
                                "asset_path": "Assets/Test.prefab",
                                "symbol_path": "Root/MonoBehaviour(Example)",
                            },
                        )
                    )

                _assert_component_surface_request(
                    mock_send,
                    expected_project_root=application.to_windows_path("/project"),
                    asset_path="Assets/Test.prefab",
                    symbol_path="Root/MonoBehaviour(Example)",
                    include_override_origin=False,
                )

                self.assertEqual(
                    (
                        False,
                        "warning",
                        "INSPECTOR_SURFACE_UNAVAILABLE",
                        "EDITOR_BRIDGE_RESPONSE_SCHEMA",
                    ),
                    (
                        result["success"],
                        result["severity"],
                        result["code"],
                        (result["diagnostics"][0].get("code") if result["diagnostics"] else None),
                    ),
                    msg=(
                        "incomplete target/ObjectReference nodes must fail at the "
                        f"serialized-surface boundary: {result!r}"
                    ),
                )

    def test_object_reference_status_variants_are_preserved(self) -> None:
        server = create_server(project_root="/project")
        references = [
            {
                "object_reference": True,
                "guid": "",
                "local_file_id": 0,
                "asset_path": "",
                "object_type": "",
                "hierarchy_path": "",
                "null": True,
                "missing": False,
            },
            {
                "object_reference": True,
                "guid": "",
                "local_file_id": 0,
                "asset_path": "",
                "object_type": "",
                "hierarchy_path": "",
                "null": False,
                "missing": True,
            },
            {
                "object_reference": True,
                "guid": "",
                "local_file_id": 123,
                "asset_path": "",
                "object_type": "UnityEngine.GameObject",
                "hierarchy_path": "Root/Target",
                "null": False,
                "missing": False,
            },
            {
                "object_reference": True,
                "guid": "c" * 32,
                "local_file_id": 2100000,
                "asset_path": "Assets/Test.mat",
                "object_type": "UnityEngine.Material",
                "hierarchy_path": "",
                "null": False,
                "missing": False,
            },
        ]
        surface = {
            "target": {
                "managed_type": "Example.Component",
                "assembly": "Example.Assembly",
                "script_guid": "a" * 32,
                "script_file_id": 11500000,
                "script_path": "Assets/Example.cs",
            },
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
            "properties": [
                {
                    "path": f"reference{index}",
                    "property_type": "ObjectReference",
                    "source_value": reference,
                    "effective_value": reference,
                    "origin": None,
                    "array_size": None,
                    "element_type": None,
                }
                for index, reference in enumerate(references)
            ],
        }
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
            "message": "Serialized surface inspected.",
            "data": {"serialized_surface_json": json.dumps(surface)},
            "diagnostics": [],
        }

        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                    },
                )
            )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path("/project"),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            references,
            [item["effective_value"] for item in result["data"]["surface"]["properties"]],
            msg=f"null, missing, local-scene, and asset references must remain exact: {result!r}",
        )


class TestProfileWorkflow(unittest.TestCase):
    def test_no_matching_profile_returns_complete_authoring_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            server = create_server(project_root=project_root)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": "Assets/Example.cs",
                },
                "properties": [
                    {
                        "path": "targetRef",
                        "property_type": "ObjectReference",
                        "source_value": {
                            "object_reference": True,
                            "guid": "b" * 32,
                            "local_file_id": 2100000,
                            "asset_path": "Assets/Base.mat",
                            "object_type": "UnityEngine.Material",
                            "hierarchy_path": "",
                            "null": False,
                            "missing": False,
                        },
                        "effective_value": {
                            "object_reference": True,
                            "guid": "c" * 32,
                            "local_file_id": 2100000,
                            "asset_path": "Assets/Override.mat",
                            "object_type": "UnityEngine.Material",
                            "hierarchy_path": "",
                            "null": False,
                            "missing": False,
                        },
                        "origin": None,
                        "array_size": None,
                        "element_type": None,
                    }
                ],
                "source_candidates_status": "complete",
                "source_candidates": [{"kind": "runtime_script", "path": "Assets/Example.cs"}],
                "custom_editor_candidates": [{"type": "Example.ComponentEditor", "active": True}],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                try:
                    result = structured_payload(call_tool_result(server,
                            "inspect_with_profile",
                            {
                                "asset_path": "Assets/Test.prefab",
                                "symbol_path": "Root/MonoBehaviour(Example)",
                                "view_name": "overview",
                            },
                        )
                    )
                except ToolError as exc:
                    self.fail(f"expected INSPECTOR_PROFILE_REQUIRED, observed tool error: {exc}")

            data = dict(result["data"])
            recommended = Path(data.pop("recommended_profile_path"))

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                False,
                "info",
                "INSPECTOR_PROFILE_REQUIRED",
                "No inspector profile exists for the requested target.",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
            ),
            msg=f"no-profile workflow state drifted: {result!r}",
        )
        self.assertEqual(
            {
                "required_skill": "prefab-sentinel:inspector-profile-authoring",
                "related_skills": ["prefab-sentinel:guide"],
                "profile_schema": "inspector_profile.v1",
                "validation_tool": "validate_inspector_profile",
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": "Assets/Example.cs",
                    "address": {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                    },
                },
                "surface_summary": {
                    "available": True,
                    "field_count": 1,
                    "array_candidates": [],
                    "object_reference_fields": ["targetRef"],
                },
                "surface_ref": {
                    "tool": "inspect_serialized_surface",
                    "args": {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                        "include_override_origin": False,
                    },
                },
                "source_candidates_status": "complete",
                "source_candidates": [{"kind": "runtime_script", "path": "Assets/Example.cs"}],
                "custom_editor_candidates": [{"type": "Example.ComponentEditor", "active": True}],
                "property_drawer_candidates": [],
                "property_drawer_candidates_status": {
                    "status": "degraded",
                    "reasons": ["PropertyDrawer discovery is not available from the public Unity API boundary."],
                },
                "next_action": "Use the required skill to author or repair a project-local inspector profile, then validate it with validate_inspector_profile.",
            },
            data,
            msg=f"authoring payload is incomplete or fabricated: {data!r}",
        )
        self.assertEqual(
            (
                Path(".prefab-sentinel") / "profiles",
                ".json",
                False,
                False,
            ),
            (
                recommended.parent,
                recommended.suffix,
                recommended.is_absolute(),
                "<" in str(recommended) or ">" in str(recommended),
            ),
            msg=f"recommended path must be concrete, project-relative, and contained: {recommended}",
        )

    def test_no_profile_plus_bridge_loss_returns_unavailable_authoring_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            assets = project_root / "Assets"
            assets.mkdir()
            prefab_path = assets / "Test.prefab"
            prefab_path.write_text(
                "--- !u!1 &1\n"
                "GameObject:\n"
                "  m_Component:\n"
                "  - component: {fileID: 2}\n"
                "  - component: {fileID: 3}\n"
                "  m_Name: Root\n"
                "--- !u!4 &2\n"
                "Transform:\n"
                "  m_GameObject: {fileID: 1}\n"
                "  m_Father: {fileID: 0}\n"
                "  m_Children: []\n"
                "--- !u!114 &3\n"
                "MonoBehaviour:\n"
                "  m_GameObject: {fileID: 1}\n"
                f"  m_Script: {{fileID: 11500000, guid: {'a' * 32}, type: 3}}\n",
                encoding="utf-8",
            )
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_BRIDGE_TIMEOUT",
                "message": "Editor Bridge response timed out.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                            "view_name": "overview",
                        },
                    )
                )

        data = dict(result["data"])
        target = data.get("target")
        target_data = target if isinstance(target, dict) else {}
        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(guid:aaaaaaaa)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                False,
                "info",
                "INSPECTOR_PROFILE_REQUIRED",
                {"available": False},
                "degraded",
                ["Editor Bridge surface and candidate discovery are unavailable."],
                None,
                ["Script source path could not be resolved from offline metadata."],
                {
                    "tool": "inspect_serialized_surface",
                    "args": {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                        "include_override_origin": False,
                    },
                },
                [],
                [],
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                data.get("surface_summary"),
                data.get("source_candidates_status"),
                data.get("source_candidates_reasons"),
                target_data.get("script_path"),
                target_data.get("script_path_degradation_reasons"),
                data.get("surface_ref"),
                data.get("source_candidates"),
                data.get("custom_editor_candidates"),
            ),
            msg=f"offline no-match classification leaked a surface or lost degradation evidence: {result!r}",
        )
        recommended_raw = data.get("recommended_profile_path")
        if not isinstance(recommended_raw, str):
            self.fail(f"offline identity recommendation is missing: {result!r}")
        recommended = Path(recommended_raw)
        self.assertEqual(
            (Path(".prefab-sentinel") / "profiles", ".json", False),
            (recommended.parent, recommended.suffix, recommended.is_absolute()),
            msg=f"offline identity recommendation must remain concrete, project-relative, and contained: {recommended}",
        )

    def test_matching_offline_invalid_profile_plus_bridge_loss_is_surface_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _write_component_prefab(project_root)
            profile_root = project_root / ".prefab-sentinel" / "profiles"
            profile_root.mkdir(parents=True)
            profile = _valid_profile()
            profile["schema_version"] = "unknown"
            (profile_root / "invalid.json").write_text(
                json.dumps(profile),
                encoding="utf-8",
            )
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_BRIDGE_TIMEOUT",
                "message": "Editor Bridge response timed out.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                            "view_name": "overview",
                        },
                    )
                )

        diagnostics = result["diagnostics"]
        diagnostic_code = (
            diagnostics[0].get("code")
            if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict)
            else None
        )
        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(guid:aaaaaaaa)",
            include_override_origin=False,
        )
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                {},
                "EDITOR_BRIDGE_TIMEOUT",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"],
                diagnostic_code,
            ),
            msg=f"Bridge loss must precede offline invalid-profile classification: {result!r}",
        )

    def test_unsafe_local_profile_plus_bridge_loss_is_surface_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            project_root.mkdir()
            _write_component_prefab(project_root)
            local_root = project_root / ".prefab-sentinel" / "profiles"
            local_root.mkdir(parents=True)
            unsafe_target = root / "unsafe.json"
            unsafe_target.write_text(json.dumps(_valid_profile()), encoding="utf-8")
            (local_root / "unsafe.json").symlink_to(unsafe_target)
            package_root = root / "package"
            bundled_root = package_root / "resources" / "profiles"
            bundled_root.mkdir(parents=True)
            (bundled_root / "valid.json").write_text(
                json.dumps(_valid_profile()),
                encoding="utf-8",
            )
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_BRIDGE_TIMEOUT",
                "message": "Editor Bridge response timed out.",
                "data": {},
                "diagnostics": [],
            }

            with (
                patch(
                    "prefab_sentinel.inspector_profiles.application.files",
                    return_value=package_root,
                ),
                patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send,
            ):
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                            "view_name": "overview",
                        },
                    )
                )

        diagnostics = result["diagnostics"]
        diagnostic_code = (
            diagnostics[0].get("code")
            if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict)
            else None
        )
        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(guid:aaaaaaaa)",
            include_override_origin=False,
        )
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                {},
                "EDITOR_BRIDGE_TIMEOUT",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"],
                diagnostic_code,
            ),
            msg=f"Bridge loss must precede unsafe-profile classification: {result!r}",
        )

    def test_symlinked_project_root_renders_project_profile_with_stable_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            project_link = root / "project-link"
            profile_root = project_root / ".prefab-sentinel" / "profiles"
            (project_root / "Assets").mkdir(parents=True)
            profile_root.mkdir(parents=True)
            project_link.symlink_to(project_root, target_is_directory=True)
            profile_path = profile_root / "example.json"
            profile_path.write_text(json.dumps(_valid_profile()), encoding="utf-8")
            server = create_server(project_root=project_link)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": "Assets/Example.cs",
                },
                "properties": [
                    {
                        "path": "exampleField",
                        "property_type": "String",
                        "source_value": "base",
                        "effective_value": "nested-override",
                        "origin": {"layer": "nested_override"},
                        "array_size": None,
                        "element_type": None,
                    }
                ],
                "source_candidates_status": "complete",
                "source_candidates": [],
                "custom_editor_candidates": [],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                try:
                    result = structured_payload(call_tool_result(server,
                            "inspect_with_profile",
                            {
                                "asset_path": "Assets/Test.prefab",
                                "symbol_path": "Root/MonoBehaviour(Example)",
                                "view_name": "overview",
                                "include_override_origin": False,
                            },
                        )
                    )
                except ToolError as exc:
                    self.fail(f"expected INSPECTOR_PROFILE_VIEW_OK, observed tool error: {exc}")

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            {
                "success": True,
                "severity": "info",
                "code": "INSPECTOR_PROFILE_VIEW_OK",
                "message": "The requested inspector profile view was rendered.",
                "data": {
                    "profile_source": "project",
                    "profile_path": ".prefab-sentinel/profiles/example.json",
                    "profile_warning": None,
                    "views": [
                        {
                            "name": "overview",
                            "kind": "fields",
                            "fields": [
                                {
                                    "name": "example",
                                    "label": "Example",
                                    "path": "exampleField",
                                    "value": "nested-override",
                                }
                            ],
                            "writable": {"enabled": False},
                        }
                    ],
                    "warnings": [],
                },
                "diagnostics": [],
            },
            result,
            msg=(
                "symlinked project roots must preserve stable project-relative "
                f"profile identity; got {result!r}"
            ),
        )


    def test_conflicting_offline_profiles_plus_bridge_loss_is_surface_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _write_component_prefab(project_root)
            profile_root = project_root / ".prefab-sentinel" / "profiles"
            profile_root.mkdir(parents=True)
            for name in ("first.json", "second.json"):
                (profile_root / name).write_text(
                    json.dumps(_valid_profile()),
                    encoding="utf-8",
                )
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_BRIDGE_TIMEOUT",
                "message": "Editor Bridge response timed out.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                            "view_name": "overview",
                        },
                    )
                )

        diagnostics = result["diagnostics"]
        diagnostic_code = (
            diagnostics[0].get("code")
            if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict)
            else None
        )
        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(guid:aaaaaaaa)",
            include_override_origin=False,
        )
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                {},
                "EDITOR_BRIDGE_TIMEOUT",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"],
                diagnostic_code,
            ),
            msg=f"Bridge loss must precede conflicting-profile classification: {result!r}",
        )

    def test_bundled_profile_uses_stable_public_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            (project_root / "Assets").mkdir(parents=True)
            package_root = root / "package"
            bundled_root = package_root / "resources" / "profiles"
            bundled_root.mkdir(parents=True)
            (bundled_root / "example.json").write_text(
                json.dumps(_valid_profile()),
                encoding="utf-8",
            )
            server = create_server(project_root=project_root)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": "Assets/Example.cs",
                },
                "properties": [
                    {
                        "path": "exampleField",
                        "property_type": "String",
                        "source_value": "base",
                        "effective_value": "saved",
                        "origin": None,
                        "array_size": None,
                        "element_type": None,
                    }
                ],
                "source_candidates_status": "complete",
                "source_candidates": [],
                "custom_editor_candidates": [],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }

            with (
                patch(
                    "prefab_sentinel.inspector_profiles.application.files",
                    return_value=package_root,
                ),
                patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send,
            ):
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                            "view_name": "overview",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )
        self.assertEqual(
            (True, "INSPECTOR_PROFILE_VIEW_OK", "bundled", "profiles/example.json", False),
            (
                result["success"],
                result["code"],
                result["data"]["profile_source"],
                result["data"]["profile_path"],
                temporary in json.dumps(result),
            ),
            msg=f"bundled profile response exposed its packaged host path: {result!r}",
        )


class TestInspectorCandidateDiscovery(unittest.TestCase):
    def test_degraded_discovery_preserves_reasons_and_bounds_editor_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            server = create_server(project_root=project_root)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": None,
                    "script_path_degradation_reasons": ["Runtime script source path is unavailable."],
                },
                "properties": [],
                "source_candidates_status": "degraded",
                "source_candidates_reasons": ["Assembly prerequisites were incomplete."],
                "source_candidates": [
                    {
                        "kind": "runtime_component",
                        "managed_type": "Example.Component",
                    },
                    {
                        "kind": "runtime_component",
                        "managed_type": "Example.Component",
                    },
                    {
                        "kind": "runtime_script",
                        "path": "Assets/Runtime.cs",
                    },
                    {
                        "kind": "runtime_script",
                        "path": "Assets/Overflow.cs",
                    },
                ],
                "custom_editor_candidates": [
                    {"type": "Example.UnselectedEditor", "active": False},
                    {"type": "Example.ActiveEditor", "active": True},
                ],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                            "view_name": "overview",
                        },
                    )
                )

        data = dict(result["data"])
        target = data.get("target")
        target_data = target if isinstance(target, dict) else {}
        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                False,
                "INSPECTOR_PROFILE_REQUIRED",
                "degraded",
                ["Assembly prerequisites were incomplete."],
                [
                    {
                        "kind": "runtime_component",
                        "managed_type": "Example.Component",
                    },
                    {
                        "kind": "runtime_script",
                        "path": "Assets/Runtime.cs",
                    },
                ],
                [{"type": "Example.ActiveEditor", "active": True}],
                [],
                {
                    "status": "degraded",
                    "reasons": ["PropertyDrawer discovery is not available from the public Unity API boundary."],
                },
                None,
                ["Runtime script source path is unavailable."],
            ),
            (
                result["success"],
                result["code"],
                data.get("source_candidates_status"),
                data.get("source_candidates_reasons"),
                data.get("source_candidates"),
                data.get("custom_editor_candidates"),
                data.get("property_drawer_candidates"),
                data.get("property_drawer_candidates_status"),
                target_data.get("script_path"),
                target_data.get("script_path_degradation_reasons"),
            ),
            msg=f"candidate degradation evidence or public bounds drifted: {result!r}",
        )


class TestValidateInspectorProfile(unittest.TestCase):
    def test_outside_project_profile_fails_before_target_or_bridge_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            (project_root / "Assets").mkdir(parents=True)
            outside_profile = root / "outside.json"
            outside_profile.write_text(json.dumps(_valid_profile()), encoding="utf-8")
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                try:
                    result = structured_payload(call_tool_result(server,
                            "validate_inspector_profile",
                            {
                                "profile_path": str(outside_profile),
                                "asset_path": "",
                            },
                        )
                    )
                except ToolError as exc:
                    self.fail(f"expected INSPECTOR_PROFILE_INVALID, observed tool error: {exc}")

        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_PROFILE_INVALID",
                "The inspector profile path is unsafe.",
                "profile_path",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                result["data"]["field"],
            ),
            msg=f"unsafe profile path must precede malformed target handling; got {result!r}",
        )
        mock_send.assert_not_called()

    def test_unsafe_project_and_bundled_entries_fail_before_target_or_bridge(self) -> None:
        for root_kind in ("project", "bundled"):
            for entry_kind in ("symlink", "directory", "fifo"):
                with self.subTest(root=root_kind, entry=entry_kind):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        project_root = root / "project"
                        (project_root / "Assets").mkdir(parents=True)
                        package_root = root / "package"
                        bundled_root = package_root / "resources" / "profiles"
                        bundled_root.mkdir(parents=True)
                        profile_root = project_root if root_kind == "project" else bundled_root
                        unsafe_profile = profile_root / "unsafe.json"
                        if entry_kind == "symlink":
                            target = root / "target.json"
                            target.write_text(json.dumps(_valid_profile()), encoding="utf-8")
                            unsafe_profile.symlink_to(target)
                        elif entry_kind == "directory":
                            unsafe_profile.mkdir()
                        else:
                            os.mkfifo(unsafe_profile)
                        server = create_server(project_root=project_root)

                        with (
                            patch(
                                "prefab_sentinel.inspector_profiles.application.files",
                                return_value=package_root,
                            ),
                            patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send,
                        ):
                            result = structured_payload(call_tool_result(server,
                                    "validate_inspector_profile",
                                    {
                                        "profile_path": str(unsafe_profile),
                                        "asset_path": "",
                                    },
                                )
                            )

                    self.assertEqual(
                        (
                            False,
                            "warning",
                            "INSPECTOR_PROFILE_INVALID",
                            {"field": "profile_path"},
                            "INSPECTOR_PROFILE_PATH_UNSAFE",
                            False,
                        ),
                        (
                            result["success"],
                            result["severity"],
                            result["code"],
                            result["data"],
                            result["diagnostics"][0]["code"],
                            temporary in json.dumps(result),
                        ),
                    )
                    mock_send.assert_not_called()

    def test_contained_valid_profile_preserves_authored_evidence_without_semantic_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            profile_path = project_root / "draft.json"
            profile_path.write_text(json.dumps(_valid_profile()), encoding="utf-8")
            server = create_server(project_root=project_root)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": "Assets/Example.cs",
                },
                "properties": [
                    {
                        "path": "exampleField",
                        "property_type": "String",
                        "source_value": "base",
                        "effective_value": "saved",
                        "origin": None,
                        "array_size": None,
                        "element_type": None,
                    }
                ],
                "source_candidates_status": "complete",
                "source_candidates": [],
                "custom_editor_candidates": [],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(profile_path),
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                True,
                "info",
                "INSPECTOR_PROFILE_VALIDATION_RESULT",
                "draft.json",
                True,
                "high",
                [{"kind": "source", "detail": "Source binds exampleField."}],
                [],
                {"overview": {"enabled": False}},
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["profile_path"],
                result["data"]["valid"],
                result["data"]["confidence"],
                result["data"]["evidence"],
                result["data"]["length_mismatches"],
                result["data"]["writable"],
            ),
            msg=f"mechanical validation must preserve authored evidence without promotion: {result!r}",
        )

    def test_zipped_length_mismatch_is_warning_only_and_disables_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            profile = _valid_profile()
            profile["views"] = [
                {
                    "name": "rows",
                    "kind": "zipped_arrays",
                    "evidence": [],
                    "limitations": [],
                    "arrays": [
                        {"name": "first", "path": "firstArray", "element_type": "String"},
                        {"name": "second", "path": "secondArray", "element_type": "String"},
                        {"name": "third", "path": "thirdArray", "element_type": "String"},
                    ],
                    "writable": {
                        "enabled": True,
                        "operations": ["set_element"],
                        "requires_equal_array_lengths": True,
                    },
                }
            ]
            profile_path = project_root / "draft.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            server = create_server(project_root=project_root)
            properties = [
                {
                    "path": path,
                    "property_type": "Array",
                    "source_value": None,
                    "effective_value": None,
                    "origin": None,
                    "array_size": size,
                    "element_type": "String",
                }
                for path, size in (("firstArray", 3), ("secondArray", 2), ("thirdArray", 4))
            ]
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": "Assets/Example.cs",
                },
                "properties": properties,
                "source_candidates_status": "complete",
                "source_candidates": [],
                "custom_editor_candidates": [],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(profile_path),
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                True,
                "warning",
                "INSPECTOR_PROFILE_VALIDATION_RESULT",
                True,
                [
                    {
                        "view_name": "rows",
                        "lengths": {"firstArray": 3, "secondArray": 2, "thirdArray": 4},
                    }
                ],
                {"rows": {"enabled": False}},
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["valid"],
                result["data"]["length_mismatches"],
                result["data"]["writable"],
            ),
            msg=f"zipped mismatch must remain valid, report exact lengths, and disable writes: {result!r}",
        )

    def test_invalid_utf8_profile_returns_invalid_before_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            profile_path = project_root / "invalid.json"
            profile_path.write_bytes(b"\xff")
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(profile_path),
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        self.assertEqual(
            (False, "warning", "INSPECTOR_PROFILE_INVALID", "profile_path", False),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["field"],
                temporary in json.dumps(result),
            ),
            msg=f"invalid UTF-8 must use the profile-invalid envelope: {result!r}",
        )
        mock_send.assert_not_called()

    def test_schema_invalid_profile_returns_before_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            profile = _valid_profile()
            profile["views"] = "invalid"
            profile_path = project_root / "invalid.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(profile_path),
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        self.assertEqual(
            (False, "warning", "INSPECTOR_PROFILE_INVALID", "invalid.json", False, False),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["profile_path"],
                result["data"]["valid"],
                temporary in json.dumps(result),
            ),
            msg=f"schema-invalid profile must fail mechanically before Bridge access: {result!r}",
        )
        mock_send.assert_not_called()

    def test_writable_path_uses_dry_run_addressability_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _write_component_prefab(project_root)
            (project_root / "Assets" / "Example.cs").write_text(
                "public class Example {}\n",
                encoding="utf-8",
            )
            (project_root / "Assets" / "Example.cs.meta").write_text(
                f"guid: {'a' * 32}\n",
                encoding="utf-8",
            )
            profile = _valid_profile()
            profile["views"][0]["writable"] = {
                "enabled": True,
                "operations": ["set"],
            }
            profile_path = project_root / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            server = create_server(project_root=project_root)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "local_file_id": 3,
                    "script_path": "Assets/Example/Component.cs",
                },
                "source_candidates_status": "complete",
                "source_candidates": [],
                "custom_editor_candidates": [],
                "properties": [
                    {
                        "path": "exampleField",
                        "property_type": "String",
                        "source_value": "base",
                        "effective_value": "saved",
                        "origin": None,
                        "array_size": None,
                        "element_type": None,
                    }
                ],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }
            orchestrator = Mock()
            orchestrator.serialized_value_patch_apply.return_value = Mock(
                success=False,
            )

            with (
                patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send,
                patch(
                    "prefab_sentinel.inspector_profiles.application.ProjectSession.get_orchestrator",
                    return_value=orchestrator,
                ),
            ):
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(profile_path),
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )
        self.assertEqual(
            (False, "INSPECTOR_PROFILE_INVALID", {"overview": {"enabled": False}}),
            (
                result["success"],
                result["code"],
                result["data"]["writable"],
            ),
            msg=f"dry-run rejection must disable the writable profile declaration: {result!r}",
        )
        self.assertIn(
            "writable path is not addressable: exampleField",
            result["diagnostics"][0]["message"],
            msg=f"dry-run rejection must retain the rejected property path: {result!r}",
        )
        orchestrator.serialized_value_patch_apply.assert_called_once_with(
            plan={
                "plan_version": 2,
                "resources": [
                    {
                        "id": "target",
                        "path": "Assets/Test.prefab",
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "target",
                        "op": "set",
                        "file_id": "3",
                        "path": "exampleField",
                        "value": "saved",
                    }
                ],
            },
            dry_run=True,
            confirm=False,
            change_reason=None,
        )
        orchestrator.serialized_object.dry_run_patch.assert_not_called()


    def test_zipped_writer_dry_runs_each_declared_row_operation_at_real_addresses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _write_component_prefab(project_root)
            (project_root / "Assets" / "Example.cs").write_text(
                "public class Example {}\n",
                encoding="utf-8",
            )
            (project_root / "Assets" / "Example.cs.meta").write_text(
                f"guid: {'a' * 32}\n",
                encoding="utf-8",
            )
            profile = _valid_profile()
            profile["views"] = [
                {
                    "name": "rows",
                    "kind": "zipped_arrays",
                    "evidence": [],
                    "limitations": [],
                    "arrays": [
                        {"name": "first", "path": "firstArray", "element_type": "String"},
                        {"name": "second", "path": "secondArray", "element_type": "String"},
                    ],
                    "writable": {
                        "enabled": True,
                        "operations": ["set_element", "append_row", "remove_row"],
                        "requires_equal_array_lengths": True,
                    },
                }
            ]
            profile_path = project_root / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            server = create_server(project_root=project_root)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "local_file_id": 3,
                    "script_path": "Assets/Example/Component.cs",
                },
                "source_candidates_status": "complete",
                "source_candidates": [],
                "custom_editor_candidates": [],
                "properties": [
                    {
                        "path": path,
                        "property_type": "Array",
                        "source_value": None,
                        "effective_value": None,
                        "origin": None,
                        "array_size": 2,
                        "element_type": "String",
                    }
                    for path in ("firstArray", "secondArray")
                ]
                + [
                    {
                        "path": f"{path}.Array.data[{index}]",
                        "property_type": "String",
                        "source_value": value,
                        "effective_value": value,
                        "origin": None,
                        "array_size": None,
                        "element_type": None,
                    }
                    for path, values in (
                        ("firstArray", ("first-0", "first-1")),
                        ("secondArray", ("second-0", "second-1")),
                    )
                    for index, value in enumerate(values)
                ],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }
            orchestrator = Mock()
            orchestrator.serialized_value_patch_apply.return_value = Mock(success=True)

            with (
                patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ),
                patch(
                    "prefab_sentinel.inspector_profiles.application.ProjectSession.get_orchestrator",
                    return_value=orchestrator,
                ),
            ):
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(profile_path),
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        self.assertEqual(
            (True, {"rows": profile["views"][0]["writable"]}),
            (result["success"], result["data"]["writable"]),
            msg=f"all accepted row operations must keep the declaration writable: {result!r}",
        )
        resources = [
            {
                "id": "target",
                "path": "Assets/Test.prefab",
                "mode": "open",
            }
        ]
        self.assertEqual(
            [
                call(
                    plan={
                        "plan_version": 2,
                        "resources": resources,
                        "ops": [
                            {
                                "resource": "target",
                                "op": "set",
                                "file_id": "3",
                                "path": "firstArray.Array.data[0]",
                                "value": "first-0",
                            },
                            {
                                "resource": "target",
                                "op": "set",
                                "file_id": "3",
                                "path": "secondArray.Array.data[0]",
                                "value": "second-0",
                            },
                        ],
                    },
                    dry_run=True,
                    confirm=False,
                    change_reason=None,
                ),
                call(
                    plan={
                        "plan_version": 2,
                        "resources": resources,
                        "ops": [
                            {
                                "resource": "target",
                                "op": "insert_array_element",
                                "file_id": "3",
                                "path": "firstArray.Array.data",
                                "index": 2,
                                "value": "first-1",
                            },
                            {
                                "resource": "target",
                                "op": "insert_array_element",
                                "file_id": "3",
                                "path": "secondArray.Array.data",
                                "index": 2,
                                "value": "second-1",
                            },
                        ],
                    },
                    dry_run=True,
                    confirm=False,
                    change_reason=None,
                ),
                call(
                    plan={
                        "plan_version": 2,
                        "resources": resources,
                        "ops": [
                            {
                                "resource": "target",
                                "op": "remove_array_element",
                                "file_id": "3",
                                "path": "firstArray.Array.data",
                                "index": 1,
                            },
                            {
                                "resource": "target",
                                "op": "remove_array_element",
                                "file_id": "3",
                                "path": "secondArray.Array.data",
                                "index": 1,
                            },
                        ],
                    },
                    dry_run=True,
                    confirm=False,
                    change_reason=None,
                ),
            ],
            orchestrator.serialized_value_patch_apply.call_args_list,
            msg="writer validation must use the existing dry-run patch path with exact row addresses",
        )


class TestInspectorSurfaceBlockers(unittest.TestCase):
    def _assert_blocker(self, bridge_code: str) -> None:
        server = create_server(project_root="/project")
        bridge_response = {
            "success": False,
            "severity": "error",
            "code": bridge_code,
            "message": "Editor Bridge unavailable.",
            "data": {},
            "diagnostics": [],
        }

        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {"asset_path": "Assets/Test.asset"},
                )
            )

        diagnostics = result["diagnostics"]
        diagnostic_code = (
            diagnostics[0].get("code")
            if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict)
            else None
        )
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                {},
                bridge_code,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"],
                diagnostic_code,
            ),
            msg=f"Bridge blocker provenance drifted for {bridge_code}: {result!r}",
        )
        mock_send.assert_called_once()

    def test_timeout_is_a_structured_surface_blocker(self) -> None:
        self._assert_blocker("EDITOR_BRIDGE_TIMEOUT")

    def test_dirty_scriptable_object_is_a_structured_surface_blocker(self) -> None:
        self._assert_blocker("EDITOR_CTRL_INSPECTOR_SURFACE_DIRTY")

    def test_transport_blockers_redact_arbitrary_bridge_data(self) -> None:
        secret = "/secret/inspector-transport"
        watch_dir_guidance = {
            "blocker_class": "watch_dir",
            "suggested_next_action": ("Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory."),
        }
        connection_guidance = {
            "blocker_class": "bridge_connection",
            "suggested_next_action": (
                "Confirm Unity is running and the PrefabSentinel Editor Bridge watcher is active."
            ),
        }
        cases: tuple[tuple[str, dict[str, str]], ...] = (
            ("EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND", watch_dir_guidance),
            ("EDITOR_BRIDGE_WRITE", watch_dir_guidance),
            ("EDITOR_BRIDGE_RESPONSE_READ", {}),
            ("EDITOR_BRIDGE_TIMEOUT", connection_guidance),
        )

        for bridge_code, expected_data in cases:
            with self.subTest(bridge_code=bridge_code):
                server = create_server(project_root="/project")
                bridge_response = {
                    "success": False,
                    "severity": "error",
                    "code": bridge_code,
                    "message": f"transport failure at {secret}",
                    "data": {
                        "blocker_class": "caller-controlled",
                        "suggested_next_action": secret,
                        "error": secret,
                        "request_file": f"{secret}/request.json",
                        "response_file": f"{secret}/response.json",
                    },
                    "diagnostics": [],
                }
                with patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send:
                    result = structured_payload(call_tool_result(server,
                            "inspect_serialized_surface",
                            {"asset_path": "Assets/Test.asset"},
                        )
                    )

                diagnostic = result["diagnostics"][0]
                mock_send.assert_called_once_with(
                    action="editor_inspect_serialized_surface",
                    asset_path="Assets/Test.asset",
                    include_override_origin=False,
                    expected_project_root=application.to_windows_path("/project"),
                )

                self.assertEqual(
                    (
                        "error",
                        bridge_code,
                        "Editor Bridge request failed.",
                        expected_data,
                        False,
                    ),
                    (
                        diagnostic["severity"],
                        diagnostic["code"],
                        diagnostic["message"],
                        diagnostic["data"],
                        secret in json.dumps(result),
                    ),
                    msg=(f"Inspector blockers must recompute stable guidance without transport internals: {result!r}"),
                )

    def test_project_root_mismatch_is_a_structured_surface_blocker(self) -> None:
        self._assert_blocker("EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH")


class TestInspectSerializedSurfaceAddresses(unittest.TestCase):
    def _assert_invalid(
        self,
        asset_path: str,
        symbol_path: str | None,
        field: str,
    ) -> None:
        server = create_server(project_root="/project")
        arguments: dict[str, Any] = {"asset_path": asset_path}
        if symbol_path is not None:
            arguments["symbol_path"] = symbol_path

        with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
            result = structured_payload(call_tool_result(server,"inspect_serialized_surface", arguments))

        self.assertEqual(
            (False, "error", "INSPECTOR_SURFACE_ADDRESS_INVALID", field),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"].get("field"),
            ),
            msg=f"malformed raw-surface address was not rejected exactly: {result!r}",
        )
        mock_send.assert_not_called()

    def test_component_asset_requires_symbol_path_before_bridge(self) -> None:
        self._assert_invalid("Assets/Test.prefab", None, "symbol_path")

    def test_scriptable_object_root_rejects_symbol_path_before_bridge(self) -> None:
        self._assert_invalid("Assets/Test.asset", "Root", "symbol_path")

    def test_unsupported_asset_kind_fails_before_bridge(self) -> None:
        self._assert_invalid("Assets/Test.mat", None, "asset_path")

    def test_absolute_asset_path_fails_before_bridge(self) -> None:
        self._assert_invalid(
            "/project/Assets/Test.prefab",
            "Root/MonoBehaviour(Example)",
            "asset_path",
        )

    def test_traversal_asset_path_fails_before_bridge(self) -> None:
        self._assert_invalid(
            "Assets/../Outside/Test.prefab",
            "Root/MonoBehaviour(Example)",
            "asset_path",
        )

    def test_symlink_asset_path_fails_before_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            assets = project_root / "Assets"
            assets.mkdir()
            outside = project_root / "outside.prefab"
            outside.write_text("outside", encoding="utf-8")
            (assets / "Linked.prefab").symlink_to(outside)
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_serialized_surface",
                        {
                            "asset_path": "Assets/Linked.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        self.assertEqual(
            (False, "error", "INSPECTOR_SURFACE_ADDRESS_INVALID", "asset_path"),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["field"],
            ),
            msg=f"symlinked Inspector asset must fail before Bridge access: {result!r}",
        )
        mock_send.assert_not_called()

    def test_symlinked_asset_directory_fails_before_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            assets = project_root / "Assets"
            target_directory = assets / "Real"
            target_directory.mkdir(parents=True)
            (target_directory / "Test.prefab").write_text("prefab", encoding="utf-8")
            (assets / "Linked").symlink_to(target_directory, target_is_directory=True)
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_serialized_surface",
                        {
                            "asset_path": "Assets/Linked/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        self.assertEqual(
            (False, "error", "INSPECTOR_SURFACE_ADDRESS_INVALID", "asset_path"),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["field"],
            ),
            msg=f"symlinked Inspector directory must fail before Bridge access: {result!r}",
        )
        mock_send.assert_not_called()

    def test_scriptable_object_root_reaches_bridge_without_component_address(self) -> None:
        server = create_server(project_root="/project")
        surface = {
            "target": {
                "managed_type": "Example.Settings",
                "assembly": "Example.Assembly",
                "script_guid": "b" * 32,
                "script_file_id": 11500000,
                "script_path": "Assets/ExampleSettings.cs",
            },
            "properties": [
                {
                    "path": "mode",
                    "name": "mode",
                    "display_name": "Mode",
                    "property_type": "Enum",
                    "source_value": {"index": 1, "name": "Manual"},
                    "effective_value": {"index": 1, "name": "Manual"},
                    "origin": None,
                    "array_size": None,
                    "element_type": None,
                },
                {
                    "path": "targets",
                    "name": "targets",
                    "display_name": "Targets",
                    "property_type": "Array",
                    "source_value": None,
                    "effective_value": None,
                    "origin": None,
                    "array_size": 2,
                    "element_type": "ObjectReference",
                },
            ],
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
        }
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
            "message": "Serialized surface inspected.",
            "data": {"serialized_surface_json": json.dumps(surface)},
            "diagnostics": [],
        }

        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_serialized_surface",
                    {
                        "asset_path": "Assets/Settings.asset",
                        "include_override_origin": False,
                    },
                )
            )

        self.assertEqual(
            (True, "INSPECTOR_SERIALIZED_SURFACE_OK", surface),
            (result["success"], result["code"], result["data"].get("surface")),
            msg=f"ScriptableObject root surface or metadata was altered: {result!r}",
        )
        mock_send.assert_called_once_with(
            action="editor_inspect_serialized_surface",
            asset_path="Assets/Settings.asset",
            include_override_origin=False,
            expected_project_root=application.to_windows_path("/project"),
        )


class TestInspectorProfileWorkflowStates(unittest.TestCase):
    def test_selected_offline_valid_profile_plus_bridge_loss_is_only_a_surface_blocker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _write_component_prefab(project_root)
            profile_root = project_root / ".prefab-sentinel" / "profiles"
            profile_root.mkdir(parents=True)
            (profile_root / "valid.json").write_text(
                json.dumps(_valid_profile()),
                encoding="utf-8",
            )
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_BRIDGE_TIMEOUT",
                "message": "Editor Bridge response timed out.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                            "view_name": "overview",
                        },
                    )
                )

        diagnostics = result["diagnostics"]
        diagnostic_code = (
            diagnostics[0].get("code")
            if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict)
            else None
        )
        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(guid:aaaaaaaa)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                {},
                "EDITOR_BRIDGE_TIMEOUT",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"],
                diagnostic_code,
            ),
            msg=f"selected profile plus Bridge loss fabricated authoring state: {result!r}",
        )

    def test_managed_identity_profiles_plus_bridge_loss_remain_surface_blockers(
        self,
    ) -> None:
        for assembly in (None, "Example.Assembly"):
            with self.subTest(assembly=assembly), tempfile.TemporaryDirectory() as temporary:
                project_root = Path(temporary)
                _write_component_prefab(project_root)
                (project_root / "Assets" / "Component.cs").write_text(
                    "namespace Example { public class Component {} }\n",
                    encoding="utf-8",
                )
                (project_root / "Assets" / "Component.cs.meta").write_text(
                    f"guid: {'a' * 32}\n",
                    encoding="utf-8",
                )
                profile = _valid_profile()
                profile_target: dict[str, Any] = {
                    "managed_type": "Example.Component",
                }
                if assembly is not None:
                    profile_target["assembly"] = assembly
                profile["target"] = profile_target
                profile_root = project_root / ".prefab-sentinel" / "profiles"
                profile_root.mkdir(parents=True)
                (profile_root / "managed.json").write_text(
                    json.dumps(profile),
                    encoding="utf-8",
                )
                server = create_server(project_root=project_root)
                bridge_response = {
                    "success": False,
                    "severity": "error",
                    "code": "EDITOR_BRIDGE_TIMEOUT",
                    "message": "Editor Bridge response timed out.",
                    "data": {},
                    "diagnostics": [],
                }

                with patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send:
                    result = structured_payload(call_tool_result(server,
                            "inspect_with_profile",
                            {
                                "asset_path": "Assets/Test.prefab",
                                "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                                "view_name": "overview",
                            },
                        )
                    )

                diagnostics = result["diagnostics"]
                diagnostic_code = (
                    diagnostics[0].get("code")
                    if (
                        isinstance(diagnostics, list)
                        and diagnostics
                        and isinstance(diagnostics[0], dict)
                    )
                    else None
                )
                _assert_component_surface_request(
                    mock_send,
                    expected_project_root=application.to_windows_path(
                        str(project_root)
                    ),
                    asset_path="Assets/Test.prefab",
                    symbol_path="Root/MonoBehaviour(guid:aaaaaaaa)",
                    include_override_origin=False,
                )
                self.assertEqual(
                    (
                        False,
                        "warning",
                        "INSPECTOR_SURFACE_UNAVAILABLE",
                        {},
                        "EDITOR_BRIDGE_TIMEOUT",
                    ),
                    (
                        result["success"],
                        result["severity"],
                        result["code"],
                        result["data"],
                        diagnostic_code,
                    ),
                    msg=(
                        "an assembly-unknown offline identity must not fabricate a "
                        f"profile-required state for target={profile_target!r}: {result!r}"
                    ),
                )

    def test_unrelated_managed_profile_plus_bridge_loss_still_requires_authoring(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _write_component_prefab(project_root)
            (project_root / "Assets" / "Component.cs").write_text(
                "namespace Example { public class Component {} }\n",
                encoding="utf-8",
            )
            (project_root / "Assets" / "Component.cs.meta").write_text(
                f"guid: {'a' * 32}\n",
                encoding="utf-8",
            )
            profile = _valid_profile()
            profile["target"] = {"managed_type": "Example.Controller"}
            profile_root = project_root / ".prefab-sentinel" / "profiles"
            profile_root.mkdir(parents=True)
            (profile_root / "unrelated.json").write_text(
                json.dumps(profile),
                encoding="utf-8",
            )
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_BRIDGE_TIMEOUT",
                "message": "Editor Bridge response timed out.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(guid:aaaaaaaa)",
                            "view_name": "overview",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(guid:aaaaaaaa)",
            include_override_origin=False,
        )
        self.assertEqual(
            (False, "info", "INSPECTOR_PROFILE_REQUIRED"),
            (
                result["success"],
                result["severity"],
                result["code"],
            ),
            msg=f"an unrelated managed profile must not suppress authoring: {result!r}",
        )


    def test_writable_profile_inspection_revalidates_addressability_with_dry_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            _write_component_prefab(project_root)
            (project_root / "Assets" / "Example.cs").write_text(
                "public class Example {}\n",
                encoding="utf-8",
            )
            (project_root / "Assets" / "Example.cs.meta").write_text(
                f"guid: {'a' * 32}\n",
                encoding="utf-8",
            )
            profile = _valid_profile()
            profile["views"][0]["writable"] = {
                "enabled": True,
                "operations": ["set"],
            }
            profile_root = project_root / ".prefab-sentinel" / "profiles"
            profile_root.mkdir(parents=True)
            (profile_root / "profile.json").write_text(
                json.dumps(profile),
                encoding="utf-8",
            )
            server = create_server(project_root=project_root)
            surface = {
                "target": {
                    "managed_type": "Example.Component",
                    "assembly": "Example.Assembly",
                    "script_guid": "a" * 32,
                    "script_file_id": 11500000,
                    "script_path": "Assets/Example/Component.cs",
                    "local_file_id": 9001,
                },
                "source_candidates_status": "complete",
                "source_candidates": [],
                "custom_editor_candidates": [],
                "properties": [
                    {
                        "path": "exampleField",
                        "property_type": "String",
                        "source_value": "base",
                        "effective_value": "saved",
                        "origin": None,
                        "array_size": None,
                        "element_type": None,
                    }
                ],
            }
            bridge_response = {
                "success": True,
                "severity": "info",
                "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
                "message": "Serialized surface inspected.",
                "data": {"serialized_surface_json": json.dumps(surface)},
                "diagnostics": [],
            }
            orchestrator = Mock()
            orchestrator.serialized_value_patch_apply.return_value = Mock(success=True)

            with (
                patch(
                    "prefab_sentinel.inspector_profiles.application.send_action",
                    return_value=bridge_response,
                ) as mock_send,
                patch(
                    "prefab_sentinel.inspector_profiles.application.ProjectSession.get_orchestrator",
                    return_value=orchestrator,
                ),
            ):
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                            "view_name": "overview",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )
        self.assertEqual(
            (
                True,
                "INSPECTOR_PROFILE_VIEW_OK",
                {"enabled": True, "operations": ["set"]},
            ),
            (
                result["success"],
                result["code"],
                result["data"]["views"][0]["writable"],
            ),
            msg=f"writable inspection must follow a successful current-target probe: {result!r}",
        )
        orchestrator.serialized_value_patch_apply.assert_called_once()
        call = orchestrator.serialized_value_patch_apply.call_args
        self.assertEqual(
            (True, False, None),
            (
                call.kwargs["dry_run"],
                call.kwargs["confirm"],
                call.kwargs["change_reason"],
            ),
            msg=f"inspection writer probe must remain a dry-run: {call!r}",
        )
        self.assertEqual(
            [
                {
                    "resource": "target",
                    "op": "set",
                    "file_id": "9001",
                    "path": "exampleField",
                    "value": "saved",
                }
            ],
            call.kwargs["plan"]["ops"],
            msg=f"inspection writer probe must use Unity's inspected target local fileID: {call!r}",
        )
        orchestrator.serialized_object.dry_run_patch.assert_not_called()


class TestInspectorProfileWorkflowPartitions(unittest.TestCase):
    @staticmethod
    def _zipped_view() -> dict[str, Any]:
        return {
            "name": "rows",
            "kind": "zipped_arrays",
            "evidence": [],
            "limitations": [],
            "arrays": [
                {"name": "first", "path": "firstArray", "element_type": "String"},
                {"name": "second", "path": "secondArray", "element_type": "String"},
                {"name": "third", "path": "thirdArray", "element_type": "String"},
            ],
            "writable": {
                "enabled": True,
                "operations": ["set_element"],
                "requires_equal_array_lengths": True,
            },
        }

    @staticmethod
    def _surface(properties: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "target": {
                "managed_type": "Example.Component",
                "assembly": "Example.Assembly",
                "script_guid": "a" * 32,
                "script_file_id": 11500000,
                "script_path": "Assets/Example.cs",
            },
            "properties": properties,
            "source_candidates_status": "complete",
            "source_candidates": [{"kind": "runtime_script", "path": "Assets/Example.cs"}],
            "custom_editor_candidates": [],
        }

    @staticmethod
    def _property(
        path: str,
        property_type: str,
        effective_value: Any,
        array_size: int | None = None,
        element_type: str | None = None,
    ) -> dict[str, Any]:
        return {
            "path": path,
            "property_type": property_type,
            "source_value": effective_value,
            "effective_value": effective_value,
            "origin": None,
            "array_size": array_size,
            "element_type": element_type,
        }

    def _inspect(
        self,
        project_root: Path,
        profile: dict[str, Any],
        surface: dict[str, Any],
        view_name: str,
    ) -> dict[str, Any]:
        (project_root / "Assets").mkdir()
        profile_root = project_root / ".prefab-sentinel" / "profiles"
        profile_root.mkdir(parents=True)
        (profile_root / "profile.json").write_text(
            json.dumps(profile),
            encoding="utf-8",
        )
        server = create_server(project_root=project_root)
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
            "message": "Serialized surface inspected.",
            "data": {"serialized_surface_json": json.dumps(surface)},
            "diagnostics": [],
        }
        with patch(
            "prefab_sentinel.inspector_profiles.application.send_action",
            return_value=bridge_response,
        ) as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_with_profile",
                    {
                        "asset_path": "Assets/Test.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                        "view_name": view_name,
                    },
                )
            )
        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Test.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )
        if not isinstance(result, dict):
            self.fail(f"inspect_with_profile returned a non-object result: {result!r}")
        return result

    def test_missing_view_keeps_unrequested_length_mismatch_warning(self) -> None:
        profile = _valid_profile()
        profile["views"].append(self._zipped_view())
        surface = self._surface(
            [
                self._property("exampleField", "String", "effective"),
                self._property("firstArray", "Array", None, 3, "String"),
                self._property("secondArray", "Array", None, 2, "String"),
                self._property("thirdArray", "Array", None, 4, "String"),
            ]
        )

        with tempfile.TemporaryDirectory() as temporary:
            result = self._inspect(
                Path(temporary),
                profile,
                surface,
                "missing",
            )

        data = result["data"]
        self.assertEqual(
            (
                False,
                "info",
                "INSPECTOR_PROFILE_INCOMPLETE",
                ["overview", "rows"],
                [
                    {
                        "code": "INSPECTOR_ZIPPED_ARRAY_LENGTH_MISMATCH",
                        "view_name": "rows",
                        "lengths": {
                            "firstArray": 3,
                            "secondArray": 2,
                            "thirdArray": 4,
                        },
                    }
                ],
                None,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                data.get("available_views"),
                data.get("warnings"),
                data.get("views"),
            ),
            msg=f"missing view lost its distinct state or unrelated mismatch warning: {result!r}",
        )

    def test_invalid_unrequested_view_rejects_whole_profile_without_rendering(
        self,
    ) -> None:
        profile = _valid_profile()
        profile["views"].append(
            {
                "name": "broken",
                "kind": "fields",
                "evidence": [],
                "limitations": [],
                "fields": [
                    {
                        "name": "missing",
                        "label": "Missing",
                        "path": "missingField",
                    }
                ],
            }
        )
        surface = self._surface([self._property("exampleField", "String", "effective")])

        with tempfile.TemporaryDirectory() as temporary:
            result = self._inspect(
                Path(temporary),
                profile,
                surface,
                "overview",
            )

        diagnostic_paths = [
            diagnostic.get("path") for diagnostic in result["diagnostics"] if isinstance(diagnostic, dict)
        ]
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_PROFILE_INVALID",
                ["$.views[1].fields[0].path"],
                None,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                diagnostic_paths,
                result["data"].get("views"),
            ),
            msg=f"invalid unrequested view partially rendered the requested view: {result!r}",
        )

    def test_requested_length_mismatch_renders_shared_rows_and_disables_writes(
        self,
    ) -> None:
        profile = _valid_profile()
        profile["views"] = [self._zipped_view()]
        properties = [
            self._property("firstArray", "Array", None, 3, "String"),
            self._property("secondArray", "Array", None, 2, "String"),
            self._property("thirdArray", "Array", None, 4, "String"),
        ]
        for index in range(2):
            for name in ("first", "second", "third"):
                properties.append(
                    self._property(
                        f"{name}Array.Array.data[{index}]",
                        "String",
                        f"{name}-{index}",
                    )
                )
        surface = self._surface(properties)

        with tempfile.TemporaryDirectory() as temporary:
            result = self._inspect(
                Path(temporary),
                profile,
                surface,
                "rows",
            )

        self.assertEqual(
            (
                True,
                "warning",
                "INSPECTOR_PROFILE_VIEW_OK",
                [
                    {
                        "name": "rows",
                        "kind": "zipped_arrays",
                        "writable": {"enabled": False},
                        "rows": [
                            {
                                "index": 0,
                                "fields": {
                                    "first": {
                                        "path": "firstArray.Array.data[0]",
                                        "value": "first-0",
                                    },
                                    "second": {
                                        "path": "secondArray.Array.data[0]",
                                        "value": "second-0",
                                    },
                                    "third": {
                                        "path": "thirdArray.Array.data[0]",
                                        "value": "third-0",
                                    },
                                },
                            },
                            {
                                "index": 1,
                                "fields": {
                                    "first": {
                                        "path": "firstArray.Array.data[1]",
                                        "value": "first-1",
                                    },
                                    "second": {
                                        "path": "secondArray.Array.data[1]",
                                        "value": "second-1",
                                    },
                                    "third": {
                                        "path": "thirdArray.Array.data[1]",
                                        "value": "third-1",
                                    },
                                },
                            },
                        ],
                    }
                ],
                [
                    {
                        "code": "INSPECTOR_ZIPPED_ARRAY_LENGTH_MISMATCH",
                        "view_name": "rows",
                        "lengths": {
                            "firstArray": 3,
                            "secondArray": 2,
                            "thirdArray": 4,
                        },
                    }
                ],
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"].get("views"),
                result["data"].get("warnings"),
            ),
            msg=f"requested mismatch did not expose exact shared rows and disabled writes: {result!r}",
        )


class TestInspectorProfileRecommendationPaths(unittest.TestCase):
    @staticmethod
    def _bridge_response(
        managed_type: str,
        assembly: str | None,
        script_guid: str | None,
        script_file_id: int | None,
    ) -> dict[str, Any]:
        target: dict[str, Any] = {
            "managed_type": managed_type,
            "assembly": assembly,
            "script_guid": script_guid,
            "script_file_id": script_file_id,
            "script_path": None,
        }
        target["script_path_degradation_reasons"] = ["Runtime script source path is unavailable."]
        surface = {
            "target": target,
            "properties": [],
            "source_candidates_status": "complete",
            "source_candidates": [],
            "custom_editor_candidates": [],
        }
        return {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_INSPECTOR_SURFACE_OK",
            "message": "Serialized surface inspected.",
            "data": {"serialized_surface_json": json.dumps(surface)},
            "diagnostics": [],
        }

    @staticmethod
    def _call(server: Any) -> dict[str, Any]:
        result = structured_payload(call_tool_result(server,
                "inspect_with_profile",
                {
                    "asset_path": "Assets/Missing.prefab",
                    "symbol_path": "Root/MonoBehaviour(Example)",
                    "view_name": "overview",
                },
            )
        )
        if not isinstance(result, dict):
            raise AssertionError(f"inspect_with_profile returned a non-object result: {result!r}")
        return result

    def test_repeated_strong_identity_returns_same_concrete_contained_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            server = create_server(project_root=project_root)
            response = self._bridge_response(
                "Example.Component",
                "Example.Assembly",
                "a" * 32,
                11500000,
            )

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=response,
            ) as mock_send:
                paths = [self._call(server)["data"]["recommended_profile_path"] for _ in range(2)]

            expected_request = call(
                action="editor_inspect_serialized_surface",
                asset_path="Assets/Missing.prefab",
                symbol_path="Root/MonoBehaviour(Example)",
                include_override_origin=False,
                expected_project_root=application.to_windows_path(str(project_root)),
            )
            self.assertEqual(
                [expected_request, expected_request],
                mock_send.call_args_list,
                msg=f"repeated identity did not dispatch the exact Bridge request twice: {mock_send.call_args_list!r}",
            )

        recommended = Path(paths[0])
        self.assertEqual(
            (
                paths[0],
                Path(".prefab-sentinel") / "profiles",
                ".json",
                False,
            ),
            (
                paths[1],
                recommended.parent,
                recommended.suffix,
                "<" in paths[0] or ">" in paths[0],
            ),
            msg=f"repeated strong identity produced unstable or placeholder path: {paths!r}",
        )

    def test_distinct_script_and_managed_identities_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            server = create_server(project_root=project_root)
            responses = [
                self._bridge_response(
                    "Example.Component",
                    "Example.Assembly",
                    "a" * 32,
                    11500000,
                ),
                self._bridge_response(
                    "Example.Component",
                    "Example.Assembly",
                    "b" * 32,
                    11500001,
                ),
                self._bridge_response(
                    "Example.Component",
                    "Example.Assembly",
                    None,
                    None,
                ),
                self._bridge_response(
                    "Other.Component",
                    "Other.Assembly",
                    None,
                    None,
                ),
            ]

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                side_effect=responses,
            ) as mock_send:
                paths = [self._call(server)["data"]["recommended_profile_path"] for _ in responses]

            expected_request = call(
                action="editor_inspect_serialized_surface",
                asset_path="Assets/Missing.prefab",
                symbol_path="Root/MonoBehaviour(Example)",
                include_override_origin=False,
                expected_project_root=application.to_windows_path(str(project_root)),
            )
            self.assertEqual(
                [expected_request] * len(responses),
                mock_send.call_args_list,
                msg=f"distinct identities did not dispatch the exact Bridge request sequence: {mock_send.call_args_list!r}",
            )

        self.assertEqual(
            (4, Path(".prefab-sentinel") / "profiles"),
            (len(set(paths)), Path(paths[0]).parent),
            msg=f"distinct composite identities collided or escaped containment: {paths!r}",
        )


class TestInspectWithProfileAddresses(unittest.TestCase):
    def test_missing_project_precedes_malformed_asset_path(self) -> None:
        server = create_server()

        with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
            result = structured_payload(call_tool_result(server,
                    "inspect_with_profile",
                    {
                        "asset_path": "../outside.prefab",
                        "view_name": "overview",
                    },
                )
            )

        self.assertEqual(
            (
                False,
                "error",
                "PROJECT_NOT_ACTIVATED",
                "Activate a Unity project before inspecting with a profile.",
                {},
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                result["data"],
            ),
            msg=f"project activation must precede malformed address validation; got {result!r}",
        )
        mock_send.assert_not_called()

    def test_nonempty_view_rejects_malformed_component_address_without_authoring(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Test.prefab",
                            "view_name": "overview",
                        },
                    )
                )

        self.assertEqual(
            (
                False,
                "INSPECTOR_SURFACE_ADDRESS_INVALID",
                {"field": "symbol_path", "asset_path": "Assets/Test.prefab"},
            ),
            (result["success"], result["code"], result["data"]),
            msg=f"profile workflow fabricated state for malformed address: {result!r}",
        )
        mock_send.assert_not_called()

    def test_unresolved_component_preserves_address_without_authoring_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_CTRL_INSPECTOR_TARGET_NOT_FOUND",
                "message": "Target not found.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "inspect_with_profile",
                        {
                            "asset_path": "Assets/Missing.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                            "view_name": "overview",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Missing.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                False,
                "error",
                "INSPECTOR_SURFACE_TARGET_NOT_FOUND",
                {
                    "address": {
                        "asset_path": "Assets/Missing.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                    }
                },
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"],
            ),
            msg=f"unresolved profile target was misclassified as workflow state: {result!r}",
        )


class TestValidateInspectorProfileSurfaceBlocker(unittest.TestCase):
    def _assert_blocker(self, bridge_code: str) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            draft = project_root / "draft.json"
            draft_text = json.dumps(_valid_profile())
            draft.write_text(draft_text, encoding="utf-8")
            published = project_root / "published.json"
            published_text = '{"existing":true}'
            published.write_text(published_text, encoding="utf-8")
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": bridge_code,
                "message": "Editor Bridge unavailable.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(draft),
                            "asset_path": "Assets/Test.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

            _assert_component_surface_request(
                mock_send,
                expected_project_root=application.to_windows_path(str(project_root)),
                asset_path="Assets/Test.prefab",
                symbol_path="Root/MonoBehaviour(Example)",
                include_override_origin=False,
            )
            observed_files = (
                draft.read_text(encoding="utf-8"),
                published.read_text(encoding="utf-8"),
            )

        diagnostics = result["diagnostics"]
        diagnostic_code = (
            diagnostics[0].get("code")
            if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict)
            else None
        )
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_SURFACE_UNAVAILABLE",
                "The last-saved serialized surface is unavailable because the Editor Bridge could not be used.",
                bridge_code,
                (draft_text, published_text),
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                diagnostic_code,
                observed_files,
            ),
            msg=f"validation blocker or draft preservation drifted for {bridge_code}: {result!r}",
        )

    def test_watch_dir_loss_blocks_validation_without_mutation(self) -> None:
        self._assert_blocker("EDITOR_BRIDGE_WATCH_DIR_MISSING")

    def test_timeout_blocks_validation_without_mutation(self) -> None:
        self._assert_blocker("EDITOR_BRIDGE_TIMEOUT")

    def test_project_root_mismatch_blocks_validation_without_mutation(self) -> None:
        self._assert_blocker("EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH")


class TestValidateInspectorProfileAddresses(unittest.TestCase):
    def test_contained_profile_then_malformed_target_returns_address_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            draft = project_root / "draft.json"
            invalid_profile = _valid_profile()
            invalid_profile["views"] = "invalid"
            draft.write_text(json.dumps(invalid_profile), encoding="utf-8")
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(draft),
                            "asset_path": "Assets/Test.prefab",
                        },
                    )
                )

        self.assertEqual(
            (
                False,
                "INSPECTOR_SURFACE_ADDRESS_INVALID",
                {"field": "symbol_path", "asset_path": "Assets/Test.prefab"},
            ),
            (result["success"], result["code"], result["data"]),
            msg=f"contained profile did not preserve target-address failure: {result!r}",
        )
        mock_send.assert_not_called()

    def test_contained_profile_then_unresolved_target_preserves_address(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            (project_root / "Assets").mkdir()
            draft = project_root / "draft.json"
            draft.write_text(json.dumps(_valid_profile()), encoding="utf-8")
            server = create_server(project_root=project_root)
            bridge_response = {
                "success": False,
                "severity": "error",
                "code": "EDITOR_CTRL_INSPECTOR_TARGET_NOT_FOUND",
                "message": "Target not found.",
                "data": {},
                "diagnostics": [],
            }

            with patch(
                "prefab_sentinel.inspector_profiles.application.send_action",
                return_value=bridge_response,
            ) as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(draft),
                            "asset_path": "Assets/Missing.prefab",
                            "symbol_path": "Root/MonoBehaviour(Example)",
                        },
                    )
                )

        _assert_component_surface_request(
            mock_send,
            expected_project_root=application.to_windows_path(str(project_root)),
            asset_path="Assets/Missing.prefab",
            symbol_path="Root/MonoBehaviour(Example)",
            include_override_origin=False,
        )

        self.assertEqual(
            (
                False,
                "INSPECTOR_SURFACE_TARGET_NOT_FOUND",
                {
                    "address": {
                        "asset_path": "Assets/Missing.prefab",
                        "symbol_path": "Root/MonoBehaviour(Example)",
                    }
                },
            ),
            (result["success"], result["code"], result["data"]),
            msg=f"contained profile lost unresolved target address: {result!r}",
        )

    def test_unsafe_profile_path_precedes_malformed_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_root = root / "project"
            (project_root / "Assets").mkdir(parents=True)
            outside = root / "outside.json"
            outside.write_text(json.dumps(_valid_profile()), encoding="utf-8")
            server = create_server(project_root=project_root)

            with patch("prefab_sentinel.inspector_profiles.application.send_action") as mock_send:
                result = structured_payload(call_tool_result(server,
                        "validate_inspector_profile",
                        {
                            "profile_path": str(outside),
                            "asset_path": "Assets/Test.prefab",
                        },
                    )
                )

        diagnostics = result["diagnostics"]
        diagnostic_code = (
            diagnostics[0].get("code")
            if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict)
            else None
        )
        self.assertEqual(
            (
                False,
                "warning",
                "INSPECTOR_PROFILE_INVALID",
                "The inspector profile path is unsafe.",
                {"field": "profile_path"},
                "INSPECTOR_PROFILE_PATH_UNSAFE",
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                result["data"],
                diagnostic_code,
            ),
            msg=f"unsafe profile path did not precede malformed target: {result!r}",
        )
        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
