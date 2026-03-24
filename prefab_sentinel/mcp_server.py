"""MCP server for Prefab Sentinel.

Exposes Unity asset inspection tools via the Model Context Protocol,
enabling AI agents to address Unity objects by human-readable names.

Requires the ``mcp`` optional dependency::

    pip install prefab-sentinel[mcp]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise ImportError(
        "MCP server requires the 'mcp' extra: "
        "pip install prefab-sentinel[mcp]"
    ) from exc

from prefab_sentinel.orchestrator import Phase1Orchestrator
from prefab_sentinel.symbol_tree import SymbolTree, build_script_name_map
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.wsl_compat import to_wsl_path

__all__ = ["create_server"]


def create_server(
    project_root: str | Path | None = None,
) -> FastMCP:
    """Create and configure the Prefab Sentinel MCP server.

    Args:
        project_root: Unity project root. Auto-detected when ``None``.

    Returns:
        A configured ``FastMCP`` server instance ready to run.
    """
    server = FastMCP(
        name="prefab-sentinel",
        instructions=(
            "Unity asset inspection and editing tools. "
            "Use get_unity_symbols to explore asset structure, "
            "find_unity_symbol to locate specific objects by name, "
            "and validate_refs to check for broken references."
        ),
    )

    _root = Path(project_root) if project_root else None

    def _get_orchestrator() -> Phase1Orchestrator:
        return Phase1Orchestrator.default(project_root=_root)

    def _read_asset(path: str) -> str:
        """Read a Unity asset file, returning its text content."""
        resolved = Path(to_wsl_path(path))
        if not resolved.is_file():
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        text = decode_text_file(resolved)
        if text is None:
            msg = f"Unable to decode file: {path}"
            raise ValueError(msg)
        return text

    def _script_map() -> dict[str, str]:
        if _root:
            return build_script_name_map(_root)
        return {}

    # ------------------------------------------------------------------
    # Symbol model tools
    # ------------------------------------------------------------------

    @server.tool()
    def get_unity_symbols(
        path: str,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Get the symbol tree (GameObject/Component hierarchy) of a Unity asset.

        Args:
            path: Asset file path (.prefab, .unity, .asset).
            depth: Expansion depth. 0=root GOs only, 1=GOs+components,
                   2=components+properties.
        """
        text = _read_asset(path)
        include_props = depth >= 2
        tree = SymbolTree.build(
            text, path, _script_map(), include_properties=include_props
        )
        return {
            "success": True,
            "asset_path": path,
            "depth": depth,
            "symbols": tree.to_overview(depth=depth),
        }

    @server.tool()
    def find_unity_symbol(
        path: str,
        symbol_path: str,
        depth: int = 0,
        include_properties: bool = False,
    ) -> dict[str, Any]:
        """Find a Unity object by its human-readable symbol path.

        Symbol path examples:
        - "CharacterBody" — a GameObject
        - "CharacterBody/MeshRenderer" — a component
        - "CharacterBody/MonoBehaviour(PlayerScript)" — a script component
        - "CharacterBody/MonoBehaviour(PlayerScript)/moveSpeed" — a field

        Args:
            path: Asset file path.
            symbol_path: Human-readable path to the target object.
            depth: How deep to expand below the matched node.
            include_properties: Include serialized field values.
        """
        text = _read_asset(path)
        tree = SymbolTree.build(
            text, path, _script_map(), include_properties=include_properties
        )
        results = tree.query(symbol_path, depth=depth)
        if not results:
            return {
                "success": False,
                "message": f"No match for symbol path: {symbol_path!r}",
                "matches": [],
            }
        return {
            "success": True,
            "asset_path": path,
            "symbol_path": symbol_path,
            "matches": results,
        }

    # ------------------------------------------------------------------
    # Orchestrator-backed tools
    # ------------------------------------------------------------------

    @server.tool()
    def find_referencing_assets(
        asset_or_guid: str,
        scope: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Find all assets that reference a given asset path or GUID.

        Args:
            asset_or_guid: Asset path or 32-char GUID to search for.
            scope: Directory to restrict search scope.
            max_results: Maximum number of results to return.
        """
        orch = _get_orchestrator()
        resp = orch.inspect_where_used(
            asset_or_guid=asset_or_guid,
            scope=scope,
            max_usages=max_results,
        )
        return resp.to_dict()

    @server.tool()
    def validate_refs(
        scope: str,
        details: bool = False,
        max_diagnostics: int = 200,
    ) -> dict[str, Any]:
        """Scan for broken GUID/fileID references in a Unity project scope.

        Args:
            scope: Directory or file path to scan.
            details: Include per-reference diagnostics.
            max_diagnostics: Cap on the number of diagnostics returned.
        """
        orch = _get_orchestrator()
        resp = orch.validate_refs(
            scope=scope,
            details=details,
            max_diagnostics=max_diagnostics,
        )
        return resp.to_dict()

    @server.tool()
    def inspect_wiring(
        path: str,
        udon_only: bool = False,
    ) -> dict[str, Any]:
        """Analyze MonoBehaviour field wiring in a Prefab or Scene.

        Args:
            path: Asset file path (.prefab, .unity).
            udon_only: Only inspect UdonSharp components.
        """
        orch = _get_orchestrator()
        resp = orch.inspect_wiring(target_path=path, udon_only=udon_only)
        return resp.to_dict()

    @server.tool()
    def inspect_variant(
        path: str,
        component_filter: str | None = None,
        show_origin: bool = False,
    ) -> dict[str, Any]:
        """Inspect a Prefab Variant's override chain and effective values.

        Args:
            path: Variant prefab file path.
            component_filter: Filter overrides by component substring.
            show_origin: Show which Prefab in the chain set each value.
        """
        orch = _get_orchestrator()
        resp = orch.inspect_variant(
            variant_path=path,
            component_filter=component_filter,
            show_origin=show_origin,
        )
        return resp.to_dict()

    return server


def main() -> None:
    """Entry point for the MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="Prefab Sentinel MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Unity project root directory (auto-detected if omitted)",
    )
    args = parser.parse_args()

    server = create_server(project_root=args.project_root)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
