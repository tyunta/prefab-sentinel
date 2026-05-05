"""Branch-coverage uplift for ``prefab_sentinel.services.serialized_object.scene_component_ops`` (issue #188).

Pins each scene component validator branch and the preview shape it
produces.  Every ``return`` in ``validate_scene_add_component_op`` and
``validate_scene_remove_component_op`` is hit by one row.

Branches in the target module not covered: none.  The two functions
under test together comprise the full module surface, and each
diagnostic-emitting path plus the success path is reached.
"""

from __future__ import annotations

import unittest
from typing import Any

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.handles import SCENE_HANDLE
from prefab_sentinel.services.serialized_object.scene_component_ops import (
    validate_scene_add_component_op,
    validate_scene_remove_component_op,
)
from prefab_sentinel.services.serialized_object.scene_dispatch import _SceneContext


def _make_ctx(
    *,
    scene_initialized: bool = True,
    extra_handles: dict[str, str] | None = None,
) -> _SceneContext:
    handles: dict[str, str] = {SCENE_HANDLE: "scene"}
    if extra_handles:
        handles.update(extra_handles)
    return _SceneContext(
        target="Assets/Scene.unity",
        mode="open",
        diagnostics=[],
        preview=[],
        known_handles=handles,
        ops=[],
        scene_initialized=scene_initialized,
    )


def _evidences(diagnostics: list[Diagnostic]) -> list[str]:
    return [d.evidence for d in diagnostics]


class SceneComponentOpsTests(unittest.TestCase):
    """Pin each validator branch by diagnostic detail / preview shape."""

    # --- add_component: schema-error before scene is opened ----------------

    def test_add_component_without_opened_scene_emits_schema_error(self) -> None:
        ctx = _make_ctx(scene_initialized=False)
        op: dict[str, Any] = {"op": "add_component", "target": "$go", "type": "AudioSource"}
        validate_scene_add_component_op(ctx, 0, op, "add_component")
        self.assertEqual(1, len(ctx.diagnostics))
        diag = ctx.diagnostics[0]
        self.assertEqual("schema_error", diag.detail)
        self.assertEqual("add_component requires an opened scene first", diag.evidence)
        self.assertEqual("ops[0].op", diag.location)
        self.assertEqual([], ctx.preview)

    # --- add_component: unknown target handle returns no preview -----------

    def test_add_component_with_unknown_target_returns_no_preview(self) -> None:
        ctx = _make_ctx()
        op = {"op": "add_component", "target": "$missing", "type": "AudioSource"}
        validate_scene_add_component_op(ctx, 0, op, "add_component")
        # require_handle_ref appended a schema_error diagnostic.
        self.assertTrue(any("unknown handle" in d.evidence for d in ctx.diagnostics))
        self.assertEqual([], ctx.preview)

    # --- add_component: missing type emits schema_error --------------------

    def test_add_component_without_type_emits_schema_error(self) -> None:
        ctx = _make_ctx(extra_handles={"go": "game_object"})
        op = {"op": "add_component", "target": "$go"}
        validate_scene_add_component_op(ctx, 0, op, "add_component")
        type_diags = [d for d in ctx.diagnostics if d.location == "ops[0].type"]
        self.assertEqual(1, len(type_diags))
        self.assertEqual("schema_error", type_diags[0].detail)
        self.assertEqual("type is required for add_component", type_diags[0].evidence)
        self.assertEqual([], ctx.preview)

    # --- add_component: invalid result handle returns no preview -----------

    def test_add_component_with_invalid_result_handle_returns_no_preview(
        self,
    ) -> None:
        ctx = _make_ctx(extra_handles={"go": "game_object", "duplicate": "component"})
        op = {
            "op": "add_component",
            "target": "$go",
            "type": "AudioSource",
            # Duplicate result handle => validate_result_handle emits schema_error
            "result": "$duplicate",
        }
        validate_scene_add_component_op(ctx, 0, op, "add_component")
        self.assertTrue(
            any("already defined" in d.evidence for d in ctx.diagnostics),
            f"expected duplicate-handle diagnostic, got {_evidences(ctx.diagnostics)}",
        )
        self.assertEqual([], ctx.preview)

    # --- add_component: success path records component handle --------------

    def test_add_component_records_component_handle_on_success(self) -> None:
        ctx = _make_ctx(extra_handles={"go": "game_object"})
        op = {
            "op": "add_component",
            "target": "$go",
            "type": "AudioSource",
            "result": "$audio",
        }
        validate_scene_add_component_op(ctx, 0, op, "add_component")
        self.assertEqual([], ctx.diagnostics)
        self.assertEqual(1, len(ctx.preview))
        entry = ctx.preview[0]
        self.assertEqual("add_component", entry["op"])
        # add_component before == "(missing)" sentinel.
        self.assertEqual("(missing)", entry["before"])
        self.assertEqual(
            {
                "target": "go",
                "type": "AudioSource",
                "handle": "audio",
                "kind": "component",
            },
            entry["after"],
        )
        self.assertEqual("component", ctx.known_handles["audio"])

    def test_add_component_anonymous_when_no_result_handle(self) -> None:
        ctx = _make_ctx(extra_handles={"go": "game_object"})
        op = {"op": "add_component", "target": "$go", "type": "AudioSource"}
        validate_scene_add_component_op(ctx, 0, op, "add_component")
        self.assertEqual([], ctx.diagnostics)
        self.assertEqual("(anonymous)", ctx.preview[0]["after"]["handle"])

    # --- find_component: accepts both scene and game_object kinds ----------

    def test_find_component_accepts_game_object_kind(self) -> None:
        ctx = _make_ctx(extra_handles={"go": "game_object"})
        op = {
            "op": "find_component",
            "target": "$go",
            "type": "AudioSource",
            "result": "$audio",
        }
        validate_scene_add_component_op(ctx, 0, op, "find_component")
        self.assertEqual([], ctx.diagnostics)
        self.assertEqual({"target": "go"}, ctx.preview[0]["before"])

    def test_find_component_accepts_scene_kind(self) -> None:
        ctx = _make_ctx()
        op = {
            "op": "find_component",
            "target": "$scene",
            "type": "AudioSource",
            "result": "$audio",
        }
        validate_scene_add_component_op(ctx, 0, op, "find_component")
        self.assertEqual([], ctx.diagnostics)

    # --- remove_component: missing target emits diagnostic -----------------

    def test_remove_component_without_target_emits_diagnostic(self) -> None:
        ctx = _make_ctx()
        validate_scene_remove_component_op(ctx, 0, {"op": "remove_component"})
        self.assertEqual(1, len(ctx.diagnostics))
        self.assertEqual("schema_error", ctx.diagnostics[0].detail)
        self.assertEqual([], ctx.preview)

    def test_remove_component_records_preview_entry(self) -> None:
        ctx = _make_ctx(extra_handles={"audio": "component"})
        op = {"op": "remove_component", "target": "$audio"}
        validate_scene_remove_component_op(ctx, 0, op)
        self.assertEqual([], ctx.diagnostics)
        self.assertEqual(1, len(ctx.preview))
        entry = ctx.preview[0]
        self.assertEqual("remove_component", entry["op"])
        self.assertEqual({"handle": "audio", "kind": "component"}, entry["before"])
        self.assertEqual("(removed)", entry["after"])


if __name__ == "__main__":
    unittest.main()
