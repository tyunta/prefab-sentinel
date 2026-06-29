"""Sanity test for the project's mutation-testing configuration.

Verifies that the ``[tool.mutmut]`` table in ``pyproject.toml`` matches
the operational contract documented in ``TESTING.md`` Mutation testing section:

* The table declares exactly four behavioral keys
  (``paths_to_mutate``, ``do_not_mutate``, ``also_copy``,
  ``pytest_add_cli_args_test_selection``) — no legacy per-file
  ``--ignore`` entries (issue #167) and no legacy ``-k`` filters
  (issues #154/#156/#157 retired them).
* The audited path targets the ``prefab_sentinel`` package source root.
* ``do_not_mutate`` carries no entries (issue #28): mutmut 3.5.0
  evaluates it as a file-path ``fnmatch`` glob, so construct-style
  patterns are inert and a file-path entry would narrow the audited
  surface.
* The pytest selection list contains a single
  ``-m not source_text_invariant`` marker filter and no per-file
  ignore entries (issue #167).
* The repository ignore file excludes the ``mutants/`` artifact
  directory (issue #166), and the ruff lint config carries the same
  exclusion via ``[tool.ruff].extend-exclude``.
* When mutmut is available in the runtime, a per-module sanity
  invocation in the supported single-target form — a dotted
  mutant-name glob, the only form mutmut 3.5.0 accepts (issue #29) —
  exits with ``returncode == 0`` and produces output that contains
  none of the documented forbidden strings: the four historical
  regression strings (issue #165) plus the ``AssertionError`` signature
  the broken file-path-positional form raises.  When mutmut is not
  installed the configuration checks alone run.
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
from typing import Any, cast
from unittest import mock

import pytest

from scripts.mutmut_score_report import AUDITED_MODULES as AUDITED_MODULES_FOR_HISTORY
from tests._typing_helpers import require_mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"
CONTRIBUTING_PATH = PROJECT_ROOT / "CONTRIBUTING.md"
TESTING_PATH = PROJECT_ROOT / "TESTING.md"
QUARTERLY_TEMPLATE_PATH = PROJECT_ROOT / "docs" / "quarterly_mutmut_report_template.md"


def _load_pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))


def _load_mutmut_section() -> dict[str, Any]:
    tool = require_mapping(_load_pyproject().get("tool", {}), "pyproject tool")
    return require_mapping(tool.get("mutmut", {}), "mutmut config")


def _import_run_unit_tests():
    # Local import shared by every preflight test class without mutating the
    # runner module's import surface.
    from scripts import run_unit_tests  # noqa: PLC0415

    return run_unit_tests


@pytest.mark.source_text_invariant
class MutmutConfigShapeTests(unittest.TestCase):
    """Issue #167 / #222 — reads ``pyproject.toml``'s ``[tool.mutmut]``
    section verbatim from the un-mutated tree; no mutation under
    ``prefab_sentinel/`` can affect what this class observes.
    """

    def test_section_declares_required_keys(self) -> None:
        section = _load_mutmut_section()
        # The four keys above are the entire behavioral surface that
        # ``TESTING.md`` Mutation testing section documents.  Any extra
        # key signals drift between the doc and the live configuration.
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

    def test_do_not_mutate_carries_no_entries(self) -> None:
        # Issue #28: mutmut 3.5.0 evaluates ``do_not_mutate`` with
        # ``fnmatch`` against source *file paths*, never against code
        # structure or mutant names. The previous construct-style globs
        # (``*logger.*``, ``*"""*`` …) matched no ``.py`` path and were
        # entirely inert; a file-path entry would instead exclude a whole
        # audited module (a Non-Goal). The list is therefore empty.
        section = _load_mutmut_section()
        self.assertEqual(
            [],
            section["do_not_mutate"],
            f"do_not_mutate must carry no entries; found: {section['do_not_mutate']}",
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
        # Concretise the previous ``assertTrue(any(...))`` form: exactly
        # one ``source_text_invariant`` marker is documented in
        # pyproject.toml, so the count is deterministic. A second
        # accidental registration or a silent removal both trip this
        # assertion.
        matching = [entry for entry in markers if entry.startswith("source_text_invariant")]
        self.assertEqual(
            1,
            len(matching),
            f"source_text_invariant marker registration unexpected: {matching}",
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
    audited cadence relies on.  The ``TESTING.md`` Mutation testing
    section cross-references the template so a reader can land on the
    quarterly artefact directly from the test-strategy documentation.
    (Issue #354 migrated the README test-strategy section — including
    the mutation cadence — into ``TESTING.md``, so the cross-reference
    now lives there.)

    Marked ``source_text_invariant`` because the assertions read
    ``docs/quarterly_mutmut_report_template.md`` and ``TESTING.md`` from
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
        # Issue #28: the section documents ``do_not_mutate`` as a
        # file-path exclusion list and carries the survivor-classification
        # subsection that records trivial construct-level survivors (the
        # mechanism that replaced the inert construct-glob suppression).
        self.assertIn("| do_not_mutate entry (file-path glob) |", text)
        self.assertIn("### 3.1 Survivor classification", text)

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

    def test_testing_doc_mutation_section_references_quarterly_template(
        self,
    ) -> None:
        text = TESTING_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "docs/quarterly_mutmut_report_template.md",
            text,
            "TESTING.md mutation section does not cross-reference the quarterly template path",
        )


_MUTMUT_SANITY_REQUIRED_PATHS = (
    "pyproject.toml",
    "prefab_sentinel",
    "tests",
    "scripts",
    "tools",
    "knowledge",
)
_MUTMUT_SANITY_COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    ".pytest_cache",
    "mutants",
    "*.pyc",
)


def _prepare_mutmut_sanity_project(source_root: Path, destination_root: Path) -> None:
    for relative_path in _MUTMUT_SANITY_REQUIRED_PATHS:
        source = source_root / relative_path
        if not source.exists():
            raise AssertionError(
                "fixture preparation failed: required path missing: "
                f"{source}"
            )
        destination = destination_root / relative_path
        if source.is_dir():
            shutil.copytree(source, destination, ignore=_MUTMUT_SANITY_COPY_IGNORE)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


class MutmutSanityInvocationTests(unittest.TestCase):
    """Per-module mutmut sanity invocation against ``contracts.py``.

    The test calls ``mutmut run`` on the smallest audited leaf module —
    using the dotted mutant-name-glob form mutmut 3.5.0 accepts — and
    asserts that none of the documented forbidden strings appears in the
    combined stdout/stderr capture: the four historical regression
    strings (issue #165) plus the ``AssertionError`` "nothing matches"
    signature that the broken file-path-positional invocation raises
    (issue #29).

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

    # Forbidden strings — any appearance in combined output fails the test.
    # Four historical regression strings (issue #165):
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
    #   --foo" on click usage errors).
    # Plus the issue #29 signature:
    # * ``Filtered for specific mutants, but nothing matches`` — the
    #   ``AssertionError`` mutmut 3.5.0 raises when a positional argument
    #   matches no mutant. A file-path positional (the pre-#29 broken
    #   form) never matches a dotted mutant name, so this string appears
    #   if and only if the broken invocation form is reintroduced.
    # Matching is case-insensitive to absorb capitalisation drift.
    _FORBIDDEN_REGRESSION_STRINGS = (
        "MUTANT_UNDER_TEST",
        "KeyError",
        "--no-input",
        "no such option",
        "Filtered for specific mutants, but nothing matches",
    )

    # Single-module sanity invocation.  mutmut 3.5.0's positional argument
    # is a mutant-name glob, not a file path; ``prefab_sentinel.contracts.*``
    # selects every mutant of the smallest audited leaf module.  A file-path
    # positional (``prefab_sentinel/contracts.py``) matches no mutant name
    # and aborts with the issue #29 ``AssertionError``.
    _SANITY_TARGET = "prefab_sentinel.contracts.*"
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

        # mutmut 3.5.0's positional ``MUTANT_NAMES`` argument is a glob
        # matched against dotted mutant names (``<module>.<func>__mutmut_N``),
        # not a file path.  The single-module sanity invocation passes the
        # dotted module-name glob ``prefab_sentinel.contracts.*`` so the
        # run filters to that module's mutants; passing a file path here
        # would abort with the issue #29 ``AssertionError``.
        with tempfile.TemporaryDirectory(prefix="prefab-sentinel-mutmut-") as raw:
            sanity_root = Path(raw) / "project"
            _prepare_mutmut_sanity_project(PROJECT_ROOT, sanity_root)
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
                cwd=str(sanity_root),
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


class MutmutSanityIsolationTests(unittest.TestCase):
    def _run_sanity_test(self) -> unittest.TestResult:
        from tests import test_mutmut_config as tmc  # noqa: PLC0415

        test = tmc.MutmutSanityInvocationTests(
            "test_single_module_invocation_does_not_raise_missing_state_variable"
        )
        result = unittest.TestResult()
        test.run(result)
        return result

    def test_single_module_invocation_uses_temporary_project_root(self) -> None:
        from tests import test_mutmut_config as tmc  # noqa: PLC0415

        prepared_roots: list[tuple[Path, Path]] = []
        recorded_cwds: list[Path] = []

        class _FakeCompletedProcess:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        def _fake_prepare(source_root: Path, destination_root: Path) -> None:
            prepared_roots.append((source_root, destination_root))
            destination_root.mkdir(parents=True)

        def _fake_run(*_args: object, **kwargs: object) -> _FakeCompletedProcess:
            cwd = cast(str | os.PathLike[str], kwargs["cwd"])
            recorded_cwds.append(Path(cwd))
            (recorded_cwds[-1] / "mutants").mkdir()
            return _FakeCompletedProcess()

        with (
            mock.patch.object(tmc.shutil, "which", return_value="/fake/mutmut"),
            mock.patch.object(tmc.subprocess, "run", side_effect=_fake_run),
            mock.patch.object(tmc.shutil, "rmtree") as rmtree_mock,
            mock.patch(
                "tests.test_mutmut_config._prepare_mutmut_sanity_project",
                side_effect=_fake_prepare,
                create=True,
            ),
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("MUTANT_UNDER_TEST", None)
            result = self._run_sanity_test()

        self.assertEqual(
            (0, 0, 0),
            (len(result.errors), len(result.failures), len(result.skipped)),
            f"unexpected outcome: errors={result.errors!r} "
            f"failures={result.failures!r} skipped={result.skipped!r}",
        )
        self.assertEqual(1, len(prepared_roots), prepared_roots)
        self.assertEqual(1, len(recorded_cwds), recorded_cwds)
        source_root, prepared_root = prepared_roots[0]
        self.assertEqual(tmc.PROJECT_ROOT, source_root)
        self.assertEqual(prepared_root, recorded_cwds[0])
        self.assertNotEqual(tmc.PROJECT_ROOT, recorded_cwds[0])
        self.assertFalse((tmc.PROJECT_ROOT / "mutants").exists())
        cleanup_targets = [Path(call.args[0]) for call in rmtree_mock.call_args_list]
        self.assertNotIn(tmc.PROJECT_ROOT / "mutants", cleanup_targets)

    def test_missing_required_copy_source_fails_with_fixture_preparation_path(
        self,
    ) -> None:
        from tests import test_mutmut_config as tmc  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as raw:
            project_root = Path(raw)

            class _FakeCompletedProcess:
                returncode = 0
                stdout = "ok\n"
                stderr = ""

            with (
                mock.patch.object(tmc, "PROJECT_ROOT", project_root),
                mock.patch.object(tmc.shutil, "which", return_value="/fake/mutmut"),
                mock.patch.object(
                    tmc.subprocess,
                    "run",
                    return_value=_FakeCompletedProcess(),
                ),
                mock.patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("MUTANT_UNDER_TEST", None)
                result = self._run_sanity_test()

        self.assertEqual(1, len(result.failures), result.failures)
        self.assertEqual(0, len(result.errors), result.errors)
        failure_text = result.failures[0][1]
        self.assertIn("fixture preparation", failure_text)
        self.assertIn(str(project_root / "pyproject.toml"), failure_text)


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

    def test_stale_mutants_directory_aborts_runner(self) -> None:
        run_unit_tests = _import_run_unit_tests()
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
        # Issue #222 Phase 1 dominated-assertion cleanup: the
        # ``assertEqual`` above already pins ``rc`` to
        # ``STALE_MUTANTS_EXIT_CODE``, which strictly implies the three
        # previously-present ``assertNotEqual`` rows against
        # ``MISSING_RUNNER_EXIT_CODE`` / ``0`` / ``1`` (those constants
        # are distinct from the stale-mutants exit code by construction).
        # The dominated rows added no detection signal, so they are
        # absent here per the project's Necessity Check.
        self.assertEqual(self._STALE_MUTANTS_EXIT_CODE, rc)
        joined = "\n".join(captured_stderr)
        self.assertIn("mutants", joined)
        self.assertIn("rm -rf mutants/", joined)

    def test_mutmut_child_indicator_allows_runner_passthrough(self) -> None:
        run_unit_tests = _import_run_unit_tests()
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


class RunUnitTestsMcpExtraPreflightTests(unittest.TestCase):
    """Issue #217 — ``scripts/run_unit_tests.py`` aborts with a single
    actionable hint when the ``mcp`` optional dependency is not importable
    and the documented opt-out env var is unset; passes through when the
    opt-out env var is set or the dependency is installed.  Stale-artifact
    preflight takes precedence over the mcp-extra preflight so the more
    proximal failure mode surfaces first.
    """

    _MISSING_MCP_EXTRA_EXIT_CODE = 4
    _STALE_MUTANTS_EXIT_CODE = 3
    _MISSING_RUNNER_EXIT_CODE = 2

    def _make_fake_find_spec(self, run_unit_tests, *, mcp_present: bool):
        def _fake(name: str):
            if name == run_unit_tests._MCP_EXTRA_PACKAGE:
                return object() if mcp_present else None
            return object()

        return _fake

    def test_missing_mcp_extra_aborts_with_actionable_hint(self) -> None:
        run_unit_tests = _import_run_unit_tests()
        captured_stderr: list[str] = []

        def fake_print(*args: object, **kwargs: object) -> None:
            captured_stderr.append(" ".join(str(arg) for arg in args))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "tests").mkdir()
            with (
                mock.patch.dict(os.environ, {}, clear=False),
                mock.patch.object(run_unit_tests, "ROOT_DIR", root),
                mock.patch.object(run_unit_tests, "print", fake_print),
                mock.patch.object(
                    run_unit_tests.importlib.util,
                    "find_spec",
                    side_effect=self._make_fake_find_spec(
                        run_unit_tests, mcp_present=False
                    ),
                ),
            ):
                os.environ.pop("MUTANT_UNDER_TEST", None)
                os.environ.pop(run_unit_tests._MCP_EXTRA_OPT_OUT_ENV, None)
                rc = run_unit_tests.main([])
        joined = "\n".join(captured_stderr)
        # Tuple value-pin: the runner returned the dedicated mcp-extra
        # exit code AND the abort message names the missing dependency,
        # the recommended invocation extras, and the opt-out env var.
        self.assertEqual(
            (
                self._MISSING_MCP_EXTRA_EXIT_CODE,
                True,
                True,
                True,
            ),
            (
                rc,
                run_unit_tests._MCP_EXTRA_PACKAGE in joined,
                "--extra mcp" in joined,
                run_unit_tests._MCP_EXTRA_OPT_OUT_ENV in joined,
            ),
            f"abort message incomplete: rc={rc} message={joined!r}",
        )

    def test_opt_out_env_bypasses_mcp_preflight(self) -> None:
        run_unit_tests = _import_run_unit_tests()
        sentinel_returncode = 17

        class _FakeCompletedProcess:
            returncode = sentinel_returncode

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "tests").mkdir()
            with (
                mock.patch.dict(
                    os.environ,
                    {run_unit_tests._MCP_EXTRA_OPT_OUT_ENV: "1"},
                    clear=False,
                ),
                mock.patch.object(run_unit_tests, "ROOT_DIR", root),
                mock.patch.object(
                    run_unit_tests.importlib.util,
                    "find_spec",
                    side_effect=self._make_fake_find_spec(
                        run_unit_tests, mcp_present=False
                    ),
                ),
                mock.patch.object(
                    run_unit_tests.subprocess,
                    "run",
                    return_value=_FakeCompletedProcess(),
                ),
            ):
                os.environ.pop("MUTANT_UNDER_TEST", None)
                rc = run_unit_tests.main([])
        # Sentinel returncode (distinct from the mcp-extra exit code)
        # confirms control reached the mocked parallel-runner dispatch.
        # Tuple value-pin collapses the dominated ``assertNotEqual`` that
        # would otherwise be tautological under the leading ``assertEqual``.
        self.assertEqual(
            (sentinel_returncode, False),
            (rc, rc == self._MISSING_MCP_EXTRA_EXIT_CODE),
            "opt-out env must bypass mcp preflight",
        )

    def test_present_mcp_extra_passes_preflight(self) -> None:
        run_unit_tests = _import_run_unit_tests()
        sentinel_returncode = 17

        class _FakeCompletedProcess:
            returncode = sentinel_returncode

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "tests").mkdir()
            with (
                mock.patch.dict(os.environ, {}, clear=False),
                mock.patch.object(run_unit_tests, "ROOT_DIR", root),
                mock.patch.object(
                    run_unit_tests.importlib.util,
                    "find_spec",
                    side_effect=self._make_fake_find_spec(
                        run_unit_tests, mcp_present=True
                    ),
                ),
                mock.patch.object(
                    run_unit_tests.subprocess,
                    "run",
                    return_value=_FakeCompletedProcess(),
                ),
            ):
                os.environ.pop("MUTANT_UNDER_TEST", None)
                os.environ.pop(run_unit_tests._MCP_EXTRA_OPT_OUT_ENV, None)
                rc = run_unit_tests.main([])
        self.assertEqual(sentinel_returncode, rc)

    def test_stale_mutants_takes_precedence_over_missing_mcp_extra(self) -> None:
        run_unit_tests = _import_run_unit_tests()
        captured_stderr: list[str] = []

        # Patch ``print`` to silence the abort line during the suite run
        # and to capture the message for content assertion — without it,
        # the stderr noise would otherwise leak into the surrounding test
        # output and the assertion below could not distinguish the
        # stale-artifact message from the (suppressed) mcp-extra message.
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
                mock.patch.object(
                    run_unit_tests.importlib.util,
                    "find_spec",
                    side_effect=self._make_fake_find_spec(
                        run_unit_tests, mcp_present=False
                    ),
                ),
            ):
                os.environ.pop("MUTANT_UNDER_TEST", None)
                os.environ.pop(run_unit_tests._MCP_EXTRA_OPT_OUT_ENV, None)
                rc = run_unit_tests.main([])
        joined = "\n".join(captured_stderr)
        # The stale-artifact failure mode is more proximal than the
        # missing-optional-dependency failure mode.  Pin four facts as a
        # single tuple: the stale exit code surfaced, the mcp-extra exit
        # code did not leak, the stale-artifact recovery hint reached
        # stderr, and the mcp-extra package name did not leak into stderr.
        self.assertEqual(
            (self._STALE_MUTANTS_EXIT_CODE, False, True, False),
            (
                rc,
                rc == self._MISSING_MCP_EXTRA_EXIT_CODE,
                "rm -rf mutants/" in joined,
                run_unit_tests._MCP_EXTRA_PACKAGE in joined,
            ),
            f"preflight ordering broken: rc={rc} message={joined!r}",
        )

    def test_runner_module_docstring_documents_mcp_extra(self) -> None:
        run_unit_tests = _import_run_unit_tests()
        docstring = run_unit_tests.__doc__ or ""
        # The docstring is the in-tree discoverability surface; it must
        # name the recommended-invocation extras AND the opt-out env var
        # so a reader of the source file alone can recover.
        self.assertEqual(
            (True, True),
            (
                "--extra mcp" in docstring,
                run_unit_tests._MCP_EXTRA_OPT_OUT_ENV in docstring,
            ),
            f"runner docstring missing discoverability strings: {docstring!r}",
        )


@pytest.mark.source_text_invariant
class RunUnitTestsContributingDiscoverabilityTests(unittest.TestCase):
    """Issue #217 — ``CONTRIBUTING.md`` names the ``mcp`` optional-dependency
    extra alongside the test-runner script so a contributor can compose the
    recommended invocation without grepping the source. (#356 slimmed the
    README to a pointer doc; the developer setup walkthrough — and the
    ``mcp`` extra — now lives in ``CONTRIBUTING.md``.)

    Marked ``source_text_invariant`` because it reads ``CONTRIBUTING.md``
    from the repository tree; ``CONTRIBUTING.md`` is not part of the mutmut
    ``also_copy`` surface, so the class contributes no mutant-detection
    signal and would otherwise fail collection inside the ``mutants/``
    working tree.
    """

    def test_contributing_names_mcp_extra_with_runner_script(self) -> None:
        text = CONTRIBUTING_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "--extra mcp",
            text,
            "CONTRIBUTING.md must name the 'mcp' extra alongside the test runner",
        )


if __name__ == "__main__":
    unittest.main()
