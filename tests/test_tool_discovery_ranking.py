from __future__ import annotations

import math
from pathlib import Path

import anyio
import pytest

from prefab_sentinel.tool_discovery_benchmark.models import ToolRecord
from prefab_sentinel.tool_discovery_benchmark.ranking import (
    DeterministicLexicalRanker,
    normalize_tokens,
)
from prefab_sentinel.tool_discovery_benchmark.registry import load_tool_registry


@pytest.fixture(scope="module")
def real_registry() -> tuple[ToolRecord, ...]:
    return anyio.run(load_tool_registry, Path("docs/tools.md"))


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


def test_normalization_handles_nfkc_camel_case_and_underscores() -> None:
    assert normalize_tokens("Ｆｉｎｄ editorGet_Transform") == (
        "find",
        "editor",
        "get",
        "transform",
    )


def test_name_match_outweighs_description_only_match() -> None:
    registry = (
        tool("alpha_scan", "other"),
        tool("beta", "scan assets"),
    )
    ranking = DeterministicLexicalRanker(registry).rank("scan")
    assert [item.name for item in ranking.ranked_tools[:2]] == ["alpha_scan", "beta"]


def test_zero_score_ties_are_canonical_name_order() -> None:
    ranking = DeterministicLexicalRanker((tool("zeta", "x"), tool("alpha", "y"))).rank("日本語")
    assert ranking.zero_score is True
    assert [item.name for item in ranking.ranked_tools] == ["alpha", "zeta"]


def test_ranker_is_repeatable_and_finite(real_registry: tuple[ToolRecord, ...]) -> None:
    ranker = DeterministicLexicalRanker(real_registry)
    first = ranker.rank("inspect references")
    second = ranker.rank("inspect references")
    assert first == second
    assert all(math.isfinite(item.score) for item in first.ranked_tools)




def test_rank_pins_smoothed_idf_and_field_weights() -> None:
    ranking = DeterministicLexicalRanker(
        (
            tool("alpha_scan", "common"),
            tool("beta", "scan"),
            tool("gamma", "common"),
        )
    ).rank("scan")
    scores = {item.name: item.score for item in ranking.ranked_tools}

    # N=3 and df(scan)=2: log((3 + 1) / (2 + 1)) + 1.
    assert scores["alpha_scan"] == pytest.approx(3.8630462173553424, abs=1e-12)
    assert scores["beta"] == pytest.approx(1.2876820724517808, abs=1e-12)
    assert scores["gamma"] == pytest.approx(0.0, abs=1e-12)
