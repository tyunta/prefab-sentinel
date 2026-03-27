# deploy_bridge 旧ファイル清掃 + 除外機構 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `deploy_bridge` にデプロイ前の旧ファイル清掃と VRCSDKUploadHandler のデフォルト除外を追加し、CS0101 重複定義エラーと SDK 非互換コンパイルエラーを防止する。

**Architecture:** 既存の `deploy_bridge` 関数内に 2 つのフェーズを追加する。(1) コピー前に `target_path.parent` の旧ファイル + `.meta` を検出・削除、(2) コピーループで `_UPLOAD_HANDLER` をスキップし、ターゲット内の既存コピーも削除する。レスポンスに `removed_old_files` と `skipped_files` を追加。

**Tech Stack:** Python 3.11+, shutil, unittest + tempfile

**Spec:** `docs/superpowers/specs/2026-03-28-deploy-bridge-cleanup-design.md`

---

## File Structure

| ファイル | 責務 | 変更種別 |
|---------|------|----------|
| `prefab_sentinel/mcp_server.py` | `deploy_bridge` に旧ファイル清掃 + `include_upload_handler` パラメータ追加 | Modify |
| `tests/test_mcp_server.py` | deploy_bridge 機能テスト追加 | Modify |

---

### Task 1: テスト追加 — 旧ファイル清掃 + upload handler 除外

**Files:**
- Modify: `tests/test_mcp_server.py`
- Modify: `prefab_sentinel/mcp_server.py:218-304`

- [ ] **Step 1: Write the failing tests**

`tests/test_mcp_server.py` の `TestExtractDescription` クラスの前に以下を追加:

```python
# ---------------------------------------------------------------------------
# deploy_bridge cleanup and exclusion
# ---------------------------------------------------------------------------


class TestDeployBridgeCleanup(unittest.TestCase):
    """deploy_bridge old file cleanup and upload handler exclusion."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        self._project_root = self._tmp / "UnityProject"
        self._project_root.mkdir()
        self._target = self._project_root / "Assets" / "Editor" / "PrefabSentinel"
        self._target.mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_removes_old_files_from_parent(self, _mock: MagicMock) -> None:
        """Old PrefabSentinel.*.cs in parent dir are removed before deploy."""
        parent = self._target.parent
        old_cs = parent / "PrefabSentinel.EditorBridge.cs"
        old_meta = parent / "PrefabSentinel.EditorBridge.cs.meta"
        old_cs.write_text("// old", encoding="utf-8")
        old_meta.write_text("guid: abc", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.EditorBridge.cs", result["data"]["removed_old_files"])
        self.assertIn("PrefabSentinel.EditorBridge.cs.meta", result["data"]["removed_old_files"])
        self.assertFalse(old_cs.exists())
        self.assertFalse(old_meta.exists())

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_no_old_files_no_removal(self, _mock: MagicMock) -> None:
        """When parent has no old files, removed_old_files is empty."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_old_files"], [])

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_first_deploy_no_old_files(self, _mock: MagicMock) -> None:
        """First deploy to a new path has no old files to clean up."""
        deep_target = self._project_root / "Assets" / "NewDir" / "SubDir" / "Bridge"
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(deep_target)},
        ))

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["removed_old_files"], [])

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_upload_handler_excluded_by_default(self, _mock: MagicMock) -> None:
        """VRCSDKUploadHandler.cs is not copied when include_upload_handler=False."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertNotIn("PrefabSentinel.VRCSDKUploadHandler.cs", result["data"]["copied_files"])
        self.assertIn("PrefabSentinel.VRCSDKUploadHandler.cs", result["data"]["skipped_files"])
        self.assertFalse((self._target / "PrefabSentinel.VRCSDKUploadHandler.cs").exists())

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_upload_handler_included_when_requested(self, _mock: MagicMock) -> None:
        """VRCSDKUploadHandler.cs IS copied when include_upload_handler=True."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target), "include_upload_handler": True},
        ))

        self.assertTrue(result["success"])
        self.assertIn("PrefabSentinel.VRCSDKUploadHandler.cs", result["data"]["copied_files"])
        self.assertEqual(result["data"]["skipped_files"], [])
        self.assertTrue((self._target / "PrefabSentinel.VRCSDKUploadHandler.cs").exists())

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_stale_upload_handler_removed_from_target(self, _mock: MagicMock) -> None:
        """Previously deployed VRCSDKUploadHandler.cs is removed when excluded."""
        stale_cs = self._target / "PrefabSentinel.VRCSDKUploadHandler.cs"
        stale_meta = self._target / "PrefabSentinel.VRCSDKUploadHandler.cs.meta"
        stale_cs.write_text("// old upload handler", encoding="utf-8")
        stale_meta.write_text("guid: xyz", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        self.assertTrue(result["success"])
        self.assertFalse(stale_cs.exists())
        self.assertFalse(stale_meta.exists())

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_diagnostics_warn_on_old_file_removal(self, _mock: MagicMock) -> None:
        """Diagnostics include warning when old files are removed."""
        parent = self._target.parent
        (parent / "PrefabSentinel.EditorBridge.cs").write_text("// old", encoding="utf-8")

        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        warnings = [d for d in result["diagnostics"] if d["severity"] == "warning"]
        self.assertTrue(any("old Bridge" in d["message"] for d in warnings))

    @patch("prefab_sentinel.mcp_server.send_action")
    def test_diagnostics_info_on_skip(self, _mock: MagicMock) -> None:
        """Diagnostics include info when upload handler is skipped."""
        server = create_server(project_root=str(self._project_root))
        _, result = _run(server.call_tool(
            "deploy_bridge",
            {"target_dir": str(self._target)},
        ))

        infos = [d for d in result["diagnostics"] if d["severity"] == "info"]
        self.assertTrue(any("VRCSDKUploadHandler" in d["message"] for d in infos))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_server.py::TestDeployBridgeCleanup -v`
Expected: FAIL — `removed_old_files` / `skipped_files` not in response

- [ ] **Step 3: Implement old file cleanup + upload handler exclusion in `deploy_bridge`**

`prefab_sentinel/mcp_server.py` の `deploy_bridge` 関数 (L218-304) を修正:

```python
    @server.tool()
    def deploy_bridge(
        target_dir: str = "",
        include_upload_handler: bool = False,
    ) -> dict[str, Any]:
        """Deploy or update Bridge C# files to the Unity project.

        Copies tools/unity/*.cs from prefab-sentinel to the target directory.
        Cleans up old Bridge files from the parent directory to prevent
        CS0101 duplicate definition errors.
        Triggers editor_refresh after copying to reload assets.

        Args:
            target_dir: Target directory in Unity project.
                Default: {project_root}/Assets/Editor/PrefabSentinel/
            include_upload_handler: Deploy VRCSDKUploadHandler.cs.
                Default False — excluded because it requires specific
                VRC SDK API versions that may not be present.
        """
        import shutil
        from pathlib import Path as _Path

        _UPLOAD_HANDLER = "PrefabSentinel.VRCSDKUploadHandler.cs"

        project_root = session.project_root
        if project_root is None:
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_NO_PROJECT",
                "message": "No project activated. Call activate_project first.",
                "data": {},
                "diagnostics": [],
            }

        if not target_dir:
            target_dir = str(project_root / "Assets" / "Editor" / "PrefabSentinel")

        target_path = _Path(target_dir).resolve()

        # Path traversal guard: target must be within project root
        # Uses is_relative_to (Python 3.9+) to avoid prefix-collision bypass
        # (e.g. /project_evil matching /project with startswith)
        project_resolved = project_root.resolve()
        if not target_path.is_relative_to(project_resolved):
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_OUTSIDE_PROJECT",
                "message": f"target_dir must be within the project: {project_resolved}",
                "data": {},
                "diagnostics": [],
            }

        target_path.mkdir(parents=True, exist_ok=True)

        # Find plugin's tools/unity/ directory (source tree only)
        plugin_tools = _Path(__file__).parent.parent / "tools" / "unity"
        if not plugin_tools.is_dir():
            return {
                "success": False,
                "severity": "error",
                "code": "DEPLOY_SOURCE_NOT_FOUND",
                "message": "Bridge source directory (tools/unity/) not found. "
                "deploy_bridge requires running from the source tree.",
                "data": {},
                "diagnostics": [],
            }

        diagnostics: list[dict[str, Any]] = []

        # Phase 1: Clean up old Bridge files from parent directory
        # The glob is non-recursive on parent_dir, so matches are always
        # direct children of parent_dir (never inside target_path).
        removed_old_files: list[str] = []
        parent_dir = target_path.parent
        if parent_dir.is_dir():
            for old_file in sorted(parent_dir.glob("PrefabSentinel.*.cs")):
                old_file.unlink()
                removed_old_files.append(old_file.name)
                meta_file = old_file.with_suffix(".cs.meta")
                if meta_file.exists():
                    meta_file.unlink()
                    removed_old_files.append(meta_file.name)

        if removed_old_files:
            diagnostics.append({
                "severity": "warning",
                "message": (
                    f"Removed {len(removed_old_files)} old Bridge file(s) from "
                    f"{parent_dir} to prevent CS0101 duplicate definitions"
                ),
            })

        # Phase 2: Clean up stale upload handler from target if excluded
        if not include_upload_handler:
            for suffix in (".cs", ".cs.meta"):
                stale = target_path / _UPLOAD_HANDLER.replace(".cs", suffix)
                if stale.exists():
                    stale.unlink()

        # Phase 3: Copy source files
        old_version = session.detect_bridge_version()
        copied_files: list[str] = []
        skipped_files: list[str] = []

        for cs_file in sorted(plugin_tools.glob("*.cs")):
            if cs_file.name == _UPLOAD_HANDLER and not include_upload_handler:
                skipped_files.append(cs_file.name)
                continue
            dest = target_path / cs_file.name
            shutil.copy2(cs_file, dest)
            copied_files.append(cs_file.name)

        new_version = session.detect_bridge_version()

        if skipped_files:
            diagnostics.append({
                "severity": "info",
                "message": (
                    f"Skipped {', '.join(skipped_files)} "
                    "(optional, set include_upload_handler=true to deploy)"
                ),
            })

        # Trigger asset refresh (best-effort)
        with contextlib.suppress(Exception):
            send_action(action="refresh_asset_database")

        return {
            "success": True,
            "severity": "info",
            "code": "DEPLOY_OK",
            "message": f"Deployed {len(copied_files)} files to {target_dir}",
            "data": {
                "copied_files": copied_files,
                "skipped_files": skipped_files,
                "removed_old_files": removed_old_files,
                "old_version": old_version,
                "new_version": new_version,
                "target_dir": target_dir,
            },
            "diagnostics": diagnostics,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_server.py::TestDeployBridgeCleanup -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run pytest tests/test_mcp_server.py -v --tb=short`
Expected: All tests PASS (tool count stays at 62)

- [ ] **Step 6: Run linter**

Run: `uv run ruff check prefab_sentinel/mcp_server.py tests/test_mcp_server.py`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add prefab_sentinel/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: deploy_bridge old file cleanup and upload handler exclusion

Clean up old PrefabSentinel.*.cs (and .meta) from parent directory
before deploy to prevent CS0101 duplicate definitions. Exclude
VRCSDKUploadHandler.cs by default (include_upload_handler=false)
to avoid SDK version incompatibility compile errors.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 統合検証

**Files:** (none — verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v --tb=short -q`
Expected: All tests PASS, no regressions

- [ ] **Step 2: Verify tool count unchanged**

Run: `uv run pytest tests/test_mcp_server.py::TestToolRegistration -v`
Expected: 62 tools (deploy_bridge is modified, not new)

- [ ] **Step 3: Manual smoke test — deploy_bridge help text**

Run:
```bash
uv run python -c "
import asyncio
from prefab_sentinel.mcp_server import create_server
server = create_server()
tools = asyncio.run(server.list_tools())
bridge = [t for t in tools if t.name == 'deploy_bridge'][0]
print(bridge.inputSchema)
"
```
Expected: `include_upload_handler` パラメータが schema に含まれる（type: boolean, default: false）
