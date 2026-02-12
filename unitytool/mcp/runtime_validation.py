from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from unitytool.contracts import Diagnostic, Severity, ToolResponse, max_severity
from unitytool.unity_assets import decode_text_file, find_project_root, resolve_scope_path

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


class RuntimeValidationMcp:
    """Runtime validation MCP interface for scaffolded log-based checks."""

    TOOL_NAME = "runtime-validation-mcp"

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = find_project_root(project_root or Path.cwd())

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def compile_udonsharp(self, project_root: str | None = None) -> ToolResponse:
        target_root = self.project_root if project_root is None else resolve_scope_path(
            project_root, self.project_root
        )
        if not (target_root / "Assets").exists():
            return ToolResponse(
                success=True,
                severity=Severity.WARNING,
                code="RUN_COMPILE_SKIPPED",
                message=(
                    "compile_udonsharp skipped because project root does not contain Assets."
                ),
                data={
                    "project_root": str(target_root),
                    "read_only": True,
                    "executed": False,
                },
                diagnostics=[],
            )

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="RUN_COMPILE_SKIPPED",
            message=(
                "compile_udonsharp is scaffolded only; Unity batchmode compile is not wired."
            ),
            data={
                "project_root": self._relative(target_root),
                "read_only": True,
                "executed": False,
            },
            diagnostics=[],
        )

    def run_clientsim(self, scene_path: str, profile: str) -> ToolResponse:
        scene = resolve_scope_path(scene_path, self.project_root)
        if not scene.exists():
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="RUN002",
                message="Scene path was not found for runtime validation.",
                data={
                    "scene_path": scene_path,
                    "profile": profile,
                    "read_only": True,
                    "executed": False,
                },
                diagnostics=[],
            )
        if scene.suffix.lower() != ".unity":
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="RUN002",
                message="Runtime validation requires a .unity scene path.",
                data={
                    "scene_path": scene_path,
                    "profile": profile,
                    "read_only": True,
                    "executed": False,
                },
                diagnostics=[],
            )

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="RUN_CLIENTSIM_SKIPPED",
            message="run_clientsim is scaffolded only; Unity ClientSim execution is not wired.",
            data={
                "scene_path": self._relative(scene),
                "profile": profile,
                "read_only": True,
                "executed": False,
            },
            diagnostics=[],
        )

    def collect_unity_console(
        self,
        log_file: str | None = None,
        since_timestamp: str | None = None,
        max_lines: int = 4000,
    ) -> ToolResponse:
        log_path = (
            resolve_scope_path(log_file, self.project_root)
            if log_file
            else self.project_root / "Logs" / "Editor.log"
        )
        if not log_path.exists():
            return ToolResponse(
                success=True,
                severity=Severity.WARNING,
                code="RUN_LOG_MISSING",
                message="Unity log file was not found; classification uses empty log lines.",
                data={
                    "log_path": str(log_path),
                    "line_count": 0,
                    "log_lines": [],
                    "since_timestamp": since_timestamp,
                    "read_only": True,
                },
                diagnostics=[],
            )

        lines = decode_text_file(log_path).splitlines()
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="RUN_LOG_COLLECTED",
            message="Unity log lines collected.",
            data={
                "log_path": self._relative(log_path),
                "line_count": len(lines),
                "log_lines": lines,
                "since_timestamp": since_timestamp,
                "read_only": True,
            },
            diagnostics=[],
        )

    def classify_errors(
        self,
        log_lines: list[str],
        max_diagnostics: int = 200,
    ) -> ToolResponse:
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
        severity_counts = classification_result.data.get("categories_by_severity", {})
        critical_count = int(severity_counts.get("critical", 0))
        error_count = int(severity_counts.get("error", 0))
        warning_count = int(severity_counts.get("warning", 0))

        if critical_count > 0 or error_count > 0:
            return ToolResponse(
                success=False,
                severity=Severity.CRITICAL if critical_count > 0 else Severity.ERROR,
                code="RUN001",
                message="Runtime assertion failed due to critical/error issues.",
                data={
                    "critical_count": critical_count,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "allow_warnings": allow_warnings,
                    "read_only": True,
                },
                diagnostics=[],
            )

        if warning_count > 0 and not allow_warnings:
            return ToolResponse(
                success=False,
                severity=Severity.WARNING,
                code="RUN_WARNINGS",
                message="Runtime assertion failed because warnings are not allowed.",
                data={
                    "critical_count": critical_count,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "allow_warnings": allow_warnings,
                    "read_only": True,
                },
                diagnostics=[],
            )

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="RUN_ASSERT_OK",
            message="Runtime assertion passed.",
            data={
                "critical_count": critical_count,
                "error_count": error_count,
                "warning_count": warning_count,
                "allow_warnings": allow_warnings,
                "read_only": True,
            },
            diagnostics=[],
        )
