"""Static gate that surfaces over-limit modules under audited package roots.

Why static rather than runtime: mutation-testing source expansion runs on
the same package source tree, and a runtime line-count assertion trips on
mutmut's expanded layout.  The line-count invariant is enforced once per
push by the CI lint job.

Usage:

    python scripts/check_module_line_limits.py

The default audited roots are the three split packages plus their parent
service tree; ``--limit`` and ``--root`` allow callers to override.  The
CLI exits non-zero on any over-limit file and prints the offenders.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

DEFAULT_LIMIT = 300
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE_ROOTS: tuple[Path, ...] = (
    PROJECT_ROOT / "prefab_sentinel" / "services" / "prefab_variant",
    PROJECT_ROOT / "prefab_sentinel" / "services" / "runtime_validation",
    PROJECT_ROOT / "prefab_sentinel" / "services" / "serialized_object",
)


def check(packages: Iterable[Path], limit: int = DEFAULT_LIMIT) -> list[tuple[str, int]]:
    """Return ``(path, line_count)`` for every ``.py`` file over ``limit``.

    Walks each supplied package root non-recursively (matching the
    historical runtime tests' ``glob("*.py")`` shape).  Returns an empty
    list when every audited file conforms.
    """
    offenders: list[tuple[str, int]] = []
    for package in packages:
        if not package.is_dir():
            raise FileNotFoundError(f"package directory missing: {package}")
        for module_path in sorted(package.glob("*.py")):
            with module_path.open(encoding="utf-8") as handle:
                line_count = sum(1 for _ in handle)
            if line_count > limit:
                offenders.append((str(module_path), line_count))
    return offenders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_module_line_limits",
        description="Fail when any audited module exceeds the line-count limit.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Per-file maximum line count (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        default=None,
        help=(
            "Package root directory to audit.  Repeat to add multiple roots.  "
            "When omitted, the project's three split packages are audited."
        ),
    )
    args = parser.parse_args(argv)

    roots = tuple(args.root) if args.root else DEFAULT_PACKAGE_ROOTS
    offenders = check(roots, limit=args.limit)
    if not offenders:
        return 0
    for path, count in offenders:
        sys.stdout.write(f"{path}: {count} lines (limit {args.limit})\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
