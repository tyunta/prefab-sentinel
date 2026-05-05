from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

DEFAULT_ARGS = ["-s", "tests", "-t", ".", "-v", "-j", "0"]

# Distinct exit codes per failure mode so CI can disambiguate them.
# 0  — tests passed
# 1  — at least one test failed (unittest_parallel default)
# 2  — unittest_parallel is not installed
# 3  — stale ``mutants/`` artifact tree aborts the preflight (issue #174)
MISSING_PARALLEL_RUNNER_EXIT_CODE = 2
STALE_MUTANTS_EXIT_CODE = 3

# mutmut sets this env var inside its forked child invocations of
# ``pytest``; when present, the runner is itself executing inside an
# active mutmut session and the ``mutants/`` directory is intentional
# rather than stale.  The preflight passes through silently in that case.
_MUTMUT_CHILD_INDICATOR = "MUTANT_UNDER_TEST"
_STALE_MUTANTS_DIR_NAME = "mutants"


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


def main(argv: list[str] | None = None) -> int:
    abort_message = _stale_mutants_message(ROOT_DIR)
    if abort_message is not None:
        print(abort_message, file=sys.stderr)
        return STALE_MUTANTS_EXIT_CODE

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
