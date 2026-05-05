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

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"
README_PATH = PROJECT_ROOT / "README.md"
QUARTERLY_TEMPLATE_PATH = PROJECT_ROOT / "docs" / "quarterly_mutmut_report_template.md"

# Make ``scripts/mutmut_score_report.py`` importable so the audited
# module list can be re-exported instead of duplicated in this file.
_SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from mutmut_score_report import AUDITED_MODULES as AUDITED_MODULES_FOR_HISTORY  # noqa: E402

# Issue #182 — exact suppression-pattern strings validated in PR #186.
# The configuration test below pins these by literal string comparison so
# any rename, duplicate, or accidental drift surfaces as a test failure.
PR186_SUPPRESSION_PATTERNS: tuple[str, ...] = (
    "*_text_cache.get*",
    "*_guid_map*",
    "*invalidate_*_cache*",
)


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

    def test_do_not_mutate_extends_with_documented_equivalent_patterns(
        self,
    ) -> None:
        """Issue #182 — three documented equivalent-mutation patterns
        appear verbatim in ``[tool.mutmut].do_not_mutate``.  The patterns
        target internal cache state in ``ReferenceResolverService`` whose
        mutations are semantically equivalent (cache hit/miss skip,
        guid-index re-read, cache invalidation) and would not strengthen
        the suite if mutated.
        """
        section = _load_mutmut_section()
        patterns = section["do_not_mutate"]
        for required in (
            "*_text_cache.get*",
            "*_guid_map*",
            "*invalidate_*_cache*",
        ):
            self.assertIn(
                required,
                patterns,
                f"missing equivalent-mutation pattern '{required}': {patterns}",
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


@pytest.mark.source_text_invariant
class QuarterlyTemplateTests(unittest.TestCase):
    """Issue #170 / #149 — the quarterly mutation-report template exists at
    the documented path and exposes the two structural sections the
    audited cadence relies on.  The README mutation operational-cadence
    subsection cross-references the template so a reader can land on the
    quarterly artefact directly from the operational documentation.

    Marked ``source_text_invariant`` because the assertions read
    ``docs/quarterly_mutmut_report_template.md`` and ``README.md`` from
    the repository tree — neither is part of the mutmut ``also_copy``
    surface (which is restricted to importable Python sources), so the
    class contributes no mutant-detection signal and would otherwise
    fail collection inside the ``mutants/`` working tree.
    """

    def test_quarterly_template_exists_and_has_suppression_impact_section(
        self,
    ) -> None:
        self.assertTrue(
            QUARTERLY_TEMPLATE_PATH.exists(),
            f"quarterly template not found at {QUARTERLY_TEMPLATE_PATH}",
        )
        text = QUARTERLY_TEMPLATE_PATH.read_text(encoding="utf-8")
        # Heading is locked to the documented wording so a future rename
        # surfaces as a failure here rather than as a silent doc drift.
        self.assertIn(
            "## 3. Suppression-impact section",
            text,
            "missing suppression-impact section heading",
        )
        # The per-pattern table header ("Suppression pattern ... Delta")
        # must be present so the section is structurally complete.
        self.assertIn("| Suppression pattern |", text)
        self.assertIn("Delta", text)

    def test_quarterly_template_has_per_audited_module_history_section(
        self,
    ) -> None:
        text = QUARTERLY_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "## 2. Per-audited-module mutation-score history",
            text,
            "missing per-audited-module score-history heading",
        )
        # The history section must enumerate every audited module by
        # dotted path so the reader can locate the row without grep.
        for module in AUDITED_MODULES_FOR_HISTORY:
            self.assertIn(
                module,
                text,
                f"per-audited-module section missing module row: {module}",
            )

    def test_readme_mutation_section_references_quarterly_template(
        self,
    ) -> None:
        text = README_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "docs/quarterly_mutmut_report_template.md",
            text,
            "README mutation section does not cross-reference the quarterly template path",
        )


class SuppressionPatternPinTests(unittest.TestCase):
    """Issue #182 — the three documented equivalent-mutation patterns
    appear verbatim in ``[tool.mutmut].do_not_mutate``.  This pins the
    strings by literal equality so a future rename / duplicate / accidental
    deletion surfaces as a test failure rather than as silent drift.
    """

    def test_each_pr186_suppression_pattern_is_pinned_by_literal_string(
        self,
    ) -> None:
        section = _load_mutmut_section()
        patterns = section["do_not_mutate"]
        # Each documented pattern appears at least once, by exact string.
        for required in PR186_SUPPRESSION_PATTERNS:
            self.assertIn(
                required,
                patterns,
                f"missing PR-#186 suppression pattern {required!r}: {patterns}",
            )
        # No duplicates of the documented patterns (drift catcher).
        for required in PR186_SUPPRESSION_PATTERNS:
            self.assertEqual(
                1,
                patterns.count(required),
                f"PR-#186 suppression pattern {required!r} appears more than once: {patterns}",
            )


class MutmutSanityInvocationTests(unittest.TestCase):
    """Per-module mutmut sanity invocation against ``contracts.py``.

    The test calls ``mutmut run`` on the smallest audited leaf module
    and asserts that none of the four documented historical regression
    strings (issue #165) appears in the combined stdout/stderr capture.

    Skip conditions (issue #144 — these are the conditions a developer
    reading a ``pytest --skipped`` summary can map back here without
    opening the test body):

    1. **Stale ``mutants/`` directory present at the repository root.**
       When the working tree already contains a ``mutants/`` artifact
       tree, a mutmut session is in progress (or was abandoned without
       cleanup) and re-entering ``mutmut run`` here would tangle with
       it.  The recovery is ``rm -rf mutants/`` from the repository
       root.
    2. **Upstream ``multiprocessing.set_start_method('fork')`` double-call
       ``RuntimeError`` is detected** in the combined output.  This
       indicates the mutmut runtime hit the upstream double-init bug
       (``context has already been set``); the failure is unrelated to
       the regression strings the test pins.
    3. **The ``mutmut`` binary is unavailable on PATH.**  In that case
       the configuration-shape assertions in
       :class:`MutmutConfigShapeTests` already cover the static surface
       of ``[tool.mutmut]``; the per-module sanity invocation has no
       runtime to drive.
    """

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
        if os.environ.get("MUTANT_UNDER_TEST"):
            self.skipTest("running inside mutmut; subprocess invocation would recurse")
        # mutmut's clean-test phase invokes pytest from inside the
        # ``mutants/`` working tree without ``MUTANT_UNDER_TEST`` set.
        # Detect that case by checking whether this test file is
        # itself resident inside a ``mutants/`` directory: if so, we
        # are being collected by the outer mutmut session and the
        # subprocess would spawn a nested ``mutants/mutants/`` tree.
        if "mutants" in PROJECT_ROOT.parts:
            self.skipTest("collected from inside mutants/; subprocess invocation would recurse")
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


class MutmutSanityDocstringTests(unittest.TestCase):
    """Issue #144 — the sanity test class's docstring enumerates each
    documented skip condition so a developer reading a
    ``pytest --skipped`` summary can locate the cause without reading
    the test body.
    """

    def test_class_docstring_lists_three_skip_conditions(self) -> None:
        docstring = MutmutSanityInvocationTests.__doc__ or ""
        # Skip condition 1: stale ``mutants/`` directory at repo root.
        self.assertIn("mutants/", docstring)
        self.assertRegex(
            docstring,
            r"(?is)stale.*mutants.*(repository|repo).*root",
        )
        # Skip condition 2: upstream multiprocessing double-call.
        self.assertIn("set_start_method", docstring)
        self.assertIn("fork", docstring)
        self.assertIn("RuntimeError", docstring)
        # Skip condition 3: mutmut binary unavailable on PATH.
        self.assertRegex(docstring, r"(?is)mutmut.*PATH")
        # Cleanup string: literal recovery instruction.
        self.assertIn("rm -rf mutants/", docstring)


class RunUnitTestsStaleMutantsPreflightTests(unittest.TestCase):
    """Issue #174 — ``scripts/run_unit_tests.py`` aborts with a distinct
    exit code when a stale ``mutants/`` directory is present at the
    repository root and the mutmut child indicator is unset; passes
    through to the parallel-runner dispatch when the indicator is set.
    """

    _STALE_MUTANTS_EXIT_CODE = 3
    _MISSING_RUNNER_EXIT_CODE = 2

    def _import_entrypoint(self):
        from scripts import run_unit_tests  # noqa: PLC0415

        return run_unit_tests

    def test_stale_mutants_directory_aborts_runner(self) -> None:
        run_unit_tests = self._import_entrypoint()
        captured_stderr: list[str] = []

        def fake_print(*args: object, **kwargs: object) -> None:
            captured_stderr.append(" ".join(str(arg) for arg in args))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "mutants").mkdir()
            (root / "tests").mkdir()
            with (
                mock.patch.dict(os.environ, {}, clear=False),
                mock.patch.object(run_unit_tests, "ROOT_DIR", root),
                mock.patch.object(run_unit_tests, "print", fake_print),
            ):
                os.environ.pop("MUTANT_UNDER_TEST", None)
                rc = run_unit_tests.main([])
        self.assertEqual(self._STALE_MUTANTS_EXIT_CODE, rc)
        self.assertNotEqual(self._MISSING_RUNNER_EXIT_CODE, rc)
        self.assertNotEqual(0, rc)
        self.assertNotEqual(1, rc)
        joined = "\n".join(captured_stderr)
        self.assertIn("mutants", joined)
        self.assertIn("rm -rf mutants/", joined)

    def test_mutmut_child_indicator_allows_runner_passthrough(self) -> None:
        run_unit_tests = self._import_entrypoint()
        sentinel_returncode = 17

        class _FakeCompletedProcess:
            returncode = sentinel_returncode

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "mutants").mkdir()
            (root / "tests").mkdir()
            with (
                mock.patch.dict(
                    os.environ, {"MUTANT_UNDER_TEST": "yes"}, clear=False
                ),
                mock.patch.object(run_unit_tests, "ROOT_DIR", root),
                # ``find_spec`` returns ``None`` when ``unittest_parallel`` is
                # not installed.  The passthrough path under test runs *after*
                # that guard, so the test must short-circuit it with a truthy
                # spec object before reaching the mocked subprocess dispatch.
                mock.patch.object(
                    run_unit_tests.importlib.util,
                    "find_spec",
                    return_value=object(),
                ),
                mock.patch.object(
                    run_unit_tests.subprocess,
                    "run",
                    return_value=_FakeCompletedProcess(),
                ),
            ):
                rc = run_unit_tests.main([])
        self.assertEqual(sentinel_returncode, rc)


if __name__ == "__main__":
    unittest.main()
