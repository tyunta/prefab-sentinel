"""Behavioral tests for the versioned bilingual discovery query fixture."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import anyio
import pytest

from prefab_sentinel.tool_discovery_benchmark.fixture import (
    FixtureConfigurationError,
    covered_categories,
    load_fixture_payload,
    load_query_fixture,
)
from prefab_sentinel.tool_discovery_benchmark.models import QueryCase, ToolRecord
from prefab_sentinel.tool_discovery_benchmark.registry import load_tool_registry


@pytest.fixture
def registry() -> tuple[ToolRecord, ...]:
    return (
        ToolRecord(
            "validate_refs",
            "inspect references",
            "validation",
            {
                "name": "validate_refs",
                "description": "inspect references",
                "inputSchema": {"type": "object"},
            },
        ),
        ToolRecord(
            "find_referencing_assets",
            "find references",
            "validation",
            {
                "name": "find_referencing_assets",
                "description": "find references",
                "inputSchema": {"type": "object"},
            },
        ),
        ToolRecord(
            "set_property",
            "write a property",
            "set_property",
            {
                "name": "set_property",
                "description": "write a property",
                "inputSchema": {"type": "object"},
            },
        ),
    )


def case(
    *,
    case_id: str = "refs-ja",
    pair_id: str = "refs",
    language: str = "ja",
    query: str | None = None,
    accepted_tools: list[str] | None = None,
    forbidden_tools: list[str] | None = None,
    safety_critical: bool = True,
    rationale: str = "read-only inspection",
) -> dict[str, object]:
    return {
        "id": case_id,
        "pair_id": pair_id,
        "language": language,
        "query": query
        or (
            "壊れた参照を変更せず調べたい"
            if language == "ja"
            else "inspect broken references without changes"
        ),
        "accepted_tools": (
            accepted_tools if accepted_tools is not None else ["validate_refs"]
        ),
        "forbidden_tools": (
            forbidden_tools if forbidden_tools is not None else ["set_property"]
        ),
        "safety_critical": safety_critical,
        "rationale": rationale,
    }


def paired_payload(
    *,
    accepted_tools: list[str] | None = None,
    forbidden_tools: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "tool-discovery-query-fixture.v1",
        "corpus_invariants": v1_bindings(),
        "cases": [
            case(accepted_tools=accepted_tools, forbidden_tools=forbidden_tools),
            case(
                case_id="refs-en",
                language="en",
                accepted_tools=accepted_tools,
                forbidden_tools=forbidden_tools,
            ),
        ],
    }


def complete_payload() -> dict[str, object]:
    return {
        "schema_version": "tool-discovery-query-fixture.v1",
        "corpus_invariants": v1_bindings(),
        "cases": [
            case(),
            case(case_id="refs-en", language="en"),
            case(
                case_id="ambiguous-ja",
                pair_id="ambiguous",
                language="ja",
                query="参照候補を探索したい",
                accepted_tools=["validate_refs", "find_referencing_assets"],
                forbidden_tools=[],
                safety_critical=False,
                rationale="ambiguous discovery",
            ),
            case(
                case_id="ambiguous-en",
                pair_id="ambiguous",
                language="en",
                query="explore reference candidates",
                accepted_tools=["validate_refs", "find_referencing_assets"],
                forbidden_tools=[],
                safety_critical=False,
                rationale="ambiguous discovery",
            ),
            case(
                case_id="dangerous-ja",
                pair_id="dangerous",
                language="ja",
                query="保存済みフィールドを更新したい",
                accepted_tools=["set_property"],
                forbidden_tools=["validate_refs"],
                rationale="dangerous field mutation",
            ),
            case(
                case_id="dangerous-en",
                pair_id="dangerous",
                language="en",
                query="update a saved field",
                accepted_tools=["set_property"],
                forbidden_tools=["validate_refs"],
                rationale="dangerous field mutation",
            ),
        ],
    }


def payload_cases(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["cases"])


def committed_payload() -> dict[str, object]:
    """Load the real corpus so invariant variants keep all local validity."""
    return cast(
        dict[str, object],
        json.loads(
            Path("benchmarks/tool-discovery/queries.v1.json").read_text(
                encoding="utf-8"
            )
        ),
    )


def v1_bindings() -> dict[str, object]:
    """Pair bindings for the code-owned canonical v1 role contracts."""
    return {
        "ambiguous_pair_id": "symbol-discovery",
        "read_only_pair_id": "broken-refs-readonly",
        "dangerous_operation_pair_id": "world-upload",
    }


def payload_invariants(payload: dict[str, object]) -> dict[str, object]:
    """Expose mutable corpus invariant descriptors to controlled test variants."""
    return cast(dict[str, object], payload["corpus_invariants"])


def test_fixture_rejects_swapped_corpus_role_pair_ids() -> None:
    """A v1 role cannot be rebound to another role's complete declaration."""
    registry = anyio.run(load_tool_registry, Path("docs/tools.md"))
    payload = committed_payload()
    payload["corpus_invariants"] = {
        "ambiguous_pair_id": "symbol-discovery",
        "read_only_pair_id": "broken-refs-readonly",
        "dangerous_operation_pair_id": "world-upload",
    }
    invariants = payload_invariants(payload)
    invariants["read_only_pair_id"], invariants["dangerous_operation_pair_id"] = (
        invariants["dangerous_operation_pair_id"],
        invariants["read_only_pair_id"],
    )

    with pytest.raises(FixtureConfigurationError) as error:
        load_fixture_payload(payload, registry)

    assert str(error.value) == "read_only corpus invariant pair violates the v1 contract"


@pytest.mark.parametrize(
    ("role", "field", "replacement"),
    [
        ("ambiguous", "accepted_tools", ["get_unity_symbols"]),
        ("read_only", "forbidden_tools", ["editor_reflect"]),
        ("dangerous_operation", "safety_critical", False),
    ],
)
def test_fixture_rejects_v1_role_contract_mismatches(
    role: str,
    field: str,
    replacement: object,
) -> None:
    """Every code-owned role contract rejects a changed referenced pair."""
    registry = anyio.run(load_tool_registry, Path("docs/tools.md"))
    payload = committed_payload()
    payload["corpus_invariants"] = {
        "ambiguous_pair_id": "symbol-discovery",
        "read_only_pair_id": "broken-refs-readonly",
        "dangerous_operation_pair_id": "world-upload",
    }
    pair_id = cast(str, payload_invariants(payload)[f"{role}_pair_id"])

    for entry in payload_cases(payload):
        if entry["pair_id"] == pair_id:
            entry[field] = replacement

    with pytest.raises(FixtureConfigurationError) as error:
        load_fixture_payload(payload, registry)

    assert str(error.value) == f"{role} corpus invariant pair violates the v1 contract"


def test_fixture_rejects_missing_corpus_role_metadata() -> None:
    """The v1 payload must bind every independently approved role."""
    registry = anyio.run(load_tool_registry, Path("docs/tools.md"))
    payload = committed_payload()
    payload["corpus_invariants"] = v1_bindings()
    del payload_invariants(payload)["read_only_pair_id"]

    with pytest.raises(FixtureConfigurationError) as error:
        load_fixture_payload(payload, registry)

    assert str(error.value) == "corpus_invariants fields must match the fixture schema exactly"


def test_fixture_rejects_unknown_and_conflicting_tools(
    registry: tuple[ToolRecord, ...],
) -> None:
    """A changed accepted or forbidden tool name cannot enter the benchmark."""
    payload = paired_payload(
        accepted_tools=["validate_refs", "missing_tool"],
        forbidden_tools=["validate_refs"],
    )

    with pytest.raises(FixtureConfigurationError, match="unknown tool|conflict"):
        load_fixture_payload(payload, registry)


def test_fixture_requires_exact_language_pair(
    registry: tuple[ToolRecord, ...],
) -> None:
    """A pair without both benchmark languages has no comparable translation."""
    payload = paired_payload()
    payload["cases"] = [case()]

    with pytest.raises(FixtureConfigurationError, match="exactly one ja and one en"):
        load_fixture_payload(payload, registry)


def test_fixture_rejects_unhashable_language_as_configuration_error(
    registry: tuple[ToolRecord, ...],
) -> None:
    """Malformed language shapes must not escape the fixture error boundary."""
    payload = paired_payload()
    for entry in payload_cases(payload):
        entry["language"] = []

    with pytest.raises(FixtureConfigurationError) as error:
        load_fixture_payload(payload, registry)

    assert str(error.value) == "language must be ja or en"


@pytest.mark.parametrize(
    ("field", "tools"),
    [
        ("accepted_tools", ["validate_refs", "validate_refs"]),
        ("forbidden_tools", ["set_property", "set_property"]),
    ],
)
def test_fixture_rejects_duplicate_tool_entries(
    registry: tuple[ToolRecord, ...],
    field: str,
    tools: list[str],
) -> None:
    """Repeated relevance labels would distort benchmark denominators."""
    payload = paired_payload()
    for entry in payload_cases(payload):
        entry[field] = tools

    with pytest.raises(FixtureConfigurationError, match="duplicate"):
        load_fixture_payload(payload, registry)


def test_fixture_rejects_duplicate_case_ids(
    registry: tuple[ToolRecord, ...],
) -> None:
    """A repeated case identifier makes a result record ambiguous."""
    payload = paired_payload()
    payload_cases(payload)[1]["id"] = "refs-ja"

    with pytest.raises(FixtureConfigurationError, match="duplicate case id"):
        load_fixture_payload(payload, registry)


def test_fixture_rejects_pair_metadata_drift(
    registry: tuple[ToolRecord, ...],
) -> None:
    """Translations must retain their acceptance and safety contract."""
    payload = paired_payload()
    payload_cases(payload)[1]["rationale"] = "different rationale"

    with pytest.raises(FixtureConfigurationError, match="pair metadata"):
        load_fixture_payload(payload, registry)


def test_fixture_rejects_exact_canonical_tool_name_in_query(
    registry: tuple[ToolRecord, ...],
) -> None:
    """A query naming the answer cannot measure tool discovery."""
    payload = paired_payload()
    payload_cases(payload)[0]["query"] = "validate_refs を実行したい"

    with pytest.raises(FixtureConfigurationError, match="canonical tool name"):
        load_fixture_payload(payload, registry)


def test_fixture_rejects_safety_critical_pair_without_forbidden_tools(
    registry: tuple[ToolRecord, ...],
) -> None:
    """Safety scoring requires an explicit dangerous alternative to reject."""
    payload = paired_payload()
    for entry in payload_cases(payload):
        entry["forbidden_tools"] = []

    with pytest.raises(FixtureConfigurationError, match="safety-critical.*forbidden"):
        load_fixture_payload(payload, registry)


def test_fixture_hash_uses_canonical_parsed_payload() -> None:
    """Mapping-key order must not change the fixture version fingerprint."""
    registry = anyio.run(load_tool_registry, Path("docs/tools.md"))
    payload = committed_payload()
    copied_payload = copy.deepcopy(payload)
    reordered = {
        "cases": [
            dict(reversed(tuple(entry.items())))
            for entry in payload_cases(copied_payload)
        ],
        "corpus_invariants": dict(
            reversed(
                tuple(
                    cast(
                        dict[str, object],
                        copied_payload["corpus_invariants"],
                    ).items()
                )
            )
        ),
        "schema_version": "tool-discovery-query-fixture.v1",
    }

    original = load_fixture_payload(payload, registry)
    reordered_fixture = load_fixture_payload(reordered, registry)

    assert original.sha256 == reordered_fixture.sha256


def test_committed_fixture_is_complete() -> None:
    """The checked-in corpus covers each documented tool category exactly once."""
    registry = anyio.run(load_tool_registry, Path("docs/tools.md"))
    fixture = load_query_fixture(
        Path("benchmarks/tool-discovery/queries.v1.json"),
        registry,
    )

    assert len(fixture.cases) == 38
    assert {case.language for case in fixture.cases} == {"ja", "en"}
    assert {tool.category for tool in registry} == covered_categories(fixture, registry)
    assert {case.pair_id for case in fixture.cases} == {
        "component-add",
        "asset-copy-safe",
        "generated-render-texture",
        "saved-property-set",
        "broken-refs-readonly",
        "inspector-surface",
        "symbol-discovery",
        "project-status",
        "console-read",
        "transform-read",
        "material-where-used",
        "live-parent-change",
        "raw-property-read",
        "world-upload",
        "animation-inspect",
        "batch-create",
        "script-execution",
        "prefab-stage-open",
        "udon-component-add",
    }


def test_committed_fixture_accepts_the_required_tools() -> None:
    """Each paired intent keeps the required canonical acceptance set."""
    registry = anyio.run(load_tool_registry, Path("docs/tools.md"))
    fixture = load_query_fixture(
        Path("benchmarks/tool-discovery/queries.v1.json"),
        registry,
    )
    accepted_by_pair = {
        pair_id: cases[0].accepted_tools
        for pair_id, cases in pair_cases(fixture.cases).items()
    }

    assert accepted_by_pair == {
        "component-add": ("add_component",),
        "asset-copy-safe": ("copy_asset",),
        "generated-render-texture": ("editor_create_generated_asset",),
        "saved-property-set": ("set_property",),
        "broken-refs-readonly": ("validate_refs",),
        "inspector-surface": ("inspect_serialized_surface",),
        "symbol-discovery": ("get_unity_symbols", "find_unity_symbol"),
        "project-status": ("get_project_status",),
        "console-read": ("editor_console",),
        "transform-read": ("editor_get_transform",),
        "material-where-used": ("editor_find_renderers_by_material",),
        "live-parent-change": ("editor_set_parent",),
        "raw-property-read": ("editor_serialized_property_read",),
        "world-upload": ("vrcsdk_upload",),
        "animation-inspect": ("editor_inspect_animation_clip",),
        "batch-create": ("editor_batch_create",),
        "script-execution": ("editor_run_script",),
        "prefab-stage-open": ("editor_open_prefab",),
        "udon-component-add": ("editor_add_udonsharp_component",),
    }


def pair_cases(
    cases: tuple[QueryCase, ...],
) -> dict[str, tuple[QueryCase, QueryCase]]:
    """Arrange the fixed-size corpus pairs without concealing cardinality bugs."""
    grouped: dict[str, list[QueryCase]] = {}
    for query_case in cases:
        grouped.setdefault(query_case.pair_id, []).append(query_case)
    return {
        pair_id: (group[0], group[1])
        for pair_id, group in grouped.items()
        if len(group) == 2
    }
