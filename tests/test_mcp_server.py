"""Tests for MCP server tool registration and invocation."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, call, patch

import pytest

import prefab_sentinel.editor_bridge as editor_bridge
from prefab_sentinel.contracts import Severity, ToolResponse
from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline
from prefab_sentinel.editor_bridge import BRIDGE_WATCH_DIR_ENV, PROTOCOL_VERSION
from prefab_sentinel.mcp_server import create_server
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.session import ProjectSession
from prefab_sentinel.symbol_tree_builder import build_symbol_tree
from tests._assertion_helpers import assert_error_envelope
from tests.yaml_helpers import (
    YAML_HEADER,
    make_gameobject,
    make_meshrenderer,
    make_meshrenderer_with_materials,
    make_monobehaviour,
    make_transform,
)


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously.

    When the result is a call_tool response (list[TextContent]), normalises
    across MCP versions to always return a 2-tuple (content_list, parsed_dict)
    so tests can use ``_, result = _run(server.call_tool(...))``.

    For other coroutines (e.g. list_tools), returns the raw result unchanged.
    """
    raw = asyncio.run(coro)
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        # MCP 1.6+ Python 3.11 venv: (content_list, dict)
        return raw
    if isinstance(raw, list) and raw and hasattr(raw[0], "text"):
        # MCP 1.6+ Python 3.12: list[TextContent] from call_tool
        parsed = json.loads(raw[0].text)
        return raw, parsed
    # list_tools() or other coroutines: return as-is
    return raw


def _simple_prefab() -> str:
    """Build a minimal synthetic prefab with one GO + Transform + MeshRenderer."""
    return YAML_HEADER + "\n".join(
        [
            make_gameobject("100", "Cube", ["200", "300"]),
            make_transform("200", "100"),
            make_meshrenderer("300", "100"),
        ]
    )


def _make_simple_meshrenderer_prefab(go_name: str = "Cube") -> str:
    """Build a minimal prefab with a MeshRenderer component for set_component_fields tests."""
    return YAML_HEADER + "\n".join(
        [
            make_gameobject("100", go_name, ["200", "300"]),
            make_transform("200", "100"),
            (
                "--- !u!23 &300\n"
                "MeshRenderer:\n"
                "  m_ObjectHideFlags: 0\n"
                "  m_GameObject: {fileID: 100}\n"
                "  m_Enabled: 1\n"
                "  m_CastShadows: 1\n"
            ),
        ]
    )


def _make_simple_monobehaviour_prefab(guid: str = "aaaa1111bbbb2222cccc3333dddd4444") -> str:
    """Build a minimal prefab with a MonoBehaviour component for set/copy_component_fields tests."""
    return YAML_HEADER + "\n".join(
        [
            make_gameobject("100", "Player", ["200", "300"]),
            make_transform("200", "100"),
            make_monobehaviour(
                "300",
                "100",
                guid=guid,
                fields={
                    "speed": "5",
                    "health": "100",
                },
            ),
        ]
    )


class TestRequireChangeReason(unittest.TestCase):
    """Unit tests for the require_change_reason helper."""

    def test_should_reject_when_confirm_true_and_empty_string(self) -> None:
        result = require_change_reason(confirm=True, change_reason="")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("error", result["severity"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        self.assertEqual("change_reason is required when confirm=True.", result["message"])
        self.assertEqual({}, result["data"])
        self.assertEqual([], result["diagnostics"])

    def test_should_reject_when_confirm_true_and_none(self) -> None:
        result = require_change_reason(confirm=True, change_reason=None)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])

    def test_should_pass_when_confirm_true_and_valid_reason(self) -> None:
        result = require_change_reason(confirm=True, change_reason="fix bug")

        self.assertIsNone(result)

    def test_should_pass_when_confirm_false_and_empty_string(self) -> None:
        result = require_change_reason(confirm=False, change_reason="")

        self.assertIsNone(result)

    def test_should_pass_when_confirm_false_and_none(self) -> None:
        result = require_change_reason(confirm=False, change_reason=None)

        self.assertIsNone(result)

    def test_should_pass_when_confirm_false_and_valid_reason(self) -> None:
        result = require_change_reason(confirm=False, change_reason="reason")

        self.assertIsNone(result)


class TestToolRegistration(unittest.TestCase):
    """Verify all expected tools are registered on the server."""

    def test_all_tools_registered(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        tool_names = {t.name for t in tools}
        expected = {
            # Existing 15 tools
            "activate_project",
            "get_project_status",
            "get_unity_symbols",
            "find_unity_symbol",
            "find_referencing_assets",
            "validate_refs",
            "validate_materials",
            "inspect_wiring",
            "inspect_variant",
            "diff_unity_symbols",
            "set_property",
            "add_component",
            "remove_component",
            "list_serialized_fields",
            "validate_field_rename",
            "check_field_coverage",
            # Editor bridge tools
            "editor_screenshot",
            "editor_select",
            "editor_frame",
            "editor_get_camera",
            "editor_set_camera",
            "editor_refresh",
            "editor_recompile",
            "editor_instantiate",
            "editor_set_material",
            "editor_delete",
            "editor_get_blend_shapes",
            "editor_set_blend_shape",
            "editor_list_menu_items",
            "editor_execute_menu_item",
            "editor_list_children",
            "editor_list_materials",
            "editor_list_roots",
            "editor_get_material_property",
            "editor_set_material_property",
            "editor_console",
            "editor_run_tests",
            "editor_find_renderers_by_material",
            "editor_rename",
            "editor_add_component",
            "editor_remove_component",
            "editor_create_udon_program_asset",
            "editor_set_property",
            "editor_safe_save_prefab",
            "editor_set_parent",
            "editor_serialized_property_read",
            "editor_serialized_property_list",
            "editor_serialized_property_write",
            "editor_create_generated_asset",
            "editor_move_asset",
            "editor_create_empty",
            "editor_create_primitive",
            "editor_create_ui_element",
            "editor_batch_create",
            "editor_batch_set_property",
            "editor_batch_set_material_property",
            "editor_open_scene",
            "editor_save_scene",
            "editor_batch_add_component",
            "editor_create_scene",
            # Reflection tool
            "editor_reflect",
            # Infrastructure tools
            "deploy_bridge",
            # Inspection + orchestrator tools
            "inspect_materials",
            "inspect_material_asset",
            "set_material_property",
            "copy_asset",
            "rename_asset",
            "delete_asset",
            "delete_assets",
            "validate_structure",
            "revert_overrides",
            "vrcsdk_upload",
            "inspect_hierarchy",
            "inspect_transform_effective_values",
            "inspect_unity_event_listeners",
            "validate_runtime",
            "validate_all_wiring",
            "inspect_serialized_surface",
            "inspect_with_profile",
            "validate_inspector_profile",
            "patch_apply",
            "copy_component_fields",
            "set_properties",
            "editor_set_properties",
            # Explicit diagnostics baseline management (#100)
            "update_diagnostics_baseline",
            # Editor exec tool (#74)
            "editor_run_script",
            # Issue #119: high-level UdonSharp authoring tools.
            "editor_add_udonsharp_component",
            "editor_set_udonsharp_field",
            "editor_wire_persistent_listener",
            # Issue #242: bridge-side scene-view refresh primitive.
            "editor_force_scene_view_refresh",
            # Issue #240: batch blend-shape write under one Undo group.
            "editor_batch_set_blend_shape",
            # Issue #236: Prefab Stage open / close primitives.
            "editor_open_prefab",
            "editor_close_prefab",
            # Issue #233: async run-script submit / poll surface.
            "editor_run_script_submit",
            "editor_run_script_poll",
            # Issue #243: AnimationClip primitives.
            "editor_inspect_animation_clip",
            "editor_create_animation_clip",
            "editor_apply_animation_clip",
            # Issue #98: live geometry read primitives.
            "editor_get_transform",
            "editor_get_bounds",
            "editor_measure_distance",
        }
        self.assertEqual(expected, tool_names)

    def test_tool_count(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        # Issue #195 added the dedicated ``editor_create_ui_element``
        # tool, bringing the registered surface from 75 to 76; issues
        # #233 / #236 / #240 / #242 / #243 add 9 more tools, bringing
        # the surface to 85; issue #71 retired the fire-and-return
        # ``editor_recompile_async`` tool, leaving 84; issue #98 adds
        # three live geometry tools, bringing the surface to 87; issue
        # #114 adds delete_asset and delete_assets, bringing it to 89;
        # issues #96 / #97 / #110 add two read-only effective inspectors;
        # issue #112 adds three generic serialized-property tools; issue
        # #99 adds the read-only material validation surface; issue #100
        # adds explicit diagnostics baseline management.
        self.assertEqual(101, len(tools))


class TestToolsCatalogDoc(unittest.TestCase):
    """Issue #48 — ``docs/tools.md`` is the canonical MCP tool catalog.

    The catalog must list every registered tool and its header count
    must equal the registered surface.  Both assertions compare the doc
    against an executable anchor (the registered tool list), so a drift
    between catalog and code is caught.
    """

    _TOOLS_MD = Path(__file__).resolve().parent.parent / "docs" / "tools.md"

    def test_editor_batch_table_lists_blend_shape_batch_tool(self) -> None:
        text = self._TOOLS_MD.read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if "`editor_batch_set_blend_shape`" in line and line.startswith("|")]
        self.assertEqual(
            1,
            len(rows),
            msg=("docs/tools.md must contain exactly one catalog row for editor_batch_set_blend_shape (issue #48)."),
        )
        cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
        self.assertEqual(
            ("editor_batch", "write"),
            (cells[1], cells[4]),
            msg=(
                "editor_batch_set_blend_shape must be cataloged under the "
                "editor_batch category as a write tool (issue #48)."
            ),
        )

    def test_catalog_header_count_equals_registered_surface(self) -> None:
        import re

        registered = len(_run(create_server().list_tools()))
        header = self._TOOLS_MD.read_text(encoding="utf-8").splitlines()[2]
        match = re.search(r"現在 (\d+) 件", header)
        assert match is not None, "docs/tools.md header must state the tool count as '現在 N 件'."
        self.assertEqual(
            registered,
            int(match.group(1)),
            msg=("docs/tools.md header count must equal the registered MCP tool surface (issue #48)."),
        )


@pytest.mark.source_text_invariant
class TestInspectorProfileDocumentation(unittest.TestCase):
    maxDiff = None
    _ROOT = Path(__file__).resolve().parent.parent

    def _read(self, relative_path: str) -> str:
        return (self._ROOT / relative_path).read_text(encoding="utf-8")

    def test_tools_and_skill_are_discoverable_from_public_catalogs(self) -> None:
        tools = self._read("docs/tools.md")
        readme = self._read("README.md")
        required_tools = (
            "inspect_serialized_surface",
            "inspect_with_profile",
            "validate_inspector_profile",
            "prefab-sentinel:inspector-profile-authoring",
            "inspector-profile.v1",
        )
        required_routes = (
            "docs/tools.md",
            "docs/api-reference.md",
            "CONFIGURATION.md",
            "ARCHITECTURE.md",
            "TESTING.md",
            "skills/inspector-profile-authoring/SKILL.md",
        )

        self.assertEqual(
            {token: True for token in required_tools},
            {token: token in tools for token in required_tools},
            msg="Inspector tools, skill, or schema are missing from docs/tools.md",
        )
        self.assertEqual(
            {token: True for token in required_routes},
            {token: token in readme for token in required_routes},
            msg="README does not route Inspector profile users to every specialist authority",
        )

    def test_readme_skill_inventory_matches_packaged_skills(self) -> None:
        readme = self._read("README.md")
        packaged_skills = tuple(sorted(path.parent.name for path in (self._ROOT / "skills").glob("*/SKILL.md")))
        documented_skills = tuple(
            sorted(
                line.split("|")[1].strip()
                for line in readme.splitlines()
                if line.startswith("| ") and "/prefab-sentinel:" in line
            )
        )

        self.assertEqual(
            packaged_skills,
            documented_skills,
            msg="README skill table does not match the packaged skill directories",
        )
        self.assertIn(
            f"{len(packaged_skills)} つのスキル",
            readme,
            msg="README packaged skill count is stale",
        )

    def test_inspector_profile_tools_are_not_documented_as_unity_free(self) -> None:
        readme = self._read("README.md")
        required = (
            "YAML-backed read-only 経路",
            "INSPECTOR_SURFACE_UNAVAILABLE",
            "read-only だが、常駐 Editor Bridge が前提",
        )
        stale = (
            "read-only 経路（`validate_refs` / `validate_materials` / `inspect_*` / `find_*` 等）は Unity を起動せず YAML 直読みで完結する。",
            "read-only 検査のみなら設定不要",
            "read-only 検査（`validate_*` / `inspect_*` / `find_*`）は Unity 不要",
        )

        self.assertEqual(
            (
                {token: True for token in required},
                {token: False for token in stale},
            ),
            (
                {token: token in readme for token in required},
                {token: token in readme for token in stale},
            ),
            msg="README must distinguish YAML-backed inspection from Bridge-backed Inspector profiles",
        )

    def test_api_reference_catalogues_inspector_profile_envelopes(self) -> None:
        api = self._read("docs/api-reference.md")
        required = (
            "INSPECTOR_SERIALIZED_SURFACE_OK",
            "INSPECTOR_PROFILE_VIEW_OK",
            "INSPECTOR_PROFILE_VALIDATION_RESULT",
            "PROJECT_NOT_ACTIVATED",
            "Activate a Unity project before inspecting a serialized surface.",
            "Activate a Unity project before inspecting with a profile.",
            "Activate a Unity project before validating an inspector profile.",
            "INSPECTOR_SURFACE_ADDRESS_INVALID",
            "INSPECTOR_SURFACE_TARGET_NOT_FOUND",
            "INSPECTOR_SURFACE_UNAVAILABLE",
            "INSPECTOR_PROFILE_REQUIRED",
            "INSPECTOR_PROFILE_INCOMPLETE",
            "INSPECTOR_PROFILE_INVALID",
            "INSPECTOR_VIEW_NAME_REQUIRED",
            "INSPECTOR_ZIPPED_ARRAY_LENGTH_MISMATCH",
            "INSPECTOR_PROFILE_PATH_UNSAFE",
            "source_candidates_reasons",
            "recommended_profile_path",
        )

        self.assertEqual(
            {token: True for token in required},
            {token: token in api for token in required},
            msg="Inspector profile success/error envelopes are incomplete in api-reference.md",
        )

    def test_architecture_and_configuration_preserve_authority_and_safety(self) -> None:
        architecture = self._read("ARCHITECTURE.md")
        configuration = self._read("CONFIGURATION.md")
        architecture_tokens = (
            "InspectorProfileApplication",
            "Editor Bridge",
            "last-saved",
            "project-local",
            "declarative",
        )
        configuration_tokens = (
            ".prefab-sentinel/profiles/",
            ".prefab-sentinel/profile-drafts/",
            "explicit_user_request",
            "no per-call writable bypass",
            "validate_inspector_profile",
        )

        self.assertEqual(
            {token: True for token in architecture_tokens},
            {token: token in architecture for token in architecture_tokens},
            msg="Inspector profile architecture boundaries are incomplete",
        )
        self.assertEqual(
            {token: True for token in configuration_tokens},
            {token: token in configuration for token in configuration_tokens},
            msg="Inspector profile discovery, draft, or writer configuration is incomplete",
        )

    def test_testing_documents_exact_post_takt_unity_inspector_protocol(self) -> None:
        testing = self._read("TESTING.md")
        required = (
            "Post-TAKT Unity Inspector verification",
            "compile errors = 0",
            "inspect_serialized_surface",
            "inspect_with_profile",
            "ExampleVideoCore",
            "ScriptableObject",
            "ObjectReference",
            "nested",
            "variant",
            "candidate",
            "INSPECTOR_PROFILE_REQUIRED",
            "INSPECTOR_PROFILE_INCOMPLETE",
            "INSPECTOR_PROFILE_INVALID",
        )

        self.assertEqual(
            {token: True for token in required},
            {token: token in testing for token in required},
            msg="TESTING.md does not preserve the complete deferred Unity Inspector checklist",
        )


@pytest.mark.source_text_invariant
class TestSetPropertiesDocumentation(unittest.TestCase):
    def test_api_reference_scopes_shared_report_codes_by_caller(self) -> None:
        api_reference = (
            Path(__file__).resolve().parent.parent / "docs/api-reference.md"
        ).read_text(encoding="utf-8")
        required = (
            "`set_properties` confirmed report preflight",
            "`OUT_REPORT_REQUIRED`, `OUT_REPORT_OUTSIDE_PROJECT`, and "
            "`OUT_REPORT_WRITE_FAILED`",
            "`PATCH_APPLY_RESULT` | SerializedValue apply boundary",
            "Exactly-one open Prefab transaction",
            "one-shot reservation child",
        )
        stale = (
            "Exactly-one open Prefab transaction の report preflight failure。",
        )

        self.assertEqual(
            (
                {token: True for token in required},
                {token: False for token in stale},
            ),
            (
                {token: token in api_reference for token in required},
                {token: token in api_reference for token in stale},
            ),
            msg="API report errors must distinguish shared codes from transaction-only reservation behavior",
        )

    def test_returned_writer_failure_uncertainty_is_documented_for_both_writers(
        self,
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        api_reference = (root / "docs/api-reference.md").read_text(encoding="utf-8")
        execution_reference = (root / "docs/execution-reference.md").read_text(
            encoding="utf-8",
        )
        api_contract = next(
            line
            for line in api_reference.splitlines()
            if line.startswith("| `PATCH_APPLY_RESULT` |")
        )
        execution_contract = execution_reference.split(
            "## `set_properties` パラメータ",
            maxsplit=1,
        )[1].split(
            "## `editor_set_properties` パラメータ",
            maxsplit=1,
        )[0]
        required = {
            "api": (
                "`set_property`",
                "`set_properties`",
                "例外を送出せず `success=false`",
                "`data.read_only=false`",
                "`data.state_unknown=true`",
                "後続 write の前に serialized state を再検査",
            ),
            "execution": (
                "`set_property`",
                "`set_properties`",
                "returned failure",
                "`success=false`",
                "`data.read_only=false`",
                "`data.state_unknown=true`",
                "後続 write の前に serialized state を再検査",
            ),
        }

        self.assertEqual(
            {
                section: {token: True for token in tokens}
                for section, tokens in required.items()
            },
            {
                "api": {
                    token: token in api_contract
                    for token in required["api"]
                },
                "execution": {
                    token: token in execution_contract
                    for token in required["execution"]
                },
            },
            msg=(
                "set_property and set_properties returned writer failures must "
                "document state_unknown separately from thrown exceptions"
            ),
        )


class TestSymbolTools(unittest.TestCase):
    """Test get_unity_symbols and find_unity_symbol with synthetic YAML."""

    def setUp(self) -> None:
        self.server = create_server()
        self.prefab_text = _simple_prefab()
        self.tmp_path: Path | None = None

    def _write_prefab(self, tmp_dir: Path, name: str = "test.prefab") -> Path:
        p = tmp_dir / name
        p.write_text(self.prefab_text, encoding="utf-8")
        return p

    def test_get_unity_symbols_depth0(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab), "depth": 0},
                )
            )
            self.assertEqual(str(prefab), result["asset_path"])
            symbols = result["symbols"]
            self.assertEqual(1, len(symbols))
            self.assertEqual("Cube", symbols[0]["name"])
            # depth=0: no children
            self.assertNotIn("children", symbols[0])
            # Serena-style: no envelope fields
            self.assertNotIn("success", result)
            self.assertNotIn("severity", result)

    def test_get_unity_symbols_depth1(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab), "depth": 1},
                )
            )
            symbols = result["symbols"]
            children = symbols[0]["children"]
            child_names = {c["name"] for c in children}
            self.assertIn("Transform", child_names)
            self.assertIn("MeshRenderer", child_names)

    def test_find_unity_symbol_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "find_unity_symbol",
                    {"asset_path": str(prefab), "symbol_path": "Cube"},
                )
            )
            self.assertEqual(1, len(result["matches"]))
            self.assertEqual("Cube", result["matches"][0]["name"])
            # Serena-style: no envelope fields
            self.assertNotIn("success", result)

    def test_find_unity_symbol_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "find_unity_symbol",
                    {"asset_path": str(prefab), "symbol_path": "NonExistent"},
                )
            )
            self.assertEqual([], result["matches"])
            # Serena-style: empty matches = not found, no error envelope
            self.assertNotIn("success", result)

    def test_find_unity_symbol_component_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "find_unity_symbol",
                    {"asset_path": str(prefab), "symbol_path": "Cube/MeshRenderer"},
                )
            )
            self.assertEqual(1, len(result["matches"]))
            self.assertEqual("MeshRenderer", result["matches"][0]["name"])

    def test_get_unity_symbols_file_not_found(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        with self.assertRaises(ToolError) as cm:
            _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": "/nonexistent/test.prefab"},
                )
            )
        self.assertIsInstance(cm.exception, ToolError)
        self.assertTrue(str(cm.exception))

    def test_get_unity_symbols_resolution_failure_raises_tool_error(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        with tempfile.TemporaryDirectory() as td:
            server = create_server(project_root=td)
            with patch.object(Path, "resolve", side_effect=OSError("resolve failed")):
                with self.assertRaises(ToolError) as cm:
                    _run(
                        server.call_tool(
                            "get_unity_symbols",
                            {"asset_path": "Assets/Test.prefab"},
                        )
                    )
        self.assertIsInstance(cm.exception, ToolError)
        self.assertIn("resolve failed", str(cm.exception))


class TestGetUnitySymbolsDetail(unittest.TestCase):
    """Test get_unity_symbols with detail parameter."""

    def setUp(self) -> None:
        self.server = create_server()

    def _write_prefab_with_mb(self, tmp_dir: Path) -> Path:
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour(
                    "300",
                    "100",
                    fields={"speed": "{fileID: 0}", "health": "{fileID: 0}"},
                ),
            ]
        )
        p = tmp_dir / "test.prefab"
        p.write_text(text, encoding="utf-8")
        return p

    def test_detail_summary_returns_minimal_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab), "depth": 1, "detail": "summary"},
                )
            )
            symbols = result["symbols"]
            root = symbols[0]
            for child in root.get("children", []):
                self.assertNotIn("file_id", child)
                self.assertNotIn("properties", child)
                self.assertNotIn("field_names", child)

    def test_detail_fields_returns_field_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab), "depth": 1, "detail": "fields"},
                )
            )
            symbols = result["symbols"]
            root = symbols[0]
            mb_children = [c for c in root.get("children", []) if "MonoBehaviour" in c.get("name", "")]
            self.assertGreater(len(mb_children), 0)
            for mb in mb_children:
                self.assertIn("field_names", mb)
                self.assertNotIn("properties", mb)

    def test_default_depth_none_returns_full_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab)},
                )
            )
            symbols = result["symbols"]
            root = symbols[0]
            self.assertIn("children", root)

    def test_explicit_depth_1_limits_children(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab), "depth": 1},
                )
            )
            symbols = result["symbols"]
            root = symbols[0]
            self.assertIn("children", root)
            for child in root["children"]:
                self.assertNotIn("children", child)

    def test_response_includes_detail_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab), "detail": "summary"},
                )
            )
            self.assertEqual(result["detail"], "summary")

    def test_response_detail_key_defaults_to_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "get_unity_symbols",
                    {"asset_path": str(prefab)},
                )
            )
            self.assertEqual(result["detail"], "full")


class TestFindUnitySymbolIncludeFields(unittest.TestCase):
    """Test find_unity_symbol with include_fields (renamed from include_properties)."""

    def setUp(self) -> None:
        self.server = create_server()

    def _write_prefab_with_mb(self, tmp_dir: Path) -> Path:
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour(
                    "300",
                    "100",
                    fields={"speed": "{fileID: 0}"},
                ),
            ]
        )
        p = tmp_dir / "test.prefab"
        p.write_text(text, encoding="utf-8")
        return p

    def test_include_fields_false_default_no_properties(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "find_unity_symbol",
                    {"asset_path": str(prefab), "symbol_path": "Player/MonoBehaviour"},
                )
            )
            match = result["matches"][0]
            self.assertNotIn("properties", match)

    def test_include_fields_true_has_properties(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prefab = self._write_prefab_with_mb(Path(td))
            _, result = _run(
                self.server.call_tool(
                    "find_unity_symbol",
                    {
                        "asset_path": str(prefab),
                        "symbol_path": "Player/MonoBehaviour",
                        "include_fields": True,
                    },
                )
            )
            match = result["matches"][0]
            self.assertIn("properties", match)

    def test_show_origin_implies_include_fields(self) -> None:
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour(
                    "300",
                    "100",
                    fields={"ref": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
                ),
            ]
        )
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.success = False

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch
                _, result = _run(
                    server.call_tool(
                        "find_unity_symbol",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Player/MonoBehaviour",
                            "show_origin": True,
                        },
                    )
                )
            match = result["matches"][0]
            self.assertIn("properties", match)


class TestSymbolToolsWithMonoBehaviour(unittest.TestCase):
    """Test symbol tools with MonoBehaviour components."""

    def test_find_monobehaviour_with_script_name(self) -> None:
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour("300", "100", guid="aaaa1111bbbb2222cccc3333dddd4444"),
            ]
        )
        server = create_server(project_root=None)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "player.prefab"
            p.write_text(text, encoding="utf-8")
            _, result = _run(
                server.call_tool(
                    "find_unity_symbol",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player/MonoBehaviour",
                    },
                )
            )
            self.assertEqual(1, len(result["matches"]))


class TestGetUnitySymbolsExpandNested(unittest.TestCase):
    """Test expand_nested parameter wiring in get_unity_symbols."""

    def test_expand_nested_passed_to_build(self) -> None:
        text = YAML_HEADER + make_gameobject("100", "Root", ["200"]) + make_transform("200", "100")
        server = create_server(project_root=None)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            with patch("prefab_sentinel.session_cache.build_symbol_tree", wraps=build_symbol_tree) as mock_build:
                _run(
                    server.call_tool(
                        "get_unity_symbols",
                        {"asset_path": str(p), "expand_nested": True},
                    )
                )
                mock_build.assert_called_once()
                _, kwargs = mock_build.call_args
                self.assertTrue(kwargs.get("expand_nested"))

    def test_expanded_nested_lookup_round_trips_through_find_tool(self) -> None:
        from tests.yaml_helpers import make_prefab_instance  # noqa: PLC0415

        child_guid = "77777777777777777777777777777777"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "Assets"
            assets.mkdir()
            child_path = assets / "Child.prefab"
            child_path.write_text(
                YAML_HEADER
                + make_gameobject("500", "ChildRoot", ["600", "700"])
                + make_transform("600", "500")
                + make_meshrenderer("700", "500"),
                encoding="utf-8",
            )
            parent_path = assets / "Parent.prefab"
            parent_path.write_text(
                YAML_HEADER
                + make_gameobject("100", "Avatar", ["200"])
                + make_transform("200", "100")
                + make_prefab_instance("300", child_guid, transform_parent="200"),
                encoding="utf-8",
            )
            server = create_server(project_root=td)

            with patch(
                "prefab_sentinel.session_cache.collect_project_guid_index",
                return_value={child_guid: child_path},
            ):
                _, discovery = _run(
                    server.call_tool(
                        "get_unity_symbols",
                        {"asset_path": str(parent_path), "depth": 3, "expand_nested": True},
                    )
                )
                marker = next(
                    child for child in discovery["symbols"][0]["children"] if child["kind"] == "prefab_instance"
                )
                lookup = marker["children"][0]["children"][1]["lookup"]
                _, found = _run(
                    server.call_tool(
                        "find_unity_symbol",
                        {
                            "asset_path": lookup["asset_path"],
                            "symbol_path": lookup["symbol_path"],
                            "expand_nested": lookup["expand_nested"],
                        },
                    )
                )

        self.assertEqual(
            {
                "asset_path": str(parent_path),
                "symbol_path": "Avatar/ChildRoot/MeshRenderer",
                "expand_nested": True,
            },
            lookup,
        )
        self.assertEqual(1, len(found["matches"]))
        self.assertEqual(
            ("700", "MeshRenderer", "effective_nested"),
            (
                found["matches"][0]["file_id"],
                found["matches"][0]["name"],
                found["matches"][0]["entry_kind"],
            ),
        )


class TestOrchestratorTools(unittest.TestCase):
    """Test orchestrator-backed tools via mocking."""

    def _make_server(self) -> Any:
        return create_server()

    def test_validate_refs_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "REF001",
            "message": "No broken references",
            "data": {},
            "diagnostics": [],
        }
        mock_orch = MagicMock()
        mock_orch.validate_refs.return_value = mock_resp

        server = self._make_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "validate_refs",
                    {"scope": "/some/path"},
                )
            )

        self.assertTrue(result["success"])
        mock_orch.validate_refs.assert_called_once_with(
            scope="/some/path",
            details=False,
            max_diagnostics=200,
            top_missing_breakdown=False,
            snapshot_save="",
            snapshot_diff="",
            refresh_guid_index=False,
            # Issue #237: the validation MCP tool forwards a single
            # merged ignore-GUID collection (caller list ∪ file load).
            # With no caller list and a path that has no
            # config/ignore_guids.txt the merged tuple is empty.
            ignore_asset_guids=(),
            diagnostics_baseline=DiagnosticsBaseline(
                known_diagnostics=(), path=None, status="not_loaded_no_project_root"
            ),
        )

    def test_validate_refs_forwards_refresh_guid_index_flag(self) -> None:
        """Issue #229 — the MCP tool surface forwards the refresh flag."""
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "VALIDATE_REFS_RESULT",
            "message": "ok",
            "data": {},
            "diagnostics": [],
        }
        mock_orch = MagicMock()
        mock_orch.validate_refs.return_value = mock_resp

        server = self._make_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(
                server.call_tool(
                    "validate_refs",
                    {"scope": "/some/path", "refresh_guid_index": True},
                )
            )
        kwargs = mock_orch.validate_refs.call_args.kwargs
        self.assertEqual(True, kwargs["refresh_guid_index"])

    def test_inspect_wiring_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "data": {"components": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_wiring.return_value = mock_resp

        server = self._make_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "inspect_wiring",
                    {"asset_path": "/some/test.prefab"},
                )
            )

        self.assertTrue(result["success"])
        mock_orch.inspect_wiring.assert_called_once_with(
            target_path="/some/test.prefab",
            udon_only=False,
            cursor="",
            page_size=50,
            summary_only=False,
            script_filter="",
            include_out_of_scope_diagnostics=False,
            timeout_sec=None,
            diagnostics_baseline=DiagnosticsBaseline(
                known_diagnostics=(), path=None, status="not_loaded_no_project_root"
            ),
        )

    def test_inspect_wiring_forwards_summary_and_filter_flags(self) -> None:
        """Issue #227 — the MCP tool surface forwards both new flags."""
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "data": {"component_count": 3},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_wiring.return_value = mock_resp

        server = self._make_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(
                server.call_tool(
                    "inspect_wiring",
                    {
                        "asset_path": "/some/test.prefab",
                        "summary_only": True,
                        "script_filter": "AvatarSync",
                    },
                )
            )
        kwargs = mock_orch.inspect_wiring.call_args.kwargs
        self.assertEqual(True, kwargs["summary_only"])
        self.assertEqual("AvatarSync", kwargs["script_filter"])

    def test_inspect_wiring_forwards_out_of_scope_flag_and_project_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)
            config_dir = project_root / "config"
            config_dir.mkdir()
            (config_dir / "diagnostics_baseline.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "known_diagnostics": [
                            "inspect_wiring:null_reference:Assets/Base.prefab:40:targetRef",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            mock_resp = MagicMock()
            mock_resp.to_dict.return_value = {
                "success": True,
                "severity": "info",
                "data": {"component_count": 3},
            }
            mock_orch = MagicMock()
            mock_orch.inspect_wiring.return_value = mock_resp

            server = self._make_server()

            with (
                patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
                patch.object(ProjectSession, "project_root", project_root),
            ):
                mock_cls.default.return_value = mock_orch
                _run(
                    server.call_tool(
                        "inspect_wiring",
                        {
                            "asset_path": "Assets/Base.prefab",
                            "script_filter": "FooBehaviour",
                            "include_out_of_scope_diagnostics": True,
                        },
                    )
                )

        kwargs = mock_orch.inspect_wiring.call_args.kwargs
        baseline = kwargs["diagnostics_baseline"]
        self.assertEqual(
            (True, "loaded", ("inspect_wiring:null_reference:Assets/Base.prefab:40:targetRef",)),
            (
                kwargs["include_out_of_scope_diagnostics"],
                baseline.status,
                baseline.known_diagnostics,
            ),
        )

    def test_inspect_wiring_invalid_project_baseline_returns_error_before_orchestration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)
            config_dir = project_root / "config"
            config_dir.mkdir()
            (config_dir / "diagnostics_baseline.json").write_text("{", encoding="utf-8")
            mock_orch = MagicMock()

            server = self._make_server()

            with (
                patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
                patch.object(ProjectSession, "project_root", project_root),
            ):
                mock_cls.default.return_value = mock_orch
                _, result = _run(
                    server.call_tool(
                        "inspect_wiring",
                        {"asset_path": "Assets/Base.prefab"},
                    )
                )

        self.assertEqual(
            (False, "DIAGNOSTICS_BASELINE_INVALID", 0),
            (result["success"], result["code"], mock_orch.inspect_wiring.call_count),
        )

    def test_inspect_wiring_delegates_cursor_and_page_size(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "data": {"components": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_wiring.return_value = mock_resp

        server = self._make_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "inspect_wiring",
                    {
                        "asset_path": "/some/test.prefab",
                        "udon_only": True,
                        "cursor": "pos:50",
                        "page_size": 25,
                    },
                )
            )

        self.assertTrue(result["success"])
        mock_orch.inspect_wiring.assert_called_once_with(
            target_path="/some/test.prefab",
            udon_only=True,
            cursor="pos:50",
            page_size=25,
            summary_only=False,
            script_filter="",
            include_out_of_scope_diagnostics=False,
            timeout_sec=None,
            diagnostics_baseline=DiagnosticsBaseline(
                known_diagnostics=(), path=None, status="not_loaded_no_project_root"
            ),
        )

    def test_find_referencing_assets_delegates(self) -> None:

        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="ok",
            data={"usages": [], "usage_count": 0, "returned_usages": 0, "truncated_usages": 0},
            diagnostics=[],
        )
        mock_orch = MagicMock()
        mock_orch.reference_resolver.where_used.return_value = mock_step

        server = self._make_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "find_referencing_assets",
                    {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
                )
            )

        # Direct payload format
        self.assertIn("matches", result)
        self.assertEqual([], result["matches"])
        mock_orch.reference_resolver.where_used.assert_called_once()

    def test_inspect_variant_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "data": {"overrides": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_variant.return_value = mock_resp

        server = self._make_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "inspect_variant",
                    {"asset_path": "/some/variant.prefab", "show_origin": True},
                )
            )

        self.assertTrue(result["success"])
        mock_orch.inspect_variant.assert_called_once_with(
            variant_path="/some/variant.prefab",
            component_filter=None,
            show_origin=True,
        )


class TestDiffUnitySymbolsTool(unittest.TestCase):
    """Test the diff_unity_symbols MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "data": {"diff_count": 1, "diffs": [{"property_path": "speed"}]},
        }
        mock_orch = MagicMock()
        mock_orch.diff_variant.return_value = mock_resp

        server = create_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "diff_unity_symbols",
                    {"asset_path": "/some/variant.prefab"},
                )
            )

        self.assertTrue(result["success"])
        mock_orch.diff_variant.assert_called_once_with(
            variant_path="/some/variant.prefab",
            component_filter=None,
        )

    def test_passes_component_filter(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"diffs": []}}
        mock_orch = MagicMock()
        mock_orch.diff_variant.return_value = mock_resp

        server = create_server()

        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _run(
                server.call_tool(
                    "diff_unity_symbols",
                    {"asset_path": "/v.prefab", "component_filter": "speed"},
                )
            )

        mock_orch.diff_variant.assert_called_once_with(
            variant_path="/v.prefab",
            component_filter="speed",
        )


class TestFindUnitySymbolShowOrigin(unittest.TestCase):
    """Test find_unity_symbol with show_origin parameter."""

    def test_show_origin_false_returns_flat_properties(self) -> None:
        """Default show_origin=False keeps properties as {name: value}."""
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour("300", "100", fields={"speed": "5.0"}),
            ]
        )
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            _, result = _run(
                server.call_tool(
                    "find_unity_symbol",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player/MonoBehaviour",
                        "include_fields": True,
                    },
                )
            )

        self.assertNotIn("show_origin", result)
        props = result["matches"][0].get("properties", {})
        # Flat format: {name: value_str}
        if props:
            first_val = next(iter(props.values()))
            self.assertIsInstance(first_val, str)

    def test_show_origin_true_annotates_properties(self) -> None:
        """show_origin=True changes properties to {name: {value, origin_path, origin_depth}}.

        Uses a MonoBehaviour with a reference field so analyze_wiring()
        populates properties (it only captures fileID/GUID references).
        """
        # Use a reference field that analyze_wiring will capture
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour(
                    "300",
                    "100",
                    fields={"targetRef": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
                ),
            ]
        )
        server = create_server()

        # Mock the orchestrator's prefab_variant to return origin data
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.data = {
            "values": [
                {
                    "target_file_id": "300",
                    "property_path": "targetRef",
                    "value": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}",
                    "origin_path": "Assets/Leaf.prefab",
                    "origin_depth": 0,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "find_unity_symbol",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Player/MonoBehaviour",
                            "show_origin": True,
                        },
                    )
                )

        self.assertTrue(result.get("show_origin"))
        props = result["matches"][0].get("properties", {})
        self.assertIn("targetRef", props)
        self.assertIsInstance(props["targetRef"], dict)
        self.assertEqual("Assets/Leaf.prefab", props["targetRef"]["origin_path"])
        self.assertEqual(0, props["targetRef"]["origin_depth"])

    def test_show_origin_true_annotates_nested_child_properties(self) -> None:
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour(
                    "300",
                    "100",
                    fields={"targetRef": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
                ),
            ]
        )
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.data = {
            "values": [
                {
                    "target_file_id": "300",
                    "property_path": "targetRef",
                    "value": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}",
                    "origin_path": "Assets/Leaf.prefab",
                    "origin_depth": 0,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "find_unity_symbol",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Player",
                            "depth": 1,
                            "show_origin": True,
                        },
                    )
                )

        children = result["matches"][0]["children"]
        child = next(item for item in children if item.get("file_id") == "300")
        props = child["properties"]
        self.assertEqual(
            {
                "value": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}",
                "origin_path": "Assets/Leaf.prefab",
                "origin_depth": 0,
            },
            props["targetRef"],
        )

    def test_show_origin_on_non_variant_degrades_gracefully(self) -> None:
        """show_origin=True on a non-variant still returns results (no origin)."""
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Cube", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour(
                    "300",
                    "100",
                    fields={"ref": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
                ),
            ]
        )
        server = create_server()

        # Mock returns not-variant response (success=False)
        mock_resp = MagicMock()
        mock_resp.success = False

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "base.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "find_unity_symbol",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MonoBehaviour",
                            "show_origin": True,
                        },
                    )
                )

        # find_unity_symbol still returns matches; origin annotation skipped
        self.assertEqual(1, len(result["matches"]))
        props = result["matches"][0].get("properties", {})
        # Properties remain in flat {name: value} format since annotation was skipped
        if props:
            first_val = next(iter(props.values()))
            self.assertIsInstance(first_val, str)

    def test_annotate_origins_logs_on_exception(self) -> None:
        """When orchestrator raises, _annotate_origins logs debug and returns."""
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Cube", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour(
                    "300",
                    "100",
                    fields={"ref": "{fileID: 100, guid: 00000000000000000000000000000000, type: 2}"},
                ),
            ]
        )
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.prefab_variant.resolve_chain_values_with_origin.side_effect = RuntimeError("test")
                mock_cls.default.return_value = mock_orch

                with self.assertLogs("prefab_sentinel.mcp_tools_symbols", level="DEBUG") as cm:
                    _, result = _run(
                        server.call_tool(
                            "find_unity_symbol",
                            {
                                "asset_path": str(p),
                                "symbol_path": "Cube/MonoBehaviour",
                                "show_origin": True,
                            },
                        )
                    )

        # Tool still returns matches (best-effort annotation)
        self.assertEqual(1, len(result["matches"]))
        # Verify debug log was emitted
        self.assertTrue(any("Origin annotation failed" in msg for msg in cm.output))


class TestSetPropertyTool(unittest.TestCase):
    """Test the set_property MCP tool."""

    def _prefab_with_meshrenderer(self) -> str:
        """Prefab: Cube → Transform + MeshRenderer."""
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Cube", ["200", "300"]),
                make_transform("200", "100"),
                make_meshrenderer("300", "100"),
            ]
        )

    def _prefab_with_monobehaviour(self, guid: str = "aaaa1111bbbb2222cccc3333dddd4444") -> str:
        """Prefab: Player → Transform + MonoBehaviour(script)."""
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Player", ["200", "300"]),
                make_transform("200", "100"),
                make_monobehaviour("300", "100", guid=guid),
            ]
        )

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_set_property_dry_run(self) -> None:
        """confirm=False returns dry-run preview."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                        },
                    )
                )

        self.assertTrue(result["success"])
        mock_orch.serialized_value_patch_apply.assert_called_once()
        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])

    def test_set_property_confirm(self) -> None:
        """confirm=True applies the change."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 1,
                            "confirm": True,
                            "change_reason": "enable renderer",
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_confirmed_failed_response_marks_state_unknown(self) -> None:
        text = self._prefab_with_meshrenderer()
        writer_data = {"read_only": False, "applied": 1, "executed": True}
        failed_response = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="PATCH_APPLY_RESULT",
            message="Serialized value apply failed.",
            data=writer_data,
            diagnostics=[],
        )

        with tempfile.TemporaryDirectory() as temporary:
            asset_path = Path(temporary) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = failed_response
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=temporary)
                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                            "confirm": True,
                            "change_reason": "test uncertain writer state",
                        },
                    )
                )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            (True, 1, True),
            (
                result["data"]["state_unknown"],
                result["data"]["applied"],
                result["data"]["executed"],
            ),
        )
        self.assertEqual(
            {"read_only": False, "applied": 1, "executed": True},
            failed_response.data,
        )
        mock_orch.serialized_value_patch_apply.assert_called_once_with(
            plan={
                "plan_version": 2,
                "resources": [
                    {
                        "id": "target",
                        "path": str(asset_path),
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "target",
                        "op": "set",
                        "file_id": "300",
                        "path": "m_Enabled",
                        "value": 0,
                    }
                ],
            },
            dry_run=False,
            confirm=True,
            change_reason="test uncertain writer state",
        )
        mock_orch.maybe_auto_refresh.assert_not_called()
        invalidate_symbol_tree.assert_called_once_with(asset_path.resolve())

    def test_orchestrator_acquisition_failure_is_redacted(self) -> None:
        text = self._prefab_with_meshrenderer()

        with tempfile.TemporaryDirectory() as temporary:
            asset_path = Path(temporary) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            with (
                patch.object(
                    ProjectSession,
                    "get_orchestrator",
                    side_effect=ValueError("sensitive acquisition detail"),
                ) as get_orchestrator,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                server = create_server(project_root=temporary)
                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                            "confirm": True,
                            "change_reason": "test acquisition failure",
                        },
                    )
                )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            (
                "Patch transaction apply failed.",
                {"boundary": "apply", "state_unknown": False},
            ),
            (result["message"], result["data"]),
        )
        self.assertNotIn(
            "sensitive acquisition detail",
            json.dumps(result, sort_keys=True),
        )
        get_orchestrator.assert_called_once_with()
        invalidate_symbol_tree.assert_not_called()


    def test_asset_preflight_path_escape_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory:
            escaped_target = (Path(project_directory).parent / "secret.prefab").resolve()
            with patch.object(ProjectSession, "get_orchestrator") as get_orchestrator:
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": "../secret.prefab",
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                        },
                    )
                )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            (
                "Patch transaction preflight failed.",
                {"boundary": "preflight"},
            ),
            (result["message"], result["data"]),
        )
        public_response = json.dumps(result, sort_keys=True)
        self.assertNotIn(str(escaped_target), public_response)
        self.assertNotIn(str(Path(project_directory).resolve()), public_response)
        get_orchestrator.assert_not_called()

    def test_writer_exception_is_redacted_and_marks_state_unknown(self) -> None:
        text = self._prefab_with_meshrenderer()

        with tempfile.TemporaryDirectory() as temporary:
            asset_path = Path(temporary) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.side_effect = RuntimeError(
                    "sensitive writer detail",
                )
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=temporary)
                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                            "confirm": True,
                            "change_reason": "test writer failure",
                        },
                    )
                )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            {"boundary": "apply", "state_unknown": True},
            result["data"],
        )
        self.assertNotIn(
            "sensitive writer detail",
            json.dumps(result, sort_keys=True),
        )
        mock_orch.serialized_value_patch_apply.assert_called_once_with(
            plan={
                "plan_version": 2,
                "resources": [
                    {
                        "id": "target",
                        "path": str(asset_path),
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "target",
                        "op": "set",
                        "file_id": "300",
                        "path": "m_Enabled",
                        "value": 0,
                    }
                ],
            },
            dry_run=False,
            confirm=True,
            change_reason="test writer failure",
        )
        mock_orch.maybe_auto_refresh.assert_not_called()
        invalidate_symbol_tree.assert_called_once_with(asset_path.resolve())

    def test_refresh_exception_is_redacted_and_marks_state_unknown(self) -> None:
        text = self._prefab_with_meshrenderer()
        response = self._mock_patch_apply_response(dry_run=False)
        response.success = True

        with tempfile.TemporaryDirectory() as temporary:
            asset_path = Path(temporary) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = response
                mock_orch.maybe_auto_refresh.side_effect = RuntimeError(
                    "sensitive refresh detail",
                )
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=temporary)
                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                            "confirm": True,
                            "change_reason": "test refresh failure",
                        },
                    )
                )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            {"boundary": "apply", "state_unknown": True},
            result["data"],
        )
        self.assertNotIn(
            "sensitive refresh detail",
            json.dumps(result, sort_keys=True),
        )
        mock_orch.serialized_value_patch_apply.assert_called_once_with(
            plan={
                "plan_version": 2,
                "resources": [
                    {
                        "id": "target",
                        "path": str(asset_path),
                        "mode": "open",
                    }
                ],
                "ops": [
                    {
                        "resource": "target",
                        "op": "set",
                        "file_id": "300",
                        "path": "m_Enabled",
                        "value": 0,
                    }
                ],
            },
            dry_run=False,
            confirm=True,
            change_reason="test refresh failure",
        )
        mock_orch.maybe_auto_refresh.assert_called_once_with()
        invalidate_symbol_tree.assert_called_once_with(asset_path.resolve())

    def test_set_property_symbol_not_found(self) -> None:
        """Returns error when symbol path doesn't resolve."""
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "NonExistent/MeshRenderer",
                        "property_path": "m_Enabled",
                        "value": 0,
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)

    def test_set_property_not_a_component(self) -> None:
        """Returns error when symbol path points to a GameObject."""
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "property_path": "m_Name",
                        "value": "NewName",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])
        self.assertIn("game_object", result["data"]["resolved_kind"])

    def test_set_property_builtin_component_name(self) -> None:
        """Built-in component resolves to its type name in the plan."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                        },
                    )
                )

        # Issue #37: the plan's set op identifies its target by the
        # resolved fileID, not a type-name selector.
        plan = mock_orch.serialized_value_patch_apply.call_args[1]["plan"]
        op = plan["ops"][0]
        self.assertEqual("300", op["file_id"])
        self.assertNotIn("component", op)
        # Verify symbol_resolution metadata
        self.assertEqual("MeshRenderer", result["symbol_resolution"]["resolved_component"])

    def test_set_property_monobehaviour_script_name(self) -> None:
        """MonoBehaviour resolves to its script name in symbol_resolution."""
        text = self._prefab_with_monobehaviour()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            # Create a project root with script meta
            scripts_dir = Path(td) / "Assets" / "Scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "PlayerScript.cs").write_text("class PlayerScript {}", encoding="utf-8")
            (scripts_dir / "PlayerScript.cs.meta").write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            server_with_root = create_server(project_root=td)

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server_with_root.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Player/MonoBehaviour(PlayerScript)",
                            "property_path": "speed",
                            "value": 10.0,
                        },
                    )
                )

        plan = mock_orch.serialized_value_patch_apply.call_args[1]["plan"]
        self.assertEqual("300", plan["ops"][0]["file_id"])
        self.assertEqual("PlayerScript", result["symbol_resolution"]["resolved_component"])

    def test_set_property_monobehaviour_no_script_name(self) -> None:
        """Returns error when MonoBehaviour has no resolved script name."""
        text = self._prefab_with_monobehaviour()
        server = create_server()  # no project_root

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "set_property",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player/MonoBehaviour",
                        "property_path": "speed",
                        "value": 5.0,
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_UNRESOLVABLE", result["code"])

    def test_set_property_passes_change_reason(self) -> None:
        """change_reason is forwarded to the orchestrator."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 1,
                            "change_reason": "Enable renderer for visibility",
                        },
                    )
                )

        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertEqual("Enable renderer for visibility", call_kwargs["change_reason"])

    def test_set_property_plan_structure(self) -> None:
        """Constructed plan follows V2 format."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_CastShadows",
                            "value": 0,
                        },
                    )
                )

        plan = mock_orch.serialized_value_patch_apply.call_args[1]["plan"]
        self.assertEqual(2, plan["plan_version"])
        self.assertEqual(1, len(plan["resources"]))
        self.assertEqual("target", plan["resources"][0]["id"])
        self.assertEqual(str(p), plan["resources"][0]["path"])
        self.assertEqual("open", plan["resources"][0]["mode"])
        self.assertEqual(1, len(plan["ops"]))
        op = plan["ops"][0]
        self.assertEqual("target", op["resource"])
        self.assertEqual("set", op["op"])
        self.assertEqual("m_CastShadows", op["path"])
        self.assertEqual(0, op["value"])

    def test_set_property_symbol_resolution_metadata(self) -> None:
        """Response includes symbol_resolution metadata."""
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 1,
                        },
                    )
                )

        sr = result["symbol_resolution"]
        self.assertEqual("Cube/MeshRenderer", sr["symbol_path"])
        self.assertEqual("MeshRenderer", sr["resolved_component"])
        self.assertEqual("300", sr["file_id"])
        self.assertEqual("m_Enabled", sr["property_path"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(
            server.call_tool(
                "set_property",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Cube/MeshRenderer",
                    "property_path": "m_Enabled",
                    "value": 0,
                    "confirm": True,
                    "change_reason": "",
                },
            )
        )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestListSerializedFieldsTool(unittest.TestCase):
    """Tests for the list_serialized_fields MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="CSF_LIST_OK",
            message="Found 2 serialized fields.",
            data={
                "script_guid": "aabb",
                "script_path": "/test/Foo.cs",
                "class_name": "Foo",
                "field_count": 2,
                "fields": [
                    {"name": "speed", "type_name": "float", "is_serialized": True, "is_public": True, "line": 1},
                    {"name": "health", "type_name": "int", "is_serialized": True, "is_public": False, "line": 2},
                ],
                "read_only": True,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.list_serialized_fields.return_value = mock_resp
            _, result = _run(
                server.call_tool(
                    "list_serialized_fields",
                    {"script_or_guid": "aabb"},
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual("CSF_LIST_OK", result["code"])
        self.assertEqual(2, result["data"]["field_count"])

    def test_error_propagated(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="CSF_RESOLVE_FAILED",
            message="Script not found.",
            data={"script": "missing.cs"},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.list_serialized_fields.return_value = mock_resp
            _, result = _run(
                server.call_tool(
                    "list_serialized_fields",
                    {"script_or_guid": "missing.cs"},
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("CSF_RESOLVE_FAILED", result["code"])


class TestValidateFieldRenameTool(unittest.TestCase):
    """Tests for the validate_field_rename MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="CSF_RENAME_OK",
            message="Rename 'speed' -> 'velocity': 3 affected components.",
            data={
                "script_guid": "aabb",
                "script_path": "/test/Foo.cs",
                "old_name": "speed",
                "new_name": "velocity",
                "conflict": False,
                "has_formerly_serialized_as": False,
                "affected_count": 3,
                "affected_assets": [],
                "read_only": True,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.validate_field_rename.return_value = mock_resp
            _, result = _run(
                server.call_tool(
                    "validate_field_rename",
                    {
                        "script_or_guid": "aabb",
                        "old_name": "speed",
                        "new_name": "velocity",
                    },
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual("CSF_RENAME_OK", result["code"])
        self.assertEqual(3, result["data"]["affected_count"])

    def test_with_scope_parameter(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="CSF_RENAME_OK",
            message="ok",
            data={"affected_count": 0, "read_only": True},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch = mock_orch_cls.default.return_value
            mock_orch.validate_field_rename.return_value = mock_resp
            _run(
                server.call_tool(
                    "validate_field_rename",
                    {
                        "script_or_guid": "aabb",
                        "old_name": "speed",
                        "new_name": "velocity",
                        "scope": "Assets/Scripts",
                    },
                )
            )
            mock_orch.validate_field_rename.assert_called_once_with(
                script_path_or_guid="aabb",
                old_name="speed",
                new_name="velocity",
                scope="Assets/Scripts",
            )


class TestCheckFieldCoverageTool(unittest.TestCase):
    """Tests for the check_field_coverage MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        server = create_server()
        mock_resp = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="CSF_COVERAGE_OK",
            message="Checked 5 components (2 scripts): 1 unused, 2 orphaned.",
            data={
                "scope": "Assets/",
                "scripts_checked": 2,
                "components_checked": 5,
                "unused_count": 1,
                "unused_fields": [{"field_name": "oldField", "class_name": "Foo"}],
                "orphaned_count": 2,
                "orphaned_paths": [
                    {"field_name": "legacy1", "class_name": "Foo"},
                    {"field_name": "legacy2", "class_name": "Bar"},
                ],
                "read_only": True,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_orch_cls:
            mock_orch_cls.default.return_value.check_field_coverage.return_value = mock_resp
            _, result = _run(
                server.call_tool(
                    "check_field_coverage",
                    {"scope": "Assets/"},
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual("CSF_COVERAGE_OK", result["code"])
        self.assertEqual(1, result["data"]["unused_count"])
        self.assertEqual(2, result["data"]["orphaned_count"])


class TestSessionTools(unittest.TestCase):
    """Tests for activate_project and get_project_status tools."""

    def test_get_project_status_before_activation(self) -> None:
        server = create_server()
        _, result = _run(server.call_tool("get_project_status", {}))

        self.assertTrue(result["success"])
        self.assertEqual("SESSION_STATUS", result["code"])
        data = result["data"]
        self.assertIsNone(data["project_root"])
        self.assertIsNone(data["scope"])
        self.assertFalse(data["orchestrator_cached"])
        self.assertFalse(data["script_map_cached"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_activate_project_returns_status(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        # Issue #311: the Phase1Orchestrator decorator is retained to
        # keep the orchestrator construction mocked, but no method
        # body reads the injected mock — name it ``_`` so the
        # parameter list carries only arguments referenced inside the
        # body.
        _: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {"g1": "ScriptA"}

        server = create_server()
        _, result = _run(
            server.call_tool(
                "activate_project",
                {"scope": "Assets/MyScope"},
            )
        )

        self.assertEqual(
            (True, "SESSION_ACTIVATED"),
            (result["success"], result["code"]),
            f"activation envelope mismatch: {result!r}",
        )
        data = result["data"]
        self.assertEqual(str(Path("/unity")), data["project_root"])
        self.assertTrue(data["orchestrator_cached"])
        self.assertTrue(data["script_map_cached"])
        self.assertEqual(1, data["script_map_size"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_status_updates_after_activation(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        # Issue #311: Phase1Orchestrator decorator retained, parameter
        # discarded — same rationale as test_activate_project_returns_status.
        _: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/Scope")
        mock_build.return_value = {}

        server = create_server()

        # Before: not activated
        _, before = _run(server.call_tool("get_project_status", {}))
        self.assertFalse(before["data"]["orchestrator_cached"])

        # Activate
        _run(server.call_tool("activate_project", {"scope": "Assets/Scope"}))

        # After: caches warm
        _, after = _run(server.call_tool("get_project_status", {}))
        self.assertTrue(after["data"]["orchestrator_cached"])
        self.assertTrue(after["data"]["script_map_cached"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    def test_activate_project_with_explicit_project_root(
        self,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_build.return_value = {"g1": "ScriptA"}
        with tempfile.TemporaryDirectory() as tmpdir:
            assets = Path(tmpdir) / "Assets"
            assets.mkdir()
            mock_resolve.return_value = assets / "MyScope"

            server = create_server()
            _, result = _run(
                server.call_tool(
                    "activate_project",
                    {"scope": "Assets/MyScope", "project_root": tmpdir},
                )
            )

            self.assertTrue(result["success"])
            self.assertEqual(str(Path(tmpdir).resolve()), result["data"]["project_root"])

    def test_activate_project_with_invalid_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            server = create_server()
            _, result = _run(
                server.call_tool(
                    "activate_project",
                    {"scope": "Assets/X", "project_root": tmpdir},
                )
            )

            self.assertFalse(result["success"])
            self.assertEqual("INVALID_PROJECT_ROOT", result["code"])

    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_activate_project_without_project_root_backward_compat(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {"g1": "ScriptA"}

        server = create_server()
        _, result = _run(
            server.call_tool(
                "activate_project",
                {"scope": "Assets/MyScope"},
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual("SESSION_ACTIVATED", result["code"])


class TestAddComponentTool(unittest.TestCase):
    """Test the add_component MCP tool."""

    def _prefab_with_child(self) -> str:
        """Prefab: Root → Transform(children=[ChildTransform])
        Child → Transform(father=RootTransform) + MeshRenderer
        """
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Root", ["200"]),
                make_transform("200", "100", children_file_ids=["400"]),
                make_gameobject("300", "Child", ["400", "500"]),
                make_transform("400", "300", father_file_id="200"),
                make_meshrenderer("500", "300"),
            ]
        )

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_add_component_dry_run_on_root(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "add_component",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Root",
                            "component_type": "AudioSource",
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        plan = call_kwargs["plan"]
        op = plan["ops"][0]
        self.assertEqual("add_component", op["op"])
        self.assertEqual("/", op["target"])
        self.assertEqual("AudioSource", op["type"])
        self.assertTrue(call_kwargs["dry_run"])

    def test_add_component_on_child(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "add_component",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Root/Child",
                            "component_type": "BoxCollider",
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op = plan["ops"][0]
        self.assertEqual("/Child", op["target"])
        self.assertEqual("BoxCollider", op["type"])

    def test_add_component_symbol_not_found(self) -> None:
        text = self._prefab_with_child()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "add_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Nonexistent",
                        "component_type": "AudioSource",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)

    def test_add_component_rejects_component_path(self) -> None:
        """add_component requires a game_object, not a component."""
        text = self._prefab_with_child()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "add_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Root/Child/MeshRenderer",
                        "component_type": "AudioSource",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_GAME_OBJECT", result["code"])

    def test_add_component_confirm_invalidates_cache(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "add_component",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Root",
                            "component_type": "AudioSource",
                            "confirm": True,
                            "change_reason": "add audio source",
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_add_component_symbol_resolution_metadata(self) -> None:
        text = self._prefab_with_child()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "add_component",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Root/Child",
                            "component_type": "AudioSource",
                        },
                    )
                )

        meta = result["symbol_resolution"]
        self.assertEqual("Root/Child", meta["symbol_path"])
        self.assertEqual("/Child", meta["hierarchy_target"])
        self.assertEqual("AudioSource", meta["component_type"])
        self.assertEqual("300", meta["file_id"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(
            server.call_tool(
                "add_component",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Root",
                    "component_type": "AudioSource",
                    "confirm": True,
                    "change_reason": "",
                },
            )
        )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestRemoveComponentTool(unittest.TestCase):
    """Test the remove_component MCP tool."""

    def _prefab_with_meshrenderer(self) -> str:
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Cube", ["200", "300"]),
                make_transform("200", "100"),
                make_meshrenderer("300", "100"),
            ]
        )

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_remove_component_dry_run(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "remove_component",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        plan = call_kwargs["plan"]
        op = plan["ops"][0]
        self.assertEqual("remove_component", op["op"])
        self.assertEqual("MeshRenderer", op["component"])
        self.assertTrue(call_kwargs["dry_run"])

    def test_remove_component_confirm(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "remove_component",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "confirm": True,
                            "change_reason": "remove mesh renderer",
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])

    def test_remove_component_symbol_not_found(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "remove_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/AudioSource",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])
        self.assertIsInstance(result["data"]["suggestions"], list)

    def test_remove_component_rejects_gameobject_path(self) -> None:
        """remove_component requires a component, not a game_object."""
        text = self._prefab_with_meshrenderer()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "remove_component",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])

    def test_remove_component_symbol_resolution_metadata(self) -> None:
        text = self._prefab_with_meshrenderer()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "remove_component",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                        },
                    )
                )

        meta = result["symbol_resolution"]
        self.assertEqual("Cube/MeshRenderer", meta["symbol_path"])
        self.assertEqual("MeshRenderer", meta["resolved_component"])
        self.assertEqual("300", meta["file_id"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(
            server.call_tool(
                "remove_component",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Cube/MeshRenderer",
                    "confirm": True,
                    "change_reason": "",
                },
            )
        )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestScopeFallback(unittest.TestCase):
    """MCP tools use session scope when explicit scope is omitted."""

    def test_validate_refs_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.validate_refs.return_value = mock_resp
            _run(server.call_tool("validate_refs", {"scope": "Assets/Explicit"}))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.validate_refs.call_args.kwargs["scope"],
            )

    def test_find_referencing_assets_passes_resolved_scope(self) -> None:

        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="ok",
            data={"usages": [], "usage_count": 0, "returned_usages": 0, "truncated_usages": 0},
            diagnostics=[],
        )
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Fallback"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _run(
                server.call_tool(
                    "find_referencing_assets",
                    {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
                )
            )
            self.assertEqual(
                "Assets/Fallback",
                mock_orch.reference_resolver.where_used.call_args.kwargs["scope"],
            )

    def test_validate_field_rename_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.validate_field_rename.return_value = mock_resp
            _run(
                server.call_tool(
                    "validate_field_rename",
                    {
                        "script_or_guid": "aabb",
                        "old_name": "speed",
                        "new_name": "velocity",
                    },
                )
            )
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.validate_field_rename.call_args.kwargs["scope"],
            )

    def test_check_field_coverage_passes_resolved_scope(self) -> None:
        server = create_server()
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {}}
        with (
            patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls,
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            mock_orch = mock_cls.default.return_value
            mock_orch.check_field_coverage.return_value = mock_resp
            _run(server.call_tool("check_field_coverage", {"scope": "Assets/Explicit"}))
            self.assertEqual(
                "Assets/Resolved",
                mock_orch.check_field_coverage.call_args.kwargs["scope"],
            )


class TestFindReferencingAssetsDirectPayload(unittest.TestCase):
    """find_referencing_assets returns direct payload, not envelope."""

    def test_returns_matches_array(self) -> None:
        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="Found 2 usages",
            data={
                "usages": [
                    {"file": "A.prefab", "line": 10},
                    {"file": "B.prefab", "line": 20},
                ],
                "usage_count": 2,
                "returned_usages": 2,
                "truncated_usages": 0,
                "scanned_files": 5,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _, result = _run(
                server.call_tool(
                    "find_referencing_assets",
                    {"asset_or_guid": "abcd1234abcd1234abcd1234abcd1234"},
                )
            )

        # Direct payload — no envelope
        self.assertIn("matches", result)
        self.assertEqual(2, len(result["matches"]))
        self.assertEqual("abcd1234abcd1234abcd1234abcd1234", result["target"])
        self.assertFalse(result["metadata"]["truncated"])
        self.assertEqual(2, result["metadata"]["total_count"])
        # No envelope keys
        self.assertNotIn("success", result)
        self.assertNotIn("severity", result)

    def test_missing_target_metadata_stays_in_direct_payload(self) -> None:
        server = create_server()
        usages = [{"path": "Assets/Referrer.prefab", "line": 4, "column": 11}]
        mock_step = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_WHERE_USED",
            message="Reference usage scan completed.",
            data={
                "usages": usages,
                "usage_count": 1,
                "returned_usages": 1,
                "truncated_usages": 0,
                "scanned_files": 1,
                "asset_path": None,
                "asset_missing": True,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _, result = _run(
                server.call_tool(
                    "find_referencing_assets",
                    {"asset_or_guid": "f" * 32},
                )
            )

        self.assertEqual(usages, result["matches"])
        self.assertEqual(
            {
                "total_count": 1,
                "truncated": False,
                "scope": None,
                "asset_path": None,
                "asset_missing": True,
            },
            result["metadata"],
        )

    def test_truncated_metadata(self) -> None:
        server = create_server()
        mock_step = ToolResponse(
            success=True,
            severity=Severity.WARNING,
            code="REF_WHERE_USED",
            message="Truncated",
            data={
                "usages": [{"file": "A.prefab"}],
                "usage_count": 50,
                "returned_usages": 1,
                "truncated_usages": 49,
                "scanned_files": 100,
            },
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            _, result = _run(
                server.call_tool(
                    "find_referencing_assets",
                    {"asset_or_guid": "x" * 32, "max_results": 1},
                )
            )

        self.assertTrue(result["metadata"]["truncated"])
        self.assertEqual(50, result["metadata"]["total_count"])

    def test_error_raises_tool_error(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        server = create_server()
        mock_step = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="REF404",
            message="scope path status could not be read",
            data={},
            diagnostics=[],
        )
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_orch = mock_cls.default.return_value
            mock_orch.reference_resolver.where_used.return_value = mock_step
            with self.assertRaises(ToolError) as ctx:
                _run(
                    server.call_tool(
                        "find_referencing_assets",
                        {"asset_or_guid": "x" * 32},
                    )
                )
            message = str(ctx.exception)
            self.assertIn("REF404", message)
            self.assertIn("scope path status could not be read", message)
            self.assertNotIn("PermissionError", message)
            self.assertNotIn("OSError", message)


class TestEditorReadOnlyTools(unittest.TestCase):
    """Test read-only editor bridge MCP tools."""

    def test_editor_screenshot_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"output_path": "/tmp/shot.png"}}
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value=mock_response) as mock_send:
            _, result = _run(server.call_tool("editor_screenshot", {"view": "game", "width": 1920}))
        self.assertEqual(mock_response, result)
        # Default refresh=True: refresh + capture = 2 calls
        self.assertEqual(mock_send.call_count, 2)

    def test_editor_screenshot_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {}))
        # Default refresh=True means 2 calls: refresh + capture
        self.assertEqual(mock_send.call_count, 2)
        mock_send.assert_any_call(action="capture_screenshot", view="scene", width=0, height=0)

    def test_editor_screenshot_refresh_true_calls_refresh_then_capture(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {"refresh": True}))
        self.assertEqual(mock_send.call_count, 2)
        calls = mock_send.call_args_list
        self.assertEqual(calls[0], call(action="refresh_asset_database"))
        self.assertEqual(calls[1], call(action="capture_screenshot", view="scene", width=0, height=0))

    def test_editor_screenshot_refresh_false_skips_refresh(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_screenshot", {"refresh": False}))
        mock_send.assert_called_once_with(action="capture_screenshot", view="scene", width=0, height=0)

    def test_editor_screenshot_refresh_failure_stops_before_capture(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_view.send_action",
            side_effect=Exception("refresh failed"),
        ) as mock_send:
            with self.assertRaises(ToolError) as ctx:
                _run(server.call_tool("editor_screenshot", {"refresh": True}))

        mock_send.assert_called_once_with(action="refresh_asset_database")
        self.assertIn("refresh failed", str(ctx.exception))

    def test_editor_select_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_select",
                    {
                        "hierarchy_path": "/Canvas/Panel",
                        "prefab_asset_path": "Assets/UI.prefab",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="select_object",
            hierarchy_path="/Canvas/Panel",
            prefab_asset_path="Assets/UI.prefab",
        )
        self.assertTrue(result["success"])

    def test_editor_select_omits_empty_prefab_asset_path(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_select", {"hierarchy_path": "/Root/Child"}))
        _, kwargs = mock_send.call_args
        self.assertNotIn("prefab_asset_path", kwargs)

    def test_editor_frame_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {"zoom": 2.5}))
        mock_send.assert_called_once_with(
            action="frame_selected",
            zoom=2.5,
            bounds_policy="all_visible_renderers",
        )

    def test_editor_frame_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_frame", {}))
        mock_send.assert_called_once_with(
            action="frame_selected",
            zoom=0.0,
            bounds_policy="all_visible_renderers",
        )

    def test_editor_frame_preserves_bounds_payload(self) -> None:
        """Issue #115 — Python continuous-integration coverage for the
        framing-bounds regression. The wrapper must surface the bridge's
        ``bounds_center`` and ``bounds_extents`` fields unchanged so the
        ``SynchronizeBoundsSourcesForFrame`` regression is observable
        from the MCP layer.
        """
        server = create_server()
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_FRAME_OK",
            "message": "Framed selection",
            "data": {
                "selected_object": "Avatar",
                "bounds_center": [0.0, 1.5, 0.0],
                "bounds_extents": [0.5, 1.0, 0.5],
                "executed": True,
                "read_only": False,
            },
            "diagnostics": [],
        }
        with patch(
            "prefab_sentinel.mcp_tools_editor_view.send_action",
            return_value=bridge_response,
        ):
            _, result = _run(server.call_tool("editor_frame", {"zoom": 1.0}))
        # Wrapper must preserve the bounds payload byte-for-byte.
        self.assertEqual(bridge_response, result)
        self.assertEqual([0.0, 1.5, 0.0], result["data"]["bounds_center"])
        self.assertEqual([0.5, 1.0, 0.5], result["data"]["bounds_extents"])

    def test_editor_get_camera_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_get_camera", {}))
        mock_send.assert_called_once_with(action="get_camera")

    def test_editor_set_camera_mode_b(self) -> None:
        # Issue #81: orbit-radius MCP argument is named ``size``;
        # the pre-rename ``distance`` alias is not registered on the
        # MCP surface and must not appear on the bridge call kwargs.
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_camera", {"yaw": 45.0, "pitch": 15.0, "size": 3.0}))
        mock_send.assert_called_once_with(action="set_camera", yaw=45.0, pitch=15.0, size=3.0)
        self.assertNotIn("distance", mock_send.call_args.kwargs)

    def test_editor_set_camera_defaults(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_set_camera", {}))
        mock_send.assert_called_once_with(action="set_camera")

    def test_editor_list_children_delegates(self) -> None:
        server = create_server()
        mock_response = {"success": True, "data": {"children": ["A", "B"]}}
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value=mock_response):
            _, result = _run(
                server.call_tool(
                    "editor_list_children",
                    {
                        "hierarchy_path": "/Root",
                        "depth": 2,
                    },
                )
            )
        self.assertEqual(mock_response, result)

    def test_editor_list_children_default_depth(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_children", {"hierarchy_path": "/Root"}))
        mock_send.assert_called_once_with(action="list_children", hierarchy_path="/Root", depth=1)

    def test_editor_list_materials_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_materials", {"hierarchy_path": "/Body"}))
        mock_send.assert_called_once_with(action="list_materials", hierarchy_path="/Body")

    def test_editor_list_roots_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_roots", {}))
        mock_send.assert_called_once_with(action="list_roots")

    def test_editor_get_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_get_material_property",
                    {
                        "hierarchy_path": "/Body",
                        "material_index": 0,
                        "property_name": "_Color",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="get_material_property",
            hierarchy_path="/Body",
            material_index=0,
            property_name="_Color",
        )

    def test_editor_get_material_property_default_property_name(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_get_material_property",
                    {
                        "hierarchy_path": "/Body",
                        "material_index": 0,
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="get_material_property",
            hierarchy_path="/Body",
            material_index=0,
            property_name="",
        )

    def test_editor_console_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_console",
                    {
                        "max_entries": 50,
                        "log_type_filter": "error",
                        "since_seconds": 10.0,
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=50,
            log_type_filter="error",
            since_seconds=10.0,
            classification_filter="all",
            order="newest_first",
            cursor="",
            phase_filter="all",
        )

    def test_editor_console_defaults(self) -> None:
        """Issue #113: defaults are newest-first + 60-second window + empty cursor."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {}))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=200,
            log_type_filter="all",
            since_seconds=60.0,
            classification_filter="all",
            order="newest_first",
            cursor="",
            phase_filter="all",
        )

    def test_editor_console_cursor_passthrough(self) -> None:
        """Issue #113: explicit cursor token forwards verbatim."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {"cursor": "opaque-token-42"}))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=200,
            log_type_filter="all",
            since_seconds=60.0,
            classification_filter="all",
            order="newest_first",
            cursor="opaque-token-42",
            phase_filter="all",
        )

    def test_editor_console_order_passthrough(self) -> None:
        """Issue #113: explicit ordering keyword forwards verbatim."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_console", {"order": "oldest_first"}))
        mock_send.assert_called_once_with(
            action="capture_console_logs",
            max_entries=200,
            log_type_filter="all",
            since_seconds=60.0,
            classification_filter="all",
            order="oldest_first",
            cursor="",
            phase_filter="all",
        )


class TestEditorSideEffectTools(unittest.TestCase):
    """Test side-effect editor bridge MCP tools."""

    def test_editor_refresh_delegates(self) -> None:
        # Issue #70: editor_refresh is compile-aware — it asks the bridge
        # to wait for and report a refresh-triggered compile and sizes its
        # transport budget to cover a compile plus domain reload.
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _, result = _run(server.call_tool("editor_refresh", {}))
        mock_send.assert_called_once_with(
            action="refresh_asset_database",
            timeout_sec=65,
            wait_for_compile=True,
        )
        self.assertTrue(result["success"])

    def test_editor_recompile_delegates(self) -> None:
        # Issue #54: the bare ``editor_recompile`` tool is the
        # synchronous/blocking variant, driving the
        # ``editor_recompile_and_wait`` bridge action.
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_recompile", {}))
        mock_send.assert_called_once_with(
            action="editor_recompile_and_wait",
            timeout_sec=65,
            request_extras={"timeout_sec": 60.0},
        )

    def test_editor_run_tests_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=300)

    def test_editor_run_tests_custom_timeout(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_view.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_run_tests", {"timeout_sec": 600}))
        mock_send.assert_called_once_with(action="run_integration_tests", timeout_sec=600)


class TestEditorRecompileNaming(unittest.TestCase):
    """T-54-1 / issue #71: only the synchronous blocking ``editor_recompile``
    tool is registered. The fire-and-return ``editor_recompile_async`` tool
    is retired, and no legacy ``editor_recompile_and_wait`` tool remains.
    """

    def test_blocking_recompile_registered_and_fire_and_return_absent(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        names = {t.name for t in tools}
        self.assertIn(
            "editor_recompile",
            names,
            msg="the blocking editor_recompile tool must stay registered",
        )
        self.assertNotIn(
            "editor_recompile_async",
            names,
            msg="#71: the retired fire-and-return recompile tool must be absent",
        )
        self.assertNotIn("editor_recompile_and_wait", names)

    def test_bare_recompile_is_synchronous_blocking(self) -> None:
        # The bare name drives the synchronous ``editor_recompile_and_wait``
        # bridge action and forwards a timeout budget.
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_view.send_action",
            return_value={"success": True},
        ) as mock_send:
            _run(server.call_tool("editor_recompile", {"timeout_sec": 30.0}))
        mock_send.assert_called_once_with(
            action="editor_recompile_and_wait",
            timeout_sec=35,
            request_extras={"timeout_sec": 30.0},
        )


class TestEditorAuditReclassification(unittest.TestCase):
    """T-49-1 / T-49-2: audit-pair reclassification (issue #49).

    Five editor tools newly gate on the audit pair; two no longer do.
    """

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def _newly_audited_calls(self) -> list[tuple[str, dict[str, Any]]]:
        """The five #49 newly-audited tools and a no-audit argument set."""
        return [
            ("editor_execute_menu_item", {"menu_path": "Tools/X"}),
            (
                "editor_safe_save_prefab",
                {
                    "hierarchy_path": "/Obj",
                    "asset_path": "Assets/X.prefab",
                    "protect_components": [],
                },
            ),
            (
                "editor_create_udon_program_asset",
                {"asset_path": "Assets/Scripts/X.cs"},
            ),
            ("editor_create_scene", {"asset_path": "Assets/Scenes/X.unity"}),
            ("editor_save_scene", {}),
        ]

    def test_newly_audited_tools_gate_on_audit_pair(self) -> None:
        """T-49-1: each newly-audited tool returns CHANGE_REASON_REQUIRED
        when called without the audit pair."""
        server = create_server()
        for tool_name, args in self._newly_audited_calls():
            with self.subTest(tool=tool_name):
                with (
                    patch(
                        "prefab_sentinel.mcp_tools_editor_write.send_action",
                    ) as w,
                    patch(
                        "prefab_sentinel.mcp_tools_editor_ops.send_action",
                    ) as o,
                    patch(
                        "prefab_sentinel.mcp_tools_editor_batch.send_action",
                    ) as b,
                ):
                    _, result = _run(server.call_tool(tool_name, args))
                    w.assert_not_called()
                    o.assert_not_called()
                    b.assert_not_called()
                assert_error_envelope(
                    result,
                    code="CHANGE_REASON_REQUIRED",
                    severity="error",
                )

    def test_de_audited_tools_reject_confirm_argument(self) -> None:
        """T-49-2: the two de-audited tools raise TypeError on a confirm arg."""
        server = create_server()
        tools = server._tool_manager._tools
        for tool_name in (
            "editor_batch_set_blend_shape",
            "editor_apply_animation_clip",
        ):
            with self.subTest(tool=tool_name):
                fn = tools[tool_name].fn
                with self.assertRaises(TypeError) as cm:
                    if tool_name == "editor_batch_set_blend_shape":
                        fn(
                            hierarchy_path="/Obj",
                            shapes=[],
                            confirm=True,
                            change_reason="x",
                        )
                    else:
                        fn(
                            asset_path="Assets/X.anim",
                            target_hierarchy_path="/Obj",
                            confirm=True,
                            change_reason="x",
                        )
                self.assertIn("confirm", str(cm.exception))


class TestEditorAddUdonSharpComponentTool(unittest.TestCase):
    """T-46-1 / T-46-2: UdonSharp program-asset pre-check codes pass through.

    The pre-check codes are emitted by the C# bridge; the Python wrapper
    must surface the bridge envelope verbatim rather than mask it.
    """

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_absent_program_asset_yields_actionable_code(self) -> None:
        """T-46-1: the no-program-asset bridge envelope passes through."""
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_UDON_ADD_NO_PROGRAM_ASSET",
            "message": (
                "No UdonSharpProgramAsset for VVMW.PlayController; create "
                "one with editor_create_udon_program_asset then recompile."
            ),
            "data": {},
            "diagnostics": [],
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_udonsharp.send_action",
            return_value=bridge_envelope,
        ):
            _, result = _run(
                server.call_tool(
                    "editor_add_udonsharp_component",
                    {
                        "hierarchy_path": "/UI/PlayButton",
                        "type_full_name": "VVMW.PlayController",
                        "confirm": True,
                        "change_reason": "add PlayController",
                    },
                )
            )
        assert_error_envelope(
            result,
            code="EDITOR_CTRL_UDON_ADD_NO_PROGRAM_ASSET",
            severity="error",
            message_match=r"recompile",
        )

    def test_uncompiled_program_asset_yields_actionable_code(self) -> None:
        """T-46-2: the not-compiled bridge envelope passes through."""
        bridge_envelope = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_CTRL_UDON_ADD_PROGRAM_NOT_COMPILED",
            "message": (
                "UdonSharpProgramAsset for VVMW.PlayController is not compiled; run editor_recompile then retry."
            ),
            "data": {},
            "diagnostics": [],
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_udonsharp.send_action",
            return_value=bridge_envelope,
        ):
            _, result = _run(
                server.call_tool(
                    "editor_add_udonsharp_component",
                    {
                        "hierarchy_path": "/UI/PlayButton",
                        "type_full_name": "VVMW.PlayController",
                        "confirm": True,
                        "change_reason": "add PlayController",
                    },
                )
            )
        assert_error_envelope(
            result,
            code="EDITOR_CTRL_UDON_ADD_PROGRAM_NOT_COMPILED",
            severity="error",
        )


class TestEditorSetPropertyValueSemantics(unittest.TestCase):
    """T-52-1: editor_set_property distinguishes empty-string from unspecified."""

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_empty_string_write_carries_value_present_marker(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_property",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "property_name": "label",
                        "value": "",
                    },
                )
            )
        kwargs = mock_send.call_args.kwargs
        self.assertEqual("", kwargs["property_value"])
        self.assertTrue(kwargs["property_value_present"])

    def test_unspecified_value_carries_absent_marker(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_property",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "property_name": "target",
                        "object_reference": "/Other",
                    },
                )
            )
        kwargs = mock_send.call_args.kwargs
        self.assertNotIn("property_value", kwargs)
        self.assertEqual("/Other", kwargs["object_reference"])

    def test_omitting_value_entirely_marks_value_not_present(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_property",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "property_name": "label",
                    },
                )
            )
        kwargs = mock_send.call_args.kwargs
        self.assertFalse(kwargs["property_value_present"])
        self.assertNotIn("property_value", kwargs)


class TestEditorSerializedPropertyTools(unittest.TestCase):
    """Issue #112 serialized-property MCP wrappers."""

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_read_forwards_raw_property_path_and_expands_payload(self) -> None:
        server = create_server()
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_SERIALIZED_PROPERTY_READ_OK",
            "message": "Read serialized property.",
            "data": {
                "serialized_property_json": json.dumps(
                    {
                        "property_path": "m_Name",
                        "value_kind": "string",
                    }
                ),
            },
            "diagnostics": [],
        }
        with patch(
            "prefab_sentinel.mcp_tools_editor_serialized_property.send_action",
            return_value=bridge_response,
        ) as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_serialized_property_read",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "ExampleComponent",
                        "component_index": 2,
                        "property_path": "m_Name",
                    },
                )
            )

        mock_send.assert_called_once_with(
            action="editor_serialized_property_read",
            hierarchy_path="/Obj",
            component_type="ExampleComponent",
            component_index=2,
            property_path="m_Name",
        )
        self.assertEqual("EDITOR_CTRL_SERIALIZED_PROPERTY_READ_OK", result["code"])
        self.assertEqual("m_Name", result["data"]["serialized_property"]["property_path"])

    def test_read_preserves_malformed_serialized_property_json(self) -> None:
        server = create_server()
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_SERIALIZED_PROPERTY_READ_OK",
            "message": "Read serialized property.",
            "data": {"serialized_property_json": "{not-json"},
            "diagnostics": [],
        }
        with patch(
            "prefab_sentinel.mcp_tools_editor_serialized_property.send_action",
            return_value=bridge_response,
        ) as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_serialized_property_read",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "ExampleComponent",
                        "property_path": "m_Name",
                    },
                )
            )

        mock_send.assert_called_once_with(
            action="editor_serialized_property_read",
            hierarchy_path="/Obj",
            component_type="ExampleComponent",
            property_path="m_Name",
        )
        self.assertEqual("{not-json", result["data"]["serialized_property_json"])
        self.assertNotIn("serialized_property", result["data"])

    def test_read_rejects_required_address_fields_before_transport(self) -> None:
        server = create_server()
        cases: list[tuple[dict[str, object], str]] = [
            (
                {"hierarchy_path": "", "component_type": "C", "property_path": "m_Name"},
                "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_PATH",
            ),
            (
                {"hierarchy_path": "/Obj", "component_type": "", "property_path": "m_Name"},
                "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_COMPONENT_TYPE",
            ),
            (
                {"hierarchy_path": "/Obj", "component_type": "C", "property_path": ""},
                "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_PROPERTY_PATH",
            ),
        ]
        with patch("prefab_sentinel.mcp_tools_editor_serialized_property.send_action") as mock_send:
            for payload, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    _, result = _run(server.call_tool("editor_serialized_property_read", payload))
                    self.assertEqual((False, expected_code), (result["success"], result["code"]))
        mock_send.assert_not_called()

    def test_list_defaults_and_cursor_are_forwarded(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_serialized_property.send_action",
            return_value={"success": True, "code": "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_OK"},
        ) as mock_send:
            _, default_result = _run(
                server.call_tool(
                    "editor_serialized_property_list",
                    {"hierarchy_path": "/Obj", "component_type": "ExampleComponent"},
                )
            )
            _, cursor_result = _run(
                server.call_tool(
                    "editor_serialized_property_list",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "ExampleComponent",
                        "cursor": "42",
                    },
                )
            )

        self.assertEqual(
            (
                "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_OK",
                "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_OK",
            ),
            (default_result["code"], cursor_result["code"]),
        )
        self.assertEqual(
            [
                call(
                    action="editor_serialized_property_list",
                    hierarchy_path="/Obj",
                    component_type="ExampleComponent",
                    depth=1,
                    cap=50,
                ),
                call(
                    action="editor_serialized_property_list",
                    hierarchy_path="/Obj",
                    component_type="ExampleComponent",
                    depth=1,
                    cap=50,
                    cursor="42",
                ),
            ],
            mock_send.call_args_list,
        )

    def test_list_invalid_traversal_inputs_stop_before_transport(self) -> None:
        server = create_server()
        cases: list[tuple[dict[str, object], str]] = [
            ({"depth": -1}, "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_LIMIT_INVALID"),
            ({"cap": 201}, "EDITOR_CTRL_SERIALIZED_PROPERTY_LIST_LIMIT_INVALID"),
            ({"cursor": "next"}, "EDITOR_CTRL_SERIALIZED_PROPERTY_CURSOR_INVALID"),
            ({"cursor": "+1"}, "EDITOR_CTRL_SERIALIZED_PROPERTY_CURSOR_INVALID"),
            ({"cursor": " 1"}, "EDITOR_CTRL_SERIALIZED_PROPERTY_CURSOR_INVALID"),
            ({"cursor": "1_0"}, "EDITOR_CTRL_SERIALIZED_PROPERTY_CURSOR_INVALID"),
        ]
        base: dict[str, object] = {"hierarchy_path": "/Obj", "component_type": "ExampleComponent"}
        with patch("prefab_sentinel.mcp_tools_editor_serialized_property.send_action") as mock_send:
            for extra, expected_code in cases:
                payload = {**base, **extra}
                with self.subTest(expected_code=expected_code, payload=payload):
                    _, result = _run(server.call_tool("editor_serialized_property_list", payload))
                    self.assertEqual((False, expected_code), (result["success"], result["code"]))
        mock_send.assert_not_called()

    def test_write_preserves_false_zero_and_empty_string_presence_markers(self) -> None:
        server = create_server()
        calls: list[tuple[dict[str, object], str, str, object]] = [
            ({"bool_value": False}, "serialized_property_bool_value_present", "serialized_property_bool_value", False),
            ({"int_value": 0}, "serialized_property_int_value_present", "serialized_property_int_value", 0),
            ({"string_value": ""}, "serialized_property_string_value_present", "serialized_property_string_value", ""),
        ]
        with patch(
            "prefab_sentinel.mcp_tools_editor_serialized_property.send_action",
            return_value={"success": True, "data": {"executed": False}},
        ) as mock_send:
            for extra, present_key, value_key, expected_value in calls:
                payload = {
                    "hierarchy_path": "/Obj",
                    "component_type": "ExampleComponent",
                    "property_path": "m_Name",
                    **extra,
                }
                _run(server.call_tool("editor_serialized_property_write", payload))
                kwargs = mock_send.call_args.kwargs
                self.assertTrue(kwargs[present_key])
                self.assertEqual(expected_value, kwargs[value_key])
                self.assertFalse(kwargs["confirm"])

    def test_write_rejects_value_conflicts_and_missing_values_before_transport(self) -> None:
        server = create_server()
        cases = [
            ({}, "EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_REQUIRED"),
            ({"bool_value": False, "int_value": 0}, "EDITOR_CTRL_SERIALIZED_PROPERTY_VALUE_CONFLICT"),
            ({"array_size": -1}, "EDITOR_CTRL_SERIALIZED_PROPERTY_ARRAY_SIZE_INVALID"),
        ]
        base = {
            "hierarchy_path": "/Obj",
            "component_type": "ExampleComponent",
            "property_path": "m_Name",
        }
        with patch("prefab_sentinel.mcp_tools_editor_serialized_property.send_action") as mock_send:
            for extra, expected_code in cases:
                payload = {**base, **extra}
                with self.subTest(expected_code=expected_code):
                    _, result = _run(server.call_tool("editor_serialized_property_write", payload))
                    self.assertEqual((False, expected_code), (result["success"], result["code"]))
        mock_send.assert_not_called()

    def test_confirmed_write_requires_trimmed_change_reason(self) -> None:
        server = create_server()
        base = {
            "hierarchy_path": "/Obj",
            "component_type": "ExampleComponent",
            "property_path": "m_Name",
            "int_value": 3,
            "confirm": True,
        }
        with patch("prefab_sentinel.mcp_tools_editor_serialized_property.send_action") as mock_send:
            _, rejected = _run(
                server.call_tool(
                    "editor_serialized_property_write",
                    {**base, "change_reason": "   "},
                )
            )
            self.assertEqual(
                (False, "EDITOR_CTRL_SERIALIZED_PROPERTY_CHANGE_REASON_REQUIRED"),
                (rejected["success"], rejected["code"]),
            )
            mock_send.assert_not_called()

            mock_send.return_value = {
                "success": True,
                "code": "EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK",
            }
            _, accepted = _run(
                server.call_tool(
                    "editor_serialized_property_write",
                    {**base, "change_reason": "  audit reason  "},
                )
            )

        self.assertEqual("EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK", accepted["code"])
        mock_send.assert_called_once_with(
            action="editor_serialized_property_write",
            hierarchy_path="/Obj",
            component_type="ExampleComponent",
            property_path="m_Name",
            confirm=True,
            serialized_property_int_value=3,
            serialized_property_int_value_present=True,
            change_reason="audit reason",
        )


class TestEditorSetUdonSharpFieldValueSemantics(unittest.TestCase):
    """T-52-2: editor_set_udonsharp_field accepts an empty-string value."""

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_empty_string_value_is_accepted(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_udonsharp.send_action",
            return_value={"success": True},
        ) as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_set_udonsharp_field",
                    {
                        "hierarchy_path": "/UI/PlayButton",
                        "property_name": "label",
                        "value": "",
                        "confirm": True,
                        "change_reason": "set label",
                    },
                )
            )
        # No client-side NO_VALUE rejection for an empty-string write.
        self.assertNotEqual(
            "EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE",
            result.get("code"),
        )
        kwargs = mock_send.call_args.kwargs
        self.assertEqual("", kwargs["property_value"])
        self.assertTrue(kwargs["property_value_present"])

    def test_unspecified_value_with_empty_reference_is_rejected(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_udonsharp.send_action",
        ) as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_set_udonsharp_field",
                    {
                        "hierarchy_path": "/UI/PlayButton",
                        "property_name": "label",
                    },
                )
            )
            mock_send.assert_not_called()
        assert_error_envelope(
            result,
            code="EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE",
            severity="error",
        )


class TestEditorDictPathValueSemantics(unittest.TestCase):
    """T-52-3: dict-path tools keep an empty-string entry distinct from absent."""

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_editor_set_properties_empty_string_entry_marked_present(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "properties": [
                            {"property_name": "label", "value": ""},
                            {"property_name": "target", "object_reference": "/Other"},
                        ],
                    },
                )
            )
        ops = json.loads(mock_send.call_args.kwargs["batch_operations_json"])
        # Empty-string entry: value present. object_reference entry: absent.
        self.assertEqual("", ops[0]["value"])
        self.assertTrue(ops[0]["value_present"])
        self.assertFalse(ops[1]["value_present"])

    def test_editor_batch_set_property_empty_string_op_marked_present(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_batch.send_action",
            return_value={"success": True},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_batch_set_property",
                    {
                        "operations": [
                            {
                                "hierarchy_path": "/Obj",
                                "component_type": "MyComp",
                                "property_name": "label",
                                "value": "",
                            },
                            {
                                "hierarchy_path": "/Obj",
                                "component_type": "MyComp",
                                "property_name": "target",
                                "object_reference": "/Other",
                            },
                        ],
                    },
                )
            )
        ops = json.loads(mock_send.call_args.kwargs["batch_operations_json"])
        self.assertEqual("", ops[0]["value"])
        self.assertTrue(ops[0]["value_present"])
        self.assertFalse(ops[1]["value_present"])


class TestEditorArgumentNaming(unittest.TestCase):
    """T-53-1: editor_* address/property arguments conform to the conventions.

    A conforming argument name is accepted; the legacy name raises TypeError.
    """

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def _tool_fn(self, name: str):
        return create_server()._tool_manager._tools[name].fn

    def test_conforming_argument_names_accepted(self) -> None:
        # editor_set_parent: parent_hierarchy_path
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True},
        ) as send:
            self._tool_fn("editor_set_parent")(
                hierarchy_path="/A",
                parent_hierarchy_path="/B",
            )
        send.assert_called_once()
        # editor_open_scene: asset_path
        with patch(
            "prefab_sentinel.mcp_tools_editor_batch.send_action",
            return_value={"success": True},
        ) as send:
            self._tool_fn("editor_open_scene")(asset_path="Assets/X.unity")
        send.assert_called_once()
        # editor_set_material: material_asset_path
        with patch(
            "prefab_sentinel.mcp_tools_editor_write.send_action",
            return_value={"success": True},
        ) as send:
            self._tool_fn("editor_set_material")(
                hierarchy_path="/A",
                material_index=0,
                material_asset_path="Assets/M.mat",
            )
        send.assert_called_once()
        # editor_wire_persistent_listener: target_hierarchy_path
        with patch(
            "prefab_sentinel.mcp_tools_editor_udonsharp.send_action",
            return_value={"success": True},
        ) as send:
            self._tool_fn("editor_wire_persistent_listener")(
                hierarchy_path="/A",
                property_name="onValueChanged",
                target_hierarchy_path="/B",
                method="M",
                arg="x",
                confirm=True,
                change_reason="wire listener",
            )
        send.assert_called_once()

    def test_legacy_parent_path_argument_raises_type_error(self) -> None:
        fn = self._tool_fn("editor_set_parent")
        with self.assertRaises(TypeError) as cm:
            fn(hierarchy_path="/A", parent_path="/B")
        self.assertIn("parent_path", str(cm.exception))

    def test_legacy_scene_path_argument_raises_type_error(self) -> None:
        fn = self._tool_fn("editor_open_scene")
        with self.assertRaises(TypeError) as cm:
            fn(scene_path="Assets/X.unity")
        self.assertIn("scene_path", str(cm.exception))

    def test_legacy_material_path_argument_raises_type_error(self) -> None:
        fn = self._tool_fn("editor_set_material")
        with self.assertRaises(TypeError) as cm:
            fn(hierarchy_path="/A", material_index=0, material_path="Assets/M.mat")
        self.assertIn("material_path", str(cm.exception))

    def test_legacy_target_path_argument_raises_type_error(self) -> None:
        fn = self._tool_fn("editor_wire_persistent_listener")
        with self.assertRaises(TypeError) as cm:
            fn(
                hierarchy_path="/A",
                property_name="onValueChanged",
                target_path="/B",
                method="M",
                arg="x",
            )
        self.assertIn("target_path", str(cm.exception))

    def test_legacy_event_path_argument_raises_type_error(self) -> None:
        """Issue #53/#58: the former event_path argument no longer binds."""
        fn = self._tool_fn("editor_wire_persistent_listener")
        with self.assertRaises(TypeError) as cm:
            fn(
                hierarchy_path="/A",
                event_path="onValueChanged",
                target_hierarchy_path="/B",
                method="M",
                arg="x",
            )
        self.assertIn("event_path", str(cm.exception))

    def test_listener_property_name_travels_on_event_wire_field(self) -> None:
        """Issue #61: the property_name argument is forwarded on the
        event_property_name wire field; the misleading event_path wire
        key is gone."""
        with patch(
            "prefab_sentinel.mcp_tools_editor_udonsharp.send_action",
            return_value={"success": True},
        ) as send:
            self._tool_fn("editor_wire_persistent_listener")(
                hierarchy_path="/A",
                property_name="OnX",
                target_hierarchy_path="/B",
                method="M",
                arg="x",
                confirm=True,
                change_reason="wire listener",
            )
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("editor_wire_persistent_listener", "OnX", False),
            (
                kwargs["action"],
                kwargs["event_property_name"],
                "event_path" in kwargs,
            ),
            msg=(
                "editor_wire_persistent_listener must forward the "
                "property_name argument on the event_property_name wire "
                "field and emit no stale event_path key (issue #61)."
            ),
        )


class TestEditorSetParentWireField(unittest.TestCase):
    """Issue #56 — editor_set_parent transmits the parent address on the
    dedicated parent_hierarchy_path wire field, not the rename field."""

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def _tool_fn(self, name: str):
        return create_server()._tool_manager._tools[name].fn

    def test_parent_address_travels_on_dedicated_field(self) -> None:
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True},
        ) as send:
            self._tool_fn("editor_set_parent")(
                hierarchy_path="/A",
                parent_hierarchy_path="/B",
            )
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("editor_set_parent", "/B"),
            (kwargs["action"], kwargs["parent_hierarchy_path"]),
            msg=(
                "editor_set_parent must send the parent address on the "
                "dedicated parent_hierarchy_path wire field (issue #56)."
            ),
        )
        self.assertNotIn(
            "new_name",
            kwargs,
            msg=("editor_set_parent must not reuse the rename field new_name for the parent address (issue #56)."),
        )

    def test_empty_parent_forwards_scene_root_intent(self) -> None:
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True},
        ) as send:
            self._tool_fn("editor_set_parent")(hierarchy_path="/A")
        kwargs = send.call_args.kwargs
        self.assertEqual(
            ("editor_set_parent", ""),
            (kwargs["action"], kwargs["parent_hierarchy_path"]),
            msg=(
                "editor_set_parent with no parent must still forward a "
                "well-formed payload with an empty parent_hierarchy_path "
                "carrying scene-root intent (issue #56)."
            ),
        )


class TestEditorWriteTools(unittest.TestCase):
    """Test write/mutation editor bridge MCP tools."""

    def test_editor_instantiate_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_instantiate",
                    {
                        "asset_path": "Assets/Prefabs/Mic.prefab",
                        "hierarchy_path": "/Canvas",
                        "position": "0,1.5,0",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="instantiate_to_scene",
            asset_path="Assets/Prefabs/Mic.prefab",
            hierarchy_path="/Canvas",
            position=[0.0, 1.5, 0.0],
        )

    def test_editor_instantiate_no_position(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_instantiate",
                    {
                        "asset_path": "Assets/Prefabs/Mic.prefab",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="instantiate_to_scene",
            asset_path="Assets/Prefabs/Mic.prefab",
            hierarchy_path="",
        )

    def test_editor_instantiate_invalid_position_count(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action"):
            _, result = _run(
                server.call_tool(
                    "editor_instantiate",
                    {
                        "asset_path": "Assets/X.prefab",
                        "position": "1,2",
                    },
                )
            )
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_instantiate_invalid_position_value(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action"):
            _, result = _run(
                server.call_tool(
                    "editor_instantiate",
                    {
                        "asset_path": "Assets/X.prefab",
                        "position": "a,b,c",
                    },
                )
            )
        self.assertFalse(result["success"])
        self.assertEqual("INVALID_POSITION", result["code"])

    def test_editor_set_material_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_material",
                    {
                        "hierarchy_path": "/Body",
                        "material_index": 0,
                        "material_asset_guid": "abc123def456",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="set_material",
            hierarchy_path="/Body",
            material_index=0,
            material_guid="abc123def456",
        )

    def test_editor_set_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_material_property",
                    {
                        "hierarchy_path": "/Foo",
                        "material_index": 0,
                        "property_name": "_Color",
                        "value": "[1, 0, 0, 1]",
                        "confirm": True,
                        "change_reason": "set material color",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="set_material_property",
            hierarchy_path="/Foo",
            material_index=0,
            property_name="_Color",
            property_value="[1, 0, 0, 1]",
        )

    def test_editor_set_material_property_requires_audit_pair(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_set_material_property",
                    {
                        "hierarchy_path": "/Foo",
                        "material_index": 0,
                        "property_name": "_Color",
                        "value": "[1, 0, 0, 1]",
                    },
                )
            )

        mock_send.assert_not_called()
        assert_error_envelope(
            result,
            code="CHANGE_REASON_REQUIRED",
            severity="error",
        )

    def test_editor_batch_set_material_property_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_batch_set_material_property",
                    {
                        "hierarchy_path": "/Avatar/Hair",
                        "material_index": 0,
                        "properties": [
                            {"name": "_Color", "value": "[1, 0, 0, 1]"},
                            {"name": "_MainTexHSVG", "value": [0.02, 0.48, 1.18, 1]},
                        ],
                    },
                )
            )
        args = mock_send.call_args
        self.assertEqual(args.kwargs["action"], "editor_batch_set_material_property")
        self.assertEqual(args.kwargs["hierarchy_path"], "/Avatar/Hair")
        self.assertEqual(args.kwargs["material_index"], 0)
        ops = json.loads(args.kwargs["batch_operations_json"])
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0]["name"], "_Color")
        self.assertEqual(ops[0]["value"], "[1, 0, 0, 1]")
        # list value should be JSON-stringified
        self.assertEqual(ops[1]["value"], "[0.02, 0.48, 1.18, 1]")

    def test_editor_batch_set_material_property_by_path_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_batch_set_material_property",
                    {
                        "material_asset_path": "Assets/Materials/Hair.mat",
                        "properties": [
                            {"name": "_Color", "value": "[1, 1, 1, 1]"},
                        ],
                    },
                )
            )
        args = mock_send.call_args
        self.assertEqual(args.kwargs["action"], "editor_batch_set_material_property")
        self.assertEqual(args.kwargs["material_path"], "Assets/Materials/Hair.mat")
        self.assertNotIn("hierarchy_path", args.kwargs)
        self.assertNotIn("material_index", args.kwargs)

    def test_editor_batch_set_material_property_by_guid_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_batch_set_material_property",
                    {
                        "material_asset_guid": "abc123def456abc123def456abc123de",
                        "properties": [
                            {"name": "_Float", "value": 0.5},
                        ],
                    },
                )
            )
        args = mock_send.call_args
        self.assertEqual(args.kwargs["action"], "editor_batch_set_material_property")
        self.assertEqual(args.kwargs["material_guid"], "abc123def456abc123def456abc123de")
        self.assertNotIn("hierarchy_path", args.kwargs)
        ops = json.loads(args.kwargs["batch_operations_json"])
        self.assertEqual(ops[0]["value"], "0.5")

    def test_editor_delete_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_delete", {"hierarchy_path": "/OldObject"}))
        mock_send.assert_called_once_with(action="delete_object", hierarchy_path="/OldObject")

    def test_editor_add_component_preserves_reused_envelope(self) -> None:
        """Issue #103 — Python continuous-integration coverage for the
        UdonSharp duplicate-guard reuse path. The wrapper must surface
        the bridge's ``EDITOR_CTRL_ADD_COMPONENT_REUSED`` envelope and
        accompanying data unchanged so the
        ``HandleUdonSharpAddComponentIdempotent`` reuse branch is
        observable from the MCP layer.
        """
        server = create_server()
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_ADD_COMPONENT_REUSED",
            "message": "Existing UdonSharp pair reused for AvatarSync",
            "data": {
                "selected_object": "Player",
                "asset_path": "VRC.Avatar.AvatarSync",
                "executed": False,
                "read_only": False,
            },
            "diagnostics": [],
        }
        with patch(
            "prefab_sentinel.mcp_tools_editor_write.send_action",
            return_value=bridge_response,
        ):
            _, result = _run(
                server.call_tool(
                    "editor_add_component",
                    {
                        "hierarchy_path": "/Player",
                        "component_type": "AvatarSync",
                    },
                )
            )
        self.assertEqual(bridge_response, result)
        self.assertEqual("EDITOR_CTRL_ADD_COMPONENT_REUSED", result["code"])
        self.assertFalse(result["data"]["executed"])

    def test_editor_add_component_preserves_relinked_envelope(self) -> None:
        """Issue #103 — Python continuous-integration coverage for the
        UdonSharp duplicate-guard relink path. The wrapper must surface
        the bridge's ``EDITOR_CTRL_ADD_COMPONENT_RELINKED`` envelope and
        accompanying data unchanged so the stranded-proxy relink branch
        is observable from the MCP layer.
        """
        server = create_server()
        bridge_response = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_ADD_COMPONENT_RELINKED",
            "message": "Existing proxy re-linked to new UdonBehaviour for AvatarSync",
            "data": {
                "selected_object": "Player",
                "asset_path": "VRC.Avatar.AvatarSync",
                "executed": True,
                "read_only": False,
            },
            "diagnostics": [],
        }
        with patch(
            "prefab_sentinel.mcp_tools_editor_write.send_action",
            return_value=bridge_response,
        ):
            _, result = _run(
                server.call_tool(
                    "editor_add_component",
                    {
                        "hierarchy_path": "/Player",
                        "component_type": "AvatarSync",
                    },
                )
            )
        self.assertEqual(bridge_response, result)
        self.assertEqual("EDITOR_CTRL_ADD_COMPONENT_RELINKED", result["code"])
        self.assertTrue(result["data"]["executed"])

    def test_editor_remove_component_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_remove_component",
                    {
                        "hierarchy_path": "/Player",
                        "component_type": "BoxCollider",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="editor_remove_component",
            hierarchy_path="/Player",
            component_type="BoxCollider",
        )

    def test_editor_remove_component_with_index(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_remove_component",
                    {
                        "hierarchy_path": "/Player",
                        "component_type": "BoxCollider",
                        "index": 1,
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="editor_remove_component",
            hierarchy_path="/Player",
            component_type="BoxCollider",
            component_index=1,
        )

    def test_vrcsdk_upload_delegates(self) -> None:
        """Default platforms=["windows"] is serialized and passed to send_action."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value={"success": True, "data": {}}
        ) as mock_send:
            _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "confirm": False,
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="vrcsdk_upload",
            timeout_sec=600,
            target_type="avatar",
            asset_path="Assets/Avatars/Test.prefab",
            blueprint_id="avtr_test123",
            platforms='["windows"]',
            description="",
            tags="",
            release_status="",
            confirm=False,
        )

    def test_vrcsdk_upload_requires_change_reason(self) -> None:
        """confirm=True without change_reason returns error without calling bridge."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "confirm": True,
                        "change_reason": "",
                    },
                )
            )
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])

    def test_vrcsdk_upload_invalid_platforms_empty(self) -> None:
        """Empty platforms list returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "platforms": [],
                    },
                )
            )
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_invalid_platforms_bad_value(self) -> None:
        """Invalid platform name returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "platforms": ["windows", "ps5"],
                    },
                )
            )
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_invalid_platforms_duplicate(self) -> None:
        """Duplicate platform returns validation error."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "platforms": ["windows", "windows"],
                    },
                )
            )
            mock_send.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual("VRCSDK_INVALID_PLATFORMS", result["code"])

    def test_vrcsdk_upload_converts_platform_results(self) -> None:
        """platform_results_json from C# is converted to platform_results list."""
        server = create_server()
        bridge_response = {
            "success": True,
            "data": {
                "phase": "complete",
                "platform_results_json": '[{"platform":"windows","success":true,"elapsed_sec":45.1}]',
                "original_target_restored": True,
            },
        }
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value=bridge_response):
            _, result = _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "confirm": True,
                        "change_reason": "test upload",
                    },
                )
            )
        self.assertIn("platform_results", result["data"])
        self.assertEqual(result["data"]["platform_results"][0]["platform"], "windows")
        self.assertNotIn("platform_results_json", result["data"])

    def test_vrcsdk_upload_converts_mixed_platform_results(self) -> None:
        """platform_results_json with success + failure + skipped is correctly parsed."""
        server = create_server()
        bridge_response = {
            "success": False,
            "data": {
                "phase": "failed",
                "platform_results_json": '[{"platform":"windows","success":true,"elapsed_sec":45.1},{"platform":"android","success":false,"elapsed_sec":9.9,"error":"Shader error"},{"platform":"ios","skipped":true}]',
                "original_target_restored": True,
            },
        }
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value=bridge_response):
            _, result = _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "platforms": ["windows", "android", "ios"],
                        "confirm": True,
                        "change_reason": "test upload",
                    },
                )
            )
        pr = result["data"]["platform_results"]
        self.assertEqual(len(pr), 3)
        self.assertTrue(pr[0]["success"])
        self.assertFalse(pr[1]["success"])
        self.assertTrue(pr[2]["skipped"])
        self.assertNotIn("platform_results_json", result["data"])

    def test_vrcsdk_upload_dryrun_includes_platforms(self) -> None:
        """dry-run response includes platforms echo-back from Python."""
        server = create_server()
        bridge_response = {"success": True, "data": {"phase": "validated"}}
        with patch("prefab_sentinel.mcp_tools_editor_advanced.send_action", return_value=bridge_response):
            _, result = _run(
                server.call_tool(
                    "vrcsdk_upload",
                    {
                        "target_type": "avatar",
                        "asset_path": "Assets/Avatars/Test.prefab",
                        "blueprint_id": "avtr_test123",
                        "platforms": ["windows", "android"],
                    },
                )
            )
        self.assertEqual(result["data"]["platforms"], ["windows", "android"])

    def test_editor_get_blend_shapes_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_get_blend_shapes",
                    {
                        "hierarchy_path": "/Avatar/Body",
                        "filter": "vrc.v_",
                    },
                )
            )
        # Issue #241: pagination knobs (offset / limit) are always
        # forwarded; defaults are 0 / 200 respectively.
        mock_send.assert_called_once_with(
            action="get_blend_shapes",
            hierarchy_path="/Avatar/Body",
            filter="vrc.v_",
            offset=0,
            limit=200,
        )

    def test_editor_get_blend_shapes_default_filter(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_get_blend_shapes",
                    {
                        "hierarchy_path": "/Avatar/Body",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="get_blend_shapes",
            hierarchy_path="/Avatar/Body",
            filter="",
            offset=0,
            limit=200,
        )

    def test_editor_set_blend_shape_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_blend_shape",
                    {
                        "hierarchy_path": "/Avatar/Body",
                        "name": "vrc.blink",
                        "weight": 75.0,
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="set_blend_shape",
            hierarchy_path="/Avatar/Body",
            blend_shape_name="vrc.blink",
            blend_shape_weight=75.0,
        )

    def test_editor_list_menu_items_delegates(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_menu_items", {"prefix": "Tools/"}))
        mock_send.assert_called_once_with(action="list_menu_items", filter="Tools/")

    def test_editor_list_menu_items_default_prefix(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_list_menu_items", {}))
        mock_send.assert_called_once_with(action="list_menu_items", filter="")

    def test_editor_execute_menu_item_delegates(self) -> None:
        """Issue #225 — the wrapper forwards the menu path and the
        ``assume_compiled`` opt-out flag (defaulting to ``False`` so the
        bridge keeps the implicit barrier active for callers that have
        not asserted compile state).
        """
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_execute_menu_item",
                    {
                        "menu_path": "Tools/NDMF/Manual Bake",
                        "confirm": True,
                        "change_reason": "run bake",
                    },
                )
            )
        mock_send.assert_called_once_with(
            action="execute_menu_item",
            menu_path="Tools/NDMF/Manual Bake",
            assume_compiled=False,
            confirm=True,
            change_reason="run bake",
        )

    def test_editor_execute_menu_item_forwards_assume_compiled_true(self) -> None:
        """Issue #225 — when the caller asserts compiled state, the
        wrapper forwards ``assume_compiled=True`` so the bridge takes
        the synchronous fast path.
        """
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_write.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_execute_menu_item",
                    {
                        "menu_path": "Tools/NDMF/Manual Bake",
                        "assume_compiled": True,
                        "confirm": True,
                        "change_reason": "run bake",
                    },
                )
            )
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(True, kwargs["assume_compiled"])

    def test_editor_execute_menu_item_round_trips_recompile_waited_flag(self) -> None:
        """Issue #225 — the bridge response carries a slow-path
        indicator so the caller knows whether the implicit barrier
        actually fired. The wrapper returns the bridge envelope verbatim
        so the flag round-trips unchanged.
        """
        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_MENU_EXEC_OK",
            "message": "Menu item executed",
            "data": {
                "executed": True,
                "recompile_waited": True,
            },
            "diagnostics": [],
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_write.send_action",
            return_value=bridge_envelope,
        ):
            _, envelope = _run(
                server.call_tool(
                    "editor_execute_menu_item",
                    {
                        "menu_path": "Tools/NDMF/Manual Bake",
                        "confirm": True,
                        "change_reason": "run bake",
                    },
                )
            )
        # Pin verbatim round-trip: the wrapper must not mutate any field
        # of the bridge envelope (per the docstring above and Method
        # Contract for ``editor_execute_menu_item`` in spec.md).
        self.assertEqual(bridge_envelope, envelope)

    def test_editor_batch_add_component_does_not_mutate_input(self) -> None:
        """editor_batch_add_component must not mutate caller-supplied operation dicts."""
        operations = [
            {"hierarchy_path": "/Obj", "component_type": "C", "properties": [{"name": "speed", "value": "10"}]}
        ]
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(server.call_tool("editor_batch_add_component", {"operations": operations}))
        self.assertIn("properties", operations[0])
        call_kwargs = mock_send.call_args[1]
        sent = json.loads(call_kwargs["batch_operations_json"])
        self.assertNotIn("properties", sent[0])
        self.assertIn("properties_json", sent[0])
        self.assertEqual(json.loads(sent[0]["properties_json"]), [{"name": "speed", "value": "10"}])


class TestEditorExecTools(unittest.TestCase):
    """Test editor bridge execution MCP tools (``mcp_tools_editor_exec``)."""

    def test_editor_run_script_delegates_when_confirm_and_reason(self) -> None:
        """T-92-A: ``confirm=True`` + non-empty ``change_reason`` reaches
        ``send_action`` exactly once with the ``run_script`` action."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_exec.send_action",
            return_value={"success": True, "code": "EDITOR_CTRL_RUN_SCRIPT_OK"},
        ) as mock_send:
            _, parsed = _run(
                server.call_tool(
                    "editor_run_script",
                    {
                        "code": "public static void Run() {}",
                        "confirm": True,
                        "change_reason": "smoke test",
                    },
                )
            )
        # Issue #226: the wrapper now forwards a transport poll budget
        # derived from the compile budget so the transport never gives up
        # before the bridge would. Default compile budget (15 s + 5 s
        # margin) sits below the 30 s floor, so the floor is forwarded.
        mock_send.assert_called_once_with(
            action="run_script",
            timeout_sec=30,
            code="public static void Run() {}",
            change_reason="smoke test",
            compile_timeout=15000,
        )
        self.assertTrue(parsed["success"])

    def test_editor_run_script_rejects_when_confirm_false(self) -> None:
        """T-92-B: ``confirm=False`` short-circuits to
        ``CHANGE_REASON_REQUIRED`` without contacting the bridge."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_exec.send_action") as mock_send:
            _, parsed = _run(
                server.call_tool(
                    "editor_run_script",
                    {
                        "code": "public static void Run() {}",
                        "confirm": False,
                        "change_reason": "smoke test",
                    },
                )
            )
        mock_send.assert_not_called()
        self.assertFalse(parsed["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", parsed["code"])

    def test_editor_run_script_rejects_whitespace_only_reason(self) -> None:
        """T-92-C: a whitespace-only ``change_reason`` is rejected with
        ``CHANGE_REASON_REQUIRED`` and the bridge is never invoked."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_exec.send_action") as mock_send:
            _, parsed = _run(
                server.call_tool(
                    "editor_run_script",
                    {
                        "code": "public static void Run() {}",
                        "confirm": True,
                        "change_reason": "   ",
                    },
                )
            )
        mock_send.assert_not_called()
        self.assertFalse(parsed["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", parsed["code"])


class TestInspectionTools(unittest.TestCase):
    """Test inspect_materials and validate_structure MCP tools."""

    def test_inspect_materials_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "data": {"renderers": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_materials.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "inspect_materials",
                    {
                        "asset_path": "Assets/Avatar.prefab",
                    },
                )
            )

        self.assertTrue(result["success"])
        mock_orch.inspect_materials.assert_called_once_with(
            target_path="Assets/Avatar.prefab",
        )

    def test_validate_structure_delegates(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "data": {"issues": []},
        }
        mock_orch = MagicMock()
        mock_orch.inspect_structure.return_value = mock_resp

        server = create_server()
        with patch("prefab_sentinel.session_cache.Phase1Orchestrator") as mock_cls:
            mock_cls.default.return_value = mock_orch
            _, result = _run(
                server.call_tool(
                    "validate_structure",
                    {
                        "asset_path": "Assets/Scene.unity",
                    },
                )
            )

        self.assertTrue(result["success"])
        mock_orch.inspect_structure.assert_called_once_with(
            target_path="Assets/Scene.unity",
            diagnostics_baseline=DiagnosticsBaseline(
                known_diagnostics=(), path=None, status="not_loaded_no_project_root"
            ),
        )


class TestRevertOverridesTool(unittest.TestCase):
    """Test revert_overrides MCP tool."""

    def test_dry_run_default(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "code": "REVERT_DRY_RUN",
            "data": {"match_count": 1, "read_only": True},
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _, result = _run(
                server.call_tool(
                    "revert_overrides",
                    {
                        "asset_path": "Assets/V.prefab",
                        "target_file_id": "12345",
                        "property_path": "m_Color.r",
                    },
                )
            )

        mock_revert.assert_called_once_with(
            variant_path="Assets/V.prefab",
            target_file_id="12345",
            property_path="m_Color.r",
            dry_run=True,
            confirm=False,
            change_reason=None,
        )
        self.assertTrue(result["success"])

    def test_confirm_mode(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {
            "success": True,
            "code": "REVERT_APPLIED",
            "data": {"match_count": 1, "read_only": False},
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _, result = _run(
                server.call_tool(
                    "revert_overrides",
                    {
                        "asset_path": "Assets/V.prefab",
                        "target_file_id": "12345",
                        "property_path": "m_Color.r",
                        "confirm": True,
                        "change_reason": "Remove unwanted override",
                    },
                )
            )

        mock_revert.assert_called_once_with(
            variant_path="Assets/V.prefab",
            target_file_id="12345",
            property_path="m_Color.r",
            dry_run=False,
            confirm=True,
            change_reason="Remove unwanted override",
        )
        self.assertTrue(result["success"])

    def test_empty_change_reason_becomes_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
            return_value=mock_resp,
        ) as mock_revert:
            _run(
                server.call_tool(
                    "revert_overrides",
                    {
                        "asset_path": "Assets/V.prefab",
                        "target_file_id": "12345",
                        "property_path": "m_Color.r",
                        "change_reason": "",
                    },
                )
            )

        _, kwargs = mock_revert.call_args
        self.assertIsNone(kwargs["change_reason"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_patch.revert_overrides_impl",
        ) as mock_revert:
            _, result = _run(
                server.call_tool(
                    "revert_overrides",
                    {
                        "asset_path": "Assets/V.prefab",
                        "target_file_id": "12345",
                        "property_path": "m_Color.r",
                        "confirm": True,
                        "change_reason": "",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_revert.assert_not_called()


class TestInspectHierarchyTool(unittest.TestCase):
    """Tests for the inspect_hierarchy MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"tree": "..."}}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("inspect_hierarchy", {"asset_path": "Assets/A.prefab"}))

        self.assertTrue(result["success"])
        mock_orch.inspect_hierarchy.assert_called_once_with(
            target_path="Assets/A.prefab",
            max_depth=None,
            show_components=True,
            expand_monobehaviour=False,
            expand_prefab_instances=False,
            timeout_sec=None,
        )

    def test_passes_optional_params(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _run(
                server.call_tool(
                    "inspect_hierarchy",
                    {
                        "asset_path": "Assets/A.prefab",
                        "depth": 2,
                        "show_components": False,
                    },
                )
            )

        mock_orch.inspect_hierarchy.assert_called_once_with(
            target_path="Assets/A.prefab",
            max_depth=2,
            show_components=False,
            expand_monobehaviour=False,
            expand_prefab_instances=False,
            timeout_sec=None,
        )

    def test_forwards_timeout_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "inspect_hierarchy",
                    {
                        "asset_path": "Assets/A.prefab",
                        "timeout_sec": 2.5,
                    },
                )
            )

        self.assertEqual(
            (True, 2.5),
            (result["success"], mock_orch.inspect_hierarchy.call_args.kwargs["timeout_sec"]),
            msg="inspect_hierarchy MCP wrapper must forward timeout_sec unchanged.",
        )

    def test_passes_prefab_instance_expansion_option(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"roots": []}}
        mock_orch = MagicMock()
        mock_orch.inspect_hierarchy.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "inspect_hierarchy",
                    {
                        "asset_path": "Assets/A.prefab",
                        "expand_prefab_instances": True,
                    },
                )
            )

        self.assertEqual(
            (True, True),
            (result["success"], mock_orch.inspect_hierarchy.call_args.kwargs["expand_prefab_instances"]),
            msg="inspect_hierarchy MCP wrapper must forward expand_prefab_instances=True unchanged.",
        )


class TestEffectiveInspectorTools(unittest.TestCase):
    def test_transform_inspector_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "INSPECT_TRANSFORM_VALUES"}
        mock_orch = MagicMock()
        mock_orch.inspect_transform_effective_values.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "inspect_transform_effective_values",
                    {"asset_path": "Assets/Host.prefab", "symbol_path": "Root/Child"},
                )
            )

        self.assertEqual(
            (True, "INSPECT_TRANSFORM_VALUES"),
            (result["success"], result["code"]),
            msg=f"Transform inspector MCP wrapper should pass through orchestrator response; observed result={result!r}",
        )
        mock_orch.inspect_transform_effective_values.assert_called_once_with(
            asset_path="Assets/Host.prefab",
            symbol_path="Root/Child",
        )

    def test_unity_event_listener_inspector_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "INSPECT_UNITY_EVENT_LISTENERS"}
        mock_orch = MagicMock()
        mock_orch.inspect_unity_event_listeners.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "inspect_unity_event_listeners",
                    {
                        "asset_path": "Assets/Control.prefab",
                        "symbol_path": "Control",
                        "component_type": "Button",
                        "property_name": "onClick",
                    },
                )
            )

        self.assertEqual(
            (True, "INSPECT_UNITY_EVENT_LISTENERS"),
            (result["success"], result["code"]),
            msg=f"UnityEvent inspector MCP wrapper should pass through orchestrator response; observed result={result!r}",
        )
        mock_orch.inspect_unity_event_listeners.assert_called_once_with(
            asset_path="Assets/Control.prefab",
            symbol_path="Control",
            component_type="Button",
            property_name="onClick",
        )


class TestValidateRuntimeTool(unittest.TestCase):
    def _runtime_with_validation_steps(self):
        from unittest.mock import MagicMock

        from prefab_sentinel.contracts import Severity, ToolResponse

        runtime = MagicMock()
        runtime.assert_no_critical_errors = MagicMock()
        runtime.compile_udonsharp.return_value = ToolResponse(
            True, Severity.INFO, "RUN_COMPILE_OK", "m", {"read_only": True}
        )
        runtime.run_clientsim.return_value = ToolResponse(
            True, Severity.INFO, "RUN_CLIENTSIM_OK", "m", {"read_only": False}
        )
        runtime.collect_unity_console.return_value = ToolResponse(
            True,
            Severity.INFO,
            "RUN_LOG_COLLECTED",
            "m",
            {"read_only": True, "log_lines": []},
        )
        runtime.classify_errors.return_value = ToolResponse(
            True, Severity.INFO, "RUN_CLASSIFY_OK", "m", {"read_only": True}
        )
        runtime.assert_no_critical_errors.return_value = ToolResponse(
            True, Severity.INFO, "RUN_ASSERT_OK", "m", {"read_only": True}
        )
        return runtime

    def test_delegates_to_orchestrator_with_compile_only_defaults(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"steps": []}}
        mock_orch = MagicMock()
        mock_orch.validate_runtime.return_value = mock_resp

        server = create_server()
        with patch.object(ProjectSession, "get_orchestrator", return_value=mock_orch):
            _, result = _run(
                server.call_tool(
                    "validate_runtime",
                    {"asset_path": "Assets/Scenes/Main.unity"},
                )
            )

        self.assertEqual(
            True,
            result["success"],
            msg=f"validate_runtime tool result mismatch: {result!r}",
        )
        mock_orch.validate_runtime.assert_called_once_with(
            scene_path="Assets/Scenes/Main.unity",
            profile="compile_only",
            log_file=None,
            since_timestamp=None,
            allow_warnings=False,
            max_diagnostics=200,
            confirm=False,
            change_reason=None,
            allow_dirty_before_clientsim=False,
        )

    def test_passes_all_params(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.validate_runtime.return_value = mock_resp

        server = create_server()
        with patch.object(ProjectSession, "get_orchestrator", return_value=mock_orch):
            _run(
                server.call_tool(
                    "validate_runtime",
                    {
                        "asset_path": "Assets/S.unity",
                        "profile": "clientsim",
                        "log_file": "/tmp/Editor.log",
                        "since_timestamp": "2026-05-30T00:00:00Z",
                        "allow_warnings": True,
                        "max_diagnostics": 50,
                        "confirm": True,
                        "change_reason": "audit clientsim validation",
                        "allow_dirty_before_clientsim": True,
                    },
                )
            )

        mock_orch.validate_runtime.assert_called_once_with(
            scene_path="Assets/S.unity",
            profile="clientsim",
            log_file="/tmp/Editor.log",
            since_timestamp="2026-05-30T00:00:00Z",
            allow_warnings=True,
            max_diagnostics=50,
            confirm=True,
            change_reason="audit clientsim validation",
            allow_dirty_before_clientsim=True,
        )

    def test_validate_runtime_rejects_unknown_profile(self) -> None:
        from prefab_sentinel.contracts import Severity
        from prefab_sentinel.orchestrator_validation import validate_runtime

        runtime = self._runtime_with_validation_steps()

        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "Scene.unity"
            scene.write_text("%YAML 1.1\n", encoding="utf-8")
            response = validate_runtime(runtime, str(scene), profile="smoke")

        self.assertEqual(
            (False, "VALIDATE_RUNTIME_PROFILE_UNSUPPORTED", Severity.ERROR),
            (response.success, response.code, response.severity),
            msg=f"unsupported runtime profile envelope mismatch: {response.to_dict()!r}",
        )
        runtime.compile_udonsharp.assert_not_called()
        runtime.run_clientsim.assert_not_called()

    def test_validate_runtime_clientsim_requires_audit_pair(self) -> None:
        from prefab_sentinel.contracts import Severity
        from prefab_sentinel.orchestrator_validation import validate_runtime

        runtime = self._runtime_with_validation_steps()

        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "Scene.unity"
            scene.write_text("%YAML 1.1\n", encoding="utf-8")
            response = validate_runtime(runtime, str(scene), profile="clientsim")

        self.assertEqual(
            (False, "CLIENTSIM_CONFIRM_REQUIRED", Severity.ERROR),
            (response.success, response.code, response.severity),
            msg=f"clientsim audit gate envelope mismatch: {response.to_dict()!r}",
        )
        self.assertIn("explicit audit", response.message)
        runtime.compile_udonsharp.assert_not_called()
        runtime.run_clientsim.assert_not_called()


class TestPatchApplyTool(unittest.TestCase):
    """Tests for the patch_apply MCP tool."""

    def test_dry_run_default(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "PATCH_DRY_RUN"}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        plan_json = '{"plan_version": "2", "resources": [], "ops": []}'
        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(server.call_tool("patch_apply", {"plan": plan_json}))

        self.assertTrue(result["success"])
        mock_orch.patch_apply.assert_called_once_with(
            plan={"plan_version": "2", "resources": [], "ops": []},
            dry_run=True,
            confirm=False,
            plan_sha256=None,
            plan_signature=None,
            change_reason=None,
            out_report=None,
            scope=None,
            runtime_scene=None,
            runtime_profile="default",
            runtime_log_file=None,
            runtime_since_timestamp=None,
            runtime_allow_warnings=False,
            runtime_max_diagnostics=200,
            transactional=True,
        )

    def test_confirm_mode(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "code": "PATCH_APPLIED"}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        plan_json = '{"plan_version": "2", "resources": [], "ops": []}'
        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": plan_json,
                        "confirm": True,
                        "change_reason": "Fix color",
                    },
                )
            )

        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])
        self.assertEqual("Fix color", call_kwargs["change_reason"])

    def test_unused_resource_is_omitted_by_public_patch_apply(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        plan = {
            "plan_version": 2,
            "resources": [
                {"id": "used", "path": "used.json", "kind": "json", "mode": "open"},
                {"id": "unused", "path": "unused.json", "kind": "json", "mode": "open"},
            ],
            "ops": [
                {
                    "resource": "used",
                    "op": "set",
                    "property_path": "key",
                    "value": "value",
                }
            ],
            "postconditions": [],
        }
        dry_run_result = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="DRY_RUN_OK",
            message="dry run completed",
            data={"read_only": True},
            diagnostics=[],
        )

        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = Phase1Orchestrator.default(Path(temporary))
            server = create_server()
            with (
                patch.object(
                    orchestrator.serialized_object,
                    "dry_run_resource_plan",
                    return_value=dry_run_result,
                ) as mock_dry_run,
                patch.object(
                    ProjectSession,
                    "get_orchestrator",
                    return_value=orchestrator,
                ),
            ):
                _, result = _run(
                    server.call_tool(
                        "patch_apply",
                        {"plan": plan},
                    )
                )

        self.assertEqual(
            (
                True,
                2,
                1,
                [
                    {
                        "id": "used",
                        "kind": "json",
                        "path": "used.json",
                        "mode": "open",
                        "executed": True,
                        "applied": 0,
                    },
                    {
                        "id": "unused",
                        "kind": "json",
                        "path": "unused.json",
                        "mode": "open",
                        "executed": False,
                        "applied": 0,
                    },
                ],
            ),
            (
                result["success"],
                result["data"]["resource_count"],
                mock_dry_run.call_count,
                result["data"]["resources"],
            ),
            msg=f"public patch_apply must expose declaration execution truth: {result!r}",
        )


    def _call_public_non_transactional_resource_plan(
        self,
        resources: list[dict[str, Any]],
        ops: list[dict[str, Any]],
        *,
        absolute_resource_paths: bool,
    ) -> tuple[dict[str, Any], int, str]:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        validation_response = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="DRY_RUN_OK",
            message="dry run completed",
            data={"read_only": True},
            diagnostics=[],
        )

        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            orchestrator = Phase1Orchestrator.default(project_root)
            request_resources = resources
            if absolute_resource_paths:
                request_resources = [
                    {
                        **resource,
                        "path": str(
                            (project_root / str(resource["path"])).resolve()
                        ),
                    }
                    for resource in resources
                ]

            def apply_resource(
                *,
                resource: dict[str, Any],
                ops: list[dict[str, Any]],
            ) -> ToolResponse:
                target = (project_root / str(resource["path"])).resolve()
                return ToolResponse(
                    success=True,
                    severity=Severity.INFO,
                    code="SER_APPLY_OK",
                    message="resource applied",
                    data={
                        "target": str(target),
                        "applied": len(ops),
                        "read_only": False,
                        "executed": True,
                    },
                    diagnostics=[],
                )

            server = create_server(project_root=project_root)
            with (
                patch.object(
                    orchestrator.serialized_object,
                    "dry_run_resource_plan",
                    return_value=validation_response,
                ),
                patch.object(
                    orchestrator.serialized_object,
                    "apply_resource_plan",
                    side_effect=apply_resource,
                ) as apply_mock,
                patch.object(
                    ProjectSession,
                    "get_orchestrator",
                    return_value=orchestrator,
                ),
            ):
                _, result = _run(
                    server.call_tool(
                        "patch_apply",
                        {
                            "plan": {
                                "plan_version": 2,
                                "resources": request_resources,
                                "ops": ops,
                                "postconditions": [],
                            },
                            "confirm": True,
                            "change_reason": "test public target projection",
                        },
                    )
                )
            return result, apply_mock.call_count, str(project_root)

    def test_non_transactional_material_target_is_project_relative(self) -> None:
        result, apply_count, project_root = self._call_public_non_transactional_resource_plan(
            resources=[
                {
                    "id": "material",
                    "kind": "material",
                    "path": "Assets/Private.mat",
                    "mode": "open",
                }
            ],
            ops=[
                {
                    "resource": "material",
                    "op": "set",
                    "target": "$asset",
                    "path": "m_Name",
                    "value": "Private",
                }
            ],
            absolute_resource_paths=False,
        )

        self.assertEqual(
            (True, 1, "Assets/Private.mat"),
            (
                result["success"],
                apply_count,
                result["data"]["steps"][-1]["result"]["data"]["target"],
            ),
        )
        self.assertNotIn(project_root, json.dumps(result, sort_keys=True))

    def test_non_transactional_scene_target_is_project_relative(self) -> None:
        result, apply_count, project_root = self._call_public_non_transactional_resource_plan(
            resources=[
                {
                    "id": "scene",
                    "kind": "scene",
                    "path": "Assets/Private.unity",
                    "mode": "open",
                }
            ],
            ops=[
                {"resource": "scene", "op": "open_scene"},
                {"resource": "scene", "op": "save_scene"},
            ],
            absolute_resource_paths=False,
        )

        self.assertEqual(
            (True, 1, "Assets/Private.unity"),
            (
                result["success"],
                apply_count,
                result["data"]["steps"][-1]["result"]["data"]["target"],
            ),
        )
        self.assertNotIn(project_root, json.dumps(result, sort_keys=True))

    def test_non_transactional_multi_resource_paths_are_project_relative(self) -> None:
        result, apply_count, project_root = self._call_public_non_transactional_resource_plan(
            resources=[
                {
                    "id": "material",
                    "kind": "material",
                    "path": "Assets/Private.mat",
                    "mode": "open",
                },
                {
                    "id": "scene",
                    "kind": "scene",
                    "path": "Assets/Private.unity",
                    "mode": "open",
                },
            ],
            ops=[
                {
                    "resource": "material",
                    "op": "set",
                    "target": "$asset",
                    "path": "m_Name",
                    "value": "Private",
                },
                {"resource": "scene", "op": "open_scene"},
                {"resource": "scene", "op": "save_scene"},
            ],
            absolute_resource_paths=True,
        )

        expected_paths = ["Assets/Private.mat", "Assets/Private.unity"]
        apply_targets = [
            step["result"]["data"]["target"]
            for step in result["data"]["steps"]
            if step["step"].startswith("apply_and_save:")
        ]
        resource_paths = [
            resource["path"] for resource in result["data"]["resources"]
        ]
        self.assertEqual(
            (True, 2, expected_paths, expected_paths, expected_paths),
            (
                result["success"],
                apply_count,
                apply_targets,
                result["data"]["targets"],
                resource_paths,
            ),
        )
        self.assertNotIn(project_root, json.dumps(result, sort_keys=True))

    def test_invalid_json_returns_stable_redacted_error(self) -> None:
        secret = "SECRET_JSON"
        server = create_server()

        _, result = _run(
            server.call_tool(
                "patch_apply",
                {"plan": f'{{"plan_version": "{secret}"'},
            )
        )

        self.assertEqual(
            (False, "error", "INVALID_PLAN_JSON", "Patch plan JSON is invalid.", False),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                secret in result["message"],
            ),
            msg=f"malformed patch JSON must return a stable redacted envelope: {result!r}",
        )

    def test_invalid_schema_returns_stable_redacted_error(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        secret = "SECRET_SCHEMA"
        server = create_server()
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = Phase1Orchestrator.default(Path(temporary))
            with patch.object(
                ProjectSession,
                "get_orchestrator",
                return_value=orchestrator,
            ):
                _, result = _run(
                    server.call_tool(
                        "patch_apply",
                        {
                            "plan": {
                                "plan_version": secret,
                                "resources": [],
                                "ops": [],
                            }
                        },
                    )
                )

        self.assertEqual(
            (
                False,
                "error",
                "INVALID_PLAN_SCHEMA",
                "Patch plan schema is invalid.",
                False,
            ),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["message"],
                secret in result["message"],
            ),
            msg=f"invalid patch schema must return a stable redacted envelope: {result!r}",
        )

    def test_orchestrator_acquisition_failure_is_redacted(self) -> None:
        secret = "SENSITIVE_PATCH_ACQUISITION"
        plan = {"plan_version": "2", "resources": [], "ops": []}
        server = create_server()

        with patch.object(
            ProjectSession,
            "get_orchestrator",
            side_effect=ValueError(secret),
        ) as get_orchestrator:
            _, result = _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": plan,
                        "confirm": True,
                        "change_reason": "test acquisition failure",
                    },
                )
            )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            (
                "Patch transaction apply failed.",
                {"boundary": "apply", "state_unknown": False},
            ),
            (result["message"], result["data"]),
        )
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))
        get_orchestrator.assert_called_once_with()

    def test_orchestration_failure_is_redacted_and_marks_state_unknown(self) -> None:
        secret = "SENSITIVE_PATCH_DISPATCH"
        plan = {"plan_version": "2", "resources": [], "ops": []}
        mock_orch = MagicMock()
        mock_orch.patch_apply.side_effect = RuntimeError(secret)
        server = create_server()

        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ) as get_orchestrator:
            _, result = _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": plan,
                        "confirm": True,
                        "change_reason": "test dispatch failure",
                    },
                )
            )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            {"boundary": "apply", "state_unknown": True},
            result["data"],
        )
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))
        get_orchestrator.assert_called_once_with()
        mock_orch.patch_apply.assert_called_once()

    def test_refresh_failure_is_redacted_and_marks_state_unknown(self) -> None:
        secret = "SENSITIVE_PATCH_REFRESH"
        plan = {"plan_version": "2", "resources": [], "ops": []}
        response = MagicMock()
        response.success = True
        response.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "Patch apply completed.",
            "data": {},
            "diagnostics": [],
        }
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = response
        mock_orch.maybe_auto_refresh.side_effect = RuntimeError(secret)
        server = create_server()

        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ) as get_orchestrator:
            _, result = _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": plan,
                        "confirm": True,
                        "change_reason": "test refresh failure",
                    },
                )
            )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            {"boundary": "apply", "state_unknown": True},
            result["data"],
        )
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))
        get_orchestrator.assert_called_once_with()
        mock_orch.patch_apply.assert_called_once_with(
            plan=plan,
            dry_run=False,
            confirm=True,
            plan_sha256=None,
            plan_signature=None,
            change_reason="test refresh failure",
            out_report=None,
            scope=None,
            runtime_scene=None,
            runtime_profile="default",
            runtime_log_file=None,
            runtime_since_timestamp=None,
            runtime_allow_warnings=False,
            runtime_max_diagnostics=200,
            transactional=True,
        )
        mock_orch.maybe_auto_refresh.assert_called_once_with()

    def test_outside_project_resource_paths_fail_before_dry_run_or_apply(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        original = b'{"value": 10}\n'
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            project_root = workspace / "project"
            project_root.mkdir()
            outside = workspace / "outside.json"
            linked = project_root / "linked.json"
            linked.symlink_to(outside)
            escape_paths = {
                "absolute": str(outside),
                "traversal": "../outside.json",
                "symlink": "linked.json",
            }
            server = create_server()

            for escape_kind, resource_path in escape_paths.items():
                for confirm in (False, True):
                    with self.subTest(escape_kind=escape_kind, confirm=confirm):
                        outside.write_bytes(original)
                        orchestrator = Phase1Orchestrator.default(project_root)
                        plan = {
                            "plan_version": 2,
                            "resources": [
                                {
                                    "id": "target",
                                    "path": resource_path,
                                    "kind": "json",
                                    "mode": "open",
                                }
                            ],
                            "ops": [
                                {
                                    "resource": "target",
                                    "op": "set",
                                    "component": "Example.Component",
                                    "path": "value",
                                    "value": 42,
                                }
                            ],
                            "postconditions": [],
                        }
                        with (
                            patch.object(
                                orchestrator.serialized_object,
                                "dry_run_resource_plan",
                                wraps=orchestrator.serialized_object.dry_run_resource_plan,
                            ) as mock_dry_run,
                            patch.object(
                                ProjectSession,
                                "get_orchestrator",
                                return_value=orchestrator,
                            ),
                        ):
                            _, result = _run(
                                server.call_tool(
                                    "patch_apply",
                                    {
                                        "plan": plan,
                                        "confirm": confirm,
                                        "change_reason": "Contain resource target",
                                    },
                                )
                            )

                        self.assertEqual(
                            (
                                False,
                                "error",
                                "INVALID_PLAN_SCHEMA",
                                "Patch plan schema is invalid.",
                                {},
                                [],
                                0,
                                original,
                            ),
                            (
                                result["success"],
                                result["severity"],
                                result["code"],
                                result["message"],
                                result["data"],
                                result["diagnostics"],
                                mock_dry_run.call_count,
                                outside.read_bytes(),
                            ),
                            msg=(
                                "outside resource target reached inspection or mutation: "
                                f"{escape_kind=}, {confirm=}, {result=!r}"
                            ),
                        )

    def test_non_object_json_returns_schema_error_before_orchestrator(self) -> None:
        server = create_server()
        mock_orchestrator = MagicMock()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orchestrator,
        ):
            _, result = _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": "[]",
                        "confirm": True,
                        "change_reason": "Reject malformed root",
                    },
                )
            )

        self.assertEqual(
            (False, "error", "INVALID_PLAN_SCHEMA"),
            (result["success"], result["severity"], result["code"]),
        )
        mock_orchestrator.patch_apply.assert_not_called()

    def test_empty_change_reason_becomes_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True}
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": '{"plan_version": "2"}',
                        "change_reason": "",
                    },
                )
            )

        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertIsNone(call_kwargs["change_reason"])

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": '{"plan_version": "2"}',
                        "confirm": True,
                        "change_reason": "",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.patch_apply.assert_not_called()


class TestPatchTransactionAuditPreflight(unittest.TestCase):
    _ELIGIBLE_PLAN = {
        "plan_version": "2",
        "resources": [
            {
                "id": "target",
                "kind": "prefab",
                "path": "Assets/Target.prefab",
                "mode": "open",
            }
        ],
        "ops": [
            {
                "resource": "target",
                "op": "find_game_object",
                "symbol_path": "Cube",
                "result": "existing",
            }
        ],
    }

    def _call_actual_orchestrator(
        self,
        plan: dict[str, Any],
        *,
        out_report: str | None = None,
    ) -> tuple[dict[str, Any], bytes, bytes]:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            target = project_root / "Assets" / "Target.prefab"
            target.parent.mkdir()
            target.write_text(_simple_prefab(), encoding="utf-8")
            before = target.read_bytes()
            orchestrator = Phase1Orchestrator.default(project_root)
            server = create_server()
            with patch.object(
                ProjectSession,
                "get_orchestrator",
                return_value=orchestrator,
            ):
                _, result = _run(
                    server.call_tool(
                        "patch_apply",
                        {
                            "plan": plan,
                            "confirm": True,
                            "change_reason": "Validate before report admission",
                            "out_report": out_report,
                        },
                    )
                )
            after = target.read_bytes()
        return result, before, after

    def _call_public_transaction(
        self,
        apply_response: ToolResponse,
    ) -> tuple[dict[str, Any], str]:
        from prefab_sentinel.orchestrator import Phase1Orchestrator
        from prefab_sentinel.services.serialized_object.resource_bridge_invoke import (
            parse_bridge_response,
        )

        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            target = project_root / "Assets" / "Target.prefab"
            target.parent.mkdir()
            target.write_text(_simple_prefab(), encoding="utf-8")
            orchestrator = Phase1Orchestrator.default(project_root)
            validation_response = ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="VALIDATION_OK",
                message="validation passed",
                data={},
                diagnostics=[],
            )
            bridge_apply_response = parse_bridge_response(
                {
                    "protocol_version": 2,
                    **apply_response.to_dict(),
                },
                target_path=target,
                ops=[{"op": "find_game_object"}],
            )

            def apply_once(
                *,
                resource: dict[str, Any],
                ops: list[dict[str, Any]],
            ) -> ToolResponse:
                self.assertEqual("Assets/Target.prefab", resource["path"])
                self.assertEqual("find_game_object", ops[0]["op"])
                target.write_bytes(b"after")
                return bridge_apply_response

            server = create_server()
            with (
                patch.object(
                    orchestrator.serialized_object,
                    "dry_run_resource_plan",
                    return_value=validation_response,
                ),
                patch.object(
                    orchestrator.serialized_object,
                    "apply_resource_plan",
                    side_effect=apply_once,
                ),
                patch.object(
                    orchestrator.prefab_variant,
                    "list_overrides",
                    return_value=validation_response,
                ),
                patch.object(
                    Phase1Orchestrator,
                    "inspect_structure",
                    return_value=validation_response,
                ),
                patch.object(
                    Phase1Orchestrator,
                    "validate_refs",
                    return_value=validation_response,
                ),
                patch.object(
                    Phase1Orchestrator,
                    "maybe_auto_refresh",
                    return_value="true",
                ),
                patch.object(
                    ProjectSession,
                    "get_orchestrator",
                    return_value=orchestrator,
                ),
            ):
                _, result = _run(
                    server.call_tool(
                        "patch_apply",
                        {
                            "plan": self._ELIGIBLE_PLAN,
                            "confirm": True,
                            "change_reason": "Compose nested prefab",
                            "out_report": "transaction.json",
                        },
                    )
                )

            persisted = json.loads(
                (project_root / "transaction.json").read_text(encoding="utf-8")
            )
            transaction = result["data"]["transaction"]
            self.assertEqual(result, persisted)
            self.assertEqual(
                ("Assets/Target.prefab", "Assets/Target.prefab"),
                (
                    result["data"]["steps"][-1]["result"]["data"]["target"],
                    transaction["original_result"]["data"]["steps"][-1]["result"][
                        "data"
                    ]["target"],
                ),
                msg="public step targets must preserve the exact relative Prefab path",
            )
            self.assertEqual(str(target), bridge_apply_response.data["target"])
            return result, str(project_root)

    def test_confirmed_single_open_prefab_requires_report_before_apply(self) -> None:
        result, before, after = self._call_actual_orchestrator(self._ELIGIBLE_PLAN)

        diagnostics = result.get("diagnostics") or [{}]
        self.assertEqual(
            (False, "error", "OUT_REPORT_REQUIRED", "out_report", before),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                (diagnostics[0].get("data") or {}).get("location"),
                after,
            ),
            msg=f"valid transaction must require a report without mutating its target: {result!r}",
        )

    def test_embedded_null_report_path_uses_preflight_envelope(self) -> None:
        result, before, after = self._call_actual_orchestrator(
            self._ELIGIBLE_PLAN,
            out_report="\x00",
        )

        diagnostics = result.get("diagnostics") or [{}]
        self.assertEqual(
            (False, "error", "OUT_REPORT_WRITE_FAILED", "out_report", before),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                (diagnostics[0].get("data") or {}).get("location"),
                after,
            ),
            msg=f"invalid report path must fail before target mutation: {result!r}",
        )

    def test_embedded_null_target_returns_terminal_report_without_mutation(self) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        plan = json.loads(json.dumps(self._ELIGIBLE_PLAN))
        plan["resources"][0]["path"] = "Assets/embedded\x00target.prefab"

        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            target = project_root / "Assets" / "Target.prefab"
            target.parent.mkdir()
            target.write_text(_simple_prefab(), encoding="utf-8")
            before = target.read_bytes()
            report = project_root / "audit.json"
            orchestrator = Phase1Orchestrator.default(project_root)
            dry_run = ToolResponse(
                True,
                Severity.INFO,
                "DRY_RUN",
                "Dry-run complete.",
                {},
                [],
            )
            server = create_server()
            with (
                patch.object(
                    orchestrator.serialized_object,
                    "dry_run_resource_plan",
                    return_value=dry_run,
                ),
                patch.object(
                    orchestrator.serialized_object,
                    "apply_resource_plan",
                ) as apply_mock,
                patch.object(
                    ProjectSession,
                    "get_orchestrator",
                    return_value=orchestrator,
                ),
            ):
                _, result = _run(
                    server.call_tool(
                        "patch_apply",
                        {
                            "plan": plan,
                            "confirm": True,
                            "change_reason": "Reject invalid target",
                            "out_report": str(report),
                        },
                    )
                )
            after = target.read_bytes()
            persisted = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(
            (False, "error", "PATCH_APPLY_RESULT", "not_started", before, 0, result),
            (
                result["success"],
                result["severity"],
                result["code"],
                result["data"]["transaction"]["status"],
                after,
                apply_mock.call_count,
                persisted,
            ),
            msg=f"public transaction left an orphan report or mutated its target: {result!r}",
        )

    def test_ambiguous_address_precedes_missing_transaction_report(self) -> None:
        plan = {
            "plan_version": "2",
            "resources": [
                {
                    "id": "target",
                    "kind": "prefab",
                    "path": "Assets/Target.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "target",
                    "op": "find_game_object",
                    "symbol_path": "Cube",
                    "file_id": "100",
                    "result": "existing",
                }
            ],
        }

        result, before, after = self._call_actual_orchestrator(plan)

        diagnostics = result.get("diagnostics") or [{}]
        self.assertEqual(
            (False, "error", "INVALID_PLAN_SCHEMA", "ops[0].symbol_path", before),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                (diagnostics[0].get("data") or {}).get("location"),
                after,
            ),
            msg=f"ambiguous address must fail before report admission or mutation: {result!r}",
        )

    def test_unsupported_operation_field_precedes_missing_transaction_report(self) -> None:
        plan = {
            "plan_version": "2",
            "resources": [
                {
                    "id": "target",
                    "kind": "prefab",
                    "path": "Assets/Target.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {
                    "resource": "target",
                    "op": "find_game_object",
                    "symbol_path": "Cube",
                    "recursive": True,
                    "result": "existing",
                }
            ],
        }

        result, before, after = self._call_actual_orchestrator(plan)

        diagnostics = result.get("diagnostics") or [{}]
        self.assertEqual(
            (False, "error", "INVALID_PLAN_SCHEMA", "ops[0].recursive", before),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                (diagnostics[0].get("data") or {}).get("location"),
                after,
            ),
            msg=f"unsupported operation field must fail before report admission or mutation: {result!r}",
        )

    def test_open_prefab_schema_diagnostics_redact_caller_values(self) -> None:
        secrets = ("SECRET_OPERATION", "SECRET_FIELD", "SECRET_HANDLE")
        plan = {
            "plan_version": "2",
            "resources": [
                {
                    "id": "target",
                    "kind": "prefab",
                    "path": "Assets/Target.prefab",
                    "mode": "open",
                }
            ],
            "ops": [
                {"resource": "target", "op": secrets[0]},
                {
                    "resource": "target",
                    "op": "instantiate_prefab",
                    "prefab": "Assets/Source.prefab",
                    "parent": "$root",
                    "result": secrets[2],
                },
                {
                    "resource": "target",
                    "op": "instantiate_prefab",
                    "prefab": "Assets/Source.prefab",
                    "parent": "$root",
                    "result": secrets[2],
                },
                {
                    "resource": "target",
                    "op": "rename_object",
                    "target": "$root",
                    "name": "Nested",
                    secrets[1]: True,
                },
            ],
        }

        result, before, after = self._call_actual_orchestrator(plan)

        diagnostics = result.get("diagnostics") or []
        self.assertEqual(
            (
                False,
                "error",
                "INVALID_PLAN_SCHEMA",
                ["schema_error", "schema_error", "schema_error"],
                [
                    "Open Prefab operation schema is invalid.",
                    "Open Prefab operation schema is invalid.",
                    "Open Prefab operation schema is invalid.",
                ],
                ["ops[0].op", "ops[2].result", "ops[3].SECRET_FIELD"],
                before,
            ),
            (
                result.get("success"),
                result.get("severity"),
                result.get("code"),
                [diagnostic.get("code") for diagnostic in diagnostics],
                [diagnostic.get("message") for diagnostic in diagnostics],
                [(diagnostic.get("data") or {}).get("location") for diagnostic in diagnostics],
                after,
            ),
            msg=f"open-Prefab schema diagnostics leaked or drifted: {result!r}",
        )
        public_text = json.dumps(
            {
                "message": result.get("message"),
                "diagnostic_messages": [diagnostic.get("message") for diagnostic in diagnostics],
            },
            sort_keys=True,
        )
        self.assertEqual(
            {secret: False for secret in secrets},
            {secret: secret in public_text for secret in secrets},
            msg=f"caller-controlled operation values escaped in public diagnostics: {result!r}",
        )

    def test_finalized_transaction_response_is_not_augmented_after_report(self) -> None:
        finalized = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply completed; transaction committed.",
            "data": {
                "transaction": {
                    "status": "committed",
                    "report_written": True,
                }
            },
            "diagnostics": [],
        }
        mock_resp = MagicMock()
        mock_resp.success = True
        mock_resp.to_dict.return_value = finalized
        mock_orch = MagicMock()
        mock_orch.patch_apply.return_value = mock_resp
        mock_orch.maybe_auto_refresh.return_value = {"refreshed": True}
        server = create_server()

        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "patch_apply",
                    {
                        "plan": self._ELIGIBLE_PLAN,
                        "confirm": True,
                        "change_reason": "Compose nested prefab",
                        "out_report": "transaction.json",
                    },
                )
            )

        mock_orch.patch_apply.assert_called_once_with(
            plan=self._ELIGIBLE_PLAN,
            dry_run=False,
            confirm=True,
            plan_sha256=None,
            plan_signature=None,
            change_reason="Compose nested prefab",
            out_report="transaction.json",
            scope=None,
            runtime_scene=None,
            runtime_profile="default",
            runtime_log_file=None,
            runtime_since_timestamp=None,
            runtime_allow_warnings=False,
            runtime_max_diagnostics=200,
            transactional=True,
        )
        self.assertEqual(
            finalized,
            result,
            msg=f"transaction response changed after finalization: {result!r}",
        )
        mock_orch.maybe_auto_refresh.assert_not_called()

    def test_committed_transaction_projects_public_paths(self) -> None:
        apply_response = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="APPLY_OK",
            message="apply completed",
            data={
                "target": "Assets/Bridge.prefab",
                "op_count": 99,
                "applied": 1,
                "read_only": False,
                "executed": True,
                "protocol_version": 999,
                "created_results": [],
            },
            diagnostics=[],
        )

        result, project_root = self._call_public_transaction(apply_response)

        transaction = result["data"]["transaction"]
        self.assertEqual(
            (True, "committed", "transaction.json"),
            (
                result["success"],
                transaction["status"],
                transaction["out_report"],
            ),
            msg=f"public committed paths must be project-relative: {result!r}",
        )
        self.assertNotIn(
            project_root,
            json.dumps(result),
            msg=f"public committed response exposed the project root: {result!r}",
        )

    def test_bridge_target_metadata_cannot_override_requested_target(self) -> None:
        outside_target = "/outside/Target.prefab"
        apply_response = ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="APPLY_OK",
            message="apply completed",
            data={
                "applied": 1,
                "read_only": False,
                "executed": True,
                "target": outside_target,
                "op_count": 99,
                "protocol_version": 999,
                "created_results": [],
            },
            diagnostics=[],
        )

        result, _ = self._call_public_transaction(apply_response)

        transaction = result["data"]["transaction"]
        self.assertEqual(
            (True, "committed", "Assets/Target.prefab"),
            (
                result["success"],
                transaction["status"],
                result["data"]["steps"][-1]["result"]["data"]["target"],
            ),
            msg=f"Bridge target metadata overrode the requested transaction target: {result!r}",
        )
        self.assertNotIn(outside_target, json.dumps(result))

    def test_rolled_back_transaction_projects_public_paths(self) -> None:
        apply_response = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="APPLY_FAILED",
            message="apply failed",
            data={
                "target": "Assets/Bridge.prefab",
                "op_count": 1,
                "applied": 0,
                "read_only": False,
                "executed": False,
                "protocol_version": 2,
                "created_results": [],
            },
            diagnostics=[],
        )

        result, project_root = self._call_public_transaction(apply_response)

        transaction = result["data"]["transaction"]
        self.assertEqual(
            (
                False,
                "rolled_back",
                "transaction.json",
                "Assets/Target.prefab",
            ),
            (
                result["success"],
                transaction["status"],
                transaction["out_report"],
                transaction["rollback_result"]["data"]["target"],
            ),
            msg=f"public rollback paths must be project-relative: {result!r}",
        )
        self.assertNotIn(
            project_root,
            json.dumps(result),
            msg=f"public rollback response exposed the project root: {result!r}",
        )

    _SECRET_BRIDGE_COMMAND = "/secret/private-bridge.py"

    def _call_public_bridge_failure(
        self,
        *,
        completed: subprocess.CompletedProcess[str] | None,
        raised: BaseException | None,
    ) -> tuple[dict[str, Any], dict[str, Any], MagicMock]:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            target = project_root / "Assets" / "Target.prefab"
            target.parent.mkdir()
            target.write_text(_simple_prefab(), encoding="utf-8")
            report = project_root / "transaction.json"
            mocked_run = MagicMock(return_value=completed, side_effect=raised)
            with (
                patch.dict(
                    os.environ,
                    {"UNITYTOOL_PATCH_BRIDGE": (f"{sys.executable} {self._SECRET_BRIDGE_COMMAND}")},
                    clear=False,
                ),
                patch(
                    "prefab_sentinel.services.serialized_object.resource_bridge_invoke.subprocess.run",
                    mocked_run,
                ),
            ):
                orchestrator = Phase1Orchestrator.default(project_root)
                server = create_server()
                with (
                    patch.object(
                        Phase1Orchestrator,
                        "maybe_auto_refresh",
                        return_value="true",
                    ),
                    patch.object(
                        ProjectSession,
                        "get_orchestrator",
                        return_value=orchestrator,
                    ),
                ):
                    _, result = _run(
                        server.call_tool(
                            "patch_apply",
                            {
                                "plan": self._ELIGIBLE_PLAN,
                                "confirm": True,
                                "change_reason": "Exercise public bridge failure",
                                "out_report": str(report),
                            },
                        )
                    )
            persisted = json.loads(report.read_text(encoding="utf-8"))
        return result, persisted, mocked_run

    def _assert_public_bridge_failure(
        self,
        *,
        result: dict[str, Any],
        persisted: dict[str, Any],
        mocked_run: MagicMock,
        expected_code: str,
        expected_message: str,
        secrets: tuple[str, ...],
    ) -> None:
        transaction = result["data"]["transaction"]
        bridge_result = transaction["original_result"]["data"]["steps"][-1]["result"]
        self.assertEqual(
            (
                "rolled_back",
                False,
                expected_code,
                expected_message,
                {
                    "op_count": 1,
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                },
            ),
            (
                transaction["status"],
                bridge_result["success"],
                bridge_result["code"],
                bridge_result["message"],
                bridge_result["data"],
            ),
            msg=f"public bridge failure projection mismatch: {result!r}",
        )
        self.assertEqual(
            result,
            persisted,
            msg="transaction report must equal the authoritative public response",
        )
        serialized = json.dumps(result, sort_keys=True)
        self.assertEqual(
            {secret: False for secret in secrets},
            {secret: secret in serialized for secret in secrets},
            msg=f"public transaction disclosed subprocess details: {serialized}",
        )
        mocked_run.assert_called_once()

    def test_patch_apply_redacts_bridge_spawn_failure(self) -> None:
        secret = "/secret/spawn-failure"
        result, persisted, mocked_run = self._call_public_bridge_failure(
            completed=None,
            raised=OSError(secret),
        )

        self._assert_public_bridge_failure(
            result=result,
            persisted=persisted,
            mocked_run=mocked_run,
            expected_code="SER_BRIDGE_EXEC",
            expected_message="Failed to start Unity bridge process.",
            secrets=(self._SECRET_BRIDGE_COMMAND, secret),
        )

    def test_patch_apply_redacts_bridge_timeout_failure(self) -> None:
        secret = "/secret/timeout-output"
        result, persisted, mocked_run = self._call_public_bridge_failure(
            completed=None,
            raised=subprocess.TimeoutExpired(
                cmd=[self._SECRET_BRIDGE_COMMAND],
                timeout=120,
                output=secret,
                stderr=secret,
            ),
        )

        self._assert_public_bridge_failure(
            result=result,
            persisted=persisted,
            mocked_run=mocked_run,
            expected_code="SER_BRIDGE_TIMEOUT",
            expected_message="Unity bridge process timed out.",
            secrets=(self._SECRET_BRIDGE_COMMAND, secret),
        )

    def test_patch_apply_redacts_bridge_nonzero_output(self) -> None:
        secret = "/secret/nonzero-output"
        result, persisted, mocked_run = self._call_public_bridge_failure(
            completed=subprocess.CompletedProcess(
                args=[self._SECRET_BRIDGE_COMMAND],
                returncode=9,
                stdout=secret,
                stderr=secret,
            ),
            raised=None,
        )

        self._assert_public_bridge_failure(
            result=result,
            persisted=persisted,
            mocked_run=mocked_run,
            expected_code="SER_BRIDGE_FAILED",
            expected_message="Unity bridge process returned non-zero exit code.",
            secrets=(self._SECRET_BRIDGE_COMMAND, secret),
        )

    def test_patch_apply_redacts_malformed_bridge_output(self) -> None:
        secret = "/secret/malformed-output"
        result, persisted, mocked_run = self._call_public_bridge_failure(
            completed=subprocess.CompletedProcess(
                args=[self._SECRET_BRIDGE_COMMAND],
                returncode=0,
                stdout=f"not-json-{secret}",
                stderr=secret,
            ),
            raised=None,
        )

        self._assert_public_bridge_failure(
            result=result,
            persisted=persisted,
            mocked_run=mocked_run,
            expected_code="SER_BRIDGE_PROTOCOL",
            expected_message="Unity bridge output must be valid JSON.",
            secrets=(self._SECRET_BRIDGE_COMMAND, secret),
        )

    def test_patch_apply_redacts_invalid_utf8_bridge_output(self) -> None:
        decode_error = UnicodeDecodeError(
            "utf-8",
            bytes([255]),
            0,
            1,
            "invalid start byte",
        )
        result, persisted, mocked_run = self._call_public_bridge_failure(
            completed=None,
            raised=decode_error,
        )

        self._assert_public_bridge_failure(
            result=result,
            persisted=persisted,
            mocked_run=mocked_run,
            expected_code="SER_BRIDGE_PROTOCOL",
            expected_message="Unity bridge output must be valid JSON.",
            secrets=(self._SECRET_BRIDGE_COMMAND,),
        )

    def test_patch_apply_non_transaction_redacts_invalid_utf8_bridge_output(
        self,
    ) -> None:
        from prefab_sentinel.orchestrator import Phase1Orchestrator

        decode_error = UnicodeDecodeError(
            "utf-8",
            bytes([255]),
            0,
            1,
            "invalid start byte",
        )
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            target = project_root / "Assets" / "Target.mat"
            target.parent.mkdir()
            target.write_text("%YAML 1.1\n", encoding="utf-8")
            mocked_run = MagicMock(side_effect=decode_error)
            with (
                patch.dict(
                    os.environ,
                    {"UNITYTOOL_PATCH_BRIDGE": (f"{sys.executable} {self._SECRET_BRIDGE_COMMAND}")},
                    clear=False,
                ),
                patch(
                    "prefab_sentinel.services.serialized_object.resource_bridge_invoke.subprocess.run",
                    mocked_run,
                ),
            ):
                orchestrator = Phase1Orchestrator.default(project_root)
                server = create_server()
                with patch.object(
                    ProjectSession,
                    "get_orchestrator",
                    return_value=orchestrator,
                ):
                    _, result = _run(
                        server.call_tool(
                            "patch_apply",
                            {
                                "plan": {
                                    "plan_version": "2",
                                    "resources": [
                                        {
                                            "id": "material",
                                            "kind": "material",
                                            "path": "Assets/Target.mat",
                                            "mode": "open",
                                        }
                                    ],
                                    "ops": [
                                        {
                                            "op": "set",
                                            "resource": "material",
                                            "target": "$asset",
                                            "path": "m_Name",
                                            "value": "Updated",
                                        }
                                    ],
                                },
                                "confirm": True,
                                "change_reason": "Exercise non-transaction decode failure",
                            },
                        )
                    )

        bridge_result = result["data"]["steps"][-1]["result"]
        self.assertEqual(
            (
                False,
                "PATCH_APPLY_RESULT",
                False,
                "SER_BRIDGE_PROTOCOL",
                "Unity bridge output must be valid JSON.",
                {
                    "op_count": 1,
                    "applied": 0,
                    "read_only": False,
                    "executed": False,
                },
                False,
                False,
            ),
            (
                result["success"],
                result["code"],
                bridge_result["success"],
                bridge_result["code"],
                bridge_result["message"],
                bridge_result["data"],
                "transaction" in result["data"],
                "auto_refresh" in result,
            ),
            msg=f"public non-transaction decode failure escaped safe projection: {result!r}",
        )
        self.assertNotIn(self._SECRET_BRIDGE_COMMAND, json.dumps(result, sort_keys=True))
        mocked_run.assert_called_once()

    def test_patch_apply_redacts_structurally_invalid_bridge_responses(
        self,
    ) -> None:
        protocol_secret = "/secret/bridge-protocol-value"
        output_secret = "/secret/bridge-output"
        nested_target_secret = "/outside/nested.prefab"
        cases: tuple[tuple[object, str, str], ...] = (
            ([], "SER_BRIDGE_PROTOCOL", "Unity bridge response must be a JSON object."),
            (None, "SER_BRIDGE_PROTOCOL", "Unity bridge response must be a JSON object."),
            (
                "scalar",
                "SER_BRIDGE_PROTOCOL",
                "Unity bridge response must be a JSON object.",
            ),
            (
                {"protocol_version": protocol_secret},
                "SER_BRIDGE_PROTOCOL_VERSION",
                "Unity bridge protocol version mismatch.",
            ),
            (
                {
                    "protocol_version": 2,
                    "success": True,
                    "severity": "info",
                    "code": "SER_APPLY_OK",
                    "message": "Bridge apply completed.",
                    "data": {
                        "applied": 1,
                        "steps": [
                            {
                                "step": "nested",
                                "result": {
                                    "data": {"target": nested_target_secret},
                                },
                            }
                        ],
                    },
                    "diagnostics": [],
                },
                "SER_BRIDGE_PROTOCOL",
                "Unity bridge response schema is invalid.",
            ),
            (
                {
                    "protocol_version": 2,
                    "success": True,
                    "severity": "info",
                    "code": "SER_APPLY_OK",
                    "message": "Bridge apply completed.",
                    "data": {
                        "applied": 1,
                        "metadata": {"target": nested_target_secret},
                    },
                    "diagnostics": [],
                },
                "SER_BRIDGE_PROTOCOL",
                "Unity bridge response schema is invalid.",
            ),
            (
                {
                    "protocol_version": 2,
                    "success": False,
                    "severity": "error",
                    "code": "SER_APPLY_REJECTED",
                    "message": "Bridge apply rejected.",
                    "data": {
                        "applied": 0,
                        "read_only": True,
                        "executed": False,
                    },
                    "diagnostics": [],
                },
                "SER_BRIDGE_PROTOCOL",
                "Unity bridge response schema is invalid.",
            ),
            (
                {
                    "protocol_version": 2,
                    "success": True,
                    "severity": "info",
                    "code": "SER_APPLY_OK",
                    "message": "Bridge apply completed.",
                    "data": {
                        "applied": 1,
                        "read_only": False,
                        "executed": False,
                    },
                    "diagnostics": [],
                },
                "SER_BRIDGE_PROTOCOL",
                "Unity bridge response schema is invalid.",
            ),
        )
        for payload, expected_code, expected_message in cases:
            with self.subTest(payload=payload):
                result, persisted, mocked_run = self._call_public_bridge_failure(
                    completed=subprocess.CompletedProcess(
                        args=[self._SECRET_BRIDGE_COMMAND],
                        returncode=0,
                        stdout=json.dumps(payload),
                        stderr=output_secret,
                    ),
                    raised=None,
                )

                self._assert_public_bridge_failure(
                    result=result,
                    persisted=persisted,
                    mocked_run=mocked_run,
                    expected_code=expected_code,
                    expected_message=expected_message,
                    secrets=(
                        self._SECRET_BRIDGE_COMMAND,
                        output_secret,
                        protocol_secret,
                        nested_target_secret,
                    ),
                )


# ---------------------------------------------------------------------------
# activate_project batch scope
# ---------------------------------------------------------------------------


class TestActivateProjectBatchScope(unittest.TestCase):
    """activate_project exposes only the batch-scoped session payload."""

    @patch("prefab_sentinel.session_cache.collect_project_guid_index")
    @patch("prefab_sentinel.session_cache.build_script_name_map")
    @patch("prefab_sentinel.session_cache.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_response_omits_unscoped_knowledge_fields(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
        mock_guid: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {}
        mock_guid.return_value = {}
        server = create_server()
        _, result = _run(server.call_tool("activate_project", {"scope": "Assets/MyScope"}))

        self.assertEqual(True, result["success"], result)
        self.assertNotIn("suggested_reads", result["data"])
        self.assertNotIn("knowledge_hint", result["data"])
        self.assertEqual("/unity", result["data"]["expected_project_root"])
        self.assertEqual("/unity/Assets/MyScope", result["data"]["scope"])


# ---------------------------------------------------------------------------
# Knowledge MCP Resources
# ---------------------------------------------------------------------------


class TestKnowledgeResourcesNotRegisteredForBatch(unittest.TestCase):
    """The current issue batch does not publish knowledge MCP resources."""

    def test_create_server_registers_no_knowledge_resources(self) -> None:
        server = create_server()
        resources = _run(server.list_resources())

        knowledge_uris = [str(r.uri) for r in resources if "knowledge/" in str(r.uri)]
        self.assertEqual([], knowledge_uris)


class TestExpectedRootProviderLifespan(unittest.TestCase):
    """Session lifespan owns the bridge expected-root guard."""

    def _send_with_fake_response(
        self,
        response_payload: dict[str, object],
    ) -> tuple[dict[str, object], str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_dir = Path(tmpdir)
            seen_request_id: dict[str, str] = {}
            responder_errors: list[BaseException] = []

            import threading

            request_ready = threading.Condition()
            observed_request: dict[str, Path] = {}
            original_rename = Path.rename

            def notifying_rename(self_path: Path, target: str | Path) -> Path:
                renamed_path = original_rename(self_path, target)
                target_path = Path(target)
                if target_path.parent == watch_dir and target_path.name.endswith(".request.json"):
                    with request_ready:
                        observed_request["path"] = target_path
                        request_ready.notify_all()
                return renamed_path

            def fake_send() -> None:
                with request_ready:
                    request_seen = request_ready.wait_for(
                        lambda: "path" in observed_request,
                        timeout=2,
                    )
                if not request_seen:
                    responder_errors.append(AssertionError("Expected request file before fake Unity response"))
                    return

                request_file = observed_request["path"]
                request_id = request_file.name.removesuffix(".request.json")
                seen_request_id["value"] = request_id
                response = dict(response_payload)
                response.setdefault("protocol_version", PROTOCOL_VERSION)
                (watch_dir / f"{request_id}.response.json").write_text(
                    json.dumps(response),
                    encoding="utf-8",
                )

            with (
                patch.dict(os.environ, {BRIDGE_WATCH_DIR_ENV: tmpdir}, clear=False),
                patch.object(Path, "rename", notifying_rename),
            ):
                t = threading.Thread(target=fake_send)
                t.start()
                result = editor_bridge.send_action(
                    action="get_editor_state",
                    timeout_sec=5,
                )
                t.join()

        self.assertEqual([], responder_errors)
        return result, seen_request_id["value"]

    def _successful_editor_state(self, actual_root: str) -> dict[str, object]:
        return {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_STATE_OK",
            "message": "Editor state captured",
            "data": {},
            "diagnostics": [],
            "operator_context": {"project_root": actual_root},
        }

    def _clear_provider_for_failed_red_run(self) -> None:
        editor_bridge._set_expected_project_root_provider(None)

    def test_lifespan_session_root_guards_unannotated_bridge_action(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        actual_root = "/workspace/OtherProject"

        async def exercise() -> dict[str, object]:
            server = create_server(project_root=expected_root)
            async with server._mcp_server.lifespan(cast(Any, server)):
                result, _ = self._send_with_fake_response(self._successful_editor_state(actual_root))
            return result

        result = _run(exercise())

        self.assertEqual(False, result["success"], result)
        self.assertEqual("EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH", result["code"])
        self.assertEqual(expected_root, result["data"]["expected_project_root"])
        self.assertEqual(actual_root, result["data"]["actual_project_root"])

    def test_lifespan_clears_expected_root_before_shutdown(self) -> None:
        expected_root = "/workspace/ExpectedProject"
        actual_root = "/workspace/OtherProject"

        async def fail_shutdown(_session: ProjectSession) -> None:
            raise RuntimeError("shutdown failed")

        async def exercise_failed_shutdown() -> None:
            server = create_server(project_root=expected_root)
            with patch("prefab_sentinel.session.ProjectSession.shutdown", fail_shutdown):
                with self.assertRaisesRegex(RuntimeError, "shutdown failed"):
                    async with server._mcp_server.lifespan(cast(Any, server)):
                        pass

        _run(exercise_failed_shutdown())
        try:
            result, _ = self._send_with_fake_response(self._successful_editor_state(actual_root))
            self.assertEqual(True, result["success"], result)
            self.assertEqual("EDITOR_CTRL_STATE_OK", result["code"])
        finally:
            self._clear_provider_for_failed_red_run()


# ---------------------------------------------------------------------------
# deploy_bridge cleanup and unconditional deploy
# ---------------------------------------------------------------------------


class TestDeployBridgeCleanup(unittest.TestCase):
    """deploy_bridge old file cleanup and unconditional deploy."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self._project_root = self._tmp / "UnityProject"
        self._project_root.mkdir()
        self._target = self._project_root / "Assets" / "Editor" / "PrefabSentinel"
        self._target.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    @staticmethod
    def _mock_successful_refresh(mock_send: MagicMock) -> None:
        mock_send.return_value = {"success": True}

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_removes_old_files_from_parent(self, mock_send: MagicMock) -> None:
        """Old PrefabSentinel.*.cs in parent dir are removed before deploy."""
        self._mock_successful_refresh(mock_send)
        parent = self._target.parent
        old_cs = parent / "PrefabSentinel.EditorBridge.cs"
        old_meta = parent / "PrefabSentinel.EditorBridge.cs.meta"
        old_cs.write_text("// old", encoding="utf-8")
        old_meta.write_text("guid: abc", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.EditorBridge.cs", result["data"]["removed_old_files"])
        self.assertIn("PrefabSentinel.EditorBridge.cs.meta", result["data"]["removed_old_files"])
        self.assertFalse(old_cs.exists())
        self.assertFalse(old_meta.exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_no_old_files_no_removal(self, mock_send: MagicMock) -> None:
        """When parent has no old files, removed_old_files is empty."""
        self._mock_successful_refresh(mock_send)
        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_old_files"], [])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_first_deploy_no_old_files(self, mock_send: MagicMock) -> None:
        """First deploy to a new path has no old files to clean up."""
        self._mock_successful_refresh(mock_send)
        deep_target = self._project_root / "Assets" / "NewDir" / "SubDir" / "Bridge"
        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(deep_target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_old_files"], [])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_upload_handler_always_deployed(self, mock_send: MagicMock) -> None:
        """VRCSDKUploadHandler.cs is always copied unconditionally."""
        self._mock_successful_refresh(mock_send)
        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.VRCSDKUploadHandler.cs", result["data"]["copied_files"])
        self.assertTrue((self._target / "PrefabSentinel.VRCSDKUploadHandler.cs").exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_asmdef_deployed(self, mock_send: MagicMock) -> None:
        """PrefabSentinel.Editor.asmdef is copied alongside C# files."""
        self._mock_successful_refresh(mock_send)
        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.Editor.asmdef", result["data"]["copied_files"])
        self.assertTrue((self._target / "PrefabSentinel.Editor.asmdef").exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_no_skipped_files_in_response(self, mock_send: MagicMock) -> None:
        """Response data must not contain skipped_files key."""
        self._mock_successful_refresh(mock_send)
        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertNotIn("skipped_files", result["data"])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_diagnostics_warn_on_old_file_removal(self, mock_send: MagicMock) -> None:
        """Diagnostics include warning when old files are removed."""
        self._mock_successful_refresh(mock_send)
        parent = self._target.parent
        (parent / "PrefabSentinel.EditorBridge.cs").write_text("// old", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        warnings = [d for d in result["diagnostics"] if d["severity"] == "warning"]
        self.assertTrue(any("old Bridge" in d["message"] for d in warnings))

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_cleanup_warning_sets_success_envelope_severity(
        self,
        mock_send: MagicMock,
    ) -> None:
        self._mock_successful_refresh(mock_send)
        parent = self._target.parent
        (parent / "PrefabSentinel.Legacy.cs").write_text("// old", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual("warning", result["severity"])
        warnings = [d for d in result["diagnostics"] if d["severity"] == "warning"]
        self.assertTrue(any(d["code"] == "DEPLOY_REMOVED_OLD_BRIDGE_FILES" for d in warnings))

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_clean_redeploy_removes_all_target_files(self, mock_send: MagicMock) -> None:
        """All pre-existing files in target_dir are removed before deploy."""
        self._mock_successful_refresh(mock_send)
        (self._target / "Dummy.cs").write_text("// dummy", encoding="utf-8")
        (self._target / "Dummy.cs.meta").write_text("guid: dummy", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertFalse((self._target / "Dummy.cs").exists())
        self.assertFalse((self._target / "Dummy.cs.meta").exists())

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_clean_redeploy_preserves_subdirectories(self, mock_send: MagicMock) -> None:
        """Subdirectories inside target_dir survive the clean phase."""
        self._mock_successful_refresh(mock_send)
        subdir = self._target / "subdir"
        subdir.mkdir()
        (subdir / "keep.txt").write_text("keep", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertTrue(subdir.is_dir())
        self.assertTrue((subdir / "keep.txt").exists())
        self.assertIsInstance(result["data"]["removed_stale_files"], list)

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_removed_stale_files_in_response(self, mock_send: MagicMock) -> None:
        """Stale files removed during clean phase appear in response data."""
        self._mock_successful_refresh(mock_send)
        (self._target / "OldFile.cs").write_text("// old", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertIn("OldFile.cs", result["data"]["removed_stale_files"])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_clean_redeploy_diagnostic_message(self, mock_send: MagicMock) -> None:
        """Clearing files produces an info diagnostic with 'Cleared' message."""
        self._mock_successful_refresh(mock_send)
        (self._target / "Stale.cs").write_text("// stale", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        infos = [d for d in result["diagnostics"] if d["severity"] == "info"]
        self.assertTrue(any("Cleared" in d["message"] for d in infos))

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_first_deploy_empty_removed_stale(self, mock_send: MagicMock) -> None:
        """First deploy to empty target_dir has empty removed_stale_files."""
        self._mock_successful_refresh(mock_send)
        fresh_target = self._project_root / "Assets" / "Editor" / "FreshDeploy"
        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(fresh_target)},
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_stale_files"], [])

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_returns_refresh_failure_envelope(self, mock_send: MagicMock) -> None:
        """A failed post-copy refresh is the deploy result."""
        refresh_failure = {
            "success": False,
            "severity": "error",
            "code": "EDITOR_BRIDGE_PROJECT_ROOT_MISMATCH",
            "message": "expected root did not match reached Unity project",
            "data": {"action": "refresh_asset_database"},
            "diagnostics": [],
        }
        mock_send.return_value = refresh_failure

        server = create_server(project_root=str(self._project_root))
        _, result = _run(
            server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            )
        )

        mock_send.assert_called_once_with(action="refresh_asset_database")
        self.assertEqual(refresh_failure, result)

    @patch("prefab_sentinel.mcp_tools_session.send_action")
    def test_uses_bridge_files_dir_when_available(self, mock_send: MagicMock) -> None:
        """When _bridge_files/ exists (wheel install), uses it over tools/unity/."""
        self._mock_successful_refresh(mock_send)
        # Create _bridge_files in a temp dir and patch __file__ to point there
        fake_pkg = self._tmp / "fake_pkg" / "prefab_sentinel"
        fake_pkg.mkdir(parents=True)
        bridge_dir = fake_pkg / "_bridge_files"
        bridge_dir.mkdir()
        test_cs = bridge_dir / "PrefabSentinel.TestBridge.cs"
        test_cs.write_text("// from _bridge_files", encoding="utf-8")

        import prefab_sentinel.mcp_tools_session as mcp_mod

        original_file = mcp_mod.__file__
        mcp_mod.__file__ = str(fake_pkg / "mcp_tools_session.py")
        try:
            server = create_server(project_root=str(self._project_root))
            _, result = _run(
                server.call_tool(
                    "deploy_bridge",
                    {"target_dir": str(self._target)},
                )
            )
        finally:
            mcp_mod.__file__ = original_file

        self.assertTrue(result["success"])
        # Should have copied from _bridge_files, not tools/unity/
        self.assertIn("PrefabSentinel.TestBridge.cs", result["data"]["copied_files"])
        # Should NOT contain files from tools/unity/
        self.assertNotIn("PrefabSentinel.EditorBridge.cs", result["data"]["copied_files"])


# ---------------------------------------------------------------------------


class TestCopyAssetTool(unittest.TestCase):
    """Tests for the copy_asset MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"m_name_after": "copied"}}
        mock_orch = MagicMock()
        mock_orch.copy_asset.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "copy_asset",
                    {
                        "source_path": "Assets/Mat/A.mat",
                        "dest_path": "Assets/Mat/B.mat",
                        "confirm": True,
                        "change_reason": "duplicate material",
                    },
                )
            )

        self.assertTrue(result["success"])
        mock_orch.copy_asset.assert_called_once_with(
            source_path="Assets/Mat/A.mat",
            dest_path="Assets/Mat/B.mat",
            dry_run=False,
            change_reason="duplicate material",
        )

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "copy_asset",
                    {
                        "source_path": "Assets/Mat/A.mat",
                        "dest_path": "Assets/Mat/B.mat",
                        "confirm": True,
                        "change_reason": "",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.copy_asset.assert_not_called()


class TestRenameAssetTool(unittest.TestCase):
    """Tests for the rename_asset MCP tool."""

    def test_delegates_to_orchestrator(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"m_name_after": "renamed"}}
        mock_orch = MagicMock()
        mock_orch.rename_asset.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "rename_asset",
                    {
                        "asset_path": "Assets/Mat/Old.mat",
                        "new_name": "New.mat",
                        "confirm": True,
                        "change_reason": "rename for clarity",
                    },
                )
            )

        self.assertTrue(result["success"])
        mock_orch.rename_asset.assert_called_once_with(
            asset_path="Assets/Mat/Old.mat",
            new_name="New.mat",
            dry_run=False,
            change_reason="rename for clarity",
        )

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "rename_asset",
                    {
                        "asset_path": "Assets/Mat/Old.mat",
                        "new_name": "New.mat",
                        "confirm": True,
                        "change_reason": "",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.rename_asset.assert_not_called()


class PatchAssetDeleteToolTests(unittest.TestCase):
    """Tests for delete_asset and delete_assets MCP tools."""

    def test_delete_asset_delegates_single_path_as_batch(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"targets": []}}
        mock_orch = MagicMock()
        mock_orch.delete_assets.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "delete_asset",
                    {"asset_path": "Assets/Foo.prefab"},
                )
            )

        self.assertTrue(result["success"])
        mock_orch.delete_assets.assert_called_once_with(
            ["Assets/Foo.prefab"],
            scope=None,
            dry_run=True,
            confirm=False,
            change_reason=None,
        )

    def test_delete_assets_defaults_to_dry_run(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"targets": []}}
        mock_orch = MagicMock()
        mock_orch.delete_assets.return_value = mock_resp

        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "delete_assets",
                    {"asset_paths": ["Assets/Foo.prefab"]},
                )
            )

        self.assertTrue(result["success"])
        mock_orch.delete_assets.assert_called_once_with(
            ["Assets/Foo.prefab"],
            scope=None,
            dry_run=True,
            confirm=False,
            change_reason=None,
        )

    def test_delete_assets_confirmed_apply_passes_resolved_scope_and_reason(self) -> None:
        mock_resp = MagicMock()
        mock_resp.to_dict.return_value = {"success": True, "data": {"deleted_paths": []}}
        mock_orch = MagicMock()
        mock_orch.delete_assets.return_value = mock_resp

        server = create_server()
        with (
            patch.object(ProjectSession, "get_orchestrator", return_value=mock_orch),
            patch.object(ProjectSession, "resolve_scope", return_value="Assets/Resolved"),
        ):
            _, result = _run(
                server.call_tool(
                    "delete_assets",
                    {
                        "asset_paths": ["Assets/Foo.prefab"],
                        "scope": "feature",
                        "dry_run": False,
                        "confirm": True,
                        "change_reason": "remove obsolete asset",
                    },
                )
            )

        self.assertTrue(result["success"])
        mock_orch.delete_assets.assert_called_once_with(
            ["Assets/Foo.prefab"],
            scope="Assets/Resolved",
            dry_run=False,
            confirm=True,
            change_reason="remove obsolete asset",
        )

    def test_delete_assets_confirmed_apply_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "delete_assets",
                    {
                        "asset_paths": ["Assets/Foo.prefab"],
                        "dry_run": False,
                        "confirm": True,
                        "change_reason": "",
                    },
                )
            )

        self.assertEqual(
            (False, "CHANGE_REASON_REQUIRED"),
            (result["success"], result["code"]),
        )
        mock_orch.delete_assets.assert_not_called()


class TestSetMaterialPropertyTool(unittest.TestCase):
    """Tests for the set_material_property MCP tool."""

    def test_confirm_requires_change_reason(self) -> None:
        mock_orch = MagicMock()
        server = create_server()
        with patch.object(
            ProjectSession,
            "get_orchestrator",
            return_value=mock_orch,
        ):
            _, result = _run(
                server.call_tool(
                    "set_material_property",
                    {
                        "asset_path": "Assets/M.mat",
                        "property_name": "_Color",
                        "value": "0.5",
                        "confirm": True,
                        "change_reason": "",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])
        mock_orch.set_material_property.assert_not_called()


class TestCopyComponentFieldsTool(unittest.TestCase):
    """Test the copy_component_fields MCP tool."""

    def _meshrenderer_prefab(self, go_name: str = "Cube") -> str:
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", go_name, ["200", "300"]),
                make_transform("200", "100"),
                (
                    "--- !u!23 &300\n"
                    "MeshRenderer:\n"
                    "  m_ObjectHideFlags: 0\n"
                    "  m_CorrespondingSourceObject: {fileID: 0}\n"
                    "  m_PrefabInstance: {fileID: 0}\n"
                    "  m_PrefabAsset: {fileID: 0}\n"
                    "  m_GameObject: {fileID: 100}\n"
                    "  m_Enabled: 1\n"
                    "  m_CastShadows: 1\n"
                    "  m_ReceiveShadows: 1\n"
                    "  m_Materials:\n"
                    "  - {fileID: 2100000, guid: aaa, type: 2}\n"
                ),
            ]
        )

    def _two_meshrenderer_prefab(self) -> str:
        """Prefab with two GOs each having a MeshRenderer."""
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Parent", ["200", "300"]),
                make_transform("200", "100", children_file_ids=["500"]),
                (
                    "--- !u!23 &300\n"
                    "MeshRenderer:\n"
                    "  m_ObjectHideFlags: 0\n"
                    "  m_GameObject: {fileID: 100}\n"
                    "  m_Enabled: 1\n"
                    "  m_CastShadows: 1\n"
                ),
                make_gameobject("400", "Child", ["500", "600"]),
                make_transform("500", "400", father_file_id="200"),
                (
                    "--- !u!23 &600\n"
                    "MeshRenderer:\n"
                    "  m_ObjectHideFlags: 0\n"
                    "  m_GameObject: {fileID: 400}\n"
                    "  m_Enabled: 0\n"
                    "  m_CastShadows: 0\n"
                ),
            ]
        )

    def _monobehaviour_prefab(
        self,
        guid: str = "aaaa1111bbbb2222cccc3333dddd4444",
    ) -> str:
        return _make_simple_monobehaviour_prefab(guid)

    def _system_fields_only_prefab(self) -> str:
        """Prefab where MeshRenderer has ONLY system fields."""
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Empty", ["200", "300"]),
                make_transform("200", "100"),
                (
                    "--- !u!23 &300\n"
                    "MeshRenderer:\n"
                    "  m_ObjectHideFlags: 0\n"
                    "  m_CorrespondingSourceObject: {fileID: 0}\n"
                    "  m_PrefabInstance: {fileID: 0}\n"
                    "  m_PrefabAsset: {fileID: 0}\n"
                    "  m_GameObject: {fileID: 100}\n"
                    "  m_EditorHideFlags: 0\n"
                    "  m_Script: {fileID: 0}\n"
                    "  m_EditorClassIdentifier:\n"
                ),
            ]
        )

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed." if dry_run else "patch.apply completed.",
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_copy_all_fields_dry_run(self) -> None:
        """Copy all user fields with dry_run=True produces set ops."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "copy_component_fields",
                        {
                            "src_asset_path": str(src),
                            "src_symbol_path": "Src/MeshRenderer",
                            "dst_asset_path": str(dst),
                            "dst_symbol_path": "Dst/MeshRenderer",
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertIn("m_Enabled", op_paths)
        self.assertIn("m_CastShadows", op_paths)
        self.assertIn("m_ReceiveShadows", op_paths)
        # System fields must NOT be in ops
        for op in plan["ops"]:
            self.assertNotIn(
                op["path"],
                {
                    "m_ObjectHideFlags",
                    "m_CorrespondingSourceObject",
                    "m_PrefabInstance",
                    "m_PrefabAsset",
                    "m_GameObject",
                    "m_EditorHideFlags",
                    "m_Script",
                    "m_EditorClassIdentifier",
                },
            )
        self.assertTrue(mock_orch.patch_apply.call_args[1]["dry_run"])

    def test_copy_specific_fields(self) -> None:
        """When fields parameter is provided, only those fields appear in ops."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "copy_component_fields",
                        {
                            "src_asset_path": str(src),
                            "src_symbol_path": "Src/MeshRenderer",
                            "dst_asset_path": str(dst),
                            "dst_symbol_path": "Dst/MeshRenderer",
                            "fields": ["m_Enabled"],
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertEqual(["m_Enabled"], op_paths)

    def test_copy_cross_asset(self) -> None:
        """Source and destination in different files works."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.prefab"
            dst = Path(td) / "b.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "copy_component_fields",
                        {
                            "src_asset_path": str(src),
                            "src_symbol_path": "Src/MeshRenderer",
                            "dst_asset_path": str(dst),
                            "dst_symbol_path": "Dst/MeshRenderer",
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        self.assertEqual(str(dst), plan["resources"][0]["path"])

    def test_copy_same_asset(self) -> None:
        """Source and destination in the same file, different GOs."""
        text = self._two_meshrenderer_prefab()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "copy_component_fields",
                        {
                            "src_asset_path": str(p),
                            "src_symbol_path": "Parent/MeshRenderer",
                            "dst_asset_path": str(p),
                            "dst_symbol_path": "Parent/Child/MeshRenderer",
                        },
                    )
                )

        self.assertTrue(result["success"])

    def test_copy_confirm(self) -> None:
        """confirm=True triggers apply, cache invalidated."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_orch.maybe_auto_refresh.return_value = "done"
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "copy_component_fields",
                        {
                            "src_asset_path": str(src),
                            "src_symbol_path": "Src/MeshRenderer",
                            "dst_asset_path": str(dst),
                            "dst_symbol_path": "Dst/MeshRenderer",
                            "confirm": True,
                            "change_reason": "copy fields for test",
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])
        self.assertIn("auto_refresh", result)

    def test_copy_type_mismatch(self) -> None:
        """Different component types return TYPE_MISMATCH error."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Dst", ["200", "300"]),
                make_transform("200", "100"),
                ("--- !u!33 &300\nMeshFilter:\n  m_ObjectHideFlags: 0\n  m_GameObject: {fileID: 100}\n"),
            ]
        )
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshFilter",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("TYPE_MISMATCH", result["code"])
        self.assertIn("src_type", result["data"])
        self.assertIn("dst_type", result["data"])

    def test_copy_src_symbol_not_found(self) -> None:
        """SYMBOL_NOT_FOUND when source symbol path doesn't resolve."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "NonExistent/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])
        self.assertIn("suggestions", result["data"])

    def test_copy_dst_symbol_not_found(self) -> None:
        """SYMBOL_NOT_FOUND when destination symbol path doesn't resolve."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "NonExistent/MeshRenderer",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_FOUND", result["code"])

    def test_copy_src_not_component(self) -> None:
        """SYMBOL_NOT_COMPONENT when source is a GameObject."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])

    def test_copy_dst_not_component(self) -> None:
        """SYMBOL_NOT_COMPONENT for destination path."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_NOT_COMPONENT", result["code"])

    def test_copy_field_not_found(self) -> None:
        """FIELD_NOT_FOUND when requested field doesn't exist on source."""
        src_text = self._meshrenderer_prefab("Src")
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Src/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                        "fields": ["nonExistentField"],
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("FIELD_NOT_FOUND", result["code"])
        self.assertIn("available_fields", result["data"])

    def test_copy_monobehaviour_fields(self) -> None:
        """MonoBehaviour with custom fields copies correctly."""
        src_text = self._monobehaviour_prefab()
        dst_text = self._monobehaviour_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            script_dir = Path(td) / "Assets" / "Scripts"
            script_dir.mkdir(parents=True)
            cs_file = script_dir / "PlayerScript.cs"
            cs_file.write_text(
                "using UnityEngine;\npublic class PlayerScript : MonoBehaviour {}\n",
                encoding="utf-8",
            )
            meta_file = script_dir / "PlayerScript.cs.meta"
            meta_file.write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server_inst = create_server(project_root=td)
                _, result = _run(
                    server_inst.call_tool(
                        "copy_component_fields",
                        {
                            "src_asset_path": str(src),
                            "src_symbol_path": "Player/MonoBehaviour(PlayerScript)",
                            "dst_asset_path": str(dst),
                            "dst_symbol_path": "Player/MonoBehaviour(PlayerScript)",
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.patch_apply.call_args[1]["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertIn("speed", op_paths)
        self.assertIn("health", op_paths)

    def test_copy_no_fields_to_copy(self) -> None:
        """NO_FIELDS_TO_COPY when source has only system fields."""
        src_text = self._system_fields_only_prefab()
        dst_text = self._meshrenderer_prefab("Dst")
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Empty/MeshRenderer",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Dst/MeshRenderer",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("NO_FIELDS_TO_COPY", result["code"])
        self.assertIn("src_asset_path", result["data"])
        self.assertIn("src_symbol_path", result["data"])

    def test_copy_monobehaviour_unresolvable(self) -> None:
        """SYMBOL_UNRESOLVABLE when MonoBehaviour has no resolved script name."""
        src_text = self._monobehaviour_prefab()
        dst_text = self._monobehaviour_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.prefab"
            dst = Path(td) / "dst.prefab"
            src.write_text(src_text, encoding="utf-8")
            dst.write_text(dst_text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "copy_component_fields",
                    {
                        "src_asset_path": str(src),
                        "src_symbol_path": "Player/MonoBehaviour",
                        "dst_asset_path": str(dst),
                        "dst_symbol_path": "Player/MonoBehaviour",
                    },
                )
            )

        self.assertFalse(result["success"])
        self.assertEqual("SYMBOL_UNRESOLVABLE", result["code"])
        self.assertIn("asset_path", result["data"])
        self.assertIn("symbol_path", result["data"])

    def test_confirm_requires_change_reason(self) -> None:
        server = create_server()

        _, result = _run(
            server.call_tool(
                "copy_component_fields",
                {
                    "src_asset_path": "Assets/DoesNotExist.prefab",
                    "src_symbol_path": "Cube/MeshRenderer",
                    "dst_asset_path": "Assets/DoesNotExist2.prefab",
                    "dst_symbol_path": "Cube/MeshRenderer",
                    "confirm": True,
                    "change_reason": "",
                },
            )
        )

        self.assertFalse(result["success"])
        self.assertEqual("CHANGE_REASON_REQUIRED", result["code"])


class TestSetPropertiesTool(unittest.TestCase):
    """Test the set_properties MCP tool (issue #41 rename of
    ``set_component_fields``).

    ``symbol_path`` resolves directly to a component — there is no
    separate ``component`` argument — and the multi-property dict is
    named ``properties``.
    """

    def _meshrenderer_prefab(self, go_name: str = "Cube") -> str:
        return _make_simple_meshrenderer_prefab(go_name)

    def _monobehaviour_prefab(
        self,
        guid: str = "aaaa1111bbbb2222cccc3333dddd4444",
    ) -> str:
        return _make_simple_monobehaviour_prefab(guid)

    def _double_meshrenderer_prefab(self) -> str:
        """Prefab with two MeshRenderers on the same GameObject."""
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Cube", ["200", "300", "400"]),
                make_transform("200", "100"),
                make_meshrenderer("300", "100"),
                make_meshrenderer("400", "100"),
            ]
        )

    def _two_same_name_go_prefab(self) -> str:
        """Prefab with two root-level GameObjects named 'Cube'."""
        return YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Cube", ["200", "300"]),
                make_transform("200", "100"),
                make_meshrenderer("300", "100"),
                make_gameobject("400", "Cube", ["500", "600"]),
                make_transform("500", "400"),
                make_meshrenderer("600", "400"),
            ]
        )

    def _mock_patch_apply_response(self, dry_run: bool = True) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": ("patch.apply dry-run completed." if dry_run else "patch.apply completed."),
            "data": {"dry_run": dry_run, "confirm": not dry_run, "read_only": dry_run},
            "diagnostics": [],
        }
        return resp

    def test_legacy_tool_name_not_registered(self) -> None:
        """T-41-2: ``set_component_fields`` is not registered; ``set_properties`` is."""
        server = create_server()
        names = {t.name for t in _run(server.list_tools())}
        self.assertIn("set_properties", names)
        self.assertNotIn("set_component_fields", names)

    def test_resolves_component_with_no_component_argument(self) -> None:
        """T-41-1: a component-ending symbol_path resolves with no component arg."""
        text = self._meshrenderer_prefab()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                        },
                    )
                )

        self.assertTrue(result["success"])
        self.assertEqual(
            "MeshRenderer",
            result["symbol_resolution"]["resolved_component"],
        )

    def test_component_keyword_argument_raises_type_error(self) -> None:
        """T-41-1: passing a residual ``component`` keyword raises ``TypeError``."""
        server = create_server()
        registered = server._tool_manager._tools
        fn = registered["set_properties"].fn
        with self.assertRaises(TypeError) as cm:
            fn(
                asset_path="x.prefab",
                symbol_path="Cube/MeshRenderer",
                component="MeshRenderer",
                properties={"m_Enabled": 0},
            )
        self.assertIn("component", str(cm.exception))

    def test_dry_run_multiple_properties(self) -> None:
        """Dry-run with 2 properties builds a 2-op plan and enriches symbol_resolution."""
        text = self._meshrenderer_prefab()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0, "m_CastShadows": 0},
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])
        plan = call_kwargs["plan"]
        op_paths = [op["path"] for op in plan["ops"]]
        self.assertIn("m_Enabled", op_paths)
        self.assertIn("m_CastShadows", op_paths)
        self.assertEqual(2, len(plan["ops"]))
        # Issue #37: every set op identifies its target by the resolved
        # fileID, not a type-name selector.
        for op in plan["ops"]:
            self.assertEqual("300", op["file_id"])
            self.assertNotIn("component", op)
        sr = result["symbol_resolution"]
        self.assertEqual("MeshRenderer", sr["resolved_component"])
        self.assertIn("m_Enabled", sr["fields"])
        self.assertIn("m_CastShadows", sr["fields"])

    def test_set_properties_emits_fileid_targeted_ops_for_siblings(self) -> None:
        """Issue #37: with two same-type components on one GameObject, the
        ops target the resolved fileID so the intended sibling is hit."""
        text = self._double_meshrenderer_prefab()
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                # Two MeshRenderers on one GameObject — addressing the
                # first by #0 must resolve uniquely. ``m_GameObject`` is
                # the property present on the bare synthetic fixture.
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer#0",
                            "properties": {"m_GameObject": {"fileID": 100}},
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.serialized_value_patch_apply.call_args[1]["plan"]
        op = plan["ops"][0]
        self.assertEqual("300", op["file_id"])
        self.assertNotIn("component", op)
        self.assertEqual("300", result["symbol_resolution"]["file_id"])

    def test_confirm_multiple_properties(self) -> None:
        """confirm=True applies the patch and includes auto_refresh in result."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_orch.maybe_auto_refresh.return_value = "done"
                mock_cls.default.return_value = mock_orch

                report_path = Path(td) / "report.json"
                server = create_server(project_root=td)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "disable mesh renderer",
                            "out_report": str(report_path),
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertFalse(call_kwargs["dry_run"])
        self.assertTrue(call_kwargs["confirm"])
        self.assertIn("auto_refresh", result)

    def test_monobehaviour_component(self) -> None:
        """Resolves MonoBehaviour by script_name; resolved_component is the script name."""
        text = self._monobehaviour_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            script_dir = Path(td) / "Assets" / "Scripts"
            script_dir.mkdir(parents=True)
            cs_file = script_dir / "PlayerScript.cs"
            cs_file.write_text(
                "using UnityEngine;\npublic class PlayerScript : MonoBehaviour {}\n",
                encoding="utf-8",
            )
            meta_file = script_dir / "PlayerScript.cs.meta"
            meta_file.write_text(
                "fileFormatVersion: 2\nguid: aaaa1111bbbb2222cccc3333dddd4444\n",
                encoding="utf-8",
            )

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Player/MonoBehaviour(PlayerScript)",
                            "properties": {"speed": 10},
                        },
                    )
                )

        self.assertTrue(result["success"])
        sr = result["symbol_resolution"]
        self.assertEqual("PlayerScript", sr["resolved_component"])

    def test_reference_value_in_properties(self) -> None:
        """Reference dict values are passed through unchanged to the patch plan."""
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Cube", ["200", "300"]),
                make_transform("200", "100"),
                make_meshrenderer_with_materials("300", "100", ["aaa"]),
            ]
        )
        server = create_server()
        mock_resp = self._mock_patch_apply_response(dry_run=True)
        ref_value = {"fileID": 2100000, "guid": "aabbccdd11223344aabbccdd11223344", "type": 2}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Materials": ref_value},
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.serialized_value_patch_apply.call_args[1]["plan"]
        self.assertEqual(ref_value, plan["ops"][0]["value"])
        self.assertEqual("m_Materials", plan["ops"][0]["path"])

    def test_symbol_not_found(self) -> None:
        """SYMBOL_NOT_FOUND when symbol_path does not exist in the asset."""
        text = self._meshrenderer_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": str(p),
                        "symbol_path": "NonExistent/MeshRenderer",
                        "properties": {"m_Enabled": 0},
                    },
                )
            )

        assert_error_envelope(
            result,
            code="SYMBOL_NOT_FOUND",
            severity="error",
        )
        self.assertIn("suggestions", result["data"])

    def test_symbol_ambiguous(self) -> None:
        """SYMBOL_AMBIGUOUS when symbol_path matches multiple components."""
        text = self._two_same_name_go_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            # Two root GameObjects named 'Cube', each with a MeshRenderer.
            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "properties": {"m_Enabled": 0},
                    },
                )
            )

        assert_error_envelope(
            result,
            code="SYMBOL_AMBIGUOUS",
            severity="error",
        )

    def test_symbol_not_component(self) -> None:
        """SYMBOL_NOT_COMPONENT when symbol_path resolves to a GameObject."""
        text = self._meshrenderer_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube",
                        "properties": {"m_Enabled": 0},
                    },
                )
            )

        assert_error_envelope(
            result,
            code="SYMBOL_NOT_COMPONENT",
            severity="error",
        )

    def test_property_not_found(self) -> None:
        """SER003 (property_not_found) when a named property is absent."""
        text = self._meshrenderer_prefab()
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "properties": {"m_NonexistentProp": True},
                    },
                )
            )

        assert_error_envelope(result, code="SER003", severity="error")
        diagnostics = result["diagnostics"]
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("property_not_found", diagnostics[0]["code"])

    def test_empty_properties(self) -> None:
        """EMPTY_FIELDS error returned before any file I/O when properties dict is empty."""
        server = create_server()

        _, result = _run(
            server.call_tool(
                "set_properties",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Cube/MeshRenderer",
                    "properties": {},
                },
            )
        )

        assert_error_envelope(result, code="EMPTY_FIELDS", severity="error")

    def test_confirm_requires_change_reason(self) -> None:
        """CHANGE_REASON_REQUIRED when confirm=True without change_reason."""
        server = create_server()

        _, result = _run(
            server.call_tool(
                "set_properties",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Cube/MeshRenderer",
                    "properties": {"m_Enabled": 0},
                    "confirm": True,
                },
            )
        )

        assert_error_envelope(
            result,
            code="CHANGE_REASON_REQUIRED",
            severity="error",
        )

    def test_confirm_requires_out_report(self) -> None:
        """OUT_REPORT_REQUIRED when confirm=True with change_reason but no out_report."""
        server = create_server()

        _, result = _run(
            server.call_tool(
                "set_properties",
                {
                    "asset_path": "Assets/DoesNotExist.prefab",
                    "symbol_path": "Cube/MeshRenderer",
                    "properties": {"m_Enabled": 0},
                    "confirm": True,
                    "change_reason": "test reason",
                },
            )
        )

        assert_error_envelope(
            result,
            code="OUT_REPORT_REQUIRED",
            severity="error",
        )

    def test_out_report_outside_project_rejected(self) -> None:
        """OUT_REPORT_OUTSIDE_PROJECT redacts the active project root."""
        with (
            tempfile.TemporaryDirectory() as project_directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            outside_report = Path(outside_directory) / "outside_project.json"
            server = create_server(project_root=project_directory)
            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": "Assets/DoesNotExist.prefab",
                        "symbol_path": "Cube/MeshRenderer",
                        "properties": {"m_Enabled": 0},
                        "confirm": True,
                        "change_reason": "test reason",
                        "out_report": str(outside_report),
                    },
                )
            )

            assert_error_envelope(
                result,
                code="OUT_REPORT_OUTSIDE_PROJECT",
                severity="error",
            )
            self.assertEqual(
                "out_report must resolve inside the project root.",
                result["message"],
            )
            self.assertNotIn(
                str(Path(project_directory).resolve()),
                json.dumps(result, sort_keys=True),
            )
            self.assertFalse(outside_report.exists())

    def test_missing_report_parent_stops_before_writer_dispatch(self) -> None:
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            report_path = Path(project_directory) / "missing" / "report.json"

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test report preflight",
                            "out_report": str(report_path),
                        },
                    )
                )

        assert_error_envelope(
            result,
            code="OUT_REPORT_WRITE_FAILED",
            severity="error",
        )
        mock_orch.serialized_value_patch_apply.assert_not_called()
        self.assertFalse(report_path.exists())

    def test_out_report_rejected_when_no_project_root(self) -> None:
        """PROJECT_ROOT_REQUIRED when out_report is supplied but session has no project_root."""
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "report.json"
            server = create_server()  # no project_root
            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": "Assets/DoesNotExist.prefab",
                        "symbol_path": "Cube/MeshRenderer",
                        "properties": {"m_Enabled": 0},
                        "confirm": True,
                        "change_reason": "test reason",
                        "out_report": str(out_path),
                    },
                )
            )
            assert_error_envelope(
                result,
                code="PROJECT_ROOT_REQUIRED",
                severity="error",
            )
            self.assertFalse(out_path.exists())

    def test_dry_run_explicit_parameter(self) -> None:
        """dry_run=True passes dry_run=True, confirm=False to orch.serialized_value_patch_apply."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "dry_run": True,
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])

    def test_dry_run_overrides_confirm(self) -> None:
        """dry_run=True wins over confirm=True: no validation error, dry_run passed to orch."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "dry_run": True,
                            "confirm": True,
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])
        self.assertFalse(call_kwargs["confirm"])

    def test_empty_change_reason_normalized_to_none(self) -> None:
        """change_reason="" is normalized to None before reaching orch.serialized_value_patch_apply."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "dry_run": True,
                            "change_reason": "",
                        },
                    )
                )

        self.assertTrue(result["success"])
        call_kwargs = mock_orch.serialized_value_patch_apply.call_args[1]
        self.assertIsNone(call_kwargs["change_reason"])

    def test_confirm_writes_out_report(self) -> None:
        """confirm=True with change_reason + out_report writes result JSON to out_report path."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")
            report_path = Path(td) / "report.json"

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_orch.maybe_auto_refresh.return_value = "done"
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test write report",
                            "out_report": str(report_path),
                        },
                    )
                )

                self.assertTrue(result["success"])
                self.assertTrue(report_path.exists(), "out_report file should be written")
                written = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(result, written)

    def test_report_finalization_failure_preserves_operation_result(self) -> None:
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            report_path = Path(project_directory) / "report.json"

            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.patch_transaction_io._atomic_replace",
                    side_effect=OSError("sensitive host path"),
                ),
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_orch.maybe_auto_refresh.return_value = "done"
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test report finalization",
                            "out_report": str(report_path),
                        },
                    )
                )

            assert_error_envelope(
                result,
                code="OUT_REPORT_WRITE_FAILED",
                severity="error",
            )
            self.assertEqual(
                "Operation completed but the report file could not be written.",
                result["message"],
            )
            self.assertEqual(
                (True, "PATCH_APPLY_RESULT"),
                (
                    result["data"]["operation_result"]["success"],
                    result["data"]["operation_result"]["code"],
                ),
            )
            self.assertNotIn(
                "sensitive host path",
                json.dumps(result, sort_keys=True),
            )
            self.assertEqual(b"", report_path.read_bytes())
            mock_orch.serialized_value_patch_apply.assert_called_once()


    def test_writer_exception_finalizes_reserved_report_with_stable_failure(self) -> None:
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            report_path = Path(project_directory) / "report.json"

            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.side_effect = ValueError(
                    "sensitive writer detail",
                )
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test writer exception",
                            "out_report": str(report_path),
                        },
                    )
                )

            assert_error_envelope(
                result,
                code="PATCH_APPLY_RESULT",
                severity="error",
            )
            self.assertEqual("Patch transaction apply failed.", result["message"])
            self.assertEqual(
                {"boundary": "apply", "state_unknown": True},
                result["data"],
            )
            self.assertNotIn(
                "sensitive writer detail",
                json.dumps(result, sort_keys=True),
            )
            self.assertEqual(
                result,
                json.loads(report_path.read_text(encoding="utf-8")),
            )
            mock_orch.serialized_value_patch_apply.assert_called_once()
            mock_orch.maybe_auto_refresh.assert_not_called()
            invalidate_symbol_tree.assert_called_once_with(asset_path.resolve())

    def test_refresh_exception_finalizes_stable_uncertain_report(self) -> None:
        text = self._meshrenderer_prefab()
        response = self._mock_patch_apply_response(dry_run=False)

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            report_path = Path(project_directory) / "report.json"

            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = response
                mock_orch.maybe_auto_refresh.side_effect = RuntimeError(
                    "sensitive refresh detail",
                )
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test refresh failure",
                            "out_report": str(report_path),
                        },
                    )
                )

            assert_error_envelope(
                result,
                code="PATCH_APPLY_RESULT",
                severity="error",
            )
            self.assertEqual(
                (
                    "Patch transaction apply failed.",
                    {"boundary": "apply", "state_unknown": True},
                ),
                (result["message"], result["data"]),
            )
            self.assertNotIn(
                "sensitive refresh detail",
                json.dumps(result, sort_keys=True),
            )
            self.assertEqual(
                result,
                json.loads(report_path.read_text(encoding="utf-8")),
            )
            mock_orch.serialized_value_patch_apply.assert_called_once_with(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "target",
                            "path": str(asset_path),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "target",
                            "op": "set",
                            "file_id": "300",
                            "path": "m_Enabled",
                            "value": 0,
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
                change_reason="test refresh failure",
            )
            mock_orch.maybe_auto_refresh.assert_called_once_with()
            invalidate_symbol_tree.assert_called_once_with(asset_path.resolve())

    def test_dry_run_writer_exception_returns_stable_failure_without_report(self) -> None:
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")

            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.side_effect = ValueError(
                    "sensitive dry-run detail",
                )
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                        },
                    )
                )

            assert_error_envelope(
                result,
                code="PATCH_APPLY_RESULT",
                severity="error",
            )
            self.assertEqual(
                {"boundary": "apply", "state_unknown": False},
                result["data"],
            )
            self.assertNotIn(
                "sensitive dry-run detail",
                json.dumps(result, sort_keys=True),
            )
            mock_orch.serialized_value_patch_apply.assert_called_once()
            invalidate_symbol_tree.assert_not_called()


    def test_writer_and_report_failure_preserves_redacted_operation_error(self) -> None:
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            report_path = Path(project_directory) / "report.json"

            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
                patch(
                    "prefab_sentinel.patch_transaction_io._atomic_replace",
                    side_effect=OSError("sensitive report detail"),
                ),
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.side_effect = ValueError(
                    "sensitive writer detail",
                )
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test compound failure",
                            "out_report": str(report_path),
                        },
                    )
                )

            assert_error_envelope(
                result,
                code="OUT_REPORT_WRITE_FAILED",
                severity="error",
            )
            operation_error = result["data"]["operation_error"]
            self.assertEqual(
                (
                    False,
                    "PATCH_APPLY_RESULT",
                    {"boundary": "apply", "state_unknown": True},
                ),
                (
                    operation_error["success"],
                    operation_error["code"],
                    operation_error["data"],
                ),
            )
            self.assertNotIn(
                "sensitive",
                json.dumps(result, sort_keys=True),
            )
            self.assertEqual(b"", report_path.read_bytes())
            mock_orch.serialized_value_patch_apply.assert_called_once()
            invalidate_symbol_tree.assert_called_once_with(asset_path.resolve())


    def test_orchestrator_acquisition_failure_finalizes_reserved_report(self) -> None:
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            report_path = Path(project_directory) / "report.json"

            with (
                patch.object(
                    ProjectSession,
                    "get_orchestrator",
                    side_effect=ValueError("sensitive orchestrator detail"),
                ) as get_orchestrator,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test orchestrator acquisition failure",
                            "out_report": str(report_path),
                        },
                    )
                )

            assert_error_envelope(
                result,
                code="PATCH_APPLY_RESULT",
                severity="error",
            )
            self.assertEqual(
                ("Patch transaction apply failed.", {"boundary": "apply", "state_unknown": False}),
                (result["message"], result["data"]),
            )
            self.assertNotIn(
                "sensitive orchestrator detail",
                json.dumps(result, sort_keys=True),
            )
            self.assertEqual(
                result,
                json.loads(report_path.read_text(encoding="utf-8")),
            )
            get_orchestrator.assert_called_once_with()
            invalidate_symbol_tree.assert_not_called()


    def test_asset_preflight_path_escape_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory:
            escaped_target = (Path(project_directory).parent / "secret.prefab").resolve()
            with patch.object(ProjectSession, "get_orchestrator") as get_orchestrator:
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": "../secret.prefab",
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                        },
                    )
                )

        assert_error_envelope(
            result,
            code="PATCH_APPLY_RESULT",
            severity="error",
        )
        self.assertEqual(
            (
                "Patch transaction preflight failed.",
                {"boundary": "preflight"},
            ),
            (result["message"], result["data"]),
        )
        public_response = json.dumps(result, sort_keys=True)
        self.assertNotIn(str(escaped_target), public_response)
        self.assertNotIn(str(Path(project_directory).resolve()), public_response)
        get_orchestrator.assert_not_called()

    def test_confirmed_failed_writer_response_marks_state_unknown(self) -> None:
        text = self._meshrenderer_prefab()
        failed_response = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="PATCH_APPLY_RESULT",
            message="patch.apply stopped by fail-fast policy due to apply failure.",
            data={
                "read_only": False,
                "fail_fast_triggered": True,
                "steps": [
                    {
                        "step": "apply_and_save",
                        "result": {
                            "success": False,
                            "severity": "error",
                            "code": "SER_APPLY_FAILED",
                            "message": "Prefab reload failed.",
                            "data": {
                                "applied": 1,
                                "read_only": False,
                                "executed": True,
                            },
                            "diagnostics": [],
                        },
                    }
                ],
            },
            diagnostics=[],
        )
        writer_data = dict(failed_response.data)

        with tempfile.TemporaryDirectory() as project_directory:
            asset_path = Path(project_directory) / "test.prefab"
            asset_path.write_text(text, encoding="utf-8")
            report_path = Path(project_directory) / "report.json"

            with (
                patch(
                    "prefab_sentinel.session_cache.Phase1Orchestrator",
                ) as mock_cls,
                patch(
                    "prefab_sentinel.session.ProjectSession.invalidate_symbol_tree",
                ) as invalidate_symbol_tree,
            ):
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = failed_response
                mock_cls.default.return_value = mock_orch
                server = create_server(project_root=project_directory)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(asset_path),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {"m_Enabled": 0},
                            "confirm": True,
                            "change_reason": "test persisted writer failure",
                            "out_report": str(report_path),
                        },
                    )
                )

            assert_error_envelope(
                result,
                code="PATCH_APPLY_RESULT",
                severity="error",
            )
            self.assertEqual(
                (True, 1, True),
                (
                    result["data"]["state_unknown"],
                    result["data"]["steps"][0]["result"]["data"]["applied"],
                    result["data"]["steps"][0]["result"]["data"]["executed"],
                ),
            )
            self.assertEqual(writer_data, failed_response.data)
            self.assertEqual(
                result,
                json.loads(report_path.read_text(encoding="utf-8")),
            )
            mock_orch.serialized_value_patch_apply.assert_called_once_with(
                plan={
                    "plan_version": 2,
                    "resources": [
                        {
                            "id": "target",
                            "path": str(asset_path),
                            "mode": "open",
                        }
                    ],
                    "ops": [
                        {
                            "resource": "target",
                            "op": "set",
                            "file_id": "300",
                            "path": "m_Enabled",
                            "value": 0,
                        }
                    ],
                },
                dry_run=False,
                confirm=True,
                change_reason="test persisted writer failure",
            )
            mock_orch.maybe_auto_refresh.assert_not_called()
            invalidate_symbol_tree.assert_called_once_with(asset_path.resolve())

    def test_value_coercion_passthrough(self) -> None:
        """Mixed value types (int, float, str, dict) pass through unchanged to plan ops."""
        text = self._meshrenderer_prefab()
        mock_resp = self._mock_patch_apply_response(dry_run=True)
        ref_value = {"fileID": 100, "guid": "abc", "type": 2}

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                server = create_server(project_root=td)
                _, result = _run(
                    server.call_tool(
                        "set_properties",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Cube/MeshRenderer",
                            "properties": {
                                "m_Enabled": 0,
                                "m_CastShadows": 3.14,
                                "m_ObjectHideFlags": "hello",
                                "m_GameObject": ref_value,
                            },
                        },
                    )
                )

        self.assertTrue(result["success"])
        plan = mock_orch.serialized_value_patch_apply.call_args[1]["plan"]
        ops_by_path = {op["path"]: op["value"] for op in plan["ops"]}
        self.assertEqual(0, ops_by_path["m_Enabled"])
        self.assertAlmostEqual(3.14, ops_by_path["m_CastShadows"])
        self.assertEqual("hello", ops_by_path["m_ObjectHideFlags"])
        self.assertEqual(ref_value, ops_by_path["m_GameObject"])

    def test_component_unresolvable_no_project_root(self) -> None:
        """SYMBOL_UNRESOLVABLE when MonoBehaviour matches by name but has no script name."""
        guid = "aaaa1111bbbb2222cccc3333dddd4444"
        text = self._monobehaviour_prefab(guid=guid)
        server = create_server()  # no project_root

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Player/MonoBehaviour",
                        "properties": {"speed": 10},
                    },
                )
            )

        assert_error_envelope(
            result,
            code="SYMBOL_UNRESOLVABLE",
            severity="error",
        )
        self.assertIn("asset_path", result["data"])


class TestSetPropertyTool37(unittest.TestCase):
    """Issue #37 fileID-targeted op emission for ``set_property``."""

    def _mock_patch_apply_response(self) -> MagicMock:
        resp = MagicMock()
        resp.success = True
        resp.to_dict.return_value = {
            "success": True,
            "severity": "info",
            "code": "PATCH_APPLY_RESULT",
            "message": "patch.apply dry-run completed.",
            "data": {"dry_run": True, "confirm": False, "read_only": True},
            "diagnostics": [],
        }
        return resp

    def test_emits_fileid_targeted_op(self) -> None:
        """Issue #37: a nested component emits a set op whose target is
        the resolved symbol node's fileID, with no type-name selector."""
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Body", ["110"]),
                make_transform("110", "100", "0", ["210"]),
                make_gameobject("200", "Head", ["210", "300"]),
                make_transform("210", "200", "110"),
                make_meshrenderer("300", "200"),
            ]
        )
        server = create_server()
        mock_resp = self._mock_patch_apply_response()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Body/Head/MeshRenderer",
                            "property_path": "m_Enabled",
                            "value": 0,
                        },
                    )
                )

        plan = mock_orch.serialized_value_patch_apply.call_args[1]["plan"]
        op = plan["ops"][0]
        self.assertEqual("300", op["file_id"])
        self.assertNotIn("component", op)

    def test_symbol_resolution_failure_returns_typed_envelope(self) -> None:
        """Issue #37: a non-resolvable symbol_path still returns a typed
        SYMBOL_* envelope and emits no patch."""
        text = YAML_HEADER + "\n".join(
            [
                make_gameobject("100", "Body", ["200", "300"]),
                make_transform("200", "100"),
                make_meshrenderer("300", "100"),
            ]
        )
        server = create_server()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.prefab"
            p.write_text(text, encoding="utf-8")

            mock_resp = self._mock_patch_apply_response()
            with patch(
                "prefab_sentinel.session_cache.Phase1Orchestrator",
            ) as mock_cls:
                mock_orch = MagicMock()
                mock_orch.serialized_value_patch_apply.return_value = mock_resp
                mock_cls.default.return_value = mock_orch

                _, result = _run(
                    server.call_tool(
                        "set_property",
                        {
                            "asset_path": str(p),
                            "symbol_path": "Body/NoSuchComponent",
                            "property_path": "m_Enabled",
                            "value": 0,
                        },
                    )
                )

            mock_orch.serialized_value_patch_apply.assert_not_called()

        assert_error_envelope(
            result,
            code="SYMBOL_NOT_FOUND",
            severity="error",
        )


class TestEditorSetPropertiesTool(unittest.TestCase):
    """Test the editor_set_properties MCP tool (issue #41 rename of
    ``editor_set_component_fields``).

    Entries key ``property_name`` (issue #53) and carry a per-entry
    ``value_present`` marker (issue #52).
    """

    def setUp(self) -> None:
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_legacy_tool_name_not_registered(self) -> None:
        """T-41-2: ``editor_set_component_fields`` is gone; ``editor_set_properties`` present."""
        server = create_server()
        names = {t.name for t in _run(server.list_tools())}
        self.assertIn("editor_set_properties", names)
        self.assertNotIn("editor_set_component_fields", names)

    def test_properties_with_values(self) -> None:
        """Primitive value entries are delegated to editor_batch_set_property."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Foo/Bar",
                        "component_type": "MyComponent",
                        "properties": [
                            {"property_name": "speed", "value": "60"},
                            {"property_name": "health", "value": "100"},
                        ],
                    },
                )
            )

        mock_send.assert_called_once()
        args = mock_send.call_args
        self.assertEqual("editor_batch_set_property", args.kwargs["action"])
        ops = json.loads(args.kwargs["batch_operations_json"])
        self.assertEqual(2, len(ops))
        self.assertEqual("/Foo/Bar", ops[0]["hierarchy_path"])
        self.assertEqual("MyComponent", ops[0]["component_type"])
        self.assertEqual("speed", ops[0]["property_name"])
        self.assertEqual("60", ops[0]["value"])
        self.assertTrue(ops[0]["value_present"])
        self.assertNotIn("object_reference", ops[0])

    def test_properties_with_object_reference(self) -> None:
        """Object reference entries are delegated with object_reference key."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "Controller",
                        "properties": [
                            {"property_name": "target", "object_reference": "/SomeTarget"},
                        ],
                    },
                )
            )

        ops = json.loads(mock_send.call_args.kwargs["batch_operations_json"])
        self.assertEqual(1, len(ops))
        self.assertEqual("target", ops[0]["property_name"])
        self.assertEqual("/SomeTarget", ops[0]["object_reference"])
        self.assertFalse(ops[0]["value_present"])
        self.assertNotIn("value", ops[0])

    def test_mixed_properties(self) -> None:
        """Mix of value and object_reference entries are both mapped correctly."""
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Ctrl",
                        "component_type": "DualCtrl",
                        "properties": [
                            {"property_name": "speed", "value": "10"},
                            {"property_name": "target", "object_reference": "/Target"},
                        ],
                    },
                )
            )

        ops = json.loads(mock_send.call_args.kwargs["batch_operations_json"])
        self.assertEqual(2, len(ops))
        self.assertIn("value", ops[0])
        self.assertTrue(ops[0]["value_present"])
        self.assertNotIn("object_reference", ops[0])
        self.assertIn("object_reference", ops[1])
        self.assertFalse(ops[1]["value_present"])
        self.assertNotIn("value", ops[1])

    def test_empty_properties(self) -> None:
        """EDITOR_SET_COMP_EMPTY_FIELDS returned for empty properties list."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "properties": [],
                    },
                )
            )
            mock_send.assert_not_called()

        assert_error_envelope(
            result,
            code="EDITOR_SET_COMP_EMPTY_FIELDS",
            severity="error",
        )

    def test_entry_missing_property_name(self) -> None:
        """EDITOR_SET_COMP_INVALID_FIELD when an entry has no 'property_name' key."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "properties": [{"value": "60"}],
                    },
                )
            )
            mock_send.assert_not_called()

        assert_error_envelope(
            result,
            code="EDITOR_SET_COMP_INVALID_FIELD",
            severity="error",
        )

    def test_entry_missing_value_and_reference(self) -> None:
        """EDITOR_SET_COMP_INVALID_FIELD when entry has property_name but no value/ref."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "properties": [{"property_name": "foo"}],
                    },
                )
            )
            mock_send.assert_not_called()

        assert_error_envelope(
            result,
            code="EDITOR_SET_COMP_INVALID_FIELD",
            severity="error",
        )

    def test_entry_has_both_value_and_object_reference(self) -> None:
        """EDITOR_SET_COMP_INVALID_FIELD when entry supplies both value and object_reference."""
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_ops.send_action") as mock_send:
            _, result = _run(
                server.call_tool(
                    "editor_set_properties",
                    {
                        "hierarchy_path": "/Obj",
                        "component_type": "MyComp",
                        "properties": [
                            {
                                "property_name": "target",
                                "value": "1",
                                "object_reference": "/Other",
                            },
                        ],
                    },
                )
            )
            mock_send.assert_not_called()

        assert_error_envelope(
            result,
            code="EDITOR_SET_COMP_INVALID_FIELD",
            severity="error",
        )
        self.assertIn("not both", result["message"])


class TestSetPropertiesIntegration(unittest.TestCase):
    """Integration tests for set_properties without orchestrator mocking."""

    def _meshrenderer_prefab(self) -> str:
        return _make_simple_meshrenderer_prefab()

    def test_e2e_dry_run_with_prefab_fixture(self) -> None:
        """E2E dry-run with a real .prefab fixture (no mock): result envelope is correct."""
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fixture.prefab"
            p.write_text(text, encoding="utf-8")

            server = create_server(project_root=td)
            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "properties": {"m_Enabled": 0},
                        "dry_run": True,
                    },
                )
            )

        self.assertEqual(
            (True, "PATCH_APPLY_RESULT", True, False, ["dry_run_patch"]),
            (
                result["success"],
                result["code"],
                result["data"]["dry_run"],
                result["data"]["confirm"],
                [step["step"] for step in result["data"]["steps"]],
            ),
            msg=f"unexpected legacy set_properties dry-run envelope: {result!r}",
        )
        self.assertEqual(
            {
                "symbol_path": "Cube/MeshRenderer",
                "resolved_component": "MeshRenderer",
                "file_id": "300",
                "class_id": "23",
                "fields": ["m_Enabled"],
            },
            result["symbol_resolution"],
        )

    @unittest.skipUnless(
        os.environ.get("UNITYTOOL_PATCH_BRIDGE"),
        "requires patch bridge (UNITYTOOL_PATCH_BRIDGE must be set)",
    )
    def test_e2e_confirm_roundtrip(self) -> None:
        """E2E confirm with real .prefab fixture: report file written with valid JSON."""
        text = self._meshrenderer_prefab()

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fixture.prefab"
            p.write_text(text, encoding="utf-8")
            report_path = Path(td) / "report.json"

            server = create_server(project_root=td)
            _, result = _run(
                server.call_tool(
                    "set_properties",
                    {
                        "asset_path": str(p),
                        "symbol_path": "Cube/MeshRenderer",
                        "properties": {"m_Enabled": 0},
                        "confirm": True,
                        "change_reason": "integration test roundtrip",
                        "out_report": str(report_path),
                    },
                )
            )

            self.assertTrue(result["success"])
            self.assertTrue(report_path.exists(), "out_report file should be written")
            written = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(result, written)


class TestEditorSetPropertiesIntegration(unittest.TestCase):
    """Integration tests for editor_set_properties with live Editor bridge."""

    @unittest.skipUnless(
        os.environ.get("UNITYTOOL_BRIDGE_E2E_LIVE") == "1",
        "requires live editor bridge (UNITYTOOL_BRIDGE_E2E_LIVE=1 must be set)",
    )
    def test_e2e_editor_bridge(self) -> None:
        """E2E with live Editor bridge: sets fields and returns success envelope.

        Gated on a dedicated opt-in (``UNITYTOOL_BRIDGE_E2E_LIVE=1``) so the
        unittest-parallel suite stays green on developer shells that lack a
        live Unity Editor with the fixture scene loaded (issue #88 / #89
        follow-up; post-#270 the bridge dispatch surface has no mode env var).
        """
        server = create_server()
        _, result = _run(
            server.call_tool(
                "editor_set_properties",
                {
                    "hierarchy_path": "/DualButtonController/Controller",
                    "component_type": "DualButtonController",
                    "properties": [
                        {"property_name": "clearDelaySeconds", "value": "60"},
                    ],
                },
            )
        )

        self.assertEqual(
            (True, True),
            (bool(result["success"]), "data" in result),
            msg=(f"live editor bridge envelope must report success=True and carry a 'data' field; got {result!r}"),
        )


class TestSafeSaveAsPrefabPython(unittest.TestCase):
    """Issue #193 — editor_safe_save_prefab MCP tool surface tests.

    The Python wrapper validates the required ``protect_components`` list
    before forwarding to the bridge action, JSON-serializes the list onto
    the request payload, and passes through ``force_original`` when set.
    The supported-actions exhaustive expectation lists ``safe_save_prefab``
    as the sole prefab-save action.
    """

    def setUp(self) -> None:
        # The watch-dir env var must not leak from the host shell so the
        # Python wrapper path under test is exercised deterministically
        # (issues #88 / #89 / #270 — host worktree exports are non-decisive).
        os.environ.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    def test_empty_protect_list_forwards_to_bridge_for_raw_save(self) -> None:
        """Issue #228 — an empty ``protect_components`` list is a request
        for raw-save mode. The wrapper serialises the empty list onto the
        request payload (so the bridge handler observes ``[]``) and never
        rejects it on the Python side; the bridge decides between the
        strip-and-reattach pipeline and the raw-save branch.
        """
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_safe_save_prefab",
                    {
                        "hierarchy_path": "/Obj",
                        "asset_path": "Assets/X.prefab",
                        "protect_components": [],
                        "confirm": True,
                        "change_reason": "save prefab for test",
                    },
                )
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual("safe_save_prefab", kwargs["action"])
        self.assertIn("protect_components_json", kwargs)
        self.assertEqual("[]", kwargs["protect_components_json"])
        parsed = json.loads(kwargs["protect_components_json"])
        self.assertEqual([], parsed)

    def test_empty_protect_list_returns_bridge_envelope_verbatim(self) -> None:
        """Issue #228 — the success envelope from the bridge for the raw-
        save call passes through the wrapper unchanged.
        """
        bridge_envelope = {
            "success": True,
            "severity": "info",
            "code": "EDITOR_CTRL_SAFE_SAVE_PREFAB_OK",
            "message": "Saved /Obj as Prefab: Assets/X.prefab",
            "data": {
                "output_path": "Assets/X.prefab",
                "executed": True,
                "read_only": False,
                "warnings": {
                    "udonsharp_obs_nre_count": 0,
                    "nonfatal_patterns": [],
                },
                "reattached_components": [],
                "orphan_modifications": [],
            },
            "diagnostics": [],
        }
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value=bridge_envelope,
        ):
            _, envelope = _run(
                server.call_tool(
                    "editor_safe_save_prefab",
                    {
                        "hierarchy_path": "/Obj",
                        "asset_path": "Assets/X.prefab",
                        "protect_components": [],
                        "confirm": True,
                        "change_reason": "save prefab for test",
                    },
                )
            )
        self.assertEqual(bridge_envelope, envelope)

    def test_protect_components_serialized_as_json(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_safe_save_prefab",
                    {
                        "hierarchy_path": "/Obj",
                        "asset_path": "Assets/X.prefab",
                        "protect_components": ["VRC_UiShape", "OtherComp"],
                        "confirm": True,
                        "change_reason": "save prefab for test",
                    },
                )
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual("safe_save_prefab", kwargs.get("action"))
        self.assertIn("protect_components_json", kwargs)
        parsed = json.loads(kwargs["protect_components_json"])
        self.assertEqual(["VRC_UiShape", "OtherComp"], parsed)

    def test_force_original_true_passes_through(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_safe_save_prefab",
                    {
                        "hierarchy_path": "/Obj",
                        "asset_path": "Assets/X.prefab",
                        "protect_components": ["VRC_UiShape"],
                        "force_original": True,
                        "confirm": True,
                        "change_reason": "save prefab for test",
                    },
                )
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual("safe_save_prefab", kwargs.get("action"))
        self.assertTrue(kwargs.get("force_original"))

    def test_force_original_default_omits_key(self) -> None:
        server = create_server()
        with patch(
            "prefab_sentinel.mcp_tools_editor_ops.send_action",
            return_value={"success": True, "data": {}},
        ) as mock_send:
            _run(
                server.call_tool(
                    "editor_safe_save_prefab",
                    {
                        "hierarchy_path": "/Obj",
                        "asset_path": "Assets/X.prefab",
                        "protect_components": ["VRC_UiShape"],
                        "confirm": True,
                        "change_reason": "save prefab for test",
                    },
                )
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual("safe_save_prefab", kwargs.get("action"))
        self.assertNotIn("force_original", kwargs)


class TestMcpServerToolNames(unittest.TestCase):
    """Issue #193 — membership test that the MCP tool registry contains
    exactly one prefab-save tool (``editor_safe_save_prefab``) and no
    other prefab-save tool name (e.g. the legacy ``editor_save_as_prefab``).
    """

    def test_safe_save_prefab_is_only_prefab_save_tool(self) -> None:
        server = create_server()
        tools = _run(server.list_tools())
        names = {t.name for t in tools}
        self.assertIn("editor_safe_save_prefab", names)
        self.assertNotIn("editor_save_as_prefab", names)


class TestEditorBatchCreateComponents(unittest.TestCase):
    """I3: editor_batch_create serializes components list in JSON payload."""

    def test_editor_batch_create_components_serialized(self) -> None:
        server = create_server()
        with patch("prefab_sentinel.mcp_tools_editor_batch.send_action", return_value={"success": True}) as mock_send:
            _run(
                server.call_tool(
                    "editor_batch_create",
                    {"objects": [{"name": "Box", "components": ["BoxCollider"]}]},
                )
            )
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        parsed = json.loads(call_kwargs["batch_objects_json"])
        self.assertEqual(parsed, [{"name": "Box", "components": ["BoxCollider"]}])


class TestSessionDiagnosticBuilder(unittest.TestCase):
    """Issue #304: ``_build_session_diagnostic`` emits the four-key
    unified wire shape so session-level ad-hoc diagnostics (deploy,
    project-status, bridge-version) cross the MCP boundary in the
    same form as ``Diagnostic``-backed diagnostics flowing through
    ``ToolResponse.to_dict``.
    """

    def test_helper_emits_four_key_wire_dict(self) -> None:
        from prefab_sentinel.contracts import Severity  # noqa: PLC0415
        from prefab_sentinel.mcp_tools_session import (  # noqa: PLC0415
            _build_session_diagnostic,
        )

        wire = _build_session_diagnostic(
            "BRIDGE_VERSION_MISMATCH",
            "Bridge=1.2.3, Python=1.2.4",
            severity=Severity.WARNING,
            data={"bridge_version": "1.2.3"},
        )
        self.assertEqual(
            {"severity", "code", "message", "data"},
            set(wire.keys()),
        )
        self.assertEqual("warning", wire["severity"])
        self.assertEqual("BRIDGE_VERSION_MISMATCH", wire["code"])
        self.assertEqual("Bridge=1.2.3, Python=1.2.4", wire["message"])
        self.assertEqual({"bridge_version": "1.2.3"}, wire["data"])

    def test_helper_defaults_data_payload_to_empty_dict(self) -> None:
        from prefab_sentinel.contracts import Severity  # noqa: PLC0415
        from prefab_sentinel.mcp_tools_session import (  # noqa: PLC0415
            _build_session_diagnostic,
        )

        wire = _build_session_diagnostic(
            "X",
            "y",
            severity=Severity.INFO,
        )
        self.assertEqual({}, wire["data"])


if __name__ == "__main__":
    unittest.main()
