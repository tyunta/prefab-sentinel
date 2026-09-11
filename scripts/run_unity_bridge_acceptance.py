"""Explicit CLI for the local Unity Bridge acceptance controller."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from prefab_sentinel.unity_acceptance import AcceptanceConfig, run_acceptance
from prefab_sentinel.unity_acceptance.model import AcceptanceResult
from prefab_sentinel.unity_acceptance.transport import AcceptanceTransport, McpAcceptanceTransport


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run explicit local Unity Bridge acceptance."
    )
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument(
        "--target-dir",
        required=True,
        help="Absolute path under PROJECT_ROOT/Assets.",
    )
    parser.add_argument("--watch-dir", required=True)
    parser.add_argument("--unity-log-file", required=True)
    parser.add_argument("--out-report", required=True)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--recover-run-id", default="")
    return parser


async def _run(config: AcceptanceConfig) -> AcceptanceResult:
    if not config.confirm_live:
        return await run_acceptance(
            config,
            cast(AcceptanceTransport, object()),
            time.monotonic,
            time.sleep,
        )

    async with McpAcceptanceTransport(
        worktree_root=config.repo_root,
        watch_dir=config.watch_dir,
    ) as transport:
        return await run_acceptance(config, transport, time.monotonic, time.sleep)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    result = asyncio.run(
        _run(
            AcceptanceConfig(
                repo_root=repo_root,
                project_root=Path(args.project_root).resolve(),
                scope=args.scope,
                target_dir=Path(args.target_dir).resolve(),
                watch_dir=args.watch_dir,
                unity_log_file=Path(args.unity_log_file).resolve(),
                out_report=args.out_report,
                confirm_live=args.confirm_live,
                recover_run_id=args.recover_run_id,
            )
        )
    )
    payload = result.to_dict()
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["result"]["success"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
