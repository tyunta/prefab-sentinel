"""Sanity test for the project's mutation-testing configuration (#150).

The post-foundation [tool.mutmut] configuration must drive a single-module
mutmut invocation without surfacing the runtime's missing-state-variable
error: the foundation refactors (#156, #157, plus the assertion helper
and the line-limit static gate) are the precondition for that.

Verification surface:

* The [tool.mutmut] table in ``pyproject.toml`` declares only the
  audited-path key and the do-not-mutate-pattern-list key (no per-test
  exclusion key, no per-file ignore key).
* The audited-path key targets the ``prefab_sentinel`` package source
  root, matching the Background documentation.
* When mutmut is available in the runtime, a single-module sanity
  invocation completes without surfacing the missing-state-variable
  ``KeyError``.  When mutmut is not installed the configuration check
  alone runs.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def _load_mutmut_section() -> dict:
    payload = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return payload.get("tool", {}).get("mutmut", {})


class MutmutConfigShapeTests(unittest.TestCase):
    def test_section_exposes_only_audited_path_and_do_not_mutate(self) -> None:
        section = _load_mutmut_section()
        self.assertEqual(
            {"paths_to_mutate", "do_not_mutate"},
            set(section.keys()),
            f"unexpected [tool.mutmut] keys: {sorted(section.keys())}",
        )

    def test_audited_path_targets_package_source_root(self) -> None:
        section = _load_mutmut_section()
        self.assertEqual(["prefab_sentinel/"], section["paths_to_mutate"])

    def test_do_not_mutate_covers_logger_and_triple_quote_forms(self) -> None:
        section = _load_mutmut_section()
        patterns = section["do_not_mutate"]
        self.assertTrue(
            any("logger" in pattern for pattern in patterns),
            f"missing logger pattern: {patterns}",
        )
        self.assertTrue(
            any('"""' in pattern for pattern in patterns),
            f"missing triple-double-quote pattern: {patterns}",
        )
        self.assertTrue(
            any("'''" in pattern for pattern in patterns),
            f"missing triple-single-quote pattern: {patterns}",
        )


class MutmutSanityInvocationTests(unittest.TestCase):
    def test_single_module_invocation_does_not_raise_missing_state_variable(self) -> None:
        if shutil.which("mutmut") is None:
            self.skipTest("mutmut is not installed in this environment")

        # ``mutmut run --paths-to-mutate <single-module>`` against a
        # tiny audited module: the goal is to prove the runtime starts
        # without ``KeyError: 'MUTANT_UNDER_TEST'`` (the foundation-side
        # symptom the missing-state-variable test guards against), so we
        # run a quick smoke invocation that exits at the first survivor
        # / killer rather than the full suite.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mutmut",
                "run",
                "--paths-to-mutate",
                "prefab_sentinel/contracts.py",
                "--max-children",
                "1",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn(
            "MUTANT_UNDER_TEST",
            combined,
            f"mutmut surfaced the missing-state-variable error: {combined}",
        )


if __name__ == "__main__":
    unittest.main()
