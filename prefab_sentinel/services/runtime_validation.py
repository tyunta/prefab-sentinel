from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from prefab_sentinel.bridge_constants import (
    BRIDGE_MODE_ENV,
    BRIDGE_WATCH_DIR_ENV,
    UNITY_COMMAND_ENV,
    UNITY_LOG_FILE_ENV,
    UNITY_PROJECT_PATH_ENV,
    UNITY_TIMEOUT_SEC_ENV,
)
from prefab_sentinel.contracts import Diagnostic, Severity, ToolResponse, error_response, max_severity, success_response
from prefab_sentinel.json_io import dump_json, load_json, load_json_file
from prefab_sentinel.unity_assets import decode_text_file, find_project_root
from prefab_sentinel.unity_assets_path import relative_to_root, resolve_scope_path
from prefab_sentinel.wsl_compat import needs_windows_paths, split_unity_command, to_windows_path, to_wsl_path

UNITY_RUNTIME_EXECUTE_METHOD_ENV = "UNITYTOOL_RUNTIME_EXECUTE_METHOD"
DEFAULT_RUNTIME_EXECUTE_METHOD = "PrefabSentinel.UnityRuntimeValidationBridge.RunFromJson"
DEFAULT_TIMEOUT_SEC = 300
RUNTIME_PROTOCOL_VERSION = 1
_DEFAULT_EDITOR_POLL_INTERVAL = 1.0

_LOG_PATTERNS: tuple[tuple[str, Severity, re.Pattern[str]], ...] = (
    (
        "BROKEN_PPTR",
        Severity.ERROR,
        re.compile(r"broken\s+pptr|broken pptr", re.IGNORECASE),
    ),
    (
        "UDON_NULLREF",
        Severity.CRITICAL,
        re.compile(
            r"(nullreferenceexception.*udon)|(udon.*nullreferenceexception)",
            re.IGNORECASE,
        ),
    ),
    (
        "VARIANT_OVERRIDE_MISMATCH",
        Severity.ERROR,
        re.compile(r"override.*mismatch|mismatch.*override", re.IGNORECASE),
    ),
    (
        "DUPLICATE_EVENTSYSTEM",
        Severity.WARNING,
        re.compile(r"there can be only one active eventsystem", re.IGNORECASE),
    ),
    (
        "MISSING_COMPONENT",
        Severity.ERROR,
        re.compile(
            r"missingcomponentexception|referenced script on this behaviour is missing",
            re.IGNORECASE,
        ),
    ),
)



def _coerce_severity(value: object) -> Severity | None:
    if isinstance(value, Severity):
        return value
    if isinstance(value, str):
        try:
            return Severity(value)
        except ValueError:
            return None
    return None


class RuntimeValidationService:
    """Runtime validation service for log-based checks plus Unity batchmode hooks."""

    TOOL_NAME = "runtime-validation"

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = find_project_root(project_root or Path.cwd())

    def _relative(self, path: Path) -> str:
        return relative_to_root(path, self.project_root)

    def _default_runtime_root(self) -> Path:
        configured_root = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
        if configured_root:
            return Path(configured_root).expanduser()
        return self.project_root

    def _skip_response(
        self,
        *,
        code: str,
        message: str,
        data: dict[str, Any],
    ) -> ToolResponse:
        return success_response(
            code,
            message,
            severity=Severity.WARNING,
            data={**data, "read_only": True, "executed": False},
        )

    def _load_runtime_config(self, *, default_project_root: Path) -> tuple[dict[str, Any] | None, ToolResponse | None]:
        command_raw = os.environ.get(UNITY_COMMAND_ENV, "").strip()
        if not command_raw:
            return None, None

        command, split_error = split_unity_command(command_raw)
        if split_error is not None:
            return None, error_response(
                "RUN_CONFIG_ERROR",
                "Unity runtime command cannot be parsed.",
                data={
                    "command_raw": command_raw,
                    "error": split_error,
                    "read_only": True,
                    "executed": False,
                },
            )

        timeout_raw = os.environ.get(UNITY_TIMEOUT_SEC_ENV, str(DEFAULT_TIMEOUT_SEC)).strip()
        try:
            timeout_sec = int(timeout_raw)
        except ValueError:
            timeout_sec = -1
        if timeout_sec <= 0:
            return None, error_response(
                "RUN_CONFIG_ERROR",
                f"{UNITY_TIMEOUT_SEC_ENV} must be a positive integer.",
                data={
                    "received_timeout": timeout_raw,
                    "read_only": True,
                    "executed": False,
                },
            )

        project_path_raw = os.environ.get(UNITY_PROJECT_PATH_ENV, "").strip()
        project_path = Path(to_wsl_path(project_path_raw)) if project_path_raw else default_project_root
        if not project_path.exists():
            return None, error_response(
                "RUN_CONFIG_ERROR",
                "Unity project path does not exist.",
                data={
                    "project_path": str(project_path),
                    "read_only": True,
                    "executed": False,
                },
            )

        execute_method = (
            os.environ.get(UNITY_RUNTIME_EXECUTE_METHOD_ENV, DEFAULT_RUNTIME_EXECUTE_METHOD).strip()
            or DEFAULT_RUNTIME_EXECUTE_METHOD
        )
        log_path_raw = os.environ.get(UNITY_LOG_FILE_ENV, "").strip()
        log_path = Path(log_path_raw) if log_path_raw else project_path / "Logs" / "Editor.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        return {
            "command": command,
            "project_path": project_path,
            "execute_method": execute_method,
            "timeout_sec": timeout_sec,
            "log_path": log_path,
        }, None

    def _build_runtime_command(
        self,
        *,
        config: dict[str, Any],
        request_path: Path,
        response_path: Path,
    ) -> list[str]:
        cmd = config["command"]
        _wp = to_windows_path if needs_windows_paths(cmd) else lambda p: p
        return [
            *cmd,
            "-batchmode",
            "-projectPath",
            _wp(str(config["project_path"])),
            "-executeMethod",
            str(config["execute_method"]),
            "-logFile",
            _wp(str(config["log_path"])),
            "-sentinelRuntimeRequest",
            _wp(str(request_path)),
            "-sentinelRuntimeResponse",
            _wp(str(response_path)),
        ]

    @staticmethod
    def _protocol_error(message: str, base_data: dict[str, Any]) -> ToolResponse:
        return error_response(
            "RUN_PROTOCOL_ERROR",
            message,
            data={**base_data, "read_only": True, "executed": False},
        )

    def _parse_runtime_response(
        self,
        payload: object,
        *,
        action: str,
        project_root: Path,
        scene_path: str | None,
        profile: str | None,
        log_path: Path,
    ) -> ToolResponse:
        base_data = {
            "action": action,
            "project_root": self._relative(project_root),
            "scene_path": scene_path,
            "profile": profile,
            "log_path": self._relative(log_path),
        }
        if not isinstance(payload, dict):
            return self._protocol_error("Unity runtime response root must be an object.", base_data)

        success = payload.get("success")
        severity = _coerce_severity(payload.get("severity"))
        code = payload.get("code")
        message = payload.get("message")
        data = payload.get("data")
        diagnostics_payload = payload.get("diagnostics")
        if not isinstance(success, bool):
            return self._protocol_error("Unity runtime response field 'success' must be a boolean.", base_data)
        if severity is None:
            return self._protocol_error("Unity runtime response field 'severity' is invalid.", base_data)
        if not isinstance(code, str) or not code.strip():
            return self._protocol_error("Unity runtime response field 'code' must be a non-empty string.", base_data)
        if not isinstance(message, str):
            return self._protocol_error("Unity runtime response field 'message' must be a string.", base_data)
        if not isinstance(data, dict):
            return self._protocol_error("Unity runtime response field 'data' must be an object.", base_data)
        if not isinstance(diagnostics_payload, list):
            return self._protocol_error("Unity runtime response field 'diagnostics' must be an array.", base_data)

        diagnostics: list[Diagnostic] = []
        for entry in diagnostics_payload:
            if not isinstance(entry, dict):
                return self._protocol_error("Unity runtime diagnostics entries must be objects.", base_data)
            diagnostics.append(
                Diagnostic(
                    path=str(entry.get("path", "")),
                    location=str(entry.get("location", "")),
                    detail=str(entry.get("detail", "")),
                    evidence=str(entry.get("evidence", "")),
                )
            )

        return ToolResponse(
            success=success,
            severity=severity,
            code=code.strip(),
            message=message,
            data={**base_data, **data},
            diagnostics=diagnostics,
        )

    @staticmethod
    def _failure_code(action: str) -> str:
        return "RUN_COMPILE_FAILED" if action == "compile_udonsharp" else "RUN002"

    def _invoke_unity_runtime(
        self,
        *,
        action: str,
        target_root: Path,
        scene_path: str | None = None,
        profile: str | None = None,
    ) -> ToolResponse:
        bridge_mode = os.environ.get(BRIDGE_MODE_ENV, "batchmode").strip().lower()

        if bridge_mode == "editor":
            return self._invoke_via_editor_bridge(
                action=action,
                target_root=target_root,
                scene_path=scene_path,
                profile=profile,
            )

        config, config_error = self._load_runtime_config(default_project_root=target_root)
        if config_error is not None:
            return config_error
        if config is None:
            skip_code = "RUN_COMPILE_SKIPPED" if action == "compile_udonsharp" else "RUN_CLIENTSIM_SKIPPED"
            skip_message = (
                "compile_udonsharp skipped because Unity batchmode execution is not configured."
                if action == "compile_udonsharp"
                else "run_clientsim skipped because Unity batchmode execution is not configured."
            )
            skip_data: dict[str, Any] = {"project_root": self._relative(target_root)}
            if scene_path is not None:
                skip_data["scene_path"] = scene_path
            if profile is not None:
                skip_data["profile"] = profile
            return self._skip_response(code=skip_code, message=skip_message, data=skip_data)

        with tempfile.TemporaryDirectory(prefix="prefab-sentinel-runtime-") as temp_dir:
            temp_root = Path(temp_dir)
            request_path = temp_root / "request.json"
            response_path = temp_root / "response.json"
            payload = {
                "protocol_version": RUNTIME_PROTOCOL_VERSION,
                "action": action,
                "project_root": str(target_root),
                "scene_path": scene_path or "",
                "profile": profile or "",
                "timeout_sec": int(config["timeout_sec"]),
            }
            request_path.write_text(
                dump_json(payload, indent=None),
                encoding="utf-8",
            )
            command = self._build_runtime_command(
                config=config,
                request_path=request_path,
                response_path=response_path,
            )

            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=int(config["timeout_sec"]),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                failure_code = self._failure_code(action)
                failure_message = (
                    "Unity batchmode compile timed out."
                    if action == "compile_udonsharp"
                    else "Unity ClientSim batchmode execution timed out."
                )
                return error_response(
                    failure_code,
                    failure_message,
                    data={
                        "action": action,
                        "project_root": self._relative(target_root),
                        "scene_path": scene_path,
                        "profile": profile,
                        "command": command,
                        "timeout_sec": int(config["timeout_sec"]),
                        "error": str(exc),
                        "log_path": self._relative(Path(config["log_path"])),
                        "read_only": False,
                        "executed": False,
                    },
                )
            except OSError as exc:
                failure_code = self._failure_code(action)
                failure_message = (
                    "Failed to start Unity batchmode compile process."
                    if action == "compile_udonsharp"
                    else "Failed to start Unity ClientSim batchmode process."
                )
                return error_response(
                    failure_code,
                    failure_message,
                    data={
                        "action": action,
                        "project_root": self._relative(target_root),
                        "scene_path": scene_path,
                        "profile": profile,
                        "command": command,
                        "error": str(exc),
                        "log_path": self._relative(Path(config["log_path"])),
                        "read_only": False,
                        "executed": False,
                    },
                )

            response_error: str | None = None
            if response_path.exists():
                try:
                    response_payload = load_json_file(response_path)
                except (OSError, json.JSONDecodeError) as exc:
                    response_payload = None
                    response_error = str(exc)
            else:
                response_payload = None

            if response_payload is not None:
                response = self._parse_runtime_response(
                    response_payload,
                    action=action,
                    project_root=target_root,
                    scene_path=scene_path,
                    profile=profile,
                    log_path=Path(config["log_path"]),
                )
                if completed.returncode != 0 and response.success:
                    return error_response(
                        "RUN_PROTOCOL_ERROR",
                        "Unity runtime returned success payload but exited with a non-zero code.",
                        data={
                            "action": action,
                            "project_root": self._relative(target_root),
                            "scene_path": scene_path,
                            "profile": profile,
                            "returncode": completed.returncode,
                            "stdout": completed.stdout,
                            "stderr": completed.stderr,
                            "log_path": self._relative(Path(config["log_path"])),
                            "read_only": False,
                            "executed": True,
                        },
                    )
                return response

            failure_code = self._failure_code(action)
            failure_message = (
                "Unity batchmode compile did not produce a valid response."
                if action == "compile_udonsharp"
                else "Unity ClientSim batchmode execution did not produce a valid response."
            )
            return error_response(
                failure_code,
                failure_message,
                data={
                    "action": action,
                    "project_root": self._relative(target_root),
                    "scene_path": scene_path,
                    "profile": profile,
                    "command": command,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "response_error": response_error,
                    "response_path": str(response_path),
                    "log_path": self._relative(Path(config["log_path"])),
                    "read_only": False,
                    "executed": completed.returncode == 0,
                },
            )

    def _invoke_via_editor_bridge(
        self,
        *,
        action: str,
        target_root: Path,
        scene_path: str | None = None,
        profile: str | None = None,
    ) -> ToolResponse:
        """Send a runtime validation request via the editor bridge file watcher."""
        watch_dir_raw = os.environ.get(BRIDGE_WATCH_DIR_ENV, "").strip()
        if not watch_dir_raw:
            return error_response(
                "RUN_CONFIG_ERROR",
                f"{BRIDGE_WATCH_DIR_ENV} is required when {BRIDGE_MODE_ENV}=editor.",
                data={
                    "action": action,
                    "project_root": self._relative(target_root),
                    "read_only": True,
                    "executed": False,
                },
            )

        watch_dir = Path(to_wsl_path(watch_dir_raw))
        timeout_raw = os.environ.get(UNITY_TIMEOUT_SEC_ENV, str(DEFAULT_TIMEOUT_SEC)).strip()
        try:
            timeout_sec = int(timeout_raw)
        except ValueError:
            timeout_sec = -1
        if timeout_sec <= 0:
            return error_response(
                "RUN_CONFIG_ERROR",
                f"{UNITY_TIMEOUT_SEC_ENV} must be a positive integer.",
                data={
                    "received_timeout": timeout_raw,
                    "read_only": True,
                    "executed": False,
                },
            )

        request_id = uuid.uuid4().hex
        request_file = watch_dir / f"{request_id}.request.json"
        response_file = watch_dir / f"{request_id}.response.json"
        tmp_file = Path(str(request_file) + ".tmp")

        payload = {
            "protocol_version": RUNTIME_PROTOCOL_VERSION,
            "action": action,
            "project_root": to_windows_path(str(target_root)),
            "scene_path": to_windows_path(scene_path) if scene_path else "",
            "profile": profile or "",
            "timeout_sec": timeout_sec,
        }

        try:
            watch_dir.mkdir(parents=True, exist_ok=True)
            tmp_file.write_text(
                dump_json(payload, indent=None),
                encoding="utf-8",
            )
            tmp_file.rename(request_file)
        except OSError as exc:
            return error_response(
                "RUN_EDITOR_BRIDGE_WRITE",
                "Failed to write editor bridge runtime request file.",
                data={
                    "action": action,
                    "project_root": self._relative(target_root),
                    "request_file": str(request_file),
                    "error": str(exc),
                    "read_only": True,
                    "executed": False,
                },
            )

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if response_file.exists():
                try:
                    raw = response_file.read_text(encoding="utf-8")
                    response_payload = load_json(raw)
                except (OSError, json.JSONDecodeError) as exc:
                    return error_response(
                        "RUN_EDITOR_BRIDGE_RESPONSE",
                        "Editor bridge runtime response file could not be read.",
                        data={
                            "action": action,
                            "project_root": self._relative(target_root),
                            "response_file": str(response_file),
                            "error": str(exc),
                            "read_only": False,
                            "executed": False,
                        },
                    )
                finally:
                    self._try_delete(request_file)
                    self._try_delete(response_file)

                log_path_raw = os.environ.get(UNITY_LOG_FILE_ENV, "").strip()
                log_path = Path(log_path_raw) if log_path_raw else target_root / "Logs" / "Editor.log"
                return self._parse_runtime_response(
                    response_payload,
                    action=action,
                    project_root=target_root,
                    scene_path=scene_path,
                    profile=profile,
                    log_path=log_path,
                )

            time.sleep(_DEFAULT_EDITOR_POLL_INTERVAL)

        # Timeout — clean up.
        self._try_delete(request_file)
        failure_code = self._failure_code(action)
        return error_response(
            failure_code,
            "Editor bridge runtime response timed out.",
            data={
                "action": action,
                "project_root": self._relative(target_root),
                "scene_path": scene_path,
                "profile": profile,
                "timeout_sec": timeout_sec,
                "request_file": str(request_file),
                "bridge_mode": "editor",
                "read_only": False,
                "executed": False,
            },
        )

    @staticmethod
    def _try_delete(path: Path) -> None:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)

    def compile_udonsharp(self, project_root: str | None = None) -> ToolResponse:
        """Trigger an UdonSharp compilation via Unity batchmode or editor bridge.

        Args:
            project_root: Unity project root path. Uses the configured
                default when ``None``.

        Returns:
            ``ToolResponse`` with compile result from the Unity runtime, or
            a skip response when batchmode is not configured.
        """
        target_root = (
            self._default_runtime_root()
            if project_root is None
            else resolve_scope_path(project_root, self.project_root)
        )
        if not (target_root / "Assets").exists():
            return self._skip_response(
                code="RUN_COMPILE_SKIPPED",
                message="compile_udonsharp skipped because project root does not contain Assets.",
                data={"project_root": str(target_root)},
            )
        return self._invoke_unity_runtime(
            action="compile_udonsharp",
            target_root=target_root,
        )

    def run_clientsim(self, scene_path: str, profile: str) -> ToolResponse:
        """Run a ClientSim session for a scene via Unity batchmode or editor bridge.

        Args:
            scene_path: Path to the ``.unity`` scene file.
            profile: ClientSim profile name to use.

        Returns:
            ``ToolResponse`` with the ClientSim execution result from Unity.
        """
        target_root = self._default_runtime_root()
        scene = resolve_scope_path(scene_path, target_root)
        if not scene.exists():
            return error_response(
                "RUN002",
                "Scene path was not found for runtime validation.",
                data={
                    "scene_path": scene_path,
                    "profile": profile,
                    "read_only": True,
                    "executed": False,
                },
            )
        if scene.suffix.lower() != ".unity":
            return error_response(
                "RUN002",
                "Runtime validation requires a .unity scene path.",
                data={
                    "scene_path": scene_path,
                    "profile": profile,
                    "read_only": True,
                    "executed": False,
                },
            )

        return self._invoke_unity_runtime(
            action="run_clientsim",
            target_root=target_root,
            scene_path=self._relative(scene),
            profile=profile,
        )

    def collect_unity_console(
        self,
        log_file: str | None = None,
        since_timestamp: str | None = None,
        max_lines: int = 4000,
    ) -> ToolResponse:
        """Read Unity Editor.log and return the most recent log lines.

        Args:
            log_file: Explicit log file path. Falls back to ``<project>/Logs/Editor.log``.
            since_timestamp: Reserved for future timestamp-based filtering.
            max_lines: Maximum number of tail lines to return.

        Returns:
            ``ToolResponse`` with ``data.log_lines`` and ``data.line_count``.
        """
        log_path = (
            resolve_scope_path(log_file, self._default_runtime_root())
            if log_file
            else self._default_runtime_root() / "Logs" / "Editor.log"
        )
        if not log_path.exists():
            return success_response(
                "RUN_LOG_MISSING",
                "Unity log file was not found; classification uses empty log lines.",
                severity=Severity.WARNING,
                data={
                    "log_path": str(log_path),
                    "line_count": 0,
                    "log_lines": [],
                    "since_timestamp": since_timestamp,
                    "read_only": True,
                },
            )

        lines = decode_text_file(log_path).splitlines()
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return success_response(
            "RUN_LOG_COLLECTED",
            "Unity log lines collected.",
            data={
                "log_path": self._relative(log_path),
                "line_count": len(lines),
                "log_lines": lines,
                "since_timestamp": since_timestamp,
                "read_only": True,
            },
        )

    def classify_errors(
        self,
        log_lines: list[str],
        max_diagnostics: int = 200,
    ) -> ToolResponse:
        """Classify log lines against known Unity error patterns.

        Args:
            log_lines: Raw log lines (typically from ``collect_unity_console``).
            max_diagnostics: Maximum number of diagnostic entries to return.

        Returns:
            ``ToolResponse`` with ``data.categories`` counts and
            ``data.categories_by_severity`` breakdown. Diagnostics contain
            the matched line text and category.
        """
        diagnostics: list[Diagnostic] = []
        counts = Counter[str]()
        severity_hits: list[Severity] = []
        total_hits = 0

        for index, line in enumerate(log_lines, start=1):
            for category, category_severity, pattern in _LOG_PATTERNS:
                if not pattern.search(line):
                    continue
                counts[category] += 1
                severity_hits.append(category_severity)
                total_hits += 1
                if len(diagnostics) < max_diagnostics:
                    diagnostics.append(
                        Diagnostic(
                            path="",
                            location=f"line {index}",
                            detail=category.lower(),
                            evidence=line.strip(),
                        )
                    )
                break

        severity = max_severity(severity_hits)
        if total_hits == 0:
            code = "RUN_CLASSIFY_OK"
            message = "No runtime issues matched known error categories."
            success = True
        elif severity in (Severity.ERROR, Severity.CRITICAL):
            code = "RUN001"
            message = "Runtime issues matched error or critical categories."
            success = False
        else:
            code = "RUN_CLASSIFY_WARN"
            message = "Runtime issues matched warning categories."
            success = True

        diagnostics_limit = max(0, max_diagnostics)
        truncated = max(0, total_hits - diagnostics_limit)
        return ToolResponse(
            success=success,
            severity=severity,
            code=code,
            message=message,
            data={
                "line_count": len(log_lines),
                "matched_issue_count": total_hits,
                "returned_diagnostics": len(diagnostics),
                "truncated_diagnostics": truncated,
                "categories": dict(counts),
                "categories_by_severity": {
                    "critical": sum(
                        counts.get(category, 0)
                        for category, level, _ in _LOG_PATTERNS
                        if level == Severity.CRITICAL
                    ),
                    "error": sum(
                        counts.get(category, 0)
                        for category, level, _ in _LOG_PATTERNS
                        if level == Severity.ERROR
                    ),
                    "warning": sum(
                        counts.get(category, 0)
                        for category, level, _ in _LOG_PATTERNS
                        if level == Severity.WARNING
                    ),
                },
                "read_only": True,
            },
            diagnostics=diagnostics,
        )

    def assert_no_critical_errors(
        self,
        classification_result: ToolResponse,
        allow_warnings: bool = False,
    ) -> ToolResponse:
        """Assert that a classification result contains no critical or error issues.

        Args:
            classification_result: ``ToolResponse`` from ``classify_errors``.
            allow_warnings: When ``True``, warning-level issues pass the assertion.

        Returns:
            ``ToolResponse`` with ``success=True`` when the assertion passes,
            or ``success=False`` with the failing severity counts.
        """
        severity_counts = classification_result.data.get("categories_by_severity", {})
        critical_count = int(severity_counts.get("critical", 0))
        error_count = int(severity_counts.get("error", 0))
        warning_count = int(severity_counts.get("warning", 0))

        if critical_count > 0 or error_count > 0:
            return error_response(
                "RUN001",
                "Runtime assertion failed due to critical/error issues.",
                severity=Severity.CRITICAL if critical_count > 0 else Severity.ERROR,
                data={
                    "critical_count": critical_count,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "allow_warnings": allow_warnings,
                    "read_only": True,
                },
            )

        if warning_count > 0 and not allow_warnings:
            return error_response(
                "RUN_WARNINGS",
                "Runtime assertion failed because warnings are not allowed.",
                severity=Severity.WARNING,
                data={
                    "critical_count": critical_count,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "allow_warnings": allow_warnings,
                    "read_only": True,
                },
            )

        return success_response(
            "RUN_ASSERT_OK",
            "Runtime assertion passed.",
            data={
                "critical_count": critical_count,
                "error_count": error_count,
                "warning_count": warning_count,
                "allow_warnings": allow_warnings,
                "read_only": True,
            },
        )
