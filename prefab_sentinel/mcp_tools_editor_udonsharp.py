"""MCP tools for high-level UdonSharp authoring (issue #119).

These three tools wrap multi-step Unity Editor authoring flows that
otherwise require hand-written C# snippets through ``editor_run_script``:

- ``editor_add_udonsharp_component`` performs the
  ``UdonSharpUndo.AddComponent`` (which internally chains
  ``Undo.AddComponent`` + ``RunBehaviourSetupWithUndo``) →
  ``CopyProxyToUdon`` upsert (idempotent reuse on a pre-existing match).
- ``editor_set_udonsharp_field`` writes a single serialized field
  (including the VRChat URL field shape) and synchronises the backing
  ``UdonBehaviour`` with the proxy as one transaction.
- ``editor_wire_persistent_listener`` wraps Unity's published
  ``UnityEventTools.AddStringPersistentListener`` so a Slider /
  Toggle ``onValueChanged`` event can be wired to
  ``UdonBehaviour.SendCustomEvent`` declaratively.

Local validation here mirrors the existing ``editor_set_property``
value-vs-reference convention: requests that conflict with the
client-side contract are rejected without contacting the bridge.
Everything else is forwarded to the bridge unchanged.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from prefab_sentinel.editor_bridge import send_action
from prefab_sentinel.mcp_validation import require_write_audit

__all__ = ["register_editor_udonsharp_tools"]


def _both_value_envelope() -> dict[str, Any]:
    """Return the UdonSharp field input-conflict envelope."""
    return {
        "success": False,
        "severity": "error",
        "code": "EDITOR_CTRL_UDON_SET_FIELD_INPUT_CONFLICT",
        "message": "Provide exactly one of value, object_reference, or values_json.",
        "data": {},
        "diagnostics": [],
    }


def _no_value_envelope() -> dict[str, Any]:
    """Distinct empty-input envelope for the UdonSharp field-set tool."""
    return {
        "success": False,
        "severity": "error",
        "code": "EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE",
        "message": (
            "editor_set_udonsharp_field requires exactly one of ``value``, "
            "``object_reference``, or ``values_json`` to be supplied."
        ),
        "data": {},
        "diagnostics": [],
    }


def register_editor_udonsharp_tools(server: FastMCP) -> None:
    """Register the three UdonSharp authoring tools on *server*."""

    @server.tool()
    def editor_add_udonsharp_component(
        hierarchy_path: str,
        type_full_name: str,
        fields_json: str = "",
        confirm: bool = False,
        change_reason: str | None = None,
    ) -> dict[str, Any]:
        """Upsert an UdonSharpBehaviour component on a GameObject.

        Wraps Unity's authoring chain so callers do not have to write
        editor C# through ``editor_run_script``: the bridge runs
        ``UdonSharpUndo.AddComponent`` (the public wrapper that chains
        ``Undo.AddComponent`` + ``RunBehaviourSetupWithUndo`` internally)
        → optional initial field assignment → ``CopyProxyToUdon`` for a
        fresh component, and reuses the existing proxy / UdonBehaviour
        pair when the component is already present (re-applying any
        supplied fields so the call is idempotent).

        Use this in preference to writing the same C# inline in
        ``editor_run_script`` so:

        * the proxy + backing pair stays consistent (issue #103);
        * the upsert path stays atomic from the caller's view; and
        * recovery on partial failure is just calling the tool again.

        Example::

            editor_add_udonsharp_component(
                hierarchy_path="/UI/PlayButton",
                type_full_name="VVMW.PlayController",
                fields_json='{"defaultUrl": "https://example.com/clip.m3u8"}',
                confirm=True,
                change_reason="add PlayController UdonSharp component",
            )

        Args:
            hierarchy_path: Hierarchy path of the target GameObject.
            type_full_name: Component type name (short or fully
                qualified). Must derive from
                ``UdonSharp.UdonSharpBehaviour``.
            fields_json: JSON object mapping field name to a
                string-encoded value, parsed through the same
                ApplyPropertyValue surface as ``editor_set_property``.
                Pass an empty string to skip initial field assignment.
            confirm: Must be ``True`` for this write-class tool.
            change_reason: Non-empty audit reason recorded with the write.

        Returns:
            The bridge envelope.  ``data`` carries ``was_existing``,
            ``applied_fields``, ``component_handle``, and
            ``udon_program_asset_path`` per the issue #119 contract.
        """
        audit_err = require_write_audit(
            "editor_add_udonsharp_component", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err

        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "component_type": type_full_name,
        }
        if fields_json:
            kwargs["fields_json"] = fields_json
        return send_action(action="editor_add_udonsharp_component", **kwargs)

    @server.tool()
    def editor_set_udonsharp_field(
        hierarchy_path: str,
        property_name: str,
        value: str | None = None,
        object_reference: str = "",
        values_json: str | None = None,
        expected_length: int | None = None,
        confirm: bool = False,
        change_reason: str | None = None,
    ) -> dict[str, Any]:
        """Write a serialized field on the unique UdonSharp behaviour."""
        input_count = sum(
            [value is not None, bool(object_reference), values_json is not None]
        )
        if input_count > 1:
            return _both_value_envelope()
        if input_count == 0:
            return _no_value_envelope()

        audit_err = require_write_audit(
            "editor_set_udonsharp_field", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err

        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "field_name": property_name,
        }
        if object_reference:
            kwargs["object_reference"] = object_reference
        elif values_json is not None:
            kwargs["values_json"] = values_json
            kwargs["values_json_present"] = True
            if expected_length is not None:
                kwargs["expected_length"] = expected_length
        else:
            kwargs["property_value"] = value
            kwargs["property_value_present"] = True
        return send_action(action="editor_set_udonsharp_field", **kwargs)

    @server.tool()
    def editor_wire_persistent_listener(
        hierarchy_path: str,
        property_name: str,
        target_hierarchy_path: str,
        method: str,
        arg: str,
        confirm: bool = False,
        change_reason: str | None = None,
    ) -> dict[str, Any]:
        """Wire a string-mode persistent listener from a UnityEvent to
        a method on another component.

        Wraps the published ``UnityEventTools.AddStringPersistentListener``
        entry point.  The bridge resolves the source component carrying
        the named event field on *hierarchy_path*, the target component
        on *target_hierarchy_path* with a void ``method(string)``
        overload, and adds a string-mode listener bound to *arg*.
        Idempotent: an existing listener with matching target / method /
        mode / arg results in a no-op success response.

        The canonical use case is wiring a UI control's event to
        ``UdonBehaviour.SendCustomEvent`` so a string event name fires
        on UdonSharp without writing a tiny editor script.

        Example::

            editor_wire_persistent_listener(
                hierarchy_path="/UI/Slider",
                property_name="onValueChanged",
                target_hierarchy_path="/Logic/UdonController",
                method="SendCustomEvent",
                arg="OnSliderChanged",
                confirm=True,
                change_reason="wire slider event to Udon controller",
            )

        String mode only — ``mode`` is intentionally absent from the
        signature so the contract stays additive when void / int /
        float / bool / object modes are introduced later.

        Args:
            hierarchy_path: Hierarchy path of the source GameObject
                (the one whose UnityEvent is being wired *from*).
            property_name: Name of the UnityEvent field/property on a
                component of the source GameObject (e.g.
                ``"onValueChanged"``).
            target_hierarchy_path: Hierarchy path of the target
                GameObject (the component whose method gets invoked).
            method: Method name on a component of the target with a
                ``void method(string)`` signature (e.g.
                ``"SendCustomEvent"`` on ``UdonBehaviour``).
            arg: String argument bound at edit time and supplied to
                the method on every invocation.
            confirm: Must be ``True`` for this write-class tool.
            change_reason: Non-empty audit reason recorded with the write.

        Returns:
            The bridge envelope.
        """
        audit_err = require_write_audit(
            "editor_wire_persistent_listener", confirm, change_reason,
        )
        if audit_err is not None:
            return audit_err

        # The bridge DTO names the event field ``event_property_name``
        # (issue #61 named it for the component field it carries) and
        # the target field ``target_path``; the MCP-facing arguments
        # are ``property_name`` and ``target_hierarchy_path`` for
        # naming-convention conformance (#53/#58).
        return send_action(
            action="editor_wire_persistent_listener",
            hierarchy_path=hierarchy_path,
            event_property_name=property_name,
            target_path=target_hierarchy_path,
            method=method,
            arg=arg,
        )
