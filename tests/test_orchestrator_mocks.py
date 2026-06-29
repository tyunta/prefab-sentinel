from __future__ import annotations

import unittest

from prefab_sentinel.contracts import Severity, ToolResponse
from tests._orchestrator_mocks import make_orchestrator_harness


class OrchestratorHarnessTests(unittest.TestCase):
    def test_harness_exposes_orchestrator_behavior_and_named_mock_handles(self) -> None:
        harness = make_orchestrator_harness()
        configured = ToolResponse(
            success=False,
            severity=Severity.ERROR,
            code="CHAIN_FAILED",
            message="chain failed",
            data={"marker": "configured"},
        )
        harness.prefab_variant.resolve_prefab_chain.return_value = configured

        response = harness.orchestrator.inspect_variant("Assets/Variant.prefab")
        first_step = response.data["steps"][0]

        self.assertEqual(
            (
                False,
                "INSPECT_VARIANT_RESULT",
                "resolve_prefab_chain",
                configured.to_dict(),
            ),
            (
                response.success,
                response.code,
                first_step["step"],
                first_step["result"],
            ),
        )
        harness.prefab_variant.resolve_prefab_chain.assert_called_once_with(
            "Assets/Variant.prefab"
        )
