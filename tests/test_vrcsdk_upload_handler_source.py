"""Source-level regression tests for VRCSDKUploadHandler compile fixes.

These tests read the C# source files and verify the three compile error fixes
(CS0117, CS1501, CS0246) remain correct.  They prevent accidental reversion.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools" / "unity"
UPLOAD_HANDLER = TOOLS_DIR / "PrefabSentinel.VRCSDKUploadHandler.cs"
BRIDGE = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.cs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestCS0117GetBuildTargetGroup(unittest.TestCase):
    """CS0117: EditorUserBuildSettings.GetBuildTargetGroup does not exist."""

    def test_should_use_buildpipeline_not_editoruserbuildsettings(self) -> None:
        """BuildPipeline.GetBuildTargetGroup is the correct API."""
        source = _read(UPLOAD_HANDLER)
        self.assertIn(
            "BuildPipeline.GetBuildTargetGroup(originalTarget)",
            source,
        )

    def test_should_not_contain_nonexistent_api(self) -> None:
        """EditorUserBuildSettings.GetBuildTargetGroup must not appear."""
        source = _read(UPLOAD_HANDLER)
        self.assertNotIn(
            "EditorUserBuildSettings.GetBuildTargetGroup",
            source,
        )


class TestCS1501BuildErrorVisibility(unittest.TestCase):
    """CS1501: 3-arg BuildError must be internal (not private)."""

    def test_three_arg_build_error_should_be_internal(self) -> None:
        """The 3-argument overload must be 'internal static'."""
        source = _read(BRIDGE)
        pattern = re.compile(
            r"internal\s+static\s+EditorControlResponse\s+BuildError\s*\("
            r"\s*string\s+\w+\s*,\s*string\s+\w+\s*,\s*EditorControlData\s+\w+\s*\)",
        )
        self.assertRegex(source, pattern)

    def test_three_arg_build_error_should_not_be_private(self) -> None:
        """No private 3-argument BuildError overload should remain."""
        source = _read(BRIDGE)
        pattern = re.compile(
            r"private\s+static\s+EditorControlResponse\s+BuildError\s*\("
            r"\s*string\s+\w+\s*,\s*string\s+\w+\s*,\s*EditorControlData\s+\w+\s*\)",
        )
        self.assertIsNone(
            pattern.search(source),
            "3-arg BuildError must not be private",
        )


class TestCS0246BuildAndUploadWorldReflection(unittest.TestCase):
    """CS0246: IVRCSdkWorldBuilderApi direct type reference must be removed."""

    def test_should_not_reference_world_builder_type_directly(self) -> None:
        """No direct IVRCSdkWorldBuilderApi type usage (only string literals for reflection)."""
        source = _read(UPLOAD_HANDLER)
        # Direct type references: TryGetBuilder<IVRCSdkWorldBuilderApi> or casts
        direct_ref = re.compile(
            r"(?<!\")"  # not inside a string literal
            r"IVRCSdkWorldBuilderApi"
            r"(?![\w\"])",  # not followed by word char or quote
        )
        # Filter out string literals and comments
        lines = source.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            # Remove string literals for checking
            no_strings = re.sub(r'"[^"]*"', '""', stripped)
            self.assertIsNone(
                direct_ref.search(no_strings),
                f"Direct IVRCSdkWorldBuilderApi reference found: {stripped}",
            )

    def test_should_resolve_world_builder_via_reflection(self) -> None:
        """BuildAndUploadWorldAsync must pass the world builder type string to ResolveBuilder."""
        source = _read(UPLOAD_HANDLER)
        world_method = _extract_method(source, "BuildAndUploadWorldAsync")
        self.assertIn(
            "VRC.SDK3.Editor.IVRCSdkWorldBuilderApi, VRC.SDK3.Editor",
            world_method,
        )

    def test_should_use_reflection_for_build_and_upload_call(self) -> None:
        """BuildAndUploadWorldAsync must resolve BuildAndUpload via Array.Find reflection."""
        source = _read(UPLOAD_HANDLER)
        world_method = _extract_method(source, "BuildAndUploadWorldAsync")
        self.assertIn("Array.Find(", world_method)
        self.assertIn('"BuildAndUpload"', world_method)

    def test_resolve_builder_helper_exists_with_reflection_pattern(self) -> None:
        """ResolveBuilder helper must contain the shared reflection boilerplate."""
        source = _read(UPLOAD_HANDLER)
        helper = _extract_method(source, "ResolveBuilder")
        for pattern_desc, pattern in [
            ("Type.GetType reflection", "System.Type.GetType("),
            ("GetMethods + Array.Find", "Array.Find("),
            ("TryGetBuilder name check", '"TryGetBuilder"'),
            ("MakeGenericMethod", "MakeGenericMethod("),
        ]:
            self.assertIn(pattern, helper, f"ResolveBuilder missing: {pattern_desc}")

    def test_invoke_static_async_helper_exists(self) -> None:
        """InvokeStaticAsync helper must invoke methods via reflection and await the result."""
        source = _read(UPLOAD_HANDLER)
        helper = _extract_method(source, "InvokeStaticAsync")
        self.assertIn('GetProperty("Result")', helper)
        self.assertIn("await task", helper)

    def test_both_methods_use_shared_helpers(self) -> None:
        """Both BuildAndUploadAvatarAsync and BuildAndUploadWorldAsync must call the shared helpers."""
        source = _read(UPLOAD_HANDLER)
        avatar_method = _extract_method(source, "BuildAndUploadAvatarAsync")
        world_method = _extract_method(source, "BuildAndUploadWorldAsync")

        for method_name, method_body in [
            ("Avatar", avatar_method),
            ("World", world_method),
        ]:
            self.assertIn(
                "ResolveBuilder(",
                method_body,
                f"{method_name} method must call ResolveBuilder",
            )
            self.assertIn(
                "await",
                method_body,
                f"{method_name} method must await async operations",
            )


class TestHandleAsyncFullProtection(unittest.TestCase):
    """HandleAsync must wrap entire body in try-catch (async void must never leak exceptions)."""

    def test_no_code_after_outermost_catch(self) -> None:
        """All logic in HandleAsync must be inside the outermost try-catch."""
        source = _read(UPLOAD_HANDLER)
        method_body = _extract_method(source, "HandleAsync")
        # After the outermost catch block closes, only closing brace of the method should remain
        # Find the last "catch (Exception" which is the outermost catch
        last_catch = method_body.rfind("catch (Exception")
        self.assertGreater(last_catch, 0, "Outermost catch not found in HandleAsync")
        # After the catch block, find the WriteResponse inside it
        after_catch = method_body[last_catch:]
        self.assertIn("WriteResponse(responsePath", after_catch)

    def test_writeresponse_not_after_finally(self) -> None:
        """WriteResponse calls must not appear between finally-close and method-close unprotected."""
        source = _read(UPLOAD_HANDLER)
        method_body = _extract_method(source, "HandleAsync")
        # The finally block should be nested INSIDE the outer try, not at the same level
        # Verify: "finally" appears before the outermost catch
        finally_pos = method_body.find("finally")
        last_catch_pos = method_body.rfind("catch (Exception")
        self.assertGreater(
            last_catch_pos,
            finally_pos,
            "Outermost catch must come after finally (finally is nested inside outer try)",
        )


class TestVRCApiTypeConstant(unittest.TestCase):
    """VRCApi assembly-qualified type name must be a single constant, not duplicated."""

    def test_vrcapi_type_defined_as_constant(self) -> None:
        """VRCApiTypeName constant must be declared at class level."""
        source = _read(UPLOAD_HANDLER)
        self.assertIn(
            'private const string VRCApiTypeName = "VRC.SDKBase.Editor.Api.VRCApi, VRC.SDKBase.Editor"',
            source,
        )

    def test_vrcapi_type_string_not_duplicated(self) -> None:
        """The literal VRCApi type string must appear exactly once (in the constant declaration)."""
        source = _read(UPLOAD_HANDLER)
        literal = '"VRC.SDKBase.Editor.Api.VRCApi, VRC.SDKBase.Editor"'
        count = source.count(literal)
        self.assertEqual(count, 1, f"VRCApi type string literal appears {count} times, expected 1")


def _extract_method(source: str, method_name: str) -> str:
    """Extract the body of a named method from C# source (brace-counting)."""
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


if __name__ == "__main__":
    unittest.main()
