"""Shared live Editor and Bridge blocker classification."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from prefab_sentinel.wsl_compat import to_wsl_path

WATCH_DIR = "watch_dir"
BRIDGE_CONNECTION = "bridge_connection"
COMPILE_OR_BUILD = "compile_or_build"
PLAYMODE_TRANSITION = "playmode_transition"
PREFAB_STAGE_FOR_SCENE_BOUND_OPERATION = "prefab_stage_for_scene_bound_operation"
DIRTY_OR_SAVE_BLOCKER = "dirty_or_save_blocker"

_WATCH_DIR_CODES = {
    "EDITOR_BRIDGE_WATCH_DIR_MISSING",
    "EDITOR_BRIDGE_WATCH_DIR_NOT_FOUND",
}
_BRIDGE_CONNECTION_CODES = {
    "EDITOR_BRIDGE_TIMEOUT",
}
_DIRTY_KEYS = (
    "dirty_scene_paths",
    "dirty_prefab_paths",
    "dirty_material_paths",
    "dirty_asset_paths",
)


def _string_value(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    return value if isinstance(value, str) else ""


def _bool_value(mapping: Mapping[str, Any], key: str) -> bool:
    return mapping.get(key) is True


def _has_dirty_identity(editor_state: Mapping[str, Any]) -> bool:
    if _bool_value(editor_state, "has_unsaved_changes"):
        return True
    if _bool_value(editor_state, "prefab_stage_is_dirty"):
        return True
    for key in _DIRTY_KEYS:
        value = editor_state.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _state_source(editor_state: Mapping[str, Any] | None, default: str) -> str:
    if editor_state is None:
        return default
    source = editor_state.get("state_source")
    return source if isinstance(source, str) and source else default


def _blocker(
    blocker_class: str,
    *,
    state_source: str,
    message: str,
    suggested_next_action: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "blocker_class": blocker_class,
        "state_source": state_source,
        "message": message,
        "suggested_next_action": suggested_next_action,
    }
    if evidence:
        record["evidence"] = dict(evidence)
    return record


def _watch_dir_status_blocker(
    status: Mapping[str, Any],
    bridge: Mapping[str, Any],
) -> dict[str, Any] | None:
    configured_watch_dir = _string_value(status, "configured_watch_dir")
    reported_watch_dir = _string_value(bridge, "watch_dir")
    if configured_watch_dir and reported_watch_dir and configured_watch_dir != reported_watch_dir:
        return _blocker(
            WATCH_DIR,
            state_source="bridge_transport",
            message="Configured watch directory differs from the Bridge-reported watch directory.",
            suggested_next_action="Use the same watch directory for Codex and the Unity Editor Bridge.",
            evidence={
                "configured_watch_dir": configured_watch_dir,
                "bridge_watch_dir": reported_watch_dir,
            },
        )
    if configured_watch_dir:
        try:
            configured_watch_dir_exists = Path(to_wsl_path(configured_watch_dir)).is_dir()
        except OSError as exc:
            return _blocker(
                WATCH_DIR,
                state_source="bridge_transport",
                message="Configured Editor Bridge watch directory status could not be read.",
                suggested_next_action="Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory.",
                evidence={
                    "configured_watch_dir": configured_watch_dir,
                    "error": str(exc),
                },
            )
        if not configured_watch_dir_exists:
            return _blocker(
                WATCH_DIR,
                state_source="bridge_transport",
                message="Configured Editor Bridge watch directory is not an existing directory.",
                suggested_next_action="Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory.",
                evidence={"configured_watch_dir": configured_watch_dir},
            )
    if not reported_watch_dir and bridge.get("connected") is not True:
        return _blocker(
            WATCH_DIR,
            state_source="bridge_transport",
            message="Editor Bridge watch directory is not configured or unavailable.",
            suggested_next_action="Set UNITYTOOL_BRIDGE_WATCH_DIR to the active Editor Bridge watch directory.",
        )
    return None


def _bridge_connection_status_blocker(
    bridge: Mapping[str, Any],
) -> dict[str, Any] | None:
    if bridge.get("connected") is False and _string_value(bridge, "watch_dir"):
        return _blocker(
            BRIDGE_CONNECTION,
            state_source="bridge_transport",
            message="Editor Bridge watch directory is present but no bridge response is available.",
            suggested_next_action="Confirm Unity is running with the PrefabSentinel Editor Bridge enabled.",
        )
    return None


def _compile_or_build_blocker(
    editor_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _bool_value(editor_state, "is_compiling") or _bool_value(editor_state, "is_building_player"):
        return _blocker(
            COMPILE_OR_BUILD,
            state_source=_state_source(editor_state, "live_editor"),
            message="Unity is compiling scripts or building a player.",
            suggested_next_action="Wait for Unity compile or build activity to finish, then retry the tool.",
        )
    return None


def _playmode_transition_blocker(
    editor_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _bool_value(editor_state, "is_will_change_playmode"):
        return _blocker(
            PLAYMODE_TRANSITION,
            state_source=_state_source(editor_state, "live_editor"),
            message="Unity is entering or exiting Play Mode.",
            suggested_next_action="Wait for the Play Mode transition to complete, then retry the tool.",
        )
    return None


def _prefab_stage_blocker(
    editor_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    if (
        _string_value(editor_state, "active_stage_kind") == "prefab_stage"
        or _string_value(editor_state, "prefab_stage_asset_path")
    ):
        return _blocker(
            PREFAB_STAGE_FOR_SCENE_BOUND_OPERATION,
            state_source=_state_source(editor_state, "live_editor"),
            message="A Prefab Stage is active and can block scene-bound operations.",
            suggested_next_action="Close the active Prefab Stage before running scene-bound Editor operations.",
            evidence={
                key: editor_state[key]
                for key in ("prefab_stage_asset_path", "prefab_stage_root_name")
                if key in editor_state and editor_state[key] not in (None, "")
            },
        )
    return None


def _dirty_blocker(
    editor_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _has_dirty_identity(editor_state):
        return _blocker(
            DIRTY_OR_SAVE_BLOCKER,
            state_source=_state_source(editor_state, "live_editor"),
            message="Unity has dirty scenes, prefabs, materials, or assets.",
            suggested_next_action="Save or intentionally discard dirty Unity state before relying on saved YAML.",
        )
    return None


def classify_status_blockers(
    status: Mapping[str, Any],
    bridge: Mapping[str, Any],
    editor_state: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    watch_dir_blocker = _watch_dir_status_blocker(status, bridge)
    if watch_dir_blocker is not None:
        blockers.append(watch_dir_blocker)
    else:
        bridge_blocker = _bridge_connection_status_blocker(bridge)
        if bridge_blocker is not None:
            blockers.append(bridge_blocker)
    if editor_state is not None:
        for classify in (
            _compile_or_build_blocker,
            _playmode_transition_blocker,
            _prefab_stage_blocker,
            _dirty_blocker,
        ):
            blocker = classify(editor_state)
            if blocker is not None:
                blockers.append(blocker)
    return blockers


def _tool_error_code_blocker(
    error: Mapping[str, Any],
) -> dict[str, Any] | None:
    code = _string_value(error, "code")
    if code == "EDITOR_BRIDGE_WRITE" or code in _WATCH_DIR_CODES:
        return _blocker(
            WATCH_DIR,
            state_source="bridge_transport",
            message="Editor Bridge watch directory is missing, invalid, or not writable.",
            suggested_next_action="Set UNITYTOOL_BRIDGE_WATCH_DIR to an existing Editor Bridge watch directory.",
        )
    if code in _BRIDGE_CONNECTION_CODES:
        return _blocker(
            BRIDGE_CONNECTION,
            state_source="bridge_transport",
            message="Editor Bridge did not return a response before the transport timeout.",
            suggested_next_action="Confirm Unity is running and the PrefabSentinel Editor Bridge watcher is active.",
        )
    return None


def classify_tool_error_blocker(
    error: Mapping[str, Any],
    editor_state: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    code_blocker = _tool_error_code_blocker(error)
    if code_blocker is not None:
        return code_blocker
    if editor_state is None:
        return None
    for classify in (
        _compile_or_build_blocker,
        _playmode_transition_blocker,
        _prefab_stage_blocker,
        _dirty_blocker,
    ):
        blocker = classify(editor_state)
        if blocker is not None:
            return blocker
    return None
