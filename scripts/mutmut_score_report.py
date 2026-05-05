"""Aggregate ``mutmut results`` output into per-module score records.

The parser is the unit-testable seam: ``parse_mutmut_results`` consumes
the ``mutmut results --all=true`` text and returns a dict keyed by
module dotted-path with ``killed`` / ``survived`` / ``timeout`` /
``not_checked`` integer counts and a derived ``score`` percentage.

The CLI is a thin subprocess wrapper that runs ``mutmut results
--all=true`` and emits Markdown / CSV / JSON.

Score formula (from issue #169):

    score = (killed + timeout) / (killed + survived + timeout)

``not_checked`` mutants are reported but excluded from the denominator.

Audited-only mode restricts output to the README §14.5 audited list.
``--module <dotted.path>`` mode restricts output to that one module.

Distinct exit codes (issue #169 / spec):

* 0 — success
* 4 — the ``mutmut results`` subprocess returned non-zero
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

MUTMUT_SUBPROCESS_FAILURE_EXIT_CODE = 4

# README §14.5 audited modules.  Kept in one place so the audited-only
# filter and the test suite both read from the same source of truth.
AUDITED_MODULES: tuple[str, ...] = (
    "prefab_sentinel.services.reference_resolver",
    "prefab_sentinel.services.prefab_variant",
    "prefab_sentinel.services.serialized_object.patch_validator",
    "prefab_sentinel.services.runtime_validation.classification",
    "prefab_sentinel.orchestrator_postcondition",
    "prefab_sentinel.orchestrator_validation",
)

# A mutant line looks like one of:
#   <module>.<function>__mutmut_<N>: <status>
#   <module>.xǁ<Class>ǁ<method>__mutmut_<N>: <status>
# The leading ``<index>) `` numbering some mutmut versions emit is
# tolerated by the relaxed leading-anchor pattern below.
_LINE_RE = re.compile(
    r"""
    ^\s*
    (?:\d+\)\s+)?                # optional ``<idx>) `` prefix
    (?P<name>[A-Za-z_][\w.ǁ]*?__mutmut_\d+)
    \s*:\s*
    (?P<status>[A-Za-z_]+)
    \s*$
    """,
    re.VERBOSE,
)

_KNOWN_STATUSES = ("killed", "survived", "timeout", "not_checked")


@dataclass
class ModuleRecord:
    """Per-module aggregate counts and the derived score."""

    module: str
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    not_checked: int = 0

    @property
    def total(self) -> int:
        # ``not_checked`` is intentionally excluded from the denominator
        # so the score reflects mutants that actually ran.
        return self.killed + self.survived + self.timeout

    @property
    def score(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.killed + self.timeout) / self.total * 100.0


def _normalize_status(raw: str) -> str | None:
    """Map a raw status token to one of ``_KNOWN_STATUSES`` or None."""
    token = raw.strip().lower()
    if token in _KNOWN_STATUSES:
        return token
    if token in ("not", "skipped"):
        return "not_checked"
    return None


def _extract_module(mutant_name: str) -> str:
    """Return the module dotted-path of *mutant_name*.

    A mutant name encodes the module, an optional class wrapper, the
    function/method, and a ``__mutmut_<N>`` suffix.  The module is the
    longest dotted-path prefix that does not include the class wrapper
    marker ``xǁ`` or the function-name segment.
    """
    base = re.sub(r"__mutmut_\d+$", "", mutant_name)
    parts = base.split(".")
    for index, part in enumerate(parts):
        # ``ǁ`` (U+01C1) marks the class wrapper segment in mutmut's
        # name scheme (concretely ``xǁ<Class>ǁ<method>``); the segment
        # containing this character is where the module path ends.
        if "ǁ" in part:
            return ".".join(parts[:index])
    # No class wrapper: the last segment is the function.  Drop it.
    if len(parts) <= 1:
        return base
    return ".".join(parts[:-1])


def parse_mutmut_results(
    text: str,
    *,
    module_filter: str | None = None,
) -> dict[str, ModuleRecord]:
    """Parse *text* (captured ``mutmut results`` stdout) into per-module records.

    Lines that do not parse are skipped silently — never raises.
    *module_filter*, when supplied, restricts output to the named module
    (and its sub-modules whose dotted-path starts with ``module_filter.``).
    """
    records: dict[str, ModuleRecord] = {}
    for line in text.splitlines():
        match = _LINE_RE.match(line)
        if match is None:
            continue
        status = _normalize_status(match.group("status"))
        if status is None:
            continue
        module = _extract_module(match.group("name"))
        if not module:
            continue
        if module_filter is not None and not (
            module == module_filter
            or module.startswith(module_filter + ".")
        ):
            continue
        record = records.setdefault(module, ModuleRecord(module=module))
        setattr(record, status, getattr(record, status) + 1)
    return records


def filter_audited(records: dict[str, ModuleRecord]) -> dict[str, ModuleRecord]:
    """Restrict *records* to the README §14.5 audited module list."""
    audited = set(AUDITED_MODULES)
    return {
        module: record
        for module, record in records.items()
        if any(
            module == name or module.startswith(name + ".")
            for name in audited
        )
    }


def format_markdown(records: dict[str, ModuleRecord]) -> str:
    """Render *records* as a Markdown table with a fixed column order."""
    header = (
        "| module | killed | survived | timeout | not_checked | total | score |\n"
        "|---|---:|---:|---:|---:|---:|---:|\n"
    )
    if not records:
        return header.rstrip() + "\n"
    rows = []
    for module in sorted(records.keys()):
        record = records[module]
        rows.append(
            f"| {module} "
            f"| {record.killed} "
            f"| {record.survived} "
            f"| {record.timeout} "
            f"| {record.not_checked} "
            f"| {record.total} "
            f"| {record.score:.1f}% |"
        )
    return header + "\n".join(rows) + "\n"


@dataclass
class RunMetadata:
    """Run-context columns surfaced in the CSV output."""

    run_date: str
    mutmut_version: str
    parallelism: str

    @classmethod
    def from_now(cls, mutmut_version: str, parallelism: str) -> RunMetadata:
        return cls(
            run_date=_dt.datetime.now(tz=_dt.UTC).date().isoformat(),
            mutmut_version=mutmut_version,
            parallelism=parallelism,
        )


def format_csv(
    records: dict[str, ModuleRecord],
    metadata: RunMetadata,
) -> str:
    """Render *records* as CSV including run-date, mutmut version, parallelism."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "run_date",
            "mutmut_version",
            "parallelism",
            "module",
            "killed",
            "survived",
            "timeout",
            "not_checked",
            "total",
            "score",
        ]
    )
    for module in sorted(records.keys()):
        record = records[module]
        writer.writerow(
            [
                metadata.run_date,
                metadata.mutmut_version,
                metadata.parallelism,
                module,
                record.killed,
                record.survived,
                record.timeout,
                record.not_checked,
                record.total,
                f"{record.score:.1f}",
            ]
        )
    return buffer.getvalue()


def format_json(records: dict[str, ModuleRecord]) -> str:
    """Render *records* as a deterministic JSON document."""
    payload = []
    for module in sorted(records.keys()):
        record = records[module]
        entry = asdict(record)
        entry["total"] = record.total
        entry["score"] = round(record.score, 4)
        payload.append(entry)
    return json.dumps({"records": payload}, indent=2, sort_keys=True) + "\n"


@dataclass
class SubprocessOutcome:
    """Captured result of running ``mutmut results --all=true``."""

    returncode: int
    stdout: str
    stderr: str = ""
    metadata: RunMetadata = field(
        default_factory=lambda: RunMetadata.from_now("unknown", "unknown")
    )


def run_mutmut_results() -> SubprocessOutcome:
    """Invoke ``mutmut results --all=true`` and return the captured outcome.

    Why a thin wrapper: tests stub this function at its import site to
    avoid spawning the real CLI; the parser itself is pure.
    """
    completed = subprocess.run(
        ["mutmut", "results", "--all=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    return SubprocessOutcome(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        metadata=RunMetadata.from_now("unknown", "unknown"),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mutmut_score_report",
        description=(
            "Aggregate `mutmut results` output into per-module score records."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--module",
        dest="module",
        help="restrict output to the named dotted-path module",
    )
    group.add_argument(
        "--audited-only",
        dest="audited_only",
        action="store_true",
        help="restrict output to the README §14.5 audited module list",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("markdown", "csv", "json"),
        default="markdown",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    outcome = run_mutmut_results()
    if outcome.returncode != 0:
        if outcome.stderr:
            sys.stderr.write(outcome.stderr)
        return MUTMUT_SUBPROCESS_FAILURE_EXIT_CODE

    records = parse_mutmut_results(outcome.stdout, module_filter=args.module)
    if args.audited_only:
        records = filter_audited(records)

    if args.output_format == "markdown":
        sys.stdout.write(format_markdown(records))
    elif args.output_format == "csv":
        sys.stdout.write(format_csv(records, outcome.metadata))
    else:
        sys.stdout.write(format_json(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
