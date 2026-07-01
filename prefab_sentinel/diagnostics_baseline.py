from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import ToolResponse, error_response

BASELINE_RELATIVE_PATH = Path("config") / "diagnostics_baseline.json"
DIAGNOSTICS_BASELINE_INVALID = "DIAGNOSTICS_BASELINE_INVALID"
BASELINE_SCHEMA_MESSAGE = (
    "diagnostics_baseline.json must be a JSON object with version 1 "
    "and known_diagnostics as a list of non-empty string keys"
)

@dataclass(frozen=True, slots=True)
class DiagnosticKeyRecord:
    key: str
    severity: str = "warning"
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "severity": self.severity,
            "message": self.message,
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class DiagnosticsBaseline:
    known_diagnostics: tuple[str, ...]
    path: str | None
    status: str


@dataclass(frozen=True, slots=True)
class DiagnosticsBaselineLoadResult:
    baseline: DiagnosticsBaseline
    error: ToolResponse | None


@dataclass(frozen=True, slots=True)
class DiagnosticsClassification:
    baseline: DiagnosticsBaseline
    new: tuple[DiagnosticKeyRecord, ...]
    known: tuple[DiagnosticKeyRecord, ...]
    resolved: tuple[DiagnosticKeyRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.baseline.status,
            "path": self.baseline.path,
            "new_count": len(self.new),
            "known_count": len(self.known),
            "resolved_count": len(self.resolved),
            "new": [record.to_dict() for record in self.new],
            "known": [record.to_dict() for record in self.known],
            "resolved": [record.to_dict() for record in self.resolved],
        }


def _empty_baseline(path: Path | None, status: str) -> DiagnosticsBaseline:
    return DiagnosticsBaseline(
        known_diagnostics=(),
        path=None if path is None else str(path),
        status=status,
    )


def _invalid_baseline_error(path: Path) -> ToolResponse:
    return error_response(
        DIAGNOSTICS_BASELINE_INVALID,
        BASELINE_SCHEMA_MESSAGE,
        data={"path": str(path), "read_only": True},
    )

def _invalid_baseline_result(path: Path) -> DiagnosticsBaselineLoadResult:
    return DiagnosticsBaselineLoadResult(
        baseline=_empty_baseline(path, "invalid"),
        error=_invalid_baseline_error(path),
    )

def diagnostics_baseline_path(project_root: str | Path) -> tuple[Path, ToolResponse | None]:
    baseline_path = Path(project_root) / BASELINE_RELATIVE_PATH
    try:
        root = Path(project_root).resolve()
        parent = baseline_path.parent
        if baseline_path.is_symlink() or parent.is_symlink():
            return baseline_path, _invalid_baseline_error(baseline_path)
        if parent.exists():
            if not parent.is_dir():
                return baseline_path, _invalid_baseline_error(baseline_path)
            parent.resolve().relative_to(root)
        if baseline_path.exists():
            baseline_path.resolve().relative_to(root)
            if not baseline_path.is_file():
                return baseline_path, _invalid_baseline_error(baseline_path)
    except (OSError, RuntimeError):
        return baseline_path, _invalid_baseline_error(baseline_path)
    except ValueError:
        return baseline_path, _invalid_baseline_error(baseline_path)
    return baseline_path, None


def _known_diagnostics_from_payload(payload: object) -> tuple[str, ...] | None:
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    if type(version) is not int or version != 1:
        return None
    known_diagnostics = payload.get("known_diagnostics")
    if not isinstance(known_diagnostics, list):
        return None
    if any(not isinstance(key, str) or not key for key in known_diagnostics):
        return None
    return tuple(known_diagnostics)

def open_diagnostics_baseline_parent_fd(
    project_root: str | Path,
    *,
    create_parent: bool,
) -> tuple[Path, int | None, ToolResponse | None]:
    import os

    baseline_path, path_error = diagnostics_baseline_path(project_root)
    if path_error is not None:
        return baseline_path, None, path_error

    parent = baseline_path.parent
    try:
        if create_parent:
            parent.mkdir(parents=True, exist_ok=True)
        elif not parent.exists():
            return baseline_path, None, None
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            return baseline_path, None, _invalid_baseline_error(baseline_path)
        parent_fd = os.open(
            parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow,
        )
    except OSError:
        return baseline_path, None, _invalid_baseline_error(baseline_path)
    return baseline_path, parent_fd, None


def load_diagnostics_baseline(project_root: str | Path | None) -> DiagnosticsBaselineLoadResult:
    if project_root is None:
        return DiagnosticsBaselineLoadResult(
            baseline=_empty_baseline(None, "not_loaded_no_project_root"),
            error=None,
        )

    baseline_path, parent_fd, path_error = open_diagnostics_baseline_parent_fd(
        project_root,
        create_parent=False,
    )
    if path_error is not None:
        return DiagnosticsBaselineLoadResult(
            baseline=_empty_baseline(baseline_path, "invalid"),
            error=path_error,
        )
    if parent_fd is None:
        return DiagnosticsBaselineLoadResult(
            baseline=_empty_baseline(baseline_path, "absent"),
            error=None,
        )

    try:
        import os

        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            return _invalid_baseline_result(baseline_path)
        file_fd: int | None = None
        try:
            try:
                file_fd = os.open(
                    baseline_path.name,
                    os.O_RDONLY | nofollow,
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                return DiagnosticsBaselineLoadResult(
                    baseline=_empty_baseline(baseline_path, "absent"),
                    error=None,
                )
            except OSError:
                return _invalid_baseline_result(baseline_path)
            with os.fdopen(file_fd, encoding="utf-8") as handle:
                file_fd = None
                payload = json.load(handle)
        except (OSError, JSONDecodeError, UnicodeDecodeError):
            return _invalid_baseline_result(baseline_path)
        finally:
            if file_fd is not None:
                os.close(file_fd)
    finally:
        import os

        os.close(parent_fd)

    known_diagnostics = _known_diagnostics_from_payload(payload)
    if known_diagnostics is None:
        return _invalid_baseline_result(baseline_path)

    return DiagnosticsBaselineLoadResult(
        baseline=DiagnosticsBaseline(
            known_diagnostics=known_diagnostics,
            path=str(baseline_path),
            status="loaded",
        ),
        error=None,
    )


def classify_current_keys(
    current: Sequence[DiagnosticKeyRecord],
    baseline: DiagnosticsBaseline,
) -> DiagnosticsClassification:
    known_keys = set(baseline.known_diagnostics)
    current_keys = {record.key for record in current}
    return DiagnosticsClassification(
        baseline=baseline,
        new=tuple(record for record in current if record.key not in known_keys),
        known=tuple(record for record in current if record.key in known_keys),
        resolved=tuple(
            DiagnosticKeyRecord(key=key)
            for key in baseline.known_diagnostics
            if key not in current_keys
        ),
    )
