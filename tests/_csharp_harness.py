"""Python-side helper for invoking the C# xUnit harness.

Issue #222 Phase 3: a subset of bridge-side helpers compile without
Unity assembly references; the C# xUnit harness under
``tests/csharp/`` exercises them as ordinary class libraries.  Python
wrapper tests reach the harness through ``run_csharp_tests`` so the
Python suite can surface a structured outcome alongside the rest of
the unit-test run.

The wrapper does not become part of the default unit-test invocation
surface.  Wrapper tests gate themselves on the documented opt-in
environment variable (``PREFAB_SENTINEL_RUN_CSHARP_TESTS``) and are
skipped at collection without it; that gate keeps the .NET runtime
out of the default invocation while making the coverage available
under a single env-var flip when an operator wants it.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CSharpRunOutcome", "OPT_IN_ENV_VAR", "TEST_PROJECT_DIR", "run_csharp_tests"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_PROJECT_DIR = PROJECT_ROOT / "tests" / "csharp"

# Wrapper tests check this environment variable at collection time.
# The variable name is part of the discoverability contract documented
# in TESTING.md "C# xUnit harness" section and exposed here so a single symbol lookup reveals
# the gate to operators and other test modules.
OPT_IN_ENV_VAR = "PREFAB_SENTINEL_RUN_CSHARP_TESTS"

# VSTest's runtime summary line. The runner emits one of these per test
# DLL, e.g.::
#
#     Passed!  - Failed:     0, Passed:    17, Skipped:     0, Total:    17, ...
#
# Each named count is matched independently so future runner-side
# spacing or label changes do not require parsing every field together.
_COUNT_PATTERNS: dict[str, re.Pattern[str]] = {
    "failed": re.compile(r"Failed:\s*(\d+)"),
    "passed": re.compile(r"Passed:\s*(\d+)"),
    "skipped": re.compile(r"Skipped:\s*(\d+)"),
}


@dataclass(frozen=True)
class CSharpRunOutcome:
    """Structured outcome of a single C# test runner invocation.

    Attributes:
        exit_code: The runner's process exit code (zero on a green run).
        passed: Count of passed tests parsed from the runner summary.
        failed: Count of failed tests parsed from the runner summary.
        skipped: Count of skipped tests parsed from the runner summary.
        stdout: Captured standard-output text from the runner process.
        stderr: Captured standard-error text from the runner process.
    """

    exit_code: int
    passed: int
    failed: int
    skipped: int
    stdout: str
    stderr: str


def run_csharp_tests(
    *,
    filter_expression: str | None = None,
    working_directory: Path | None = None,
) -> CSharpRunOutcome:
    """Invoke ``dotnet test`` against the project's C# test project.

    Args:
        filter_expression: Optional VSTest ``--filter`` expression.  When
            supplied, the runner restricts execution to matching tests.
        working_directory: Optional override of the directory the runner
            is invoked from.  When ``None`` (the default), the runner is
            invoked from ``tests/csharp/``.

    Returns:
        A ``CSharpRunOutcome`` carrying the runner exit code, parsed
        per-category counts, and the captured stdout / stderr streams.

    Raises:
        FileNotFoundError: When the ``dotnet`` executable is not on PATH.
            The exception propagates unchanged per the project's
            infrastructure-exception contract; the wrapper test treats a
            missing runtime as an opt-in-time operator concern.
    """
    cwd = working_directory if working_directory is not None else TEST_PROJECT_DIR
    # ``--no-restore`` keeps the per-call latency low; CI restores once
    # per checkout in locked mode (see TESTING.md "C# xUnit harness" section).
    command = ["dotnet", "test", "--no-restore", "--nologo"]
    if filter_expression is not None:
        command.extend(["--filter", filter_expression])

    # Pop the bridge dispatch env var so the inherited host shell value
    # does not leak into the runner; the bridge is not part of the C#
    # test surface and tests must start from a deterministic
    # watch-dir-unconfigured state (CLAUDE.md "Editor リモート操作の
    # 行動規約" / issues #88, #89, #270).
    env = os.environ.copy()
    env.pop("UNITYTOOL_BRIDGE_WATCH_DIR", None)

    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )

    counts = _parse_counts(completed.stdout)
    return CSharpRunOutcome(
        exit_code=completed.returncode,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _parse_counts(output: str) -> dict[str, int]:
    """Extract ``passed`` / ``failed`` / ``skipped`` counts from runner stdout.

    Returns zero for any category whose label is absent from the output,
    which is the runner's behaviour when no tests of that category ran.
    A malformed summary surfaces as zero counts paired with a non-zero
    exit code so callers can still detect the failure.
    """
    out: dict[str, int] = {}
    for category, pattern in _COUNT_PATTERNS.items():
        match = pattern.search(output)
        out[category] = int(match.group(1)) if match is not None else 0
    return out
