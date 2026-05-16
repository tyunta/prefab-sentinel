"""Behavioural pins for ``prefab_sentinel.services.property_path.validate_property_path``.

The validator is syntactic only and emits the contract documented in
``prefab_sentinel.services.property_path``: ``SER001`` for shape errors,
``SER002`` for index errors, ``PP_OK`` on success.  Each test below pins
the documented ``(code, severity, success)`` triple in a single equality
so a mutation that swaps any one of the three is killed by the same
assertion message.  Error tests additionally pin the descriptor phrase
that names which rule was violated, because the rule name is the
behavioural contract a downstream consumer sees on the wire.
"""

from __future__ import annotations

import unittest

from prefab_sentinel.contracts import Severity
from prefab_sentinel.services.property_path import validate_property_path


def _envelope_triple(resp) -> tuple[str, Severity, bool]:
    """Project the validator's response onto the contract triple."""
    return (resp.code, resp.severity, resp.success)


_SHAPE_ERROR_TRIPLE: tuple[str, Severity, bool] = ("SER001", Severity.ERROR, False)
_INDEX_ERROR_TRIPLE: tuple[str, Severity, bool] = ("SER002", Severity.ERROR, False)
_SUCCESS_TRIPLE: tuple[str, Severity, bool] = ("PP_OK", Severity.INFO, True)


class PropertyPathShapeErrorTests(unittest.TestCase):
    """Inputs whose shape violates the documented syntax must yield SER001."""

    def test_empty_input_is_rejected_with_shape_error_naming_empty(self) -> None:
        resp = validate_property_path("")

        self.assertEqual(_SHAPE_ERROR_TRIPLE, _envelope_triple(resp))
        self.assertIn("empty", resp.message.lower())

    def test_unterminated_bracket_is_rejected_with_shape_error_naming_bracket(self) -> None:
        resp = validate_property_path("m_Foo.Array.data[0")

        self.assertEqual(_SHAPE_ERROR_TRIPLE, _envelope_triple(resp))
        self.assertIn("bracket", resp.message.lower())

    def test_consecutive_dots_are_rejected_with_shape_error_naming_segment(self) -> None:
        resp = validate_property_path("m_Foo..Bar")

        self.assertEqual(_SHAPE_ERROR_TRIPLE, _envelope_triple(resp))
        self.assertIn("segment", resp.message.lower())


class PropertyPathIndexErrorTests(unittest.TestCase):
    """Inputs whose subscript violates the documented index rules must yield SER002."""

    def test_negative_subscript_is_rejected_with_index_error_naming_negative(self) -> None:
        resp = validate_property_path("m_Foo.Array.data[-1]")

        self.assertEqual(_INDEX_ERROR_TRIPLE, _envelope_triple(resp))
        self.assertIn("negative", resp.message.lower())

    def test_non_integer_subscript_is_rejected_with_index_error_naming_integer(self) -> None:
        resp = validate_property_path("m_Foo.Array.data[abc]")

        self.assertEqual(_INDEX_ERROR_TRIPLE, _envelope_triple(resp))
        self.assertIn("integer", resp.message.lower())

    def test_array_size_with_subscript_is_rejected_with_index_error_naming_scalar(self) -> None:
        resp = validate_property_path("m_Foo.Array.size[0]")

        self.assertEqual(_INDEX_ERROR_TRIPLE, _envelope_triple(resp))
        self.assertIn("scalar", resp.message.lower())


class PropertyPathSuccessTests(unittest.TestCase):
    """Inputs that satisfy the documented syntax must yield PP_OK."""

    def test_scalar_dotted_path_is_accepted_with_success_envelope(self) -> None:
        resp = validate_property_path("m_Transform.m_LocalPosition.x")

        self.assertEqual(_SUCCESS_TRIPLE, _envelope_triple(resp))

    def test_simple_array_element_path_is_accepted_with_success_envelope(self) -> None:
        resp = validate_property_path("m_List.Array.data[3]")

        self.assertEqual(_SUCCESS_TRIPLE, _envelope_triple(resp))

    def test_nested_array_path_is_accepted_with_success_envelope(self) -> None:
        # Doubly-nested Array.data exercises the loop's segment-by-segment
        # accumulator and pins that nested cases are not falsely rejected.
        resp = validate_property_path("m_Outer.Array.data[0].m_Inner.Array.data[1]")

        self.assertEqual(_SUCCESS_TRIPLE, _envelope_triple(resp))


if __name__ == "__main__":
    unittest.main()
