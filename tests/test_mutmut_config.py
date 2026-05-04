"""Sanity test for the project's mutation-testing configuration.

Verifies that the ``[tool.mutmut]`` table in ``pyproject.toml`` matches
the operational contract documented in ``README.md`` §14.5:

* The table declares exactly four behavioral keys
  (``paths_to_mutate``, ``do_not_mutate``, ``also_copy``,
  ``pytest_add_cli_args_test_selection``) — no legacy per-file
  ``--ignore`` entries (issue #167) and no legacy ``-k`` filters
  (issues #154/#156/#157 retired them).
* The audited path targets the ``prefab_sentinel`` package source root.
* ``do_not_mutate`` covers logger-style calls and both triple-quoted
  string forms (issue #149).
* The pytest selection list contains a single
  ``-m not source_text_invariant`` marker filter and no per-file
  ignore entries (issue #167).
* The repository ignore file excludes the ``mutants/`` artifact
  directory (issue #166), and the ruff lint config carries the same
  exclusion via ``[tool.ruff].extend-exclude``.
* When mutmut is available in the runtime, a per-module sanity
  invocation in the supported single-target form (the only form the
  installed CLI accepts in 3.5+) exits with ``returncode == 0`` and
  produces output that contains none of the four documented historical
  regression strings (issue #165).  When mutmut is not installed the
  configuration checks alone run.
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
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def _load_mutmut_section() -> dict:
    return _load_pyproject().get("tool", {}).get("mutmut", {})


class MutmutConfigShapeTests(unittest.TestCase):
    def test_section_declares_required_keys(self) -> None:
        section = _load_mutmut_section()
        # The four keys above are the entire behavioral surface that
        # ``README.md`` §14.5 documents.  Any extra key signals drift
        # between the README and the live configuration.
        expected_keys = {
            "paths_to_mutate",
            "do_not_mutate",
            "also_copy",
            "pytest_add_cli_args_test_selection",
        }
        self.assertEqual(
            expected_keys,
            set(section.keys()),
            f"unexpected [tool.mutmut] keys: {sorted(section.keys())}",
        )
        cli_args = section["pytest_add_cli_args_test_selection"]
        for forbidden in (
            "test_module_line_limits",
            "test_every_module_line_limit",
            "test_compile_udonsharp_returns_skip_without_runtime_env",
            "test_activate_auto_detect_when_no_root_specified",
            "tests/test_unity_bridge_smoke.py",
            "tests/test_unity_patch_bridge.py",
        ):
            self.assertFalse(
                any(forbidden in entry for entry in cli_args),
                f"legacy filter still present in pytest_add_cli_args_test_selection: {forbidden}",
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

    def test_pytest_selection_uses_single_marker_filter(self) -> None:
        # The selection list must consist of the test root, a ``-m``
        # flag, and the marker expression — exactly three entries — and
        # contain no per-file ``--ignore=`` entries.  This pins issue
        # #167's "single marker filter as its sole exclusion mechanism".
        cli_args = _load_mutmut_section()["pytest_add_cli_args_test_selection"]
        self.assertEqual(
            ["tests/", "-m", "not source_text_invariant"],
            cli_args,
            f"unexpected pytest_add_cli_args_test_selection: {cli_args}",
        )
        for entry in cli_args:
            self.assertFalse(
                entry.startswith("--ignore="),
                f"per-file --ignore= entry remains in selection: {entry}",
            )

    def test_source_text_invariant_marker_is_registered(self) -> None:
        # The marker must be registered in ``[tool.pytest.ini_options]``
        # so pytest does not emit ``PytestUnknownMarkWarning``.
        markers = (
            _load_pyproject()
            .get("tool", {})
            .get("pytest", {})
            .get("ini_options", {})
            .get("markers", [])
        )
        self.assertTrue(
            any(entry.startswith("source_text_invariant") for entry in markers),
            f"source_text_invariant marker is not registered: {markers}",
        )

    def test_gitignore_excludes_mutants_artifact_directory(self) -> None:
        # Issue #166: ``mutants/`` is the mutmut artifact directory and
        # must never appear in version-control status output.
        text = GITIGNORE_PATH.read_text(encoding="utf-8")
        lines = {line.strip() for line in text.splitlines()}
        self.assertIn(
            "mutants/",
            lines,
            f".gitignore must exclude 'mutants/'; found lines: {sorted(lines)}",
        )

    def test_ruff_excludes_mutants_artifact_directory(self) -> None:
        # Issue #166: the ruff lint tool must skip the artifact tree so
        # it does not emit diagnostics from a transient working copy.
        ruff_section = _load_pyproject().get("tool", {}).get("ruff", {})
        excludes = ruff_section.get("extend-exclude", [])
        self.assertIn(
            "mutants/",
            excludes,
            f"[tool.ruff].extend-exclude must contain 'mutants/'; got {excludes}",
        )


class MutmutSanityInvocationTests(unittest.TestCase):
    # Four documented historical regression strings (issue #165):
    # * ``MUTANT_UNDER_TEST`` — the missing-state-variable identifier
    #   that mutmut's runtime raises when the test environment is not
    #   prepared (foundation-side symptom).
    # * ``KeyError`` — the corresponding key-lookup-error class name
    #   that surfaces in tracebacks for the same root cause.
    # * ``--no-input`` — the legacy invocation-flag rejection string
    #   that the older runtime emitted before ``mutmut run`` accepted
    #   the flag; its presence in combined output indicates a fallback
    #   to the legacy invocation path.
    # * ``no such option`` — any usage-error string indicating an
    #   invalid invocation form (mutmut 3.5+ surfaces "No such option:
    #   --foo" on click usage errors).  Matching is case-insensitive
    #   to absorb capitalisation drift across click versions.
    _FORBIDDEN_REGRESSION_STRINGS = (
        "MUTANT_UNDER_TEST",
        "KeyError",
        "--no-input",
        "no such option",
    )

    # Single-target sanity invocation.  ``prefab_sentinel/contracts.py``
    # is the smallest leaf module audited by the project and produces
    # the lowest mutant count, so the per-module form completes within
    # the timeout in any reasonable environment.
    _SANITY_TARGET = "prefab_sentinel/contracts.py"
    _TIMEOUT_SECONDS = 300

    def test_single_module_invocation_does_not_raise_missing_state_variable(self) -> None:
        if shutil.which("mutmut") is None:
            self.skipTest("mutmut is not installed in this environment")
        # When this test is collected by the pytest invocation that
        # mutmut starts inside its own ``mutants/`` working directory,
        # ``MUTANT_UNDER_TEST`` is set by the mutmut runtime.  Re-
        # invoking ``mutmut run`` from that context recurses
        # indefinitely.  Skip the subprocess call in that case — the
        # configuration shape assertions on the parent invocation are
        # the surface that matters.
        import os  # noqa: PLC0415
        if os.environ.get("MUTANT_UNDER_TEST"):
            self.skipTest("running inside mutmut; subprocess invocation would recurse")
        # Likewise, if the unit suite is being run by ``mutmut run``
        # from outside this test file — i.e. the working tree already
        # contains a ``mutants/`` artifact directory whose mutated
        # ``prefab_sentinel/__init__.py`` has installed the runtime
        # trampoline — re-entering ``mutmut run`` here would tangle
        # with the active mutmut session.  Skip in that case.
        if (PROJECT_ROOT / "mutants").exists():
            self.skipTest("a mutmut session is in progress; sanity test would tangle with it")

        # mutmut 3.5+ accepts only the positional ``MUTANT_NAMES`` form;
        # the legacy ``--paths-to-mutate`` flag is rejected by ``click``
        # with a usage error.  The ``[tool.mutmut].paths_to_mutate``
        # configuration entry is honoured by the runtime when the
        # positional form is omitted, but the per-module sanity
        # invocation explicitly names a single audited file so the
        # runtime narrows mutation generation immediately.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mutmut",
                "run",
                self._SANITY_TARGET,
                "--max-children",
                "1",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=self._TIMEOUT_SECONDS,
            check=False,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        # Defensive skip for the upstream mutmut 3.5+ bug whose
        # symptom is ``RuntimeError: context has already been set``
        # raised at ``set_start_method('fork')`` inside
        # ``mutmut/__main__.py`` when the trampoline import re-runs
        # the module in a forked child.  This is unrelated to the
        # silent-pass fix issue #165 targets; it indicates the
        # mutmut runtime cannot complete stats collection in this
        # environment.  Skip with a diagnostic message rather than
        # mask it as a regression-string failure.
        if (
            "context has already been set" in combined
            and "set_start_method" in combined
        ):
            self.skipTest(
                "mutmut runtime hit upstream multiprocessing.set_start_method "
                "double-init bug (combined output surfaces 'context has "
                f"already been set'): {combined}"
            )
        self.assertEqual(
            0,
            result.returncode,
            f"mutmut single-module invocation exited with {result.returncode}: {combined}",
        )
        for needle in self._FORBIDDEN_REGRESSION_STRINGS:
            self.assertNotIn(
                needle.lower(),
                combined.lower(),
                f"mutmut output surfaced a documented regression string '{needle}': {combined}",
            )


if __name__ == "__main__":
    unittest.main()
