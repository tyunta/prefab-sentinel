"""Behavioural pins for ``prefab_sentinel.material_asset_writer.write_material_property``.

The writer is a single-entry helper whose response envelope reports the
documented per-kind success / failure code, the before/after values, and a
suggestions list when the property name has no exact match.  Each test
below exercises real production code on a freshly copied fixture (T1) and
pins the observable contract by exact equality on the envelope tuple plus
re-parses the on-disk file to confirm the side-effect.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from prefab_sentinel.material_asset_inspector import inspect_material_asset
from prefab_sentinel.material_asset_writer import write_material_property

_FIXTURES = Path(__file__).parent / "fixtures" / "mat"

# Standard textured fixture's documented float ``_Glossiness`` value
# (matches the ``- _Glossiness: 0.8`` line in standard_textured.mat).
_FIXTURE_GLOSSINESS = 0.8

# The texture-block scale line that must round-trip unchanged when only
# the texture GUID is rewritten.  Names the documented field shape.
_FIXTURE_SCALE_LINE = "m_Scale: {x: 1, y: 1}"


class _MaterialFixture:
    """Context manager that copies a fixture into a tmp dir and yields its path."""

    def __init__(self, fixture_name: str) -> None:
        self._fixture_name = fixture_name
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path = Path()

    def __enter__(self) -> Path:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "test.mat"
        shutil.copy(_FIXTURES / self._fixture_name, self.path)
        return self.path

    def __exit__(self, *exc: object) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()


class WriteMaterialPropertyFloatTests(unittest.TestCase):
    """Pin the float-kind dry-run / apply contract."""

    def test_dry_run_returns_envelope_with_documented_before_value(self) -> None:
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_Glossiness", "0.3", dry_run=True
            )

        # Pin (success, code, category, after) as a single tuple so a
        # mutation that swaps any one field is killed by this assertion.
        observed = (
            result["success"],
            result["code"],
            result["data"]["category"],
            result["data"]["after"],
        )
        expected = (True, "MAT_PROP_DRY_RUN", "m_Floats", "0.3")
        self.assertEqual(expected, observed)
        # ``before`` is a stringified float; assertAlmostEqual after
        # parsing pins the documented fixture value.
        self.assertAlmostEqual(_FIXTURE_GLOSSINESS, float(result["data"]["before"]))

    def test_apply_persists_float_so_re_parse_returns_new_value(self) -> None:
        new_value = 0.3
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_Glossiness", str(new_value), dry_run=False
            )
            parsed = inspect_material_asset(str(mat))
            glossiness = next(f for f in parsed.floats if f.name == "_Glossiness")

        self.assertEqual(
            (True, "MAT_PROP_APPLIED"), (result["success"], result["code"])
        )
        self.assertAlmostEqual(new_value, glossiness.value)


class WriteMaterialPropertyIntTests(unittest.TestCase):
    """Pin the int-kind apply contract via re-parse round-trip."""

    def test_apply_persists_int_so_re_parse_returns_new_value(self) -> None:
        new_value = 64
        with _MaterialFixture("with_ints.mat") as mat:
            result = write_material_property(
                str(mat), "_StencilRef", str(new_value), dry_run=False
            )
            parsed = inspect_material_asset(str(mat))
            stencil = next(i for i in parsed.ints if i.name == "_StencilRef")

        self.assertEqual(
            (True, "MAT_PROP_APPLIED"), (result["success"], result["code"])
        )
        self.assertEqual(new_value, stencil.value)


class WriteMaterialPropertyColorTests(unittest.TestCase):
    """Pin the colour-kind apply contract."""

    def test_apply_persists_color_so_re_parse_returns_each_channel(self) -> None:
        new_color = (0.5, 0.6, 0.7, 1.0)
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_Color", "[0.5, 0.6, 0.7, 1]", dry_run=False
            )
            parsed = inspect_material_asset(str(mat))
            color = next(c for c in parsed.colors if c.name == "_Color")

        self.assertEqual(
            (True, "MAT_PROP_APPLIED"), (result["success"], result["code"])
        )
        # Pin each channel by full equality so a mutation that swaps two
        # channels is named in the failure message.
        observed_rgba = tuple(
            round(color.value[k], 6) for k in ("r", "g", "b", "a")
        )
        self.assertEqual(new_color, observed_rgba)


class WriteMaterialPropertyTextureTests(unittest.TestCase):
    """Pin the texture-kind apply contract and the sibling-block invariant."""

    def test_apply_changes_only_texture_guid_and_preserves_scale_block(self) -> None:
        new_guid = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_MainTex", f"guid:{new_guid}", dry_run=False
            )
            text = mat.read_text(encoding="utf-8")

        self.assertEqual(
            (True, "MAT_PROP_APPLIED"), (result["success"], result["code"])
        )
        # The new GUID must have been written into the texture block.
        self.assertIn(new_guid, text)
        # The scale sibling block must be preserved verbatim — this is
        # the documented invariant the spec rows pin.
        self.assertIn(_FIXTURE_SCALE_LINE, text)

    def test_apply_with_empty_value_nullifies_texture_to_file_id_zero(self) -> None:
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_MainTex", "", dry_run=False
            )
            text = mat.read_text(encoding="utf-8")

        self.assertEqual(
            (True, "MAT_PROP_APPLIED"), (result["success"], result["code"])
        )
        # Documented null-texture sentinel.
        self.assertIn("m_Texture: {fileID: 0}", text)


class WriteMaterialPropertyErrorTests(unittest.TestCase):
    """Pin every documented failure envelope code."""

    def test_non_mat_extension_yields_wrong_extension_envelope(self) -> None:
        result = write_material_property(
            "/tmp/test.prefab", "_Foo", "1", dry_run=True
        )

        self.assertEqual(
            (False, "MAT_PROP_WRONG_EXT"), (result["success"], result["code"])
        )

    def test_missing_file_yields_file_not_found_envelope(self) -> None:
        result = write_material_property(
            "/nonexistent/test.mat", "_Foo", "1", dry_run=True
        )

        self.assertEqual(
            (False, "MAT_PROP_FILE_NOT_FOUND"), (result["success"], result["code"])
        )

    def test_unknown_property_yields_not_found_envelope_with_suggestions_list(
        self,
    ) -> None:
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_NonExistent", "1", dry_run=True
            )

        # Pin (success, code) plus the suggestions-list invariant: the
        # field must exist and be a list (typed contract per docstring).
        self.assertEqual(
            (False, "MAT_PROP_NOT_FOUND"), (result["success"], result["code"])
        )
        self.assertIsInstance(result["data"]["suggestions"], list)

    def test_typo_property_yields_suggestions_containing_closest_real_name(
        self,
    ) -> None:
        # ``_Colr`` is one Levenshtein step from ``_Color``; the
        # documented fuzzy suggestion list must include the real name.
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_Colr", "1", dry_run=True
            )

        self.assertEqual(
            (False, "MAT_PROP_NOT_FOUND"), (result["success"], result["code"])
        )
        self.assertIn("_Color", result["data"]["suggestions"])

    def test_invalid_color_value_yields_parse_error_envelope(self) -> None:
        with _MaterialFixture("standard_textured.mat") as mat:
            result = write_material_property(
                str(mat), "_Color", "not_a_color", dry_run=False
            )

        self.assertEqual(
            (False, "MAT_PROP_PARSE_ERROR"), (result["success"], result["code"])
        )


if __name__ == "__main__":
    unittest.main()
