"""Contract tests for the tool-discovery benchmark report."""

from __future__ import annotations

import json
from pathlib import Path

from prefab_sentinel.tool_discovery_benchmark.report import build_report
from tests._tool_discovery_test_support import sample_inputs


def test_report_has_exact_top_level_contract() -> None:
    report = build_report(*sample_inputs())

    assert list(report) == [
        "schema_version",
        "environment",
        "registry",
        "fixture",
        "ranker",
        "metrics",
        "context_cost",
        "queries",
    ]
    assert report["schema_version"] == "tool-discovery-benchmark-report.v1"


def test_report_contains_path_free_count_and_fingerprint_evidence(
    tmp_path: Path,
) -> None:
    environment, registry, fixture, measurements = sample_inputs()
    report = build_report(environment, registry, fixture, measurements)
    serialized = json.dumps(report, ensure_ascii=False)

    assert str(tmp_path) not in serialized
    assert "queries.v1.json" not in serialized
    assert report["registry"]["tool_count"] == 1
    assert report["registry"]["category_count"] == 1
    assert report["registry"]["fingerprint"]
    assert report["fixture"] == {
        "schema_version": "tool-discovery-query-fixture.v1",
        "query_count": 2,
        "language_counts": {"ja": 1, "en": 1},
        "sha256": "f" * 64,
    }


def test_report_redacts_fixture_prose_and_rounds_query_scores() -> None:
    report = build_report(*sample_inputs())
    query = report["queries"][0]
    serialized = json.dumps(report, ensure_ascii=False)

    assert "参照を調べる" not in serialized
    assert "inspect references" not in serialized
    assert "inspection" not in serialized
    assert list(query) == [
        "id",
        "pair_id",
        "language",
        "accepted_tools",
        "forbidden_hits",
        "top_eight",
        "first_accepted_rank",
        "zero_score",
    ]
    assert query["id"] == "refs-ja"
    assert query["pair_id"] == "refs"
    assert query["accepted_tools"] == ["validate_refs"]
    assert query["top_eight"][0]["score"] == 0.0
