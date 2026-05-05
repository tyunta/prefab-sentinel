"""Issue #180 — assertRaises value-pinning meta-test.

Walks the test source tree at run time, identifies every
``assertRaises``-style block, and asserts that each block carries a
value-pin within the enclosing test method or that the exception type is
on the documented infrastructure-exception allowlist.

A *value-pin* is any of:

* ``assertRaisesRegex`` — the call itself pins the exception message.
* ``with assertRaises(X) as cm:`` followed by any reference to the
  captured exception (``cm.exception`` / ``cm.exception.args`` / etc.) in
  the enclosing test method body.
* Any of ``assertEqual`` / ``assertIn`` / ``assertRegex`` /
  ``assertNotEqual`` / ``assertNotIn`` / ``assertSequenceEqual`` /
  ``assertDictEqual`` / ``assertListEqual`` / ``assertGreater`` /
  ``assertGreaterEqual`` / ``assertLess`` / ``assertLessEqual`` /
  ``assert_error_envelope`` anywhere in the enclosing test method body.

The infrastructure-exception allowlist matches the exception families the
project treats as infra contracts where the type itself is the pin: file
system, lookup, system-exit, and encoding errors.

This module is marked ``source_text_invariant`` because it walks the
on-disk repository tree and contributes no mutant detection signal.
"""

from __future__ import annotations

import ast
import unittest
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.source_text_invariant

_TESTS_ROOT = Path(__file__).resolve().parent

# Infrastructure-exception allowlist: types whose presence as the
# ``assertRaises`` argument is itself the pin.  The list is intentionally
# narrow to standard library / well-known infra families so application
# contract checks (ValueError / TypeError / KeyError / RuntimeError) are
# forced through the value-pin path.
INFRA_EXCEPTION_ALLOWLIST: frozenset[str] = frozenset(
    {
        # file-system / OS-level
        "FileNotFoundError",
        "FileExistsError",
        "IsADirectoryError",
        "NotADirectoryError",
        "OSError",
        "IOError",
        "PermissionError",
        # lookup / import
        "ModuleNotFoundError",
        "ImportError",
        # system-exit family
        "SystemExit",
        "KeyboardInterrupt",
        # encoding / decoding
        "UnicodeError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "JSONDecodeError",
    }
)

# Method-level assertion calls that count as value-pins.  ``assert_error_envelope``
# is the project's own helper and pins code/severity/message by value.
_VALUE_PIN_ASSERTIONS: frozenset[str] = frozenset(
    {
        "assertEqual",
        "assertNotEqual",
        "assertIn",
        "assertNotIn",
        "assertRegex",
        "assertNotRegex",
        "assertSequenceEqual",
        "assertDictEqual",
        "assertListEqual",
        "assertSetEqual",
        "assertTupleEqual",
        "assertGreater",
        "assertGreaterEqual",
        "assertLess",
        "assertLessEqual",
        "assertAlmostEqual",
        "assertNotAlmostEqual",
        "assert_error_envelope",
    }
)


def _exception_name(node: ast.expr) -> str | None:
    """Extract a bare exception class name from an ``assertRaises`` argument."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    # First element of a (X, Y) tuple: pick the first name.
    if isinstance(node, ast.Tuple) and node.elts:
        return _exception_name(node.elts[0])
    return None


def _is_assert_raises_call(node: ast.expr) -> tuple[str, ast.Call] | None:
    """Return ``(method_name, call)`` if *node* is ``...assertRaises[Regex]()``."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return None
    if name in {"assertRaises", "assertRaisesRegex"}:
        return name, node
    return None


def _capture_target_name(item: ast.withitem) -> str | None:
    """Return the ``as VAR`` target name for an ``assertRaises`` ``with`` item."""
    if item.optional_vars is None:
        return None
    if isinstance(item.optional_vars, ast.Name):
        return item.optional_vars.id
    return None


def _enclosing_test_method(
    function_stack: list[ast.AST],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the innermost ``def`` enclosing the current ``with`` block."""
    for fn in reversed(function_stack):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            return fn
    return None


def _classify_with_item(
    item: ast.withitem,
    function_stack: list[ast.AST],
) -> tuple[str, int] | None:
    """Return ``(exc_name, lineno)`` if *item* is an offending bare assertRaises.

    Returns ``None`` for items that pass any of the rule's escape hatches:
    not an ``assertRaises`` call, ``assertRaisesRegex`` (carries its own
    pin), an allowlisted infrastructure exception, no enclosing test
    method, or a value-pin present in the enclosing test method body.
    """
    info = _is_assert_raises_call(item.context_expr)
    if info is None:
        return None
    method_name, call = info
    if method_name == "assertRaisesRegex":
        return None
    exc_name = _exception_name(call.args[0]) if call.args else None
    if exc_name in INFRA_EXCEPTION_ALLOWLIST:
        return None
    method = _enclosing_test_method(function_stack)
    if method is None or not method.name.startswith("test_"):
        return None
    capture = _capture_target_name(item)
    if _has_value_pin_in_method(method, capture):
        return None
    return exc_name or "<unknown>", call.lineno


def _has_value_pin_in_method(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    capture_name: str | None,
) -> bool:
    """Detect a value-pin assertion in *method*'s body."""
    for descendant in ast.walk(method):
        if isinstance(descendant, ast.Call):
            func = descendant.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                name = ""
            if name in _VALUE_PIN_ASSERTIONS:
                return True
            if name == "assertRaisesRegex":
                # An assertRaisesRegex elsewhere in the same method also
                # contributes a pin signal, so a method that uses one
                # near-by counts as pinned overall.
                return True
        # Reference to ``cm.exception`` (or ``cm.exception.args`` etc.)
        if (
            capture_name
            and isinstance(descendant, ast.Attribute)
            and isinstance(descendant.value, ast.Name)
            and descendant.value.id == capture_name
            and descendant.attr == "exception"
        ):
            return True
    return False


def _walk_with_function_stack(
    node: ast.AST,
    function_stack: list[ast.AST],
    on_with: Callable[[ast.AST, list[ast.AST]], None],
) -> None:
    """Walk *node* tracking the enclosing ``def`` stack and dispatching on
    each ``with`` / ``async with`` to *on_with*.

    *on_with* receives ``(node, function_stack)`` and may inspect / record.
    """
    entered_function = False
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        function_stack.append(node)
        entered_function = True
    try:
        if isinstance(node, ast.With | ast.AsyncWith):
            on_with(node, function_stack)
        for child in ast.iter_child_nodes(node):
            _walk_with_function_stack(child, function_stack, on_with)
    finally:
        if entered_function:
            function_stack.pop()


def _collect_offending_sites(test_root: Path) -> list[tuple[Path, int, str]]:
    """Walk *test_root* and return the list of offending assertRaises sites."""
    offending: list[tuple[Path, int, str]] = []
    for path in sorted(test_root.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — collected only on parser break
            continue
        relative_path = path.relative_to(test_root.parent)

        def record(
            with_node: ast.AST,
            function_stack: list[ast.AST],
            *,
            _relative_path: Path = relative_path,
            _offending: list[tuple[Path, int, str]] = offending,
        ) -> None:
            for item in with_node.items:  # type: ignore[attr-defined]
                classification = _classify_with_item(item, function_stack)
                if classification is None:
                    continue
                exc_name, lineno = classification
                _offending.append((_relative_path, lineno, exc_name))

        _walk_with_function_stack(tree, [], record)

    return offending


class AssertRaisesPinTests(unittest.TestCase):
    """Authoritative value-pinning rule over every test in the suite."""

    def test_every_assertraises_site_has_pin_or_is_infra_exception(self) -> None:
        offending = _collect_offending_sites(_TESTS_ROOT)
        if offending:
            formatted = "\n".join(
                f"{path}:{line} — {exc}" for path, line, exc in offending
            )
            self.fail(
                f"{len(offending)} assertRaises site(s) lack a value-pin and "
                f"the exception type is not on the infrastructure-exception "
                f"allowlist:\n{formatted}\n\n"
                f"Add an inline value-pin (e.g. ``with self.assertRaises(X) as "
                f"cm:`` followed by ``self.assertIn('expected', "
                f"str(cm.exception))``) or, for genuine infrastructure "
                f"contracts, use one of: "
                f"{sorted(INFRA_EXCEPTION_ALLOWLIST)}."
            )

    def test_allowlisted_infrastructure_exception_passes_meta_test(self) -> None:
        """A test source whose every assertRaises uses an allowlisted exception
        type without an inline value-pin reports no offending sites.
        """
        sample_text = """
import unittest

class _Sample(unittest.TestCase):
    def test_infra_only(self) -> None:
        with self.assertRaises(FileNotFoundError):
            open('/no/such/file', 'r')
        with self.assertRaises(SystemExit):
            raise SystemExit(1)
        with self.assertRaises(UnicodeDecodeError):
            b'\\xff'.decode('utf-8')
"""
        tree = ast.parse(sample_text)
        offending: list[tuple[str, int, str]] = []

        def record(
            with_node: ast.AST,
            function_stack: list[ast.AST],
        ) -> None:
            for item in with_node.items:  # type: ignore[attr-defined]
                classification = _classify_with_item(item, function_stack)
                if classification is None:
                    continue
                exc_name, lineno = classification
                offending.append(("<sample>", lineno, exc_name))

        _walk_with_function_stack(tree, [], record)
        self.assertEqual([], offending)


if __name__ == "__main__":
    unittest.main()
