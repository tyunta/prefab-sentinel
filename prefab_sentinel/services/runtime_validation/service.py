"""``RuntimeValidationService`` — public class facade.

Public methods (``compile_udonsharp``, ``run_clientsim``,
``collect_unity_console``, ``classify_errors``, ``assert_no_critical_errors``)
delegate to pure-function helpers in sibling modules; this file owns the
project-root resolution and the relative-path helper passed down to the
helpers.  All Unity-bound dispatch flows through the resident Editor
Bridge via ``invoke_via_editor_bridge``.
"""

from __future__ import annotations

from pathlib import Path

from prefab_sentinel.contracts import Severity, ToolResponse, error_response, success_response
from prefab_sentinel.services.runtime_validation.classification import (
    assert_no_critical_errors as _classification_assert_no_critical_errors,
    classify_errors as _classification_classify_errors,
)
from prefab_sentinel.services.runtime_validation.config import (
    default_runtime_root,
    skip_response,
)
from prefab_sentinel.services.runtime_validation.editor_bridge_invoke import (
    invoke_via_editor_bridge,
)
from prefab_sentinel.unity_assets import decode_text_file, find_project_root
from prefab_sentinel.unity_assets_path import relative_to_root, resolve_scope_path


class RuntimeValidationService:
    """Runtime validation service: log classification + Editor Bridge dispatch."""

    TOOL_NAME = "runtime-validation"

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = find_project_root(project_root or Path.cwd())

    def _relative(self, path: Path) -> str:
        return relative_to_root(path, self.project_root)

    def _invoke_unity_runtime(
        self,
        *,
        action: str,
        target_root: Path,
        scene_path: str | None = None,
        profile: str | None = None,
        confirm: bool = False,
        change_reason: str | None = None,
        allow_dirty_before: bool = False,
    ) -> ToolResponse:
        return invoke_via_editor_bridge(
            action=action,
            target_root=target_root,
            scene_path=scene_path,
            profile=profile,
            relative_fn=self._relative,
            confirm=confirm,
            change_reason=change_reason,
            allow_dirty_before=allow_dirty_before,
        )





    def compile_udonsharp(self, project_root: str | None = None) -> ToolResponse:
        """Trigger an UdonSharp compilation via the resident Editor Bridge.

        Args:
            project_root: Unity project root path. Uses the configured
                default when ``None``.

        Returns:
            ``ToolResponse`` with the compile result from the Unity
            runtime; ``RUN_COMPILE_SKIPPED`` when *target_root* lacks an
            ``Assets/`` directory.
        """
        target_root = (
            default_runtime_root(self.project_root)
            if project_root is None
            else resolve_scope_path(project_root, self.project_root)
        )
        if not (target_root / "Assets").exists():
            return skip_response(
                code="RUN_COMPILE_SKIPPED",
                message="compile_udonsharp skipped because project root does not contain Assets.",
                data={"project_root": str(target_root)},
            )
        return self._invoke_unity_runtime(
            action="compile_udonsharp",
            target_root=target_root,
        )

    def run_clientsim(
        self,
        scene_path: str,
        profile: str,
        confirm: bool = False,
        change_reason: str | None = None,
        allow_dirty_before: bool = False,
    ) -> ToolResponse:
        target_root = default_runtime_root(self.project_root)
        resolved_root = Path(target_root).resolve()
        scene = resolve_scope_path(scene_path, target_root)
        rejection_data = {
            "scene_path": scene_path,
            "profile": profile,
            "read_only": True,
            "executed": False,
        }
        if not scene.is_relative_to(resolved_root):
            return error_response(
                "RUN002",
                "Scene path resolves outside runtime root.",
                data=rejection_data,
            )
        if not scene.exists():
            return error_response(
                "RUN002",
                "Scene path was not found for runtime validation.",
                data=rejection_data,
            )
        if scene.suffix.lower() != ".unity":
            return error_response(
                "RUN002",
                "Runtime validation requires a .unity scene path.",
                data=rejection_data,
            )
        if profile != "clientsim":
            return error_response(
                "VALIDATE_RUNTIME_PROFILE_UNSUPPORTED",
                "ClientSim execution requires the clientsim runtime validation profile.",
                data=rejection_data,
            )
        if not confirm or change_reason is None or not change_reason.strip():
            return error_response(
                "CLIENTSIM_CONFIRM_REQUIRED",
                "ClientSim validation requires explicit audit confirmation and a non-empty change reason.",
                data=rejection_data,
            )

        from prefab_sentinel.services.runtime_validation.editor_bridge_invoke import (
            with_clientsim_side_effect_diagnostics,
        )

        response = self._invoke_unity_runtime(
            action="run_clientsim",
            target_root=target_root,
            scene_path=self._relative(scene),
            profile=profile,
            confirm=confirm,
            change_reason=change_reason.strip(),
            allow_dirty_before=allow_dirty_before,
        )
        return with_clientsim_side_effect_diagnostics(response)

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
        runtime_root = default_runtime_root(self.project_root)
        # Issue #96: when the caller supplies an explicit log_file, require
        # the resolved real path to be contained within the resolved
        # runtime_root. Fail-closed before any filesystem read so symlinks,
        # absolute paths, and `..` traversal cannot escape the root.
        if log_file is not None:
            resolved_root = Path(runtime_root).resolve()
            resolved_log = resolve_scope_path(log_file, runtime_root).resolve()
            if not resolved_log.is_relative_to(resolved_root):
                return error_response(
                    "RUN_CONFIG_ERROR",
                    "log_file resolves outside runtime_root.",
                    severity=Severity.ERROR,
                    data={
                        "log_file": log_file,
                        "runtime_root": str(resolved_root),
                        "read_only": True,
                        "executed": False,
                    },
                )
            log_path = resolved_log
        else:
            log_path = runtime_root / "Logs" / "Editor.log"

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

        # Issue #95: a garbled Unity log must not propagate as an exception.
        # Surface it as a warning-severity success with empty log lines.
        try:
            text = decode_text_file(log_path)
        except UnicodeDecodeError:
            return success_response(
                "RUN_LOG_DECODE_WARN",
                "Unity log file could not be decoded as UTF-8; returning empty log lines.",
                severity=Severity.WARNING,
                data={
                    "log_path": self._relative(log_path),
                    "line_count": 0,
                    "log_lines": [],
                    "since_timestamp": since_timestamp,
                    "read_only": True,
                },
            )
        lines = text.splitlines()
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

    def collect_editor_console(
        self,
        since_timestamp: str | None = None,
        max_lines: int = 4000,
    ) -> ToolResponse:
        from prefab_sentinel.services.runtime_validation.editor_bridge_invoke import (
            collect_editor_console_via_bridge,
        )

        return collect_editor_console_via_bridge(
            since_timestamp=since_timestamp,
            max_lines=max_lines,
        )

    def classify_errors(
        self,
        log_lines: list[str],
        max_diagnostics: int = 200,
    ) -> ToolResponse:
        """Classify log lines against known Unity error patterns.

        See :func:`classification.classify_errors` for the full data-key
        contract pinned by issue #89.
        """
        return _classification_classify_errors(log_lines, max_diagnostics)

    def assert_no_critical_errors(
        self,
        classification_result: ToolResponse,
        allow_warnings: bool = False,
    ) -> ToolResponse:
        """Assert that a classification result contains no critical / error issues."""
        return _classification_assert_no_critical_errors(
            classification_result, allow_warnings
        )
