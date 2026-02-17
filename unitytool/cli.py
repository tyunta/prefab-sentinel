from __future__ import annotations

import argparse
from fnmatch import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from unitytool.bridge_smoke import (
    UNITY_COMMAND_ENV,
    UNITY_EXECUTE_METHOD_ENV,
    UNITY_LOG_FILE_ENV,
    UNITY_PROJECT_PATH_ENV,
    UNITY_TIMEOUT_SEC_ENV,
    build_bridge_env,
    build_bridge_request,
    load_patch_plan as load_bridge_smoke_plan,
    resolve_expected_applied as resolve_bridge_expected_applied,
    run_bridge as run_bridge_smoke,
    validate_expectation as validate_bridge_smoke_expectation,
)
from unitytool.mcp.serialized_object import (
    compute_patch_plan_hmac_sha256,
    compute_patch_plan_sha256,
    load_patch_plan,
)
from unitytool.orchestrator import Phase1Orchestrator
from unitytool.reporting import export_report, render_markdown_report
from unitytool.smoke_batch import (
    add_arguments as add_smoke_batch_arguments,
    run_from_args as run_smoke_batch_from_args,
)
from unitytool.smoke_history import (
    add_arguments as add_smoke_history_arguments,
    run_from_args as run_smoke_history_from_args,
)
from unitytool.unity_assets import find_project_root, resolve_scope_path

_DEFAULT_PLAN_SIGNING_KEY_ENV = "UNITYTOOL_PLAN_SIGNING_KEY"
_DEFAULT_IGNORE_GUID_BRANCH_ALLOWLIST = ("main", "release/*")
_IGNORE_GUID_BRANCH_ALLOWLIST_ENV = "UNITYTOOL_IGNORE_GUID_ALLOW_BRANCHES"
_CI_BRANCH_ENV = "UNITYTOOL_CI_BRANCH"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prefab-sentinel",
        description="Prefab Sentinel Phase 1 scaffold CLI.",
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
        help=(
            "UTF-8 text file with one missing-asset GUID per line (# comment supported). "
            "If omitted, <scope>/config/ignore_guids.txt is used when present."
        ),
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
    validate_bridge_smoke = validate_sub.add_parser(
        "bridge-smoke",
        help="Run an end-to-end smoke test against tools/unity_patch_bridge.py.",
    )
    validate_bridge_smoke.add_argument("--plan", required=True, help="Patch plan JSON path.")
    validate_bridge_smoke.add_argument(
        "--bridge-script",
        default=str(Path("tools") / "unity_patch_bridge.py"),
        help="Bridge script path (default: tools/unity_patch_bridge.py).",
    )
    validate_bridge_smoke.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to run --bridge-script.",
    )
    validate_bridge_smoke.add_argument(
        "--unity-command",
        default=None,
        help=f"Override {UNITY_COMMAND_ENV} for this run.",
    )
    validate_bridge_smoke.add_argument(
        "--unity-project-path",
        default=None,
        help=f"Override {UNITY_PROJECT_PATH_ENV} for this run.",
    )
    validate_bridge_smoke.add_argument(
        "--unity-execute-method",
        default=None,
        help=f"Override {UNITY_EXECUTE_METHOD_ENV} for this run.",
    )
    validate_bridge_smoke.add_argument(
        "--unity-timeout-sec",
        type=int,
        default=None,
        help=f"Override {UNITY_TIMEOUT_SEC_ENV} for this run.",
    )
    validate_bridge_smoke.add_argument(
        "--unity-log-file",
        default=None,
        help=f"Override {UNITY_LOG_FILE_ENV} for this run.",
    )
    validate_bridge_smoke.add_argument(
        "--expect-failure",
        action="store_true",
        help="Expect bridge result success=false (exit 0 when failure is observed).",
    )
    validate_bridge_smoke.add_argument(
        "--expected-code",
        default=None,
        help="Optional expected response code value.",
    )
    validate_bridge_smoke.add_argument(
        "--expected-applied",
        type=int,
        default=None,
        help="Optional expected data.applied value for bridge response.",
    )
    validate_bridge_smoke.add_argument(
        "--expect-applied-from-plan",
        action="store_true",
        help=(
            "Infer expected applied count from patch plan ops length when "
            "--expected-applied is not specified and --expect-failure is not set."
        ),
    )
    validate_bridge_smoke.add_argument(
        "--out",
        default=None,
        help="Optional output JSON path for bridge response.",
    )
    validate_bridge_smoke.add_argument("--format", choices=("json", "md"), default="json")
    validate_smoke_batch = validate_sub.add_parser(
        "smoke-batch",
        help="Run smoke cases for avatar/world targets and write batch summaries.",
    )
    add_smoke_batch_arguments(validate_smoke_batch)

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
        help=(
            "UTF-8 text file with one GUID per line to exclude from candidate suggestion. "
            "If omitted, <scope>/config/ignore_guids.txt is used when present."
        ),
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
        "--plan-signature",
        default=None,
        help=(
            "Optional expected HMAC-SHA256 signature for --plan. "
            "Requires key source (env/file)."
        ),
    )
    patch_apply.add_argument(
        "--attestation-file",
        default=None,
        help="Optional attestation JSON path containing expected plan digest/signature.",
    )
    patch_apply.add_argument(
        "--plan-signing-key-env",
        default=_DEFAULT_PLAN_SIGNING_KEY_ENV,
        help=(
            "Env var name for HMAC signing key when --plan-signature is used "
            f"(default: {_DEFAULT_PLAN_SIGNING_KEY_ENV})."
        ),
    )
    patch_apply.add_argument(
        "--plan-signing-key-file",
        default=None,
        help="Optional UTF-8 key file path for --plan-signature verification.",
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
    patch_apply.add_argument(
        "--change-reason",
        default=None,
        help="Required when using --confirm (non-dry-run). Describes why the change is needed.",
    )
    patch_apply.add_argument("--format", choices=("json", "md"), default="json")
    patch_hash = patch_sub.add_parser("hash", help="Compute SHA-256 digest of a patch plan.")
    patch_hash.add_argument("--plan", required=True, help="Input patch plan JSON path.")
    patch_hash.add_argument("--format", choices=("json", "text"), default="text")
    patch_sign = patch_sub.add_parser(
        "sign",
        help="Compute HMAC-SHA256 signature of a patch plan.",
    )
    patch_sign.add_argument("--plan", required=True, help="Input patch plan JSON path.")
    patch_sign.add_argument(
        "--key-env",
        default=_DEFAULT_PLAN_SIGNING_KEY_ENV,
        help=(
            "Env var name for HMAC signing key "
            f"(default: {_DEFAULT_PLAN_SIGNING_KEY_ENV})."
        ),
    )
    patch_sign.add_argument(
        "--key-file",
        default=None,
        help="Optional UTF-8 key file path (overrides --key-env when set).",
    )
    patch_sign.add_argument("--format", choices=("json", "text"), default="text")
    patch_attest = patch_sub.add_parser(
        "attest",
        help="Emit patch-plan attestation (sha256 + optional signature).",
    )
    patch_attest.add_argument("--plan", required=True, help="Input patch plan JSON path.")
    patch_attest.add_argument(
        "--unsigned",
        action="store_true",
        help="Emit attestation without HMAC signature.",
    )
    patch_attest.add_argument(
        "--key-env",
        default=_DEFAULT_PLAN_SIGNING_KEY_ENV,
        help=(
            "Env var name for HMAC signing key "
            f"(default: {_DEFAULT_PLAN_SIGNING_KEY_ENV})."
        ),
    )
    patch_attest.add_argument(
        "--key-file",
        default=None,
        help="Optional UTF-8 key file path (overrides --key-env when set).",
    )
    patch_attest.add_argument(
        "--out",
        default=None,
        help="Optional output path for attestation JSON file.",
    )
    patch_attest.add_argument("--format", choices=("json", "text"), default="json")
    patch_verify = patch_sub.add_parser(
        "verify",
        help="Verify SHA-256/HMAC signatures of a patch plan.",
    )
    patch_verify.add_argument("--plan", required=True, help="Input patch plan JSON path.")
    patch_verify.add_argument(
        "--attestation-file",
        default=None,
        help="Optional attestation JSON path containing expected sha256/signature.",
    )
    patch_verify.add_argument("--sha256", default=None, help="Expected SHA-256 digest.")
    patch_verify.add_argument("--signature", default=None, help="Expected HMAC-SHA256 signature.")
    patch_verify.add_argument(
        "--signing-key-env",
        default=_DEFAULT_PLAN_SIGNING_KEY_ENV,
        help=(
            "Env var name for HMAC signing key when --signature is used "
            f"(default: {_DEFAULT_PLAN_SIGNING_KEY_ENV})."
        ),
    )
    patch_verify.add_argument(
        "--signing-key-file",
        default=None,
        help="Optional UTF-8 key file path for --signature verification.",
    )
    patch_verify.add_argument("--format", choices=("json", "text"), default="json")

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
    report_smoke_history = report_sub.add_parser(
        "smoke-history",
        help="Aggregate bridge smoke summaries into CSV/Markdown/timeout profiles.",
    )
    add_smoke_history_arguments(report_smoke_history)

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


def _resolve_ignore_guid_file(
    ignore_guid_file: str | None,
    scope: str | None,
) -> str | None:
    if ignore_guid_file:
        return ignore_guid_file
    if not scope:
        return None
    project_root = find_project_root(Path.cwd())
    scope_path = resolve_scope_path(scope, project_root)
    candidate = scope_path / "config" / "ignore_guids.txt"
    if candidate.exists():
        return str(candidate)
    return None


def _is_ci_environment() -> bool:
    value = os.environ.get("CI")
    if not value:
        return False
    return value.lower() not in {"0", "false", "no"}


def _parse_ignore_guid_branch_allowlist(raw: str | None) -> tuple[str, ...]:
    if raw:
        parsed = [item.strip() for item in raw.split(",") if item.strip()]
        if parsed:
            return tuple(parsed)
    return _DEFAULT_IGNORE_GUID_BRANCH_ALLOWLIST


def _resolve_ci_branch() -> str | None:
    candidates = (
        _CI_BRANCH_ENV,
        "GITHUB_REF_NAME",
        "GITHUB_HEAD_REF",
        "CI_COMMIT_REF_NAME",
        "BRANCH_NAME",
        "GIT_BRANCH",
        "GITHUB_REF",
    )
    for key in candidates:
        value = os.environ.get(key)
        if not value:
            continue
        if key in {"GITHUB_REF", "GIT_BRANCH"}:
            if value.startswith("refs/heads/"):
                value = value[len("refs/heads/") :]
            if value.startswith("origin/"):
                value = value[len("origin/") :]
        return value
    return None


def _enforce_ci_ignore_guid_policy(
    parser: argparse.ArgumentParser,
    out_ignore_guid_file: str | None,
) -> None:
    if not out_ignore_guid_file:
        return
    if not _is_ci_environment():
        return
    allowlist = _parse_ignore_guid_branch_allowlist(
        os.environ.get(_IGNORE_GUID_BRANCH_ALLOWLIST_ENV)
    )
    branch = _resolve_ci_branch()
    if not branch:
        parser.error(
            "CI ignore-guid updates require branch context. "
            f"Set {_CI_BRANCH_ENV} or provide GITHUB_REF_NAME."
        )
    if not any(fnmatch(branch, pattern) for pattern in allowlist):
        parser.error(
            "CI ignore-guid updates are restricted to branches: "
            f"{', '.join(allowlist)} (current: {branch})."
        )


def _resolve_signing_key(
    parser: argparse.ArgumentParser,
    *,
    key_env: str | None,
    key_file: str | None,
) -> str:
    if key_file:
        path = Path(key_file)
        try:
            key = path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            parser.error(f"Failed to read signing key file: {exc}")
        if not key:
            parser.error("Signing key file is empty.")
        return key

    if key_env:
        key = os.environ.get(key_env)
        if key is None:
            parser.error(f"Signing key env var is not set: {key_env}")
        key = key.rstrip("\r\n")
        if not key:
            parser.error(f"Signing key env var is empty: {key_env}")
        return key

    parser.error("Signing key source is not configured.")
    return ""


def _normalize_expected_digest(
    parser: argparse.ArgumentParser,
    *,
    option_name: str,
    digest: str,
) -> str:
    normalized = digest.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        parser.error(f"{option_name} must be a 64-character hexadecimal digest.")
    return normalized


def _load_attestation_expectations(
    parser: argparse.ArgumentParser,
    attestation_file: str,
) -> tuple[str | None, str | None]:
    path = Path(attestation_file)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"Failed to load --attestation-file: {exc}")

    if not isinstance(payload, dict):
        parser.error("--attestation-file root must be a JSON object.")

    source: dict[str, Any] = payload
    data = payload.get("data")
    if isinstance(data, dict):
        source = data

    sha256 = source.get("sha256")
    signature = source.get("signature")
    if sha256 is not None and not isinstance(sha256, str):
        parser.error("--attestation-file field 'sha256' must be a string when present.")
    if signature is not None and not isinstance(signature, str):
        parser.error("--attestation-file field 'signature' must be a string when present.")
    return sha256, signature


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
        ignore_guid_file = _resolve_ignore_guid_file(
            args.ignore_guid_file,
            args.scope,
        )
        try:
            ignore_guids = _load_ignore_guids(args.ignore_guid, ignore_guid_file)
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

    if args.command == "validate" and args.validate_command == "bridge-smoke":
        if args.expected_applied is not None and args.expected_applied < 0:
            parser.error("--expected-applied must be greater than or equal to 0.")
        plan_path = Path(args.plan)
        bridge_script = Path(args.bridge_script)
        try:
            plan = load_bridge_smoke_plan(plan_path)
            expected_applied, expected_applied_source = resolve_bridge_expected_applied(
                plan=plan,
                expected_applied=args.expected_applied,
                expect_applied_from_plan=args.expect_applied_from_plan,
                expect_failure=args.expect_failure,
            )
            request = build_bridge_request(plan)
            env = build_bridge_env(
                unity_command=args.unity_command,
                unity_project_path=args.unity_project_path,
                unity_execute_method=args.unity_execute_method,
                unity_timeout_sec=args.unity_timeout_sec,
                unity_log_file=args.unity_log_file,
            )
            payload = run_bridge_smoke(
                bridge_script=bridge_script,
                python_executable=args.python,
                request=request,
                env=env,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            payload = {
                "success": False,
                "severity": "error",
                "code": "SMOKE_BRIDGE_ERROR",
                "message": str(exc),
                "data": {
                    "plan": str(plan_path),
                    "bridge_script": str(bridge_script),
                },
                "diagnostics": [],
            }
            _emit_payload(payload, args.format)
            return 1

        matched_expectation = validate_bridge_smoke_expectation(
            payload,
            args.expect_failure,
            expected_applied,
            expected_applied_source,
            args.expected_code,
        )
        if args.out:
            output_path = Path(args.out)
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                parser.error(f"Failed to write --out: {exc}")
        _emit_payload(payload, args.format)
        return 0 if matched_expectation else 1

    if args.command == "validate" and args.validate_command == "smoke-batch":
        return run_smoke_batch_from_args(args, parser)

    if args.command == "suggest" and args.suggest_command == "ignore-guids":
        ignore_guid_file = _resolve_ignore_guid_file(
            args.ignore_guid_file,
            args.scope,
        )
        try:
            ignore_guids = _load_ignore_guids(args.ignore_guid, ignore_guid_file)
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
            _enforce_ci_ignore_guid_policy(parser, out_ignore_file)
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
        if not args.dry_run and args.confirm:
            if not args.out_report:
                parser.error(
                    "patch apply --confirm requires --out-report to record the audit log."
                )
            if not args.change_reason or not args.change_reason.strip():
                parser.error("patch apply --confirm requires --change-reason.")
        try:
            plan = load_patch_plan(plan_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"Failed to load --plan: {exc}")
        plan_sha256 = compute_patch_plan_sha256(plan_path)
        plan_signature = None
        attested_sha256 = None
        attested_signature = None
        if args.attestation_file:
            attested_sha256, attested_signature = _load_attestation_expectations(
                parser,
                args.attestation_file,
            )
            if (
                attested_sha256 is None
                and attested_signature is None
                and args.plan_sha256 is None
                and args.plan_signature is None
            ):
                parser.error(
                    "patch apply --attestation-file must include sha256/signature when "
                    "--plan-sha256/--plan-signature are not specified."
                )

        sha256_input = args.plan_sha256 if args.plan_sha256 is not None else attested_sha256
        signature_input = (
            args.plan_signature if args.plan_signature is not None else attested_signature
        )

        if sha256_input is not None:
            expected_digest = _normalize_expected_digest(
                parser,
                option_name="--plan-sha256",
                digest=sha256_input,
            )
            if expected_digest != plan_sha256:
                parser.error(
                    "Plan digest mismatch: "
                    f"--plan-sha256={expected_digest} does not match actual {plan_sha256}."
                )
        if signature_input is not None:
            expected_signature = _normalize_expected_digest(
                parser,
                option_name="--plan-signature",
                digest=signature_input,
            )
            key = _resolve_signing_key(
                parser,
                key_env=args.plan_signing_key_env,
                key_file=args.plan_signing_key_file,
            )
            plan_signature = compute_patch_plan_hmac_sha256(plan_path, key)
            if expected_signature != plan_signature:
                parser.error(
                    "Plan signature mismatch: "
                    f"--plan-signature={expected_signature} does not match actual {plan_signature}."
                )
        response = orchestrator.patch_apply(
            plan=plan,
            dry_run=args.dry_run,
            confirm=args.confirm,
            plan_sha256=plan_sha256,
            plan_signature=plan_signature,
            change_reason=args.change_reason,
            scope=args.scope,
            runtime_scene=args.runtime_scene,
            runtime_profile=args.runtime_profile,
            runtime_log_file=args.runtime_log_file,
            runtime_since_timestamp=args.runtime_since_timestamp,
            runtime_allow_warnings=args.runtime_allow_warnings,
            runtime_max_diagnostics=args.runtime_max_diagnostics,
        )
        payload = response.to_dict()
        payload.setdefault("data", {})["plan_attestation_file"] = args.attestation_file
        if args.out_report:
            report_path = Path(args.out_report)
            try:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                parser.error(f"Failed to write --out-report: {exc}")
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

    if args.command == "patch" and args.patch_command == "sign":
        plan_path = Path(args.plan)
        try:
            load_patch_plan(plan_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"Failed to load --plan: {exc}")
        key = _resolve_signing_key(parser, key_env=args.key_env, key_file=args.key_file)
        signature = compute_patch_plan_hmac_sha256(plan_path, key)
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "success": True,
                        "severity": "info",
                        "code": "PATCH_PLAN_SIGNATURE",
                        "message": "Patch plan signature calculated.",
                        "data": {"plan": str(plan_path), "signature": signature},
                        "diagnostics": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(signature)
        return 0

    if args.command == "patch" and args.patch_command == "attest":
        plan_path = Path(args.plan)
        try:
            load_patch_plan(plan_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"Failed to load --plan: {exc}")
        sha256 = compute_patch_plan_sha256(plan_path)
        signature = None
        if not args.unsigned:
            key = _resolve_signing_key(parser, key_env=args.key_env, key_file=args.key_file)
            signature = compute_patch_plan_hmac_sha256(plan_path, key)

        payload = {
            "success": True,
            "severity": "info",
            "code": "PATCH_PLAN_ATTESTATION",
            "message": "Patch plan attestation generated.",
            "data": {
                "plan": str(plan_path),
                "sha256": sha256,
                "signature": signature,
            },
            "diagnostics": [],
        }
        if args.out:
            output_path = Path(args.out)
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                parser.error(f"Failed to write --out: {exc}")
            payload["data"]["attestation_path"] = str(output_path)

        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"sha256={sha256}")
            if signature:
                print(f"signature={signature}")
        return 0

    if args.command == "patch" and args.patch_command == "verify":
        plan_path = Path(args.plan)
        try:
            load_patch_plan(plan_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"Failed to load --plan: {exc}")
        attested_sha256 = None
        attested_signature = None
        if args.attestation_file:
            attested_sha256, attested_signature = _load_attestation_expectations(
                parser,
                args.attestation_file,
            )

        sha256_input = args.sha256 if args.sha256 is not None else attested_sha256
        signature_input = args.signature if args.signature is not None else attested_signature
        if sha256_input is None and signature_input is None:
            parser.error(
                "patch verify requires at least one expected value: "
                "--sha256 / --signature / --attestation-file."
            )

        actual_sha256 = compute_patch_plan_sha256(plan_path)
        sha256_checked = sha256_input is not None
        sha256_expected = (
            _normalize_expected_digest(
                parser,
                option_name="--sha256",
                digest=sha256_input,
            )
            if sha256_checked
            else None
        )
        sha256_matched = (actual_sha256 == sha256_expected) if sha256_checked else None

        signature_checked = signature_input is not None
        signature_expected = (
            _normalize_expected_digest(
                parser,
                option_name="--signature",
                digest=signature_input,
            )
            if signature_checked
            else None
        )
        signature_actual = None
        signature_matched = None
        if signature_checked:
            key = _resolve_signing_key(
                parser,
                key_env=args.signing_key_env,
                key_file=args.signing_key_file,
            )
            signature_actual = compute_patch_plan_hmac_sha256(plan_path, key)
            signature_matched = signature_actual == signature_expected

        checks = [value for value in (sha256_matched, signature_matched) if value is not None]
        success = all(checks)
        code = "PATCH_PLAN_VERIFY_OK" if success else "PATCH_PLAN_VERIFY_MISMATCH"
        message = (
            "Patch plan verification succeeded."
            if success
            else "Patch plan verification failed."
        )
        payload = {
            "success": success,
            "severity": "info" if success else "error",
            "code": code,
            "message": message,
            "data": {
                "plan": str(plan_path),
                "attestation_file": args.attestation_file,
                "sha256": {
                    "checked": sha256_checked,
                    "expected": sha256_expected,
                    "actual": actual_sha256,
                    "matched": sha256_matched,
                },
                "signature": {
                    "checked": signature_checked,
                    "expected": signature_expected,
                    "actual": signature_actual,
                    "matched": signature_matched,
                },
            },
            "diagnostics": [],
        }
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("OK" if success else "MISMATCH")
        return 0 if success else 1

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

    if args.command == "report" and args.report_command == "smoke-history":
        return run_smoke_history_from_args(args, parser)

    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
