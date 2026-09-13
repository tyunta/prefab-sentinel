"""Regression tests for deterministic tool-discovery benchmark metrics."""

from __future__ import annotations

import json
from random import Random
from typing import cast

import pytest

from prefab_sentinel.tool_discovery_benchmark.metrics import (
    K_VALUES,
    TOKEN_ESTIMATOR_ID,
    estimate_tokens,
    measure_benchmark,
    retrieval_metrics,
    serialized_schema_bytes,
    unsafe_metrics,
)
from prefab_sentinel.tool_discovery_benchmark.models import (
    LexicalRanking,
    QueryCase,
    QueryFixture,
    QueryMeasurement,
    RankedTool,
    ToolRecord,
)


def measurement(
    case_id: str,
    *,
    accepted: tuple[str, ...],
    order: tuple[str, ...],
    forbidden: tuple[str, ...] = (),
    safety_critical: bool = False,
    zero_score: bool = False,
) -> QueryMeasurement:
    """Construct a hand-ranked measurement without exercising the metric code."""
    first_rank = next(
        (index for index, name in enumerate(order, start=1) if name in accepted),
        None,
    )
    forbidden_hits = tuple(name for name in order[:8] if name in forbidden)
    return QueryMeasurement(
        case_id=case_id,
        pair_id=case_id,
        language="en",
        accepted_tools=accepted,
        forbidden_tools=forbidden,
        top_eight=tuple(
            RankedTool(name, 1.0 / index)
            for index, name in enumerate(order[:8], start=1)
        ),
        first_accepted_rank=first_rank,
        forbidden_hits=forbidden_hits,
        zero_score=zero_score,
        safety_critical=safety_critical,
    )


def tool(name: str, description: str) -> ToolRecord:
    return ToolRecord(
        name=name,
        description=description,
        category="test",
        wire_definition={
            "name": name,
            "description": description,
            "inputSchema": {"type": "object"},
        },
    )


def test_recall_mrr_and_zero_scores_are_hand_calculated() -> None:
    measurements = (
        measurement("q1", accepted=("a",), order=("a", "b", "c")),
        measurement("q2", accepted=("c",), order=("a", "c", "b")),
        measurement(
            "q3",
            accepted=("z",),
            order=("a", "b", "c"),
            zero_score=True,
        ),
    )

    metrics = retrieval_metrics(measurements)

    assert metrics.query_count == 3
    assert metrics.recall_at == {1: 1 / 3, 3: 2 / 3, 5: 2 / 3, 8: 2 / 3}
    assert metrics.mrr == pytest.approx((1.0 + 0.5 + 0.0) / 3)
    assert metrics.zero_score_query_count == 1


def test_retrieval_metrics_are_independent_of_input_order() -> None:
    measurements = [
        measurement("q1", accepted=("a",), order=("a", "b")),
        measurement("q2", accepted=("b",), order=("a", "b")),
        measurement("q3", accepted=("z",), order=("a", "b")),
    ]
    shuffled = measurements[:]
    Random(190).shuffle(shuffled)

    assert retrieval_metrics(measurements) == retrieval_metrics(shuffled)


def test_unsafe_metrics_count_safety_critical_queries_and_placements() -> None:
    metrics = unsafe_metrics(
        (
            measurement(
                "unsafe",
                accepted=("validate_refs",),
                order=("delete_asset", "patch_apply", "validate_refs"),
                forbidden=("delete_asset", "patch_apply"),
                safety_critical=True,
            ),
            measurement(
                "safe",
                accepted=("validate_refs",),
                order=("validate_refs", "inspect_wiring"),
                forbidden=("delete_asset",),
                safety_critical=True,
            ),
            measurement(
                "not-in-scope",
                accepted=("validate_refs",),
                order=("delete_asset",),
                forbidden=("delete_asset",),
            ),
        )
    )

    assert metrics.safety_critical_query_count == 2
    assert metrics.unsafe_false_positive_count == 2
    assert metrics.unsafe_query_count == 1
    assert metrics.unsafe_false_positive_rate == 0.5


def test_unsafe_metrics_are_independent_of_input_order() -> None:
    measurements = [
        measurement(
            "unsafe",
            accepted=("a",),
            order=("forbidden",),
            forbidden=("forbidden",),
            safety_critical=True,
        ),
        measurement(
            "safe",
            accepted=("a",),
            order=("a",),
            forbidden=("forbidden",),
            safety_critical=True,
        ),
    ]

    assert unsafe_metrics(measurements) == unsafe_metrics(tuple(reversed(measurements)))


@pytest.mark.parametrize(
    ("byte_count", "expected"),
    [(0, 0), (1, 1), (4, 1), (5, 2)],
)
def test_token_estimate_is_ceiling_division(byte_count: int, expected: int) -> None:
    assert estimate_tokens(byte_count) == expected


@pytest.mark.parametrize("value", [-1, 1.0, True])
def test_token_estimate_rejects_non_integer_or_negative_bytes(value: object) -> None:
    with pytest.raises(ValueError) as error:
        estimate_tokens(cast(int, value))

    assert str(error.value) == "byte_count must be a non-negative integer"


def test_schema_bytes_use_compact_sorted_utf8_json() -> None:
    payload = {"tools": [{"name": "β", "inputSchema": {"type": "object"}}]}
    expected = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert serialized_schema_bytes(payload["tools"]) == len(expected)


def test_measurement_rejects_accepted_forbidden_overlap_constructed_manually() -> None:
    invalid = QueryMeasurement(
        case_id="invalid",
        pair_id="invalid",
        language="en",
        accepted_tools=("same_tool",),
        forbidden_tools=("other_tool",),
        top_eight=(RankedTool("same_tool", 1.0),),
        first_accepted_rank=1,
        forbidden_hits=("same_tool",),
        zero_score=False,
        safety_critical=True,
    )

    with pytest.raises(ValueError) as error:
        retrieval_metrics((invalid,))

    assert str(error.value) == "accepted_tools and forbidden_hits conflict"


def test_measure_benchmark_uses_complete_rankings_and_query_specific_context_cost() -> None:
    registry = (
        tool("zulu", "long description for zulu"),
        tool("alpha", "α"),
        tool("beta", "a medium beta description"),
    )
    cases = (
        QueryCase(
            "alpha-en",
            "alpha",
            "en",
            "find alpha",
            ("alpha",),
            (),
            False,
            "test",
        ),
        QueryCase(
            "beta-ja",
            "beta",
            "ja",
            "ベータを探す",
            ("beta",),
            ("zulu",),
            True,
            "test",
        ),
    )
    fixture = QueryFixture("fixture.v1", cases, "a" * 64)
    rankings = {
        "alpha-en": LexicalRanking(
            ("find", "alpha"),
            (
                RankedTool("zulu", 3.0),
                RankedTool("beta", 2.0),
                RankedTool("alpha", 1.0),
            ),
            False,
        ),
        "beta-ja": LexicalRanking(
            ("ベータ",),
            (
                RankedTool("beta", 3.0),
                RankedTool("zulu", 2.0),
                RankedTool("alpha", 1.0),
            ),
            False,
        ),
    }

    measurements = measure_benchmark(registry, fixture, rankings)

    assert measurements.overall.recall_at == {1: 1 / 2, 3: 1.0, 5: 1.0, 8: 1.0}
    assert measurements.overall.mrr == pytest.approx((1 / 3 + 1.0) / 2)
    assert measurements.by_language["en"].query_count == 1
    assert measurements.by_language["ja"].query_count == 1
    assert measurements.safety.unsafe_false_positive_count == 1
    assert measurements.safety.unsafe_query_count == 1
    assert measurements.queries[0].first_accepted_rank == 3
    assert measurements.queries[1].forbidden_hits == ("zulu",)
    assert measurements.context_cost.full_bytes == serialized_schema_bytes(
        [record.wire_definition for record in sorted(registry, key=lambda item: item.name)]
    )
    assert measurements.context_cost.full_tokens == estimate_tokens(
        measurements.context_cost.full_bytes
    )
    assert measurements.context_cost.token_estimator_id == TOKEN_ESTIMATOR_ID
    assert tuple(candidate.k for candidate in measurements.context_cost.candidates) == K_VALUES
    first_candidate = measurements.context_cost.candidates[0]
    zulu_bytes = serialized_schema_bytes([registry[0].wire_definition])
    beta_bytes = serialized_schema_bytes([registry[2].wire_definition])
    assert (first_candidate.minimum_bytes, first_candidate.maximum_bytes) == (
        min(zulu_bytes, beta_bytes),
        max(zulu_bytes, beta_bytes),
    )
    assert first_candidate.mean_bytes == pytest.approx((zulu_bytes + beta_bytes) / 2)
    assert first_candidate.minimum_tokens == min(
        estimate_tokens(zulu_bytes), estimate_tokens(beta_bytes)
    )
    assert first_candidate.maximum_tokens == max(
        estimate_tokens(zulu_bytes), estimate_tokens(beta_bytes)
    )
    assert first_candidate.mean_tokens == pytest.approx(
        (estimate_tokens(zulu_bytes) + estimate_tokens(beta_bytes)) / 2
    )


@pytest.mark.parametrize("score", (float("nan"), float("inf"), float("-inf")))
def test_measure_benchmark_rejects_nonfinite_scores_after_top_eight(
    score: float,
) -> None:
    registry = tuple(tool(f"tool_{index}", "test") for index in range(9))
    case = QueryCase(
        "query-en",
        "query",
        "en",
        "find a tool",
        ("tool_0",),
        (),
        False,
        "test",
    )
    fixture = QueryFixture("fixture.v1", (case,), "a" * 64)
    ranking = LexicalRanking(
        (),
        tuple(
            RankedTool(f"tool_{index}", score if index == 8 else 1.0)
            for index in range(9)
        ),
        False,
    )

    with pytest.raises(ValueError) as error:
        measure_benchmark(registry, fixture, {case.case_id: ranking})

    assert str(error.value) == "ranking scores must be finite"
