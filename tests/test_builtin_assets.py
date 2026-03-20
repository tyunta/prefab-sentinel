from __future__ import annotations

import unittest

from prefab_sentinel.builtin_assets import (
    BUILTIN_CUBE_MESH,
    BUILTIN_DEFAULT_MATERIAL,
    BUILTIN_SPHERE_MESH,
    BuiltinAssetInfo,
    builtin_reference,
    resolve_builtin_reference,
)
from prefab_sentinel.unity_assets import (
    UNITY_BUILTIN_EXTRA_GUID,
    UNITY_DEFAULT_RESOURCES_GUID,
)


class ResolveBuiltinReferenceTests(unittest.TestCase):
    def test_resolve_sphere_mesh(self) -> None:
        result = resolve_builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10207)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "Sphere")
        self.assertEqual(result.file_id, 10207)
        self.assertEqual(result.bundle, "unity default resources")

    def test_resolve_default_material(self) -> None:
        result = resolve_builtin_reference(UNITY_BUILTIN_EXTRA_GUID, 10303)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "Default-Material")
        self.assertEqual(result.bundle, "unity_builtin_extra")

    def test_resolve_cube_mesh(self) -> None:
        result = resolve_builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10202)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "Cube")

    def test_unknown_file_id_returns_none(self) -> None:
        result = resolve_builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 99999)
        self.assertIsNone(result)

    def test_non_builtin_guid_returns_none(self) -> None:
        result = resolve_builtin_reference("abcdef01234567890abcdef012345678", 10207)
        self.assertIsNone(result)

    def test_case_insensitive_guid(self) -> None:
        upper = UNITY_DEFAULT_RESOURCES_GUID.upper()
        result = resolve_builtin_reference(upper, 10207)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.name, "Sphere")


class BuiltinReferenceTests(unittest.TestCase):
    def test_builtin_reference_dict_format(self) -> None:
        ref = builtin_reference(UNITY_DEFAULT_RESOURCES_GUID, 10207)
        self.assertEqual(ref["fileID"], 10207)
        self.assertEqual(ref["guid"], UNITY_DEFAULT_RESOURCES_GUID)
        self.assertEqual(ref["type"], 0)


class ConvenienceConstantTests(unittest.TestCase):
    def test_sphere_mesh_constant(self) -> None:
        self.assertEqual(BUILTIN_SPHERE_MESH["fileID"], 10207)
        self.assertEqual(BUILTIN_SPHERE_MESH["guid"], UNITY_DEFAULT_RESOURCES_GUID)

    def test_default_material_constant(self) -> None:
        self.assertEqual(BUILTIN_DEFAULT_MATERIAL["fileID"], 10303)
        self.assertEqual(BUILTIN_DEFAULT_MATERIAL["guid"], UNITY_BUILTIN_EXTRA_GUID)

    def test_cube_mesh_constant(self) -> None:
        self.assertEqual(BUILTIN_CUBE_MESH["fileID"], 10202)
        self.assertEqual(BUILTIN_CUBE_MESH["guid"], UNITY_DEFAULT_RESOURCES_GUID)


if __name__ == "__main__":
    unittest.main()
