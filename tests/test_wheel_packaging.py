"""Issue #309 — wheel-bundling regression test.

A wheel built from the source tree must ship every ``knowledge/*.md``
document under the package-internal ``_knowledge_files`` sub-path so
wheel installs keep the same packaged knowledge corpus that source-tree
checkouts validate.

The build is comparatively slow and depends on the project's build
tool being on PATH, so the test is opted in by the ``wheel_build``
pytest marker and skipped when ``uv`` (the project's build front-end)
is absent. The default ``scripts/run_unit_tests.py`` invocation does
not include this marker; runners that want the coverage select it
explicitly via ``-m wheel_build``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_SOURCE_DIR = PROJECT_ROOT / "knowledge"
BUNDLED_PREFIX = "prefab_sentinel/_knowledge_files/"

# ``wheel_build`` keeps the slow build out of opt-out marker selections;
# ``source_text_invariant`` keeps the test out of the mutmut baseline
# stats pass, where the test would fail because mutmut's ``mutants/``
# sandbox lacks ``README.md`` (referenced by ``[project].readme`` in
# ``pyproject.toml``) and the ``uv build --wheel`` subprocess therefore
# aborts before the manifest assertions can run.  The test compares the
# wheel manifest against ``knowledge/*.md`` — neither artifact lives
# under ``[tool.mutmut].paths_to_mutate`` so the assertions cannot
# observe any mutation applied to ``prefab_sentinel/``.
pytestmark = [pytest.mark.wheel_build, pytest.mark.source_text_invariant]


class _WheelBuildTestCase(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("uv") is None:
            self.skipTest(
                "uv not found on PATH; wheel-build coverage requires the project's build front-end."
            )
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _build_wheel(self, out_dir: Path) -> Path:
        completed = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            0,
            completed.returncode,
            msg=(
                "uv build --wheel failed:\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            ),
        )
        wheels = sorted(out_dir.glob("*.whl"))
        self.assertEqual(
            1,
            len(wheels),
            msg=f"expected exactly one wheel artifact under {out_dir}, found {wheels!r}",
        )
        return wheels[0]


class TestWheelKnowledgeBundling(_WheelBuildTestCase):
    """The wheel manifest mirrors source-tree knowledge markdown files."""

    def test_wheel_ships_every_knowledge_markdown_file(self) -> None:
        wheel = self._build_wheel(self._tmp)

        with zipfile.ZipFile(wheel) as zf:
            bundled_md = sorted(
                name
                for name in zf.namelist()
                if name.startswith(BUNDLED_PREFIX) and name.endswith(".md")
            )

        source_md = sorted(
            f"{BUNDLED_PREFIX}{p.name}"
            for p in KNOWLEDGE_SOURCE_DIR.glob("*.md")
        )

        self.assertEqual(
            source_md,
            bundled_md,
            msg=(
                "Wheel manifest under "
                f"{BUNDLED_PREFIX!r} must mirror "
                f"{KNOWLEDGE_SOURCE_DIR}/*.md exactly; missing or extra entries "
                "indicate the wheel source mapping drifted."
            ),
        )


class TestWheelInspectorProfileResources(_WheelBuildTestCase):
    def test_wheel_ships_only_the_canonical_inspector_profile_resources(self) -> None:
        wheel = self._build_wheel(self._tmp)

        with zipfile.ZipFile(wheel) as zf:
            names = sorted(zf.namelist())
        inspector_resources = [
            name
            for name in names
            if name.startswith("prefab_sentinel/resources/inspector-profile")
        ]
        production_profiles = [
            name
            for name in names
            if name.startswith("prefab_sentinel/resources/profiles/")
            and name.endswith(".json")
        ]
        skill_schema_copies = [
            name
            for name in names
            if "/skills/" in name
            and name.endswith(
                (
                    "inspector-profile.v1.schema.json",
                    "inspector-profile.template.json",
                )
            )
        ]

        self.assertEqual(
            (
                [
                    "prefab_sentinel/resources/inspector-profile.template.json",
                    "prefab_sentinel/resources/inspector-profile.v1.schema.json",
                ],
                [],
                [],
            ),
            (inspector_resources, production_profiles, skill_schema_copies),
            msg="wheel must expose one canonical schema/template and no production profile or skill copy",
        )



class TestWheelBridgeBundling(_WheelBuildTestCase):
    def test_wheel_ships_canonical_bridge_source(self) -> None:
        wheel = self._build_wheel(self._tmp)

        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())

        self.assertIn(
            "prefab_sentinel/_bridge_files/"
            "PrefabSentinel.UnityEditorControlBridge.cs",
            names,
            msg="wheel source mapping must retain the canonical Unity bridge",
        )


class TestWheelMetadataHygiene(_WheelBuildTestCase):
    def test_wheel_excludes_nested_serena_metadata(self) -> None:
        metadata_dir = PROJECT_ROOT / "tools" / "unity" / ".serena"
        metadata_dir_preexisted = metadata_dir.exists()
        metadata_dir.mkdir(parents=True, exist_ok=True)
        sentinel = metadata_dir / f"_wheel_hygiene_{self._tmp.name}.yml"
        sentinel.write_text("workspace-local: true\n", encoding="utf-8")

        try:
            wheel = self._build_wheel(self._tmp)
        finally:
            sentinel.unlink(missing_ok=True)
            if not metadata_dir_preexisted and not any(metadata_dir.iterdir()):
                metadata_dir.rmdir()

        with zipfile.ZipFile(wheel) as zf:
            leaked = sorted(
                name
                for name in zf.namelist()
                if ".serena" in name.split("/")
            )

        self.assertEqual(
            [],
            leaked,
            msg="wheel must not ship workspace-local .serena metadata",
        )


if __name__ == "__main__":
    unittest.main()
