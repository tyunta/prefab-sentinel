"""Repository-synchrony invariants for the batchmode-removal refactor.

Issue #270 collapses the prefab-sentinel dispatch surface to a single
Editor Bridge file-IPC path.  The behavioural assertions live in the
runtime / patch-CLI / editor-bridge T1 suites.  This module is the
negative-coverage net for the deletions and source-text invariants
that cannot be expressed as a runtime behaviour:

* The Python smoke / benchmark / batchmode / live-Unity modules and
  scripts are absent from the tree.
* The C# runtime bridge and integration-tests sources contain no
  ``Application.isBatchMode`` branches, no ``RunFromJson`` entry
  method, no SceneView-absent skip lines, and no ``batchmode`` tokens
  in comments / string literals.
* ``pyproject.toml`` does not register the ``unity_live`` marker, and
  ``tests/test_udon_compile_coverage.py`` does not reference it.

The module reads the un-mutated repository tree, so mutmut cannot
observe its assertions.  The ``source_text_invariant`` marker is the
inclusion mechanism for the mutmut pytest selection's single ``-m``
filter (``-m "not source_text_invariant"``).
"""

from __future__ import annotations

import importlib
import re
import tomllib
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant

_REPO_ROOT = Path(__file__).resolve().parents[1]
_UNITY_DIR = _REPO_ROOT / "tools" / "unity"

# Modules that the refactor removes wholesale from the Python tree.
_DELETED_PYTHON_MODULES: tuple[str, ...] = (
    "prefab_sentinel.services.runtime_validation.batchmode",
    "prefab_sentinel.integration_tests",
    "prefab_sentinel.smoke_batch",
    "prefab_sentinel.smoke_batch_case",
    "prefab_sentinel.smoke_batch_runner",
    "prefab_sentinel.bridge_smoke",
    "prefab_sentinel.smoke_history",
    "prefab_sentinel.smoke_history_pipeline",
    "prefab_sentinel.smoke_history_report",
    "prefab_sentinel.smoke_history_stats",
)

# Scripts that the refactor removes wholesale from the scripts/ tree.
_DELETED_SCRIPTS: tuple[str, ...] = (
    "scripts/unity_bridge_smoke.py",
    "scripts/bridge_smoke_samples.py",
    "scripts/smoke_summary_to_csv.py",
    "scripts/benchmark_refs.py",
    "scripts/benchmark_history_to_csv.py",
    "scripts/benchmark_regression_report.py",
    "scripts/benchmark_samples.py",
    "scripts/unity_integration_tests.py",
)


class BatchmodeRemovalInvariants(unittest.TestCase):
    """Negative-coverage invariants for issue #270's deletion list."""

    def test_deleted_python_modules_are_not_importable(self) -> None:
        for module_name in _DELETED_PYTHON_MODULES:
            with self.assertRaises(ModuleNotFoundError) as ctx:
                importlib.import_module(module_name)
            self.assertIn(module_name.rsplit(".", 1)[-1], str(ctx.exception))

    def test_deleted_scripts_are_absent_from_tree(self) -> None:
        for relative_path in _DELETED_SCRIPTS:
            absolute = _REPO_ROOT / relative_path
            self.assertFalse(
                absolute.exists(),
                f"Deleted script must not exist: {relative_path}",
            )

    def test_pyproject_does_not_register_unity_live_marker(self) -> None:
        pyproject_path = _REPO_ROOT / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        markers = data["tool"]["pytest"]["ini_options"]["markers"]
        unity_live_entries = [m for m in markers if m.split(":", 1)[0].strip() == "unity_live"]
        self.assertEqual(
            [], unity_live_entries,
            "unity_live marker must be unregistered in pyproject.toml",
        )

    def test_udon_compile_coverage_test_does_not_reference_unity_live(self) -> None:
        path = _REPO_ROOT / "tests" / "test_udon_compile_coverage.py"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("unity_live", text)

    def test_runtime_bridge_source_contains_no_batchmode_branches(self) -> None:
        path = _UNITY_DIR / "PrefabSentinel.UnityRuntimeValidationBridge.cs"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("Application.isBatchMode", text)
        # ``RunFromJson`` is the batchmode-only ``-executeMethod`` entry
        # point; the surviving file-IPC entry is ``RunFromPaths``.
        run_from_json_declarations = re.findall(
            r"\bvoid\s+RunFromJson\b|\bRunFromJson\s*\(", text
        )
        self.assertEqual([], run_from_json_declarations)

    def test_integration_tests_source_contains_no_sceneview_absent_skips(self) -> None:
        path = _UNITY_DIR / "PrefabSentinel.UnityIntegrationTests.cs"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("TryGetActiveSceneView() == null", text)

    def test_csharp_bridge_files_contain_no_batchmode_token(self) -> None:
        target_files = (
            "PrefabSentinel.UnityRuntimeValidationBridge.cs",
            "PrefabSentinel.EditorBridge.cs",
            "PrefabSentinel.UnityIntegrationTests.cs",
        )
        for filename in target_files:
            path = _UNITY_DIR / filename
            text = path.read_text(encoding="utf-8")
            matches = re.findall(r"batchmode", text, flags=re.IGNORECASE)
            self.assertEqual(
                [], matches,
                f"{filename} must not reference batchmode (found {len(matches)} hit(s))",
            )

    def test_docs_reference_batchmode_only_in_historical_phrasing(self) -> None:
        """Every surviving ``batchmode`` mention in ``README.md`` /
        ``CLAUDE.md`` is in a sentence that explicitly marks the
        construct as removed (``削除済み`` or ``旧``).  Plain present-tense
        references would re-introduce the deleted dispatch path.
        """
        # Historical markers anchor each surviving mention to the
        # deletion event so future readers know batchmode is gone.
        historical_markers = ("削除済み", "旧")
        for filename in ("README.md", "CLAUDE.md"):
            path = _REPO_ROOT / filename
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if "batchmode" not in line.lower():
                    continue
                self.assertTrue(
                    any(marker in line for marker in historical_markers),
                    f"{filename}:{line_number} mentions batchmode without a "
                    f"historical marker ({historical_markers}): {line!r}",
                )

    def test_claude_md_contains_issue_268_migration_note(self) -> None:
        """``CLAUDE.md`` carries the issue #268 / post-#264 migration
        note for ``editor_force_scene_view_refresh``: the Prefab Stage
        ``NOT_FOUND`` behaviour and the ``editor_close_prefab`` workaround
        must both be discoverable. (#356 slimmed the README to a pointer
        doc; the tool operational note now lives in CLAUDE.md's Editor
        remote-operation rules.)
        """
        text = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("issue #268", text)
        self.assertIn("NOT_FOUND", text)
        self.assertIn("editor_close_prefab", text)


if __name__ == "__main__":
    unittest.main()
