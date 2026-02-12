from __future__ import annotations

import fnmatch
import os
from collections import Counter
from pathlib import Path

from unitytool.contracts import Diagnostic, Severity, ToolResponse
from unitytool.unity_assets import (
    DEFAULT_EXCLUDED_DIR_NAMES,
    collect_project_guid_index,
    decode_text_file,
    extract_meta_guid,
    extract_local_file_ids,
    find_project_root,
    is_unity_builtin_guid,
    is_unity_text_asset,
    iter_references,
    looks_like_guid,
    normalize_guid,
    resolve_scope_path,
)

class ReferenceResolverMcp:
    """Read-only reference resolver MCP interface for Phase 1."""

    TOOL_NAME = "reference-resolver-mcp"

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = find_project_root(project_root or Path.cwd())
        self._guid_index_cache: dict[Path, dict[str, Path]] = {}
        self._text_cache: dict[Path, str | None] = {}
        self._local_id_cache: dict[Path, set[str]] = {}
        self._unreadable_paths: set[Path] = set()

    def _guid_map(self, index_root: Path | None = None) -> dict[str, Path]:
        root = (index_root or self.project_root).resolve()
        cached = self._guid_index_cache.get(root)
        if cached is None:
            cached = collect_project_guid_index(
                project_root=root,
                excluded_dir_names=DEFAULT_EXCLUDED_DIR_NAMES,
            )
            self._guid_index_cache[root] = cached
        return cached

    def _read_text(self, path: Path) -> str | None:
        cached = self._text_cache.get(path)
        if cached is not None or path in self._unreadable_paths:
            return cached
        try:
            text = decode_text_file(path)
        except UnicodeDecodeError:
            self._unreadable_paths.add(path)
            self._text_cache[path] = None
            return None
        self._text_cache[path] = text
        return text

    def _local_ids(self, path: Path) -> set[str]:
        cached = self._local_id_cache.get(path)
        if cached is not None:
            return cached
        if not is_unity_text_asset(path):
            ids: set[str] = set()
        else:
            text = self._read_text(path)
            ids = extract_local_file_ids(text) if text is not None else set()
        self._local_id_cache[path] = ids
        return ids

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    @staticmethod
    def _normalize_pattern(path_pattern: str) -> str:
        return path_pattern.replace("\\", "/")

    def _is_excluded(
        self,
        path: Path,
        scope_path: Path,
        exclude_patterns: tuple[str, ...],
    ) -> bool:
        try:
            rel = path.resolve().relative_to(scope_path.resolve()).as_posix()
        except ValueError:
            rel = path.resolve().as_posix()

        parts = {part.lower() for part in Path(rel).parts}
        if parts & DEFAULT_EXCLUDED_DIR_NAMES:
            return True

        if not exclude_patterns:
            return False

        return any(
            fnmatch.fnmatch(rel, self._normalize_pattern(pattern))
            for pattern in exclude_patterns
        )

    def _collect_scope_files(
        self,
        scope_path: Path,
        exclude_patterns: tuple[str, ...],
    ) -> list[Path]:
        if scope_path.is_file():
            if (
                is_unity_text_asset(scope_path)
                and not self._is_excluded(scope_path, scope_path.parent, exclude_patterns)
            ):
                return [scope_path]
            return []

        files: list[Path] = []
        for root, dirnames, filenames in os.walk(scope_path):
            root_path = Path(root)
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not self._is_excluded(root_path / dirname, scope_path, exclude_patterns)
            ]

            for filename in filenames:
                path = root_path / filename
                if not is_unity_text_asset(path):
                    continue
                if self._is_excluded(path, scope_path, exclude_patterns):
                    continue
                files.append(path)

        files.sort()
        return files

    def _resolve_scan_project_root(self, scope_path: Path) -> Path:
        scope_anchor = scope_path if scope_path.is_dir() else scope_path.parent
        candidate = find_project_root(scope_anchor)
        if (candidate / "Assets").exists():
            return candidate
        return self.project_root

    @staticmethod
    def _should_validate_external_file_id(target: Path) -> bool:
        # Unity prefab references often use imported local IDs (e.g. 100100000)
        # that don't appear in raw prefab YAML anchors, so validating those IDs
        # against text anchors causes many false positives.
        return target.suffix.lower() != ".prefab"

    @staticmethod
    def _normalize_ignore_guids(
        ignore_asset_guids: tuple[str, ...],
    ) -> tuple[set[str], list[str]]:
        normalized: set[str] = set()
        invalid: list[str] = []
        for raw in ignore_asset_guids:
            guid = normalize_guid(raw)
            if not looks_like_guid(guid):
                invalid.append(raw)
                continue
            if is_unity_builtin_guid(guid):
                continue
            normalized.add(guid)
        return normalized, invalid

    def resolve_reference(self, guid: str, file_id: str) -> ToolResponse:
        normalized_guid = normalize_guid(guid)
        if is_unity_builtin_guid(normalized_guid):
            return ToolResponse(
                success=True,
                severity=Severity.INFO,
                code="REF_BUILTIN",
                message="Reference points to Unity builtin resource.",
                data={"guid": normalized_guid, "file_id": file_id, "read_only": True},
                diagnostics=[],
            )

        if not looks_like_guid(normalized_guid):
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="REF001",
                message="GUID must be a 32-character hexadecimal string.",
                data={"guid": guid, "file_id": file_id, "read_only": True},
                diagnostics=[],
            )

        asset_path = self._guid_map().get(normalized_guid)
        if asset_path is None:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="REF001",
                message="GUID was not found in project meta files.",
                data={"guid": normalized_guid, "file_id": file_id, "read_only": True},
                diagnostics=[
                    Diagnostic(
                        path="",
                        location="guid",
                        detail="missing_asset",
                        evidence=f"guid {normalized_guid} not found",
                    )
                ],
            )

        diagnostics: list[Diagnostic] = []
        file_id_validated = True
        validation_note = ""
        if file_id != "0" and is_unity_text_asset(asset_path):
            if self._should_validate_external_file_id(asset_path):
                local_ids = self._local_ids(asset_path)
                if local_ids and file_id not in local_ids:
                    diagnostics.append(
                        Diagnostic(
                            path=self._relative(asset_path),
                            location="local fileID",
                            detail="missing_local_id",
                            evidence=f"fileID {file_id} not found in referenced asset",
                        )
                    )
            else:
                file_id_validated = False
                validation_note = "prefab_external_fileid_validation_skipped"

        if diagnostics:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="REF002",
                message="GUID resolved but fileID was not found in the referenced asset.",
                data={
                    "guid": normalized_guid,
                    "file_id": file_id,
                    "asset_path": self._relative(asset_path),
                    "read_only": True,
                },
                diagnostics=diagnostics,
            )

        return ToolResponse(
            success=True,
            severity=Severity.INFO,
            code="REF_RESOLVED",
            message="Reference resolved successfully.",
            data={
                "guid": normalized_guid,
                "file_id": file_id,
                "asset_path": self._relative(asset_path),
                "file_id_validated": file_id_validated,
                "validation_note": validation_note,
                "read_only": True,
            },
            diagnostics=[],
        )

    def scan_broken_references(
        self,
        scope: str,
        include_diagnostics: bool = False,
        max_diagnostics: int = 200,
        exclude_patterns: tuple[str, ...] = (),
        top_guid_limit: int = 10,
        ignore_asset_guids: tuple[str, ...] = (),
    ) -> ToolResponse:
        scope_path = resolve_scope_path(scope, self.project_root)
        if not scope_path.exists():
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="REF404",
                message="Scope path does not exist.",
                data={"scope": scope, "read_only": True},
                diagnostics=[],
            )

        ignore_guid_set, invalid_ignore_guids = self._normalize_ignore_guids(ignore_asset_guids)
        if invalid_ignore_guids:
            return ToolResponse(
                success=False,
                severity=Severity.ERROR,
                code="REF001",
                message="ignore_asset_guids must contain only 32-character hexadecimal GUIDs.",
                data={
                    "scope": scope,
                    "invalid_ignore_asset_guids": invalid_ignore_guids,
                    "read_only": True,
                },
                diagnostics=[],
            )

        max_diagnostics = max(0, max_diagnostics)
        top_guid_limit = max(1, top_guid_limit)
        files = self._collect_scope_files(scope_path, exclude_patterns)
        scan_project_root = self._resolve_scan_project_root(scope_path)
        guid_map = self._guid_map(scan_project_root)

        diagnostics: list[Diagnostic] = []
        raw_counts = Counter()
        unique_counts = Counter()
        unique_issue_keys: set[tuple[str, ...]] = set()
        missing_asset_guid_occurrences = Counter()
        ignored_missing_asset_guid_occurrences = Counter()
        scanned_files = 0
        scanned_refs = 0
        unreadable_files = 0
        total_broken = 0
        skipped_external_prefab_fileid_checks = 0

        def record_issue(
            issue_key: tuple[str, ...],
            category: str,
            path: str,
            location: str,
            evidence: str,
        ) -> None:
            nonlocal total_broken
            raw_counts[category] += 1
            if issue_key in unique_issue_keys:
                return
            unique_issue_keys.add(issue_key)
            unique_counts[category] += 1
            total_broken += 1
            if not include_diagnostics:
                return
            if len(diagnostics) >= max_diagnostics:
                return
            diagnostics.append(
                Diagnostic(
                    path=path,
                    location=location,
                    detail=category,
                    evidence=evidence,
                )
            )

        for file_path in files:
            scanned_files += 1
            text = self._read_text(file_path)
            if text is None:
                unreadable_files += 1
                continue

            local_ids: set[str] | None = None
            references = iter_references(text, include_location=include_diagnostics)
            scanned_refs += len(references)

            for ref in references:
                if ref.file_id == "0" and not ref.guid:
                    continue

                location = f"{ref.line}:{ref.column}" if ref.line and ref.column else ""
                src_path = self._relative(file_path)

                if ref.guid:
                    if is_unity_builtin_guid(ref.guid):
                        continue

                    target = guid_map.get(ref.guid)
                    if target is None:
                        if ref.guid in ignore_guid_set:
                            ignored_missing_asset_guid_occurrences[ref.guid] += 1
                            continue
                        missing_asset_guid_occurrences[ref.guid] += 1
                        record_issue(
                            ("missing_asset_guid", ref.guid),
                            "missing_asset",
                            src_path,
                            location,
                            f"{ref.raw} -> guid {ref.guid} not found",
                        )
                        continue

                    if ref.file_id != "0" and is_unity_text_asset(target):
                        if not self._should_validate_external_file_id(target):
                            skipped_external_prefab_fileid_checks += 1
                            continue

                        target_ids = self._local_ids(target)
                        # If target cannot be decoded, skip local fileID validation.
                        if target in self._unreadable_paths:
                            continue
                        if target_ids and ref.file_id not in target_ids:
                            record_issue(
                                (
                                    "missing_local_id_external",
                                    self._relative(target),
                                    ref.file_id,
                                ),
                                "missing_local_id",
                                src_path,
                                location,
                                f"{ref.raw} -> fileID {ref.file_id} not found in {self._relative(target)}",
                            )
                    continue

                if ref.file_id != "0":
                    if local_ids is None:
                        local_ids = extract_local_file_ids(text)
                    if ref.file_id in local_ids:
                        continue
                    record_issue(
                        ("missing_local_id_local", src_path, ref.file_id),
                        "missing_local_id",
                        src_path,
                        location,
                        f"{ref.raw} -> fileID {ref.file_id} not found in local objects",
                    )

        broken_occurrences = raw_counts["missing_asset"] + raw_counts["missing_local_id"]
        returned_diagnostics = len(diagnostics)
        truncated_diagnostics = (
            max(0, total_broken - returned_diagnostics) if include_diagnostics else total_broken
        )

        if total_broken > 0:
            severity = Severity.ERROR
            success = False
            code = "REF_SCAN_BROKEN"
            message = "Broken references were detected in scope."
        elif unreadable_files > 0:
            severity = Severity.WARNING
            success = True
            code = "REF_SCAN_PARTIAL"
            message = "No broken references found, but some files could not be decoded."
        else:
            severity = Severity.INFO
            success = True
            code = "REF_SCAN_OK"
            message = "No broken references were detected in scope."

        return ToolResponse(
            success=success,
            severity=severity,
            code=code,
            message=message,
            data={
                "scope": self._relative(scope_path),
                "project_root": self._relative(self.project_root),
                "scan_project_root": self._relative(scan_project_root),
                "read_only": True,
                "ignore_asset_guids": sorted(ignore_guid_set),
                "details_included": include_diagnostics,
                "max_diagnostics": max_diagnostics,
                "scanned_files": scanned_files,
                "scanned_references": scanned_refs,
                "broken_count": total_broken,
                "broken_occurrences": broken_occurrences,
                "ignored_missing_asset_unique_count": len(
                    ignored_missing_asset_guid_occurrences
                ),
                "ignored_missing_asset_occurrences": sum(
                    ignored_missing_asset_guid_occurrences.values()
                ),
                "returned_diagnostics": returned_diagnostics,
                "truncated_diagnostics": truncated_diagnostics,
                "unreadable_files": unreadable_files,
                "skipped_external_prefab_fileid_checks": skipped_external_prefab_fileid_checks,
                "exclude_patterns": list(exclude_patterns),
                "categories": {
                    "missing_asset": unique_counts["missing_asset"],
                    "missing_local_id": unique_counts["missing_local_id"],
                },
                "categories_occurrences": {
                    "missing_asset": raw_counts["missing_asset"],
                    "missing_local_id": raw_counts["missing_local_id"],
                },
                "top_missing_asset_guids": [
                    {"guid": guid, "occurrences": count}
                    for guid, count in missing_asset_guid_occurrences.most_common(top_guid_limit)
                ],
                "top_ignored_missing_asset_guids": [
                    {"guid": guid, "occurrences": count}
                    for guid, count in ignored_missing_asset_guid_occurrences.most_common(
                        top_guid_limit
                    )
                ],
            },
            diagnostics=diagnostics,
        )

    def where_used(
        self,
        asset_or_guid: str,
        scope: str | None = None,
        exclude_patterns: tuple[str, ...] = (),
        max_usages: int = 500,
    ) -> ToolResponse:
        max_usages = max(1, max_usages)
        scan_scope_path: Path | None = None
        scan_project_root = self.project_root
        if scope:
            scan_scope_path = resolve_scope_path(scope, self.project_root)
            if not scan_scope_path.exists():
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="REF404",
                    message="Scope path does not exist.",
                    data={"scope": scope, "read_only": True},
                    diagnostics=[],
                )
            scan_project_root = self._resolve_scan_project_root(scan_scope_path)

        if looks_like_guid(asset_or_guid):
            guid = normalize_guid(asset_or_guid)
            asset_path = self._guid_map(scan_project_root).get(guid)
            if asset_path is None:
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="REF001",
                    message="GUID was not found in project meta files.",
                    data={"asset_or_guid": asset_or_guid, "read_only": True},
                    diagnostics=[],
                )
        else:
            candidate = resolve_scope_path(asset_or_guid, self.project_root)
            if not candidate.exists():
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="REF404",
                    message="Target asset path does not exist.",
                    data={"asset_or_guid": asset_or_guid, "read_only": True},
                    diagnostics=[],
                )
            meta_path = candidate.with_suffix(candidate.suffix + ".meta")
            if not meta_path.exists():
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="REF001",
                    message="Target asset has no .meta GUID file.",
                    data={"asset_or_guid": asset_or_guid, "read_only": True},
                    diagnostics=[],
                )
            try:
                guid = extract_meta_guid(meta_path) or ""
            except UnicodeDecodeError:
                guid = ""
            if not looks_like_guid(guid):
                return ToolResponse(
                    success=False,
                    severity=Severity.ERROR,
                    code="REF001",
                    message="Target asset meta file does not contain a valid GUID.",
                    data={"asset_or_guid": asset_or_guid, "read_only": True},
                    diagnostics=[],
                )
            asset_path = candidate

        usages: list[dict[str, str | int]] = []
        if scan_scope_path is None:
            scan_scope_path = self.project_root
        files = self._collect_scope_files(scan_scope_path, exclude_patterns)
        scanned_files = 0
        truncated_usages = 0
        for path in files:
            scanned_files += 1
            text = self._read_text(path)
            if text is None:
                continue
            references = iter_references(text, include_location=True)
            for ref in references:
                if ref.guid == guid:
                    usage = {
                        "path": self._relative(path),
                        "line": ref.line,
                        "column": ref.column,
                        "reference": ref.raw,
                    }
                    if len(usages) < max_usages:
                        usages.append(usage)
                    else:
                        truncated_usages += 1

        if usages:
            severity = Severity.INFO
        elif truncated_usages > 0:
            severity = Severity.WARNING
        else:
            severity = Severity.WARNING

        return ToolResponse(
            success=True,
            severity=severity,
            code="REF_WHERE_USED",
            message="Reference usage scan completed.",
            data={
                "guid": guid,
                "asset_path": self._relative(asset_path),
                "scope": self._relative(scan_scope_path),
                "scan_project_root": self._relative(scan_project_root),
                "usage_count": len(usages) + truncated_usages,
                "returned_usages": len(usages),
                "truncated_usages": truncated_usages,
                "max_usages": max_usages,
                "scanned_files": scanned_files,
                "exclude_patterns": list(exclude_patterns),
                "usages": usages,
                "read_only": True,
            },
            diagnostics=[],
        )
