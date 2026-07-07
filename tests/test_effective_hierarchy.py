from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_transform

SOURCE_GUID = "11111111222222223333333344444444"
SECOND_SOURCE_GUID = "55555555666666667777777788888888"
MISSING_GUID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _write_prefab(root: Path, name: str, guid: str, text: str) -> Path:
    assets = root / "Assets"
    assets.mkdir(parents=True, exist_ok=True)
    prefab_path = assets / name
    prefab_path.write_text(text, encoding="utf-8")
    prefab_path.with_suffix(prefab_path.suffix + ".meta").write_text(
        f"fileFormatVersion: 2\nguid: {guid}\n",
        encoding="utf-8",
    )
    return prefab_path


def _load_builder() -> Any:
    try:
        from prefab_sentinel.effective_hierarchy import build_effective_hierarchy
    except ImportError as exc:
        raise AssertionError(
            "expected build_effective_hierarchy to return expanded saved-YAML "
            f"hierarchy data; observed missing import: {exc}"
        ) from exc
    return build_effective_hierarchy


def _source_prefab(root_name: str = "NestedRoot") -> str:
    return (
        YAML_HEADER
        + make_gameobject("100", root_name, ["200"])
        + make_transform("200", "100", children_file_ids=["201"])
        + make_gameobject("101", "NestedLeaf", ["201"])
        + make_transform("201", "101", father_file_id="200")
    )


def _host_prefab(*instances: str) -> str:
    return (
        YAML_HEADER
        + make_gameobject("1000", "HostRoot", ["2000"])
        + make_transform("2000", "1000")
        + "".join(instances)
    )


def _prefab_instance(
    instance_file_id: str,
    source_guid: str,
    *,
    parent_transform: str = "2000",
    modifications: list[tuple[str, str, str]] | None = None,
) -> str:
    if modifications:
        modification_lines = "\n".join(
            "\n".join(
                [
                    f"    - target: {{fileID: {target_file_id}, guid: {source_guid}, type: 3}}",
                    f"      propertyPath: {property_path}",
                    f"      value: {value}",
                    "      objectReference: {fileID: 0}",
                ]
            )
            for target_file_id, property_path, value in modifications
        )
        modifications_block = f"    m_Modifications:\n{modification_lines}\n"
    else:
        modifications_block = "    m_Modifications: []\n"
    return (
        f"--- !u!1001 &{instance_file_id}\n"
        "PrefabInstance:\n"
        "  m_Modification:\n"
        f"    m_TransformParent: {{fileID: {parent_transform}}}\n"
        f"{modifications_block}"
        f"  m_SourcePrefab: {{fileID: 100100000, guid: {source_guid}, type: 3}}\n"
    )


class EffectiveHierarchyExpansionTests(unittest.TestCase):
    def test_nested_prefab_children_include_origin_and_override_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Nested.prefab", SOURCE_GUID, _source_prefab())
            host = _write_prefab(
                root,
                "Host.prefab",
                SECOND_SOURCE_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[("100", "m_Name", "HostNestedRoot")],
                    )
                ),
            )
            build_effective_hierarchy = _load_builder()

            result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host.read_text(encoding="utf-8")
            )

        roots = result.to_dict()["roots"]
        self.assertEqual(
            ["HostRoot"],
            [node["name"] for node in roots],
            msg=f"expected one host root; observed roots={roots!r}",
        )
        nested = roots[0]["children"][0]
        leaf = nested["children"][0]
        origin = nested["origin"]
        self.assertEqual(
            (
                "HostNestedRoot",
                "NestedLeaf",
                "Assets/Nested.prefab",
                "100",
                "9000",
                "Assets/Host.prefab",
                ["m_Name"],
                "HostRoot/HostNestedRoot",
            ),
            (
                nested["name"],
                leaf["name"],
                origin["source"]["asset_path"],
                origin["source"]["file_id"],
                origin["nested_instance"]["file_id"],
                origin["override_host"]["asset_path"],
                origin["override_host"]["property_paths"],
                origin["effective"]["symbol_path"],
            ),
            msg=f"expanded nested metadata did not match: {nested!r}",
        )


    def test_total_components_counts_transform_records_even_when_hidden_from_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = _write_prefab(root, "Host.prefab", SECOND_SOURCE_GUID, _source_prefab())
            build_effective_hierarchy = _load_builder()

            result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host.read_text(encoding="utf-8")
            )

        self.assertEqual(
            2,
            result.to_dict()["total_components"],
            msg="expanded hierarchy summary must count raw Transform component records like legacy hierarchy even when Transform labels are hidden",
        )

class EffectiveHierarchyDiagnosticsTests(unittest.TestCase):
    def test_unresolved_nested_source_keeps_resolved_sibling_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Nested.prefab", SOURCE_GUID, _source_prefab())
            host = _write_prefab(
                root,
                "Host.prefab",
                SECOND_SOURCE_GUID,
                _host_prefab(
                    _prefab_instance("9000", SOURCE_GUID),
                    _prefab_instance("9001", MISSING_GUID),
                ),
            )
            build_effective_hierarchy = _load_builder()

            result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host.read_text(encoding="utf-8")
            )

        payload = result.to_dict()
        child_names = [node["name"] for node in payload["roots"][0]["children"]]
        diagnostic_details = [diag["code"] for diag in payload["diagnostics"]]
        self.assertEqual(
            (["NestedRoot"], ["EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED"]),
            (child_names, diagnostic_details),
            msg=(
                "unresolved nested source should warn while resolved sibling "
                f"remains visible; observed payload={payload!r}"
            ),
        )

    def test_unreadable_nested_source_diagnostic_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "Assets"
            assets.mkdir(parents=True, exist_ok=True)
            child = assets / "Unreadable.prefab"
            child.write_bytes(b"\xff")
            child.with_suffix(child.suffix + ".meta").write_text(
                f"fileFormatVersion: 2\nguid: {SOURCE_GUID}\n",
                encoding="utf-8",
            )
            host = _write_prefab(
                root,
                "Host.prefab",
                SECOND_SOURCE_GUID,
                _host_prefab(_prefab_instance("9000", SOURCE_GUID)),
            )
            build_effective_hierarchy = _load_builder()

            result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host.read_text(encoding="utf-8")
            )

        payload = result.to_dict()
        self.assertEqual(
            [
                {
                    "severity": "warning",
                    "code": "EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED",
                    "message": (
                        f"Nested PrefabInstance source GUID {SOURCE_GUID} "
                        "could not be decoded."
                    ),
                    "data": {
                        "path": "Assets/Host.prefab",
                        "location": "9000",
                    },
                }
            ],
            payload["diagnostics"],
        )

    def test_package_cache_fallback_matches_context_guid_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_dir = root / "Library" / "PackageCache" / "com.example.pkg"
            package_dir.mkdir(parents=True)
            child = package_dir / "Nested.prefab"
            child.write_text(_source_prefab("PackageNested"), encoding="utf-8")
            child.with_suffix(child.suffix + ".meta").write_text(
                f"fileFormatVersion: 2\nguid: {SOURCE_GUID}\n",
                encoding="utf-8",
            )
            host = _write_prefab(
                root,
                "Host.prefab",
                SECOND_SOURCE_GUID,
                _host_prefab(_prefab_instance("9000", SOURCE_GUID)),
            )
            build_effective_hierarchy = _load_builder()
            host_text = host.read_text(encoding="utf-8")

            fallback_result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host_text,
            )
            context_result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host_text, guid_index={},
            )

        fallback_payload = fallback_result.to_dict()
        context_payload = context_result.to_dict()
        self.assertEqual(
            (
                [],
                ["EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED"],
                [],
                ["EFFECTIVE_HIERARCHY_SOURCE_UNRESOLVED"],
            ),
            (
                [node["name"] for node in fallback_payload["roots"][0]["children"]],
                [diag["code"] for diag in fallback_payload["diagnostics"]],
                [node["name"] for node in context_payload["roots"][0]["children"]],
                [diag["code"] for diag in context_payload["diagnostics"]],
            ),
            msg=(
                "effective hierarchy fallback must match the shared context "
                f"package-cache policy; fallback={fallback_payload!r} context={context_payload!r}"
            ),
        )

    def test_nested_cache_reuses_duplicate_child_source(self) -> None:
        from prefab_sentinel.nested_prefab_cache import NestedPrefabCache
        from prefab_sentinel.unity_assets import decode_text_file
        from prefab_sentinel.unity_yaml_parser import split_yaml_blocks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _write_prefab(root, "Nested.prefab", SOURCE_GUID, _source_prefab())
            host = _write_prefab(
                root,
                "Host.prefab",
                SECOND_SOURCE_GUID,
                _host_prefab(
                    _prefab_instance("9000", SOURCE_GUID),
                    _prefab_instance("9001", SOURCE_GUID),
                ),
            )
            build_effective_hierarchy = _load_builder()

            with (
                patch(
                    "prefab_sentinel.nested_prefab_cache.decode_text_file",
                    wraps=decode_text_file,
                ) as decode_text,
                patch(
                    "prefab_sentinel.nested_prefab_cache.split_yaml_blocks",
                    wraps=split_yaml_blocks,
                ) as cache_split_blocks,
                patch(
                    "prefab_sentinel.effective_hierarchy.parser.split_yaml_blocks",
                    wraps=split_yaml_blocks,
                ) as parser_split_blocks,
            ):
                result = build_effective_hierarchy(
                    root,
                    "Assets/Host.prefab",
                    host.read_text(encoding="utf-8"),
                    guid_index={SOURCE_GUID: child},
                    nested_prefab_cache=NestedPrefabCache(),
                )

        payload = result.to_dict()
        child_names = [node["name"] for node in payload["roots"][0]["children"]]
        self.assertEqual(
            (["NestedRoot", "NestedRoot"], 1, 1, 1, []),
            (
                child_names,
                decode_text.call_count,
                cache_split_blocks.call_count,
                parser_split_blocks.call_count,
                payload["diagnostics"],
            ),
            msg=(
                "duplicate nested PrefabInstance siblings should preserve "
                "serialized output order while decoding and parsing the "
                f"child source once; payload={payload!r}"
            ),
        )

    def test_cycle_and_depth_limit_stop_only_the_unsafe_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_text = _source_prefab("PrefabA") + _prefab_instance(
                "9100", SECOND_SOURCE_GUID, parent_transform="200"
            )
            b_text = _source_prefab("PrefabB") + _prefab_instance(
                "9200", SOURCE_GUID, parent_transform="200"
            )
            _write_prefab(root, "PrefabA.prefab", SOURCE_GUID, a_text)
            _write_prefab(root, "PrefabB.prefab", SECOND_SOURCE_GUID, b_text)
            host = _write_prefab(
                root,
                "Host.prefab",
                "99999999000000009999999900000000",
                _host_prefab(_prefab_instance("9000", SOURCE_GUID)),
            )
            build_effective_hierarchy = _load_builder()

            cycle_result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host.read_text(encoding="utf-8")
            )
            limited_result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host.read_text(encoding="utf-8"), max_depth=1
            )

        cycle_payload = cycle_result.to_dict()
        limited_payload = limited_result.to_dict()
        self.assertEqual(
            (
                ["PrefabA"],
                ["EFFECTIVE_HIERARCHY_CYCLE"],
                ["PrefabA"],
                ["EFFECTIVE_HIERARCHY_DEPTH_LIMIT"],
            ),
            (
                [node["name"] for node in cycle_payload["roots"][0]["children"]],
                [diag["code"] for diag in cycle_payload["diagnostics"]],
                [node["name"] for node in limited_payload["roots"][0]["children"]],
                [diag["code"] for diag in limited_payload["diagnostics"]],
            ),
            msg=(
                "cycle and depth-limit diagnostics should stop only the unsafe "
                f"branch; observed cycle={cycle_payload!r} limited={limited_payload!r}"
            ),
        )


    def test_malformed_transform_child_cycle_warns_and_stops_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = (
                YAML_HEADER
                + make_gameobject("100", "LoopRoot", ["200"])
                + make_transform("200", "100", children_file_ids=["200"])
            )
            host = _write_prefab(root, "Loop.prefab", SECOND_SOURCE_GUID, text)
            build_effective_hierarchy = _load_builder()

            result = build_effective_hierarchy(
                root, "Assets/Loop.prefab", host.read_text(encoding="utf-8")
            )

        self.assertEqual(
            (
                ["LoopRoot"],
                [[]],
                ["EFFECTIVE_HIERARCHY_TRANSFORM_CHILD_CYCLE"],
            ),
            (
                [node.name for node in result.roots],
                [node.children for node in result.roots],
                [diagnostic.detail for diagnostic in result.diagnostics],
            ),
            msg="malformed Transform m_Children cycles must warn and stop only the recursive branch",
        )

class EffectiveHierarchyUnitySmokeFixtureTests(unittest.TestCase):
    def test_unity_style_nested_prefab_fixture_exposes_effective_metadata(self) -> None:
        unity_style_source = (
            "%YAML 1.1\n"
            "%TAG !u! tag:unity3d.com,2011:\n"
            "--- !u!1 &100\n"
            "GameObject:\n"
            "  m_Component:\n"
            "  - component: {fileID: 200}\n"
            "  m_Name: AuthoredRoot\n"
            "--- !u!4 &200\n"
            "Transform:\n"
            "  m_GameObject: {fileID: 100}\n"
            "  m_Father: {fileID: 0}\n"
            "  m_Children: []\n"
            "  m_LocalPosition: {x: 0, y: 0, z: 0}\n"
            "  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}\n"
            "  m_LocalScale: {x: 1, y: 1, z: 1}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Authored.prefab", SOURCE_GUID, unity_style_source)
            host = _write_prefab(
                root,
                "Host.prefab",
                SECOND_SOURCE_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[("200", "m_LocalPosition.y", "3.5")],
                    )
                ),
            )
            build_effective_hierarchy = _load_builder()

            result = build_effective_hierarchy(
                root, "Assets/Host.prefab", host.read_text(encoding="utf-8")
            )

        nested = result.to_dict()["roots"][0]["children"][0]
        self.assertEqual(
            (
                "AuthoredRoot",
                "Assets/Authored.prefab",
                ["m_LocalPosition.y"],
            ),
            (
                nested["name"],
                nested["origin"]["source"]["asset_path"],
                nested["origin"]["override_host"]["property_paths"],
            ),
            msg=f"Unity-style fixture lost effective hierarchy metadata: {nested!r}",
        )


class EffectiveHierarchyPackageSplitTests(unittest.TestCase):
    def test_public_imports_are_served_by_package_init(self) -> None:
        import prefab_sentinel.effective_hierarchy as package
        from prefab_sentinel.effective_hierarchy import (
            EffectiveHierarchyNode,
            EffectiveHierarchyResult,
            build_effective_hierarchy,
        )

        self.assertEqual("__init__.py", Path(package.__file__).name)
        self.assertEqual(
            (
                "EffectiveHierarchyNode",
                "EffectiveHierarchyResult",
                "build_effective_hierarchy",
            ),
            tuple(package.__all__),
        )
        self.assertIs(package.EffectiveHierarchyNode, EffectiveHierarchyNode)
        self.assertIs(package.EffectiveHierarchyResult, EffectiveHierarchyResult)
        self.assertIs(package.build_effective_hierarchy, build_effective_hierarchy)
