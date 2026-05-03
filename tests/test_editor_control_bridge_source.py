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


class TestHandleEditorSetPropertyQuaternion(unittest.TestCase):
    """Issue #111 — HandleEditorSetProperty must accept the Quaternion
    type, require xyzw four-component input, enforce a unit-norm
    tolerance, and reject non-normalized values with a dedicated code.
    """

    def test_handle_editor_set_property_handles_quaternion(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("SerializedPropertyType.Quaternion", body)

    def test_handle_editor_set_property_quaternion_requires_four_components(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        # The wrong-component-count branch must reuse the existing
        # type-mismatch envelope and name the four-component requirement.
        self.assertIn("EDITOR_CTRL_SET_PROP_TYPE_MISMATCH", body)
        self.assertRegex(body, r"Quaternion[^\n]*4")

    def test_handle_editor_set_property_quaternion_unit_norm_code(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        # Non-unit norm rejection must use a dedicated severity-error code
        # (issue #111). The code lives next to the other SET_PROP codes.
        self.assertIn("EDITOR_CTRL_SET_PROP_QUATERNION_NOT_NORMALIZED", body)

    def test_handle_editor_set_property_quaternion_tolerance_constant(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        # Tolerance literal used in the norm check (1e-4). The choice is
        # documented in the issue spec; a regression must keep that value.
        self.assertIn("1e-4", body)


class TestHandleCaptureConsoleLogsContract(unittest.TestCase):
    """Issue #113 — capture handler must accept ordering + opaque cursor,
    reject unknown ordering / malformed cursor with dedicated codes,
    and emit a continuation token whenever more matches remain.
    """

    def test_request_struct_carries_order_field(self) -> None:
        source = _read(BRIDGE)
        # Request struct holds the ordering keyword forwarded by the wrapper.
        self.assertIn("public string order", source)

    def test_request_struct_carries_cursor_field(self) -> None:
        source = _read(BRIDGE)
        # Request struct holds the opaque continuation token.
        self.assertIn("public string cursor", source)

    def test_response_data_carries_next_cursor_field(self) -> None:
        source = _read(BRIDGE)
        # Response payload field for the opaque continuation token.
        self.assertIn("public string next_cursor", source)

    def test_console_log_entry_carries_sequence_id(self) -> None:
        source = _read(BRIDGE)
        # Per-entry monotonic sequence identifier so the cursor token can
        # name an ingestion position unambiguously.
        self.assertRegex(source, r"public\s+long\s+sequence_id")

    def test_handler_rejects_unknown_ordering(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleCaptureConsoleLogs")
        self.assertIn("EDITOR_CTRL_INVALID_ORDER", body)
        # Message lists both accepted keywords.
        self.assertIn("newest_first", body)
        self.assertIn("oldest_first", body)

    def test_handler_rejects_malformed_cursor(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleCaptureConsoleLogs")
        self.assertIn("EDITOR_CTRL_INVALID_CURSOR", body)


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
    """Issue #108: the per-frame ``RunScriptPollFrame`` observes the
    documented completion conditions (``EditorApplication.isCompiling``,
    assembly mtime advance, deadline) and locates the freshly compiled
    type, returning to wait for the next frame whenever the conditions
    have not yet settled.  The compile-pending response surfaced when the
    deadline elapses still hints at the persistent helper alternative.
    """

    def test_compile_poll_uses_iscompiling_with_timeout(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "RunScriptPollFrame")
        self.assertIn("EditorApplication.isCompiling", body)
        # The deadline derives from the per-request compile timeout; the
        # poller compares the current Unix-ms timestamp against it.
        self.assertIn("deadlineUnixMs", body)

    def test_entry_type_lookup_polls_each_frame(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "RunScriptPollFrame")
        # Each frame attempts ``FindTempScriptType``; a ``null`` result
        # returns control to the editor so the next frame retries.
        self.assertIn("FindTempScriptType()", body)
        self.assertRegex(
            body,
            r"scriptType\s*==\s*null\s*\)\s*return",
        )

    def test_compile_pending_message_hints_persistent_helper(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "RunScriptPollFrame")
        # Compile-pending response must point users at the persistent helper
        # alternative when the deadline elapses without compile settling.
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
        # The async-runner registry's deadline must derive from the
        # resolved budget so a caller-supplied value takes effect across
        # the frame-poll lifetime.
        self.assertRegex(body, r"deadlineMs\s*=\s*callTimeMs\s*\+\s*compilePollMs")


class TestAsmdefAssemblyDisambiguation(unittest.TestCase):
    """Bridge: every iteration site that scans AppDomain assemblies must use
    a fully qualified ``System.Reflection.Assembly`` so the file compiles
    regardless of which other namespaces are imported.

    ``HandleEditorAddComponent`` originally had two such iteration sites; the
    duplicate that re-resolved ``UdonSharpBehaviour`` was removed (DRY — the
    type is already cached as ``usbTypeForGuard`` via
    ``ResolveUdonSharpBehaviourType``). The remaining site (the
    ``UdonSharpProgramAsset`` lookup) must still be fully qualified.
    """

    def test_remaining_iteration_site_uses_fully_qualified_assembly(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorAddComponent")
        occurrences = body.count("System.Reflection.Assembly")
        self.assertGreaterEqual(
            occurrences,
            1,
            (
                "Expected at least one fully qualified "
                "``System.Reflection.Assembly`` in HandleEditorAddComponent "
                "(UdonSharpProgramAsset lookup), "
                f"found {occurrences} occurrence(s)"
            ),
        )


class TestConsoleLogBufferCapacityVisibility(unittest.TestCase):
    """Issue #131: ``ConsoleLogBuffer.DefaultCapacity`` must be ``public
    const int`` so the request validator and the Python mirror share a
    single named value.  The Python mirror lives in ``bridge_constants``.
    """

    def test_capacity_declaration_is_public_const(self) -> None:
        source = _read(BRIDGE)
        self.assertRegex(
            source,
            r"public\s+const\s+int\s+DefaultCapacity\s*=\s*\d+",
        )


class TestHandleCaptureConsoleLogsBoundCheck(unittest.TestCase):
    """Issue #131: the console-capture handler rejects ``max_entries``
    outside the inclusive ``[1, ConsoleLogBuffer.DefaultCapacity]`` range
    with the dedicated bridge-side out-of-range error code, before
    consulting the buffer.
    """

    def test_handler_references_published_capacity_and_error_code(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleCaptureConsoleLogs")
        self.assertIn("ConsoleLogBuffer.DefaultCapacity", body)
        self.assertIn("EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE", body)


class TestRecompileAndWaitDispatch(unittest.TestCase):
    """Issue #118: the synchronous recompile-and-wait action is wired
    into both the supported-action set and the asynchronous-action set.
    The handler must reference the documented completion signals (the
    compiled-assembly file and the post-reload signal) and the timeout
    error code.
    """

    def test_supported_action_lists_recompile_and_wait(self) -> None:
        source = _read(BRIDGE)
        # The supported-action set is the literal hashset initialiser.
        match = re.search(
            r"SupportedActions\s*=\s*new\s+HashSet<string>\s*\{[^}]*\}",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn('"editor_recompile_and_wait"', match.group(0))

    def test_async_action_lists_recompile_and_wait(self) -> None:
        source = _read(BRIDGE)
        match = re.search(
            r"AsyncActions\s*=\s*new\s+System\.Collections\.Generic\."
            r"HashSet<string>\s*\{[^}]*\}",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn('"editor_recompile_and_wait"', match.group(0))

    def test_async_action_lists_run_script(self) -> None:
        """Issue #108: the script-runner action completes asynchronously
        through the run-script registry; source must reflect that."""
        source = _read(BRIDGE)
        match = re.search(
            r"AsyncActions\s*=\s*new\s+System\.Collections\.Generic\."
            r"HashSet<string>\s*\{[^}]*\}",
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn('"run_script"', match.group(0))

    def test_recompile_and_wait_handler_references_completion_signals(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        # The handler must reference both completion signals: the
        # compiled-assembly file (via the published rel-path constant
        # whose value is ``Library/ScriptAssemblies/Assembly-CSharp.dll``)
        # and the post-reload signal (``afterAssemblyReload``).
        self.assertIn("CompiledAssemblyRelPath", body)
        self.assertIn("afterAssemblyReload", body)
        # The constant itself must resolve to the canonical path.
        self.assertRegex(
            source,
            r'CompiledAssemblyRelPath\s*=\s*\n?\s*"Library/ScriptAssemblies/Assembly-CSharp\.dll"',
        )

    def test_recompile_and_wait_handler_references_timeout_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", body)


class TestRunScriptNoSleep(unittest.TestCase):
    """Issue #108: ``HandleRunScript`` must not block the main thread on
    a ``Thread.Sleep`` busy-wait.  Replaced with an
    ``EditorApplication.update`` polling registry so the Editor stays
    responsive during the compile-and-reload window.
    """

    def test_run_script_handler_has_no_thread_sleep(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRunScript")
        self.assertNotIn("Thread.Sleep", body)


class TestRecompileAndWaitDomainReloadResume(unittest.TestCase):
    """Issue #118: after a domain reload, the in-flight
    ``editor_recompile_and_wait`` request must be resumed by
    ``ResumePendingAsyncRunners`` so completion drainage continues from
    the new AppDomain.  The completion-signal logic is centralised in
    ``BuildRecompileAndWaitPoll`` (DRY); the resume branch dispatches
    on the persisted action string and re-installs the shared poll.
    """

    def test_resume_wires_recompile_and_wait_action(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
        # Resume branch must dispatch on the persisted action string.
        self.assertIn('"editor_recompile_and_wait"', body)

    def test_resume_delegates_to_shared_poll_builder(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
        # Shared helper avoids duplicating the completion-signal checks
        # between first-dispatch and post-reload paths.
        self.assertIn("BuildRecompileAndWaitPoll", body)
        # The rehydrated entry must be reattached to the in-flight
        # registry so ``Complete`` can later drain it.
        self.assertIn("RehydrateEntry", body)

    def test_shared_poll_observes_documented_completion_signals(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "BuildRecompileAndWaitPoll")
        # Three completion signals: compile finished, assembly mtime
        # advanced beyond the call-time snapshot, and the post-reload
        # counter ticked past the threshold supplied by the call site.
        self.assertIn("EditorApplication.isCompiling", body)
        self.assertIn("ReadAssemblyMtimeUnixMs", body)
        self.assertIn("AssemblyReloadCount", body)

    def test_shared_poll_emits_success_and_timeout_envelopes(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "BuildRecompileAndWaitPoll")
        self.assertIn("EDITOR_CTRL_RECOMPILE_AND_WAIT_OK", body)
        self.assertIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", body)


if __name__ == "__main__":
    unittest.main()
