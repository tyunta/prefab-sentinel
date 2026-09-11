"""Immutable result model for Unity bridge acceptance reports."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACCEPTANCE_REPORT_SCHEMA = "unity_bridge_acceptance.v1"


@dataclass(frozen=True, slots=True)
class AcceptancePhaseResult:
    """Snapshot caller payloads and detach each serialized representation."""

    name: str
    success: bool
    code: str
    data: dict[str, Any]
    diagnostics: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", deepcopy(self.data))
        object.__setattr__(
            self,
            "diagnostics",
            tuple(deepcopy(diagnostic) for diagnostic in self.diagnostics),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "code": self.code,
            "data": deepcopy(self.data),
            "diagnostics": [deepcopy(item) for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class AcceptanceEvidenceSection:
    """One controller-owned, serializable acceptance-evidence section."""

    executed: bool = False
    success: bool | None = None
    code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", deepcopy(self.data))
        object.__setattr__(
            self,
            "diagnostics",
            tuple(deepcopy(diagnostic) for diagnostic in self.diagnostics),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "success": self.success,
            "code": self.code,
            "data": deepcopy(self.data),
            "diagnostics": [deepcopy(diagnostic) for diagnostic in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class AcceptanceEvidence:
    """Explicit semantic evidence sections supplied by the controller."""

    audit: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)
    source: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)
    environment: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)
    preflight: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)
    deploy: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)
    compile: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)
    smoke: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)
    cleanup: AcceptanceEvidenceSection = field(default_factory=AcceptanceEvidenceSection)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {
            "audit": self.audit.to_dict(),
            "source": self.source.to_dict(),
            "environment": self.environment.to_dict(),
            "preflight": self.preflight.to_dict(),
            "deploy": self.deploy.to_dict(),
            "compile": self.compile.to_dict(),
            "smoke": self.smoke.to_dict(),
            "cleanup": self.cleanup.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    success: bool
    severity: str
    code: str
    message: str
    failed_phase: str | None
    phases: tuple[AcceptancePhaseResult, ...]
    diagnostics: tuple[dict[str, Any], ...] = ()
    evidence: AcceptanceEvidence = field(default_factory=AcceptanceEvidence)

    @classmethod
    def failure(
        cls,
        *,
        code: str,
        message: str,
        failed_phase: str,
        phases: Sequence[AcceptancePhaseResult],
        diagnostics: Sequence[dict[str, Any]] = (),
        evidence: AcceptanceEvidence | None = None,
    ) -> AcceptanceResult:
        return cls(
            success=False,
            severity="error",
            code=code,
            message=message,
            failed_phase=failed_phase,
            phases=tuple(phases),
            diagnostics=tuple(deepcopy(item) for item in diagnostics),
            evidence=evidence or AcceptanceEvidence(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ACCEPTANCE_REPORT_SCHEMA,
            **self.evidence.to_dict(),
            "result": {
                "success": self.success,
                "severity": self.severity,
                "code": self.code,
                "message": self.message,
                "failed_phase": self.failed_phase,
                "phases": [phase.to_dict() for phase in self.phases],
                "diagnostics": [deepcopy(item) for item in self.diagnostics],
            },
        }


@dataclass(frozen=True, slots=True)
class BridgeFileIdentity:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AcceptanceSourceIdentity:
    head: str
    branch: str | None
    managed_dirty_paths: tuple[str, ...]
    package_versions: dict[str, str]
    bridge_files: tuple[BridgeFileIdentity, ...]
    bridge_manifest_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_versions", dict(self.package_versions))


@dataclass(frozen=True, slots=True)
class CompileBaseline:
    dll_path: Path
    dll_size: int | None
    dll_mtime_ns: int | None
    dll_sha256: str | None
    log_path: Path
    log_device: int
    log_inode: int
    log_size: int


@dataclass(frozen=True, slots=True)
class CompileObservation:
    success: bool
    code: str
    dll_size: int | None
    dll_mtime_ns: int | None
    dll_sha256: str | None
    compiler_errors: tuple[str, ...] = ()
    superseded_compiler_errors: tuple[str, ...] = ()
    compile_observation: str = "observed"
