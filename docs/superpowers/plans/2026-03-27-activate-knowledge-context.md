# activate_project ナレッジコンテキスト Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `activate_project` のレスポンスにプロジェクト内容に応じた knowledge ファイル一覧を返し、knowledge/*.md を MCP Resources として公開する。

**Architecture:** `ProjectSession.suggest_reads()` がキーワードマッチングロジックを持ち、`script_name_map()` の値と `collect_project_guid_index()` のアセットパスからエコシステムツールを検出する。`mcp_server.py` は activate_project レスポンスに結果を注入し、knowledge ファイルを MCP Resource として動的登録する。

**Tech Stack:** Python 3.11+, FastMCP (`@server.resource`), unittest + mock

**Spec:** `docs/superpowers/specs/2026-03-27-activate-knowledge-context-design.md`

---

## File Structure

| ファイル | 責務 | 変更種別 |
|---------|------|----------|
| `prefab_sentinel/session.py` | `suggest_reads()` メソッド — マッピングテーブル + 検出ロジック | Modify |
| `prefab_sentinel/mcp_server.py` | activate_project に suggested_reads 注入 + MCP Resources 登録 | Modify |
| `tests/test_session.py` | suggest_reads テスト群 | Modify |
| `tests/test_mcp_server.py` | Resources 登録テスト + activate_project レスポンステスト | Modify |

---

### Task 1: `suggest_reads()` — テストファースト

**Files:**
- Modify: `tests/test_session.py`
- Modify: `prefab_sentinel/session.py`

> **実装順序:** Step 3 (import 追加) は `@patch("prefab_sentinel.session.collect_project_guid_index")` デコレータの前提条件。実装時は **Step 3 → Step 1 → Step 2** の順で適用する。

- [ ] **Step 1: Write the failing tests for `suggest_reads()`**

`tests/test_session.py` の末尾に以下を追加:

```python
# ---------------------------------------------------------------------------
# suggest_reads
# ---------------------------------------------------------------------------


class TestSuggestReads(unittest.TestCase):
    """suggest_reads() returns knowledge file paths based on project content."""

    def test_always_includes_prefab_sentinel_knowledge(self) -> None:
        """Even with empty maps, prefab-sentinel's own knowledge is returned."""
        session = ProjectSession()
        reads = session.suggest_reads()
        expected_prefixes = [
            "knowledge/prefab-sentinel-editor-camera.md",
            "knowledge/prefab-sentinel-material-operations.md",
            "knowledge/prefab-sentinel-patch-patterns.md",
            "knowledge/prefab-sentinel-variant-patterns.md",
            "knowledge/prefab-sentinel-wiring-triage.md",
            "knowledge/prefab-sentinel-workflow-patterns.md",
        ]
        for path in expected_prefixes:
            self.assertIn(path, reads)

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    def test_detects_udonsharp_from_script_name_map(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        mock_build.return_value = {
            "abc123": "UdonSharpBehaviour",  # contains "UdonSharp" → match
            "def456": "MyUdonSharpScript",   # contains "UdonSharp" → match
        }
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertIn("knowledge/udonsharp.md", reads)
        self.assertIn("knowledge/vrchat-sdk-base.md", reads)

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    def test_detects_liltoon_from_guid_index(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        mock_build.return_value = {}
        mock_guid.return_value = {
            "aaa": Path("/proj/Assets/lilToon/Shader/lts.shader"),
        }
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertIn("knowledge/liltoon.md", reads)
        self.assertIn("knowledge/vrchat-sdk-base.md", reads)

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    def test_no_ecosystem_match_no_base(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        """When no ecosystem keyword matches, vrchat-sdk-base is NOT included."""
        mock_build.return_value = {"guid1": "SomeRandomScript"}
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertNotIn("knowledge/vrchat-sdk-base.md", reads)

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    def test_deduplication(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        """Same knowledge file matched by multiple keywords is returned once."""
        mock_build.return_value = {
            "g1": "UdonSharpBehaviourWrapper",
            "g2": "UdonBehaviourSync",
        }
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        count = reads.count("knowledge/udonsharp.md")
        self.assertEqual(1, count)

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    def test_sorted_prefab_sentinel_first(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        """prefab-sentinel knowledge comes before ecosystem knowledge."""
        mock_build.return_value = {"g1": "VRCAvatarDescriptor"}
        mock_guid.return_value = {}
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        ps_indices = [i for i, r in enumerate(reads) if "prefab-sentinel" in r]
        eco_indices = [i for i, r in enumerate(reads) if "prefab-sentinel" not in r]
        if ps_indices and eco_indices:
            self.assertLess(max(ps_indices), min(eco_indices))

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    def test_detects_ndmf_from_asmdef_path(
        self, mock_build: MagicMock, mock_guid: MagicMock
    ) -> None:
        mock_build.return_value = {}
        mock_guid.return_value = {
            "xyz": Path("/proj/Packages/nadena.dev.ndmf/Runtime/NDMF.asmdef"),
        }
        session = ProjectSession(project_root=Path("/fake"))
        reads = session.suggest_reads()
        self.assertIn("knowledge/ndmf.md", reads)

    def test_no_project_root_returns_only_prefab_sentinel(self) -> None:
        """Without project_root, only prefab-sentinel knowledge is returned."""
        session = ProjectSession()
        reads = session.suggest_reads()
        for r in reads:
            self.assertIn("prefab-sentinel", r)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session.py::TestSuggestReads -v`
Expected: `AttributeError: 'ProjectSession' object has no attribute 'suggest_reads'`
(Step 3 未適用の場合は `@patch` デコレータで `AttributeError: <module 'prefab_sentinel.session' does not have the attribute 'collect_project_guid_index'>` が先に発生する)

- [ ] **Step 3: Add `collect_project_guid_index` import and `_guid_index` cache field**

`prefab_sentinel/session.py` の import ブロックを修正:

```python
from prefab_sentinel.unity_assets import (
    collect_project_guid_index,
    find_project_root,
    resolve_scope_path,
)
```

`__init__` の Caches セクションに `_guid_index` を追加:

```python
        # Caches
        self._orchestrator: Phase1Orchestrator | None = None
        self._guid_index: dict[str, Path] | None = None
        self._script_name_map: dict[str, str] | None = None
        self._symbol_cache: dict[Path, _SymbolCacheEntry] = {}
```

`invalidate_guid_index()` に `_guid_index` クリアを追加:

```python
    def invalidate_guid_index(self) -> None:
        self._orchestrator = None
        self._guid_index = None
        self._script_name_map = None
        self._symbol_cache.clear()
```

`invalidate_all()` にも追加:

```python
    def invalidate_all(self) -> None:
        self._orchestrator = None
        self._guid_index = None
        self._script_name_map = None
        self._symbol_cache.clear()
```

- [ ] **Step 4: Add `guid_index()` accessor and implement `suggest_reads()`**

`prefab_sentinel/session.py` の `script_name_map()` メソッドの後 (L161 付近) に追加:

```python
    def guid_index(self) -> dict[str, Path]:
        """Return the cached GUID index, building on first call.

        Known limitation: build_script_name_map() internally calls
        collect_project_guid_index(), so the first activate_project
        scans the GUID index twice. Tracked in
        project_improve_guid_index_cache_unification.md.
        """
        if self._guid_index is None:
            if self._project_root is None:
                return {}
            self._guid_index = collect_project_guid_index(
                self._project_root, include_package_cache=False,
            )
        return self._guid_index

    # ------------------------------------------------------------------
    # Knowledge suggestions
    # ------------------------------------------------------------------

    _SELF_KNOWLEDGE: list[str] = [
        "knowledge/prefab-sentinel-editor-camera.md",
        "knowledge/prefab-sentinel-material-operations.md",
        "knowledge/prefab-sentinel-patch-patterns.md",
        "knowledge/prefab-sentinel-variant-patterns.md",
        "knowledge/prefab-sentinel-wiring-triage.md",
        "knowledge/prefab-sentinel-workflow-patterns.md",
    ]

    # Case-insensitive substring matching against script_name_map values
    # and guid_index asset path strings.
    # Note: Spec lists "liltoon" (lowercase) as separate entry, but
    # case-insensitive matching makes "lilToon" sufficient. Intentionally
    # omitted to avoid dead entry.
    _KEYWORD_TO_KNOWLEDGE: dict[str, str] = {
        "UdonSharp": "knowledge/udonsharp.md",
        "UdonBehaviour": "knowledge/udonsharp.md",
        "VRCSceneDescriptor": "knowledge/vrchat-sdk-worlds.md",
        "VRC_SceneDescriptor": "knowledge/vrchat-sdk-worlds.md",
        "VRCAvatarDescriptor": "knowledge/vrchat-sdk-avatars.md",
        "ModularAvatar": "knowledge/modular-avatar.md",
        "VRCFury": "knowledge/vrcfury.md",
        "AvatarOptimizer": "knowledge/avatar-optimizer.md",
        "lilToon": "knowledge/liltoon.md",
        "NDMF": "knowledge/ndmf.md",
        "nadena.dev.ndmf": "knowledge/ndmf.md",
    }

    def suggest_reads(self) -> list[str]:
        """Return knowledge file paths relevant to the current project.

        Combines prefab-sentinel's own knowledge (always) with ecosystem
        knowledge detected via script_name_map values and guid_index
        asset paths.
        """
        ecosystem: set[str] = set()

        script_lower = [v.lower() for v in self.script_name_map().values()]
        guid_lower = [str(p).lower() for p in self.guid_index().values()]

        for keyword, knowledge_file in self._KEYWORD_TO_KNOWLEDGE.items():
            kw_lower = keyword.lower()
            if any(kw_lower in v for v in script_lower) or any(
                kw_lower in p for p in guid_lower
            ):
                ecosystem.add(knowledge_file)

        if ecosystem:
            ecosystem.add("knowledge/vrchat-sdk-base.md")

        return sorted(self._SELF_KNOWLEDGE) + sorted(ecosystem)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_session.py::TestSuggestReads -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Run full test_session.py to verify no regressions**

Run: `uv run pytest tests/test_session.py -v`
Expected: All existing + new tests PASS

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/session.py tests/test_session.py
git commit -m "feat: add ProjectSession.suggest_reads() with keyword matching

Detects ecosystem tools (UdonSharp, liltoon, NDMF, etc.) from
script_name_map values and guid_index asset paths, returning
relevant knowledge file paths for agent context.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `activate_project` レスポンスに `suggested_reads` を注入

**Files:**
- Modify: `prefab_sentinel/mcp_server.py:131-165`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_server.py` に以下のテストクラスを追加:

```python
class TestActivateProjectSuggestedReads(unittest.TestCase):
    """activate_project response includes suggested_reads."""

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    @patch("prefab_sentinel.session.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_response_contains_suggested_reads(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
        mock_guid: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {}
        mock_guid.return_value = {}
        server = create_server()
        _, result = _run(server.call_tool("activate_project", {"scope": "Assets/MyScope"}))
        self.assertIn("suggested_reads", result["data"])
        self.assertIsInstance(result["data"]["suggested_reads"], list)
        self.assertTrue(
            any("prefab-sentinel" in r for r in result["data"]["suggested_reads"])
        )

    @patch("prefab_sentinel.session.collect_project_guid_index")
    @patch("prefab_sentinel.session.build_script_name_map")
    @patch("prefab_sentinel.session.Phase1Orchestrator")
    @patch("prefab_sentinel.session.resolve_scope_path")
    @patch("prefab_sentinel.session.find_project_root")
    def test_response_contains_knowledge_hint(
        self,
        mock_find: MagicMock,
        mock_resolve: MagicMock,
        mock_orch: MagicMock,
        mock_build: MagicMock,
        mock_guid: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/unity")
        mock_resolve.return_value = Path("/unity/Assets/MyScope")
        mock_build.return_value = {}
        mock_guid.return_value = {}
        server = create_server()
        _, result = _run(server.call_tool("activate_project", {"scope": "Assets/MyScope"}))
        self.assertIn("knowledge_hint", result["data"])
        from prefab_sentinel.mcp_server import _KNOWLEDGE_URI_PREFIX
        self.assertIn(_KNOWLEDGE_URI_PREFIX, result["data"]["knowledge_hint"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_server.py::TestActivateProjectSuggestedReads -v`
Expected: FAIL — `suggested_reads` not in `result["data"]`

- [ ] **Step 3: Add `_KNOWLEDGE_URI_PREFIX` constant and modify `activate_project` in `mcp_server.py`**

`prefab_sentinel/mcp_server.py` の `create_server()` の **前** (import の後、関数定義の前) にモジュールレベル定数を追加:

```python
_KNOWLEDGE_URI_PREFIX = "resource://prefab-sentinel/knowledge/"
```

`activate_project` 関数 (L131-165) を修正。`result = await session.activate(scope)` の後に `suggested_reads` と `knowledge_hint` を注入:

```python
    @server.tool()
    async def activate_project(
        scope: str,
    ) -> dict[str, Any]:
        """Set the project scope and warm caches for subsequent requests.

        Call this once at the start of a session to set the working scope.
        Subsequent tool calls will be faster due to cached GUID index and
        script name map.

        Args:
            scope: Path to the Assets subdirectory to work with
                (e.g. "Assets/Tyunta/SoulLinkerSystem").
        """
        result = await session.activate(scope)
        result["suggested_reads"] = session.suggest_reads()
        result["knowledge_hint"] = (
            "Other knowledge files available via Glob('knowledge/*.md') "
            f"or MCP Resources ({_KNOWLEDGE_URI_PREFIX})"
        )
        diagnostics: list[dict[str, Any]] = [
            {
                "message": (
                    f"Scope '{scope}' will be used as default for: "
                    "validate_refs, find_referencing_assets, "
                    "validate_field_rename, check_field_coverage."
                ),
                "severity": "info",
            },
        ]
        bridge_diag = session.check_bridge_version()
        if bridge_diag:
            diagnostics.append(bridge_diag)
        return {
            "success": True,
            "severity": "info",
            "code": "SESSION_ACTIVATED",
            "message": f"Project activated with scope: {scope}",
            "data": result,
            "diagnostics": diagnostics,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py::TestActivateProjectSuggestedReads -v`
Expected: Both tests PASS

- [ ] **Step 5: Run full test_mcp_server.py to verify no regressions**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: All tests PASS (tool count stays at 62 — no new tools added)

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add suggested_reads and knowledge_hint to activate_project response

Injects session.suggest_reads() into the data dict so agents know
which knowledge files to read at session start.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: MCP Resources — knowledge/*.md を動的登録

**Files:**
- Modify: `prefab_sentinel/mcp_server.py` (create_server 内)
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_server.py` に以下を追加:

```python
class TestKnowledgeResources(unittest.TestCase):
    """knowledge/*.md files are registered as MCP Resources."""

    def test_resources_registered(self) -> None:
        """At least one knowledge resource is registered."""
        server = create_server()
        resources = _run(server.list_resources())
        uris = [r.uri for r in resources]
        knowledge_uris = [u for u in uris if "knowledge/" in str(u)]
        self.assertGreater(len(knowledge_uris), 0)

    def test_resource_uri_scheme(self) -> None:
        """All knowledge resources use the expected URI scheme."""
        from prefab_sentinel.mcp_server import _KNOWLEDGE_URI_PREFIX
        server = create_server()
        resources = _run(server.list_resources())
        for r in resources:
            uri_str = str(r.uri)
            if "knowledge/" in uri_str:
                self.assertTrue(
                    uri_str.startswith(_KNOWLEDGE_URI_PREFIX),
                    f"Unexpected URI: {uri_str}",
                )
                self.assertTrue(uri_str.endswith(".md"), f"Not .md: {uri_str}")

    def test_resource_read_returns_content(self) -> None:
        """Reading a knowledge resource returns non-empty markdown text."""
        server = create_server()
        resources = _run(server.list_resources())
        knowledge_resources = [
            r for r in resources if "knowledge/" in str(r.uri)
        ]
        self.assertGreater(len(knowledge_resources), 0)
        # Read the first one
        uri = str(knowledge_resources[0].uri)
        content = _run(server.read_resource(uri))
        # content is a list with one item (text or blob)
        text = content[0].content if hasattr(content[0], "content") else str(content[0])
        self.assertGreater(len(text), 0)

    def test_resource_has_description(self) -> None:
        """Each knowledge resource has a non-empty description."""
        server = create_server()
        resources = _run(server.list_resources())
        for r in resources:
            if "knowledge/" in str(r.uri):
                self.assertTrue(
                    r.description and len(r.description) > 0,
                    f"Missing description for {r.uri}",
                )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_server.py::TestKnowledgeResources -v`
Expected: FAIL — no resources registered (empty list or `list_resources` returns nothing)

- [ ] **Step 3: Add `_extract_description` and `_KNOWLEDGE_DIR` at module level, resource registration in `create_server()`**

`prefab_sentinel/mcp_server.py` の `_KNOWLEDGE_URI_PREFIX` 定義 (Task 2 で追加済み) の **直後** にモジュールレベル関数と定数を追加:

```python
_KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


def _extract_description(path: Path) -> str:
    """Extract description from YAML frontmatter without external dependencies.

    Parses flat key-value frontmatter (no nested structures).
    Falls back to file stem if no frontmatter or relevant fields.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return path.stem
    end = text.find("---", 3)
    if end < 0:
        return path.stem
    fm: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    if "description" in fm:
        return fm["description"]
    if "tool" in fm:
        return f"{fm['tool']} knowledge"
    return path.stem
```

`create_server()` 内、`server = FastMCP(...)` ブロックの直後 (helpers セクションの前) に resource 登録を追加:

```python
    # ------------------------------------------------------------------
    # Knowledge resources (_KNOWLEDGE_DIR is module-level)
    # ------------------------------------------------------------------

    def _make_reader(file_path: Path):  # noqa: ANN202
        """Create a closure-based reader to avoid FastMCP parameter mismatch."""
        @server.resource(
            f"{_KNOWLEDGE_URI_PREFIX}{file_path.name}",
            name=file_path.stem,
            description=_extract_description(file_path),
        )
        def _read_knowledge() -> str:
            return file_path.read_text(encoding="utf-8")

    if _KNOWLEDGE_DIR.is_dir():
        for _md_file in sorted(_KNOWLEDGE_DIR.glob("*.md")):
            _make_reader(_md_file)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py::TestKnowledgeResources -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: register knowledge/*.md as MCP Resources

Dynamically registers each knowledge file as a resource with URI
resource://prefab-sentinel/knowledge/{filename}. Description is
extracted from YAML frontmatter without PyYAML dependency.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `_extract_description` のエッジケーステスト

**Files:**
- Modify: `tests/test_mcp_server.py`

`_extract_description` は Task 3 でモジュールレベルに定義済みなので、直接 import してテストできる。

- [ ] **Step 1: Write focused unit tests for frontmatter parsing**

`tests/test_mcp_server.py` に以下を追加:

```python
class TestExtractDescription(unittest.TestCase):
    """_extract_description handles various frontmatter formats."""

    def _extract(self, content: str) -> str:
        """Write content to a temp file and extract description."""
        with tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            f.flush()
            path = Path(f.name)
        try:
            from prefab_sentinel.mcp_server import _extract_description
            return _extract_description(path)
        finally:
            path.unlink(missing_ok=True)

    def test_with_description_field(self) -> None:
        content = "---\ntool: foo\ndescription: A helpful guide\n---\n# Title\n"
        self.assertEqual("A helpful guide", self._extract(content))

    def test_with_tool_field_only(self) -> None:
        content = "---\ntool: liltoon\nversion_tested: 1.0\n---\n# Title\n"
        self.assertEqual("liltoon knowledge", self._extract(content))

    def test_no_frontmatter(self) -> None:
        content = "# Just a markdown file\nSome content.\n"
        result = self._extract(content)
        # Returns the file stem (temp file name)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_incomplete_frontmatter(self) -> None:
        content = "---\ntool: broken\n# No closing delimiter\n"
        result = self._extract(content)
        self.assertIsInstance(result, str)

    def test_quoted_values_stripped(self) -> None:
        content = '---\ntool: "udonsharp"\n---\n# Title\n'
        self.assertEqual("udonsharp knowledge", self._extract(content))
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py::TestExtractDescription -v`
Expected: All 5 tests PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_mcp_server.py
git commit -m "test: add edge case tests for _extract_description frontmatter parser

Covers: description field, tool-only fallback, no frontmatter,
incomplete frontmatter, quoted values.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 統合検証 — 全テスト + lint

**Files:** (none — verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v --tb=short`
Expected: All tests PASS, no regressions

- [ ] **Step 2: Run linter**

Run: `uv run ruff check prefab_sentinel/ tests/`
Expected: No errors

- [ ] **Step 3: Verify tool count is unchanged**

Run: `uv run pytest tests/test_mcp_server.py::TestToolRegistration -v`
Expected: 62 tools (unchanged — suggest_reads はツールではなくレスポンスフィールド)

- [ ] **Step 4: Manual smoke test — `suggest_reads` の結果確認**

Run:
```bash
uv run python -c "
from prefab_sentinel.session import ProjectSession
s = ProjectSession()
print(s.suggest_reads())
"
```
Expected: prefab-sentinel knowledge ファイル 6 件のリストが出力される

- [ ] **Step 5: Manual smoke test — MCP Resources 登録確認**

Run:
```bash
uv run python -c "
import asyncio
from prefab_sentinel.mcp_server import create_server
server = create_server()
resources = asyncio.run(server.list_resources())
for r in resources:
    print(f'{r.uri}  [{r.description}]')
"
```
Expected: `resource://prefab-sentinel/knowledge/*.md` 形式で 23 件の Resources が出力される
