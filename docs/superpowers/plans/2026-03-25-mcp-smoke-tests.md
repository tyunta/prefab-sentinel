# MCP Auto Smoke Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MCP ツールを mock なしで実 YAML fixtures に対して実行し、レスポンス構造とデータ正確性を検証するスモークテストを追加する。

**Architecture:** `tests/fixtures/smoke/` に静的 YAML fixture を 3 ファイル配置。`tests/test_mcp_smoke.py` で `create_server()` + `call_tool()` を mock なしで実行し、コア 5 ツール（inspect_wiring, validate_refs, inspect_hierarchy, validate_structure, get_unity_symbols）をエンドツーエンドで検証する。

**Tech Stack:** Python (unittest), prefab_sentinel.mcp_server, tests/yaml_helpers.py

**Spec:** `docs/superpowers/specs/2026-03-25-mcp-smoke-tests-design.md`

**重要な API メモ:**
- `create_server()` → FastMCP インスタンス。`call_tool(name, params)` は `(metadata, result_dict)` タプルを返す。
- `validate_refs` のパラメータ名は `scope`（他ツールは `asset_path`）。`details=True` で diagnostics リストにエントリが入る。
- `inspect_hierarchy` のレスポンスは `data.roots`（`data.hierarchy` ではない）。
- `get_unity_symbols` は direct-payload（envelope なし）。`symbols` キーにルートレベル GameObject のみ含まれる。
- `activate_project` のパラメータ名は `scope`。

---

### Task 1: Fixture ファイル作成

**Files:**
- Create: `tests/fixtures/smoke/basic.prefab`
- Create: `tests/fixtures/smoke/broken_ref.prefab`
- Create: `tests/fixtures/smoke/hierarchy.prefab`

fixture は `tests/yaml_helpers.py` のヘルパーで生成した YAML を静的ファイルとして保存する。各 fixture の期待値はテストで使うので正確に把握すること。

- [ ] **Step 1: `basic.prefab` を生成・保存**

以下のスクリプトで生成し、出力を `tests/fixtures/smoke/basic.prefab` に保存する:

```bash
mkdir -p tests/fixtures/smoke
uv run python -c "
from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour
text = (
    YAML_HEADER
    + make_gameobject('100', 'BasicObj', ['200'])
    + make_monobehaviour('200', '100', fields={'validRef': '{fileID: 100}', 'nullRef': '{fileID: 0}'})
)
print(text, end='')
" > tests/fixtures/smoke/basic.prefab
```

期待値メモ:
- inspect_wiring: components=1, null_ratio="1/2", null_field_names=["nullRef"]
- validate_structure: success=True (fileID 重複なし)
- get_unity_symbols: symbols に "BasicObj" が含まれる

- [ ] **Step 2: `broken_ref.prefab` を生成・保存**

```bash
uv run python -c "
from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_monobehaviour
text = (
    YAML_HEADER
    + make_gameobject('100', 'BrokenObj', ['200'])
    + make_monobehaviour('200', '100', fields={'goodRef': '{fileID: 100}', 'badRef': '{fileID: 99999}'})
)
print(text, end='')
" > tests/fixtures/smoke/broken_ref.prefab
```

期待値メモ:
- validate_refs (details=True): diagnostics に missing_local_id カテゴリのエントリが 1 件以上
- inspect_wiring: internal_broken_ref_count >= 1

- [ ] **Step 3: `hierarchy.prefab` を生成・保存**

```bash
uv run python -c "
from tests.yaml_helpers import YAML_HEADER, make_gameobject, make_transform
text = (
    YAML_HEADER
    + make_gameobject('100', 'Root', ['101'])
    + make_transform('101', '100', father_file_id='0', children_file_ids=['201'])
    + make_gameobject('200', 'Child', ['201'])
    + make_transform('201', '200', father_file_id='101', children_file_ids=['301'])
    + make_gameobject('300', 'GrandChild', ['301'])
    + make_transform('301', '300', father_file_id='201')
)
print(text, end='')
" > tests/fixtures/smoke/hierarchy.prefab
```

期待値メモ:
- inspect_hierarchy: data.roots[0].name == "Root"、子孫に "Child", "GrandChild"
- get_unity_symbols: ルートレベルの symbols に "Root" のみ含まれる（Child/GrandChild は Transform 配線で子ノード扱い）

- [ ] **Step 4: fixture ファイルの内容確認**

```bash
cat tests/fixtures/smoke/basic.prefab
cat tests/fixtures/smoke/broken_ref.prefab
cat tests/fixtures/smoke/hierarchy.prefab
```

各ファイルが正しい YAML ヘッダー (`%YAML 1.1`) と期待するブロック構造を持っていることを確認する。

- [ ] **Step 5: コミット**

```
feat(test): add smoke test YAML fixtures
```

---

### Task 2: `test_mcp_smoke.py` — inspect_wiring テスト

**Files:**
- Create: `tests/test_mcp_smoke.py`

- [ ] **Step 1: テストモジュール作成 + inspect_wiring テスト 3 件**

`tests/test_mcp_smoke.py` を新規作成:

```python
"""Smoke tests that exercise MCP tools against real YAML fixtures (no mocks)."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from typing import Any

from prefab_sentinel.mcp_server import create_server

FIXTURES = Path(__file__).parent / "fixtures" / "smoke"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class McpSmokeTests(unittest.TestCase):
    """End-to-end smoke tests for MCP tools against static YAML fixtures."""

    server: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server()

    # --- inspect_wiring ---

    def test_inspect_wiring_envelope_structure(self) -> None:
        """inspect_wiring returns a well-formed envelope response."""
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])

    def test_inspect_wiring_null_ratio_correct(self) -> None:
        """basic.prefab has 1 null ref out of 2 fields → null_ratio='1/2'."""
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        comps = result["data"]["components"]
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["null_ratio"], "1/2")

    def test_inspect_wiring_null_field_names_correct(self) -> None:
        """basic.prefab null_field_names should be ['nullRef']."""
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        comps = result["data"]["components"]
        self.assertEqual(comps[0]["null_field_names"], ["nullRef"])
```

- [ ] **Step 2: テスト実行 — PASS 確認**

Run: `uv run --extra test python -m unittest tests.test_mcp_smoke.McpSmokeTests -v`
Expected: 3 tests PASS

- [ ] **Step 3: コミット**

```
feat(test): add MCP smoke tests for inspect_wiring
```

---

### Task 3: validate_refs テスト追加

**Files:**
- Modify: `tests/test_mcp_smoke.py`

- [ ] **Step 1: validate_refs テスト 2 件追加**

`McpSmokeTests` クラスに追加。**注意**: `validate_refs` のパラメータ名は `scope`（`asset_path` ではない）。`details=True` を渡さないと diagnostics リストが空になる。

```python
    # --- validate_refs (parameter: scope, not asset_path) ---

    def test_validate_refs_detects_broken_ref(self) -> None:
        """broken_ref.prefab has fileID:99999 that does not exist."""
        _, result = _run(self.server.call_tool(
            "validate_refs",
            {"scope": str(FIXTURES / "broken_ref.prefab"), "details": True},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        # broken_ref.prefab should have at least 1 broken reference diagnostic
        broken_local = [
            d for d in result["diagnostics"]
            if d.get("detail", "").startswith("missing_local_id")
        ]
        self.assertGreater(len(broken_local), 0)

    def test_validate_refs_clean_file_no_broken_local_ids(self) -> None:
        """basic.prefab has no broken internal fileID references."""
        _, result = _run(self.server.call_tool(
            "validate_refs",
            {"scope": str(FIXTURES / "basic.prefab"), "details": True},
        ))
        self.assertIn("success", result)
        # basic.prefab: all internal fileIDs resolve correctly.
        # m_Script GUID may be flagged as missing_asset (no .meta files),
        # but internal fileID references should all be valid.
        broken_local = [
            d for d in result.get("diagnostics", [])
            if d.get("detail", "").startswith("missing_local_id")
        ]
        self.assertEqual(len(broken_local), 0)
```

- [ ] **Step 2: テスト実行 — PASS 確認**

Run: `uv run --extra test python -m unittest tests.test_mcp_smoke.McpSmokeTests.test_validate_refs_detects_broken_ref tests.test_mcp_smoke.McpSmokeTests.test_validate_refs_clean_file_no_broken_local_ids -v`
Expected: 2 tests PASS

テストが FAIL した場合: `validate_refs` のレスポンス構造を `print(result)` で確認し、diagnostics のフィルタ条件を調整する。`Diagnostic.detail` はカテゴリ文字列（`missing_local_id`, `missing_asset` 等）。

- [ ] **Step 3: コミット**

```
feat(test): add MCP smoke tests for validate_refs
```

---

### Task 4: inspect_hierarchy + validate_structure + get_unity_symbols テスト追加

**Files:**
- Modify: `tests/test_mcp_smoke.py`

- [ ] **Step 1: inspect_hierarchy テスト追加**

```python
    # --- inspect_hierarchy ---

    def test_inspect_hierarchy_returns_root(self) -> None:
        """hierarchy.prefab has Root as the root node."""
        _, result = _run(self.server.call_tool(
            "inspect_hierarchy",
            {"asset_path": str(FIXTURES / "hierarchy.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])
        roots = result["data"]["roots"]
        self.assertGreater(len(roots), 0)
        self.assertEqual(roots[0]["name"], "Root")
```

- [ ] **Step 2: validate_structure テスト追加**

```python
    # --- validate_structure ---

    def test_validate_structure_clean_file(self) -> None:
        """hierarchy.prefab should pass structure validation (no dup fileIDs)."""
        _, result = _run(self.server.call_tool(
            "validate_structure",
            {"asset_path": str(FIXTURES / "hierarchy.prefab")},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result, f"Missing envelope key: {key}")
        self.assertTrue(result["success"])

    def test_validate_structure_basic_file(self) -> None:
        """basic.prefab should also pass structure validation."""
        _, result = _run(self.server.call_tool(
            "validate_structure",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        self.assertIn("success", result)
```

- [ ] **Step 3: get_unity_symbols テスト追加**

```python
    # --- get_unity_symbols ---

    def test_get_unity_symbols_returns_symbols(self) -> None:
        """basic.prefab should return symbols with BasicObj."""
        _, result = _run(self.server.call_tool(
            "get_unity_symbols",
            {"asset_path": str(FIXTURES / "basic.prefab")},
        ))
        # direct-payload format: no envelope
        self.assertNotIn("success", result)
        self.assertIn("symbols", result)
        names = [s["name"] for s in result["symbols"]]
        self.assertIn("BasicObj", names)

    def test_get_unity_symbols_hierarchy_root(self) -> None:
        """hierarchy.prefab root-level symbols should contain Root."""
        _, result = _run(self.server.call_tool(
            "get_unity_symbols",
            {"asset_path": str(FIXTURES / "hierarchy.prefab")},
        ))
        # Root is the only root-level GO (Child/GrandChild are nested via Transform)
        root_names = [s["name"] for s in result["symbols"]]
        self.assertIn("Root", root_names)
```

- [ ] **Step 4: テスト実行 — 全テスト PASS 確認**

Run: `uv run --extra test python -m unittest tests.test_mcp_smoke.McpSmokeTests -v`
Expected: 全テスト (10 件) PASS

- [ ] **Step 5: コミット**

```
feat(test): add MCP smoke tests for hierarchy, structure, symbols
```

---

### Task 5: 外部プロジェクトテストクラス + 検証

**Files:**
- Modify: `tests/test_mcp_smoke.py`
- Modify: `tasks/todo.md`

- [ ] **Step 1: McpSmokeExternalTests クラス追加**

`tests/test_mcp_smoke.py` の末尾に追加:

```python
@unittest.skipUnless(os.environ.get("SMOKE_PROJECT_ROOT"), "no external project")
class McpSmokeExternalTests(unittest.TestCase):
    """Smoke tests against a real Unity project (opt-in via SMOKE_PROJECT_ROOT env var).

    These tests validate response structure only — no fixture-specific value assertions.
    """

    server: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server()
        cls.project_root = os.environ["SMOKE_PROJECT_ROOT"]
        _run(cls.server.call_tool(
            "activate_project", {"scope": cls.project_root},
        ))

    def test_validate_refs_structure(self) -> None:
        _, result = _run(self.server.call_tool(
            "validate_refs",
            {"scope": self.project_root},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result)

    def test_inspect_wiring_structure(self) -> None:
        import glob
        prefabs = glob.glob(
            os.path.join(self.project_root, "**", "*.prefab"),
            recursive=True,
        )
        if not prefabs:
            self.skipTest("no .prefab files in project")
        _, result = _run(self.server.call_tool(
            "inspect_wiring",
            {"asset_path": prefabs[0]},
        ))
        for key in ("success", "severity", "code", "data", "diagnostics"):
            self.assertIn(key, result)
```

- [ ] **Step 2: 外部テストがスキップされることを確認**

Run: `uv run --extra test python -m unittest tests.test_mcp_smoke.McpSmokeExternalTests -v`
Expected: skipped (no external project)

- [ ] **Step 3: 全テスト実行 — 回帰なし確認**

Run: `uv run --extra test python scripts/run_unit_tests.py`
Expected: 全テスト PASS（既存 1124 件 + 新規 ~10 件）

- [ ] **Step 4: tasks/todo.md に完了記録**

- [ ] **Step 5: コミット**

```
feat(test): add external project smoke tests + record completion
```

---
