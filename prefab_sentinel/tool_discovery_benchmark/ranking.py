"""Deterministic weighted lexical ranking for tool discovery benchmarks."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from math import log

from prefab_sentinel.tool_discovery_benchmark.models import (
    LexicalRanking,
    RankedTool,
    ToolRecord,
)

RANKER_ID = "weighted-idf-name3-description1.v1"
NAME_WEIGHT = 3
DESCRIPTION_WEIGHT = 1

_CAMEL_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize_tokens(value: str) -> tuple[str, ...]:
    """Normalize text into unique-independent Unicode alphanumeric tokens."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _CAMEL_ACRONYM_BOUNDARY.sub(" ", normalized)
    normalized = _CAMEL_BOUNDARY.sub(" ", normalized)

    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.append("".join(current).casefold())
            current = []
    if current:
        tokens.append("".join(current).casefold())

    return tuple(token for token in tokens if len(token) >= 2)


def build_idf(
    name_tokens: Mapping[str, frozenset[str]],
    description_tokens: Mapping[str, frozenset[str]],
) -> dict[str, float]:
    """Build document frequencies over the union of each tool's two public fields."""

    tool_names = set(name_tokens) | set(description_tokens)
    document_frequency: dict[str, int] = {}
    for tool_name in tool_names:
        for token in name_tokens.get(tool_name, frozenset()) | description_tokens.get(
            tool_name, frozenset()
        ):
            document_frequency[token] = document_frequency.get(token, 0) + 1

    count = len(tool_names)
    return {
        token: log((count + 1) / (frequency + 1)) + 1
        for token, frequency in document_frequency.items()
    }


class DeterministicLexicalRanker:
    """Rank tools using weighted field-token overlap and IDF."""

    def __init__(self, registry: Sequence[ToolRecord]) -> None:
        self._registry = tuple(registry)
        self._name_tokens = {
            tool.name: frozenset(normalize_tokens(tool.name)) for tool in self._registry
        }
        self._description_tokens = {
            tool.name: frozenset(normalize_tokens(tool.description)) for tool in self._registry
        }
        self._idf = build_idf(self._name_tokens, self._description_tokens)

    def rank(self, query: str) -> LexicalRanking:
        tokens = tuple(dict.fromkeys(normalize_tokens(query)))
        ranked_tools: list[RankedTool] = []
        for tool in self._registry:
            score = sum(
                self._idf.get(token, 0.0)
                * (
                    NAME_WEIGHT * int(token in self._name_tokens[tool.name])
                    + DESCRIPTION_WEIGHT
                    * int(token in self._description_tokens[tool.name])
                )
                for token in tokens
            )
            ranked_tools.append(RankedTool(tool.name, score))

        ranked_tools.sort(key=lambda item: (-item.score, item.name))
        return LexicalRanking(
            tokens,
            tuple(ranked_tools),
            all(item.score == 0.0 for item in ranked_tools),
        )
