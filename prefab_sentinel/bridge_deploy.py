from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from prefab_sentinel.contracts import Severity, ToolResponse, error_response, success_response

logger = logging.getLogger(__name__)

_BUILTIN_OSERROR_TYPE_NAMES: tuple[tuple[type[OSError], str], ...] = (
    (OSError, "OSError"),
    (BlockingIOError, "BlockingIOError"),
    (ChildProcessError, "ChildProcessError"),
    (ConnectionError, "ConnectionError"),
    (BrokenPipeError, "BrokenPipeError"),
    (ConnectionAbortedError, "ConnectionAbortedError"),
    (ConnectionRefusedError, "ConnectionRefusedError"),
    (ConnectionResetError, "ConnectionResetError"),
    (FileExistsError, "FileExistsError"),
    (FileNotFoundError, "FileNotFoundError"),
    (InterruptedError, "InterruptedError"),
    (IsADirectoryError, "IsADirectoryError"),
    (NotADirectoryError, "NotADirectoryError"),
    (PermissionError, "PermissionError"),
    (ProcessLookupError, "ProcessLookupError"),
    (TimeoutError, "TimeoutError"),
)


def _builtin_oserror_type_name(error: BaseException) -> str:
    return next(
        (
            name
            for builtin_type, name in _BUILTIN_OSERROR_TYPE_NAMES
            if type(error) is builtin_type
        ),
        "OSError",
    )

_BRIDGE_VERSION_FILE = "PrefabSentinel.UnityEditorControlBridge.cs"
_BRIDGE_VERSION_RE = re.compile(r'BridgeVersion\s*=\s*"([^"]+)"')
_SUPPORTED_SUFFIXES = frozenset({".cs", ".asmdef"})


OWNERSHIP_SCHEMA = "prefab_sentinel_bridge_deploy_ownership.v1"
OWNERSHIP_COLLECTION_SCHEMA = (
    "prefab_sentinel_bridge_deploy_ownership_collection.v1"
)
OWNERSHIP_RELATIVE_PATH = Path("Library/PrefabSentinel/deploy-ownership-v1.json")
DEFAULT_TARGET = Path("Assets/Editor/PrefabSentinel")

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TRANSACTION_ID_RE = re.compile(r"[0-9a-f]{32}")
_OWNERSHIP_FIELDS = frozenset(
    {
        "schema",
        "target",
        "bridge_version",
        "files",
        "manifest_sha256",
        "owned_meta_entries",
        "owned_parent_entries",
        "last_transaction_id",
    }
)
_OWNERSHIP_COLLECTION_FIELDS = frozenset({"schema", "records"})

_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x400,
)

_OWNERSHIP_PUBLICATION_MUTEX = threading.Lock()


@dataclass(frozen=True, slots=True)
class BridgeManifestEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BridgeBundleManifest:
    bridge_version: str
    files: tuple[BridgeManifestEntry, ...]
    sha256: str



@dataclass(frozen=True, slots=True)
class BridgeOwnershipRecord:
    target_path: str
    manifest: BridgeBundleManifest
    owned_meta_entries: tuple[str, ...]
    owned_parent_entries: tuple[str, ...]
    last_transaction_id: str


@dataclass(frozen=True, slots=True)
class TargetOwnership:
    managed_entries: tuple[str, ...]
    unmanaged_entries: tuple[str, ...]
    preserved_meta_entries: tuple[str, ...]
    legacy_import: bool


@dataclass(frozen=True, slots=True)
class DeployPreparation:
    run_id: str
    project_root: Path
    target_path: Path
    transaction_path: Path
    staging_path: Path
    backup_path: Path
    source_manifest: BridgeBundleManifest
    previous_manifest: BridgeBundleManifest | None
    ownership: TargetOwnership
    staging_verified: bool

    @property
    def success(self) -> bool:
        return self.staging_verified


@dataclass(frozen=True, slots=True)
class FreshTargetInstall:
    promotion_state: str
    barrier_used: bool
    rollback_attempted: bool = False
    rollback_restored: bool = False
    backup_retained: bool = False


@dataclass(frozen=True, slots=True)
class ExistingTargetReuse:
    promotion_state: str = "already_current"
    barrier_used: bool = False
    rollback_attempted: bool = False
    rollback_restored: bool = False
    backup_retained: bool = False


class _OwnershipInvalidError(ValueError):
    pass


def _source_error(message: str) -> ToolResponse:
    return error_response("DEPLOY_SOURCE_NOT_FOUND", message)


def build_bridge_manifest(source_dir: Path) -> BridgeBundleManifest | ToolResponse:
    source_path = source_dir if source_dir.is_absolute() else Path.cwd() / source_dir
    source_root = Path(source_path.anchor)
    if _path_chain_contains_link(source_root, source_path) or not source_dir.is_dir():
        return _source_error("Bridge source directory is unavailable.")

    try:
        children = tuple(source_dir.iterdir())
    except OSError as exc:
        error_type = _builtin_oserror_type_name(exc)
        logger.warning(
            "Bridge manifest read failed phase=manifest_directory_read "
            "error_type=%s errno=%s",
            error_type,
            exc.errno,
        )
        return _source_error("Bridge source directory could not be read.")

    if any(
        _path_chain_contains_link(
            source_path,
            child if child.is_absolute() else Path.cwd() / child,
        )
        for child in children
    ):
        return _source_error("Bridge source contains a link or reparse point.")

    entries: list[BridgeManifestEntry] = []
    version_bytes: bytes | None = None
    for child in sorted(children, key=lambda item: item.name):
        if child.suffix not in _SUPPORTED_SUFFIXES or not child.is_file():
            continue
        try:
            payload = child.read_bytes()
        except OSError as exc:
            error_type = _builtin_oserror_type_name(exc)
            logger.warning(
                "Bridge manifest read failed phase=manifest_file_read "
                "error_type=%s errno=%s",
                error_type,
                exc.errno,
            )
            return _source_error("Bridge source file could not be read.")
        entries.append(
            BridgeManifestEntry(
                path=child.name,
                size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
        if child.name == _BRIDGE_VERSION_FILE:
            version_bytes = payload

    if not entries:
        return _source_error("Bridge source contains no supported files.")
    if version_bytes is None:
        return _source_error("Canonical Bridge version source is missing.")

    try:
        version_text = version_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _source_error("Canonical Bridge version source is malformed.")
    version_match = _BRIDGE_VERSION_RE.search(version_text)
    if version_match is None or not version_match.group(1):
        return _source_error("Canonical Bridge version source is malformed.")

    files = tuple(entries)
    return BridgeBundleManifest(
        bridge_version=version_match.group(1),
        files=files,
        sha256=_manifest_digest(files),
    )



def _ownership_error(message: str) -> ToolResponse:
    return error_response("DEPLOY_OWNERSHIP_INVALID", message)


def _unmanaged_error(entries: tuple[str, ...]) -> ToolResponse:
    return error_response(
        "DEPLOY_TARGET_UNMANAGED",
        "Bridge target contains unmanaged entries.",
        data={"unmanaged_entries": list(entries)},
    )


def _outside_project_error() -> ToolResponse:
    return error_response(
        "DEPLOY_OUTSIDE_PROJECT",
        "Bridge target must be a normalized path inside the project.",
    )


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _OwnershipInvalidError("duplicate JSON key")
        result[key] = value
    return result


def _normalized_relative_path(
    value: object,
    *,
    root_entry: bool,
    assets_only: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\0" in value
        or "\\" in value
    ):
        raise _OwnershipInvalidError("path must be a non-empty POSIX string")

    windows_path = PureWindowsPath(value)
    if windows_path.drive or windows_path.root:
        raise _OwnershipInvalidError("Windows-qualified paths are not allowed")

    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise _OwnershipInvalidError("path must be normalized and relative")
    if root_entry and len(path.parts) != 1:
        raise _OwnershipInvalidError("entry must be directly under the target")
    if assets_only and (not path.parts or path.parts[0] != "Assets"):
        raise _OwnershipInvalidError("asset path must use the canonical Assets root")
    return value


def _sorted_unique_strings(
    value: object,
    *,
    root_entry: bool,
    assets_only: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _OwnershipInvalidError("entry collection must be an array")
    entries = tuple(
        _normalized_relative_path(
            item,
            root_entry=root_entry,
            assets_only=assets_only,
        )
        for item in value
    )
    if entries != tuple(sorted(entries)) or len(entries) != len(set(entries)):
        raise _OwnershipInvalidError("entries must be sorted and unique")
    return entries


def _manifest_digest(entries: tuple[BridgeManifestEntry, ...]) -> str:
    aggregate = hashlib.sha256()
    for entry in entries:
        aggregate.update(entry.path.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(str(entry.size).encode("ascii"))
        aggregate.update(b"\0")
        aggregate.update(entry.sha256.encode("ascii"))
    return aggregate.hexdigest()


def _parse_manifest_entries(value: object) -> tuple[BridgeManifestEntry, ...]:
    if not isinstance(value, list) or not value:
        raise _OwnershipInvalidError("manifest files must be a non-empty array")

    entries: list[BridgeManifestEntry] = []
    for raw_entry in value:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "path",
            "size",
            "sha256",
        }:
            raise _OwnershipInvalidError("manifest entry has an invalid shape")
        path = _normalized_relative_path(raw_entry["path"], root_entry=True)
        size = raw_entry["size"]
        sha256 = raw_entry["sha256"]
        if type(size) is not int or size < 0:
            raise _OwnershipInvalidError("manifest size must be a non-negative integer")
        if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
            raise _OwnershipInvalidError("manifest hash must be lowercase SHA-256")
        entries.append(BridgeManifestEntry(path=path, size=size, sha256=sha256))

    paths = tuple(entry.path for entry in entries)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise _OwnershipInvalidError("manifest files must be sorted and unique")
    return tuple(entries)


def _parse_ownership_record(raw: object) -> BridgeOwnershipRecord:
    if not isinstance(raw, dict) or set(raw) != _OWNERSHIP_FIELDS:
        raise _OwnershipInvalidError("ownership document has an invalid shape")
    if raw["schema"] != OWNERSHIP_SCHEMA:
        raise _OwnershipInvalidError("ownership schema is unknown")

    target_path = _normalized_relative_path(
        raw["target"],
        root_entry=False,
        assets_only=True,
    )
    bridge_version = raw["bridge_version"]
    if (
        not isinstance(bridge_version, str)
        or not bridge_version
        or bridge_version.strip() != bridge_version
        or "\0" in bridge_version
    ):
        raise _OwnershipInvalidError("Bridge version is invalid")

    entries = _parse_manifest_entries(raw["files"])
    manifest_sha256 = raw["manifest_sha256"]
    if (
        not isinstance(manifest_sha256, str)
        or _SHA256_RE.fullmatch(manifest_sha256) is None
        or manifest_sha256 != _manifest_digest(entries)
    ):
        raise _OwnershipInvalidError("manifest aggregate hash is invalid")

    owned_meta_entries = _sorted_unique_strings(
        raw["owned_meta_entries"],
        root_entry=True,
    )
    manifest_paths = {entry.path for entry in entries}
    if any(
        not name.endswith(".meta") or name[:-5] not in manifest_paths
        for name in owned_meta_entries
    ):
        raise _OwnershipInvalidError("owned metadata must partner an owned source file")

    owned_parent_entries = _sorted_unique_strings(
        raw["owned_parent_entries"],
        root_entry=False,
        assets_only=True,
    )
    transaction_id = raw["last_transaction_id"]
    if (
        not isinstance(transaction_id, str)
        or _TRANSACTION_ID_RE.fullmatch(transaction_id) is None
    ):
        raise _OwnershipInvalidError("transaction ID is invalid")

    return BridgeOwnershipRecord(
        target_path=target_path,
        manifest=BridgeBundleManifest(
            bridge_version=bridge_version,
            files=entries,
            sha256=manifest_sha256,
        ),
        owned_meta_entries=owned_meta_entries,
        owned_parent_entries=owned_parent_entries,
        last_transaction_id=transaction_id,
    )


def _parse_ownership_document(raw: object) -> tuple[BridgeOwnershipRecord, ...]:
    if (
        not isinstance(raw, dict)
        or set(raw) != _OWNERSHIP_COLLECTION_FIELDS
        or raw["schema"] != OWNERSHIP_COLLECTION_SCHEMA
        or not isinstance(raw["records"], list)
        or not raw["records"]
    ):
        raise _OwnershipInvalidError("ownership collection has an invalid shape")
    records = tuple(_parse_ownership_record(item) for item in raw["records"])
    target_paths = tuple(record.target_path for record in records)
    if target_paths != tuple(sorted(target_paths)) or len(set(target_paths)) != len(
        target_paths
    ):
        raise _OwnershipInvalidError(
            "ownership records must have unique sorted targets"
        )
    return records


def _ownership_record_document(
    record: BridgeOwnershipRecord,
) -> dict[str, object]:
    return {
        "schema": OWNERSHIP_SCHEMA,
        "target": record.target_path,
        "bridge_version": record.manifest.bridge_version,
        "files": [
            {
                "path": entry.path,
                "size": entry.size,
                "sha256": entry.sha256,
            }
            for entry in record.manifest.files
        ],
        "manifest_sha256": record.manifest.sha256,
        "owned_meta_entries": list(record.owned_meta_entries),
        "owned_parent_entries": list(record.owned_parent_entries),
        "last_transaction_id": record.last_transaction_id,
    }


def _ownership_collection_document(
    records: tuple[BridgeOwnershipRecord, ...],
) -> dict[str, object]:
    return {
        "schema": OWNERSHIP_COLLECTION_SCHEMA,
        "records": [
            _ownership_record_document(record)
            for record in sorted(records, key=lambda item: item.target_path)
        ],
    }


def _path_chain_contains_link(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True

    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        try:
            file_stat = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError:
            return True
        file_attributes = getattr(file_stat, "st_file_attributes", 0)
        if stat.S_ISLNK(file_stat.st_mode) or (
            file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            return True
    return False



def _is_regular_non_link_file(path: Path) -> bool:
    try:
        file_stat = os.lstat(path)
    except OSError:
        return False
    file_attributes = getattr(file_stat, "st_file_attributes", 0)
    return (
        stat.S_ISREG(file_stat.st_mode)
        and not stat.S_ISLNK(file_stat.st_mode)
        and not (file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
    )


def _load_ownership_records(
    project_root: Path,
) -> tuple[BridgeOwnershipRecord, ...] | ToolResponse:
    record_path = project_root / OWNERSHIP_RELATIVE_PATH
    if _path_chain_contains_link(project_root, record_path):
        return _ownership_error("Bridge ownership record is invalid.")
    if not record_path.exists():
        return ()
    if not record_path.is_file():
        return _ownership_error("Bridge ownership record is invalid.")

    try:
        raw: object = json.loads(
            record_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        if (
            isinstance(raw, dict)
            and set(raw) == _OWNERSHIP_FIELDS
            and raw.get("schema") == OWNERSHIP_SCHEMA
        ):
            return (_parse_ownership_record(raw),)
        return _parse_ownership_document(raw)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        _OwnershipInvalidError,
    ):
        return _ownership_error("Bridge ownership record is invalid.")


def load_ownership_record(
    project_root: Path,
    target_path: Path | str | None = None,
) -> BridgeOwnershipRecord | ToolResponse | None:
    records = _load_ownership_records(project_root)
    if isinstance(records, ToolResponse):
        return records
    if not records:
        return None
    if target_path is None:
        if len(records) == 1:
            return records[0]
        return _ownership_error(
            "Bridge ownership target is required for a multi-target record."
        )

    normalized_target = _normalized_target(project_root, Path(target_path))
    if isinstance(normalized_target, ToolResponse):
        return normalized_target
    relative_target, _ = normalized_target
    return next(
        (
            record
            for record in records
            if record.target_path == relative_target
        ),
        None,
    )


def _normalized_target(
    project_root: Path,
    target_path: Path,
) -> tuple[str, Path] | ToolResponse:
    if any(part == ".." for part in target_path.parts):
        return _outside_project_error()

    lexical_target = (
        target_path if target_path.is_absolute() else project_root / target_path
    )
    try:
        relative_lexical = lexical_target.relative_to(project_root)
    except ValueError:
        return _outside_project_error()
    if not relative_lexical.parts or any(
        part in {".", ".."} for part in relative_lexical.parts
    ):
        return _outside_project_error()
    if _path_chain_contains_link(project_root, lexical_target):
        return _outside_project_error()

    try:
        resolved_root = project_root.resolve(strict=True)
        resolved_target = lexical_target.resolve(strict=False)
        relative = resolved_target.relative_to(resolved_root)
    except (OSError, ValueError):
        return _outside_project_error()
    normalized = PurePosixPath(*relative.parts).as_posix()
    if not normalized or normalized == ".":
        return _outside_project_error()
    return normalized, resolved_target


def _target_children(target_path: Path) -> tuple[Path, ...] | ToolResponse:
    try:
        return tuple(sorted(target_path.iterdir(), key=lambda item: item.name))
    except OSError:
        return _unmanaged_error(())


def _classify_recorded_target(
    target_path: Path,
    source_manifest: BridgeBundleManifest,
    record: BridgeOwnershipRecord,
    children: tuple[Path, ...],
) -> TargetOwnership | ToolResponse:
    unsafe = tuple(
        child.name
        for child in children
        if not _is_regular_non_link_file(child)
    )
    if unsafe:
        return _unmanaged_error(unsafe)

    managed = tuple(entry.path for entry in record.manifest.files)
    allowed = set(managed) | set(record.owned_meta_entries)
    actual = {child.name for child in children}
    unmanaged = tuple(sorted(actual - allowed))
    if unmanaged:
        return _unmanaged_error(unmanaged)
    if not set(managed).issubset(actual):
        return _ownership_error("Recorded Bridge target entries are missing.")

    current_manifest = build_bridge_manifest(target_path)
    if isinstance(current_manifest, ToolResponse):
        return current_manifest
    if current_manifest != record.manifest:
        return _ownership_error("Recorded Bridge target manifest does not match.")

    incoming_paths = {entry.path for entry in source_manifest.files}
    preserved_meta = tuple(
        name
        for name in record.owned_meta_entries
        if name in actual and name[:-5] in incoming_paths
    )
    return TargetOwnership(
        managed_entries=managed,
        unmanaged_entries=(),
        preserved_meta_entries=preserved_meta,
        legacy_import=False,
    )


def _is_legacy_source_name(name: str) -> bool:
    if name == "PrefabSentinel.Editor.asmdef":
        return True
    return (
        name.startswith("PrefabSentinel.")
        and name.endswith(".cs")
        and len(name) > len("PrefabSentinel..cs")
    )


def _classify_legacy_target(
    children: tuple[Path, ...],
    source_manifest: BridgeBundleManifest,
) -> TargetOwnership | ToolResponse:
    unsafe = tuple(
        child.name
        for child in children
        if not _is_regular_non_link_file(child)
    )
    if unsafe:
        return _unmanaged_error(unsafe)

    names = tuple(child.name for child in children)
    managed = tuple(sorted(name for name in names if _is_legacy_source_name(name)))
    managed_set = set(managed)
    meta = tuple(
        sorted(
            name
            for name in names
            if name.endswith(".meta") and name[:-5] in managed_set
        )
    )
    allowed = managed_set | set(meta)
    unmanaged = tuple(sorted(set(names) - allowed))
    if unmanaged:
        return _unmanaged_error(unmanaged)

    incoming_paths = {entry.path for entry in source_manifest.files}
    preserved_meta = tuple(name for name in meta if name[:-5] in incoming_paths)
    return TargetOwnership(
        managed_entries=managed,
        unmanaged_entries=(),
        preserved_meta_entries=preserved_meta,
        legacy_import=True,
    )


def _classify_target_ownership_record(
    project_root: Path,
    target_path: Path,
    source_manifest: BridgeBundleManifest,
    record: BridgeOwnershipRecord | None,
) -> TargetOwnership | ToolResponse:
    normalized_target = _normalized_target(project_root, target_path)
    if isinstance(normalized_target, ToolResponse):
        return normalized_target
    relative_target, resolved_target = normalized_target
    if record is not None and record.target_path != relative_target:
        return _ownership_error("Bridge ownership record targets another location.")

    if not resolved_target.exists():
        if record is not None:
            return _ownership_error("Recorded Bridge target is missing.")
        return TargetOwnership((), (), (), False)
    if resolved_target.is_symlink() or not resolved_target.is_dir():
        return _unmanaged_error((resolved_target.name,))

    children = _target_children(resolved_target)
    if isinstance(children, ToolResponse):
        return children
    if not children:
        if record is not None:
            return _ownership_error("Recorded Bridge target is empty.")
        return TargetOwnership((), (), (), False)

    if record is not None:
        return _classify_recorded_target(
            resolved_target,
            source_manifest,
            record,
            children,
        )
    if relative_target != DEFAULT_TARGET.as_posix():
        return _unmanaged_error(tuple(child.name for child in children))
    return _classify_legacy_target(children, source_manifest)


def classify_target_ownership(
    project_root: Path,
    target_path: Path,
    source_manifest: BridgeBundleManifest,
) -> TargetOwnership | ToolResponse:
    record = load_ownership_record(project_root, target_path)
    if isinstance(record, ToolResponse):
        return record
    return _classify_target_ownership_record(
        project_root,
        target_path,
        source_manifest,
        record,
    )


def _staging_error(message: str) -> ToolResponse:
    return error_response("DEPLOY_STAGING_FAILED", message)


def _promotion_error(code: str, message: str) -> ToolResponse:
    return error_response(code, message)


def _transaction_path(project_root: Path, run_id: str) -> Path:
    return (
        project_root
        / "Library"
        / "PrefabSentinel"
        / "deploy-transactions"
        / run_id
    )


def _private_source_manifest(
    *,
    run_id: str,
    target: str,
    manifest: BridgeBundleManifest,
) -> dict[str, object]:
    return {
        "schema": "prefab_sentinel_bridge_source_manifest.v1",
        "run_id": run_id,
        "target": target,
        "bridge_version": manifest.bridge_version,
        "manifest_sha256": manifest.sha256,
        "files": [
            {
                "path": entry.path,
                "size": entry.size,
                "sha256": entry.sha256,
            }
            for entry in manifest.files
        ],
    }


def _atomic_write_json(destination: Path, document: dict[str, object]) -> bool:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary_path, destination)
        return True
    except OSError:
        return False
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _discard_transaction(
    transaction_path: Path,
    failure: ToolResponse,
) -> ToolResponse:
    try:
        shutil.rmtree(transaction_path)
    except OSError:
        transaction_retained = transaction_path.exists()
        return error_response(
            "DEPLOY_CLEANUP_FAILED",
            "Bridge preparation failed and its transaction could not be cleaned up.",
            data={
                "staging_prepared": (
                    transaction_path / "staged-target"
                ).is_dir(),
                "staging_verified": False,
                "promotion_state": "not_attempted",
                "barrier_used": False,
                "rollback_attempted": False,
                "rollback_restored": False,
                "backup_retained": False,
                "transaction_retained": transaction_retained,
                "recovery_required": transaction_retained,
            },
        )
    return failure


def _same_filesystem(first: Path, second: Path) -> bool:
    try:
        return os.stat(first).st_dev == os.stat(second).st_dev
    except OSError:
        return False


def verify_deployed_target(
    target_path: Path,
    source_manifest: BridgeBundleManifest,
) -> ToolResponse:
    deployed_manifest = build_bridge_manifest(target_path)
    if isinstance(deployed_manifest, ToolResponse):
        return deployed_manifest
    if deployed_manifest != source_manifest:
        return _promotion_error(
            "DEPLOY_FINAL_MANIFEST_MISMATCH",
            "Bridge target does not match the prepared source manifest.",
        )
    return success_response(
        "DEPLOY_OK",
        "Bridge target matches the prepared source manifest.",
    )


def _parent_sibling_conflict(
    project_root: Path,
    target_path: Path,
    record: BridgeOwnershipRecord | None,
) -> ToolResponse | None:
    try:
        siblings = tuple(target_path.parent.iterdir())
    except OSError:
        return error_response(
            "DEPLOY_PARENT_CONFLICT",
            "Bridge target parent directory could not be inspected.",
        )
    recorded = set(record.owned_parent_entries) if record is not None else set()
    for sibling in siblings:
        if not (
            sibling.name.startswith("PrefabSentinel.")
            and sibling.name.endswith(".cs")
            and len(sibling.name) > len("PrefabSentinel..cs")
        ):
            continue
        try:
            relative = sibling.relative_to(project_root).as_posix()
        except ValueError:
            return error_response(
                "DEPLOY_PARENT_CONFLICT",
                "Bridge target parent contains an unmanaged Bridge source.",
            )
        if relative not in recorded:
            return error_response(
                "DEPLOY_PARENT_CONFLICT",
                "Bridge target parent contains an unmanaged Bridge source.",
            )
    return None

def prepare_bridge_deploy(
    project_root: Path,
    target_path: Path,
    source_dir: Path,
    *,
    run_id: str,
) -> DeployPreparation | ToolResponse:
    if _TRANSACTION_ID_RE.fullmatch(run_id) is None:
        return _staging_error("Bridge deploy transaction ID is invalid.")
    if not project_root.is_dir() or _path_chain_contains_link(project_root, project_root):
        return _staging_error("Bridge deploy project directory is unavailable.")

    source_manifest = build_bridge_manifest(source_dir)
    if isinstance(source_manifest, ToolResponse):
        return source_manifest

    normalized_target = _normalized_target(project_root, target_path)
    if isinstance(normalized_target, ToolResponse):
        return normalized_target
    relative_target, normalized_target_path = normalized_target
    target_parent = normalized_target_path.parent
    if _path_chain_contains_link(project_root, target_parent):
        return error_response(
            "DEPLOY_PARENT_CONFLICT",
            "Bridge target parent directory is unavailable.",
        )
    try:
        target_parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return error_response(
            "DEPLOY_PARENT_CONFLICT",
            "Bridge target parent directory is unavailable.",
        )
    if _path_chain_contains_link(project_root, target_parent) or not target_parent.is_dir():
        return error_response(
            "DEPLOY_PARENT_CONFLICT",
            "Bridge target parent directory is unavailable.",
        )

    transaction_path = _transaction_path(project_root, run_id)
    if _path_chain_contains_link(project_root, transaction_path):
        return _staging_error("Bridge deploy transaction path is unsafe.")

    record = load_ownership_record(project_root, normalized_target_path)
    if isinstance(record, ToolResponse):
        return record
    ownership = _classify_target_ownership_record(
        project_root,
        normalized_target_path,
        source_manifest,
        record,
    )
    if isinstance(ownership, ToolResponse):
        return ownership
    parent_conflict = _parent_sibling_conflict(
        project_root,
        normalized_target_path,
        record,
    )
    if parent_conflict is not None:
        return parent_conflict

    transaction_parent = transaction_path.parent
    try:
        transaction_parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _staging_error("Bridge deploy transaction parent could not be created.")
    if _path_chain_contains_link(project_root, transaction_path):
        return _staging_error("Bridge deploy transaction path is unsafe.")
    try:
        transaction_path.mkdir()
    except OSError:
        return _staging_error("Bridge deploy transaction already exists.")

    staging_path = transaction_path / "staged-target"
    backup_path = transaction_path / "backup-target"
    try:
        staging_path.mkdir()
    except OSError:
        return _discard_transaction(
            transaction_path,
            _staging_error("Bridge staging directory could not be created."),
        )
    if not _same_filesystem(staging_path, target_parent):
        return _discard_transaction(
            transaction_path,
            error_response(
                "DEPLOY_CROSS_FILESYSTEM",
                "Bridge staging and target parent are on different filesystems.",
            ),
        )

    try:
        for entry in source_manifest.files:
            shutil.copyfile(source_dir / entry.path, staging_path / entry.path)
        for meta_name in ownership.preserved_meta_entries:
            source_meta = normalized_target_path / meta_name
            if not _is_regular_non_link_file(source_meta):
                raise OSError("owned metadata is unavailable")
            shutil.copyfile(source_meta, staging_path / meta_name)
    except OSError:
        return _discard_transaction(
            transaction_path,
            _staging_error("Bridge source files could not be staged."),
        )

    staged_manifest = build_bridge_manifest(staging_path)
    if staged_manifest != source_manifest:
        return _discard_transaction(
            transaction_path,
            error_response(
                "DEPLOY_STAGING_MISMATCH",
                "Bridge staging does not match the source manifest.",
            ),
        )
    if not _atomic_write_json(
        transaction_path / "source-manifest-v1.json",
        _private_source_manifest(
            run_id=run_id,
            target=relative_target,
            manifest=source_manifest,
        ),
    ):
        return _discard_transaction(
            transaction_path,
            _staging_error("Bridge source manifest could not be published."),
        )

    previous_manifest = record.manifest if record is not None else None
    if previous_manifest is None and ownership.managed_entries:
        inspected_previous = build_bridge_manifest(normalized_target_path)
        if isinstance(inspected_previous, ToolResponse):
            return _discard_transaction(
                transaction_path,
                _ownership_error(
                    "Legacy Bridge target manifest could not be verified."
                ),
            )
        previous_manifest = inspected_previous

    return DeployPreparation(
        run_id=run_id,
        project_root=project_root,
        target_path=normalized_target_path,
        transaction_path=transaction_path,
        staging_path=staging_path,
        backup_path=backup_path,
        source_manifest=source_manifest,
        previous_manifest=previous_manifest,
        ownership=ownership,
        staging_verified=True,
    )


def _merge_ownership_record_locked(
    prepared: DeployPreparation,
) -> ToolResponse:
    normalized_target = _normalized_target(
        prepared.project_root,
        prepared.target_path,
    )
    if isinstance(normalized_target, ToolResponse):
        return normalized_target
    relative_target, _ = normalized_target
    verification = verify_deployed_target(
        prepared.target_path,
        prepared.source_manifest,
    )
    if not verification.success:
        return verification

    record_path = prepared.project_root / OWNERSHIP_RELATIVE_PATH
    if _path_chain_contains_link(prepared.project_root, record_path):
        return _promotion_error(
            "DEPLOY_OWNERSHIP_WRITE_FAILED",
            "Bridge ownership record path is unsafe.",
        )
    try:
        record_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _promotion_error(
            "DEPLOY_OWNERSHIP_WRITE_FAILED",
            "Bridge ownership record parent could not be created.",
        )
    if _path_chain_contains_link(prepared.project_root, record_path):
        return _promotion_error(
            "DEPLOY_OWNERSHIP_WRITE_FAILED",
            "Bridge ownership record path is unsafe.",
        )

    records = _load_ownership_records(prepared.project_root)
    if isinstance(records, ToolResponse):
        return records
    current_record = BridgeOwnershipRecord(
        target_path=relative_target,
        manifest=prepared.source_manifest,
        owned_meta_entries=tuple(
            f"{entry.path}.meta"
            for entry in prepared.source_manifest.files
        ),
        owned_parent_entries=(),
        last_transaction_id=prepared.run_id,
    )
    merged_records = tuple(
        record
        for record in records
        if record.target_path != relative_target
    ) + (current_record,)
    if not _atomic_write_json(
        record_path,
        _ownership_collection_document(merged_records),
    ):
        return _promotion_error(
            "DEPLOY_OWNERSHIP_WRITE_FAILED",
            "Bridge ownership record could not be published.",
        )
    return success_response(
        "DEPLOY_OK",
        "Bridge ownership record was published.",
    )


def _publish_ownership_record_locked(
    prepared: DeployPreparation,
) -> ToolResponse:
    return _merge_ownership_record_locked(prepared)


def publish_ownership_record(prepared: DeployPreparation) -> ToolResponse:
    with _OWNERSHIP_PUBLICATION_MUTEX:
        lock = _acquire_deploy_lock(
            prepared.project_root,
            prepared.run_id,
        )
        if isinstance(lock, ToolResponse):
            return lock
        try:
            return _merge_ownership_record_locked(prepared)
        finally:
            _release_deploy_lock(lock)


if sys.platform == "win32":

    def _lock_descriptor_nonblocking(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)

    def _unlock_descriptor(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:

    def _lock_descriptor_nonblocking(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_descriptor(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _deploy_lock_path(project_root: Path) -> Path:
    return project_root / "Library" / "PrefabSentinel" / "deploy.lock"


def _acquire_deploy_lock(
    project_root: Path,
    run_id: str,
) -> int | ToolResponse:
    lock_path = _deploy_lock_path(project_root)
    if _path_chain_contains_link(project_root, lock_path):
        return _promotion_error(
            "DEPLOY_PROMOTION_FAILED",
            "Bridge deployment lock path is unsafe.",
        )

    descriptor: int | None = None
    locked = False
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if _path_chain_contains_link(project_root, lock_path):
            raise OSError("unsafe deployment lock path")
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o600,
        )
        _lock_descriptor_nonblocking(descriptor)
        locked = True

        descriptor_stat = os.fstat(descriptor)
        path_stat = os.lstat(lock_path)
        path_attributes = getattr(path_stat, "st_file_attributes", 0)
        if (
            _path_chain_contains_link(project_root, lock_path)
            or not stat.S_ISREG(descriptor_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or path_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
            or (descriptor_stat.st_dev, descriptor_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            with suppress(OSError):
                _unlock_descriptor(descriptor)
            with suppress(OSError):
                os.close(descriptor)
            return _promotion_error(
                "DEPLOY_LOCK_REPLACED",
                "Bridge deployment lock identity changed during acquisition.",
            )

        owner_bytes = run_id.encode("ascii")
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.write(descriptor, owner_bytes) != len(owner_bytes):
            raise OSError("deployment lock owner write was incomplete")
        return descriptor
    except (OSError, UnicodeError):
        if descriptor is not None:
            if locked:
                with suppress(OSError):
                    _unlock_descriptor(descriptor)
            with suppress(OSError):
                os.close(descriptor)
        return _promotion_error(
            "DEPLOY_PROMOTION_FAILED",
            "Bridge deployment is already active or its lock is unavailable.",
        )


def _release_deploy_lock(descriptor: int) -> None:
    try:
        with suppress(OSError):
            _unlock_descriptor(descriptor)
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _revalidate_fresh_preparation(
    prepared: DeployPreparation,
) -> ToolResponse | None:
    if (
        _path_chain_contains_link(prepared.project_root, prepared.staging_path)
        or _path_chain_contains_link(prepared.project_root, prepared.target_path)
        or not prepared.staging_path.is_dir()
        or not prepared.target_path.parent.is_dir()
    ):
        return _promotion_error(
            "DEPLOY_PROMOTION_FAILED",
            "Bridge fresh-target promotion preconditions changed.",
        )
    if not _same_filesystem(
        prepared.staging_path,
        prepared.target_path.parent,
    ):
        return _promotion_error(
            "DEPLOY_CROSS_FILESYSTEM",
            "Bridge staging and target parent are on different filesystems.",
        )
    if not verify_deployed_target(
        prepared.staging_path,
        prepared.source_manifest,
    ).success:
        return _promotion_error(
            "DEPLOY_PROMOTION_FAILED",
            "Bridge staging changed before fresh promotion.",
        )
    staged_children = _target_children(prepared.staging_path)
    expected_staging_entries = {
        entry.path for entry in prepared.source_manifest.files
    } | set(prepared.ownership.preserved_meta_entries)
    if (
        isinstance(staged_children, ToolResponse)
        or {child.name for child in staged_children} != expected_staging_entries
        or any(not _is_regular_non_link_file(child) for child in staged_children)
    ):
        return _promotion_error(
            "DEPLOY_PROMOTION_FAILED",
            "Bridge staging layout changed before fresh promotion.",
        )

    target_exists = prepared.target_path.exists()
    if target_exists:
        if (
            _path_chain_contains_link(
                prepared.project_root,
                prepared.target_path,
            )
            or not prepared.target_path.is_dir()
        ):
            return _promotion_error(
                "DEPLOY_PROMOTION_FAILED",
                "Bridge fresh-target promotion target is unsafe.",
            )
        children = _target_children(prepared.target_path)
        if isinstance(children, ToolResponse) or children:
            return _promotion_error(
                "DEPLOY_PROMOTION_FAILED",
                "Bridge fresh-target promotion target is not empty.",
            )

    record = load_ownership_record(
        prepared.project_root,
        prepared.target_path,
    )
    if isinstance(record, ToolResponse):
        return record
    ownership = _classify_target_ownership_record(
        prepared.project_root,
        prepared.target_path,
        prepared.source_manifest,
        record,
    )
    if isinstance(ownership, ToolResponse):
        return ownership
    previous_manifest = record.manifest if record is not None else None
    if (
        previous_manifest != prepared.previous_manifest
        or ownership != prepared.ownership
    ):
        return _promotion_error(
            "DEPLOY_PROMOTION_FAILED",
            "Bridge ownership changed before fresh promotion.",
        )

    if target_exists:
        try:
            prepared.target_path.rmdir()
        except OSError:
            return _promotion_error(
                "DEPLOY_PROMOTION_FAILED",
                "Bridge empty target could not be reserved for promotion.",
            )
    if (
        prepared.target_path.exists()
        or _path_chain_contains_link(
            prepared.project_root,
            prepared.target_path,
        )
    ):
        return _promotion_error(
            "DEPLOY_PROMOTION_FAILED",
            "Bridge target changed before fresh promotion.",
        )
    return None


def install_fresh_target(
    prepared: DeployPreparation,
    *,
    acquired_lock: int | None = None,
) -> FreshTargetInstall | ToolResponse:
    if not prepared.staging_verified:
        return _staging_error("Bridge staging has not been verified.")

    owns_lock = acquired_lock is None
    if acquired_lock is None:
        lock = _acquire_deploy_lock(
            prepared.project_root,
            prepared.run_id,
        )
        if isinstance(lock, ToolResponse):
            return lock
        lock_descriptor = lock
    else:
        lock_descriptor = acquired_lock
    try:
        revalidated = _revalidate_fresh_preparation(prepared)
        if isinstance(revalidated, ToolResponse):
            return revalidated
        try:
            os.rename(prepared.staging_path, prepared.target_path)
        except OSError:
            return _promotion_error(
                "DEPLOY_PROMOTION_FAILED",
                "Bridge fresh-target promotion could not be completed.",
            )

        verification = verify_deployed_target(
            prepared.target_path,
            prepared.source_manifest,
        )
        if not verification.success:
            try:
                shutil.rmtree(prepared.target_path)
            except OSError:
                return error_response(
                    "DEPLOY_ROLLBACK_FAILED",
                    "Bridge mismatched fresh target was retained for recovery.",
                    severity=Severity.CRITICAL,
                    data={
                        "promotion_state": "installed_fresh",
                        "target_state": "mismatched_retained",
                        "recovery_required": True,
                        "transaction_retained": True,
                    },
                )
            return error_response(
                "DEPLOY_FINAL_MANIFEST_MISMATCH",
                "Bridge fresh target failed byte verification.",
                data={
                    "promotion_state": "installed_fresh",
                    "target_state": "absent",
                },
            )

        ownership = _publish_ownership_record_locked(prepared)
        if not ownership.success:
            target_complete = verify_deployed_target(
                prepared.target_path,
                prepared.source_manifest,
            ).success
            if (
                ownership.code == "DEPLOY_OWNERSHIP_WRITE_FAILED"
                and target_complete
            ):
                return error_response(
                    "DEPLOY_OWNERSHIP_WRITE_FAILED",
                    "Bridge ownership publication failed after fresh installation.",
                    data={
                        "promotion_state": "installed_fresh",
                        "target_complete": True,
                        "manifest_sha256": prepared.source_manifest.sha256,
                        "bridge_version": prepared.source_manifest.bridge_version,
                        "ownership_published": False,
                        "recovery_required": True,
                        "transaction_retained": True,
                        "underlying_code": ownership.code,
                    },
                )
            recovery_code = (
                ownership.code
                if target_complete
                else "DEPLOY_FINAL_MANIFEST_MISMATCH"
            )
            return error_response(
                recovery_code,
                "Bridge ownership publication failed after target revalidation.",
                severity=Severity.CRITICAL,
                data={
                    "promotion_state": "installed_fresh",
                    "target_complete": target_complete,
                    "manifest_sha256": prepared.source_manifest.sha256,
                    "bridge_version": prepared.source_manifest.bridge_version,
                    "ownership_published": False,
                    "recovery_required": True,
                    "transaction_retained": True,
                    "underlying_code": ownership.code,
                },
            )
        return FreshTargetInstall(
            promotion_state="installed_fresh",
            barrier_used=False,
        )
    finally:
        if owns_lock:
            _release_deploy_lock(lock_descriptor)


def cleanup_deploy_transaction(prepared: DeployPreparation) -> ToolResponse:
    if _path_chain_contains_link(
        prepared.project_root,
        prepared.transaction_path,
    ):
        return error_response(
            "DEPLOY_CLEANUP_FAILED",
            "Bridge deploy transaction path is unsafe.",
        )
    if not prepared.transaction_path.exists():
        return success_response(
            "DEPLOY_OK",
            "Bridge deploy transaction is absent.",
        )
    cleanup_failure: tuple[str, str, int | None, int | None] | None = None

    def capture_cleanup_failure(
        function: Callable[..., Any],
        path: Any,
        excinfo: tuple[type[BaseException], BaseException, Any],
    ) -> None:
        nonlocal cleanup_failure
        error = excinfo[1]
        if cleanup_failure is None:
            if function is os.unlink:
                operation = "unlink"
            elif function is os.rmdir:
                callback_path = os.path.normcase(
                    os.path.normpath(os.fsdecode(path))
                )
                transaction_path = os.path.normcase(
                    os.path.normpath(os.fsdecode(prepared.transaction_path))
                )
                operation = (
                    "root_rmdir"
                    if callback_path == transaction_path
                    else "child_rmdir"
                )
            elif function is os.scandir:
                operation = "scandir"
            elif function is os.open:
                operation = "open"
            elif function is os.lstat:
                operation = "lstat"
            elif function is os.close:
                operation = "close"
            else:
                operation = "other"
            error_type = _builtin_oserror_type_name(error)
            error_errno = getattr(error, "errno", None)
            error_winerror = getattr(error, "winerror", None)
            cleanup_failure = (
                operation,
                error_type,
                error_errno if type(error_errno) is int else None,
                error_winerror if type(error_winerror) is int else None,
            )
        raise error

    try:
        shutil.rmtree(
            prepared.transaction_path,
            onerror=capture_cleanup_failure,
        )
    except OSError as exc:
        if cleanup_failure is None:
            error_type = _builtin_oserror_type_name(exc)
            error_errno = getattr(exc, "errno", None)
            error_winerror = getattr(exc, "winerror", None)
            cleanup_failure = (
                "rmtree",
                error_type,
                error_errno if type(error_errno) is int else None,
                error_winerror if type(error_winerror) is int else None,
            )
        logger.warning(
            "Bridge transaction cleanup failed phase=transaction_cleanup "
            "operation=%s error_type=%s errno=%s winerror=%s",
            *cleanup_failure,
        )
        return error_response(
            "DEPLOY_CLEANUP_FAILED",
            "Bridge deploy transaction could not be cleaned up.",
        )
    return success_response(
        "DEPLOY_OK",
        "Bridge deploy transaction was cleaned up.",
    )

def _canonical_deploy_data(
    prepared: DeployPreparation,
    *,
    manifest_sha256: str,
    bridge_version: str,
    promotion_state: str,
    barrier_used: bool,
    rollback_attempted: bool,
    rollback_restored: bool,
    backup_retained: bool,
    target_complete: bool,
    ownership_published: bool,
    transaction_retained: bool,
) -> dict[str, Any]:
    deployed_files: list[str] = []
    if target_complete:
        target_children = _target_children(prepared.target_path)
        if isinstance(target_children, tuple):
            deployed_files = [
                child.name
                for child in target_children
                if _is_regular_non_link_file(child)
            ]
    incoming_sources = {entry.path for entry in prepared.source_manifest.files}
    stale_owned = tuple(
        name
        for name in prepared.ownership.managed_entries
        if name not in incoming_sources
    )
    return {
        "source_manifest_sha256": prepared.source_manifest.sha256,
        "manifest_sha256": manifest_sha256,
        "bridge_version": bridge_version,
        "source_file_count": len(prepared.source_manifest.files),
        "managed_entry_count": len(deployed_files),
        "unmanaged_entry_count": len(prepared.ownership.unmanaged_entries),
        "preserved_meta_count": len(prepared.ownership.preserved_meta_entries),
        "stale_owned_count": len(stale_owned),
        "staging_prepared": True,
        "staging_verified": prepared.staging_verified,
        "promotion_state": promotion_state,
        "barrier_used": barrier_used,
        "rollback_attempted": rollback_attempted,
        "rollback_restored": rollback_restored,
        "backup_retained": backup_retained,
        "target_complete": target_complete,
        "ownership_published": ownership_published,
        "transaction_retained": transaction_retained,
        "deployed_files": deployed_files,
    }


def _verified_backup_retained(prepared: DeployPreparation) -> bool:
    previous = prepared.previous_manifest
    return previous is not None and verify_deployed_target(
        prepared.backup_path,
        previous,
    ).success


def _action_state(
    promotion: dict[str, Any],
) -> tuple[str, bool, bool, bool, bool, bool]:
    data = promotion.get("data")
    if not isinstance(data, dict):
        return ("not_attempted", False, False, False, False, False)
    state = data.get("promotion_state")
    if state not in {
        "not_attempted",
        "promoted",
        "rolled_back",
        "rollback_failed",
    }:
        state = "not_attempted"
    return (
        state,
        data.get("barrier_used") is True,
        data.get("rollback_attempted") is True,
        data.get("rollback_restored") is True,
        data.get("backup_retained") is True,
        data.get("target_complete") is True,
    )


def _is_quiescent_legacy_patch_dispatch_response(
    promotion: dict[str, Any],
) -> bool:
    """Recognize the pre-promotion dispatcher rejecting the fixed action."""
    data = promotion.get("data")
    protocol_version = promotion.get("protocol_version")
    return (
        promotion.get("success") is False
        and promotion.get("severity") == "error"
        and promotion.get("code") == "UNITY_BRIDGE_SCHEMA"
        and promotion.get("action") == "promote_bridge_bundle"
        and promotion.get("bridge_mode") == "editor"
        and promotion.get("diagnostics") == []
        and promotion.get("_request_published") is True
        and type(protocol_version) is int
        and isinstance(data, dict)
        and set(data)
        == {
            "target",
            "op_count",
            "applied",
            "read_only",
            "executed",
            "protocol_version",
            "created_results",
        }
        and data.get("target") == ""
        and type(data.get("op_count")) is int
        and data["op_count"] == 0
        and type(data.get("applied")) is int
        and data["applied"] == 0
        and data.get("read_only") is False
        and data.get("executed") is False
        and type(data.get("protocol_version")) is int
        and data["protocol_version"] == protocol_version
        and data.get("created_results") == []
    )


def _cleanup_failed_response(
    prepared: DeployPreparation,
    data: dict[str, Any],
) -> ToolResponse | None:
    cleanup = cleanup_deploy_transaction(prepared)
    if cleanup.success:
        return None
    transaction_retained = prepared.transaction_path.is_dir()
    return error_response(
        cleanup.code,
        "Bridge deployment completed but transaction cleanup failed.",
        severity=Severity.CRITICAL,
        data={
            **data,
            "backup_retained": _verified_backup_retained(prepared),
            "transaction_retained": transaction_retained,
            "recovery_required": transaction_retained,
        },
    )


def _prepare_bridge_recovery(
    prepared: DeployPreparation,
) -> ToolResponse:
    previous = prepared.previous_manifest
    if previous is None:
        return error_response(
            "DEPLOY_ROLLBACK_FAILED",
            "Previous Bridge manifest is unavailable for recovery.",
            severity=Severity.CRITICAL,
        )
    if (
        prepared.staging_path.exists()
        or not prepared.backup_path.is_dir()
        or not prepared.target_path.is_dir()
        or _path_chain_contains_link(
            prepared.project_root,
            prepared.transaction_path,
        )
        or not verify_deployed_target(
            prepared.backup_path,
            previous,
        ).success
    ):
        return error_response(
            "DEPLOY_ROLLBACK_FAILED",
            "Bridge recovery layout is unavailable.",
            severity=Severity.CRITICAL,
        )
    try:
        os.rename(prepared.backup_path, prepared.staging_path)
    except OSError:
        return error_response(
            "DEPLOY_ROLLBACK_FAILED",
            "Bridge recovery staging could not be prepared.",
            severity=Severity.CRITICAL,
        )
    manifest_path = prepared.transaction_path / "source-manifest-v1.json"
    if _atomic_write_json(
        manifest_path,
        _private_source_manifest(
            run_id=prepared.run_id,
            target=prepared.target_path.relative_to(
                prepared.project_root
            ).as_posix(),
            manifest=previous,
        ),
    ):
        return success_response(
            "DEPLOY_OK",
            "Bridge recovery staging was prepared.",
        )
    try:
        os.rename(prepared.staging_path, prepared.backup_path)
    except OSError:
        return error_response(
            "DEPLOY_ROLLBACK_FAILED",
            "Bridge recovery manifest failed and staging was retained.",
            severity=Severity.CRITICAL,
        )
    return error_response(
        "DEPLOY_ROLLBACK_FAILED",
        "Bridge recovery manifest could not be published.",
        severity=Severity.CRITICAL,
    )


def _normalize_exact_old_backup(
    prepared: DeployPreparation,
    previous: BridgeBundleManifest,
) -> bool:
    if _verified_backup_retained(prepared):
        return True
    if (
        not prepared.staging_path.is_dir()
        or _path_chain_contains_link(
            prepared.project_root,
            prepared.staging_path,
        )
        or not verify_deployed_target(
            prepared.staging_path,
            previous,
        ).success
    ):
        return False
    if prepared.backup_path.exists():
        if (
            not prepared.backup_path.is_dir()
            or _path_chain_contains_link(
                prepared.project_root,
                prepared.backup_path,
            )
        ):
            return False
        try:
            shutil.rmtree(prepared.backup_path)
        except OSError:
            return False
    if not verify_deployed_target(
        prepared.staging_path,
        previous,
    ).success:
        return False
    try:
        os.rename(prepared.staging_path, prepared.backup_path)
    except OSError:
        return False
    return _verified_backup_retained(prepared)


def _recover_final_manifest_mismatch(
    prepared: DeployPreparation,
    recovery_action: Callable[[BridgeBundleManifest], dict[str, Any]],
) -> ToolResponse:
    previous = prepared.previous_manifest
    recovery_preparation = _prepare_bridge_recovery(prepared)
    if not recovery_preparation.success or previous is None:
        return error_response(
            "DEPLOY_ROLLBACK_FAILED",
            "Bridge final mismatch could not enter recovery.",
            severity=Severity.CRITICAL,
            data=_canonical_deploy_data(
                prepared,
                manifest_sha256="",
                bridge_version="",
                promotion_state="rollback_failed",
                barrier_used=True,
                rollback_attempted=True,
                rollback_restored=False,
                backup_retained=_verified_backup_retained(prepared),
                target_complete=False,
                ownership_published=False,
                transaction_retained=prepared.transaction_path.is_dir(),
            ),
        )

    recovery_response = recovery_action(previous)
    recovery_code = recovery_response.get("code")
    action_completed = recovery_code in {
        "DEPLOY_OK",
        "DEPLOY_REFRESH_FAILED",
    }
    restored = (
        action_completed
        and verify_deployed_target(
            prepared.target_path,
            previous,
        ).success
    )
    if restored:
        data = _canonical_deploy_data(
            prepared,
            manifest_sha256=previous.sha256,
            bridge_version=previous.bridge_version,
            promotion_state="rolled_back",
            barrier_used=True,
            rollback_attempted=True,
            rollback_restored=True,
            backup_retained=False,
            target_complete=True,
            ownership_published=not prepared.ownership.legacy_import,
            transaction_retained=False,
        )
        cleanup_failure = _cleanup_failed_response(prepared, data)
        if cleanup_failure is not None:
            return cleanup_failure
        return error_response(
            "DEPLOY_FINAL_MANIFEST_MISMATCH",
            "Bridge promoted bytes mismatched and the exact old target was restored.",
            data=data,
        )

    response_quiesced = (
        isinstance(recovery_code, str)
        and not recovery_code.startswith("EDITOR_BRIDGE_")
    )
    exact_old_backup_retained = (
        _normalize_exact_old_backup(prepared, previous)
        if response_quiesced
        else _verified_backup_retained(prepared)
    )
    return error_response(
        "DEPLOY_ROLLBACK_FAILED",
        "Bridge promoted bytes mismatched and exact-old recovery failed.",
        severity=Severity.CRITICAL,
        data=_canonical_deploy_data(
            prepared,
            manifest_sha256="",
            bridge_version="",
            promotion_state="rollback_failed",
            barrier_used=True,
            rollback_attempted=True,
            rollback_restored=False,
            backup_retained=exact_old_backup_retained,
            target_complete=False,
            ownership_published=False,
            transaction_retained=prepared.transaction_path.is_dir(),
        ),
    )


def _project_fresh_failure(
    prepared: DeployPreparation,
    failure: ToolResponse,
) -> ToolResponse:
    target_complete = verify_deployed_target(
        prepared.target_path,
        prepared.source_manifest,
    ).success
    raw_state = failure.data.get("promotion_state")
    promotion_state = (
        raw_state
        if raw_state in {"not_attempted", "installed_fresh"}
        else ("installed_fresh" if target_complete else "not_attempted")
    )
    ownership = load_ownership_record(
        prepared.project_root,
        prepared.target_path,
    )
    ownership_published = (
        isinstance(ownership, BridgeOwnershipRecord)
        and ownership.manifest == prepared.source_manifest
    )
    return error_response(
        failure.code,
        "Bridge fresh-target deployment did not complete.",
        severity=failure.severity,
        data=_canonical_deploy_data(
            prepared,
            manifest_sha256=(
                prepared.source_manifest.sha256 if target_complete else ""
            ),
            bridge_version=(
                prepared.source_manifest.bridge_version
                if target_complete
                else ""
            ),
            promotion_state=promotion_state,
            barrier_used=False,
            rollback_attempted=(
                failure.data.get("rollback_attempted") is True
            ),
            rollback_restored=(
                failure.data.get("rollback_restored") is True
            ),
            backup_retained=_verified_backup_retained(prepared),
            target_complete=target_complete,
            ownership_published=ownership_published,
            transaction_retained=prepared.transaction_path.is_dir(),
        ),
    )


def complete_bridge_deploy(
    prepared: DeployPreparation,
    promotion: ExistingTargetReuse | FreshTargetInstall | ToolResponse | dict[str, Any],
    *,
    recovery_action: Callable[[BridgeBundleManifest], dict[str, Any]] | None = None,
    lock_held: bool = False,
) -> ToolResponse:
    """Verify, publish, clean, and project a path-free deployment result."""
    if isinstance(promotion, ToolResponse):
        return _project_fresh_failure(prepared, promotion)
    if isinstance(promotion, (ExistingTargetReuse, FreshTargetInstall)):
        verification = verify_deployed_target(
            prepared.target_path,
            prepared.source_manifest,
        )
        if not verification.success:
            return error_response(
                "DEPLOY_FINAL_MANIFEST_MISMATCH",
                "Bridge fresh target failed final byte verification.",
                data=_canonical_deploy_data(
                    prepared,
                    manifest_sha256="",
                    bridge_version="",
                    promotion_state=promotion.promotion_state,
                    barrier_used=promotion.barrier_used,
                    rollback_attempted=False,
                    rollback_restored=False,
                    backup_retained=False,
                    target_complete=False,
                    ownership_published=True,
                    transaction_retained=True,
                ),
            )
        data = _canonical_deploy_data(
            prepared,
            manifest_sha256=prepared.source_manifest.sha256,
            bridge_version=prepared.source_manifest.bridge_version,
            promotion_state=promotion.promotion_state,
            barrier_used=promotion.barrier_used,
            rollback_attempted=False,
            rollback_restored=False,
            backup_retained=False,
            target_complete=True,
            ownership_published=True,
            transaction_retained=False,
        )
        cleanup_failure = _cleanup_failed_response(prepared, data)
        if cleanup_failure is not None:
            return cleanup_failure
        message = (
            "Bridge bundle is already current and byte-verified."
            if isinstance(promotion, ExistingTargetReuse)
            else "Bridge bundle was installed and byte-verified."
        )
        return success_response(
            "DEPLOY_OK",
            message,
            data=data,
        )

    code = promotion.get("code")
    action_code = code if isinstance(code, str) else "DEPLOY_PROMOTION_FAILED"
    unknown_action_codes = {
        "EDITOR_BRIDGE_UNKNOWN_ACTION",
        "EDITOR_CTRL_UNKNOWN_ACTION",
    }
    legacy_patch_rejection = _is_quiescent_legacy_patch_dispatch_response(
        promotion
    )
    transport_failure = (
        action_code.startswith("EDITOR_BRIDGE_")
        and action_code != "EDITOR_BRIDGE_UNKNOWN_ACTION"
    )
    unexpected_published_response = (
        promotion.get("_request_published") is True
        and not action_code.startswith("DEPLOY_")
        and action_code not in unknown_action_codes
        and not legacy_patch_rejection
    )
    state = _action_state(promotion)
    (
        promotion_state,
        barrier_used,
        rollback_attempted,
        rollback_restored,
        _,
        action_target_complete,
    ) = state

    if transport_failure or unexpected_published_response:
        request_published = promotion.get("_request_published") is not False
        observed_manifest: BridgeBundleManifest | None = None
        observed_state = "not_attempted"
        if verify_deployed_target(
            prepared.target_path,
            prepared.source_manifest,
        ).success:
            observed_manifest = prepared.source_manifest
            observed_state = "promoted"
        elif (
            prepared.previous_manifest is not None
            and verify_deployed_target(
                prepared.target_path,
                prepared.previous_manifest,
            ).success
        ):
            observed_manifest = prepared.previous_manifest
        observed_complete = observed_manifest is not None
        data = _canonical_deploy_data(
            prepared,
            manifest_sha256=(
                observed_manifest.sha256
                if observed_manifest is not None
                else ""
            ),
            bridge_version=(
                observed_manifest.bridge_version
                if observed_manifest is not None
                else ""
            ),
            promotion_state=observed_state,
            barrier_used=False,
            rollback_attempted=False,
            rollback_restored=False,
            backup_retained=_verified_backup_retained(prepared),
            target_complete=observed_complete,
            ownership_published=(
                observed_manifest == prepared.previous_manifest
                and not prepared.ownership.legacy_import
            ),
            transaction_retained=prepared.transaction_path.is_dir(),
        )
        if request_published:
            return error_response(
                "DEPLOY_PROMOTION_FAILED",
                "Private Bridge promotion outcome is ambiguous.",
                severity=Severity.CRITICAL,
                data=data,
            )
        cleanup_failure = _cleanup_failed_response(prepared, data)
        if cleanup_failure is not None:
            return cleanup_failure
        return error_response(
            "DEPLOY_PROMOTION_FAILED",
            "Private Bridge promotion failed before request publication.",
            data={**data, "transaction_retained": False},
        )

    if action_code in unknown_action_codes or legacy_patch_rejection:
        previous_complete = (
            prepared.previous_manifest is not None
            and verify_deployed_target(
                prepared.target_path,
                prepared.previous_manifest,
            ).success
        )
        if not previous_complete:
            return error_response(
                "DEPLOY_FINAL_MANIFEST_MISMATCH",
                "Bridge target changed before safe promotion was available.",
                data=_canonical_deploy_data(
                    prepared,
                    manifest_sha256="",
                    bridge_version="",
                    promotion_state="not_attempted",
                    barrier_used=False,
                    rollback_attempted=False,
                    rollback_restored=False,
                    backup_retained=False,
                    target_complete=False,
                    ownership_published=False,
                    transaction_retained=True,
                ),
            )
        if prepared.previous_manifest is None:
            raise AssertionError("verified previous manifest is required")
        data = _canonical_deploy_data(
            prepared,
            manifest_sha256=prepared.previous_manifest.sha256,
            bridge_version=prepared.previous_manifest.bridge_version,
            promotion_state="not_attempted",
            barrier_used=False,
            rollback_attempted=False,
            rollback_restored=False,
            backup_retained=False,
            target_complete=True,
            ownership_published=not prepared.ownership.legacy_import,
            transaction_retained=False,
        )
        cleanup_failure = _cleanup_failed_response(prepared, data)
        if cleanup_failure is not None:
            return cleanup_failure
        return error_response(
            "DEPLOY_BARRIER_UNAVAILABLE",
            "Connected Bridge does not support safe bundle promotion.",
            data=data,
        )

    expected_manifest = (
        prepared.source_manifest
        if promotion_state == "promoted"
        else prepared.previous_manifest
    )
    target_complete = False
    manifest_sha256 = ""
    bridge_version = ""
    post_promotion_verification: ToolResponse | None = None
    if action_target_complete and expected_manifest is not None:
        post_promotion_verification = verify_deployed_target(
            prepared.target_path,
            expected_manifest,
        )
        target_complete = post_promotion_verification.success
        if target_complete:
            manifest_sha256 = expected_manifest.sha256
            bridge_version = expected_manifest.bridge_version

    if promotion_state == "rolled_back" and not target_complete:
        exact_old_backup_retained = _verified_backup_retained(prepared)
        return error_response(
            "DEPLOY_ROLLBACK_FAILED",
            "Bridge rollback did not restore the exact previous target.",
            severity=Severity.CRITICAL,
            data=_canonical_deploy_data(
                prepared,
                manifest_sha256="",
                bridge_version="",
                promotion_state="rollback_failed",
                barrier_used=barrier_used,
                rollback_attempted=True,
                rollback_restored=False,
                backup_retained=exact_old_backup_retained,
                target_complete=False,
                ownership_published=False,
                transaction_retained=prepared.transaction_path.is_dir(),
            ),
        )

    if promotion_state == "promoted" and not target_complete:
        if recovery_action is not None:
            verification_code = (
                post_promotion_verification.code
                if post_promotion_verification is not None
                else "NOT_RUN"
            )
            logged_action_code = (
                action_code
                if action_code in {"DEPLOY_OK", "DEPLOY_REFRESH_FAILED"}
                else "DEPLOY_OTHER"
            )
            logger.warning(
                "Bridge verification failed before recovery "
                "phase=post_promotion_verification verification_code=%s "
                "action_code=%s promotion_state=%s target_complete=%s",
                verification_code,
                logged_action_code,
                promotion_state,
                action_target_complete,
            )
            return _recover_final_manifest_mismatch(
                prepared,
                recovery_action,
            )
        verified_backup_retained = _verified_backup_retained(prepared)
        return error_response(
            "DEPLOY_FINAL_MANIFEST_MISMATCH",
            "Bridge promoted target failed independent byte verification.",
            severity=(
                Severity.CRITICAL
                if verified_backup_retained
                else Severity.ERROR
            ),
            data=_canonical_deploy_data(
                prepared,
                manifest_sha256="",
                bridge_version="",
                promotion_state=promotion_state,
                barrier_used=barrier_used,
                rollback_attempted=rollback_attempted,
                rollback_restored=rollback_restored,
                backup_retained=verified_backup_retained,
                target_complete=False,
                ownership_published=False,
                transaction_retained=True,
            ),
        )

    ownership_published = False
    if promotion_state == "promoted" and target_complete:
        ownership = (
            _merge_ownership_record_locked(prepared)
            if lock_held
            else publish_ownership_record(prepared)
        )
        if not ownership.success:
            ownership_verification_failed = ownership.code in {
                "DEPLOY_OUTSIDE_PROJECT",
                "DEPLOY_SOURCE_NOT_FOUND",
                "DEPLOY_FINAL_MANIFEST_MISMATCH",
            }
            return error_response(
                ownership.code,
                (
                    "Bridge target verification failed before ownership publication."
                    if ownership_verification_failed
                    else "Bridge target was verified but ownership publication failed."
                ),
                severity=ownership.severity,
                data=_canonical_deploy_data(
                    prepared,
                    manifest_sha256=(
                        "" if ownership_verification_failed else manifest_sha256
                    ),
                    bridge_version=(
                        "" if ownership_verification_failed else bridge_version
                    ),
                    promotion_state=promotion_state,
                    barrier_used=barrier_used,
                    rollback_attempted=rollback_attempted,
                    rollback_restored=rollback_restored,
                    backup_retained=_verified_backup_retained(prepared),
                    target_complete=not ownership_verification_failed,
                    ownership_published=False,
                    transaction_retained=True,
                ),
            )
        ownership_published = True

    preserve_transaction = promotion_state == "rollback_failed"
    data = _canonical_deploy_data(
        prepared,
        manifest_sha256=manifest_sha256,
        bridge_version=bridge_version,
        promotion_state=promotion_state,
        barrier_used=barrier_used,
        rollback_attempted=rollback_attempted,
        rollback_restored=rollback_restored,
        backup_retained=(
            _verified_backup_retained(prepared)
            if preserve_transaction
            else False
        ),
        target_complete=target_complete,
        ownership_published=ownership_published,
        transaction_retained=preserve_transaction,
    )
    if not preserve_transaction:
        cleanup_failure = _cleanup_failed_response(prepared, data)
        if cleanup_failure is not None:
            return cleanup_failure

    if promotion.get("success") is True and action_code == "DEPLOY_OK":
        return success_response(
            "DEPLOY_OK",
            "Bridge bundle was promoted and byte-verified.",
            data=data,
        )

    stable_code = (
        action_code
        if action_code
        in {
            "DEPLOY_BARRIER_UNAVAILABLE",
            "DEPLOY_PROMOTION_FAILED",
            "DEPLOY_ROLLED_BACK",
            "DEPLOY_ROLLBACK_FAILED",
            "DEPLOY_REFRESH_FAILED",
        }
        else "DEPLOY_PROMOTION_FAILED"
    )
    severity = (
        Severity.CRITICAL
        if stable_code == "DEPLOY_ROLLBACK_FAILED"
        else Severity.ERROR
    )
    return error_response(
        stable_code,
        "Bridge bundle promotion did not complete successfully.",
        severity=severity,
        data=data,
    )
