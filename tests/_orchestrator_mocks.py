from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from prefab_sentinel.orchestrator import Phase1Orchestrator


class MockedPhase1Orchestrator(Phase1Orchestrator):
    reference_resolver: MagicMock
    prefab_variant: MagicMock
    runtime_validation: MagicMock
    serialized_object: MagicMock


@dataclass(slots=True)
class OrchestratorHarness:
    orchestrator: MockedPhase1Orchestrator
    reference_resolver: MagicMock
    prefab_variant: MagicMock
    runtime_validation: MagicMock
    serialized_object: MagicMock


def make_mocked_orchestrator() -> MockedPhase1Orchestrator:
    reference_resolver = MagicMock()
    prefab_variant = MagicMock()
    runtime_validation = MagicMock()
    serialized_object = MagicMock()
    return MockedPhase1Orchestrator(
        reference_resolver=reference_resolver,
        prefab_variant=prefab_variant,
        runtime_validation=runtime_validation,
        serialized_object=serialized_object,
    )


def make_orchestrator_harness() -> OrchestratorHarness:
    orchestrator = make_mocked_orchestrator()
    return OrchestratorHarness(
        orchestrator=orchestrator,
        reference_resolver=orchestrator.reference_resolver,
        prefab_variant=orchestrator.prefab_variant,
        runtime_validation=orchestrator.runtime_validation,
        serialized_object=orchestrator.serialized_object,
    )
