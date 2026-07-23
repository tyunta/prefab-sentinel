from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prefab_sentinel.inspector_profiles.model import (
    SelectedProfile,
    TargetIdentity,
    identity_match_priority,
)
from prefab_sentinel.inspector_profiles.schema import ProfileDiagnostic, validate_profile_document


class ProfileRepositoryError(ValueError):
    def __init__(self, message: str, diagnostics: tuple[ProfileDiagnostic, ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


def _plausible_offline_match_priority(
    target: object,
    identity: TargetIdentity,
) -> tuple[int, str | None] | None:
    strict_match = identity_match_priority(target, identity)
    if strict_match is not None:
        return strict_match
    if not isinstance(target, dict):
        return None
    if target.get("script_guid") is not None or target.get("script_file_id") is not None:
        return None
    managed_type = target.get("managed_type")
    if not isinstance(managed_type, str):
        return None
    if managed_type.rsplit(".", 1)[-1] != identity.managed_type.rsplit(".", 1)[-1]:
        return None
    return 3, "Profile plausibly matched by short managed_type from offline metadata."


class ProfileRepository:
    def __init__(self, project_root: Path, bundled_root: Path) -> None:
        self._project_root = project_root.resolve(strict=True)
        self._local_root = self._project_root / ".prefab-sentinel" / "profiles"
        self._bundled_root = bundled_root

    def select(self, identity: TargetIdentity) -> SelectedProfile | None:
        local = self._choose(self._matching_candidates(self._local_root, "project", identity))
        if local is not None:
            return local
        return self._choose(self._matching_candidates(self._bundled_root, "bundled", identity))

    def select_plausible_for_offline(
        self,
        identity: TargetIdentity,
    ) -> SelectedProfile | None:
        local = self._choose(
            self._matching_candidates(
                self._local_root,
                "project",
                identity,
                plausible_offline=True,
            )
        )
        if local is not None:
            return local
        return self._choose(
            self._matching_candidates(
                self._bundled_root,
                "bundled",
                identity,
                plausible_offline=True,
            )
        )

    def _matching_candidates(
        self,
        root: Path,
        source: str,
        identity: TargetIdentity,
        *,
        plausible_offline: bool = False,
    ) -> tuple[SelectedProfile, ...]:
        boundary = self._project_root if source == "project" else root
        candidates: list[SelectedProfile] = []
        for path in self._safe_profile_paths(root, boundary):
            document = self._read_document(path)
            target = document.get("target")
            match = (
                _plausible_offline_match_priority(target, identity)
                if plausible_offline
                else identity_match_priority(target, identity)
            )
            if match is None:
                continue
            diagnostics = validate_profile_document(document)
            if diagnostics:
                raise ProfileRepositoryError("matching profile is invalid", diagnostics)
            priority, warning = match
            candidates.append(SelectedProfile(path, source, priority, warning, document))
        return tuple(candidates)

    @staticmethod
    def _safe_profile_paths(root: Path, boundary: Path) -> tuple[Path, ...]:
        try:
            if root.is_symlink():
                raise ProfileRepositoryError("profile root contains a symlink")
            relative_root = root.relative_to(boundary)
            current = boundary
            for segment in relative_root.parts:
                current /= segment
                if current.is_symlink():
                    raise ProfileRepositoryError("profile root contains a symlink")
            if not root.exists():
                return ()
            if not root.is_dir():
                raise ProfileRepositoryError("profile root is not a regular directory")
            resolved_boundary = boundary.resolve(strict=True)
            resolved_root = root.resolve(strict=True)
            resolved_root.relative_to(resolved_boundary)
            paths = tuple(sorted(root.glob("*.json")))
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    raise ProfileRepositoryError("profile is not a regular file")
                path.resolve(strict=True).relative_to(resolved_root)
            return paths
        except (OSError, ValueError) as exc:
            raise ProfileRepositoryError("profile path is unsafe") from exc

    @staticmethod
    def _read_document(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProfileRepositoryError("profile JSON could not be read") from exc
        if not isinstance(payload, dict):
            raise ProfileRepositoryError("profile root must be an object")
        return payload

    @staticmethod
    def _choose(candidates: tuple[SelectedProfile, ...]) -> SelectedProfile | None:
        if not candidates:
            return None
        priority = min(candidate.priority for candidate in candidates)
        selected = tuple(candidate for candidate in candidates if candidate.priority == priority)
        if len(selected) != 1:
            raise ProfileRepositoryError(f"multiple profiles match at priority {priority}")
        return selected[0]
