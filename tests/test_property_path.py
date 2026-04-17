"""Tests for ``prefab_sentinel.services.property_path.validate_property_path`` (issue #82).

The validator is syntactic only — no target resolution — and must emit
``SER001`` for shape errors, ``SER002`` for index errors, and ``PP_OK``
on success.  These tests pin the error-code / severity / code-path
mapping that downstream wire-ins depend on.
"""

from __future__ import annotations

import unittest

from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.property_path import validate_property_path


class PropertyPathTests(unittest.TestCase):
    def test_empty_rejected(self) -> None:
        """T12: empty string -> SER001 with 'empty' in message."""
        resp = validate_property_path("")
        self.assertEqual("SER001", resp.code)
        self.assertEqual(Severity.ERROR, resp.severity)
        self.assertFalse(resp.success)
        self.assertIn("empty", resp.message.lower())

    def test_negative_array_index_rejected(self) -> None:
        """T13: negative subscript -> SER002 with 'negative' in message."""
        resp = validate_property_path("m_Foo.Array.data[-1]")
        self.assertEqual("SER002", resp.code)
        self.assertIn("negative", resp.message.lower())

    def test_non_integer_array_index_rejected(self) -> None:
        """T14: non-integer subscript -> SER002."""
        resp = validate_property_path("m_Foo.Array.data[abc]")
        self.assertEqual("SER002", resp.code)

    def test_array_size_with_subscript_rejected(self) -> None:
        """T15: Array.size[N] -> SER002 (size is scalar)."""
        resp = validate_property_path("m_Foo.Array.size[0]")
        self.assertEqual("SER002", resp.code)

    def test_unterminated_bracket_rejected(self) -> None:
        """T16: missing closing bracket -> SER001."""
        resp = validate_property_path("m_Foo.Array.data[0")
        self.assertEqual("SER001", resp.code)

    def test_empty_segment_rejected(self) -> None:
        """T17: consecutive dots -> SER001."""
        resp = validate_property_path("m_Foo..Bar")
        self.assertEqual("SER001", resp.code)

    def test_nested_array_accepted(self) -> None:
        """T18: nested array accessors -> PP_OK."""
        resp = validate_property_path("m_Outer.Array.data[0].m_Inner.Array.data[1]")
        self.assertEqual("PP_OK", resp.code)
        self.assertTrue(resp.success)
        self.assertEqual(Severity.INFO, resp.severity)

    def test_valid_scalar_accepted(self) -> None:
        """T19: scalar dotted path -> PP_OK."""
        resp = validate_property_path("m_Transform.m_LocalPosition.x")
        self.assertEqual("PP_OK", resp.code)
        self.assertTrue(resp.success)

    def test_valid_array_element_accepted(self) -> None:
        """T20: simple Array.data[N] -> PP_OK."""
        resp = validate_property_path("m_List.Array.data[3]")
        self.assertEqual("PP_OK", resp.code)
        self.assertTrue(resp.success)


if __name__ == "__main__":
    unittest.main()
