"""Unit-test runner for prefab-sentinel.

Wraps ``unittest_parallel`` and runs the project's preflight chain
before dispatching, in order: stale ``mutants/`` artifact tree
(issue #174), then ``mcp`` optional dependency (issue #217), then
``unittest_parallel`` installation.  Each preflight has a distinct
exit code so CI and operators can disambiguate the failure mode
without parsing stderr.

Recommended invocation (from the repository root)::

    uv run --extra test --extra mcp python scripts/run_unit_tests.py

The ``--extra test`` extra installs ``unittest_parallel``; the
``--extra mcp`` extra installs the ``mcp`` package that
``prefab_sentinel.mcp_server`` imports at module-load time.  Without
the ``mcp`` extra, ~14 tests that import the MCP server module error
at collection time.

Opt-out: set ``PREFAB_SENTINEL_RUN_TESTS_SKIP_MCP_EXTRA=1`` (any
non-empty value) to bypass the mcp-extra preflight.  The env var is
honoured only by the runner's own preflight; the affected tests
themselves still error at collection if ``mcp`` is missing.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

DEFAULT_ARGS = ["-s", "tests", "-t", ".", "-v", "-j", "0"]

# Distinct exit codes per failure mode so CI can disambiguate them.
# 0  — tests passed
# 1  — at least one test failed (unittest_parallel default)
# 2  — unittest_parallel is not installed
# 3  — stale ``mutants/`` artifact tree aborts the preflight (issue #174)
# 4  — the ``mcp`` optional dependency is not importable (issue #217)
MISSING_PARALLEL_RUNNER_EXIT_CODE = 2
STALE_MUTANTS_EXIT_CODE = 3
MISSING_MCP_EXTRA_EXIT_CODE = 4

# mutmut sets this env var inside its forked child invocations of
# ``pytest``; when present, the runner is itself executing inside an
# active mutmut session and the ``mutants/`` directory is intentional
# rather than stale.  The preflight passes through silently in that case.
_MUTMUT_CHILD_INDICATOR = "MUTANT_UNDER_TEST"
_STALE_MUTANTS_DIR_NAME = "mutants"

# Issue #217 — the ``mcp`` optional dependency is required by the
# ``prefab_sentinel.mcp_server`` module at module-load time.  When it
# is not importable, ~14 tests that import that module error at
# collection.  The runner detects this once at preflight and emits a
# single actionable hint instead of letting the per-test errors fan out.
_MCP_EXTRA_PACKAGE = "mcp"
_MCP_EXTRA_OPT_OUT_ENV = "PREFAB_SENTINEL_RUN_TESTS_SKIP_MCP_EXTRA"
_RECOMMENDED_INVOCATION = (
    "uv run --extra test --extra mcp python scripts/run_unit_tests.py"
)


def _build_command(argv: list[str]) -> list[str]:
    return [sys.executable, "-m", "unittest_parallel", *(argv or DEFAULT_ARGS)]


def _stale_mutants_message(root: Path) -> str | None:
    """Return the abort message when the stale-mutants preflight should fire.

    Returns ``None`` when the preflight passes through (no ``mutants/``
    directory at the repository root, or ``MUTANT_UNDER_TEST`` is set).

    Why a separate helper: mutmut copies the audited tree into
    ``ROOT_DIR / mutants/`` at run start.  Pytest collection over the
    working tree imports modules from that copy and silently shadows the
    package under test, producing impossible-to-debug failures.  The
    abort message names the offending directory and includes the literal
    ``rm -rf mutants/`` cleanup string so the operator can recover
    without guessing.
    """
    if os.environ.get(_MUTMUT_CHILD_INDICATOR):
        return None
    stale_dir = root / _STALE_MUTANTS_DIR_NAME
    if not stale_dir.exists():
        return None
    return (
        f"Stale mutmut working tree detected at '{_STALE_MUTANTS_DIR_NAME}/' "
        f"(absolute path: {stale_dir}). "
        f"Pytest collection imports modules out of that tree and shadows "
        f"the package under test. "
        f"Clean it up before re-running: rm -rf mutants/"
    )


def _missing_mcp_message(env: Mapping[str, str] | None = None) -> str | None:
    """Return the abort message when the mcp-extra preflight should fire.

    Returns ``None`` when the preflight passes through (the opt-out env
    var is set to a non-empty value, or the ``mcp`` optional dependency
    is importable in the current interpreter).

    Why a separate helper: the runner module's docstring documents the
    recommended invocation including the ``--extra mcp`` extras and the
    opt-out env var; concentrating the abort message here keeps the
    docstring guidance and the runtime hint phrased identically and
    locks the discoverability contract by name in tests.
    """
    env_map = env if env is not None else os.environ
    if env_map.get(_MCP_EXTRA_OPT_OUT_ENV):
        return None
    if importlib.util.find_spec(_MCP_EXTRA_PACKAGE) is not None:
        return None
    return (
        f"The '{_MCP_EXTRA_PACKAGE}' optional dependency is not importable; "
        f"tests that import 'prefab_sentinel.mcp_server' will error at "
        f"collection. "
        f"Install the test + mcp extras and rerun: {_RECOMMENDED_INVOCATION}. "
        f"To opt out and skip this preflight, "
        f"set {_MCP_EXTRA_OPT_OUT_ENV}=1."
    )


def main(argv: list[str] | None = None) -> int:
    abort_message = _stale_mutants_message(ROOT_DIR)
    if abort_message is not None:
        print(abort_message, file=sys.stderr)
        return STALE_MUTANTS_EXIT_CODE

    mcp_message = _missing_mcp_message()
    if mcp_message is not None:
        print(mcp_message, file=sys.stderr)
        return MISSING_MCP_EXTRA_EXIT_CODE

    if importlib.util.find_spec("unittest_parallel") is None:
        print(
            (
                "unittest_parallel is not installed. "
                "Install test extras with `python -m pip install -e '.[test]'` "
                "or run via `uv run --extra test python scripts/run_unit_tests.py`."
            ),
            file=sys.stderr,
        )
        return MISSING_PARALLEL_RUNNER_EXIT_CODE

    command = _build_command(list(argv or sys.argv[1:]))
    return subprocess.run(command, cwd=ROOT_DIR, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
