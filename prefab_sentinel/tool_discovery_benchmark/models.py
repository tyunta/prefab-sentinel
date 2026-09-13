"""Immutable data contracts for offline tool-discovery benchmarks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolRecord:
    """One canonical public MCP tool and its documentation category."""

    name: str
    description: str
    category: str
    wire_definition: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class QueryCase:
    """One Japanese or English benchmark query and its relevance contract."""

    case_id: str
    pair_id: str
    language: Literal["ja", "en"]
    query: str
    accepted_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    safety_critical: bool
    rationale: str


@dataclass(frozen=True, slots=True)
class QueryFixture:
    """One parsed, versioned discovery-query corpus."""

    schema_version: str
    cases: tuple[QueryCase, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class RankedTool:
    """A tool name and its unrounded lexical relevance score."""

    name: str
    score: float


@dataclass(frozen=True, slots=True)
class LexicalRanking:
    """A deterministic lexical ranking for one normalized query."""

    query_tokens: tuple[str, ...]
    ranked_tools: tuple[RankedTool, ...]
    zero_score: bool


@dataclass(frozen=True, slots=True)
class QueryMeasurement:
    """One query's retrieval and safety observations."""

    case_id: str
    pair_id: str
    language: Literal["ja", "en"]
    accepted_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    top_eight: tuple[RankedTool, ...]
    first_accepted_rank: int | None
    forbidden_hits: tuple[str, ...]
    zero_score: bool
    safety_critical: bool


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """Aggregate retrieval accuracy for one query population."""

    query_count: int
    recall_at: Mapping[int, float]
    mrr: float
    zero_score_query_count: int


@dataclass(frozen=True, slots=True)
class SafetyMetrics:
    """Forbidden-tool placements among safety-critical queries."""

    safety_critical_query_count: int
    unsafe_false_positive_count: int
    unsafe_query_count: int
    unsafe_false_positive_rate: float


@dataclass(frozen=True, slots=True)
class CandidateContextCost:
    """Byte and token-estimate summary for one top-k candidate surface."""

    k: int
    minimum_bytes: int
    maximum_bytes: int
    mean_bytes: float
    minimum_tokens: int
    maximum_tokens: int
    mean_tokens: float


@dataclass(frozen=True, slots=True)
class ContextCost:
    """Full registry and query-specific candidate context costs."""

    full_bytes: int
    full_tokens: int
    candidates: tuple[CandidateContextCost, ...]
    token_estimator_id: str


@dataclass(frozen=True, slots=True)
class BenchmarkMeasurements:
    """All deterministic measurements for one registry and fixture run."""

    overall: RetrievalMetrics
    by_language: Mapping[Literal["ja", "en"], RetrievalMetrics]
    safety: SafetyMetrics
    context_cost: ContextCost
    queries: tuple[QueryMeasurement, ...]
