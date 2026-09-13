from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from prefab_sentinel.tool_discovery_benchmark.fixture import load_query_fixture
from prefab_sentinel.tool_discovery_benchmark.registry import load_tool_registry

pytestmark = pytest.mark.source_text_invariant


def test_docs_publish_reproducible_command_and_decision() -> None:
    report = Path("docs/benchmarks/2026-09-02-tool-discovery.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "run_tool_discovery_benchmark.py",
        "recall@1",
        "recall@3",
        "recall@5",
        "recall@8",
        "MRR",
        "unsafe false-positive",
        "utf8-bytes-div-4-ceiling.v1",
        "Decision",
    ):
        assert required in report



def test_checked_result_uses_committed_fixture_fingerprint() -> None:
    registry = asyncio.run(load_tool_registry(Path("docs/tools.md")))
    fixture = load_query_fixture(
        Path("benchmarks/tool-discovery/queries.v1.json"),
        registry,
    )
    report = Path("docs/benchmarks/2026-09-02-tool-discovery.md").read_text(
        encoding="utf-8"
    )

    assert fixture.sha256 in report



def test_execution_reference_states_post_parse_status_contract() -> None:
    execution_reference = Path("docs/execution-reference.md").read_text(
        encoding="utf-8"
    )

    assert "After successful argument parsing" in execution_reference


def test_readme_routes_to_tool_discovery_result() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "docs/benchmarks/2026-09-02-tool-discovery.md" in readme
