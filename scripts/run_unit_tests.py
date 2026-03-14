from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

DEFAULT_ARGS = ["-s", "tests", "-t", ".", "-v", "-j", "0"]


def _build_command(argv: list[str]) -> list[str]:
    return [sys.executable, "-m", "unittest_parallel", *(argv or DEFAULT_ARGS)]


def main(argv: list[str] | None = None) -> int:
    if importlib.util.find_spec("unittest_parallel") is None:
        print(
            (
                "unittest_parallel is not installed. "
                "Install test extras with `python -m pip install -e '.[test]'` "
                "or run via `uv run --extra test python scripts/run_unit_tests.py`."
            ),
            file=sys.stderr,
        )
        return 2

    command = _build_command(list(argv or sys.argv[1:]))
    return subprocess.run(command, cwd=ROOT_DIR, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
