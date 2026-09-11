"""Shared deterministic inputs for tool-discovery benchmark tests."""

from __future__ import annotations

from prefab_sentinel.tool_discovery_benchmark.metrics import measure_benchmark
from prefab_sentinel.tool_discovery_benchmark.models import (
    BenchmarkMeasurements,
    QueryCase,
    QueryFixture,
    ToolRecord,
)
from prefab_sentinel.tool_discovery_benchmark.ranking import DeterministicLexicalRanker


def sample_inputs() -> tuple[
    dict[str, str],
    tuple[ToolRecord, ...],
    QueryFixture,
    BenchmarkMeasurements,
]:
    """Build one path-independent benchmark result with Japanese and English cases."""
    registry = (
        ToolRecord(
            name="validate_refs",
            description="inspect references",
            category="validation",
            wire_definition={
                "name": "validate_refs",
                "description": "inspect references",
                "inputSchema": {"type": "object"},
            },
        ),
    )
    cases = (
        QueryCase(
            "refs-ja",
            "refs",
            "ja",
            "参照を調べる",
            ("validate_refs",),
            (),
            False,
            "inspection",
        ),
        QueryCase(
            "refs-en",
            "refs",
            "en",
            "inspect references",
            ("validate_refs",),
            (),
            False,
            "inspection",
        ),
    )
    fixture = QueryFixture("tool-discovery-query-fixture.v1", cases, "f" * 64)
    ranker = DeterministicLexicalRanker(registry)
    rankings = {case.case_id: ranker.rank(case.query) for case in cases}
    measurements = measure_benchmark(registry, fixture, rankings)
    environment = {
        "commit": "a" * 40,
        "prefab_sentinel_version": "0.0.0",
        "mcp_sdk_version": "0.0.0",
        "python_version": "3.11.0",
        "platform": "linux",
    }
    return environment, registry, fixture, measurements
