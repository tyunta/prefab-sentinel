"""MCP tools for Unity symbol tree inspection."""

from __future__ import annotations

import logging
from typing import Any, Literal

from mcp.server import MCPServer

from prefab_sentinel.editor_bridge import bridge_status, send_action
from prefab_sentinel.mcp_helpers import read_asset
from prefab_sentinel.session import ProjectSession

__all__ = ["register_symbol_tools"]

logger = logging.getLogger(__name__)

# Issue #40: marker attached to offline symbol-reference payloads when
# the Editor Bridge is connected and reports unsaved live changes. The
# offline symbol tree is built from last-saved disk YAML, so a connected
# Editor with unsaved edits means the payload may diverge from live
# state. With no Bridge connection no marker is attached, preserving the
# offline path's no-Unity-required property.
_FRESHNESS_MARKER = {
    "source": "last_saved_disk",
    "note": (
        "The Editor Bridge is connected and reports unsaved live "
        "changes. This symbol tree reflects the last-saved disk YAML "
        "and may diverge from the live editor state. Save the scene or "
        "Prefab Stage in Unity to reconcile."
    ),
}


def _offline_freshness_marker() -> dict[str, Any] | None:
    """Return the issue #40 freshness marker, or ``None``.

    The marker is attached only when the Editor Bridge is connected
    *and* its editor-state snapshot reports unsaved changes. A
    disconnected bridge, a failed bridge action, or a clean editor
    yields ``None`` so the marker never becomes a constant false alarm.
    """
    if not bridge_status().get("connected"):
        return None
    try:
        resp = send_action(action="get_editor_state")
    except Exception:
        logger.debug("get_editor_state failed for freshness marker", exc_info=True)
        return None
    if not resp.get("success"):
        return None
    editor_state = resp.get("data", {}).get("editor_state") or {}
    if editor_state.get("has_unsaved_changes"):
        return dict(_FRESHNESS_MARKER)
    return None


def register_symbol_tools(server: MCPServer, session: ProjectSession) -> None:
    """Register symbol tree inspection tools on *server*."""

    def _annotate_origins(
        matches: list[dict[str, Any]], asset_path: str,
    ) -> list[dict[str, Any]]:
        """Return a copy of *matches* with Variant chain origin info injected."""
        try:
            orch = session.get_orchestrator()
            resp = orch.prefab_variant.resolve_chain_values_with_origin(asset_path)
        except Exception:
            logger.debug(
                "Origin annotation failed for %s", asset_path, exc_info=True,
            )
            return matches
        if not resp.success:
            return matches
        origin_map: dict[tuple[str, str], dict[str, Any]] = {}
        for v in resp.data.get("values", []):
            key = (v["target_file_id"], v["property_path"])
            if key not in origin_map:
                origin_map[key] = {
                    "origin_path": v["origin_path"],
                    "origin_depth": v["origin_depth"],
                }

        def annotate_node(node: dict[str, Any]) -> dict[str, Any]:
            updated = node
            props = node.get("properties")
            file_id = node.get("file_id", "")
            if props and file_id:
                annotated: dict[str, Any] = {}
                for prop_name, prop_value in props.items():
                    entry: dict[str, Any] = {"value": prop_value}
                    origin = origin_map.get((file_id, prop_name))
                    if origin:
                        entry["origin_path"] = origin["origin_path"]
                        entry["origin_depth"] = origin["origin_depth"]
                    annotated[prop_name] = entry
                updated = {**node, "properties": annotated}

            children = node.get("children")
            if not isinstance(children, list):
                return updated
            annotated_children = [
                annotate_node(child) if isinstance(child, dict) else child
                for child in children
            ]
            if updated is node:
                updated = {**node}
            updated["children"] = annotated_children
            return updated

        return [annotate_node(match) for match in matches]

    @server.tool()
    def get_unity_symbols(
        asset_path: str,
        depth: int | None = None,
        detail: Literal["summary", "fields", "full"] = "full",
        expand_nested: bool = False,
    ) -> dict[str, Any]:
        """Get the symbol tree (GameObject/Component hierarchy) of a Unity asset.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            depth: Max child levels to include. None=full tree, 0=root GOs only.
            detail: Information richness per node. "summary"=kind+name,
                    "fields"=+field name list, "full"=all info.
            expand_nested: Expand Nested Prefab instances into the tree.
        """
        text, resolved = read_asset(asset_path, session.project_root)
        include_props = detail != "summary"
        guid_to_asset_path = None
        if expand_nested and session.project_root:
            guid_to_asset_path = session.guid_index()
        tree = session.get_symbol_tree(
            resolved,
            text,
            include_properties=include_props,
            expand_nested=expand_nested,
            guid_to_asset_path=guid_to_asset_path,
        )
        payload: dict[str, Any] = {
            "asset_path": asset_path,
            "depth": depth,
            "detail": detail,
            "symbols": tree.to_overview(depth=depth, detail=detail),
        }
        marker = _offline_freshness_marker()
        if marker is not None:
            payload["freshness"] = marker
        return payload

    @server.tool()
    def find_unity_symbol(
        asset_path: str,
        symbol_path: str,
        depth: int = 0,
        include_fields: bool = False,
        show_origin: bool = False,
        expand_nested: bool = False,
    ) -> dict[str, Any]:
        """Find a Unity object by its human-readable symbol path.

        Symbol path examples:
        - "CharacterBody" — a GameObject
        - "CharacterBody/MeshRenderer" — a component
        - "CharacterBody/MonoBehaviour(PlayerScript)" — a script component
        - "CharacterBody/MonoBehaviour(PlayerScript)/moveSpeed" — a field

        Args:
            asset_path: Asset file path.
            symbol_path: Human-readable path to the target object.
            depth: How deep to expand below the matched node.
            include_fields: Include all field values for matched symbols.
            show_origin: Annotate properties with Variant chain origin
                (which Prefab set each value). Implies include_fields.
            expand_nested: Resolve against expanded Nested Prefab symbols.
        """
        fields = include_fields or show_origin
        text, resolved = read_asset(asset_path, session.project_root)
        guid_to_asset_path = None
        if expand_nested and session.project_root:
            guid_to_asset_path = session.guid_index()
        tree = session.get_symbol_tree(
            resolved,
            text,
            include_properties=fields,
            expand_nested=expand_nested,
            guid_to_asset_path=guid_to_asset_path,
        )
        results = tree.query(symbol_path, depth=depth)
        if results and show_origin:
            results = _annotate_origins(results, asset_path)
        response: dict[str, Any] = {
            "asset_path": asset_path,
            "symbol_path": symbol_path,
            "matches": results,
        }
        if expand_nested:
            response["expand_nested"] = True
        if show_origin:
            response["show_origin"] = True
        marker = _offline_freshness_marker()
        if marker is not None:
            response["freshness"] = marker
        return response
