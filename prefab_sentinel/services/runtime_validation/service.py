"""``RuntimeValidationService`` — public class facade.

Public methods (``execute_write_profile``, ``collect_unity_console``,
``classify_errors``, ``assert_no_critical_errors``)
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
        target_root: Path,
        scene_path: str,
        profile: str,
        confirm: bool,
        change_reason: str,
        generated_asset_policy: str,
        allow_dirty_program_assets_before_compile: bool,
        allow_dirty_scenes_before_compile: bool,
    ) -> ToolResponse:
        return invoke_via_editor_bridge(
            target_root=target_root,
            scene_path=scene_path,
            profile=profile,
            relative_fn=self._relative,
            confirm=confirm,
            change_reason=change_reason,
            generated_asset_policy=generated_asset_policy,
            allow_dirty_program_assets_before_compile=(
                allow_dirty_program_assets_before_compile
            ),
            allow_dirty_scenes_before_compile=allow_dirty_scenes_before_compile,
        )





    def execute_write_profile(
        self,
        *,
        scene_path: str,
        profile: str,
        confirm: bool,
        change_reason: str,
        generated_asset_policy: str,
        allow_dirty_program_assets_before_compile: bool,
        allow_dirty_scenes_before_compile: bool,
    ) -> ToolResponse:
        """Execute one audited write-class runtime profile through one Bridge action."""
        target_root = default_runtime_root(self.project_root)
        if not (target_root / "Assets").exists():
            return skip_response(
                code="RUN_COMPILE_SKIPPED",
                message=(
                    "validate_runtime skipped because project root does not contain Assets."
                ),
                data={"project_root": str(target_root)},
            )

        resolved_root = target_root.resolve()
        scene = resolve_scope_path(scene_path, target_root).resolve()
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
        if profile not in ("compile_only", "clientsim"):
            return error_response(
                "VALIDATE_RUNTIME_PROFILE_UNSUPPORTED",
                "Write execution requires compile_only or clientsim.",
                data=rejection_data,
            )

        response = self._invoke_unity_runtime(
            target_root=target_root,
            scene_path=self._relative(scene),
            profile=profile,
            confirm=confirm,
            change_reason=change_reason,
            generated_asset_policy=generated_asset_policy,
            allow_dirty_program_assets_before_compile=(
                allow_dirty_program_assets_before_compile
            ),
            allow_dirty_scenes_before_compile=allow_dirty_scenes_before_compile,
        )
        if profile != "clientsim":
            return response

        from prefab_sentinel.services.runtime_validation.editor_bridge_invoke import (
            with_clientsim_side_effect_diagnostics,
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
                        "console_authority": "unity_log",
                        "evidence_available": False,
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
                "Unity log file was not found; Console evidence is unavailable.",
                severity=Severity.WARNING,
                data={
                    "log_path": str(log_path),
                    "line_count": 0,
                    "log_lines": [],
                    "console_authority": "unity_log",
                    "evidence_available": False,
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
                "Unity log file could not be decoded as UTF-8; Console evidence is unavailable.",
                severity=Severity.WARNING,
                data={
                    "log_path": self._relative(log_path),
                    "line_count": 0,
                    "log_lines": [],
                    "console_authority": "unity_log",
                    "evidence_available": False,
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
                "console_authority": "unity_log",
                "evidence_available": True,
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
