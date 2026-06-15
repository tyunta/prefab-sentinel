from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from prefab_sentinel.services.prefab_variant import PrefabVariantService
from tests.yaml_helpers import YAML_HEADER, make_gameobject


SOURCE_GUID = "11111111111111111111111111111111"
HOST_GUID = "22222222222222222222222222222222"


def _load_inspector() -> Any:
    try:
        from prefab_sentinel.effective_transform_inspector import (
            inspect_transform_effective_values,
        )
    except ImportError as exc:
        raise AssertionError(
            "expected inspect_transform_effective_values to return Transform "
            f"default/override/effective values; observed missing import: {exc}"
        ) from exc
    return inspect_transform_effective_values


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


def _transform(
    file_id: str,
    go_file_id: str,
    *,
    father_file_id: str = "0",
    children_file_ids: list[str] | None = None,
    local_position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    local_rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    local_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> str:
    children = children_file_ids or []
    if children:
        children_block = "\n".join(f"  - {{fileID: {child}}}" for child in children)
        children_yaml = f"  m_Children:\n{children_block}"
    else:
        children_yaml = "  m_Children: []"
    return (
        f"--- !u!4 &{file_id}\n"
        "Transform:\n"
        f"  m_GameObject: {{fileID: {go_file_id}}}\n"
        f"  m_Father: {{fileID: {father_file_id}}}\n"
        f"{children_yaml}\n"
        f"  m_LocalPosition: {{x: {local_position[0]}, y: {local_position[1]}, z: {local_position[2]}}}\n"
        f"  m_LocalRotation: {{x: {local_rotation[0]}, y: {local_rotation[1]}, z: {local_rotation[2]}, w: {local_rotation[3]}}}\n"
        f"  m_LocalScale: {{x: {local_scale[0]}, y: {local_scale[1]}, z: {local_scale[2]}}}\n"
    )


def _source_prefab() -> str:
    return (
        YAML_HEADER
        + make_gameobject("100", "NestedRoot", ["200"])
        + _transform(
            "200",
            "100",
            children_file_ids=["201"],
            local_position=(1.0, 2.0, 3.0),
            local_scale=(2.0, 2.0, 2.0),
        )
        + make_gameobject("101", "NestedLeaf", ["201"])
        + _transform(
            "201",
            "101",
            father_file_id="200",
            local_position=(0.5, 0.0, 0.0),
        )
    )


def _host_prefab(*instances: str) -> str:
    return (
        YAML_HEADER
        + make_gameobject("1000", "HostRoot", ["2000"])
        + _transform("2000", "1000", local_position=(10.0, 0.0, 0.0))
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


class EffectiveTransformValueTests(unittest.TestCase):
    def test_nested_prefab_transform_values_distinguish_default_override_and_effective(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Nested.prefab", SOURCE_GUID, _source_prefab())
            _write_prefab(
                root,
                "Host.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[("200", "m_LocalPosition.y", "4")],
                    )
                ),
            )
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/NestedRoot",
            ).to_dict()

        values = response["data"].get("values")
        observed = (
            response["success"],
            response["code"],
            values["local_position"]["default"],
            values["local_position"]["override"],
            values["local_position"]["effective"],
            values["local_position"]["overridden"],
            values["local_rotation"]["overridden"],
            values["local_scale"]["overridden"],
            values["world_position"]["effective"],
            values["world_rotation"]["effective"],
            values["world_scale"]["effective"],
            values["local_position"]["origin"]["override_host"]["asset_path"],
        )
        self.assertEqual(
            (
                True,
                "INSPECT_TRANSFORM_VALUES",
                [1.0, 2.0, 3.0],
                {"y": 4.0},
                [1.0, 4.0, 3.0],
                True,
                False,
                False,
                [11.0, 4.0, 3.0],
                [0.0, 0.0, 0.0, 1.0],
                [2.0, 2.0, 2.0],
                "Assets/Host.prefab",
            ),
            observed,
            msg=f"Transform values should expose default, override, effective, and world columns; observed response={response!r}",
        )


class EffectiveTransformWorldValueTests(unittest.TestCase):
    def test_world_values_are_computed_for_resolved_chain_and_warn_for_unresolved_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Nested.prefab", SOURCE_GUID, _source_prefab())
            _write_prefab(
                root,
                "Host.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[("200", "m_LocalPosition.y", "4")],
                    )
                ),
            )
            _write_prefab(
                root,
                "Orphan.prefab",
                "33333333333333333333333333333333",
                YAML_HEADER
                + make_gameobject("300", "Orphan", ["400"])
                + _transform(
                    "400",
                    "300",
                    father_file_id="999",
                    local_position=(3.0, 0.0, 0.0),
                ),
            )
            inspect = _load_inspector()

            resolved = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/NestedRoot/NestedLeaf",
            ).to_dict()
            unresolved = inspect(
                PrefabVariantService(root),
                "Assets/Orphan.prefab",
                "Orphan",
            ).to_dict()

        unresolved_values = unresolved["data"].get("values")
        self.assertEqual(
            (
                True,
                [12.0, 4.0, 3.0],
                [2.0, 2.0, 2.0],
                True,
                "warning",
                ["INSPECT_TRANSFORM_WORLD_UNRESOLVED"],
                [3.0, 0.0, 0.0],
                {"computed": False, "diagnostic": "INSPECT_TRANSFORM_WORLD_UNRESOLVED"},
            ),
            (
                resolved["success"],
                resolved["data"]["values"]["world_position"]["effective"],
                resolved["data"]["values"]["world_scale"]["effective"],
                unresolved["success"],
                unresolved["severity"],
                [diag["code"] for diag in unresolved["diagnostics"]],
                unresolved_values["local_position"]["effective"],
                unresolved_values["world_position"],
            ),
            msg=(
                "resolved chains should compute world values, while unresolved "
                f"parents warn and omit only world values; resolved={resolved!r} unresolved={unresolved!r}"
            ),
        )


class EffectiveTransformErrorTests(unittest.TestCase):
    def test_missing_symbol_path_returns_typed_read_only_error_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Host.prefab", HOST_GUID, _host_prefab())
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/Missing",
            ).to_dict()

        self.assertEqual(
            (
                False,
                "error",
                "INSPECT_TRANSFORM_SYMBOL_NOT_FOUND",
                True,
                "HostRoot/Missing",
                False,
            ),
            (
                response["success"],
                response["severity"],
                response["code"],
                response["data"]["read_only"],
                response["data"]["symbol_path"],
                "values" in response["data"],
            ),
            msg=f"missing Transform symbol should be a typed read-only error without values; observed response={response!r}",
        )

    def test_duplicate_symbol_path_returns_typed_ambiguity_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Nested.prefab", SOURCE_GUID, _source_prefab())
            _write_prefab(
                root,
                "Host.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance("9000", SOURCE_GUID),
                    _prefab_instance("9001", SOURCE_GUID),
                ),
            )
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/NestedRoot/NestedLeaf",
            ).to_dict()

        self.assertEqual(
            (
                False,
                "error",
                "INSPECT_TRANSFORM_SYMBOL_AMBIGUOUS",
                True,
                "HostRoot/NestedRoot/NestedLeaf",
                2,
                False,
            ),
            (
                response["success"],
                response["severity"],
                response["code"],
                response["data"]["read_only"],
                response["data"]["symbol_path"],
                response["data"]["match_count"],
                "values" in response["data"],
            ),
            msg=f"duplicate effective Transform symbol paths must be explicit ambiguity errors; observed response={response!r}",
        )

    def test_malformed_numeric_transform_override_returns_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_prefab(root, "Nested.prefab", SOURCE_GUID, _source_prefab())
            _write_prefab(
                root,
                "Host.prefab",
                HOST_GUID,
                _host_prefab(
                    _prefab_instance(
                        "9000",
                        SOURCE_GUID,
                        modifications=[("200", "m_LocalPosition.y", "not-a-number")],
                    )
                ),
            )
            inspect = _load_inspector()

            response = inspect(
                PrefabVariantService(root),
                "Assets/Host.prefab",
                "HostRoot/NestedRoot",
            ).to_dict()

        self.assertEqual(
            (
                False,
                "INSPECT_TRANSFORM_NUMERIC_PARSE_ERROR",
                "error",
                ["INSPECT_TRANSFORM_NUMERIC_PARSE_ERROR"],
            ),
            (
                response["success"],
                response["code"],
                response["severity"],
                [diag["code"] for diag in response["diagnostics"]],
            ),
            msg=f"malformed Transform override values must return a typed error instead of crashing: {response!r}",
        )

    def test_missing_or_undecodable_asset_returns_typed_read_only_error_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "Assets"
            assets.mkdir(parents=True, exist_ok=True)
            bad_path = assets / "Bad.prefab"
            bad_path.write_bytes(b"\xff\xfe\xfa")
            inspect = _load_inspector()

            missing = inspect(
                PrefabVariantService(root),
                "Assets/Missing.prefab",
                "Any",
            ).to_dict()
            read_error = inspect(
                PrefabVariantService(root),
                "Assets/Bad.prefab",
                "Any",
            ).to_dict()

        self.assertEqual(
            (
                False,
                "INSPECT_TRANSFORM_FILE_NOT_FOUND",
                True,
                False,
                False,
                "INSPECT_TRANSFORM_READ_ERROR",
                True,
                False,
            ),
            (
                missing["success"],
                missing["code"],
                missing["data"]["read_only"],
                "values" in missing["data"],
                read_error["success"],
                read_error["code"],
                read_error["data"]["read_only"],
                "values" in read_error["data"],
            ),
            msg=f"asset read failures should be typed read-only errors without values; missing={missing!r} read_error={read_error!r}",
        )
