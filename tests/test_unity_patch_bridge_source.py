"""Source-text invariant tests for the UnityPatchBridge partial layout.

Issue #129 split the patch bridge into a canonical core source plus eight
per-concern partials.  These tests enforce the layout itself:

* every documented partial source is on disk;
* every partial declares exactly one ``public static partial class
  UnityPatchBridge`` (and none declare it as a non-partial class);
* the canonical core source declares the two cross-partial anchors —
  the public ``ProtocolVersion`` constant and the ``[ThreadStatic]``
  per-request handle slot — so external callers and shared state stay
  bound to the canonical file;
* the operational rules file (``AGENTS.md``) lists every per-concern
  partial token currently on disk.

The patch bridge is C# whose behavioural contract is exercised by the
Unity-gated integration suite (``tests/test_unity_patch_bridge.py``);
these tests are the source-text safety net for the structural split.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import pytest

from tests._typing_helpers import require_not_none

# The patch-bridge layout invariants are read-only inspections of the
# un-mutated ``tools/unity`` tree; they cannot observe mutations applied
# to ``prefab_sentinel/`` so they are excluded from the mutmut run via
# the project-wide ``source_text_invariant`` filter.
pytestmark = pytest.mark.source_text_invariant


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _PROJECT_ROOT / "tools" / "unity"
_AGENTS_MD = _PROJECT_ROOT / "AGENTS.md"


# C# block comments may not nest, so a single non-greedy ``/* ... */`` scrub
# followed by an end-of-line ``// ...`` scrub is sufficient.  The helper is
# applied before ``assertNotRegex`` on non-canonical partials so that a
# stale anchor copy quoted inside a future comment (e.g.
# ``// was: public const int ProtocolVersion = 2;``) does not trigger a
# false-positive failure that would block fixes unrelated to the anchor.
_CS_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_CS_LINE_COMMENT_RE = re.compile(r"//[^\n]*")


def _strip_cs_comments(source: str) -> str:
    return _CS_LINE_COMMENT_RE.sub("", _CS_BLOCK_COMMENT_RE.sub("", source))


def _extract_method(source: str, method_name: str) -> str:
    signature = (
        r"(private|internal|public)\s+static\s+"
        r"(?:async\s+)?\S+(?:\s*<[^>]+>)?\s+"
        rf"{re.escape(method_name)}(?:\s*<[^>]+>)?\s*\("
    )
    pattern = re.compile(signature)
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

# Canonical core source name — the entry points (``ApplyFromJson`` and
# ``ApplyFromPaths``) and the public ``ProtocolVersion`` constant live
# here; external callers in ``PrefabSentinel.EditorBridge.cs`` and the
# integration harness bind to this file's CLR symbols.
_CORE = "PrefabSentinel.UnityPatchBridge.cs"

# Per-concern partial inventory (issue #129).  These names are
# duplicated in the operational rules file and in the deploy-list
# constant in ``prefab_sentinel/integration_tests.py``; the test in
# ``TestPatchBridgeOperationalRulesInventory`` enforces the rules-file
# inventory alignment, and ``DeployTests.test_deploy_list_includes_every_patch_bridge_partial``
# enforces the deploy-list alignment.
_EXPECTED_PARTIAL_NAMES = (
    _CORE,
    "PrefabSentinel.UnityPatchBridge.Payloads.cs",
    "PrefabSentinel.UnityPatchBridge.Prefab.cs",
    "PrefabSentinel.UnityPatchBridge.Asset.cs",
    "PrefabSentinel.UnityPatchBridge.Scene.cs",
    "PrefabSentinel.UnityPatchBridge.Resolve.cs",
    "PrefabSentinel.UnityPatchBridge.Mutation.cs",
    "PrefabSentinel.UnityPatchBridge.ManagedReference.cs",
    "PrefabSentinel.UnityPatchBridge.Diagnostics.cs",
)

_PARTIAL_GLOB = "PrefabSentinel.UnityPatchBridge*.cs"


class TestPatchBridgePartialLayout(unittest.TestCase):
    """The on-disk set of patch-bridge partial sources equals the
    documented inventory.  A partial silently dropped from disk (or a
    new partial added without inventory update) is caught here before
    the next Unity recompile would catch the CS0103 / CS0246 produced
    downstream.
    """

    def test_on_disk_partial_set_equals_documented_inventory(self) -> None:
        on_disk = sorted(p.name for p in _TOOLS_DIR.glob(_PARTIAL_GLOB))
        expected = sorted(_EXPECTED_PARTIAL_NAMES)
        self.assertEqual(
            on_disk,
            expected,
            "Patch-bridge partial inventory drift: on-disk set does "
            "not match the documented inventory.",
        )


class TestPatchBridgePartialDeclaresPartialClass(unittest.TestCase):
    """Every patch-bridge source declares exactly one
    ``public static partial class UnityPatchBridge`` and no source
    declares the class as a non-partial.  A typo dropping the
    ``partial`` keyword would surface only at the next Unity recompile
    as CS0101 (duplicate definition); this test catches the drift at
    source-text level so the deploy-list integration step does not need
    to be the first observer.
    """

    def test_every_partial_declares_exactly_one_partial_class(self) -> None:
        for name in _EXPECTED_PARTIAL_NAMES:
            with self.subTest(name=name):
                text = _strip_cs_comments(
                    (_TOOLS_DIR / name).read_text(encoding="utf-8")
                )
                hits = re.findall(
                    r"public\s+static\s+partial\s+class\s+UnityPatchBridge\b",
                    text,
                )
                self.assertEqual(
                    1,
                    len(hits),
                    f"{name}: expected exactly 1 partial-class "
                    f"declaration, got {len(hits)}",
                )

    def test_no_partial_declares_the_class_as_non_partial(self) -> None:
        for name in _EXPECTED_PARTIAL_NAMES:
            with self.subTest(name=name):
                text = _strip_cs_comments(
                    (_TOOLS_DIR / name).read_text(encoding="utf-8")
                )
                self.assertNotRegex(
                    text,
                    r"public\s+static\s+class\s+UnityPatchBridge\b",
                    f"{name}: must use partial class, not plain class",
                )


class TestPatchBridgeCoreConstantsPresent(unittest.TestCase):
    """The canonical core source is the single declaration site for
    cross-partial anchors.  ``ProtocolVersion`` is referenced from
    ``PrefabSentinel.EditorBridge.cs`` as a compile-time constant; the
    per-request ``[ThreadStatic]`` handle slot is read by the value-reader
    layer (mutation partial) and written by the scene-create dispatcher
    (scene partial), so both anchors must remain colocated with the
    canonical core's entry-point dispatch.
    """

    def _core_text(self) -> str:
        # Issue #5/#358: strip C# comments before the source greps below
        # so a quoted anchor in a ``//`` comment cannot satisfy a
        # declaration-site assertion.
        return _strip_cs_comments((_TOOLS_DIR / _CORE).read_text(encoding="utf-8"))

    def test_protocol_version_constant_declared_in_canonical_core(self) -> None:
        text = self._core_text()
        # The value is itself part of the contract — the cross-language
        # drift checker reads request payloads with this number — so the
        # test pins the literal.
        self.assertRegex(
            text,
            r"public\s+const\s+int\s+ProtocolVersion\s*=\s*2\s*;",
            "Canonical core source must declare 'public const int "
            "ProtocolVersion = 2;'.",
        )
        # The constant must NOT be redeclared in any other partial: a
        # duplicate declaration would compile (partials share the
        # namespace) but split the documented single-declaration site.
        # Comment regions are stripped first so that a quoted-anchor
        # comment (e.g. ``// was: public const int ProtocolVersion = 2;``)
        # does not register as a redeclaration.
        for name in (n for n in _EXPECTED_PARTIAL_NAMES if n != _CORE):
            with self.subTest(name=name):
                other_text = _strip_cs_comments(
                    (_TOOLS_DIR / name).read_text(encoding="utf-8")
                )
                self.assertNotRegex(
                    other_text,
                    r"public\s+const\s+int\s+ProtocolVersion\b",
                    f"{name}: ProtocolVersion must live only in the "
                    f"canonical core source.",
                )

    def test_thread_static_handle_slot_declared_in_canonical_core(self) -> None:
        text = self._core_text()
        # The slot must carry the ``[ThreadStatic]`` attribute and use
        # the documented field name ``s_currentHandles``; the field is
        # the only shared mutable state between the scene-create
        # dispatcher and the value-reader layer.
        self.assertRegex(
            text,
            r"\[ThreadStatic\]\s+private\s+static\s+Dictionary<\s*string\s*,\s*UnityEngine\.Object\s*>\s+s_currentHandles\s*;",
            "Canonical core source must declare the '[ThreadStatic] "
            "private static Dictionary<string, UnityEngine.Object> "
            "s_currentHandles;' slot.",
        )
        # As with ProtocolVersion, the slot must not be redeclared in
        # any other partial.  Comment regions are stripped first so that
        # a quoted-anchor comment does not register as a redeclaration.
        for name in (n for n in _EXPECTED_PARTIAL_NAMES if n != _CORE):
            with self.subTest(name=name):
                other_text = _strip_cs_comments(
                    (_TOOLS_DIR / name).read_text(encoding="utf-8")
                )
                self.assertNotRegex(
                    other_text,
                    r"\[ThreadStatic\][\s\S]{0,200}s_currentHandles\s*;",
                    f"{name}: s_currentHandles must live only in the "
                    f"canonical core source.",
                )


class TestPatchBridgeOperationalRulesInventory(unittest.TestCase):
    """``AGENTS.md`` is the single human-readable source of truth for
    the partial layout.  Every per-concern token currently on disk must
    appear in the operational rules file; absent tokens advertised in
    the rules file would describe a layout that no longer matches disk.
    """

    def _disk_partial_concerns(self) -> set[str]:
        """Return the per-concern token (e.g. ``Payloads``,
        ``Prefab``) for every per-concern patch-bridge partial currently
        on disk.  The canonical core file (no concern segment) is
        excluded because it carries no per-concern token to advertise.
        """
        concerns: set[str] = set()
        head = "PrefabSentinel.UnityPatchBridge"
        for path in _TOOLS_DIR.glob(_PARTIAL_GLOB):
            stem = path.stem
            if stem == head:
                continue
            assert stem.startswith(head + "."), stem
            concerns.add(stem[len(head) + 1:])
        return concerns

    def test_operational_rules_list_every_present_partial_concern(self) -> None:
        text = _AGENTS_MD.read_text(encoding="utf-8")
        for concern in sorted(self._disk_partial_concerns()):
            with self.subTest(concern=concern):
                self.assertIn(
                    concern,
                    text,
                    f"AGENTS.md inventory line is missing patch-bridge "
                    f"concern '{concern}'.",
                )


class TestPatchSelectorNResolverDelegation(unittest.TestCase):
    """Issue #38 (T-38-6): the ``TypeName@/hierarchy/path`` patch
    selector routes ``#N`` token resolution through the shared Unity-free
    ``SymbolPathResolver``.

    Tier 3 (spec.md Tier 3 Justification T-38-6): the patch selector
    matching runs against live ``Component`` collections inside the
    Unity process and is not xUnit-compiled; the ``#N`` resolution rule
    itself is Tier 1-covered through the shared Unity-free resolver
    (T-38-c2 / T-38-3 / T-38-4).  This source-scan pins only the
    delegation to the shared resolver — a third independent ``#N``
    matcher drifting from the resolver is the failure mode caught.
    """

    _RESOLVE_PARTIAL = _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Resolve.cs"

    def _resolve_source(self) -> str:
        return _strip_cs_comments(
            self._RESOLVE_PARTIAL.read_text(encoding="utf-8")
        )

    def test_selector_resolution_delegates_to_shared_resolver(self) -> None:
        source = self._resolve_source()
        self.assertIn(
            "SymbolPathResolver.Resolve",
            source,
            msg=(
                "the patch selector hierarchy resolution must delegate "
                "to the shared Unity-free SymbolPathResolver so the #N "
                "rule does not drift from the offline / live tracks."
            ),
        )

    def test_unique_component_finder_routes_hierarchy_through_resolver(
        self,
    ) -> None:
        # ``TryFindUniqueComponent`` must call the resolver-backed
        # hierarchy resolver rather than re-implementing a path-string
        # equality match for the hierarchy part of the selector.
        match = re.search(
            r"bool\s+TryFindUniqueComponent\s*\(",
            self._resolve_source(),
        )
        self.assertIsNotNone(
            match, msg="TryFindUniqueComponent declaration not found"
        )
        match = require_not_none(match, "TryFindUniqueComponent declaration")
        source = self._resolve_source()
        start = match.start()
        depth = 0
        opened = False
        end = len(source)
        for index in range(start, len(source)):
            ch = source[index]
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth == 0:
                    end = index + 1
                    break
        body = source[start:end]
        self.assertIn(
            "TryResolveHierarchyPathWithResolver",
            body,
            msg=(
                "TryFindUniqueComponent must resolve the selector's "
                "hierarchy part through the resolver-backed helper."
            ),
        )


class TestPrefabApplyRejectionEnvelopeSource(unittest.TestCase):
    """Issue #298 — the prefab apply rejection path declares the
    documented ``SER_APPLY_REJECTED`` code together with the diagnostic
    payload keys ``property_path`` and ``component_type``.

    Post H-track migration the rejection-envelope assembly (the
    ``SER_APPLY_REJECTED`` code and the structured evidence string with
    the three payload keys) was extracted into the Unity-free
    ``PrefabApplyRejectionEnvelope``; that behavioral coverage now lives
    in ``tests/csharp/PrefabApplyRejectionEnvelopeTests.cs``. This
    source-text test retains the Tier 3 delegation invariant (the
    ``BuildPrefabApplyRejectionDiagnostics`` handler routes through
    ``PrefabApplyRejectionEnvelope.Build``) plus constant/field pins on
    the relocated envelope class.
    """

    def _prefab_partial_path(self) -> Path:
        return _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Prefab.cs"

    def _envelope_path(self) -> Path:
        return _TOOLS_DIR / "PrefabSentinel.Prefab.ApplyRejectionEnvelope.cs"

    def test_handler_delegates_to_rejection_envelope(self) -> None:
        text = _strip_cs_comments(
            self._prefab_partial_path().read_text(encoding="utf-8")
        )
        self.assertIn("PrefabApplyRejectionEnvelope.Build", text)

    def test_rejection_envelope_declares_new_code(self) -> None:
        text = _strip_cs_comments(
            self._envelope_path().read_text(encoding="utf-8")
        )
        self.assertIn(
            'RejectedCode = "SER_APPLY_REJECTED"',
            text,
            msg=(
                "PrefabApplyRejectionEnvelope must declare the documented "
                "`SER_APPLY_REJECTED` code (issue #298)."
            ),
        )

    def test_rejection_envelope_carries_property_path_field(self) -> None:
        text = _strip_cs_comments(
            self._envelope_path().read_text(encoding="utf-8")
        )
        # The diagnostic payload's property-path key is named
        # ``property_path`` on the wire (matches the SerializedProperty
        # vocabulary the README error register uses).
        self.assertIn(
            "property_path",
            text,
            msg=(
                "Rejection envelope must surface the affected property "
                "path under the `property_path` payload key (issue #298)."
            ),
        )

    def test_rejection_envelope_carries_component_type_field(self) -> None:
        text = _strip_cs_comments(
            self._envelope_path().read_text(encoding="utf-8")
        )
        self.assertIn(
            "component_type",
            text,
            msg=(
                "Rejection envelope must surface the affected component "
                "type under the `component_type` payload key (issue #298)."
            ),
        )

    def test_rejection_envelope_carries_attempted_value_field(self) -> None:
        text = _strip_cs_comments(
            self._envelope_path().read_text(encoding="utf-8")
        )
        self.assertIn(
            "attempted_value",
            text,
            msg=(
                "Rejection envelope must surface the attempted value "
                "under the `attempted_value` payload key (issue #298)."
            ),
        )


    def test_attempted_value_summary_preserves_null_marker(self) -> None:
        text = _strip_cs_comments(
            self._prefab_partial_path().read_text(encoding="utf-8")
        )
        body = _extract_method(text, "SummarizePatchOpValue")
        self.assertNotIn(
            "op.value_string ?? string.Empty",
            body,
            msg="Prefab rejection summaries must not collapse null string values to empty.",
        )
        self.assertNotIn(
            "op.value_json ?? string.Empty",
            body,
            msg="Prefab rejection summaries must not collapse null json values to empty.",
        )
        self.assertIn(
            'op.value_string == null ? "null" : op.value_string',
            body,
            msg="Null string/handle values must stay visible in rejection summaries.",
        )


class TestFileIdTargetedSetOp(unittest.TestCase):
    """Issue #37 — a patch v2 ``set`` op may identify its target
    component by an exact fileID, resolved through Unity's
    global-object-id facility.

    Tier 3: the patch bridge runs inside the Unity Editor runtime and is
    not xUnit-compiled; this comment-stripped scan pins the resolver and
    the op-target branching. Runtime fileID resolution is verified by
    the mandatory deploy_bridge pass (observations.md).
    """

    _RESOLVE = _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Resolve.cs"
    _MUTATION = _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Mutation.cs"
    _CORE_PATH = _TOOLS_DIR / _CORE

    def test_patch_op_declares_file_id_field(self) -> None:
        text = _strip_cs_comments(self._CORE_PATH.read_text(encoding="utf-8"))
        self.assertRegex(
            text,
            r"public\s+string\s+file_id\s*=",
            msg=(
                "the PatchOp DTO must declare a file_id target field so "
                "a set op can carry an exact fileID (issue #37)."
            ),
        )

    def test_resolve_partial_has_fileid_resolver_using_global_object_id(
        self,
    ) -> None:
        text = _strip_cs_comments(self._RESOLVE.read_text(encoding="utf-8"))
        self.assertIn(
            "TryResolveComponentByFileId",
            text,
            msg=(
                "Resolve.cs must declare a fileID-based component "
                "resolver (issue #37)."
            ),
        )
        self.assertIn(
            "GlobalObjectId",
            text,
            msg=(
                "the fileID component resolver must match a component by "
                "its Unity local fileID via the GlobalObjectId facility "
                "(issue #37)."
            ),
        )

    def test_set_op_target_resolution_branches_on_file_id(self) -> None:
        text = _strip_cs_comments(self._MUTATION.read_text(encoding="utf-8"))
        self.assertIn(
            "op.file_id",
            text,
            msg=(
                "the set-op target resolution must branch on op.file_id "
                "so a fileID target takes the exact-resolution path "
                "(issue #37)."
            ),
        )
        self.assertIn(
            "TryResolveComponentByFileId",
            text,
            msg=(
                "the set-op fileID branch must resolve through "
                "TryResolveComponentByFileId (issue #37)."
            ),
        )


class TestPatchBridgeMutationNullContractSource(unittest.TestCase):
    """Source invariants for Unity-only mutation null-vs-empty contracts."""

    _MUTATION = _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Mutation.cs"

    def _source(self) -> str:
        return _strip_cs_comments(self._MUTATION.read_text(encoding="utf-8"))

    def _method_body(self, method_name: str) -> str:
        return _extract_method(self._source(), method_name)

    def test_required_op_null_is_rejected_before_name_routing(self) -> None:
        body = self._method_body("TryApplyOp")
        null_index = body.find("op.op == null")
        trim_index = body.find("op.op.Trim()")
        null_message_index = body.find('"op is null"')

        self.assertNotEqual(
            -1,
            null_index,
            msg="TryApplyOp must reject null op before routing by operation name.",
        )
        self.assertLess(
            null_index,
            trim_index,
            msg="A required null op must not be normalized into the empty-op path.",
        )
        self.assertNotEqual(
            -1,
            null_message_index,
            msg="Null op must have a distinct schema diagnostic.",
        )
        self.assertNotIn(
            "op.op ?? string.Empty",
            body,
            msg="Required op must not use empty-string fallback routing.",
        )

    def test_set_component_selector_does_not_require_file_id(self) -> None:
        body = self._method_body("TryApplyOp")
        file_id_branch = body.find(
            "isSetOp && op.file_id != null && op.file_id.Trim().Length > 0"
        )
        component_null_branch = body.find("else if (op.component == null)")

        self.assertNotIn(
            "if (isSetOp && op.file_id == null)",
            body,
            msg="Component-targeted set ops must not fail only because file_id is absent.",
        )
        self.assertNotEqual(
            -1,
            file_id_branch,
            msg="Set-op file_id routing must run only when a file_id value is present.",
        )
        self.assertLess(
            file_id_branch,
            component_null_branch,
            msg="Set ops without file_id must continue into component selector routing.",
        )

    def test_value_kind_null_is_rejected_before_assignment_routing(self) -> None:
        assign_body = self._method_body("TryAssignPropertyValue")
        null_index = assign_body.find("op.value_kind == null")
        trim_index = assign_body.find("op.value_kind.Trim()")
        null_message_index = assign_body.find('"value_kind is null"')

        self.assertNotEqual(
            -1,
            null_index,
            msg="TryAssignPropertyValue must reject null value_kind before trimming.",
        )
        self.assertLess(
            null_index,
            trim_index,
            msg="value_kind null must not reach the explicit-empty trim path.",
        )
        self.assertNotEqual(
            -1,
            null_message_index,
            msg="value_kind null must have a distinct contract error.",
        )

        mutation_body = self._method_body("TryApplyMutationOpToObject")
        self.assertNotIn(
            "string.IsNullOrWhiteSpace(op.value_kind)",
            mutation_body,
            msg="Array insert routing must not collapse null value_kind into omitted/empty.",
        )
        self.assertIn(
            "op.value_kind == null || op.value_kind.Trim().Length > 0",
            mutation_body,
            msg="Array insert routing must forward explicit null into TryAssignPropertyValue.",
        )

    def test_value_string_readers_reject_null_before_empty_or_parse_handling(self) -> None:
        source = self._source()
        self.assertNotIn(
            "op.value_string ?? string.Empty",
            source,
            msg="Mutation value_string readers must not collapse JSON null into empty string.",
        )

        for method_name, evidence in (
            ("TryAssignPropertyValue", "string property value_string is null"),
            ("TryReadCharacterValue", "character property value_string is null"),
            ("TryReadIntegerValue", "integer property value_string is null"),
            ("TryReadFloatValue", "float property value_string is null"),
            ("TryReadBoolValue", "boolean property value_string is null"),
            ("TryReadEnumValue", "enum property value_string is null"),
            ("TryReadColorValue", "color property value_string is null"),
        ):
            self.assertIn(
                evidence,
                self._method_body(method_name),
                msg=f"{method_name} must reject explicit null value_string distinctly.",
            )

    def test_json_payload_helpers_reject_null_before_empty_handling(self) -> None:
        for method_name in ("TryDecodeJsonToType", "TryParseJsonPayload"):
            body = self._method_body(method_name)
            null_index = body.find("raw == null")
            empty_index = body.find("string.IsNullOrWhiteSpace(raw)")
            evidence_index = body.find('"value_json is null"')

            self.assertNotEqual(
                -1,
                null_index,
                msg=f"{method_name} must reject null value_json before whitespace checks.",
            )
            self.assertLess(
                null_index,
                empty_index,
                msg=f"{method_name} must not collapse null value_json into empty.",
            )
            self.assertNotEqual(
                -1,
                evidence_index,
                msg=f"{method_name} must expose a distinct value_json null error.",
            )

    def test_type_resolver_null_and_empty_have_distinct_messages(self) -> None:
        body = self._method_body("TryResolveType")
        null_index = body.find("rawTypeName == null")
        trim_index = body.find("rawTypeName.Trim()")
        null_message_index = body.find('"type name is null"')
        empty_message_index = body.find('"type name is empty"')

        self.assertNotEqual(
            -1,
            null_index,
            msg="TryResolveType must reject null type names before trimming.",
        )
        self.assertLess(
            null_index,
            trim_index,
            msg="TryResolveType must not normalize null type names into empty strings.",
        )
        self.assertNotEqual(
            -1,
            null_message_index,
            msg="TryResolveType must report null type names distinctly.",
        )
        self.assertNotEqual(
            -1,
            empty_message_index,
            msg="TryResolveType must retain the explicit-empty type-name error.",
        )

    def test_handle_value_string_is_resolved_raw_so_null_reaches_typed_error(self) -> None:
        body = self._method_body("TryAssignPropertyValue")
        self.assertEqual(
            2,
            body.count("TryResolveHandle(op.value_string, s_currentHandles"),
            msg="ObjectReference and ExposedReference handle paths must pass raw value_string.",
        )
        self.assertNotIn(
            "(op.value_string ?? string.Empty).Trim()",
            body,
            msg="Handle paths must not turn null handles into empty handles before TryResolveHandle.",
        )

    def test_component_routing_forwards_explicit_null_to_typed_resolvers(self) -> None:
        body = self._method_body("TryApplyOp")
        self.assertIn(
            "op.component == null",
            body,
            msg="Component routing must detect explicit null before treating component as absent.",
        )
        self.assertIn(
            "TryFindUniqueComponent(\n                        prefabRoot, op.component",
            body,
            msg="Explicit null component must be forwarded to TryFindUniqueComponent.",
        )

    def test_object_reference_guid_null_is_distinct_from_empty_guid(self) -> None:
        body = self._method_body("TryReadObjectReferenceValue")
        self.assertNotIn(
            "payload.guid ?? string.Empty",
            body,
            msg="ObjectReference guid parsing must not collapse JSON null into empty guid.",
        )
        self.assertIn(
            "ObjectReference value_json guid is null",
            body,
            msg="ObjectReference JSON null guid must have a distinct contract error.",
        )
        self.assertIn(
            "ObjectReference value_json requires non-empty guid",
            body,
            msg="Explicit empty guid must keep the existing empty-guid failure.",
        )


class TestPatchBridgeResolveNullContractSource(unittest.TestCase):
    _RESOLVE = _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Resolve.cs"
    _PREFAB = _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Prefab.cs"
    _SCENE = _TOOLS_DIR / "PrefabSentinel.UnityPatchBridge.Scene.cs"

    def _source(self, path: Path) -> str:
        return _strip_cs_comments(path.read_text(encoding="utf-8"))

    def _method_body(self, method_name: str) -> str:
        return _extract_method(self._source(self._RESOLVE), method_name)

    def test_normalize_handle_has_no_empty_string_fallback_for_null(self) -> None:
        body = self._method_body("NormalizeHandle")

        self.assertNotIn(
            "?? string.Empty",
            body,
            msg="NormalizeHandle must not coerce null handles to empty strings.",
        )
        self.assertIn(
            "raw.Trim()",
            body,
            msg="NormalizeHandle must trim the non-null handle value directly.",
        )

    def test_optional_result_handle_null_reports_schema_error_and_callers_use_helper(
        self,
    ) -> None:
        resolve_source = self._source(self._RESOLVE)
        prefab_source = self._source(self._PREFAB)
        scene_source = self._source(self._SCENE)

        self.assertIn(
            "TryNormalizeResultHandle",
            resolve_source,
            msg="Resolve.cs must declare TryNormalizeResultHandle for op.result.",
        )
        self.assertIn(
            "result handle is null",
            resolve_source,
            msg=(
                "TryNormalizeResultHandle must report null op.result with "
                "evidence 'result handle is null'."
            ),
        )
        self.assertNotIn(
            "NormalizeHandle(op.result)",
            prefab_source,
            msg="Prefab operations must not call the raw normalizer on op.result.",
        )
        self.assertNotIn(
            "NormalizeHandle(op.result)",
            scene_source,
            msg="Scene operations must not call the raw normalizer on op.result.",
        )

        for source, expected_message in (
            (prefab_source, '"Invalid prefab create plan."'),
            (scene_source, '"Invalid scene plan."'),
        ):
            branch_start = source.find("if (!TryNormalizeResultHandle(")
            self.assertNotEqual(
                -1,
                branch_start,
                msg="Add-component result handling must preflight op.result through TryNormalizeResultHandle.",
            )
            branch_end = source.find("TrySetupUdonSharpBacking", branch_start)
            self.assertNotEqual(
                -1,
                branch_end,
                msg="Result-handle preflight must run before UdonSharp backing setup.",
            )
            null_result_branch = source[branch_start:branch_end]
            self.assertIn(
                '"UNITY_BRIDGE_SCHEMA"',
                null_result_branch,
                msg="Null op.result is invalid plan schema, not an apply failure.",
            )
            self.assertIn(
                expected_message,
                null_result_branch,
                msg="Null op.result must use the same invalid-plan message as TryRegisterHandle failures.",
            )
            self.assertNotIn(
                '"UNITY_BRIDGE_APPLY"',
                null_result_branch,
                msg="Null op.result must not split into the apply envelope.",
            )

    def test_prefab_create_router_rejects_null_operation_before_routing(self) -> None:
        body = _extract_method(self._source(self._PREFAB), "ApplyPrefabCreateOperations")
        operation_null_index = body.find("if (op == null)")
        op_null_index = body.find("if (op.op == null)")
        trim_index = body.find("op.op.Trim()")

        self.assertNotIn(
            "op?.op ?? string.Empty",
            body,
            msg="Prefab create routing must not collapse null operations into empty op names.",
        )
        self.assertNotEqual(
            -1,
            operation_null_index,
            msg="Prefab create routing must reject null PatchOp entries explicitly.",
        )
        self.assertNotEqual(
            -1,
            op_null_index,
            msg="Prefab create routing must reject null op names explicitly.",
        )
        self.assertLess(
            operation_null_index,
            op_null_index,
            msg="Null PatchOp entries must be rejected before reading op.op.",
        )
        self.assertLess(
            op_null_index,
            trim_index,
            msg="Null op names must be rejected before operation-name trimming.",
        )
        self.assertIn(
            'evidence = "operation is null"',
            body,
            msg="Null PatchOp entries must use the shared schema evidence string.",
        )
        self.assertIn(
            'evidence = "op is null"',
            body,
            msg="Null op names must use the shared schema evidence string.",
        )

    def test_scene_router_rejects_null_operation_before_routing(self) -> None:
        body = _extract_method(self._source(self._SCENE), "ApplySceneOperations")
        operation_null_index = body.find("if (op == null)")
        op_null_index = body.find("if (op.op == null)")
        trim_index = body.find("op.op.Trim()")

        self.assertNotIn(
            "op?.op ?? string.Empty",
            body,
            msg="Scene routing must not collapse null operations into empty op names.",
        )
        self.assertNotEqual(
            -1,
            operation_null_index,
            msg="Scene routing must reject null PatchOp entries explicitly.",
        )
        self.assertNotEqual(
            -1,
            op_null_index,
            msg="Scene routing must reject null op names explicitly.",
        )
        self.assertLess(
            operation_null_index,
            op_null_index,
            msg="Null PatchOp entries must be rejected before reading op.op.",
        )
        self.assertLess(
            op_null_index,
            trim_index,
            msg="Null op names must be rejected before operation-name trimming.",
        )
        self.assertIn(
            'evidence = "operation is null"',
            body,
            msg="Null PatchOp entries must use the shared schema evidence string.",
        )
        self.assertIn(
            'evidence = "op is null"',
            body,
            msg="Null op names must use the shared schema evidence string.",
        )

    def test_register_handle_uses_optional_helper_and_keeps_duplicate_diagnostic(
        self,
    ) -> None:
        body = self._method_body("TryRegisterHandle")

        self.assertIn(
            "TryNormalizeResultHandle",
            body,
            msg="TryRegisterHandle must normalize op.result through the null-aware helper.",
        )
        self.assertIn(
            "is already defined",
            body,
            msg="TryRegisterHandle must preserve duplicate-handle diagnostics.",
        )

    def test_required_handle_null_and_empty_have_distinct_messages(self) -> None:
        body = self._method_body("TryResolveHandle")
        null_index = body.find('"handle is null"')
        normalize_index = body.find("NormalizeHandle")
        empty_index = body.find('"handle is required"')

        self.assertNotEqual(
            -1,
            null_index,
            msg="TryResolveHandle must reject null with 'handle is null'.",
        )
        self.assertLess(
            null_index,
            normalize_index,
            msg="TryResolveHandle must reject null before NormalizeHandle.",
        )
        self.assertNotEqual(
            -1,
            empty_index,
            msg="TryResolveHandle must retain 'handle is required' for empty input.",
        )

    def test_target_path_null_and_empty_have_distinct_messages(self) -> None:
        body = self._method_body("TryResolveAssetPath")
        null_index = body.find('"target is null."')
        trim_index = body.find(".Trim()")
        empty_index = body.find('"target is empty."')

        self.assertNotEqual(
            -1,
            null_index,
            msg="TryResolveAssetPath must reject null with 'target is null.'.",
        )
        self.assertLess(
            null_index,
            trim_index,
            msg="TryResolveAssetPath must reject null before trimming target.",
        )
        self.assertNotEqual(
            -1,
            empty_index,
            msg="TryResolveAssetPath must retain 'target is empty.' for empty input.",
        )

    def test_target_path_canonicalizes_relative_assets_before_acceptance(self) -> None:
        body = self._method_body("TryResolveAssetPath")
        canonical_index = body.find(
            "Path.GetFullPath(Path.Combine(projectRoot, assetPath))"
        )
        assets_root_index = body.find("Application.dataPath")
        containment_index = body.find("IsPathInsideDirectory(assetsRoot, fullTarget)")
        assets_prefix_index = body.find('StartsWith("Assets/"')

        self.assertNotEqual(
            -1,
            canonical_index,
            msg=(
                "TryResolveAssetPath must canonicalize relative targets like "
                "Assets/../ProjectSettings before accepting an Assets prefix."
            ),
        )
        self.assertNotEqual(
            -1,
            containment_index,
            msg="TryResolveAssetPath must check the canonical target under the canonical Assets root.",
        )
        self.assertLess(
            assets_root_index,
            containment_index,
            msg="The Assets root must be established before containment is evaluated.",
        )
        self.assertEqual(
            -1,
            assets_prefix_index,
            msg="Raw Assets/ prefix acceptance must not bypass canonical containment.",
        )

    def test_file_id_null_and_empty_have_distinct_messages(self) -> None:
        body = self._method_body("TryResolveComponentByFileId")
        null_index = body.find('"file_id is null"')
        trim_index = body.find(".Trim()")
        empty_index = body.find('"file_id is empty"')

        self.assertNotEqual(
            -1,
            null_index,
            msg="TryResolveComponentByFileId must reject null with 'file_id is null'.",
        )
        self.assertLess(
            null_index,
            trim_index,
            msg="TryResolveComponentByFileId must reject null before trimming file_id.",
        )
        self.assertNotEqual(
            -1,
            empty_index,
            msg="TryResolveComponentByFileId must retain 'file_id is empty'.",
        )

    def test_hierarchy_path_null_and_empty_have_distinct_messages(self) -> None:
        body = self._method_body("TryFindGameObjectByPath")
        null_index = body.find("hierarchyPath == null")
        trim_index = body.find("hierarchyPath.Trim()")
        null_message_index = body.find('"hierarchy path is null"')
        root_assignment_index = body.find("result = root")

        self.assertNotEqual(
            -1,
            null_index,
            msg="TryFindGameObjectByPath must reject null hierarchy paths.",
        )
        self.assertLess(
            null_index,
            trim_index,
            msg="TryFindGameObjectByPath must reject null before trimming hierarchy paths.",
        )
        self.assertNotEqual(
            -1,
            null_message_index,
            msg="TryFindGameObjectByPath must report null hierarchy paths distinctly.",
        )
        self.assertNotEqual(
            -1,
            root_assignment_index,
            msg="Explicit empty hierarchy paths must still resolve to the root object.",
        )

    def test_component_selector_null_and_empty_have_distinct_messages(self) -> None:
        body = self._method_body("TryParseComponentSelector")
        null_index = body.find('"component selector is null"')
        trim_index = body.find(".Trim()")
        empty_index = body.find('"component selector is empty"')

        self.assertNotEqual(
            -1,
            null_index,
            msg=(
                "TryParseComponentSelector must reject null with "
                "'component selector is null'."
            ),
        )
        self.assertLess(
            null_index,
            trim_index,
            msg="TryParseComponentSelector must reject null before trimming selector.",
        )
        self.assertNotEqual(
            -1,
            empty_index,
            msg=(
                "TryParseComponentSelector must retain "
                "'component selector is empty'."
            ),
        )
