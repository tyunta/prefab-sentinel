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
* the operational rules file (``CLAUDE.md``) lists every per-concern
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

# The patch-bridge layout invariants are read-only inspections of the
# un-mutated ``tools/unity`` tree; they cannot observe mutations applied
# to ``prefab_sentinel/`` so they are excluded from the mutmut run via
# the project-wide ``source_text_invariant`` filter.
pytestmark = pytest.mark.source_text_invariant


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _PROJECT_ROOT / "tools" / "unity"
_CLAUDE_MD = _PROJECT_ROOT / "CLAUDE.md"


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
    """``CLAUDE.md`` is the single human-readable source of truth for
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
        text = _CLAUDE_MD.read_text(encoding="utf-8")
        for concern in sorted(self._disk_partial_concerns()):
            with self.subTest(concern=concern):
                self.assertIn(
                    concern,
                    text,
                    f"CLAUDE.md inventory line is missing patch-bridge "
                    f"concern '{concern}'.",
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
