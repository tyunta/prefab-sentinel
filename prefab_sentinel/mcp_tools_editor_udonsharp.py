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

__all__ = ["register_editor_udonsharp_tools"]


def _both_value_envelope() -> dict[str, Any]:
    """Mirror the existing property-set conflict envelope.

    The field-set tool reuses the editor-control bridge's
    ``EDITOR_CTRL_SET_PROP_BOTH_VALUE`` code so client-side and
    bridge-side error paths are addressable by one identifier.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "EDITOR_CTRL_SET_PROP_BOTH_VALUE",
        "message": "Provide value or object_reference, not both.",
        "data": {},
        "diagnostics": [],
    }


def _no_value_envelope() -> dict[str, Any]:
    """Distinct empty-input envelope for the UdonSharp field-set tool.

    Reusing the existing property-set value-vs-reference shape would
    erase the surface distinction; callers may want to write an empty
    string to a string field, but the value-versus-reference contract
    requires *one* of the two to be supplied so the bridge can pick the
    SerializedProperty branch deterministically.
    """
    return {
        "success": False,
        "severity": "error",
        "code": "EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE",
        "message": (
            "editor_set_udonsharp_field requires either ``value`` or "
            "``object_reference`` to be non-empty."
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

        Returns:
            The bridge envelope.  ``data`` carries ``was_existing``,
            ``applied_fields``, ``component_handle``, and
            ``udon_program_asset_path`` per the issue #119 contract.
        """
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
        field_name: str,
        value: str = "",
        object_reference: str = "",
    ) -> dict[str, Any]:
        """Write a single serialized field on the unique UdonSharp
        behaviour at *hierarchy_path*.

        Resolves the field through ``SerializedObject.FindProperty``
        (covering both ``public`` fields and ``[SerializeField]
        private`` fields), writes the value through the existing
        ApplyPropertyValue surface, and synchronises the backing
        ``UdonBehaviour`` with the proxy as one transaction.  VRChat
        URL fields are addressable through the same shape — pass the
        URL string in ``value`` and the bridge writes it to the
        nested ``url`` sub-property of the ``VRCUrl`` wrapper.

        Use this in preference to ``editor_set_property`` /
        ``editor_run_script`` when:

        * the field lives on an UdonSharpBehaviour (so
          ``CopyProxyToUdon`` must run after the write); or
        * the field is a ``VRCUrl`` (so the bridge writes the inner
          string instead of constructing a wrapper instance).

        Example::

            editor_set_udonsharp_field(
                hierarchy_path="/UI/PlayButton",
                field_name="defaultUrl",
                value="https://example.com/clip.m3u8",
            )

        Args:
            hierarchy_path: Hierarchy path of the target GameObject.
            field_name: Serialized field name (matches what
                SerializedObject.FindProperty resolves).
            value: String-encoded value for primitive / enum / VRCUrl
                fields.  Mutually exclusive with ``object_reference``.
            object_reference: Hierarchy path or asset path for
                ObjectReference fields (e.g. ``"/ToggleTarget"``).
                Mutually exclusive with ``value``.

        Returns:
            The bridge envelope.  Conflicting inputs return
            ``EDITOR_CTRL_SET_PROP_BOTH_VALUE`` from the client
            without contacting the bridge; both empty returns
            ``EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE``.
        """
        if value and object_reference:
            return _both_value_envelope()
        if not value and not object_reference:
            return _no_value_envelope()

        kwargs: dict[str, Any] = {
            "hierarchy_path": hierarchy_path,
            "field_name": field_name,
        }
        if object_reference:
            kwargs["object_reference"] = object_reference
        else:
            kwargs["property_value"] = value
        return send_action(action="editor_set_udonsharp_field", **kwargs)

    @server.tool()
    def editor_wire_persistent_listener(
        hierarchy_path: str,
        event_path: str,
        target_path: str,
        method: str,
        arg: str,
    ) -> dict[str, Any]:
        """Wire a string-mode persistent listener from a UnityEvent to
        a method on another component.

        Wraps the published ``UnityEventTools.AddStringPersistentListener``
        entry point.  The bridge resolves the source component carrying
        the named event field on *hierarchy_path*, the target component
        on *target_path* with a void ``method(string)`` overload, and
        adds a string-mode listener bound to *arg*.  Idempotent: an
        existing listener with matching target / method / mode / arg
        results in a no-op success response.

        The canonical use case is wiring a UI control's event to
        ``UdonBehaviour.SendCustomEvent`` so a string event name fires
        on UdonSharp without writing a tiny editor script.

        Example::

            editor_wire_persistent_listener(
                hierarchy_path="/UI/Slider",
                event_path="onValueChanged",
                target_path="/Logic/UdonController",
                method="SendCustomEvent",
                arg="OnSliderChanged",
            )

        String mode only — ``mode`` is intentionally absent from the
        signature so the contract stays additive when void / int /
        float / bool / object modes are introduced later.

        Args:
            hierarchy_path: Hierarchy path of the source GameObject
                (the one whose UnityEvent is being wired *from*).
            event_path: Name of the UnityEvent field/property on a
                component of the source GameObject (e.g.
                ``"onValueChanged"``).
            target_path: Hierarchy path of the target GameObject (the
                component whose method gets invoked).
            method: Method name on a component of the target with a
                ``void method(string)`` signature (e.g.
                ``"SendCustomEvent"`` on ``UdonBehaviour``).
            arg: String argument bound at edit time and supplied to
                the method on every invocation.

        Returns:
            The bridge envelope.
        """
        return send_action(
            action="editor_wire_persistent_listener",
            hierarchy_path=hierarchy_path,
            event_path=event_path,
            target_path=target_path,
            method=method,
            arg=arg,
        )
