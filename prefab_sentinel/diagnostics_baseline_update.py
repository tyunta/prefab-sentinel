from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from prefab_sentinel.contracts import ToolResponse, error_response, success_response
from prefab_sentinel.diagnostics_baseline import DiagnosticsBaseline

_BASELINE_UPDATE_MODES = ("preview", "write")


def _classification_keys(
    classification: Mapping[str, object],
) -> tuple[set[str], set[str]] | None:
    raw_new = classification.get("new")
    raw_resolved = classification.get("resolved")
    if not isinstance(raw_new, list) or not isinstance(raw_resolved, list):
        return None
    new_keys = _record_keys(raw_new)
    resolved_keys = _record_keys(raw_resolved)
    if new_keys is None or resolved_keys is None:
        return None
    return set(new_keys), set(resolved_keys)


def _record_keys(records: Sequence[object]) -> tuple[str, ...] | None:
    keys: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            return None
        key = record.get("key")
        if not isinstance(key, str) or not key:
            return None
        keys.append(key)
    return tuple(keys)


def _baseline_update_data(
    *,
    baseline: DiagnosticsBaseline,
    next_known: list[str],
    added: list[str],
    pruned: list[str],
    mode: str,
    sample_limit: int,
) -> dict[str, Any]:
    return {
        "path": baseline.path,
        "mode": mode,
        "baseline_status": baseline.status,
        "written": False,
        "would_create": baseline.status == "absent",
        "known_count_before": len(baseline.known_diagnostics),
        "known_count_after": len(next_known),
        "added_count": len(added),
        "pruned_count": len(pruned),
        "added_sample": added[:sample_limit],
        "pruned_sample": pruned[:sample_limit],
        "known_diagnostics": next_known,
    }


def _diagnostics_baseline_write_failed(path: Path) -> ToolResponse:
    return error_response(
        "DIAGNOSTICS_BASELINE_WRITE_FAILED",
        "diagnostics baseline write failed.",
        data={"path": str(path), "read_only": False},
    )


def write_diagnostics_baseline(
    project_root: str | Path,
    known_diagnostics: Sequence[str],
) -> ToolResponse | None:
    import os
    import secrets
    from contextlib import suppress

    from prefab_sentinel.diagnostics_baseline import open_diagnostics_baseline_parent_fd

    baseline_path, parent_fd, path_error = open_diagnostics_baseline_parent_fd(
        project_root,
        create_parent=True,
    )
    if path_error is not None:
        return path_error
    if parent_fd is None:
        return _diagnostics_baseline_write_failed(baseline_path)

    payload = {"version": 1, "known_diagnostics": list(known_diagnostics)}
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temp_name: str | None = None
    temp_fd: int | None = None
    try:
        for _attempt in range(100):
            candidate = f".{baseline_path.name}.{secrets.token_hex(8)}.tmp"
            try:
                temp_fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            temp_name = candidate
            break
        else:
            return _diagnostics_baseline_write_failed(baseline_path)
        if temp_fd is None:
            return _diagnostics_baseline_write_failed(baseline_path)

        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                temp_fd = None
                handle.write(text)
        finally:
            if temp_fd is not None:
                os.close(temp_fd)

        os.replace(
            temp_name,
            baseline_path.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    except OSError:
        if temp_name is not None:
            with suppress(OSError):
                os.unlink(temp_name, dir_fd=parent_fd)
        return _diagnostics_baseline_write_failed(baseline_path)
    finally:
        os.close(parent_fd)
    return None

def compute_diagnostics_baseline_update(
    *,
    baseline: DiagnosticsBaseline,
    classification: Mapping[str, object],
    mode: str = "preview",
    prune_resolved: bool = False,
    sample_limit: int = 20,
) -> ToolResponse:
    if mode not in _BASELINE_UPDATE_MODES:
        return error_response(
            "DIAGNOSTICS_BASELINE_MODE_INVALID",
            "diagnostics baseline update mode must be preview or write.",
            data={"mode": mode},
        )

    parsed_keys = _classification_keys(classification)
    if parsed_keys is None:
        return error_response(
            "DIAGNOSTICS_BASELINE_SOURCE_MISSING_CLASSIFICATION",
            "source response data.diagnostics_baseline is missing or malformed.",
            data={"field": "data.diagnostics_baseline"},
        )

    new_keys, resolved_keys = parsed_keys
    previous = set(baseline.known_diagnostics)
    next_known_set = previous | new_keys
    pruned = sorted(next_known_set & resolved_keys) if prune_resolved else []
    if prune_resolved:
        next_known_set -= resolved_keys
    next_known = sorted(next_known_set)
    added = sorted(new_keys - previous)
    return success_response(
        "DIAGNOSTICS_BASELINE_UPDATE_PREVIEW",
        "Diagnostics baseline update preview computed.",
        data=_baseline_update_data(
            baseline=baseline,
            next_known=next_known,
            added=added,
            pruned=pruned,
            mode=mode,
            sample_limit=sample_limit,
        ),
    )
