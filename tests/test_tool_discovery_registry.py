"""Regression tests for the offline tool-discovery registry."""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from prefab_sentinel.tool_discovery_benchmark.registry import (
    RegistryConfigurationError,
    load_tool_registry,
    parse_catalog,
)


def test_catalog_join_rejects_duplicate_rows() -> None:
    """A duplicated catalog tool must never be silently overwritten."""
    text = """## 全ツール一覧（カテゴリ別）
### validation
| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |
|---|---|---|---|---|
| `validate_refs` | validation | refs | - | read-only |
| `validate_refs` | validation | refs | - | read-only |
"""
    with pytest.raises(RegistryConfigurationError, match="duplicate tool row"):
        parse_catalog(text)


@pytest.mark.parametrize(
    ("catalog_text", "message"),
    [
        (
            "### validation\n| `validate_refs` | symbols | x | - | read-only |",
            "category cell",
        ),
        (
            "### validation\n| malformed | validation | x | - | read-only |",
            "catalog row",
        ),
    ],
)
def test_catalog_parser_is_fail_closed(catalog_text: str, message: str) -> None:
    """Malformed category rows must not produce an incomplete benchmark."""
    with pytest.raises(RegistryConfigurationError, match=message):
        parse_catalog(
            "## 全ツール一覧（カテゴリ別）\n" + catalog_text
        )


def test_real_registry_joins_101_tools_into_19_categories() -> None:
    """The benchmark boundary serializes the whole public tool surface."""
    registry = anyio.run(load_tool_registry, Path("docs/tools.md"))
    assert len(registry) == 101
    assert len({tool.category for tool in registry}) == 19
    assert [tool.name for tool in registry] == sorted(tool.name for tool in registry)
