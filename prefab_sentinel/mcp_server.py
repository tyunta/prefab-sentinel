"""MCP server for Prefab Sentinel.

Exposes Unity asset inspection tools via the Model Context Protocol,
enabling AI agents to address Unity objects by human-readable names.

Requires the ``mcp`` optional dependency::

    pip install prefab-sentinel[mcp]
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise ImportError(
        "MCP server requires the 'mcp' extra: "
        "pip install prefab-sentinel[mcp]"
    ) from exc

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.fuzzy_match import suggest_similar
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.patch_revert import revert_overrides as revert_overrides_impl
from prefab_sentinel.session import ProjectSession
from prefab_sentinel.symbol_tree import (
    AmbiguousSymbolError,
    SymbolKind,
    SymbolNode,
    SymbolNotFoundError,
    SymbolTree,
)
from prefab_sentinel.unity_assets import decode_text_file
from prefab_sentinel.unity_yaml_parser import CLASS_ID_MONOBEHAVIOUR
from prefab_sentinel.wsl_compat import to_wsl_path

__all__ = ["create_server"]

logger = logging.getLogger(__name__)


def create_server(
    project_root: str | Path | None = None,
) -> FastMCP:
    """Create and configure the Prefab Sentinel MCP server.

    Args:
        project_root: Unity project root. Auto-detected when ``None``.

    Returns:
        A configured ``FastMCP`` server instance ready to run.
    """
    _root = Path(project_root) if project_root else None
    session = ProjectSession(project_root=_root)

    @asynccontextmanager
    async def _lifespan(_app: FastMCP):  # type: ignore[type-arg]
        try:
            yield
        finally:
            await session.shutdown()

    server = FastMCP(
        name="prefab-sentinel",
        instructions=(
            "Unity asset inspection and editing tools. "
            "Use activate_project to set scope, "
            "get_unity_symbols to explore asset structure, "
            "find_unity_symbol to locate specific objects by name, "
            "and validate_refs to check for broken references."
        ),
        lifespan=_lifespan,
    )

    # ------------------------------------------------------------------
    # Helpers (closure-scoped, not exposed as tools)
    # ------------------------------------------------------------------

    def _read_asset(path: str) -> tuple[str, Path]:
        """Read a Unity asset file, returning (text, resolved_path)."""
        resolved = Path(to_wsl_path(path))
        if not resolved.is_file():
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)
        text = decode_text_file(resolved)
        if text is None:
            msg = f"Unable to decode file: {path}"
            raise ValueError(msg)
        return text, resolved

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

    def _collect_symbol_paths(tree: SymbolTree) -> list[str]:
        """Collect all symbol paths from a tree for suggestion purposes."""
        paths: list[str] = []

        def _walk(nodes: list[SymbolNode], prefix: str) -> None:
            for node in nodes:
                path = f"{prefix}/{node.name}" if prefix else node.name
                paths.append(path)
                _walk(node.children, path)

        _walk(tree.roots, "")
        return paths

    # ------------------------------------------------------------------
    # Session management tools
    # ------------------------------------------------------------------

    @server.tool()
    async def activate_project(
        scope: str,
    ) -> dict[str, Any]:
        """Set the project scope and warm caches for subsequent requests.

        Call this once at the start of a session to set the working scope.
        Subsequent tool calls will be faster due to cached GUID index and
        script name map.

        Args:
            scope: Path to the Assets subdirectory to work with
                (e.g. "Assets/Tyunta/SoulLinkerSystem").
        """
        result = await session.activate(scope)
        return {
            "success": True,
            "severity": "info",
            "code": "SESSION_ACTIVATED",
            "message": f"Project activated with scope: {scope}",
            "data": result,
            "diagnostics": [
                {
                    "message": (
                        f"Scope '{scope}' will be used as default for: "
                        "validate_refs, find_referencing_assets, "
                        "validate_field_rename, check_field_coverage."
                    ),
                    "severity": "info",
                },
            ],
        }

    @server.tool()
    def get_project_status() -> dict[str, Any]:
        """Show current session state: cached items, scope, project root.

        Use this to check whether caches are warm or if activate_project
        needs to be called.
        """
        return {
            "success": True,
            "severity": "info",
            "code": "SESSION_STATUS",
            "message": "Current session status",
            "data": session.status(),
            "diagnostics": [],
        }

    # ------------------------------------------------------------------
    # Symbol model tools
    # ------------------------------------------------------------------

    @server.tool()
    def get_unity_symbols(
        asset_path: str,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Get the symbol tree (GameObject/Component hierarchy) of a Unity asset.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            depth: Expansion depth. 0=root GOs only, 1=GOs+components,
                   2=components+properties.
        """
        text, resolved = _read_asset(asset_path)
        include_props = depth >= 2
        tree = session.get_symbol_tree(
            resolved, text, include_properties=include_props,
        )
        return {
            "asset_path": asset_path,
            "depth": depth,
            "symbols": tree.to_overview(depth=depth),
        }

    @server.tool()
    def find_unity_symbol(
        asset_path: str,
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
            asset_path: Asset file path.
            symbol_path: Human-readable path to the target object.
            depth: How deep to expand below the matched node.
            include_properties: Include serialized field values.
            show_origin: Annotate properties with Variant chain origin
                (which Prefab set each value). Implies include_properties.
        """
        props = include_properties or show_origin
        text, resolved = _read_asset(asset_path)
        tree = session.get_symbol_tree(
            resolved, text, include_properties=props,
        )
        results = tree.query(symbol_path, depth=depth)
        if results and show_origin:
            _annotate_origins(results, asset_path)
        response: dict[str, Any] = {
            "asset_path": asset_path,
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
            orch = session.get_orchestrator()
            resp = orch.prefab_variant.resolve_chain_values_with_origin(asset_path)
        except Exception:
            logger.debug(
                "Origin annotation failed for %s", asset_path, exc_info=True,
            )
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

        Returns a direct payload with matches array (not an envelope).

        Args:
            asset_or_guid: Asset path or 32-char GUID to search for.
            scope: Directory to restrict search scope.
            max_results: Maximum number of results to return.
        """
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope)
        step = orch.reference_resolver.where_used(
            asset_or_guid=asset_or_guid,
            scope=resolved_scope,
            max_usages=max_results,
        )
        if not step.success:
            from mcp.server.fastmcp.exceptions import ToolError
            raise ToolError(step.message)

        usages = step.data.get("usages", [])
        return {
            "matches": usages,
            "target": asset_or_guid,
            "metadata": {
                "total_count": step.data.get("usage_count", len(usages)),
                "truncated": step.data.get("truncated_usages", 0) > 0,
                "scope": str(resolved_scope) if resolved_scope else None,
            },
        }

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
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope) or scope
        resp = orch.validate_refs(
            scope=resolved_scope,
            details=details,
            max_diagnostics=max_diagnostics,
        )
        return resp.to_dict()

    @server.tool()
    def inspect_wiring(
        asset_path: str,
        udon_only: bool = False,
    ) -> dict[str, Any]:
        """Analyze MonoBehaviour field wiring in a Prefab or Scene.

        Args:
            asset_path: Asset file path (.prefab, .unity).
            udon_only: Only inspect UdonSharp components.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_wiring(target_path=asset_path, udon_only=udon_only)
        return resp.to_dict()

    @server.tool()
    def inspect_variant(
        asset_path: str,
        component_filter: str | None = None,
        show_origin: bool = False,
    ) -> dict[str, Any]:
        """Inspect a Prefab Variant's override chain and effective values.

        Args:
            asset_path: Variant prefab file path.
            component_filter: Filter overrides by component substring.
            show_origin: Show which Prefab in the chain set each value.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_variant(
            variant_path=asset_path,
            component_filter=component_filter,
            show_origin=show_origin,
        )
        return resp.to_dict()

    @server.tool()
    def diff_unity_symbols(
        asset_path: str,
        component_filter: str | None = None,
    ) -> dict[str, Any]:
        """Show only the differences between a Variant and its Base.

        Returns overridden properties with both variant and base values,
        plus origin annotations showing which Prefab in the chain set each value.

        Args:
            asset_path: Variant prefab file path.
            component_filter: Filter diffs by property path substring.
        """
        orch = session.get_orchestrator()
        resp = orch.diff_variant(
            variant_path=asset_path,
            component_filter=component_filter,
        )
        return resp.to_dict()

    # ------------------------------------------------------------------
    # Semantic editing tools
    # ------------------------------------------------------------------

    @server.tool()
    def set_property(
        asset_path: str,
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
            asset_path: Asset file path (.prefab, .unity, .asset, .mat).
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
        text, resolved = _read_asset(asset_path)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        try:
            node = tree.resolve_unique(symbol_path)
        except SymbolNotFoundError:
            suggestions = suggest_similar(
                symbol_path, _collect_symbol_paths(tree),
            )
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No component found at symbol path: {symbol_path!r}",
                "data": {
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "suggestions": suggestions,
                },
                "diagnostics": [],
            }
        except AmbiguousSymbolError as exc:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_AMBIGUOUS",
                "message": str(exc),
                "data": {"asset_path": asset_path, "symbol_path": symbol_path},
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
                    "asset_path": asset_path,
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
                "data": {"asset_path": asset_path, "symbol_path": symbol_path},
                "diagnostics": [],
            }

        # 4. Build V2 patch plan
        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
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
        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=(not confirm),
            confirm=confirm,
            change_reason=change_reason or None,
        )

        # 6. Invalidate SymbolTree cache after confirmed write
        auto_refresh = "skipped"
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            auto_refresh = orch.maybe_auto_refresh()

        # 7. Enrich response with symbol resolution metadata
        result = resp.to_dict()
        if confirm and resp.success:
            result["auto_refresh"] = auto_refresh
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
            "property_path": property_path,
        }
        return result

    @server.tool()
    def add_component(
        asset_path: str,
        symbol_path: str,
        component_type: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Add a component to an existing GameObject in an open-mode asset.

        Two-phase workflow:
        - confirm=False (default): dry-run preview.
        - confirm=True: applies the change.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            symbol_path: Symbol path to the target GameObject
                (e.g. "Player" for the root, "Player/Body" for a child).
            component_type: Unity component type to add
                (e.g. "AudioSource", "BoxCollider", "VRC.Udon.UdonBehaviour").
            confirm: Set True to apply (False = dry-run only).
            change_reason: Human-readable reason for the change (audit trail).
        """
        text, resolved = _read_asset(asset_path)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        try:
            node = tree.resolve_unique(symbol_path)
        except SymbolNotFoundError:
            suggestions = suggest_similar(
                symbol_path, _collect_symbol_paths(tree),
            )
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No game object found at symbol path: {symbol_path!r}",
                "data": {
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "suggestions": suggestions,
                },
                "diagnostics": [],
            }
        except AmbiguousSymbolError as exc:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_AMBIGUOUS",
                "message": str(exc),
                "data": {"asset_path": asset_path, "symbol_path": symbol_path},
                "diagnostics": [],
            }

        if node.kind != SymbolKind.GAME_OBJECT:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_GAME_OBJECT",
                "message": (
                    f"Symbol path {symbol_path!r} resolves to a {node.kind.value}, "
                    f"not a game_object. Provide a path to a GameObject."
                ),
                "data": {
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "resolved_kind": node.kind.value,
                },
                "diagnostics": [],
            }

        # Build hierarchy path: strip root GO name, keep children.
        parts = [p for p in symbol_path.split("/") if p]
        hierarchy_target = "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"

        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
            "ops": [
                {
                    "resource": "target",
                    "op": "add_component",
                    "target": hierarchy_target,
                    "type": component_type,
                },
            ],
        }

        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=(not confirm),
            confirm=confirm,
            change_reason=change_reason or None,
        )

        auto_refresh = "skipped"
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            auto_refresh = orch.maybe_auto_refresh()

        result = resp.to_dict()
        if confirm and resp.success:
            result["auto_refresh"] = auto_refresh
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "hierarchy_target": hierarchy_target,
            "component_type": component_type,
            "file_id": node.file_id,
        }
        return result

    @server.tool()
    def remove_component(
        asset_path: str,
        symbol_path: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Remove a component from an existing GameObject in an open-mode asset.

        Two-phase workflow:
        - confirm=False (default): dry-run preview.
        - confirm=True: applies the removal.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            symbol_path: Symbol path to the component to remove
                (e.g. "Player/AudioSource" or
                "Player/Body/MonoBehaviour(PlayerScript)").
            confirm: Set True to apply (False = dry-run only).
            change_reason: Human-readable reason for the change (audit trail).
        """
        text, resolved = _read_asset(asset_path)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        try:
            node = tree.resolve_unique(symbol_path)
        except SymbolNotFoundError:
            suggestions = suggest_similar(
                symbol_path, _collect_symbol_paths(tree),
            )
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_NOT_FOUND",
                "message": f"No component found at symbol path: {symbol_path!r}",
                "data": {
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "suggestions": suggestions,
                },
                "diagnostics": [],
            }
        except AmbiguousSymbolError as exc:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_AMBIGUOUS",
                "message": str(exc),
                "data": {"asset_path": asset_path, "symbol_path": symbol_path},
                "diagnostics": [],
            }

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
                    "asset_path": asset_path,
                    "symbol_path": symbol_path,
                    "resolved_kind": node.kind.value,
                },
                "diagnostics": [],
            }

        try:
            component_name = _resolve_component_name(node)
        except ValueError as exc:
            return {
                "success": False,
                "severity": "error",
                "code": "SYMBOL_UNRESOLVABLE",
                "message": str(exc),
                "data": {"asset_path": asset_path, "symbol_path": symbol_path},
                "diagnostics": [],
            }

        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
            "ops": [
                {
                    "resource": "target",
                    "op": "remove_component",
                    "component": component_name,
                },
            ],
        }

        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=(not confirm),
            confirm=confirm,
            change_reason=change_reason or None,
        )

        auto_refresh = "skipped"
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            auto_refresh = orch.maybe_auto_refresh()

        result = resp.to_dict()
        if confirm and resp.success:
            result["auto_refresh"] = auto_refresh
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
        }
        return result

    @server.tool()
    def list_serialized_fields(
        script_or_guid: str,
        include_inherited: bool = False,
    ) -> dict[str, Any]:
        """List serialized C# fields for a Unity script.

        Parses the C# source to extract fields that Unity will serialize,
        enabling field coverage checks and rename impact analysis.

        Args:
            script_or_guid: .cs file path, class name (e.g. "NadeSharePuppetSpec"),
                or 32-char GUID string. Class name resolution requires an active project.
            include_inherited: If true, include fields from base classes
                (each annotated with source_class).
        """
        orch = session.get_orchestrator()
        resp = orch.list_serialized_fields(
            script_path_or_guid=script_or_guid,
            include_inherited=include_inherited,
        )
        return resp.to_dict()

    @server.tool()
    def validate_field_rename(
        script_or_guid: str,
        old_name: str,
        new_name: str,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """Analyze the impact of renaming a serialized C# field (read-only).

        Scans YAML assets for MonoBehaviours using the script and reports
        which assets reference the field. Does NOT apply any changes.

        Args:
            script_or_guid: .cs file path, class name, or 32-char GUID string.
                Class name resolution requires an active project.
            old_name: Current field name to rename.
            new_name: Proposed new field name.
            scope: Directory to restrict impact search (default: project root).
        """
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope)
        resp = orch.validate_field_rename(
            script_path_or_guid=script_or_guid,
            old_name=old_name,
            new_name=new_name,
            scope=resolved_scope,
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
        orch = session.get_orchestrator()
        resolved_scope = session.resolve_scope(scope) or scope
        resp = orch.check_field_coverage(scope=resolved_scope)
        return resp.to_dict()

    # ------------------------------------------------------------------
    # Editor bridge tools (read-only)
    # ------------------------------------------------------------------

    @server.tool()
    def editor_screenshot(
        view: str = "scene",
        width: int = 0,
        height: int = 0,
        refresh: bool = True,
    ) -> dict[str, Any]:
        """Capture a screenshot of the Unity Editor.

        Args:
            view: Which view to capture ("scene" or "game").
            width: Capture width in pixels (0 = current window size).
            height: Capture height in pixels (0 = current window size).
            refresh: Refresh the asset database before capturing (default True).
        """
        if refresh:
            try:
                send_action(action="refresh_asset_database")
            except Exception:
                logger.warning("Pre-screenshot refresh failed", exc_info=True)
        return send_action(action="capture_screenshot", view=view, width=width, height=height)

    @server.tool()
    def editor_select(
        hierarchy_path: str,
        prefab_asset_path: str = "",
    ) -> dict[str, Any]:
        """Select a GameObject in the Unity Hierarchy.

        Args:
            hierarchy_path: Hierarchy path of the GameObject (e.g. /Canvas/Panel/Button).
            prefab_asset_path: Asset path of a Prefab to open in Prefab Stage before selecting.
        """
        kwargs: dict[str, Any] = {"hierarchy_path": hierarchy_path}
        if prefab_asset_path:
            kwargs["prefab_asset_path"] = prefab_asset_path
        return send_action(action="select_object", **kwargs)

    @server.tool()
    def editor_frame(
        zoom: float = 0.0,
    ) -> dict[str, Any]:
        """Frame the selected object in Scene view.

        Args:
            zoom: Scene view distance factor (SceneView.size). 0 = keep current.
                Larger values zoom OUT, smaller values zoom IN. Typical: 0.1-5.0.
        """
        return send_action(action="frame_selected", zoom=zoom)

    @server.tool()
    def editor_get_camera() -> dict[str, Any]:
        """Get current Scene view camera state.

        Returns position, rotation (quaternion + euler), pivot, size, and
        orthographic mode. Euler uses yaw=0 as front (+Z direction).
        """
        return send_action(action="get_camera")

    @server.tool()
    def editor_set_camera(
        position: str = "",
        rotation: str = "",
        size: float = -1.0,
        pivot: str = "",
        yaw: float = float("nan"),
        pitch: float = float("nan"),
        distance: float = -1.0,
        orthographic: int = -1,
    ) -> dict[str, Any]:
        """Set Scene view camera. Two modes (cannot mix):

        Mode A (absolute): position, rotation, size
        Mode B (pivot orbit): pivot, yaw, pitch, distance

        Euler convention: yaw=0 = front (+Z direction).
        Omitted params keep their current value.

        Args:
            position: JSON '{"x":0,"y":1,"z":-5}' — world position.
            rotation: JSON '{"yaw":0,"pitch":15,"roll":0}' — euler degrees.
            size: SceneView zoom level (>=0 to set, -1 = keep).
            pivot: JSON '{"x":0,"y":0,"z":0}' — orbit center.
            yaw: Horizontal rotation in degrees (Mode B).
            pitch: Vertical rotation in degrees (Mode B).
            distance: Distance from pivot (Mode B, >=0 to set, -1 = keep).
            orthographic: -1=keep, 0=perspective, 1=orthographic.
        """
        import json as _json
        import math

        kwargs: dict[str, Any] = {}

        # Mode A params
        if position:
            p = _json.loads(position)
            kwargs["camera_position"] = [p["x"], p["y"], p["z"]]
        if rotation:
            r = _json.loads(rotation)
            kwargs["camera_rotation"] = [r.get("yaw", 0), r.get("pitch", 0), r.get("roll", 0)]
        if size >= 0:
            kwargs["camera_size"] = size

        # Mode B params
        if pivot:
            pv = _json.loads(pivot)
            kwargs["camera_pivot"] = [pv["x"], pv["y"], pv["z"]]
        if not math.isnan(yaw):
            kwargs["yaw"] = yaw
        if not math.isnan(pitch):
            kwargs["pitch"] = pitch
        if distance >= 0:
            kwargs["distance"] = distance

        # Shared
        if orthographic >= 0:
            kwargs["camera_orthographic"] = orthographic

        return send_action(action="set_camera", **kwargs)

    @server.tool()
    def editor_list_children(
        hierarchy_path: str,
        depth: int = 1,
    ) -> dict[str, Any]:
        """List children of a GameObject in the running scene.

        Args:
            hierarchy_path: Hierarchy path to the parent GameObject.
            depth: Maximum depth to traverse (default: 1).
        """
        return send_action(action="list_children", hierarchy_path=hierarchy_path, depth=depth)

    @server.tool()
    def editor_list_materials(
        hierarchy_path: str,
    ) -> dict[str, Any]:
        """List material slots on renderers under a GameObject at runtime.

        Args:
            hierarchy_path: Hierarchy path to the root GameObject.
        """
        return send_action(action="list_materials", hierarchy_path=hierarchy_path)

    @server.tool()
    def editor_list_roots() -> dict[str, Any]:
        """List root GameObjects in the current Scene or Prefab Stage."""
        return send_action(action="list_roots")

    @server.tool()
    def editor_get_material_property(
        hierarchy_path: str,
        material_index: int,
        property_name: str = "",
    ) -> dict[str, Any]:
        """Read shader property values from a material at runtime.

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            property_name: Shader property to read (empty = list all properties).
        """
        return send_action(
            action="get_material_property",
            hierarchy_path=hierarchy_path, material_index=material_index,
            property_name=property_name,
        )

    @server.tool()
    def editor_set_material_property(
        hierarchy_path: str,
        material_index: int,
        property_name: str,
        value: str | list | int | float,
    ) -> dict[str, Any]:
        """Set a shader property value on a material at runtime.

        Type is determined from shader definition (not from the value format).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            property_name: Shader property name (e.g. "_Color", "_MainTex").
            value: Value as string. Format depends on shader type:
                Float/Range: "0.5"
                Int: "2"
                Color: "[1, 0.8, 0.6, 1]" (RGBA)
                Vector: "[0, 1, 0, 0]" (XYZW)
                Texture: "guid:abc123..." or "" (null)
        """
        import json as _json
        str_value = value if isinstance(value, str) else _json.dumps(value)
        return send_action(
            action="set_material_property",
            hierarchy_path=hierarchy_path,
            material_index=material_index,
            property_name=property_name,
            property_value=str_value,
        )

    @server.tool()
    def editor_console(
        max_entries: int = 200,
        log_type_filter: str = "all",
        since_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Capture Unity Console log entries as structured data.

        Args:
            max_entries: Maximum number of log entries to retrieve (default: 200).
            log_type_filter: Filter by log type: "all", "error", "warning", "exception".
            since_seconds: Only entries from the last N seconds (0 = no time filter).
        """
        return send_action(
            action="capture_console_logs",
            max_entries=max_entries, log_type_filter=log_type_filter,
            since_seconds=since_seconds,
        )

    # ------------------------------------------------------------------
    # Editor bridge tools (side-effect)
    # ------------------------------------------------------------------

    @server.tool()
    def editor_refresh() -> dict[str, Any]:
        """Trigger AssetDatabase.Refresh() in the running Unity Editor."""
        return send_action(action="refresh_asset_database")

    @server.tool()
    def editor_recompile() -> dict[str, Any]:
        """Trigger C# script recompilation in the running Unity Editor."""
        return send_action(action="recompile_scripts")

    @server.tool()
    def editor_run_tests(
        timeout_sec: int = 300,
    ) -> dict[str, Any]:
        """Run Unity integration tests via Editor Bridge.

        Args:
            timeout_sec: Maximum wait time in seconds (default: 300).
        """
        return send_action(action="run_integration_tests", timeout_sec=timeout_sec)

    @server.tool()
    def vrcsdk_upload(
        target_type: str,
        asset_path: str,
        blueprint_id: str,
        platforms: list[str] | None = None,
        description: str = "",
        tags: str = "",
        release_status: str = "",
        confirm: bool = False,
        change_reason: str = "",
        timeout_sec: int = 600,
    ) -> dict[str, Any]:
        """Build and upload an avatar or world to VRChat via VRC SDK.

        Existing asset update only (blueprint_id required).

        Two-phase workflow:
        - confirm=False (default): validates SDK login, asset, descriptor.
        - confirm=True: builds and uploads to VRChat.

        Args:
            target_type: "avatar" or "world".
            asset_path: Prefab path (avatar) or Scene path (world).
            blueprint_id: Existing VRC asset ID (e.g. "avtr_xxx..."). Required.
            platforms: List of target platforms (default: ["windows"]).
                Valid values: "windows", "android", "ios".
            description: Description text (empty = no change).
            tags: JSON array of tag strings (empty = no change).
            release_status: "public" or "private" (empty = no change).
            confirm: Set True to build + upload (False = validation only).
            change_reason: Required when confirm=True. Audit log reason.
            timeout_sec: Bridge timeout in seconds (default: 600).
                For multi-platform, recommend 600 * len(platforms).
        """
        if platforms is None:
            platforms = ["windows"]

        _valid_platforms = {"windows", "android", "ios"}
        if not platforms:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": "platforms must not be empty",
                "data": {},
                "diagnostics": [],
            }
        invalid = [p for p in platforms if p not in _valid_platforms]
        if invalid:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": f"Invalid platform(s): {invalid}. Valid: {sorted(_valid_platforms)}",
                "data": {},
                "diagnostics": [],
            }
        if len(platforms) != len(set(platforms)):
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_INVALID_PLATFORMS",
                "message": f"Duplicate platform(s) in: {platforms}",
                "data": {},
                "diagnostics": [],
            }

        if confirm and not change_reason:
            return {
                "success": False,
                "severity": "error",
                "code": "VRCSDK_REASON_REQUIRED",
                "message": "change_reason is required when confirm=True",
                "data": {},
                "diagnostics": [],
            }

        result = send_action(
            action="vrcsdk_upload",
            timeout_sec=timeout_sec,
            target_type=target_type,
            asset_path=asset_path,
            blueprint_id=blueprint_id,
            platforms=json.dumps(platforms),
            description=description,
            tags=tags,
            release_status=release_status,
            confirm=confirm,
        )

        # Post-process: convert C# platform_results_json to structured data
        data = result.setdefault("data", {})
        if isinstance(data, dict):
            prj = data.pop("platform_results_json", "")
            if prj:
                data["platform_results"] = json.loads(prj)
            if not confirm:
                data["platforms"] = platforms

        return result

    # ------------------------------------------------------------------
    # Editor bridge tools (write / mutation)
    # ------------------------------------------------------------------

    @server.tool()
    def editor_instantiate(
        asset_path: str,
        hierarchy_path: str = "",
        position: str = "",
    ) -> dict[str, Any]:
        """Instantiate a Prefab into the current Scene.

        Args:
            asset_path: Asset path of the prefab (e.g. Assets/Prefabs/Mic.prefab).
            hierarchy_path: Hierarchy path of the parent GameObject (empty = scene root).
            position: Local position as "x,y,z" string (e.g. "0,1.5,0"). Empty = default.
        """
        kwargs: dict[str, Any] = {"asset_path": asset_path, "hierarchy_path": hierarchy_path}
        if position:
            try:
                parts = [float(v) for v in position.split(",")]
            except ValueError:
                return {
                    "success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"Non-numeric position values: {position} (expected x,y,z)",
                    "data": {}, "diagnostics": [],
                }
            if len(parts) != 3:
                return {
                    "success": False, "severity": "error", "code": "INVALID_POSITION",
                    "message": f"position requires exactly 3 values (x,y,z), got {len(parts)}",
                    "data": {}, "diagnostics": [],
                }
            kwargs["position"] = parts
        return send_action(action="instantiate_to_scene", **kwargs)

    @server.tool()
    def editor_set_material(
        hierarchy_path: str,
        material_index: int,
        material_guid: str,
    ) -> dict[str, Any]:
        """Replace a material slot on a Renderer at runtime (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a Renderer.
            material_index: Material slot index (0-based).
            material_guid: GUID of the replacement Material asset (32-char hex).
        """
        return send_action(
            action="set_material",
            hierarchy_path=hierarchy_path, material_index=material_index,
            material_guid=material_guid,
        )

    @server.tool()
    def editor_delete(
        hierarchy_path: str,
    ) -> dict[str, Any]:
        """Delete a GameObject from the scene hierarchy (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject to delete.
        """
        return send_action(action="delete_object", hierarchy_path=hierarchy_path)

    # ------------------------------------------------------------------
    # Editor Bridge – Phase 2: BlendShape + Menu
    # ------------------------------------------------------------------

    @server.tool()
    def editor_get_blend_shapes(
        hierarchy_path: str,
        filter: str = "",
    ) -> dict[str, Any]:
        """Get BlendShape names and current weight values from a SkinnedMeshRenderer.

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a SkinnedMeshRenderer.
            filter: Substring filter on BlendShape names (empty = return all).
        """
        return send_action(
            action="get_blend_shapes",
            hierarchy_path=hierarchy_path,
            filter=filter,
        )

    @server.tool()
    def editor_set_blend_shape(
        hierarchy_path: str,
        name: str,
        weight: float,
    ) -> dict[str, Any]:
        """Set a BlendShape weight by name on a SkinnedMeshRenderer (Undo-able).

        Args:
            hierarchy_path: Hierarchy path to the GameObject with a SkinnedMeshRenderer.
            name: BlendShape name (exact match).
            weight: Weight value (0-100).
        """
        return send_action(
            action="set_blend_shape",
            hierarchy_path=hierarchy_path,
            blend_shape_name=name,
            blend_shape_weight=weight,
        )

    @server.tool()
    def editor_list_menu_items(
        prefix: str = "",
    ) -> dict[str, Any]:
        """List Unity Editor menu items registered via [MenuItem] attribute.

        Args:
            prefix: Path prefix filter (e.g. "Tools/", "CONTEXT/"). Empty = all items.
        """
        return send_action(
            action="list_menu_items",
            filter=prefix,
        )

    @server.tool()
    def editor_execute_menu_item(
        menu_path: str,
    ) -> dict[str, Any]:
        """Execute a Unity Editor menu item by path.

        Some menu items may display modal dialogs that block the Editor.
        Dangerous paths (File/New Scene, File/New Project, Assets/Delete) are denied.

        Args:
            menu_path: Full menu path (e.g. "Tools/NDMF/Manual Bake").
        """
        return send_action(
            action="execute_menu_item",
            menu_path=menu_path,
        )

    # ------------------------------------------------------------------
    # Inspection tools (orchestrator-backed)
    # ------------------------------------------------------------------

    @server.tool()
    def inspect_materials(asset_path: str) -> dict[str, Any]:
        """Show per-renderer material slot assignments with override/inherited markers.

        Args:
            asset_path: Path to a .prefab or .unity file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_materials(target_path=asset_path)
        return resp.to_dict()

    @server.tool()
    def inspect_material_asset(asset_path: str) -> dict[str, Any]:
        """Inspect shader, properties, and texture references in a .mat file (read-only).

        Returns structured data about the material's shader, texture slots,
        float/color/int properties, and summary counts. Unset texture slots
        are omitted.

        Args:
            asset_path: Path to a .mat file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_material_asset(target_path=asset_path)
        return resp.to_dict()

    @server.tool()
    def set_material_property(
        asset_path: str,
        property_name: str,
        value: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Set a single property in a .mat file (offline YAML editing).

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing before/after.
        - confirm=True: applies the change and writes back.

        Value format depends on property category:
        - Float: "0.5"
        - Int: "2"
        - Color: "[1, 0.8, 0.6, 1]" (RGBA)
        - Texture: "guid:abc123..." or "" (null)

        Args:
            asset_path: Path to the .mat file.
            property_name: Property name (e.g. "_Glossiness", "_Color").
            value: New value as string.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        orch = session.get_orchestrator()
        resp = orch.set_material_property(
            target_path=asset_path,
            property_name=property_name,
            value=value,
            dry_run=not confirm,
            change_reason=change_reason or None,
        )
        return resp.to_dict()

    @server.tool()
    def validate_structure(asset_path: str) -> dict[str, Any]:
        """Validate internal YAML structure (fileID duplicates, Transform consistency).

        Args:
            asset_path: Path to a .prefab, .unity, or .asset file.
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_structure(target_path=asset_path)
        return resp.to_dict()

    @server.tool()
    def inspect_hierarchy(
        asset_path: str,
        depth: int | None = None,
        show_components: bool = True,
    ) -> dict[str, Any]:
        """Display the GameObject hierarchy tree of a Unity asset.

        Args:
            asset_path: Path to a .prefab or .unity file.
            depth: Maximum tree depth to display (None = unlimited).
            show_components: Show component annotations (default: True).
        """
        orch = session.get_orchestrator()
        resp = orch.inspect_hierarchy(
            target_path=asset_path,
            max_depth=depth,
            show_components=show_components,
        )
        return resp.to_dict()

    # ------------------------------------------------------------------
    # AI workflow tools
    # ------------------------------------------------------------------

    @server.tool()
    def validate_runtime(
        asset_path: str,
        profile: str = "default",
        log_file: str | None = None,
        since_timestamp: str | None = None,
        allow_warnings: bool = False,
        max_diagnostics: int = 200,
    ) -> dict[str, Any]:
        """Run runtime validation: UdonSharp compile + ClientSim execution.

        Args:
            asset_path: Target Unity scene path.
            profile: Runtime profile label for ClientSim execution context.
            log_file: Unity log file path (default: <project>/Logs/Editor.log).
            since_timestamp: Log cursor label for filtering.
            allow_warnings: Treat warning-only findings as pass.
            max_diagnostics: Maximum diagnostics to include (default: 200).
        """
        orch = session.get_orchestrator()
        resp = orch.validate_runtime(
            scene_path=asset_path,
            profile=profile,
            log_file=log_file,
            since_timestamp=since_timestamp,
            allow_warnings=allow_warnings,
            max_diagnostics=max_diagnostics,
        )
        return resp.to_dict()

    @server.tool()
    def patch_apply(
        plan: str | dict,
        confirm: bool = False,
        change_reason: str = "",
        scope: str | None = None,
        runtime_scene: str | None = None,
        runtime_profile: str = "default",
        runtime_log_file: str | None = None,
        runtime_since_timestamp: str | None = None,
        runtime_allow_warnings: bool = False,
        runtime_max_diagnostics: int = 200,
    ) -> dict[str, Any]:
        """Validate and apply a patch plan to Unity assets.

        Two-phase workflow:
        - confirm=False (default): dry-run validation only.
        - confirm=True: applies changes and runs post-apply checks.

        Args:
            plan: Patch plan as JSON string. Must conform to plan_version "2".
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
            scope: Directory for post-apply reference validation.
            runtime_scene: Scene path for post-apply runtime validation.
            runtime_profile: ClientSim profile for runtime validation.
            runtime_log_file: Unity log file path for runtime validation.
            runtime_since_timestamp: Log cursor for runtime validation.
            runtime_allow_warnings: Allow warnings in runtime validation.
            runtime_max_diagnostics: Max diagnostics for runtime validation.
        """
        import json as _json
        # Pydantic 2.11+ may pre-parse JSON strings into dicts
        if isinstance(plan, dict):
            plan_dict = plan
        else:
            try:
                plan_dict = _json.loads(plan)
            except (ValueError, TypeError) as exc:
                return {
                    "success": False, "severity": "error", "code": "INVALID_PLAN_JSON",
                    "message": f"Failed to parse plan JSON: {exc}",
                    "data": {}, "diagnostics": [],
                }

        orch = session.get_orchestrator()
        try:
            resp = orch.patch_apply(
                plan=plan_dict,
                dry_run=not confirm,
                confirm=confirm,
                plan_sha256=None,
                plan_signature=None,
                change_reason=change_reason or None,
                scope=scope,
                runtime_scene=runtime_scene,
                runtime_profile=runtime_profile,
                runtime_log_file=runtime_log_file,
                runtime_since_timestamp=runtime_since_timestamp,
                runtime_allow_warnings=runtime_allow_warnings,
                runtime_max_diagnostics=runtime_max_diagnostics,
            )
        except ValueError as exc:
            return {
                "success": False, "severity": "error",
                "code": "INVALID_PLAN_SCHEMA",
                "message": f"Plan validation failed: {exc}",
                "data": {}, "diagnostics": [],
            }
        result = resp.to_dict()
        if confirm and resp.success:
            orch_ref = session.get_orchestrator()
            result["auto_refresh"] = orch_ref.maybe_auto_refresh()
        return result

    # ------------------------------------------------------------------
    # Revert tool
    # ------------------------------------------------------------------

    @server.tool()
    def revert_overrides(
        asset_path: str,
        target_file_id: str,
        property_path: str,
        confirm: bool = False,
        change_reason: str = "",
    ) -> dict[str, Any]:
        """Remove a specific property override from a Prefab Variant.

        Two-phase workflow:
        - confirm=False (default): dry-run preview showing what would be removed.
        - confirm=True: applies the removal and writes back.

        Args:
            asset_path: Path to the Prefab Variant file.
            target_file_id: fileID of the target component in the parent prefab.
            property_path: propertyPath of the override to remove.
            confirm: Set True to apply (False = dry-run only).
            change_reason: Required when confirm=True. Audit log reason.
        """
        resp = revert_overrides_impl(
            variant_path=asset_path,
            target_file_id=target_file_id,
            property_path=property_path,
            dry_run=not confirm,
            confirm=confirm,
            change_reason=change_reason or None,
        )
        result = resp.to_dict()
        if confirm and resp.success:
            orch = session.get_orchestrator()
            result["auto_refresh"] = orch.maybe_auto_refresh()
        return result

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
