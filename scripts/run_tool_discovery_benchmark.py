"""Run the offline MCP tool-discovery benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prefab_sentinel.tool_discovery_benchmark.application import (
    run_tool_discovery_benchmark,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_tool_discovery_benchmark")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--tools-doc", type=Path, required=True)
    parser.add_argument("--out-report", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_tool_discovery_benchmark(
        fixture_path=args.fixture,
        tools_doc_path=args.tools_doc,
        out_report=args.out_report,
        repository_root=Path(__file__).resolve().parents[1],
    )
    stream = sys.stdout if result.exit_code == 0 else sys.stderr
    stream.write(json.dumps({"code": result.code, "message": result.message}) + "\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
