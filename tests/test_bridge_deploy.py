from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import unittest
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import FrozenInstanceError
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

import prefab_sentinel.bridge_deploy as bridge_deploy
import prefab_sentinel.editor_bridge as editor_bridge
from prefab_sentinel.bridge_deploy import (
    BridgeBundleManifest,
    BridgeOwnershipRecord,
    DeployPreparation,
    FreshTargetInstall,
    TargetOwnership,
    build_bridge_manifest,
    classify_target_ownership,
    cleanup_deploy_transaction,
    install_fresh_target,
    load_ownership_record,
    prepare_bridge_deploy,
    publish_ownership_record,
    verify_deployed_target,
)
from prefab_sentinel.contracts import ToolResponse
from tests._assertion_helpers import assert_error_envelope

_BRIDGE_BYTES = b'public const string BridgeVersion = "1.2.3";'


_EXTRA_FILE_SHA256 = (
    "c8dee78f8c7b466c881847accc196998bad00e2b96c5ef913dfbe454d3807c96"
)
_TWO_FILE_MANIFEST_SHA256 = (
    "a9215625685b57bd07d9c6f66f53eed25231dd2e782f60027ad4f84e02f57f08"
)

def _write_manifest_source(source: Path, entries: list[tuple[str, bytes]]) -> None:
    source.mkdir()
    for name, payload in entries:
        (source / name).write_bytes(payload)

def _exercise_private_deploy_transport(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    request_ready = threading.Condition()
    observed_request: dict[str, Path] = {}
    observed_payload: dict[str, object] = {}
    responder_errors: list[BaseException] = []
    original_rename = Path.rename

    def notifying_rename(self_path: Path, target: str | Path) -> Path:
        renamed_path = original_rename(self_path, target)
        target_path = Path(target)
        if (
            target_path.parent == tmp_path
            and target_path.name.endswith(".request.json")
        ):
            with request_ready:
                observed_request["path"] = target_path
                request_ready.notify_all()
        return renamed_path

    def fake_unity_response() -> None:
        try:
            with request_ready:
                request_seen = request_ready.wait_for(
                    lambda: "path" in observed_request,
                    timeout=2,
                )
            if not request_seen:
                raise AssertionError("private deploy request was not published")
            request_path = observed_request["path"]
            observed_payload.update(
                json.loads(request_path.read_text(encoding="utf-8"))
            )
            response_path = request_path.with_name(
                request_path.name.replace(".request.json", ".response.json")
            )
            response_path.write_text(
                json.dumps(
                    {
                        "protocol_version": editor_bridge.PROTOCOL_VERSION,
                        "success": True,
                        "severity": "info",
                        "code": "DEPLOY_OK",
                        "message": "Bridge bundle promotion completed.",
                        "data": {
                            "executed": True,
                            "promotion_state": "promoted",
                            "barrier_used": True,
                            "rollback_attempted": False,
                            "rollback_restored": False,
                            "backup_retained": True,
                            "target_complete": True,
                            "manifest_sha256": "b" * 64,
                            "bridge_version": "0.9.130",
                        },
                        "diagnostics": [],
                    }
                ),
                encoding="utf-8",
            )
        except BaseException as exc:
            responder_errors.append(exc)

    with (
        patch.dict(
            os.environ,
            {editor_bridge.BRIDGE_WATCH_DIR_ENV: str(tmp_path)},
            clear=False,
        ),
        patch.object(Path, "rename", notifying_rename),
    ):
        responder = threading.Thread(target=fake_unity_response)
        responder.start()
        result = editor_bridge.send_private_deploy_action(
            action="promote_bridge_bundle",
            deploy_run_id="a" * 32,
            deploy_target_path="Assets/Editor/PrefabSentinel",
            deploy_transaction_path=(
                "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
            ),
            deploy_manifest_sha256="b" * 64,
            deploy_bridge_version="0.9.130",
        )
        responder.join()

    if responder_errors:
        raise responder_errors[0]
    return result, observed_payload


def test_private_deploy_transport_publishes_only_fixed_promotion_request(
    tmp_path: Path,
) -> None:
    result, request = _exercise_private_deploy_transport(tmp_path)

    assert result["success"] is True
    assert request == {
        "protocol_version": editor_bridge.PROTOCOL_VERSION,
        "action": "promote_bridge_bundle",
        "deploy_run_id": "a" * 32,
        "deploy_target_path": "Assets/Editor/PrefabSentinel",
        "deploy_transaction_path": (
            "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
        ),
        "deploy_manifest_sha256": "b" * 64,
        "deploy_bridge_version": "0.9.130",
    }
    assert "promote_bridge_bundle" not in editor_bridge.SUPPORTED_ACTIONS


@pytest.mark.parametrize("action", ["refresh_asset_database", "arbitrary_action"])
def test_private_deploy_transport_rejects_every_other_action(
    tmp_path: Path,
    action: str,
) -> None:
    with patch.dict(
        os.environ,
        {editor_bridge.BRIDGE_WATCH_DIR_ENV: str(tmp_path)},
        clear=False,
    ):
        result = editor_bridge.send_private_deploy_action(
            action=action,
            deploy_run_id="a" * 32,
            deploy_target_path="Assets/Editor/PrefabSentinel",
            deploy_transaction_path=(
                "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
            ),
            deploy_manifest_sha256="b" * 64,
            deploy_bridge_version="0.9.130",
        )

    assert result["success"] is False
    assert result["code"] == "EDITOR_BRIDGE_UNKNOWN_ACTION"
    assert list(tmp_path.glob("*.request.json")) == []

def test_private_deploy_transport_marks_prepublication_rejection(
    tmp_path: Path,
) -> None:
    with patch.dict(
        os.environ,
        {editor_bridge.BRIDGE_WATCH_DIR_ENV: str(tmp_path)},
        clear=False,
    ):
        result = editor_bridge.send_private_deploy_action(
            action="refresh_asset_database",
            deploy_run_id="a" * 32,
            deploy_target_path="Assets/Editor/PrefabSentinel",
            deploy_transaction_path=(
                "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
            ),
            deploy_manifest_sha256="b" * 64,
            deploy_bridge_version="0.9.113",
        )

    assert result["success"] is False
    assert result["_request_published"] is False


def test_private_deploy_transport_marks_timeout_after_request_publication(
    tmp_path: Path,
) -> None:
    with patch.dict(
        os.environ,
        {
            editor_bridge.BRIDGE_WATCH_DIR_ENV: str(tmp_path),
            editor_bridge.BRIDGE_TIMEOUT_ENV: "1",
        },
        clear=False,
    ):
        result = editor_bridge.send_private_deploy_action(
            action="promote_bridge_bundle",
            deploy_run_id="a" * 32,
            deploy_target_path="Assets/Editor/PrefabSentinel",
            deploy_transaction_path=(
                "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
            ),
            deploy_manifest_sha256="b" * 64,
            deploy_bridge_version="0.9.113",
        )

    assert result["success"] is False
    assert result["code"] == "EDITOR_BRIDGE_TIMEOUT"
    assert result["_request_published"] is True


def test_bridge_manifest_is_sorted_and_byte_exact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            ("B.cs", b"B"),
            ("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES),
            ("A.cs", b"A"),
            ("PrefabSentinel.Editor.asmdef", b"{}"),
        ],
    )

    manifest = build_bridge_manifest(source)

    assert isinstance(manifest, BridgeBundleManifest)
    assert manifest.bridge_version == "1.2.3"
    assert [(item.path, item.size, item.sha256) for item in manifest.files] == [
        (
            "A.cs",
            1,
            "559aead08264d5795d3909718cdd05abd49572e84fe55590eef31a88a08fdffd",
        ),
        (
            "B.cs",
            1,
            "df7e70e5021544f4834bbee64a9e3789febc4be81470df629cad6ddb03320a5c",
        ),
        (
            "PrefabSentinel.Editor.asmdef",
            2,
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        ),
        (
            "PrefabSentinel.UnityEditorControlBridge.cs",
            44,
            "890beff0ea9ee6d7ea59b1ebd6a841f46b136e0a91c0189cc27aae4583f7c889",
        ),
    ]
    assert manifest.sha256 == (
        "25c7e71196bd2a0bd53792f650e4e512d72d1ce035978e18d869933d7cb222fc"
    )


def test_bridge_manifest_rejects_symlink_without_path_disclosure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
    )
    outside = tmp_path / "outside.cs"
    outside.write_bytes(b"outside")
    (source / "PrefabSentinel.Linked.cs").symlink_to(outside)

    result = build_bridge_manifest(source)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_SOURCE_NOT_FOUND")
    assert str(tmp_path) not in str(result.to_dict())


def test_bridge_manifest_ignores_unsupported_regular_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            ("notes.txt", b"not bridge source"),
            ("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES),
        ],
    )

    manifest = build_bridge_manifest(source)

    assert isinstance(manifest, BridgeBundleManifest)
    assert [item.path for item in manifest.files] == [
        "PrefabSentinel.UnityEditorControlBridge.cs"
    ]


def test_bridge_manifest_rejects_empty_supported_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_manifest_source(source, [("notes.txt", b"not bridge source")])

    result = build_bridge_manifest(source)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_SOURCE_NOT_FOUND")


@pytest.mark.parametrize(
    "bridge_bytes",
    [
        b"public const string Version = \"1.2.3\";",
        b'public const string BridgeVersion = "";',
    ],
)
def test_bridge_manifest_rejects_missing_or_malformed_canonical_version(
    tmp_path: Path,
    bridge_bytes: bytes,
) -> None:
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", bridge_bytes)],
    )

    result = build_bridge_manifest(source)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_SOURCE_NOT_FOUND")


def test_bridge_manifest_is_independent_of_creation_order(tmp_path: Path) -> None:
    entries = [
        ("PrefabSentinel.B.cs", b"B"),
        ("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES),
        ("PrefabSentinel.A.cs", b"A"),
        ("PrefabSentinel.Editor.asmdef", b"{}"),
    ]
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_manifest_source(first, entries)
    _write_manifest_source(second, list(reversed(entries)))

    first_manifest = build_bridge_manifest(first)
    second_manifest = build_bridge_manifest(second)

    assert isinstance(first_manifest, BridgeBundleManifest)
    assert isinstance(second_manifest, BridgeBundleManifest)
    assert first_manifest == second_manifest


def test_bridge_manifest_is_an_immutable_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
    )
    manifest = build_bridge_manifest(source)
    assert isinstance(manifest, BridgeBundleManifest)

    source.joinpath("PrefabSentinel.UnityEditorControlBridge.cs").write_bytes(b"changed")

    assert manifest.bridge_version == "1.2.3"
    assert manifest.files[0].size == 44
    with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
        manifest.bridge_version = "9.9.9"  # type: ignore[misc]



_ONE_FILE_MANIFEST_SHA256 = (
    "2e4b12b4c365a1f150faa720e248a67a9a0b6f8e2519deae214aad9d09ceb3aa"
)
_ONE_FILE_SHA256 = (
    "890beff0ea9ee6d7ea59b1ebd6a841f46b136e0a91c0189cc27aae4583f7c889"
)


def _source_manifest(tmp_path: Path) -> BridgeBundleManifest:
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
    )
    manifest = build_bridge_manifest(source)
    assert isinstance(manifest, BridgeBundleManifest)
    return manifest


def _ownership_payload(
    target: str = "Assets/Editor/PrefabSentinel",
    *,
    owned_meta_entries: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "prefab_sentinel_bridge_deploy_ownership.v1",
        "target": target,
        "bridge_version": "1.2.3",
        "files": [
            {
                "path": "PrefabSentinel.UnityEditorControlBridge.cs",
                "size": 44,
                "sha256": _ONE_FILE_SHA256,
            }
        ],
        "manifest_sha256": _ONE_FILE_MANIFEST_SHA256,
        "owned_meta_entries": owned_meta_entries or [],
        "owned_parent_entries": [],
        "last_transaction_id": "a" * 32,
    }



def _two_file_ownership_payload() -> dict[str, object]:
    payload = _ownership_payload()
    payload["files"] = [
        {
            "path": "PrefabSentinel.Extra.cs",
            "size": 5,
            "sha256": _EXTRA_FILE_SHA256,
        },
        {
            "path": "PrefabSentinel.UnityEditorControlBridge.cs",
            "size": 44,
            "sha256": _ONE_FILE_SHA256,
        },
    ]
    payload["manifest_sha256"] = _TWO_FILE_MANIFEST_SHA256
    return payload


def _write_ownership_record(project: Path, payload: object) -> Path:
    record_path = project / "Library/PrefabSentinel/deploy-ownership-v1.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": "prefab_sentinel_bridge_deploy_ownership_collection.v1",
        "records": [payload],
    }
    record_path.write_text(json.dumps(document), encoding="utf-8")
    return record_path



def _assert_invalid_ownership_payload(
    project: Path,
    payload: object,
) -> None:
    _write_ownership_record(project, payload)

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")
    assert str(project) not in str(result.to_dict())


def _owned_fixture(
    tmp_path: Path,
    *,
    target: str = "Assets/Editor/PrefabSentinel",
) -> tuple[Path, Path, BridgeBundleManifest]:
    project = tmp_path / "project"
    project.mkdir()
    manifest = _source_manifest(tmp_path)
    target_path = project / target
    target_path.mkdir(parents=True)
    target_path.joinpath("PrefabSentinel.UnityEditorControlBridge.cs").write_bytes(
        _BRIDGE_BYTES
    )
    return project, target_path, manifest


def test_load_ownership_record_returns_none_when_absent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert load_ownership_record(project) is None


def test_load_ownership_record_snapshots_valid_literal_document(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_ownership_record(
        project,
        _ownership_payload(
            owned_meta_entries=[
                "PrefabSentinel.UnityEditorControlBridge.cs.meta",
            ]
        ),
    )

    record = load_ownership_record(project)

    assert isinstance(record, BridgeOwnershipRecord)
    assert record.target_path == "Assets/Editor/PrefabSentinel"
    assert record.manifest.bridge_version == "1.2.3"
    assert record.manifest.sha256 == _ONE_FILE_MANIFEST_SHA256
    assert [(entry.path, entry.size, entry.sha256) for entry in record.manifest.files] == [
        (
            "PrefabSentinel.UnityEditorControlBridge.cs",
            44,
            _ONE_FILE_SHA256,
        )
    ]
    assert record.owned_meta_entries == (
        "PrefabSentinel.UnityEditorControlBridge.cs.meta",
    )
    assert record.owned_parent_entries == ()
    assert record.last_transaction_id == "a" * 32
    with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
        record.target_path = "Assets/changed"  # type: ignore[misc]


def test_malformed_ownership_record_fails_closed_and_redacts_path(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    record_path = project / "Library/PrefabSentinel/deploy-ownership-v1.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_text("{", encoding="utf-8")

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")
    assert str(tmp_path) not in str(result.to_dict())


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update(schema="unknown"),
        lambda payload: payload.update(target="Assets/../Other"),
        lambda payload: payload.update(files=payload["files"] * 2),
    ],
    ids=["unknown-schema", "traversal-target", "duplicate-files"],
)
def test_invalid_ownership_schema_paths_and_duplicates_fail_closed(
    tmp_path: Path,
    mutator: Callable[[dict[str, object]], None],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    assert callable(mutator)
    mutator(payload)
    _write_ownership_record(project, payload)

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")


def test_ownership_record_symlink_is_invalid(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external.json"
    external.write_text(json.dumps(_ownership_payload()), encoding="utf-8")
    record_path = project / "Library/PrefabSentinel/deploy-ownership-v1.json"
    record_path.parent.mkdir(parents=True)
    record_path.symlink_to(external)

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")
    assert str(tmp_path) not in str(result.to_dict())


@pytest.mark.parametrize("target_exists", [False, True], ids=["absent", "empty"])
def test_fresh_or_empty_target_has_no_owned_entries(
    tmp_path: Path,
    target_exists: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = _source_manifest(tmp_path)
    target = project / "Assets/Editor/PrefabSentinel"
    if target_exists:
        target.mkdir(parents=True)

    result = classify_target_ownership(project, target, manifest)

    assert result == TargetOwnership(
        managed_entries=(),
        unmanaged_entries=(),
        preserved_meta_entries=(),
        legacy_import=False,
    )


def test_valid_recorded_target_is_owned_and_preserves_matching_meta(
    tmp_path: Path,
) -> None:
    project, target, manifest = _owned_fixture(tmp_path)
    meta_name = "PrefabSentinel.UnityEditorControlBridge.cs.meta"
    target.joinpath(meta_name).write_text("guid: keep", encoding="utf-8")
    _write_ownership_record(
        project,
        _ownership_payload(owned_meta_entries=[meta_name]),
    )

    result = classify_target_ownership(project, target, manifest)

    assert result == TargetOwnership(
        managed_entries=("PrefabSentinel.UnityEditorControlBridge.cs",),
        unmanaged_entries=(),
        preserved_meta_entries=(meta_name,),
        legacy_import=False,
    )


def test_nonempty_custom_target_without_record_is_unmanaged(tmp_path: Path) -> None:
    project, target, manifest = _owned_fixture(tmp_path, target="Assets/ExampleProject")
    (target / "user-file.txt").write_text("mine", encoding="utf-8")

    result = classify_target_ownership(project, target, manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert (target / "user-file.txt").read_text(encoding="utf-8") == "mine"
    assert str(tmp_path) not in str(result.to_dict())


def test_unrecorded_dedicated_target_imports_only_product_shaped_entries(
    tmp_path: Path,
) -> None:
    project, target, manifest = _owned_fixture(tmp_path)
    target.joinpath("PrefabSentinel.Extra.cs").write_bytes(b"extra")
    target.joinpath("PrefabSentinel.Editor.asmdef").write_bytes(b"{}")
    target.joinpath(
        "PrefabSentinel.UnityEditorControlBridge.cs.meta"
    ).write_text(
        "guid: bridge",
        encoding="utf-8",
    )

    result = classify_target_ownership(project, target, manifest)

    assert result == TargetOwnership(
        managed_entries=(
            "PrefabSentinel.Editor.asmdef",
            "PrefabSentinel.Extra.cs",
            "PrefabSentinel.UnityEditorControlBridge.cs",
        ),
        unmanaged_entries=(),
        preserved_meta_entries=(
            "PrefabSentinel.UnityEditorControlBridge.cs.meta",
        ),
        legacy_import=True,
    )


@pytest.mark.parametrize(
    ("entry_name", "entry_kind"),
    [
        ("PrefabSentinel.Orphan.cs.meta", "file"),
        ("Nested", "directory"),
        ("linked.cs", "symlink"),
        ("notes.txt", "file"),
    ],
    ids=["unrelated-meta", "directory", "symlink", "unrelated-file"],
)
def test_unrecorded_dedicated_target_rejects_unrelated_entry_kinds(
    tmp_path: Path,
    entry_name: str,
    entry_kind: str,
) -> None:
    project, target, manifest = _owned_fixture(tmp_path)
    entry = target / entry_name
    if entry_kind == "directory":
        entry.mkdir()
    elif entry_kind == "symlink":
        external = tmp_path / "external.cs"
        external.write_bytes(b"external")
        entry.symlink_to(external)
    else:
        entry.write_bytes(b"unrelated")

    result = classify_target_ownership(project, target, manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert entry.exists() or entry.is_symlink()


def test_recorded_target_manifest_drift_fails_closed(tmp_path: Path) -> None:
    project, target, manifest = _owned_fixture(tmp_path)
    _write_ownership_record(project, _ownership_payload())
    target.joinpath("PrefabSentinel.UnityEditorControlBridge.cs").write_bytes(
        b'public const string BridgeVersion = "1.2.4";'
    )

    result = classify_target_ownership(project, target, manifest)

    assert isinstance(result, ToolResponse)
    assert_error_envelope(
        result,
        code="DEPLOY_OWNERSHIP_INVALID",
        message_match=r"^Recorded Bridge target manifest does not match\.$",
        data={},
    )


@pytest.mark.parametrize("entry_point", ["classify", "prepare"])
@pytest.mark.parametrize(
    ("failure_boundary", "message"),
    [
        ("directory", "Bridge source directory could not be read."),
        ("file", "Bridge source file could not be read."),
    ],
)
def test_recorded_target_manifest_read_failure_preserves_diagnostic_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_point: str,
    failure_boundary: str,
    message: str,
) -> None:
    project, target, manifest = _owned_fixture(tmp_path)
    bridge_file = target / "PrefabSentinel.UnityEditorControlBridge.cs"
    meta_file = target / (bridge_file.name + ".meta")
    meta_file.write_text("guid: keep", encoding="utf-8")
    record_file = _write_ownership_record(
        project, _ownership_payload(owned_meta_entries=[meta_file.name])
    )
    original_bytes = {
        path: path.read_bytes() for path in (bridge_file, meta_file, record_file)
    }
    real_iterdir = Path.iterdir
    real_read_bytes = Path.read_bytes
    target_enumerations = 0

    def fail_manifest_enumeration(path: Path) -> Iterator[Path]:
        nonlocal target_enumerations
        if path == target:
            target_enumerations += 1
            # Initial ownership entry classification succeeds; manifest reading fails.
            if target_enumerations == 2:
                raise OSError(13, "private-read-failure", str(path))
        return real_iterdir(path)

    def fail_manifest_file_read(path: Path) -> bytes:
        if path == bridge_file:
            raise OSError(13, "private-read-failure", str(path))
        return real_read_bytes(path)

    result: TargetOwnership | DeployPreparation | ToolResponse
    with monkeypatch.context() as fault:
        if failure_boundary == "directory":
            fault.setattr(Path, "iterdir", fail_manifest_enumeration)
        else:
            fault.setattr(Path, "read_bytes", fail_manifest_file_read)
        if entry_point == "classify":
            result = classify_target_ownership(project, target, manifest)
        else:
            result = prepare_bridge_deploy(
                project, target, tmp_path / "source", run_id="b" * 32
            )

    assert isinstance(result, ToolResponse)
    assert_error_envelope(result, code="DEPLOY_SOURCE_NOT_FOUND", data={})
    assert result.message == message
    assert result.diagnostics == []
    serialized = json.dumps(result.to_dict())
    assert str(tmp_path) not in serialized
    assert "private-read-failure" not in serialized
    assert {path: path.read_bytes() for path in original_bytes} == original_bytes
    assert set(target.iterdir()) == {bridge_file, meta_file}
    assert not (project / "Library/PrefabSentinel/deploy-transactions").exists()


def test_recorded_target_does_not_adopt_parent_siblings(tmp_path: Path) -> None:
    project, target, manifest = _owned_fixture(tmp_path)
    _write_ownership_record(project, _ownership_payload())
    sibling = target.parent / "PrefabSentinel.Parent.cs"
    sibling.write_bytes(b"parent")

    result = classify_target_ownership(project, target, manifest)

    assert isinstance(result, TargetOwnership)
    assert result.managed_entries == (
        "PrefabSentinel.UnityEditorControlBridge.cs",
    )
    assert "PrefabSentinel.Parent.cs" not in result.managed_entries
    assert sibling.read_bytes() == b"parent"


@pytest.mark.parametrize(
    "target_builder",
    [
        lambda project, tmp_path: tmp_path / "outside",
        lambda project, tmp_path: project / "Assets/../Other",
    ],
    ids=["outside-project", "dotdot"],
)
def test_target_path_validation_rejects_outside_or_non_normalized_paths(
    tmp_path: Path,
    target_builder: Callable[[Path, Path], Path],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = _source_manifest(tmp_path)
    assert callable(target_builder)
    target = target_builder(project, tmp_path)

    result = classify_target_ownership(project, target, manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OUTSIDE_PROJECT")
    assert str(tmp_path) not in str(result.to_dict())



def _fake_reparse_lstat(
    reparse_path: Path,
) -> Callable[[Path], os.stat_result | SimpleNamespace]:
    real_lstat = os.lstat

    def fake_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        result = real_lstat(path)
        if Path(path) == reparse_path:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_file_attributes=0x400,
            )
        return result

    return fake_lstat


@pytest.mark.skipif(os.name == "nt", reason="Unix symbolic-link coverage")
def test_path_component_guard_rejects_symlinked_source_parent(
    tmp_path: Path,
) -> None:
    physical_parent = tmp_path / "physical"
    physical_parent.mkdir()
    source = physical_parent / "source"
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
    )
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(physical_parent, target_is_directory=True)

    result = build_bridge_manifest(linked_parent / "source")

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_SOURCE_NOT_FOUND")
    assert str(tmp_path) not in str(result.to_dict())


@pytest.mark.skipif(os.name == "nt", reason="Unix symbolic-link coverage")
def test_path_component_guard_rejects_symlinked_ownership_parent(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external_library = tmp_path / "external-library"
    record_path = (
        external_library / "PrefabSentinel/deploy-ownership-v1.json"
    )
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps(_ownership_payload()), encoding="utf-8")
    project.joinpath("Library").symlink_to(
        external_library,
        target_is_directory=True,
    )

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")
    assert str(tmp_path) not in str(result.to_dict())


@pytest.mark.skipif(os.name == "nt", reason="Unix symbolic-link coverage")
def test_path_component_guard_rejects_symlinked_target_parent(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = _source_manifest(tmp_path)
    external_assets = tmp_path / "external-assets"
    target = external_assets / "Editor/PrefabSentinel"
    target.mkdir(parents=True)
    project.joinpath("Assets").symlink_to(
        external_assets,
        target_is_directory=True,
    )

    result = classify_target_ownership(
        project,
        project / "Assets/Editor/PrefabSentinel",
        manifest,
    )

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OUTSIDE_PROJECT")
    assert str(tmp_path) not in str(result.to_dict())


def test_path_component_guard_rejects_fake_source_parent_reparse_point(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
    )

    with patch("os.lstat", side_effect=_fake_reparse_lstat(tmp_path)):
        result = build_bridge_manifest(source)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_SOURCE_NOT_FOUND")


def test_path_component_guard_rejects_fake_ownership_parent_reparse_point(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    record_path = _write_ownership_record(project, _ownership_payload())

    with patch(
        "os.lstat",
        side_effect=_fake_reparse_lstat(record_path.parent),
    ):
        result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")


def test_path_component_guard_rejects_fake_target_parent_reparse_point(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manifest = _source_manifest(tmp_path)
    target = project / "Assets/Editor/PrefabSentinel"
    target.mkdir(parents=True)

    with patch(
        "os.lstat",
        side_effect=_fake_reparse_lstat(project / "Assets"),
    ):
        result = classify_target_ownership(project, target, manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OUTSIDE_PROJECT")


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-link coverage")
def test_path_component_guard_rejects_real_windows_directory_link(
    tmp_path: Path,
) -> None:
    physical_parent = tmp_path / "physical"
    physical_parent.mkdir()
    source = physical_parent / "source"
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
    )
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(physical_parent, target_is_directory=True)
    except OSError:
        pytest.skip("Windows directory-link creation is unavailable")

    result = build_bridge_manifest(linked_parent / "source")

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_SOURCE_NOT_FOUND")



def test_path_component_guard_preserves_relative_source_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = Path("source")
    _write_manifest_source(
        source,
        [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
    )

    result = build_bridge_manifest(source)

    assert isinstance(result, BridgeBundleManifest)
    assert result.sha256 == _ONE_FILE_MANIFEST_SHA256



@pytest.mark.parametrize(
    "target",
    [
        "",
        "C:/Assets/Editor/PrefabSentinel",
        r"C:\Assets\Editor\PrefabSentinel",
        "C:Assets/Editor/PrefabSentinel",
        "//server/share/PrefabSentinel",
        r"\\server\share\PrefabSentinel",
        "/Assets/Editor/PrefabSentinel",
        r"\Assets\Editor\PrefabSentinel",
        r"Assets\Editor\PrefabSentinel",
        "Assets/Editor/../PrefabSentinel",
        "Assets/./Editor/PrefabSentinel",
        "Assets//Editor/PrefabSentinel",
        "Assets/Editor/PrefabSentinel/",
        "Assets/Editor/Prefab\0Sentinel",
        "assets/Editor/PrefabSentinel",
    ],
    ids=[
        "empty",
        "drive-absolute-forward",
        "drive-absolute-backslash",
        "drive-relative",
        "unc-forward",
        "unc-backslash",
        "posix-root",
        "backslash-root",
        "backslash-relative",
        "traversal",
        "dot",
        "double-slash",
        "trailing-slash",
        "nul",
        "assets-case-drift",
    ],
)
def test_ownership_path_grammar_rejects_noncanonical_target(
    tmp_path: Path,
    target: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_ownership_record(project, _ownership_payload(target))

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")
    assert str(tmp_path) not in str(result.to_dict())


@pytest.mark.parametrize(
    "parent_entry",
    [
        "C:/Assets/Editor/PrefabSentinel.Parent.cs",
        r"\\server\share\PrefabSentinel.Parent.cs",
        "assets/Editor/PrefabSentinel.Parent.cs",
    ],
    ids=["drive", "unc", "assets-case-drift"],
)
def test_ownership_path_grammar_rejects_noncanonical_parent_entry(
    tmp_path: Path,
    parent_entry: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    payload["owned_parent_entries"] = [parent_entry]
    _write_ownership_record(project, payload)

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")



def test_strict_ownership_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    encoded = json.dumps(_ownership_payload(), separators=(",", ":"))
    duplicate_schema = encoded.replace(
        "{",
        '{"schema":"duplicate",',
        1,
    )
    record_path = project / "Library/PrefabSentinel/deploy-ownership-v1.json"
    record_path.parent.mkdir(parents=True)
    record_path.write_bytes(duplicate_schema.encode("utf-8"))

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")


def test_strict_ownership_json_rejects_nonobject_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    _assert_invalid_ownership_payload(project, [])


def test_strict_ownership_json_rejects_unknown_root_field(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    payload["unexpected"] = True

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_unknown_entry_field(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    files = payload["files"]
    assert isinstance(files, list)
    entry = files[0]
    assert isinstance(entry, dict)
    entry["unexpected"] = True

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_nonstring_schema(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    payload["schema"] = 1

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_uppercase_file_hash(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    files = payload["files"]
    assert isinstance(files, list)
    entry = files[0]
    assert isinstance(entry, dict)
    entry["sha256"] = "A" * 64

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_short_manifest_hash(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    payload["manifest_sha256"] = "0" * 63

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_negative_file_size(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    files = payload["files"]
    assert isinstance(files, list)
    entry = files[0]
    assert isinstance(entry, dict)
    entry["size"] = -1

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_boolean_file_size(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    files = payload["files"]
    assert isinstance(files, list)
    entry = files[0]
    assert isinstance(entry, dict)
    entry["size"] = True

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_malformed_transaction_id(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    payload["last_transaction_id"] = "A" * 32

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_unsorted_manifest_entries(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _two_file_ownership_payload()
    files = payload["files"]
    assert isinstance(files, list)
    payload["files"] = list(reversed(files))

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_duplicate_manifest_entries(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    files = payload["files"]
    assert isinstance(files, list)
    payload["files"] = [files[0], files[0]]

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_json_rejects_metadata_without_owned_source_partner(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = _ownership_payload()
    payload["owned_meta_entries"] = ["PrefabSentinel.Missing.cs.meta"]

    _assert_invalid_ownership_payload(project, payload)


def test_strict_ownership_rejects_nonversion_file_byte_drift(
    tmp_path: Path,
) -> None:
    project, target, source_manifest = _owned_fixture(tmp_path)
    target.joinpath("PrefabSentinel.Extra.cs").write_bytes(b"drift")
    _write_ownership_record(project, _two_file_ownership_payload())

    result = classify_target_ownership(project, target, source_manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")
    assert str(tmp_path) not in str(result.to_dict())



def test_target_child_predicate_rejects_fake_legacy_source_reparse(
    tmp_path: Path,
) -> None:
    project, target, source_manifest = _owned_fixture(tmp_path)
    child = target / "PrefabSentinel.UnityEditorControlBridge.cs"

    with patch("os.lstat", side_effect=_fake_reparse_lstat(child)):
        result = classify_target_ownership(project, target, source_manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert str(tmp_path) not in str(result.to_dict())


def test_target_child_predicate_rejects_fake_legacy_meta_reparse(
    tmp_path: Path,
) -> None:
    project, target, source_manifest = _owned_fixture(tmp_path)
    meta = target / "PrefabSentinel.UnityEditorControlBridge.cs.meta"
    meta.write_text("guid: bridge", encoding="utf-8")

    with patch("os.lstat", side_effect=_fake_reparse_lstat(meta)):
        result = classify_target_ownership(project, target, source_manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert str(tmp_path) not in str(result.to_dict())


def test_target_child_predicate_rejects_fake_recorded_source_reparse(
    tmp_path: Path,
) -> None:
    project, target, source_manifest = _owned_fixture(tmp_path)
    _write_ownership_record(project, _ownership_payload())
    child = target / "PrefabSentinel.UnityEditorControlBridge.cs"

    with patch("os.lstat", side_effect=_fake_reparse_lstat(child)):
        result = classify_target_ownership(project, target, source_manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert str(tmp_path) not in str(result.to_dict())


def test_target_child_predicate_rejects_fake_recorded_meta_reparse(
    tmp_path: Path,
) -> None:
    project, target, source_manifest = _owned_fixture(tmp_path)
    meta_name = "PrefabSentinel.UnityEditorControlBridge.cs.meta"
    meta = target / meta_name
    meta.write_text("guid: bridge", encoding="utf-8")
    _write_ownership_record(
        project,
        _ownership_payload(owned_meta_entries=[meta_name]),
    )

    with patch("os.lstat", side_effect=_fake_reparse_lstat(meta)):
        result = classify_target_ownership(project, target, source_manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert str(tmp_path) not in str(result.to_dict())


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-child coverage")
@pytest.mark.parametrize(
    "target_is_directory",
    [False, True],
    ids=["file", "directory"],
)
def test_target_child_predicate_rejects_real_windows_product_shaped_reparse(
    tmp_path: Path,
    target_is_directory: bool,
) -> None:
    project, target, source_manifest = _owned_fixture(tmp_path)
    external = tmp_path / "external"
    if target_is_directory:
        external.mkdir()
    else:
        external.write_bytes(b"external")
    child = target / "PrefabSentinel.Linked.cs"
    try:
        child.symlink_to(external, target_is_directory=target_is_directory)
    except OSError:
        pytest.skip("Windows reparse-child creation is unavailable")

    result = classify_target_ownership(project, target, source_manifest)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert str(tmp_path) not in str(result.to_dict())


def _redeploy_fixture(tmp_path: Path) -> SimpleNamespace:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            ("PrefabSentinel.Extra.cs", b"public sealed class Extra {}"),
            (
                "PrefabSentinel.UnityEditorControlBridge.cs",
                b'public const string BridgeVersion = "1.2.3";',
            ),
        ],
    )
    source_manifest = build_bridge_manifest(source)
    assert isinstance(source_manifest, BridgeBundleManifest)

    target = project / "Assets/Editor/PrefabSentinel"
    target.mkdir(parents=True)
    old_source = tmp_path / "old-source"
    _write_manifest_source(
        old_source,
        [
            ("PrefabSentinel.Stale.cs", b"public sealed class Stale {}"),
            (
                "PrefabSentinel.UnityEditorControlBridge.cs",
                b'public const string BridgeVersion = "1.2.2";',
            ),
        ],
    )
    old_manifest = build_bridge_manifest(old_source)
    assert isinstance(old_manifest, BridgeBundleManifest)
    for entry in old_manifest.files:
        target.joinpath(entry.path).write_bytes(old_source.joinpath(entry.path).read_bytes())
        target.joinpath(f"{entry.path}.meta").write_text(
            f"guid: {entry.path}", encoding="utf-8"
        )
    _write_ownership_record(
        project,
        {
            "schema": "prefab_sentinel_bridge_deploy_ownership.v1",
            "target": "Assets/Editor/PrefabSentinel",
            "bridge_version": old_manifest.bridge_version,
            "files": [
                {
                    "path": entry.path,
                    "size": entry.size,
                    "sha256": entry.sha256,
                }
                for entry in old_manifest.files
            ],
            "manifest_sha256": old_manifest.sha256,
            "owned_meta_entries": [
                f"{entry.path}.meta" for entry in old_manifest.files
            ],
            "owned_parent_entries": [],
            "last_transaction_id": "b" * 32,
        },
    )
    return SimpleNamespace(
        project=project,
        source=source,
        source_manifest=source_manifest,
        target=target,
        old_manifest=old_manifest,
    )


@pytest.mark.parametrize(
    ("backup_state", "expected_retained"),
    (
        ("exact-old", True),
        ("corrupt", False),
        ("partial", False),
        ("nonexistent", False),
    ),
)
def test_verified_backup_retained_requires_exact_previous_manifest(
    tmp_path: Path,
    backup_state: str,
    expected_retained: bool,
) -> None:
    fixture = _redeploy_fixture(tmp_path)
    prepared = prepare_bridge_deploy(
        fixture.project,
        fixture.target,
        fixture.source,
        run_id="a" * 32,
    )
    assert isinstance(prepared, DeployPreparation)
    shutil.copytree(fixture.target, prepared.backup_path)

    if backup_state == "corrupt":
        prepared.backup_path.joinpath(
            "PrefabSentinel.UnityEditorControlBridge.cs"
        ).write_bytes(b"corrupt")
    elif backup_state == "partial":
        prepared.backup_path.joinpath("PrefabSentinel.Stale.cs").unlink()
    elif backup_state == "nonexistent":
        shutil.rmtree(prepared.backup_path)

    assert (
        bridge_deploy._verified_backup_retained(prepared)
        is expected_retained
    )

def _non_old_backup_with_exact_old_staging(
    tmp_path: Path,
) -> DeployPreparation:
    fixture = _redeploy_fixture(tmp_path)
    prepared = prepare_bridge_deploy(
        fixture.project,
        fixture.target,
        fixture.source,
        run_id="c" * 32,
    )
    assert isinstance(prepared, DeployPreparation)
    assert prepared.previous_manifest is not None
    prepared.staging_path.rename(prepared.backup_path)
    prepared.target_path.rename(prepared.staging_path)
    assert verify_deployed_target(
        prepared.staging_path,
        prepared.previous_manifest,
    ).success
    assert not verify_deployed_target(
        prepared.backup_path,
        prepared.previous_manifest,
    ).success
    return prepared


def test_exact_old_staging_survives_non_old_backup_removal_failure(
    tmp_path: Path,
) -> None:
    prepared = _non_old_backup_with_exact_old_staging(tmp_path)
    assert prepared.previous_manifest is not None

    with patch(
        "prefab_sentinel.bridge_deploy.shutil.rmtree",
        side_effect=OSError("forced non-old backup removal failure"),
    ):
        normalized = bridge_deploy._normalize_exact_old_backup(
            prepared,
            prepared.previous_manifest,
        )

    assert normalized is False
    assert verify_deployed_target(
        prepared.staging_path,
        prepared.previous_manifest,
    ).success
    assert prepared.backup_path.is_dir()
    assert not prepared.target_path.exists()


def test_exact_old_staging_survives_canonical_backup_rename_failure(
    tmp_path: Path,
) -> None:
    prepared = _non_old_backup_with_exact_old_staging(tmp_path)
    assert prepared.previous_manifest is not None
    original_rename = os.rename

    def reject_canonical_rename(
        source: Path,
        destination: Path,
    ) -> None:
        if (
            Path(source) == prepared.staging_path
            and Path(destination) == prepared.backup_path
        ):
            raise OSError("forced canonical backup rename failure")
        original_rename(source, destination)

    with patch(
        "prefab_sentinel.bridge_deploy.os.rename",
        side_effect=reject_canonical_rename,
    ):
        normalized = bridge_deploy._normalize_exact_old_backup(
            prepared,
            prepared.previous_manifest,
        )

    assert normalized is False
    assert verify_deployed_target(
        prepared.staging_path,
        prepared.previous_manifest,
    ).success
    assert not prepared.backup_path.exists()
    assert not prepared.target_path.exists()


def _fresh_preparation(tmp_path: Path) -> DeployPreparation:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            (
                "PrefabSentinel.UnityEditorControlBridge.cs",
                b'public const string BridgeVersion = "1.2.3";',
            ),
        ],
    )
    result = prepare_bridge_deploy(
        project,
        project / "Assets/Editor/PrefabSentinel",
        source,
        run_id="a" * 32,
    )
    assert isinstance(result, DeployPreparation)
    return result


def test_prepare_staging_complete_verified_bundle_and_preserves_meta(
    tmp_path: Path,
) -> None:
    fixture = _redeploy_fixture(tmp_path)
    kept_meta = fixture.target / "PrefabSentinel.UnityEditorControlBridge.cs.meta"
    kept_meta.write_text("guid: keep", encoding="utf-8")

    prepared = prepare_bridge_deploy(
        fixture.project,
        fixture.target,
        fixture.source,
        run_id="a" * 32,
    )

    assert isinstance(prepared, DeployPreparation)
    assert prepared.transaction_path == fixture.project / (
        "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
    )
    assert prepared.staging_path == prepared.transaction_path / "staged-target"
    assert prepared.backup_path == prepared.transaction_path / "backup-target"
    assert prepared.staging_verified is True
    assert verify_deployed_target(prepared.staging_path, prepared.source_manifest).success
    assert kept_meta.name in prepared.ownership.preserved_meta_entries
    assert (
        prepared.staging_path / kept_meta.name
    ).read_text(encoding="utf-8") == "guid: keep"
    assert not (prepared.staging_path / "PrefabSentinel.Stale.cs").exists()
    assert not (prepared.staging_path / "PrefabSentinel.Stale.cs.meta").exists()
    assert json.loads(
        (prepared.transaction_path / "source-manifest-v1.json").read_text(
            encoding="utf-8"
        )
    ) == {
        "bridge_version": "1.2.3",
        "files": [
            {
                "path": "PrefabSentinel.Extra.cs",
                "sha256": "c11a04f4d3620355f2650151004b6081f8da6494f8d3d56f5e78e0ff42d940e5",
                "size": 28,
            },
            {
                "path": "PrefabSentinel.UnityEditorControlBridge.cs",
                "sha256": "890beff0ea9ee6d7ea59b1ebd6a841f46b136e0a91c0189cc27aae4583f7c889",
                "size": 44,
            },
        ],
        "manifest_sha256": "b302b12e2a22f54705edb4d7045f44bf2bb16a96ef30eb6dc1dec82545af8ba6",
        "run_id": "a" * 32,
        "schema": "prefab_sentinel_bridge_source_manifest.v1",
        "target": "Assets/Editor/PrefabSentinel",
    }


def test_prepare_rejects_staging_copy_failure_without_mutating_target(tmp_path: Path) -> None:
    fixture = _redeploy_fixture(tmp_path)
    old_manifest_sha256 = fixture.old_manifest.sha256
    source_manifest_sha256 = fixture.source_manifest.sha256

    with patch(
        "prefab_sentinel.bridge_deploy.shutil.copyfile",
        side_effect=OSError("copy unavailable"),
    ):
        result = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="a" * 32,
        )

    target_manifest = build_bridge_manifest(fixture.target)
    source_manifest = build_bridge_manifest(fixture.source)
    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_STAGING_FAILED")
    assert isinstance(target_manifest, BridgeBundleManifest)
    assert isinstance(source_manifest, BridgeBundleManifest)
    assert target_manifest.sha256 == old_manifest_sha256
    assert source_manifest.sha256 == source_manifest_sha256


def test_prepare_reports_retained_transaction_when_abort_cleanup_fails(
    tmp_path: Path,
) -> None:
    fixture = _redeploy_fixture(tmp_path)
    transaction_path = fixture.project / (
        "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
    )
    old_manifest_sha256 = fixture.old_manifest.sha256
    cleanup_secret = str(transaction_path / "private-cleanup-error")

    with (
        patch(
            "prefab_sentinel.bridge_deploy.shutil.copyfile",
            side_effect=OSError("copy unavailable"),
        ),
        patch(
            "prefab_sentinel.bridge_deploy.shutil.rmtree",
            side_effect=OSError(cleanup_secret),
        ),
    ):
        result = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="a" * 32,
        )

    target_manifest = build_bridge_manifest(fixture.target)
    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_CLEANUP_FAILED")
    assert result.data == {
        "staging_prepared": True,
        "staging_verified": False,
        "promotion_state": "not_attempted",
        "barrier_used": False,
        "rollback_attempted": False,
        "rollback_restored": False,
        "backup_retained": False,
        "transaction_retained": True,
        "recovery_required": True,
    }
    assert isinstance(target_manifest, BridgeBundleManifest)
    assert target_manifest.sha256 == old_manifest_sha256
    assert transaction_path.is_dir()
    assert cleanup_secret not in json.dumps(result.to_dict())
    assert str(fixture.project) not in json.dumps(result.to_dict())


def test_prepare_rejects_staging_rehash_mismatch(tmp_path: Path) -> None:
    fixture = _redeploy_fixture(tmp_path)
    old_manifest_sha256 = fixture.old_manifest.sha256
    source_manifest_sha256 = fixture.source_manifest.sha256

    def corrupt_copy(source: Path, target: Path) -> str:
        target.write_bytes(source.read_bytes() + b"corrupt")
        return str(target)

    with patch(
        "prefab_sentinel.bridge_deploy.shutil.copyfile",
        side_effect=corrupt_copy,
    ):
        result = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="a" * 32,
        )

    target_manifest = build_bridge_manifest(fixture.target)
    source_manifest = build_bridge_manifest(fixture.source)
    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_STAGING_MISMATCH")
    assert isinstance(target_manifest, BridgeBundleManifest)
    assert isinstance(source_manifest, BridgeBundleManifest)
    assert target_manifest.sha256 == old_manifest_sha256
    assert source_manifest.sha256 == source_manifest_sha256


def test_prepare_rejects_unmanaged_target_before_transaction_creation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            (
                "PrefabSentinel.UnityEditorControlBridge.cs",
                b'public const string BridgeVersion = "1.2.3";',
            ),
        ],
    )
    target = project / "Assets/Editor/PrefabSentinel"
    target.mkdir(parents=True)
    target.joinpath("user-file.txt").write_text("mine", encoding="utf-8")

    result = prepare_bridge_deploy(project, target, source, run_id="a" * 32)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_TARGET_UNMANAGED")
    assert not (project / "Library/PrefabSentinel/deploy-transactions").exists()


def test_prepare_rejects_transaction_path_collision(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)
    cleanup = cleanup_deploy_transaction(prepared)
    assert cleanup.success is True
    prepared.transaction_path.mkdir(parents=True)
    prepared.transaction_path.joinpath("evidence").write_text("keep", encoding="utf-8")

    result = prepare_bridge_deploy(
        prepared.project_root,
        prepared.target_path,
        prepared.project_root.parent / "source",
        run_id=prepared.run_id,
    )

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_STAGING_FAILED")
    assert prepared.transaction_path.joinpath("evidence").read_text(encoding="utf-8") == "keep"


def test_cleanup_deploy_transaction_uses_distinct_cleanup_code(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)

    with patch(
        "prefab_sentinel.bridge_deploy.shutil.rmtree",
        side_effect=OSError("cleanup unavailable"),
    ):
        result = cleanup_deploy_transaction(prepared)

    assert (result.success, result.code) == (False, "DEPLOY_CLEANUP_FAILED")


def test_cleanup_real_traversal_logs_first_unlink_failure_and_stops(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    shutil.rmtree(prepared.transaction_path)
    prepared.transaction_path.mkdir()
    deleted = prepared.transaction_path / "00-deleted"
    failing = prepared.transaction_path / "10-failing"
    untouched = prepared.transaction_path / "20-untouched"
    for path in (deleted, failing, untouched):
        path.write_bytes(path.name.encode())
    sentinel = "PRIVATE_UNLINK_FAILURE"
    private_path = str(tmp_path / "private-unlink-path")
    real_scandir = os.scandir
    real_unlink = os.unlink
    attempted: list[str] = []

    class OrderedScandir:
        def __init__(self, path: Any) -> None:
            self._iterator = real_scandir(path)
            self._ordered = iter(
                sorted(self._iterator, key=lambda entry: entry.name)
            )

        def __iter__(self) -> OrderedScandir:
            return self

        def __next__(self) -> Any:
            return next(self._ordered)

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: Any) -> None:
            self._iterator.close()

    def fail_second_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        child_name = Path(os.fsdecode(path)).name
        attempted.append(child_name)
        if child_name == failing.name:
            raise PermissionError(13, sentinel, private_path)
        real_unlink(path, *args, **kwargs)

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with (
        patch.object(bridge_deploy.os, "scandir", side_effect=OrderedScandir),
        patch.object(bridge_deploy.os, "unlink", side_effect=fail_second_unlink),
    ):
        result = cleanup_deploy_transaction(prepared)

    assert_error_envelope(
        result,
        code="DEPLOY_CLEANUP_FAILED",
        message_match=r"^Bridge deploy transaction could not be cleaned up\.$",
        data={},
    )
    assert attempted == [deleted.name, failing.name]
    assert not deleted.exists()
    assert failing.read_bytes() == failing.name.encode()
    assert untouched.read_bytes() == untouched.name.encode()
    records = [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]
    assert len(records) == 1
    assert records[0].args == ("unlink", "PermissionError", 13, None)
    assert records[0].exc_info is None
    assert records[0].stack_info is None
    private_log = records[0].getMessage()
    assert "phase=transaction_cleanup" in private_log
    assert sentinel not in private_log
    assert private_path not in private_log
    assert str(prepared.transaction_path) not in private_log


@pytest.mark.parametrize(
    ("operation", "expected_operation"),
    [
        ("child_rmdir", "child_rmdir"),
        ("root_rmdir", "root_rmdir"),
        ("scandir", "scandir"),
        ("open", "open"),
        ("lstat", "lstat"),
        ("close", "close"),
        ("unknown", "other"),
    ],
)
def test_cleanup_callback_classifies_only_allowlisted_operations(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    operation: str,
    expected_operation: str,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    sentinel = "PRIVATE_CALLABLE_NAME"

    def private_callable_name_must_not_escape(*_args: Any) -> None:
        raise AssertionError("the classifier must not execute the callback callable")

    functions = {
        "child_rmdir": os.rmdir,
        "root_rmdir": os.rmdir,
        "scandir": os.scandir,
        "open": os.open,
        "lstat": os.lstat,
        "close": os.close,
        "unknown": private_callable_name_must_not_escape,
    }
    callback_path = (
        prepared.transaction_path
        if operation == "root_rmdir"
        else prepared.transaction_path / "private-child"
    )
    if operation == "child_rmdir":
        callback_path.mkdir()
    error = PermissionError(13, "PRIVATE_OPERATION_FAILURE", str(callback_path))
    error.__dict__["winerror"] = 1234

    def invoke_callback(
        path: Path,
        *,
        onerror: Any = None,
    ) -> None:
        assert path == prepared.transaction_path
        assert onerror is not None
        try:
            onerror(
                functions[operation],
                callback_path,
                (type(error), error, None),
            )
        except OSError as raised:
            assert raised is error
            raise
        raise AssertionError("cleanup callback returned instead of raising")

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with patch.object(
        bridge_deploy.shutil,
        "rmtree",
        side_effect=invoke_callback,
    ):
        result = cleanup_deploy_transaction(prepared)

    assert_error_envelope(
        result,
        code="DEPLOY_CLEANUP_FAILED",
        message_match=r"^Bridge deploy transaction could not be cleaned up\.$",
        data={},
    )
    records = [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]
    assert len(records) == 1
    assert records[0].args == (
        expected_operation,
        "PermissionError",
        13,
        1234,
    )
    private_log = records[0].getMessage()
    assert sentinel not in private_log
    assert "private_callable_name_must_not_escape" not in private_log
    assert str(callback_path) not in private_log
    if operation == "child_rmdir":
        assert callback_path.is_dir()
        assert prepared.transaction_path.is_dir()


def test_cleanup_direct_oserror_logs_rmtree_with_sanitized_codes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    sentinel = "PRIVATE_DIRECT_CLEANUP_FAILURE"
    private_path = str(tmp_path / "private-direct-path")
    error = OSError(5, sentinel, private_path)
    error.__dict__["winerror"] = 32

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with patch.object(
        bridge_deploy.shutil,
        "rmtree",
        side_effect=error,
    ) as remove_tree:
        result = cleanup_deploy_transaction(prepared)

    assert_error_envelope(
        result,
        code="DEPLOY_CLEANUP_FAILED",
        message_match=r"^Bridge deploy transaction could not be cleaned up\.$",
        data={},
    )
    assert remove_tree.call_count == 1
    records = [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]
    assert len(records) == 1
    assert records[0].args == ("rmtree", "OSError", 5, 32)
    serialized = json.dumps(result.to_dict())
    assert sentinel not in serialized
    assert private_path not in serialized
    assert sentinel not in records[0].getMessage()
    assert private_path not in records[0].getMessage()


def test_cleanup_preserves_first_callback_tuple_across_later_failures(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    first = PermissionError(
        13,
        "PRIVATE_FIRST_FAILURE",
        str(prepared.transaction_path / "PRIVATE_FIRST_PATH"),
    )
    later_callback = OSError(
        5,
        "PRIVATE_LATER_CALLBACK",
        str(prepared.transaction_path / "PRIVATE_LATER_PATH"),
    )
    outer = OSError(
        16,
        "PRIVATE_OUTER_FAILURE",
        str(prepared.transaction_path / "PRIVATE_OUTER_PATH"),
    )

    def invoke_multiple_failures(
        _path: Path,
        *,
        onerror: Any = None,
    ) -> None:
        assert onerror is not None
        for function, callback_path, error in (
            (
                os.unlink,
                prepared.transaction_path / "PRIVATE_FIRST_PATH",
                first,
            ),
            (os.rmdir, prepared.transaction_path, later_callback),
        ):
            with pytest.raises(OSError) as caught:
                onerror(
                    function,
                    callback_path,
                    (type(error), error, None),
                )
            assert caught.value is error
        raise outer

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with patch.object(
        bridge_deploy.shutil,
        "rmtree",
        side_effect=invoke_multiple_failures,
    ):
        result = cleanup_deploy_transaction(prepared)

    assert_error_envelope(
        result,
        code="DEPLOY_CLEANUP_FAILED",
        message_match=r"^Bridge deploy transaction could not be cleaned up\.$",
        data={},
    )
    records = [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]
    assert len(records) == 1
    assert records[0].args == ("unlink", "PermissionError", 13, None)
    private_log = records[0].getMessage()
    for sentinel in (
        "PRIVATE_FIRST_FAILURE",
        "PRIVATE_FIRST_PATH",
        "PRIVATE_LATER_CALLBACK",
        "PRIVATE_LATER_PATH",
        "PRIVATE_OUTER_FAILURE",
        "PRIVATE_OUTER_PATH",
    ):
        assert sentinel not in private_log


def test_cleanup_excludes_custom_type_and_non_exact_integer_codes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)

    private_error_type = type(
        "PRIVATE_CUSTOM_TYPE",
        (OSError,),
        {"__module__": "builtins"},
    )
    error = private_error_type("PRIVATE_CUSTOM_MESSAGE")
    error.errno = True
    error.__dict__["winerror"] = "PRIVATE_WINERROR"

    def invoke_callback(
        _path: Path,
        *,
        onerror: Any = None,
    ) -> None:
        assert onerror is not None
        try:
            onerror(
                os.unlink,
                prepared.transaction_path / "PRIVATE_CUSTOM_PATH",
                (type(error), error, None),
            )
        except OSError as raised:
            assert raised is error
            raise
        raise AssertionError("cleanup callback returned instead of raising")

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with patch.object(
        bridge_deploy.shutil,
        "rmtree",
        side_effect=invoke_callback,
    ):
        result = cleanup_deploy_transaction(prepared)

    assert_error_envelope(
        result,
        code="DEPLOY_CLEANUP_FAILED",
        message_match=r"^Bridge deploy transaction could not be cleaned up\.$",
        data={},
    )
    records = [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]
    assert len(records) == 1
    assert records[0].args == ("unlink", "OSError", None, None)
    private_log = records[0].getMessage()
    serialized = json.dumps(result.to_dict())
    for sentinel in (
        "PRIVATE_CUSTOM_TYPE",
        "PRIVATE_CUSTOM_MESSAGE",
        "PRIVATE_WINERROR",
        "PRIVATE_CUSTOM_PATH",
    ):
        assert sentinel not in private_log
        assert sentinel not in serialized


def test_cleanup_direct_oserror_rejects_spoofed_builtin_type_name(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    private_error_type = type(
        "PRIVATE_CUSTOM_TYPE",
        (OSError,),
        {"__module__": "builtins"},
    )
    error = private_error_type(
        13,
        "PRIVATE_DIRECT_CUSTOM_MESSAGE",
        str(prepared.transaction_path / "PRIVATE_DIRECT_CUSTOM_PATH"),
    )

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with patch.object(
        bridge_deploy.shutil,
        "rmtree",
        side_effect=error,
    ):
        result = cleanup_deploy_transaction(prepared)

    assert_error_envelope(
        result,
        code="DEPLOY_CLEANUP_FAILED",
        message_match=r"^Bridge deploy transaction could not be cleaned up\.$",
        data={},
    )
    records = [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]
    assert len(records) == 1
    assert records[0].args == ("rmtree", "OSError", 13, None)
    private_log = records[0].getMessage()
    serialized = json.dumps(result.to_dict())
    for sentinel in (
        "PRIVATE_CUSTOM_TYPE",
        "PRIVATE_DIRECT_CUSTOM_MESSAGE",
        "PRIVATE_DIRECT_CUSTOM_PATH",
    ):
        assert sentinel not in private_log
        assert sentinel not in serialized


def test_cleanup_missing_numeric_codes_are_logged_as_none(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    error = OSError("PRIVATE_MISSING_CODES")

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with patch.object(
        bridge_deploy.shutil,
        "rmtree",
        side_effect=error,
    ):
        result = cleanup_deploy_transaction(prepared)

    assert_error_envelope(
        result,
        code="DEPLOY_CLEANUP_FAILED",
        message_match=r"^Bridge deploy transaction could not be cleaned up\.$",
        data={},
    )
    records = [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]
    assert len(records) == 1
    assert records[0].args == ("rmtree", "OSError", None, None)
    assert "PRIVATE_MISSING_CODES" not in records[0].getMessage()


def test_cleanup_callback_propagates_same_non_oserror_without_logging(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    error = RuntimeError("PRIVATE_NON_OSERROR")

    def invoke_callback(
        _path: Path,
        *,
        onerror: Any = None,
    ) -> None:
        assert onerror is not None
        onerror(
            os.unlink,
            prepared.transaction_path / "private-non-oserror",
            (type(error), error, None),
        )
        raise AssertionError("cleanup callback returned instead of raising")

    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")
    with (
        patch.object(
            bridge_deploy.shutil,
            "rmtree",
            side_effect=invoke_callback,
        ),
        pytest.raises(RuntimeError) as caught,
    ):
        cleanup_deploy_transaction(prepared)

    assert caught.value is error
    assert not [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]


def test_cleanup_success_absence_and_unsafe_guard_emit_no_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    caplog.set_level("WARNING", logger="prefab_sentinel.bridge_deploy")

    removed = cleanup_deploy_transaction(prepared)
    absent = cleanup_deploy_transaction(prepared)
    with (
        patch.object(
            bridge_deploy,
            "_path_chain_contains_link",
            return_value=True,
        ),
        patch.object(
            bridge_deploy.shutil,
            "rmtree",
            side_effect=AssertionError("unsafe cleanup must not delete"),
        ) as remove_tree,
    ):
        unsafe = cleanup_deploy_transaction(prepared)

    assert removed.to_dict() == {
        "success": True,
        "severity": "info",
        "code": "DEPLOY_OK",
        "message": "Bridge deploy transaction was cleaned up.",
        "data": {},
        "diagnostics": [],
    }
    assert absent.to_dict() == {
        "success": True,
        "severity": "info",
        "code": "DEPLOY_OK",
        "message": "Bridge deploy transaction is absent.",
        "data": {},
        "diagnostics": [],
    }
    assert unsafe.to_dict() == {
        "success": False,
        "severity": "error",
        "code": "DEPLOY_CLEANUP_FAILED",
        "message": "Bridge deploy transaction path is unsafe.",
        "data": {},
        "diagnostics": [],
    }
    remove_tree.assert_not_called()
    assert not [
        record
        for record in caplog.records
        if record.name == "prefab_sentinel.bridge_deploy"
        and record.getMessage().startswith("Bridge transaction cleanup failed ")
    ]


def test_prepare_rejects_linked_transaction_parent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            (
                "PrefabSentinel.UnityEditorControlBridge.cs",
                b'public const string BridgeVersion = "1.2.3";',
            ),
        ],
    )
    target = project / "Assets/Editor/PrefabSentinel"
    target.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (project / "Library").symlink_to(external, target_is_directory=True)

    result = prepare_bridge_deploy(project, target, source, run_id="a" * 32)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_STAGING_FAILED")
    assert not list(external.iterdir())


def test_prepare_rejects_invalid_run_id(tmp_path: Path) -> None:
    fixture = _redeploy_fixture(tmp_path)

    result = prepare_bridge_deploy(
        fixture.project,
        fixture.target,
        fixture.source,
        run_id="A" * 32,
    )

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_STAGING_FAILED")
    assert not (fixture.project / "Library/PrefabSentinel/deploy-transactions").exists()


def test_prepare_source_manifest_replace_failure_cleans_private_transaction(
    tmp_path: Path,
) -> None:
    fixture = _redeploy_fixture(tmp_path)
    prior_record = load_ownership_record(fixture.project)
    assert isinstance(prior_record, BridgeOwnershipRecord)
    transaction_path = fixture.project / (
        "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
    )
    manifest_path = transaction_path / "source-manifest-v1.json"
    temporary_paths: list[Path] = []

    def reject_manifest_replace(source: Path, destination: Path) -> None:
        assert Path(destination) == manifest_path
        temporary_paths.append(Path(source))
        raise OSError("manifest replacement unavailable")

    with patch(
        "prefab_sentinel.bridge_deploy.os.replace",
        side_effect=reject_manifest_replace,
    ):
        result = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="a" * 32,
        )

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_STAGING_FAILED")
    assert not manifest_path.exists()
    assert temporary_paths
    assert all(not path.exists() for path in temporary_paths)
    assert not transaction_path.exists()
    assert verify_deployed_target(
        fixture.target,
        prior_record.manifest,
    ).success


def test_prepare_rejects_cross_filesystem_before_returning_preparation(
    tmp_path: Path,
) -> None:
    fixture = _redeploy_fixture(tmp_path)
    original_stat = os.stat
    transaction_path = fixture.project / (
        "Library/PrefabSentinel/deploy-transactions/" + "a" * 32
    )
    staging_path = transaction_path / "staged-target"

    def cross_device_stat(
        path: str | Path,
        *,
        follow_symlinks: bool = True,
    ) -> object:
        result = original_stat(path, follow_symlinks=follow_symlinks)
        device = result.st_dev + 1 if Path(path) == staging_path else result.st_dev
        return SimpleNamespace(st_dev=device, st_mode=result.st_mode)

    with patch(
        "prefab_sentinel.bridge_deploy.os.stat",
        side_effect=cross_device_stat,
    ):
        result = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="a" * 32,
        )

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_CROSS_FILESYSTEM")
    assert not transaction_path.exists()
    assert fixture.target.exists()


def test_prepare_rejects_reparse_transaction_parent(tmp_path: Path) -> None:
    fixture = _redeploy_fixture(tmp_path)
    transaction_parent = (
        fixture.project / "Library/PrefabSentinel/deploy-transactions"
    )
    transaction_parent.mkdir(parents=True, exist_ok=True)

    with patch(
        "prefab_sentinel.bridge_deploy.os.lstat",
        side_effect=_fake_reparse_lstat(transaction_parent),
    ):
        result = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="a" * 32,
        )

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_STAGING_FAILED")
    assert not (transaction_parent / ("a" * 32)).exists()


def test_prepare_uses_one_ownership_snapshot_for_manifest_and_classification(
    tmp_path: Path,
) -> None:
    fixture = _redeploy_fixture(tmp_path)
    record = load_ownership_record(fixture.project)
    assert isinstance(record, BridgeOwnershipRecord)

    with patch(
        "prefab_sentinel.bridge_deploy.load_ownership_record",
        side_effect=[record, None],
    ) as load_record:
        prepared = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="a" * 32,
        )

    assert isinstance(prepared, DeployPreparation)
    assert load_record.call_count == 1
    assert prepared.previous_manifest == record.manifest
    assert prepared.ownership.managed_entries == tuple(
        entry.path for entry in record.manifest.files
    )


def test_absent_target_is_installed_by_one_directory_rename(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_rename = os.rename

    with (
        patch(
            "prefab_sentinel.bridge_deploy.os.rename",
            wraps=original_rename,
        ) as rename,
        patch(
            "prefab_sentinel.bridge_deploy.shutil.move",
            side_effect=AssertionError("copy fallback is forbidden"),
        ),
        patch(
            "prefab_sentinel.bridge_deploy.shutil.copytree",
            side_effect=AssertionError("copy fallback is forbidden"),
        ),
        patch(
            "prefab_sentinel.bridge_deploy.shutil.copyfile",
            side_effect=AssertionError("copy fallback is forbidden"),
        ),
        patch(
            "prefab_sentinel.bridge_deploy.shutil.copy",
            side_effect=AssertionError("copy fallback is forbidden"),
        ),
        patch(
            "prefab_sentinel.bridge_deploy.shutil.copy2",
            side_effect=AssertionError("copy fallback is forbidden"),
        ),
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, FreshTargetInstall)
    assert result.promotion_state == "installed_fresh"
    assert result.barrier_used is False
    assert rename.call_count == 1
    assert rename.call_args.args == (prepared.staging_path, prepared.target_path)
    record = load_ownership_record(prepared.project_root)
    assert isinstance(record, BridgeOwnershipRecord)
    assert record.manifest == prepared.source_manifest
    assert verify_deployed_target(
        prepared.target_path, prepared.source_manifest
    ).success
    assert not prepared.staging_path.exists()
    assert (
        prepared.project_root / "Library/PrefabSentinel/deploy.lock"
    ).is_file()


def test_fresh_target_rename_failure_leaves_no_target(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)

    with patch(
        "prefab_sentinel.bridge_deploy.os.rename",
        side_effect=OSError("rename unavailable"),
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert not prepared.target_path.exists()
    assert verify_deployed_target(
        prepared.staging_path, prepared.source_manifest
    ).success


def test_fresh_target_rejects_cross_filesystem_before_rename(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_stat = os.stat

    def cross_device_stat(
        path: str | Path,
        *,
        follow_symlinks: bool = True,
    ) -> object:
        result = original_stat(path, follow_symlinks=follow_symlinks)
        path_value = Path(path)
        if path_value == prepared.staging_path:
            return SimpleNamespace(
                st_dev=result.st_dev + 1,
                st_mode=result.st_mode,
            )
        return SimpleNamespace(st_dev=result.st_dev, st_mode=result.st_mode)

    with patch(
        "prefab_sentinel.bridge_deploy.os.stat",
        side_effect=cross_device_stat,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_CROSS_FILESYSTEM")
    assert not prepared.target_path.exists()
    assert verify_deployed_target(
        prepared.staging_path, prepared.source_manifest
    ).success


def test_fresh_target_final_mismatch_removes_partial_target(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_rename = os.rename

    def rename_then_corrupt(source: Path, target: Path) -> None:
        original_rename(source, target)
        target.joinpath("PrefabSentinel.UnityEditorControlBridge.cs").write_bytes(
            b'public const string BridgeVersion = "9.9.9";'
        )

    with patch(
        "prefab_sentinel.bridge_deploy.os.rename",
        side_effect=rename_then_corrupt,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_FINAL_MANIFEST_MISMATCH")
    assert not prepared.target_path.exists()


def test_fresh_target_ownership_failure_retains_complete_recovery_target(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    record_path = (
        prepared.project_root
        / "Library/PrefabSentinel/deploy-ownership-v1.json"
    )
    temporary_paths: list[Path] = []

    def reject_ownership_replace(source: Path, destination: Path) -> None:
        assert Path(destination) == record_path
        temporary_paths.append(Path(source))
        raise OSError("ownership replacement unavailable")

    with patch(
        "prefab_sentinel.bridge_deploy.os.replace",
        side_effect=reject_ownership_replace,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_WRITE_FAILED")
    assert result.data == {
        "promotion_state": "installed_fresh",
        "target_complete": True,
        "manifest_sha256": prepared.source_manifest.sha256,
        "bridge_version": prepared.source_manifest.bridge_version,
        "ownership_published": False,
        "recovery_required": True,
        "transaction_retained": True,
        "underlying_code": "DEPLOY_OWNERSHIP_WRITE_FAILED",
    }
    assert verify_deployed_target(
        prepared.target_path, prepared.source_manifest
    ).success
    assert not prepared.staging_path.exists()
    assert not record_path.exists()
    assert temporary_paths
    assert all(not path.exists() for path in temporary_paths)
    assert prepared.transaction_path.exists()
    assert (
        prepared.project_root / "Library/PrefabSentinel/deploy.lock"
    ).is_file()


def test_fresh_target_cleanup_failure_retains_typed_critical_recovery(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_rename = os.rename
    mismatched_bytes = b'public const string BridgeVersion = "9.9.9";'

    def rename_then_corrupt(source: Path, target: Path) -> None:
        original_rename(source, target)
        target.joinpath("PrefabSentinel.UnityEditorControlBridge.cs").write_bytes(
            mismatched_bytes
        )

    with (
        patch(
            "prefab_sentinel.bridge_deploy.os.rename",
            side_effect=rename_then_corrupt,
        ),
        patch(
            "prefab_sentinel.bridge_deploy.shutil.rmtree",
            side_effect=OSError("cleanup unavailable"),
        ),
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_ROLLBACK_FAILED")
    assert result.severity.value == "critical"
    assert result.data == {
        "promotion_state": "installed_fresh",
        "target_state": "mismatched_retained",
        "recovery_required": True,
        "transaction_retained": True,
    }
    assert (
        prepared.target_path / "PrefabSentinel.UnityEditorControlBridge.cs"
    ).read_bytes() == mismatched_bytes
    assert prepared.transaction_path.exists()


def test_fresh_target_lock_is_acquired_before_empty_target_removal(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    prepared.target_path.mkdir()
    original_open = os.open
    target_states_at_lock: list[bool] = []

    def observe_lock_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if str(path).endswith(".lock"):
            target_states_at_lock.append(prepared.target_path.is_dir())
        return original_open(path, flags, mode, dir_fd=dir_fd)

    with patch(
        "prefab_sentinel.bridge_deploy.os.open",
        side_effect=observe_lock_open,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, FreshTargetInstall)
    assert target_states_at_lock == [True]
    assert verify_deployed_target(
        prepared.target_path, prepared.source_manifest
    ).success


def test_fresh_target_rejects_nonempty_target_appearing_under_lock(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_open = os.open
    unmanaged_bytes = b"must survive"

    def create_unmanaged_target_after_lock(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".lock"):
            prepared.target_path.mkdir()
            prepared.target_path.joinpath("unmanaged.txt").write_bytes(
                unmanaged_bytes
            )
        return descriptor

    with patch(
        "prefab_sentinel.bridge_deploy.os.open",
        side_effect=create_unmanaged_target_after_lock,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert prepared.target_path.joinpath("unmanaged.txt").read_bytes() == unmanaged_bytes
    assert verify_deployed_target(
        prepared.staging_path, prepared.source_manifest
    ).success


def test_fresh_target_rejects_link_appearing_under_lock(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_bytes(b"must survive")
    original_open = os.open

    def create_link_after_lock(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".lock"):
            prepared.target_path.symlink_to(external, target_is_directory=True)
        return descriptor

    with patch(
        "prefab_sentinel.bridge_deploy.os.open",
        side_effect=create_link_after_lock,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert prepared.target_path.is_symlink()
    assert sentinel.read_bytes() == b"must survive"
    assert verify_deployed_target(
        prepared.staging_path, prepared.source_manifest
    ).success


def test_fresh_target_rejects_reparse_target_appearing_under_lock(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_open = os.open

    def create_target_after_lock(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".lock"):
            prepared.target_path.mkdir()
        return descriptor

    with (
        patch(
            "prefab_sentinel.bridge_deploy.os.open",
            side_effect=create_target_after_lock,
        ),
        patch(
            "prefab_sentinel.bridge_deploy.os.lstat",
            side_effect=_fake_reparse_lstat(prepared.target_path),
        ),
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert prepared.target_path.is_dir()
    assert verify_deployed_target(
        prepared.staging_path, prepared.source_manifest
    ).success


def test_fresh_target_rejects_ownership_change_under_lock(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_open = os.open

    def publish_changed_ownership_after_lock(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".lock"):
            _write_ownership_record(
                prepared.project_root,
                _ownership_payload(),
            )
        return descriptor

    with patch(
        "prefab_sentinel.bridge_deploy.os.open",
        side_effect=publish_changed_ownership_after_lock,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert result.success is False
    assert not prepared.target_path.exists()
    assert verify_deployed_target(
        prepared.staging_path, prepared.source_manifest
    ).success


def test_fresh_target_rejects_staging_change_under_lock_before_rename(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_open = os.open

    def corrupt_staging_after_lock(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".lock"):
            prepared.staging_path.joinpath(
                "PrefabSentinel.UnityEditorControlBridge.cs"
            ).write_bytes(b'public const string BridgeVersion = "9.9.9";')
        return descriptor

    with patch(
        "prefab_sentinel.bridge_deploy.os.open",
        side_effect=corrupt_staging_after_lock,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert not prepared.target_path.exists()
    assert prepared.staging_path.exists()
    assert (
        prepared.project_root / "Library/PrefabSentinel/deploy.lock"
    ).is_file()


def test_fresh_target_rejects_extra_staging_entry_under_lock_before_rename(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_open = os.open
    unmanaged_bytes = b"must not enter Assets"

    def add_unmanaged_staging_entry_after_lock(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".lock"):
            prepared.staging_path.joinpath("unmanaged.txt").write_bytes(
                unmanaged_bytes
            )
        return descriptor

    with patch(
        "prefab_sentinel.bridge_deploy.os.open",
        side_effect=add_unmanaged_staging_entry_after_lock,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert not prepared.target_path.exists()
    assert (
        prepared.staging_path / "unmanaged.txt"
    ).read_bytes() == unmanaged_bytes


def test_fresh_target_lock_owner_write_failure_releases_advisory_lock(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)

    with patch(
        "prefab_sentinel.bridge_deploy.os.write",
        side_effect=OSError("lock identity write unavailable"),
    ):
        result = install_fresh_target(prepared)

    lock_path = prepared.project_root / "Library/PrefabSentinel/deploy.lock"
    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert lock_path.is_file()
    assert not prepared.target_path.exists()
    assert verify_deployed_target(
        prepared.staging_path, prepared.source_manifest
    ).success
    reacquired = bridge_deploy._acquire_deploy_lock(
        prepared.project_root,
        "b" * 32,
    )
    assert isinstance(reacquired, int)
    bridge_deploy._release_deploy_lock(reacquired)


def test_fresh_target_rejects_lock_parent_becoming_reparse_after_creation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    lock_parent = project / "Library/PrefabSentinel"

    with patch(
        "prefab_sentinel.bridge_deploy.os.lstat",
        side_effect=_fake_reparse_lstat(lock_parent),
    ):
        result = bridge_deploy._acquire_deploy_lock(project, "a" * 32)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert not (lock_parent / "deploy.lock").exists()


def test_publish_ownership_replace_failure_preserves_existing_document(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    os.rename(prepared.staging_path, prepared.target_path)
    record_path = _write_ownership_record(
        prepared.project_root,
        _ownership_payload(target="Assets/Editor/OtherBridge"),
    )
    existing_bytes = record_path.read_bytes()
    temporary_paths: list[Path] = []

    def reject_ownership_replace(source: Path, destination: Path) -> None:
        assert Path(destination) == record_path
        temporary_paths.append(Path(source))
        raise OSError("ownership replacement unavailable")

    with patch(
        "prefab_sentinel.bridge_deploy.os.replace",
        side_effect=reject_ownership_replace,
    ):
        result = publish_ownership_record(prepared)

    assert (result.success, result.code) == (
        False,
        "DEPLOY_OWNERSHIP_WRITE_FAILED",
    )
    assert record_path.read_bytes() == existing_bytes
    assert temporary_paths
    assert all(not path.exists() for path in temporary_paths)
    assert verify_deployed_target(
        prepared.target_path, prepared.source_manifest
    ).success


def test_project_deploy_lock_is_persistent_and_reusable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    lock_path = project / "Library/PrefabSentinel/deploy.lock"

    first = bridge_deploy._acquire_deploy_lock(project, "a" * 32)

    assert isinstance(first, int)
    assert lock_path.read_text(encoding="utf-8") == "a" * 32
    bridge_deploy._release_deploy_lock(first)
    assert lock_path.is_file()

    second = bridge_deploy._acquire_deploy_lock(project, "b" * 32)

    assert isinstance(second, int)
    assert lock_path.read_text(encoding="utf-8") == "b" * 32
    bridge_deploy._release_deploy_lock(second)
    assert lock_path.is_file()


def test_project_deploy_lock_rejects_same_process_contention(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = bridge_deploy._acquire_deploy_lock(project, "a" * 32)
    assert isinstance(first, int)

    second = bridge_deploy._acquire_deploy_lock(project, "b" * 32)

    assert isinstance(second, ToolResponse)
    assert (second.success, second.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert str(project) not in second.message
    assert (
        project / "Library/PrefabSentinel/deploy.lock"
    ).read_text(encoding="utf-8") == "a" * 32
    bridge_deploy._release_deploy_lock(first)


def test_project_deploy_lock_arbitrates_simultaneous_same_process_callers(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    start_barrier = threading.Barrier(3)
    release_successful_callers = threading.Event()
    outcomes: Queue[tuple[str, int | ToolResponse | Exception]] = Queue()

    def acquire(run_id: str) -> None:
        start_barrier.wait()
        try:
            outcome = bridge_deploy._acquire_deploy_lock(project, run_id)
        except Exception as error:
            outcomes.put((run_id, error))
            return
        outcomes.put((run_id, outcome))
        if isinstance(outcome, int):
            release_successful_callers.wait()

    observed: list[tuple[str, int | ToolResponse | Exception]] = []
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(acquire, "a" * 32),
                executor.submit(acquire, "b" * 32),
            ]
            start_barrier.wait()
            observed = [outcomes.get(), outcomes.get()]
            release_successful_callers.set()
            for future in futures:
                future.result()

        unexpected = [
            outcome for _, outcome in observed if isinstance(outcome, Exception)
        ]
        successful = [
            (run_id, outcome)
            for run_id, outcome in observed
            if isinstance(outcome, int)
        ]
        busy = [
            outcome
            for _, outcome in observed
            if isinstance(outcome, ToolResponse)
        ]

        assert unexpected == []
        assert len(successful) == 1
        assert len(busy) == 1
        busy_result = busy[0]
        assert (
            busy_result.success,
            busy_result.code,
            busy_result.message,
        ) == (
            False,
            "DEPLOY_PROMOTION_FAILED",
            "Bridge deployment is already active or its lock is unavailable.",
        )
        assert str(project) not in busy_result.message
        assert (
            project / "Library/PrefabSentinel/deploy.lock"
        ).read_text(encoding="utf-8") == successful[0][0]
    finally:
        release_successful_callers.set()
        for _, outcome in observed:
            if isinstance(outcome, int):
                bridge_deploy._release_deploy_lock(outcome)

    third = bridge_deploy._acquire_deploy_lock(project, "c" * 32)
    assert isinstance(third, int)
    assert (
        project / "Library/PrefabSentinel/deploy.lock"
    ).read_text(encoding="utf-8") == "c" * 32
    bridge_deploy._release_deploy_lock(third)


def test_project_deploy_lock_blocks_another_process(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    held = bridge_deploy._acquire_deploy_lock(project, "a" * 32)
    assert isinstance(held, int)
    script = """
import sys
from pathlib import Path
from prefab_sentinel.bridge_deploy import _acquire_deploy_lock, _release_deploy_lock
from prefab_sentinel.contracts import ToolResponse

result = _acquire_deploy_lock(Path(sys.argv[1]), "b" * 32)
if isinstance(result, ToolResponse):
    raise SystemExit(2)
_release_deploy_lock(result)
raise SystemExit(0)
"""

    blocked = subprocess.run(
        [sys.executable, "-c", script, str(project)],
        check=False,
        capture_output=True,
        text=True,
    )
    bridge_deploy._release_deploy_lock(held)
    acquired = subprocess.run(
        [sys.executable, "-c", script, str(project)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert blocked.returncode == 2
    assert acquired.returncode == 0


def test_project_deploy_lock_releases_when_install_raises(tmp_path: Path) -> None:
    prepared = _fresh_preparation(tmp_path)

    with (
        patch(
            "prefab_sentinel.bridge_deploy.os.rename",
            side_effect=RuntimeError("forced unexpected failure"),
        ),
        pytest.raises(RuntimeError, match="forced unexpected failure"),
    ):
        install_fresh_target(prepared)

    lock_path = prepared.project_root / "Library/PrefabSentinel/deploy.lock"
    assert lock_path.is_file()
    reacquired = bridge_deploy._acquire_deploy_lock(
        prepared.project_root,
        "b" * 32,
    )
    assert isinstance(reacquired, int)
    bridge_deploy._release_deploy_lock(reacquired)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows prevents replacing this open lock path",
)
def test_project_deploy_lock_release_never_unlinks_replacement(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    held = bridge_deploy._acquire_deploy_lock(project, "a" * 32)
    assert isinstance(held, int)
    lock_path = project / "Library/PrefabSentinel/deploy.lock"
    replacement = lock_path.with_suffix(".replacement")
    replacement.write_text("replacement-owner", encoding="utf-8")
    os.replace(replacement, lock_path)

    bridge_deploy._release_deploy_lock(held)

    assert lock_path.read_text(encoding="utf-8") == "replacement-owner"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows prevents replacing this open lock path",
)
def test_project_deploy_lock_rejects_path_replaced_during_open(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    lock_path = project / "Library/PrefabSentinel/deploy.lock"
    replacement = lock_path.with_suffix(".replacement")
    original_open = os.open

    def replace_path_after_open(
        path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == lock_path:
            replacement.write_text("replacement-owner", encoding="utf-8")
            os.replace(replacement, lock_path)
        return descriptor

    with patch(
        "prefab_sentinel.bridge_deploy.os.open",
        side_effect=replace_path_after_open,
    ):
        result = bridge_deploy._acquire_deploy_lock(project, "a" * 32)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_LOCK_REPLACED")
    assert lock_path.read_text(encoding="utf-8") == "replacement-owner"
    acquired = bridge_deploy._acquire_deploy_lock(project, "b" * 32)
    assert isinstance(acquired, int)
    bridge_deploy._release_deploy_lock(acquired)


def test_two_prepared_targets_install_sequentially_without_lost_ownership(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            (
                "PrefabSentinel.UnityEditorControlBridge.cs",
                b'public const string BridgeVersion = "1.2.3";',
            ),
        ],
    )
    target_a = project / "Assets/Editor/PrefabSentinelA"
    target_b = project / "Assets/Editor/PrefabSentinelB"
    prepared_a = prepare_bridge_deploy(
        project,
        target_a,
        source,
        run_id="a" * 32,
    )
    prepared_b = prepare_bridge_deploy(
        project,
        target_b,
        source,
        run_id="b" * 32,
    )
    assert isinstance(prepared_a, DeployPreparation)
    assert isinstance(prepared_b, DeployPreparation)

    installed_a = install_fresh_target(prepared_a)
    installed_b = install_fresh_target(prepared_b)

    assert isinstance(installed_a, FreshTargetInstall)
    assert isinstance(installed_b, FreshTargetInstall)
    ownership_path = project / "Library/PrefabSentinel/deploy-ownership-v1.json"
    ownership_document = json.loads(ownership_path.read_text(encoding="utf-8"))
    assert ownership_document["schema"] == (
        "prefab_sentinel_bridge_deploy_ownership_collection.v1"
    )
    assert [record["target"] for record in ownership_document["records"]] == [
        "Assets/Editor/PrefabSentinelA",
        "Assets/Editor/PrefabSentinelB",
    ]
    record_a = load_ownership_record(project, target_a)
    record_b = load_ownership_record(project, target_b)
    assert isinstance(record_a, BridgeOwnershipRecord)
    assert isinstance(record_b, BridgeOwnershipRecord)
    assert record_a.manifest == prepared_a.source_manifest
    assert record_b.manifest == prepared_b.source_manifest


def test_publication_manifest_mismatch_reports_incomplete_critical_recovery(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    original_publish = bridge_deploy._publish_ownership_record_locked

    def corrupt_then_publish(current: DeployPreparation) -> ToolResponse:
        current.target_path.joinpath(
            "PrefabSentinel.UnityEditorControlBridge.cs"
        ).write_bytes(b'public const string BridgeVersion = "9.9.9";')
        return original_publish(current)

    with patch(
        "prefab_sentinel.bridge_deploy._publish_ownership_record_locked",
        side_effect=corrupt_then_publish,
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (
        False,
        "DEPLOY_FINAL_MANIFEST_MISMATCH",
    )
    assert result.severity.value == "critical"
    assert result.data == {
        "promotion_state": "installed_fresh",
        "target_complete": False,
        "manifest_sha256": prepared.source_manifest.sha256,
        "bridge_version": prepared.source_manifest.bridge_version,
        "ownership_published": False,
        "recovery_required": True,
        "transaction_retained": True,
        "underlying_code": "DEPLOY_FINAL_MANIFEST_MISMATCH",
    }
    assert not (
        prepared.project_root
        / "Library/PrefabSentinel/deploy-ownership-v1.json"
    ).exists()
    assert prepared.transaction_path.exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows prevents replacing this open lock path",
)
def test_project_deploy_lock_rechecks_identity_after_os_lock(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    lock_path = project / "Library/PrefabSentinel/deploy.lock"
    replacement = lock_path.with_suffix(".replacement")
    original_lock = bridge_deploy._lock_descriptor_nonblocking

    def replace_path_then_lock(descriptor: int) -> None:
        replacement.write_text("replacement-owner", encoding="utf-8")
        os.replace(replacement, lock_path)
        original_lock(descriptor)

    with patch(
        "prefab_sentinel.bridge_deploy._lock_descriptor_nonblocking",
        side_effect=replace_path_then_lock,
    ):
        first = bridge_deploy._acquire_deploy_lock(project, "a" * 32)

    second = bridge_deploy._acquire_deploy_lock(project, "b" * 32)

    assert isinstance(first, ToolResponse)
    assert (first.success, first.code) == (False, "DEPLOY_LOCK_REPLACED")
    assert isinstance(second, int)
    assert lock_path.read_text(encoding="utf-8") == "b" * 32
    bridge_deploy._release_deploy_lock(second)


def test_legacy_bare_ownership_record_loads_and_rewrites_as_collection(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    os.rename(prepared.staging_path, prepared.target_path)
    record_path = (
        prepared.project_root
        / "Library/PrefabSentinel/deploy-ownership-v1.json"
    )
    legacy_document = _ownership_payload(
        owned_meta_entries=[
            "PrefabSentinel.UnityEditorControlBridge.cs.meta",
        ]
    )
    record_path.write_text(json.dumps(legacy_document), encoding="utf-8")

    loaded = load_ownership_record(
        prepared.project_root,
        prepared.target_path,
    )

    assert isinstance(loaded, BridgeOwnershipRecord)
    assert loaded.target_path == "Assets/Editor/PrefabSentinel"
    assert loaded.manifest == prepared.source_manifest

    published = publish_ownership_record(prepared)

    assert published.success is True
    canonical = json.loads(record_path.read_text(encoding="utf-8"))
    assert canonical["schema"] == (
        "prefab_sentinel_bridge_deploy_ownership_collection.v1"
    )
    assert canonical["records"] == [legacy_document]


@pytest.mark.parametrize("malformation", ["unknown_field", "unknown_schema"])
def test_malformed_or_unknown_bare_ownership_record_remains_invalid(
    tmp_path: Path,
    malformation: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    record_path = project / "Library/PrefabSentinel/deploy-ownership-v1.json"
    record_path.parent.mkdir(parents=True)
    bare = _ownership_payload()
    if malformation == "unknown_field":
        bare["unexpected"] = True
    else:
        bare["schema"] = "unknown"
    record_path.write_text(json.dumps(bare), encoding="utf-8")

    result = load_ownership_record(project)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_OWNERSHIP_INVALID")
    assert str(project) not in result.message


def test_public_ownership_publication_serializes_concurrent_targets(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "source"
    _write_manifest_source(
        source,
        [
            (
                "PrefabSentinel.UnityEditorControlBridge.cs",
                b'public const string BridgeVersion = "1.2.3";',
            ),
        ],
    )
    prepared_a = prepare_bridge_deploy(
        project,
        project / "Assets/Editor/PrefabSentinelA",
        source,
        run_id="a" * 32,
    )
    prepared_b = prepare_bridge_deploy(
        project,
        project / "Assets/Editor/PrefabSentinelB",
        source,
        run_id="b" * 32,
    )
    assert isinstance(prepared_a, DeployPreparation)
    assert isinstance(prepared_b, DeployPreparation)
    os.rename(prepared_a.staging_path, prepared_a.target_path)
    os.rename(prepared_b.staging_path, prepared_b.target_path)
    publication_barrier = threading.Barrier(2)
    original_atomic_write = bridge_deploy._atomic_write_json

    def synchronize_atomic_write(
        destination: Path,
        document: dict[str, object],
    ) -> bool:
        with suppress(threading.BrokenBarrierError):
            publication_barrier.wait(timeout=0.2)
        return original_atomic_write(destination, document)

    with (
        patch(
            "prefab_sentinel.bridge_deploy._atomic_write_json",
            side_effect=synchronize_atomic_write,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        future_a = executor.submit(publish_ownership_record, prepared_a)
        future_b = executor.submit(publish_ownership_record, prepared_b)
        result_a = future_a.result()
        result_b = future_b.result()

    assert result_a.success is True
    assert result_b.success is True
    record_path = project / "Library/PrefabSentinel/deploy-ownership-v1.json"
    document = json.loads(record_path.read_text(encoding="utf-8"))
    assert [record["target"] for record in document["records"]] == [
        "Assets/Editor/PrefabSentinelA",
        "Assets/Editor/PrefabSentinelB",
    ]


def test_public_ownership_publication_fails_closed_on_lock_collision(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    os.rename(prepared.staging_path, prepared.target_path)
    held = bridge_deploy._acquire_deploy_lock(
        prepared.project_root,
        "b" * 32,
    )
    assert isinstance(held, int)

    result = publish_ownership_record(prepared)

    assert isinstance(result, ToolResponse)
    assert (result.success, result.code) == (False, "DEPLOY_PROMOTION_FAILED")
    assert not (
        prepared.project_root
        / "Library/PrefabSentinel/deploy-ownership-v1.json"
    ).exists()
    bridge_deploy._release_deploy_lock(held)


def test_public_ownership_publication_releases_lock_when_write_raises(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)
    os.rename(prepared.staging_path, prepared.target_path)

    with (
        patch(
            "prefab_sentinel.bridge_deploy._atomic_write_json",
            side_effect=RuntimeError("forced publication failure"),
        ),
        pytest.raises(RuntimeError, match="forced publication failure"),
    ):
        publish_ownership_record(prepared)

    lock_path = prepared.project_root / "Library/PrefabSentinel/deploy.lock"
    assert lock_path.is_file()
    reacquired = bridge_deploy._acquire_deploy_lock(
        prepared.project_root,
        "b" * 32,
    )
    assert isinstance(reacquired, int)
    bridge_deploy._release_deploy_lock(reacquired)


def test_fresh_install_uses_locked_private_ownership_publication(
    tmp_path: Path,
) -> None:
    prepared = _fresh_preparation(tmp_path)

    with patch(
        "prefab_sentinel.bridge_deploy.publish_ownership_record",
        side_effect=AssertionError("public publisher would deadlock"),
    ):
        result = install_fresh_target(prepared)

    assert isinstance(result, FreshTargetInstall)


class BridgeDeployVerificationEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def _prepare_promoted_target(
        self,
    ) -> tuple[DeployPreparation, bytes, dict[str, object], SimpleNamespace]:
        fixture = _redeploy_fixture(self.root)
        prepared = prepare_bridge_deploy(
            fixture.project,
            fixture.target,
            fixture.source,
            run_id="c" * 32,
        )
        self.assertIsInstance(prepared, DeployPreparation)
        if not isinstance(prepared, DeployPreparation):
            raise AssertionError("expected deploy preparation")

        prepared.target_path.rename(prepared.backup_path)
        prepared.staging_path.rename(prepared.target_path)
        record_path = (
            prepared.project_root
            / "Library/PrefabSentinel/deploy-ownership-v1.json"
        )
        original_record = record_path.read_bytes()
        promotion: dict[str, object] = {
            "success": True,
            "severity": "info",
            "code": "DEPLOY_OK",
            "message": "Bridge bundle was promoted.",
            "data": {
                "promotion_state": "promoted",
                "barrier_used": True,
                "rollback_attempted": False,
                "rollback_restored": False,
                "backup_retained": True,
                "target_complete": True,
            },
            "diagnostics": [],
        }
        return prepared, original_record, promotion, fixture

    def _assert_ownership_failure_state(
        self,
        prepared: DeployPreparation,
        result: ToolResponse,
        *,
        target_complete: bool,
    ) -> None:
        observed_state = {
            key: result.data[key]
            for key in (
                "manifest_sha256",
                "bridge_version",
                "promotion_state",
                "barrier_used",
                "rollback_attempted",
                "rollback_restored",
                "backup_retained",
                "target_complete",
                "ownership_published",
                "transaction_retained",
            )
        }
        self.assertEqual(
            observed_state,
            {
                "manifest_sha256": (
                    prepared.source_manifest.sha256 if target_complete else ""
                ),
                "bridge_version": (
                    prepared.source_manifest.bridge_version
                    if target_complete
                    else ""
                ),
                "promotion_state": "promoted",
                "barrier_used": True,
                "rollback_attempted": False,
                "rollback_restored": False,
                "backup_retained": True,
                "target_complete": target_complete,
                "ownership_published": False,
                "transaction_retained": True,
            },
        )

    def test_verify_deployed_target_distinguishes_builder_error_mismatch_and_match(
        self,
    ) -> None:
        source_manifest = _source_manifest(self.root)
        equal_target = self.root / "equal-target"
        _write_manifest_source(
            equal_target,
            [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
        )
        unequal_target = self.root / "unequal-target"
        _write_manifest_source(
            unequal_target,
            [
                (
                    "PrefabSentinel.UnityEditorControlBridge.cs",
                    b'public const string BridgeVersion = "1.2.4";',
                )
            ],
        )

        builder_error = verify_deployed_target(
            self.root / "missing-target",
            source_manifest,
        )
        mismatch = verify_deployed_target(unequal_target, source_manifest)
        match = verify_deployed_target(equal_target, source_manifest)

        assert_error_envelope(
            builder_error,
            code="DEPLOY_SOURCE_NOT_FOUND",
            message_match=r"^Bridge source directory is unavailable\.$",
            data={},
        )
        assert_error_envelope(
            mismatch,
            code="DEPLOY_FINAL_MANIFEST_MISMATCH",
            message_match=(
                r"^Bridge target does not match the prepared source manifest\.$"
            ),
            data={},
        )
        self.assertEqual(
            (match.success, match.severity.value, match.code, match.message),
            (
                True,
                "info",
                "DEPLOY_OK",
                "Bridge target matches the prepared source manifest.",
            ),
        )

    def test_ownership_target_read_failure_clears_stale_verified_identity(
        self,
    ) -> None:
        prepared, original_record, promotion, _ = self._prepare_promoted_target()
        real_builder = bridge_deploy.build_bridge_manifest
        target_verifications = 0

        def fail_second_target_build(path: Path) -> BridgeBundleManifest | ToolResponse:
            nonlocal target_verifications
            if path == prepared.target_path:
                target_verifications += 1
                if target_verifications == 2:
                    return bridge_deploy._source_error(
                        "Bridge source file could not be read."
                    )
            return real_builder(path)

        with patch(
            "prefab_sentinel.bridge_deploy.build_bridge_manifest",
            side_effect=fail_second_target_build,
        ):
            result = bridge_deploy.complete_bridge_deploy(
                prepared,
                promotion,
                lock_held=True,
            )

        assert_error_envelope(
            result,
            code="DEPLOY_SOURCE_NOT_FOUND",
            message_match=(
                r"^Bridge target verification failed before ownership publication\.$"
            ),
        )
        self.assertEqual(target_verifications, 2)
        self._assert_ownership_failure_state(
            prepared,
            result,
            target_complete=False,
        )
        serialized = json.dumps(result.to_dict())
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("private-read-failure", serialized)
        record_path = (
            prepared.project_root
            / "Library/PrefabSentinel/deploy-ownership-v1.json"
        )
        self.assertEqual(record_path.read_bytes(), original_record)
        self.assertTrue(prepared.transaction_path.is_dir())

    def test_manifest_directory_read_log_is_sanitized_and_return_is_unchanged(
        self,
    ) -> None:
        source = self.root / "directory-source"
        _write_manifest_source(
            source,
            [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
        )
        sentinel = "PRIVATE_DIRECTORY_READ_SENTINEL"

        with (
            self.assertLogs("prefab_sentinel.bridge_deploy", level="WARNING")
            as captured,
            patch.object(
                Path,
                "iterdir",
                side_effect=OSError(13, sentinel, str(source)),
            ),
        ):
            result = build_bridge_manifest(source)

        assert_error_envelope(
            result,
            code="DEPLOY_SOURCE_NOT_FOUND",
            message_match=r"^Bridge source directory could not be read\.$",
            data={},
        )
        private_log = "\n".join(captured.output)
        self.assertIn("phase=manifest_directory_read", private_log)
        self.assertIn("error_type=PermissionError", private_log)
        self.assertIn("errno=13", private_log)
        self.assertNotIn(sentinel, private_log)
        self.assertNotIn(str(source), private_log)
        self.assertNotIn("Traceback", private_log)

    def test_manifest_file_read_log_is_sanitized_and_return_is_unchanged(
        self,
    ) -> None:
        source = self.root / "file-source"
        _write_manifest_source(
            source,
            [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
        )
        bridge_file = source / "PrefabSentinel.UnityEditorControlBridge.cs"
        sentinel = "PRIVATE_FILE_READ_SENTINEL"
        real_read_bytes = Path.read_bytes

        def fail_bridge_read(path: Path) -> bytes:
            if path == bridge_file:
                raise OSError(5, sentinel, str(path))
            return real_read_bytes(path)

        with (
            self.assertLogs("prefab_sentinel.bridge_deploy", level="WARNING")
            as captured,
            patch.object(
                Path,
                "read_bytes",
                autospec=True,
                side_effect=fail_bridge_read,
            ),
        ):
            result = build_bridge_manifest(source)

        assert_error_envelope(
            result,
            code="DEPLOY_SOURCE_NOT_FOUND",
            message_match=r"^Bridge source file could not be read\.$",
            data={},
        )
        private_log = "\n".join(captured.output)
        self.assertIn("phase=manifest_file_read", private_log)
        self.assertIn("error_type=OSError", private_log)
        self.assertIn("errno=5", private_log)
        self.assertNotIn(sentinel, private_log)
        self.assertNotIn(str(bridge_file), private_log)
        self.assertNotIn("Traceback", private_log)

    def test_manifest_directory_read_rejects_spoofed_builtin_type_name(
        self,
    ) -> None:
        source = self.root / "spoofed-directory-source"
        _write_manifest_source(
            source,
            [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
        )
        private_error_type = type(
            "PRIVATE_DIRECTORY_TYPE",
            (OSError,),
            {"__module__": "builtins"},
        )
        error = private_error_type(
            13,
            "PRIVATE_DIRECTORY_MESSAGE",
            str(source / "PRIVATE_DIRECTORY_PATH"),
        )

        with (
            self.assertLogs("prefab_sentinel.bridge_deploy", level="WARNING")
            as captured,
            patch.object(Path, "iterdir", side_effect=error),
        ):
            result = build_bridge_manifest(source)

        assert_error_envelope(
            result,
            code="DEPLOY_SOURCE_NOT_FOUND",
            message_match=r"^Bridge source directory could not be read\.$",
            data={},
        )
        self.assertEqual(1, len(captured.records))
        self.assertEqual(("OSError", 13), captured.records[0].args)
        private_log = captured.records[0].getMessage()
        self.assertIsInstance(result, ToolResponse)
        assert isinstance(result, ToolResponse)
        serialized = json.dumps(result.to_dict())
        for sentinel in (
            "PRIVATE_DIRECTORY_TYPE",
            "PRIVATE_DIRECTORY_MESSAGE",
            "PRIVATE_DIRECTORY_PATH",
        ):
            self.assertNotIn(sentinel, private_log)
            self.assertNotIn(sentinel, serialized)

    def test_manifest_file_read_rejects_spoofed_builtin_type_name(
        self,
    ) -> None:
        source = self.root / "spoofed-file-source"
        _write_manifest_source(
            source,
            [("PrefabSentinel.UnityEditorControlBridge.cs", _BRIDGE_BYTES)],
        )
        bridge_file = source / "PrefabSentinel.UnityEditorControlBridge.cs"
        private_error_type = type(
            "PRIVATE_FILE_TYPE",
            (OSError,),
            {"__module__": "builtins"},
        )
        error = private_error_type(
            5,
            "PRIVATE_FILE_MESSAGE",
            str(bridge_file / "PRIVATE_FILE_PATH"),
        )
        real_read_bytes = Path.read_bytes

        def fail_bridge_read(path: Path) -> bytes:
            if path == bridge_file:
                raise error
            return real_read_bytes(path)

        with (
            self.assertLogs("prefab_sentinel.bridge_deploy", level="WARNING")
            as captured,
            patch.object(
                Path,
                "read_bytes",
                autospec=True,
                side_effect=fail_bridge_read,
            ),
        ):
            result = build_bridge_manifest(source)

        assert_error_envelope(
            result,
            code="DEPLOY_SOURCE_NOT_FOUND",
            message_match=r"^Bridge source file could not be read\.$",
            data={},
        )
        self.assertEqual(1, len(captured.records))
        self.assertEqual(("OSError", 5), captured.records[0].args)
        private_log = captured.records[0].getMessage()
        self.assertIsInstance(result, ToolResponse)
        assert isinstance(result, ToolResponse)
        serialized = json.dumps(result.to_dict())
        for sentinel in (
            "PRIVATE_FILE_TYPE",
            "PRIVATE_FILE_MESSAGE",
            "PRIVATE_FILE_PATH",
        ):
            self.assertNotIn(sentinel, private_log)
            self.assertNotIn(sentinel, serialized)

    def _complete_after_first_verification_failure(
        self,
        verification_failure: ToolResponse,
        *,
        action_code: str,
        private_sentinel: str,
        action_target_complete: bool = True,
    ) -> tuple[ToolResponse, str]:
        prepared, original_record, promotion, fixture = (
            self._prepare_promoted_target()
        )
        old_manifest = fixture.old_manifest
        self.assertIsInstance(old_manifest, BridgeBundleManifest)
        if not isinstance(old_manifest, BridgeBundleManifest):
            raise AssertionError("expected previous Bridge manifest")
        self.assertEqual(prepared.previous_manifest, old_manifest)
        self.assertNotEqual(
            prepared.source_manifest.sha256,
            old_manifest.sha256,
        )

        promotion["code"] = action_code
        promotion["message"] = private_sentinel
        promotion_data = promotion["data"]
        self.assertIsInstance(promotion_data, dict)
        if not isinstance(promotion_data, dict):
            raise AssertionError("expected promotion data")
        promotion_data["target_complete"] = action_target_complete
        promotion_data["private_payload"] = private_sentinel
        real_verify = bridge_deploy.verify_deployed_target
        first_target_verification = True

        def fail_first_target_verification(
            path: Path,
            manifest: BridgeBundleManifest,
        ) -> ToolResponse:
            nonlocal first_target_verification
            if (
                first_target_verification
                and path == prepared.target_path
                and manifest == prepared.source_manifest
            ):
                first_target_verification = False
                return verification_failure
            return real_verify(path, manifest)

        def restore_previous_target(
            _manifest: BridgeBundleManifest,
        ) -> dict[str, object]:
            prepared.target_path.rename(prepared.backup_path)
            prepared.staging_path.rename(prepared.target_path)
            return {"code": "DEPLOY_OK"}

        with (
            self.assertLogs("prefab_sentinel.bridge_deploy", level="WARNING")
            as captured,
            patch(
                "prefab_sentinel.bridge_deploy.verify_deployed_target",
                side_effect=fail_first_target_verification,
            ),
        ):
            result = bridge_deploy.complete_bridge_deploy(
                prepared,
                promotion,
                recovery_action=restore_previous_target,
                lock_held=True,
            )

        assert_error_envelope(
            result,
            code="DEPLOY_FINAL_MANIFEST_MISMATCH",
            message_match=(
                r"^Bridge promoted bytes mismatched and the exact old target "
                r"was restored\.$"
            ),
        )
        self.assertEqual(
            (
                result.data["manifest_sha256"],
                result.data["bridge_version"],
                result.data["promotion_state"],
                result.data["barrier_used"],
                result.data["rollback_attempted"],
                result.data["rollback_restored"],
                result.data["backup_retained"],
                result.data["target_complete"],
                result.data["ownership_published"],
                result.data["transaction_retained"],
            ),
            (
                old_manifest.sha256,
                old_manifest.bridge_version,
                "rolled_back",
                True,
                True,
                True,
                False,
                True,
                True,
                False,
            ),
        )
        serialized = json.dumps(result.to_dict())
        self.assertNotIn(private_sentinel, serialized)
        record_path = (
            prepared.project_root
            / "Library/PrefabSentinel/deploy-ownership-v1.json"
        )
        self.assertEqual(record_path.read_bytes(), original_record)
        self.assertEqual(
            build_bridge_manifest(prepared.target_path),
            old_manifest,
        )
        self.assertFalse(prepared.transaction_path.exists())
        self.assertFalse(prepared.backup_path.exists())
        self.assertFalse(prepared.staging_path.exists())
        return result, "\n".join(captured.output)

    def test_pre_recovery_log_retains_builder_failure_and_first_action_state(
        self,
    ) -> None:
        sentinel = "PRIVATE_BUILDER_FAILURE_SENTINEL"
        _, private_log = self._complete_after_first_verification_failure(
            bridge_deploy._source_error("Bridge source file could not be read."),
            action_code="DEPLOY_REFRESH_FAILED",
            private_sentinel=sentinel,
        )

        self.assertIn("phase=post_promotion_verification", private_log)
        self.assertIn("verification_code=DEPLOY_SOURCE_NOT_FOUND", private_log)
        self.assertIn("action_code=DEPLOY_REFRESH_FAILED", private_log)
        self.assertIn("promotion_state=promoted", private_log)
        self.assertIn("target_complete=True", private_log)
        self.assertNotIn(sentinel, private_log)

    def test_pre_recovery_log_distinguishes_real_manifest_mismatch(
        self,
    ) -> None:
        sentinel = "PRIVATE_MANIFEST_MISMATCH_SENTINEL"
        _, private_log = self._complete_after_first_verification_failure(
            bridge_deploy._promotion_error(
                "DEPLOY_FINAL_MANIFEST_MISMATCH",
                "Bridge target does not match the prepared source manifest.",
            ),
            action_code="DEPLOY_OK",
            private_sentinel=sentinel,
        )

        self.assertIn("phase=post_promotion_verification", private_log)
        self.assertIn(
            "verification_code=DEPLOY_FINAL_MANIFEST_MISMATCH",
            private_log,
        )
        self.assertIn("action_code=DEPLOY_OK", private_log)
        self.assertIn("promotion_state=promoted", private_log)
        self.assertIn("target_complete=True", private_log)
        self.assertNotIn(sentinel, private_log)

    def test_pre_recovery_log_normalizes_untrusted_action_code(self) -> None:
        sentinel = "PRIVATE_ACTION_CODE_SENTINEL"
        _, private_log = self._complete_after_first_verification_failure(
            bridge_deploy._promotion_error(
                "DEPLOY_FINAL_MANIFEST_MISMATCH",
                "Bridge target does not match the prepared source manifest.",
            ),
            action_code=f"DEPLOY_OK_{sentinel}",
            private_sentinel=sentinel,
        )

        self.assertIn("action_code=DEPLOY_OTHER", private_log)
        self.assertNotIn(sentinel, private_log)

    def test_pre_recovery_log_survives_recovery_failure_without_stale_identity(
        self,
    ) -> None:
        sentinel = "PRIVATE_RECOVERY_FAILURE_SENTINEL"
        prepared, original_record, promotion, fixture = (
            self._prepare_promoted_target()
        )
        old_manifest = fixture.old_manifest
        self.assertIsInstance(old_manifest, BridgeBundleManifest)
        if not isinstance(old_manifest, BridgeBundleManifest):
            raise AssertionError("expected previous Bridge manifest")
        self.assertNotEqual(
            prepared.source_manifest.sha256,
            old_manifest.sha256,
        )
        promotion["message"] = sentinel
        promotion_data = promotion["data"]
        self.assertIsInstance(promotion_data, dict)
        if not isinstance(promotion_data, dict):
            raise AssertionError("expected promotion data")
        promotion_data["private_payload"] = sentinel

        real_verify = bridge_deploy.verify_deployed_target
        first_target_verification = True

        def fail_first_target_verification(
            path: Path,
            manifest: BridgeBundleManifest,
        ) -> ToolResponse:
            nonlocal first_target_verification
            if (
                first_target_verification
                and path == prepared.target_path
                and manifest == prepared.source_manifest
            ):
                first_target_verification = False
                return bridge_deploy._source_error(sentinel)
            return real_verify(path, manifest)

        def fail_recovery(
            _manifest: BridgeBundleManifest,
        ) -> dict[str, object]:
            return {
                "code": "DEPLOY_ROLLBACK_FAILED",
                "message": sentinel,
                "data": {"private_payload": sentinel},
            }

        with (
            self.assertLogs("prefab_sentinel.bridge_deploy", level="WARNING")
            as captured,
            patch(
                "prefab_sentinel.bridge_deploy.verify_deployed_target",
                side_effect=fail_first_target_verification,
            ),
        ):
            result = bridge_deploy.complete_bridge_deploy(
                prepared,
                promotion,
                recovery_action=fail_recovery,
                lock_held=True,
            )

        assert_error_envelope(
            result,
            code="DEPLOY_ROLLBACK_FAILED",
            severity="critical",
            message_match=(
                r"^Bridge promoted bytes mismatched and exact-old recovery "
                r"failed\.$"
            ),
        )
        self.assertEqual(
            (
                result.data["manifest_sha256"],
                result.data["bridge_version"],
                result.data["promotion_state"],
                result.data["barrier_used"],
                result.data["rollback_attempted"],
                result.data["rollback_restored"],
                result.data["backup_retained"],
                result.data["target_complete"],
                result.data["ownership_published"],
                result.data["transaction_retained"],
            ),
            (
                "",
                "",
                "rollback_failed",
                True,
                True,
                False,
                True,
                False,
                False,
                True,
            ),
        )
        private_log = "\n".join(captured.output)
        self.assertIn("phase=post_promotion_verification", private_log)
        self.assertIn("verification_code=DEPLOY_SOURCE_NOT_FOUND", private_log)
        self.assertIn("action_code=DEPLOY_OK", private_log)
        self.assertIn("promotion_state=promoted", private_log)
        self.assertIn("target_complete=True", private_log)
        self.assertNotIn(sentinel, private_log)
        serialized = json.dumps(result.to_dict())
        self.assertNotIn(sentinel, serialized)
        record_path = (
            prepared.project_root
            / "Library/PrefabSentinel/deploy-ownership-v1.json"
        )
        self.assertEqual(record_path.read_bytes(), original_record)
        self.assertEqual(
            build_bridge_manifest(prepared.backup_path),
            old_manifest,
        )
        self.assertEqual(
            build_bridge_manifest(prepared.target_path),
            prepared.source_manifest,
        )
        self.assertTrue(prepared.transaction_path.is_dir())
        self.assertTrue(prepared.backup_path.is_dir())
        self.assertFalse(prepared.staging_path.exists())

    def test_pre_recovery_log_reports_not_run_for_incomplete_first_action(
        self,
    ) -> None:
        sentinel = "PRIVATE_NOT_RUN_SENTINEL"
        _, private_log = self._complete_after_first_verification_failure(
            bridge_deploy._promotion_error(
                "DEPLOY_FINAL_MANIFEST_MISMATCH",
                "This verifier result must not be observed.",
            ),
            action_code="DEPLOY_REFRESH_FAILED",
            private_sentinel=sentinel,
            action_target_complete=False,
        )

        self.assertIn("phase=post_promotion_verification", private_log)
        self.assertIn("verification_code=NOT_RUN", private_log)
        self.assertIn("action_code=DEPLOY_REFRESH_FAILED", private_log)
        self.assertIn("promotion_state=promoted", private_log)
        self.assertIn("target_complete=False", private_log)
        self.assertNotIn("verification_code=DEPLOY_FINAL_MANIFEST_MISMATCH", private_log)
        self.assertNotIn(sentinel, private_log)

    def test_success_path_return_is_unchanged_and_emits_no_diagnostic(
        self,
    ) -> None:
        prepared, _, promotion, _ = self._prepare_promoted_target()

        with self.assertNoLogs(
            "prefab_sentinel.bridge_deploy",
            level="WARNING",
        ):
            result = bridge_deploy.complete_bridge_deploy(
                prepared,
                promotion,
                lock_held=True,
            )

        self.assertEqual(
            (result.success, result.severity.value, result.code, result.message),
            (
                True,
                "info",
                "DEPLOY_OK",
                "Bridge bundle was promoted and byte-verified.",
            ),
        )
        self.assertEqual(
            (
                result.data["promotion_state"],
                result.data["target_complete"],
                result.data["ownership_published"],
                result.data["transaction_retained"],
            ),
            ("promoted", True, True, False),
        )

    def test_ownership_target_mismatch_clears_stale_verified_identity(
        self,
    ) -> None:
        prepared, original_record, promotion, fixture = (
            self._prepare_promoted_target()
        )
        old_manifest = fixture.old_manifest
        self.assertIsInstance(old_manifest, BridgeBundleManifest)
        if not isinstance(old_manifest, BridgeBundleManifest):
            raise AssertionError("expected previous Bridge manifest")
        real_builder = bridge_deploy.build_bridge_manifest
        target_verifications = 0

        def mismatch_second_target_build(
            path: Path,
        ) -> BridgeBundleManifest | ToolResponse:
            nonlocal target_verifications
            if path == prepared.target_path:
                target_verifications += 1
                if target_verifications == 2:
                    return old_manifest
            return real_builder(path)

        with patch(
            "prefab_sentinel.bridge_deploy.build_bridge_manifest",
            side_effect=mismatch_second_target_build,
        ):
            result = bridge_deploy.complete_bridge_deploy(
                prepared,
                promotion,
                lock_held=True,
            )

        assert_error_envelope(
            result,
            code="DEPLOY_FINAL_MANIFEST_MISMATCH",
            message_match=(
                r"^Bridge target verification failed before ownership publication\.$"
            ),
        )
        self.assertEqual(target_verifications, 2)
        self._assert_ownership_failure_state(
            prepared,
            result,
            target_complete=False,
        )
        record_path = (
            prepared.project_root
            / "Library/PrefabSentinel/deploy-ownership-v1.json"
        )
        self.assertEqual(record_path.read_bytes(), original_record)
        self.assertTrue(prepared.transaction_path.is_dir())

    def test_ownership_write_failure_keeps_latest_verified_identity(
        self,
    ) -> None:
        prepared, original_record, promotion, _ = self._prepare_promoted_target()

        with patch(
            "prefab_sentinel.bridge_deploy._atomic_write_json",
            return_value=False,
        ):
            result = bridge_deploy.complete_bridge_deploy(
                prepared,
                promotion,
                lock_held=True,
            )

        assert_error_envelope(
            result,
            code="DEPLOY_OWNERSHIP_WRITE_FAILED",
            message_match=(
                r"^Bridge target was verified but ownership publication failed\.$"
            ),
        )
        self._assert_ownership_failure_state(
            prepared,
            result,
            target_complete=True,
        )
        record_path = (
            prepared.project_root
            / "Library/PrefabSentinel/deploy-ownership-v1.json"
        )
        self.assertEqual(record_path.read_bytes(), original_record)
        self.assertTrue(prepared.transaction_path.is_dir())
