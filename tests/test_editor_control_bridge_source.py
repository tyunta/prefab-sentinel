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

import pytest

# Issue #167: this module reads the C# bridge sources from the
# un-mutated ``tools/unity`` tree to verify structural and source-text
# invariants; its assertions are insensitive to mutations applied to
# ``prefab_sentinel/``.  The marker is the inclusion mechanism for
# repository-synchrony tests; mutmut's pytest selection excludes it via
# a single ``-m`` filter.
pytestmark = pytest.mark.source_text_invariant

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools" / "unity"
# Issue #123 — the editor-control bridge is split into a canonical core
# source plus per-functional partial sources.  Source-level invariants
# now apply across the whole class, so the loader concatenates every
# bridge source file as one text and the regex-based extractors operate
# on that concatenation.  The canonical core file retains the name
# ``PrefabSentinel.UnityEditorControlBridge.cs`` so that the
# version-detection rglob and the bump-my-version search/replace anchor
# keep working unchanged.
BRIDGE: Path = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.cs"
_BRIDGE_GLOB = "PrefabSentinel.UnityEditorControlBridge*.cs"


def _read(path: Path) -> str:
    """Read the bridge source.

    When ``path`` resolves to the canonical bridge file, return every
    bridge partial concatenated so the regex-based extractors see the
    full class body.  Other paths are returned verbatim so unrelated
    callers (tests for VRC-SDK / patch-bridge / etc.) keep working.
    """
    if path == BRIDGE:
        parts: list[str] = []
        for cs_file in sorted(TOOLS_DIR.glob(_BRIDGE_GLOB)):
            parts.append(cs_file.read_text(encoding="utf-8"))
        return "\n".join(parts)
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


class TestRecompileAndWaitTimeoutBoundCheck(unittest.TestCase):
    """Issue #134 — the bridge handler must reject non-default
    out-of-range ``timeout_sec`` values with the dedicated error code
    before scheduling compilation, mirroring the client-side range.
    """

    def test_handler_references_upper_bound_constant_and_error_code(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("RecompileAndWaitTimeoutMaxSec", body)
        self.assertIn("EDITOR_CTRL_COMPILE_TIMEOUT_OUT_OF_RANGE", body)

    def test_upper_bound_constant_value(self) -> None:
        source = _read(BRIDGE)
        # The upper-bound literal must equal the Python mirror value
        # (1800 seconds); drift between the two would let an oversized
        # budget slip past one side and trip the other.
        self.assertRegex(
            source,
            r"RecompileAndWaitTimeoutMaxSec\s*=\s*1800",
        )

    def test_handler_rejects_negative_budget_accepts_zero_as_default(self) -> None:
        """A *negative* budget must be rejected with the out-of-range
        code, while a literal ``0`` (the JsonUtility default for an
        omitted ``timeout_sec``) maps to the published default.  The
        bridge guard is therefore ``< 0f``, not ``<= 0f`` — zero is the
        documented "use the default" sentinel.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        # The handler dispatches the out-of-range branch when the
        # request's timeout_sec is not the default sentinel and falls
        # outside the published range.
        self.assertIn("request.timeout_sec", body)
        self.assertIn("EDITOR_CTRL_COMPILE_TIMEOUT_OUT_OF_RANGE", body)


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

    def test_resumer_uses_minus_one_reload_count_threshold(self) -> None:
        """Issue #191: the resumer-driven poll runs after a domain reload has
        already occurred, so the post-reload counter has already advanced past
        any positive snapshot. A threshold of -1 makes the
        ``AssemblyReloadCount > threshold`` check pass on the first tick
        regardless of whether ``[InitializeOnLoad]`` static constructors run
        before or after ``afterAssemblyReload``.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
        # Extract the BuildRecompileAndWaitPoll(...) call inside the resumer
        # branch and verify the third positional argument (reload-count
        # threshold) is the literal -1.
        call_match = re.search(
            r"BuildRecompileAndWaitPoll\s*\(([^;]*?)\)\s*;",
            body,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(
            call_match,
            "Resumer must call BuildRecompileAndWaitPoll(...) to share completion logic",
        )
        args = [a.strip() for a in call_match.group(1).split(",")]
        # Args: responsePath, deadlineUnixMs, callTimeAssemblyMtimeUnixMs,
        # reloadCountThreshold, timeoutDetail
        self.assertGreaterEqual(len(args), 5)
        self.assertEqual(args[3], "-1")


class TestSafeSaveAsPrefabSource(unittest.TestCase):
    """Issue #193 — source-text invariants for ``HandleSafeSaveAsPrefab``.

    The handler body must reference the protect-components payload field,
    the Editor add-component API, the prefab-save API for the re-save
    step, the parent-prefab modification enumeration API, and emit both
    the re-attached-components list field and the orphan-modifications
    list field in the response payload.  All four documented response
    codes must appear as literal strings.
    """

    def test_handler_body_references_required_apis_and_codes(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSafeSaveAsPrefab")
        # Protect-components payload field.
        self.assertIn("protect_components_json", body)
        # Save core helper invocation (the prefab-save API for the
        # initial save and the re-attach re-save step are both routed
        # through ``SaveAsPrefabCore`` / ``PrefabUtility.SaveAsPrefabAsset``).
        self.assertIn("SaveAsPrefabCore", body)
        # Editor add-component API for re-attaching protected types.
        self.assertIn("Undo.AddComponent", body)
        # Parent-prefab modification enumeration entry point (used by
        # the orphan-modifications detection helper).
        self.assertIn("CollectParentModifications", body)
        # Response payload list fields.
        self.assertIn("reattached_components", body)
        self.assertIn("orphan_modifications", body)
        # Documented response codes — value-pinned literal occurrences.
        self.assertIn("EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED", body)
        self.assertIn("EDITOR_CTRL_SAFE_SAVE_PREFAB_BAD_JSON", body)
        self.assertIn("EDITOR_CTRL_SAFE_SAVE_PREFAB_NOT_FOUND", body)
        # The save-failed envelope is emitted from the core helper that
        # the handler delegates to; reading the concatenated bridge text
        # ensures both surfaces remain consistent.
        self.assertIn("EDITOR_CTRL_SAFE_SAVE_PREFAB_FAILED", source)

    def test_orphan_modification_entry_carries_target_object_path_and_property_path(
        self,
    ) -> None:
        source = _read(BRIDGE)
        # The DTO defines both fields; the handler emits orphan entries
        # via ``ComputeOrphanModifications`` which must use both.
        self.assertRegex(
            source,
            r"public sealed class OrphanModificationEntry[^}]*"
            r"target_object_path[^}]*property_path",
        )
        compute_body = _extract_method(source, "ComputeOrphanModifications")
        self.assertIn("target_object_path", compute_body)
        self.assertIn("property_path", compute_body)


class TestBridgePartialLayout(unittest.TestCase):
    """Issue #123 — every named bridge partial source must exist and
    declare the same partial class.  The canonical core source name is
    fixed because both the drift checker and the bump-my-version anchor
    rglob it.
    """

    _EXPECTED_PARTIAL_NAMES = (
        # Canonical core (load-bearing constants live here; see CLAUDE.md
        # version-management section).
        "PrefabSentinel.UnityEditorControlBridge.cs",
        # Pre-existing partials (issue #123).
        "PrefabSentinel.UnityEditorControlBridge.CameraView.cs",
        "PrefabSentinel.UnityEditorControlBridge.SaveInstantiate.cs",
        "PrefabSentinel.UnityEditorControlBridge.RunScriptCompile.cs",
        "PrefabSentinel.UnityEditorControlBridge.ConsoleCapture.cs",
        # Partials introduced by the issue #138 split of the legacy
        # HierarchyComponents.cs partial.
        "PrefabSentinel.UnityEditorControlBridge.MaterialQuery.cs",
        "PrefabSentinel.UnityEditorControlBridge.MaterialWrite.cs",
        "PrefabSentinel.UnityEditorControlBridge.MaterialBatch.cs",
        "PrefabSentinel.UnityEditorControlBridge.BlendShape.cs",
        "PrefabSentinel.UnityEditorControlBridge.Hierarchy.cs",
        "PrefabSentinel.UnityEditorControlBridge.Components.cs",
        "PrefabSentinel.UnityEditorControlBridge.Properties.cs",
        "PrefabSentinel.UnityEditorControlBridge.Menu.cs",
        "PrefabSentinel.UnityEditorControlBridge.Helpers.cs",
        # Partials introduced by the issue #138 split of the legacy
        # UdonSharp.cs partial.
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpAddComponent.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpInvocation.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpFieldWrite.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpListenerWiring.cs",
    )

    # Names of partials that the issue #138 split removed.  These must
    # be absent from disk so the CLAUDE.md inventory and the actual file
    # set agree.
    _DELETED_PARTIAL_NAMES = (
        "PrefabSentinel.UnityEditorControlBridge.HierarchyComponents.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharp.cs",
    )

    def test_every_named_partial_file_exists(self) -> None:
        for name in self._EXPECTED_PARTIAL_NAMES:
            with self.subTest(name=name):
                self.assertTrue((TOOLS_DIR / name).is_file(), f"missing partial: {name}")

    def test_every_partial_declares_same_partial_class(self) -> None:
        # Every partial source must declare exactly one
        # ``public static partial class UnityEditorControlBridge`` so the
        # CLR sees the bridge as a single class spread across files.
        for name in self._EXPECTED_PARTIAL_NAMES:
            with self.subTest(name=name):
                text = (TOOLS_DIR / name).read_text(encoding="utf-8")
                hits = re.findall(
                    r"public\s+static\s+partial\s+class\s+UnityEditorControlBridge\b",
                    text,
                )
                self.assertEqual(
                    1,
                    len(hits),
                    f"{name}: expected exactly 1 partial-class declaration, got {len(hits)}",
                )

    def test_no_non_partial_class_declaration(self) -> None:
        # If any source declares the class without ``partial``, the C#
        # compiler reports a duplicate-class error; this test catches
        # that drift before the editor recompile does.
        for name in self._EXPECTED_PARTIAL_NAMES:
            with self.subTest(name=name):
                text = (TOOLS_DIR / name).read_text(encoding="utf-8")
                self.assertNotRegex(
                    text,
                    r"public\s+static\s+class\s+UnityEditorControlBridge\b",
                    f"{name}: must use partial class, not plain class",
                )

    def test_deleted_partials_are_absent(self) -> None:
        """Issue #138 — the legacy oversized partials must be gone from
        disk so the CLAUDE.md inventory and the live file set match.
        """
        for name in self._DELETED_PARTIAL_NAMES:
            with self.subTest(name=name):
                self.assertFalse(
                    (TOOLS_DIR / name).exists(),
                    f"unexpected leftover partial: {name}",
                )


class TestBridgePartialSizing(unittest.TestCase):
    """Issue #138 — every issue-introduced partial fits inside the
    project's per-partial size guideline (≤400 lines absolute), and any
    partial below 200 lines carries a leading single-line comment that
    names the cohesive concern justifying the smaller size.

    The legacy partials introduced by other issues (CameraView,
    SaveInstantiate, RunScriptCompile) are out of scope for this issue
    and therefore not constrained here.
    """

    # Files tracked by issue #138; the canonical core ``...Bridge.cs``
    # plus the eight partials that came out of the HierarchyComponents
    # split and the four partials that came out of the UdonSharp split.
    _ISSUE_138_PARTIALS = (
        "PrefabSentinel.UnityEditorControlBridge.MaterialQuery.cs",
        "PrefabSentinel.UnityEditorControlBridge.MaterialWrite.cs",
        "PrefabSentinel.UnityEditorControlBridge.MaterialBatch.cs",
        "PrefabSentinel.UnityEditorControlBridge.BlendShape.cs",
        "PrefabSentinel.UnityEditorControlBridge.Hierarchy.cs",
        "PrefabSentinel.UnityEditorControlBridge.Components.cs",
        "PrefabSentinel.UnityEditorControlBridge.Properties.cs",
        "PrefabSentinel.UnityEditorControlBridge.Menu.cs",
        "PrefabSentinel.UnityEditorControlBridge.Helpers.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpAddComponent.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpInvocation.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpFieldWrite.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharpListenerWiring.cs",
    )

    _MAX_LINES = 400
    _SMALL_THRESHOLD = 200

    def test_every_issue_138_partial_is_within_size_bound(self) -> None:
        for name in self._ISSUE_138_PARTIALS:
            with self.subTest(name=name):
                path = TOOLS_DIR / name
                self.assertTrue(path.is_file(), f"missing partial: {name}")
                line_count = sum(1 for _ in path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(
                    line_count,
                    self._MAX_LINES,
                    f"{name}: {line_count} lines exceeds the {self._MAX_LINES}-line cap",
                )

    def test_small_partials_carry_concern_comment(self) -> None:
        """Each partial below 200 lines must have a leading single-line
        ``//`` comment somewhere before the namespace block that names
        the cohesive concern justifying the smaller size.
        """
        for name in self._ISSUE_138_PARTIALS:
            with self.subTest(name=name):
                path = TOOLS_DIR / name
                text = path.read_text(encoding="utf-8")
                line_count = sum(1 for _ in text.splitlines())
                if line_count >= self._SMALL_THRESHOLD:
                    continue
                # Capture every line up to (but excluding) the first
                # ``namespace`` declaration.  The concern comment must
                # appear in that header band.
                header_lines: list[str] = []
                for line in text.splitlines():
                    if line.lstrip().startswith("namespace "):
                        break
                    header_lines.append(line)
                concern_comments = [
                    line for line in header_lines
                    if re.match(r"\s*//\s*\S", line)
                ]
                self.assertTrue(
                    concern_comments,
                    f"{name}: small partial ({line_count} lines) must "
                    "carry a leading single-line concern comment.",
                )


class TestOperationalRulesPartialInventory(unittest.TestCase):
    """Issue #138 — the project's operational rules file (``CLAUDE.md``)
    must list every present per-concern partial and list no absent
    partial in its partial-inventory line. The inventory line is the
    single source of truth on disk for the partial layout.
    """

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _CLAUDE_MD = _PROJECT_ROOT / "CLAUDE.md"
    _PARTIAL_GLOB = "PrefabSentinel.UnityEditorControlBridge*.cs"

    def _disk_partial_concerns(self) -> set[str]:
        """Return the per-concern token (e.g. ``MaterialQuery``) for
        every per-concern partial currently on disk. Excludes the
        canonical core file (``PrefabSentinel.UnityEditorControlBridge.cs``)
        whose name has no concern segment.
        """
        concerns: set[str] = set()
        for path in TOOLS_DIR.glob(self._PARTIAL_GLOB):
            stem = path.stem  # e.g. PrefabSentinel.UnityEditorControlBridge.MaterialQuery
            head = "PrefabSentinel.UnityEditorControlBridge"
            if stem == head:
                continue
            assert stem.startswith(head + "."), stem
            concerns.add(stem[len(head) + 1:])
        return concerns

    def test_inventory_line_lists_every_present_partial(self) -> None:
        text = self._CLAUDE_MD.read_text(encoding="utf-8")
        for concern in sorted(self._disk_partial_concerns()):
            with self.subTest(concern=concern):
                self.assertIn(
                    concern,
                    text,
                    f"CLAUDE.md inventory line is missing concern '{concern}'.",
                )

    def test_inventory_line_lists_no_absent_partial(self) -> None:
        """The legacy partial concern names that issue #138 removed must
        not appear in CLAUDE.md, otherwise the inventory advertises files
        that no longer exist on disk."""
        text = self._CLAUDE_MD.read_text(encoding="utf-8")
        for absent in ("HierarchyComponents", "UdonSharp.cs"):
            with self.subTest(absent=absent):
                # ``UdonSharp`` alone is a substring of UdonSharp* names,
                # so we anchor on the trailing ``.cs`` for that one.
                self.assertNotIn(
                    absent,
                    text,
                    f"CLAUDE.md still references the deleted partial '{absent}'.",
                )


class TestUdonSharpActionWiring(unittest.TestCase):
    """Issue #119 — the three new UdonSharp action names must be present
    in the bridge supported-actions set, the dispatcher must route each
    to its dedicated handler, and the async-action set must be unchanged.
    """

    _NEW_ACTIONS = (
        "editor_add_udonsharp_component",
        "editor_set_udonsharp_field",
        "editor_wire_persistent_listener",
    )

    def test_supported_actions_lists_new_udonsharp_actions(self) -> None:
        source = _read(BRIDGE)
        # Locate the SupportedActions HashSet literal block.
        start = source.find("SupportedActions = new HashSet<string>")
        self.assertNotEqual(-1, start, "SupportedActions block not found")
        block_close = source.find("};", start)
        self.assertNotEqual(-1, block_close, "SupportedActions terminator not found")
        block = source[start:block_close]
        for action in self._NEW_ACTIONS:
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', block)

    def test_async_actions_unchanged_for_udonsharp(self) -> None:
        # The new authoring handlers complete synchronously; if any of
        # them slip into AsyncActions the dispatcher's "no response
        # written" guard would never fire for them.
        source = _read(BRIDGE)
        start = source.find("AsyncActions =")
        self.assertNotEqual(-1, start, "AsyncActions block not found")
        block_close = source.find("};", start)
        block = source[start:block_close]
        for action in self._NEW_ACTIONS:
            with self.subTest(action=action):
                self.assertNotIn(f'"{action}"', block)

    def test_dispatcher_routes_each_new_action(self) -> None:
        # ``RunFromPaths`` switches on ``request.action`` and assigns
        # ``response = HandleX(...)``.  Each new action must route to
        # its named handler.
        source = _read(BRIDGE)
        body = _extract_method(source, "RunFromPaths")
        for action, handler in (
            ("editor_add_udonsharp_component", "HandleAddUdonSharpComponent"),
            ("editor_set_udonsharp_field", "HandleSetUdonSharpField"),
            ("editor_wire_persistent_listener", "HandleWirePersistentListener"),
        ):
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', body)
                self.assertIn(handler, body)


class TestAddUdonSharpComponentHandler(unittest.TestCase):
    """Issue #119 — ``HandleAddUdonSharpComponent`` must perform an
    upsert with prior validation, reuse the existing UdonSharp setup
    and proxy-to-backing synchronisation touchpoints, and return the
    documented response shape.
    """

    def test_handler_present(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleAddUdonSharpComponent")
        self.assertTrue(len(body) > 0)

    def test_handler_references_setup_and_synchronisation(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleAddUdonSharpComponent")
        # The handler delegates component creation to
        # ``InvokeUdonSharpUndoAddComponent`` (a wrapper around the public
        # ``UdonSharpUndo.AddComponent`` entry point, which internally
        # chains ``Undo.AddComponent`` + ``RunBehaviourSetupWithUndo``);
        # ``InvokeUdonSharpCopyProxyToUdon`` performs the proxy-to-backing
        # sync.  Asserting the helper-call names makes the contract resilient
        # to comment edits.
        self.assertIn("InvokeUdonSharpUndoAddComponent", body)
        self.assertIn("InvokeUdonSharpCopyProxyToUdon", body)

    def test_handler_returns_upsert_flag_and_handle(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleAddUdonSharpComponent")
        # Documented payload fields: was_existing flag, applied_fields
        # list, component handle, and program-asset path.
        self.assertIn("was_existing", body)
        self.assertIn("applied_fields", body)
        self.assertIn("udon_program_asset_path", body)
        # The bridge's existing component-handle struct field name is
        # ``component_handle`` per the existing add-component contract.
        self.assertIn("component_handle", body)

    def test_handler_emits_documented_error_codes(self) -> None:
        # Method-contract error codes for type / kind / payload / runtime.
        # The component-creation surface is split from the field-write
        # surface so callers can distinguish "AddComponent failed" from
        # "field write failed mid-application".  Field-failure codes
        # are emitted by helpers that the handler delegates to
        # (``ApplyUdonSharpInitialFields`` for per-field failures,
        # ``InvokeUdonSharpCopyProxyToUdon`` for the sync step), so the
        # test concatenates the handler body with both helpers' bodies
        # to keep the contract assertion intact across refactors.
        source = _read(BRIDGE)
        scope = "\n".join(
            _extract_method(source, name)
            for name in (
                "HandleAddUdonSharpComponent",
                "ApplyUdonSharpInitialFields",
                "InvokeUdonSharpCopyProxyToUdon",
            )
        )
        for code in (
            "EDITOR_CTRL_UDON_ADD_TYPE_NOT_FOUND",
            "EDITOR_CTRL_UDON_ADD_NOT_USHARP",
            "EDITOR_CTRL_UDON_ADD_BAD_FIELDS_JSON",
            "EDITOR_CTRL_UDON_ADD_COMPONENT_FAILED",
            "EDITOR_CTRL_UDON_ADD_FIELD_FAILED",
        ):
            with self.subTest(code=code):
                self.assertIn(code, scope)


class TestSetUdonSharpFieldHandler(unittest.TestCase):
    """Issue #119 — ``HandleSetUdonSharpField`` must locate the field
    via the SerializedObject surface, route VRChat URL fields, and
    synchronise the backing UdonBehaviour with the proxy as one
    transaction.
    """

    def test_handler_present(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSetUdonSharpField")
        self.assertTrue(len(body) > 0)

    def test_handler_uses_serialized_object_surface(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSetUdonSharpField")
        self.assertIn("FindProperty", body)
        # Synchronises the backing UdonBehaviour with the proxy.
        self.assertIn("CopyProxyToUdon", body)

    def test_handler_routes_vrchat_url_fields(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSetUdonSharpField")
        # SerializedProperty for VRCUrl is a Generic property whose
        # nested ``url`` string carries the value.
        self.assertIn("VRCUrl", body)
        self.assertIn('"url"', body)

    def test_handler_emits_documented_error_codes(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSetUdonSharpField")
        for code in (
            "EDITOR_CTRL_UDON_SET_FIELD_NOT_FOUND",
            "EDITOR_CTRL_UDON_SET_FIELD_AMBIGUOUS",
            "EDITOR_CTRL_UDON_SET_FIELD_FIELD_NOT_FOUND",
        ):
            with self.subTest(code=code):
                self.assertIn(code, body)


class TestWirePersistentListenerHandler(unittest.TestCase):
    """Issue #119 — ``HandleWirePersistentListener`` must use the
    published string-mode entry point, walk the existing persistent-call
    array to short-circuit on a match, and mark the source dirty.
    """

    def test_handler_present(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleWirePersistentListener")
        self.assertTrue(len(body) > 0)

    def test_handler_uses_published_entry_point(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleWirePersistentListener")
        # ``UnityEventTools.AddStringPersistentListener`` is the only
        # public string-mode entry point published by Unity.
        self.assertIn("UnityEventTools", body)
        self.assertIn("AddStringPersistentListener", body)

    def test_handler_walks_existing_listeners(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleWirePersistentListener")
        # ``GetPersistentEventCount`` / ``GetPersistentMethodName`` /
        # ``GetPersistentTarget`` walk the persistent-call array.
        self.assertIn("GetPersistentEventCount", body)
        self.assertIn("GetPersistentMethodName", body)
        self.assertIn("GetPersistentTarget", body)

    def test_handler_marks_source_dirty(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleWirePersistentListener")
        # The source component must be marked dirty so the listener
        # persists; ``EditorUtility.SetDirty`` is the documented call.
        self.assertIn("SetDirty", body)

    def test_handler_emits_documented_error_codes(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleWirePersistentListener")
        for code in (
            "EDITOR_CTRL_UDON_WIRE_EVENT_NOT_FOUND",
            "EDITOR_CTRL_UDON_WIRE_METHOD_NOT_FOUND",
            "EDITOR_CTRL_UDON_WIRE_TARGET_NOT_FOUND",
        ):
            with self.subTest(code=code):
                self.assertIn(code, body)


class TestUdonSharpRequestFields(unittest.TestCase):
    """Issue #119 — ``EditorControlRequest`` must carry the new payload
    fields used by the wire-listener handler so the JsonUtility-based
    deserialiser exposes them.  ``editor_set_udonsharp_field`` reuses
    the existing ``field_name`` / ``property_value`` / ``object_reference``
    fields from the property-set surface.
    """

    def test_request_carries_field_name_field(self) -> None:
        source = _read(BRIDGE)
        body = _extract_editor_control_request_body(source)
        self.assertIn("field_name", body)

    def test_request_carries_wire_listener_fields(self) -> None:
        source = _read(BRIDGE)
        body = _extract_editor_control_request_body(source)
        # Source/target identity, method name, and the string argument.
        self.assertIn("event_path", body)
        self.assertIn("target_path", body)
        self.assertIn("method", body)
        self.assertIn("arg", body)

    def test_request_carries_fields_json_for_add_udonsharp(self) -> None:
        source = _read(BRIDGE)
        body = _extract_editor_control_request_body(source)
        self.assertIn("fields_json", body)


class TestBestEffortCatchWarnings(unittest.TestCase):
    """Issue #137 — every best-effort catch site listed below binds the
    exception via a typed parameter and emits exactly one warning whose
    text is ``[PrefabSentinel] {EnclosingMethod}: {ExceptionTypeName}:
    {ExceptionMessage}``. Control flow inside the catch is the
    documented fallback path of the enclosing method.

    The audit is structural (regex on the source text) because the
    catch sites span Editor / Patch / Runtime bridges that the unit-test
    process cannot exercise without a Unity Editor. The assertion
    therefore verifies that the typed-catch line and the warning
    template both occur, and that the untyped empty/comment-only catch
    block at that site is gone.
    """

    # (relative path, enclosing method name, minimum typed-catch count).
    # The minimum count locks two-site method bodies (e.g.
    # ``HandleUdonSharpAddComponentIdempotent`` and
    # ``TryIsFixedBufferProperty``) to require both catches typed; a
    # half-fixed regression therefore fails the audit. After issue
    # #138's split of the legacy HierarchyComponents.cs partial,
    # ``HandleUdonSharpAddComponentIdempotent`` lives in the
    # ``Components.cs`` partial.
    # Issues #152 / #153 raise ``WriteAtomic`` to ``min_typed=2`` so both
    # the outer atomic-write fallback and the inner direct-write
    # fallback are typed catch sites with the warn-level template; a
    # half-fixed regression that reverts the inner stage to bare catch
    # therefore fails the audit.
    _SITES = (
        ("PrefabSentinel.UnityEditorControlBridge.Components.cs",
         "HandleUdonSharpAddComponentIdempotent", 2),
        ("PrefabSentinel.EditorBridge.cs", "ProcessRequest", 1),
        ("PrefabSentinel.EditorBridge.cs", "WriteAtomic", 2),
        ("PrefabSentinel.EditorBridge.cs", "TryDelete", 1),
        ("PrefabSentinel.UnityRuntimeValidationBridge.cs", "WriteResponse", 1),
        ("PrefabSentinel.UnityPatchBridge.cs", "TryIsFixedBufferProperty", 2),
        ("PrefabSentinel.UnityPatchBridge.cs", "TryReadGradientValue", 1),
        ("PrefabSentinel.UnityPatchBridge.cs", "TryReadManagedReferenceTypeHint", 1),
        ("PrefabSentinel.UnityPatchBridge.cs", "WriteResponseSafe", 1),
    )

    @staticmethod
    def _read_method_body(source: str, method_name: str) -> str:
        return _extract_method(source, method_name)

    def test_every_site_emits_typed_catch_with_mandated_warning(self) -> None:
        for file_name, method_name, min_typed in self._SITES:
            with self.subTest(file=file_name, method=method_name):
                text = (TOOLS_DIR / file_name).read_text(encoding="utf-8")
                body = self._read_method_body(text, method_name)
                # The mandated warning string anchors on the enclosing
                # method name plus ``ex.GetType().Name`` and
                # ``ex.Message`` interpolation.
                self.assertRegex(
                    body,
                    rf"\[PrefabSentinel\]\s+{re.escape(method_name)}:\s*\{{[a-zA-Z_]+\.GetType\(\)\.Name\}}",
                    f"{file_name}::{method_name}: missing mandated warning template",
                )
                self.assertRegex(
                    body,
                    r"Debug\.LogWarning\(\s*\$\"\[PrefabSentinel\]",
                    f"{file_name}::{method_name}: missing Debug.LogWarning emission",
                )
                # Permit best-effort *nested* untyped catches that are
                # not in the enumerated 11 (e.g. WriteAtomic's inner
                # File.WriteAllText fallback at line 231 in
                # EditorBridge.cs).  The audit asserts that every
                # documented catch site at this method name is typed:
                # for two-site methods (``HandleUdonSharpAddComponentIdempotent``
                # and ``TryIsFixedBufferProperty``) ``min_typed`` is 2,
                # so a half-fixed regression where one of two catches
                # reverts to untyped is caught.
                typed_catches = re.findall(
                    r"catch\s*\(\s*[A-Za-z_][A-Za-z0-9_.]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\)",
                    body,
                )
                self.assertGreaterEqual(
                    len(typed_catches),
                    min_typed,
                    f"{file_name}::{method_name}: expected at least "
                    f"{min_typed} typed catch (...) blocks; found "
                    f"{len(typed_catches)}",
                )

    def test_udonsharp_idempotent_sites_carry_intentional_comment(self) -> None:
        """The two HandleUdonSharpAddComponentIdempotent sites carry an
        inline comment marking the failure as intentional best-effort
        (per Method Contracts; one comment per site)."""
        path = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Components.cs"
        text = path.read_text(encoding="utf-8")
        body = _extract_method(text, "HandleUdonSharpAddComponentIdempotent")
        # The two catch sites surround reflective Invoke calls
        # (``GetBackingUdonBehaviour`` and ``CreateBehaviourForProxy``).
        intentional_comments = re.findall(
            r"intentional best-effort", body, flags=re.IGNORECASE
        )
        self.assertGreaterEqual(
            len(intentional_comments),
            2,
            "Expected two 'intentional best-effort' comments at the "
            "UdonSharp idempotent-reuse catch sites.",
        )

    def test_write_atomic_inner_fallback_has_no_commentary_only_catch(self) -> None:
        """Issue #152 — ``WriteAtomic`` had an inner ``catch { /* best
        effort */ }`` that swallowed the second-stage write failure with
        no log trace.  Both fallback stages must now carry a typed catch
        with the warn-level template; no permitting commentary
        (``/* best effort */``-style or any other inline comment that
        annotates the catch as silent) is allowed on either site.
        """
        text = (TOOLS_DIR / "PrefabSentinel.EditorBridge.cs").read_text(encoding="utf-8")
        body = _extract_method(text, "WriteAtomic")
        # No bare ``catch { ... }`` (no exception parameter list); a
        # bare catch is the structural marker of the regressed silent
        # site.  The regex matches ``catch`` followed by optional
        # whitespace and an opening brace, with no parenthesised
        # parameter list in between.
        self.assertNotRegex(
            body,
            r"catch\s*\{",
            "WriteAtomic must not contain a bare 'catch {' (issue #152 silent-catch regression)",
        )
        # No ``best effort``-style inline commentary inside the body.
        self.assertNotRegex(
            body,
            r"best\s*effort",
            "WriteAtomic must not annotate a catch site as 'best effort'",
        )
        # Both stages emit the project warn-level template:
        warn_emissions = re.findall(
            r"Debug\.LogWarning\(\s*\$\"\[PrefabSentinel\]\s+WriteAtomic:",
            body,
        )
        self.assertGreaterEqual(
            len(warn_emissions),
            2,
            f"WriteAtomic must emit two warn-level templates (outer + inner); found {len(warn_emissions)}",
        )

    @staticmethod
    def _extract_outer_catch_block(method_body: str) -> str:
        """Return the body (between braces) of the outer
        ``catch (Exception ex) { ... }`` block — the one whose
        ``Exception`` parameter is ``ex`` (the project convention).
        Brace-counts so the inner ``catch (Exception fallbackEx)``
        nested under the outer block remains inside.
        """
        match = re.search(r"catch\s*\(\s*Exception\s+ex\s*\)\s*\{", method_body)
        if not match:
            return ""
        start = match.end()
        depth = 1
        for index in range(start, len(method_body)):
            ch = method_body[index]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return method_body[start:index]
        return ""

    def test_write_response_outer_fallback_uses_warn_level_template(self) -> None:
        """Issue #153 — the two response-writer methods'
        ``Debug.LogError`` outer-fallback log lines were inconsistent
        with the warn-level convention used by every other catch site
        in the bridge family.  Both must now emit the project
        warn-level template, and neither must emit ``Debug.LogError``
        from inside the outer ``catch (Exception ex)`` block.

        Scope (per spec Non-Goals): only the catch-family fallback
        logs.  Non-catch ``Debug.LogError`` calls elsewhere in the
        method body (e.g. ``WriteResponseSafe``'s empty-path log
        statement) are out of scope.
        """
        for file_name, method_name in (
            ("PrefabSentinel.UnityRuntimeValidationBridge.cs", "WriteResponse"),
            ("PrefabSentinel.UnityPatchBridge.cs", "WriteResponseSafe"),
        ):
            with self.subTest(file=file_name, method=method_name):
                text = (TOOLS_DIR / file_name).read_text(encoding="utf-8")
                body = _extract_method(text, method_name)
                outer_catch = self._extract_outer_catch_block(body)
                self.assertTrue(
                    outer_catch,
                    f"{file_name}::{method_name}: outer 'catch (Exception ex)' block not found",
                )
                # The outer catch's first emission must be a
                # ``Debug.LogWarning`` carrying the project template.
                self.assertRegex(
                    outer_catch,
                    r"^\s*Debug\.LogWarning\(\s*\$\"\[PrefabSentinel\]\s+"
                    + re.escape(method_name)
                    + r":",
                    f"{file_name}::{method_name}: outer catch must emit Debug.LogWarning with project template",
                )
                # No ``Debug.LogError`` inside the catch-family scope
                # (the outer catch and its nested inner catch).
                self.assertNotRegex(
                    outer_catch,
                    r"Debug\.LogError\(",
                    f"{file_name}::{method_name}: outer catch must not emit Debug.LogError; warn-level convention applies",
                )


if __name__ == "__main__":
    unittest.main()
