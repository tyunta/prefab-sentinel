from __future__ import annotations

import unittest
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PROJECT_ROOT / "skills" / "inspector-profile-authoring" / "SKILL.md"
RESOURCE_DIR = PROJECT_ROOT / "prefab_sentinel" / "resources"

pytestmark = pytest.mark.source_text_invariant


class TestInspectorProfileAuthoringSkill(unittest.TestCase):
    def _source(self) -> str:
        if not SKILL_PATH.is_file():
            self.fail(f"inspector profile authoring skill is missing: {SKILL_PATH}")
        return SKILL_PATH.read_text(encoding="utf-8")

    def test_trigger_and_canonical_resource_contract_is_complete(self) -> None:
        source = self._source()
        required = (
            "INSPECTOR_PROFILE_REQUIRED",
            "INSPECTOR_PROFILE_INCOMPLETE",
            "INSPECTOR_PROFILE_INVALID",
            "PROFILE_AUTHORING_REQUIRED",
            "create or repair an inspector profile",
            "prefab_sentinel/resources/inspector-profile.v1.schema.json",
            "prefab_sentinel/resources/inspector-profile.template.json",
        )

        self.assertEqual(
            {token: True for token in required},
            {token: token in source for token in required},
            msg="skill trigger or canonical resource reference is incomplete",
        )
        self.assertEqual(
            [],
            sorted(path.name for path in SKILL_PATH.parent.glob("*.json")),
            msg="the skill directory must not duplicate the canonical schema or template",
        )

    def test_confidence_rules_preserve_semantic_evidence_boundaries(self) -> None:
        source = self._source()
        required = (
            "High confidence requires complete qualifying semantic evidence and a current serialized surface that validates the mapping.",
            "Source unavailability does not lower a complete active-custom-inspector mapping below high confidence.",
            "Medium confidence requires qualifying semantic evidence that is strong but incomplete or conflicting.",
            "Low confidence is reserved for field-name or shape inference when no qualifying semantic evidence is available.",
            "Mechanical validation never raises semantic confidence.",
        )

        self.assertEqual(
            {statement: True for statement in required},
            {statement: statement in source for statement in required},
            msg="skill confidence language must value-pin every MC-006 evidence partition",
        )

    def test_safe_authoring_sequence_and_promotion_gates_are_ordered(self) -> None:
        source = self._source()
        ordered_steps = (
            "1. **Refetch the surface.**",
            "2. **Collect semantic evidence.**",
            "3. **Classify confidence.**",
            "4. **Stage outside discovery.**",
            "5. **Apply writer gates.**",
            "6. **Validate the draft.**",
            "7. **Promote atomically.**",
            "8. **Verify the promoted profile.**",
            "9. **Clean up or restore.**",
        )
        positions = {step: source.find(step) for step in ordered_steps}

        self.assertEqual(
            {},
            {step: position for step, position in positions.items() if position < 0},
            msg="safe authoring sequence is missing a required step",
        )
        self.assertEqual(
            list(positions.values()),
            sorted(positions.values()),
            msg=f"safe authoring steps are out of order: {positions!r}",
        )

        required_gates = (
            "A validated read-only project-local profile is eligible for autonomous promotion without user approval.",
            "Every writer-enabled view must set `writable.enabled=true` explicitly.",
            "Medium- and low-confidence writer-enabled views require a persistent `explicit_user_request` approval record.",
            "A Bridge blocker stops before validation and promotion.",
            "On any failure, remove the draft and preserve or atomically restore the pre-existing profile.",
        )
        self.assertEqual(
            {statement: True for statement in required_gates},
            {statement: statement in source for statement in required_gates},
            msg="read-only promotion, writer approval, Bridge, or restoration gate is incomplete",
        )

    def test_repair_seed_is_selected_profile_while_first_authoring_uses_template(self) -> None:
        source = self._source()
        required = (
            "Only first-time authoring starts from `prefab_sentinel/resources/inspector-profile.template.json`; copy it byte-for-byte into the outside-discovery draft.",
            "For repair, copy the selected project-local profile byte-for-byte into the outside-discovery draft, then minimally extend or correct that copy.",
        )

        self.assertEqual(
            {statement: True for statement in required},
            {statement: statement in source for statement in required},
            msg="authoring and repair must use distinct, value-pinned draft seeds",
        )

    def test_failure_restores_selected_profile_bytes_and_records_evidence(self) -> None:
        source = self._source()
        required = (
            "On validation, promotion, or final-verification failure, remove the draft and leave the selected profile byte-for-byte identical to the captured original, atomically restoring those exact bytes if the destination was touched.",
            "Record the draft validation diagnostics, promotion outcome, restoration outcome, and every intended-view verification result as authoring evidence.",
        )

        self.assertEqual(
            {statement: True for statement in required},
            {statement: statement in source for statement in required},
            msg="repair failure must restore exact bytes and retain auditable evidence",
        )

    def test_canonical_resources_exist_outside_the_skill(self) -> None:
        expected = (
            RESOURCE_DIR / "inspector-profile.v1.schema.json",
            RESOURCE_DIR / "inspector-profile.template.json",
        )

        self.assertEqual(
            (True, True),
            tuple(path.is_file() for path in expected),
            msg=f"canonical Inspector profile resources are missing: {expected!r}",
        )
