from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from unitytool.mcp.serialized_object import compute_patch_plan_sha256, load_patch_plan
from unitytool.orchestrator import Phase1Orchestrator
from unitytool.reporting import export_report, render_markdown_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unitytool",
        description="UnityTool Phase 1 scaffold CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspection commands.")
    inspect_sub = inspect_parser.add_subparsers(dest="inspect_command", required=True)
    inspect_variant = inspect_sub.add_parser("variant", help="Inspect prefab variant state.")
    inspect_variant.add_argument("--path", required=True, help="Path to target variant prefab.")
    inspect_variant.add_argument(
        "--component-filter",
        default=None,
        help="Optional component filter for effective-value inspection.",
    )
    inspect_variant.add_argument("--format", choices=("json", "md"), default="json")
    inspect_where_used = inspect_sub.add_parser(
        "where-used",
        help="Find usages of a target asset path or GUID.",
    )
    inspect_where_used.add_argument(
        "--asset-or-guid",
        required=True,
        help="Target asset path or 32-char GUID.",
    )
    inspect_where_used.add_argument(
        "--scope",
        default=None,
        help="Optional scan scope path.",
    )
    inspect_where_used.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob-style path pattern to exclude from scan (can be specified multiple times).",
    )
    inspect_where_used.add_argument(
        "--max-usages",
        type=int,
        default=500,
        help="Maximum usage rows to include in output.",
    )
    inspect_where_used.add_argument("--format", choices=("json", "md"), default="json")

    validate_parser = subparsers.add_parser("validate", help="Validation commands.")
    validate_sub = validate_parser.add_subparsers(dest="validate_command", required=True)
    validate_refs = validate_sub.add_parser("refs", help="Validate broken references in scope.")
    validate_refs.add_argument("--scope", required=True, help="Asset scope path.")
    validate_refs.add_argument(
        "--details",
        action="store_true",
        help="Include diagnostics list in output (off by default for performance).",
    )
    validate_refs.add_argument(
        "--max-diagnostics",
        type=int,
        default=200,
        help="Maximum diagnostics to include when --details is enabled.",
    )
    validate_refs.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob-style path pattern to exclude from scan (can be specified multiple times).",
    )
    validate_refs.add_argument(
        "--ignore-guid",
        action="append",
        default=[],
        help="Missing-asset GUID to ignore during validation (can be specified multiple times).",
    )
    validate_refs.add_argument(
        "--ignore-guid-file",
        default=None,
        help="UTF-8 text file with one missing-asset GUID per line (# comment supported).",
    )
    validate_refs.add_argument("--format", choices=("json", "md"), default="json")
    validate_runtime = validate_sub.add_parser(
        "runtime",
        help="Validate runtime status using scene checks and log classification.",
    )
    validate_runtime.add_argument("--scene", required=True, help="Target Unity scene path.")
    validate_runtime.add_argument(
        "--profile",
        default="default",
        help="Runtime profile label for ClientSim execution context.",
    )
    validate_runtime.add_argument(
        "--log-file",
        default=None,
        help="Optional Unity log file path. Default: <project>/Logs/Editor.log.",
    )
    validate_runtime.add_argument(
        "--since-timestamp",
        default=None,
        help="Optional log cursor label for future integrations.",
    )
    validate_runtime.add_argument(
        "--allow-warnings",
        action="store_true",
        help="Treat warning-only runtime findings as pass.",
    )
    validate_runtime.add_argument(
        "--max-diagnostics",
        type=int,
        default=200,
        help="Maximum diagnostics to include from runtime log classification.",
    )
    validate_runtime.add_argument("--format", choices=("json", "md"), default="json")

    suggest_parser = subparsers.add_parser("suggest", help="Suggestion commands.")
    suggest_sub = suggest_parser.add_subparsers(dest="suggest_command", required=True)
    suggest_ignore = suggest_sub.add_parser(
        "ignore-guids",
        help="Suggest missing-asset GUIDs as ignore candidates.",
    )
    suggest_ignore.add_argument("--scope", required=True, help="Asset scope path.")
    suggest_ignore.add_argument(
        "--min-occurrences",
        type=int,
        default=50,
        help="Minimum missing-asset occurrences required to include a GUID candidate.",
    )
    suggest_ignore.add_argument(
        "--max-items",
        type=int,
        default=20,
        help="Maximum number of candidate GUIDs to return.",
    )
    suggest_ignore.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob-style path pattern to exclude from scan (can be specified multiple times).",
    )
    suggest_ignore.add_argument(
        "--ignore-guid",
        action="append",
        default=[],
        help="Missing-asset GUID to exclude from candidate suggestion.",
    )
    suggest_ignore.add_argument(
        "--ignore-guid-file",
        default=None,
        help="UTF-8 text file with one GUID per line to exclude from candidate suggestion.",
    )
    suggest_ignore.add_argument(
        "--out-ignore-guid-file",
        default=None,
        help="Optional output file to store suggested GUIDs (one GUID per line).",
    )
    suggest_ignore.add_argument(
        "--out-ignore-guid-mode",
        choices=("replace", "append"),
        default="replace",
        help="Write mode for --out-ignore-guid-file (default: replace).",
    )
    suggest_ignore.add_argument("--format", choices=("json", "md"), default="json")

    patch_parser = subparsers.add_parser("patch", help="Patch commands.")
    patch_sub = patch_parser.add_subparsers(dest="patch_command", required=True)
    patch_apply = patch_sub.add_parser("apply", help="Validate/apply a patch plan.")
    patch_apply.add_argument("--plan", required=True, help="Input patch plan JSON path.")
    patch_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate plan and emit dry-run diff preview only.",
    )
    patch_apply.add_argument(
        "--confirm",
        action="store_true",
        help="Allow non-dry-run execution path (.json built-in, Unity targets via bridge).",
    )
    patch_apply.add_argument(
        "--plan-sha256",
        default=None,
        help=(
            "Optional expected SHA-256 digest for --plan. "
            "When specified, mismatched plan content is rejected."
        ),
    )
    patch_apply.add_argument(
        "--scope",
        default=None,
        help="Optional preflight scope for scan_broken_references before apply.",
    )
    patch_apply.add_argument(
        "--runtime-scene",
        default=None,
        help="Optional scene path for runtime validation steps after apply.",
    )
    patch_apply.add_argument(
        "--runtime-profile",
        default="default",
        help="Runtime validation profile passed to run_clientsim (default: default).",
    )
    patch_apply.add_argument(
        "--runtime-log-file",
        default=None,
        help="Optional Unity log file path used by runtime validation classification.",
    )
    patch_apply.add_argument(
        "--runtime-since-timestamp",
        default=None,
        help="Optional timestamp marker to annotate runtime log collection.",
    )
    patch_apply.add_argument(
        "--runtime-allow-warnings",
        action="store_true",
        help="Allow warnings in runtime assertion when --runtime-scene is used.",
    )
    patch_apply.add_argument(
        "--runtime-max-diagnostics",
        type=int,
        default=200,
        help="Maximum runtime classification diagnostics when --runtime-scene is used.",
    )
    patch_apply.add_argument(
        "--out-report",
        default=None,
        help="Optional output path to store patch apply result envelope as JSON.",
    )
    patch_apply.add_argument("--format", choices=("json", "md"), default="json")
    patch_hash = patch_sub.add_parser("hash", help="Compute SHA-256 digest of a patch plan.")
    patch_hash.add_argument("--plan", required=True, help="Input patch plan JSON path.")
    patch_hash.add_argument("--format", choices=("json", "text"), default="text")

    report_parser = subparsers.add_parser("report", help="Report conversion commands.")
    report_sub = report_parser.add_subparsers(dest="report_command", required=True)
    report_export = report_sub.add_parser("export", help="Export a stored JSON report.")
    report_export.add_argument("--input", required=True, help="Input report JSON path.")
    report_export.add_argument("--format", choices=("json", "md"), required=True)
    report_export.add_argument("--out", required=True, help="Output report path.")
    report_export.add_argument(
        "--md-max-usages",
        type=int,
        default=None,
        help="When --format md, keep at most N usage rows per usages list.",
    )
    report_export.add_argument(
        "--md-max-steps",
        type=int,
        default=None,
        help="When --format md, keep at most N items per steps list.",
    )
    report_export.add_argument(
        "--md-omit-usages",
        action="store_true",
        help="When --format md, omit all usage rows from usages lists.",
    )
    report_export.add_argument(
        "--md-omit-steps",
        action="store_true",
        help="When --format md, omit all steps arrays from payload data.",
    )

    return parser


def _emit_payload(payload: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown_report(payload))


def _load_ignore_guids(
    ignore_guid_args: list[str],
    ignore_guid_file: str | None,
) -> tuple[str, ...]:
    collected = [guid for guid in ignore_guid_args if guid]
    if not ignore_guid_file:
        return tuple(collected)

    path = Path(ignore_guid_file)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            collected.append(line)
    return tuple(collected)


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _write_ignore_guid_file(path: Path, guids: list[str], mode: str) -> dict[str, Any]:
    incoming = _ordered_unique([guid for guid in guids if guid])
    existing: list[str] = []
    if mode == "append" and path.exists():
        existing = _ordered_unique(list(_load_ignore_guids([], str(path))))

    merged = incoming if mode == "replace" else _ordered_unique([*existing, *incoming])
    added = len(merged) if mode == "replace" else max(0, len(merged) - len(existing))

    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(merged)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")

    return {
        "path": str(path),
        "mode": mode,
        "added": added,
        "total": len(merged),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    orchestrator = Phase1Orchestrator.default()

    if args.command == "inspect" and args.inspect_command == "variant":
        response = orchestrator.inspect_variant(args.path, args.component_filter)
        _emit_payload(response.to_dict(), args.format)
        return 0

    if args.command == "inspect" and args.inspect_command == "where-used":
        response = orchestrator.inspect_where_used(
            asset_or_guid=args.asset_or_guid,
            scope=args.scope,
            exclude_patterns=tuple(args.exclude),
            max_usages=args.max_usages,
        )
        _emit_payload(response.to_dict(), args.format)
        return 0

    if args.command == "validate" and args.validate_command == "refs":
        try:
            ignore_guids = _load_ignore_guids(args.ignore_guid, args.ignore_guid_file)
        except OSError as exc:
            parser.error(f"Failed to read --ignore-guid-file: {exc}")
        response = orchestrator.validate_refs(
            scope=args.scope,
            details=args.details,
            max_diagnostics=args.max_diagnostics,
            exclude_patterns=tuple(args.exclude),
            ignore_asset_guids=ignore_guids,
        )
        _emit_payload(response.to_dict(), args.format)
        return 0

    if args.command == "validate" and args.validate_command == "runtime":
        response = orchestrator.validate_runtime(
            scene_path=args.scene,
            profile=args.profile,
            log_file=args.log_file,
            since_timestamp=args.since_timestamp,
            allow_warnings=args.allow_warnings,
            max_diagnostics=args.max_diagnostics,
        )
        _emit_payload(response.to_dict(), args.format)
        return 0

    if args.command == "suggest" and args.suggest_command == "ignore-guids":
        try:
            ignore_guids = _load_ignore_guids(args.ignore_guid, args.ignore_guid_file)
        except OSError as exc:
            parser.error(f"Failed to read --ignore-guid-file: {exc}")
        response = orchestrator.suggest_ignore_guids(
            scope=args.scope,
            min_occurrences=args.min_occurrences,
            max_items=args.max_items,
            exclude_patterns=tuple(args.exclude),
            ignore_asset_guids=ignore_guids,
        )
        payload = response.to_dict()
        out_ignore_file = args.out_ignore_guid_file
        if out_ignore_file:
            candidate_guids = [
                str(item.get("guid", ""))
                for item in payload.get("data", {}).get("candidates", [])
                if item.get("guid")
            ]
            if candidate_guids:
                try:
                    file_update = _write_ignore_guid_file(
                        path=Path(out_ignore_file),
                        guids=candidate_guids,
                        mode=args.out_ignore_guid_mode,
                    )
                except OSError as exc:
                    parser.error(f"Failed to write --out-ignore-guid-file: {exc}")
                payload.setdefault("data", {})["ignore_file_update"] = file_update
            else:
                payload.setdefault("data", {})["ignore_file_update"] = {
                    "path": out_ignore_file,
                    "mode": args.out_ignore_guid_mode,
                    "added": 0,
                    "total": 0,
                    "written": False,
                    "reason": "no_candidates",
                }
        _emit_payload(payload, args.format)
        return 0

    if args.command == "patch" and args.patch_command == "apply":
        plan_path = Path(args.plan)
        try:
            plan = load_patch_plan(plan_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"Failed to load --plan: {exc}")
        plan_sha256 = compute_patch_plan_sha256(plan_path)
        if args.plan_sha256:
            expected_digest = args.plan_sha256.strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
                parser.error("--plan-sha256 must be a 64-character hexadecimal digest.")
            if expected_digest != plan_sha256:
                parser.error(
                    "Plan digest mismatch: "
                    f"--plan-sha256={expected_digest} does not match actual {plan_sha256}."
                )
        response = orchestrator.patch_apply(
            plan=plan,
            dry_run=args.dry_run,
            confirm=args.confirm,
            plan_sha256=plan_sha256,
            scope=args.scope,
            runtime_scene=args.runtime_scene,
            runtime_profile=args.runtime_profile,
            runtime_log_file=args.runtime_log_file,
            runtime_since_timestamp=args.runtime_since_timestamp,
            runtime_allow_warnings=args.runtime_allow_warnings,
            runtime_max_diagnostics=args.runtime_max_diagnostics,
        )
        payload = response.to_dict()
        if args.out_report:
            report_path = Path(args.out_report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        _emit_payload(payload, args.format)
        return 0

    if args.command == "patch" and args.patch_command == "hash":
        plan_path = Path(args.plan)
        try:
            load_patch_plan(plan_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"Failed to load --plan: {exc}")
        digest = compute_patch_plan_sha256(plan_path)
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "success": True,
                        "severity": "info",
                        "code": "PATCH_PLAN_SHA256",
                        "message": "Patch plan digest calculated.",
                        "data": {"plan": str(plan_path), "sha256": digest},
                        "diagnostics": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(digest)
        return 0

    if args.command == "report" and args.report_command == "export":
        input_path = Path(args.input)
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        md_max_usages = args.md_max_usages
        md_max_steps = args.md_max_steps
        if args.md_omit_usages:
            md_max_usages = 0
        if args.md_omit_steps:
            md_max_steps = 0
        output = export_report(
            payload=payload,
            output_path=args.out,
            fmt=args.format,
            md_max_usages=md_max_usages,
            md_max_steps=md_max_steps,
        )
        print(f"Exported report: {output}")
        return 0

    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
