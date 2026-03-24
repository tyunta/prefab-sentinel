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
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.symbol_tree import (
    AmbiguousSymbolError,
    SymbolKind,
    SymbolNode,
    SymbolNotFoundError,
    SymbolTree,
    build_script_name_map,
)
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_yaml_parser import CLASS_ID_MONOBEHAVIOUR
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

    def _build_symbol_tree(path: str) -> SymbolTree:
        """Read asset and build SymbolTree for name resolution."""
        text = _read_asset(path)
        return SymbolTree.build(text, path, _script_map(), include_properties=False)

    def _resolve_component_name(node: SymbolNode) -> str:
        """Map a component SymbolNode to the type name used by patch ops."""
        if node.class_id == CLASS_ID_MONOBEHAVIOUR:
            if not node.script_name:
                msg = (
                    f"MonoBehaviour at fileID={node.file_id} has no script name. "
                    f"Provide --project-root for script name resolution."
                )
                raise ValueError(msg)
            return node.script_name
        return node.name

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
        show_origin: bool = False,
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
            show_origin: Annotate properties with Variant chain origin
                (which Prefab set each value). Implies include_properties.
        """
        props = include_properties or show_origin
        text = _read_asset(path)
        tree = SymbolTree.build(
            text, path, _script_map(), include_properties=props
        )
        results = tree.query(symbol_path, depth=depth)
        if not results:
            return {
                "success": False,
                "message": f"No match for symbol path: {symbol_path!r}",
                "matches": [],
            }
        if show_origin:
            _annotate_origins(results, path)
        response: dict[str, Any] = {
            "success": True,
            "asset_path": path,
            "symbol_path": symbol_path,
            "matches": results,
        }
        if show_origin:
            response["show_origin"] = True
        return response

    # ------------------------------------------------------------------
    # Origin annotation helper
    # ------------------------------------------------------------------

    def _annotate_origins(matches: list[dict[str, Any]], asset_path: str) -> None:
        """Inject Variant chain origin info into property dicts in-place."""
        try:
            orch = _get_orchestrator()
            resp = orch.prefab_variant.resolve_chain_values_with_origin(asset_path)
        except Exception:
            return
        if not resp.success:
            return
        # Build lookup: (file_id, property_path) -> origin info
        origin_map: dict[tuple[str, str], dict[str, Any]] = {}
        for v in resp.data.get("values", []):
            key = (v["target_file_id"], v["property_path"])
            if key not in origin_map:
                origin_map[key] = {
                    "origin_path": v["origin_path"],
                    "origin_depth": v["origin_depth"],
                }
        # Annotate properties on matched nodes
        for match in matches:
            props = match.get("properties")
            file_id = match.get("file_id", "")
            if not props or not file_id:
                continue
            annotated: dict[str, Any] = {}
            for prop_name, prop_value in props.items():
                entry: dict[str, Any] = {"value": prop_value}
                origin = origin_map.get((file_id, prop_name))
                if origin:
                    entry["origin_path"] = origin["origin_path"]
                    entry["origin_depth"] = origin["origin_depth"]
                annotated[prop_name] = entry
            match["properties"] = annotated

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

    @server.tool()
    def diff_unity_symbols(
        path: str,
        component_filter: str | None = None,
    ) -> dict[str, Any]:
        """Show only the differences between a Variant and its Base.

        Returns overridden properties with both variant and base values,
        plus origin annotations showing which Prefab in the chain set each value.

        Args:
            path: Variant prefab file path.
            component_filter: Filter diffs by property path substring.
        """
        orch = _get_orchestrator()
        resp = orch.diff_variant(
            variant_path=path,
            component_filter=component_filter,
        )
        return resp.to_dict()

    # ------------------------------------------------------------------
    # Semantic editing tools
    # ------------------------------------------------------------------

    @server.tool()
    def set_property(
        path: str,
        symbol_path: str,
        property_path: str,
        value: Any,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Set a serialized field value on a component identified by symbol path.

        Two-phase workflow:
        - confirm=False (default): dry-run preview of changes.
        - confirm=True: applies changes to disk.

        Args:
            path: Asset file path (.prefab, .unity, .asset, .mat).
            symbol_path: Human-readable path to a component
                (e.g. "CharacterBody/MeshRenderer" or
                "CharacterBody/MonoBehaviour(PlayerScript)").
            property_path: Serialized property path (e.g. "m_Speed",
                "m_Materials.Array.data[0]").
            value: New value to set (string, number, or object reference dict).
            confirm: Set True to apply changes (False = dry-run only).
            change_reason: Human-readable reason for the change (audit trail).
        """
        # 1. Symbol resolution
        tree = _build_symbol_tree(path)
        try:
            node = tree.resolve_unique(symbol_path)
        except SymbolNotFoundError:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No component found at symbol path: {symbol_path!r}",
                "data": {"asset_path": path, "symbol_path": symbol_path},
                "diagnostics": [],
            }
        except AmbiguousSymbolError as exc:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_AMBIGUOUS",
                "message": str(exc),
                "data": {"asset_path": path, "symbol_path": symbol_path},
                "diagnostics": [],
            }

        # 2. Must be a component
        if node.kind != SymbolKind.COMPONENT:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_COMPONENT",
                "message": (
                    f"Symbol path {symbol_path!r} resolves to a {node.kind.value}, "
                    f"not a component. Provide a path to a component."
                ),
                "data": {
                    "asset_path": path,
                    "symbol_path": symbol_path,
                    "resolved_kind": node.kind.value,
                },
                "diagnostics": [],
            }

        # 3. Resolve component type name
        try:
            component_name = _resolve_component_name(node)
        except ValueError as exc:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_UNRESOLVABLE",
                "message": str(exc),
                "data": {"asset_path": path, "symbol_path": symbol_path},
                "diagnostics": [],
            }

        # 4. Build V2 patch plan
        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": path, "mode": "open"}],
            "ops": [
                {
                    "resource": "target",
                    "op": "set",
                    "component": component_name,
                    "path": property_path,
                    "value": value,
                },
            ],
        }

        # 5. Execute via orchestrator
        orch = _get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=(not confirm),
            confirm=confirm,
            change_reason=change_reason or None,
        )

        # 6. Enrich response with symbol resolution metadata
        result = resp.to_dict()
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
            "property_path": property_path,
        }
        return result

    @server.tool()
    def list_serialized_fields(
        script_path_or_guid: str,
    ) -> dict[str, Any]:
        """List serialized C# fields for a Unity script.

        Parses the C# source to extract fields that Unity will serialize,
        enabling field coverage checks and rename impact analysis.

        Args:
            script_path_or_guid: .cs file path or 32-char GUID string.
        """
        orch = _get_orchestrator()
        resp = orch.list_serialized_fields(script_path_or_guid=script_path_or_guid)
        return resp.to_dict()

    @server.tool()
    def validate_field_rename(
        script_path_or_guid: str,
        old_name: str,
        new_name: str,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """Analyze the impact of renaming a serialized C# field (read-only).

        Scans YAML assets for MonoBehaviours using the script and reports
        which assets reference the field. Does NOT apply any changes.

        Args:
            script_path_or_guid: .cs file path or 32-char GUID string.
            old_name: Current field name to rename.
            new_name: Proposed new field name.
            scope: Directory to restrict impact search (default: project root).
        """
        orch = _get_orchestrator()
        resp = orch.validate_field_rename(
            script_path_or_guid=script_path_or_guid,
            old_name=old_name,
            new_name=new_name,
            scope=scope,
        )
        return resp.to_dict()

    @server.tool()
    def check_field_coverage(
        scope: str,
    ) -> dict[str, Any]:
        """Detect unused C# fields or orphaned YAML propertyPaths in scope.

        Compares serialized C# field definitions against YAML MonoBehaviour
        data to find mismatches: fields defined in code but absent in assets
        (unused), or fields present in assets but absent in code (orphaned).

        Args:
            scope: Directory or file path to scan.
        """
        orch = _get_orchestrator()
        resp = orch.check_field_coverage(scope=scope)
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
