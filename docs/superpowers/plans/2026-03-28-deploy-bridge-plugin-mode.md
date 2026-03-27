# deploy_bridge プラグインモード対応 + BridgeVersion 自動同期 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `deploy_bridge` を Plugin モード (wheel install) でも動作させ、BridgeVersion を Python バージョンと自動同期する。

**Architecture:** hatch `force-include` で `tools/unity/` を wheel にバンドルし、`deploy_bridge` のパス解決を環境検出方式 (`_bridge_files` → `tools/unity/`) に変更する。`bump-my-version` に C# ファイルを追加し、pre-commit hook で自動同期する。

**Tech Stack:** Python 3.11+, hatch (force-include), bump-my-version, unittest

**Spec:** `docs/superpowers/specs/2026-03-28-deploy-bridge-plugin-mode-design.md`

---

## File Structure

| ファイル | 責務 | 変更種別 |
|---------|------|----------|
| `pyproject.toml` | `force-include` 追加 + bumpversion エントリ追加 | Modify |
| `prefab_sentinel/mcp_server.py` | `deploy_bridge` パス解決 + docstring | Modify |
| `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` | BridgeVersion 初期同期 | Modify |
| `.git/hooks/pre-commit` | C# ファイルのステージング追加 | Modify |
| `CLAUDE.md` | バージョン記述箇所を 3 箇所に更新 | Modify |
| `tests/test_mcp_server.py` | パス解決テスト追加 | Modify |

---

### Task 1: BridgeVersion 初期同期 + bumpversion 設定 + pre-commit hook

**Files:**
- Modify: `tools/unity/PrefabSentinel.UnityEditorControlBridge.cs:20`
- Modify: `pyproject.toml:49-66`
- Modify: `.git/hooks/pre-commit:19`
- Modify: `CLAUDE.md:67`

- [ ] **Step 1: C# の BridgeVersion を current_version に合わせる**

`tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` L20:

```csharp
        public const string BridgeVersion = "0.5.139";
```

(現在の `pyproject.toml` の `current_version` に合わせる。コミット時に pre-commit で次のパッチにバンプされる)

- [ ] **Step 2: pyproject.toml に force-include と bumpversion エントリを追加**

`pyproject.toml` の `[tool.hatch.build.targets.wheel]` セクションの後に追加:

```toml
[tool.hatch.build.targets.wheel.force-include]
"tools/unity" = "prefab_sentinel/_bridge_files"
```

`[[tool.bumpversion.files]]` ブロックの末尾に追加:

```toml
[[tool.bumpversion.files]]
filename = "tools/unity/PrefabSentinel.UnityEditorControlBridge.cs"
search = 'BridgeVersion = "{current_version}"'
replace = 'BridgeVersion = "{new_version}"'
```

- [ ] **Step 3: pre-commit hook に C# ファイルのステージングを追加**

`.git/hooks/pre-commit` L19:

```bash
git add pyproject.toml .claude-plugin/plugin.json uv.lock tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
```

- [ ] **Step 4: CLAUDE.md のバージョン管理セクションを更新**

`CLAUDE.md` L67:

```markdown
- バージョン記述箇所は `pyproject.toml`、`.claude-plugin/plugin.json`、`tools/unity/PrefabSentinel.UnityEditorControlBridge.cs` の 3 箇所（`[tool.bumpversion]` で一括管理）。
```

- [ ] **Step 5: Commit (bumpversion が 3 箇所を同期することを確認)**

```bash
git add tools/unity/PrefabSentinel.UnityEditorControlBridge.cs pyproject.toml CLAUDE.md
git commit -m "feat: auto-sync BridgeVersion via bump-my-version

Add C# BridgeVersion to bump-my-version config and pre-commit
hook staging. Align BridgeVersion to current Python version.
Add hatch force-include for wheel bundling of tools/unity/.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

Expected: pre-commit hook がパッチバンプを実行し、`pyproject.toml`、`plugin.json`、C# ファイルの 3 箇所が同一バージョンになる。

- [ ] **Step 6: バージョン同期を確認**

```bash
grep 'current_version' pyproject.toml
grep 'BridgeVersion' tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
grep '"version"' .claude-plugin/plugin.json
```

Expected: 3 箇所すべて同一バージョン

---

### Task 2: deploy_bridge パス解決の環境検出方式への変更 (TDD)

**Files:**
- Modify: `tests/test_mcp_server.py`
- Modify: `prefab_sentinel/mcp_server.py:218-283`

- [ ] **Step 1: Write the failing test — bridge_files パスの優先テスト**

`tests/test_mcp_server.py` の `TestDeployBridgeCleanup` クラスに追加:

```python
    @patch("prefab_sentinel.mcp_server.send_action")
    def test_uses_bridge_files_dir_when_available(self, _mock: MagicMock) -> None:
        """When _bridge_files/ exists (wheel install), uses it over tools/unity/."""
        # Create _bridge_files in a temp dir and patch __file__ to point there
        fake_pkg = self._tmp / "fake_pkg" / "prefab_sentinel"
        fake_pkg.mkdir(parents=True)
        bridge_dir = fake_pkg / "_bridge_files"
        bridge_dir.mkdir()
        test_cs = bridge_dir / "PrefabSentinel.TestBridge.cs"
        test_cs.write_text("// from _bridge_files", encoding="utf-8")

        import prefab_sentinel.mcp_server as mcp_mod
        original_file = mcp_mod.__file__
        mcp_mod.__file__ = str(fake_pkg / "mcp_server.py")
        try:
            server = create_server(project_root=str(self._project_root))
            _, result = _run(server.call_tool(
                "deploy_bridge",
                {"target_dir": str(self._target)},
            ))
        finally:
            mcp_mod.__file__ = original_file

        self.assertTrue(result["success"])
        # Should have copied from _bridge_files, not tools/unity/
        self.assertIn("PrefabSentinel.TestBridge.cs", result["data"]["copied_files"])
        # Should NOT contain files from tools/unity/
        self.assertNotIn("PrefabSentinel.EditorBridge.cs", result["data"]["copied_files"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py::TestDeployBridgeCleanup::test_uses_bridge_files_dir_when_available -v`
Expected: FAIL — `PrefabSentinel.TestBridge.cs` not in `copied_files` (current code reads from `tools/unity/`)

- [ ] **Step 3: Implement environment-aware path detection**

`prefab_sentinel/mcp_server.py` の `deploy_bridge` 関数内、L272-283 を置き換え:

```python
        # Find Bridge source files: prefer _bridge_files/ (wheel install)
        # over tools/unity/ (editable/source install).
        plugin_tools = _Path(__file__).parent / "_bridge_files"
        if not plugin_tools.is_dir():
            plugin_tools = _Path(__file__).parent.parent / "tools" / "unity"
        if not plugin_tools.is_dir():
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_SOURCE_NOT_FOUND",
                "message": "Bridge source directory not found. "
                "Ensure tools/unity/ exists (source) or package includes "
                "_bridge_files/ (wheel install).",
                "data": {},
                "diagnostics": [],
            }
```

docstring も更新:

```python
        """Deploy or update Bridge C# files to the Unity project.

        Copies Bridge C# files to the target directory. Source files are
        read from _bridge_files/ (wheel install) or tools/unity/ (source
        tree). Cleans up old Bridge files from the parent directory to
        prevent CS0101 duplicate definition errors.
        Triggers editor_refresh after copying to reload assets.

        Args:
            target_dir: Target directory in Unity project.
                Default: {project_root}/Assets/Editor/PrefabSentinel/
            include_upload_handler: Deploy VRCSDKUploadHandler.cs.
                Default False — excluded because it requires specific
                VRC SDK API versions that may not be present.
        """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_server.py::TestDeployBridgeCleanup -v`
Expected: All 9 tests PASS (8 existing + 1 new)

- [ ] **Step 5: Run full test suite + lint**

Run: `uv run pytest tests/test_mcp_server.py -q --tb=short && uv run ruff check prefab_sentinel/mcp_server.py`
Expected: All pass, no lint errors

- [ ] **Step 6: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: deploy_bridge environment-aware path detection for plugin mode

Try _bridge_files/ (wheel install) before tools/unity/ (source tree)
so deploy_bridge works when installed via /plugin. Update docstring.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 統合検証

**Files:** (none — verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -q --tb=short`
Expected: All tests pass, no regressions

- [ ] **Step 2: Verify tool count unchanged**

Run: `uv run pytest tests/test_mcp_server.py::TestToolRegistration -v`
Expected: 62 tools

- [ ] **Step 3: Verify wheel includes _bridge_files**

Run:
```bash
uv build --wheel && unzip -l dist/prefab_sentinel-*.whl | grep _bridge_files
```
Expected: `prefab_sentinel/_bridge_files/PrefabSentinel.*.cs` が含まれる

- [ ] **Step 4: Verify version sync across all 3 locations**

Run:
```bash
grep 'current_version' pyproject.toml
grep 'BridgeVersion' tools/unity/PrefabSentinel.UnityEditorControlBridge.cs
grep '"version"' .claude-plugin/plugin.json
```
Expected: 3 箇所すべて同一バージョン
