"""Load the canonical public MCP tool registry for offline benchmarks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from prefab_sentinel.mcp_server import create_server
from prefab_sentinel.tool_discovery_benchmark.models import ToolRecord

_CATALOG_HEADING = "## 全ツール一覧（カテゴリ別）"
_CATEGORY_HEADING_RE = re.compile(r"^### (?P<category>[a-z][a-z0-9_]*)$")
_ROW_RE = re.compile(
    r"^\| `(?P<name>[a-z][a-z0-9_]*)` \| "
    r"(?P<category>[a-z][a-z0-9_]*) \| "
    r"(?P<description>[^|]+) \| "
    r"(?P<issue>[^|]+) \| "
    r"(?P<kind>[^|]+) \|$"
)
_SEPARATOR_ROW_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")
_HEADER_ROW = "| ツール | 区分 | 簡潔説明 | 関連 issue | 種別 |"


class RegistryConfigurationError(ValueError):
    """The executable tool surface and canonical catalog cannot be joined."""


def parse_catalog(text: str) -> Mapping[str, str]:
    """Parse the canonical category table from ``docs/tools.md`` fail-closed."""
    entered_catalog = False
    active_category: str | None = None
    categories: dict[str, set[str]] = {}
    tools: dict[str, str] = {}

    for line in text.splitlines():
        if not entered_catalog:
            if line == _CATALOG_HEADING:
                entered_catalog = True
            continue

        if line.startswith("## "):
            break

        category_heading = _CATEGORY_HEADING_RE.fullmatch(line)
        if category_heading is not None:
            category = category_heading["category"]
            active_category = category
            if category in categories:
                raise RegistryConfigurationError("duplicate catalog category heading")
            categories[category] = set()
            continue

        if not line.startswith("|"):
            continue
        if active_category is None:
            raise RegistryConfigurationError("catalog row appears before category heading")
        if line == _HEADER_ROW or _SEPARATOR_ROW_RE.fullmatch(line) is not None:
            continue

        row = _ROW_RE.fullmatch(line)
        if row is None:
            raise RegistryConfigurationError("catalog row does not match strict grammar")
        name = row["name"]
        category = row["category"]
        if category != active_category:
            raise RegistryConfigurationError("catalog row category cell differs from heading")
        if name in tools:
            raise RegistryConfigurationError("duplicate tool row")
        tools[name] = category
        categories[category].add(name)

    if not entered_catalog:
        raise RegistryConfigurationError("catalog heading is missing")
    if len(categories) != 19 or any(not names for names in categories.values()):
        raise RegistryConfigurationError("catalog must define exactly 19 non-empty categories")
    return tools


def build_registry(
    public_tools: Sequence[Any],
    catalog_text: str,
) -> tuple[ToolRecord, ...]:
    """Serialize public tools only after their canonical categories fully agree."""
    categories = parse_catalog(catalog_text)
    by_name: dict[str, Mapping[str, Any]] = {}
    for tool in public_tools:
        definition = tool.model_dump(by_alias=True, exclude_none=True)
        name = definition.get("name")
        description = definition.get("description")
        if not isinstance(name, str) or not name:
            raise RegistryConfigurationError("registered tool name is invalid")
        if not isinstance(description, str) or not description.strip():
            raise RegistryConfigurationError("registered tool description is invalid")
        if name in by_name:
            raise RegistryConfigurationError("registered tool name is duplicated")
        by_name[name] = definition
    if set(by_name) != set(categories):
        raise RegistryConfigurationError("registered tools and catalog tools differ")
    return tuple(
        ToolRecord(name, str(by_name[name]["description"]), categories[name], by_name[name])
        for name in sorted(by_name)
    )


async def load_tool_registry(tools_doc_path: Path) -> tuple[ToolRecord, ...]:
    """Load the live public definition set and its documented canonical categories."""
    tools = await create_server().list_tools()
    return build_registry(tools, tools_doc_path.read_text(encoding="utf-8"))
