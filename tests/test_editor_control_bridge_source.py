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

from tests._typing_helpers import require_not_none

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
EDITOR_BRIDGE: Path = TOOLS_DIR / "PrefabSentinel.EditorBridge.cs"
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
    """Read the bridge source, C# comments stripped.

    When ``path`` resolves to the canonical bridge file, return every
    bridge partial concatenated so the regex-based extractors see the
    full class body.  Other paths are read as the single file.

    Issue #5/#358: the returned text always has ``//`` and ``/* ... */``
    comments stripped so every retained source-text grep matches code
    only — a literal surviving in a comment cannot produce a false-green
    assertion. A test that deliberately verifies comment content must
    read the raw source directly instead of through this helper.

    This helper exists for the bridge-concatenation case; tests that
    grep a single named-constant ``.cs`` path call
    ``_strip_cs_comments(path.read_text(...))`` directly — the
    equivalent single-file form — rather than routing through here.
    """
    if path == BRIDGE:
        parts: list[str] = []
        for cs_file in sorted(TOOLS_DIR.glob(_BRIDGE_GLOB)):
            parts.append(cs_file.read_text(encoding="utf-8"))
        return _strip_cs_comments("\n".join(parts))
    return _strip_cs_comments(path.read_text(encoding="utf-8"))


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
    """Issue #24 — the unified property-write layer.

    The two former property-write implementations — the boolean
    ``ApplyPropertyValue`` helper and the per-type ``switch`` inlined
    into ``HandleEditorSetProperty`` — are consolidated into a single
    ``WritePropertyValue`` layer returning a ``PropertyWriteResult``.
    The layer operates on a live Unity ``SerializedProperty`` and so
    cannot be executed by this harness (spec Tier 3 Justification: no
    Unity-loadable serialized-property harness in this repo). Its
    Unity-free parsing sub-logic is exercised at Tier 1 in
    ``tests/csharp/PropertyValueParserTests.cs``; these source-text
    invariants pin the parser delegation and the absence of any parallel
    implementation.
    """

    def test_unified_writer_delegates_parsing_to_property_value_parser(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "WritePropertyValue")
        self.assertIn(
            "PropertyValueParser.TryParse",
            body,
            msg=(
                "WritePropertyValue must route textual parsing through "
                "the Unity-free PropertyValueParser; a reverted inline "
                "parse path re-introduces the duplication (issue #24)."
            ),
        )

    def test_no_standalone_boolean_property_write_helper_remains(self) -> None:
        # Tier 3 Justification (spec): exactly one property-write layer
        # may exist. The former boolean ``ApplyPropertyValue`` helper
        # must be fully removed — no parallel implementation or alias.
        source = _read(BRIDGE)
        self.assertEqual(
            [],
            re.findall(r"\bApplyPropertyValue\b", source),
            msg=(
                "The boolean ApplyPropertyValue helper must not survive "
                "the issue #24 unification — no parallel property-write "
                "implementation or alias may remain."
            ),
        )

    def test_set_property_handler_carries_no_per_type_branching(self) -> None:
        # The per-type ``switch (prop.propertyType)`` block must live
        # only in the unified layer; the set-property handler obtains
        # its write outcome from WritePropertyValue.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn(
            "WritePropertyValue",
            body,
            msg=(
                "HandleEditorSetProperty must obtain its write outcome "
                "from the unified WritePropertyValue layer (issue #24)."
            ),
        )
        self.assertNotIn(
            "switch (prop.propertyType)",
            body,
            msg=(
                "HandleEditorSetProperty must not carry a per-type "
                "property switch — per-type dispatch is owned solely by "
                "the unified WritePropertyValue layer (issue #24)."
            ),
        )





class TestTypedPropertyWriterSource(unittest.TestCase):
    def _property_writer_source(self) -> str:
        return _read(TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.PropertyWrite.cs")

    def test_enum_writer_uses_declared_enum_backing_values(self) -> None:
        source = self._property_writer_source()
        body = _extract_method(source, "WriteEnumValue")
        self.assertIn("ResolveEnumBackingValues(prop)", body)
        resolver = _extract_method(source, "ResolveEnumBackingValues")
        self.assertIn("Enum.GetValues(enumType)", resolver)
        self.assertNotIn("Enumerable.Range(0, prop.enumNames.Length)", body)

    def test_object_reference_writer_uses_expected_type_and_typed_codes(self) -> None:
        writer = self._property_writer_source()
        body = _extract_method(writer, "WriteObjectReferenceValue")
        self.assertIn("ResolveExpectedObjectReferenceType(prop)", body)
        self.assertIn("ResolveTypedObjectReference", body)
        resolver_source = _read(
            TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.PropertyObjectReference.cs"
        )
        expected_type = _extract_method(
            resolver_source, "ResolveExpectedObjectReferenceType"
        )
        self.assertIn("EDITOR_CTRL_SET_PROP_OBJECT_REF_TYPE_MISMATCH", expected_type)
        resolver = _extract_method(resolver_source, "ResolveTypedObjectReference")
        for token in (
            "EDITOR_CTRL_SET_PROP_OBJECT_REF_NOT_FOUND",
            "EDITOR_CTRL_SET_PROP_OBJECT_REF_TYPE_MISMATCH",
            "EDITOR_CTRL_SET_PROP_OBJECT_REF_AMBIGUOUS",
        ):
            with self.subTest(token=token):
                self.assertIn(token, resolver)

    def test_object_reference_resolver_prefers_asset_paths_over_hierarchy_shorthand(self) -> None:
        resolver_source = _read(
            TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.PropertyObjectReference.cs"
        )
        resolver = _extract_method(resolver_source, "ResolveTypedObjectReference")
        asset_lookup = resolver.find("AssetDatabase.LoadAssetAtPath(reference, expectedType)")
        hierarchy_lookup = resolver.find("TryResolveGameObjectInActiveStage(goPath")
        self.assertNotEqual(
            -1,
            asset_lookup,
            msg="ResolveTypedObjectReference must look up asset paths directly.",
        )
        self.assertNotEqual(
            -1,
            hierarchy_lookup,
            msg="ResolveTypedObjectReference must keep hierarchy shorthand resolution.",
        )
        self.assertLess(
            asset_lookup,
            hierarchy_lookup,
            msg=(
                "ResolveTypedObjectReference must prefer project asset paths "
                "before hierarchy shorthand so asset references are not shadowed."
            ),
        )

    def test_property_write_result_carries_structured_error_data(self) -> None:
        source = self._property_writer_source()
        self.assertIn("internal readonly struct PropertyWriteResult", source)
        self.assertIn("EditorControlData ErrorData", source)
        handler = _extract_method(_read(TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Properties.cs"), "HandleEditorSetProperty")
        self.assertIn("writeResult.ErrorData", handler)


class TestHandleEditorSetPropertyQuaternion(unittest.TestCase):
    """Issue #24 / #111 — quaternion coverage in the unified layer.

    Quaternion handling moved from the inline ``HandleEditorSetProperty``
    switch into the unified ``WritePropertyValue`` layer, where it must
    remain covered. Arity and unit-norm validation is owned by the
    Unity-free ``QuaternionInputValidator`` (Tier 1-covered in
    ``tests/csharp/QuaternionInputValidatorTests.cs``). These source-text
    invariants pin the dispatch and the delegation; the final test is a
    constant-value pin on the relocated ``NormTolerance`` literal.
    """

    def test_unified_writer_dispatches_quaternion_type(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "WritePropertyValue")
        self.assertIn(
            "SerializedPropertyType.Quaternion",
            body,
            msg=(
                "WritePropertyValue must dispatch the Quaternion "
                "property type so quaternion coverage is not lost when "
                "the two implementations are consolidated (issue #24)."
            ),
        )

    def test_unified_writer_delegates_quaternion_to_validator(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "WriteQuaternionValue")
        self.assertIn(
            "QuaternionInputValidator.Validate",
            body,
            msg=(
                "The quaternion write path must route arity / unit-norm "
                "validation through QuaternionInputValidator.Validate."
            ),
        )

    def test_unified_writer_quaternion_surfaces_unit_norm_code(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "WriteQuaternionValue")
        # Non-unit norm rejection must surface the dedicated code.
        self.assertIn(
            "QuaternionInputValidator.NotNormalizedCode",
            body,
            msg=(
                "A non-unit-norm quaternion must surface the dedicated "
                "QuaternionInputValidator.NotNormalizedCode."
            ),
        )

    def test_handle_editor_set_property_quaternion_tolerance_constant(self) -> None:
        # Constant-value pin: the norm tolerance literal (1e-4f) lives
        # on QuaternionInputValidator; a regression must keep that value.
        source = _strip_cs_comments(INPUT_VALIDATORS.read_text(encoding="utf-8"))
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

    def test_handler_checks_request_id_existence_before_post_filters(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleCaptureConsoleLogs")
        self.assertIn(
            "bool requestIdSelectorActive = ConsoleCaptureRequestValidator.UsesRequestIdSelector(",
            body,
        )
        self.assertIn(
            "bool knownRequestId = !requestIdSelectorActive",
            body,
        )
        self.assertIn(
            "ConsoleLogBuffer.HasRequestId(request.since_request_id)",
            body,
        )
        self.assertLess(
            body.index("ConsoleLogBuffer.HasRequestId(request.since_request_id)"),
            body.index("ConsoleLogBuffer.GetEntries"),
            msg="request-id existence must be decided before type/classification/phase filters",
        )


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
    """Issue #108 (brushed-up under #222 Phase 1/2; #64): run-script
    completion detection is a two-phase split.  The pre-reload
    ``RunScriptPreReloadWatchdog`` enforces only the compile-pending
    deadline; the post-reload ``RunScriptPollFrame`` treats resolution
    of the freshly compiled temp type as the completion signal, with no
    assembly-modification-time gate.  The compile-pending response
    surfaced when the deadline elapses still hints at the persistent
    helper alternative.

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

    def test_completion_poll_has_no_assembly_mtime_gate(self) -> None:
        """Issue #64 — the post-reload completion poll detects completion
        by resolving the freshly compiled temp type, not by comparing
        assembly modification times; the editor-only temp script
        compiles into Assembly-CSharp-Editor.dll, which the old mtime
        gate (watching Assembly-CSharp.dll) never advanced.
        """
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        self.assertEqual(
            (False, False),
            (
                "ReadAssemblyMtimeUnixMs" in body,
                "callTimeAssemblyMtimeUnixMs" in body,
            ),
            msg=(
                "RunScriptPollFrame must carry no assembly-mtime gate "
                "(neither ReadAssemblyMtimeUnixMs nor "
                "callTimeAssemblyMtimeUnixMs)."
            ),
        )

    def test_deadline_only_pre_reload_watchdog_is_absent(self) -> None:
        """Issue #68 — the deadline-only ``RunScriptPreReloadWatchdog`` is
        replaced by the shared compile-watch barrier's deadline watchdog
        and must be absent from the bridge source.
        """
        source = _read(BRIDGE)
        self.assertNotIn(
            "RunScriptPreReloadWatchdog", source,
            msg=(
                "the deadline-only pre-reload run-script watchdog must be "
                "removed once the shared compile barrier owns the deadline."
            ),
        )

    def test_run_script_handlers_route_through_compile_barrier(self) -> None:
        """Issue #68 — both run-script entry handlers hand the compile
        observation to the shared ``ScheduleCompileBarrier`` mechanism so
        a non-compiling snippet fast-fails with real diagnostics; a
        compiled snippet's completion poll is installed post-reload by the
        startup resumer.
        """
        source = _read(BRIDGE)
        for handler in ("HandleRunScript", "HandleRunScriptSubmit"):
            with self.subTest(handler=handler):
                body = _extract_method(source, handler)
                self.assertIn(
                    "ScheduleCompileBarrier", body,
                    msg=(
                        f"{handler} must route compilation through "
                        "ScheduleCompileBarrier so a snippet compile failure "
                        "is detected and reported with real diagnostics."
                    ),
                )

    def test_assembly_mtime_machinery_absent_from_bridge_source(self) -> None:
        """Issue #64 — once the run-script poll stops reading assembly
        modification time, its sole consumer is gone, so the mtime
        helper, the compiled-assembly path constant, and the persisted
        mtime field are removed (no-dead-code rule).
        """
        source = _read(BRIDGE)
        still_present = [
            ident
            for ident in (
                "callTimeAssemblyMtimeUnixMs",
                "ReadAssemblyMtimeUnixMs",
                "CompiledAssemblyRelPath",
            )
            if ident in source
        ]
        self.assertEqual(
            [],
            still_present,
            msg=(
                "the dead assembly-mtime machinery must be fully removed "
                f"from the bridge source; still present: {still_present}."
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
        # coverage now lives in ``tests/csharp/GameObjectPropertyAllowlistTests.cs``.
        # The handler must route through the relocated allowlist.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleEditorSetProperty")
        self.assertIn("GameObjectPropertyAllowlist.IsAllowed", body)

    def test_allowlist_names_pinned_on_allowlist_class(self) -> None:
        # Constant-value pin on the relocated allowlist membership.
        source = _strip_cs_comments(INPUT_VALIDATORS.read_text(encoding="utf-8"))
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
    ``tests/csharp/SuggestionRankerTests.cs``. This source-text test
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
    source = _strip_cs_comments(EDITOR_CONTROL_REQUEST.read_text(encoding="utf-8"))
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
    source = _strip_cs_comments(ACTION_REGISTRY.read_text(encoding="utf-8"))
    match = re.search(
        rf"\b{re.escape(field)}\s*=\s*new\s+HashSet<string>\s*\{{",
        source,
    )
    if match is None:
        raise AssertionError(f"ActionRegistry.{field} HashSet initialiser not found")
    return _extract_braced_block(
        source, match.end(), f"ActionRegistry.{field} HashSet initialiser"
    )


class TestFireAndReturnRecompileRemovedFromRequestDto(unittest.TestCase):
    """Issue #71: the retired fire-and-return recompile tool's
    caller-supplied ``reimport_paths`` field is absent from the request
    DTO, and the request DTO instead declares the compile-awareness
    field consumed by the compile-aware ``editor_refresh``."""

    def test_reimport_paths_field_is_absent(self) -> None:
        body = _extract_editor_control_request_body()
        self.assertNotIn(
            "reimport_paths",
            body,
            msg=(
                "#71: the retired fire-and-return recompile surface's "
                "reimport_paths request field must be gone."
            ),
        )

    def test_request_declares_wait_for_compile_field(self) -> None:
        body = _extract_editor_control_request_body()
        self.assertIn(
            "public bool wait_for_compile",
            body,
            msg=(
                "#70: the request DTO must declare the wait_for_compile "
                "compile-awareness field."
            ),
        )


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
        source = _strip_cs_comments(CONSOLE_REQUEST_VALIDATOR.read_text(encoding="utf-8"))
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

    def test_recompile_and_wait_handler_routes_through_compile_barrier(self) -> None:
        # Issue #68: the pre-reload compile observation is owned by the
        # shared ``ScheduleCompileBarrier`` mechanism. The handler hands
        # the barrier the compile trigger (``RequestScriptCompilation``)
        # and the per-outcome terminal actions.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("ScheduleCompileBarrier", body)
        self.assertIn("RequestScriptCompilation", body)

    def test_recompile_and_wait_handler_supplies_three_outcome_actions(self) -> None:
        # Issue #68 / #203: the handler supplies the per-outcome terminal
        # actions — compile-failed, no-assembly-compiled, compiled. The
        # no-op action delegates to ``WriteRecompileNoOpResponse`` and the
        # compiled action registers the post-reload reload-wait poll.
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleRecompileAndWait")
        self.assertIn("onCompileFailed", body)
        self.assertIn("onNoAssemblyCompiled", body)
        self.assertIn("onCompiled", body)
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
        source = _strip_cs_comments(RUN_SCRIPT_COMPILE_VALIDATORS.read_text(encoding="utf-8"))
        self.assertRegex(source, r"MaxTimeoutSec\s*=\s*1800f")

    def test_out_of_range_code_pinned_on_validator(self) -> None:
        source = _strip_cs_comments(RUN_SCRIPT_COMPILE_VALIDATORS.read_text(encoding="utf-8"))
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
        # Issue #203 / #69: the post-reload poll body observes only the
        # reload counter and the deadline, invokes the caller-supplied
        # reload-complete action, and references neither the mtime helper
        # nor any handler-specific terminal envelope (the recompile OK
        # envelope moved into BuildRecompileAndWaitReloadComplete).
        source = _read(BRIDGE)
        body = _extract_method(source, "BuildRecompileReloadWaitPoll")
        self.assertIn("AssemblyReloadCount", body)
        self.assertIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", body)
        self.assertIn("onReloadComplete", body)
        self.assertNotIn("ReadAssemblyMtimeUnixMs", body)
        self.assertNotIn("EDITOR_CTRL_RECOMPILE_AND_WAIT_OK", body)

    def test_resumer_uses_minus_one_reload_count_threshold(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "ResumePendingAsyncRunners")
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
        if branch_match is None:
            self.fail("ResumePendingAsyncRunners editor_recompile_and_wait branch not found")
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
        if call_match is None:
            self.fail("Resumer branch must call BuildRecompileReloadWaitPoll")
        args = [a.strip() for a in call_match.group(1).split(",")]
        self.assertEqual(
            [
                "entry.responsePath",
                "entry.callTimeUnixMs",
                "entry.deadlineUnixMs",
                "-1",
                '"editor_recompile_and_wait"',
                '"editor_recompile_and_wait: timed out after domain reload."',
                "BuildRecompileAndWaitReloadComplete(entry.responsePath)",
            ],
            args,
            "BuildRecompileReloadWaitPoll call shape changed unexpectedly",
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
        source = _strip_cs_comments(UI_ELEMENT_ALLOWLIST.read_text(encoding="utf-8"))
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

    def test_menu_barrier_compiled_path_uses_reload_wait_poll(self) -> None:
        """Issue #69: when the menu-execute barrier observes a compile,
        it must hand the post-reload wait to the single named
        ``BuildRecompileReloadWaitPoll`` builder with the menu-specific
        ``BuildMenuExecuteReloadComplete`` terminal action — not an
        inline reload-counter loop. A revert to an inline poll would
        re-duplicate the reload-wait logic #69 collapsed.
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "ScheduleMenuExecuteBarrier")
        self.assertIn("BuildRecompileReloadWaitPoll", body)
        self.assertIn("BuildMenuExecuteReloadComplete", body)


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


def _read_serialized_property_partials() -> str:
    filenames = [
        "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.cs",
        "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.ObjectReference.cs",
        "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.Payload.cs",
        "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.Target.cs",
        "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.Traversal.cs",
        "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.Write.cs",
    ]
    present = set(_bridge_partial_filenames())
    missing = [name for name in filenames if name not in present]
    if missing:
        raise AssertionError(
            f"SerializedProperty bridge partial family is missing: {missing!r}"
        )
    return "\n".join(_read(TOOLS_DIR / name) for name in filenames)


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
    # absent from disk so the AGENTS.md inventory and the actual file
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
                text = _strip_cs_comments((TOOLS_DIR / name).read_text(encoding="utf-8"))
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
                text = _strip_cs_comments((TOOLS_DIR / name).read_text(encoding="utf-8"))
                self.assertNotRegex(
                    text,
                    r"public\s+static\s+class\s+UnityEditorControlBridge\b",
                    f"{name}: must use partial class, not plain class",
                )

    def test_deleted_partials_are_absent(self) -> None:
        """The legacy oversized partials must be gone from disk so the
        AGENTS.md inventory and the live file set match.
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
    """Issue #138 — the project's operational rules file (``AGENTS.md``)
    must list every present per-concern partial and list no absent
    partial in its partial-inventory line. The inventory line is the
    single source of truth on disk for the partial layout.
    """

    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _AGENTS_MD = _PROJECT_ROOT / "AGENTS.md"
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
        text = self._AGENTS_MD.read_text(encoding="utf-8")
        for concern in sorted(self._disk_partial_concerns()):
            with self.subTest(concern=concern):
                self.assertIn(
                    concern,
                    text,
                    f"AGENTS.md inventory line is missing concern '{concern}'.",
                )

    def test_inventory_line_lists_no_absent_partial(self) -> None:
        """The legacy partial concern names that issue #138 removed must
        not appear in AGENTS.md, otherwise the inventory advertises files
        that no longer exist on disk."""
        text = self._AGENTS_MD.read_text(encoding="utf-8")
        for absent in ("HierarchyComponents", "UdonSharp.cs"):
            with self.subTest(absent=absent):
                # ``UdonSharp`` alone is a substring of UdonSharp* names,
                # so we anchor on the trailing ``.cs`` for that one.
                self.assertNotIn(
                    absent,
                    text,
                    f"AGENTS.md still references the deleted partial '{absent}'.",
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
        # Issue #51: ``RunFromPaths`` wraps the action switch in an
        # exception boundary and delegates the switch to ``DispatchAction``,
        # which assigns ``response = HandleX(...)``.  Each new action must
        # route to its named handler in that switch.
        source = _read(BRIDGE)
        body = _extract_method(source, "DispatchAction")
        for action, handler in (
            ("editor_add_udonsharp_component", "HandleAddUdonSharpComponent"),
            ("editor_set_udonsharp_field", "HandleSetUdonSharpField"),
            ("editor_wire_persistent_listener", "HandleWirePersistentListener"),
        ):
            with self.subTest(action=action):
                self.assertIn(f'"{action}"', body)
                self.assertIn(handler, body)


class EditorControlBridgeDeleteSourceTests(unittest.TestCase):
    """Issue #114 delete_assets bridge invariants that cannot run without Unity."""

    def test_delete_assets_handler_rejects_malformed_payload_before_assetdatabase_call(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleDeleteAssets")
        parse_failure = body.find("DELETE_ASSETS_BAD_PAYLOAD")
        delete_call = body.find("AssetDatabase.DeleteAssets")
        self.assertNotEqual(-1, parse_failure, msg="delete_assets bad payload code missing")
        self.assertNotEqual(-1, delete_call, msg="AssetDatabase.DeleteAssets call missing")
        self.assertLess(
            parse_failure,
            delete_call,
            msg="malformed delete payload must be rejected before AssetDatabase.DeleteAssets",
        )

    def test_delete_assets_handler_rejects_non_assets_paths_before_assetdatabase_call(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleDeleteAssets")
        path_rejection = body.find("DELETE_ASSETS_UNSUPPORTED_PATH")
        delete_call = body.find("AssetDatabase.DeleteAssets")
        self.assertNotEqual(-1, path_rejection, msg="delete_assets path rejection code missing")
        self.assertNotEqual(-1, delete_call, msg="AssetDatabase.DeleteAssets call missing")
        self.assertLess(
            path_rejection,
            delete_call,
            msg="unsupported delete paths must be rejected before AssetDatabase.DeleteAssets",
        )

    def test_delete_assets_handler_uses_assetdatabase_without_filesystem_delete(self) -> None:
        source = _read(TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.AssetDelete.cs")
        self.assertIn("AssetDatabase.DeleteAssets", source)
        for forbidden in ("File.Delete", "Directory.Delete", "System.IO.File", "System.IO.Directory"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_delete_assets_dispatcher_routes_to_handler(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "DispatchAction")
        self.assertIn('"delete_assets"', body)
        self.assertIn("HandleDeleteAssets", body)

    def test_editor_control_request_carries_delete_batch_payload(self) -> None:
        source = _read(TOOLS_DIR / "PrefabSentinel.Dispatch.EditorControlRequest.cs")
        self.assertIn("asset_paths_json", source)


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

    def test_handler_consumes_unified_writer_under_its_envelope_code(self) -> None:
        """Issue #24 — the UdonSharp field-write handler applies values
        through the unified ``WritePropertyValue`` layer but maps a
        non-success outcome onto its own documented
        ``EDITOR_CTRL_UDON_SET_FIELD_FAILED`` envelope code rather than
        surfacing the writer's error code (Non-Goal: the UdonSharp
        field-write envelope code is unchanged).
        """
        source = _read(BRIDGE)
        body = _extract_method(source, "HandleSetUdonSharpField")
        self.assertIn(
            "WritePropertyValue",
            body,
            msg=(
                "HandleSetUdonSharpField must apply values through the "
                "unified WritePropertyValue layer (issue #24)."
            ),
        )
        self.assertIn(
            "EDITOR_CTRL_UDON_SET_FIELD_FAILED",
            body,
            msg=(
                "A failed unified-writer outcome must surface under the "
                "UdonSharp field-write envelope code, not the writer's "
                "own error code (issue #24 Non-Goal)."
            ),
        )


class TestUdonSharpArrayWriterSource(unittest.TestCase):
    def _array_writer_source(self) -> str:
        return _read(TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.UdonSharpArrayWrite.cs")

    def test_array_writer_emits_typed_error_partitions(self) -> None:
        source = self._array_writer_source()
        for code in (
            "EDITOR_CTRL_UDON_SET_FIELD_NON_ARRAY_VALUES",
            "EDITOR_CTRL_UDON_SET_FIELD_VALUES_JSON_PARSE",
            "EDITOR_CTRL_UDON_SET_FIELD_ARRAY_LENGTH_MISMATCH",
            "EDITOR_CTRL_UDON_SET_FIELD_UNSUPPORTED_ARRAY_TYPE",
            "EDITOR_CTRL_UDON_SET_FIELD_ARRAY_ELEMENT_PARSE",
        ):
            with self.subTest(code=code):
                self.assertIn(code, source)

    def test_array_writer_resolves_supported_element_type_from_field_info(self) -> None:
        source = self._array_writer_source()
        body = _extract_method(source, "WriteUdonSharpArrayValue")
        for token in (
            "fieldInfo.FieldType.IsArray",
            "fieldInfo.FieldType.GetElementType()",
            "!IsSupportedUdonArrayElementType(elementType)",
            "prop.arraySize = elements.Count",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertNotIn("&& elements.Count == 0", body)

    def test_array_element_parse_error_reports_structured_index_context(self) -> None:
        source = self._array_writer_source()
        body = _extract_method(source, "ArrayElementParseError")
        for token in (
            "field_name = fieldName",
            "element_index = index",
            "expected_type = expectedType",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_array_writer_handles_supported_element_partitions(self) -> None:
        source = self._array_writer_source()
        body = _extract_method(source, "WriteUdonSharpArrayElement")
        for token in (
            "VRCUrl",
            "SerializedPropertyType.String",
            "SerializedPropertyType.Integer",
            "SerializedPropertyType.Float",
            "SerializedPropertyType.Boolean",
            "values_json[{index}]",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_array_writer_supports_object_reference_elements(self) -> None:
        source = self._array_writer_source()
        support_body = _extract_method(source, "IsSupportedUdonArrayElementType")
        element_body = _extract_method(source, "WriteUdonSharpArrayElement")
        self.assertIn("typeof(UnityEngine.Object).IsAssignableFrom(elementType)", support_body)
        for token in (
            "ResolveObjectReference(element.Value)",
            "elementType.IsAssignableFrom(obj.GetType())",
            "item.objectReferenceValue = obj",
        ):
            with self.subTest(token=token):
                self.assertIn(token, element_body)


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
        # Target identity, method name, and the string argument.
        self.assertIn("target_path", body)
        self.assertIn("method", body)
        self.assertIn("arg", body)

    def test_event_field_named_event_property_name_in_dto_and_handler(self) -> None:
        """Issue #61 — the persistent-listener event wire field is named
        ``event_property_name`` (the value is a component field name, so
        the former ``_path`` suffix was misleading); the old name is
        absent from both the DTO and the handler.
        """
        dto = _extract_editor_control_request_body()
        handler = _extract_method(_read(BRIDGE), "HandleWirePersistentListener")
        self.assertEqual(
            (True, False, True, False),
            (
                "event_property_name" in dto,
                "event_path" in dto,
                "event_property_name" in handler,
                "event_path" in handler,
            ),
            msg=(
                "the persistent-listener event field must be named "
                "event_property_name in both the EditorControlRequest "
                "DTO and HandleWirePersistentListener; event_path must "
                "be absent from both."
            ),
        )

    def test_request_carries_fields_json_for_add_udonsharp(self) -> None:
        body = _extract_editor_control_request_body()
        self.assertIn("fields_json", body)

    def test_request_carries_array_fields_for_set_udonsharp(self) -> None:
        body = _extract_editor_control_request_body()
        self.assertIn("values_json", body)
        self.assertIn("values_json_present", body)
        self.assertIn("expected_length", body)


def _extract_editor_bridge_ongui() -> str:
    """Return the comment-stripped body of ``EditorBridgeWindow.OnGUI()``.

    ``OnGUI`` is an instance method (``private void``), so the
    ``static``-anchored ``_extract_method`` extractor does not apply; this
    helper locates the signature and brace-extracts the body directly.
    """
    source = _strip_cs_comments(EDITOR_BRIDGE.read_text(encoding="utf-8"))
    match = re.search(r"private\s+void\s+OnGUI\s*\(\s*\)\s*\{", source)
    if match is None:
        raise AssertionError("OnGUI not found in EditorBridge source")
    return _extract_braced_block(source, match.end(), "OnGUI body")


class TestEditorBridgeWindowVersionLine(unittest.TestCase):
    """Issue #65 — the Editor Bridge window shows the running bridge
    version beneath the title, sourced from the canonical
    ``UnityEditorControlBridge.BridgeVersion`` constant. No new
    hardcoded version-string literal is introduced.
    """

    def test_ongui_renders_version_from_constant_without_hardcoded_literal(self) -> None:
        body = _extract_editor_bridge_ongui()
        version_literal = re.search(r'"\d+\.\d+(?:\.\d+)?"', body)
        # Tuple value-pin: the version line must reference the canonical
        # constant AND introduce no version-string literal of its own.
        self.assertEqual(
            (True, None),
            (
                "UnityEditorControlBridge.BridgeVersion" in body,
                version_literal.group(0) if version_literal else None,
            ),
            msg=(
                "OnGUI must render the bridge version from the canonical "
                "UnityEditorControlBridge.BridgeVersion constant and must "
                "not hardcode a version-string literal."
            ),
        )


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
                text = _strip_cs_comments(
                    (TOOLS_DIR / file_name).read_text(encoding="utf-8")
                )
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
                text = _strip_cs_comments(
                    (TOOLS_DIR / file_name).read_text(encoding="utf-8")
                )
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
        if not isinstance(manifest, dict):
            self.fail("PrefabSentinel.Editor.asmdef root must be an object")
        references = manifest.get("references")
        if not isinstance(references, list) or not all(
            isinstance(item, str) for item in references
        ):
            self.fail("PrefabSentinel.Editor.asmdef references must be a string list")
        return references

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


class TestCompileBarrierSource(unittest.TestCase):
    """Issue #68 — source-text invariants for the shared compile-watch
    mechanism (``ScheduleCompileBarrier``).

    Tier 3 (spec Tier 3 Justification): the barrier references
    ``CompilationPipeline`` events and runs inside the Unity Editor
    process, which CI and the xUnit harness do not compile. These
    source-scan invariants guard the consolidation against reversion;
    runtime equivalence is verified by the real-Unity recompile / menu /
    run-script / refresh matrix recorded in observations.md.
    """

    def test_barrier_subscribes_to_both_pipeline_events(self) -> None:
        body = _extract_method(_read(BRIDGE), "ScheduleCompileBarrier")
        self.assertIn("CompilationPipeline.assemblyCompilationFinished", body)
        self.assertIn("CompilationPipeline.compilationFinished", body)
        self.assertRegex(
            body,
            r"Action<\s*string\s*,\s*CompilerMessage\[\]\s*>",
            msg=(
                "the per-assembly subscription must use Unity's published "
                "Action<string, CompilerMessage[]> delegate signature"
            ),
        )

    def test_compile_watch_subscription_is_single_sourced(self) -> None:
        # Issue #68 acceptance: the pre-reload compile observation exists
        # exactly once. After consolidation only ScheduleCompileBarrier
        # subscribes to the per-assembly compile-finished event.
        source = _read(BRIDGE)
        subscribe_count = source.count(
            "CompilationPipeline.assemblyCompilationFinished +="
        )
        self.assertEqual(
            1,
            subscribe_count,
            msg=(
                "the per-assembly compile-finished subscription must exist "
                "exactly once (in ScheduleCompileBarrier); a re-duplicated "
                f"copy re-grows the #68 duplication — found {subscribe_count}"
            ),
        )

    def test_compile_aware_handlers_consume_the_barrier(self) -> None:
        source = _read(BRIDGE)
        for handler in (
            "HandleRecompileAndWait",
            "ScheduleMenuExecuteBarrier",
            "HandleRunScript",
            "HandleRunScriptSubmit",
        ):
            with self.subTest(handler=handler):
                body = _extract_method(source, handler)
                self.assertIn(
                    "ScheduleCompileBarrier",
                    body,
                    msg=f"{handler} must consume the shared compile barrier",
                )

    def test_barrier_emits_compiler_message_with_a_single_prefix(self) -> None:
        # Issue #68: CompilerMessage.message already carries the
        # file(line,col): prefix emitted by csc; the aggregation must emit
        # it verbatim, not prepend a second {msg.file}(...) prefix.
        body = _extract_method(_read(BRIDGE), "ScheduleCompileBarrier")
        self.assertIn("compileErrors.Add(msg.message)", body)
        self.assertNotIn(
            "{msg.file}(",
            body,
            msg=(
                "the diagnostic must not be prefixed twice with the "
                "file(line,col): prefix (#68 double-prefix defect)"
            ),
        )

    def test_barrier_routes_outcomes_through_classifier_and_guard(self) -> None:
        body = _extract_method(_read(BRIDGE), "ScheduleCompileBarrier")
        self.assertIn("RecompileOutcomeClassifier.Classify", body)
        self.assertIn("new RecompileResolutionGuard()", body)
        self.assertRegex(
            body,
            r"if\s*\(\s*!\s*resolutionGuard\.TryClaim\(\)\s*\)\s*return\s*;",
            msg=(
                "the barrier must claim single-resolution through the "
                "shared RecompileResolutionGuard so exactly one terminal "
                "outcome resolves per episode"
            ),
        )

    def test_barrier_deadline_watchdog_invokes_injected_action(self) -> None:
        body = _extract_method(_read(BRIDGE), "ScheduleCompileBarrier")
        self.assertIn("RecompileDeadline.HasElapsed", body)
        self.assertIn("onDeadlineExceeded", body)

    def test_barrier_grace_window_resolves_no_compile_outcome(self) -> None:
        # Issue #70: when the no-compile grace window is armed the barrier
        # resolves the no-compile outcome only after the window elapses
        # with the editor not compiling — the isCompiling check keeps a
        # slow-to-start compile from being misread as no-compile.
        body = _extract_method(_read(BRIDGE), "ScheduleCompileBarrier")
        self.assertIn("onNoCompileObserved", body)
        self.assertIn("EditorApplication.isCompiling", body)

    def test_barrier_schedule_failure_is_redacted(self) -> None:
        # Issue #214: a compile trigger that raises routes to the
        # caller-supplied schedule-failure action; the exception text is
        # mirrored to the Unity console only and the barrier itself writes
        # no MCP envelope.
        body = _extract_method(_read(BRIDGE), "ScheduleCompileBarrier")
        match = re.search(r"catch\s*\(\s*Exception\s+\w+\s*\)\s*\{", body)
        self.assertIsNotNone(
            match, "the barrier must catch a failing compile trigger"
        )
        if match is None:
            self.fail("the barrier must catch a failing compile trigger")
        catch_body = _extract_braced_block(
            body, match.end(), "ScheduleCompileBarrier schedule-failure catch"
        )
        self.assertIn("Debug.LogWarning", catch_body)
        self.assertIn("onScheduleFailure", catch_body)
        self.assertNotRegex(
            catch_body,
            r"\bWriteResponse\b",
            msg=(
                "the barrier must not write the MCP envelope itself — the "
                "caller's onScheduleFailure action owns the redacted envelope"
            ),
        )

    def test_recompile_schedule_failure_action_uses_dedicated_code(self) -> None:
        # Issue #204 / #214: the recompile-and-wait schedule-failure action
        # writes the dedicated EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED code
        # and the fixed redacted message — no exception text, no timeout
        # code cross-emission.
        body = _extract_method(_read(BRIDGE), "HandleRecompileAndWait")
        match = re.search(r"onScheduleFailure\s*=\s*\(\s*\)\s*=>\s*\{", body)
        self.assertIsNotNone(
            match, "HandleRecompileAndWait must supply an onScheduleFailure action"
        )
        if match is None:
            self.fail("HandleRecompileAndWait must supply an onScheduleFailure action")
        action = _extract_braced_block(
            body, match.end(), "recompile onScheduleFailure action"
        )
        self.assertIn("EDITOR_CTRL_RECOMPILE_SCHEDULE_FAILED", action)
        self.assertIn("ScheduleFailureEnvelope.RedactedMessage()", action)
        self.assertNotIn("EDITOR_CTRL_RECOMPILE_TIMEOUT", action)
        self.assertNotRegex(action, r"\.Message\b")
        redaction = _strip_cs_comments(
            RUN_SCRIPT_COMPILE_REDACTION.read_text(encoding="utf-8")
        )
        self.assertIn(
            "editor_recompile_and_wait: failed to schedule compilation.",
            redaction,
        )


class BridgeBackgroundCompileDeferralSourceTests(unittest.TestCase):
    """Issue #72 source invariants for background compile deferral.

    Tier 3: the affected bridge partials depend on UnityEditor APIs, so
    the Unity-free classifier has xUnit coverage and these checks pin the
    Unity-dependent payload/wiring structure until live Editor validation.
    """

    def test_editor_control_data_declares_deferred_payload_fields(self) -> None:
        body = _extract_editor_control_data_body(_read(BRIDGE))
        expected_fields = (
            "public string operation = string.Empty;",
            "public bool editor_focused = false;",
            "public string deferred_reason = string.Empty;",
            "public float budget_sec = 0f;",
            "public bool job_retained = false;",
            "public bool cleanup_performed = false;",
        )
        for field in expected_fields:
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    body,
                    msg=f"EditorControlData must expose deferred payload field {field}",
                )

    def test_common_builder_emits_warning_deferred_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "BuildCompileDeferredBackgroundResponse")
        self.assertIn(
            'BackgroundCompileDeferredReason = "editor_background_compile_reload"',
            source,
        )
        for expected in (
            'code = "EDITOR_COMPILE_DEFERRED_BACKGROUND"',
            'severity = "warning"',
            "success = false",
            "operation = operation",
            "editor_focused = false",
            "deferred_reason = BackgroundCompileDeferredReason",
            "elapsed_sec = elapsedSec",
            "budget_sec = budgetSec",
            "diagnostic_compiling = compiling",
            "job_retained = jobRetained",
            "cleanup_performed = cleanupPerformed",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, body)

    def test_compile_timeout_paths_observe_focus_before_deferred_response(self) -> None:
        source = _read(BRIDGE)
        for method in (
            "BuildRecompileReloadWaitPoll",
            "HandleRefreshAssetDatabase",
            "HandleRecompileAndWait",
            "HandleRunScript",
            "RunScriptPollFrame",
            "HandleRunScriptPoll",
            "ScheduleMenuExecuteBarrier",
        ):
            with self.subTest(method=method):
                body = _extract_method(source, method)
                focus_index = body.find("ObserveEditorFocused()")
                deferred_index = body.find("BuildCompileDeferredBackgroundResponse")
                self.assertNotEqual(
                    -1,
                    focus_index,
                    msg=f"{method} must observe editor focus at its deadline branch",
                )
                self.assertNotEqual(
                    -1,
                    deferred_index,
                    msg=f"{method} must call the common deferred response builder",
                )
                self.assertLess(
                    focus_index,
                    deferred_index,
                    msg=f"{method} must observe focus before building deferred response",
                )

    def test_generic_timeout_branches_remain_available(self) -> None:
        source = _read(BRIDGE)
        expected_timeout_markers = {
            "BuildRecompileReloadWaitPoll": "EDITOR_CTRL_RECOMPILE_TIMEOUT",
            "HandleRefreshAssetDatabase": "EDITOR_CTRL_REFRESH_COMPILE_TIMEOUT",
            "HandleRecompileAndWait": "EDITOR_CTRL_RECOMPILE_TIMEOUT",
            "HandleRunScript": "RunScriptCompilePendingResponse",
            "RunScriptPollFrame": "RunScriptCompilePendingResponse",
            "HandleRunScriptPoll": "EDITOR_RUN_SCRIPT_SUBMIT_TIMEOUT",
            "ScheduleMenuExecuteBarrier": "EDITOR_CTRL_RECOMPILE_TIMEOUT",
        }
        for method, marker in expected_timeout_markers.items():
            with self.subTest(method=method):
                body = _extract_method(source, method)
                self.assertIn(
                    marker,
                    body,
                    msg=f"{method} must retain the focused/unknown generic timeout path",
                )

    def test_run_script_poll_deferred_cleanup_retains_job_before_cleanup(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScriptPoll")
        match = re.search(r"if\s*\(\s*request\.cleanup_on_timeout\s*\)\s*\{", body)
        self.assertIsNotNone(
            match,
            "HandleRunScriptPoll must keep the cleanup_on_timeout branch",
        )
        if match is None:
            self.fail("HandleRunScriptPoll must keep the cleanup_on_timeout branch")
        cleanup_body = _extract_braced_block(
            body,
            match.end(),
            "HandleRunScriptPoll cleanup_on_timeout branch",
        )
        deferred_index = cleanup_body.find("BuildCompileDeferredBackgroundResponse")
        complete_index = cleanup_body.find("PendingAsyncRunner.Complete(completionFile)")
        self.assertNotEqual(
            -1,
            deferred_index,
            msg="background-deferred poll cleanup must return a deferred envelope",
        )
        self.assertNotEqual(
            -1,
            complete_index,
            msg="non-deferred poll cleanup must still complete the stale job",
        )
        self.assertLess(
            deferred_index,
            complete_index,
            msg="deferred poll cleanup must return before completing or deleting the job",
        )
        self.assertIn("jobRetained: true", cleanup_body)
        self.assertIn("cleanupPerformed: false", cleanup_body)

    def test_run_script_poll_deferred_cleanup_response_stays_pollable(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScriptPoll")
        match = re.search(r"if\s*\(\s*request\.cleanup_on_timeout\s*\)\s*\{", body)
        self.assertIsNotNone(
            match,
            "HandleRunScriptPoll must keep the cleanup_on_timeout branch",
        )
        if match is None:
            self.fail("HandleRunScriptPoll must keep the cleanup_on_timeout branch")
        cleanup_body = _extract_braced_block(
            body,
            match.end(),
            "HandleRunScriptPoll cleanup_on_timeout branch",
        )
        for expected in (
            "PendingAsyncRunner.TryGet(completionFile, out var entry)",
            "DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()",
            "entry.callTimeUnixMs",
            "entry.deadlineUnixMs",
            "elapsedSec: elapsedSec",
            "budgetSec: budgetSec",
            "deferred.data.request_id = request.request_id",
            'deferred.data.status = "pending"',
            "return deferred;",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, cleanup_body)
        self.assertNotIn("elapsedSec: 0f", cleanup_body)
        self.assertNotIn("budgetSec: 0f", cleanup_body)


    def test_run_script_poll_deferred_marker_does_not_redefer_after_focus_return(self) -> None:
        source = _read(BRIDGE)
        pending_body = _extract_class_body(source, "PendingAsyncRunner")
        submit_body = _extract_method(source, "HandleRunScriptSubmit")
        poll_body = _extract_method(source, "HandleRunScriptPoll")
        match = re.search(r"if\s*\(\s*request\.cleanup_on_timeout\s*\)\s*\{", poll_body)
        self.assertIsNotNone(
            match,
            "HandleRunScriptPoll must keep the cleanup_on_timeout branch",
        )
        if match is None:
            self.fail("HandleRunScriptPoll must keep the cleanup_on_timeout branch")
        cleanup_body = _extract_braced_block(
            poll_body,
            match.end(),
            "HandleRunScriptPoll cleanup_on_timeout branch",
        )

        for expected in (
            "public bool deferredCompileBackground;",
            "internal static void MarkBackgroundDeferred(",
            "internal static bool IsBackgroundDeferred(",
            "internal static void ClearBackgroundDeferred(",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, pending_body)
        self.assertIn(
            "PendingAsyncRunner.MarkBackgroundDeferred(completionFile)",
            submit_body,
        )
        self.assertIn(
            "bool backgroundDeferredBefore = PendingAsyncRunner.IsBackgroundDeferred(completionFile);",
            cleanup_body,
        )
        self.assertIn(
            "bool backgroundDeferredNow = BackgroundCompileDeferralClassifier.Classify(",
            cleanup_body,
        )
        foreground_index = cleanup_body.find(
            "if (backgroundDeferredBefore && editorFocused == true)"
        )
        deferred_index = cleanup_body.find(
            "if (backgroundDeferredNow || backgroundDeferredBefore)"
        )
        complete_index = cleanup_body.find("PendingAsyncRunner.Complete(completionFile)")
        self.assertNotEqual(-1, foreground_index)
        self.assertNotEqual(-1, deferred_index)
        self.assertNotEqual(-1, complete_index)
        self.assertLess(
            foreground_index,
            deferred_index,
            msg="foreground focus must stop the stale marker from re-deferring",
        )
        self.assertLess(
            deferred_index,
            complete_index,
            msg="continued background deferral must still return before cleanup",
        )

    def test_run_script_poll_frame_preserves_rehydrated_submit_deferred_marker(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptPollFrame")
        match = re.search(r"if\s*\(\s*nowMs\s*>\s*entry\.deadlineUnixMs\s*\)\s*\{", body)
        self.assertIsNotNone(
            match,
            "RunScriptPollFrame must keep a deadline branch",
        )
        if match is None:
            self.fail("RunScriptPollFrame must keep a deadline branch")
        deadline_body = _extract_braced_block(
            body,
            match.end(),
            "RunScriptPollFrame deadline branch",
        )

        marker_check_index = deadline_body.find(
            "bool backgroundDeferredBefore = PendingAsyncRunner.IsBackgroundDeferred(responsePath);"
        )
        classifier_index = deadline_body.find(
            "bool backgroundDeferredNow = BackgroundCompileDeferralClassifier.Classify("
        )
        foreground_guard_index = deadline_body.find(
            'entry.action == "run_script_submit"\n'
            "                    && backgroundDeferredBefore\n"
            "                    && editorFocused == true"
        )
        clear_index = deadline_body.find(
            "PendingAsyncRunner.ClearBackgroundDeferred(responsePath)"
        )
        submit_guard_index = deadline_body.find(
            'else if (entry.action == "run_script_submit"\n'
            "                    && (backgroundDeferredNow || backgroundDeferredBefore)"
        )
        background_timeout_index = deadline_body.find("else if (backgroundDeferredNow)")
        mark_index = deadline_body.find(
            "PendingAsyncRunner.MarkBackgroundDeferred(responsePath)"
        )
        retained_return_index = deadline_body.find("return;", mark_index)
        complete_index = deadline_body.find("PendingAsyncRunner.Complete(responsePath)")
        cleanup_index = deadline_body.find("CleanupRunScriptTempFiles(scriptAbs, metaAbs)")
        self.assertNotEqual(-1, marker_check_index)
        self.assertNotEqual(-1, classifier_index)
        self.assertNotEqual(-1, foreground_guard_index)
        self.assertNotEqual(-1, clear_index)
        self.assertNotEqual(-1, submit_guard_index)
        self.assertNotEqual(-1, background_timeout_index)
        self.assertNotEqual(-1, mark_index)
        self.assertNotEqual(-1, retained_return_index)
        self.assertNotEqual(-1, complete_index)
        self.assertNotEqual(-1, cleanup_index)
        self.assertLess(marker_check_index, foreground_guard_index)
        self.assertLess(classifier_index, foreground_guard_index)
        self.assertLess(foreground_guard_index, submit_guard_index)
        self.assertLess(clear_index, submit_guard_index)
        self.assertLess(submit_guard_index, retained_return_index)
        self.assertLess(retained_return_index, complete_index)
        self.assertLess(retained_return_index, cleanup_index)
        self.assertLess(background_timeout_index, complete_index)

    def test_run_script_poll_preserves_deferred_completion_payload(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScriptPoll")
        for expected in (
            "inner.data.operation",
            "inner.data.editor_focused",
            "inner.data.deferred_reason",
            "inner.data.elapsed_sec",
            "inner.data.budget_sec",
            "inner.data.diagnostic_compiling",
            "inner.data.job_retained",
            "inner.data.cleanup_performed",
        ):
            with self.subTest(expected=expected):
                self.assertIn(
                    expected,
                    body,
                    msg=(
                        "HandleRunScriptPoll must preserve deferred completion "
                        f"payload field {expected} from the inner envelope"
                    ),
                )

    def test_run_script_submit_background_deadline_leaves_job_for_poll(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScriptSubmit")
        match = re.search(r"Action\s+writeCompileDeadline\s*=\s*\(\s*\)\s*=>\s*\{", body)
        self.assertIsNotNone(
            match,
            "HandleRunScriptSubmit must keep a dedicated deadline action",
        )
        if match is None:
            self.fail("HandleRunScriptSubmit must keep a dedicated deadline action")
        deadline_body = _extract_braced_block(
            body,
            match.end(),
            "HandleRunScriptSubmit writeCompileDeadline action",
        )
        focus_index = deadline_body.find("ObserveEditorFocused()")
        classifier_index = deadline_body.find(
            "BackgroundCompileDeferralClassifier.Classify"
        )
        retained_return_index = deadline_body.find("return;", classifier_index)
        pending_index = deadline_body.find("writeCompilePending()")
        self.assertNotEqual(-1, focus_index)
        self.assertNotEqual(-1, classifier_index)
        self.assertNotEqual(
            -1,
            retained_return_index,
            msg="background-deferred submit deadline must return without cleanup",
        )
        self.assertNotEqual(
            -1,
            pending_index,
            msg="focused/unknown submit deadline must keep the generic timeout path",
        )
        self.assertLess(
            classifier_index,
            retained_return_index,
            msg="submit deadline must classify focus before retaining the job",
        )
        self.assertLess(
            retained_return_index,
            pending_index,
            msg="background-deferred submit deadline must skip writeCompilePending cleanup",
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
    """Issue #239: console logs keep the canonical phase precedence."""

    def test_delegates_phase_classification(self) -> None:
        text = _read(BRIDGE)
        log_body = _extract_method(text, "OnLogMessage")
        refresh_body = _extract_method(text, "ClassifyCurrentEditorPhase")
        self.assertIn("_phaseSnapshot", log_body)
        self.assertIn("ConsoleLogPhaseClassifier.Classify", refresh_body)
        self.assertIn("BuildPipeline.isBuildingPlayer", refresh_body)
        self.assertIn("EditorApplication.isPlayingOrWillChangePlaymode", refresh_body)


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

    def test_get_entries_resolves_selector_precedence_before_filters(self) -> None:
        body = _extract_get_entries_body(_read(BRIDGE))
        self.assertIn(
            "bool hasSequenceSelector = sinceSequence >= 0",
            body,
        )
        self.assertIn(
            "bool hasRequestSelector = ConsoleCaptureRequestValidator.UsesRequestIdSelector(",
            body,
        )
        self.assertIn(
            "bool hasCursorSelector = !hasSequenceSelector && !hasRequestSelector && !cursorIsEmpty",
            body,
        )
        self.assertIn(
            "bool hasTimeSelector = !hasSequenceSelector && !hasRequestSelector && !hasCursorSelector && sinceSeconds > 0f",
            body,
        )

    def test_request_id_known_check_uses_active_selector(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCaptureConsoleLogs")
        self.assertIn(
            "ConsoleCaptureRequestValidator.UsesRequestIdSelector(",
            body,
        )
        self.assertIn("bool knownRequestId = !requestIdSelectorActive", body)

    def test_get_entries_reports_buffer_reset_from_retained_lower_bound(self) -> None:
        source = _read(BRIDGE)
        self.assertIn("PeekLowestRetainedSequenceId", source)
        body = _extract_method(source, "HandleCaptureConsoleLogs")
        self.assertIn("EDITOR_CTRL_CONSOLE_BUFFER_RESET", body)
        self.assertIn("ConsoleLogBuffer.PeekLowestRetainedSequenceId()", body)

    def test_run_from_paths_scopes_dispatch_by_derived_transport_request_id(self) -> None:
        source = _read(BRIDGE)
        run_body = _extract_method(source, "RunFromPaths")
        dispatch_body = _extract_method(source, "DispatchAction")
        self.assertIn("DeriveTransportRequestId(requestPath)", run_body)
        self.assertIn("ConsoleLogBuffer.BeginRequest(transportRequestId)", run_body)
        self.assertIn("ConsoleLogBuffer.EndRequest(transportRequestId)", run_body)
        self.assertIn("string transportRequestId", dispatch_body)

    def test_run_script_persists_transport_request_id_for_delayed_logs(self) -> None:
        source = _read(BRIDGE)
        run_body = _extract_method(source, "HandleRunScript")
        poll_body = _extract_method(source, "RunScriptPollFrame")
        entry_body = _extract_class_body(source, "PersistedEntry")
        self.assertIn("public string transportRequestId", entry_body)
        self.assertIn("transportRequestId = transportRequestId", run_body)
        self.assertIn("ConsoleLogBuffer.BeginRequest(entry.transportRequestId)", poll_body)


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


class TestHandleGetEditorStateReadsFiveFlags(unittest.TestCase):
    """Issue #239 / #40 (T-40-4): the editor-state handler reads five
    ``EditorStateSnapshot`` fields, the fifth being the unsaved-changes
    flag.

    Tier 3 (spec.md Tier 3 Justification T-40-4): the editor-state
    handler reads live editor and scene-dirty state inside the Unity
    process and is not xUnit-compiled; this source-scan pins the
    flag-count contract and the live dirty-state read is verified via
    ``deploy_bridge``.
    """

    def test_handler_assigns_five_named_flags(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleGetEditorState")
        # Each of the five documented snapshot fields must be assigned;
        # a missing flag surfaces as a False in this tuple and names the
        # gap.  The first four read editor-API symbols directly; the
        # fifth (issue #40) reads the unsaved-changes helper.
        checks = (
            ("is_playing = EditorApplication.isPlaying" in body),
            (
                "is_will_change_playmode = "
                "EditorApplication.isPlayingOrWillChangePlaymode"
            ) in body,
            ("is_compiling = EditorApplication.isCompiling" in body),
            ("is_building_player = BuildPipeline.isBuildingPlayer" in body),
            ("has_unsaved_changes = HasUnsavedEditorChanges()" in body),
        )
        self.assertEqual(
            (True, True, True, True, True),
            checks,
            msg=(
                "HandleGetEditorState must assign each of the five "
                "documented EditorStateSnapshot fields, the fifth being "
                "has_unsaved_changes — checks="
                f"{checks}"
            ),
        )

    def test_snapshot_class_declares_five_flag_fields(self) -> None:
        body = _read(BRIDGE)
        match = re.search(
            r"class\s+EditorStateSnapshot\s*\{",
            body,
        )
        self.assertIsNotNone(
            match, msg="EditorStateSnapshot class declaration not found"
        )
        if match is None:
            self.fail("EditorStateSnapshot class declaration not found")
        snapshot_body = _extract_braced_block(
            body, match.end(), "EditorStateSnapshot body"
        )
        bool_fields = set(re.findall(r"public\s+bool\s+(\w+)\s*=", snapshot_body))
        expected_flags = {
            "is_playing",
            "is_will_change_playmode",
            "is_compiling",
            "is_building_player",
            "has_unsaved_changes",
        }
        self.assertEqual(
            expected_flags,
            expected_flags & bool_fields,
            msg=(
                "EditorStateSnapshot must carry the five play/compile/dirty "
                f"flags; found {sorted(bool_fields)}"
            ),
        )


class TestHandleGetEditorStateOperatorContextSource(unittest.TestCase):
    def test_snapshot_and_response_declare_operator_identity_and_stage_fields(self) -> None:
        source = _strip_cs_comments(_read(BRIDGE))
        snapshot_body = _extract_class_body(source, "EditorStateSnapshot")
        response_body = _extract_class_body(source, "EditorControlResponse")
        context_body = _extract_class_body(source, "EditorOperatorContext")

        snapshot_checks = {
            "active_stage_kind": "public string active_stage_kind" in snapshot_body,
            "active_scene_path": "public string active_scene_path" in snapshot_body,
            "active_scene_name": "public string active_scene_name" in snapshot_body,
            "prefab_stage_asset_path": "public string prefab_stage_asset_path" in snapshot_body,
            "prefab_stage_root_name": "public string prefab_stage_root_name" in snapshot_body,
            "prefab_stage_is_dirty": "public bool prefab_stage_is_dirty" in snapshot_body,
            "open_scenes": "public EditorSceneStatus[] open_scenes" in snapshot_body,
        }
        context_checks = {
            "response_context": "public EditorOperatorContext operator_context" in response_body,
            "project_root": "public string project_root" in context_body,
            "bridge_session_id": "public string bridge_session_id" in context_body,
            "bridge_instance_id": "public string bridge_instance_id" in context_body,
            "bridge_version": "public string bridge_version" in context_body,
            "plugin_version": "public string plugin_version" in context_body,
        }
        self.assertEqual(
            {key: True for key in snapshot_checks},
            snapshot_checks,
            msg=f"EditorStateSnapshot stage/dirty fields missing: {snapshot_checks}",
        )
        self.assertEqual(
            {key: True for key in context_checks},
            context_checks,
            msg=f"Editor operator context fields missing: {context_checks}",
        )

    def test_get_editor_state_populates_identity_and_does_not_mutate_editor_state(self) -> None:
        source = _strip_cs_comments(_read(BRIDGE))
        body = _extract_method(source, "HandleGetEditorState")
        required_tokens = {
            "operator_context = BuildEditorOperatorContext()": (
                "operator_context = BuildEditorOperatorContext()" in source
            ),
            "PopulateActiveSceneStatus": "PopulateActiveSceneStatus(snapshot, diagnostics)" in body,
            "PopulatePrefabStageStatus": "PopulatePrefabStageStatus(snapshot, diagnostics)" in body,
            "open_scenes": "open_scenes" in body,
            "active_stage_kind": "active_stage_kind" in source,
            "prefab_stage": 'active_stage_kind = "prefab_stage"' in source,
        }
        forbidden_tokens = {
            "EditorSceneManager.Save": "EditorSceneManager.Save" in body,
            "SaveCurrentModifiedScenesIfUserWantsTo": (
                "SaveCurrentModifiedScenesIfUserWantsTo" in body
            ),
            "StageUtility.GoToMainStage": "StageUtility.GoToMainStage" in body,
            "ClearDirtiness": "ClearDirtiness" in body,
        }
        self.assertEqual(
            {key: True for key in required_tokens},
            required_tokens,
            msg=f"HandleGetEditorState missing identity/stage population: {required_tokens}",
        )
        self.assertEqual(
            {key: False for key in forbidden_tokens},
            forbidden_tokens,
            msg=f"HandleGetEditorState must be read-only: {forbidden_tokens}",
        )

    def test_get_editor_state_limited_enumeration_diagnostic_is_successful_warning(self) -> None:
        source = _strip_cs_comments(_read(BRIDGE))
        body = _extract_method(source, "HandleGetEditorState")
        checks = {
            "diagnostic_code": "EDITOR_STATE_ENUMERATION_LIMITED" in source,
            "warning_severity": 'severity = "warning"' in source,
            "success_response": "BuildSuccess(" in body,
            "response_severity_gate": (
                'if (diagnostics.Count > 0) response.severity = "warning";' in body
            ),
            "catch_exception": "catch (Exception" in source,
        }
        self.assertEqual(
            {key: True for key in checks},
            checks,
            msg=f"HandleGetEditorState limited-enumeration diagnostic missing: {checks}",
        )


class TestRunFromPathsExceptionBoundary(unittest.TestCase):
    """Issue #51 (T-51-1): the bridge dispatch encloses its action switch
    in an exception boundary that emits the
    ``EDITOR_CTRL_HANDLER_EXCEPTION`` envelope.

    Tier 3 (spec.md Tier 3 Justification T-51-1): the dispatch boundary
    runs inside the Unity Editor process and is not xUnit-compiled; its
    runtime behavior cannot be executed Python-side.  This source-scan
    pins the structural invariant — the action switch is wrapped by a
    try/catch that yields the typed envelope naming the action with the
    exception redacted to its type name.
    """

    def _run_from_paths_body(self) -> str:
        return _strip_cs_comments(
            _extract_method(_read(BRIDGE), "RunFromPaths")
        )

    def test_action_switch_runs_inside_a_try_catch(self) -> None:
        body = self._run_from_paths_body()
        # The dispatch call must be inside a try block followed by a
        # catch on Exception.
        self.assertRegex(
            body,
            r"try\s*\{[^{}]*DispatchAction\([^;]*;[^{}]*\}\s*"
            r"catch\s*\(\s*Exception\s+\w+\s*\)",
            msg=(
                "RunFromPaths must call the action dispatch inside a "
                "try block guarded by a catch(Exception ...) boundary."
            ),
        )

    def test_boundary_emits_handler_exception_envelope(self) -> None:
        body = self._run_from_paths_body()
        self.assertIn(
            "EDITOR_CTRL_HANDLER_EXCEPTION",
            body,
            msg=(
                "The dispatch exception boundary must emit the "
                "EDITOR_CTRL_HANDLER_EXCEPTION envelope."
            ),
        )

    def test_envelope_names_action_and_redacts_exception_type(self) -> None:
        body = self._run_from_paths_body()
        # The handler-exception catch is the one guarding the
        # ``DispatchAction`` call — not the earlier request-read catch.
        # Anchor the search on the DispatchAction try block.
        catch_match = re.search(
            r"DispatchAction\([^;]*;\s*\}\s*"
            r"catch\s*\(\s*Exception\s+(\w+)\s*\)\s*\{",
            body,
        )
        self.assertIsNotNone(
            catch_match,
            msg="handler-exception catch guarding DispatchAction not found",
        )
        if catch_match is None:
            self.fail("handler-exception catch guarding DispatchAction not found")
        catch_var = catch_match.group(1)
        catch_body = _extract_braced_block(
            body, catch_match.end(), "RunFromPaths handler-exception catch"
        )
        self.assertIn(
            "request.action",
            catch_body,
            msg="the handler-exception envelope must name the action",
        )
        self.assertIn(
            f"{catch_var}.GetType().Name",
            catch_body,
            msg=(
                "the handler-exception envelope must redact the exception "
                "to its type name"
            ),
        )
        # No stack trace nor raw exception message may reach the envelope
        # message; the only permitted exception-derived value is the type
        # name.  The full detail is mirrored to the Unity console.
        self.assertNotRegex(
            catch_body,
            rf"BuildError\([^)]*{catch_var}\.Message",
            msg="the envelope message must not embed the raw exception message",
        )
        self.assertIn(
            "Debug.LogWarning",
            catch_body,
            msg=(
                "the full exception detail must be mirrored to the Unity "
                "console"
            ),
        )


class TestRecompileNoOpImporterWarning(unittest.TestCase):
    """Issue #45 (T-45-2): the synchronous recompile handler's no-op
    branch consults the importer-error predicate over the console buffer
    and downgrades a masked importer failure to a ``warning``-severity
    response.

    Tier 3 (spec.md Tier 3 Justification T-45-2): the synchronous
    recompile handler runs inside the Unity Editor process and is not
    xUnit-compiled; the console buffer it scans is populated by live
    Unity importer events.  The importer-error predicate is Tier
    1-covered (T-45-1, ``ImporterErrorClassifierTests``); this
    source-scan pins the handler wiring.
    """

    def test_noop_branch_consults_importer_error_predicate(self) -> None:
        # Issue #68: the recompile no-op response is built by the
        # dedicated WriteRecompileNoOpResponse helper, which must route
        # the console buffer through the Unity-free importer-error
        # classifier.
        body = _extract_method(_read(BRIDGE), "WriteRecompileNoOpResponse")
        self.assertIn(
            "CollectImporterErrorDiagnostics",
            body,
            msg=(
                "the no-op recompile response must collect importer-error "
                "diagnostics from the console buffer."
            ),
        )

    def test_collector_uses_importer_error_classifier(self) -> None:
        collector = _extract_method(
            _read(BRIDGE), "CollectImporterErrorDiagnostics"
        )
        self.assertIn(
            "ImporterErrorClassifier.IsImporterError",
            collector,
            msg=(
                "the importer-error collector must delegate the line "
                "predicate to the Unity-free ImporterErrorClassifier."
            ),
        )

    def test_collector_supplies_disabled_sequence_and_request_selectors(self) -> None:
        collector = _extract_method(
            _read(BRIDGE), "CollectImporterErrorDiagnostics"
        )
        self.assertIn(
            "\"all\",\n                -1,\n                string.Empty,\n                newestFirst: false",
            collector,
            msg=(
                "the importer-error collector snapshots the full console "
                "buffer, so it must explicitly disable sequence/request "
                "selectors when calling ConsoleLogBuffer.GetEntries."
            ),
        )

    def test_noop_importer_response_carries_warning_severity(self) -> None:
        # When importer errors are present the no-op response must carry
        # warning severity and the detected importer errors as diagnostics
        # rather than reporting a silent success.
        body = _strip_cs_comments(
            _extract_method(_read(BRIDGE), "WriteRecompileNoOpResponse")
        )
        self.assertIn(
            'severity = "warning"',
            body,
            msg=(
                "an importer failure in the no-op response must yield a "
                "warning-severity response, not a silent success."
            ),
        )
        self.assertRegex(
            body,
            r"diagnostics\s*=\s*importerErrors\.ToArray\(\)",
            msg=(
                "the warning response must list the detected importer "
                "errors as diagnostics."
            ),
        )

    def test_noop_importer_response_carries_operator_context(self) -> None:
        body = _extract_method(_read(BRIDGE), "WriteRecompileNoOpResponse")
        self.assertIn(
            "operator_context = BuildEditorOperatorContext()",
            body,
            msg=(
                "The successful no-op warning response must carry operator "
                "context so expected-root verification can accept the response."
            ),
        )


# ---------------------------------------------------------------------------
# Issue #216 — script-runner leak-safe envelope at four catch sites + shared
# payload omits exception-text field.
# ---------------------------------------------------------------------------


# Forbidden tokens that would re-introduce raw exception-text leakage.
# Structured exception summaries are allowed; full exception message and
# ToString payloads remain console-only.
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
    """Issue #216/#93: runtime catches carry no raw exception text."""

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
    """Issue #235 / #69: the recompile-and-wait reload-complete action
    synchronously drains the AssetDatabase import queue before writing
    the success envelope, through the shared ``DrainImportQueueBestEffort``
    helper. A drain failure is mirrored to the Unity Console without
    affecting the envelope outcome (the contract concerns compilation,
    not import completion).
    """

    def test_reload_complete_drains_before_writing_success(self) -> None:
        body = _extract_method(
            _read(BRIDGE), "BuildRecompileAndWaitReloadComplete"
        )
        # End-state ordering: the import-queue drain must come before the
        # success ``WriteResponse`` call so a freshly compiled asset path
        # resolves immediately.
        drain_pos = body.find("DrainImportQueueBestEffort")
        success_pos = body.find("EDITOR_CTRL_RECOMPILE_AND_WAIT_OK")
        self.assertEqual(
            (True, True),
            (
                drain_pos >= 0,
                success_pos > drain_pos,
            ),
            msg=(
                "BuildRecompileAndWaitReloadComplete must drain the import "
                "queue before writing the success envelope — observed "
                f"positions drain={drain_pos}, success={success_pos}."
            ),
        )

    def test_drain_call_uses_synchronous_import_options(self) -> None:
        body = _extract_method(_read(BRIDGE), "DrainImportQueueBestEffort")
        # The drain is required to be synchronous so a freshly compiled
        # asset path resolves on the call immediately following the
        # success envelope on the documented happy path.
        self.assertIn(
            "ImportAssetOptions.ForceSynchronousImport",
            body,
            msg=(
                "DrainImportQueueBestEffort must use "
                "ImportAssetOptions.ForceSynchronousImport so the import "
                "queue is drained synchronously before the success "
                "envelope is written (issue #235)."
            ),
        )

    def test_drain_failure_logged_and_does_not_crash_poll(self) -> None:
        body = _extract_method(_read(BRIDGE), "DrainImportQueueBestEffort")
        # The drain refresh call must be wrapped in a try/catch that
        # mirrors the failure to the Unity Console; an unhandled refresh
        # exception must not turn the success path into a crash.
        try_pos = body.find("try")
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
                "DrainImportQueueBestEffort's drain call must be wrapped "
                "in try/catch with Debug.LogWarning so an unhandled drain "
                "exception cannot crash the success path (issue #235)."
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
        source = _strip_cs_comments(RUN_SCRIPT_COMPILE_VALIDATORS.read_text(encoding="utf-8"))
        self.assertIn(
            'RecoveryCode = "EDITOR_CTRL_RUN_SCRIPT_RECOVERY"', source
        )
        self.assertIn(
            'TimeoutCode = "EDITOR_RUN_SCRIPT_COMPILE_TIMEOUT"', source
        )

    def test_recovery_response_carries_operator_context(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunScriptCompilePendingResponse")
        self.assertIn(
            "operator_context = BuildEditorOperatorContext()",
            body,
            msg=(
                "Manual recovery responses must carry operator context like "
                "the central bridge response builders."
            ),
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
        source = _strip_cs_comments(EDITOR_SCRIPT_PATH_CLASSIFIER.read_text(encoding="utf-8"))
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

    def test_handler_dimension_check_precedes_output_and_texture_allocation(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCaptureScreenshot")
        dimension_pos = body.find("ScreenshotDimensionBounds.Accepts")
        output_dir_pos = body.find("string outputDir = Path.Combine")
        object_dispatch_pos = body.find("HandleObjectCaptureScreenshot(request, outputPath)")
        texture_pos = body.find("new Texture2D")
        render_texture_pos = body.find("RenderTexture.GetTemporary")
        self.assertEqual(
            (True, True, True, True, True, True),
            (
                dimension_pos >= 0,
                output_dir_pos >= 0,
                object_dispatch_pos >= 0,
                texture_pos >= 0,
                render_texture_pos >= 0,
                dimension_pos < output_dir_pos
                and dimension_pos < object_dispatch_pos
                and dimension_pos < texture_pos
                and dimension_pos < render_texture_pos,
            ),
            msg=(
                "Screenshot dimension rejection must run before output path "
                "composition, target dispatch, Texture2D allocation, and "
                "RenderTexture allocation; "
                f"positions: dimension={dimension_pos}, output_dir={output_dir_pos}, "
                f"object_dispatch={object_dispatch_pos}, texture={texture_pos}, "
                f"render_texture={render_texture_pos}."
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


class TestScreenshotCropBoundsSource(unittest.TestCase):
    _SCREENSHOT = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.cs"
    _TARGET_CAPTURE = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.cs"
    )

    def _method_body(self, method_name: str) -> str:
        return _extract_method(_read(self._SCREENSHOT), method_name)

    def _target_method_body(self, method_name: str) -> str:
        self.assertTrue(
            self._TARGET_CAPTURE.exists(),
            msg="Screenshot.TargetCapture partial must exist before reading target capture methods.",
        )
        return _extract_method(_read(self._TARGET_CAPTURE), method_name)

    def test_crop_roi_null_is_rejected_before_empty_no_crop_path(self) -> None:
        resolver_body = self._method_body("TryResolveCropRoi")
        null_guard_index = resolver_body.index("if (value == null)")
        empty_guard_index = resolver_body.index("if (value.Length == 0)")

        self.assertLess(null_guard_index, empty_guard_index)
        self.assertNotIn("string.IsNullOrEmpty(value)", resolver_body)

    def test_object_capture_null_crop_roi_is_rejected_before_no_crop_path(self) -> None:
        body = self._target_method_body("ResolveTargetPixelCrop")
        null_guard_index = body.index("if (request.crop_roi == null)")
        empty_guard_index = body.index("if (request.crop_roi.Length == 0)")

        self.assertLess(null_guard_index, empty_guard_index)
        self.assertIn(
            '"EDITOR_CTRL_CROP_ROI_INVALID"',
            body[null_guard_index:empty_guard_index],
        )

    def test_object_capture_pixel_crop_delegates_before_render_and_read(self) -> None:
        object_body = self._target_method_body("HandleObjectCaptureScreenshot")
        crop_index = object_body.find("ResolveTargetPixelCrop(")
        render_index = object_body.find("RenderSceneViewToTexture")
        read_index = object_body.find("ReadPixels")

        self.assertNotEqual(
            -1,
            crop_index,
            msg="HandleObjectCaptureScreenshot must call ResolveTargetPixelCrop.",
        )
        self.assertNotEqual(
            -1,
            render_index,
            msg="HandleObjectCaptureScreenshot must render through RenderSceneViewToTexture.",
        )
        self.assertNotEqual(
            -1,
            read_index,
            msg="HandleObjectCaptureScreenshot must read pixels after crop validation.",
        )
        self.assertLess(
            crop_index,
            render_index,
            msg=(
                "Expected object-capture crop validation via "
                "ResolveTargetPixelCrop before RenderSceneViewToTexture."
            ),
        )
        self.assertLess(
            crop_index,
            read_index,
            msg=(
                "Expected object-capture crop validation via "
                "ResolveTargetPixelCrop before ReadPixels."
            ),
        )

        resolver_body = self._target_method_body("ResolveTargetPixelCrop")
        self.assertIn(
            "ScreenshotCropBounds.FitsWithinFrame",
            resolver_body,
            msg=(
                "Expected ResolveTargetPixelCrop to reject non-fitting "
                "pixel crops through ScreenshotCropBounds.FitsWithinFrame."
            ),
        )
        self.assertIn(
            '"EDITOR_CTRL_CROP_ROI_OUT_OF_BOUNDS"',
            resolver_body,
            msg=(
                "Expected ResolveTargetPixelCrop to preserve the existing "
                "EDITOR_CTRL_CROP_ROI_OUT_OF_BOUNDS envelope."
            ),
        )

    def test_scene_pixel_crop_delegates_and_has_no_direct_edge_addition(self) -> None:
        body = self._method_body("HandleCaptureScreenshot")
        self.assertIn(
            "ScreenshotCropBounds.FitsWithinFrame",
            body,
            msg=(
                "Expected HandleCaptureScreenshot scene pixel crops to use "
                "ScreenshotCropBounds.FitsWithinFrame."
            ),
        )
        self.assertNotRegex(
            body,
            r"cropBounds\.x\s*\+\s*cropBounds\.w|"
            r"cropBounds\.y\s*\+\s*cropBounds\.h",
            msg=(
                "HandleCaptureScreenshot must not use direct int edge "
                "addition for scene pixel crop bounds."
            ),
        )


class ScreenshotObjectCaptureSourceTests(unittest.TestCase):
    """Issue #84/#87/#90 — bridge-side source-text invariants for the
    target-oriented capture branch.

    The dispatcher still lives in ``HandleCaptureScreenshot``. Target-specific
    capture behavior lives in ``Screenshot.TargetCapture`` so the stage-aware
    resolver, World Space UI branch, renderer framing, and target pixel crop
    behavior can evolve without growing the generic screenshot partial.

    T3 source-text invariant: the bridge runs inside the Unity Editor; the
    Python harness cannot drive the SceneView (justified in the spec's Tier 3
    entries for target capture).
    """

    _TARGET_CAPTURE_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.cs"
    )

    _BRIDGE_CODES = (
        "EDITOR_CTRL_SCREENSHOT_TARGET_NOT_FOUND",
        "EDITOR_CTRL_SCREENSHOT_TARGET_NO_RENDERERS",
        "EDITOR_CTRL_SCREENSHOT_ANGLE_INVALID",
    )

    _DOCS_API_REFERENCE = (
        Path(__file__).resolve().parent.parent / "docs" / "api-reference.md"
    )

    _DOCS_TOOLS = (
        Path(__file__).resolve().parent.parent / "docs" / "tools.md"
    )

    def _screenshot_partial_body(self) -> str:
        self.assertTrue(
            self._TARGET_CAPTURE_PARTIAL.exists(),
            msg="Screenshot.TargetCapture partial must exist for target capture split.",
        )
        return _strip_cs_comments(_read(self._TARGET_CAPTURE_PARTIAL))

    def _object_capture_body(self) -> str:
        return _extract_method(
            self._screenshot_partial_body(),
            "HandleObjectCaptureScreenshot",
        )

    def test_handler_branches_on_request_target(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCaptureScreenshot")
        self.assertIn(
            "request.target",
            body,
            msg=(
                "HandleCaptureScreenshot must dispatch on "
                "``request.target`` to engage the target-oriented "
                "capture branch (#84)."
            ),
        )

    def test_target_capture_partial_is_split_from_screenshot_dispatcher(self) -> None:
        self.assertTrue(
            self._TARGET_CAPTURE_PARTIAL.exists(),
            msg="Screenshot.TargetCapture partial must exist for issue #87 split.",
        )
        screenshot_body = _strip_cs_comments(
            _read(TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.cs")
        )
        dispatch_body = _extract_method(screenshot_body, "HandleCaptureScreenshot")
        self.assertIn(
            "HandleObjectCaptureScreenshot(request, outputPath)",
            dispatch_body,
            msg="Screenshot.cs must keep target dispatch wired to the moved helper.",
        )
        self.assertNotIn(
            "private static EditorControlResponse HandleObjectCaptureScreenshot",
            screenshot_body,
            msg="Target object capture must move out of Screenshot.cs into Screenshot.TargetCapture.cs.",
        )

    def test_handler_routes_target_through_stage_aware_resolver(self) -> None:
        body = self._screenshot_partial_body()
        self.assertIn(
            "TryResolveGameObjectInActiveStage",
            body,
            msg=(
                "The target-capture partial must delegate target resolution to "
                "TryResolveGameObjectInActiveStage so the existing "
                "EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS envelope surfaces unchanged (#84)."
            ),
        )

    def test_handler_invokes_pure_framing_math_helper(self) -> None:
        body = self._screenshot_partial_body()
        self.assertIn(
            "ObjectCaptureFramingMath",
            body,
            msg=(
                "The target-capture partial must invoke the Unity-free "
                "ObjectCaptureFramingMath helper (#84/#90)."
            ),
        )

    def test_handler_uses_shared_renderer_bounds_before_framing_math(self) -> None:
        body = self._object_capture_body()
        helper_index = body.find("TryResolveRendererFramingBounds")
        solver_index = body.find("TrySolveFramingForAabb")
        success_index = body.find("BuildSuccess")
        self.assertNotEqual(
            -1,
            helper_index,
            msg="HandleObjectCaptureScreenshot must call TryResolveRendererFramingBounds.",
        )
        self.assertNotEqual(
            -1,
            solver_index,
            msg="HandleObjectCaptureScreenshot must call TrySolveFramingForAabb.",
        )
        self.assertLess(
            helper_index,
            solver_index,
            msg="Renderer bounds must be resolved before framing math runs.",
        )
        for forbidden in (
            "GetComponentsInChildren<SkinnedMeshRenderer>",
            ".BakeMesh(",
            "SelectFramingRenderers",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    body,
                    msg="HandleObjectCaptureScreenshot must not duplicate the shared renderer-bounds model.",
                )
        for token in ("bounds_center", "bounds_extents"):
            with self.subTest(token=token):
                token_index = body.find(token, helper_index)
                self.assertNotEqual(
                    -1,
                    token_index,
                    msg=f"Successful object capture must report aggregate {token}.",
                )
                self.assertLess(
                    success_index,
                    token_index,
                    msg=f"Aggregate {token} must be populated in the success payload.",
                )

    def test_handler_rejects_invalid_fit_mode_before_renderer_framing_and_render(self) -> None:
        body = self._object_capture_body()
        fit_error_index = body.find("SCREENSHOT_FIT_MODE_INVALID")
        helper_index = body.find("TryResolveRendererFramingBounds")
        render_index = body.find("RenderSceneViewToTexture")
        self.assertNotEqual(
            -1,
            fit_error_index,
            msg="Bridge-side invalid fit_mode must return SCREENSHOT_FIT_MODE_INVALID.",
        )
        self.assertLess(
            fit_error_index,
            helper_index,
            msg="Invalid fit_mode must be rejected before renderer framing.",
        )
        self.assertLess(
            fit_error_index,
            render_index,
            msg="Invalid fit_mode must be rejected before file rendering.",
        )
        for token in ("request.fit_mode", "max_axis", "both_axes"):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_omitted_angle_defaults_after_world_space_ui_routing(self) -> None:
        body = self._object_capture_body()
        self.assertNotIn(
            "string angle = string.IsNullOrEmpty(request.angle)",
            body,
            msg="request.angle must not resolve to the renderer default before UI routing.",
        )
        raw_angle_index = body.find("string angle = request.angle;")
        ui_branch_index = body.find("ShouldUseWorldSpaceUiCapture")
        ui_default_index = body.find("string uiAngle = string.IsNullOrEmpty(angle)", ui_branch_index)
        renderer_default_index = body.find("angle = string.IsNullOrEmpty(angle)", ui_branch_index)
        preset_index = body.find("ObjectCaptureFramingMath.PresetNames")
        for label, index in (
            ("raw angle assignment", raw_angle_index),
            ("UI routing branch", ui_branch_index),
            ("UI omitted-angle default", ui_default_index),
            ("renderer omitted-angle default", renderer_default_index),
            ("renderer preset validation", preset_index),
        ):
            with self.subTest(label=label):
                self.assertNotEqual(-1, index)
        self.assertLess(raw_angle_index, ui_branch_index)
        self.assertLess(ui_branch_index, ui_default_index)
        self.assertLess(ui_default_index, renderer_default_index)
        self.assertLess(renderer_default_index, preset_index)
        self.assertIn('"front"', body[ui_default_index:renderer_default_index])
        self.assertIn('"three_quarter"', body[renderer_default_index:preset_index])

    def test_handler_reports_no_renderers_before_solver_and_output(self) -> None:
        body = self._object_capture_body()
        helper_index = body.find("TryResolveRendererFramingBounds")
        no_renderer_index = body.find("EDITOR_CTRL_SCREENSHOT_TARGET_NO_RENDERERS", helper_index)
        solver_index = body.find("TrySolveFramingForAabb")
        render_index = body.find("RenderSceneViewToTexture")
        self.assertNotEqual(-1, helper_index)
        self.assertNotEqual(
            -1,
            no_renderer_index,
            msg="No renderer contributors must return EDITOR_CTRL_SCREENSHOT_TARGET_NO_RENDERERS.",
        )
        self.assertLess(no_renderer_index, solver_index)
        self.assertLess(no_renderer_index, render_index)

    def test_handler_reports_solver_failures_before_success_output(self) -> None:
        body = self._object_capture_body()
        for token in (
            "TryResolveBothAxesAspectForAabb",
            "ResolveOutputSizeForFitMode",
            "TrySolveFramingForAabb",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        success_index = body.find("BuildSuccess")
        aspect_call_index = body.find("TryResolveBothAxesAspectForAabb")
        aspect_failure_index = body.find("EDITOR_CTRL_SCREENSHOT_FAILED", aspect_call_index)
        framing_call_index = body.find("TrySolveFramingForAabb")
        framing_failure_index = body.find("EDITOR_CTRL_SCREENSHOT_FAILED", framing_call_index)
        self.assertNotEqual(
            -1,
            aspect_failure_index,
            msg="Both-axis aspect failure must return EDITOR_CTRL_SCREENSHOT_FAILED.",
        )
        self.assertNotEqual(
            -1,
            framing_failure_index,
            msg="Framing solver failure must return EDITOR_CTRL_SCREENSHOT_FAILED.",
        )
        self.assertLess(aspect_failure_index, success_index)
        self.assertLess(framing_failure_index, success_index)
        self.assertIn("aspectReason", body)
        self.assertIn("framingReason", body)

    def test_handler_emits_new_bridge_error_codes(self) -> None:
        body = self._screenshot_partial_body()
        for code in self._BRIDGE_CODES:
            with self.subTest(code=code):
                self.assertIn(
                    f'"{code}"', body,
                    msg=(
                        f"The target-capture partial must reference the "
                        f"bridge-side error code literal {code!r} (#84)."
                    ),
                )

    def test_dispatcher_routes_capture_screenshot_unchanged(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "DispatchAction")
        self.assertIn('case "capture_screenshot":', body)
        self.assertIn("HandleCaptureScreenshot", body)

    def test_new_error_codes_documented_in_api_reference(self) -> None:
        docs = self._DOCS_API_REFERENCE.read_text(encoding="utf-8")
        wrapper_codes = (
            "SCREENSHOT_ANGLE_INVALID",
            "SCREENSHOT_TARGET_INVALID_VIEW",
            "SCREENSHOT_TARGET_CROP_CONFLICT",
        )
        for code in wrapper_codes + self._BRIDGE_CODES:
            with self.subTest(code=code):
                self.assertIn(
                    code, docs,
                    msg=(
                        f"docs/api-reference.md must document the new "
                        f"error code {code!r} introduced by issue #84."
                    ),
                )

    def test_tools_registry_mentions_target_and_angle(self) -> None:
        docs = self._DOCS_TOOLS.read_text(encoding="utf-8")
        for literal in ("target", "angle", "SCREENSHOT_ANGLE_PRESETS"):
            with self.subTest(literal=literal):
                self.assertIn(
                    literal, docs,
                    msg=(
                        f"docs/tools.md must mention {literal!r} so the "
                        f"editor_screenshot registry entry exposes the "
                        f"new target-oriented capability (#84)."
                    ),
                )


class CameraScreenshotFramingDocsTests(unittest.TestCase):
    _DOCS_API_REFERENCE = (
        Path(__file__).resolve().parent.parent / "docs" / "api-reference.md"
    )
    _DOCS_TOOLS = (
        Path(__file__).resolve().parent.parent / "docs" / "tools.md"
    )

    def test_api_reference_documents_fit_mode_and_new_diagnostics(self) -> None:
        docs = self._DOCS_API_REFERENCE.read_text(encoding="utf-8")
        for token in (
            "SCREENSHOT_FIT_MODE_INVALID",
            "EDITOR_CTRL_CAMERA_PROJECTION_TRANSITION",
            "fit_mode",
            "both_axes",
            "max_axis",
        ):
            with self.subTest(token=token):
                self.assertIn(token, docs)

    def test_api_reference_documents_auto_world_space_ui_routing(self) -> None:
        docs = self._DOCS_API_REFERENCE.read_text(encoding="utf-8")
        self.assertIn(
            'target_mode="auto"',
            docs,
            msg="API reference must name the auto target-mode selector explicitly.",
        )
        self.assertIn(
            "World Space Canvas",
            docs,
            msg="Auto routing docs must state the World Space Canvas eligibility condition.",
        )
        self.assertNotIn(
            "auto renderer path",
            docs,
            msg="API reference must not describe auto as renderer-only after UI auto routing is intentional.",
        )

    def test_tools_reference_documents_screenshot_fit_mode(self) -> None:
        docs = self._DOCS_TOOLS.read_text(encoding="utf-8")
        editor_screenshot_row = next(
            line for line in docs.splitlines() if line.startswith("| `editor_screenshot`")
        )
        for token in ("fit_mode", "both_axes", "max_axis", "width", "height", "target"):
            with self.subTest(token=token):
                self.assertIn(token, editor_screenshot_row)


class EditorFrameRendererFramingSourceTests(unittest.TestCase):
    """Issue #85 — editor_frame must use the shared renderer bounds model."""

    def _handle_frame_selected_body(self) -> str:
        return _extract_method(_read(BRIDGE), "HandleFrameSelected")

    def test_frame_selected_uses_shared_renderer_bounds_before_rect_transform_fallback(self) -> None:
        body = self._handle_frame_selected_body()
        helper_index = body.find("TryResolveRendererFramingBounds")
        rect_index = body.find("GetComponent<RectTransform>")
        self.assertNotEqual(
            -1,
            helper_index,
            msg="HandleFrameSelected must call TryResolveRendererFramingBounds for renderer targets.",
        )
        self.assertNotEqual(
            -1,
            rect_index,
            msg="HandleFrameSelected must preserve the RectTransform fallback branch.",
        )
        self.assertLess(
            helper_index,
            rect_index,
            msg="Renderer bounds must be attempted before the RectTransform fallback.",
        )

    def test_frame_selected_has_no_single_renderer_bounds_path(self) -> None:
        body = self._handle_frame_selected_body()
        self.assertNotIn(
            "GetComponentInChildren<Renderer>()",
            body,
            msg="HandleFrameSelected must not frame only the first child Renderer.",
        )
        self.assertNotIn(
            "renderer.bounds",
            body,
            msg="HandleFrameSelected must not carry a separate Renderer.bounds path.",
        )


class RendererFramingBoundsPolicySourceTests(unittest.TestCase):
    """Issue #85 — source invariants for renderer bounds policy selection."""

    _HELPER_PATH = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.RendererFramingBounds.cs"

    def _helper_body(self) -> str:
        self.assertTrue(
            self._HELPER_PATH.exists(),
            msg="RendererFramingBounds partial must exist as the shared #85 bounds model.",
        )
        return _extract_method(
            _strip_cs_comments(self._HELPER_PATH.read_text(encoding="utf-8")),
            "TryResolveRendererFramingBounds",
        )

    def test_helper_signature_exposes_policy_and_included_excluded_records(self) -> None:
        body = self._helper_body()
        for literal in (
            "string boundsPolicy",
            "out IList<ObjectCaptureFramingMath.RendererBoundsRecord> includedRecords",
            "out IList<ObjectCaptureFramingMath.RendererBoundsRecord> excludedRecords",
        ):
            with self.subTest(literal=literal):
                self.assertIn(
                    literal,
                    body,
                    msg=f"TryResolveRendererFramingBounds must expose {literal!r}.",
                )

    def test_helper_collects_active_enabled_renderer_contributors(self) -> None:
        body = self._helper_body()
        for literal in (
            "GetComponentsInChildren<Renderer>(false)",
            "renderer.enabled",
            "records.Add(ToRendererBoundsRecord(bounds))",
        ):
            with self.subTest(literal=literal):
                self.assertIn(
                    literal,
                    body,
                    msg=f"TryResolveRendererFramingBounds must contain {literal!r}.",
                )

    def test_helper_bakes_skinned_meshes_and_destroys_temporary_meshes(self) -> None:
        body = self._helper_body()
        for literal in (
            "SkinnedMeshRenderer",
            ".BakeMesh(",
            "UnityEngine.Object.DestroyImmediate",
        ):
            with self.subTest(literal=literal):
                self.assertIn(
                    literal,
                    body,
                    msg=f"TryResolveRendererFramingBounds must contain {literal!r}.",
                )

    def test_all_visible_branch_aggregates_records_without_core_filter(self) -> None:
        body = self._helper_body()
        all_visible_index = body.find('boundsPolicy == "all_visible_renderers"')
        focus_core_index = body.find('boundsPolicy == "focus_core"')
        selector_index = body.find("ObjectCaptureFramingMath.SelectFramingRenderers")
        self.assertNotEqual(
            -1,
            all_visible_index,
            msg="TryResolveRendererFramingBounds must branch on all_visible_renderers.",
        )
        self.assertNotEqual(
            -1,
            focus_core_index,
            msg="TryResolveRendererFramingBounds must branch on focus_core.",
        )
        self.assertLess(
            all_visible_index,
            focus_core_index,
            msg="The default all_visible_renderers branch must be evaluated before focus_core.",
        )
        self.assertNotIn(
            "ObjectCaptureFramingMath.SelectFramingRenderers",
            body[all_visible_index:focus_core_index],
            msg="all_visible_renderers must aggregate gathered records without core selection.",
        )
        self.assertIn(
            "includedRecords = records",
            body[all_visible_index:focus_core_index],
            msg="all_visible_renderers must include every gathered renderer record.",
        )
        self.assertLess(
            focus_core_index,
            selector_index,
            msg="SelectFramingRenderers must be confined to the focus_core branch.",
        )

    def test_focus_core_branch_records_selector_exclusions(self) -> None:
        body = self._helper_body()
        focus_core_index = body.find('boundsPolicy == "focus_core"')
        self.assertNotEqual(-1, focus_core_index, msg="focus_core branch missing.")
        focus_core_body = body[focus_core_index:]
        for literal in (
            "ObjectCaptureFramingMath.SelectFramingRenderers(records)",
            "excludedRecords",
            "includedRecords",
        ):
            with self.subTest(literal=literal):
                self.assertIn(
                    literal,
                    focus_core_body,
                    msg=f"focus_core branch must contain {literal!r}.",
                )


class ObjectCaptureBoundsEvidenceSourceTests(unittest.TestCase):
    _TARGET_CAPTURE_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.cs"
    )

    def _object_capture_body(self) -> str:
        self.assertTrue(
            self._TARGET_CAPTURE_PARTIAL.exists(),
            msg="Screenshot.TargetCapture partial must exist for renderer target capture.",
        )
        return _extract_method(
            _strip_cs_comments(self._TARGET_CAPTURE_PARTIAL.read_text(encoding="utf-8")),
            "HandleObjectCaptureScreenshot",
        )

    def test_data_contract_declares_bounds_policy_and_exclusion_fields(self) -> None:
        source = _read(BRIDGE)
        for literal in (
            "public string bounds_policy = string.Empty",
            "public int excluded_count = 0",
            "public GeometryBoundsContributorEntry[] excluded_renderers",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, source, msg=f"EditorControlData must declare {literal!r}.")

    def test_object_capture_success_payload_assigns_bounds_evidence(self) -> None:
        body = self._object_capture_body()
        for literal in (
            "request.bounds_policy",
            "includedRecords",
            "excludedRecords",
            "bounds_policy = request.bounds_policy",
            "bounds_center = new float[]",
            "bounds_extents = new float[]",
            "contributor_count = includedRecords.Count",
            "excluded_count = excludedRecords.Count",
            "bounds_contributors = ToContributorEntries(includedRecords)",
            "excluded_renderers = ToContributorEntries(excludedRecords)",
        ):
            with self.subTest(literal=literal):
                self.assertIn(
                    literal,
                    body,
                    msg=f"HandleObjectCaptureScreenshot must assign {literal!r}.",
                )


class ObjectCaptureBoundsPolicyErrorSourceTests(unittest.TestCase):
    _TARGET_CAPTURE_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.cs"
    )

    def _object_capture_body(self) -> str:
        return _extract_method(
            _strip_cs_comments(self._TARGET_CAPTURE_PARTIAL.read_text(encoding="utf-8")),
            "HandleObjectCaptureScreenshot",
        )

    def test_invalid_policy_error_precedes_renderer_success(self) -> None:
        body = self._object_capture_body()
        error_index = body.find("BuildBoundsPolicyInvalidError(request.bounds_policy)")
        success_index = body.find("EDITOR_CTRL_SCREENSHOT_OK")
        self.assertNotEqual(
            -1,
            error_index,
            msg="HandleObjectCaptureScreenshot must call the bounds-policy error helper.",
        )
        self.assertLess(
            error_index,
            success_index,
            msg="Invalid bounds_policy must be rejected before screenshot success.",
        )
        source = _read(BRIDGE)
        helper_body = _extract_method(source, "BuildBoundsPolicyInvalidError")
        for literal in (
            "EDITOR_CTRL_BOUNDS_POLICY_INVALID",
            "all_visible_renderers",
            "focus_core",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, helper_body)


class FrameSelectedBoundsEvidenceSourceTests(unittest.TestCase):
    def _handle_frame_selected_body(self) -> str:
        return _extract_method(_read(BRIDGE), "HandleFrameSelected")

    def test_frame_selected_success_payload_assigns_bounds_evidence(self) -> None:
        body = self._handle_frame_selected_body()
        for literal in (
            "request.bounds_policy",
            "includedRecords",
            "excludedRecords",
            "sceneView.Frame(frameBounds, instant: true)",
            "data.bounds_policy = request.bounds_policy",
            "data.contributor_count = includedRecords.Count",
            "data.excluded_count = excludedRecords.Count",
            "data.bounds_contributors = ToContributorEntries(includedRecords)",
            "data.excluded_renderers = ToContributorEntries(excludedRecords)",
            "data.bounds_source = \"rect_transform\"",
        ):
            with self.subTest(literal=literal):
                self.assertIn(
                    literal,
                    body,
                    msg=f"HandleFrameSelected must contain {literal!r}.",
                )


class FrameSelectedBoundsPolicyErrorSourceTests(unittest.TestCase):
    def test_invalid_policy_error_precedes_frame_success(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleFrameSelected")
        error_index = body.find("BuildBoundsPolicyInvalidError(request.bounds_policy)")
        success_index = body.find("EDITOR_CTRL_FRAME_OK")
        self.assertNotEqual(
            -1,
            error_index,
            msg="HandleFrameSelected must call the bounds-policy error helper.",
        )
        self.assertLess(
            error_index,
            success_index,
            msg="Invalid bounds_policy must be rejected before frame success.",
        )
        helper_body = _extract_method(_read(BRIDGE), "BuildBoundsPolicyInvalidError")
        for literal in (
            "EDITOR_CTRL_BOUNDS_POLICY_INVALID",
            "all_visible_renderers",
            "focus_core",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, helper_body)


class GeometryMeasureDistanceSourceTests(unittest.TestCase):
    def test_measure_distance_validates_bounds_source_before_pivot_shortcut(self) -> None:
        source = _read(TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Geometry.cs")
        body = _extract_method(source, "HandleMeasureDistance")
        validation_idx = body.find("ValidateBoundsSourceSelector(")
        pivot_idx = body.find("request.distance_mode == \"pivot\"")
        self.assertNotEqual(
            -1,
            validation_idx,
            msg="HandleMeasureDistance must validate bounds_source explicitly.",
        )
        self.assertIn("request.bounds_source", body[validation_idx:pivot_idx])
        self.assertNotEqual(
            -1,
            pivot_idx,
            msg="HandleMeasureDistance must keep the pivot distance branch.",
        )
        self.assertLess(
            validation_idx,
            pivot_idx,
            msg="bounds_source validation must run before the pivot shortcut.",
        )


class ScreenshotWorldSpaceUiSourceTests(unittest.TestCase):
    _TARGET_CAPTURE_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.cs"
    )
    _WORLD_SPACE_UI_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.WorldSpaceUi.cs"
    )

    def _target_capture_body(self) -> str:
        self.assertTrue(
            self._TARGET_CAPTURE_PARTIAL.exists(),
            msg="Screenshot.TargetCapture partial must exist for renderer target capture.",
        )
        return _read(self._TARGET_CAPTURE_PARTIAL)

    def _world_space_ui_body(self) -> str:
        self.assertTrue(
            self._WORLD_SPACE_UI_PARTIAL.exists(),
            msg="Screenshot.TargetCapture.WorldSpaceUi partial must exist for world_space_ui target capture.",
        )
        return _read(self._WORLD_SPACE_UI_PARTIAL)

    def test_ui_capture_supports_front_back_current_camera_and_rejects_other_angles(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "HandleWorldSpaceUiCaptureScreenshot",
        )
        self.assertIn("angle != \"front\" && angle != \"back\" && angle != \"current_camera\"", body)
        self.assertIn("EDITOR_CTRL_SCREENSHOT_ANGLE_INVALID", body)
        self.assertIn("angle == \"back\"", body)
        self.assertIn("angle == \"current_camera\"", body)
        self.assertIn("cam.transform.forward", body)

    def test_ui_capture_front_uses_readable_side_of_rect_transform(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "HandleWorldSpaceUiCaptureScreenshot",
        )
        self.assertIn(
            "Vector3 cameraDir = -uiNormal;",
            body,
            msg=(
                "World Space UI angle='front' must place the camera on the "
                "readable side of Unity UI graphics; using +uiNormal captures "
                "the panel from behind and mirrors the text."
            ),
        )
        back_index = body.index('if (angle == "back")')
        self.assertIn(
            "cameraDir = uiNormal;",
            body[back_index:],
            msg="angle='back' must select the opposite side from front.",
        )

    def test_object_capture_validates_renderer_angle_after_ui_branch(self) -> None:
        body = _extract_method(
            self._target_capture_body(),
            "HandleObjectCaptureScreenshot",
        )
        ui_selector_index = body.index("ShouldUseWorldSpaceUiCapture")
        renderer_helper_index = body.index("TryResolveRendererFramingBounds")
        renderer_preset_index = body.index("ObjectCaptureFramingMath.PresetNames")
        renderer_error_index = body.index("EDITOR_CTRL_SCREENSHOT_ANGLE_INVALID", renderer_preset_index)
        self.assertLess(
            ui_selector_index,
            renderer_helper_index,
            msg="World Space UI dispatch must run before renderer bounds are resolved.",
        )
        self.assertLess(
            ui_selector_index,
            renderer_preset_index,
            msg="current_camera must reach the World Space UI branch before renderer preset validation",
        )
        self.assertLess(renderer_preset_index, renderer_error_index)
        self.assertNotIn(
            "ObjectCaptureFramingMath.PresetNames",
            body[:ui_selector_index],
            msg="renderer-only angle validation must not run before World Space UI dispatch",
        )

    def test_ui_capture_reports_required_framing_metadata(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "HandleWorldSpaceUiCaptureScreenshot",
        )
        for token in (
            "bounds_source = \"rect_transform\"",
            "bounds_center = Vector3ToArray(center)",
            "bounds_extents = Vector3ToArray(extents)",
            "ui_normal = Vector3ToArray(uiNormal)",
            "camera_position = Vector3ToArray(cameraPosition)",
            "camera_look_at = Vector3ToArray(center)",
            "camera_orthographic = orthographic",
            "camera_size = paddedHalfHeight",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_ui_capture_derives_framing_aspect_from_final_output_size(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "HandleWorldSpaceUiCaptureScreenshot",
        )
        width_index = body.find(
            "int w = request.width > 0 ? request.width : (int)sceneView.position.width;"
        )
        height_index = body.find(
            "int h = request.height > 0 ? request.height : (int)sceneView.position.height;"
        )
        aspect_index = body.find("float aspect = (float)w / (float)h;")
        padded_index = body.find(
            "float paddedHalfHeight = Math.Max(extents.y, extents.x / Math.Max(aspect, 0.001f))"
        )

        self.assertNotEqual(
            -1,
            aspect_index,
            msg="World Space UI framing aspect must derive from the final output size.",
        )
        self.assertLess(
            width_index,
            aspect_index,
            msg="World Space UI width must be finalized before framing aspect is computed.",
        )
        self.assertLess(
            height_index,
            aspect_index,
            msg="World Space UI height must be finalized before framing aspect is computed.",
        )
        self.assertLess(
            aspect_index,
            padded_index,
            msg="World Space UI padded framing must consume the final output aspect.",
        )
        self.assertNotIn(
            "cam.aspect",
            body[width_index:padded_index],
            msg="World Space UI one-sided output sizes must not frame against stale camera aspect.",
        )

    def test_ui_capture_applies_target_pixel_rectangle_crop(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "HandleWorldSpaceUiCaptureScreenshot",
        )
        crop_index = body.index("ResolveTargetPixelCrop(")
        render_index = body.index("RenderSceneViewToTexture", crop_index)
        texture_index = body.index("new Texture2D(readW, readH", render_index)
        read_index = body.index("ReadPixels(new Rect(readX, readY, readW, readH)", texture_index)
        response_index = body.index("BuildSuccess(", read_index)
        for token in (
            'width = readW',
            'height = readH',
            'crop_roi_applied = pixelRectApplied != null ? "pixel_rect" : string.Empty',
            "crop_bounds = pixelRectApplied",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body[response_index:])

    def test_explicit_world_space_ui_without_rect_transform_is_handled_error(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "ShouldUseWorldSpaceUiCapture",
        )
        error_index = body.index("has no active RectTransform contributors")
        handled_index = body.index("return true;", error_index)
        self.assertNotIn(
            "return false;",
            body[error_index:handled_index],
            msg="explicit world_space_ui targets without RectTransform must not fall through before the unsupported envelope is handled",
        )
        self.assertLess(
            body.index("if (!wantsUi) return false;"),
            error_index,
            msg="only auto mode may fall back to renderer capture when RectTransform contributors are absent",
        )

    def test_auto_world_space_canvas_with_rect_transform_routes_to_ui_capture(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "ShouldUseWorldSpaceUiCapture",
        )
        renderer_short_circuit_index = body.index(
            'if (request.target_mode == "renderer") return false;'
        )
        canvas_index = body.index("Canvas canvas = ResolveRelevantCanvas(target);")
        rect_index = body.index("bool hasRect =")
        no_rect_auto_fallback_index = body.index("if (!wantsUi) return false;")
        canvas_guard_index = body.index(
            "canvas == null || canvas.renderMode != RenderMode.WorldSpace"
        )
        final_ui_route_index = body.rfind("return true;")

        self.assertLess(
            renderer_short_circuit_index,
            canvas_index,
            msg="Explicit renderer target_mode must remain the only early renderer route.",
        )
        self.assertLess(
            rect_index,
            no_rect_auto_fallback_index,
            msg="Auto fallback to renderer must be scoped to missing RectTransform contributors.",
        )
        self.assertLess(
            no_rect_auto_fallback_index,
            canvas_guard_index,
            msg="Auto fallback must occur before the World Space Canvas eligibility check.",
        )
        self.assertNotIn(
            "if (!wantsUi) return false;",
            body[canvas_guard_index:final_ui_route_index],
            msg="Eligible auto targets under a World Space Canvas must route to UI capture.",
        )

    def test_ui_capture_anchor_resolution_avoids_null_coalescing_on_unity_objects(self) -> None:
        body = _extract_method(
            self._world_space_ui_body(),
            "HandleWorldSpaceUiCaptureScreenshot",
        )
        self.assertIn("RectTransform anchor = target.GetComponent<RectTransform>();", body)
        self.assertIn(
            "if (anchor == null)\n                anchor = target.GetComponentInChildren<RectTransform>(includeInactive: false);",
            body,
            msg=(
                "RectTransform anchor resolution must use Unity's overloaded "
                "null check before falling back to children; C# ?? can retain "
                "a Unity fake-null component wrapper and later throw "
                "MissingComponentException."
            ),
        )
        self.assertNotIn(
            "GetComponent<RectTransform>()\n                ??",
            body,
            msg="UnityEngine.Object references must not use ?? for RectTransform fallback.",
        )


class UnityCameraTypeQualificationSourceTests(unittest.TestCase):
    def test_screenshot_partials_fully_qualify_unity_camera_type(self):
        files = [
            "PrefabSentinel.UnityEditorControlBridge.Screenshot.cs",
            "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.cs",
            "PrefabSentinel.UnityEditorControlBridge.Screenshot.TargetCapture.WorldSpaceUi.cs",
            "PrefabSentinel.UnityIntegrationTests.cs",
        ]

        for filename in files:
            source = _strip_cs_comments(_read(TOOLS_DIR / filename))
            self.assertNotRegex(source, r"(?<![\w.])Camera\s+cam\b", filename)
            self.assertNotIn("RenderSceneViewToTexture(Camera ", source, filename)
            self.assertNotIn("GetComponent<Camera>", source, filename)


class HandleSetCameraSizeFieldSourceTests(unittest.TestCase):
    """Issue #81 — the orbit-radius argument is named ``size`` end-to-end
    (Python wrapper, kwargs builder, wire DTO, bridge handler).  This T3
    net pins:

    * ``HandleSetCamera`` reads from ``request.size`` (the consumer-side
      half of the rename — a DTO-only rename that misses the consumer
      would slip past the schema test);
    * ``HandleSetCamera`` does not reference ``request.distance`` (no
      hidden alias).

    The handler runs only inside the Unity Editor process; this is the
    bridge-side source-text regression net (justified in spec.md Tier 3
    Justification).
    """

    def test_handle_set_camera_consumes_request_size(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleSetCamera")
        self.assertIn(
            "request.size",
            body,
            msg=(
                "HandleSetCamera must consume the orbit-radius field "
                "under the name ``request.size`` (#81)."
            ),
        )

    def test_handle_set_camera_does_not_reference_request_distance(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleSetCamera")
        self.assertNotIn(
            "request.distance",
            body,
            msg=(
                "HandleSetCamera must not reference ``request.distance``; "
                "the orbit-radius field was renamed to ``size`` end-to-end "
                "(#81) and the pre-rename alias must not survive."
            ),
        )


class EditorSetCameraDocsRenameTests(unittest.TestCase):
    """Issue #81 — the camera-modes table in ``docs/api-reference.md``
    names the orbit-radius argument as ``size``; the pre-rename name
    ``distance`` does not appear in the Pivot-orbit row.
    """

    _DOCS_PATH = (
        Path(__file__).resolve().parent.parent / "docs" / "api-reference.md"
    )

    def _pivot_orbit_row(self) -> str:
        text = self._DOCS_PATH.read_text(encoding="utf-8")
        # Find the row starting with the "Pivot orbit" cell label so
        # the assertion is anchored to the orbit-radius row and not to
        # an unrelated mention of ``distance`` elsewhere in the doc.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("| Pivot orbit "):
                return line
        raise AssertionError("Pivot orbit row not found in api-reference.md")

    def test_pivot_orbit_row_names_size(self) -> None:
        row = self._pivot_orbit_row()
        self.assertIn(
            "`size`",
            row,
            msg=(
                "Pivot-orbit row of the camera-modes table must name the "
                "orbit-radius argument as ``size`` (#81); observed row="
                f"{row!r}"
            ),
        )

    def test_pivot_orbit_row_does_not_name_distance(self) -> None:
        row = self._pivot_orbit_row()
        self.assertNotIn(
            "distance",
            row,
            msg=(
                "Pivot-orbit row of the camera-modes table must not "
                "carry the pre-rename name ``distance`` (#81); observed "
                f"row={row!r}"
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
        # (AGENTS.md / README.md); a rename would silently break the
        # temp-exclusion contract with the run-script handler that writes
        # there. The literal now lives on EditorScriptPathClassifier.
        source = _strip_cs_comments(EDITOR_SCRIPT_PATH_CLASSIFIER.read_text(encoding="utf-8"))
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
        # Animation-clip primitives (issue #243; issue #53 consolidated
        # the directory/stem fields into the reused asset_path field).
        "curves_json",
        # Prefab Stage save flag (issue #236).
        "save_on_close",
        # Target-oriented screenshot (issue #84).
        "target",
        "angle",
        "target_mode",
        "padding_ratio",
        "projection",
        "since_sequence",
        "since_request_id",
        "bounds_source",
        "include_children",
        "distance_mode",
        "values_json",
        "values_json_present",
        "expected_length",
        "property_path",
        "root_property_path",
        "cap",
        "serialized_property_bool_value",
        "serialized_property_bool_value_present",
        "serialized_property_int_value",
        "serialized_property_int_value_present",
        "serialized_property_long_value",
        "serialized_property_long_value_present",
        "serialized_property_float_value",
        "serialized_property_float_value_present",
        "serialized_property_string_value",
        "serialized_property_string_value_present",
        "serialized_property_enum_name",
        "serialized_property_enum_name_present",
        "serialized_property_enum_index",
        "serialized_property_enum_index_present",
        "serialized_property_object_reference_asset_path",
        "serialized_property_object_reference_asset_path_present",
        "serialized_property_object_reference_hierarchy_path",
        "serialized_property_object_reference_hierarchy_path_present",
        "serialized_property_object_reference_null",
        "serialized_property_array_size",
        "serialized_property_array_size_present",
        "asset_type",
        "source_asset_path",
        "destination_asset_path",
        "parameters",
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
        "return_value",
        "outputs",
        "exception",
        "path_hints",
        "hierarchy_path",
        "local_position",
        "world_position",
        "bounds_source",
        "bounds_center",
        "bounds_extents",
        "target_mode",
        "projection",
        "ui_normal",
        "distance_mode",
        "distance",
        # UdonSharp array write error context.
        "field_name",
        "element_index",
        "expected_type",
        "serialized_property_json",
        "asset_type",
        "unity_type",
        "guid",
        "would_create",
        "created",
        "dry_run",
        "refreshed",
        "dirty_before",
        "dirty_after",
        "name",
        "applied_parameters",
        "source_asset_path",
        "destination_asset_path",
        "before_guid",
        "after_guid",
        "guid_preserved",
        "would_move",
        "moved",
        "old_name",
        "new_name",
        "name_changed",
        "phase",
        "exception_type",
        "exception_message",
        "unity_error",
        "meta_exists",
        "state_unknown",
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
        "get_transform",
        "get_bounds",
        "measure_distance",
        "editor_serialized_property_read",
        "editor_serialized_property_list",
        "editor_serialized_property_write",
        "create_generated_asset",
        "move_asset",
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
        "get_transform": "HandleGetTransform",
        "get_bounds": "HandleGetBounds",
        "measure_distance": "HandleMeasureDistance",
        "editor_serialized_property_read": "HandleSerializedPropertyRead",
        "editor_serialized_property_list": "HandleSerializedPropertyList",
        "editor_serialized_property_write": "HandleSerializedPropertyWrite",
        "create_generated_asset": "HandleCreateGeneratedAsset",
        "move_asset": "HandleMoveAsset",
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
        # Issue #51: the action switch lives in ``DispatchAction``, which
        # ``RunFromPaths`` calls inside its exception boundary.
        source = _read(BRIDGE)
        body = _extract_method(source, "DispatchAction")
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

    def test_dispatcher_default_branch_keeps_unknown_action_envelope(self) -> None:
        source = _read(BRIDGE)
        body = _extract_method(source, "DispatchAction")

        self.assertIn("default:", body)
        self.assertIn('"EDITOR_CTRL_UNKNOWN_ACTION"', body)
        self.assertIn("Unknown action:", body)


class AssetOpsSourceTests(unittest.TestCase):
    _ASSET_OPS = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.AssetOps.cs"

    def _source(self) -> str:
        return "\n".join(
            _read(path)
            for path in sorted(
                TOOLS_DIR.glob("PrefabSentinel.UnityEditorControlBridge.AssetOps*.cs")
            )
        )

    def test_asset_ops_partial_exists_and_declares_partial_class(self) -> None:
        self.assertTrue(self._ASSET_OPS.exists(), "AssetOps partial file is missing")
        source = self._source()
        self.assertIn("public static partial class UnityEditorControlBridge", source)

    def test_asset_ops_validation_results_avoid_record_syntax_for_unity(self) -> None:
        source = _read(TOOLS_DIR / "PrefabSentinel.AssetOpsPathValidation.cs")
        self.assertNotIn(
            "record ",
            source,
            msg="Unity 2022.3 does not provide IsExternalInit for C# record types.",
        )

    def test_asset_ops_path_validation_uses_numeric_null_char_check(self) -> None:
        source = _read(TOOLS_DIR / "PrefabSentinel.AssetOpsPathValidation.cs")
        self.assertIn("assetPath.IndexOf((char)0) >= 0", source)
        self.assertNotIn(
            "assetPath.Contains('\\0', StringComparison.Ordinal)",
            source,
        )

    def test_create_handler_uses_required_unity_apis_and_avoids_forbidden_paths(self) -> None:
        source = self._source()
        required = (
            "HandleCreateGeneratedAsset",
            "AssetOpsPathValidation.ValidateGeneratedAssetPath",
            "new RenderTexture(",
            ".filterMode",
            ".useMipMap",
            ".wrapMode",
            "AssetDatabase.CreateAsset",
            "AssetDatabase.SaveAssets",
            "AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)",
            "AssetDatabase.AssetPathToGUID",
            "AssetDatabase.LoadMainAssetAtPath",
            "AssetDatabase.IsValidFolder",
            "EditorUtility.IsDirty",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, source)
        forbidden = (
            "RenderTexture.Create(",
            "RenderTextureDescriptor",
            "graphicsFormat",
            "autoGenerateMips",
            "wrapModeU",
            "wrapModeV",
            "wrapModeW",
            "AssetDatabase.CreateFolder",
            "File.Move(",
            "File.Write",
            "EditorUtility.SetDirty(renderTexture",
            "EditorUtility.IsDirty(renderTexture",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_move_handler_uses_assetdatabase_move_and_name_dirty_policy(self) -> None:
        source = self._source()
        required = (
            "HandleMoveAsset",
            "AssetOpsPathValidation.ValidateMoveAssetPaths",
            "AssetDatabase.MoveAsset",
            "unity_error",
            "name_changed",
            "EditorUtility.SetDirty(asset",
            "AssetDatabase.SaveAssets",
            "AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport)",
            "AssetDatabase.AssetPathToGUID",
            "AssetDatabase.LoadMainAssetAtPath",
            "AssetDatabase.IsValidFolder",
            "EditorUtility.IsDirty",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn("File.Move(", source)
        self.assertNotIn("AssetDatabase.CreateFolder", source)

    def test_create_handler_declares_required_error_codes_and_partial_diagnostic(self) -> None:
        source = self._source()
        save_refresh = _extract_method(source, "SaveAndRefreshCreate")
        for code in (
            "GENERATED_ASSET_DESTINATION_EXISTS",
            "GENERATED_ASSET_DESTINATION_META_EXISTS",
            "GENERATED_ASSET_PARENT_NOT_FOUND",
            "GENERATED_ASSET_PARENT_NOT_FOLDER",
            "GENERATED_ASSET_CREATE_FAILED",
            "GENERATED_ASSET_SAVE_OR_REFRESH_FAILED",
            "GENERATED_ASSET_POSTCHECK_FAILED",
            "GENERATED_ASSET_DIRTY_POSTCHECK_FAILED",
            "PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW",
        ):
            with self.subTest(code=code):
                self.assertIn(code, source)
        for phase in ('data.phase = "save";', 'data.phase = "refresh";'):
            with self.subTest(phase=phase):
                self.assertIn(phase, save_refresh)

    def test_move_handler_declares_required_error_codes_and_partial_diagnostic(self) -> None:
        source = self._source()
        save_refresh = _extract_method(source, "SaveAndRefreshMove")
        for code in (
            "ASSET_SOURCE_NOT_FOUND",
            "ASSET_SOURCE_LOAD_FAILED",
            "ASSET_SOURCE_IS_FOLDER",
            "ASSET_DESTINATION_EXISTS",
            "ASSET_DESTINATION_META_EXISTS",
            "ASSET_DESTINATION_PARENT_NOT_FOUND",
            "ASSET_DESTINATION_PARENT_NOT_FOLDER",
            "ASSET_MOVE_FAILED",
            "ASSET_MOVE_SAVE_OR_REFRESH_FAILED",
            "ASSET_MOVE_POSTCHECK_FAILED",
            "ASSET_MOVE_DIRTY_POSTCHECK_FAILED",
            "PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW",
            "meta_exists",
        ):
            with self.subTest(code=code):
                self.assertIn(code, source)
        for phase in ('data.phase = "save";', 'data.phase = "refresh";'):
            with self.subTest(phase=phase):
                self.assertIn(phase, save_refresh)


class EditorSerializedPropertyBridgeSourceTests(unittest.TestCase):
    _PARTIAL = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.cs"

    def _source(self) -> str:
        return _read_serialized_property_partials()

    def _method(self, method_name: str) -> str:
        return _extract_method(self._source(), method_name)

    def test_read_handler_uses_canonical_serialized_property_surface(self) -> None:
        body = self._method("HandleSerializedPropertyRead")
        for token in (
            "ResolveSerializedPropertyTarget",
            "new SerializedObject",
            "FindProperty(request.property_path)",
            "BuildSerializedPropertyJson",
            "serialized_property_json",
            "BuildPropertyNotFoundError",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_list_handler_normalizes_traversal_and_retains_no_snapshot_store(self) -> None:
        source = self._source()
        body = _extract_method(source, "HandleSerializedPropertyList")
        for token in (
            "SerializedPropertyTraversalOptions.Parse",
            "GetIterator",
            "next_cursor",
            "truncated",
            "CollectSerializedPropertyList",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        list_builder = _extract_method(source, "BuildSerializedPropertyListJson")
        self.assertIn('\\\"items\\\"', list_builder)
        self.assertNotIn('\\\"properties\\\"', list_builder)
        collector = _extract_method(source, "CollectSerializedPropertyList")
        self.assertIn("SerializedPropertyTraversalOptions.Parse(1, 1, string.Empty)", collector)
        self.assertIn(
            "int maxRelativeDepth = root != null ? options.Depth : options.Depth + 1;",
            collector,
        )
        self.assertIn("if (relativeDepth > maxRelativeDepth) continue;", collector)
        self.assertNotIn("if (relativeDepth > options.Depth + 1) continue;", collector)
        for token in ("NextVisible", "unsupported"):
            with self.subTest(token=token):
                self.assertIn(token, source)
        for forbidden in ("static Dictionary", "static List", "snapshot"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_serialized_property_helpers_do_not_keep_unused_state(self) -> None:
        value_intent = _strip_cs_comments(
            (TOOLS_DIR / "PrefabSentinel.SerializedProperty.ValueIntent.cs").read_text(
                encoding="utf-8"
            )
        )
        target = _strip_cs_comments(
            (
                TOOLS_DIR
                / "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.Target.cs"
            ).read_text(encoding="utf-8")
        )
        for token in ("ObjectReferencePath", "HasCursor"):
            with self.subTest(token=token):
                self.assertNotIn(token, value_intent)
        for token in ("public int ComponentIndex", "ComponentIndex = selectedIndex"):
            with self.subTest(token=token):
                self.assertNotIn(token, target)

    def test_payload_builder_reports_required_property_evidence(self) -> None:
        source = self._source()
        body = _extract_method(source, "BuildSerializedPropertyJson")
        for token in (
            "property.propertyPath",
            "property.displayName",
            "property.propertyType",
            "value_kind",
            "AppendPropertyValueFields",
            "children",
            "unsupported",
            "state",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        value_fields = _extract_method(source, "AppendPropertyValueFields")
        for token in ("object_reference", "array_size"):
            with self.subTest(token=token):
                self.assertIn(token, value_fields)
        object_reference = _extract_method(source, "BuildObjectReferenceJson")
        for token in ("hierarchy_path", "GetHierarchyPath", "AssetDatabase.GetAssetPath"):
            with self.subTest(token=token):
                self.assertIn(token, object_reference)

    def test_property_not_found_suggestions_use_raw_property_paths(self) -> None:
        source = self._source()
        body = self._method("BuildPropertyNotFoundError")
        for token in (
            "SuggestionRanker.SuggestSimilar",
            "propertyPath",
            "BuildSuggestionJson",
            "truncated",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_NOT_FOUND",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        suggestion_builder = _extract_method(source, "BuildSuggestionJson")
        for token in ("displayName", "propertyType", "depth"):
            with self.subTest(token=token):
                self.assertIn(token, suggestion_builder)

    def test_component_selection_rejects_ambiguous_matches_with_candidates(self) -> None:
        source = self._source()
        body = self._method("ResolveSerializedPropertyTarget")
        for token in (
            "GetComponents",
            "component_index",
            "BuildComponentAmbiguityError",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        ambiguity = _extract_method(source, "BuildComponentAmbiguityError")
        for token in (
            "EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_AMBIGUOUS",
            "candidate",
            "component_index",
        ):
            with self.subTest(token=token):
                self.assertIn(token, ambiguity)
        self.assertNotIn("GetComponent(", body)

    def test_state_evidence_distinguishes_scene_and_prefab_stage(self) -> None:
        body = self._method("BuildSerializedPropertyStateEvidence")
        for token in (
            "PrefabStageUtility.GetCurrentPrefabStage",
            '"prefab_stage"',
            '"scene"',
            "prefab_asset_path",
            "scene_path",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)


class EditorSerializedPropertyWriterScopeTests(unittest.TestCase):
    _PARTIAL = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.cs"

    def _source(self) -> str:
        return _read_serialized_property_partials()

    def _method(self, method_name: str) -> str:
        return _extract_method(self._source(), method_name)

    def test_write_handler_separates_dry_run_noop_and_apply_side_effects(self) -> None:
        source = self._source()
        body = _extract_method(source, "HandleSerializedPropertyWrite")
        dry_run = body.find("EDITOR_CTRL_SERIALIZED_PROPERTY_DRY_RUN_OK")
        no_change = body.find("EDITOR_CTRL_SERIALIZED_PROPERTY_NO_CHANGE")
        undo = body.find("Undo.RecordObject")
        apply = body.find("ApplyModifiedProperties")
        for label, index in (
            ("dry-run branch", dry_run),
            ("no-change branch", no_change),
            ("Undo apply branch", undo),
            ("SerializedObject apply branch", apply),
        ):
            with self.subTest(label=label):
                self.assertNotEqual(-1, index, msg=f"Missing {label}.")
        self.assertLess(dry_run, undo)
        self.assertLess(no_change, undo)
        self.assertLess(undo, apply)
        for token in (
            "MarkSerializedPropertyTargetDirty",
            "RecordSerializedPropertyPrefabOverride",
            "saved = false",
            "executed = true",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        write_result_builder = _extract_method(source, "BuildWriteResultJson")
        self.assertIn(
            "dirty_target",
            write_result_builder,
            msg="Write result payload must report the dirty target evidence required by #112 dry-run/apply responses.",
        )
        for token in (
            "EditorSceneManager.MarkSceneDirty",
            "PrefabUtility.RecordPrefabInstancePropertyModifications",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_writer_scope_rejects_structs_mismatches_unsigned_and_bad_enums(self) -> None:
        source = self._source()
        for token in (
            "SerializedPropertyType.Vector3",
            "SerializedPropertyType.Color",
            "SerializedPropertyType.Quaternion",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_UNSUPPORTED_WRITE",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_TYPE_MISMATCH",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_UNSIGNED_RANGE",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_ENUM_VALUE_NOT_FOUND",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        range_helper = self._method("ResolveSerializedPropertyIntegerRange")
        for token in (
            "property.numericType",
            "SerializedPropertyNumericType.Int64",
            "SerializedPropertyNumericType.UInt64",
            "ulong.MaxValue",
        ):
            with self.subTest(token=token):
                self.assertIn(token, range_helper)
        value_fields = self._method("AppendPropertyValueFields")
        for token in (
            "SerializedPropertyNumericType.UInt64",
            "ulong_value",
            "property.ulongValue",
        ):
            with self.subTest(token=token):
                self.assertIn(token, value_fields)
        integer_writer = self._method("ApplyIntegerValue")
        self.assertIn('BuildScalarJson("ulong_value", property.ulongValue)', integer_writer)
        error_plan = self._method("WritePlanError")
        for token in ("CurrentJson", "ProposedJson", "BuildCurrentPropertyValueJson", "BuildErrorProposalJson"):
            with self.subTest(token=token):
                self.assertIn(token, error_plan)
        write_body = self._method("HandleSerializedPropertyWrite")
        self.assertIn("BuildWriteResultJson(", write_body)

    def test_object_reference_writes_resolve_paths_null_and_identity(self) -> None:
        body = self._method("ResolveSerializedPropertyObjectReference")
        for token in (
            "serialized_property_object_reference_asset_path",
            "AssetDatabase.LoadAssetAtPath",
            "serialized_property_object_reference_hierarchy_path",
            "serialized_property_object_reference_null",
            "TryResolveGameObjectInActiveStage",
            "ambiguity.code",
            "ambiguity.message",
            "GetComponents",
            "candidates",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_AMBIGUOUS",
            "property.objectReferenceValue != resolved",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_NOT_FOUND",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_TYPE_MISMATCH",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertNotIn("ReferenceEquals", body)
        self.assertNotIn("ambiguity != null ? ambiguity.message", body)
        ambiguity = self._method("BuildObjectReferenceAmbiguityJson")
        for token in (
            "object_reference_hierarchy_path",
            "candidates",
            "candidate",
            "component_index",
            "AppendJsonString",
        ):
            with self.subTest(token=token):
                self.assertIn(token, ambiguity)
        self.assertNotIn("go.GetComponent(expected.Type)", body)

    def test_array_resize_reports_resulting_size_and_changed_evidence(self) -> None:
        body = self._method("ApplySerializedPropertyValueIntent")
        for token in (
            "arraySize",
            "resulting_array_size",
            "would_change",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_ARRAY_SIZE_INVALID",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)


class EditorSerializedPropertyUdonSyncSourceTests(unittest.TestCase):
    _PARTIAL = TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.SerializedProperty.cs"

    def _source(self) -> str:
        return _read_serialized_property_partials()

    def _method(self, method_name: str) -> str:
        return _extract_method(self._source(), method_name)

    def test_confirmed_changed_write_attempts_udonsharp_sync_after_apply(self) -> None:
        body = self._method("HandleSerializedPropertyWrite")
        apply = body.find("ApplyModifiedProperties")
        sync = body.find("BuildUdonSharpSyncStatus")
        self.assertNotEqual(-1, apply, msg="Write handler must apply the SerializedObject.")
        self.assertNotEqual(-1, sync, msg="Write handler must build UdonSharp sync status.")
        self.assertLess(apply, sync)
        self.assertIn("InvokeUdonSharpCopyProxyToUdon", self._source())

    def test_sync_detection_uses_component_assembly_and_backing_behaviour(self) -> None:
        body = self._method("BuildUdonSharpSyncStatus")
        for token in (
            "component.GetType().Assembly",
            "UdonSharp",
            "GetComponent",
            "UdonBehaviour",
            "not_applicable",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)
        self.assertIn("if (!typeLooksUdonSharp || backing == null)", body)

    def test_sync_warning_preserves_completed_write_success_code(self) -> None:
        body = self._method("HandleSerializedPropertyWrite")
        for token in (
            'severity = "warning"',
            "EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK",
            "diagnostics",
            "sync_status",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)


class EditorSerializedPropertyUnitySmokeSourceTests(unittest.TestCase):
    _UNITY_INTEGRATION = TOOLS_DIR / "PrefabSentinel.UnityIntegrationTests.cs"

    def _source(self) -> str:
        return _strip_cs_comments(self._UNITY_INTEGRATION.read_text(encoding="utf-8"))

    def test_unity_smoke_fixture_registers_serialized_property_probe(self) -> None:
        source = self._source()
        run_suite = _extract_method(source, "RunTestSuite")
        self.assertIn("SerializedPropertySmokeSupport", source)
        for token in (
            "Test_EditorCtrl_SerializedProperty_ReadListWriteDryRunNoOp",
            "EditorCtrl_SerializedProperty_ReadListWriteDryRunNoOp",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
                self.assertIn(token, run_suite)

    def test_unity_smoke_probe_exercises_three_serialized_property_actions(self) -> None:
        body = _extract_method(
            self._source(),
            "Test_EditorCtrl_SerializedProperty_ReadListWriteDryRunNoOp",
        )
        for token in (
            "editor_serialized_property_read",
            "editor_serialized_property_list",
            "editor_serialized_property_write",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_DRY_RUN_OK",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_CHANGE",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK",
            "root_property_path",
            "m_LocalPosition",
            "missingRoot",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_NOT_FOUND",
        ):
            with self.subTest(token=token):
                self.assertIn(token, body)


class EditorSerializedPropertyDocsTests(unittest.TestCase):
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent

    def _doc(self, relative: str) -> str:
        return (self._PROJECT_ROOT / relative).read_text(encoding="utf-8")

    def test_readme_routes_serialized_property_surface_to_specialized_docs(self) -> None:
        text = self._doc("README.md")
        self.assertIn("SerializedObject-backed", text)
        self.assertIn("editor_serialized_property_read", text)
        self.assertIn("docs/tools.md", text)
        self.assertIn("docs/api-reference.md", text)

    def test_tools_catalog_lists_all_serialized_property_tools(self) -> None:
        text = self._doc("docs/tools.md")
        for tool in (
            "editor_serialized_property_read",
            "editor_serialized_property_list",
            "editor_serialized_property_write",
            "root_property_path",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_NOT_FOUND",
        ):
            with self.subTest(tool=tool):
                self.assertIn(tool, text)

    def test_api_reference_documents_payload_and_error_codes(self) -> None:
        text = self._doc("docs/api-reference.md")
        for token in (
            "serialized_property_json",
            "value_kind",
            "bool_value",
            "int_value",
            "long_value",
            "float_value",
            "string_value",
            "enum_name",
            "enum_index",
            "root_property_path",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_READ_OK",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_DRY_RUN_OK",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_NO_CHANGE",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_WRITE_OK",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_NOT_FOUND",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_COMPONENT_AMBIGUOUS",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_OBJECT_REF_AMBIGUOUS",
            "EDITOR_CTRL_SERIALIZED_PROPERTY_UNSIGNED_RANGE",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertNotIn(" / `value` / ", text)

    def test_operational_docs_cover_audit_and_unity_validation_boundary(self) -> None:
        config = self._doc("CONFIGURATION.md")
        testing = self._doc("TESTING.md")
        self.assertIn("editor_serialized_property_write", config)
        self.assertIn("SerializedPropertySmokeSupport", testing)
        self.assertIn("Unity real-device validation", testing)
        self.assertIn("editor_serialized_property_write", testing)


class EditorAssetOpsDocsTests(unittest.TestCase):
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent

    def _doc(self, relative: str) -> str:
        return (self._PROJECT_ROOT / relative).read_text(encoding="utf-8")

    def test_tools_catalog_lists_editor_asset_category_and_tools(self) -> None:
        text = self._doc("docs/tools.md")
        for token in (
            "現在 98 件",
            "18 カテゴリ",
            "**editor_assets**",
            "### editor_assets",
            "prefab_sentinel/mcp_tools_editor_assets.py",
            "`editor_create_generated_asset`",
            "`editor_move_asset`",
            "render_texture",
            "AssetDatabase.MoveAsset",
            "`copy_asset` / `rename_asset`",
            "`delete_assets` は削除 surface",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertNotIn("editor_delete_assets", text)

    def test_configuration_lists_confirm_report_requirements(self) -> None:
        text = self._doc("CONFIGURATION.md")
        for tool in ("editor_create_generated_asset", "editor_move_asset"):
            rows = [
                line for line in text.splitlines()
                if f"`{tool}`" in line and line.startswith("|")
            ]
            self.assertEqual(1, len(rows), msg=f"missing audit row for {tool}")
            self.assertIn("✅", rows[0], msg=f"{tool} must require change_reason")
            self.assertIn("`out_report`", rows[0], msg=f"{tool} must require out_report")
        self.assertIn("dry-run", text)
        self.assertIn("OUT_REPORT_REQUIRED", text)

    def test_tool_conventions_document_dry_run_and_asset_boundaries(self) -> None:
        text = self._doc("docs/tool-conventions.md")
        for token in (
            "editor_create_generated_asset",
            "editor_move_asset",
            "confirm=True",
            "out_report",
            "dry-run",
            "AssetDatabase",
            "copy_asset",
            "rename_asset",
            "delete_assets",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_api_reference_documents_asset_ops_contract(self) -> None:
        text = self._doc("docs/api-reference.md")
        for token in (
            "editor_create_generated_asset",
            "editor_move_asset",
            "asset_type",
            "asset_path",
            "parameters",
            "source_asset_path",
            "destination_asset_path",
            "applied_parameters",
            "format",
            "read_write",
            "unity_type",
            "guid_preserved",
            "UNITY_BRIDGE_INVALID_RESPONSE",
            "PARTIAL_SIDE_EFFECT_REQUIRES_REVIEW",
            "OUT_REPORT_WRITE_FAILED",
            "UNSUPPORTED_GENERATED_ASSET_TYPE",
            "GENERATED_ASSET_DESTINATION_EXISTS",
            "GENERATED_ASSET_SAVE_OR_REFRESH_FAILED",
            "ASSET_SOURCE_NOT_FOUND",
            "ASSET_DESTINATION_PARENT_NOT_FOUND",
            "ASSET_MOVE_FAILED",
            "ASSET_MOVE_DIRTY_POSTCHECK_FAILED",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn(
            "`applied_parameters` は snake_case `width`, `height`, `depth`, `format`, `read_write`, `filter_mode`, `wrap_mode`, `mip_map`",
            text,
        )
        self.assertIn(
            "Bridge success payload は `source_asset_path`, `destination_asset_path`, `unity_type`, `before_guid`",
            text,
        )
        for stale_token in (
            "graphics_format",
            "ASSET_MOVE_SOURCE_NOT_FOUND",
            "ASSET_MOVE_DESTINATION_EXISTS",
            "ASSET_MOVE_PARENT_NOT_FOUND",
            "ASSET_MOVE_GUID_CHANGED",
        ):
            with self.subTest(stale_token=stale_token):
                self.assertNotIn(stale_token, text)

    def test_testing_doc_records_deferred_unity_smoke_sequence(self) -> None:
        text = self._doc("TESTING.md")
        for token in (
            "Issue #116",
            "editor_create_generated_asset",
            "editor_move_asset",
            "create dry-run",
            "create confirm",
            "move dry-run",
            "move confirm",
            "lowercase `.rendertexture`",
            "case-only move",
            "report equality",
            "delete_assets",
            "Unity 2022.3",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertNotIn("editor_delete_assets", text)

    def test_readme_routes_editor_asset_tools_to_specialized_docs(self) -> None:
        text = self._doc("README.md")
        for token in (
            "editor_create_generated_asset",
            "editor_move_asset",
            "docs/tools.md",
            "docs/api-reference.md",
            "CONFIGURATION.md",
            "TESTING.md",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)


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
        # Issue #38: the resolution logic (normalization, stage walk,
        # scene fallback) lives in ``TryResolveGameObjectInActiveStage``;
        # ``ResolveGameObjectInActiveStage`` is a thin wrapper delegating
        # to it.
        return _extract_method(
            self._PREFAB_STAGE_PARTIAL.read_text(encoding="utf-8"),
            "TryResolveGameObjectInActiveStage",
        )

    def _close_handler_body(self) -> str:
        return _extract_method(
            self._PREFAB_STAGE_PARTIAL.read_text(encoding="utf-8"),
            "HandleClosePrefab",
        )

    def test_resolver_delegates_normalization_to_path_logic(self) -> None:
        """Issue #18 / H-10 T1 — the active-stage resolver must route
        leading-slash normalization through the dedicated Unity-free
        ``StageHierarchyPathLogic.NormalizeStagePath`` component (whose
        behavior is exercised by ``StageHierarchyPathLogicTests`` in the
        C# harness) rather than re-inlining the strip.
        """
        body = _strip_cs_comments(self._resolver_body())
        self.assertIn(
            "StageHierarchyPathLogic.NormalizeStagePath",
            body,
            msg=(
                "TryResolveGameObjectInActiveStage must delegate "
                "active-stage path normalization to "
                "StageHierarchyPathLogic.NormalizeStagePath; a "
                "re-inlined StartsWith/Substring strip re-introduces "
                "the duplicated normalization (issue #18)."
            ),
        )

    def test_resolver_active_stage_branch_has_no_scene_find(self) -> None:
        """Inside the active-stage branch (the code reached once the
        ``stage == null`` early-return is passed), no ``GameObject.Find``
        call may appear; the scene-wide lookup is reserved for the
        terminal no-stage path.
        """
        body = _strip_cs_comments(self._resolver_body())
        # The no-stage path early-returns; everything after the
        # ``if (stage == null)`` block's closing brace is the
        # active-stage branch.
        guard_idx = body.find("if (stage == null)")
        self.assertNotEqual(
            -1, guard_idx,
            msg="no-stage guard ``if (stage == null)`` is missing",
        )
        open_idx = body.find("{", guard_idx)
        self.assertNotEqual(-1, open_idx, msg="no-stage guard body missing")
        no_stage_branch = _extract_braced_block(
            body, open_idx + 1, "no-stage branch",
        )
        # The active-stage branch is the source past the no-stage block.
        block_end = body.index(no_stage_branch) + len(no_stage_branch)
        active_branch = body[block_end:]
        self.assertNotIn(
            "GameObject.Find",
            active_branch,
            msg=(
                "Active-stage branch must NOT call GameObject.Find "
                "(issue #264 scene-leak regression)."
            ),
        )

    def test_resolver_inactive_stage_terminal_uses_scene_find(self) -> None:
        """The no-stage path must consult the open scene via
        ``GameObject.Find`` so existing scene-edit workflows continue
        to work when no Prefab Stage is open.
        """
        body = _strip_cs_comments(self._resolver_body())
        self.assertIn(
            "GameObject.Find(hierarchyPath)",
            body,
            msg=(
                "TryResolveGameObjectInActiveStage must consult the open "
                "scene via ``GameObject.Find`` as its no-stage path."
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


class TestResolveGameObjectDelegation(unittest.TestCase):
    """Issue #38 (T-38-5) — the live Prefab Stage resolver delegates
    ``#N`` segment resolution to the shared Unity-free
    ``SymbolPathResolver`` and maps the resolver's ambiguity signal to a
    typed envelope.

    Tier 3 (spec.md Tier 3 Justification T-38-5): the live stage
    resolver builds its child-node view from live Unity ``Transform``
    objects, which the xUnit harness cannot compile; the ``#N``
    resolution rule itself is Tier 1-covered through the shared
    Unity-free resolver (T-38-c2 / T-38-3 / T-38-4).  This source-scan
    pins only the delegation structure: the live resolver routes
    segment resolution through the shared resolver, carries no
    independent first-pick path walk, and maps the ambiguity signal to
    the ``EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS`` envelope.
    """

    _PREFAB_STAGE_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.PrefabStage.cs"
    )

    def _resolver_body(self) -> str:
        return _strip_cs_comments(
            _extract_method(
                self._PREFAB_STAGE_PARTIAL.read_text(encoding="utf-8"),
                "TryResolveGameObjectInActiveStage",
            )
        )

    def test_resolver_delegates_segment_resolution_to_shared_resolver(self) -> None:
        body = self._resolver_body()
        self.assertIn(
            "SymbolPathResolver.Resolve",
            body,
            msg=(
                "TryResolveGameObjectInActiveStage must delegate segment "
                "resolution to the shared SymbolPathResolver."
            ),
        )

    def test_resolver_has_no_independent_transform_find_walk(self) -> None:
        # A re-inlined ``Transform.Find`` path walk would silently
        # first-pick a same-named sibling, bypassing the resolver's
        # ambiguity rejection.
        body = self._resolver_body()
        self.assertNotIn(
            ".transform.Find(",
            body,
            msg=(
                "The live stage resolver must not carry an independent "
                "Transform.Find path walk; segment resolution belongs to "
                "the shared SymbolPathResolver."
            ),
        )

    def test_resolver_maps_ambiguity_signal_to_typed_envelope(self) -> None:
        body = self._resolver_body()
        self.assertIn(
            "SymbolPathOutcome.Ambiguous",
            body,
            msg=(
                "The resolver must branch on the SymbolPathResolver "
                "ambiguity signal."
            ),
        )
        self.assertIn(
            "EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS",
            body,
            msg=(
                "An ambiguous live path must map to the "
                "EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS envelope."
            ),
        )


class TestValuePresentMarkerConsumption(unittest.TestCase):
    """Issue #52 — the bridge write handlers *consume* the value-present
    marker so an empty-string write is applied rather than rejected as
    "no value".

    Tier 3: the handlers run inside the Unity Editor process and are not
    xUnit-compiled.  The Python surface tests (T-52-1/2/3) pin only that
    the marker is *sent* across the bridge boundary; they cannot observe
    whether the C# handler *reads* it.  This source-scan pins the read
    side — the wire contract the DTO field documents — for the three
    #52 write handlers.
    """

    def test_set_property_handler_consults_value_present_marker(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleEditorSetProperty")
        self.assertIn(
            "request.property_value_present",
            body,
            msg=(
                "HandleEditorSetProperty must consult "
                "request.property_value_present so an empty-string write "
                "is not rejected as 'no value'."
            ),
        )

    def test_udonsharp_field_handler_consults_value_present_marker(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleSetUdonSharpField")
        self.assertIn(
            "request.property_value_present",
            body,
            msg=(
                "HandleSetUdonSharpField must consult "
                "request.property_value_present so an empty-string field "
                "value is not rejected with EDITOR_CTRL_UDON_SET_FIELD_NO_VALUE."
            ),
        )

    def test_batch_handler_forwards_op_value_present(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleEditorBatchSetProperty")
        self.assertIn(
            "property_value_present = op.value_present",
            body,
            msg=(
                "HandleEditorBatchSetProperty must forward the per-op "
                "value_present marker into the delegated sub-request."
            ),
        )


class TestSetPropertyAmbiguityPropagation(unittest.TestCase):
    """Issue #38 — the primary write handlers surface an ambiguous
    hierarchy_path as the dedicated EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS
    envelope instead of swallowing it via ``ResolveGameObjectInActiveStage``.

    Tier 3: the handlers run inside the Unity Editor process and are not
    xUnit-compiled.  This source-scan pins that they route resolution
    through ``TryResolveGameObjectInActiveStage`` — the variant that
    yields the ambiguity envelope — rather than the discarding
    ``ResolveGameObjectInActiveStage`` overload.
    """

    def test_set_property_handler_propagates_ambiguity_envelope(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleEditorSetProperty")
        self.assertIn(
            "TryResolveGameObjectInActiveStage",
            body,
            msg=(
                "HandleEditorSetProperty must resolve via "
                "TryResolveGameObjectInActiveStage so an ambiguous "
                "hierarchy_path surfaces EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS."
            ),
        )

    def test_udonsharp_field_handler_propagates_ambiguity_envelope(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleSetUdonSharpField")
        self.assertIn(
            "TryResolveGameObjectInActiveStage",
            body,
            msg=(
                "HandleSetUdonSharpField must resolve via "
                "TryResolveGameObjectInActiveStage so an ambiguous "
                "hierarchy_path surfaces EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS."
            ),
        )

    def test_discarding_resolver_has_no_call_sites_and_no_definition(self) -> None:
        """Issue #59: the ambiguity-discarding ResolveGameObjectInActiveStage
        wrapper is fully removed — every hierarchy-bound handler now routes
        through the ambiguity-aware TryResolveGameObjectInActiveStage."""
        bare_name = re.compile(r"\bResolveGameObjectInActiveStage")
        offenders: list[str] = []
        for cs_file in sorted(TOOLS_DIR.glob("*.cs")):
            text = _strip_cs_comments(cs_file.read_text(encoding="utf-8"))
            if bare_name.search(text):
                offenders.append(cs_file.name)
        self.assertEqual(
            [],
            offenders,
            msg=(
                "the discarding ResolveGameObjectInActiveStage wrapper "
                "must have zero call sites and no definition (issue #59); "
                f"still referenced in: {offenders}"
            ),
        )

    def test_representative_handlers_route_through_ambiguity_aware_resolver(
        self,
    ) -> None:
        """Issue #59: the delete / rename / reparent write handlers and a
        representative read handler each route through the ambiguity-aware
        resolver so an ambiguous hierarchy_path is rejected uniformly."""
        source = _read(BRIDGE)
        for handler in (
            "HandleDeleteObject",
            "HandleEditorRename",
            "HandleEditorSetParent",
            "HandleListChildren",
        ):
            body = _extract_method(source, handler)
            self.assertIn(
                "TryResolveGameObjectInActiveStage",
                body,
                msg=(
                    f"{handler} must resolve hierarchy paths through "
                    "TryResolveGameObjectInActiveStage so an ambiguous "
                    "path surfaces EDITOR_CTRL_HIERARCHY_PATH_AMBIGUOUS "
                    "(issue #59)."
                ),
            )


class MenuScriptWatchSplitSourceInvariantTests(unittest.TestCase):
    """Issue #262 — the editor-script mtime detector and its three
    standalone constants now live in a dedicated partial; the Menu
    partial no longer declares the detector.  These invariants pin
    the post-split layout end-to-end (filesystem, source, AGENTS.md
    inventory).
    """

    _MENU_SCRIPT_WATCH_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.MenuScriptWatch.cs"
    )
    _MENU_PARTIAL = (
        TOOLS_DIR / "PrefabSentinel.UnityEditorControlBridge.Menu.cs"
    )
    _AGENTS_MD = (
        Path(__file__).resolve().parent.parent / "AGENTS.md"
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
        text = _strip_cs_comments(
            self._MENU_SCRIPT_WATCH_PARTIAL.read_text(encoding="utf-8")
        )
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
        text = _strip_cs_comments(self._MENU_PARTIAL.read_text(encoding="utf-8"))
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
        text = _strip_cs_comments(self._MENU_PARTIAL.read_text(encoding="utf-8"))
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
        text = _strip_cs_comments(
            self._MENU_SCRIPT_WATCH_PARTIAL.read_text(encoding="utf-8")
        )
        self.assertIn("MenuExecuteAssetsRoot", text)
        # The two relocated constants must no longer be declared here.
        self.assertNotIn("MenuExecuteEditorSegment", text)
        self.assertNotIn("MenuExecuteRunScriptTempExclusion", text)

    def test_relocated_segment_constants_live_on_path_classifier(self) -> None:
        text = _strip_cs_comments(EDITOR_SCRIPT_PATH_CLASSIFIER.read_text(encoding="utf-8"))
        for constant in ("EditorSegment", "RunScriptTempSegment"):
            with self.subTest(constant=constant):
                self.assertIn(constant, text)

    def test_agents_md_inventory_lists_menuscriptwatch(self) -> None:
        text = self._AGENTS_MD.read_text(encoding="utf-8")
        self.assertIn(
            "MenuScriptWatch",
            text,
            msg=(
                "AGENTS.md partial inventory must list the new "
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


class TestAddComponentInitialPropertyDiagnostics(unittest.TestCase):
    """Issue #27 — ``HandleEditorAddComponent`` no longer silently
    discards initial-property application failures. Each ``properties_json``
    entry that fails name resolution, object-reference resolution, or the
    unified value write contributes a diagnostic, and any property failure
    (or a ``properties_json`` parse failure) escalates the response
    severity to ``warning`` while ``success`` stays ``true``.

    Tier 3: ``HandleEditorAddComponent`` operates on a live Unity
    ``SerializedObject`` creatable only inside the Unity Editor; the
    repository has no EditMode harness and the migration table
    classifies the entire Components concern as Tier-3-only. These
    assertions read the comment-stripped bridge source so a literal
    surviving only inside a ``//`` comment cannot mask a reverted fix.
    """

    def _handler_body(self) -> str:
        source = _read(BRIDGE)
        return _extract_method(source, "HandleEditorAddComponent")

    def test_value_write_branch_inspects_the_property_write_outcome(self) -> None:
        body = self._handler_body()
        # The unified write layer returns a ``PropertyWriteResult``; the
        # add-component loop must capture and inspect it rather than
        # discard the call's return value (issue #27 case ③).
        self.assertIn(
            "PropertyWriteResult",
            body,
            "value-write branch does not capture the WritePropertyValue outcome",
        )
        self.assertIn(
            ".Success",
            body,
            "value-write branch does not inspect the PropertyWriteResult success flag",
        )
        self.assertIn(
            "ErrorMessage",
            body,
            "value-write failure diagnostic does not carry the write layer's error text",
        )

    def test_failed_entry_emits_a_per_entry_diagnostic(self) -> None:
        source = _read(BRIDGE)
        # All three failure causes funnel into a diagnostic whose
        # ``location`` names the offending ``properties_json`` entry
        # (``properties_json[<name>]``).
        self.assertIn(
            "properties_json[",
            source,
            "a failed initial-property entry is not surfaced with a per-entry diagnostic location",
        )

    def test_property_failure_escalates_response_severity_to_warning(self) -> None:
        body = self._handler_body()
        # A property or parse failure escalates the response severity to
        # the warning level; the escalation is gated on the collected
        # per-entry/parse diagnostics, and ``success`` is left unchanged.
        self.assertIn(
            'severity = "warning"',
            body,
            "a failed initial-property entry does not escalate response severity to warning",
        )
        self.assertIn(
            "diagList.Count",
            body,
            "severity escalation is not gated on the collected-diagnostics count",
        )

    def test_parse_failure_diagnostic_feeds_the_severity_gate(self) -> None:
        body = self._handler_body()
        # Issue #27 case ④: a malformed ``properties_json`` payload is
        # caught and must add a diagnostic to the same ``diagList`` the
        # ``diagList.Count > 0`` severity gate inspects, so the parse
        # failure escalates the response severity to ``warning`` rather
        # than being discarded silently.
        match = re.search(r"catch\s*\(\s*System\.Exception\s+ex\s*\)\s*\{", body)
        self.assertIsNotNone(
            match,
            "properties_json parse catch block not found in HandleEditorAddComponent",
        )
        if match is None:
            self.fail("properties_json parse catch block not found in HandleEditorAddComponent")
        catch_body = _extract_braced_block(
            body, match.end(), "properties_json parse catch block"
        )
        self.assertIn(
            "diagList.Add",
            catch_body,
            "parse catch does not append to diagList; the parse failure is discarded",
        )
        self.assertIn(
            'evidence = "properties_json"',
            catch_body,
            "parse-failure diagnostic does not carry the properties_json evidence tag",
        )


def _extract_class_body(source: str, class_name: str) -> str:
    """Return the brace-delimited body of a named C# class."""
    match = re.search(
        rf"class\s+{re.escape(class_name)}\b[^{{]*{{", source
    )
    if match is None:
        raise AssertionError(f"class {class_name} not found in source")
    return _extract_braced_block(source, match.end(), f"class {class_name}")


class TestReparentDedicatedWireField(unittest.TestCase):
    """Issue #56 — the reparent argument travels on a dedicated
    EditorControlRequest field, not the rename field.

    Tier 3: HandleEditorSetParent and the request DTO are Unity-runtime
    types not compiled by CI or the xUnit harness; this comment-stripped
    source scan pins the field rename. Runtime reparent behaviour is
    verified by the mandatory deploy_bridge pass (observations.md).
    """

    def test_request_declares_parent_hierarchy_path_field(self) -> None:
        source = _strip_cs_comments(
            EDITOR_CONTROL_REQUEST.read_text(encoding="utf-8")
        )
        self.assertRegex(
            source,
            r"public\s+string\s+parent_hierarchy_path\s*=",
            msg=(
                "EditorControlRequest must declare a dedicated "
                "parent_hierarchy_path field for the reparent address "
                "(issue #56)."
            ),
        )

    def test_set_parent_handler_reads_dedicated_field(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleEditorSetParent")
        self.assertIn(
            "request.parent_hierarchy_path",
            body,
            msg=(
                "HandleEditorSetParent must read the parent address from "
                "request.parent_hierarchy_path (issue #56)."
            ),
        )

    def test_set_parent_handler_does_not_read_rename_field(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleEditorSetParent")
        self.assertNotIn(
            "request.new_name",
            body,
            msg=(
                "HandleEditorSetParent must not fall back to the rename "
                "field request.new_name for the parent address "
                "(issue #56 Non-Goal)."
            ),
        )


class TestDispatchExceptionActionField(unittest.TestCase):
    """Issue #51 — the EDITOR_CTRL_HANDLER_EXCEPTION envelope carries the
    dispatched action as a structured response-payload field.

    Tier 3: the dispatch boundary runs inside the Unity Editor process
    and is not xUnit-compiled; this comment-stripped scan pins the field
    declaration and the catch-path population. Runtime serialization is
    verified by the mandatory deploy_bridge pass (observations.md).
    """

    def test_response_payload_declares_action_field(self) -> None:
        body = _extract_class_body(_read(BRIDGE), "EditorControlData")
        self.assertRegex(
            body,
            r"public\s+string\s+action\s*=",
            msg=(
                "EditorControlData must declare a structured action "
                "field so the dispatch-boundary catch can populate it "
                "(issue #51)."
            ),
        )

    def test_dispatch_catch_populates_action_field(self) -> None:
        body = _extract_method(_read(BRIDGE), "RunFromPaths")
        self.assertIn(
            "EDITOR_CTRL_HANDLER_EXCEPTION",
            body,
            msg="RunFromPaths must build the handler-exception envelope.",
        )
        self.assertIn(
            "action = request.action",
            body,
            msg=(
                "the dispatch-boundary catch must set the response "
                "payload's action field to the dispatched action so "
                "callers can branch on it (issue #51)."
            ),
        )


class TestCreateAnimationClipSinglePath(unittest.TestCase):
    """Issue #53 — editor_create_animation_clip takes one asset path and
    the bridge derives directory and filename; the request DTO no longer
    declares the directory/stem fields.

    Tier 3: HandleCreateAnimationClip and the request DTO are
    Unity-runtime types; this comment-stripped scan pins the wire shape.
    Runtime path-split and .anim enforcement are verified by the
    mandatory deploy_bridge pass (observations.md).
    """

    def test_request_drops_directory_and_stem_clip_fields(self) -> None:
        source = _strip_cs_comments(
            EDITOR_CONTROL_REQUEST.read_text(encoding="utf-8")
        )
        self.assertNotRegex(
            source,
            r"public\s+string\s+target_dir\s*=",
            msg=(
                "EditorControlRequest must no longer declare the "
                "directory-form target_dir clip field (issue #53)."
            ),
        )
        self.assertNotRegex(
            source,
            r"public\s+string\s+animation_clip_name\s*=",
            msg=(
                "EditorControlRequest must no longer declare the "
                "stem-form animation_clip_name clip field (issue #53)."
            ),
        )

    def test_clip_handler_reads_single_asset_path(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCreateAnimationClip")
        self.assertIn(
            "request.asset_path",
            body,
            msg=(
                "HandleCreateAnimationClip must read the single "
                "request.asset_path field (issue #53)."
            ),
        )

    def test_clip_handler_does_not_read_directory_or_stem_fields(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCreateAnimationClip")
        self.assertNotIn(
            "request.target_dir",
            body,
            msg=(
                "HandleCreateAnimationClip must not read the removed "
                "request.target_dir field (issue #53)."
            ),
        )
        self.assertNotIn(
            "request.animation_clip_name",
            body,
            msg=(
                "HandleCreateAnimationClip must not read the removed "
                "request.animation_clip_name field (issue #53)."
            ),
        )

    def test_clip_handler_enforces_anim_extension(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleCreateAnimationClip")
        self.assertIn(
            ".anim",
            body,
            msg=(
                "HandleCreateAnimationClip must enforce the .anim "
                "extension on the supplied asset path (issue #53)."
            ),
        )


class TestRunScriptPollSurfacesCompileDiagnostics(unittest.TestCase):
    """Issue #68 — a compile-failed run_script_submit records the real
    compiler diagnostics in the completion artefact; HandleRunScriptPoll
    copies them onto the outer poll response so a failed poll surfaces
    why the snippet failed.
    """

    def test_poll_copies_completion_artifact_errors(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScriptPoll")
        self.assertIn(
            "inner.data.errors",
            body,
            msg=(
                "#68: HandleRunScriptPoll must copy the completion "
                "artefact's data.errors onto the poll response so a "
                "failed poll carries the compiler diagnostics."
            ),
        )


class TestRunScriptPollFailureEnvelopeSource(unittest.TestCase):
    def test_failed_poll_preserves_inner_failure_envelope(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScriptPoll")
        self.assertIn(
            "success = inner.success",
            body,
            msg="Async poll completion must preserve failed inner success state.",
        )
        self.assertIn(
            "severity = inner.severity",
            body,
            msg="Async poll completion must preserve failed inner severity.",
        )
        self.assertIn(
            "? \"EDITOR_CTRL_RUN_SCRIPT_POLL_COMPLETED\"\n                                : inner.code",
            body,
            msg="Failed async poll completion must keep the runtime/compile error code.",
        )
        self.assertIn(
            "exception = inner.data.exception",
            body,
            msg="Failed async poll completion must keep structured exception payloads.",
        )

    def test_completed_poll_preserves_inner_operator_context(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRunScriptPoll")
        self.assertIn(
            "operator_context = inner.operator_context",
            body,
            msg=(
                "Async poll completion must preserve the completed bridge "
                "operator_context so root verification accepts valid success "
                "responses."
            ),
        )


class TestClientSimSideEffectAssetCandidatesSource(unittest.TestCase):
    def test_clientsim_report_uses_snapshot_asset_candidates(self) -> None:
        source = _read(TOOLS_DIR / "PrefabSentinel.UnityRuntimeValidationBridge.cs")
        body = _extract_method(source, "BuildSideEffectReport")
        self.assertIn(
            "asset_change_candidates = Difference(\n                    after?.AssetChangeCandidates,\n                    before?.AssetChangeCandidates)",
            body,
            msg="ClientSim side-effect report must serialize observed asset candidates.",
        )

    def test_clientsim_snapshot_collects_dirty_asset_candidates(self) -> None:
        source = _read(TOOLS_DIR / "PrefabSentinel.UnityRuntimeValidationBridge.cs")
        snapshot_body = _extract_method(source, "CaptureSceneSnapshot")
        collect_body = _extract_method(source, "DirtyAssetChangeCandidates")
        self.assertIn(
            "AssetChangeCandidates = DirtyAssetChangeCandidates()",
            snapshot_body,
            msg="ClientSim snapshots must capture asset candidates before diffing.",
        )
        self.assertIn(
            "DirtyScenePaths()",
            collect_body,
            msg="Dirty scene paths must be included as asset-change candidates.",
        )
        self.assertIn(
            "DirtyAssetPaths()",
            collect_body,
            msg="Dirty project assets must be included as asset-change candidates.",
        )


class TestCompileAwareRefreshWiring(unittest.TestCase):
    """Issue #70 — source-text invariants for the compile-aware
    ``editor_refresh`` wiring.

    Tier 3 (spec Tier 3 Justification): the refresh handler, dispatcher,
    and resumer are Unity-dependent partials CI does not compile; the
    three-outcome runtime behaviour is verified by the real-Unity refresh
    matrix recorded in observations.md.
    """

    _DOCS = Path(__file__).resolve().parent.parent / "docs"

    def test_refresh_handler_is_compile_aware_through_the_barrier(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRefreshAssetDatabase")
        self.assertIn("request.wait_for_compile", body)
        self.assertIn("ScheduleCompileBarrier", body)

    def test_refresh_handler_injects_timeout_and_schedule_failure_codes(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleRefreshAssetDatabase")
        for code in (
            "EDITOR_CTRL_REFRESH_COMPILE_FAILED",
            "EDITOR_CTRL_REFRESH_COMPILE_TIMEOUT",
            "EDITOR_CTRL_REFRESH_SCHEDULE_FAILED",
        ):
            with self.subTest(code=code):
                self.assertIn(code, body)

    def test_refresh_codes_catalogued_in_api_reference(self) -> None:
        api = (self._DOCS / "api-reference.md").read_text(encoding="utf-8")
        for code in (
            "EDITOR_CTRL_REFRESH_COMPILE_SUCCESS",
            "EDITOR_CTRL_REFRESH_COMPILE_FAILED",
            "EDITOR_CTRL_REFRESH_COMPILE_TIMEOUT",
            "EDITOR_CTRL_REFRESH_SCHEDULE_FAILED",
        ):
            with self.subTest(code=code):
                self.assertIn(
                    code,
                    api,
                    f"docs/api-reference.md must catalogue {code}",
                )

    def test_refresh_action_is_async_capable(self) -> None:
        literal = _action_registry_hashset("Async")
        self.assertIn('"refresh_asset_database"', literal)

    def test_dispatch_passes_response_path_to_refresh_handler(self) -> None:
        body = _extract_method(_read(BRIDGE), "DispatchAction")
        self.assertRegex(
            body,
            r"HandleRefreshAssetDatabase\(\s*request\s*,\s*responsePath\s*\)",
        )

    def test_resumer_has_compile_aware_refresh_branch(self) -> None:
        body = _extract_method(_read(BRIDGE), "ResumePendingAsyncRunners")
        self.assertIn('"refresh_asset_database"', body)


class TestFireAndReturnRecompileActionAbsent(unittest.TestCase):
    """Issue #71 — the fire-and-return recompile action, its handler, and
    the reimport-diagnostic redaction helper are gone from the bridge.
    """

    def test_recompile_scripts_absent_from_action_registry(self) -> None:
        supported = _action_registry_hashset("Supported")
        async_set = _action_registry_hashset("Async")
        self.assertNotIn('"recompile_scripts"', supported)
        self.assertNotIn('"recompile_scripts"', async_set)

    def test_recompile_scripts_absent_from_dispatch_switch(self) -> None:
        body = _extract_method(_read(BRIDGE), "DispatchAction")
        self.assertNotIn('"recompile_scripts"', body)

    def test_fire_and_return_handler_is_absent(self) -> None:
        source = _read(BRIDGE)
        self.assertNotIn(
            "HandleRecompileScripts",
            source,
            msg="#71: the fire-and-return recompile handler must be removed",
        )

    def test_reimport_diagnostic_helper_is_absent(self) -> None:
        redaction = _strip_cs_comments(
            RUN_SCRIPT_COMPILE_REDACTION.read_text(encoding="utf-8")
        )
        self.assertNotIn(
            "ReimportDiagnostic",
            redaction,
            msg=(
                "#71: the reimport-diagnostic redaction helper, referenced "
                "only by the retired handler, must be removed"
            ),
        )


class TestSetCameraGeometrySource(unittest.TestCase):
    """Issues #66 / #73 / #74 — source-text invariants for ``HandleSetCamera``
    Scene-view camera accuracy.

    * #66: the perspective position-mode size↔camera-distance conversion is
      sine-based, matching Unity's ``SceneView.GetPerspectiveCameraDistance``
      contract (``cameraDistance = size / Sin(fov/2)``); a tangent conversion
      re-introduces the landing offset.
    * #73: the orthographic-projection switch is applied ahead of the
      field-of-view read and the position/pivot geometry, so a single call
      that both switches projection and positions the camera computes its
      geometry under the requested projection.
    * #74: ``editor_set_camera`` responses report the camera world position
      through the synchronous resolver, not a raw transform read that
      reflects the pre-call position.
    """

    def test_perspective_conversion_is_sine_based(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleSetCamera")
        self.assertNotIn(
            "Mathf.Tan",
            body,
            msg=(
                "#66: HandleSetCamera must not use Mathf.Tan for the "
                "size<->camera-distance conversion — the tangent form "
                "re-introduces the perspective landing offset."
            ),
        )
        # The position-only and the position-with-look-at branches each
        # convert size<->camera-distance once; both must use Mathf.Sin.
        self.assertEqual(
            2,
            body.count("Mathf.Sin"),
            msg=(
                "#66: both the position-only and the look-at conversions "
                "in HandleSetCamera must use the sine-based form"
            ),
        )

    def test_projection_switch_precedes_geometry(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleSetCamera")
        switch_idx = body.index("request.camera_orthographic >= 0")
        # Anchor on the main-path ``float fov =`` declaration: the reset
        # branch passes ``sceneView.camera.fieldOfView`` as an argument
        # earlier in the method (#74 synced-position resolver), so a bare
        # substring search would match that unrelated read first.
        fov_idx = body.index("float fov = sceneView.camera.fieldOfView")
        self.assertLess(
            switch_idx,
            fov_idx,
            msg=(
                "#73: the orthographic-projection switch must be applied "
                "ahead of the field-of-view read and the position/pivot "
                "geometry so a switch-and-position call computes geometry "
                "under the requested projection"
            ),
        )

    def test_set_camera_responses_resolve_position_synchronously(self) -> None:
        body = _extract_method(_read(BRIDGE), "HandleSetCamera")
        # Both response paths — the reset path and the main path — must
        # obtain the reported camera position from the synchronous
        # resolver rather than the post-call transform read.
        self.assertEqual(
            2,
            body.count("ResolveSyncedCameraPosition"),
            msg=(
                "#74: both editor_set_camera response paths (reset and "
                "main) must resolve the reported camera position through "
                "ResolveSyncedCameraPosition"
            ),
        )

    def test_sync_position_resolver_derives_from_view_state(self) -> None:
        body = _extract_method(_read(BRIDGE), "ResolveSyncedCameraPosition")
        # #73/#74: the resolver must derive the camera distance from the
        # synchronously-settled size + projection, NOT read
        # SceneView.cameraDistance. That property is transiently invalid
        # across a same-call projection switch — it evaluates the
        # sine-based perspective distance against a field-of-view still
        # mid-transition, blowing the reported position up to a
        # near-divide-by-zero value.
        self.assertNotIn(
            "sv.cameraDistance",
            body,
            msg=(
                "#73/#74: the resolver must not read "
                "SceneView.cameraDistance — it is transiently invalid "
                "across a projection switch; derive the distance from "
                "size + orthographic + fov instead"
            ),
        )
        self.assertIn("sv.size", body)
        self.assertIn("sv.orthographic", body)
        self.assertIn("sv.pivot", body)
        self.assertIn("sv.rotation", body)


class SetCameraProjectionTransitionGuardSourceTests(unittest.TestCase):
    def _handle_set_camera_body(self) -> str:
        return _extract_method(_read(BRIDGE), "HandleSetCamera")

    def _position_branch_body(self) -> str:
        body = self._handle_set_camera_body()
        match = re.search(r"if \(hasPosition\)\s*\{", body)
        self.assertIsNotNone(
            match,
            msg="HandleSetCamera must retain a position-mode branch.",
        )
        match = require_not_none(match, "HandleSetCamera hasPosition branch")
        return _extract_braced_block(body, match.end(), "HandleSetCamera hasPosition branch")

    def test_position_mode_checks_projection_stability_before_fov_geometry(self) -> None:
        body = self._handle_set_camera_body()
        position_match = re.search(r"if \(hasPosition\)\s*\{", body)
        self.assertIsNotNone(position_match, msg="HandleSetCamera must retain position mode.")
        position_match = require_not_none(position_match, "HandleSetCamera position mode")
        position_body = self._position_branch_body()
        guard_index = position_body.find("ProjectionStateStability.IsStableForPositionMode")
        sin_index = position_body.find("Mathf.Sin")
        look_at_index = position_body.find("sceneView.LookAt")
        self.assertNotEqual(
            -1,
            guard_index,
            msg="Position mode must call ProjectionStateStability.IsStableForPositionMode.",
        )
        self.assertLess(
            guard_index,
            sin_index,
            msg="Projection stability must be checked before position-mode fov geometry.",
        )
        self.assertLess(
            guard_index,
            look_at_index,
            msg="Projection stability must be checked before position-mode LookAt mutation.",
        )
        self.assertNotIn(
            "ProjectionStateStability.IsStableForPositionMode",
            body[: position_match.start()],
            msg="Reset/conflict/projection-switch setup must not reject non-position modes.",
        )

    def test_unstable_position_mode_returns_transition_error_before_lookat(self) -> None:
        position_body = self._position_branch_body()
        guard_index = position_body.find("!ProjectionStateStability.IsStableForPositionMode")
        restore_index = position_body.find("RestoreSceneViewCameraState(previous);")
        transition_index = position_body.find("EDITOR_CTRL_CAMERA_PROJECTION_TRANSITION")
        look_at_index = position_body.find("sceneView.LookAt")
        self.assertNotEqual(
            -1,
            guard_index,
            msg="Transition rejection must be guarded by a false stability classifier result.",
        )
        self.assertNotEqual(
            -1,
            restore_index,
            msg="Rejected projection-transition calls must restore the captured SceneView state.",
        )
        self.assertNotEqual(
            -1,
            transition_index,
            msg="Unstable position mode must return EDITOR_CTRL_CAMERA_PROJECTION_TRANSITION.",
        )
        self.assertLess(
            guard_index,
            restore_index,
            msg="State restoration must live inside the classifier-false branch.",
        )
        self.assertLess(
            restore_index,
            transition_index,
            msg="SceneView state must be restored before the transition diagnostic returns.",
        )
        self.assertLess(
            transition_index,
            look_at_index,
            msg="Transition diagnostic must return before position-mode LookAt is applied.",
        )

    def test_projection_guard_uses_public_state_without_reflection_or_wait(self) -> None:
        sources = (
            self._handle_set_camera_body()
            + _read(TOOLS_DIR / "PrefabSentinel.Camera.ProjectionStateStability.cs")
        )
        for forbidden in (
            "m_Ortho",
            "perspectiveFov",
            "BindingFlags",
            "GetField",
            "EditorApplication.delayCall",
            "EditorApplication.update",
            "Thread.Sleep",
            "Task.Delay",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, sources)


class TestGenericCollectionUsingDirective(unittest.TestCase):
    """Issue #84 follow-up — every bridge ``.cs`` file that references a
    generic collection type from ``System.Collections.Generic``
    (``List<>`` / ``IList<>`` / ``Dictionary<>`` …) must also declare
    ``using System.Collections.Generic;``.

    The original target-oriented screenshot port shipped ``IList<>`` and
    ``List<>`` references in ``PrefabSentinel.UnityEditorControlBridge
    .Screenshot.cs`` without that using directive. CI compiles neither
    the ``tools/unity/`` Unity-dependent ``.cs`` files (no Unity
    reference assemblies in CI per AGENTS.md §"Bridge C# コンパイル
    検証") nor the xUnit-hosted ``tests/csharp`` mirror that excludes
    them, so the resulting ``CS0246`` only surfaced when the bridge was
    deployed into a real Unity project. This text-level invariant
    closes that gap statically.
    """

    # Negative lookbehind ``(?<!\.)`` rejects fully-qualified usages
    # like ``System.Collections.Generic.List<...>`` which resolve
    # without the using directive (see PrefabSentinel.VRCSDKUploadHandler
    # .cs for an in-tree example) — only the *short* unqualified form
    # requires the directive to compile.
    GENERIC_TYPE_REGEX = re.compile(
        r"(?<!\.)\b(?:List|IList|IReadOnlyList|Dictionary|IDictionary|HashSet|"
        r"Queue|Stack|IEnumerable|IReadOnlyCollection|ICollection)<"
    )
    USING_DIRECTIVE = "using System.Collections.Generic;"

    def test_bridge_files_referencing_generic_collections_declare_the_using(
        self,
    ) -> None:
        offenders: list[str] = []
        for path in sorted(TOOLS_DIR.glob("PrefabSentinel*.cs")):
            text = path.read_text(encoding="utf-8")
            if self.GENERIC_TYPE_REGEX.search(text) and (
                self.USING_DIRECTIVE not in text
            ):
                offenders.append(path.name)
        self.assertEqual(
            [],
            offenders,
            msg=(
                "Bridge C# files reference generic collection types but miss "
                f"`{self.USING_DIRECTIVE}`: {offenders}. Without the "
                "directive, Unity rejects the file at deploy time (CS0246)."
            ),
        )


class TestUnityBridgeCSharpLanguageVersionSource(unittest.TestCase):
    """Bridge sources must stay within Unity Editor's supported C# syntax.

    PR #109 introduced a C# 10 file-scoped namespace in a bridge helper.
    The dotnet test mirror compiled with ``LangVersion=latest`` and missed
    it, but Unity rejected the deployed file with CS8773 under C# 9.0.
    """

    FILE_SCOPED_NAMESPACE_REGEX = re.compile(
        r"(?m)^\s*namespace\s+[A-Za-z_][A-Za-z0-9_.]*\s*;"
    )

    def test_bridge_files_do_not_use_file_scoped_namespaces(self) -> None:
        offenders: list[str] = []
        for path in sorted(TOOLS_DIR.glob("*.cs")):
            text = _strip_cs_comments(path.read_text(encoding="utf-8"))
            if self.FILE_SCOPED_NAMESPACE_REGEX.search(text):
                offenders.append(path.name)

        self.assertEqual(
            [],
            offenders,
            msg=(
                "Bridge C# files use file-scoped namespaces: "
                f"{offenders}. Unity compiles the bridge with C# 9.0 in "
                "supported projects, so C# 10 namespace syntax fails at "
                "deploy time (CS8773). Use block-scoped namespaces instead."
            ),
        )

    def test_bridge_files_do_not_use_init_only_accessors(self) -> None:
        init_only_accessor = re.compile(r"\binit\s*;")
        offenders: list[str] = []
        for path in sorted(TOOLS_DIR.glob("*.cs")):
            text = _strip_cs_comments(path.read_text(encoding="utf-8"))
            if init_only_accessor.search(text):
                offenders.append(path.name)

        self.assertEqual(
            [],
            offenders,
            msg=(
                "Bridge C# files use init-only accessors: "
                f"{offenders}. Unity's supported .NET profile does not "
                "provide System.Runtime.CompilerServices.IsExternalInit, "
                "so these fail at deploy time (CS0518). Use set/private set "
                "accessors instead."
            ),
        )

    def test_run_script_result_channels_avoids_nullable_reference_annotations(self) -> None:
        source = _strip_cs_comments(
            (TOOLS_DIR / "PrefabSentinel.RunScript.ResultChannels.cs").read_text(
                encoding="utf-8"
            )
        )
        nullable_reference_annotation = re.compile(
            r"\b(?:object|string|RunScriptValue|List<RunScriptOutputEntry>)\s*\?"
        )
        self.assertNotRegex(
            source,
            nullable_reference_annotation,
            msg=(
                "RunScript result-channel bridge code must not use nullable "
                "reference annotations without a nullable annotations context; "
                "Unity reports those as CS8632 warnings at deploy time."
            ),
        )

    def test_run_script_result_channels_declares_nullable_disabled_context(self) -> None:
        source = (TOOLS_DIR / "PrefabSentinel.RunScript.ResultChannels.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "#nullable disable",
            source,
            msg=(
                "RunScript result-channel bridge code intentionally uses "
                "nullable-disabled C# so both Unity's default context and the "
                "nullable-enabled dotnet mirror compile without nullable noise."
            ),
        )


if __name__ == "__main__":
    unittest.main()
