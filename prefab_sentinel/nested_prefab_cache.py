from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.parallel_scan import run_ordered
from prefab_sentinel.unity_assets import (
    SOURCE_PREFAB_PATTERN,
    decode_text_file,
    normalize_guid,
)
from prefab_sentinel.unity_assets_path import relative_to_root
from prefab_sentinel.unity_yaml_parser import (
    CLASS_ID_PREFAB_INSTANCE,
    MAX_NESTED_DEPTH,
    YamlBlock,
    split_yaml_blocks,
)


@dataclass(frozen=True, slots=True)
class NestedPrefabChildRecord:
    source_guid: str
    path: Path | None
    rel_posix: str
    text: str | None
    blocks: Sequence[YamlBlock]
    diagnostic: Diagnostic | None


@dataclass(frozen=True, slots=True)
class NestedPrefabPrefetchResult:
    children: Sequence[NestedPrefabChildRecord]


class NestedPrefabCache:
    def __init__(self) -> None:
        from concurrent.futures import Future
        from threading import Lock

        self._records: dict[tuple[str, Path], NestedPrefabChildRecord] = {}
        self._inflight: dict[tuple[str, Path], Future[NestedPrefabChildRecord]] = {}
        self._version = 0
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._inflight.clear()
            self._version += 1

    def invalidate_path(self, path: Path) -> None:
        resolved = path.resolve()
        with self._lock:
            stale_record_keys = [
                key for key in self._records
                if key[1].resolve() == resolved
            ]
            stale_inflight_keys = [
                key for key in self._inflight
                if key[1].resolve() == resolved
            ]
            if stale_record_keys or stale_inflight_keys:
                self._version += 1
            for key in stale_record_keys:
                del self._records[key]
            for key in stale_inflight_keys:
                del self._inflight[key]

    def load_child(
        self,
        source_guid: str,
        path: Path,
        project_root: Path,
    ) -> NestedPrefabChildRecord:
        key = (source_guid, path)
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                return record
            future = self._inflight.get(key)
            if future is None:
                from concurrent.futures import Future

                future = Future()
                self._inflight[key] = future
                load_version = self._version
                should_load = True
            else:
                load_version = -1
                should_load = False

        if not should_load:
            return future.result()

        try:
            record = self._load_record(source_guid, path, project_root)
        except BaseException as exc:
            with self._lock:
                if self._inflight.get(key) is future:
                    del self._inflight[key]
            future.set_exception(exc)
            raise

        with self._lock:
            if self._inflight.get(key) is future:
                del self._inflight[key]
            if self._version == load_version:
                self._records[key] = record
        future.set_result(record)
        return record

    def prefetch_children(
        self,
        text: str,
        guid_index: Mapping[str, Path],
        project_root: Path,
        *,
        max_workers: int | None = None,
    ) -> NestedPrefabPrefetchResult:
        return NestedPrefabPrefetchResult(tuple(
            self._prefetch_children(
                text,
                guid_index,
                project_root,
                max_workers=max_workers,
                depth=0,
            )
        ))

    def _prefetch_children(
        self,
        text: str,
        guid_index: Mapping[str, Path],
        project_root: Path,
        *,
        max_workers: int | None,
        depth: int,
        blocks: Sequence[YamlBlock] | None = None,
    ) -> list[NestedPrefabChildRecord]:
        child_guids = _source_guids(text) if blocks is None else _source_guids_from_blocks(blocks)
        unique_sources: list[tuple[str, Path]] = []
        missing_records: dict[str, NestedPrefabChildRecord] = {}
        for source_guid in child_guids:
            source_path = guid_index.get(source_guid)
            if source_path is None or not source_path.exists():
                missing_records[source_guid] = _missing_record(source_guid)
                continue
            key = (source_guid, source_path)
            with self._lock:
                record_is_cached = key in self._records
            if not record_is_cached and key not in unique_sources:
                unique_sources.append(key)

        if unique_sources:
            run_ordered(
                unique_sources,
                lambda item: self.load_child(item[0], item[1], project_root),
                max_workers=max_workers,
            )

        children: list[NestedPrefabChildRecord] = []
        for source_guid in child_guids:
            source_path = guid_index.get(source_guid)
            if source_path is None or not source_path.exists():
                children.append(missing_records[source_guid])
                continue
            record = self.load_child(source_guid, source_path, project_root)
            children.append(record)
            if record.text is not None and depth + 1 < MAX_NESTED_DEPTH:
                children.extend(self._prefetch_children(
                    record.text,
                    guid_index,
                    project_root,
                    max_workers=max_workers,
                    depth=depth + 1,
                    blocks=record.blocks,
                ))
        return children

    def _load_record(
        self,
        source_guid: str,
        path: Path,
        project_root: Path,
    ) -> NestedPrefabChildRecord:
        rel_posix = relative_to_root(path, project_root)
        try:
            text = decode_text_file(path)
            blocks = tuple(split_yaml_blocks(text))
        except (OSError, UnicodeDecodeError):
            return NestedPrefabChildRecord(
                source_guid=source_guid,
                path=path,
                rel_posix=rel_posix,
                text=None,
                blocks=(),
                diagnostic=Diagnostic(
                    path=rel_posix,
                    location="nested_prefab_instance",
                    detail="NESTED_PREFAB_SOURCE_UNREADABLE",
                    evidence=(
                        f"Nested PrefabInstance source GUID {source_guid} "
                        "could not be decoded."
                    ),
                ),
            )
        return NestedPrefabChildRecord(
            source_guid=source_guid,
            path=path,
            rel_posix=rel_posix,
            text=text,
            blocks=blocks,
            diagnostic=None,
        )


def _source_guids(text: str) -> list[str]:
    return _source_guids_from_blocks(split_yaml_blocks(text))


def _source_guids_from_blocks(blocks: Sequence[YamlBlock]) -> list[str]:
    guids: list[str] = []
    for block in blocks:
        if block.class_id != CLASS_ID_PREFAB_INSTANCE:
            continue
        source_match = SOURCE_PREFAB_PATTERN.search(block.text)
        if source_match is None:
            continue
        guids.append(normalize_guid(source_match.group(2)))
    return guids


def _missing_record(source_guid: str) -> NestedPrefabChildRecord:
    return NestedPrefabChildRecord(
        source_guid=source_guid,
        path=None,
        rel_posix="",
        text=None,
        blocks=(),
        diagnostic=Diagnostic(
            path="",
            location="nested_prefab_instance",
            detail="NESTED_PREFAB_SOURCE_UNRESOLVED",
            evidence=(
                f"Nested PrefabInstance source GUID {source_guid} "
                "could not be resolved."
            ),
        ),
    )
