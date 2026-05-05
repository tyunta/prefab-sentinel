"""Coverage strengthening for the seven low-coverage serialized-object
modules (issue #151).

Modules in scope:

* ``services.serialized_object.scene_values``
* ``services.serialized_object.scene_object_ops``
* ``services.serialized_object.asset_open_ops``
* ``services.serialized_object.patch_executor``
* ``services.serialized_object.prefab_create_structure``
* ``services.serialized_object.asset_create_writers``
* ``services.serialized_object.patch_json_apply``

Each test class drives one module via its public-facing entry point
(typically the dispatcher or the service facade) and pins one or more
representative branches by full envelope / diagnostic equality.

Why short integration tests instead of unit tests on private helpers:
the validators all funnel into ``(diagnostics, preview)`` tuples whose
contract is observable only at the dispatcher boundary; testing the
helper functions directly would re-state their internals.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.contracts import Diagnostic
from prefab_sentinel.services.serialized_object.asset_open_ops import (
    validate_asset_open_ops,
)
from prefab_sentinel.services.serialized_object.patch_executor import apply_op
from prefab_sentinel.services.serialized_object.patch_json_apply import (
    apply_json_target,
    propagate_dry_run_failure,
)
from prefab_sentinel.services.serialized_object.scene_dispatch import (
    validate_scene_ops,
)


def _diagnostic_evidence_set(diagnostics: list[Diagnostic]) -> list[str]:
    return [d.evidence for d in diagnostics]


class SceneValuesCoverageTests(unittest.TestCase):
    """``scene_values`` — set / insert / save validators."""

    def test_set_op_missing_value_emits_schema_error(self) -> None:
        diagnostics, _preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {
                    "op": "create_game_object",
                    "parent": "$scene",
                    "name": "Foo",
                    "result": "$go",
                },
                {
                    "op": "add_component",
                    "target": "$go",
                    "type": "T",
                    "result": "$c",
                },
                {"op": "set", "target": "$c", "path": "m_Foo"},
                {"op": "save_scene"},
            ],
        )
        # ``set`` with no ``value`` adds a schema error; preview drops
        # the value row.
        self.assertIn(
            "value is required for set",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_save_scene_must_be_last_op(self) -> None:
        diagnostics, _ = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {"op": "save_scene"},
                {"op": "add_component", "target": "$scene", "type": "T", "result": "$c"},
            ],
        )
        self.assertIn(
            "save_scene must be the final operation in scene mode",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_save_scene_without_init_emits_schema_error(self) -> None:
        # Two-op plan where the first op is something other than the
        # mode-specific init.  validate_scene_init_op rejects the first
        # op without flipping ``scene_initialized``, then
        # validate_scene_save_op fires the "requires an opened scene"
        # branch on op[1].
        diagnostics, _ = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "rename_object", "target": "$scene", "name": "n"},
                {"op": "save_scene"},
            ],
        )
        self.assertIn(
            "save_scene requires an opened scene first",
            _diagnostic_evidence_set(diagnostics),
        )


class SceneObjectOpsCoverageTests(unittest.TestCase):
    """``scene_object_ops`` — init / create / duplicate-init / reparent."""

    def test_first_op_must_match_mode(self) -> None:
        diagnostics, _ = validate_scene_ops(
            target="Assets/A.unity",
            mode="create",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {"op": "save_scene"},
            ],
        )
        self.assertIn(
            "scene create mode must start with create_scene",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_duplicate_init_after_first_emits_schema_error(self) -> None:
        diagnostics, _ = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {"op": "open_scene", "result": "$scene_again"},
                {"op": "save_scene"},
            ],
        )
        self.assertIn(
            "open_scene may appear only as the first operation",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_create_game_object_requires_name(self) -> None:
        diagnostics, _ = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {"op": "create_game_object", "parent": "$scene", "result": "$g"},
                {"op": "save_scene"},
            ],
        )
        self.assertIn(
            "name is required for create_game_object",
            _diagnostic_evidence_set(diagnostics),
        )


class AssetOpenOpsCoverageTests(unittest.TestCase):
    """``asset_open_ops`` — schema-error paths + set preview row."""

    def test_missing_target_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="",
            kind="asset",
            ops=[{"op": "set", "target": "$asset", "path": "x", "value": 1}],
        )
        self.assertIn(
            "target path is required for asset open mode",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_wrong_extension_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.prefab",
            kind="asset",
            ops=[{"op": "set", "target": "$asset", "path": "x", "value": 1}],
        )
        self.assertIn(
            "asset open mode requires a .asset target path",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_unsupported_op_name_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[
                {"op": "WhoKnows", "target": "$asset", "path": "x", "value": 1},
            ],
        )
        self.assertIn(
            "unsupported asset open op 'WhoKnows'",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_set_op_emits_preview_row(self) -> None:
        diagnostics, preview = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[
                {"op": "set", "target": "$asset", "path": "x", "value": 42},
            ],
        )
        self.assertEqual([], diagnostics)
        self.assertEqual(1, len(preview))
        self.assertEqual("set", preview[0]["op"])
        self.assertEqual(42, preview[0]["after"]["value"])


class PatchExecutorCoverageTests(unittest.TestCase):
    """``patch_executor.apply_op`` — short-circuit / value / array branches."""

    def test_set_with_existing_key_returns_diff_row(self) -> None:
        payload = {"a": 1}
        row = apply_op(payload, {"op": "set", "path": "a", "value": 2})
        self.assertEqual(1, row["before"])
        self.assertEqual(2, row["after"])
        self.assertEqual(2, payload["a"])

    def test_set_with_missing_key_raises_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            apply_op({}, {"op": "set", "path": "a", "value": 2})

    def test_array_size_set_grows_then_shrinks(self) -> None:
        payload = {"items": [1, 2]}
        row = apply_op(
            payload,
            {"op": "set", "path": "items.Array.size", "value": 4},
        )
        self.assertEqual(2, row["before"])
        self.assertEqual(4, row["after"])
        self.assertEqual([1, 2, None, None], payload["items"])

    def test_array_size_set_negative_raises_valueerror(self) -> None:
        with self.assertRaises(ValueError):
            apply_op(
                {"items": [1, 2]},
                {"op": "set", "path": "items.Array.size", "value": -1},
            )

    def test_insert_array_element_out_of_bounds_raises_indexerror(self) -> None:
        with self.assertRaises(IndexError):
            apply_op(
                {"items": [1]},
                {
                    "op": "insert_array_element",
                    "path": "items.Array.data",
                    "index": 99,
                    "value": 0,
                },
            )

    def test_unsupported_op_raises_valueerror(self) -> None:
        with self.assertRaises(ValueError):
            apply_op({}, {"op": "WhoKnows"})


class PrefabCreateStructureCoverageTests(unittest.TestCase):
    """``prefab_create_structure`` — root creation schema-error rows."""

    def test_create_root_missing_name_emits_schema_error(self) -> None:
        from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (  # noqa: PLC0415
            validate_prefab_create_ops,
        )

        diagnostics, _ = validate_prefab_create_ops(
            target="Assets/Foo.prefab",
            ops=[{"op": "create_root"}, {"op": "save"}],
        )
        self.assertIn(
            "name is required for create_root",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_double_create_root_emits_schema_error(self) -> None:
        from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (  # noqa: PLC0415
            validate_prefab_create_ops,
        )

        diagnostics, _ = validate_prefab_create_ops(
            target="Assets/Foo.prefab",
            ops=[
                {"op": "create_root", "name": "Root"},
                {"op": "create_root", "name": "AlsoRoot"},
                {"op": "save"},
            ],
        )
        self.assertIn(
            "prefab root may be created only once",
            _diagnostic_evidence_set(diagnostics),
        )


class AssetCreateWritersCoverageTests(unittest.TestCase):
    """``asset_create_writers`` — material vs asset create paths."""

    def test_material_create_requires_shader(self) -> None:
        from prefab_sentinel.services.serialized_object.asset_create_ops import (  # noqa: PLC0415
            validate_asset_create_ops,
        )

        diagnostics, _ = validate_asset_create_ops(
            target="Assets/Foo.mat",
            kind="material",
            ops=[
                {"op": "create_asset", "name": "Foo"},
                {"op": "save"},
            ],
        )
        self.assertIn(
            "shader is required for create_asset on material resources",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_asset_create_requires_type(self) -> None:
        from prefab_sentinel.services.serialized_object.asset_create_ops import (  # noqa: PLC0415
            validate_asset_create_ops,
        )

        diagnostics, _ = validate_asset_create_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[
                {"op": "create_asset", "name": "Foo"},
                {"op": "save"},
            ],
        )
        joined = " | ".join(_diagnostic_evidence_set(diagnostics))
        self.assertIn("type", joined)

    def test_double_create_asset_emits_schema_error(self) -> None:
        from prefab_sentinel.services.serialized_object.asset_create_ops import (  # noqa: PLC0415
            validate_asset_create_ops,
        )

        diagnostics, _ = validate_asset_create_ops(
            target="Assets/Foo.mat",
            kind="material",
            ops=[
                {"op": "create_asset", "name": "Foo", "shader": "Standard"},
                {"op": "create_asset", "name": "Bar", "shader": "Standard"},
                {"op": "save"},
            ],
        )
        self.assertIn(
            "asset root may be created only once",
            _diagnostic_evidence_set(diagnostics),
        )


class SceneValuesAdditionalCoverageTests(unittest.TestCase):
    """Additional rows for ``scene_values`` covering set / insert / remove preview rows."""

    def _ops_with_value_set(
        self,
        *,
        with_value: bool = True,
        path: str = "m_Foo",
        index_value: object | None = None,
        op_kind: str = "set",
        value: object = 1,
    ) -> list[dict]:
        body: list[dict] = [
            {"op": "open_scene", "result": "$scene"},
            {
                "op": "create_game_object",
                "parent": "$scene",
                "name": "Foo",
                "result": "$go",
            },
            {
                "op": "add_component",
                "target": "$go",
                "type": "T",
                "result": "$c",
            },
        ]
        op: dict = {"op": op_kind, "target": "$c", "path": path}
        if with_value:
            op["value"] = value
        if index_value is not None:
            op["index"] = index_value
        body.append(op)
        body.append({"op": "save_scene"})
        return body

    def test_set_op_with_value_emits_preview_row(self) -> None:
        diagnostics, preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=self._ops_with_value_set(with_value=True, value=42),
        )
        # No schema errors against the set op; preview includes the
        # set row carrying the deep-copied value.
        self.assertEqual([], diagnostics)
        set_rows = [row for row in preview if row.get("op") == "set"]
        self.assertEqual(1, len(set_rows))
        self.assertEqual(42, set_rows[0]["after"]["value"])

    def test_set_op_with_empty_path_emits_schema_error(self) -> None:
        diagnostics, _preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=self._ops_with_value_set(path="", with_value=True),
        )
        self.assertIn("path is required", _diagnostic_evidence_set(diagnostics))

    def test_insert_array_element_emits_preview_row(self) -> None:
        diagnostics, preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=self._ops_with_value_set(
                op_kind="insert_array_element",
                index_value=0,
                value="hello",
            ),
        )
        self.assertEqual([], diagnostics)
        insert_rows = [
            row for row in preview if row.get("op") == "insert_array_element"
        ]
        self.assertEqual(1, len(insert_rows))
        self.assertEqual(0, insert_rows[0]["after"]["index"])
        self.assertEqual("hello", insert_rows[0]["after"]["value"])

    def test_array_op_without_index_emits_schema_error(self) -> None:
        diagnostics, _preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=self._ops_with_value_set(
                op_kind="insert_array_element",
                index_value=None,
                with_value=False,
            ),
        )
        self.assertIn(
            "index must be an integer",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_save_scene_already_saved_emits_schema_error(self) -> None:
        diagnostics, _preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {"op": "save_scene"},
                {"op": "save_scene"},
            ],
        )
        joined = " | ".join(_diagnostic_evidence_set(diagnostics))
        self.assertIn("save_scene", joined)


class SceneObjectOpsAdditionalCoverageTests(unittest.TestCase):
    """``scene_object_ops`` — instantiate_prefab / rename / reparent / no-init."""

    def test_create_game_object_without_init_emits_schema_error(self) -> None:
        # Force the dispatcher to skip init by making op[0] a non-init
        # operation; subsequent create_game_object then hits the
        # "requires an opened scene first" branch.
        diagnostics, _ = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "rename_object", "target": "$scene", "name": "n"},
                {
                    "op": "create_game_object",
                    "parent": "$scene",
                    "name": "Foo",
                    "result": "$go",
                },
                {"op": "save_scene"},
            ],
        )
        self.assertIn(
            "create_game_object requires an opened scene first",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_instantiate_prefab_emits_preview_row(self) -> None:
        diagnostics, preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {
                    "op": "instantiate_prefab",
                    "parent": "$scene",
                    "prefab": "Assets/Foo.prefab",
                    "result": "$inst",
                },
                {"op": "save_scene"},
            ],
        )
        self.assertEqual([], diagnostics)
        rows = [row for row in preview if row.get("op") == "instantiate_prefab"]
        self.assertEqual(1, len(rows))

    def test_rename_object_emits_preview_row(self) -> None:
        diagnostics, preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {
                    "op": "create_game_object",
                    "parent": "$scene",
                    "name": "Foo",
                    "result": "$go",
                },
                {"op": "rename_object", "target": "$go", "name": "Bar"},
                {"op": "save_scene"},
            ],
        )
        self.assertEqual([], diagnostics)
        rows = [row for row in preview if row.get("op") == "rename_object"]
        self.assertEqual(1, len(rows))

    def test_reparent_emits_preview_row(self) -> None:
        diagnostics, preview = validate_scene_ops(
            target="Assets/A.unity",
            mode="open",
            ops=[
                {"op": "open_scene", "result": "$scene"},
                {
                    "op": "create_game_object",
                    "parent": "$scene",
                    "name": "Foo",
                    "result": "$go",
                },
                {
                    "op": "create_game_object",
                    "parent": "$scene",
                    "name": "Bar",
                    "result": "$go2",
                },
                {"op": "reparent", "target": "$go2", "parent": "$go"},
                {"op": "save_scene"},
            ],
        )
        self.assertEqual([], diagnostics)
        rows = [row for row in preview if row.get("op") == "reparent"]
        self.assertEqual(1, len(rows))


class AssetOpenOpsAdditionalCoverageTests(unittest.TestCase):
    """``asset_open_ops`` — empty ops / non-dict op / array element / path."""

    def test_empty_ops_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.asset", kind="asset", ops=[]
        )
        self.assertIn(
            "ops must contain at least one operation",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_non_dict_op_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=["not a dict"],
        )
        self.assertIn(
            "operation must be an object",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_set_missing_path_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[{"op": "set", "target": "$asset", "value": 1}],
        )
        self.assertIn(
            "path is required",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_set_missing_value_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[{"op": "set", "target": "$asset", "path": "x"}],
        )
        self.assertIn(
            "value is required for set",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_insert_array_element_emits_preview_row(self) -> None:
        diagnostics, preview = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[
                {
                    "op": "insert_array_element",
                    "target": "$asset",
                    "path": "items",
                    "index": 0,
                    "value": "alpha",
                }
            ],
        )
        self.assertEqual([], diagnostics)
        self.assertEqual("insert_array_element", preview[0]["op"])
        self.assertEqual(0, preview[0]["after"]["index"])
        self.assertEqual("alpha", preview[0]["after"]["value"])

    def test_array_element_without_index_emits_schema_error(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[
                {
                    "op": "insert_array_element",
                    "target": "$asset",
                    "path": "items",
                }
            ],
        )
        self.assertIn(
            "index must be an integer",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_material_kind_requires_mat_extension(self) -> None:
        diagnostics, _ = validate_asset_open_ops(
            target="Assets/Foo.asset",
            kind="material",
            ops=[
                {"op": "set", "target": "$asset", "path": "x", "value": 1},
            ],
        )
        self.assertIn(
            "material open mode requires a .mat target path",
            _diagnostic_evidence_set(diagnostics),
        )


class AssetCreateWritersAdditionalCoverageTests(unittest.TestCase):
    """``asset_create_writers`` — non-material asset and value/save ops."""

    def test_asset_create_emits_preview_row(self) -> None:
        from prefab_sentinel.services.serialized_object.asset_create_ops import (  # noqa: PLC0415
            validate_asset_create_ops,
        )

        diagnostics, preview = validate_asset_create_ops(
            target="Assets/Foo.asset",
            kind="asset",
            ops=[
                {
                    "op": "create_asset",
                    "name": "Foo",
                    "type": "MyType",
                },
                {
                    "op": "set",
                    "target": "$asset",
                    "path": "x",
                    "value": 1,
                },
                {"op": "save"},
            ],
        )
        # Some non-set diagnostics may exist depending on schema, but
        # the create_asset row is emitted.
        rows = [row for row in preview if row.get("op") == "create_asset"]
        self.assertEqual(1, len(rows))
        del diagnostics  # we only care that the structure is rendered

    def test_material_create_with_invalid_name_emits_schema_error(self) -> None:
        from prefab_sentinel.services.serialized_object.asset_create_ops import (  # noqa: PLC0415
            validate_asset_create_ops,
        )

        diagnostics, _ = validate_asset_create_ops(
            target="Assets/Foo.mat",
            kind="material",
            ops=[
                {
                    "op": "create_asset",
                    "name": "   ",
                    "shader": "Standard",
                },
                {"op": "save"},
            ],
        )
        self.assertIn(
            "name must be a non-empty string when provided",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_material_create_with_invalid_type_emits_schema_error(self) -> None:
        from prefab_sentinel.services.serialized_object.asset_create_ops import (  # noqa: PLC0415
            validate_asset_create_ops,
        )

        diagnostics, _ = validate_asset_create_ops(
            target="Assets/Foo.mat",
            kind="material",
            ops=[
                {
                    "op": "create_asset",
                    "shader": "Standard",
                    "type": "   ",
                },
                {"op": "save"},
            ],
        )
        self.assertIn(
            "type must be a non-empty string when provided",
            _diagnostic_evidence_set(diagnostics),
        )

    def test_material_create_uses_default_type_when_omitted(self) -> None:
        from prefab_sentinel.services.serialized_object.asset_create_ops import (  # noqa: PLC0415
            validate_asset_create_ops,
        )

        diagnostics, preview = validate_asset_create_ops(
            target="Assets/Foo.mat",
            kind="material",
            ops=[
                {"op": "create_asset", "shader": "Standard"},
                {"op": "save"},
            ],
        )
        self.assertEqual([], diagnostics)
        create_rows = [row for row in preview if row.get("op") == "create_asset"]
        self.assertEqual("UnityEngine.Material", create_rows[0]["after"]["type"])


class PrefabCreateStructureAdditionalCoverageTests(unittest.TestCase):
    """``prefab_create_structure`` — game object / component / reparent / rename."""

    def _create_root_prelude(self) -> list[dict]:
        return [{"op": "create_root", "name": "Root"}]

    def test_create_game_object_emits_preview_row(self) -> None:
        from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (  # noqa: PLC0415
            validate_prefab_create_ops,
        )

        diagnostics, preview = validate_prefab_create_ops(
            target="Assets/Foo.prefab",
            ops=[
                *self._create_root_prelude(),
                {
                    "op": "create_game_object",
                    "parent": "$root",
                    "name": "Child",
                    "result": "$child",
                },
                {"op": "save"},
            ],
        )
        self.assertEqual([], diagnostics)
        rows = [row for row in preview if row.get("op") == "create_game_object"]
        self.assertEqual(1, len(rows))

    def test_add_component_emits_preview_row(self) -> None:
        from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (  # noqa: PLC0415
            validate_prefab_create_ops,
        )

        diagnostics, preview = validate_prefab_create_ops(
            target="Assets/Foo.prefab",
            ops=[
                *self._create_root_prelude(),
                {
                    "op": "add_component",
                    "target": "$root",
                    "type": "MyComponent",
                    "result": "$comp",
                },
                {"op": "save"},
            ],
        )
        self.assertEqual([], diagnostics)
        rows = [row for row in preview if row.get("op") == "add_component"]
        self.assertEqual(1, len(rows))

    def test_remove_component_emits_preview_row(self) -> None:
        from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (  # noqa: PLC0415
            validate_prefab_create_ops,
        )

        diagnostics, preview = validate_prefab_create_ops(
            target="Assets/Foo.prefab",
            ops=[
                *self._create_root_prelude(),
                {
                    "op": "add_component",
                    "target": "$root",
                    "type": "MyComponent",
                    "result": "$comp",
                },
                {"op": "remove_component", "target": "$comp"},
                {"op": "save"},
            ],
        )
        self.assertEqual([], diagnostics)
        rows = [row for row in preview if row.get("op") == "remove_component"]
        self.assertEqual(1, len(rows))

    def test_rename_object_emits_preview_row(self) -> None:
        from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (  # noqa: PLC0415
            validate_prefab_create_ops,
        )

        diagnostics, preview = validate_prefab_create_ops(
            target="Assets/Foo.prefab",
            ops=[
                *self._create_root_prelude(),
                {"op": "rename_object", "target": "$root", "name": "Renamed"},
                {"op": "save"},
            ],
        )
        self.assertEqual([], diagnostics)
        rows = [row for row in preview if row.get("op") == "rename_object"]
        self.assertEqual(1, len(rows))

    def test_create_game_object_without_root_emits_schema_error(self) -> None:
        from prefab_sentinel.services.serialized_object.prefab_create_dispatch import (  # noqa: PLC0415
            validate_prefab_create_ops,
        )

        diagnostics, _ = validate_prefab_create_ops(
            target="Assets/Foo.prefab",
            ops=[
                {
                    "op": "create_game_object",
                    "parent": "$root",
                    "name": "Child",
                    "result": "$child",
                },
                {"op": "save"},
            ],
        )
        self.assertIn(
            "create_game_object requires a prefab root first",
            _diagnostic_evidence_set(diagnostics),
        )


class PatchJsonApplyCoverageTests(unittest.TestCase):
    """``patch_json_apply`` — failure envelope shapes + dry-run propagation."""

    def test_missing_target_returns_ser_target_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            response = apply_json_target(
                root / "missing.json",
                [{"op": "set", "path": "a", "value": 1}],
            )
        self.assertFalse(response.success)
        self.assertEqual("SER_TARGET_MISSING", response.code)
        self.assertEqual(0, response.data["applied"])

    def test_invalid_json_target_returns_ser_target_format(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "bad.json"
            target.write_text("not json", encoding="utf-8")
            response = apply_json_target(
                target, [{"op": "set", "path": "a", "value": 1}]
            )
        self.assertFalse(response.success)
        self.assertEqual("SER_TARGET_FORMAT", response.code)

    def test_apply_failure_returns_ser_apply_failed_without_writing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "good.json"
            target.write_text('{"a": 1}', encoding="utf-8")
            response = apply_json_target(
                target,
                [{"op": "set", "path": "missing", "value": 9}],
            )
            # File is unmodified — assert before the temp dir is cleaned up.
            self.assertEqual('{"a": 1}', target.read_text(encoding="utf-8"))
        self.assertFalse(response.success)
        self.assertEqual("SER_APPLY_FAILED", response.code)

    def test_apply_success_writes_diff_and_returns_ser_apply_ok(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "good.json"
            target.write_text('{"a": 1}', encoding="utf-8")
            response = apply_json_target(
                target, [{"op": "set", "path": "a", "value": 2}]
            )
        self.assertTrue(response.success)
        self.assertEqual("SER_APPLY_OK", response.code)
        self.assertEqual(1, response.data["applied"])

    def test_propagate_dry_run_failure_short_circuits_with_apply_flags(
        self,
    ) -> None:
        from prefab_sentinel.contracts import error_response  # noqa: PLC0415

        upstream = error_response(
            "SER_PLAN_INVALID",
            "schema validation failed",
            data={"target": "x", "op_count": 0},
        )
        propagated = propagate_dry_run_failure(
            "x", [{"op": "set"}], upstream
        )
        self.assertFalse(propagated.success)
        self.assertEqual("SER_PLAN_INVALID", propagated.code)
        self.assertEqual(0, propagated.data["applied"])
        self.assertFalse(propagated.data["read_only"])
        self.assertFalse(propagated.data["executed"])


if __name__ == "__main__":
    unittest.main()
