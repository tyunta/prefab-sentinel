from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from prefab_sentinel.services.reference_resolver import ReferenceResolverService
from prefab_sentinel.unity_assets import extract_meta_guid
from prefab_sentinel.unity_assets_path import relative_to_root

__all__ = ["build_delete_plan", "compute_broken_reference_delta"]


def _result(
    success: bool,
    code: str,
    message: str,
    *,
    data: dict[str, Any],
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "code": code,
        "message": message,
        "data": data,
        "diagnostics": diagnostics or [],
    }


def _target_error(
    code: str,
    message: str,
    field: str,
    asset_path: str,
) -> dict[str, Any]:
    return _result(
        False,
        code,
        message,
        data={"targets": [], field: asset_path},
    )


def _resolve_project_asset(asset_path: str, project_root: Path) -> tuple[Path, str] | dict[str, Any]:
    root = project_root.resolve()
    raw = Path(asset_path)
    resolved = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not resolved.is_relative_to(root):
        return _target_error(
            "ASSET_DELETE_EXTERNAL_PACKAGE_UNSUPPORTED",
            "Asset deletion is only supported under the project Assets tree.",
            "rejected_path",
            asset_path,
        )

    rel = resolved.relative_to(root).as_posix()
    parts = Path(rel).parts
    if not parts or parts[0] != "Assets":
        return _target_error(
            "ASSET_DELETE_EXTERNAL_PACKAGE_UNSUPPORTED",
            "Asset deletion is only supported under the project Assets tree.",
            "rejected_path",
            asset_path,
        )
    if not resolved.exists():
        return _target_error(
            "ASSET_DELETE_NOT_FOUND",
            "Target asset path does not exist.",
            "missing_asset_path",
            asset_path,
        )
    return resolved, rel


def _meta_path(asset_path: Path, project_root: Path) -> tuple[Path, str]:
    meta = Path(str(asset_path) + ".meta")
    return meta, relative_to_root(meta, project_root)


def _reference_scope_for_target(relative_asset_path: str, *, explicit_scope: str | None) -> str:
    if explicit_scope is not None:
        return explicit_scope
    if "/" not in relative_asset_path:
        return "Assets"
    parent = relative_asset_path.rsplit("/", 1)[0]
    return parent or "Assets"


def _reference_impact(
    reference_resolver: ReferenceResolverService,
    guid: str | None,
    *,
    scope: str | None,
    exclude_patterns: tuple[str, ...],
    max_usages: int,
) -> dict[str, Any]:
    if not guid:
        return {
            "success": False,
            "code": "ASSET_DELETE_META_GUID_MISSING",
            "usages": [],
            "usage_count": 0,
        }

    response = reference_resolver.where_used(
        guid,
        scope=scope,
        exclude_patterns=exclude_patterns,
        max_usages=max_usages,
    )
    if response.success:
        return dict(response.data)
    return {
        "success": False,
        "code": response.code,
        "message": response.message,
        "usages": [],
        "usage_count": 0,
    }


def _scan_data(response: Any) -> dict[str, Any]:
    if hasattr(response, "to_dict"):
        wire = response.to_dict()
        data = wire.get("data")
        return data if isinstance(data, dict) else {}
    if isinstance(response, Mapping):
        nested = response.get("data")
        if isinstance(nested, dict):
            return nested
        return dict(response)
    return {}


def _scan_failure_response(response: Any) -> dict[str, Any] | None:
    wire: dict[str, Any]
    if hasattr(response, "to_dict"):
        wire = dict(response.to_dict())
    elif isinstance(response, Mapping):
        wire = dict(response)
    else:
        return None
    if "success" in wire and wire["success"] is False:
        if wire.get("code") == "REF_SCAN_BROKEN":
            return None
        return wire
    return None


def _pre_delete_baseline(
    reference_resolver: ReferenceResolverService,
    *,
    scope: str | None,
    exclude_patterns: tuple[str, ...],
) -> dict[str, Any]:
    scan_scope = scope if scope is not None else "Assets"
    response = reference_resolver.scan_broken_references(
        scope=scan_scope,
        include_diagnostics=False,
        exclude_patterns=exclude_patterns,
    )
    failure = _scan_failure_response(response)
    if failure is not None:
        return failure
    return _scan_data(response)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _udonsharp_program_matches(
    *,
    project_root: Path,
    script_guid: str | None,
    target_path: Path,
) -> list[str]:
    if target_path.suffix.lower() != ".cs" or not script_guid:
        return []

    matches: list[str] = []
    for candidate in sorted((project_root / "Assets").rglob("*.asset")):
        try:
            text = _read_text(candidate)
        except (OSError, UnicodeDecodeError):
            continue
        if "UdonSharpProgramAsset" in text and script_guid in text:
            matches.append(relative_to_root(candidate, project_root))
    return matches


def _related_udon_candidates(
    *,
    project_root: Path,
    script_guid: str | None,
    target_path: Path,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    matches = _udonsharp_program_matches(
        project_root=project_root,
        script_guid=script_guid,
        target_path=target_path,
    )
    if len(matches) == 1:
        return (
            [
                {
                    "asset_path": matches[0],
                    "reason": "udonsharp_program_asset",
                    "candidate_status": "deterministic",
                }
            ],
            [],
        )
    if len(matches) > 1:
        return (
            [],
            [
                {
                    "code": "ASSET_DELETE_DECISION_REQUIRED",
                    "detail": "ambiguous_udonsharp_program_asset",
                    "asset_paths": matches,
                }
            ],
        )
    return [], []


def build_delete_plan(
    asset_paths: Sequence[str],
    *,
    project_root: Path,
    reference_resolver: ReferenceResolverService,
    scope: str | None = None,
    exclude_patterns: tuple[str, ...] = (),
    max_usages: int = 100,
) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    related_candidates: list[dict[str, str]] = []
    diagnostics: list[dict[str, Any]] = []
    plan_scope = scope if scope is not None else "Assets"

    for asset_path in asset_paths:
        resolved = _resolve_project_asset(asset_path, project_root)
        if isinstance(resolved, dict):
            return resolved

        target_path, relative_asset_path = resolved
        meta_path, relative_meta_path = _meta_path(target_path, project_root)
        if meta_path.exists():
            try:
                guid = extract_meta_guid(meta_path)
            except (OSError, UnicodeDecodeError):
                return _target_error(
                    "ASSET_DELETE_META_UNREADABLE",
                    "Target asset .meta file could not be read.",
                    "meta_path",
                    relative_meta_path,
                )
        else:
            guid = None
        reference_scope = _reference_scope_for_target(
            relative_asset_path,
            explicit_scope=scope,
        )
        reference_impact = _reference_impact(
            reference_resolver,
            guid,
            scope=reference_scope,
            exclude_patterns=exclude_patterns,
            max_usages=max_usages,
        )
        candidates, candidate_diagnostics = _related_udon_candidates(
            project_root=project_root,
            script_guid=guid,
            target_path=target_path,
        )
        related_candidates.extend(candidates)
        diagnostics.extend(candidate_diagnostics)
        targets.append(
            {
                "asset_path": relative_asset_path,
                "meta_path": relative_meta_path,
                "asset_exists": target_path.exists(),
                "meta_exists": meta_path.exists(),
                "deletable": True,
                "reference_impact": reference_impact,
            }
        )

    pre_delete_broken_references = _pre_delete_baseline(
        reference_resolver,
        scope=plan_scope,
        exclude_patterns=exclude_patterns,
    )
    if _scan_failure_response(pre_delete_broken_references) is not None:
        return pre_delete_broken_references

    decision_required = [
        dict(diagnostic)
        for diagnostic in diagnostics
        if diagnostic.get("code") == "ASSET_DELETE_DECISION_REQUIRED"
    ]
    return _result(
        True,
        "ASSET_DELETE_DRY_RUN",
        "Asset delete dry-run plan completed.",
        data={
            "targets": targets,
            "related_candidates": related_candidates,
            "decision_required": decision_required,
            "pre_delete_broken_references": pre_delete_broken_references,
            "read_only": True,
        },
        diagnostics=diagnostics,
    )


def compute_broken_reference_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    before_data = _scan_data(before)
    after_data = _scan_data(after)
    before_categories = before_data.get("categories", {})
    after_categories = after_data.get("categories", {})
    category_names = sorted(set(before_categories) | set(after_categories))
    before_guids = set(before_data.get("unique_missing_asset_guids", []))
    after_guids = set(after_data.get("unique_missing_asset_guids", []))

    return {
        "before_broken_count": int(before_data.get("broken_count", 0)),
        "after_broken_count": int(after_data.get("broken_count", 0)),
        "broken_count_delta": (
            int(after_data.get("broken_count", 0))
            - int(before_data.get("broken_count", 0))
        ),
        "categories_delta": {
            name: int(after_categories.get(name, 0)) - int(before_categories.get(name, 0))
            for name in category_names
        },
        "new_missing_asset_guids": sorted(after_guids - before_guids),
    }
