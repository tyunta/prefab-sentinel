"""Deterministic retrieval, safety, and context-cost measurements."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from prefab_sentinel.tool_discovery_benchmark.models import (
    BenchmarkMeasurements,
    CandidateContextCost,
    ContextCost,
    LexicalRanking,
    QueryFixture,
    QueryMeasurement,
    RetrievalMetrics,
    SafetyMetrics,
    ToolRecord,
)

K_VALUES = (1, 3, 5, 8)
TOKEN_ESTIMATOR_ID = "utf8-bytes-div-4-ceiling.v1"


def estimate_tokens(byte_count: int) -> int:
    """Estimate tokens with the benchmark's documented byte-only estimator."""
    if type(byte_count) is not int or byte_count < 0:
        raise ValueError("byte_count must be a non-negative integer")
    return (byte_count + 3) // 4


def serialized_schema_bytes(tools: Sequence[Mapping[str, Any]]) -> int:
    """Return the exact compact UTF-8 byte size of an MCP tools payload."""
    payload = {"tools": list(tools)}
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _validate_measurement(measurement: QueryMeasurement) -> None:
    accepted_tools = set(measurement.accepted_tools)
    if accepted_tools & set(measurement.forbidden_tools):
        raise ValueError("accepted_tools and forbidden_tools conflict")
    if accepted_tools & set(measurement.forbidden_hits):
        raise ValueError("accepted_tools and forbidden_hits conflict")
    if any(hit not in measurement.forbidden_tools for hit in measurement.forbidden_hits):
        raise ValueError("forbidden_hits must be forbidden_tools")
    if measurement.first_accepted_rank is not None and measurement.first_accepted_rank < 1:
        raise ValueError("first_accepted_rank must be positive")


def _validate_measurements(measurements: Sequence[QueryMeasurement]) -> None:
    for measurement in measurements:
        _validate_measurement(measurement)


def retrieval_metrics(measurements: Sequence[QueryMeasurement]) -> RetrievalMetrics:
    """Aggregate recall@k and MRR, retaining every supplied query."""
    _validate_measurements(measurements)
    query_count = len(measurements)
    if query_count == 0:
        return RetrievalMetrics(0, {k: 0.0 for k in K_VALUES}, 0.0, 0)

    recall_at = {
        k: sum(
            measurement.first_accepted_rank is not None
            and measurement.first_accepted_rank <= k
            for measurement in measurements
        )
        / query_count
        for k in K_VALUES
    }
    mrr = (
        sum(
            0.0
            if measurement.first_accepted_rank is None
            else 1.0 / measurement.first_accepted_rank
            for measurement in measurements
        )
        / query_count
    )
    return RetrievalMetrics(
        query_count=query_count,
        recall_at=recall_at,
        mrr=mrr,
        zero_score_query_count=sum(
            measurement.zero_score for measurement in measurements
        ),
    )


def unsafe_metrics(measurements: Sequence[QueryMeasurement]) -> SafetyMetrics:
    """Count forbidden placements only for safety-critical benchmark cases."""
    _validate_measurements(measurements)
    safety_critical = tuple(
        measurement for measurement in measurements if measurement.safety_critical
    )
    unsafe_query_count = sum(bool(measurement.forbidden_hits) for measurement in safety_critical)
    safety_critical_query_count = len(safety_critical)
    return SafetyMetrics(
        safety_critical_query_count=safety_critical_query_count,
        unsafe_false_positive_count=sum(
            len(measurement.forbidden_hits) for measurement in safety_critical
        ),
        unsafe_query_count=unsafe_query_count,
        unsafe_false_positive_rate=(
            unsafe_query_count / safety_critical_query_count
            if safety_critical_query_count
            else 0.0
        ),
    )


def _measurement_for_case(
    case_id: str,
    pair_id: str,
    language: Literal["ja", "en"],
    accepted_tools: tuple[str, ...],
    forbidden_tools: tuple[str, ...],
    safety_critical: bool,
    ranking: LexicalRanking,
) -> QueryMeasurement:
    first_accepted_rank = next(
        (
            rank
            for rank, ranked_tool in enumerate(ranking.ranked_tools, start=1)
            if ranked_tool.name in accepted_tools
        ),
        None,
    )
    top_eight = ranking.ranked_tools[: max(K_VALUES)]
    return QueryMeasurement(
        case_id=case_id,
        pair_id=pair_id,
        language=language,
        accepted_tools=accepted_tools,
        forbidden_tools=forbidden_tools,
        top_eight=top_eight,
        first_accepted_rank=first_accepted_rank,
        forbidden_hits=tuple(
            ranked_tool.name
            for ranked_tool in top_eight
            if ranked_tool.name in forbidden_tools
        ),
        zero_score=ranking.zero_score,
        safety_critical=safety_critical,
    )


def _validate_rankings(
    registry: Sequence[ToolRecord],
    fixture: QueryFixture,
    rankings: Mapping[str, LexicalRanking],
) -> None:
    registry_names = {tool.name for tool in registry}
    case_ids = {case.case_id for case in fixture.cases}
    if set(rankings) != case_ids:
        raise ValueError("rankings must match fixture case IDs")
    for ranking in rankings.values():
        ranked_names = tuple(item.name for item in ranking.ranked_tools)
        if len(ranked_names) != len(registry_names) or set(ranked_names) != registry_names:
            raise ValueError("each ranking must contain every registry tool exactly once")
        if any(not math.isfinite(item.score) for item in ranking.ranked_tools):
            raise ValueError("ranking scores must be finite")


def _candidate_context_cost(
    k: int,
    measurements: Sequence[QueryMeasurement],
    records_by_name: Mapping[str, ToolRecord],
) -> CandidateContextCost:
    byte_counts = tuple(
        serialized_schema_bytes(
            [records_by_name[ranked_tool.name].wire_definition for ranked_tool in measurement.top_eight[:k]]
        )
        for measurement in measurements
    )
    token_counts = tuple(estimate_tokens(byte_count) for byte_count in byte_counts)
    if not byte_counts:
        return CandidateContextCost(k, 0, 0, 0.0, 0, 0, 0.0)
    return CandidateContextCost(
        k=k,
        minimum_bytes=min(byte_counts),
        maximum_bytes=max(byte_counts),
        mean_bytes=sum(byte_counts) / len(byte_counts),
        minimum_tokens=min(token_counts),
        maximum_tokens=max(token_counts),
        mean_tokens=sum(token_counts) / len(token_counts),
    )


def measure_benchmark(
    registry: Sequence[ToolRecord],
    fixture: QueryFixture,
    rankings: Mapping[str, LexicalRanking],
) -> BenchmarkMeasurements:
    """Measure one complete ranking per fixture query against the registry."""
    _validate_rankings(registry, fixture, rankings)
    records_by_name = {record.name: record for record in registry}
    query_measurements = tuple(
        _measurement_for_case(
            case.case_id,
            case.pair_id,
            case.language,
            case.accepted_tools,
            case.forbidden_tools,
            case.safety_critical,
            rankings[case.case_id],
        )
        for case in fixture.cases
    )
    _validate_measurements(query_measurements)
    language_measurements: dict[Literal["ja", "en"], tuple[QueryMeasurement, ...]] = {
        "ja": tuple(
            measurement
            for measurement in query_measurements
            if measurement.language == "ja"
        ),
        "en": tuple(
            measurement
            for measurement in query_measurements
            if measurement.language == "en"
        ),
    }
    sorted_registry = tuple(sorted(registry, key=lambda record: record.name))
    full_bytes = serialized_schema_bytes(
        [record.wire_definition for record in sorted_registry]
    )
    by_language: dict[Literal["ja", "en"], RetrievalMetrics] = {
        language: retrieval_metrics(measurements)
        for language, measurements in language_measurements.items()
    }
    return BenchmarkMeasurements(
        overall=retrieval_metrics(query_measurements),
        by_language=by_language,
        safety=unsafe_metrics(query_measurements),
        context_cost=ContextCost(
            full_bytes=full_bytes,
            full_tokens=estimate_tokens(full_bytes),
            candidates=tuple(
                _candidate_context_cost(k, query_measurements, records_by_name)
                for k in K_VALUES
            ),
            token_estimator_id=TOKEN_ESTIMATOR_ID,
        ),
        queries=query_measurements,
    )
