"""Source-level regression tests for UnityEditorControlBridge compile fixes.

Reads the C# source file and verifies structural invariants that prevent
accidental reversion of fixes: S1 (GetHierarchyPath dedup), S4
(ApplyPropertyValue type coverage), I2 (batch_create parent warning),
I3 (BatchObjectSpec.components field and attachment logic).
"""

from __future__ import annotations

import json
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

# H-track xUnit migration: pure-logic decisions were extracted from the
# bridge handlers into Unity-free classes under ``tools/unity/``. The
# behavioral coverage of those classes now lives in ``tests/csharp/``;
# the source-text tests below retain only Tier 3 delegation-invariant
# and constant-value-pin assertions, reading the relocated declarations
# from these dedicated files.
EDITOR_CONTROL_REQUEST: Path = TOOLS_DIR / "PrefabSentinel.Dispatch.EditorControlRequest.cs"
ACTION_REGISTRY: Path = TOOLS_DIR / "PrefabSentinel.Dispatch.ActionRegistry.cs"
INPUT_VALIDATORS: Path = TOOLS_DIR / "PrefabSentinel.Properties.InputValidators.cs"
EDITOR_SCRIPT_PATH_CLASSIFIER: Path = (
    TOOLS_DIR / "PrefabSentinel.MenuScriptWatch.EditorScriptPathClassifier.cs"
)
CONSOLE_REQUEST_VALIDATOR: Path = TOOLS_DIR / "PrefabSentinel.ConsoleCapture.RequestValidator.cs"
RUN_SCRIPT_COMPILE_VALIDATORS: Path = TOOLS_DIR / "PrefabSentinel.RunScriptCompile.Validators.cs"
RUN_SCRIPT_COMPILE_REDACTION: Path = TOOLS_DIR / "PrefabSentinel.RunScriptCompile.Redaction.cs"
UI_ELEMENT_ALLOWLIST: Path = TOOLS_DIR / "PrefabSentinel.UiElement.Allowlist.cs"


# Issue #310: C# comment stripping reused from the patch-bridge source
# tests so the screenshot routing scan does not flag a literal that
# appears only inside ``// ...`` or ``/* ... */`` documentation.
_CS_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_CS_LINE_COMMENT_RE = re.compile(r"//[^\n]*")


def _strip_cs_comments(source: str) -> str:
    return _CS_LINE_COMMENT_RE.sub("", _CS_BLOCK_COMMENT_RE.sub("", source))


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


def _extract_braced_block(source: str, start: int, context: str) -> str:
    """Return the body of a brace-delimited block.

    ``start`` must point one past an opening ``{`` already consumed by
    the caller (typically ``match.end()`` of a regex that ends in
    ``\\{``); the returned slice runs up to — but not including — the
    matching closing ``}``, with nested braces accounted for. ``context``
    is a human label used in the AssertionError raised when the input
    runs out before the matching close-brace is found.
    """
    depth = 1
    for index in range(start, len(source)):
        ch = source[index]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"Could not find closing brace of {context}")


def _schedule_catch_body(handler_body: str) -> str:
    """Return the body of the catch block surrounding
    ``CompilationPipeline.RequestScriptCompilation()``.

    Shared by the schedule-failure code-pin tests (issue #204) and the
    schedule-failure sanitization tests (issue #214) so the regex that
    locates the catch arm has a single source of truth.
    """
    match = re.search(
        r"CompilationPipeline\.RequestScriptCompilation\(\)\s*;\s*\}\s*"
        r"catch\s*\(\s*Exception\s+\w+\s*\)\s*\{",
        handler_body,
    )
    if match is None:
        raise AssertionError(
            "catch block surrounding RequestScriptCompilation not found"
        )
    return _extract_braced_block(
        handler_body, match.end(), "RequestScriptCompilation schedule-failure catch"
    )


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
    """S4: ApplyPropertyValue value-kind coverage.

    Post H-track migration the per-type literal parsing (Color/Vector2/
    Vector4/ObjectReference) was extracted into the Unity-free
    ``PropertyValueParser``; that behavioral coverage now lives in
    ``tests/csharp/PropertiesPureLogicTests.cs``. This source-text test
    retains only the Tier 3 delegation invariant: the bridge handler must
    still route parsing through ``PropertyValueParser.TryParse``.
    """

    def test_apply_property_value_delegates_to_property_value_parser(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ApplyPropertyValue")
        self.assertIn("PropertyValueParser.TryParse", body)


class TestHandleEditorSetPropertyQuaternion(unittest.TestCase):
    """Issue #111 — HandleEditorSetProperty Quaternion handling.

    Post H-track migration the arity and unit-norm validation logic was
    extracted into the Unity-free ``QuaternionInputValidator``; that
    behavioral coverage now lives in
    ``tests/csharp/PropertiesPureLogicTests.cs``. This source-text test
    retains the Tier 3 delegation invariant (the handler routes through
    ``QuaternionInputValidator.Validate``) plus a constant-value pin on
    the relocated ``NormTolerance`` literal.
    """

    def test_handle_editor_set_property_handles_quaternion(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("SerializedPropertyType.Quaternion", body)

    def test_handle_editor_set_property_quaternion_delegates_to_validator(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("QuaternionInputValidator.Validate", body)

    def test_handle_editor_set_property_quaternion_unit_norm_code(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        # Non-unit norm rejection must surface the relocated dedicated code.
        self.assertIn("QuaternionInputValidator.NotNormalizedCode", body)

    def test_handle_editor_set_property_quaternion_tolerance_constant(self) -> None:
        # Constant-value pin: the norm tolerance literal (1e-4f) now lives
        # on QuaternionInputValidator; a regression must keep that value.
        source = INPUT_VALIDATORS.read_text(encoding="utf-8")
        self.assertIn("internal const float NormTolerance = 1e-4f;", source)


class TestHandleCaptureConsoleLogsContract(unittest.TestCase):
    """Issue #113 — capture handler accepts ordering + opaque cursor.

    Post H-track migration the ordering/cursor/max-entries validation was
    extracted into the Unity-free ``ConsoleCaptureRequestValidator``; that
    behavioral coverage now lives in ``tests/csharp/ConsoleCaptureTests.cs``.
    This source-text test retains the Tier 3 delegation invariant (the
    handler routes through ``ConsoleCaptureRequestValidator.Validate``)
    plus the request-DTO field-surface pins (the DTO now lives in
    ``PrefabSentinel.Dispatch.EditorControlRequest.cs``).
    """

    def test_request_struct_carries_order_field(self) -> None:
        body = _extract_editor_control_request_body()
        # Request DTO holds the ordering keyword forwarded by the wrapper.
        self.assertIn("public string order", body)

    def test_request_struct_carries_cursor_field(self) -> None:
        body = _extract_editor_control_request_body()
        # Request DTO holds the opaque continuation token.
        self.assertIn("public string cursor", body)

    def test_response_data_carries_next_cursor_field(self) -> None:
        source = _read(BRIDGE)
        # Response payload field for the opaque continuation token.
        self.assertIn("public string next_cursor", source)

    def test_console_log_entry_carries_sequence_id(self) -> None:
        source = _read(BRIDGE)
        # Per-entry monotonic sequence identifier so the cursor token can
        # name an ingestion position unambiguously.
        self.assertRegex(source, r"public\s+long\s+sequence_id")

    def test_handler_delegates_request_validation(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleCaptureConsoleLogs")
        # Ordering / malformed-cursor / max-entries validation is owned by
        # the Unity-free validator; the handler must route through it.
        self.assertIn("ConsoleCaptureRequestValidator.Validate", body)


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
    """Issue #108 (brushed-up under #222 Phase 1/2): the per-frame
    ``RunScriptPollFrame`` observes the documented completion
    conditions (``EditorApplication.isCompiling``, assembly mtime
    advance, deadline) and locates the freshly compiled type, returning
    to wait for the next frame whenever the conditions have not yet
    settled. The compile-pending response surfaced when the deadline
    elapses still hints at the persistent helper alternative.

    Brush-up: paired source-text assertions on the same body are
    collapsed into tuple value-pins so a mutation that drops either
    fact surfaces both expected tokens in the same failure message.
    """

    def test_compile_state_poll_references_isCompiling_and_deadline(self) -> None:  # noqa: N802
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        # Tuple value-pin: a mutation that drops either token names
        # both expected anchors in the failure message.
        self.assertEqual(
            (True, True),
            (
                "EditorApplication.isCompiling" in body,
                "deadlineUnixMs" in body,
            ),
            msg=(
                "RunScriptPollFrame must reference both the compile-state "
                "poll (EditorApplication.isCompiling) and the per-request "
                "deadline (deadlineUnixMs); body lacks one of them."
            ),
        )

    def test_entry_type_retry_loop_calls_find_and_returns_on_null(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        # Tuple value-pin: the call site and the null-return branch are
        # logically inseparable; one without the other breaks the
        # per-frame retry contract.
        self.assertEqual(
            (True, True),
            (
                "FindTempScriptType()" in body,
                bool(re.search(r"scriptType\s*==\s*null\s*\)\s*return", body)),
            ),
            msg=(
                "RunScriptPollFrame must call FindTempScriptType() and "
                "return to the editor when the result is null so the "
                "next frame retries."
            ),
        )

    def test_compile_pending_message_hints_persistent_helper(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        # Specific failure message: name the anchor the caller-facing
        # hint must reference so a regression surfaces concretely.
        self.assertIn(
            "editor_execute_menu_item", body,
            msg=(
                "Compile-pending response must hint at the persistent "
                "helper alternative (editor_execute_menu_item)."
            ),
        )


class TestSetPropertyGameObject(unittest.TestCase):
    """Task 9: GameObject-level property writes with allowlist."""

    def test_handles_gameobject_target_special_case(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        # The branch must construct a SerializedObject directly from the
        # GameObject when the caller addresses the GameObject itself.
        self.assertIn("new SerializedObject(go)", body)

    def test_allowlist_delegates_to_allowlist_class(self) -> None:
        # Post H-track migration the GameObject property allowlist (the
        # inline ``gameObjectAllowedProperties`` array) was extracted into
        # the Unity-free ``GameObjectPropertyAllowlist``; its membership
        # coverage now lives in ``tests/csharp/PropertiesPureLogicTests.cs``.
        # The handler must route through the relocated allowlist.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("GameObjectPropertyAllowlist.IsAllowed", body)

    def test_allowlist_names_pinned_on_allowlist_class(self) -> None:
        # Constant-value pin on the relocated allowlist membership.
        source = INPUT_VALIDATORS.read_text(encoding="utf-8")
        for name in ("m_IsActive", "m_Layer", "m_Name", "m_TagString"):
            self.assertIn(f'"{name}"', source, f"missing allowlist name: {name}")

    def test_out_of_allowlist_returns_dedicated_code(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("EDITOR_CTRL_SET_PROP_GAMEOBJECT_PROP_NOT_ALLOWED", body)


class TestSetPropertySuggestions(unittest.TestCase):
    """Task 10: Property-name suggestions on EDITOR_CTRL_SET_PROP_FIELD_NOT_FOUND.

    Post H-track migration the similarity ranking (Levenshtein distance
    + the 0.4 distance-ratio threshold) was extracted into the Unity-free
    ``SuggestionRanker``; that behavioral coverage now lives in
    ``tests/csharp/PropertiesPureLogicTests.cs``. This source-text test
    retains the Tier 3 delegation invariant (the handler routes the
    not-found branch through ``SuggestionRanker.SuggestSimilar``).
    """

    def test_not_found_branch_delegates_to_suggestion_ranker(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("SuggestionRanker.SuggestSimilar", body)


def _extract_editor_control_request_body() -> str:
    """Return the text between the opening and closing braces of
    ``public sealed class EditorControlRequest``.

    Post H-track migration the DTO was relocated verbatim out of the
    bridge core into ``PrefabSentinel.Dispatch.EditorControlRequest.cs``;
    the relocated file is read directly so request-schema invariants stay
    pinned to the new home.
    """
    source = EDITOR_CONTROL_REQUEST.read_text(encoding="utf-8")
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


def _action_registry_hashset(field: str) -> str:
    """Return the HashSet initialiser literal for ``ActionRegistry.<field>``.

    Post H-track migration the bridge action sets (``SupportedActions`` /
    ``AsyncActions``) are aliases of the canonical ``ActionRegistry.Supported``
    / ``ActionRegistry.Async`` HashSet literals declared in
    ``PrefabSentinel.Dispatch.ActionRegistry.cs``; the action-string set is
    pinned there now, so the dispatch/wiring source-text tests read it from
    the registry file.
    """
    source = ACTION_REGISTRY.read_text(encoding="utf-8")
    match = re.search(
        rf"\b{re.escape(field)}\s*=\s*new\s+HashSet<string>\s*\{{",
        source,
    )
    if match is None:
        raise AssertionError(f"ActionRegistry.{field} HashSet initialiser not found")
    return _extract_braced_block(
        source, match.end(), f"ActionRegistry.{field} HashSet initialiser"
    )


class TestForceReimportSupport(unittest.TestCase):
    """Task 11: HandleRecompileScripts honors a force_reimport request flag."""

    def test_request_carries_force_reimport_field(self) -> None:
        body = _extract_editor_control_request_body()
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
    budget consumed by HandleRunScript's bounded compile poll.

    Post H-track migration the budget-vs-default resolution + deadline
    arithmetic was extracted into the Unity-free ``RunScriptDeadline``;
    that behavioral coverage now lives in
    ``tests/csharp/RunScriptCompileValidatorTests.cs``. This source-text
    test retains the request-DTO field pin (the DTO lives in
    ``PrefabSentinel.Dispatch.EditorControlRequest.cs``) and the Tier 3
    delegation invariant (the handler routes through
    ``RunScriptDeadline.Resolve`` with ``request.compile_timeout``).
    """

    def test_request_carries_compile_timeout_field(self) -> None:
        body = _extract_editor_control_request_body()
        self.assertIn("public int compile_timeout", body)

    def test_run_script_delegates_deadline_resolution(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRunScript")
        # The handler must forward the request's compile_timeout field to
        # the Unity-free deadline resolver.
        self.assertIn("request.compile_timeout", body)
        self.assertIn("RunScriptDeadline.Resolve", body)


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
    outside the inclusive ``[1, ConsoleLogBuffer.DefaultCapacity]`` range.

    Post H-track migration the range check itself was extracted into the
    Unity-free ``ConsoleCaptureRequestValidator``; that behavioral
    coverage now lives in ``tests/csharp/ConsoleCaptureTests.cs``. This
    source-text test retains the Tier 3 invariants: the handler still
    feeds ``ConsoleLogBuffer.DefaultCapacity`` into the validator, and
    the out-of-range error code is constant-pinned on the validator.
    """

    def test_handler_feeds_published_capacity_to_validator(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleCaptureConsoleLogs")
        self.assertIn("ConsoleLogBuffer.DefaultCapacity", body)
        self.assertIn("ConsoleCaptureRequestValidator.Validate", body)

    def test_out_of_range_code_pinned_on_validator(self) -> None:
        source = CONSOLE_REQUEST_VALIDATOR.read_text(encoding="utf-8")
        self.assertIn(
            'MaxEntriesOutOfRangeCode =', source
        )
        self.assertIn('"EDITOR_CTRL_MAX_ENTRIES_OUT_OF_RANGE"', source)


class TestRecompileAndWaitDispatch(unittest.TestCase):
    """Issue #118: the synchronous recompile-and-wait action is wired
    into both the supported-action set and the asynchronous-action set.
    The handler must reference the documented completion signals (the
    compiled-assembly file and the post-reload signal) and the timeout
    error code.
    """

    def test_supported_action_lists_recompile_and_wait(self) -> None:
        # The action-string set is the ActionRegistry.Supported literal.
        literal = _action_registry_hashset("Supported")
        self.assertIn('"editor_recompile_and_wait"', literal)

    def test_async_action_lists_recompile_and_wait(self) -> None:
        literal = _action_registry_hashset("Async")
        self.assertIn('"editor_recompile_and_wait"', literal)

    def test_async_action_lists_run_script(self) -> None:
        """Issue #108: the script-runner action completes asynchronously
        through the run-script registry; the registry must reflect that."""
        literal = _action_registry_hashset("Async")
        self.assertIn('"run_script"', literal)

    def test_recompile_and_wait_handler_subscribes_to_pipeline_events(self) -> None:
        # Issue #203 / #213: the event-driven handler subscribes to the
        # per-assembly finished event (records compile errors and
        # ``compiledAny``) and the pipeline-level finished event (the
        # always-fires terminator that synthesises the outcome before
        # Unity enters domain reload). The no-op case is determined
        # passively via ``!compiledAny`` at pipeline-finished time, so no
        # subscription to ``assemblyCompilationNotRequired`` is needed.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("CompilationPipeline.assemblyCompilationFinished", body)
        self.assertIn("CompilationPipeline.compilationFinished", body)
        self.assertNotIn("CompilationPipeline.assemblyCompilationNotRequired", body)

    def test_recompile_and_wait_handler_emits_three_outcome_codes(self) -> None:
        # Issue #203: on the pipeline-level finished event the handler
        # synthesises one of three outcomes — no-op / OK / FAILED.
        # Post H-track migration the no-op/failed/continue classification
        # was extracted into the Unity-free ``RecompileOutcomeClassifier``
        # (behavioral coverage in
        # ``tests/csharp/RunScriptCompileResolutionTests.cs``); the handler
        # routes through it (no-op and failed codes are the relocated
        # ``RecompileOutcomeClassifier`` consts) and delegates the OK
        # envelope to ``BuildRecompileReloadWaitPoll``.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("RecompileOutcomeClassifier.Classify", body)
        self.assertIn("RecompileOutcomeClassifier.NoopCode", body)
        self.assertIn("RecompileOutcomeClassifier.FailedCode", body)
        self.assertIn("BuildRecompileReloadWaitPoll", body)
        self.assertIn("EDITOR_CTRL_RECOMPILE_AND_WAIT_OK", source)

    def test_recompile_and_wait_handler_does_not_poll_assembly_mtime(self) -> None:
        # Issue #203 root cause: ``Library/ScriptAssemblies/Assembly-CSharp.dll``
        # mtime does not advance when Unity reports the assembly as not
        # requiring compilation. The new event-driven handler must not
        # reference the modification-time read helper, otherwise the
        # original timeout bug recurs.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertNotIn("ReadAssemblyMtimeUnixMs", body)

    def test_recompile_and_wait_handler_references_timeout_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", body)


class TestRecompileAndWaitTimeoutBoundCheck(unittest.TestCase):
    """Issue #134 — the bridge handler rejects non-default out-of-range
    ``timeout_sec`` values before scheduling compilation.

    Post H-track migration the range check (negative rejection, the zero
    "use-the-default" sentinel, the 1800s upper bound) was extracted into
    the Unity-free ``RecompileTimeoutValidator``; that behavioral coverage
    now lives in ``tests/csharp/RunScriptCompileValidatorTests.cs``. This
    source-text test retains the Tier 3 delegation invariant (the handler
    routes ``request.timeout_sec`` through ``RecompileTimeoutValidator``)
    plus constant-value pins on the relocated bound and error code.
    """

    def test_handler_delegates_to_timeout_validator(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("request.timeout_sec", body)
        self.assertIn("RecompileTimeoutValidator.Validate", body)

    def test_upper_bound_constant_value(self) -> None:
        # The upper-bound literal must equal the Python mirror value
        # (1800 seconds); drift between the two would let an oversized
        # budget slip past one side and trip the other.
        source = RUN_SCRIPT_COMPILE_VALIDATORS.read_text(encoding="utf-8")
        self.assertRegex(source, r"MaxTimeoutSec\s*=\s*1800f")

    def test_out_of_range_code_pinned_on_validator(self) -> None:
        source = RUN_SCRIPT_COMPILE_VALIDATORS.read_text(encoding="utf-8")
        self.assertIn(
            'OutOfRangeCode = "EDITOR_CTRL_COMPILE_TIMEOUT_OUT_OF_RANGE"', source
        )


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
    """Issue #118 / #203: after a domain reload triggered by the
    "compiledAny=true" path, the in-flight ``editor_recompile_and_wait``
    request is resumed by ``ResumePendingAsyncRunners`` so completion
    drainage continues from the new AppDomain. The post-reload poll
    observes only the reload counter and the deadline (issue #203
    redesign) — the mtime / pipeline-event observation is owned by the
    pre-reload phase inside ``HandleRecompileAndWait``.
    """

    def test_resume_wires_recompile_and_wait_action(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
        # Resume branch must dispatch on the persisted action string.
        self.assertIn('"editor_recompile_and_wait"', body)

    def test_resume_uses_reload_only_poll_builder(self) -> None:
        # Issue #203: the post-reload phase has its own dedicated poll
        # builder (``BuildRecompileReloadWaitPoll``) which observes only
        # the reload counter and the deadline. The resumer must invoke
        # this builder, not any mtime-based variant.
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
        self.assertIn("BuildRecompileReloadWaitPoll", body)
        # The rehydrated entry must be reattached to the in-flight
        # registry so ``Complete`` can later drain it.
        self.assertIn("RehydrateEntry", body)

    def test_reload_only_poll_observes_only_reload_counter(self) -> None:
        # Issue #203: the post-reload poll body must reference the
        # reload counter, the OK code, and the timeout code, and must
        # NOT reference the mtime helper. Cross-call interference from
        # any mtime-based check would re-introduce the no-op timeout
        # regression.
        source = _read(BRIDGE)
        body = _extract_method(source, "BuildRecompileReloadWaitPoll")
        self.assertIn("AssemblyReloadCount", body)
        self.assertIn("EDITOR_CTRL_RECOMPILE_AND_WAIT_OK", body)
        self.assertIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", body)
        self.assertNotIn("ReadAssemblyMtimeUnixMs", body)

    def test_resumer_uses_minus_one_reload_count_threshold(self) -> None:
        # Issue #191 / #203 race-condition pin: the resumer runs on the
        # post-reload AppDomain whose ``AssemblyReloadCount`` starts at
        # ``0``. The post-reload poll completes the request the first
        # time ``AssemblyReloadCount > threshold`` evaluates true. With
        # ``threshold = 0`` (mutation candidate) ``0 > 0`` is false on
        # the first tick and the request stalls until the next reload —
        # the exact regression issue #191 fixed. Pin the literal so the
        # mutation kills the test.
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
        # Restrict to the recompile-and-wait branch so a future addition
        # of another action's resumer cannot accidentally satisfy the
        # match.
        branch_match = re.search(
            r'else if \(entry\.action == "editor_recompile_and_wait"\)\s*\{(.*?)^\s{16}\}',
            body,
            flags=re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(
            branch_match,
            "ResumePendingAsyncRunners must contain the "
            "editor_recompile_and_wait branch",
        )
        branch_body = branch_match.group(1)
        call_match = re.search(
            r"BuildRecompileReloadWaitPoll\s*\(([^;]*)\)\s*;",
            branch_body,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(
            call_match,
            "Resumer branch must call BuildRecompileReloadWaitPoll",
        )
        # The poll builder signature is
        # ``(responsePath, deadlineMs, reloadCountThreshold, timeoutDetail)``;
        # the third positional argument is the threshold literal.
        args = [a.strip() for a in call_match.group(1).split(",")]
        self.assertGreaterEqual(
            len(args), 4,
            f"BuildRecompileReloadWaitPoll call must have 4 args, got {args}",
        )
        self.assertEqual(
            "-1", args[2],
            "reloadCountThreshold must be -1 to satisfy the first-tick "
            "post-reload check (issue #191).",
        )


class TestCreateUiElementSource(unittest.TestCase):
    """Issue #195 — source-text invariants for ``HandleEditorCreateUiElement``.

    The uGUI element creation handler must:

    * Live behind the dedicated ``editor_create_ui_element`` action so the
      surface name doesn't lie about its scope.
    * Pin the canonical allowed type set as exactly the five tokens
      ``Image``, ``TextMeshProUGUI``, ``Button``, ``Slider``, ``Toggle``.
    * Emit the documented typed envelopes (no-name, bad-type,
      parent-not-found, TMP-font-missing) and the OK envelope.
    * Reference the canonical default font asset path used when the
      caller omits ``font`` for TextMeshPro elements.
    """

    def test_supported_action_lists_create_ui_element(self) -> None:
        # The action-string set is the ActionRegistry.Supported literal.
        literal = _action_registry_hashset("Supported")
        self.assertIn('"editor_create_ui_element"', literal)

    def test_dispatcher_routes_create_ui_element(self) -> None:
        source = _read(BRIDGE)
        self.assertRegex(
            source,
            r'case\s+"editor_create_ui_element"\s*:\s*\n\s*response\s*=\s*HandleEditorCreateUiElement',
        )

    def test_handler_delegates_to_type_allowlist(self) -> None:
        # Post H-track migration the inline ``UiElementAllowedTypes`` array
        # was extracted into the Unity-free ``UiElementTypeAllowlist``;
        # its membership coverage now lives in
        # ``tests/csharp/UiElementTests.cs``. The handler must route
        # through the relocated allowlist.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorCreateUiElement")
        self.assertIn("UiElementTypeAllowlist.IsAllowed", body)

    def test_handler_pins_canonical_allowed_type_set(self) -> None:
        # Constant-value pin on the relocated allowlist membership.
        source = UI_ELEMENT_ALLOWLIST.read_text(encoding="utf-8")
        for token in (
            '"Image"', '"TextMeshProUGUI"', '"Button"', '"Slider"', '"Toggle"',
        ):
            self.assertIn(
                token, source,
                f"canonical allowed type set must include {token}",
            )

    def test_handler_emits_no_name_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorCreateUiElement")
        self.assertIn("EDITOR_CTRL_CREATE_UI_NO_NAME", body)

    def test_handler_emits_bad_type_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorCreateUiElement")
        self.assertIn("EDITOR_CTRL_CREATE_UI_BAD_TYPE", body)

    def test_handler_emits_parent_not_found_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorCreateUiElement")
        self.assertIn("EDITOR_CTRL_CREATE_UI_PARENT_NOT_FOUND", body)

    def test_handler_emits_tmp_font_missing_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorCreateUiElement")
        self.assertIn("EDITOR_CTRL_CREATE_UI_TMP_FONT_MISSING", body)

    def test_handler_emits_ok_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorCreateUiElement")
        self.assertIn("EDITOR_CTRL_CREATE_UI_OK", body)

    def test_handler_references_canonical_default_font_asset(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorCreateUiElement")
        # Per ``knowledge/prefab-sentinel-saveasprefabasset-pitfalls.md``
        # §3 the canonical default font asset path is LiberationSans SDF
        # under the TMP Resources tree. The handler assigns this via the
        # named constant; the source file holds the literal path so a
        # mutation of either site fires the assertion.
        self.assertIn("UiElementDefaultTmpFontAssetPath", body)
        self.assertIn(
            'UiElementDefaultTmpFontAssetPath =\n'
            '            "Assets/TextMesh Pro/Resources/Fonts & Materials/'
            'LiberationSans SDF.asset"',
            source,
        )


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

    def test_handler_body_does_not_reject_empty_parsed_list(self) -> None:
        """Issue #228 — the empty-list raw-save mode requires the handler
        body NOT to contain the empty-list rejection message string. A
        bifurcated guard pattern (absent-field branch raises
        PROTECT_REQUIRED, empty parsed list flows through) is the only
        shape that admits raw-save mode while keeping the absent-field
        contract intact.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSafeSaveAsPrefab")
        # The pre-#228 implementation rejected the parsed-empty case with
        # this exact message string. The presence of any substring of it
        # is a regression that re-broadens the rejection trigger back to
        # ``protectTypes.Length == 0``.
        self.assertNotIn("at least one component type name", body)

    def test_protect_required_only_in_absent_field_branch(self) -> None:
        """Issue #228 — the ``EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED``
        identifier must appear in the absent-field branch (the
        ``string.IsNullOrEmpty(request.protect_components_json)`` guard)
        but must NOT appear inside the parsed-empty branch. Locating the
        identifier within the absent-field guard scope and counting
        occurrences pins the bifurcated trigger.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSafeSaveAsPrefab")
        # Exactly one occurrence of the error code identifier — the
        # absent-field branch. A second occurrence would reintroduce the
        # parsed-empty rejection.
        self.assertEqual(
            1,
            body.count("EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED"),
            "PROTECT_REQUIRED must fire only on the absent-field branch.",
        )
        # The identifier must be co-located with the absent-field guard.
        # If we partition the body at the ``IsNullOrEmpty(...protect_components_json)``
        # check, the error code must live in the prefix.
        marker = "string.IsNullOrEmpty(request.protect_components_json)"
        self.assertIn(marker, body)
        guard_index = body.index(marker)
        code_index = body.index("EDITOR_CTRL_SAFE_SAVE_PREFAB_PROTECT_REQUIRED")
        # The code's literal must follow the guard (the BuildError call
        # comes immediately after the if-guard) and must precede any
        # parsed-list logic. ``protectTypes`` is the first parsed-list
        # symbol introduced after JSON parsing.
        self.assertGreater(code_index, guard_index)
        protect_types_index = body.index("protectTypes")
        self.assertLess(code_index, protect_types_index)

    def test_raw_save_path_inherits_console_snapshot_population(self) -> None:
        """Issue #228 — the raw-save (empty parsed list) code path must
        produce the same console-classification snapshot as the
        non-empty path. Both paths route through ``SaveAsPrefabCore``,
        which populates ``udonsharp_obs_nre_count`` / ``nonfatal_patterns``
        on the ``warnings`` payload via ``ConsoleLogBuffer.CollectNonFatalCountsSince``.
        Pinning all three identifiers in the source guarantees the
        empty-list path inherits noise-diagnostic aggregation (notably
        the U# OnBeforeSerialize NRE family seen in raw save).
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSafeSaveAsPrefab")
        # The handler body delegates to SaveAsPrefabCore on every path
        # (no early-return between the JSON parse and the core call).
        self.assertIn("SaveAsPrefabCore", body)
        # SaveAsPrefabCore is the single source of console-snapshot data.
        core_body = _extract_method(source, "SaveAsPrefabCore")
        self.assertIn("ConsoleLogBuffer", core_body)
        self.assertIn("CollectNonFatalCountsSince", core_body)
        self.assertIn("udonsharp_obs_nre_count", core_body)
        self.assertIn("nonfatal_patterns", core_body)


class TestMenuExecuteBarrierSource(unittest.TestCase):
    """Issue #225 — source-text invariants for the menu-execute
    implicit recompile barrier. Pins identifiers in the bridge source
    so refactors that strip the barrier logic, the script-mtime branch,
    or the post-reload resumer entry fail visibly here. T1/T2 coverage
    is infeasible without a live Unity Editor harness (issue #222
    Phase 3) — see the Tier 3 Justification in the spec.
    """

    def test_handler_consults_compile_state_and_opt_out_flag(self) -> None:
        """The menu-execute handler body must reference both the
        compile-state symbol (``EditorApplication.isCompiling``) and
        the opt-out payload field (``assume_compiled``) so the barrier
        predicate observes both inputs.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleExecuteMenuItem")
        self.assertIn("EditorApplication.isCompiling", body)
        self.assertIn("assume_compiled", body)

    def test_handler_consults_editor_script_change_detector(self) -> None:
        """The menu-execute handler body must reference the
        ``HasEditorScriptChangedSince`` predicate so the script-mtime
        branch of the barrier participates in the decision.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleExecuteMenuItem")
        self.assertIn("HasEditorScriptChangedSince", body)

    def test_change_detector_delegates_to_path_classifier(self) -> None:
        """The change detector must route per-path Editor/temp-area
        classification through the Unity-free ``EditorScriptPathClassifier``.

        Post H-track migration the temp-exclusion / Editor-segment
        classification (and its ``_PrefabSentinelTemp`` constant) moved
        out of ``HasEditorScriptChangedSince`` into the classifier; that
        behavioral coverage now lives in
        ``tests/csharp/EditorScriptPathClassifierTests.cs``. This test
        retains the Tier 3 delegation invariant for the change detector.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HasEditorScriptChangedSince")
        self.assertIn("EditorScriptPathClassifier.IsEditorSourcePath", body)

    def test_post_reload_resumer_covers_execute_menu_item(self) -> None:
        """The post-reload resumer
        (``ResumePendingAsyncRunners``) must reference the
        ``execute_menu_item`` action label so a menu-execute that was
        scheduled before a domain reload still completes after the
        new AppDomain comes up.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
        self.assertIn("execute_menu_item", body)

    def test_async_action_lists_execute_menu_item(self) -> None:
        """Issue #225: when the implicit recompile barrier fires the
        menu-execute handler returns ``null`` and the response file is
        written asynchronously by the post-pipeline callback. The
        dispatcher's "no response written" guard short-circuits to
        ``EDITOR_BRIDGE_NO_RESPONSE`` for any action absent from
        ``AsyncActions``; without this membership the slow path
        produces the synchronous error envelope before the async
        callback can write the real response, making the barrier
        feature non-functional.

        Post H-track migration the action-string set is the canonical
        ``ActionRegistry.Async`` literal; this test reads it there.
        """
        literal = _action_registry_hashset("Async")
        self.assertIn('"execute_menu_item"', literal)


_BRIDGE_PARTIAL_GLOB = "PrefabSentinel.UnityEditorControlBridge*.cs"


def _bridge_partial_filenames() -> list[str]:
    """Return the on-disk bridge partial filenames sorted by name.

    Issue #266: the layout and sizing tests previously listed every
    expected filename as a literal tuple.  Five partials introduced by
    later issues drifted out of the manual list and dropped out of the
    layout / sizing invariants.  Disk derivation under the documented
    glob is the source of truth — adding a new partial requires nothing
    more than dropping the file in place.
    """
    return sorted(p.name for p in TOOLS_DIR.glob(_BRIDGE_PARTIAL_GLOB))


class TestBridgePartialLayout(unittest.TestCase):
    """Issue #123 / #266 — every bridge partial source on disk must
    declare the same partial class.  The contract set is derived from
    disk via the documented filename glob so new partials are picked up
    automatically; the deleted-partials negative-set is retained as a
    literal because absence-from-disk cannot otherwise be observed.
    The canonical core source name is fixed because both the drift
    checker and the bump-my-version anchor rglob it.
    """

    # Names of partials that earlier splits removed.  These must be
    # absent from disk so the CLAUDE.md inventory and the actual file
    # set agree.
    _DELETED_PARTIAL_NAMES = (
        "PrefabSentinel.UnityEditorControlBridge.HierarchyComponents.cs",
        "PrefabSentinel.UnityEditorControlBridge.UdonSharp.cs",
    )

    def _on_disk(self) -> list[str]:
        return _bridge_partial_filenames()

    def test_canonical_core_partial_exists(self) -> None:
        # The canonical core source name is load-bearing: the drift
        # checker (``scripts/check_bridge_constants.py``) and the
        # bump-my-version search/replace anchor both rglob it.
        self.assertIn(
            "PrefabSentinel.UnityEditorControlBridge.cs",
            self._on_disk(),
            msg=(
                "Canonical core partial PrefabSentinel.UnityEditorControlBridge.cs "
                "missing from disk; the version-detection rglob and the "
                "bumpversion anchor both depend on this filename."
            ),
        )

    def test_every_partial_on_disk_declares_same_partial_class(self) -> None:
        # Every partial source must declare exactly one
        # ``public static partial class UnityEditorControlBridge`` so the
        # CLR sees the bridge as a single class spread across files.
        for name in self._on_disk():
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

    def test_no_partial_on_disk_uses_non_partial_class(self) -> None:
        # If any source declares the class without ``partial``, the C#
        # compiler reports a duplicate-class error; this test catches
        # that drift before the editor recompile does.
        for name in self._on_disk():
            with self.subTest(name=name):
                text = (TOOLS_DIR / name).read_text(encoding="utf-8")
                self.assertNotRegex(
                    text,
                    r"public\s+static\s+class\s+UnityEditorControlBridge\b",
                    f"{name}: must use partial class, not plain class",
                )

    def test_deleted_partials_are_absent(self) -> None:
        """The legacy oversized partials must be gone from disk so the
        CLAUDE.md inventory and the live file set match.
        """
        for name in self._DELETED_PARTIAL_NAMES:
            with self.subTest(name=name):
                self.assertFalse(
                    (TOOLS_DIR / name).exists(),
                    f"unexpected leftover partial: {name}",
                )


class TestBridgePartialSizing(unittest.TestCase):
    """Issue #138 / #266 — every per-concern bridge partial on disk fits
    inside the project's per-partial size guideline (≤400 lines absolute),
    except a small, explicitly-listed legacy-oversized allow-list whose
    entries each carry a one-line rationale below. Any partial below
    200 lines carries a leading single-line comment that names the
    cohesive concern justifying the smaller size.

    The contract set is derived from disk via the documented filename
    glob, with the canonical core file (``...Bridge.cs``) excluded by
    glob shape and the legacy-oversized allow-list excluded by name.

    Legacy-oversized allow-list rationale (per spec):

    - ``CameraView``: pre-existing oversized partial predating issue #138.
    - ``SaveInstantiate``: pre-existing oversized partial predating issue #138.
    - ``RunScriptCompile``: pre-existing oversized partial predating issue #138.
    - ``Screenshot``: calibrated post-#249 exemption (the screenshot
      handler aggregates region resolution, preset mapping, scene-vs-game
      view branching, and pixel-rect cropping into one cohesive concern;
      a uniform-cap migration belongs to a separate split issue).
    """

    _MAX_LINES = 400
    _SMALL_THRESHOLD = 200

    # Canonical core source (``PrefabSentinel.UnityEditorControlBridge.cs``)
    # is excluded by name: it has no per-concern segment and carries the
    # bumpversion-anchored load-bearing constants.
    _CORE_NAME = "PrefabSentinel.UnityEditorControlBridge.cs"

    # Per-spec calibrated legacy-oversized allow-list. Each entry must
    # also be present on disk; the negative-set test below pins that
    # invariant so dead allow-list entries cannot silently mask future
    # drift.
    _LEGACY_OVERSIZED_CONCERNS = (
        "CameraView",
        "SaveInstantiate",
        "RunScriptCompile",
        "Screenshot",
    )

    def _per_concern_partials(self) -> list[str]:
        """Return the per-concern bridge partial filenames currently on
        disk, excluding the canonical core file by glob shape and the
        legacy-oversized allow-list by name.
        """
        head = "PrefabSentinel.UnityEditorControlBridge"
        legacy = {f"{head}.{c}.cs" for c in self._LEGACY_OVERSIZED_CONCERNS}
        out: list[str] = []
        for name in _bridge_partial_filenames():
            if name == self._CORE_NAME:
                continue
            if name in legacy:
                continue
            out.append(name)
        return out

    def test_every_non_legacy_partial_is_within_size_bound(self) -> None:
        for name in self._per_concern_partials():
            with self.subTest(name=name):
                path = TOOLS_DIR / name
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
        for name in self._per_concern_partials():
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

    def test_legacy_oversized_allowlist_contains_no_dead_entry(self) -> None:
        """I-3 — every legacy-oversized allow-list entry has a matching
        on-disk per-concern partial.  A dead allow-list entry surviving
        a partial rename or deletion would silently mask future drift.
        """
        head = "PrefabSentinel.UnityEditorControlBridge"
        on_disk = set(_bridge_partial_filenames())
        for concern in self._LEGACY_OVERSIZED_CONCERNS:
            with self.subTest(concern=concern):
                expected = f"{head}.{concern}.cs"
                self.assertIn(
                    expected,
                    on_disk,
                    msg=(
                        f"Legacy-oversized allow-list lists {concern!r} but "
                        f"{expected} is missing from disk; either remove the "
                        "allow-list entry or restore the partial."
                    ),
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
        # Post H-track migration the action-string set is the canonical
        # ``ActionRegistry.Supported`` HashSet literal; this test reads it
        # from ``PrefabSentinel.Dispatch.ActionRegistry.cs``.
        block = _action_registry_hashset("Supported")
        for action in self._NEW_ACTIONS:
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', block)

    def test_async_actions_unchanged_for_udonsharp(self) -> None:
        # The new authoring handlers complete synchronously; if any of
        # them slip into AsyncActions the dispatcher's "no response
        # written" guard would never fire for them.
        block = _action_registry_hashset("Async")
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

    Post H-track migration the ``EditorControlRequest`` DTO was relocated
    verbatim into ``PrefabSentinel.Dispatch.EditorControlRequest.cs``; its
    construction/field-surface coverage now lives in
    ``tests/csharp/ActionRegistryTests.cs``. These remaining checks are
    constant/field-surface pins read from the relocated DTO file.
    """

    def test_request_carries_field_name_field(self) -> None:
        body = _extract_editor_control_request_body()
        self.assertIn("field_name", body)

    def test_request_carries_wire_listener_fields(self) -> None:
        body = _extract_editor_control_request_body()
        # Source/target identity, method name, and the string argument.
        self.assertIn("event_path", body)
        self.assertIn("target_path", body)
        self.assertIn("method", body)
        self.assertIn("arg", body)

    def test_request_carries_fields_json_for_add_udonsharp(self) -> None:
        body = _extract_editor_control_request_body()
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
        # Issue #129 — the patch bridge is split into per-concern
        # partials; each anchored method moved to the partial that
        # holds its declaration.
        ("PrefabSentinel.UnityPatchBridge.Mutation.cs", "TryIsFixedBufferProperty", 2),
        ("PrefabSentinel.UnityPatchBridge.Mutation.cs", "TryReadGradientValue", 1),
        ("PrefabSentinel.UnityPatchBridge.ManagedReference.cs", "TryReadManagedReferenceTypeHint", 1),
        # ``WriteResponseSafe`` has two typed catches — outer atomic-rename
        # fallback and inner direct-write fallback — both with the warn
        # template; a half-fixed regression reverting either site to an
        # untyped catch therefore fails the audit.
        ("PrefabSentinel.UnityPatchBridge.Diagnostics.cs", "WriteResponseSafe", 2),
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
        nested under the outer block remains inside. Returns the empty
        string when no such outer catch is present (the caller asserts
        the result is truthy).
        """
        match = re.search(r"catch\s*\(\s*Exception\s+ex\s*\)\s*\{", method_body)
        if not match:
            return ""
        return _extract_braced_block(
            method_body, match.end(), "outer catch (Exception ex) block"
        )

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
            # Issue #129 — WriteResponseSafe moved to the patch bridge's
            # Diagnostics partial.
            ("PrefabSentinel.UnityPatchBridge.Diagnostics.cs", "WriteResponseSafe"),
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


class TestEditorAsmdefUiReferences(unittest.TestCase):
    """Issue #213 secondary bug B (CS0246): the editor assembly definition
    must list the package references that ``UiElement.cs`` depends on so
    the deployed bridge compiles in projects that consume it.
    """

    _ASMDEF = TOOLS_DIR / "PrefabSentinel.Editor.asmdef"

    def _references(self) -> list[str]:
        manifest = json.loads(self._ASMDEF.read_text(encoding="utf-8"))
        return manifest["references"]

    def test_textmeshpro_reference_present(self) -> None:
        self.assertIn("Unity.TextMeshPro", self._references())

    def test_ugui_reference_present(self) -> None:
        self.assertIn("UnityEngine.UI", self._references())


class TestResolveComponentTypeDedup(unittest.TestCase):
    """Issue #212 (CS0111): exactly one ``ResolveComponentType`` definition
    must exist across all bridge partials so the deployed bridge compiles.
    """

    def test_single_resolve_component_type_definition(self) -> None:
        source = _read(BRIDGE)
        matches = re.findall(
            r"private\s+static\s+(?:System\.)?Type\s+ResolveComponentType\s*\(\s*string\s+typeName\s*\)",
            source,
        )
        self.assertEqual(
            1,
            len(matches),
            f"Expected exactly 1 ResolveComponentType definition, found {len(matches)}",
        )


class TestRecompileAsmFinishedDelegateType(unittest.TestCase):
    """Issue #213 secondary bug A (CS0426): the per-assembly compile-finished
    subscription uses Unity's publicly documented two-argument delegate
    signature, not a non-existent nested delegate type on
    ``CompilationPipeline``.
    """

    def test_handler_uses_action_string_compilermessage_array(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        self.assertRegex(
            body,
            r"Action<\s*string\s*,\s*CompilerMessage\[\]\s*>",
            "Per-assembly compile-finished subscription must use Action<string, CompilerMessage[]>",
        )

    def test_handler_does_not_reference_nested_delegate(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        self.assertNotIn("CompilationPipeline.AssemblyCompilationFinished", body)


class TestRecompileAndWaitOutcomeSync(unittest.TestCase):
    """Issue #213 root cause: outcome synthesis must run inside the
    pipeline-finished event subscription on the original application
    domain (before Unity's domain reload destroys the callback), and a
    boolean re-entry guard must prevent double-resolution if the deadline
    watchdog and the pipeline-finished signal both observe a terminal
    condition in the same frame.
    """

    @staticmethod
    def _pipeline_finished_subscription_body(handler_body: str) -> str:
        """Return the body of the ``compilationFinished`` lambda assigned
        to ``onPipelineFinished``.
        """
        match = re.search(
            r"onPipelineFinished\s*=\s*[^=]*?=>\s*\{",
            handler_body,
        )
        if match is None:
            raise AssertionError("onPipelineFinished assignment not found")
        return _extract_braced_block(
            handler_body, match.end(), "onPipelineFinished body"
        )

    def test_pipeline_finished_body_synthesises_failure_noop_and_switchover(self) -> None:
        # Post H-track migration the outcome precedence (failed > no-op >
        # continue) was extracted into the Unity-free
        # ``RecompileOutcomeClassifier`` (behavioral coverage in
        # ``tests/csharp/RunScriptCompileResolutionTests.cs``); the
        # subscription routes through it and emits the relocated consts.
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        sub_body = self._pipeline_finished_subscription_body(body)
        self.assertIn("RecompileOutcomeClassifier.Classify", sub_body)
        self.assertIn("RecompileOutcomeClassifier.FailedCode", sub_body)
        self.assertIn("RecompileOutcomeClassifier.NoopCode", sub_body)
        self.assertIn("BuildRecompileReloadWaitPoll", sub_body)
        self.assertIn("WriteResponse(", sub_body)

    def test_pipeline_finished_body_checks_and_sets_reentry_flag(self) -> None:
        # Post H-track migration the boolean re-entry guard was extracted
        # into the Unity-free ``RecompileResolutionGuard`` (behavioral
        # coverage in ``tests/csharp/RunScriptCompileResolutionTests.cs``).
        # The handler still owns the shared guard instance and the
        # subscription claims single-resolution through it.
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        self.assertIn("new RecompileResolutionGuard()", body)
        sub_body = self._pipeline_finished_subscription_body(body)
        self.assertRegex(
            sub_body, r"if\s*\(\s*!\s*resolutionGuard\.TryClaim\(\)\s*\)\s*return\s*;"
        )


class TestRecompileAndWaitDeadlineWatchdog(unittest.TestCase):
    """Issue #213: the per-frame deadline watchdog observes only the
    deadline and the shared re-entry flag. It does not classify outcomes
    or reach into the post-reload path; otherwise the same race that
    motivates this work resurfaces.
    """

    @staticmethod
    def _watchdog_body(handler_body: str) -> str:
        """Return the body of the ``prePoll`` lambda — the per-frame
        deadline watchdog.
        """
        match = re.search(r"prePoll\s*=\s*\(\s*\)\s*=>\s*\{", handler_body)
        if match is None:
            raise AssertionError("prePoll assignment not found")
        return _extract_braced_block(handler_body, match.end(), "prePoll body")

    def test_watchdog_only_emits_timeout_envelope(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        watchdog = self._watchdog_body(body)
        self.assertIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", watchdog)
        self.assertNotIn("EDITOR_CTRL_RECOMPILE_FAILED", watchdog)
        self.assertNotIn("EDITOR_CTRL_RECOMPILE_AND_WAIT_NOOP", watchdog)
        self.assertNotIn("BuildRecompileReloadWaitPoll", watchdog)

    def test_watchdog_consults_shared_reentry_flag(self) -> None:
        # Post H-track migration the single-resolution claim is owned by
        # the Unity-free ``RecompileResolutionGuard``; the watchdog must
        # route through ``resolutionGuard.TryClaim()`` so exactly one
        # envelope is written per request.
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        watchdog = self._watchdog_body(body)
        self.assertRegex(
            watchdog, r"if\s*\(\s*!\s*resolutionGuard\.TryClaim\(\)\s*\)\s*return\s*;"
        )


class TestRecompileScheduleFailedCode(unittest.TestCase):
    """Issue #204: the editor-side rejection of ``RequestScriptCompilation``
    is a schedule-failure, not a deadline-elapsed condition. It must use
    a dedicated ``EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED`` code so callers
    can distinguish "Unity refused to start" from "we waited and got no
    response".
    """

    def test_schedule_failed_code_emitted(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        catch_body = _schedule_catch_body(body)
        self.assertIn("EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED", catch_body)

    def test_schedule_catch_does_not_emit_timeout_code(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        catch_body = _schedule_catch_body(body)
        self.assertNotIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", catch_body)


class RecompileScheduleFailedSanitization(unittest.TestCase):
    """Issue #214: the schedule-failure envelope returned to the MCP client
    must not embed ``ex.Message`` (or any other exception-derived accessor)
    in its top-level ``message`` field. Internal detail flows to the Unity
    console only via ``Debug.LogWarning`` so an operator inspecting the
    local Editor session can still diagnose the rejection.

    The cross-emission of the timeout code is value-pinned by
    ``TestRecompileScheduleFailedCode.test_schedule_catch_does_not_emit_timeout_code``
    (issue #204) so no duplicate row is added here.
    """

    def test_schedule_catch_does_not_leak_exception_message(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        catch_body = _schedule_catch_body(body)
        # The exception-message accessor must not appear inside the
        # schedule-failure catch arm. The redacted top-level envelope is
        # the only string returned to the MCP client; internal detail is
        # restricted to ``Debug.LogWarning``.
        self.assertNotRegex(catch_body, r"\bex\.Message\b")
        self.assertNotRegex(catch_body, r"\.Message\b")

    def test_schedule_catch_emits_console_warning(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        catch_body = _schedule_catch_body(body)
        # The full exception detail must be mirrored to the Unity
        # console so an operator inspecting the local Editor session
        # can still diagnose the rejection.
        self.assertIn("Debug.LogWarning", catch_body)

    def test_schedule_catch_message_is_fixed_redacted_string(self) -> None:
        # Post H-track migration the fixed redacted message string was
        # extracted into the Unity-free ``ScheduleFailureEnvelope``
        # (behavioral coverage in
        # ``tests/csharp/RunScriptCompileResolutionTests.cs``); the catch
        # arm routes through ``ScheduleFailureEnvelope.RedactedMessage()``
        # and the message literal is constant-pinned on that class.
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        catch_body = _schedule_catch_body(body)
        self.assertIn("ScheduleFailureEnvelope.RedactedMessage()", catch_body)
        redaction = RUN_SCRIPT_COMPILE_REDACTION.read_text(encoding="utf-8")
        self.assertIn(
            "editor_recompile_and_wait: failed to schedule compilation.",
            redaction,
        )


class TmpFontMissingMessageBranching(unittest.TestCase):
    """Issue #205: the TextMeshPro font-missing warning emitted by the
    UI element creation handler must differentiate between the case
    where the caller relied on the canonical default path and the case
    where the caller supplied an explicit font path.

    Post H-track migration the empty-vs-explicit arm selection and
    message construction were extracted into the Unity-free
    ``UiFontMissingMessage`` (behavioral coverage in
    ``tests/csharp/UiElementTests.cs``). This source-text test retains
    the Tier 3 delegation invariant (the handler routes through
    ``UiFontMissingMessage.ForMissingFont``) and the envelope-shape pins.
    """

    @staticmethod
    def _tmp_font_missing_branch(handler_body: str) -> str:
        """Return the body of the ``if (tmpFontMissing)`` branch."""
        match = re.search(
            r"if\s*\(\s*tmpFontMissing\s*\)\s*\{",
            handler_body,
        )
        if match is None:
            raise AssertionError(
                "tmpFontMissing branch not found in HandleEditorCreateUiElement"
            )
        return _extract_braced_block(
            handler_body, match.end(), "tmpFontMissing branch"
        )

    def test_branch_delegates_to_font_missing_message(self) -> None:
        """The branch must route arm selection + message construction
        through the Unity-free ``UiFontMissingMessage.ForMissingFont``,
        forwarding the caller-supplied font path and the canonical
        default so the message differs by whether the caller supplied a
        font path."""
        body = _extract_method(_read(BRIDGE), "HandleEditorCreateUiElement")
        branch = self._tmp_font_missing_branch(body)
        self.assertIn("UiFontMissingMessage.ForMissingFont", branch)
        self.assertIn("props.font", branch)

    def test_branch_message_names_canonical_default_only_on_empty_caller_arm(
        self,
    ) -> None:
        """The canonical-default message must remain reachable only on
        the empty-caller arm. The non-empty arm names the caller-supplied
        path verbatim, not the canonical default."""
        body = _extract_method(_read(BRIDGE), "HandleEditorCreateUiElement")
        branch = self._tmp_font_missing_branch(body)
        # Source text retains the canonical default path token (the
        # empty-caller arm names it). The branch must also reference
        # ``UiElementDefaultTmpFontAssetPath`` for the empty-caller case.
        self.assertIn("UiElementDefaultTmpFontAssetPath", branch)

    def test_branch_retains_envelope_code_severity_and_payload_keys(
        self,
    ) -> None:
        """The dedicated TMP-font-missing code, warning severity, and
        the existing structured payload keys (``selected_object``,
        ``output_path``, ``executed``, ``read_only``) must remain
        unchanged across the differentiation."""
        body = _extract_method(_read(BRIDGE), "HandleEditorCreateUiElement")
        branch = self._tmp_font_missing_branch(body)
        self.assertIn("EDITOR_CTRL_CREATE_UI_TMP_FONT_MISSING", branch)
        self.assertIn('severity = "warning"', branch)
        self.assertIn("selected_object", branch)
        self.assertIn("output_path", branch)
        self.assertIn("executed", branch)
        self.assertIn("read_only", branch)


class RecompileForceReimportDiagnosticRedaction(unittest.TestCase):
    """Issue #214: the per-file force-reimport diagnostic appended to the
    ``HandleRecompileScripts`` success envelope must not embed
    ``ex.Message`` on its ``evidence`` field. The exception category
    identifies the failure, full detail flows to the Unity console.
    """

    @staticmethod
    def _force_reimport_catch_body(handler_body: str) -> str:
        """Return the body of the per-file catch surrounding
        ``AssetDatabase.ImportAsset(rel, ...)``.
        """
        match = re.search(
            r"AssetDatabase\.ImportAsset\([^;]*;\s*\}\s*"
            r"catch\s*\(\s*Exception\s+\w+\s*\)\s*\{",
            handler_body,
            flags=re.DOTALL,
        )
        if match is None:
            raise AssertionError(
                "catch block surrounding force-reimport ImportAsset not found"
            )
        return _extract_braced_block(
            handler_body, match.end(), "force-reimport per-file catch"
        )

    def test_force_reimport_diagnostic_evidence_omits_exception_message(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileScripts")
        catch_body = self._force_reimport_catch_body(body)
        # The diagnostic ``evidence`` field must not be populated from
        # ``ex.Message``. The exception category (``ex.GetType().Name``)
        # is the only exception-derived value permitted here.
        self.assertNotRegex(catch_body, r"\bex\.Message\b")
        self.assertNotRegex(
            catch_body, r"evidence\s*=\s*[A-Za-z_]\w*\.Message\b"
        )

    def test_force_reimport_catch_emits_console_warning(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRecompileScripts")
        catch_body = self._force_reimport_catch_body(body)
        # The full exception detail must be mirrored to the Unity console.
        self.assertIn("Debug.LogWarning", catch_body)


# ---------------------------------------------------------------------------
# Issue #239 — per-entry phase tag + retrieval phase filter + editor-state
# snapshot handler.
# ---------------------------------------------------------------------------


def _extract_console_log_entry_body(source: str) -> str:
    """Return the body of ``public sealed class ConsoleLogEntry``."""
    start = source.find("public sealed class ConsoleLogEntry")
    if start == -1:
        raise AssertionError("ConsoleLogEntry class not found")
    return _extract_braced_block(
        source,
        source.find("{", start) + 1,
        "ConsoleLogEntry",
    )


def _extract_editor_control_data_body(source: str) -> str:
    """Return the body of ``public sealed class EditorControlData``."""
    start = source.find("public sealed class EditorControlData")
    if start == -1:
        raise AssertionError("EditorControlData class not found")
    return _extract_braced_block(
        source,
        source.find("{", start) + 1,
        "EditorControlData",
    )


class TestConsoleLogEntryDeclaresPhaseField(unittest.TestCase):
    """Issue #239: ConsoleLogEntry exposes a per-entry phase tag."""

    def test_phase_field_is_public_string(self) -> None:
        body = _extract_console_log_entry_body(_read(BRIDGE))
        self.assertRegex(
            body,
            r"public\s+string\s+phase\b",
            msg=(
                "ConsoleLogEntry must declare a public string ``phase`` "
                "field so each captured entry carries its editor-phase "
                "tag in the serialized response."
            ),
        )


class TestOnLogMessagePhasePriority(unittest.TestCase):
    """Issue #239: OnLogMessage snapshots phase with ``build > play > edit``.

    Post H-track migration the build > play > edit precedence was
    extracted into the Unity-free ``ConsoleLogPhaseClassifier.Classify``
    (behavioral coverage in ``tests/csharp/ConsoleCaptureTests.cs``).
    This source-text test retains the Tier 3 delegation invariant: the
    handler reads both canonical editor-API flags and feeds them into
    the classifier.
    """

    def test_delegates_phase_classification(self) -> None:
        body = _extract_method(_read(BRIDGE), "OnLogMessage")
        self.assertIn("ConsoleLogPhaseClassifier.Classify", body)
        self.assertIn("BuildPipeline.isBuildingPlayer", body)
        self.assertIn("EditorApplication.isPlayingOrWillChangePlaymode", body)


def _extract_get_entries_body(source: str) -> str:
    """Custom extractor for ``GetEntries`` because the method's return
    type is a value tuple ``(List<ConsoleLogEntry>, bool)`` that the
    generic ``_extract_method`` regex's ``\\S+`` return-type group does
    not match.
    """
    start = source.find("public static (List<ConsoleLogEntry> entries, bool hasMore) GetEntries(")
    if start == -1:
        raise AssertionError("GetEntries method declaration not found")
    open_brace = source.find("{", start)
    return _extract_braced_block(source, open_brace + 1, "GetEntries")


class TestConsoleLogBufferRetrievalAppliesPhaseFilter(unittest.TestCase):
    """Issue #239: ``GetEntries`` honors the phase-filter argument.

    Post H-track migration the catch-all / strict phase-match predicate
    was extracted into the Unity-free
    ``ConsoleLogEntryPredicate.MatchesPhaseFilter`` (behavioral coverage
    in ``tests/csharp/ConsoleCaptureTests.cs``). This source-text test
    retains the Tier 3 delegation invariant: ``GetEntries`` must route
    its phase filtering through the relocated predicate.
    """

    def test_get_entries_delegates_to_phase_predicate(self) -> None:
        body = _extract_get_entries_body(_read(BRIDGE))
        self.assertIn("phaseFilter", body)
        self.assertIn(
            "ConsoleLogEntryPredicate.MatchesPhaseFilter(entry.phase, phaseFilter)",
            body,
        )


class TestHandleCaptureConsoleLogsValidatesPhaseFilter(unittest.TestCase):
    """Issue #239: the capture handler rejects unsupported phase selectors.

    Post H-track migration the supported-set membership check (and the
    ``SupportedPhaseFilters`` array) was extracted into the Unity-free
    ``ConsoleLogEntryPredicate`` (behavioral coverage in
    ``tests/csharp/ConsoleCaptureTests.cs``). This source-text test
    retains the Tier 3 delegation invariant: the handler gates the
    phase selector through ``ConsoleLogEntryPredicate.IsSupportedPhaseFilter``
    and surfaces the supported set in the rejection message.
    """

    def test_handler_delegates_phase_filter_validation(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCaptureConsoleLogs")
        self.assertIn("ConsoleLogEntryPredicate.IsSupportedPhaseFilter", body)
        self.assertIn("ConsoleLogEntryPredicate.SupportedPhaseFilters", body)
        self.assertIn("EDITOR_CTRL_INVALID_PHASE_FILTER", body)


class TestHandleGetEditorStateReadsFourFlags(unittest.TestCase):
    """Issue #239: the editor-state handler reads exactly four flags."""

    def test_handler_assigns_four_named_flags(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleGetEditorState")
        # Each of the four documented editor-API symbols must be present
        # alongside the matching snapshot field name; a missing flag
        # surfaces as a False in this tuple and names the gap.
        checks = (
            ("is_playing = EditorApplication.isPlaying" in body),
            (
                "is_will_change_playmode = "
                "EditorApplication.isPlayingOrWillChangePlaymode"
            ) in body,
            ("is_compiling = EditorApplication.isCompiling" in body),
            ("is_building_player = BuildPipeline.isBuildingPlayer" in body),
        )
        self.assertEqual(
            (True, True, True, True),
            checks,
            msg=(
                "HandleGetEditorState must read each of the four "
                "documented editor-API symbols into the matching "
                "EditorStateSnapshot field — checks="
                f"{checks}"
            ),
        )


# ---------------------------------------------------------------------------
# Issue #216 — script-runner leak-safe envelope at four catch sites + shared
# payload omits exception-text field.
# ---------------------------------------------------------------------------


# Forbidden tokens that would re-introduce the leak.  Each script-runner
# catch site must contain none of these inside the catch block, and the
# shared EditorControlData class body must declare no exception-text
# field.
_LEAK_TOKENS = (
    "ex.Message",
    "ex.ToString()",
    "stagingEx.Message",
    "stagingEx.ToString()",
    "refreshEx.Message",
    "refreshEx.ToString()",
    "inner.Message",
    "inner.ToString()",
    "tie.Message",
    "tie.ToString()",
    "exception =",
)


def _extract_catch_block(method_body: str, exception_pattern: str) -> str:
    """Return the body of a ``catch (<exception_pattern>) { ... }`` block."""
    match = re.search(
        rf"catch\s*\(\s*{exception_pattern}\s*\)\s*\{{",
        method_body,
    )
    if match is None:
        raise AssertionError(
            f"Catch block matching pattern {exception_pattern!r} not found"
        )
    return _extract_braced_block(
        method_body,
        match.end(),
        f"catch({exception_pattern})",
    )


class TestRunScriptPollFrameRuntimeCatchesNoLeakInEnvelope(unittest.TestCase):
    """Issue #216: per-frame runtime catches carry no exception text."""

    def test_target_invocation_exception_envelope_has_no_exception_text(
        self,
    ) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        catch_body = _extract_catch_block(
            body, r"TargetInvocationException\s+tie",
        )
        # Strip the Debug.LogWarning interpolation up to the next line
        # so the warning's diagnostic interpolation does not match
        # against the leak-token scan.  We only look at the envelope
        # construction afterwards.
        envelope_segment = re.sub(
            r"Debug\.LogWarning\([^;]*;",
            "",
            catch_body,
        )
        offenders = [
            token for token in _LEAK_TOKENS if token in envelope_segment
        ]
        self.assertEqual(
            [],
            offenders,
            msg=(
                "RunScriptPollFrame TargetInvocationException catch site's "
                "envelope construction must not contain any exception-text "
                f"tokens; offenders={offenders}."
            ),
        )

    def test_general_exception_envelope_has_no_exception_text(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        catch_body = _extract_catch_block(body, r"Exception\s+ex")
        envelope_segment = re.sub(
            r"Debug\.LogWarning\([^;]*;",
            "",
            catch_body,
        )
        offenders = [
            token for token in _LEAK_TOKENS if token in envelope_segment
        ]
        self.assertEqual(
            [],
            offenders,
            msg=(
                "RunScriptPollFrame general Exception catch site's "
                "envelope construction must not contain any exception-text "
                f"tokens; offenders={offenders}."
            ),
        )


class TestRunScriptPollFrameRuntimeCatchesRouteToConsole(unittest.TestCase):
    """Issue #216: per-frame runtime catches mirror detail to the console."""

    def test_target_invocation_exception_catch_calls_debug_logwarning(
        self,
    ) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        catch_body = _extract_catch_block(
            body, r"TargetInvocationException\s+tie",
        )
        # The original caught exception identifier (``inner``) must be
        # interpolated into the console warning so the detail is not
        # silently dropped.
        self.assertEqual(
            (True, True),
            (
                "Debug.LogWarning" in catch_body,
                bool(re.search(r"\{inner\}", catch_body)),
            ),
            msg=(
                "RunScriptPollFrame TargetInvocationException catch must "
                "interpolate the caught exception identifier into "
                "Debug.LogWarning so the detail is mirrored to the Unity "
                "console for local diagnosis."
            ),
        )

    def test_general_exception_catch_calls_debug_logwarning(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        catch_body = _extract_catch_block(body, r"Exception\s+ex")
        self.assertEqual(
            (True, True),
            (
                "Debug.LogWarning" in catch_body,
                bool(re.search(r"\{ex\}", catch_body)),
            ),
            msg=(
                "RunScriptPollFrame general Exception catch must "
                "interpolate the caught exception identifier into "
                "Debug.LogWarning so the detail is mirrored to the Unity "
                "console for local diagnosis."
            ),
        )


class TestHandleRunScriptStageRefreshCatchesNoLeak(unittest.TestCase):
    """Issue #216: pre-poll catches at the stage / refresh sites are leak-safe."""

    def test_stage_failure_envelope_has_no_exception_text(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScript")
        catch_body = _extract_catch_block(body, r"Exception\s+stagingEx")
        envelope_segment = re.sub(
            r"Debug\.LogWarning\([^;]*;",
            "",
            catch_body,
        )
        offenders = [
            token for token in _LEAK_TOKENS if token in envelope_segment
        ]
        self.assertEqual(
            (
                offenders,
                "Debug.LogWarning" in catch_body,
                bool(re.search(r"\{stagingEx\}", catch_body)),
            ),
            ([], True, True),
            msg=(
                "HandleRunScript staging catch must build a leak-free "
                f"envelope (offenders={offenders}) and interpolate the "
                "caught exception identifier into Debug.LogWarning."
            ),
        )

    def test_refresh_failure_envelope_has_no_exception_text(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScript")
        catch_body = _extract_catch_block(body, r"Exception\s+refreshEx")
        envelope_segment = re.sub(
            r"Debug\.LogWarning\([^;]*;",
            "",
            catch_body,
        )
        offenders = [
            token for token in _LEAK_TOKENS if token in envelope_segment
        ]
        self.assertEqual(
            (
                offenders,
                "Debug.LogWarning" in catch_body,
                bool(re.search(r"\{refreshEx\}", catch_body)),
            ),
            ([], True, True),
            msg=(
                "HandleRunScript refresh catch must build a leak-free "
                f"envelope (offenders={offenders}) and interpolate the "
                "caught exception identifier into Debug.LogWarning."
            ),
        )


class TestEditorControlDataDeclaresNoExceptionTextField(unittest.TestCase):
    """Issue #216: the shared response data shape carries no exception text."""

    def test_class_body_omits_exception_field(self) -> None:
        body = _extract_editor_control_data_body(_read(BRIDGE))
        self.assertNotRegex(
            body,
            r"public\s+string\s+exception\b",
            msg=(
                "EditorControlData must not declare an ``exception`` "
                "string field — exception text never crosses the MCP "
                "boundary per issue #216."
            ),
        )


# ---------------------------------------------------------------------------
# Issue #251 — HandleRunIntegrationTests catch leak-safe envelope + console
# mirror.  The pattern matches the four prior run-script catch sites
# fixed under issue #216: the MCP-bound envelope carries a fixed
# surface-identifying string and the full exception detail flows to the
# Unity Console via Debug.LogWarning only.
# ---------------------------------------------------------------------------


class TestHandleRunIntegrationTestsCatchNoLeakInEnvelope(unittest.TestCase):
    """Issue #251: the integration-tests handler's catch path returns a
    leak-safe envelope (no exception text in any envelope field) and
    mirrors the full caught exception detail to the Unity Console via a
    single Debug.LogWarning call.
    """

    def test_catch_envelope_has_no_exception_text(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunIntegrationTests")
        catch_body = _extract_catch_block(body, r"Exception\s+ex")
        # Strip the Debug.LogWarning interpolation up to the next ``;``
        # so the warning's diagnostic interpolation does not match
        # against the leak-token scan.  Only the envelope construction
        # afterwards must be leak-free.
        envelope_segment = re.sub(
            r"Debug\.LogWarning\([^;]*;",
            "",
            catch_body,
        )
        offenders = [
            token for token in _LEAK_TOKENS if token in envelope_segment
        ]
        self.assertEqual(
            [],
            offenders,
            msg=(
                "HandleRunIntegrationTests catch site's envelope "
                "construction must not contain any exception-text tokens; "
                f"offenders={offenders}."
            ),
        )

    def test_catch_routes_detail_to_console(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunIntegrationTests")
        catch_body = _extract_catch_block(body, r"Exception\s+ex")
        # The full caught exception identifier (``ex``) must be
        # interpolated into the console warning so the detail is mirrored
        # rather than silently dropped.
        self.assertEqual(
            (True, True),
            (
                "Debug.LogWarning" in catch_body,
                bool(re.search(r"\{ex\}", catch_body)),
            ),
            msg=(
                "HandleRunIntegrationTests catch must call Debug.LogWarning "
                "and interpolate ``{ex}`` so the full exception detail "
                "is mirrored to the Unity Console for local diagnosis."
            ),
        )


# ---------------------------------------------------------------------------
# Issue #252 — CleanupRunScriptTempFiles post-refresh log carries the
# full exception detail token (``{ex}``), aligning with the four prior
# run-script catch sites fixed under issue #216.
# ---------------------------------------------------------------------------


class TestCleanupRunScriptTempFilesRefreshCatchLogFormat(unittest.TestCase):
    """Issue #252: the cleanup helper's post-refresh warning interpolates
    the full exception detail token (``{ex}`` or equivalent identifier
    used in the catch parameter) and not the message-only token
    (``{<id>.Message}``), so the stack trace and inner-exception chain
    surface in the Unity Console for local diagnosis.
    """

    def test_refresh_catch_uses_full_exception_token_not_message_only(self) -> None:
        body = _extract_method(_read(BRIDGE), "CleanupRunScriptTempFiles")
        catch_body = _extract_catch_block(body, r"Exception\s+(\w+)")
        # The catch parameter name is part of the surrounding source; the
        # body must interpolate that identifier directly (full detail) and
        # must not interpolate ``<identifier>.Message`` (message-only form
        # which strips the stack trace and inner-exception chain).
        self.assertRegex(
            catch_body,
            r"\{[a-zA-Z_]\w*\}",
            msg=(
                "CleanupRunScriptTempFiles refresh catch must interpolate "
                "the caught exception identifier (full-detail token) into "
                "the Debug.LogWarning call so the stack trace and inner "
                "chain are mirrored to the Unity Console."
            ),
        )
        self.assertNotRegex(
            catch_body,
            r"\{[a-zA-Z_]\w*\.Message\}",
            msg=(
                "CleanupRunScriptTempFiles refresh catch must not "
                "interpolate ``{<id>.Message}`` (message-only form) — "
                "use the full ``{<id>}`` token to preserve the stack "
                "trace, matching the rest of the run-script catch sites."
            ),
        )


# ---------------------------------------------------------------------------
# Issue #235 — BuildRecompileReloadWaitPoll drains the AssetDatabase
# import queue between the watermark check and the success envelope.
# ---------------------------------------------------------------------------


class TestBuildRecompileReloadWaitPollDrainsImportQueue(unittest.TestCase):
    """Issue #235: the post-reload poll synchronously drains the
    AssetDatabase import queue between observing the assembly-reload
    watermark advance and writing the success envelope. A drain failure
    is mirrored to the Unity Console without affecting the envelope
    outcome (the contract concerns compilation, not import completion).
    """

    def test_success_branch_calls_assetdatabase_refresh_before_writing(self) -> None:
        body = _extract_method(_read(BRIDGE), "BuildRecompileReloadWaitPoll")
        # End-state ordering: the watermark comparison
        # (``AssemblyReloadCount <= reloadCountThreshold``) must come
        # before the synchronous AssetDatabase refresh, which in turn
        # must come before the success ``WriteResponse`` call.
        watermark_pos = body.find("AssemblyReloadCount")
        refresh_pos = body.find("AssetDatabase.Refresh")
        success_pos = body.find("EDITOR_CTRL_RECOMPILE_AND_WAIT_OK")
        self.assertEqual(
            (True, True, True),
            (
                watermark_pos >= 0,
                refresh_pos > watermark_pos,
                success_pos > refresh_pos,
            ),
            msg=(
                "BuildRecompileReloadWaitPoll's success branch must order "
                "(1) AssemblyReloadCount watermark check, "
                "(2) AssetDatabase.Refresh import-queue drain, "
                "(3) success envelope write — observed positions "
                f"watermark={watermark_pos}, refresh={refresh_pos}, "
                f"success={success_pos}."
            ),
        )

    def test_drain_call_uses_synchronous_import_options(self) -> None:
        body = _extract_method(_read(BRIDGE), "BuildRecompileReloadWaitPoll")
        # The drain is required to be synchronous so a freshly compiled
        # asset path resolves on the call immediately following the
        # success envelope on the documented happy path.
        self.assertIn(
            "ImportAssetOptions.ForceSynchronousImport",
            body,
            msg=(
                "BuildRecompileReloadWaitPoll drain must use "
                "ImportAssetOptions.ForceSynchronousImport so the import "
                "queue is drained synchronously before the success "
                "envelope is written (issue #235)."
            ),
        )

    def test_drain_failure_logged_and_does_not_crash_poll(self) -> None:
        body = _extract_method(_read(BRIDGE), "BuildRecompileReloadWaitPoll")
        # The drain refresh call must be wrapped in a try/catch that
        # mirrors the failure to the Unity Console and continues to write
        # the success envelope; an unhandled refresh exception must not
        # turn the success path into a crash.
        try_pos = body.find("try {")
        if try_pos < 0:
            try_pos = body.find("try\n")
        catch_pos = body.find("catch (Exception")
        warning_pos = body.find("Debug.LogWarning")
        self.assertEqual(
            (True, True, True),
            (
                try_pos >= 0,
                catch_pos > try_pos,
                warning_pos > catch_pos,
            ),
            msg=(
                "BuildRecompileReloadWaitPoll's drain call must be "
                "wrapped in try/catch with Debug.LogWarning so an "
                "unhandled drain exception cannot turn the success path "
                "into a crash (issue #235)."
            ),
        )


# ---------------------------------------------------------------------------
# Issue #234 — RunScriptCompilePendingResponse deadline-only branch emits
# a dedicated compile-timeout code distinct from the generic compile /
# staging / entry-point failure code and from the wrapper-layer transport
# timeout code.
# ---------------------------------------------------------------------------


class TestRunScriptCompilePendingResponseDeadlinePath(unittest.TestCase):
    """Issue #234: the compile-pending response builder distinguishes the
    deadline-only timeout code from the consecutive-stuck recovery code.

    Post H-track migration the recovery-vs-timeout code selection was
    extracted into the Unity-free ``RunScriptCompilePendingCodeSelector``
    (behavioral coverage in
    ``tests/csharp/RunScriptCompileValidatorTests.cs``). This source-text
    test retains the Tier 3 delegation invariant (the builder routes
    through ``RunScriptCompilePendingCodeSelector.SelectCode``) plus
    constant-value pins on the relocated codes.
    """

    def test_builder_delegates_to_pending_code_selector(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptCompilePendingResponse")
        self.assertIn("RunScriptCompilePendingCodeSelector.SelectCode", body)
        self.assertIn("RunScriptCompilePendingCodeSelector.RecoveryCode", body)

    def test_deadline_branch_does_not_emit_generic_compile_code(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptCompilePendingResponse")
        # The generic compile-failure code must not be returned from this
        # builder; the selector picks recovery vs. dedicated timeout.
        self.assertNotIn("EDITOR_CTRL_RUN_SCRIPT_COMPILE", body)

    def test_pending_codes_pinned_on_selector(self) -> None:
        source = RUN_SCRIPT_COMPILE_VALIDATORS.read_text(encoding="utf-8")
        self.assertIn(
            'RecoveryCode = "EDITOR_CTRL_RUN_SCRIPT_RECOVERY"', source
        )
        self.assertIn(
            'TimeoutCode = "EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT"', source
        )


# ---------------------------------------------------------------------------
# Issue #248 — HasEditorScriptChangedSince walks the Assets root and
# selects only paths whose chain contains an Editor segment, with the
# run-script temp-area exclusion preserved.
# ---------------------------------------------------------------------------


class TestHasEditorScriptChangedSinceScopeExpanded(unittest.TestCase):
    """Issue #248: the implicit-recompile-barrier dirty-source predicate
    walks the entire Assets root and matches any C# file under any
    Editor-named directory segment, retaining the run-script temp-area
    exclusion. The hard-coded single-root walk against ``Assets/Editor``
    is gone so feature-scoped editor folders (under nested Editor-named
    directories) participate in the dirty-source check.
    """

    def test_predicate_walks_assets_root(self) -> None:
        body = _extract_method(_read(BRIDGE), "HasEditorScriptChangedSince")
        # The walk root constant must name the Assets root and be
        # referenced in the predicate body. The hard-coded
        # ``Assets/Editor`` literal as a single walk root must be gone.
        self.assertIn(
            "MenuExecuteAssetsRoot",
            body,
            msg=(
                "HasEditorScriptChangedSince must reference the "
                "MenuExecuteAssetsRoot constant so the walk covers the "
                "entire Assets root (issue #248)."
            ),
        )
        self.assertNotRegex(
            body,
            r'"Assets/Editor"',
            msg=(
                "HasEditorScriptChangedSince must not hard-code "
                "``\"Assets/Editor\"`` as a single walk root literal — "
                "feature-scoped Editor folders nested under Assets/ must "
                "participate (issue #248)."
            ),
        )

    def test_predicate_delegates_segment_classification(self) -> None:
        # Post H-track migration the per-path Editor-segment match and the
        # run-script temp-area exclusion were extracted into the Unity-free
        # ``EditorScriptPathClassifier`` (behavioral coverage in
        # ``tests/csharp/EditorScriptPathClassifierTests.cs``); the walk
        # predicate routes per-path classification through it.
        body = _extract_method(_read(BRIDGE), "HasEditorScriptChangedSince")
        self.assertIn("EditorScriptPathClassifier.IsEditorSourcePath", body)

    def test_walk_root_constant_is_assets(self) -> None:
        source = _read(BRIDGE)
        # The walk-root constant literal must remain ``Assets`` (the
        # entire Assets root); it stays declared on MenuScriptWatch.cs.
        self.assertRegex(source, r'MenuExecuteAssetsRoot\s*=\s*"Assets"')

    def test_classifier_segment_constants_pinned(self) -> None:
        # Constant-value pin: the Editor-segment and run-script temp-area
        # segment literals moved into EditorScriptPathClassifier.
        source = EDITOR_SCRIPT_PATH_CLASSIFIER.read_text(encoding="utf-8")
        self.assertIn('EditorSegment = "Editor"', source)
        self.assertIn('RunScriptTempSegment = "_PrefabSentinelTemp"', source)


class ScreenshotViewAllowlistSourceTests(unittest.TestCase):
    """Issue #259 / #222 Phase 3 — the bridge-side screenshot handler
    refuses any view selector outside the bridge-side allowlist BEFORE
    any output-path composition.  The allowlist is the two lower-case
    ASCII selectors ``"scene"`` and ``"game"``; an unrecognised selector
    returns the typed ``EDITOR_CTRL_SCREENSHOT_VIEW_INVALID`` error
    envelope, so no filename is composed and no file is written.

    T3 source-text invariant: the bridge runs inside the Unity Editor
    and this repository's Unity-free harness cannot exercise the
    handler end-to-end (see Tier 3 Justification).  The behavioral
    decision is now delegated to
    ``ScreenshotViewAllowlistClassifier.IsAccepted`` which is covered
    end-to-end by the C# xUnit harness
    (``ScreenshotViewAllowlistClassifierTests``); this T3 net is
    retained as a regression guard for the delegation-site wiring and
    for the relative ordering of the reject path against the
    ``Path.Combine`` interpolation that composes the filename out of
    ``request.view``.
    """

    def test_handler_delegates_view_acceptance_to_pure_classifier(self) -> None:
        """C-4 — the handler reaches its view-acceptance gate through
        ``ScreenshotViewAllowlistClassifier.IsAccepted`` and no longer
        carries an inline classifier loop in its body.
        """
        body = _extract_method(_read(BRIDGE), "HandleCaptureScreenshot")
        self.assertIn(
            "ScreenshotViewAllowlistClassifier.IsAccepted",
            body,
            msg=(
                "HandleCaptureScreenshot must delegate the view-acceptance "
                "gate to ScreenshotViewAllowlistClassifier.IsAccepted (#222 Phase 3)."
            ),
        )
        # The previous inline ``foreach (var supported in SupportedScreenshotViews)``
        # loop must be gone — otherwise the delegation can silently
        # coexist with a parallel decision path and the classifier net
        # would not cover the live behaviour.
        self.assertNotRegex(
            body,
            r"foreach\s*\(\s*var\s+\w+\s+in\s+SupportedScreenshotViews\s*\)",
            msg=(
                "HandleCaptureScreenshot must not retain an inline "
                "foreach loop over SupportedScreenshotViews; the "
                "decision belongs to the pure classifier."
            ),
        )

    def test_handler_references_bridge_view_invalid_code_literal(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCaptureScreenshot")
        # The bridge-side error code must appear as a literal so a
        # caller can correlate a rejection with the documented code.
        self.assertIn(
            '"EDITOR_CTRL_SCREENSHOT_VIEW_INVALID"',
            body,
            msg=(
                "HandleCaptureScreenshot must reference the bridge-side "
                "EDITOR_CTRL_SCREENSHOT_VIEW_INVALID error code literal "
                "(#259)."
            ),
        )

    def test_handler_allowlist_check_precedes_filename_composition(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCaptureScreenshot")
        # Pin: the reject literal appears in source order BEFORE the
        # filename-composition call that interpolates the view selector
        # into the output path.  This is the defense-in-depth property
        # — a rejected view never causes a file write.
        reject_pos = body.find('"EDITOR_CTRL_SCREENSHOT_VIEW_INVALID"')
        # Path.Combine(... $"{request.view}_ ...) is the filename
        # composition site we anchor against.
        filename_pos = body.find('{request.view}_')
        self.assertEqual(
            (True, True, True),
            (
                reject_pos >= 0,
                filename_pos >= 0,
                reject_pos < filename_pos,
            ),
            msg=(
                "Bridge-side allowlist reject (literal "
                "``EDITOR_CTRL_SCREENSHOT_VIEW_INVALID``) must appear "
                "in source order before the ``Path.Combine`` call that "
                "interpolates ``request.view`` into the output filename; "
                f"observed reject_pos={reject_pos}, "
                f"filename_pos={filename_pos} (#259)."
            ),
        )

    def test_handler_pins_scene_and_game_allowlist_literals(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCaptureScreenshot")
        # The two accepted selectors must both appear as literals so
        # the bridge-side allowlist matches the wrapper-side allowlist
        # verbatim.  Both are lower-case ASCII.
        self.assertEqual(
            (True, True),
            ('"scene"' in body, '"game"' in body),
            msg=(
                "Bridge-side allowlist must enumerate both "
                "lower-case ASCII selectors (``\"scene\"`` and "
                "``\"game\"``) so the two layers cannot drift (#259)."
            ),
        )


def _extract_resolve_object_reference_body(source: str) -> str:
    """Custom extractor for ``ResolveObjectReference`` because the
    method's return type is the value tuple ``(UnityEngine.Object obj,
    string error)`` that ``_extract_method``'s ``\\S+`` return-type
    group does not match.
    """
    start = source.find(
        "ResolveObjectReference(string reference)",
    )
    if start == -1:
        raise AssertionError(
            "ResolveObjectReference method declaration not found",
        )
    # Step back to the method's declaration start (the visibility
    # token at the line head); the brace-counting walks from there.
    line_start = source.rfind("\n", 0, start) + 1
    brace_count = 0
    found_open = False
    for i in range(line_start, len(source)):
        if source[i] == "{":
            brace_count += 1
            found_open = True
        elif source[i] == "}":
            brace_count -= 1
            if found_open and brace_count == 0:
                return source[line_start : i + 1]
    raise AssertionError(
        "Could not find closing brace for ResolveObjectReference",
    )


class HelpersResolveObjectReferenceSourceTests(unittest.TestCase):
    """Issue #258 — ``ResolveObjectReference`` delegates its
    GameObject phase to ``ResolveGameObjectInActiveStage`` so
    references inside an active Prefab Stage resolve to the stage's
    contents.  Outside any stage, the helper falls through to
    ``GameObject.Find`` transparently, preserving existing behaviour.

    T3 source-text invariant: the resolver runs inside the Unity
    Editor and no in-repo harness exercises its GameObject phase
    end-to-end (see Tier 3 Justification).  The source-text net pins
    the helper call's presence and the absence of the direct scene-
    only ``GameObject.Find`` call in that phase.
    """

    def test_resolver_delegates_gameobject_phase_to_stage_aware_helper(self) -> None:
        body = _extract_resolve_object_reference_body(_read(BRIDGE))
        # The resolver's GameObject phase must consult the stage-aware
        # helper.  ``ResolveGameObjectInActiveStage`` is the single
        # documented entry point.
        self.assertIn(
            "ResolveGameObjectInActiveStage",
            body,
            msg=(
                "ResolveObjectReference must consult "
                "ResolveGameObjectInActiveStage so references inside "
                "an active Prefab Stage resolve to the stage's contents "
                "(#258)."
            ),
        )

    def test_resolver_does_not_call_direct_scene_only_lookup(self) -> None:
        body = _extract_resolve_object_reference_body(_read(BRIDGE))
        # The resolver must not call ``GameObject.Find`` directly in
        # its body; the stage-aware helper owns the scene fallback
        # transparently when no stage is active.
        self.assertNotRegex(
            body,
            r"GameObject\.Find\s*\(",
            msg=(
                "ResolveObjectReference must not call "
                "``GameObject.Find`` directly; the stage-aware helper "
                "owns the scene fallback so references inside a Prefab "
                "Stage are not silently dropped (#258)."
            ),
        )


class MenuHasEditorScriptChangedSinceSegmentExclusionTests(unittest.TestCase):
    """Issue #255 — the run-script temp-area exclusion is whole-segment
    equality on the path's directory chain, parallel to the
    Editor-segment companion check.

    Post H-track migration the whole-segment-equality logic (and its
    ``_PrefabSentinelTemp`` exclusion constant) was extracted into the
    Unity-free ``EditorScriptPathClassifier``; the segment-match
    behavioral coverage now lives in
    ``tests/csharp/EditorScriptPathClassifierTests.cs``. This source-text
    test retains the Tier 3 delegation invariant (the change detector
    routes through ``EditorScriptPathClassifier.IsEditorSourcePath``)
    plus a constant-value pin on the relocated exclusion literal.
    """

    def test_change_detector_delegates_to_path_classifier(self) -> None:
        body = _extract_method(_read(BRIDGE), "HasEditorScriptChangedSince")
        self.assertIn("EditorScriptPathClassifier.IsEditorSourcePath", body)

    def test_temp_exclusion_constant_literal_value_unchanged(self) -> None:
        # The constant value is part of the public operating convention
        # (CLAUDE.md / README.md); a rename would silently break the
        # temp-exclusion contract with the run-script handler that writes
        # there. The literal now lives on EditorScriptPathClassifier.
        source = EDITOR_SCRIPT_PATH_CLASSIFIER.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r'RunScriptTempSegment\s*=\s*"_PrefabSentinelTemp"',
        )


class EditorControlBridgeRequestSchemaTests(unittest.TestCase):
    """Spec row "Bridge request and response DTOs declare every new field"
    (T3, source-text-invariant) — the EditorControlRequest and
    EditorControlData DTOs must declare every field consumed by the new
    actions introduced by this run (screenshot region, blend-shape
    pagination, batch blend-shape payload, async submit/poll, animation
    clip primitives, prefab-stage save flag, scene-view refresh, audit
    pair on the audited wrappers).

    Behavior pinned: protocol drift between the Python wrappers and the
    bridge DTOs.  A field renamed or removed C#-side that breaks the
    wire would otherwise surface only at integration time; this row is
    the source-text regression net (justified in spec.md Tier 3
    Justification).
    """

    _NEW_REQUEST_FIELDS = (
        # Screenshot region (issue #249).
        "crop_roi",
        # Blend-shape pagination (issue #241).
        "offset",
        "limit",
        # Batch blend-shape (issue #240).
        "shapes_json",
        # Async submit/poll (issue #233).
        "request_id",
        "cleanup_on_timeout",
        # Animation-clip primitives (issue #243).
        "target_dir",
        "animation_clip_name",
        "curves_json",
        # Prefab Stage save flag (issue #236).
        "save_on_close",
    )

    _NEW_RESPONSE_FIELDS = (
        # Screenshot region.
        "crop_roi_applied",
        "crop_bounds",
        # Blend-shape pagination continuation.
        "next_cursor",  # already declared but reused for pagination;
        # Batch blend-shape result.
        "set_count",
        "failed_shapes",
        # Animation-clip primitives.
        "applied_curve_count",
        "curves",
        "frame_rate",
        "length",
        "asset_path",
        "curve_count",
        # Prefab Stage close.
        "stage_root_name",
        "saved",
        # Async submit acceptance.
        "request_id",
        "accepted_at",
        # Force scene-view refresh.
        "renderers_touched",
        # Async poll status.
        "status",
    )

    def test_request_dto_declares_every_new_field(self) -> None:
        body = _extract_editor_control_request_body()
        for field in self._NEW_REQUEST_FIELDS:
            with self.subTest(field=field):
                self.assertIn(
                    field, body,
                    msg=f"EditorControlRequest missing new field '{field}'.",
                )

    def test_response_dto_declares_every_new_field(self) -> None:
        source = _read(BRIDGE)
        # Locate the EditorControlData class body (response DTO).
        start = source.find("public sealed class EditorControlData")
        self.assertNotEqual(-1, start, "EditorControlData class not found")
        brace = 0
        opened = False
        end = -1
        for i in range(start, len(source)):
            ch = source[i]
            if ch == "{":
                brace += 1
                opened = True
            elif ch == "}":
                brace -= 1
                if opened and brace == 0:
                    end = i + 1
                    break
        self.assertNotEqual(-1, end, "EditorControlData closing brace not found")
        body = source[start:end]
        for field in self._NEW_RESPONSE_FIELDS:
            with self.subTest(field=field):
                self.assertIn(
                    field, body,
                    msg=f"EditorControlData missing new response field '{field}'.",
                )


class EditorControlBridgeScreenshotCameraStateRestoreTests(unittest.TestCase):
    """Spec row "Bridge screenshot handler restores camera state on preset
    path" (T3, source-text-invariant) — the screenshot partial must
    contain a save-then-restore call pair for the SceneView camera state
    on the preset branch so callers observe no persistent scene-view
    framing change (issue #249).

    The Unity Editor camera framing API only runs inside the editor
    process; the Python harness cannot drive it.  Source-text shape is
    the regression net (justified in spec.md Tier 3 Justification).
    """

    _SCREENSHOT_PARTIAL = (
        "PrefabSentinel.UnityEditorControlBridge.Screenshot.cs"
    )

    def test_screenshot_partial_exists(self) -> None:
        path = TOOLS_DIR / self._SCREENSHOT_PARTIAL
        self.assertTrue(
            path.is_file(),
            f"missing screenshot partial: {self._SCREENSHOT_PARTIAL}",
        )

    def test_preset_branch_saves_and_restores_camera_state(self) -> None:
        path = TOOLS_DIR / self._SCREENSHOT_PARTIAL
        text = path.read_text(encoding="utf-8")
        body = _extract_method(text, "HandleCaptureScreenshot")
        # The preset branch must capture the SceneView's camera state
        # via the existing CaptureCameraState helper, frame onto the
        # preset target, then restore via SceneView.LookAt(... instant:
        # true) — both the capture and the restore must appear in the
        # handler body, paired around the framing operation.
        self.assertIn(
            "CaptureCameraState",
            body,
            msg=(
                "HandleCaptureScreenshot preset branch must save the "
                "SceneView camera state via CaptureCameraState before "
                "framing onto the preset target."
            ),
        )
        self.assertIn(
            "LookAt",
            body,
            msg=(
                "HandleCaptureScreenshot preset branch must restore the "
                "SceneView camera state via SceneView.LookAt after the "
                "framing-and-render pass so callers observe no "
                "persistent scene-view state change."
            ),
        )


class EditorControlBridgeDispatcherRoutingTests(unittest.TestCase):
    """Spec row "Dispatcher routes the new action labels" (T3,
    source-text-invariant) — the bridge SupportedActions set, the
    Python supported-action set, and the dispatcher switch in
    RunFromPaths must each carry every new action label introduced by
    this run (screenshot region-extended action reused as
    capture_screenshot, force_scene_view_refresh, batch_set_blend_shape,
    open_prefab, close_prefab, run_script_submit, run_script_poll,
    inspect_animation_clip, create_animation_clip, apply_animation_clip).

    Dispatcher routing happens inside Unity's compiled assembly; the
    Python harness cannot execute the switch.  Source-text shape is
    the regression net (justified in spec.md Tier 3 Justification).
    """

    _NEW_ACTIONS = (
        "force_scene_view_refresh",
        "batch_set_blend_shape",
        "open_prefab",
        "close_prefab",
        "run_script_submit",
        "run_script_poll",
        "inspect_animation_clip",
        "create_animation_clip",
        "apply_animation_clip",
    )

    _NEW_HANDLER_NAMES = {
        "force_scene_view_refresh": "HandleForceSceneViewRefresh",
        "batch_set_blend_shape": "HandleBatchSetBlendShape",
        "open_prefab": "HandleOpenPrefab",
        "close_prefab": "HandleClosePrefab",
        "run_script_submit": "HandleRunScriptSubmit",
        "run_script_poll": "HandleRunScriptPoll",
        "inspect_animation_clip": "HandleInspectAnimationClip",
        "create_animation_clip": "HandleCreateAnimationClip",
        "apply_animation_clip": "HandleApplyAnimationClip",
    }

    def test_supported_actions_lists_every_new_action(self) -> None:
        # Post H-track migration the action-string set is the canonical
        # ``ActionRegistry.Supported`` HashSet literal in
        # ``PrefabSentinel.Dispatch.ActionRegistry.cs``.
        block = _action_registry_hashset("Supported")
        for action in self._NEW_ACTIONS:
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', block)

    def test_dispatcher_routes_every_new_action(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "RunFromPaths")
        for action, handler in self._NEW_HANDLER_NAMES.items():
            with self.subTest(action=action):
                self.assertIn(
                    f'case "{action}":',
                    body,
                    msg=(
                        f"RunFromPaths missing dispatcher case for "
                        f"action '{action}'."
                    ),
                )
                self.assertIn(
                    handler, body,
                    msg=(
                        f"RunFromPaths case for '{action}' must route "
                        f"to '{handler}'."
                    ),
                )

    def test_python_supported_action_set_lists_every_new_action(self) -> None:
        from prefab_sentinel.editor_bridge import SUPPORTED_ACTIONS
        for action in self._NEW_ACTIONS:
            with self.subTest(action=action):
                self.assertIn(action, SUPPORTED_ACTIONS)


class PrefabStagePersistFixSourceInvariantTests(unittest.TestCase):
    """Issue #264 — source-text pins on the post-fix shape of the
    Prefab Stage partial.

    The C# bridge runs inside Unity Editor; we cannot execute
    ``ResolveGameObjectInActiveStage`` or ``HandleClosePrefab`` from
    the Python test harness (spec Tier 3 Justification: no
    ``dotnet test`` / Unity batchmode harness in this repo).  These
    invariants prevent regressions to the pre-fix behavior:

    * the active-stage branch must not call ``GameObject.Find`` (the
      scene-leak bug);
    * the close handler must persist through
      ``PrefabUtility.SaveAsPrefabAsset`` rather than
      ``EditorSceneManager.SaveScene`` on the preview scene;
    * the close handler must clear the stage's dirty marker after a
      successful save so closing does not pop a "Save?" modal;
    * the close handler must bind the response's ``saved`` flag to the
      persistence-API's reported ``success`` rather than the
      caller-supplied request flag.
    """

    _PREFAB_STAGE_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.PrefabStage.cs"
    )

    def _resolver_body(self) -> str:
        return _extract_method(
            self._PREFAB_STAGE_PARTIAL.read_text(encoding="utf-8"),
            "ResolveGameObjectInActiveStage",
        )

    def _close_handler_body(self) -> str:
        return _extract_method(
            self._PREFAB_STAGE_PARTIAL.read_text(encoding="utf-8"),
            "HandleClosePrefab",
        )

    def test_resolver_normalizes_absolute_paths_under_stage_root(self) -> None:
        body = self._resolver_body()
        # The leading-slash normalization must run inside the
        # active-stage branch — i.e. between the ``stage != null`` /
        # ``stageRoot != null`` guards and the ``stageRoot.transform
        # .Find`` call.  Pin the literal ``StartsWith("/"`` token AND
        # the substring-strip token together.
        self.assertIn(
            'StartsWith("/"',
            body,
            msg=(
                "ResolveGameObjectInActiveStage must normalize "
                "leading-slash paths before delegating to the stage "
                "root (issue #264 absolute-path regression)."
            ),
        )
        self.assertIn(
            "Substring(1)",
            body,
            msg=(
                "ResolveGameObjectInActiveStage must strip the "
                "leading slash with ``Substring(1)`` before "
                "delegating to Transform.Find."
            ),
        )
        self.assertIn(
            "stageRoot.transform.Find",
            body,
            msg=(
                "ResolveGameObjectInActiveStage must descend into the "
                "stage root via Transform.Find when a stage is active."
            ),
        )

    def test_resolver_active_stage_branch_has_no_scene_find(self) -> None:
        """Inside the ``stage != null`` branch, no ``GameObject.Find``
        call may appear; the scene-wide lookup is reserved for the
        terminal no-stage path.
        """
        body = self._resolver_body()
        # Extract the source between the ``if (stage != null)`` guard
        # and its matching close-brace.  Find the guard, then walk
        # braces.
        guard_idx = body.find("if (stage != null)")
        self.assertNotEqual(
            -1, guard_idx,
            msg="active-stage guard ``if (stage != null)`` is missing",
        )
        open_idx = body.find("{", guard_idx)
        self.assertNotEqual(
            -1, open_idx,
            msg=(
                "Opening brace for the active-stage guard block must "
                "be present so the branch body can be extracted."
            ),
        )
        active_branch = _extract_braced_block(
            body, open_idx + 1, "active-stage branch",
        )
        self.assertNotIn(
            "GameObject.Find",
            active_branch,
            msg=(
                "Active-stage branch must NOT call GameObject.Find "
                "(issue #264 scene-leak regression)."
            ),
        )

    def test_resolver_inactive_stage_terminal_uses_scene_find(self) -> None:
        """The no-stage terminal path must consult the open scene via
        ``GameObject.Find`` so existing scene-edit workflows continue
        to work when no Prefab Stage is open.
        """
        body = self._resolver_body()
        self.assertIn(
            "return GameObject.Find(hierarchyPath);",
            body,
            msg=(
                "ResolveGameObjectInActiveStage must consult the open "
                "scene via ``GameObject.Find`` as its no-stage "
                "terminal path."
            ),
        )

    def test_close_handler_persists_via_prefab_asset_api(self) -> None:
        body = self._close_handler_body()
        self.assertIn(
            "PrefabUtility.SaveAsPrefabAsset",
            body,
            msg=(
                "HandleClosePrefab save branch must persist staged "
                "contents through PrefabUtility.SaveAsPrefabAsset "
                "(issue #264 preview-scene save fix)."
            ),
        )
        # The bug-fix replaces the preview-scene ``SaveScene`` call;
        # the post-fix source must not call SaveScene against
        # ``stage.scene`` anywhere in the close handler.
        self.assertNotIn(
            "EditorSceneManager.SaveScene(stage.scene)",
            body,
            msg=(
                "HandleClosePrefab must not save the stage's preview "
                "scene via EditorSceneManager.SaveScene (issue #264)."
            ),
        )

    def test_close_handler_clears_dirtiness_on_success(self) -> None:
        body = self._close_handler_body()
        self.assertIn(
            "stage.ClearDirtiness()",
            body,
            msg=(
                "HandleClosePrefab must call ``stage.ClearDirtiness()`` "
                "after a successful save so closing does not pop the "
                "Unity Save? modal (issue #264)."
            ),
        )

    def test_close_handler_save_flag_bound_to_persistence_api(self) -> None:
        """The response's ``saved`` field must come from the
        persistence API's reported success — not from the caller's
        request flag.  Pin the ``out didSave`` form and assert the
        old ``didSave = true`` literal assignment is gone.
        """
        body = self._close_handler_body()
        self.assertIn(
            "out didSave",
            body,
            msg=(
                "HandleClosePrefab must bind ``didSave`` to the "
                "PrefabUtility.SaveAsPrefabAsset out-parameter, not "
                "to the caller's request flag (issue #264)."
            ),
        )
        # Find the save-requested branch and confirm no naked
        # ``didSave = true;`` assignment exists inside it.
        save_branch_start = body.find("if (request.save_on_close)")
        self.assertNotEqual(
            -1, save_branch_start,
            msg="save-requested branch guard missing",
        )
        open_idx = body.find("{", save_branch_start)
        save_branch = _extract_braced_block(
            body, open_idx + 1, "save-requested branch",
        )
        self.assertNotRegex(
            save_branch,
            r"didSave\s*=\s*true\s*;",
            msg=(
                "HandleClosePrefab save branch must not assign "
                "``didSave = true`` outside the SaveAsPrefabAsset "
                "out-parameter (issue #264 save-flag-lies regression)."
            ),
        )

    def test_close_handler_no_save_branch_skips_persistence_call(self) -> None:
        """When ``save_on_close`` is false the persistence call must
        not run; pin that the API name appears only behind the
        request guard.
        """
        body = self._close_handler_body()
        save_branch_start = body.find("if (request.save_on_close)")
        self.assertNotEqual(
            -1, save_branch_start,
            msg=(
                "``if (request.save_on_close)`` guard must be present "
                "in the close handler so its body can be extracted."
            ),
        )
        open_idx = body.find("{", save_branch_start)
        save_branch = _extract_braced_block(
            body, open_idx + 1, "save-requested branch",
        )
        # The persistence call must live inside the save branch.
        self.assertIn(
            "PrefabUtility.SaveAsPrefabAsset",
            save_branch,
            msg=(
                "PrefabUtility.SaveAsPrefabAsset call must be inside "
                "the ``if (request.save_on_close)`` block."
            ),
        )
        # And the close-handler body must contain exactly one call to
        # the persistence API.  No second call means the no-save
        # branch cannot persist.
        self.assertEqual(
            1, body.count("PrefabUtility.SaveAsPrefabAsset"),
            msg=(
                "HandleClosePrefab must contain exactly one "
                "PrefabUtility.SaveAsPrefabAsset call (inside the "
                "save-requested branch)."
            ),
        )

    def test_close_handler_no_active_stage_returns_documented_envelope(
        self,
    ) -> None:
        body = self._close_handler_body()
        # The guard must precede any persistence attempt.  Pin both
        # the code and the documented message substring.
        guard_idx = body.find("if (stage == null)")
        self.assertNotEqual(
            -1, guard_idx,
            msg=(
                "HandleClosePrefab must guard on ``stage == null`` "
                "before attempting persistence."
            ),
        )
        persistence_idx = body.find("PrefabUtility.SaveAsPrefabAsset")
        self.assertNotEqual(
            -1, persistence_idx,
            msg=(
                "PrefabUtility.SaveAsPrefabAsset call site must be "
                "present in the close handler so its position can be "
                "compared to the guard."
            ),
        )
        self.assertLess(
            guard_idx, persistence_idx,
            msg=(
                "The no-active-stage guard must run before the "
                "persistence call to avoid dereferencing a null "
                "stage."
            ),
        )
        self.assertIn(
            "EDITOR_CTRL_PREFAB_STAGE_CLOSE_FAILED",
            body,
            msg=(
                "Close handler must emit "
                "EDITOR_CTRL_PREFAB_STAGE_CLOSE_FAILED on the "
                "no-active-stage path."
            ),
        )
        self.assertIn(
            "no Prefab Stage is currently active",
            body,
            msg=(
                "No-active-stage envelope must carry the documented "
                "message."
            ),
        )

    def test_close_handler_persistence_exception_carries_message(self) -> None:
        body = self._close_handler_body()
        # The catch block must interpolate the caught exception's
        # message into the response so callers see the underlying
        # cause.  Pin both the catch shape and the message
        # interpolation.
        self.assertIn(
            "catch (Exception ex)",
            body,
            msg="HandleClosePrefab must catch persistence exceptions.",
        )
        self.assertIn(
            "ex.Message",
            body,
            msg=(
                "HandleClosePrefab catch block must surface the "
                "caught exception's message (issue #264 silent-"
                "failure regression)."
            ),
        )
        self.assertIn(
            "EDITOR_CTRL_PREFAB_STAGE_CLOSE_FAILED",
            body,
            msg=(
                "Close-handler exception path must emit the "
                "documented error code."
            ),
        )


class MenuScriptWatchSplitSourceInvariantTests(unittest.TestCase):
    """Issue #262 — the editor-script mtime detector and its three
    standalone constants now live in a dedicated partial; the Menu
    partial no longer declares the detector.  These invariants pin
    the post-split layout end-to-end (filesystem, source, CLAUDE.md
    inventory).
    """

    _MENU_SCRIPT_WATCH_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.MenuScriptWatch.cs"
    )
    _MENU_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Menu.cs"
    )
    _CLAUDE_MD = (
        Path(__file__).resolve().parent.parent / "CLAUDE.md"
    )

    def test_dedicated_partial_declares_detector_with_documented_signature(
        self,
    ) -> None:
        self.assertTrue(
            self._MENU_SCRIPT_WATCH_PARTIAL.is_file(),
            msg=(
                "MenuScriptWatch partial must exist at the canonical "
                "path (issue #262)."
            ),
        )
        text = self._MENU_SCRIPT_WATCH_PARTIAL.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"private\s+static\s+bool\s+HasEditorScriptChangedSince\(\s*long\s+sinceUnixMs\s*\)",
            msg=(
                "MenuScriptWatch partial must declare "
                "HasEditorScriptChangedSince(long sinceUnixMs) with "
                "the documented signature (issue #262)."
            ),
        )

    def test_menu_partial_does_not_redeclare_detector(self) -> None:
        text = self._MENU_PARTIAL.read_text(encoding="utf-8")
        # Pin: no declaration in Menu.cs.  A call site is fine; only
        # the method body's declaration form is forbidden.
        self.assertNotRegex(
            text,
            r"private\s+static\s+bool\s+HasEditorScriptChangedSince\s*\(",
            msg=(
                "Menu partial must NOT redeclare "
                "HasEditorScriptChangedSince after the issue #262 "
                "split (two declarations break the C# compile)."
            ),
        )

    def test_menu_partial_still_invokes_detector(self) -> None:
        """The implicit-barrier predicate in HandleExecuteMenuItem still
        relies on the detector; the call site must reference the
        moved method by name so the cross-partial wiring survives.
        """
        text = self._MENU_PARTIAL.read_text(encoding="utf-8")
        self.assertIn(
            "HasEditorScriptChangedSince(",
            text,
            msg=(
                "Menu partial must still call "
                "HasEditorScriptChangedSince(...) from the "
                "implicit-barrier predicate after the issue #262 "
                "split."
            ),
        )

    def test_dedicated_partial_carries_walk_root_constant(self) -> None:
        """Post H-track migration only the walk-root constant
        (``MenuExecuteAssetsRoot``) remains on the MenuScriptWatch
        partial; the Editor-segment and run-script temp-area segment
        constants were extracted into the Unity-free
        ``EditorScriptPathClassifier`` (as ``EditorSegment`` /
        ``RunScriptTempSegment``).
        """
        text = self._MENU_SCRIPT_WATCH_PARTIAL.read_text(encoding="utf-8")
        self.assertIn("MenuExecuteAssetsRoot", text)
        # The two relocated constants must no longer be declared here.
        self.assertNotIn("MenuExecuteEditorSegment", text)
        self.assertNotIn("MenuExecuteRunScriptTempExclusion", text)

    def test_relocated_segment_constants_live_on_path_classifier(self) -> None:
        text = EDITOR_SCRIPT_PATH_CLASSIFIER.read_text(encoding="utf-8")
        for constant in ("EditorSegment", "RunScriptTempSegment"):
            with self.subTest(constant=constant):
                self.assertIn(constant, text)

    def test_claude_md_inventory_lists_menuscriptwatch(self) -> None:
        text = self._CLAUDE_MD.read_text(encoding="utf-8")
        self.assertIn(
            "MenuScriptWatch",
            text,
            msg=(
                "CLAUDE.md partial inventory must list the new "
                "MenuScriptWatch partial (issue #262)."
            ),
        )


class TestScreenshotRoutingUsesClassifier(unittest.TestCase):
    """Issue #310 — the scene-vs-game routing decision inside
    ``HandleCaptureScreenshot`` delegates to the same single-source
    classifier that gates view acceptance, so the scene-selector
    literal ``"scene"`` lives in exactly one place (the classifier).

    T3 source-text invariant: the routing decision is reachable only
    inside a Unity Editor host, so the in-repo guard is a source-level
    pin on the delegation site and the absence of a parallel
    literal-equality branch in the bridge file. The classifier-side
    contract is covered by the C# xUnit row in
    ``tests/csharp/PrefabSentinel.Tests/ScreenshotViewKindTests.cs``.
    """

    def _screenshot_partial_path(self) -> Path:
        return TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.cs"

    def test_handler_routing_calls_view_kind_helper(self) -> None:
        text = self._screenshot_partial_path().read_text(encoding="utf-8")
        stripped = _strip_cs_comments(text)
        self.assertIn(
            "ScreenshotViewAllowlistClassifier.IsSceneView",
            stripped,
            msg=(
                "Screenshot partial must route scene-vs-game through "
                "ScreenshotViewAllowlistClassifier.IsSceneView so the "
                "decision is owned by the classifier (issue #310)."
            ),
        )

    def test_scene_selector_literal_does_not_appear_in_free_standing_equality_check(
        self,
    ) -> None:
        # The literal ``"scene"`` may still appear as a member of the
        # SupportedScreenshotViews allowlist (declared once) and inside
        # documentation strings — both are masked by comment-stripping
        # and by the allowlist-declaration line which never includes
        # ``string.Equals``. The forbidden form is a free-standing
        # ``string.Equals(..., "scene", ...)`` inside the partial's
        # method bodies; that would re-introduce a parallel routing
        # branch outside the classifier.
        text = self._screenshot_partial_path().read_text(encoding="utf-8")
        stripped = _strip_cs_comments(text)
        # Match any ``string.Equals( ... "scene" ... )`` expression
        # within the partial source body.  Equality against the literal
        # is the wire shape the classifier replaces.
        self.assertNotRegex(
            stripped,
            r'string\.Equals\([^)]*"scene"[^)]*\)',
            msg=(
                "Free-standing string.Equals(..., \"scene\", ...) "
                "comparison present in the screenshot partial; route "
                "the decision through "
                "ScreenshotViewAllowlistClassifier.IsSceneView instead "
                "(issue #310)."
            ),
        )


if __name__ == "__main__":
    unittest.main()
