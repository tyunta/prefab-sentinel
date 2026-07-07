from __future__ import annotations

from typing import Any

from prefab_sentinel.contracts import Diagnostic, Severity
from prefab_sentinel.effective_hierarchy import EffectiveHierarchyNode

from .models import _UDON_PROXY_WARNING, _Listener
from .overrides import _listener_origin
from .selectors import _component_lookup_key


def _listener_entry(
    listener: _Listener,
    node: EffectiveHierarchyNode,
    index: int,
    source_listener_count: int,
    component_index: dict[str, dict[str, str]],
    diagnostics: list[Diagnostic],
    asset_path: str,
    symbol_path: str,
) -> dict[str, Any]:
    target_key = _component_lookup_key(node, listener.target_file_id)
    target = component_index.get(target_key, {})
    if not target and listener.target_file_id not in ("", "0"):
        diagnostics.append(
            Diagnostic(
                path=asset_path,
                location=symbol_path,
                detail="INSPECT_UNITY_EVENT_TARGET_UNRESOLVED",
                evidence=(
                    "UnityEvent listener target component could not be resolved "
                    "within the selected effective prefab instance."
                ),
                severity=Severity.WARNING.value,
            )
        )
    _append_udonsharp_diagnostic(
        listener,
        target,
        diagnostics,
        asset_path,
        symbol_path,
    )
    return {
        "index": index,
        "target_object_path": target.get("symbol_path", ""),
        "target_component_type": target.get("component_type", ""),
        "method": listener.method,
        "persistent_listener_mode": listener.mode,
        "argument": listener.argument,
        "call_state": listener.call_state,
        "origin": _listener_origin(node, source_listener_count, listener),
    }

def _append_udonsharp_diagnostic(
    listener: _Listener,
    target: dict[str, str],
    diagnostics: list[Diagnostic],
    asset_path: str,
    symbol_path: str,
) -> None:
    if target.get("is_udon_proxy") != "true":
        return
    if target.get("has_backing_udon_behaviour") != "true":
        return
    if listener.method == "SendCustomEvent":
        return
    diagnostics.append(
        Diagnostic(
            path=asset_path,
            location=symbol_path,
            detail=_UDON_PROXY_WARNING,
            evidence=(
                "UnityEvent targets an UdonSharp proxy method while the backing "
                "UdonBehaviour is present; wire SendCustomEvent on the backing "
                "UdonBehaviour for persistent listener dispatch."
            ),
            severity=Severity.WARNING.value,
        )
    )
