from __future__ import annotations

import argparse
import csv
import glob
import json
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark_history_to_csv",
        description="Convert benchmark summary JSON files into a single CSV.",
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="Input JSON paths or glob patterns.",
    )
    parser.add_argument("--out", required=True, help="Output CSV path.")
    return parser


def _expand_inputs(patterns: list[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        candidate_patterns = [pattern]
        if "/" in pattern or "\\" in pattern:
            candidate_patterns.append(pattern.replace("/", "\\"))
            candidate_patterns.append(pattern.replace("\\", "/"))

        matched: list[str] = []
        for candidate in candidate_patterns:
            matched.extend(glob.glob(candidate))
        matched = sorted(set(matched))

        if matched:
            for entry in matched:
                paths.add(Path(entry))
        else:
            paths.add(Path(pattern))
    return sorted(path.resolve() for path in paths if path.exists())


def _summary_to_row(source: Path, summary: dict[str, Any]) -> list[str]:
    seconds = summary.get("seconds", {})
    validate = summary.get("validate_result", {})
    return [
        str(source),
        str(summary.get("scope", "")),
        str(summary.get("warmup_runs", "")),
        str(summary.get("runs", "")),
        str(seconds.get("avg", "")),
        str(seconds.get("min", "")),
        str(seconds.get("max", "")),
        str(validate.get("success", "")),
        str(validate.get("severity", "")),
        str(validate.get("code", "")),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_paths = _expand_inputs(args.inputs)
    if not input_paths:
        parser.error("No input JSON files were found.")

    rows: list[list[str]] = []
    for path in input_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.append(_summary_to_row(path, payload))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source",
                "scope",
                "warmup_runs",
                "runs",
                "avg_sec",
                "min_sec",
                "max_sec",
                "success",
                "severity",
                "code",
            ]
        )
        writer.writerows(rows)

    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
