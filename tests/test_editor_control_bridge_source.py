"""Source-level regression tests for UnityEditorControlBridge compile fixes.

Reads the C# source file and verifies structural invariants that prevent
accidental reversion of fixes: S1 (GetHierarchyPath dedup), S4
(ApplyPropertyValue type coverage), I2 (batch_create parent warning),
I3 (BatchObjectSpec.components field and attachment logic).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools" / "unity"
BRIDGE: Path = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.cs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_method(source: str, method_name: str) -> str:
    """Extract the full body of a named method from C# source (brace-counting)."""
    pattern = re.compile(
        rf"(private|internal|public)\s+static\s+(?:async\s+)?\S+(?:\s*<[^>]+>)?\s+{re.escape(method_name)}(?:\s*<[^>]+>)?\s*\(",
    )
    match = pattern.search(source)
    if not match:
        raise AssertionError(f"Method {method_name} not found in source")

    start = match.start()
    brace_count = 0
    found_open = False
    for i in range(start, len(source)):
        if source[i] == "{":
            brace_count += 1
            found_open = True
        elif source[i] == "}":
            brace_count -= 1
            if found_open and brace_count == 0:
                return source[start : i + 1]

    raise AssertionError(f"Could not find closing brace for {method_name}")


class TestGetHierarchyPathDedup(unittest.TestCase):
    """S1: Only one GetHierarchyPath definition should remain."""

    def test_single_gethierarchypath_definition(self) -> None:
        source = _read(BRIDGE)
        matches = re.findall(r"private static string GetHierarchyPath\(", source)
        self.assertEqual(
            len(matches),
            1,
            f"Expected exactly 1 GetHierarchyPath definition, found {len(matches)}",
        )

    def test_gethierarchypath_takes_transform(self) -> None:
        source = _read(BRIDGE)
        self.assertIn("GetHierarchyPath(Transform t)", source)


class TestApplyPropertyValueTypes(unittest.TestCase):
    """S4: ApplyPropertyValue must handle Color, Vector2, Vector4, ObjectReference."""

    def test_apply_property_value_handles_color(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ApplyPropertyValue")
        self.assertIn("SerializedPropertyType.Color", body)

    def test_apply_property_value_color_alpha_default(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ApplyPropertyValue")
        self.assertIn("aParsed", body)

    def test_apply_property_value_handles_vector2(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ApplyPropertyValue")
        self.assertIn("SerializedPropertyType.Vector2", body)

    def test_apply_property_value_handles_vector4(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ApplyPropertyValue")
        self.assertIn("SerializedPropertyType.Vector4", body)

    def test_apply_property_value_handles_object_reference(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ApplyPropertyValue")
        self.assertIn("SerializedPropertyType.ObjectReference", body)


class TestBatchCreateParentWarning(unittest.TestCase):
    """I2: HandleEditorBatchCreate must emit a warning when parent not found."""

    def test_batch_create_warns_on_parent_not_found(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorBatchCreate")
        self.assertIn("Parent not found", body)
        self.assertIn("warning", body)


class TestBatchObjectSpecComponents(unittest.TestCase):
    """I3: BatchObjectSpec must have a components field and handler logic."""

    def test_batch_object_spec_has_components_field(self) -> None:
        source = _read(BRIDGE)
        spec_start = source.find("class BatchObjectSpec")
        self.assertNotEqual(spec_start, -1, "BatchObjectSpec class not found")
        brace_count = 0
        found_open = False
        spec_body = ""
        for i in range(spec_start, len(source)):
            if source[i] == "{":
                brace_count += 1
                found_open = True
            elif source[i] == "}":
                brace_count -= 1
                if found_open and brace_count == 0:
                    spec_body = source[spec_start : i + 1]
                    break
        self.assertIn("public string[] components", spec_body)

    def test_batch_create_resolves_component_types(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorBatchCreate")
        self.assertIn("ResolveComponentType", body)

    def test_batch_create_warns_on_component_not_found(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorBatchCreate")
        self.assertIn("Component type not found", body)


class TestRunScriptShortPoll(unittest.TestCase):
    """Task 8: HandleRunScript bounded compile poll + entry-type retry."""

    def test_compile_poll_uses_iscompiling_with_timeout(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRunScript")
        self.assertIn("EditorApplication.isCompiling", body)
        # Bounded compile poll has a timeout symbol referenced.
        self.assertTrue(
            re.search(r"compile.*timeout", body, re.IGNORECASE)
            or re.search(r"DateTime\.UtcNow|Stopwatch", body),
            "HandleRunScript must poll within a bounded compile timeout window",
        )

    def test_entry_type_lookup_has_second_bounded_loop(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRunScript")
        # FindTempScriptType is called more than once after the compile poll
        # (initial call + bounded retry loop body).
        find_calls = body.count("FindTempScriptType()")
        self.assertGreaterEqual(
            find_calls,
            2,
            (
                "HandleRunScript must invoke FindTempScriptType from a bounded "
                "retry loop after the compile poll settles "
                f"(found {find_calls} call(s))"
            ),
        )

    def test_compile_pending_message_hints_persistent_helper(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRunScript")
        # Compile-pending response must point users at the persistent helper
        # alternative.
        self.assertIn("editor_execute_menu_item", body)


class TestSetPropertyGameObject(unittest.TestCase):
    """Task 9: GameObject-level property writes with allowlist."""

    def test_handles_gameobject_target_special_case(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        # The branch must construct a SerializedObject directly from the
        # GameObject when the caller addresses the GameObject itself.
        self.assertIn("new SerializedObject(go)", body)

    def test_allowlist_names_present(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        for name in ("m_IsActive", "m_Layer", "m_Name", "m_TagString"):
            self.assertIn(name, body, f"missing GameObject allowlist name: {name}")

    def test_out_of_allowlist_returns_dedicated_code(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("EDITOR_CTRL_SET_PROP_GAMEOBJECT_PROP_NOT_ALLOWED", body)


class TestSetPropertySuggestions(unittest.TestCase):
    """Task 10: Property-name suggestions on EDITOR_CTRL_SET_PROP_FIELD_NOT_FOUND."""

    def test_emits_suggestions_on_not_found(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("data.suggestions", body)
        self.assertIn("Did you mean", body)

    def test_walks_serialized_iterator(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("GetIterator()", body)
        self.assertIn("NextVisible(true)", body)

    def test_zero_candidates_branch_present(self) -> None:
        """Zero-candidates path must NOT append 'Did you mean' to the message."""
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        # The body should reference an empty suggestions array fallback —
        # i.e. when SuggestSimilar returns Length 0 the response is built
        # with an empty array. We require both `suggestions` (when non-empty)
        # and the `Length == 0` short-circuit somewhere in the body.
        self.assertTrue(
            re.search(r"suggestions\.Length\s*==\s*0", body)
            or re.search(r"suggestions\.Length\s*>\s*0", body)
            or re.search(r"suggestions\.Length\s*<=\s*0", body),
            "Expected an explicit zero-candidates branch on suggestions.Length",
        )


def _extract_editor_control_request_body(source: str) -> str:
    """Return the text between the opening and closing braces of
    ``public sealed class EditorControlRequest``."""
    start = source.find("public sealed class EditorControlRequest")
    if start == -1:
        raise AssertionError("EditorControlRequest class not found")
    brace = 0
    opened = False
    for i in range(start, len(source)):
        ch = source[i]
        if ch == "{":
            brace += 1
            opened = True
        elif ch == "}":
            brace -= 1
            if opened and brace == 0:
                return source[start : i + 1]
    raise AssertionError("Could not locate closing brace of EditorControlRequest")


class TestForceReimportSupport(unittest.TestCase):
    """Task 11: HandleRecompileScripts honors a force_reimport request flag."""

    def test_request_carries_force_reimport_field(self) -> None:
        source = _read(BRIDGE)
        body = _extract_editor_control_request_body(source)
        self.assertIn("public bool force_reimport", body)

    def test_recompile_carries_force_reimport_plumbing(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileScripts")
        self.assertIn("force_reimport", body)
        self.assertIn("ImportAssetOptions.ForceUpdate", body)
        self.assertIn("ImportAssetOptions.ForceSynchronousImport", body)

    def test_per_path_failure_emits_warning_diagnostic(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileScripts")
        self.assertIn("warning", body)


class TestCompileTimeoutRequestField(unittest.TestCase):
    """Task 8: EditorControlRequest carries the per-request compile_timeout
    budget consumed by HandleRunScript's bounded compile poll."""

    def test_request_carries_compile_timeout_field(self) -> None:
        source = _read(BRIDGE)
        body = _extract_editor_control_request_body(source)
        self.assertIn("public int compile_timeout", body)

    def test_run_script_consumes_request_compile_timeout(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRunScript")
        # The handler must reference the request's compile_timeout field
        # and select between it and the bridge default for its compile poll.
        self.assertIn("request.compile_timeout", body)
        # The bounded poll's loop condition must use the resolved budget,
        # not the constant directly, so a caller-supplied value takes effect.
        self.assertRegex(body, r"compileWatch\.ElapsedMilliseconds\s*<\s*compilePollMs")


class TestAsmdefAssemblyDisambiguation(unittest.TestCase):
    """Bridge: the two iteration sites that scan AppDomain assemblies must use
    a fully qualified `System.Reflection.Assembly` so the file compiles
    regardless of which other namespaces are imported."""

    def test_two_iteration_sites_use_fully_qualified_assembly(self) -> None:
        source = _read(BRIDGE)
        # Both sites are inside HandleEditorAddComponent.
        body = _extract_method(source, "HandleEditorAddComponent")
        occurrences = body.count("System.Reflection.Assembly")
        self.assertGreaterEqual(
            occurrences,
            2,
            (
                "Expected the two AppDomain.GetAssemblies iteration sites in "
                "HandleEditorAddComponent to use the fully qualified "
                f"System.Reflection.Assembly, found {occurrences} occurrence(s)"
            ),
        )


if __name__ == "__main__":
    unittest.main()
