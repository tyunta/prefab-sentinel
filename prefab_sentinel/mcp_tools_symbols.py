"""MCP tools for Unity symbol tree inspection."""

from __future__ import annotations

import logging
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.mcp_helpers import read_asset
from prefab_sentinel.session import ProjectSession

__all__ = ["register_symbol_tools"]

logger = logging.getLogger(__name__)


def register_symbol_tools(server: FastMCP, session: ProjectSession) -> None:
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
        result: list[dict[str, Any]] = []
        for match in matches:
            props = match.get("properties")
            file_id = match.get("file_id", "")
            if not props or not file_id:
                result.append(match)
                continue
            annotated: dict[str, Any] = {}
            for prop_name, prop_value in props.items():
                entry: dict[str, Any] = {"value": prop_value}
                origin = origin_map.get((file_id, prop_name))
                if origin:
                    entry["origin_path"] = origin["origin_path"]
                    entry["origin_depth"] = origin["origin_depth"]
                annotated[prop_name] = entry
            result.append({**match, "properties": annotated})
        return result

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
        return {
            "asset_path": asset_path,
            "depth": depth,
            "detail": detail,
            "symbols": tree.to_overview(depth=depth, detail=detail),
        }

    @server.tool()
    def find_unity_symbol(
        asset_path: str,
        symbol_path: str,
        depth: int = 0,
        include_fields: bool = False,
        show_origin: bool = False,
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
        """
        fields = include_fields or show_origin
        text, resolved = read_asset(asset_path, session.project_root)
        tree = session.get_symbol_tree(
            resolved, text, include_properties=fields,
        )
        results = tree.query(symbol_path, depth=depth)
        if results and show_origin:
            results = _annotate_origins(results, asset_path)
        response: dict[str, Any] = {
            "asset_path": asset_path,
            "symbol_path": symbol_path,
            "matches": results,
        }
        if show_origin:
            response["show_origin"] = True
        return response
