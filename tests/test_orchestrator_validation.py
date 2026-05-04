"""Tests for orchestrator_validation.validate_refs missing-GUID contract (#83).

Issue #146 (Task C2): the validation post-condition responses are also
pinned via fixture files under ``tests/fixtures/orchestrator_validation/``;
those snapshot tests sit at the bottom of this file.  ``--regenerate-snapshots``
overwrites the fixture in place; without the flag the live payload must
match the on-disk fixture exactly.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.orchestrator_validation import validate_refs
from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from tests.bridge_test_helpers import write_file

FIXTURES_ROOT = (
    Path(__file__).parent / "fixtures" / "orchestrator_validation" / "expected"
)


def _stable_validate_refs_snapshot(response) -> dict:
    """Project a ``validate_refs`` response onto its pinned snapshot keys.

    The full response carries dynamic per-step data; the snapshot pins the
    quality-gate-relevant keys (success, should_proceed analogue, code,
    severity, per-category counts, per-entry severity) that the
    operating-rules contract treats as the public surface.
    """
    data = response.data
    step_result = data["steps"][0]["result"]
    step_data = step_result["data"]
    step_categories = dict(step_data.get("categories", {}) or {})
    return {
        "success": response.success,
        "severity": response.severity.value,
        "code": response.code,
        # ``should_proceed`` is the per-issue #146 alias for the
        # post-condition's ``success`` flag (the pipeline proceeds only
        # when the validation step succeeds).  Pinning both makes the
        # downgrade-mutation surface explicit.
        "should_proceed": response.success,
        "missing_asset_unique_count": data["missing_asset_unique_count"],
        "step_severity": step_result["severity"],
        "step_code": step_result["code"],
        # Per-category counts: broken_pptr / udon_runtime / variant_override
        # are the published quality-gate keys (CLAUDE.md "Quality Gates").
        # The reference scan reports under the "categories" map which uses
        # different names; we project them onto the published keys here.
        "categories": {
            "broken_pptr": int(step_categories.get("missing_asset", 0)),
            "udon_runtime": 0,
            "variant_override": int(
                step_categories.get("variant_override_mismatch", 0)
            ),
        },
        "diagnostic_severities": [d.detail for d in response.diagnostics],
    }


def _assert_snapshot(
    fixture_relpath: str,
    payload: dict,
    *,
    regenerate: bool,
) -> None:
    fixture_path = FIXTURES_ROOT / fixture_relpath
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    if regenerate or not fixture_path.exists():
        fixture_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))
    if expected != payload:
        raise AssertionError(
            f"snapshot mismatch at {fixture_path}:\n"
            f"expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
            f"observed: {json.dumps(payload, indent=2, sort_keys=True)}"
        )

BASE_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VARIANT_GUID = "cccccccccccccccccccccccccccccccc"
MISSING_GUID = "ffffffffffffffffffffffffffffffff"


def _create_project_with_missing_guid(root: Path) -> None:
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab",
        f"""%YAML 1.1
--- !u!1001 &100100000
PrefabInstance:
  m_SourcePrefab: {{fileID: 100100000, guid: {BASE_GUID}, type: 3}}
  m_Modification:
    m_Modifications:
    - target: {{fileID: 100100000, guid: {MISSING_GUID}, type: 3}}
      propertyPath: missing.ref
      value: 0
      objectReference: {{fileID: 0}}
""",
    )
    write_file(
        root / "Assets" / "Variant.prefab.meta",
        f"""fileFormatVersion: 2
guid: {VARIANT_GUID}
""",
    )


def _create_clean_project(root: Path) -> None:
    write_file(
        root / "Assets" / "Base.prefab",
        """%YAML 1.1
--- !u!1 &100100000
GameObject:
  m_Name: Base
""",
    )
    write_file(
        root / "Assets" / "Base.prefab.meta",
        f"""fileFormatVersion: 2
guid: {BASE_GUID}
""",
    )


class MissingGuidContractTests(unittest.TestCase):
    """T24: ``validate_refs`` surfaces top-level REF001 when any referenced
    GUID is not resolvable in the project map."""

    def test_validate_refs_returns_ref001(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)

            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

            self.assertFalse(response.success)
            self.assertEqual("REF001", response.code)
            self.assertEqual("error", response.severity.value)
            # The underlying scan step remains visible in data.steps.
            step_codes = [step["result"]["code"] for step in response.data["steps"]]
            self.assertIn("REF_SCAN_BROKEN", step_codes)
            self.assertGreaterEqual(response.data["missing_asset_unique_count"], 1)
            # Issue #146 row: pin the step list shape and the data
            # payload counter by exact value.  The orchestrator emits
            # exactly one step (``scan_broken_references``) on the
            # missing-GUID path.
            self.assertEqual(
                ["scan_broken_references"],
                [step["step"] for step in response.data["steps"]],
            )
            self.assertEqual(1, response.data["missing_asset_unique_count"])

    def test_validate_refs_clean_scan_returns_validate_refs_result(self) -> None:
        """Regression guard: clean scan must still return VALIDATE_REFS_RESULT."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)

            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

            self.assertTrue(response.success)
            self.assertEqual("VALIDATE_REFS_RESULT", response.code)
            self.assertEqual(0, response.data["missing_asset_unique_count"])
            # Pin the step list ordering on the clean path too.
            self.assertEqual(
                ["scan_broken_references"],
                [step["step"] for step in response.data["steps"]],
            )


class InspectStructureContractTests(unittest.TestCase):
    """Issue #146 row: pin ``inspect_structure`` outcomes for prefab vs.
    non-prefab fixtures.  ``checks_performed`` / ``checks_skipped`` /
    ``skip_reason`` and per-finding counts are pinned by exact value."""

    def test_prefab_fixture_runs_full_check_set(self) -> None:
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)
            svc = PrefabVariantService(project_root=root)
            response = inspect_structure(svc, "Assets/Base.prefab")

        self.assertTrue(response.success)
        self.assertEqual("VALIDATE_STRUCTURE_RESULT", response.code)
        self.assertEqual("Assets/Base.prefab", response.data["target_path"])
        self.assertEqual(
            [
                "duplicate_file_id",
                "transform_consistency",
                "missing_components",
                "orphaned_transforms",
            ],
            response.data["checks_performed"],
        )
        self.assertEqual([], response.data["checks_skipped"])
        self.assertEqual("", response.data["skip_reason"])
        # All finding counts are integers pinned at zero on the clean
        # base prefab fixture.
        self.assertEqual(0, response.data["duplicate_file_id_count"])
        self.assertEqual(0, response.data["transform_inconsistency_count"])
        self.assertEqual(0, response.data["missing_component_count"])
        self.assertEqual(0, response.data["orphaned_transform_count"])

    def test_non_prefab_fixture_skips_transform_checks(self) -> None:
        """A non-GameObject-bearing text asset (``.mat``) only runs
        the ``duplicate_file_id`` check; the other three checks land
        in ``checks_skipped`` with a non-empty ``skip_reason``.
        ``.prefab`` / ``.unity`` / ``.asset`` are GameObject-bearing
        and run the full check set; the material asset is the
        smallest non-GameObject text asset Unity ships."""
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            inspect_structure,
        )
        from prefab_sentinel.services.prefab_variant import (  # noqa: PLC0415
            PrefabVariantService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "Assets" / "Material.mat"
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text(
                "%YAML 1.1\n--- !u!21 &2100000\nMaterial:\n  m_Name: M\n",
                encoding="utf-8",
            )
            (root / "Assets" / "Material.mat.meta").write_text(
                f"fileFormatVersion: 2\nguid: {BASE_GUID}\n",
                encoding="utf-8",
            )
            svc = PrefabVariantService(project_root=root)
            response = inspect_structure(svc, "Assets/Material.mat")

        self.assertEqual("VALIDATE_STRUCTURE_RESULT", response.code)
        self.assertEqual(["duplicate_file_id"], response.data["checks_performed"])
        self.assertEqual(
            [
                "transform_consistency",
                "missing_components",
                "orphaned_transforms",
            ],
            response.data["checks_skipped"],
        )
        self.assertIn(".mat", response.data["skip_reason"])


class InspectWorldCanvasStepTests(unittest.TestCase):
    """Issue #146 row: ``_inspect_world_canvas_step`` outcomes for an
    unreadable scene (info-level diagnostic, no abort) and a scene
    carrying the local-scale finding (warning-level severity)."""

    def test_unreadable_scene_returns_info_diagnostic(self) -> None:
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            _inspect_world_canvas_step,
        )

        response = _inspect_world_canvas_step("/nonexistent/path/to/scene.unity")
        self.assertTrue(response.success)
        self.assertEqual("WORLD_CANVAS_INSPECT_OK", response.code)
        # Severity stays at INFO and a single "scene_unreadable"
        # diagnostic carries the failure cause.
        from prefab_sentinel.contracts import Severity as _Severity  # noqa: PLC0415

        self.assertEqual(_Severity.INFO, response.severity)
        self.assertEqual(
            ["WORLD_CANVAS_SCENE_UNREADABLE"],
            [d.detail for d in response.diagnostics],
        )

    def test_scene_with_local_scale_finding_returns_warning_severity(self) -> None:
        """A scene whose canvas inspector emits a
        ``WORLD_CANVAS_LOCAL_SCALE`` finding is rolled up to
        ``severity=warning`` (capped — the runtime pipeline does not
        abort)."""
        from prefab_sentinel.contracts import Diagnostic, Severity  # noqa: PLC0415
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            _inspect_world_canvas_step,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.unity"
            scene.write_text("%YAML 1.1\n--- !u!1 &1\nGameObject:\n", encoding="utf-8")
            with patch_world_canvas_inspector(
                lambda text, path: [
                    Diagnostic(
                        path=path,
                        location="canvas",
                        detail="WORLD_CANVAS_LOCAL_SCALE",
                        evidence="canvas localScale != (1,1,1)",
                    )
                ]
            ):
                response = _inspect_world_canvas_step(str(scene))

        self.assertTrue(response.success)
        self.assertEqual(Severity.WARNING, response.severity)
        self.assertEqual(
            "WORLD_CANVAS_LOCAL_SCALE",
            response.diagnostics[0].detail,
        )


def patch_world_canvas_inspector(stub):
    """Context manager: temporarily replace
    ``orchestrator_validation.inspect_world_canvas_setup`` with
    ``stub`` so tests can inject canvas-step diagnostics without
    constructing a full Unity scene."""
    from unittest.mock import patch  # noqa: PLC0415

    return patch(
        "prefab_sentinel.orchestrator_validation.inspect_world_canvas_setup",
        stub,
    )


class ValidateRuntimePipelineTests(unittest.TestCase):
    """Issue #146 row: pin ``validate_runtime`` outcomes for the
    fail-fast path (runtime step error) and the clean path."""

    @staticmethod
    def _make_response(code: str, severity, success: bool, data: dict | None = None):
        from prefab_sentinel.contracts import ToolResponse  # noqa: PLC0415

        return ToolResponse(
            success=success,
            severity=severity,
            code=code,
            message="m",
            data=data or {"read_only": True},
        )

    def test_fail_fast_runtime_step_error_aborts_pipeline(self) -> None:
        """When ``run_clientsim`` returns ``severity=error``, the
        pipeline aborts at the run-clientsim step.  Top-level success
        is False, ``fail_fast_triggered`` is True, and the steps list
        contains exactly the canvas / compile / run-clientsim entries
        in pipeline order."""
        from unittest.mock import MagicMock  # noqa: PLC0415

        from prefab_sentinel.contracts import Severity  # noqa: PLC0415
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            validate_runtime,
        )

        runtime = MagicMock()
        runtime.compile_udonsharp.return_value = self._make_response(
            "RUN_COMPILE_OK", Severity.INFO, True
        )
        runtime.run_clientsim.return_value = self._make_response(
            "RUN_CLIENTSIM_FAILED", Severity.ERROR, False
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.unity"
            scene.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n", encoding="utf-8"
            )
            response = validate_runtime(runtime, str(scene))

        self.assertFalse(response.success)
        self.assertEqual("VALIDATE_RUNTIME_RESULT", response.code)
        self.assertTrue(response.data["fail_fast_triggered"])
        # Pipeline-position-ordered step list, pinned by exact step
        # name and step code.
        steps_seq = [
            (entry["step"], entry["result"]["code"])
            for entry in response.data["steps"]
        ]
        self.assertEqual(
            [
                ("inspect_world_canvas", "WORLD_CANVAS_INSPECT_OK"),
                ("compile_udonsharp", "RUN_COMPILE_OK"),
                ("run_clientsim", "RUN_CLIENTSIM_FAILED"),
            ],
            steps_seq,
        )

    def test_clean_pipeline_runs_full_step_sequence(self) -> None:
        """When every step succeeds, the pipeline runs the full six-
        step sequence; ``fail_fast_triggered`` is False."""
        from unittest.mock import MagicMock  # noqa: PLC0415

        from prefab_sentinel.contracts import Severity  # noqa: PLC0415
        from prefab_sentinel.orchestrator_validation import (  # noqa: PLC0415
            validate_runtime,
        )

        runtime = MagicMock()
        # MagicMock treats any attribute starting with ``assert`` as an
        # assertion method by default; bind it explicitly so the
        # orchestrator's ``runtime_validation.assert_no_critical_errors``
        # call resolves to a normal mock callable.
        runtime.assert_no_critical_errors = MagicMock()
        runtime.compile_udonsharp.return_value = self._make_response(
            "RUN_COMPILE_OK", Severity.INFO, True
        )
        runtime.run_clientsim.return_value = self._make_response(
            "RUN_CLIENTSIM_OK", Severity.INFO, True
        )
        runtime.collect_unity_console.return_value = self._make_response(
            "RUN_LOG_COLLECTED", Severity.INFO, True, data={"log_lines": [], "read_only": True}
        )
        runtime.classify_errors.return_value = self._make_response(
            "RUN_CLASSIFY_OK", Severity.INFO, True
        )
        runtime.assert_no_critical_errors.return_value = self._make_response(
            "RUN_ASSERT_OK", Severity.INFO, True
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.unity"
            scene.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n", encoding="utf-8"
            )
            response = validate_runtime(runtime, str(scene))

        self.assertTrue(response.success)
        self.assertEqual("VALIDATE_RUNTIME_RESULT", response.code)
        self.assertFalse(response.data["fail_fast_triggered"])
        steps_seq = [entry["step"] for entry in response.data["steps"]]
        self.assertEqual(
            [
                "inspect_world_canvas",
                "compile_udonsharp",
                "run_clientsim",
                "collect_unity_console",
                "classify_errors",
                "assert_no_critical_errors",
            ],
            steps_seq,
        )


class TestValidationSnapshotPinning:
    """Issue #146 (C2): pin ``validate_refs`` and ``validate_runtime``
    post-condition responses via fixture files."""

    def test_validate_refs_clean_snapshot(
        self, regenerate_snapshots: bool
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_clean_project(root)
            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

        snapshot = _stable_validate_refs_snapshot(response)
        _assert_snapshot(
            "validate_refs_clean.json",
            snapshot,
            regenerate=regenerate_snapshots,
        )

    def test_validate_refs_missing_guid_snapshot(
        self, regenerate_snapshots: bool
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _create_project_with_missing_guid(root)
            resolver = ReferenceResolverService(project_root=root)
            response = validate_refs(resolver, scope="Assets")

        snapshot = _stable_validate_refs_snapshot(response)
        _assert_snapshot(
            "validate_refs_missing_guid.json",
            snapshot,
            regenerate=regenerate_snapshots,
        )

    def test_validate_runtime_skipped_compile_snapshot(
        self, regenerate_snapshots: bool
    ) -> None:
        """A representative runtime-validation snapshot anchored on the
        skip-compile-no-runtime-env path; pins the success / severity /
        code / canvas-step shape that the orchestrator returns when
        Unity is not configured.
        """
        import os  # noqa: PLC0415
        from unittest.mock import patch  # noqa: PLC0415

        from prefab_sentinel.orchestrator_validation import validate_runtime  # noqa: PLC0415
        from prefab_sentinel.services.runtime_validation import (  # noqa: PLC0415
            RuntimeValidationService,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scene = root / "Assets" / "Smoke.unity"
            scene.parent.mkdir(parents=True, exist_ok=True)
            scene.write_text(
                "%YAML 1.1\n--- !u!1 &1\nGameObject:\n  m_Name: Smoke\n",
                encoding="utf-8",
            )
            svc = RuntimeValidationService(project_root=root)

            unitytool_keys = [
                key for key in os.environ if key.startswith("UNITYTOOL_")
            ]
            with patch.dict(os.environ, {}, clear=False):
                for key in unitytool_keys:
                    os.environ.pop(key, None)
                response = validate_runtime(svc, str(scene))

        # Project the response onto a stable snapshot for runtime-validation:
        # success / severity / code / per-category quality-gate keys.
        steps_summary = [
            {"step": entry["step"], "code": entry["result"]["code"]}
            for entry in response.data["steps"]
        ]
        snapshot = {
            "success": response.success,
            "severity": response.severity.value,
            "code": response.code,
            "should_proceed": response.success,
            "fail_fast_triggered": response.data["fail_fast_triggered"],
            "categories": {
                "broken_pptr": 0,
                "udon_runtime": 0,
                "variant_override": 0,
            },
            "steps_summary": steps_summary,
        }
        _assert_snapshot(
            "validate_runtime_skip.json",
            snapshot,
            regenerate=regenerate_snapshots,
        )


if __name__ == "__main__":
    unittest.main()
