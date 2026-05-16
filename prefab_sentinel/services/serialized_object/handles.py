"""Shared handle validators and SerializedObject constants.

Free functions that validate create-mode handle references — used by the
prefab-create, asset-create, and scene-ops validator modules.  Lifted out
of the monolithic ``service.py`` per issue #91 so each sibling can reuse
them without importing the service class.
"""

from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import Diagnostic

# ---------------------------------------------------------------------------
# Shared constants (moved out of service.py for cross-module use)
# ---------------------------------------------------------------------------

ASSET_HANDLE = "asset"
SCENE_HANDLE = "scene"
ROOT_HANDLE = "root"

ARRAY_DATA_SUFFIX = ".Array.data"
ARRAY_SIZE_SUFFIX = ".Array.size"

VALUE_OPS = frozenset({"set", "insert_array_element", "remove_array_element"})

PREFAB_CREATE_OPS = {
    "create_prefab",
    "create_root",
    "create_game_object",
    "rename_object",
    "reparent",
    "add_component",
    "find_component",
    "remove_component",
    "set",
    "insert_array_element",
    "remove_array_element",
    "save",
}


# ---------------------------------------------------------------------------
# Handle helpers
# ---------------------------------------------------------------------------


def normalize_handle_name(raw: object) -> str:
    """Normalize a handle reference to its bare name.

    Accepts both ``"$name"`` (the on-wire form) and ``"name"``; returns
    the stripped bare name, or ``""`` when the input is not a string or
    is blank.
    """
    if not isinstance(raw, str):
        return ""
    normalized = raw.strip()
    if normalized.startswith("$"):
        normalized = normalized[1:]
    return normalized.strip()


def validate_result_handle(
    *,
    target: str,
    index: int,
    op: dict[str, Any],
    known_handles: dict[str, str],
    diagnostics: list[Diagnostic],
) -> str | None:
    """Validate and return the ``result`` handle name on ``op``.

    Returns ``None`` if ``result`` is absent, empty, or duplicates an
    existing handle (in which case a ``schema_error`` diagnostic is
    appended).  The caller decides whether the op proceeds without the
    handle or aborts.
    """
    if "result" not in op:
        return None
    handle_name = normalize_handle_name(op.get("result"))
    if not handle_name:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}].result",
                detail="schema_error",
                evidence="result must be a non-empty string when provided",
            )
        )
        return None
    if handle_name in known_handles:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}].result",
                detail="schema_error",
                evidence=f"handle '{handle_name}' is already defined",
            )
        )
        return None
    return handle_name


def require_handle_ref(
    *,
    target: str,
    index: int,
    field: str,
    op: dict[str, Any],
    known_handles: dict[str, str],
    diagnostics: list[Diagnostic],
    expected_kind: str | set[str] | tuple[str, ...] | None = None,
) -> str | None:
    """Require ``op[field]`` to be a known handle of the expected kind.

    Emits a ``schema_error`` diagnostic and returns ``None`` on any of:

    * missing / empty / non-string reference,
    * unknown handle,
    * kind mismatch (when ``expected_kind`` is provided).

    Returns the bare handle name otherwise.
    """
    handle_name = normalize_handle_name(op.get(field))
    if not handle_name:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}].{field}",
                detail="schema_error",
                evidence=f"{field} must reference a handle",
            )
        )
        return None
    if handle_name not in known_handles:
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}].{field}",
                detail="schema_error",
                evidence=f"unknown handle '{handle_name}'",
            )
        )
        return None
    actual_kind = known_handles[handle_name]
    if expected_kind is not None:
        expected_kinds: set[str] | None = (
            {expected_kind}
            if isinstance(expected_kind, str)
            else set(expected_kind)
        )
    else:
        expected_kinds = None
    if expected_kinds is not None and actual_kind not in expected_kinds:
        if len(expected_kinds) == 1:
            expected_text = next(iter(expected_kinds)).replace("_", " ")
        else:
            expected_text = " or ".join(
                kind.replace("_", " ") for kind in sorted(expected_kinds)
            )
        diagnostics.append(
            Diagnostic(
                path=target,
                location=f"ops[{index}].{field}",
                detail="schema_error",
                evidence=(
                    f"handle '{handle_name}' must reference a {expected_text}"
                ),
            )
        )
        return None
    return handle_name


__all__ = [
    "ASSET_HANDLE",
    "SCENE_HANDLE",
    "ROOT_HANDLE",
    "ARRAY_DATA_SUFFIX",
    "ARRAY_SIZE_SUFFIX",
    "VALUE_OPS",
    "PREFAB_CREATE_OPS",
    "normalize_handle_name",
    "validate_result_handle",
    "require_handle_ref",
]
