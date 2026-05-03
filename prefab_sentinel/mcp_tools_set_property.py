"""MCP tools for setting serialized field values."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.json_io import dump_json
from prefab_sentinel.mcp_helpers import (
    find_component_on_go,
    read_asset,
    resolve_component_with_type,
    resolve_game_object_node,
)
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.services.prefab_variant.overrides import (
    iter_base_property_values,
)
from prefab_sentinel.services.serialized_object.property_diagnostics import (
    resolve_component_not_found,
    resolve_property_not_found,
)
from prefab_sentinel.session import ProjectSession

__all__ = ["register_set_property_tools"]


# Suffix produced by ``iter_base_property_values`` for the first array
# element of any container field. Splitting on this lets us recover the
# container name (``m_Materials``) from element paths
# (``m_Materials.Array.data[0]``); the container itself is a valid
# ``set_component_fields`` target.
_ARRAY_ELEMENT_SUFFIX = ".Array.data["


def _collect_known_property_paths(text: str, file_id: str) -> list[str]:
    """Return property paths set on the component identified by *file_id*.

    Reads the prefab text directly via ``iter_base_property_values`` so the
    check works for both base prefabs and Variant prefabs without requiring
    a chain walk. Both element paths (``m_Materials.Array.data[0]``) and the
    matching container name (``m_Materials``) are emitted so callers can
    target either form. Order of first appearance is preserved.
    """
    seen: set[str] = set()
    out: list[str] = []
    for fid, prop_path, _value in iter_base_property_values(text):
        if fid != file_id:
            continue
        if prop_path not in seen:
            seen.add(prop_path)
            out.append(prop_path)
        # Surface the container name so ``set_component_fields`` calls
        # that target the whole array (e.g. ``{"m_Materials": [...]}``)
        # are not rejected with a false ``SER003`` (issue #109 follow-up).
        suffix_index = prop_path.find(_ARRAY_ELEMENT_SUFFIX)
        if suffix_index <= 0:
            continue
        container = prop_path[:suffix_index]
        if container in seen:
            continue
        seen.add(container)
        out.append(container)
    return out


def register_set_property_tools(server: FastMCP, session: ProjectSession) -> None:
    """Register property-setting tools on *server*."""

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
        err = require_change_reason(confirm, change_reason)
        if err is not None:
            return err
        text, resolved = read_asset(asset_path, session.project_root)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        node, component_name, err = resolve_component_with_type(
            tree, symbol_path, asset_path,
        )
        if err is not None:
            return err
        assert node is not None

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

        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=(not confirm),
            confirm=confirm,
            change_reason=change_reason or None,
        )

        result = resp.to_dict()
        if confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            result["auto_refresh"] = orch.maybe_auto_refresh()
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
            "property_path": property_path,
        }
        return result

    @server.tool()
    def set_component_fields(
        asset_path: str,
        symbol_path: str,
        component: str,
        fields: dict[str, Any],
        dry_run: bool = False,
        confirm: bool = False,
        change_reason: str | None = None,
        out_report: str | None = None,
    ) -> dict[str, Any]:
        """Set multiple serialized field values on a component in a single transaction.

        Two-phase workflow:
        - confirm=False (default): dry-run preview of changes.
        - confirm=True: applies changes to disk (requires change_reason + out_report).
        - dry_run=True: explicit preview flag (overrides confirm if both are True).

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            symbol_path: Human-readable path to the target GameObject
                (e.g. "Controller" or "Body/Head").
            component: Component type name on the GameObject
                (e.g. "MeshRenderer" or "DualButtonController").
            fields: Mapping of property paths to new values
                ({property_path: value, ...}).
            dry_run: Explicit preview flag; overrides confirm when both are True.
            confirm: Set True to apply changes (requires change_reason + out_report).
            change_reason: Human-readable reason for the change (required when confirm=True).
            out_report: Path to write result JSON report (required when confirm=True).
        """
        if not fields:
            return {
                "success": False,
                "severity": "error",
                "code": "EMPTY_FIELDS",
                "message": "fields dict must not be empty.",
                "data": {},
                "diagnostics": [],
            }

        effective_dry_run = dry_run or not confirm
        effective_confirm = confirm and not dry_run

        err = require_change_reason(effective_confirm, change_reason)
        if err is not None:
            return err
        if effective_confirm and not out_report:
            return {
                "success": False,
                "severity": "error",
                "code": "OUT_REPORT_REQUIRED",
                "message": "out_report is required when confirm=True.",
                "data": {},
                "diagnostics": [],
            }

        if effective_confirm and session.project_root is None:
            return {
                "success": False,
                "severity": "error",
                "code": "PROJECT_ROOT_REQUIRED",
                "message": "out_report requires a configured project_root for path containment.",
                "data": {},
                "diagnostics": [],
            }

        report_path = Path(out_report).resolve() if out_report else None
        if (
            report_path is not None
            and session.project_root is not None
            and not report_path.is_relative_to(Path(session.project_root).resolve())
        ):
            return {
                "success": False,
                "severity": "error",
                "code": "OUT_REPORT_OUTSIDE_PROJECT",
                "message": (
                    f"out_report must be within the project root: "
                    f"{Path(session.project_root).resolve()}"
                ),
                "data": {},
                "diagnostics": [],
            }

        text, resolved = read_asset(asset_path, session.project_root)
        tree = session.get_symbol_tree(resolved, text, include_properties=False)
        go_node, err = resolve_game_object_node(tree, symbol_path, asset_path)
        if err is not None:
            return err
        assert go_node is not None

        node, component_name, err = find_component_on_go(go_node, component, asset_path)
        if err is not None:
            if err.get("code") == "COMPONENT_NOT_FOUND":
                # ``find_component_on_go`` already enumerated the component
                # types on the GameObject under ``data.available_components``;
                # reuse the list rather than walking ``go_node.children``
                # again (DRY).
                err_data = err.get("data", {})
                candidates = err_data.get("available_components", [])
                return resolve_component_not_found(
                    asset_path,
                    component,
                    candidates,
                ).to_dict()
            return err
        assert node is not None
        assert component_name is not None

        known_paths = _collect_known_property_paths(text, node.file_id)
        for field_path in fields:
            if field_path not in known_paths:
                return resolve_property_not_found(
                    asset_path,
                    component_name,
                    field_path,
                    known_paths,
                ).to_dict()

        ops = [
            {
                "resource": "target",
                "op": "set",
                "component": component_name,
                "path": field_path,
                "value": field_value,
            }
            for field_path, field_value in fields.items()
        ]
        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
            "ops": ops,
        }

        orch = session.get_orchestrator()
        resp = orch.patch_apply(
            plan=plan,
            dry_run=effective_dry_run,
            confirm=effective_confirm,
            change_reason=change_reason or None,
        )

        result = resp.to_dict()
        if effective_confirm and resp.success:
            session.invalidate_symbol_tree(resolved)
            result["auto_refresh"] = orch.maybe_auto_refresh()
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
            "fields": list(fields.keys()),
        }

        if report_path is not None and effective_confirm:
            report_path.write_text(
                dump_json(result) + "\n",
                encoding="utf-8",
            )

        return result
