from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prefab_sentinel.benchmarking.application import run_benchmark_application


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_performance_benchmarks")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-report", type=Path, required=True)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--baseline-out", type=Path)
    parser.add_argument("--enforce", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    result = run_benchmark_application(
        manifest_path=args.manifest,
        out_report=args.out_report,
        repository_root=repository_root,
        enforce=args.enforce,
        baseline_ref=args.baseline_ref,
        baseline_out=args.baseline_out,
    )
    stream = sys.stdout if result.exit_code == 0 else sys.stderr
    stream.write(json.dumps({"code": result.code, "message": result.message}) + "\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
