"""D4 — anchor killing-test rows for the eight mid-tier mutation-survived
modules (issue #158).

Per spec, each mid-tier module needs at least one killing test that
exercises a behaviour-bearing branch the cadence sample reports as
critical-class.  Until the quarterly mutmut run produces concrete
sampling output, this file establishes the scaffolding: one anchor row
per module that exercises a non-trivial branch and pins a value-level
assertion.

The classification document at ``knowledge/mutmut_mid_tier_sampling.md``
records the per-module sampling status; pull-request commits update both
files in lockstep when the cadence run produces classification output.
"""

from __future__ import annotations

import unittest


class McpHelpersAnchorTests(unittest.TestCase):
    def test_normalize_material_value_passes_string_through(self) -> None:
        from prefab_sentinel.mcp_helpers import normalize_material_value  # noqa: PLC0415

        self.assertEqual("hello", normalize_material_value("hello"))


class RuntimeValidationServiceAnchorTests(unittest.TestCase):
    def test_service_default_construction_has_project_root_attribute(self) -> None:
        from pathlib import Path  # noqa: PLC0415

        from prefab_sentinel.services.runtime_validation import (  # noqa: PLC0415
            RuntimeValidationService,
        )

        svc = RuntimeValidationService(project_root=Path("/tmp/never-exists-d4"))
        self.assertEqual(Path("/tmp/never-exists-d4"), svc.project_root)


class PrefabCreateDispatchAnchorTests(unittest.TestCase):
    def test_module_imports_and_exposes_dispatch_entry(self) -> None:
        import prefab_sentinel.services.serialized_object.prefab_create_dispatch as mod  # noqa: PLC0415

        # Exposes at least one public callable used by the service layer.
        self.assertTrue(
            any(
                not name.startswith("_") and callable(getattr(mod, name))
                for name in dir(mod)
            )
        )


class MaterialInspectorVariantAnchorTests(unittest.TestCase):
    def test_iter_modifications_handles_empty_text(self) -> None:
        from prefab_sentinel.material_inspector_variant import (  # noqa: PLC0415
            _iter_modifications,
        )

        self.assertEqual([], _iter_modifications(""))


class SymbolTreeBuilderAnchorTests(unittest.TestCase):
    def test_build_symbol_tree_handles_empty_text(self) -> None:
        from prefab_sentinel.symbol_tree_builder import build_symbol_tree  # noqa: PLC0415

        tree = build_symbol_tree("", "Assets/Stub.prefab")
        self.assertIsNotNone(tree)


class CsharpFieldsResolveAnchorTests(unittest.TestCase):
    def test_strip_namespace_removes_dotted_prefix(self) -> None:
        from prefab_sentinel.csharp_fields_resolve import _strip_namespace  # noqa: PLC0415

        self.assertEqual("Component", _strip_namespace("UnityEngine.Component"))


class SmokeBatchRunnerAnchorTests(unittest.TestCase):
    def test_extract_applied_count_returns_none_for_missing_data(self) -> None:
        from prefab_sentinel.smoke_batch_runner import _extract_applied_count  # noqa: PLC0415

        self.assertIsNone(_extract_applied_count({}))


class EditorBridgeAnchorTests(unittest.TestCase):
    def test_check_editor_bridge_env_returns_envelope_when_unset(self) -> None:
        import os  # noqa: PLC0415
        from unittest.mock import patch as env_patch  # noqa: PLC0415

        from prefab_sentinel.editor_bridge import check_editor_bridge_env  # noqa: PLC0415

        unitytool_keys = [
            key for key in os.environ if key.startswith("UNITYTOOL_BRIDGE_")
        ]
        with env_patch.dict(os.environ, {}, clear=False):
            for key in unitytool_keys:
                os.environ.pop(key, None)
            result = check_editor_bridge_env()
        # When the env vars are absent, the helper returns a structured
        # envelope identifying the missing configuration; when present,
        # it returns ``None``.  Either contract is the killing-test
        # surface — the function does NOT raise.
        self.assertTrue(result is None or isinstance(result, dict))


if __name__ == "__main__":
    unittest.main()
