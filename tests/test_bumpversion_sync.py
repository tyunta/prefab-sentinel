"""Bumpversion dry-run sync test (issue #338).

The project's canonical version literal is propagated to every
version-bearing artifact by ``[tool.bumpversion]`` in
``pyproject.toml``. After adding ``.codex-plugin/plugin.json``, the
file inventory grows from three to four artifacts. The dry-run
invocation must name each of the four documented file paths so a
silently-dropped artifact (e.g. forgetting to register the new Codex
manifest) is caught locally.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

import pytest

# The bump-my-version dry-run inspects the un-mutated ``[tool.bumpversion]``
# table in ``pyproject.toml``; the assertions cannot observe any mutation
# applied under ``prefab_sentinel/``.  The marker also keeps the test out
# of mutmut's baseline stats pass, where the sandbox lacks ``README.md``
# and the uv-driven editable build fails before bump-my-version runs.
pytestmark = pytest.mark.source_text_invariant

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

_EXPECTED_FILES = (
    "pyproject.toml",
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs",
)


class TestBumpversionSync(unittest.TestCase):
    """Bumpversion dry-run lists all four version-bearing artifacts."""

    def test_dry_run_patch_bump_names_each_documented_file(self) -> None:
        if (
            shutil.which("bump-my-version") is None
            and shutil.which("uv") is None
        ):
            self.skipTest("neither bump-my-version nor uv is available")

        # Prefer ``uv run`` so the call reuses the project's locked
        # dependency set; fall back to a direct ``bump-my-version``
        # invocation when uv is unavailable.
        if shutil.which("uv") is not None:
            # ``bump-my-version`` ships in the ``lint`` optional extra
            # so ``uv run --extra lint`` is the documented invocation
            # for environments that have not pre-synced the extra.
            cmd = [
                "uv",
                "run",
                "--extra",
                "lint",
                "bump-my-version",
                "bump",
                "patch",
                "--dry-run",
                "--verbose",
            ]
        else:
            cmd = [
                "bump-my-version",
                "bump",
                "patch",
                "--dry-run",
                "--verbose",
            ]

        result = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        combined = (result.stdout or "") + (result.stderr or "")

        # Pin every expected artifact path so a missing registration
        # surfaces with the offending file name.
        for path in _EXPECTED_FILES:
            self.assertIn(
                path,
                combined,
                f"bumpversion dry-run output does not name {path!r}: {combined!r}",
            )

        # Issue #144 — when bump-my-version cannot complete (e.g.
        # missing config syntax), surface returncode so the failure
        # mode is not silently the missing-file assertion above.
        self.assertEqual(
            0,
            result.returncode,
            f"bump-my-version dry-run exited with {result.returncode}: {combined}",
        )


if __name__ == "__main__":
    unittest.main()
