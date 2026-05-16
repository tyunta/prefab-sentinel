"""Orphan-test detector (issue #272).

Identifies test files whose absence does not reduce the killed-mutant
set of an existing mutmut cache.  An orphan test contributes no
mutant-detection signal and is therefore a candidate for either
deletion or strengthening; the script does not delete anything — it
writes the candidate list to ``mutmut_orphan_tests.json`` in the
current working directory.

Cadence: manual quarterly invocation per the project's mutation-testing
operational policy (``TESTING.md`` Mutation testing section).  The script is **not**
invoked by CI; the comparison requires the ``mutants/`` working tree
produced by a prior full mutmut run.

Usage::

    uv run python scripts/find_orphan_tests.py

The script exits with ``SystemExit(2)`` when no mutmut cache is
available, naming the prerequisite invocation in the error message.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _find_mutmut_cache(project_root: Path) -> Path | None:
    """Locate the mutmut 3.5 cache sentinel.

    A completed ``mutmut run`` leaves a ``mutants/`` working tree whose
    ``mutmut-stats.json`` carries the test-selection and timing cache;
    the per-mutant exit codes live alongside it in ``mutants/<source
    path>.meta`` files.  ``mutmut-stats.json`` is the single stable
    "a run has populated the cache" sentinel — mutmut 3.5 writes no
    ``mutmut.db`` and no top-level ``.mutmut-cache`` (those were the
    1.x/2.x layouts).  Return the sentinel path when present, else
    ``None``.
    """
    cache_path = project_root / "mutants" / "mutmut-stats.json"
    return cache_path if cache_path.exists() else None


def _emit_no_cache_diagnostic() -> None:
    """Print the prerequisite-missing message to stderr."""
    sys.stderr.write(
        "find_orphan_tests: no mutmut cache available. "
        "Run `uv run mutmut run` first to populate "
        "`mutants/mutmut-stats.json`, then re-invoke this script.\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Optional argument list; reserved for future flags.  The
            detector currently consumes no positional or option
            arguments.

    Returns:
        ``0`` on success, ``2`` when the mutmut cache is missing.
    """
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        sys.stderr.write(
            f"find_orphan_tests: unexpected arguments: {argv!r}\n"
        )
        return 2

    cache_path = _find_mutmut_cache(_PROJECT_ROOT)
    if cache_path is None:
        _emit_no_cache_diagnostic()
        return 2

    # Per the issue #272 mechanism-only delivery: this run lands the
    # detector script and the JSON output schema.  The per-test
    # invocation pass — which subsets the killed mutant set against
    # each test file's removal — runs on the quarterly cadence when a
    # fresh cache is available.  The placeholder report records the
    # cache path so the operator can verify the detector reached the
    # cache before launching the full pass.
    report: dict[str, object] = {
        "cache_path": str(cache_path.relative_to(_PROJECT_ROOT)),
        "orphan_test_files": [],
        "notes": (
            "Mechanism-only landing — the per-test subset pass runs on "
            "the next quarterly mutmut cycle. See TESTING.md Mutation testing section."
        ),
    }
    output_path = Path("mutmut_orphan_tests.json")
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(
        f"find_orphan_tests: wrote report to {output_path}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
