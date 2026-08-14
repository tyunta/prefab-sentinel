"""MCP tools for setting serialized field values."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server import MCPServer

from prefab_sentinel.contracts import ToolResponse, error_dict
from prefab_sentinel.mcp_helpers import (
    read_asset,
    resolve_component_with_type,
)
from prefab_sentinel.mcp_validation import require_change_reason
from prefab_sentinel.patch_plan import PLAN_VERSION
from prefab_sentinel.patch_transaction_io import (
    reserve_transaction_report,
    validate_transaction_report_path,
    write_report_payload,
)
from prefab_sentinel.patch_transaction_results import boundary_failure
from prefab_sentinel.services.prefab_variant.overrides import (
    iter_base_property_values,
)
from prefab_sentinel.services.serialized_object.property_diagnostics import (
    resolve_property_not_found,
)
from prefab_sentinel.session import ProjectSession

if TYPE_CHECKING:
    from prefab_sentinel.symbol_tree import SymbolNode

__all__ = ["register_set_property_tools"]


# Suffix produced by ``iter_base_property_values`` for the first array
# element of any container field. Splitting on this lets us recover the
# container name (``m_Materials``) from element paths
# (``m_Materials.Array.data[0]``); the container itself is a valid
# ``set_properties`` target.
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
        # Surface the container name so ``set_properties`` calls
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


def _write_set_properties_report(
    report_path: Path,
    operation_response: dict[str, Any],
) -> dict[str, Any]:
    try:
        write_report_payload(report_path, operation_response)
    except OSError:
        result_key = (
            "operation_result"
            if operation_response["success"]
            else "operation_error"
        )
        return error_dict(
            "OUT_REPORT_WRITE_FAILED",
            "Operation completed but the report file could not be written.",
            data={result_key: operation_response},
        )
    return operation_response

def _project_writer_response(
    response: ToolResponse,
    confirmed: bool,
) -> tuple[dict[str, Any], bool]:
    result = response.to_dict()
    response_data = dict(result["data"])
    result["data"] = response_data
    state_unknown = (
        confirmed
        and not response.success
        and response_data.get("read_only") is False
    )
    if state_unknown:
        response_data["state_unknown"] = True
    return result, state_unknown


def _resolve_writer_target(
    session: ProjectSession,
    asset_path: str,
    symbol_path: str,
) -> tuple[str, Path, SymbolNode, str] | dict[str, Any]:
    try:
        text, resolved = read_asset(asset_path, session.project_root)
        tree = session.get_symbol_tree(
            resolved,
            text,
            include_properties=False,
        )
        node, component_name, error = resolve_component_with_type(
            tree,
            symbol_path,
            asset_path,
        )
    except Exception as exc:
        return boundary_failure("preflight", exc).to_dict()
    if error is not None:
        return error
    assert node is not None
    assert component_name is not None
    return text, resolved, node, component_name




def register_set_property_tools(server: MCPServer, session: ProjectSession) -> None:
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
        preflight = _resolve_writer_target(session, asset_path, symbol_path)
        if isinstance(preflight, dict):
            return preflight
        text, resolved, node, component_name = preflight

        # Issue #37: the set op identifies its target by the resolved
        # symbol node's exact fileID, so an asset with several same-type
        # components on one GameObject resolves to the intended one.
        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [{"id": "target", "path": asset_path, "mode": "open"}],
            "ops": [
                {
                    "resource": "target",
                    "op": "set",
                    "file_id": node.file_id,
                    "path": property_path,
                    "value": value,
                },
            ],
        }

        mutation_state_unknown = False
        try:
            orch = session.get_orchestrator()
            mutation_state_unknown = confirm
            resp = orch.serialized_value_patch_apply(
                plan=plan,
                dry_run=(not confirm),
                confirm=confirm,
                change_reason=change_reason or None,
            )
        except Exception as exc:
            result = boundary_failure(
                "apply",
                exc,
                state_unknown=mutation_state_unknown,
            ).to_dict()
            if mutation_state_unknown:
                session.invalidate_symbol_tree(resolved)
            return result

        result, returned_state_unknown = _project_writer_response(resp, confirm)
        if confirm and (resp.success or returned_state_unknown):
            session.invalidate_symbol_tree(resolved)
        if confirm and resp.success:
            try:
                result["auto_refresh"] = orch.maybe_auto_refresh()
            except Exception as exc:
                return boundary_failure("apply", exc, state_unknown=True).to_dict()
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
            "property_path": property_path,
        }
        return result

    @server.tool()
    def set_properties(
        asset_path: str,
        symbol_path: str,
        properties: dict[str, Any],
        dry_run: bool = False,
        confirm: bool = False,
        change_reason: str | None = None,
        out_report: str | None = None,
    ) -> dict[str, Any]:
        """Set multiple serialized property values on a component in a single transaction.

        Two-phase workflow:
        - confirm=False (default): dry-run preview of changes.
        - confirm=True: applies changes to disk (requires change_reason + out_report).
        - dry_run=True: explicit preview flag (overrides confirm if both are True).

        Issue #41: symbol_path resolves directly to a component through the
        same component-resolution path set_property uses.

        Args:
            asset_path: Asset file path (.prefab, .unity, .asset).
            symbol_path: Human-readable path to a component
                (e.g. "Controller/MeshRenderer" or
                "Body/Head/MonoBehaviour(PlayerScript)").
            properties: Mapping of property paths to new values
                ({property_path: value, ...}).
            dry_run: Explicit preview flag; overrides confirm when both are True.
            confirm: Set True to apply changes (requires change_reason + out_report).
            change_reason: Human-readable reason for the change (required when confirm=True).
            out_report: Path to write result JSON report (required when confirm=True).
        """
        if not properties:
            return {
                "success": False,
                "severity": "error",
                "code": "EMPTY_FIELDS",
                "message": "properties dict must not be empty.",
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

        report_path: Path | None = None
        if effective_confirm:
            assert session.project_root is not None
            report_candidate = validate_transaction_report_path(
                Path(session.project_root),
                out_report,
            )
            if isinstance(report_candidate, ToolResponse):
                return report_candidate.to_dict()

        preflight = _resolve_writer_target(session, asset_path, symbol_path)
        if isinstance(preflight, dict):
            return preflight
        text, resolved, node, component_name = preflight

        known_paths = _collect_known_property_paths(text, node.file_id)
        for field_path in properties:
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
                "file_id": node.file_id,
                "path": field_path,
                "value": field_value,
            }
            for field_path, field_value in properties.items()
        ]
        plan: dict[str, object] = {
            "plan_version": PLAN_VERSION,
            "resources": [
                {"id": "target", "path": asset_path, "mode": "open"}
            ],
            "ops": ops,
        }

        if effective_confirm:
            assert session.project_root is not None
            reservation = reserve_transaction_report(
                Path(session.project_root),
                out_report,
            )
            if isinstance(reservation, ToolResponse):
                return reservation.to_dict()
            report_path = reservation

        mutation_state_unknown = False
        try:
            orch = session.get_orchestrator()
            mutation_state_unknown = effective_confirm
            resp = orch.serialized_value_patch_apply(
                plan=plan,
                dry_run=effective_dry_run,
                confirm=effective_confirm,
                change_reason=change_reason or None,
            )
        except Exception as exc:
            result = boundary_failure(
                "apply",
                exc,
                state_unknown=mutation_state_unknown,
            ).to_dict()
            if mutation_state_unknown:
                session.invalidate_symbol_tree(resolved)
            if report_path is not None:
                return _write_set_properties_report(report_path, result)
            return result

        result, returned_state_unknown = _project_writer_response(
            resp,
            effective_confirm,
        )
        if effective_confirm and (resp.success or returned_state_unknown):
            session.invalidate_symbol_tree(resolved)
        if effective_confirm and resp.success:
            try:
                result["auto_refresh"] = orch.maybe_auto_refresh()
            except Exception as exc:
                result = boundary_failure("apply", exc, state_unknown=True).to_dict()
                if report_path is not None:
                    return _write_set_properties_report(report_path, result)
                return result
        result["symbol_resolution"] = {
            "symbol_path": symbol_path,
            "resolved_component": component_name,
            "file_id": node.file_id,
            "class_id": node.class_id,
            "fields": list(properties.keys()),
        }

        if report_path is not None:
            return _write_set_properties_report(report_path, result)
        return result
