"""Path-free payload construction and JSON publication for discovery benchmarks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from prefab_sentinel.benchmarking.report import (
    BenchmarkReportWriteError,
    write_json_report,
)
from prefab_sentinel.tool_discovery_benchmark.models import (
    BenchmarkMeasurements,
    CandidateContextCost,
    QueryFixture,
    QueryMeasurement,
    RetrievalMetrics,
    ToolRecord,
)
from prefab_sentinel.tool_discovery_benchmark.ranking import (
    DESCRIPTION_WEIGHT,
    NAME_WEIGHT,
    RANKER_ID,
)

_ENVIRONMENT_FIELDS = (
    "commit",
    "prefab_sentinel_version",
    "mcp_sdk_version",
    "python_version",
    "platform",
)


def _registry_fingerprint(registry: Sequence[ToolRecord]) -> str:
    payload = {
        "tools": [
            record.wire_definition
            for record in sorted(registry, key=lambda record: record.name)
        ]
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _retrieval_metrics_payload(metrics: RetrievalMetrics) -> dict[str, Any]:
    return {
        "query_count": metrics.query_count,
        "recall_at": {
            str(k): metrics.recall_at[k] for k in sorted(metrics.recall_at)
        },
        "mrr": metrics.mrr,
        "zero_score_query_count": metrics.zero_score_query_count,
    }


def _candidate_context_cost_payload(
    candidate: CandidateContextCost,
) -> dict[str, int | float]:
    return {
        "k": candidate.k,
        "minimum_bytes": candidate.minimum_bytes,
        "maximum_bytes": candidate.maximum_bytes,
        "mean_bytes": candidate.mean_bytes,
        "minimum_tokens": candidate.minimum_tokens,
        "maximum_tokens": candidate.maximum_tokens,
        "mean_tokens": candidate.mean_tokens,
    }


def _query_payload(measurement: QueryMeasurement) -> dict[str, Any]:
    return {
        "id": measurement.case_id,
        "pair_id": measurement.pair_id,
        "language": measurement.language,
        "accepted_tools": list(measurement.accepted_tools),
        "forbidden_hits": list(measurement.forbidden_hits),
        "top_eight": [
            {"name": candidate.name, "score": round(candidate.score, 6)}
            for candidate in measurement.top_eight
        ],
        "first_accepted_rank": measurement.first_accepted_rank,
        "zero_score": measurement.zero_score,
    }



def build_report(
    environment: Mapping[str, str],
    registry: Sequence[ToolRecord],
    fixture: QueryFixture,
    measurements: BenchmarkMeasurements,
) -> dict[str, Any]:
    """Build the stable, path-free discovery benchmark report payload."""
    language_counts = {
        language: sum(case.language == language for case in fixture.cases)
        for language in ("ja", "en")
    }
    return {
        "schema_version": "tool-discovery-benchmark-report.v1",
        "environment": {
            field: environment[field] for field in _ENVIRONMENT_FIELDS
        },
        "registry": {
            "tool_count": len(registry),
            "category_count": len({record.category for record in registry}),
            "fingerprint": _registry_fingerprint(registry),
        },
        "fixture": {
            "schema_version": fixture.schema_version,
            "query_count": len(fixture.cases),
            "language_counts": language_counts,
            "sha256": fixture.sha256,
        },
        "ranker": {
            "id": RANKER_ID,
            "name_weight": NAME_WEIGHT,
            "description_weight": DESCRIPTION_WEIGHT,
        },
        "metrics": {
            "overall": _retrieval_metrics_payload(measurements.overall),
            "by_language": {
                "ja": _retrieval_metrics_payload(measurements.by_language["ja"]),
                "en": _retrieval_metrics_payload(measurements.by_language["en"]),
            },
            "safety": {
                "safety_critical_query_count": (
                    measurements.safety.safety_critical_query_count
                ),
                "unsafe_false_positive_count": (
                    measurements.safety.unsafe_false_positive_count
                ),
                "unsafe_query_count": measurements.safety.unsafe_query_count,
                "unsafe_false_positive_rate": (
                    measurements.safety.unsafe_false_positive_rate
                ),
            },
        },
        "context_cost": {
            "full_bytes": measurements.context_cost.full_bytes,
            "full_tokens": measurements.context_cost.full_tokens,
            "token_estimator_id": measurements.context_cost.token_estimator_id,
            "candidates": [
                _candidate_context_cost_payload(candidate)
                for candidate in measurements.context_cost.candidates
            ],
        },
        "queries": [
            _query_payload(measurement) for measurement in measurements.queries
        ],
    }


__all__ = [
    "BenchmarkReportWriteError",
    "build_report",
    "write_json_report",
]
