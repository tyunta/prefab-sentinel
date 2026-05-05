"""Value-pinned assertions for mid-tier mutmut survived modules (issue #158).

Scope: the six mid-tier survived modules listed below; ``serialized_object``
modules are explicitly excluded so this file does not overlap with the
issue #151 coverage file.  Editor-bridge coverage is intentionally
deferred (Non-Goals §): the dependency on a live Unity Editor places it
outside unit-test scope.

Modules in scope:

* ``prefab_sentinel.mcp_helpers``
* ``prefab_sentinel.services.runtime_validation.service``
* ``prefab_sentinel.material_inspector_variant``
* ``prefab_sentinel.symbol_tree_builder``  (the ``StructureResult``
  duplicate-fileID diagnostic surfaced by ``structure_validator``,
  which is what the symbol-tree walk relies on)
* ``prefab_sentinel.csharp_fields_resolve``
* ``prefab_sentinel.smoke_batch_runner``  (filter parsing in
  ``smoke_batch_case``, shared by the runner)

Modules excluded by this batch:

* ``services.serialized_object.*`` — covered by issue #151.
* ``editor_bridge`` — Editor-dependent; intentionally deferred.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.mcp_helpers import read_asset, resolve_component_name
from prefab_sentinel.services.runtime_validation import RuntimeValidationService
from prefab_sentinel.services.runtime_validation.classification import (
    assert_no_critical_errors,
    classify_errors,
)
from prefab_sentinel.structure_validator import validate_structure
from prefab_sentinel.symbol_tree import SymbolKind, SymbolNode
from prefab_sentinel.unity_yaml_parser import CLASS_ID_MONOBEHAVIOUR


class McpHelpersReadAssetTests(unittest.TestCase):
    """``read_asset`` raises documented exception types with the input path
    embedded in the message."""

    def test_missing_path_raises_filenotfounderror_with_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            # Use an absolute path inside the temp project root so the
            # containment guard accepts it; ``is_file()`` is False so
            # ``read_asset`` raises FileNotFoundError.
            missing_abs = str(root / "Assets" / "Missing.asset")
            with self.assertRaises(FileNotFoundError) as ctx:
                read_asset(missing_abs, root)
            self.assertIn("Missing.asset", str(ctx.exception))

    def test_undecodable_path_raises_unicodedecodeerror(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            target = root / "Assets" / "Bin.asset"
            target.write_bytes(b"\xff\xfe binary \x80\x90")
            # ``decode_text_file`` raises UnicodeDecodeError on this
            # input; ``read_asset`` propagates it without wrapping.
            with self.assertRaises(UnicodeDecodeError) as ctx:
                read_asset(str(target), root)
            # Pin the exception's encoding name so a regression that
            # swallowed the original exception and re-raised a generic
            # one would be caught.
            self.assertEqual("utf-8", ctx.exception.encoding)


class McpHelpersResolveComponentNameTests(unittest.TestCase):
    """``resolve_component_name`` raises with a documented diagnostic
    message for MonoBehaviours missing ``script_name`` and passes
    through the type name otherwise."""

    def test_monobehaviour_missing_script_name_raises_value_error(self) -> None:
        node = SymbolNode(
            kind=SymbolKind.COMPONENT,
            name="Component",
            file_id="11400000",
            class_id=CLASS_ID_MONOBEHAVIOUR,
            script_name="",
        )
        with self.assertRaises(ValueError) as ctx:
            resolve_component_name(node)
        message = str(ctx.exception)
        self.assertIn("MonoBehaviour", message)
        self.assertIn("11400000", message)
        self.assertIn("--project-root", message)

    def test_monobehaviour_with_script_name_returns_script_name(self) -> None:
        node = SymbolNode(
            kind=SymbolKind.COMPONENT,
            name="Component",
            file_id="11400000",
            class_id=CLASS_ID_MONOBEHAVIOUR,
            script_name="MyController",
        )
        self.assertEqual("MyController", resolve_component_name(node))

    def test_non_monobehaviour_passes_node_name_through(self) -> None:
        # A Transform component (class_id "4") returns the node name
        # rather than the script_name (which is empty for builtin types).
        node = SymbolNode(
            kind=SymbolKind.COMPONENT,
            name="Transform",
            file_id="11400000",
            class_id="4",
        )
        self.assertEqual("Transform", resolve_component_name(node))


class RuntimeValidationServiceWrapperPassthroughTests(unittest.TestCase):
    """The service-level ``classify_errors`` and ``assert_no_critical_errors``
    delegate to the pure functions; the wrapper output equals the pure
    function output by full equality (including data payload and
    severity)."""

    def test_classify_errors_wrapper_equals_pure_function(self) -> None:
        svc = RuntimeValidationService()
        log_lines = [
            "Broken PPtr in file Foo",
            "NullReferenceException in UdonBehaviour.X",
        ]
        wrapped = svc.classify_errors(log_lines)
        pure = classify_errors(log_lines)
        self.assertEqual(pure.code, wrapped.code)
        self.assertEqual(pure.severity, wrapped.severity)
        self.assertEqual(pure.success, wrapped.success)
        self.assertEqual(pure.message, wrapped.message)
        self.assertEqual(pure.data, wrapped.data)

    def test_assert_no_critical_errors_wrapper_equals_pure_function(self) -> None:
        svc = RuntimeValidationService()
        classification = svc.classify_errors(
            ["NullReferenceException in UdonBehaviour.X"]
        )
        wrapped = svc.assert_no_critical_errors(classification)
        pure = assert_no_critical_errors(classification)
        self.assertEqual(pure.code, wrapped.code)
        self.assertEqual(pure.severity, wrapped.severity)
        self.assertEqual(pure.success, wrapped.success)
        self.assertEqual(pure.data, wrapped.data)


class MaterialInspectorVariantParserTests(unittest.TestCase):
    """``_iter_modifications`` produces documented per-slot mappings.
    A multi-block YAML carrying material slot overrides plus an inherited
    slot proves the parser distinguishes overridden vs inherited."""

    def test_iter_modifications_pins_per_slot_mapping(self) -> None:
        from prefab_sentinel.material_inspector_variant import (  # noqa: PLC0415
            _iter_modifications,
            _parse_material_overrides,
        )

        text = (
            "%YAML 1.1\n"
            "--- !u!1001 &123\n"
            "PrefabInstance:\n"
            "  m_Modification:\n"
            "    m_Modifications:\n"
            "    - target: {fileID: 100100000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}\n"
            "      propertyPath: m_Materials.Array.data[0]\n"
            "      value:\n"
            "      objectReference: {fileID: 21300000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 2}\n"
            "    - target: {fileID: 100100000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}\n"
            "      propertyPath: m_Name\n"
            "      value: Renamed\n"
            "      objectReference: {fileID: 0}\n"
        )

        entries = _iter_modifications(text)
        self.assertEqual(2, len(entries))
        slot_entry = next(
            e for e in entries if e.property_path.startswith("m_Materials.")
        )
        self.assertEqual("100100000", slot_entry.target_file_id)
        self.assertEqual(
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            slot_entry.object_reference_guid,
        )

        # ``_parse_material_overrides`` populates the (target, slot) map
        # only for entries whose path matches the slot pattern; the
        # ``m_Name`` entry must be skipped (inherited slot semantics).
        overrides: dict[tuple[str, int], str] = {}
        _parse_material_overrides(text, overrides)
        self.assertEqual(
            {
                ("100100000", 0): "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            },
            overrides,
        )


class StructureValidatorDuplicateFileIdTests(unittest.TestCase):
    """``validate_structure`` reports duplicate fileIDs with the
    documented diagnostic shape (path / location / detail / evidence)."""

    def test_duplicate_file_id_emits_documented_diagnostic(self) -> None:
        text = (
            "--- !u!1 &100100000\nGameObject:\n  m_Name: A\n"
            "--- !u!1 &100100000\nGameObject:\n  m_Name: B\n"
        )
        result = validate_structure(text, "Assets/Foo.prefab")
        self.assertEqual(1, len(result.duplicate_file_ids))
        diag = result.duplicate_file_ids[0]
        self.assertEqual("Assets/Foo.prefab", diag.path)
        self.assertEqual("fileID:100100000", diag.location)
        self.assertIn("Duplicate fileID", diag.detail)
        self.assertIn("100100000", diag.detail)
        self.assertEqual("{fileID: 100100000}", diag.evidence)

    def test_unique_file_ids_emit_no_duplicate_diagnostic(self) -> None:
        text = (
            "--- !u!1 &100100000\nGameObject:\n  m_Name: A\n"
            "--- !u!1 &100100001\nGameObject:\n  m_Name: B\n"
        )
        result = validate_structure(text, "Assets/Foo.prefab")
        self.assertEqual([], result.duplicate_file_ids)


class CSharpFieldsInheritanceTests(unittest.TestCase):
    """``resolve_inherited_fields`` returns a unioned field set with
    ``source_class`` set per origin class (base before derived)."""

    def test_inheritance_union_pins_source_class_per_field(self) -> None:
        from prefab_sentinel.csharp_fields_resolve import (  # noqa: PLC0415
            resolve_inherited_fields,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "Assets").mkdir()
            base_cs = root / "Assets" / "Base.cs"
            base_cs.write_text(
                "using UnityEngine;\n"
                "public class Base : MonoBehaviour {\n"
                "    [SerializeField] private int baseField = 0;\n"
                "}\n",
                encoding="utf-8",
            )
            (root / "Assets" / "Base.cs.meta").write_text(
                "fileFormatVersion: 2\nguid: 1111111111111111111111111111aaaa\n",
                encoding="utf-8",
            )
            derived_cs = root / "Assets" / "Derived.cs"
            derived_cs.write_text(
                "using UnityEngine;\n"
                "public class Derived : Base {\n"
                "    [SerializeField] private int derivedField = 0;\n"
                "}\n",
                encoding="utf-8",
            )
            (root / "Assets" / "Derived.cs.meta").write_text(
                "fileFormatVersion: 2\nguid: 2222222222222222222222222222bbbb\n",
                encoding="utf-8",
            )
            (root / "Assets").mkdir(exist_ok=True)

            fields = resolve_inherited_fields(
                "2222222222222222222222222222bbbb",
                project_root=root,
            )

        names = [(f.name, f.source_class) for f in fields]
        self.assertIn(("baseField", "Base"), names)
        self.assertIn(("derivedField", "Derived"), names)
        # Base fields appear before derived fields in the result.
        base_index = names.index(("baseField", "Base"))
        derived_index = names.index(("derivedField", "Derived"))
        self.assertLess(base_index, derived_index)


class SmokeBatchTargetFilterParsingTests(unittest.TestCase):
    """``_resolve_targets`` parses each documented filter expression."""

    def test_all_expands_to_avatar_then_world(self) -> None:
        from prefab_sentinel.smoke_batch_case import _resolve_targets  # noqa: PLC0415

        self.assertEqual(["avatar", "world"], _resolve_targets(["all"]))

    def test_explicit_targets_pass_through_in_order(self) -> None:
        from prefab_sentinel.smoke_batch_case import _resolve_targets  # noqa: PLC0415

        self.assertEqual(["avatar"], _resolve_targets(["avatar"]))
        self.assertEqual(["world"], _resolve_targets(["world"]))
        self.assertEqual(
            ["world", "avatar"], _resolve_targets(["world", "avatar"])
        )

    def test_duplicate_targets_collapse_with_first_occurrence_winning(
        self,
    ) -> None:
        from prefab_sentinel.smoke_batch_case import _resolve_targets  # noqa: PLC0415

        self.assertEqual(
            ["avatar", "world"],
            _resolve_targets(["avatar", "world", "avatar"]),
        )

    def test_all_followed_by_explicit_does_not_re_add(self) -> None:
        from prefab_sentinel.smoke_batch_case import _resolve_targets  # noqa: PLC0415

        # ``all`` expands to ``[avatar, world]``; subsequent ``avatar``
        # is deduped.
        self.assertEqual(
            ["avatar", "world"],
            _resolve_targets(["all", "avatar"]),
        )

    def test_empty_list_returns_empty(self) -> None:
        from prefab_sentinel.smoke_batch_case import _resolve_targets  # noqa: PLC0415

        self.assertEqual([], _resolve_targets([]))


if __name__ == "__main__":
    unittest.main()
