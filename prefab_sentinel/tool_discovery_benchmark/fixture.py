"""Fail-closed loader for the versioned tool-discovery query corpus."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from prefab_sentinel.tool_discovery_benchmark.models import (
    QueryCase,
    QueryFixture,
    ToolRecord,
)

_SCHEMA_VERSION = "tool-discovery-query-fixture.v1"
_CASE_FIELDS = {
    "id",
    "pair_id",
    "language",
    "query",
    "accepted_tools",
    "forbidden_tools",
    "safety_critical",
    "rationale",
}
_FIXTURE_FIELDS = {"schema_version", "corpus_invariants", "cases"}
_CORPUS_INVARIANT_FIELDS = {
    "ambiguous_pair_id",
    "read_only_pair_id",
    "dangerous_operation_pair_id",
}
# Canonical v1 benchmark semantics are intentionally code-owned: pair IDs may
# move, but role-specific tool/risk contracts must not self-attest in the fixture.
_V1_CORPUS_ROLE_CONTRACTS = {
    "ambiguous": (("get_unity_symbols", "find_unity_symbol"), (), False),
    "read_only": (("validate_refs",), ("patch_apply", "set_property"), True),
    "dangerous_operation": (("vrcsdk_upload",), ("editor_reflect",), True),
}


class FixtureConfigurationError(ValueError):
    """The benchmark fixture cannot provide an unambiguous measurement."""


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FixtureConfigurationError(f"{field} must be a non-empty string")
    return value


def _tool_names(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(name, str) or not name.strip() for name in value
    ):
        raise FixtureConfigurationError(f"{field} must be a list of non-empty strings")
    names = tuple(cast(list[str], value))
    if len(names) != len(set(names)):
        raise FixtureConfigurationError(f"{field} contains duplicate tool entries")
    return names


def _parse_case(
    value: object,
    known_tools: set[str],
) -> QueryCase:
    if not isinstance(value, Mapping) or set(value) != _CASE_FIELDS:
        raise FixtureConfigurationError("case fields must match the fixture schema exactly")

    case_id = _nonempty_string(value["id"], "id")
    pair_id = _nonempty_string(value["pair_id"], "pair_id")
    language = value["language"]
    if not isinstance(language, str) or language not in {"ja", "en"}:
        raise FixtureConfigurationError("language must be ja or en")

    query = _nonempty_string(value["query"], "query")
    accepted_tools = _tool_names(value["accepted_tools"], "accepted_tools")
    forbidden_tools = _tool_names(value["forbidden_tools"], "forbidden_tools")
    if not accepted_tools:
        raise FixtureConfigurationError("accepted_tools must not be empty")

    unknown_tools = (set(accepted_tools) | set(forbidden_tools)) - known_tools
    if unknown_tools:
        raise FixtureConfigurationError("fixture refers to an unknown tool")
    if set(accepted_tools) & set(forbidden_tools):
        raise FixtureConfigurationError("accepted_tools and forbidden_tools conflict")
    if any(tool_name in query for tool_name in known_tools):
        raise FixtureConfigurationError("query contains an exact canonical tool name")

    safety_critical = value["safety_critical"]
    if type(safety_critical) is not bool:
        raise FixtureConfigurationError("safety_critical must be a boolean")
    if safety_critical and not forbidden_tools:
        raise FixtureConfigurationError(
            "safety-critical cases require forbidden tools"
        )

    return QueryCase(
        case_id=case_id,
        pair_id=pair_id,
        language=cast(Literal["ja", "en"], language),
        query=query,
        accepted_tools=accepted_tools,
        forbidden_tools=forbidden_tools,
        safety_critical=safety_critical,
        rationale=_nonempty_string(value["rationale"], "rationale"),
    )


def _validate_pairs(cases: Sequence[QueryCase]) -> None:
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise FixtureConfigurationError("duplicate case id")

    by_pair: dict[str, list[QueryCase]] = {}
    for case in cases:
        by_pair.setdefault(case.pair_id, []).append(case)

    for pair in by_pair.values():
        if len(pair) != 2 or {case.language for case in pair} != {"ja", "en"}:
            raise FixtureConfigurationError(
                "each pair must contain exactly one ja and one en case"
            )
        first, second = pair
        if (
            first.accepted_tools,
            first.forbidden_tools,
            first.safety_critical,
            first.rationale,
        ) != (
            second.accepted_tools,
            second.forbidden_tools,
            second.safety_critical,
            second.rationale,
        ):
            raise FixtureConfigurationError("ja/en pair metadata drift")


def _validate_corpus_invariants(
    cases: Sequence[QueryCase],
    value: object,
) -> None:
    if not isinstance(value, Mapping) or set(value) != _CORPUS_INVARIANT_FIELDS:
        raise FixtureConfigurationError(
            "corpus_invariants fields must match the fixture schema exactly"
        )

    pair_ids = {
        field: _nonempty_string(value[field], field)
        for field in _CORPUS_INVARIANT_FIELDS
    }
    if len(set(pair_ids.values())) != len(pair_ids):
        raise FixtureConfigurationError("corpus invariant pair IDs must be distinct")

    cases_by_pair = {case.pair_id: case for case in cases}
    for role, expected in _V1_CORPUS_ROLE_CONTRACTS.items():
        pair = cases_by_pair.get(pair_ids[f"{role}_pair_id"])
        if pair is None:
            raise FixtureConfigurationError(
                "corpus invariant pair ID must identify a bilingual pair"
            )
        if (
            pair.accepted_tools,
            pair.forbidden_tools,
            pair.safety_critical,
        ) != expected:
            raise FixtureConfigurationError(
                f"{role} corpus invariant pair violates the v1 contract"
            )

    if len(_V1_CORPUS_ROLE_CONTRACTS["ambiguous"][0]) < 2:
        raise AssertionError("v1 ambiguous role must accept multiple tools")


def covered_categories(
    fixture: QueryFixture,
    registry: Sequence[ToolRecord],
) -> set[str]:
    """Return catalog categories reached by a fixture's accepted tools."""
    category_by_tool = {tool.name: tool.category for tool in registry}
    return {
        category_by_tool[tool_name]
        for case in fixture.cases
        for tool_name in case.accepted_tools
    }


def load_fixture_payload(
    payload: object,
    registry: Sequence[ToolRecord],
) -> QueryFixture:
    """Parse, validate, and fingerprint an already-decoded fixture payload."""
    if not isinstance(payload, Mapping) or set(payload) != _FIXTURE_FIELDS:
        raise FixtureConfigurationError("fixture fields must match the schema exactly")
    if payload["schema_version"] != _SCHEMA_VERSION:
        raise FixtureConfigurationError("unsupported fixture schema version")
    cases_payload = payload["cases"]
    if not isinstance(cases_payload, list) or not cases_payload:
        raise FixtureConfigurationError("cases must be a non-empty list")

    registry_names = [tool.name for tool in registry]
    if len(registry_names) != len(set(registry_names)):
        raise FixtureConfigurationError("registry contains duplicate tool names")
    known_tools = set(registry_names)
    cases = tuple(_parse_case(case, known_tools) for case in cases_payload)
    _validate_pairs(cases)
    _validate_corpus_invariants(cases, payload["corpus_invariants"])

    provisional_fixture = QueryFixture(
        schema_version=_SCHEMA_VERSION,
        cases=cases,
        sha256="",
    )
    registry_categories = {tool.category for tool in registry}
    if covered_categories(provisional_fixture, registry) != registry_categories:
        raise FixtureConfigurationError(
            "fixture accepted tools do not provide complete category coverage"
        )

    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return QueryFixture(
        schema_version=_SCHEMA_VERSION,
        cases=cases,
        sha256=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
    )


def load_query_fixture(
    path: Path,
    registry: Sequence[ToolRecord],
) -> QueryFixture:
    """Load a JSON fixture while preserving payload-based fingerprinting."""
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureConfigurationError("fixture JSON could not be loaded") from error
    return load_fixture_payload(payload, registry)
