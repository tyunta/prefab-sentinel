"""Structural tests guarding the serialized_object package split (issue #91).

These tests pin the architectural invariants of the post-split package:

* the public API surface of ``prefab_sentinel.services.serialized_object``
  exposes exactly ``SerializedObjectService`` (no sibling module
  re-exported, no deprecation shim);
* every ``*.py`` under the package stays within the 300-line hard
  limit — including ``service.py`` itself after the Phase 2+ carve-out.

Also covers the SER003 envelope contract added by issue #109 — when
``set_component_fields`` references a component or property that cannot be
resolved on the chain, the response is a ``SER003`` error envelope with
did-you-mean suggestions and a typed diagnostic.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import prefab_sentinel.services.serialized_object as serialized_object_pkg
from prefab_sentinel.services.serialized_object import SerializedObjectService
from prefab_sentinel.services.serialized_object.service import (
    SerializedObjectService as _ServiceSerializedObjectService,
)

PACKAGE_DIR = Path(serialized_object_pkg.__file__).parent
LINE_LIMIT = 300


def _run_call_tool(coro: Any) -> dict[str, Any]:
    """Run a server.call_tool coroutine and return the parsed JSON dict."""
    raw = asyncio.run(coro)
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        return raw[1]
    if isinstance(raw, list) and raw and hasattr(raw[0], "text"):
        return json.loads(raw[0].text)
    raise RuntimeError("Unexpected call_tool return shape")


def _meshrenderer_prefab() -> str:
    """A minimal synthetic prefab with one GameObject + Transform + MeshRenderer.

    Field set was chosen to exercise both the property-found and
    property-not-found branches of ``set_component_fields``.
    """
    return (
        "%YAML 1.1\n"
        "%TAG !u! tag:unity3d.com,2011:\n"
        "--- !u!1 &100\n"
        "GameObject:\n"
        "  m_ObjectHideFlags: 0\n"
        "  serializedVersion: 6\n"
        "  m_Component:\n"
        "  - component: {fileID: 200}\n"
        "  - component: {fileID: 300}\n"
        "  m_Layer: 0\n"
        "  m_Name: Cube\n"
        "  m_TagString: Untagged\n"
        "  m_IsActive: 1\n"
        "--- !u!4 &200\n"
        "Transform:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_GameObject: {fileID: 100}\n"
        "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
        "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
        "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        "  m_Children: []\n"
        "  m_Father: {fileID: 0}\n"
        "  m_RootOrder: 0\n"
        "--- !u!23 &300\n"
        "MeshRenderer:\n"
        "  m_ObjectHideFlags: 0\n"
        "  m_GameObject: {fileID: 100}\n"
        "  m_Enabled: 1\n"
        "  m_CastShadows: 1\n"
        "  m_Materials:\n"
        "  - {fileID: 2100000, guid: aaa, type: 2}\n"
    )


class SerializedObjectPackageSurfaceTests(unittest.TestCase):
    """T-91-SURFACE: package exports exactly ``SerializedObjectService``."""

    def test_public_surface_preserved(self) -> None:
        """Legacy import path resolves to the post-split implementation."""
        self.assertIs(SerializedObjectService, _ServiceSerializedObjectService)

    def test_all_exports_service_class_only(self) -> None:
        """``__all__`` lists exactly ``SerializedObjectService``; no
        sibling module or private helper is re-exported from the
        package root."""
        self.assertEqual(
            ("SerializedObjectService",),
            tuple(serialized_object_pkg.__all__),
        )


class SerializedObjectPackageLimitTests(unittest.TestCase):
    """T-91-LIMIT: every ``*.py`` under the subtree is ≤300 lines."""

    def test_every_module_line_limit(self) -> None:
        self.assertTrue(PACKAGE_DIR.is_dir(), f"Package dir missing: {PACKAGE_DIR}")
        modules = sorted(PACKAGE_DIR.glob("*.py"))
        self.assertGreater(len(modules), 0, "Expected at least one module")

        oversized: list[tuple[str, int]] = []
        for module_path in modules:
            with module_path.open(encoding="utf-8") as handle:
                line_count = sum(1 for _ in handle)
            if line_count > LINE_LIMIT:
                oversized.append((module_path.name, line_count))

        self.assertEqual(
            oversized,
            [],
            f"Modules exceed {LINE_LIMIT}-line limit: {oversized}",
        )


class SetComponentFieldsSER003Tests(unittest.TestCase):
    """Issue #109 — ``set_component_fields`` returns the structured SER003
    envelope when a referenced property or component cannot be resolved on
    the chain, and returns the dry-run preview envelope when the input is
    valid.
    """

    def _setup_server_and_prefab(self, td: Path) -> tuple[Any, Path]:
        from prefab_sentinel.mcp_server import create_server  # noqa: PLC0415

        prefab_path = td / "test.prefab"
        prefab_path.write_text(_meshrenderer_prefab(), encoding="utf-8")
        server = create_server()
        return server, prefab_path

    def test_set_component_fields_returns_ser003_for_unknown_property(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            server, prefab_path = self._setup_server_and_prefab(td)
            result = _run_call_tool(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(prefab_path),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_NoSuchField": 0},
                },
            ))

        self.assertEqual("SER003", result["code"])
        self.assertEqual("error", result["severity"])
        diagnostics = result["diagnostics"]
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("property_not_found", diagnostics[0]["detail"])
        # MeshRenderer's own field names should appear among suggestions.
        suggestions = result["data"]["suggestions"]
        self.assertIn("m_Enabled", suggestions)

    def test_set_component_fields_returns_ser003_for_unknown_component(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            server, prefab_path = self._setup_server_and_prefab(td)
            result = _run_call_tool(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(prefab_path),
                    "symbol_path": "Cube",
                    "component": "MeshRendererr",
                    "fields": {"m_Enabled": 0},
                },
            ))

        self.assertEqual("SER003", result["code"])
        self.assertEqual("error", result["severity"])
        diagnostics = result["diagnostics"]
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("component_not_found", diagnostics[0]["detail"])
        suggestions = result["data"]["suggestions"]
        self.assertIn("MeshRenderer", suggestions)

    def test_set_component_fields_succeeds_for_real_property(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            server, prefab_path = self._setup_server_and_prefab(td)
            result = _run_call_tool(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(prefab_path),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {"m_Enabled": 0},
                },
            ))

        self.assertNotEqual("SER003", result["code"])
        for diagnostic in result["diagnostics"]:
            self.assertNotEqual("property_not_found", diagnostic["detail"])
            self.assertNotEqual("component_not_found", diagnostic["detail"])

    def test_set_component_fields_accepts_array_container_field(self) -> None:
        """Whole-array assignment to ``m_Materials`` is not a false SER003.

        ``iter_base_property_values`` only emits the per-element path
        (``m_Materials.Array.data[0]``); without container-name surfacing
        the property-existence guard would reject the whole-array form.
        Issue #109 follow-up — pins the behaviour after the fix.
        """
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            server, prefab_path = self._setup_server_and_prefab(td)
            result = _run_call_tool(server.call_tool(
                "set_component_fields",
                {
                    "asset_path": str(prefab_path),
                    "symbol_path": "Cube",
                    "component": "MeshRenderer",
                    "fields": {
                        "m_Materials": [
                            {
                                "fileID": 2100000,
                                "guid": "aaa",
                                "type": 2,
                            },
                        ],
                    },
                },
            ))

        self.assertNotEqual("SER003", result["code"])
        for diagnostic in result["diagnostics"]:
            self.assertNotEqual("property_not_found", diagnostic["detail"])
            self.assertNotEqual("component_not_found", diagnostic["detail"])


if __name__ == "__main__":
    unittest.main()
