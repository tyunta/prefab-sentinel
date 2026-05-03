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

Issue #124: the unresolved-before-value resolver returns members of the
typed ``UnresolvedReason`` StrEnum, and the preview-warning extractor
recognizes those members by isinstance check rather than by string
prefix.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import prefab_sentinel.services.serialized_object as serialized_object_pkg
from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.prefab_variant import PrefabVariantService
from prefab_sentinel.services.serialized_object import SerializedObjectService
from prefab_sentinel.services.serialized_object.before_cache import (
    UnresolvedReason,
    resolve_before_value,
)
from prefab_sentinel.services.serialized_object.patch_preview import (
    soft_warnings_for_preview,
)
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


_VARIANT_YAML = (
    "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n"
    "--- !u!1001 &100100000\n"
    "PrefabInstance:\n"
    "  m_SourcePrefab: {fileID: 100100000, "
    "guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}\n"
    "  m_Modifications:\n"
    "  - target: {fileID: 42, "
    "guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}\n"
    "    propertyPath: m_Materials.Array.data[0]\n"
    "    value: \n"
    "    objectReference: {fileID: 2100000, "
    "guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}\n"
)


def _make_variant_project(tmp: str) -> Path:
    """Variant prefab fixture for the unresolved-reason resolver tests."""
    project_root = Path(tmp)
    assets = project_root / "Assets"
    assets.mkdir(parents=True)
    (assets / "Variant.prefab").write_text(_VARIANT_YAML)
    (assets / "Variant.prefab.meta").write_text(
        "guid: cccccccccccccccccccccccccccccccc\n"
    )
    return project_root


class UnresolvedReasonResolverTests(unittest.TestCase):
    """Issue #124 — resolver returns ``UnresolvedReason`` enum members.

    Each unresolved branch of ``resolve_before_value`` returns a typed
    member rather than a free-form string, so the preview-warning
    extractor can distinguish the unresolved vocabulary from a resolved
    string by isinstance check.
    """

    def test_no_variant_resolver_when_prefab_variant_unbound(self) -> None:
        """No PrefabVariantService bound → ``NO_VARIANT_RESOLVER`` member."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _make_variant_project(tmp)
            svc = SerializedObjectService(project_root=project_root)
            result = resolve_before_value(
                svc, "Assets/Variant.prefab", "42",
                "m_Materials.Array.data[0]",
            )
        self.assertIs(result, UnresolvedReason.NO_VARIANT_RESOLVER)

    def test_file_unreadable_when_target_path_missing(self) -> None:
        """Target file deleted between resolution attempts → ``FILE_UNREADABLE``."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _make_variant_project(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            (project_root / "Assets" / "Variant.prefab").unlink()
            result = resolve_before_value(
                svc, "Assets/Variant.prefab", "42",
                "m_Materials.Array.data[0]",
            )
        self.assertIs(result, UnresolvedReason.FILE_UNREADABLE)

    def test_not_a_variant_when_target_is_base_prefab(self) -> None:
        """Target whose contents are a base prefab → ``NOT_A_VARIANT``."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            assets = project_root / "Assets"
            assets.mkdir()
            base = assets / "Base.prefab"
            base.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Root\n"
            )
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            result = resolve_before_value(
                svc, "Assets/Base.prefab", "1", "m_Name",
            )
        self.assertIs(result, UnresolvedReason.NOT_A_VARIANT)

    def test_not_a_variant_repeats_on_same_service_instance(self) -> None:
        """Issue #130: calling twice on the same service instance against a
        base-prefab target must return ``NOT_A_VARIANT`` both times.  The
        previous implementation cached an empty dict for the non-Variant
        branch, which the early "cache empty" guard mis-routed into
        ``EMPTY_CHAIN`` on the second call.
        """
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            assets = project_root / "Assets"
            assets.mkdir()
            base = assets / "Base.prefab"
            base.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Root\n"
            )
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            first = resolve_before_value(
                svc, "Assets/Base.prefab", "1", "m_Name",
            )
            second = resolve_before_value(
                svc, "Assets/Base.prefab", "1", "m_Name",
            )
        self.assertIs(first, UnresolvedReason.NOT_A_VARIANT)
        self.assertIs(second, UnresolvedReason.NOT_A_VARIANT)

    def test_empty_chain_when_chain_resolves_to_empty(self) -> None:
        """Chain resolves but the value map is empty → ``EMPTY_CHAIN``."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _make_variant_project(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            with patch.object(
                pv, "resolve_chain_values", return_value={},
            ), patch.object(
                pv, "resolve_chain_class_map", return_value={},
            ):
                result = resolve_before_value(
                    svc, "Assets/Variant.prefab", "42",
                    "m_Materials.Array.data[0]",
                )
        self.assertIs(result, UnresolvedReason.EMPTY_CHAIN)

    def test_type_not_found_when_component_name_absent(self) -> None:
        """Component name absent from the chain class map → ``TYPE_NOT_FOUND``."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _make_variant_project(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            with patch.object(
                pv, "resolve_chain_values",
                return_value={"42:m_IsActive": "1"},
            ), patch.object(
                pv, "resolve_chain_class_map",
                return_value={"42": "MeshRenderer"},
            ):
                result = resolve_before_value(
                    svc, "Assets/Variant.prefab", "Camera", "m_IsActive",
                )
        self.assertIs(result, UnresolvedReason.TYPE_NOT_FOUND)

    def test_ambiguous_type_when_component_name_repeats(self) -> None:
        """Component name occurs more than once in the chain → ``AMBIGUOUS_TYPE``."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _make_variant_project(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            with patch.object(
                pv, "resolve_chain_values",
                return_value={"42:m_IsActive": "1", "43:m_IsActive": "0"},
            ), patch.object(
                pv, "resolve_chain_class_map",
                return_value={"42": "MeshRenderer", "43": "MeshRenderer"},
            ):
                result = resolve_before_value(
                    svc, "Assets/Variant.prefab", "MeshRenderer", "m_IsActive",
                )
        self.assertIs(result, UnresolvedReason.AMBIGUOUS_TYPE)

    def test_path_not_found_when_property_path_absent(self) -> None:
        """Property path absent from the resolved chain values → ``PATH_NOT_FOUND``."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _make_variant_project(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            result = resolve_before_value(
                svc, "Assets/Variant.prefab", "42", "m_NoSuchProperty",
            )
        self.assertIs(result, UnresolvedReason.PATH_NOT_FOUND)

    def test_resolved_value_is_returned_as_plain_string(self) -> None:
        """Resolved branch returns a plain ``str``, not an enum member."""
        with tempfile.TemporaryDirectory() as tmp:
            project_root = _make_variant_project(tmp)
            pv = PrefabVariantService(project_root=project_root)
            svc = SerializedObjectService(
                project_root=project_root, prefab_variant=pv,
            )
            result = resolve_before_value(
                svc, "Assets/Variant.prefab", "42",
                "m_Materials.Array.data[0]",
            )
        self.assertIsInstance(result, str)
        self.assertNotIsInstance(result, UnresolvedReason)
        self.assertIn("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", result)


class PreviewWarningTypedDetectionTests(unittest.TestCase):
    """Issue #124 — ``soft_warnings_for_preview`` recognizes the typed
    vocabulary by isinstance and ignores arbitrary strings."""

    def test_recognizes_typed_unresolved_reason(self) -> None:
        """A preview row whose ``before`` is an enum member emits a diagnostic
        whose evidence names the specific reason."""
        preview = [
            {
                "op": "set",
                "component": "MeshRenderer",
                "path": "m_IsActive",
                "before": UnresolvedReason.TYPE_NOT_FOUND,
                "after": "0",
            },
        ]
        warnings = soft_warnings_for_preview("Assets/X.prefab", preview)
        self.assertEqual(1, len(warnings))
        self.assertIsInstance(warnings[0], Diagnostic)
        self.assertEqual("unresolved_before_value", warnings[0].detail)
        self.assertIn(UnresolvedReason.TYPE_NOT_FOUND.value, warnings[0].evidence)

    def test_ignores_arbitrary_string_before_value(self) -> None:
        """Arbitrary strings (resolved values) do not emit warnings."""
        preview = [
            {
                "op": "set",
                "component": "MeshRenderer",
                "path": "m_IsActive",
                "before": "1",
                "after": "0",
            },
        ]
        warnings = soft_warnings_for_preview("Assets/X.prefab", preview)
        self.assertEqual(0, len(warnings))


if __name__ == "__main__":
    unittest.main()
